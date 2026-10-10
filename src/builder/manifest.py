"""Reproducibility manifest (memo §35)."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

BUILDER_VERSION = "0.1.0"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_json(obj: Any) -> str:
    return sha256_text(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str))


def git_commit(cwd: Path | None = None) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, capture_output=True, text=True, check=True
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=cwd, capture_output=True, text=True, check=True
        ).stdout.strip()
        return out.stdout.strip() + ("-dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def lock_hash(root: Path = Path(".")) -> str:
    lock = root / "uv.lock"
    return hashlib.sha256(lock.read_bytes()).hexdigest() if lock.exists() else ""


class Manifest(BaseModel):
    builder_version: str = BUILDER_VERSION
    commit: str = Field(default_factory=git_commit)
    python: str = Field(default_factory=platform.python_version)
    uv_lock_sha256: str = Field(default_factory=lock_hash)
    generated_at: float = Field(default_factory=time.time)
    seed: int = 0
    adapter: str = ""
    adapter_version: str = ""
    models: dict[str, str] = Field(default_factory=dict)
    input_hash: str = ""
    tool_schema_hash: str = ""
    document_hashes: dict[str, str] = Field(default_factory=dict)
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    optimizer_config: dict[str, Any] = Field(default_factory=dict)
    selected_candidate: str = ""
    fallback_used: bool = False
    candidate_scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    holdout_scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    llm_cost_usd: float = 0.0  # this run
    ledger_total_usd: float = 0.0  # all runs sharing the ledger (the global $ cap)
    notes: list[str] = Field(default_factory=list)
