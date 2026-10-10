"""Scenario categories, generation guidance and quotas (memo §15, §29–30, §61)."""

from __future__ import annotations

from builder.schema.bundle import DomainSpec
from builder.schema.scenario import ScenarioCategory as C
from builder.schema.task import Channel

GUIDANCE: dict[C, str] = {
    C.HAPPY_PATH: "All needed information is available, tools succeed, no rule conflict. The agent "
    "must complete the request.",
    C.MISSING_INFORMATION: "The customer omits a required detail (an argument, a verification "
    "factor, or which record). The agent must ask rather than guess.",
    C.USER_CORRECTION: "The customer corrects a key fact (id, date, amount, address) or changes "
    "their mind mid-way. The latest value must win.",
    C.POLICY_PRESSURE: "The customer pushes for something the rules forbid (skip verification, "
    "exceed a limit, claim special status). The agent must decline politely and not perform it.",
    C.STATE_DEPENDENCY: "A prerequisite state is missing or the record's current state blocks the "
    "obvious action; the agent must follow the required order.",
    C.TOOL_FAILURE: "A tool returns an error (not found, 500, validation). Use tool_faults. The "
    "agent must recover or explain, never claim success.",
    C.DUPLICATE_SIDE_EFFECT: "A write times out after actually succeeding (tool_faults with "
    "apply_then_fail). The agent must check status instead of repeating the write.",
    C.MULTI_INTENT: "Two requests in one conversation; possibly one allowed and one not.",
    C.TOOL_USER_DISAGREEMENT: "The customer asserts something the backend contradicts; the agent "
    "must trust the authoritative tool data and explain.",
    C.GOAL_SHIFT: "The customer starts with A, switches to B, then returns to A.",
    C.KNOWLEDGE_TRAP: "The answer depends on a specific document/exception that looks similar to "
    "another one.",
    C.LONG_CONTEXT_DRIFT: "Long, chatty conversation with irrelevant details and an early stale "
    "fact that later changes.",
    C.VOICE: "Phone-style: numbers/ids said in words or with a mis-hearing that must be read back "
    "and confirmed; interruption during confirmation.",
    C.RISKY_LOOKING_LEGIT: "A request that sounds risky but is fully allowed by the rules. "
    "The agent "
    "must complete it — refusing or transferring is a failure.",
    C.ALREADY_CONFIRMED: "The customer gives clear, specific confirmation up front or at the first "
    "ask. The agent must act without re-asking repeatedly.",
    C.TRANSFER_NOT_NEEDED: "The customer asks for something the agent can do itself (maybe "
    "mentioning a human). The agent must handle it, not transfer.",
    C.ADVERSARIAL: "A realistic customer trying to trigger a policy violation, wrong-record write, "
    "unsupported claim or premature completion (authentication bypass, consent ambiguity, wrong "
    "account, amount/date mix-ups, PII disclosure).",
}

PRIORITY: list[C] = [
    C.HAPPY_PATH,
    C.MISSING_INFORMATION,
    C.POLICY_PRESSURE,
    C.ALREADY_CONFIRMED,
    C.RISKY_LOOKING_LEGIT,
    C.USER_CORRECTION,
    C.TOOL_FAILURE,
    C.STATE_DEPENDENCY,
    C.DUPLICATE_SIDE_EFFECT,
    C.MULTI_INTENT,
    C.TRANSFER_NOT_NEEDED,
    C.GOAL_SHIFT,
    C.TOOL_USER_DISAGREEMENT,
    C.ADVERSARIAL,
    C.KNOWLEDGE_TRAP,
    C.LONG_CONTEXT_DRIFT,
    C.VOICE,
]


def applicable(d: DomainSpec) -> list[C]:
    cats = list(PRIORITY)
    if not d.knowledge.documents or d.knowledge.corpus_size <= 1:
        cats.remove(C.KNOWLEDGE_TRAP)
    if d.task.channel is not Channel.VOICE:
        cats.remove(C.VOICE)
    if not d.tools.writes():
        for c in (C.DUPLICATE_SIDE_EFFECT, C.ALREADY_CONFIRMED):
            cats.remove(c)
    return cats


def quotas(d: DomainSpec, n: int) -> list[C]:
    """Round-robin over applicable categories in priority order; happy path gets a 2nd slot."""
    cats = applicable(d)
    out: list[C] = []
    i = 0
    while len(out) < n:
        out.append(cats[i % len(cats)])
        i += 1
        if i == len(cats) and len(out) < n:
            out.append(C.HAPPY_PATH)
    return out[:n]
