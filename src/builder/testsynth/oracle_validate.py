"""Second-model cross-check of LLM-written success criteria (memo §58)."""

from __future__ import annotations

from pydantic import BaseModel

from builder.llm import LLMClient, Message, Role
from builder.schema.bundle import DomainSpec
from builder.schema.scenario import ScenarioSpec, ToolCallAssertion


class Verdict(BaseModel):
    consistent: bool
    reason: str = ""


CHECK_PROMPT = """You audit a generated test scenario for a customer-service agent.
Decide whether the expected outcome (criteria, expected/forbidden tool calls, refusal/escalation
flags) is exactly what the BUSINESS RULES require for this customer situation. Answer
consistent=false if the expectation contradicts a rule, is impossible with the tools, or is
ambiguous enough that two careful judges would disagree. Also answer consistent=false if the
situation relies on a premise (an earlier order, a blocked card, an existing record) that the
initial state does not contain: the test environment would then contradict the story.
The reference trajectory lists tool calls only: disclosures, read-backs and confirmations
happen in the conversation between them and are not missing steps.
Return JSON only."""


def _render_call(a: ToolCallAssertion) -> dict[str, object]:
    # Counts are meaningless for forbidden calls; showing them made the auditor reject
    # valid scenarios as "negated but min_count 1" (live run 9 Oct).
    if a.negated:
        return {"must_not_call": a.tool, "with_args": a.args or "any"}
    return {"must_call": a.tool, "args": a.args, "match": a.match.value, "min": a.min_count}


def cross_check(d: DomainSpec, s: ScenarioSpec, llm: LLMClient) -> Verdict:
    rules = "\n".join(f"[{r.id}] {r.description}" for r in d.policy.ordered())
    o = s.oracle
    body = (
        f"BUSINESS RULES:\n{rules}\n\nCUSTOMER SITUATION:\n{s.persona.prompt}\n"
        f"Facts: {s.persona.hidden_facts}\n\nEXPECTED:\ncriteria: {o.success_criteria}\n"
        f"tool calls: {[_render_call(a) for a in o.tool_calls]}\n"
        f"must say: {o.required_communication}\nmust not claim: {o.forbidden_claims}\n"
        f"refusal expected: {o.expect_refusal}; escalation expected: {o.expect_escalation}"
        f"\nREFERENCE TRAJECTORY (audit every action against policy, including arguments/order):\n"
        f"{o.reference_actions}\nInitial state: {s.initial_state}\nTools: "
        f"{[(t.name, t.inputs, t.preconditions) for t in d.tools.tools]}"
    )
    return llm.complete_structured(
        Role.JUDGE,
        Verdict,
        [Message(role="system", content=CHECK_PROMPT), Message(role="user", content=body)],
        temperature=0.0,
    )


def filter_consistent(
    d: DomainSpec, scenarios: list[ScenarioSpec], llm: LLMClient
) -> tuple[list[ScenarioSpec], list[tuple[str, str]]]:
    kept: list[ScenarioSpec] = []
    dropped: list[tuple[str, str]] = []
    for s in scenarios:
        v = cross_check(d, s, llm)
        if v.consistent:
            kept.append(s)
        else:
            dropped.append((s.scenario_id, v.reason))
    return kept, dropped
