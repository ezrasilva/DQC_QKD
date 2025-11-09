import uvicorn
import requests
import threading
import time
import random
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict

# --- Modelos de Dados (Pydantic) ---

class RequestPair(BaseModel):
    """
    Define a requisição para um novo par entrelaçado.
    """
    pair_id: str
    node_a: str
    node_b: str
    
class PairReadyNotification(BaseModel):
    """
    Define o payload que este serviço enviará para os QNodes
    (Deve ser o mesmo modelo que o QNode espera em /pair_ready).
    """
    pair_id: str
    fidelity: float

# --- Configuração da Aplicação ---

app = FastAPI(
    title="Pair Queue (Emulador)",
    description="Serviço que emula a geração de pares entrelaçados (EPR pairs).",
    version="0.1.0"
)

# --- Topologia da Rede (PoC Estático) ---
# O PairQueue precisa saber o endereço dos QNodes para enviar os callbacks.
TOPOLOGY_MAP: Dict[str, str] = {
    "alice": "http://127.0.0.1:8001",
    "bob": "http://127.0.0.1:8002",
    "charlie": "http://127.0.0.1:8003"
}
print("[PairQueue] Topologia carregada.")

# --- Lógica de Emulação (Executada em Thread) ---

def notify_node(node_url: str, payload: PairReadyNotification):
    """
    Função auxiliar para enviar a notificação de 'par pronto'.
    """
    endpoint = f"{node_url}/pair_ready"
    node_id = node_url.split(":")[-1] # Hack simples para log
    
    try:
        response = requests.post(endpoint, json=payload.dict(), timeout=3)
        if response.status_code == 200:
            print(f"  - [PairQueue] Nó ({node_id}) notificado com sucesso.")
        else:
            print(f"  - [PairQueue] Erro ao notificar Nó ({node_id}). Status: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print(f"  - [PairQueue] ERRO DE CONEXÃO: Nó ({node_id}) em {endpoint} está offline.")
    except Exception as e:
        print(f"  - [PairQueue] Erro inesperado ao notificar {node_id}: {e}")


def emulate_pair_generation(node_a_url: str, node_b_url: str, pair_id: str):
    """
    Simula a geração de pares: dorme por um tempo aleatório,
    gera uma fidelidade e notifica os dois nós.
    """
    print(f"[PairQueue] Gerando par '{pair_id}' entre {node_a_url} e {node_b_url}...")
    
    # 1. Emula a latência da rede/geração
    delay = random.uniform(0.5, 2.0) # 0.5 a 2.0 segundos
    time.sleep(delay)
    
    # 2. Emula a fidelidade do par
    fidelity = round(random.uniform(0.70, 0.99), 4) # 70% a 99%
    
    print(f"[PairQueue] Par '{pair_id}' pronto. Fidelidade: {fidelity}. Notificando nós...")
    
    # 3. Prepara o payload da notificação
    payload = PairReadyNotification(pair_id=pair_id, fidelity=fidelity)
    
    # 4. Notifica ambos os nós (poderia ser em paralelo, mas sequencial é ok para PoC)
    notify_node(node_a_url, payload)
    notify_node(node_b_url, payload)
    print(f"[PairQueue] Notificações para '{pair_id}' concluídas.")


# --- Endpoints da API ---

@app.get("/", summary="Health Check")
def read_root():
    """Endpoint básico para verificar se o serviço está online."""
    return {"status": "PairQueue está online"}

@app.post("/request_pair", summary="Solicita um novo par entrelaçado")
def request_pair(request: RequestPair):
    """
    Recebe um pedido de par e inicia o processo de geração
    emulado em uma thread separada.
    """
    print(f"\n[PairQueue] Recebida solicitação de par: {request.pair_id} ({request.node_a} <-> {request.node_b})")
    
    # 1. Valida os nós contra a topologia
    if request.node_a not in TOPOLOGY_MAP or request.node_b not in TOPOLOGY_MAP:
        raise HTTPException(status_code=404, detail="Nó(s) não encontrado(s) na topologia.")
        
    node_a_url = TOPOLOGY_MAP[request.node_a]
    node_b_url = TOPOLOGY_MAP[request.node_b]

    # 2. Inicia a geração emulada em uma thread
    # Isso permite que a API retorne imediatamente (não-bloqueante)
    thread = threading.Thread(
        target=emulate_pair_generation,
        args=(node_a_url, node_b_url, request.pair_id)
    )
    thread.start() # Inicia a thread

    # 3. Retorna resposta imediata para o solicitante (Orchestrator)
    return {"status": "pair_requested", "pair_id": request.pair_id}

# --- Execução do Servidor ---

if __name__ == "__main__":
    """
    Permite rodar o servidor diretamente com 'python app.py'
    """
    print("Iniciando servidor PairQueue na porta 8003...")
    uvicorn.run(app, host="0.0.0.0", port=8003)