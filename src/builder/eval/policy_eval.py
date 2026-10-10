"""Deterministic policy checks over a trace (memo §17.1, §57).

Replays the conversation through the MutationGate's checks — independently of
whether the candidate itself ran a gate — so B0 and gated candidates are
judged by the same yardstick. Ungrounded arguments are reported as
informational (the heuristic can false-positive on formatting).
"""

from __future__ import annotations

from builder.eval.trace_view import executed_calls
from builder.runtime.mutation_gate import ConversationLedger, check_mutation
from builder.schema.eval import CheckResult
from builder.schema.tool import ToolGraph
from builder.schema.trace import EventKind, SimulationTrace


def evaluate_policy(trace: SimulationTrace, tools: ToolGraph) -> list[CheckResult]:
    ledger = ConversationLedger()
    remaining = executed_calls(trace)
    violations: list[str] = []
    ungrounded: list[str] = []
    agent_text: list[str] = []
    for e in trace.events:
        if e.kind is EventKind.USER:
            if agent_text:
                ledger.on_agent("\n".join(agent_text))
                agent_text = []
            ledger.on_user(e.content)
        elif e.kind is EventKind.AGENT and e.content:
            agent_text.append(e.content)
        elif e.kind is EventKind.TOOL_RESULT:
            call = next((c for c in remaining if c.tool == e.tool), None)
            if call is None:
                continue
            remaining.remove(call)
            if call.blocked:
                if isinstance(call.result, dict) and (
                    call.result.get("status") == "confirmation_required"
                ):
                    ledger.request_confirmation(call.tool, call.args)  # mirror the runtime
                continue
            spec = tools.get(call.tool)
            if spec is not None and spec.is_write:
                chk = check_mutation(spec, call.args, ledger, tools)
                bad = [
                    r
                    for r in chk.reasons
                    if r.startswith(("explicit confirmation", "this exact", "prerequisite"))
                ]
                if bad:
                    violations.append(f"turn {e.turn} {call.tool}: {'; '.join(bad)}")
                if not chk.arguments_grounded:
                    ungrounded.append(f"turn {e.turn} {call.tool}")
            ledger.on_tool(call.tool, call.args, bool(call.ok), call.result)
    return [
        CheckResult(
            name="policy:write_safeguards",
            passed=not violations,
            detail=" | ".join(violations)[:1000],
            evaluator="policy",
        ),
        CheckResult(
            name="policy:grounded_args",
            passed=not ungrounded,
            detail=" | ".join(ungrounded)[:500],
            evaluator="policy_info",
        ),
    ]
