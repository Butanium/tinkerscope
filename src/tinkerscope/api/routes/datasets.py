"""Training-dataset deep-link: load samples from a run's training JSONL.

config.json links each run to the JSONL it was trained on; the UI lets you peek
at what the model actually saw. Paths are confined to the serving root via
`safe_path` (no escaping above the scanned tree)."""
from __future__ import annotations

import json
import random

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..settings import safe_path

router = APIRouter(prefix="/api", tags=["datasets"])


class DatasetLoadRequest(BaseModel):
    path: str
    count: int = Field(10, ge=0)  # ge=0 → a 0/negative count can't crash random.sample
    seed: int | None = None


@router.post("/load-dataset")
def load_dataset(req: DatasetLoadRequest) -> dict:
    try:
        resolved = safe_path(req.path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not resolved.exists():
        raise HTTPException(404, f"file not found: {req.path}")
    if resolved.suffix != ".jsonl":
        raise HTTPException(400, "only .jsonl files are supported")

    records = []
    for line in resolved.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    total = len(records)
    n = max(0, min(req.count, total))
    rng = random.Random(req.seed)
    sampled = rng.sample(records, n) if n < total else records
    return {"records": sampled, "total": total}
