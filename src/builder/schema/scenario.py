"""ScenarioSpec, personas and machine-checkable oracles (memo §5.6, §15, §16, §55.3, §57)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ScenarioCategory(StrEnum):
    # memo §15.1–15.13
    HAPPY_PATH = "happy_path"
    MISSING_INFORMATION = "missing_information"
    USER_CORRECTION = "user_correction"
    POLICY_PRESSURE = "policy_pressure"
    STATE_DEPENDENCY = "state_dependency"
    TOOL_FAILURE = "tool_failure"
    DUPLICATE_SIDE_EFFECT = "duplicate_side_effect"
    MULTI_INTENT = "multi_intent"
    TOOL_USER_DISAGREEMENT = "tool_user_disagreement"
    GOAL_SHIFT = "goal_shift"
    KNOWLEDGE_TRAP = "knowledge_trap"
    LONG_CONTEXT_DRIFT = "long_context_drift"
    VOICE = "voice"
    # memo §61 — over-caution costs points too
    RISKY_LOOKING_LEGIT = "risky_looking_legit"
    ALREADY_CONFIRMED = "already_confirmed"
    TRANSFER_NOT_NEEDED = "transfer_not_needed"
    # memo §29–30
    ADVERSARIAL = "adversarial"


class PersonaStyle(StrEnum):
    """memo §59 — vary styles so we don't over-tune to one simulator."""

    COOPERATIVE = "cooperative"
    TERSE = "terse"
    CHATTY = "chatty"
    CONFUSED = "confused"
    IMPATIENT = "impatient"
    NON_NATIVE = "non_native"


class Persona(BaseModel):
    name: str = "customer"
    style: PersonaStyle = PersonaStyle.COOPERATIVE
    language: str = "en"
    prompt: str = Field(description="What the simulated user knows and wants (user-visible goal)")
    hidden_facts: dict[str, Any] = Field(
        default_factory=dict, description="Facts the user reveals only when asked"
    )
    system_prompt: str = ""


class StateOp(StrEnum):
    EQ = "eq"
    NE = "ne"
    EXISTS = "exists"
    ABSENT = "absent"
    CONTAINS = "contains"
    COUNT_EQ = "count_eq"


class StateAssertion(BaseModel):
    """Assert on the final environment state via a dotted path, e.g. `cards.C1.status`."""

    path: str
    op: StateOp = StateOp.EQ
    value: Any = None
    note: str = ""


class ArgMatch(StrEnum):
    SUBSET = "subset"
    EXACT = "exact"


class ToolCallAssertion(BaseModel):
    """Freya-style tool-call assertion (memo §55.3): subset/exact, optionally negated."""

    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    match: ArgMatch = ArgMatch.SUBSET
    negated: bool = False
    min_count: int = 1
    max_count: int | None = None
    note: str = ""


class Oracle(BaseModel):
    """memo §16. Deterministic parts first; `success_criteria` is the LLM-judge fallback."""

    state: list[StateAssertion] = Field(default_factory=list)
    unchanged: list[str] = Field(
        default_factory=list, description="state paths that must not change"
    )
    tolerate_new_fields: bool = Field(
        False,
        description="simulated backends may add fields to a record that is expected to change",
    )
    tool_calls: list[ToolCallAssertion] = Field(default_factory=list)
    required_communication: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    policy: dict[str, Literal["satisfied", "violated", "not_triggered"]] = Field(
        default_factory=dict
    )
    reference_actions: list[dict[str, Any]] = Field(
        default_factory=list, description="One valid action sequence; replayed to derive state"
    )
    success_criteria: str = ""
    evaluation_prompt: str = ""
    expect_refusal: bool = False
    expect_escalation: bool = False


class Split(StrEnum):
    TRAIN = "train"
    VALIDATION = "validation"
    HOLDOUT = "holdout"


class ScenarioSpec(BaseModel):
    scenario_id: str
    category: ScenarioCategory = ScenarioCategory.HAPPY_PATH
    complexity: int = 1
    target_policies: list[str] = Field(default_factory=list)

    initial_state: dict[str, Any] = Field(default_factory=dict)
    user_goal: str = ""
    persona: Persona
    tool_faults: dict[str, Any] = Field(
        default_factory=dict, description="Injected tool failures, e.g. {'tool': {'error': 500}}"
    )
    oracle: Oracle = Field(default_factory=Oracle)

    language: str = "en"
    seed: int = 0
    split: Split = Split.TRAIN
    max_turns: int = 30
    provenance: str = ""
