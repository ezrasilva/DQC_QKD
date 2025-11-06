import uvicorn
import uuid
import secrets
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List

# --- Modelos de Dados (Pydantic) - Simulação da API ETSI QKD 014 ---

class GetKeyRequest(BaseModel):
    """
    Modelo simplificado para o corpo de POST /qkd/v1/keys
    """
    client_a: str
    client_b: str
    key_type: str = "session"
    key_length: int = 256 # Comprimento da chave em bits

class KeyObject(BaseModel):
    """
    Modelo simplificado para a resposta
    """
    key_id: str
    key_data_hex: str # A chave real, em hexadecimal

class GetKeyResponse(BaseModel):
    """
    Resposta do endpoint /qkd/v1/keys
    """
    status: str
    keys: List[KeyObject]


# --- Configuração da Aplicação ---

app = FastAPI(
    title="QKDN Stub (Quditto Demo)",
    description="Um 'stub' que simula a API ETSI QKD 014 para fornecimento de chaves.",
    version="0.1.0"
)

# --- Banco de Dados de Chaves (Em Memória) ---
# Em uma implementação real, o Quditto gerenciaria isso.
# Aqui, apenas armazenamos para fins de log.
KEY_STORE = {}

# --- Endpoints da API ---

@app.get("/", summary="Health Check")
def read_root():
    """Endpoint básico para verificar se o serviço está online."""
    return {"status": "QKDN Stub (ETSI 014) está online"}

@app.post("/qkd/v1/keys", response_model=GetKeyResponse, summary="Solicita chaves QKD (ETSI 014)")
def get_key(request: GetKeyRequest):
    """
    Simula o endpoint ETSI QKD 014 para solicitar uma chave.
    
    Recebe um pedido e retorna imediatamente uma chave falsa gerada
    e um ID de chave.
    """
    print(f"\n[QKDN Stub] Recebida solicitação de chave de {request.key_length}-bits entre: {request.client_a} <-> {request.client_b}")
    
    try:
        # 1. Gera um ID de chave único
        key_id = str(uuid.uuid4())
        
        # 2. Gera uma chave criptográfica falsa
        #    (dividimos por 8 para converter bits em bytes)
        key_bytes_len = request.key_length // 8
        key_bytes = secrets.token_bytes(key_bytes_len)
        
        # 3. Converte a chave para hexadecimal (como é comumente enviada)
        key_hex = key_bytes.hex()
        
        print(f"  - Gerada Key ID: {key_id}")
        print(f"  - Gerada Chave (hex): {key_hex[:10]}... (Total: {len(key_hex)} hex chars)")

        # 4. Cria o objeto de resposta
        key_obj = KeyObject(key_id=key_id, key_data_hex=key_hex)
        
        # 5. Armazena no nosso "banco de dados" falso
        KEY_STORE[key_id] = key_obj
        
        # 6. Retorna a resposta no formato ETSI
        return GetKeyResponse(status="success", keys=[key_obj])
        
    except Exception as e:
        print(f"[QKDN Stub] Erro ao gerar chave: {e}")
        raise HTTPException(status_code=500, detail=f"Erro interno do QKDN: {e}")

@app.get("/qkd/v1/keys/{key_id}", summary="Obtém uma chave específica (Não implementado)")
def get_key_by_id(key_id: str):
    """
    Simula a obtenção de uma chave que já foi gerada.
    """
    if key_id in KEY_STORE:
        return {"status": "success", "key": KEY_STORE[key_id]}
    raise HTTPException(status_code=404, detail="Key ID não encontrado")

# --- Execução do Servidor ---

if __name__ == "__main__":
    """
    Permite rodar o servidor diretamente com 'python app.py'
    """
    print("Iniciando servidor QKDN Stub (ETSI 014) na porta 8005...")
    uvicorn.run(app, host="0.0.0.0", port=8005)