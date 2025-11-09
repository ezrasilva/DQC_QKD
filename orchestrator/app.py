#!/usr/bin/env python3
# orchestrator/app.py
"""
Orchestrator (FastAPI) - complete implementation ready to paste into your repo.

Behavior & guarantees implemented here:
- Requests an EPR pair from SeQUeNCe (synchronous POST /create_pair) when needed.
- Requests key material from Quditto nodes using enc_keys / dec_keys (ETSI-like API).
- Always dispatches to QNode with explicit pair_id AND fidelity (no reliance on LAST_* globals).
- Records a mapping to the Collector (CSV-based) *without* storing key material (key_hex NOT stored).
- Uses BackgroundTasks for non-blocking recording operations.
- Clear environment variable configuration, timeouts and retries.
- No file modifications are performed by this snippet — copy/paste to your repo.

Notes:
- This code assumes the Sequence service responds synchronously to create_pair with pair_id and fidelity.
  If your Sequence is callback-based, replace request_pair_from_sequence with polling or a callback handler.
- Do not log or persist `key_hex` to Collector. The QNode receives it over HTTPS/HTTP as needed.
"""

from typing import Optional, Dict, Any
import os
import time
import logging
import json
import requests

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel

# ------------------------------------------------------------
# Configuration (override via environment variables)
# ------------------------------------------------------------
QUDITTO_NODE_A_URL = os.environ.get("QUDITTO_NODE_A_URL", "http://quditto-node-a:8000")
QUDITTO_NODE_B_URL = os.environ.get("QUDITTO_NODE_B_URL", "http://quditto-node-b:8000")

# The "logical id" of the QNodes as known to Quditto when requesting keys.
# Example: "alice-qnode" and "bob-qnode" — these are the identifiers used in Quditto URLs.
DEFAULT_QNODE_TARGET_A_ID = os.environ.get("QNODE_TARGET_A_ID", "alice-qnode")
DEFAULT_QNODE_TARGET_B_ID = os.environ.get("QNODE_TARGET_B_ID", "bob-qnode")

SEQUENCE_URL = os.environ.get("SEQUENCE_URL", "http://sequence:8004")
DEFAULT_QNODE_URL = os.environ.get("QNODE_URL", "http://qnode:8003")
COLLECTOR_URL = os.environ.get("COLLECTOR_URL", "http://collector:8006")

REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "10"))      # seconds
RETRY_COUNT = int(os.environ.get("RETRY_COUNT", "3"))
RETRY_BACKOFF = float(os.environ.get("RETRY_BACKOFF", "0.5"))      # seconds

# Logging
logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("orchestrator")

# ------------------------------------------------------------
# FastAPI app
# ------------------------------------------------------------
app = FastAPI(title="DQC Orchestrator (Quditto + Sequence + QNode)", version="0.3")


# ------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------
class CreatePairRequest(BaseModel):
    node_a: str
    node_b: str
    params: Optional[Dict[str, Any]] = None


class ExecuteSliceRequest(BaseModel):
    circuit_qasm: str
    slice_id: str
    shots: int = 1024

    # Which Quditto nodes (base URLs) to use for key exchange (optional overrides)
    quditto_node_a: Optional[str] = None
    quditto_node_b: Optional[str] = None

    # Logical identifiers of the quantum nodes as registered in Quditto (these are NOT HTTP URLs)
    qnode_target_a_id: Optional[str] = None
    qnode_target_b_id: Optional[str] = None

    # The QNode (execution target) HTTP URL (optional override)
    qnode_url: Optional[str] = None

    # Optional: if caller already has pair info, it can provide them
    pair_id: Optional[str] = None
    fidelity: Optional[float] = None


# ------------------------------------------------------------
# Utilities: HTTP helpers with simple retries
# ------------------------------------------------------------
def http_get_with_retries(url: str, params: dict = None, timeout: int = REQUEST_TIMEOUT) -> requests.Response:
    last_exc = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            logger.warning("GET failed: %s (attempt %d/%d): %s", url, attempt, RETRY_COUNT, e)
            time.sleep(RETRY_BACKOFF * attempt)
    raise last_exc


def http_post_with_retries(url: str, json_payload: dict = None, timeout: int = REQUEST_TIMEOUT) -> requests.Response:
    last_exc = None
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.post(url, json=json_payload, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as e:
            last_exc = e
            logger.warning("POST failed: %s (attempt %d/%d): %s", url, attempt, RETRY_COUNT, e)
            time.sleep(RETRY_BACKOFF * attempt)
    raise last_exc


# ------------------------------------------------------------
# Quditto helpers (enc_keys / dec_keys style)
# ------------------------------------------------------------
def request_quditto_enc_keys(quditto_base_url: str, target_node_id: str, size_bits: int = 512) -> Dict[str, Optional[Any]]:
    """
    Calls: GET {quditto_base_url}/api/v1/keys/{target_node_id}/enc_keys?size={size_bits}
    Returns: {"key_id": str|None, "key_hex": str|None, "raw": dict}
    """
    url = f"{quditto_base_url.rstrip('/')}/api/v1/keys/{target_node_id}/enc_keys"
    params = {"size": int(size_bits)}
    logger.info("Requesting enc_keys from Quditto %s for target %s (size=%d)", quditto_base_url, target_node_id, size_bits)
    resp = http_get_with_retries(url, params=params)
    data = resp.json()
    # heuristic field extraction (Quditto deployments vary)
    key_id = data.get("key_id") or data.get("keyID") or data.get("id") or data.get("keyId")
    key_hex = None
    for cand in ("key_material", "key_material_hex", "key_hex", "key", "key_data", "material"):
        if cand in data:
            key_hex = data[cand]
            break
    if not key_hex and isinstance(data.get("data"), dict):
        for cand in ("key_material", "key_hex", "key"):
            if cand in data["data"]:
                key_hex = data["data"][cand]
                break
    return {"key_id": key_id, "key_hex": key_hex, "raw": data}


def request_quditto_dec_keys(quditto_base_url: str, other_node_id: str, key_id: str) -> Dict[str, Optional[Any]]:
    """
    Calls: GET {quditto_base_url}/api/v1/keys/{other_node_id}/dec_keys?key_ID={key_id}
    Returns: {"key_id": key_id, "key_hex": str|None, "raw": dict}
    """
    url = f"{quditto_base_url.rstrip('/')}/api/v1/keys/{other_node_id}/dec_keys"
    params = {"key_ID": key_id}
    logger.info("Requesting dec_keys from Quditto %s for key_id %s", quditto_base_url, key_id)
    resp = http_get_with_retries(url, params=params)
    data = resp.json()
    key_hex = None
    for cand in ("key_material", "key_hex", "key", "key_data", "material"):
        if cand in data:
            key_hex = data[cand]
            break
    if not key_hex and isinstance(data.get("data"), dict):
        for cand in ("key_material", "key_hex", "key"):
            if cand in data["data"]:
                key_hex = data["data"][cand]
                break
    return {"key_id": key_id, "key_hex": key_hex, "raw": data}


# ------------------------------------------------------------
# Sequence (pair creation) helper
# ------------------------------------------------------------
def request_pair_from_sequence(node_a_id: str, node_b_id: str, params: Dict[str, Any] = None) -> (str, Optional[float], dict):
    """
    Synchronous call to Sequence to create an EPR pair.
    POST {SEQUENCE_URL}/create_pair with payload {"nodes":[node_a_id,node_b_id], ...}
    Expects JSON response containing pair_id and fidelity.
    """
    url = f"{SEQUENCE_URL.rstrip('/')}/create_pair"
    payload = {"nodes": [node_a_id, node_b_id]}
    if params:
        payload.update(params)
    logger.info("Requesting EPR pair from SeQUeNCe: %s", payload)
    resp = http_post_with_retries(url, json_payload=payload)
    data = resp.json()
    pair_id = data.get("pair_id") or data.get("id") or data.get("pairId")
    fidelity = data.get("fidelity") or data.get("F") or None
    return pair_id, fidelity, data


# ------------------------------------------------------------
# Dispatch to QNode (must include pair_id & fidelity explicitly)
# ------------------------------------------------------------
def dispatch_slice_to_qnode(qnode_url: str,
                            circuit_qasm: str,
                            slice_id: str,
                            shots: int,
                            key_id: Optional[str],
                            key_hex: Optional[str],
                            pair_id: Optional[str],
                            fidelity: Optional[float]) -> Dict[str, Any]:
    """
    Sends /execute_slice to QNode. The QNode will receive key_hex (sensitive) but Collector will not.
    Returns JSON response from QNode.
    """
    payload = {
        "circuit_qasm": circuit_qasm,
        "slice_id": slice_id,
        "shots": shots,
        "key_id": key_id,
        "key_hex": key_hex,          # sensitive: will NOT be forwarded to Collector
        "pair_id": pair_id,
        "fidelity": fidelity
    }
    logger.info("Dispatching slice '%s' to QNode %s (pair=%s, key=%s, fidelity=%s)",
                slice_id, qnode_url, pair_id, key_id, fidelity)
    resp = http_post_with_retries(f"{qnode_url.rstrip('/')}/execute_slice", json_payload=payload)
    return resp.json()


# ------------------------------------------------------------
# Collector recording (send metadata only, do NOT include key_hex)
# ------------------------------------------------------------
def record_run_to_collector(slice_id: str,
                            pair_id: Optional[str],
                            key_id: Optional[str],
                            fidelity: Optional[float],
                            qnode_url: str,
                            qnode_resp: Dict[str, Any],
                            extra_metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Posts a record to the Collector service. This is best-effort: failures won't abort the experiment.
    The Collector stores metadata and qnode_result, but NOT key_hex.
    """
    payload = {
        "slice_id": slice_id,
        "pair_id": pair_id,
        "key_id": key_id,
        "fidelity": fidelity,
        "qnode_url": qnode_url,
        # record a compact qnode_result summary (counts + fidelity)
        "qnode_result": {
            "fidelity": qnode_resp.get("fidelity") if isinstance(qnode_resp, dict) else None,
            "counts": qnode_resp.get("counts") if isinstance(qnode_resp, dict) else None,
            "raw": None
        },
        "metadata": extra_metadata or {}
    }
    try:
        resp = requests.post(f"{COLLECTOR_URL.rstrip('/')}/record", json=payload, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("Collector record failed: %s", e)
        return None


# ------------------------------------------------------------
# FastAPI endpoints
# ------------------------------------------------------------
@app.post("/orchestrator/request_and_dispatch")
def request_and_dispatch(req: ExecuteSliceRequest, background: BackgroundTasks):
    """
    End-to-end orchestration:
    - Optionally request pair (if req.pair_id is None)
    - Request Quditto key (enc_keys at node A)
    - If needed, request dec_keys at node B to obtain key material
    - Dispatch to QNode including key_hex and pair metadata (pair_id & fidelity)
    - Record mapping in Collector (background)
    """
    # choose quditto endpoints and qnode logical ids
    quditto_a = req.quditto_node_a or QUDITTO_NODE_A_URL
    quditto_b = req.quditto_node_b or QUDITTO_NODE_B_URL
    target_a_id = req.qnode_target_a_id or DEFAULT_QNODE_TARGET_A_ID
    target_b_id = req.qnode_target_b_id or DEFAULT_QNODE_TARGET_B_ID
    qnode_target_url = req.qnode_url or DEFAULT_QNODE_URL

    # 1) Ensure pair_id & fidelity (ask Sequence if not provided)
    pair_id = req.pair_id
    fidelity = req.fidelity
    seq_raw = None
    if not pair_id:
        try:
            pair_id, fidelity, seq_raw = request_pair_from_sequence(target_a_id, target_b_id, params=None)
            logger.info("Sequence returned pair_id=%s fidelity=%s", pair_id, fidelity)
        except Exception as e:
            logger.exception("Failed to request pair from SeQUeNCe: %s", e)
            raise HTTPException(status_code=502, detail=f"SeQUeNCe create_pair failed: {e}")

    # 2) Request Quditto key via enc_keys on quditto_a
    try:
        enc = request_quditto_enc_keys(quditto_a, target_b_id, size_bits=512)
        key_id = enc.get("key_id")
        key_hex = enc.get("key_hex")
    except Exception as e:
        logger.exception("Quditto enc_keys failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Quditto enc_keys failed: {e}")

    # 3) If key_material not returned by enc_keys, try dec_keys on quditto_b
    if not key_hex and key_id:
        try:
            dec = request_quditto_dec_keys(quditto_b, target_a_id, key_id=key_id)
            key_hex = dec.get("key_hex")
        except Exception as e:
            logger.exception("Quditto dec_keys failed: %s", e)
            raise HTTPException(status_code=502, detail=f"Quditto dec_keys failed: {e}")

    if not key_hex:
        # cannot continue securely if no key material available
        logger.error("No key material available for this exchange (key_id=%s). Aborting.", key_id)
        raise HTTPException(status_code=502, detail="No key material returned by Quditto enc_keys/dec_keys")

    # 4) Dispatch the slice to QNode with explicit pair_id & fidelity
    try:
        qnode_resp = dispatch_slice_to_qnode(qnode_target_url,
                                             req.circuit_qasm,
                                             req.slice_id,
                                             req.shots,
                                             key_id=key_id,
                                             key_hex=key_hex,
                                             pair_id=pair_id,
                                             fidelity=fidelity)
    except Exception as e:
        logger.exception("Dispatch to QNode failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Dispatch to QNode failed: {e}")

    # 5) Record in Collector asynchronously (important: DO NOT send key_hex)
    extra_metadata = {
        "sequence_raw": seq_raw,
        "requested_quditto_a": quditto_a,
        "requested_quditto_b": quditto_b,
    }
    background.add_task(record_run_to_collector, req.slice_id, pair_id, key_id, fidelity, qnode_target_url, qnode_resp, extra_metadata)

    return {
        "status": "dispatched",
        "slice_id": req.slice_id,
        "pair_id": pair_id,
        "fidelity": fidelity,
        "key_id": key_id,
        "qnode_response": qnode_resp
    }


@app.get("/debug/request_key")
def debug_request_key(quditto_node: str, target: str, size: int = 512):
    """Debug helper to request enc_keys from a Quditto node URL."""
    try:
        resp = request_quditto_enc_keys(quditto_node, target, size_bits=size)
        return {"ok": True, "enc_response": resp}
    except Exception as e:
        logger.exception("debug_request_key failed: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
def health():
    return {"status": "ok", "component": "orchestrator", "seq": SEQUENCE_URL, "qnode": DEFAULT_QNODE_URL, "quditto_a": QUDITTO_NODE_A_URL}


# ------------------------------------------------------------
# Run with uvicorn when executed directly
# ------------------------------------------------------------
if __name__ == "__main__":
    import sys
    import uvicorn

    port = 8001
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
        except ValueError:
            logger.warning("Invalid port argument '%s', using default %d", sys.argv[1], port)

    logger.info("Starting Orchestrator on port %d", port)
    uvicorn.run("orchestrator.app:app", host="0.0.0.0", port=port, log_level="info")
