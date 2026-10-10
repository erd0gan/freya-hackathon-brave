"""Persistent cost ledger with a hard spending cap (PLAN §0.1: solo, ~$50).

Every non-cached LLM call is appended as one JSON line. The running total is
restored on start-up so the cap holds across processes and sessions.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from builder.llm.errors import BudgetExceeded
from builder.llm.types import Usage


class CostLedger:
    def __init__(self, path: Path | None, max_cost_usd: float) -> None:
        self.path = path
        self.max_cost_usd = max_cost_usd
        self._lock = threading.Lock()
        self.total_usd = 0.0
        self.calls = 0
        if path is not None and path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.total_usd += float(rec.get("cost_usd", 0.0))
                    self.calls += 1

    def check(self) -> None:
        if self.total_usd >= self.max_cost_usd:
            raise BudgetExceeded(
                f"LLM spend ${self.total_usd:.2f} reached cap ${self.max_cost_usd:.2f}"
            )

    def record(self, role: str, model: str, usage: Usage) -> None:
        with self._lock:
            self.total_usd += usage.cost_usd
            self.calls += 1
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as f:
                    f.write(
                        json.dumps(
                            {
                                "ts": time.time(),
                                "role": role,
                                "model": model,
                                "prompt_tokens": usage.prompt_tokens,
                                "completion_tokens": usage.completion_tokens,
                                "cost_usd": usage.cost_usd,
                            }
                        )
                        + "\n"
                    )
