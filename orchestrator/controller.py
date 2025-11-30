# orchestrator/controller.py
import requests
import logging

# Configuração de Logs
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sdn-controller")

# URLs da infraestrutura (Use nomes dos containers Docker)
# Se estiver rodando localmente sem docker, mude para localhost
SEQUENCE_URL = "http://qnet:8004"
QNODE_URLS = {
    "alice": "http://qnode-alice:8001", 
    "bob": "http://qnode-bob:8002"
}

class SDNController:
    def __init__(self):
        pass

    def get_network_status(self):
        """Consulta o Sequence para saber a saúde da rede."""
        try:
            resp = requests.get(f"{SEQUENCE_URL}/network_status", timeout=2)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"Falha ao monitorar rede: {e}")
        return {"links": {}, "global_fidelity": 0.95}

    def analyze_circuit_with_hdh(self, full_qasm: str):
        """
        MOCK: Simula a biblioteca HDH quebrando o circuito.
        """
        logger.info("Analisando circuito com HDH...")
        
        # Fatia 1: Alice
        slice_1 = "OPENQASM 2.0; include \"qelib1.inc\"; qreg q[1]; creg c[1]; x q[0]; measure q[0]->c[0];"
        
        # Fatia 2: Bob
        slice_2 = "OPENQASM 2.0; include \"qelib1.inc\"; qreg q[1]; creg c[1]; h q[0]; measure q[0]->c[0];"
        
        slices = [slice_1, slice_2]
        # Dependência dummy
        dependencies = [{"from_slice": 0, "to_slice": 1, "type": "quantum_teleport"}]
        
        return slices, dependencies

    def plan_execution(self, full_qasm: str):
        """Planeja onde cada fatia será executada."""
        # 1. Pega estado da rede
        net_status = self.get_network_status()
        
        # 2. Analisa circuito
        slices, deps = self.analyze_circuit_with_hdh(full_qasm)
        
        # 3. Decisão: Alice pega 0, Bob pega 1
        plan = []
        
        # --- CORREÇÃO: Usando 'slice_idx' explicitamente ---
        plan.append({"slice_idx": 0, "qasm": slices[0], "target_node": "alice"})
        plan.append({"slice_idx": 1, "qasm": slices[1], "target_node": "bob"})
        
        return plan

    def execute_plan(self, job_id, plan, shots):
        """Orquestra a execução do plano."""
        results = {}
        
        # Passo 1: Solicitar Par EPR (opcional no teste, mas bom ter)
        try:
            requests.post(f"{SEQUENCE_URL}/create_pair", json={
                "request_id": f"pair-{job_id}",
                "node_a": "alice",
                "node_b": "bob"
            }, timeout=1)
        except Exception as e:
            logger.warning(f"Erro ao pedir par EPR (não fatal): {e}")

        # Passo 2: Executar fatias
        for step in plan:
            node_name = step['target_node']
            url = QNODE_URLS.get(node_name)
            
            # --- CORREÇÃO: Usando 'slice_idx' para ler ---
            idx = step['slice_idx']
            
            payload = {
                "circuit_qasm": step['qasm'],
                "slice_id": f"{job_id}_slice_{idx}",
                "shots": shots,
                "fidelity": 0.99  # Necessário para evitar erro 400 no QNode
            }
            
            try:
                logger.info(f"Enviando fatia {idx} para {node_name}...")
                resp = requests.post(f"{url}/execute_slice", json=payload, timeout=10)
                resp.raise_for_status()
                results[f"slice_{idx}"] = resp.json()
            except Exception as e:
                logger.error(f"Erro ao executar no nó {node_name}: {e}")
                results[f"slice_{idx}"] = {"error": str(e)}
                
        return results