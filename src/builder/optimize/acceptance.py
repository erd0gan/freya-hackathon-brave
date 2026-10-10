"""Paired acceptance with a simplicity prior (memo §60 protocol step 3)."""

from __future__ import annotations

import math

from pydantic import BaseModel

from builder.eval.metrics import scenario_success
from builder.eval.stats import PairedComparison, paired
from builder.schema.eval import CandidateEvaluation


class AcceptancePolicy(BaseModel):
    """Defaults keep a no-op mutation's acceptance at ~3-6% (Monte Carlo, n=9..30 scenarios,
    2 trials each; `tests/unit/test_optimize.py`). The previous rule (net >= 1, p <= 0.5)
    accepted a child identical to its parent 38-43% of the time (GAP_ANALYSIS A8)."""

    min_net_wins: int = 2
    min_net_fraction: float = 0.2  # net wins must also cover this share of shared scenarios
    max_sign_p: float = 0.1  # one-sided sign test
    min_mean_diff: float = 0.05
    max_false_compliance_increase: float = 0.0


class Decision(BaseModel):
    accepted: bool
    comparison: PairedComparison
    reason: str


def accept(
    child: CandidateEvaluation, parent: CandidateEvaluation, policy: AcceptancePolicy | None = None
) -> Decision:
    p = policy or AcceptancePolicy()
    cmp = paired(scenario_success(child), scenario_success(parent))
    if cmp.n == 0:
        return Decision(accepted=False, comparison=cmp, reason="no shared scenarios")
    fc_up = child.false_compliance_rate - parent.false_compliance_rate
    if fc_up > p.max_false_compliance_increase + 1e-9:
        return Decision(
            accepted=False, comparison=cmp, reason=f"false compliance rose by {fc_up:.2f}"
        )
    net = cmp.wins - cmp.losses
    need = max(p.min_net_wins, math.ceil(p.min_net_fraction * cmp.n))
    if net >= need and cmp.mean_diff >= p.min_mean_diff and cmp.sign_test_p <= p.max_sign_p:
        return Decision(accepted=True, comparison=cmp, reason=f"net +{net} scenarios")
    return Decision(
        accepted=False,
        comparison=cmp,
        reason=f"net {net:+d}/{need} needed (p={cmp.sign_test_p:.2f}); keep simpler parent",
    )
