from __future__ import annotations

from pathlib import Path

import pytest

from builder.llm import CostLedger, DiskCache, LLMClient, Role
from builder.llm.fake import FakeProvider


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def make_client(tmp_path: Path):
    def _make(provider: FakeProvider, max_cost: float = 50.0, cache: bool = True) -> LLMClient:
        return LLMClient(
            provider=provider,
            models={r: f"model-{r.value}" for r in Role},
            ledger=CostLedger(tmp_path / "ledger.jsonl", max_cost),
            cache=DiskCache(tmp_path / "cache") if cache else None,
        )

    return _make
