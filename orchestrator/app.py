import uvicorn
import requests # Para chamar outros microsserviços (QNode, QKDN)
import networkx as nx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

# --- Modelos de Dados (Pydantic) ---

class Slice(BaseModel):
    """
    Define um 'slice' de circuito destinado a um nó específico.
    """
    node_id: str
    qasm_slice: str

class RunRequest(BaseModel):
    """
    Define a requisição para o endpoint /run, que contém
    um ID de job e uma lista de slices para despachar.
    """
    job_id: str
    slices: List[Slice]

# --- Configuração da Aplicação ---

app = FastAPI(
    title="DQC Orchestrator",
    description="Serviço central que recebe jobs e despacha 'slices' para os QNodes.",
    version="0.2.0" # Versão atualizada
)

# --- URL do QKDN Stub (Passo 7) ---
QKDN_URL = "http://127.0.0.1:8005"

# --- Topologia da Rede (PoC Estático) ---
# Usamos o NetworkX como sugerido no plano para armazenar a topologia.
TOPOLOGY_GRAPH = nx.Graph()
TOPOLOGY_GRAPH.add_node("alice", url="http://127.0.0.1:8001")
TOPOLOGY_GRAPH.add_node("bob", url="http://127.0.0.1:8002")

print("[Orchestrator] Topologia carregada:")
for node, data in TOPOLOGY_GRAPH.nodes(data=True):
    print(f"  - Node: {node}, URL: {data.get('url')}")


# --- Endpoints da API ---

@app.get("/", summary="Health Check")
def read_root():
    """Endpoint básico para verificar se o serviço está online."""
    return {"status": "Orchestrator está online"}

@app.post("/run", summary="Executa um job distribuído")
def run_job(request: RunRequest):
    """
    Recebe um job com 'slices', solicita uma chave ao QKDN,
    e despacha os 'slices' (com a chave) para os QNodes.
    """
    print(f"\n[Orchestrator] Recebido Job: {request.job_id}")
    
    # --- Início da Integração QKDN (Passo 7) ---
    key_id: Optional[str] = None
    key_hex: Optional[str] = None
    
    # 1. Identifica os participantes a partir dos slices
    nodes = list(set([s.node_id for s in request.slices]))
    
    if len(nodes) >= 2:
        client_a = nodes[0]
        client_b = nodes[1]
        print(f"  - Job envolve {nodes}. Solicitando chave para {client_a} <-> {client_b}.")
        
        try:
            # 2. Chama o QKDN Stub (Porta 8005)
            key_payload = {"client_a": client_a, "client_b": client_b, "key_length": 256}
            key_response = requests.post(f"{QKDN_URL}/qkd/v1/keys", json=key_payload, timeout=5)
            
            if key_response.status_code == 200:
                key_data = key_response.json()["keys"][0]
                key_id = key_data["key_id"]
                key_hex = key_data["key_data_hex"]
                print(f"  - [KMA] Chave {key_id} recebida do QKDN.")
            else:
                print(f"  - [KMA] ERRO: QKDN retornou status {key_response.status_code}")
        except requests.exceptions.ConnectionError:
            print(f"  - [KMA] ERRO: Falha ao conectar ao QKDN em {QKDN_URL}.")
        except Exception as e:
            print(f"  - [KMA] ERRO: Erro inesperado ao buscar chave: {e}")
    else:
        print("  - AVISO: Menos de 2 nós no job, pulando solicitação de chave.")
    
    # --- Fim da Integração QKDN ---
    
    dispatch_results = {
        "job_id": request.job_id,
        "success": [],
        "failed": []
    }

    # Itera sobre os slices recebidos na requisição
    for slice_to_run in request.slices:
        node_id = slice_to_run.node_id
        
        if node_id not in TOPOLOGY_GRAPH:
            print(f"  - Erro: Nó '{node_id}' não encontrado na topologia.")
            dispatch_results["failed"].append({"node": node_id, "reason": "Nó desconhecido"})
            continue

        node_url = TOPOLOGY_GRAPH.nodes[node_id]["url"]
        slice_endpoint = f"{node_url}/execute_slice"
        
        # Prepara o payload para enviar ao QNode
        qnode_payload = {
            "circuit_qasm": slice_to_run.qasm_slice,
            "slice_id": f"{request.job_id}-{node_id}",
            "shots": 1024,
            "key_id": key_id,   # Envia a chave (mesmo que seja None)
            "key_hex": key_hex  # Envia a chave (mesmo que seja None)
        }
        
        # Despacha o 'slice'
        try:
            print(f"  - Despachando slice para: {node_id} ({slice_endpoint})")
            response = requests.post(slice_endpoint, json=qnode_payload, timeout=5)
            
            response.raise_for_status() # Levanta um erro se o status for 4xx ou 5xx
            
            print(f"  - Sucesso de {node_id}. Counts: {response.json().get('counts')}")
            dispatch_results["success"].append({"node": node_id, "response": response.json()})
            
        except requests.exceptions.ConnectionError:
            print(f"  - Erro: Impossível conectar ao nó {node_id} em {slice_endpoint}")
            dispatch_results["failed"].append({"node": node_id, "reason": "ConnectionError"})
        except requests.exceptions.HTTPError as e:
            # Captura erros 4xx/5xx (como o erro 400 do QASM)
            print(f"  - Falha em {node_id}. Status: {e.response.status_code}, Detalhe: {e.response.text}")
            dispatch_results["failed"].append({"node": node_id, "reason": e.response.text})
        except Exception as e:
            print(f"  - Erro inesperado com {node_id}: {e}")
            dispatch_results["failed"].append({"node": node_id, "reason": str(e)})

    # --- ATUALIZAÇÃO (Passo 10.a) ---
    # Adiciona o key_id à resposta final para o script de teste E2E
    dispatch_results["key_id"] = key_id
    # --- FIM DA ATUALIZAÇÃO ---

    print(f"[Orchestrator] Job {request.job_id} concluído.")
    return dispatch_results

# --- Execução do Servidor ---

if __name__ == "__main__":
    """
    Permite rodar o servidor diretamente com 'python app.py'
    """
    print("Iniciando servidor Orchestrator na porta 8000...")
    uvicorn.run(app, host="0.0.0.0", port=8000)