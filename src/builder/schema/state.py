"""Runtime operational state, mutation checks, completion spec (memo §10–12, §31)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ActionClass(StrEnum):
    """memo §11 — exactly one per turn."""

    ASK_USER = "ASK_USER"
    GUIDE_USER = "GUIDE_USER"
    RETRIEVE_KNOWLEDGE = "RETRIEVE_KNOWLEDGE"
    CALL_READ_TOOL = "CALL_READ_TOOL"
    PROPOSE_WRITE = "PROPOSE_WRITE"
    CALL_WRITE_TOOL = "CALL_WRITE_TOOL"
    RESPOND = "RESPOND"
    ESCALATE = "ESCALATE"
    COMPLETE = "COMPLETE"


class ToolRecord(BaseModel):
    tool: str
    args: dict[str, Any]
    ok: bool
    result: Any = None
    state_version: int = 0
    turn: int = 0


class RuntimeState(BaseModel):
    """Grounded operational state — not chain-of-thought (memo §10)."""

    goal: str = ""
    active_intents: list[str] = Field(default_factory=list)
    known_facts: dict[str, Any] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list)
    policy_rules_in_scope: list[str] = Field(default_factory=list)
    completed_writes: list[ToolRecord] = Field(default_factory=list)
    reads: list[ToolRecord] = Field(default_factory=list)
    pending_actions: list[str] = Field(default_factory=list)
    awaiting_confirmation: str | None = None
    confirmed_actions: list[str] = Field(default_factory=list)
    unresolved_errors: list[str] = Field(default_factory=list)
    state_version: int = 0  # memo §13: += 1 after every write

    def stale_reads(self) -> list[ToolRecord]:
        return [r for r in self.reads if r.state_version < self.state_version]


class MutationCheck(BaseModel):
    """memo §12."""

    tool_name: str
    goal_alignment: bool = True
    arguments_grounded: bool = True
    preconditions_satisfied: bool = True
    policy_allows: bool = True
    confirmation_satisfied: bool = True
    duplicate_risk_clear: bool = True
    state_is_fresh: bool = True
    target_unambiguous: bool = True
    expected_postconditions: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)

    @property
    def approved(self) -> bool:
        return all(
            (
                self.goal_alignment,
                self.arguments_grounded,
                self.preconditions_satisfied,
                self.policy_allows,
                self.confirmation_satisfied,
                self.duplicate_risk_clear,
                self.state_is_fresh,
                self.target_unambiguous,
            )
        )

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "approved"


class CompletionSpec(BaseModel):
    """memo §31."""

    must_hold: list[str] = Field(
        default_factory=lambda: [
            "all_required_intents_resolved",
            "no_pending_write",
            "no_unresolved_tool_error",
            "required_communication_sent",
            "expected_state_verified",
        ]
    )
    must_not_hold: list[str] = Field(
        default_factory=lambda: [
            "waiting_for_user_info",
            "ambiguous_target",
            "pending_confirmation",
        ]
    )
