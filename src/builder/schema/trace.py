"""Simulation traces (memo §36). Every run produces one; the optimizer learns from them."""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventKind(StrEnum):
    USER = "user"
    AGENT = "agent"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    RETRIEVAL = "retrieval"
    STATE_DIFF = "state_diff"
    MUTATION_CHECK = "mutation_check"
    POLICY_CHECK = "policy_check"
    COMPLETION_CHECK = "completion_check"
    NOTE = "note"


class TraceEvent(BaseModel):
    turn: int
    kind: EventKind
    content: str = ""
    tool: str | None = None
    args: dict[str, Any] | None = None
    result: Any = None
    ok: bool | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class EndReason(StrEnum):
    AGENT_ENDED = "agent_ended"
    USER_ENDED = "user_ended"
    MAX_TURNS = "max_turns"
    TIMEOUT = "timeout"
    ERROR = "error"


class SimulationTrace(BaseModel):
    run_id: str
    candidate_id: str
    scenario_id: str
    seed: int = 0
    trial: int = 0
    adapter: str = ""
    user_model: str = ""
    events: list[TraceEvent] = Field(default_factory=list)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    final_state: dict[str, Any] | None = None
    end_reason: EndReason = EndReason.AGENT_ENDED
    error: str | None = None
    latency_s: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    platform_result: dict[str, Any] = Field(
        default_factory=dict, description="e.g. Freya pass/fail + judge text"
    )
    tool_calls_observable: bool = Field(
        default=True,
        description="False when the platform transcript shows no tool calls: tool-call and "
        "write-based checks are then unknown, not failed",
    )
    started_at: float = Field(default_factory=time.time)

    def tool_calls(self) -> list[TraceEvent]:
        return [e for e in self.events if e.kind is EventKind.TOOL_CALL]

    def agent_messages(self) -> list[str]:
        return [e.content for e in self.events if e.kind is EventKind.AGENT]

    def transcript(self, with_tools: bool = True) -> str:
        lines: list[str] = []
        for e in self.events:
            if e.kind is EventKind.USER:
                lines.append(f"[{e.turn}] USER: {e.content}")
            elif e.kind is EventKind.AGENT:
                lines.append(f"[{e.turn}] AGENT: {e.content}")
            elif with_tools and e.kind is EventKind.TOOL_CALL:
                lines.append(f"[{e.turn}] TOOL_CALL {e.tool}({e.args})")
            elif with_tools and e.kind is EventKind.TOOL_RESULT:
                lines.append(f"[{e.turn}] TOOL_RESULT {e.tool}: {str(e.result)[:400]}")
            elif with_tools and e.kind is EventKind.MUTATION_CHECK:
                lines.append(f"[{e.turn}] GATE {e.tool}: {e.content}")
        return "\n".join(lines)
