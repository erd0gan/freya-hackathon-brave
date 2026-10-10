"""Content-addressed disk cache for LLM responses (memo §19 "cache", §35)."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from builder.llm.types import LLMRequest, LLMResponse


def request_key(model: str, request: LLMRequest) -> str:
    payload = request.model_dump(mode="json", exclude={"model"})
    payload["model"] = model
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


class DiskCache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> LLMResponse | None:
        p = self._path(key)
        if not p.exists():
            return None
        resp = LLMResponse.model_validate_json(p.read_text())
        return resp.model_copy(update={"cached": True})

    def put(self, key: str, response: LLMResponse) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(response.model_dump_json())
        tmp.replace(p)
