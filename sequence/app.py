import uvicorn
import requests
import threading
import time
import random
import traceback
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any
from contextlib import asynccontextmanager

# --- Importações do SeQUeNCe (v0.8.1) ---
from sequence.kernel.timeline import Timeline
from sequence.topology.node import Node
from sequence.components.memory import Memory as QuantumMemory
from sequence.components.optical_channel import QuantumChannel
from sequence.protocol import Protocol

# --- Constantes Físicas ---
C_LIGHT_SPEED_MPS = 299_792_458
FIBER_REFRACTIVE_INDEX = 1.468
C_IN_FIBER = C_LIGHT_SPEED_MPS / FIBER_REFRACTIVE_INDEX

# --- URL DO COLLECTOR (Passo 9) ---
COLLECTOR_URL = "http://127.0.0.1:8006"
# --- FIM ---

# --- Utilidades de normalização/keys ---
def _norm_name(name: str) -> str:
    return name.strip().lower()

def _chan_key(a: str, b: str) -> str:
    return f"{_norm_name(a)}-{_norm_name(b)}"

# --- Modelos de Dados da API (Pydantic) ---
class CreatePairRequest(BaseModel):
    request_id: str
    node_a: str
    node_b: str

class PairReadyNotification(BaseModel):
    pair_id: str
    fidelity: float

# --- Variáveis Globais para o Simulador ---
simulator_thread = None
timeline: Timeline = None
node_map: Dict[str, Node] = {}
channel_map: Dict[str, QuantumChannel] = {}
NEW_EVENT_SEMAPHORE = threading.Event()
SIMULATOR_RUNNING = True

# --- Topologia da Rede (PoC Estático) ---
QNODE_URLS: Dict[str, str] = {
    "alice": "http://127.0.0.1:8001",
    "bob":   "http://127.0.0.1:8002"
}

# --- FUNÇÃO DE MÉTRICAS (Passo 9) ---
def post_metric_async(name: str, value: float, tags: dict = None):
    """
    Envia uma métrica para o Collector Service em uma thread separada
    para não bloquear o loop da simulação.
    """
    try:
        payload = {"name": name, "value": value, "tags": tags or {}}
        requests.post(f"{COLLECTOR_URL}/submit_metric", json=payload, timeout=2)
        # (Opcional: logar o envio)
        # print(f"[SeQUeNCe Sim] Métrica '{name}' enviada ao Collector.")
    except Exception as e:
        # Silenciosamente falha se o collector estiver offline
        print(f"[SeQUeNCe Sim] AVISO: Falha ao enviar métrica '{name}' para o Collector: {e}")
# --- FIM DA FUNÇÃO ---

# --- Lógica de Simulação ---

def create_sequence_topology(tl: Timeline) -> Dict[str, Node]:
    print("[SeQUeNCe] Criando topologia: Alice <-> Bob (10km)")
    # (Código de criação da topologia... sem alterações)
    distance_km = 10.0
    distance_m = distance_km * 1000
    alice = Node("alice", tl)
    bob = Node("bob", tl)
    MEM_PARAMS = {
        "fidelity": 1.0, "frequency": 0, "efficiency": 1.0,
        "coherence_time": 1e9, "wavelength": 1550
    }
    mem_alice = QuantumMemory("mem_alice", tl, **MEM_PARAMS)
    mem_bob = QuantumMemory("mem_bob", tl, **MEM_PARAMS)
    alice.add_component(mem_alice)
    bob.add_component(mem_bob)
    qc_ab = QuantumChannel("qc_alice_bob", tl, attenuation=0.0, distance=distance_m)
    qc_ba = QuantumChannel("qc_bob_alice", tl, attenuation=0.0, distance=distance_m)
    qc_ab.set_ends(alice, bob)
    qc_ba.set_ends(bob, alice)
    channel_map[_chan_key("alice", "bob")] = qc_ab
    channel_map[_chan_key("bob", "alice")] = qc_ba
    print(f"[SeQUeNCe] Topologia criada. Distância: {distance_km} km.")
    return {"alice": alice, "bob": bob}


class ApiEntanglementProtocol(Protocol):
    def __init__(self, owner, name: str, request_id: str, node_a_name: str, node_b_name: str):
        super().__init__(owner, name)
        self.request_id = request_id
        self.node_a_name = _norm_name(node_a_name)
        self.node_b_name = _norm_name(node_b_name)

    def run(self):
        print(f"[SeQUeNCe Sim] Protocolo {self.request_id} iniciado na timeline (T={self.timeline.now()}).")
        a = self.node_a_name
        b = self.node_b_name
        channel = channel_map.get(_chan_key(a, b)) or channel_map.get(_chan_key(b, a))
        if not channel:
            print(f"[SeQUeNCe Sim] ERRO: Canal {a}-{b} não encontrado.")
            return

        latency_sec = channel.distance / C_IN_FIBER
        latency_ns = latency_sec * 1e9
        processing_time_ns = 10 * 1000
        total_delay_ns = latency_ns + processing_time_ns

        print(f"[SeQUeNCe Sim] Distância: {channel.distance/1000} km. Latência (fibra): {latency_ns:.0f} ns.")
        print(f"[SeQUeNCe Sim] Aguardando {total_delay_ns:.0f} ns (tempo de simulação)...")

        yield self.await_timer(total_delay_ns)

        print(f"[SeQUeNCe Sim] Simulação de par concluída (T={self.timeline.now()}).")
        fidelity = round(random.uniform(0.70, 0.99), 4)

        # --- MODIFICAÇÃO (Passo 9) ---
        # Envia métricas para o Collector (sem bloquear a simulação)
        metric_tags = {"pair_id": self.request_id, "nodes": _chan_key(a, b), "mode": "timeline"}
        threading.Thread(target=post_metric_async, args=("pair_fidelity", fidelity, metric_tags), daemon=True).start()
        threading.Thread(target=post_metric_async, args=("pair_latency_ns", total_delay_ns, metric_tags), daemon=True).start()
        # --- FIM DA MODIFICAÇÃO ---
        
        payload = PairReadyNotification(pair_id=self.request_id, fidelity=fidelity)

        print(f"[SeQUeNCe Sim] Par {self.request_id} pronto. Notificando QNodes...")
        threading.Thread(target=self.do_http_callback, args=(self.node_a_name, payload.dict()), daemon=True).start()
        threading.Thread(target=self.do_http_callback, args=(self.node_b_name, payload.dict()), daemon=True).start()

    def received_message(self, src: str, msg: Any):
        pass

    def do_http_callback(self, node_name: str, payload_dict: dict):
        try:
            node_url = QNODE_URLS[_norm_name(node_name)]
        except KeyError:
            print(f"[SeQUeNCe Callback] URL do nó '{node_name}' não encontrada em QNODE_URLS.")
            return
        try:
            resp = requests.post(f"{node_url}/pair_ready", json=payload_dict, timeout=3)
            resp.raise_for_status()
            print(f"[SeQUeNCe Callback] Nó {node_name} notificado (status={resp.status_code}).")
        except Exception as e:
            print(f"[SeQUeNCe Callback] Erro ao notificar {node_name}: {e}")

# --- Fallback (Simulação fora da timeline) ---

def _simulate_pair_fallback(request_id: str, node_a: str, node_b: str):
    a = _norm_name(node_a)
    b = _norm_name(node_b)
    channel = channel_map.get(_chan_key(a, b)) or channel_map.get(_chan_key(b, a))
    if not channel:
        print(f"[Fallback] ERRO: Canal {a}-{b} não encontrado.")
        return

    latency_sec = channel.distance / C_IN_FIBER
    latency_ns = latency_sec * 1e9
    processing_time_ns = 10 * 1000
    total_delay_ns = latency_ns + processing_time_ns

    print(f"[Fallback] Protocolo {request_id} simulado fora da timeline.")
    print(f"[Fallback] Latência (fibra): {latency_ns:.0f} ns. Dormindo por {total_delay_ns/1e9:.6f} s...")

    time.sleep(total_delay_ns / 1e9)
    fidelity = round(random.uniform(0.70, 0.99), 4)
    
    # --- MODIFICAÇÃO (Passo 9) ---
    # Envia métricas para o Collector (do fallback)
    metric_tags = {"pair_id": request_id, "nodes": _chan_key(a, b), "mode": "fallback"}
    threading.Thread(target=post_metric_async, args=("pair_fidelity", fidelity, metric_tags), daemon=True).start()
    threading.Thread(target=post_metric_async, args=("pair_latency_ns", total_delay_ns, metric_tags), daemon=True).start()
    # --- FIM DA MODIFICAÇÃO ---
    
    payload = PairReadyNotification(pair_id=request_id, fidelity=fidelity).dict()

    print(f"[Fallback] Par {request_id} pronto. Notificando QNodes...")
    for node_name in (a, b):
        # (O resto da função de callback do fallback... sem alterações)
        try:
            node_url = QNODE_URLS[_norm_name(node_name)]
        except KeyError:
            print(f"[Fallback] ERRO: URL do nó '{node_name}' não encontrada.")
            continue
        try:
            resp = requests.post(f"{node_url}/pair_ready", json=payload, timeout=3)
            resp.raise_for_status()
            print(f"[Fallback] Nó {node_name} notificado (status={resp.status_code}).")
        except Exception as e:
            print(f"[Fallback] Erro ao notificar {node_name}: {e}")

# --- Arquitetura de Sincronização (v0.5.x) ---

def run_simulator_loop(tl: Timeline):
    print("[SeQUeNCe Thread] Iniciada. Aguardando eventos...")
    global SIMULATOR_RUNNING
    SIMULATOR_RUNNING = True
    while SIMULATOR_RUNNING:
        try:
            NEW_EVENT_SEMAPHORE.wait()
            if not SIMULATOR_RUNNING: break
            print("[SeQUeNCe Thread] Sinal recebido. Processando tl.run()...")
            tl.run()
            print("[SeQUeNCe Thread] Fila de eventos vazia. Aguardando novamente...")
            NEW_EVENT_SEMAPHORE.clear()
        except Exception as e:
            print(f"[SeQUeNCe Thread] Erro fatal no simulador: {e}")
            traceback.print_exc()
            SIMULATOR_RUNNING = False
    print("[SeQUeNCe Thread] Loop do simulador encerrado.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[FastAPI] Evento de startup (lifespan) disparado.")
    global timeline, simulator_thread, node_map
    timeline = Timeline()
    print("[FastAPI] Timeline do SeQUeNCe criada.")
    node_map = create_sequence_topology(timeline)
    if not node_map:
        print("[FastAPI] AVISO: Topologia não foi criada. Saindo.")
    else:
        print("[FastAPI] Iniciando thread do simulador SeQUeNCe...")
        simulator_thread = threading.Thread(
            target=run_simulator_loop, args=(timeline,), daemon=True
        )
        simulator_thread.start()
        print("[FastAPI] Servidor pronto. Simulador rodando em background.")
    yield
    print("[FastAPI] Evento de shutdown (lifespan) disparado.")
    global SIMULATOR_RUNNING
    SIMULATOR_RUNNING = False
    NEW_EVENT_SEMAPHORE.set()
    if simulator_thread:
        simulator_thread.join(timeout=3)
    print("[FastAPI] Thread do simulador encerrada.")

# --- Configuração da Aplicação FastAPI ---
app = FastAPI(
    title="SeQUeNCe Integration Layer",
    description="Serviço que executa o simulador SeQUeNCe em background.",
    version="0.5.4", # <- Versão atualizada
    lifespan=lifespan
)

@app.get("/", summary="Health Check")
def read_root():
    # (Sem alterações)
    status = "running" if (simulator_thread and simulator_thread.is_alive()) else "stopped"
    return {
        "status": "SeQUeNCe Integration Layer está online",
        "version": app.version,
        "simulator": status,
        "timeline_now": timeline.now() if timeline else None
    }

@app.post("/create_pair", summary="Solicita a criação de um par entrelaçado")
def create_pair(request: CreatePairRequest):
    print(f"\n[FastAPI] Recebida solicitação de par: {request.request_id} ({request.node_a} <-> {request.node_b})")
    try:
        # (Sem alterações nesta função)
        if not timeline or not simulator_thread or not simulator_thread.is_alive():
            print("[Debug] FALHA NA VERIFICAÇÃO 'is_alive()'")
            raise HTTPException(status_code=500, detail="Simulador SeQUeNCe não está rodando.")
        req_a = _norm_name(request.node_a)
        req_b = _norm_name(request.node_b)
        if req_a not in node_map or req_b not in node_map:
            print(f"[FastAPI] ERRO: Nós {req_a} ou {req_b} não encontrados no 'node_map'.")
            raise HTTPException(status_code=404, detail="Nó(s) não encontrado(s) na topologia do SeQUeNCe.")
        print("[Debug] Obtendo 'node_a' do node_map...")
        node_a_obj = node_map[req_a]
        owner_tl = getattr(node_a_obj, "timeline", None)
        if owner_tl is not timeline:
            raise HTTPException(status_code=500, detail="Timeline inconsistente no nó proprietário do protocolo.")
        print("[Debug] Criando objeto ApiEntanglementProtocol...")
        protocol = ApiEntanglementProtocol(
            owner=node_a_obj, name=f"api_protocol_{request.request_id}",
            request_id=request.request_id, node_a_name=req_a, node_b_name=req_b
        )
        print("[Debug] Chamando protocol.start()...")
        if hasattr(protocol, "start") and callable(getattr(protocol, "start")):
            try:
                protocol.start()
            except Exception as e:
                print("[Debug] Falha em protocol.start():", e)
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=f"Falha ao iniciar protocolo: {e}")
            print("[Debug] Sinalizando a thread do simulador (semaphore.set())...")
            NEW_EVENT_SEMAPHORE.set()
            print(f"[FastAPI] Protocolo {request.request_id} iniciado no SeQUeNCe.")
        else:
            print("[Debug] 'Protocol.start()' indisponível — usando fallback com thread.")
            threading.Thread(
                target=_simulate_pair_fallback,
                args=(request.request_id, req_a, req_b),
                daemon=True
            ).start()
            print(f"[FastAPI] Protocolo {request.request_id} agendado via fallback.")
        return {"status": "pair_creation_started", "request_id": request.request_id}
    except HTTPException:
        raise
    except Exception as e:
        print("=" * 50)
        print(f"[FASTAPI] ERRO 500 DETALHADO:")
        traceback.print_exc()
        print("=" * 50)
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")

# --- Execução do Servidor ---
if __name__ == "__main__":
    print("Iniciando servidor SeQUeNCe Integration Layer (v0.8.1) na porta 8004...")
    uvicorn.run(app, host="0.0.0.0", port=8004)