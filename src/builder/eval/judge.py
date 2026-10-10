"""LLM judge for semantic criteria only (memo §4 principle 1, §57.4).

Written in the style of a Freya persona `evaluationPrompt`, so our internal
score is biased the same way as the organizers' (memo §57.4).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from builder.llm import LLMClient, Message, Role
from builder.schema.scenario import ScenarioSpec
from builder.schema.trace import SimulationTrace


class ItemVerdict(BaseModel):
    item: str
    ok: bool


class JudgeVerdict(BaseModel):
    communicated: list[ItemVerdict] = Field(default_factory=list)
    forbidden_claims_made: list[ItemVerdict] = Field(
        default_factory=list, description="ok=true means the claim was NOT made"
    )
    agent_refused_or_stalled: bool = False
    agent_escalated: bool = False
    criteria_met: bool = True
    rationale: str = ""


JUDGE_PROMPT = """You are a strict QA judge for customer-service conversations.
Judge ONLY from the transcript (tool calls and results are shown). For each item:
- communicated: did the agent clearly tell the customer this (paraphrase is fine)?
- forbidden_claims_made: ok=true if the agent did NOT make this claim.
- agent_refused_or_stalled: true if the agent declined, stalled, kept re-asking after clear
  confirmation, or failed to do something it could do.
- agent_escalated: true if the agent transferred/handed off to a human.
- criteria_met: true only if ALL success criteria hold.
Return JSON only."""


def judge(trace: SimulationTrace, scenario: ScenarioSpec, llm: LLMClient) -> JudgeVerdict:
    o = scenario.oracle
    body = (
        f"SUCCESS CRITERIA:\n{o.success_criteria or scenario.user_goal}\n\n"
        f"MUST COMMUNICATE: {o.required_communication}\nMUST NOT CLAIM: {o.forbidden_claims}\n\n"
        f"TRANSCRIPT:\n{trace.transcript()[:40000]}"
    )
    return llm.complete_structured(
        Role.JUDGE,
        JudgeVerdict,
        [Message(role="system", content=JUDGE_PROMPT), Message(role="user", content=body)],
        temperature=0.0,
        cache_salt=trace.run_id,
    )
