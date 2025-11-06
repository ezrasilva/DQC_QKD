import uvicorn
import sys  # <--- ADICIONADO
import requests # <--- ADICIONADO
import json # <--- ADICIONADO
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator
from qiskit.compiler import transpile
from cryptography.exceptions import InvalidTag # <--- ADICIONADO

# --- Importações locais ---
from secure_channel import encrypt_aes_gcm, decrypt_aes_gcm # <--- ADICIONADO

# --- Modelos de Dados (Pydantic) ---

class ExecuteSliceRequest(BaseModel):
    circuit_qasm: str
    slice_id: str = "default-slice"
    shots: int = 1024
    key_id: Optional[str] = None
    key_hex: Optional[str] = None

class PairReadyRequest(BaseModel):
    pair_id: str
    fidelity: float = 1.0

# --- NOVOS MODELOS (Passo 8) ---
class SecureMessage(BaseModel):
    """Payload para receber uma mensagem cifrada (o que Bob recebe)"""
    key_id: str
    nonce_hex: str
    ciphertext_hex: str

class FeedforwardRequest(BaseModel):
    """Payload para enviar uma mensagem cifrada (o que Alice envia)"""
    key_id: str
    message: str # Ex: "01" (bits de medição)
# --- FIM DOS NOVOS MODELOS ---


# --- Configuração da Aplicação ---

app = FastAPI(
    title="QPU Node",
    description="Microsserviço que simula um nó de QPU (Alice ou Bob).",
    version="0.2.0"
)

try:
    simulator = AerSimulator()
    print("[QNode] AerSimulator carregado com sucesso.")
except Exception as e:
    print(f"[QNode] Erro ao carregar AerSimulator: {e}")
    simulator = None

# --- Armazenamento em Memória (Passo 8) ---
# Armazena as chaves que este nó recebe do Orchestrator
NODE_KEYS: Dict[str, str] = {}

# Topologia de outros nós (para onde enviar feed-forward)
TOPOLOGY_MAP: Dict[str, str] = {
    "alice": "http://127.0.0.1:8001",
    "bob": "http://127.0.0.1:8002"
}
# --- FIM DO ARMAZENAMENTO ---


# --- Endpoints da API ---

@app.get("/", summary="Health Check")
def read_root():
    """Endpoint básico para verificar se o serviço está online."""
    return {"status": "QPU Node está online", "keys": list(NODE_KEYS.keys())}

@app.post("/execute_slice", summary="Executa um 'slice' de circuito")
def execute_slice(request: ExecuteSliceRequest):
    """
    Recebe um circuito quântico (em OpenQASM), executa no simulador
    e armazena a chave QKD associada.
    """
    if simulator is None:
        raise HTTPException(status_code=500, detail="AerSimulator não está disponível.")

    print(f"[QNode] Recebido job para slice: {request.slice_id}")

    # --- LÓGICA ATUALIZADA (Passo 8) ---
    if request.key_id and request.key_hex:
        print(f"  - Job protegido. Armazenando Key ID: {request.key_id}")
        NODE_KEYS[request.key_id] = request.key_hex
    else:
        print("  - AVISO: Job não protegido (sem Key ID).")
    # --- FIM DA ATUALIZAÇÃO ---

    try:
        qc = QuantumCircuit.from_qasm_str(request.circuit_qasm)
        transpiled_qc = transpile(qc, simulator)
        result = simulator.run(transpiled_qc, shots=request.shots).result()
        counts = result.get_counts(qc)
        
        print(f"[QNode] Execução concluída. Counts: {counts}")
        
        return {"status": "success", "slice_id": request.slice_id, "counts": counts, "key_stored": (request.key_id is not None)}
    except Exception as e:
        print(f"[QNode] Erro ao executar o circuito: {e}")
        raise HTTPException(status_code=400, detail=f"Erro no processamento do QASM: {str(e)}")

@app.post("/pair_ready", summary="Recebe notificação de par entrelaçado")
def pair_ready(request: PairReadyRequest):
    print(f"[QNode] Notificação recebida: Par {request.pair_id} está pronto com fidelidade {request.fidelity}.")
    return {"status": "received", "pair_id": request.pair_id}

# --- NOVOS ENDPOINTS (Passo 8) ---

@app.post("/receive_secure_message", summary="[Bob] Recebe e decifra uma mensagem segura")
def receive_secure_message(request: SecureMessage):
    """
    Este é o endpoint que 'Bob' expõe.
    Ele recebe uma mensagem cifrada, busca a chave QKD e a decifra.
    """
    print(f"\n[QNode] Mensagem segura recebida (Key ID: {request.key_id})")
    
    # 1. Buscar a chave
    if request.key_id not in NODE_KEYS:
        print("  - ERRO: Key ID não encontrada. Mensagem rejeitada.")
        raise HTTPException(status_code=404, detail="Key ID não encontrada.")
    
    key_hex = NODE_KEYS[request.key_id]
    
    # 2. Tentar decifrar
    try:
        decrypted_data = decrypt_aes_gcm(key_hex, request.nonce_hex, request.ciphertext_hex)
        
        print(f"  - SUCESSO: Mensagem decifrada: {decrypted_data}")
        return {"status": "decryption_success", "data": decrypted_data}
        
    except InvalidTag:
        print("  - ERRO: Falha na decifragem (InvalidTag). Mensagem rejeitada.")
        raise HTTPException(status_code=403, detail="Falha na autenticação da mensagem (InvalidTag)")
    except Exception as e:
        print(f"  - ERRO: {e}")
        raise HTTPException(status_code=500, detail=f"Erro na decifragem: {e}")

@app.post("/send_feedforward/{node_name}", summary="[Alice] Cifra e envia uma mensagem (teste)")
def send_feedforward(node_name: str, request: FeedforwardRequest):
    """
    Este é o endpoint que 'Alice' usa para enviar dados.
    Ele cifra dados e os envia para /receive_secure_message de outro nó.
    """
    print(f"\n[QNode] Solicitado envio de feed-forward para: {node_name}")
    
    # 1. Validar destino
    if node_name not in TOPOLOGY_MAP:
        raise HTTPException(status_code=404, detail=f"Nó '{node_name}' desconhecido na topologia.")
    
    target_url = f"{TOPOLOGY_MAP[node_name]}/receive_secure_message"
    
    # 2. Buscar a chave
    if request.key_id not in NODE_KEYS:
        raise HTTPException(status_code=404, detail="Key ID não encontrada.")
    
    key_hex = NODE_KEYS[request.key_id]
    
    # 3. Preparar e cifrar
    plaintext_data = {"m_bits": request.message, "from_node": "alice"} # (Exemplo de m_bits)
    print(f"  - Cifrando dados: {plaintext_data}")
    
    try:
        encrypted_payload = encrypt_aes_gcm(key_hex, plaintext_data)
        
        # 4. Preparar payload final (incluindo o Key ID)
        final_payload = {
            "key_id": request.key_id,
            "nonce_hex": encrypted_payload["nonce_hex"],
            "ciphertext_hex": encrypted_payload["ciphertext_hex"]
        }
        
        # 5. Enviar a mensagem cifrada
        print(f"  - Enviando mensagem cifrada para {target_url}...")
        response = requests.post(target_url, json=final_payload, timeout=5)
        
        if response.status_code == 200:
            print("  - SUCESSO: 'Bob' confirmou recebimento e decifragem.")
            return {"status": "send_success", "response_from_bob": response.json()}
        else:
            print(f"  - ERRO: 'Bob' rejeitou a mensagem (Status: {response.status_code})")
            raise HTTPException(status_code=500, detail=f"Bob rejeitou a mensagem: {response.text}")

    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=500, detail=f"Falha ao conectar em 'Bob' ({target_url})")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro no envio: {e}")

# --- FIM DOS NOVOS ENDPOINTS ---


# --- Execução do Servidor (ATUALIZADO) ---

if __name__ == "__main__":
    """
    Permite rodar o servidor diretamente com 'python app.py [porta]'
    """
    # Define a porta padrão
    port = 8001
    
    # Permite sobrescrever a porta via argumento de linha de comando
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            print(f"AVISO: Argumento de porta '{sys.argv[1]}' inválido. Usando porta padrão {port}.")
            
    print(f"Iniciando servidor QNode na porta {port}...")
    uvicorn.run(app, host="0.0.0.0", port=port)