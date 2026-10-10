"""Failure taxonomy (memo §18.2) and the failure→component map (memo §18.3)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class FailureType(StrEnum):
    MISSING_INFORMATION = "MISSING_INFORMATION"
    AMBIGUOUS_ENTITY = "AMBIGUOUS_ENTITY"
    WRONG_TOOL = "WRONG_TOOL"
    WRONG_ARGUMENT = "WRONG_ARGUMENT"
    WRONG_TOOL_ORDER = "WRONG_TOOL_ORDER"
    TOOL_CALLED_TOO_EARLY = "TOOL_CALLED_TOO_EARLY"
    TOOL_NOT_CALLED = "TOOL_NOT_CALLED"
    DUPLICATE_SIDE_EFFECT = "DUPLICATE_SIDE_EFFECT"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    MISSING_CONFIRMATION = "MISSING_CONFIRMATION"
    STALE_STATE = "STALE_STATE"
    RETRIEVAL_MISS = "RETRIEVAL_MISS"
    RETRIEVAL_WRONG_POLICY = "RETRIEVAL_WRONG_POLICY"
    CROSS_DOC_REASONING_FAILURE = "CROSS_DOC_REASONING_FAILURE"
    TOOL_ERROR_NOT_RECOVERED = "TOOL_ERROR_NOT_RECOVERED"
    USER_CORRECTION_IGNORED = "USER_CORRECTION_IGNORED"
    PREMATURE_TERMINATION = "PREMATURE_TERMINATION"
    REQUIRED_INFO_NOT_COMMUNICATED = "REQUIRED_INFO_NOT_COMMUNICATED"
    VOICE_CONFIRMATION_FAILURE = "VOICE_CONFIRMATION_FAILURE"
    # additions from memo §57, §61
    FALSE_REFUSAL = "FALSE_REFUSAL"
    UNNECESSARY_ESCALATION = "UNNECESSARY_ESCALATION"
    UNSUPPORTED_CLAIM = "UNSUPPORTED_CLAIM"
    TIMEOUT = "TIMEOUT"
    OTHER = "OTHER"


# Which AgentSpec prompt blocks / builder components a failure implicates.
FAILURE_COMPONENTS: dict[FailureType, list[str]] = {
    FailureType.MISSING_INFORMATION: ["action_protocol", "tool_use_policy"],
    FailureType.AMBIGUOUS_ENTITY: ["mutation_guard", "action_protocol"],
    FailureType.WRONG_TOOL: ["tool_use_policy", "tool_descriptions"],
    FailureType.WRONG_ARGUMENT: ["tool_descriptions", "tool_use_policy"],
    FailureType.WRONG_TOOL_ORDER: ["domain_rules", "planning", "workflow"],
    FailureType.TOOL_CALLED_TOO_EARLY: ["mutation_guard", "domain_rules"],
    FailureType.TOOL_NOT_CALLED: ["tool_use_policy", "completion_verifier"],
    FailureType.DUPLICATE_SIDE_EFFECT: ["mutation_guard", "post_write_verify"],
    FailureType.POLICY_VIOLATION: ["domain_rules", "rule_priority", "global_policy"],
    FailureType.MISSING_CONFIRMATION: ["mutation_guard", "domain_rules"],
    FailureType.STALE_STATE: ["operational_state"],
    FailureType.RETRIEVAL_MISS: ["knowledge_retrieval_policy"],
    FailureType.RETRIEVAL_WRONG_POLICY: ["knowledge_retrieval_policy", "rule_priority"],
    FailureType.CROSS_DOC_REASONING_FAILURE: ["knowledge_retrieval_policy"],
    FailureType.TOOL_ERROR_NOT_RECOVERED: ["post_write_verify", "tool_use_policy"],
    FailureType.USER_CORRECTION_IGNORED: ["operational_state"],
    FailureType.PREMATURE_TERMINATION: ["completion_verifier"],
    FailureType.REQUIRED_INFO_NOT_COMMUNICATED: ["outcome_statement", "completion_verifier"],
    FailureType.VOICE_CONFIRMATION_FAILURE: ["voice_policy"],
    FailureType.FALSE_REFUSAL: ["over_caution_guard", "rule_priority"],
    FailureType.UNNECESSARY_ESCALATION: ["over_caution_guard"],
    FailureType.UNSUPPORTED_CLAIM: ["post_write_verify", "outcome_statement"],
    FailureType.TIMEOUT: ["global_policy"],
    FailureType.OTHER: ["global_policy"],
}


class FailureReport(BaseModel):
    run_id: str = ""
    scenario_id: str = ""
    candidate_id: str = ""
    failure_type: FailureType
    severity: int = Field(default=2, ge=1, le=3)
    first_bad_turn: int | None = None
    evidence_turns: list[int] = Field(default_factory=list)
    implicated_rules: list[str] = Field(default_factory=list)
    implicated_tools: list[str] = Field(default_factory=list)
    implicated_components: list[str] = Field(default_factory=list)
    explanation: str = ""
    suggested_fix: str = ""
    suggested_mutation_targets: list[str] = Field(default_factory=list)


class Lesson(BaseModel):
    """Distilled reusable rule (memo §3.8, §20)."""

    lesson_id: str
    failure_type: FailureType
    applies_to: list[str] = Field(default_factory=list)
    rule: str
    trigger_pattern: str = ""
    positive_examples: list[str] = Field(default_factory=list)
    negative_examples: list[str] = Field(default_factory=list)
    provenance: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    retired: bool = False
