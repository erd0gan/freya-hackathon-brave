"""Deterministic failure localisation (memo §28 step 1-2, SABER §3.7: writes first)."""

from __future__ import annotations

import re

from builder.eval.trace_view import executed_calls
from builder.schema.eval import ScenarioResult
from builder.schema.failure import FailureReport, FailureType
from builder.schema.scenario import ScenarioSpec
from builder.schema.tool import ToolGraph
from builder.schema.trace import EndReason, SimulationTrace

_TURN = re.compile(r"turn (\d+) (\w+): (.*)")


def localize(
    trace: SimulationTrace, scenario: ScenarioSpec, result: ScenarioResult, tools: ToolGraph
) -> FailureReport:
    failed = {c.name: c for c in result.checks if not c.passed}
    calls = executed_calls(trace)
    expected_tools = {a.tool for a in scenario.oracle.tool_calls if not a.negated}
    base = {
        "run_id": trace.run_id,
        "scenario_id": scenario.scenario_id,
        "candidate_id": trace.candidate_id,
    }

    def report(
        ft: FailureType, turn: int | None, tools_: list[str], why: str, sev: int = 2
    ) -> FailureReport:
        from builder.schema.failure import FAILURE_COMPONENTS

        return FailureReport(
            **base,
            failure_type=ft,
            severity=sev,
            first_bad_turn=turn,
            evidence_turns=[turn] if turn is not None else [],
            implicated_tools=tools_,
            implicated_rules=list(scenario.target_policies),
            implicated_components=FAILURE_COMPONENTS[ft],
            explanation=why,
            suggested_mutation_targets=FAILURE_COMPONENTS[ft],
        )

    # 1. Mutating deviations dominate (SABER).
    if (c := failed.get("policy:write_safeguards")) is not None:
        m = _TURN.search(c.detail)
        turn, tool, why = (int(m.group(1)), m.group(2), m.group(3)) if m else (None, "", c.detail)
        if "already succeeded" in why:
            ft = FailureType.DUPLICATE_SIDE_EFFECT
        elif "confirmation" in why:
            ft = FailureType.MISSING_CONFIRMATION
        else:
            ft = FailureType.TOOL_CALLED_TOO_EARLY
        return report(ft, turn, [tool], why, 3)
    neg = [n for n in failed if n.startswith("not_call:")]
    if neg:
        tool = neg[0].split(":", 1)[1]
        turn = next((c.turn for c in calls if c.tool == tool and not c.blocked), None)
        return report(FailureType.POLICY_VIOLATION, turn, [tool], failed[neg[0]].detail, 3)
    if (c := failed.get("state:no_unexpected_changes")) is not None:
        writes = [x for x in calls if (t := tools.get(x.tool)) and t.is_write and x.ok]
        wrong = next((x for x in writes if x.tool not in expected_tools), None)
        if wrong is not None:
            return report(
                FailureType.WRONG_TOOL,
                wrong.turn,
                [wrong.tool],
                f"unexpected change via {wrong.tool}: {c.detail}",
                3,
            )
        first = writes[0] if writes else None
        return report(
            FailureType.WRONG_ARGUMENT,
            first.turn if first else None,
            [first.tool] if first else [],
            f"unexpected state change: {c.detail}",
            3,
        )
    if "no_unnecessary_escalation" in failed:
        turn = next((x.turn for x in calls if "transfer" in x.tool or "escalat" in x.tool), None)
        return report(FailureType.UNNECESSARY_ESCALATION, turn, [], "transferred needlessly")
    # 2. Missing outcome.
    missing_state = [n for n in failed if n.startswith("state:")]
    missing_calls = [n for n in failed if n.startswith("call:")]
    if missing_state or missing_calls or "escalated" in failed:
        errors = [x for x in calls if x.ok is False and not x.blocked]
        if errors:
            e = errors[-1]
            return report(
                FailureType.TOOL_ERROR_NOT_RECOVERED,
                e.turn,
                [e.tool],
                f"{e.tool} returned {e.result}",
            )
        if trace.end_reason in (EndReason.MAX_TURNS, EndReason.TIMEOUT):
            return report(FailureType.TIMEOUT, None, [], "ran out of turns/time")
        blocked = [x for x in calls if x.blocked]
        if blocked:
            return report(
                FailureType.MISSING_CONFIRMATION,
                blocked[-1].turn,
                [blocked[-1].tool],
                "write was attempted but never confirmed/approved",
            )
        if result.false_refusal:
            return report(
                FailureType.FALSE_REFUSAL,
                None,
                sorted(expected_tools),
                "valid request not completed",
            )
        return report(
            FailureType.TOOL_NOT_CALLED,
            None,
            sorted(expected_tools),
            "expected action never happened",
        )
    # 3. Communication.
    if any(n.startswith("no_claim:") for n in failed):
        return report(FailureType.UNSUPPORTED_CLAIM, None, [], "made a forbidden claim", 3)
    if any(n.startswith("say:") for n in failed):
        return report(
            FailureType.REQUIRED_INFO_NOT_COMMUNICATED,
            None,
            [],
            "; ".join(n for n in failed if n.startswith("say:")),
        )
    if "completed_run" in failed:
        return report(FailureType.OTHER, None, [], failed["completed_run"].detail, 1)
    return report(FailureType.OTHER, None, [], ", ".join(failed) or "unknown", 1)
