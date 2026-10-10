"""Robustness selection on the frozen validation split (memo §17.2–17.3, §59, §60).

Primary key: Beta lower bound of scenario-level success (small-n aware).
Then mean, then fewer false compliances, then fewer false refusals. The chosen
champion must not lose a paired comparison against the fallback.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from builder.eval.metrics import scenario_success
from builder.eval.stats import PairedComparison, paired
from builder.schema.eval import CandidateEvaluation


class Selection(BaseModel):
    champion: str
    ranking: list[str]
    fallback_used: bool = False
    vs_fallback: PairedComparison | None = None
    reasons: list[str] = Field(default_factory=list)


def selection_key(e: CandidateEvaluation, size: int = 0) -> tuple[float, float, float, float, int]:
    # Final tie-breaker: the simpler (shorter) agent wins (memo §60 simplicity prior).
    return (-e.lower_bound, -e.mean, e.false_compliance_rate, e.false_refusal_rate, size)


def select(
    evals: dict[str, CandidateEvaluation],
    fallback_id: str,
    sizes: dict[str, int] | None = None,
) -> Selection:
    sizes = sizes or {}
    ranking = sorted(evals, key=lambda c: selection_key(evals[c], sizes.get(c, 0)))
    if not ranking:
        return Selection(
            champion=fallback_id, ranking=[], fallback_used=True, reasons=["no evaluations"]
        )
    best = ranking[0]
    reasons = [
        f"{c}: lb={evals[c].lower_bound:.2f} mean={evals[c].mean:.2f} "
        f"fc={evals[c].false_compliance_rate:.2f} fr={evals[c].false_refusal_rate:.2f}"
        for c in ranking
    ]
    if best == fallback_id or fallback_id not in evals:
        return Selection(champion=best, ranking=ranking, reasons=reasons)
    cmp = paired(scenario_success(evals[best]), scenario_success(evals[fallback_id]))
    if cmp.losses > cmp.wins:
        reasons.append(f"{best} loses paired comparison vs fallback ({cmp.wins}-{cmp.losses})")
        return Selection(
            champion=fallback_id,
            ranking=ranking,
            fallback_used=True,
            vs_fallback=cmp,
            reasons=reasons,
        )
    return Selection(champion=best, ranking=ranking, vs_fallback=cmp, reasons=reasons)
