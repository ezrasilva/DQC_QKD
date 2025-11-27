from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, Dict
import logging
from orchestrator.controller import SDNController

app = FastAPI(title="SDN Quantum Controller", version="1.0-HDH")
logger = logging.getLogger("sdn-controller")

sdn_controller = SDNController()

class QuantumJobRequest(BaseModel):
    job_id: str
    circuit_qasm: str  
    shots: int = 1024

@app.post("/submit_job")
def submit_job(req: QuantumJobRequest, background_tasks: BackgroundTasks):
    """
    Recebe um circuito completo, particiona via HDH e executa na rede.
    """
    try:
        # 1. Análise e Particionamento (Camada HDH)
        # O controller vai retornar um plano de execução: 
        # Ex: [{'node': 'alice', 'slice': '...'}, {'node': 'bob', 'slice': '...'}]
        execution_plan = sdn_controller.plan_execution(req.circuit_qasm)
        
        # 2. Execução (Camada de Dados)
        # Despacha as fatias conforme o plano
        results = sdn_controller.execute_plan(req.job_id, execution_plan, req.shots)
        
        return {
            "status": "completed", 
            "job_id": req.job_id,
            "plan_summary": f"Dividido em {len(execution_plan)} fatias",
            "results": results
        }
    except Exception as e:
        logger.error(f"Erro no job {req.job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Health Check simples
@app.get("/health")
def health():
    return {"status": "online", "mode": "SDN-Controller-Option-A"}