"""Hand-written mock domains by bundle task_id."""

from __future__ import annotations

from builder.adapters.mock.env import DomainPack


def get(task_id: str) -> DomainPack | None:
    if task_id == "mock-retail-bank":
        from builder.adapters.mock.domains.retail_bank import make_pack

        return make_pack()
    return None
