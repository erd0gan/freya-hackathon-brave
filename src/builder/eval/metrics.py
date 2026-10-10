"""Aggregate ScenarioResults into a CandidateEvaluation (memo §17.2, §60)."""

from __future__ import annotations

from collections import defaultdict

from builder.eval.stats import (
    beta_lower_bound,
    clustered_std_err,
    mean,
    pass_hat_k,
    std_err,
)
from builder.schema.eval import CandidateEvaluation, ScenarioResult


def aggregate(candidate_id: str, results: list[ScenarioResult]) -> CandidateEvaluation:
    rs = [r for r in results if r.candidate_id == candidate_id]
    if not rs:
        return CandidateEvaluation(candidate_id=candidate_id)
    vals = [float(r.success) for r in rs]
    clusters = [r.scenario_id for r in rs]
    by_sc: dict[str, list[bool]] = defaultdict(list)
    for r in rs:
        by_sc[r.scenario_id].append(r.success)
    # Lower bound on the scenario-level success rate (each scenario weighted equally).
    sc_means = [mean([float(x) for x in v]) for v in by_sc.values()]
    lb = beta_lower_bound(sum(sc_means), len(sc_means))
    phk: dict[int, float] = {}
    max_trials = min(len(v) for v in by_sc.values())
    for k in range(1, max_trials + 1):
        phk[k] = mean([pass_hat_k(sum(v), len(v), k) for v in by_sc.values()])
    return CandidateEvaluation(
        candidate_id=candidate_id,
        results=rs,
        mean=mean(vals),
        std_err=std_err(vals),
        clustered_std_err=clustered_std_err(vals, clusters),
        lower_bound=lb,
        pass_hat_k=phk,
        false_compliance_rate=mean([float(r.false_compliance) for r in rs]),
        false_refusal_rate=mean([float(r.false_refusal) for r in rs]),
        n_scenarios=len(by_sc),
        n_runs=len(rs),
    )


def scenario_success(ev: CandidateEvaluation) -> dict[str, float]:
    by: dict[str, list[float]] = defaultdict(list)
    for r in ev.results:
        by[r.scenario_id].append(float(r.success))
    return {k: mean(v) for k, v in by.items()}
