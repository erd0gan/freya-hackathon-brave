"""Fork-based counterfactual check (memo §55.4, §60 step 4).

Before paying for a full re-simulation, replay a failed conversation's user
turns up to the first decisive mistake against the mutated child and see
whether the same bad write recurs. Cheap and low-variance; used as a pre-filter.
"""

from __future__ import annotations

from typing import Any

from builder.schema.agent import AgentSpec
from builder.schema.failure import FailureReport, FailureType
from builder.schema.scenario import ScenarioSpec
from builder.schema.trace import EventKind, SimulationTrace

PROBEABLE = {
    FailureType.MISSING_CONFIRMATION,
    FailureType.TOOL_CALLED_TOO_EARLY,
    FailureType.DUPLICATE_SIDE_EFFECT,
    FailureType.POLICY_VIOLATION,
    FailureType.WRONG_TOOL,
    FailureType.WRONG_ARGUMENT,
}


def supports_probe(adapter: Any) -> bool:
    return hasattr(adapter, "replay_prefix")


def probe(
    adapter: Any,
    child: AgentSpec,
    trace: SimulationTrace,
    scenario: ScenarioSpec,
    failure: FailureReport,
) -> bool | None:
    """True = mistake avoided, False = repeated, None = not probeable."""
    if failure.failure_type not in PROBEABLE or failure.first_bad_turn is None:
        return None
    if not failure.implicated_tools:
        return None
    users = [
        e.content
        for e in trace.events
        if e.kind is EventKind.USER and e.turn <= failure.first_bad_turn
    ]
    if not users:
        return None
    events = adapter.replay_prefix(
        child, scenario, users, salt=f"{child.candidate_id}:{trace.run_id}"
    )
    bad_tool = failure.implicated_tools[0]
    executed = [
        e
        for e in events
        if e.kind is EventKind.TOOL_RESULT
        and e.tool == bad_tool
        and not (isinstance(e.result, dict) and e.result.get("error") == "blocked_by_safeguard")
    ]
    return not executed


def prefilter(
    adapter: Any,
    child: AgentSpec,
    cases: list[tuple[SimulationTrace, ScenarioSpec, FailureReport]],
    max_probes: int = 2,
) -> tuple[bool, list[bool]]:
    """Reject only when every probe shows the same mistake again."""
    outcomes = [
        o for t, s, f in cases[:max_probes] if (o := probe(adapter, child, t, s, f)) is not None
    ]
    if not outcomes:
        return True, []
    return any(outcomes), outcomes
