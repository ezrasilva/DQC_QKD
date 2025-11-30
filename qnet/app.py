import uvicorn
import requests
import threading
import time
import random
import traceback
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List
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
    "alice": "http://qnode-alice:8001",
    "bob":   "http://qnode-bob:8002"
}

# --- Lógica de Simulação ---

def create_sequence_topology(tl: Timeline) -> Dict[str, Node]:
    print("[SeQUeNCe] Criando topologia: Alice <-> Bob (10km)")
    # (Pode ajustar a distância aqui para testar a decisão do SDN)
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
    
    # Canais bidirecionais
    qc_ab = QuantumChannel("qc_alice_bob", tl, attenuation=0.2, distance=distance_m) # 0.2 dB/km
    qc_ba = QuantumChannel("qc_bob_alice", tl, attenuation=0.2, distance=distance_m)
    
    qc_ab.set_ends(alice, bob)
    qc_ba.set_ends(bob, alice)
    
    # Mapa usado pelo endpoint de monitoramento
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
        
        # Simula atraso físico
        channel = channel_map.get(_chan_key(a, b)) or channel_map.get(_chan_key(b, a))
        if not channel:
            print(f"[SeQUeNCe Sim] ERRO: Canal {a}-{b} não encontrado.")
            return

        latency_sec = channel.distance / C_IN_FIBER
        latency_ns = latency_sec * 1e9
        processing_time_ns = 10 * 1000 # overhead fixo
        total_delay_ns = latency_ns + processing_time_ns

        print(f"[SeQUeNCe Sim] Distância: {channel.distance/1000} km. Latência (fibra): {latency_ns:.0f} ns.")
        
        # Aguarda na timeline
        yield self.await_timer(total_delay_ns)

        # Geração de Fidelidade (Mock da física)
        # Numa versão avançada, isso dependeria da distância (attenuation)
        base_fidelity = 0.99
        loss_factor = (channel.distance / 1000) * 0.005 # perde 0.5% por km
        final_fidelity = round(max(0.5, base_fidelity - loss_factor + random.uniform(-0.02, 0.02)), 4)

        print(f"[SeQUeNCe Sim] Par concluído. Fidelidade gerada: {final_fidelity}")
        
        payload = PairReadyNotification(pair_id=self.request_id, fidelity=final_fidelity)

        print(f"[SeQUeNCe Sim] Par {self.request_id} pronto. Notificando QNodes...")
        threading.Thread(target=self.do_http_callback, args=(self.node_a_name, payload.dict()), daemon=True).start()
        threading.Thread(target=self.do_http_callback, args=(self.node_b_name, payload.dict()), daemon=True).start()

    def received_message(self, src: str, msg: Any):
        pass

    def do_http_callback(self, node_name: str, payload_dict: dict):
        try:
            node_url = QNODE_URLS.get(_norm_name(node_name))
            if not node_url:
                print(f"[Callback] URL não encontrada para {node_name}")
                return
            resp = requests.post(f"{node_url}/pair_ready", json=payload_dict, timeout=3)
            resp.raise_for_status()
            print(f"[Callback] Nó {node_name} notificado.")
        except Exception as e:
            print(f"[Callback] Erro ao notificar {node_name}: {e}")

# --- Fallback (Simulação fora da timeline) ---

def _simulate_pair_fallback(request_id: str, node_a: str, node_b: str):
    a = _norm_name(node_a)
    b = _norm_name(node_b)
    channel = channel_map.get(_chan_key(a, b)) or channel_map.get(_chan_key(b, a))
    
    # Latencia padrao se canal nao existe
    total_delay_s = 0.001
    dist_km = 0
    
    if channel:
        latency_sec = channel.distance / C_IN_FIBER
        total_delay_s = latency_sec + 0.00001
        dist_km = channel.distance / 1000

    print(f"[Fallback] Simulando par {request_id} (delay={total_delay_s:.6f}s)...")
    time.sleep(total_delay_s)
    
    # Fidelidade mockada com base na distancia
    fidelity = round(max(0.5, 0.99 - (dist_km * 0.005)), 4)
    
    payload = PairReadyNotification(pair_id=request_id, fidelity=fidelity).dict()

    print(f"[Fallback] Par {request_id} pronto ({fidelity}). Notificando QNodes...")
    for node_name in (a, b):
        try:
            node_url = QNODE_URLS.get(_norm_name(node_name))
            if node_url:
                requests.post(f"{node_url}/pair_ready", json=payload, timeout=3)
        except Exception as e:
            print(f"[Fallback] Erro ao notificar {node_name}: {e}")

# --- Arquitetura de Sincronização ---

def run_simulator_loop(tl: Timeline):
    print("[SeQUeNCe Thread] Iniciada.")
    global SIMULATOR_RUNNING
    SIMULATOR_RUNNING = True
    while SIMULATOR_RUNNING:
        try:
            NEW_EVENT_SEMAPHORE.wait()
            if not SIMULATOR_RUNNING: break
            tl.run()
            NEW_EVENT_SEMAPHORE.clear()
        except Exception as e:
            print(f"[SeQUeNCe Thread] Erro: {e}")
            traceback.print_exc()
            SIMULATOR_RUNNING = False

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[SeQUeNCe] Startup...")
    global timeline, simulator_thread, node_map
    timeline = Timeline()
    node_map = create_sequence_topology(timeline)
    
    simulator_thread = threading.Thread(
        target=run_simulator_loop, args=(timeline,), daemon=True
    )
    simulator_thread.start()
    yield
    print("[SeQUeNCe] Shutdown...")
    global SIMULATOR_RUNNING
    SIMULATOR_RUNNING = False
    NEW_EVENT_SEMAPHORE.set()
    if simulator_thread:
        simulator_thread.join(timeout=3)

# --- Configuração da Aplicação FastAPI ---
app = FastAPI(
    title="SeQUeNCe Network Simulator",
    description="Simulador de Camada Física para SDN Quântica.",
    version="1.0-SDN",
    lifespan=lifespan
)

@app.get("/", summary="Health Check")
def read_root():
    status = "running" if (simulator_thread and simulator_thread.is_alive()) else "stopped"
    return {
        "status": "online",
        "simulator": status,
        "timeline_now": timeline.now() if timeline else 0
    }
# sequence/app.py

@app.get("/network_status", summary="Telemetria da Rede para o SDN Controller")
def get_network_status():
    """
    Retorna o estado atual dos links (latência, fidelidade estimada).
    O Orchestrator usa isso para decidir onde alocar os slices.
    """
    telemetry = {}
    
    for key, channel in channel_map.items():
        # Cálculo de latência baseado na física
        latency_ns = (channel.distance / C_IN_FIBER) * 1e9
        
        # CORREÇÃO AQUI: Usar .sender e .receiver em vez de .ends
        source_name = channel.sender.name if hasattr(channel, "sender") and channel.sender else "?"
        target_name = channel.receiver.name if hasattr(channel, "receiver") and channel.receiver else "?"
        
        # Mock de fidelidade baseado na atenuação
        estimated_fidelity = max(0.5, 0.99 - (channel.distance/1000 * 0.005))
        
        telemetry[key] = {
            "source": source_name,
            "target": target_name,
            "distance_km": channel.distance / 1000.0,
            "latency_ns": round(latency_ns, 2),
            "estimated_fidelity": round(estimated_fidelity, 4),
            "status": "active"
        }
        
    return {
        "timestamp": time.time(),
        "links": telemetry
    }

@app.post("/create_pair", summary="Solicita a criação de um par entrelaçado")
def create_pair(request: CreatePairRequest):
    print(f"\n[API] Solicitação de par: {request.request_id} ({request.node_a} <-> {request.node_b})")
    try:
        if not timeline:
            raise HTTPException(status_code=500, detail="Simulador não inicializado.")
            
        req_a = _norm_name(request.node_a)
        req_b = _norm_name(request.node_b)
        
        if req_a not in node_map or req_b not in node_map:
            raise HTTPException(status_code=404, detail="Nós não encontrados na topologia.")
            
        node_a_obj = node_map[req_a]
        
        # Cria e inicia o protocolo
        protocol = ApiEntanglementProtocol(
            owner=node_a_obj, 
            name=f"proto_{request.request_id}",
            request_id=request.request_id, 
            node_a_name=req_a, 
            node_b_name=req_b
        )
        
        # Tenta iniciar na timeline ou fallback
        if hasattr(protocol, "start"):
            protocol.start()
            NEW_EVENT_SEMAPHORE.set()
        else:
            threading.Thread(
                target=_simulate_pair_fallback,
                args=(request.request_id, req_a, req_b),
                daemon=True
            ).start()
            
        return {"status": "started", "request_id": request.request_id}
        
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")

# sequence/app.py (final do arquivo)
if __name__ == "__main__":
    print("Iniciando Sequence Simulator (SDN-Enabled) na porta 8004...")
    # Para habilitar reload/workers, passe o application import string
    uvicorn.run(app, host="0.0.0.0", port=8004, )