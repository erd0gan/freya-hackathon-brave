"""Score vectors and per-scenario results (memo §17, §57, §61)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str = ""
    evaluator: str = Field(
        default="", description="state | tool_call | policy | communication | judge"
    )


class ScoreVector(BaseModel):
    """Kept as a vector; collapse only at selection time (memo §17)."""

    task_success: float = 0.0
    policy: float = 1.0
    state: float = 1.0
    communication: float = 1.0
    reliability: float = 0.0
    false_compliance: float = 0.0
    false_refusal: float = 0.0
    tool_calls: float = 0.0
    token_cost: float = 0.0
    latency_s: float = 0.0


class ScenarioResult(BaseModel):
    run_id: str
    candidate_id: str
    scenario_id: str
    trial: int = 0
    success: bool
    checks: list[CheckResult] = Field(default_factory=list)
    score: ScoreVector = Field(default_factory=ScoreVector)
    false_compliance: bool = False
    false_refusal: bool = False
    judge_rationale: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)

    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


class CandidateEvaluation(BaseModel):
    candidate_id: str
    results: list[ScenarioResult] = Field(default_factory=list)
    mean: float = 0.0
    std_err: float = 0.0
    clustered_std_err: float = 0.0
    lower_bound: float = 0.0
    pass_hat_k: dict[int, float] = Field(default_factory=dict)
    false_compliance_rate: float = 0.0
    false_refusal_rate: float = 0.0
    n_scenarios: int = 0
    n_runs: int = 0
