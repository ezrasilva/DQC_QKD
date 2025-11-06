import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator
from qiskit.compiler import transpile

# --- Modelos de Dados (Pydantic) ---
# Define como devem ser os dados recebidos nas requisições

class ExecuteSliceRequest(BaseModel):
    """
    Define a estrutura esperada para executar um 'slice' de circuito.
    O circuito é recebido como uma string no formato OpenQASM 2.0.
    """
    circuit_qasm: str
    slice_id: str = "default-slice"
    shots: int = 1024

class PairReadyRequest(BaseModel):
    """
    Define a notificação de que um par entrelaçado está pronto.
    """
    pair_id: str
    fidelity: float = 1.0


# --- Configuração da Aplicação ---

app = FastAPI(
    title="QPU Node (Alice)",
    description="Um microsserviço que simula um nó de QPU (Quantum Processing Unit).",
    version="0.1.0"
)

# Inicializa o simulador uma vez
# Usamos o AerSimulator, que é um backend de simulação de alta performance.
try:
    simulator = AerSimulator()
    print("[QNode] AerSimulator carregado com sucesso.")
except Exception as e:
    print(f"[QNode] Erro ao carregar AerSimulator: {e}")
    simulator = None

# --- Endpoints da API ---

@app.get("/", summary="Health Check")
def read_root():
    """Endpoint básico para verificar se o serviço está online."""
    return {"status": "QPU Node (Alice) está online"}

@app.post("/execute_slice", summary="Executa um 'slice' de circuito")
def execute_slice(request: ExecuteSliceRequest):
    """
    Recebe um circuito quântico (em OpenQASM), executa no simulador
    e retorna os 'counts' (resultados da medição).
    """
    if simulator is None:
        raise HTTPException(status_code=500, detail="AerSimulator não está disponível.")

    print(f"[QNode] Recebido job para slice: {request.slice_id}")

    try:
        # 1. Carrega o circuito a partir da string QASM
        qc = QuantumCircuit.from_qasm_str(request.circuit_qasm)
        
        # 2. Transpila o circuito para o backend (simulador)
        # Isso otimiza o circuito para a arquitetura do backend.
        transpiled_qc = transpile(qc, simulator)

        # 3. Executa o circuito
        result = simulator.run(transpiled_qc, shots=request.shots).result()
        
        # 4. Obtém os resultados (contagens)
        counts = result.get_counts(qc)
        
        print(f"[QNode] Execução concluída. Counts: {counts}")

        # Para este PoC, retornamos os counts diretamente.
        # No Passo 9, este endpoint faria o QNode *enviar* os resultados
        # para o serviço 'Collector'.
        return {"status": "success", "slice_id": request.slice_id, "counts": counts}

    except Exception as e:
        print(f"[QNode] Erro ao executar o circuito: {e}")
        raise HTTPException(status_code=400, detail=f"Erro no processamento do QASM: {str(e)}")

@app.post("/pair_ready", summary="Recebe notificação de par entrelaçado")
def pair_ready(request: PairReadyRequest):
    """
    Endpoint de 'callback' que o PairQueue (ou SeQUeNCe)
    chamará quando um par entrelaçado estiver pronto.
    """
    # Por enquanto, apenas registramos a notificação.
    # Em uma implementação real, armazenaríamos isso em uma fila interna.
    print(f"[QNode] Notificação recebida: Par {request.pair_id} está pronto com fidelidade {request.fidelity}.")
    
    return {"status": "received", "pair_id": request.pair_id}


# --- Execução do Servidor ---

if __name__ == "__main__":
    """
    Permite rodar o servidor diretamente com 'python app.py'
    """
    print("Iniciando servidor QNode (Alice) na porta 8001...")
    uvicorn.run(app, host="0.0.0.0", port=8001)