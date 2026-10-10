"""Pareto retention over per-scenario outcomes (memo §18.5)."""

from __future__ import annotations

from builder.eval.metrics import scenario_success
from builder.schema.eval import CandidateEvaluation


def dominates(a: dict[str, float], b: dict[str, float]) -> bool:
    keys = set(a) & set(b)
    return bool(keys) and all(a[k] >= b[k] for k in keys) and any(a[k] > b[k] for k in keys)


def pareto_front(evals: list[CandidateEvaluation]) -> list[CandidateEvaluation]:
    vecs = {e.candidate_id: scenario_success(e) for e in evals}
    front = [
        e
        for e in evals
        if not any(
            dominates(vecs[o.candidate_id], vecs[e.candidate_id])
            for o in evals
            if o.candidate_id != e.candidate_id
        )
    ]
    return sorted(front, key=lambda e: (-e.mean, e.candidate_id))


def prune(evals: list[CandidateEvaluation], keep: int) -> list[CandidateEvaluation]:
    """Keep the front, then fill by mean — candidates best on a slice survive."""
    front = pareto_front(evals)
    ids = {e.candidate_id for e in front}
    rest = sorted((e for e in evals if e.candidate_id not in ids), key=lambda e: -e.mean)
    return (front + rest)[: max(keep, 1)] if len(front) <= keep else front[:keep]
