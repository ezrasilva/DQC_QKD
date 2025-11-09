#!/usr/bin/env python3
# qnode/app.py
"""
Quantum Node (QNode) - race-condition safe version

Key behaviors implemented here to mitigate race conditions / correlation by slice_id:
- All executions must either provide a pair_id (preferred) or provide fidelity explicitly.
- If pair_id is provided, the QNode will look up fidelity from the PAIR_STORE and will *not*
  use LAST_PAIR_FIDELITY. If fidelity is also provided in the request and pair_id is present,
  the provided fidelity will be stored/updated for that pair_id (explicit override).
- Writes/reads to PAIR_STORE are protected by a threading.Lock to avoid concurrent corruption.
- PAIR_STORE is persisted to disk atomically (via temp file + rename) to survive restarts.
- /pair_ready endpoint stores pair metadata into PAIR_STORE (thread-safe + persisted).
- /execute_slice validates presence of pair_id/fidelity and returns clear error if missing.
- Uses qiskit_aer (AerSimulator + noise model helper) and cryptography AES-GCM for message ops.
- No key material (key_hex) is persisted by this service.
- Can be run directly: `python qnode/app.py [port]`.
"""

from typing import Optional, Dict, Any
import os
import sys
import json
import time
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

# Qiskit (modular) and noise
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, depolarizing_error

# cryptography for AES-GCM
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.backends import default_backend

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
APP_VERSION = "qnode-0.3-safe"
PERSIST_PATH = os.environ.get("QNODE_PAIR_STORE_PATH", "/data/pair_store.json")
PERSIST_TMP = str(Path(PERSIST_PATH).with_suffix(".tmp"))
os.makedirs(os.path.dirname(PERSIST_PATH), exist_ok=True)

# -----------------------------------------------------------------------------
# App and concurrency primitives
# -----------------------------------------------------------------------------
app = FastAPI(title="Quantum Node (QNode) - race-safe", version=APP_VERSION)
PAIR_STORE: Dict[str, float] = {}         # pair_id -> fidelity
PAIR_STORE_LOCK = threading.Lock()       # guard for reads/writes
# LAST_PAIR_FIDELITY kept for backward compat only; _not_ used for execution decisions
LAST_PAIR_FIDELITY: Optional[float] = None

# -----------------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------------
class ExecuteSliceRequest(BaseModel):
    circuit_qasm: str
    slice_id: str = "default-slice"
    shots: int = 1024
    key_id: Optional[str] = None
    key_hex: Optional[str] = None
    # Prefer pair_id to correlate fidelity; if provided, QNode will use PAIR_STORE[pair_id]
    pair_id: Optional[str] = None
    # Optional override; if pair_id provided and fidelity included, it will update stored fidelity for that pair_id
    fidelity: Optional[float] = None


class PairReadyRequest(BaseModel):
    pair_id: str
    fidelity: float
    timestamp: Optional[float] = None

# -----------------------------------------------------------------------------
# Helpers: persistence, locking, noise model
# -----------------------------------------------------------------------------
def persist_pair_store_atomic():
    """Persist PAIR_STORE atomically (write temp file then rename)."""
    try:
        tmp = PERSIST_TMP
        with open(tmp, "w", encoding="utf-8") as f:
            # Only persist pair_id -> fidelity (no sensitive data)
            with PAIR_STORE_LOCK:
                json.dump(PAIR_STORE, f)
        os.replace(tmp, PERSIST_PATH)
    except Exception as e:
        # non-fatal: log to stdout
        print(f"[QNode] Erro ao persistir PAIR_STORE: {e}", file=sys.stderr)


def load_pair_store():
    """Load PAIR_STORE from PERSIST_PATH (if exists)."""
    global PAIR_STORE, LAST_PAIR_FIDELITY
    try:
        if os.path.exists(PERSIST_PATH):
            with open(PERSIST_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    with PAIR_STORE_LOCK:
                        # ensure values are floats
                        PAIR_STORE = {str(k): float(v) for k, v in data.items()}
                        if PAIR_STORE:
                            # set LAST_PAIR_FIDELITY to last inserted value as fallback only
                            LAST_PAIR_FIDELITY = list(PAIR_STORE.values())[-1]
                            print(f"[QNode] Loaded PAIR_STORE ({len(PAIR_STORE)} entries), last fidelity={LAST_PAIR_FIDELITY}")
    except Exception as e:
        print(f"[QNode] Erro ao carregar PAIR_STORE: {e}", file=sys.stderr)


def noise_model_from_fidelity(fidelity: float) -> NoiseModel:
    """Simple depolarizing noise model derived from fidelity (approx): error_rate = 1 - fidelity"""
    if fidelity is None:
        return None
    error_rate = max(0.0, min(1.0, 1.0 - float(fidelity)))
    nm = NoiseModel()
    if error_rate == 0.0:
        return nm
    single_q_err = depolarizing_error(error_rate, 1)
    two_q_err = depolarizing_error(error_rate, 2)
    nm.add_all_qubit_quantum_error(single_q_err, ['u3', 'u2', 'u1', 'id', 'reset'])
    nm.add_all_qubit_quantum_error(two_q_err, ['cx', 'cz'])
    return nm

# -----------------------------------------------------------------------------
# Crypto helpers (AES-GCM using cryptography)
# -----------------------------------------------------------------------------
def encrypt_message(message: str, key_hex: str) -> Dict[str, str]:
    key = bytes.fromhex(key_hex)
    if len(key) not in (16, 24, 32):
        raise ValueError("AES key must be 128/192/256 bits (hex length 32/48/64).")
    iv = os.urandom(12)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(iv), backend=default_backend()).encryptor()
    ct = encryptor.update(message.encode()) + encryptor.finalize()
    return {"nonce": iv.hex(), "ciphertext": ct.hex(), "tag": encryptor.tag.hex()}


def decrypt_message(ciphertext_hex: str, nonce_hex: str, tag_hex: str, key_hex: str) -> str:
    key = bytes.fromhex(key_hex)
    iv = bytes.fromhex(nonce_hex)
    tag = bytes.fromhex(tag_hex)
    ct = bytes.fromhex(ciphertext_hex)
    decryptor = Cipher(algorithms.AES(key), modes.GCM(iv, tag), backend=default_backend()).decryptor()
    plaintext = decryptor.update(ct) + decryptor.finalize()
    return plaintext.decode()

# -----------------------------------------------------------------------------
# Endpoints
# -----------------------------------------------------------------------------
@app.get("/health")
def health():
    with PAIR_STORE_LOCK:
        count = len(PAIR_STORE)
    return {"status": "ok", "component": "qnode", "pair_store_entries": count}


@app.post("/pair_ready")
def pair_ready(request: PairReadyRequest):
    """
    Callback from Sequence (or orchestrator) indicating a pair is ready with fidelity.
    This stores the fidelity under pair_id in a thread-safe way and persists the store.
    """
    global LAST_PAIR_FIDELITY
    try:
        fid = float(request.fidelity)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid fidelity value")

    with PAIR_STORE_LOCK:
        PAIR_STORE[str(request.pair_id)] = fid
        LAST_PAIR_FIDELITY = fid
    # persist (best-effort)
    try:
        persist_pair_store_atomic()
    except Exception:
        pass
    print(f"[QNode] pair_ready: stored pair_id={request.pair_id} fidelity={fid}")
    return {"status": "received", "pair_id": request.pair_id, "fidelity": fid}


@app.post("/execute_slice")
def execute_slice(request: ExecuteSliceRequest):
    """
    Execute a provided circuit slice in a race-condition safe manner.
    Rules:
    - If pair_id is provided:
        - If fidelity is provided, it overrides/stores the fidelity for that pair_id (explicit override).
        - Otherwise: fidelity will be looked up from PAIR_STORE[pair_id]. If missing -> 400 error.
    - If pair_id is NOT provided:
        - fidelity must be provided in the request (otherwise -> 400 error).
    - The effective fidelity used to build the noise model is returned in the response.
    """
    # parse circuit
    try:
        qc = QuantumCircuit.from_qasm_str(request.circuit_qasm)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid QASM: {e}")

    # Determine effective fidelity following strict, race-safe rules
    effective_fidelity: Optional[float] = None

    if request.pair_id:
        pid = str(request.pair_id)
        # If client provided fidelity explicitly with a pair_id, trust and store it (explicit override)
        if request.fidelity is not None:
            try:
                fid = float(request.fidelity)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid fidelity value")
            with PAIR_STORE_LOCK:
                PAIR_STORE[pid] = fid
            effective_fidelity = fid
            # persist update
            try:
                persist_pair_store_atomic()
            except Exception:
                pass
        else:
            # No fidelity explicitly given: must be present in PAIR_STORE
            with PAIR_STORE_LOCK:
                if pid in PAIR_STORE:
                    effective_fidelity = float(PAIR_STORE[pid])
                else:
                    # Explicit failure: missing mapping for provided pair_id
                    raise HTTPException(status_code=400, detail=f"Unknown pair_id '{pid}'. Provide fidelity or ensure /pair_ready was called.")
    else:
        # No pair_id provided: require fidelity explicitly
        if request.fidelity is None:
            raise HTTPException(status_code=400, detail="Either pair_id (with known mapping) or fidelity must be provided.")
        try:
            effective_fidelity = float(request.fidelity)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid fidelity value")

    # Build simulator and run with noise model derived from effective_fidelity
    try:
        nm = noise_model_from_fidelity(effective_fidelity) if effective_fidelity is not None else None
        if nm is not None:
            sim = AerSimulator(noise_model=nm)
            transpiled_qc = transpile(qc, sim)
            job = sim.run(transpiled_qc, shots=request.shots)
            result = job.result()
        else:
            sim = AerSimulator()
            transpiled_qc = transpile(qc, sim)
            job = sim.run(transpiled_qc, shots=request.shots)
            result = job.result()
    except Exception as e:
        print(f"[QNode] Execution error: {e}", file=sys.stderr)
        raise HTTPException(status_code=500, detail=f"Execution failed: {e}")

    counts = result.get_counts()
    print(f"[QNode] execute_slice: slice={request.slice_id} pair_id={request.pair_id} fidelity={effective_fidelity} counts={counts}")

    # Return counts and effective fidelity (do NOT return key_hex)
    return {"slice_id": request.slice_id, "counts": counts, "fidelity": effective_fidelity}


@app.post("/decrypt_message")
def decrypt_endpoint(payload: Dict[str, str]):
    """
    Decrypt helper: expects {ciphertext, nonce, tag, key_hex}.
    Use only for debugging or for authenticated classical messages.
    """
    try:
        plaintext = decrypt_message(payload["ciphertext"], payload["nonce"], payload["tag"], payload["key_hex"])
        print(f"[QNode] Mensagem descriptografada: {plaintext}")
        return {"plaintext": plaintext}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# -----------------------------------------------------------------------------
# Startup behavior
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    # Load persisted pair store if present before starting server
    try:
        load_pair_store()
    except Exception:
        pass

    # Allow running directly: python qnode/app.py [port]
    import uvicorn
    port = 8003
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except Exception:
            print(f"[QNode] Invalid port arg '{sys.argv[1]}', using default {port}")
    print(f"[QNode] Starting on port {port} (race-safe mode)...")
    uvicorn.run("qnode.app:app", host="0.0.0.0", port=port, log_level="info")
