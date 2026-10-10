"""Helpers that turn a trace into executed calls (pairing calls with results)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from builder.schema.trace import EventKind, SimulationTrace


@dataclass
class ExecutedCall:
    turn: int
    tool: str
    args: dict[str, Any]
    ok: bool | None
    result: Any
    blocked: bool


def executed_calls(trace: SimulationTrace) -> list[ExecutedCall]:
    out: list[ExecutedCall] = []
    pending: list[ExecutedCall] = []
    for e in trace.events:
        if e.kind is EventKind.TOOL_CALL:
            c = ExecutedCall(e.turn, e.tool or "", dict(e.args or {}), None, None, False)
            out.append(c)
            pending.append(c)
        elif e.kind is EventKind.TOOL_RESULT:
            match = next((c for c in pending if c.tool == e.tool), None)
            if match is not None:
                pending.remove(match)
                match.ok = e.ok
                match.result = e.result
                match.blocked = isinstance(e.result, dict) and (
                    e.result.get("error") == "blocked_by_safeguard"
                )
    return out


def attempted(trace: SimulationTrace) -> list[ExecutedCall]:
    """Calls that reached the backend (gate-blocked attempts excluded)."""
    return [c for c in executed_calls(trace) if not c.blocked]
