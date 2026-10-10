"""Failure analyzer (memo §18.2–18.3, §28; TextGrad-style credit assignment §3.10).

Deterministic localisation first; an optional cheap LLM pass refines the type,
explains the root cause and proposes a minimal fix for the implicated block.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

from builder.failure.first_deviation import localize
from builder.llm import LLMClient, Message, Role
from builder.schema.eval import ScenarioResult
from builder.schema.failure import FAILURE_COMPONENTS, FailureReport, FailureType
from builder.schema.scenario import ScenarioSpec
from builder.schema.tool import ToolGraph
from builder.schema.trace import SimulationTrace


class Refinement(BaseModel):
    failure_type: FailureType
    first_bad_turn: int | None = None
    root_cause: str
    implicated_rules: list[str] = Field(default_factory=list)
    minimal_fix: str = Field(
        description="One general instruction that would prevent this, "
        "with no scenario-specific ids, names or amounts"
    )


ANALYZE_PROMPT = """You debug a failed customer-service agent conversation.
Find the FIRST decisive mistake (prefer mistakes on data-changing calls), classify it with one
failure type from the list, explain the root cause in 1-2 sentences, and propose ONE minimal,
general instruction that would have prevented it. The fix must not mention specific ids, names,
amounts or phrasings from this conversation (it must generalize to unseen tasks).
Failure types: {types}
Return JSON only."""


def analyze(
    trace: SimulationTrace,
    scenario: ScenarioSpec,
    result: ScenarioResult,
    tools: ToolGraph,
    llm: LLMClient | None = None,
) -> FailureReport:
    rep = localize(trace, scenario, result, tools)
    if llm is None:
        return rep
    failed = [f"{c.name}: {c.detail}" for c in result.failed_checks()]
    body = (
        f"PRELIMINARY: {rep.failure_type.value} at turn {rep.first_bad_turn} — {rep.explanation}\n"
        f"FAILED CHECKS:\n" + "\n".join(failed) + f"\n\nCUSTOMER GOAL: {scenario.user_goal}\n"
        f"RULES TESTED: {scenario.target_policies}\n\nTRANSCRIPT:\n{trace.transcript()[:30000]}"
    )
    ref = llm.complete_structured(
        Role.ANALYZER,
        Refinement,
        [
            Message(
                role="system",
                content=ANALYZE_PROMPT.format(types=", ".join(t.value for t in FailureType)),
            ),
            Message(role="user", content=body),
        ],
        temperature=0.0,
        cache_salt=trace.run_id,
    )
    comps = FAILURE_COMPONENTS[ref.failure_type]
    return rep.model_copy(
        update={
            "failure_type": ref.failure_type,
            "first_bad_turn": ref.first_bad_turn
            if ref.first_bad_turn is not None
            else rep.first_bad_turn,
            "explanation": ref.root_cause,
            "suggested_fix": ref.minimal_fix,
            "implicated_rules": ref.implicated_rules or rep.implicated_rules,
            "implicated_components": comps,
            "suggested_mutation_targets": comps,
        }
    )


def cluster(reports: list[FailureReport]) -> list[tuple[FailureType, int, list[FailureReport]]]:
    """Group by type, most frequent first (memo §18.2)."""
    counts = Counter(r.failure_type for r in reports)
    return [(ft, n, [r for r in reports if r.failure_type is ft]) for ft, n in counts.most_common()]


def component_pressure(reports: list[FailureReport]) -> Counter[str]:
    """Which prompt blocks are implicated most — the optimizer's mutation targets."""
    c: Counter[str] = Counter()
    for r in reports:
        for comp in r.implicated_components:
            c[comp] += r.severity
    return c
