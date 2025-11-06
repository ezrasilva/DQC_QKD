import requests
import time
import sys

# --- Configuração das URLs dos Serviços ---
URL_ORCHESTRATOR = "http://127.0.0.1:8000"
URL_SEQUENCE = "http://127.0.0.1:8004"
URL_COLLECTOR = "http://127.0.0.1:8006"
URL_QNODE_ALICE = "http://127.0.0.1:8001"
URL_QNODE_BOB = "http://127.0.0.1:8002"

# Payloads de circuito (com 'include' corrigido)
QASM_ALICE = "OPENQASM 2.0; include \"qelib1.inc\"; qreg q[1]; creg c[1]; x q[0]; measure q[0]->c[0];"
QASM_BOB = "OPENQASM 2.0; include \"qelib1.inc\"; qreg q[1]; creg c[1]; h q[0]; measure q[0]->c[0];"

def run_test(test_mode="SUCCESS"):
    """
    Executa o fluxo de trabalho E2E.
    
    test_mode:
    - "SUCCESS": Teste de fluxo feliz.
    - "MITM":    Teste de falha de segurança (Man-in-the-Middle).
    """
    
    print("\n" + "="*50)
    print(f"   INICIANDO TESTE E2E (Modo: {test_mode})")
    print("="*50)
    
    key_id_do_job = None
    
    try:
        # --- LIMPEZA (Opcional) ---
        print("[TESTE] Limpando métricas antigas no Collector...")
        requests.delete(f"{URL_COLLECTOR}/metrics", timeout=5)
        
        # --- FASE 1: Distribuição de Chaves (Orchestrator -> QKDN -> QNodes) ---
        print("\n--- FASE 1: Distribuindo Chaves (via Orchestrator) ---")
        job_payload = {
            "job_id": f"e2e-job-{int(time.time())}",
            "slices": [
                {"node_id": "alice", "qasm_slice": QASM_ALICE},
                {"node_id": "bob", "qasm_slice": QASM_BOB}
            ]
        }
        resp = requests.post(f"{URL_ORCHESTRATOR}/run", json=job_payload, timeout=10)
        resp.raise_for_status()
        
        data = resp.json()
        key_id_do_job = data.get("key_id")
        
        if not key_id_do_job:
            raise ValueError("Orchestrator não retornou um key_id!")
            
        print(f"[SUCESSO] Chaves distribuídas. Key ID: {key_id_do_job}")

        # --- FASE 2: Geração de Pares (Sequence -> QNodes) ---
        print("\n--- FASE 2: Gerando Par Emaranhado (via Sequence) ---")
        pair_payload = {
            "request_id": f"e2e-pair-{int(time.time())}",
            "node_a": "alice",
            "node_b": "bob"
        }
        resp = requests.post(f"{URL_SEQUENCE}/create_pair", json=pair_payload, timeout=5)
        resp.raise_for_status()
        print("[SUCESSO] Pedido de par enviado. Simulação em background...")
        
        # Espera a simulação (callback) e o envio da métrica
        time.sleep(1) 

        # --- FASE 3: Canal Clássico Seguro (Alice -> Bob) ---
        print("\n--- FASE 3: Alice envia M-Bits (Teleport) para Bob ---")
        
        test_key_id = key_id_do_job
        if test_mode == "MITM":
            # Simula um MITM: troca o Key ID por um UUID falso
            test_key_id = "mitm-fake-key-id-00000000"
            print("[TESTE] MODO MITM: Usando um Key ID FALSO!")
            
        feedforward_payload = {
            "key_id": test_key_id,
            "message": "01" # m-bits do teleporte
        }
        
        resp = requests.post(f"{URL_QNODE_ALICE}/send_feedforward/bob", json=feedforward_payload, timeout=10)
        
        if test_mode == "SUCCESS":
            resp.raise_for_status()
            print(f"[SUCESSO] Bob recebeu e decifrou a mensagem: {resp.json()}")
        
        elif test_mode == "MITM":
            if resp.status_code == 404 or resp.status_code == 403:
                print(f"[SUCESSO] Bob REJEITOU a mensagem (Status {resp.status_code})")
            else:
                raise Exception(f"Falha no teste MITM! Bob aceitou a chave (Status {resp.status_code})")

        # --- FASE 4: Coleta de Métricas (Collector) ---
        print("\n--- FASE 4: Verificando Métricas no Collector ---")
        time.sleep(1) # Garante que as métricas assíncronas chegaram
        
        resp = requests.get(f"{URL_COLLECTOR}/metrics", timeout=5)
        resp.raise_for_status()
        
        metrics = resp.json()
        if not metrics:
            raise Exception("Falha no teste de Métricas! O Collector está vazio.")
            
        # Filtra as métricas que o 'sequence' enviou
        fidelity_metrics = [m for m in metrics if m["name"] == "pair_fidelity"]
        if not fidelity_metrics:
            raise Exception("Falha no teste de Métricas! Nenhuma métrica 'pair_fidelity' encontrada.")
        
        print(f"[SUCESSO] Métricas coletadas ({len(metrics)} total).")
        print(f"  - Exemplo (Fidelidade): {fidelity_metrics[0]['value']}")
        
        print("\n" + "="*50)
        print(f"   TESTE E2E (Modo: {test_mode}) CONCLUÍDO COM SUCESSO")
        print("="*50)
        
    except Exception as e:
        print("\n" + "X"*50)
        print(f"   TESTE E2E (Modo: {test_mode}) FALHOU")
        print(f"   ERRO: {e}")
        print("X"*50)
        sys.exit(1) # Sai com código de erro

if __name__ == "__main__":
    # Roda o teste de sucesso
    run_test(test_mode="SUCCESS")
    
    # Roda o teste de falha (MITM)
    run_test(test_mode="MITM")