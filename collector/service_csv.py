#!/usr/bin/env python3
# collector/service_csv.py
"""
Collector (CSV version) - Lightweight, dependency-free experiment logger.

Endpoints:
    POST /record        -> append new experiment row
    GET  /export_csv    -> return CSV file path
    GET  /health        -> report number of records

Stores in: data/collector_records.csv
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json
import time
import csv
from pathlib import Path
import threading

# --- Config ---
DATA_PATH = Path("data/collector_records.csv")
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
LOCK = threading.Lock()  # ensures thread-safe writes

# --- FastAPI app ---
app = FastAPI(title="Collector (CSV-based)", version="0.2")

# --- Pydantic model ---
class RecordRequest(BaseModel):
    slice_id: str
    pair_id: Optional[str] = None
    key_id: Optional[str] = None
    fidelity: Optional[float] = None
    qnode_url: Optional[str] = None
    qnode_result: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None

# --- Internal helper: append row safely ---
def append_row(data: dict):
    is_new = not DATA_PATH.exists()
    with LOCK:  # avoids concurrent writes
        with open(DATA_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(data.keys()))
            if is_new:
                writer.writeheader()
            writer.writerow(data)

# --- Routes ---
@app.post("/record")
def record(req: RecordRequest):
    try:
        now = time.time()
        row = {
            "timestamp": now,
            "slice_id": req.slice_id,
            "pair_id": req.pair_id,
            "key_id": req.key_id,
            "fidelity": req.fidelity,
            "qnode_url": req.qnode_url,
            "qnode_result_summary": json.dumps(req.qnode_result) if req.qnode_result else "",
            "metadata": json.dumps(req.metadata) if req.metadata else "{}"
        }
        append_row(row)
        return {"ok": True, "timestamp": now, "path": str(DATA_PATH)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/export_csv")
def export_csv():
    if not DATA_PATH.exists():
        raise HTTPException(status_code=404, detail="No records yet")
    return {"ok": True, "path": str(DATA_PATH), "size_bytes": DATA_PATH.stat().st_size}

@app.get("/health")
def health():
    lines = 0
    if DATA_PATH.exists():
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            lines = sum(1 for _ in f) - 1  # header line excluded
    return {"status": "ok", "records": max(lines, 0)}

# --- CLI Run ---
if __name__ == "__main__":
    import uvicorn
    print("Starting CSV Collector on port 8006...")
    uvicorn.run("collector.service_csv:app", host="0.0.0.0", port=8006, reload=True)
