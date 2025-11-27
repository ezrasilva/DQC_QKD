import requests
import time
import sys

# URLs (Ajuste se usar Docker ou localhost)
URL_ORCHESTRATOR = "http://127.0.0.1:8000"
URL_SEQUENCE = "http://127.0.0.1:8004"

# Circuito "Inteiro" (Mock, pois o controller vai ignorar e usar o slice fixo do mock HDH)
FULL_QASM = """
OPENQASM 2.0;
include "qelib1.inc";
qreg q[2];
creg c[2];
x q[0];
h q[1];
measure q[0]->c[0];
measure q[1]->c[1];
"""

def run_test():
    print("="*50)
    print("   TESTE E2E - ARQUITETURA SDN (SEM COLLECTOR)")
    print("="*50)

    try:
        # 1. Verificar Monitoramento (Novo Endpoint)
        print("\n[1] Verificando Monitoramento de Rede (Sequence)...")
        resp = requests.get(f"{URL_SEQUENCE}/network_status")
        resp.raise_for_status()
        print(f"Status da Rede: {resp.json()['links'].keys()}")

        # 2. Submeter Job Completo (Novo Fluxo SDN)
        print("\n[2] Submetendo Job Completo ao Orchestrator...")
        job_id = f"sdn-job-{int(time.time())}"
        payload = {
            "job_id": job_id,
            "circuit_qasm": FULL_QASM,
            "shots": 1024
        }
        
        # Note que o endpoint mudou de /run para /submit_job
        resp = requests.post(f"{URL_ORCHESTRATOR}/submit_job", json=payload, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        
        print("\n[3] Resultado da Execução:")
        print(f"Plano Gerado: {len(result['execution_plan'])} fatias")
        print(f"Resultados dos Nós: {result['results'].keys()}")
        
        if "slice_0" in result['results'] and "slice_1" in result['results']:
            print("\n[SUCESSO] O Job foi particionado e executado em Alice e Bob!")
        else:
            raise Exception("Falha: Resultados incompletos.")

    except Exception as e:
        print(f"\n[ERRO] O teste falhou: {e}")
        sys.exit(1)

if __name__ == "__main__":
    run_test()