# orchestrator/app.py
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
import logging
# Importa o controlador corretamente
from orchestrator.controller import SDNController

app = FastAPI(title="SDN Quantum Controller", version="1.1-Fixed")
logger = logging.getLogger("sdn-gateway")

# Instancia o cérebro
sdn_controller = SDNController()

class QuantumJobRequest(BaseModel):
    job_id: str
    circuit_qasm: str
    shots: int = 1024

@app.post("/submit_job")
def submit_job(req: QuantumJobRequest):
    """
    Recebe o circuito COMPLETO. O Controller decide como fatiar e onde rodar.
    """
    logger.info(f"Recebido Job {req.job_id}")
    try:
        # 1. O Controller cria o plano (HDH + Rede)
        plan = sdn_controller.plan_execution(req.circuit_qasm)
        
        # 2. O Controller executa
        results = sdn_controller.execute_plan(req.job_id, plan, req.shots)
        
        # --- AQUI ESTAVA O PROBLEMA ---
        # Retornamos o plano completo para o teste poder validar
        return {
            "status": "success",
            "job_id": req.job_id,
            "execution_plan": plan,  # <--- ESSA CHAVE É OBRIGATÓRIA PARA O TESTE
            "results": results
        }
    except Exception as e:
        logger.error(f"Erro fatal no Job {req.job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
def health():
    return {"status": "online", "mode": "SDN-Controller"}