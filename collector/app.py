import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, Dict, List

# --- Modelos de Dados ---
class MetricRequest(BaseModel):
    name: str
    value: float
    tags: Optional[Dict[str, str]] = None

# --- ATUALIZAÇÃO (Passo 10) ---
# Armazenamento em memória para as métricas
METRIC_STORE: List[MetricRequest] = []
# --- FIM DA ATUALIZAÇÃO ---

# --- Configuração da Aplicação ---
app = FastAPI(
    title="Collector Service",
    description="Recebe e registra métricas de outros serviços.",
    version="0.2.0" # <- Versão atualizada
)

@app.get("/", summary="Health Check")
def read_root():
    return {"status": "Collector está online"}

# --- ATUALIZAÇÃO (Passo 10) ---
@app.post("/submit_metric", summary="Recebe uma nova métrica")
def submit_metric(metric: MetricRequest):
    """
    Recebe uma métrica e a armazena na lista METRIC_STORE.
    """
    print(f"[Collector] Métrica recebida: {metric.name}={metric.value}, Tags={metric.tags}")
    
    # Armazena a métrica
    METRIC_STORE.append(metric)
    
    return {"status": "metric_received", "metric_name": metric.name}

@app.get("/metrics", response_model=List[MetricRequest], summary="Retorna todas as métricas coletadas")
def get_metrics():
    """
    Endpoint de depuração para ver todas as métricas armazenadas.
    """
    return METRIC_STORE

@app.delete("/metrics", summary="Limpa o cache de métricas")
def clear_metrics():
    """
    Limpa o armazenamento de métricas (útil entre testes).
    """
    print("[Collector] Limpando o armazenamento de métricas.")
    METRIC_STORE.clear()
    return {"status": "metrics_cleared"}
# --- FIM DA ATUALIZAÇÃO ---

# --- Execução do Servidor ---
if __name__ == "__main__":
    print("Iniciando servidor Collector na porta 8006...")
    uvicorn.run(app, host="0.0.0.0", port=8006)