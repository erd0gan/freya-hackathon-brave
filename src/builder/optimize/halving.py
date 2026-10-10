"""Successive halving (memo §19): never give weak candidates the leaders' budget."""

from __future__ import annotations

from collections.abc import Callable

from builder.schema.agent import AgentSpec
from builder.schema.eval import CandidateEvaluation, ScenarioResult
from builder.schema.scenario import ScenarioSpec

RunFn = Callable[[AgentSpec, list[ScenarioSpec]], list[ScenarioResult]]
AggFn = Callable[[str, list[ScenarioResult]], CandidateEvaluation]


def rank_key(e: CandidateEvaluation) -> tuple[float, float, float, float]:
    """Lower bound first: candidates eliminated early were scored on fewer scenarios, and a
    perfect score on 2 scenarios must not outrank 0.9 on 12 (memo §17.3)."""
    return (-e.lower_bound, -e.mean, e.false_compliance_rate, e.false_refusal_rate)


def successive_halving(
    candidates: list[AgentSpec],
    scenarios: list[ScenarioSpec],
    run: RunFn,
    aggregate: AggFn,
    first_rung: int = 3,
    keep_min: int = 2,
    protected: set[str] | None = None,
) -> tuple[list[AgentSpec], dict[str, CandidateEvaluation], list[str]]:
    """Returns (survivors, evaluations, log). Rungs grow geometrically over `scenarios`."""
    protected = protected or set()
    results: dict[str, list[ScenarioResult]] = {c.candidate_id: [] for c in candidates}
    alive = list(candidates)
    used = 0
    rung = max(1, first_rung)
    log: list[str] = []
    evals: dict[str, CandidateEvaluation] = {}
    while alive:
        upto = min(len(scenarios), used + rung)
        batch = scenarios[used:upto]
        if not batch:
            break
        for c in alive:
            results[c.candidate_id] += run(c, batch)
        used = upto
        evals = {
            c.candidate_id: aggregate(c.candidate_id, results[c.candidate_id])
            for c in candidates
            if results[c.candidate_id]
        }
        ranked = sorted(alive, key=lambda c: rank_key(evals[c.candidate_id]))
        scores = ", ".join(f"{c.candidate_id}={evals[c.candidate_id].mean:.2f}" for c in ranked)
        log.append(f"rung@{used}: {scores}")
        if len(alive) <= keep_min or used >= len(scenarios):
            alive = ranked
            break
        n_keep = max(keep_min, len(alive) // 2)
        # No elimination without evidence: while nobody is distinguishable from the leader,
        # everyone tied with it stays (an all-0.50 rung was broken by list order, favouring
        # B0). Ties among clear losers are still cut.
        best = rank_key(evals[ranked[0].candidate_id])
        while n_keep < len(ranked) and rank_key(evals[ranked[n_keep].candidate_id]) == best:
            n_keep += 1
        keep = ranked[:n_keep] + [c for c in ranked[n_keep:] if c.candidate_id in protected]
        alive = keep
        rung *= 2
    return alive, evals, log
