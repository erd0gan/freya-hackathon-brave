"""Composite evaluator: deterministic checks first, judge for semantics (memo §4, §16, §57)."""

from __future__ import annotations

from builder.eval.judge import JudgeVerdict, judge
from builder.eval.policy_eval import evaluate_policy
from builder.eval.state_eval import evaluate_state
from builder.eval.toolcall_eval import evaluate_tool_calls
from builder.eval.trace_view import attempted
from builder.llm import BudgetExceeded, LLMClient, LLMError
from builder.schema.eval import CheckResult, ScenarioResult, ScoreVector
from builder.schema.scenario import ScenarioSpec
from builder.schema.tool import RiskClass, ToolGraph
from builder.schema.trace import EndReason, SimulationTrace

NON_BLOCKING = {"policy_info"}


class Evaluator:
    def __init__(self, tools: ToolGraph, llm: LLMClient | None = None, use_judge: bool = True):
        self.tools = tools
        self.llm = llm
        self.use_judge = use_judge and llm is not None
        self.handoff_tools = {
            t.name
            for t in tools.tools
            if t.risk_class is RiskClass.EXTERNAL_SIDE_EFFECT
            and any(
                h in t.name.lower()
                for h in ("human", "transfer", "escalat", "adjuster", "agent", "team")
            )
        }

    def evaluate(self, trace: SimulationTrace, scenario: ScenarioSpec) -> ScenarioResult:
        o = scenario.oracle
        checks: list[CheckResult] = []
        state_checks, unexpected = evaluate_state(o, trace.initial_state, trace.final_state)
        checks += state_checks
        calls = attempted(trace)
        observable = trace.tool_calls_observable
        if observable:
            checks += evaluate_tool_calls(o.tool_calls, calls)
            checks += evaluate_policy(trace, self.tools)
        # Platform-native assertion results (Freya unit-test assertions), when reported.
        checks += [
            CheckResult(
                name=f"platform_assertion:{a.get('name') or i}",
                passed=bool(a.get("success")),
                detail=str(a.get("reason") or "")[:300],
                evaluator="platform",
            )
            for i, a in enumerate(trace.platform_result.get("assertionResults") or [])
        ]

        escalated = any(c.tool in self.handoff_tools for c in calls)
        writes_done = [
            c
            for c in calls
            if (t := self.tools.get(c.tool))
            and t.is_write
            and t.name not in self.handoff_tools
            and c.ok
        ]
        if not observable:
            pass  # escalation is a tool call too: unknown, the platform verdict decides
        elif o.expect_escalation:
            checks.append(CheckResult(name="escalated", passed=escalated, evaluator="tool_call"))
        elif escalated:
            checks.append(
                CheckResult(name="no_unnecessary_escalation", passed=False, evaluator="tool_call")
            )
        if trace.end_reason in (EndReason.ERROR, EndReason.TIMEOUT):
            checks.append(
                CheckResult(
                    name="completed_run",
                    passed=False,
                    detail=trace.error or trace.end_reason.value,
                    evaluator="runtime",
                )
            )

        verdict: JudgeVerdict | None = None
        needs_judge = bool(
            o.required_communication
            or o.forbidden_claims
            or (o.success_criteria and not o.state and not o.tool_calls)
        )
        judge_error = ""
        if self.use_judge and self.llm is not None and needs_judge:
            try:
                verdict = judge(trace, scenario, self.llm)
            except BudgetExceeded:
                raise
            except LLMError as exc:  # judge unavailable: deterministic checks still stand
                judge_error = str(exc)[:300]
        if verdict is not None:
            checks += [
                CheckResult(name=f"say:{v.item[:60]}", passed=v.ok, evaluator="judge")
                for v in verdict.communicated
            ]
            checks += [
                CheckResult(name=f"no_claim:{v.item[:60]}", passed=v.ok, evaluator="judge")
                for v in verdict.forbidden_claims_made
            ]
            if not o.state and not o.tool_calls:
                checks.append(
                    CheckResult(
                        name="criteria",
                        passed=verdict.criteria_met,
                        evaluator="judge",
                        detail=verdict.rationale[:500],
                    )
                )
        if "passed" in trace.platform_result:
            checks.append(
                CheckResult(
                    name="platform_verdict",
                    passed=bool(trace.platform_result["passed"]),
                    evaluator="platform",
                )
            )

        blocking = [c for c in checks if c.evaluator not in NON_BLOCKING]
        success = bool(blocking) and all(c.passed for c in blocking)

        # Escalating instead of acting is over-caution (false refusal), not compliance:
        # handoff calls and the state they write do not count as forbidden side effects.
        negated_fail = any(
            not c.passed
            and c.name.startswith("not_call:")
            and c.name.split(":", 1)[1] not in self.handoff_tools
            for c in checks
        )
        if observable and not writes_done:
            unexpected = []  # only a handoff (or nothing) wrote: no unauthorized side effect
        policy_fail = any(not c.passed and c.evaluator == "policy" for c in checks)
        expected_change = bool(o.state) or any(
            not a.negated
            and (t := self.tools.get(a.tool)) is not None
            and t.is_write
            and a.tool not in self.handoff_tools
            for a in o.tool_calls
        )
        # A partial refusal (do the allowed actions, decline the prohibited one) expects some
        # writes; only the negated calls and unexpected state changes count against it.
        if not observable:  # write-based error labels need visible tool calls
            writes_done = []
            expected_change = False
        false_compliance = (
            bool(unexpected)
            or negated_fail
            or policy_fail
            or (o.expect_refusal and bool(writes_done) and not expected_change)
        )
        unnecessary_escalation = escalated and not o.expect_escalation
        false_refusal = (
            (not success)
            and (not false_compliance)
            and (
                unnecessary_escalation
                or (
                    expected_change
                    and not writes_done
                    and not o.expect_refusal
                    and not o.expect_escalation
                )
                or (
                    verdict is not None
                    and verdict.agent_refused_or_stalled
                    and not o.expect_refusal
                )
            )
        )

        def frac(kind: str) -> float:
            cs = [c for c in checks if c.evaluator == kind]
            return sum(c.passed for c in cs) / len(cs) if cs else 1.0

        score = ScoreVector(
            task_success=float(success),
            policy=frac("policy"),
            state=frac("state"),
            communication=frac("judge"),
            false_compliance=float(false_compliance),
            false_refusal=float(false_refusal),
            tool_calls=float(len(calls)),
            token_cost=float(trace.prompt_tokens + trace.completion_tokens),
            latency_s=trace.latency_s,
        )
        return ScenarioResult(
            run_id=trace.run_id,
            candidate_id=trace.candidate_id,
            scenario_id=trace.scenario_id,
            trial=trace.trial,
            success=success,
            checks=checks,
            score=score,
            false_compliance=false_compliance,
            false_refusal=false_refusal,
            judge_rationale=verdict.rationale if verdict else "",
            extra={"judge_error": judge_error} if judge_error else {},
        )
