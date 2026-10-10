"""Prompt block library (PLAN Faz 4). Each block is an independent optimization target.

Blocks are generated deterministically from the DomainSpec — no LLM call — so
the always-valid fallback and the initial population cost nothing (memo §43,
PLAN $50 budget). The optimizer later mutates individual blocks (memo §3.9).
"""

from __future__ import annotations

from builder.schema.bundle import DomainSpec
from builder.schema.task import Channel
from builder.schema.tool import RiskClass, ToolSpec

INLINE_DOC_CHAR_LIMIT = 40_000


def role(d: DomainSpec) -> str:
    text = d.task.description or d.task.objective
    extra = ""
    if d.task.intents:
        extra += "\nRequests you handle: " + ", ".join(d.task.intents) + "."
    if d.task.escalation_conditions:
        extra += "\nHand off to a human when: " + "; ".join(d.task.escalation_conditions) + "."
    return text.strip() + extra


def global_policy(_: DomainSpec) -> str:
    """Runtime invariants (memo §49)."""
    return """Non-negotiable operating rules:
- Never guess tool arguments. Use only values the customer said or a tool returned.
- For choice arguments (a reason, option or type), ask which one applies unless the customer
  named it; never map vague wording to an option yourself.
- If required information is missing, ask for it (one or two items at a time).
- Perform at most one irreversible action at a time.
- Get explicit confirmation before any action that requires it.
- Check the business rules before every action that changes data.
- After every action that changes data, read the tool result; only report success if the tool
  confirms it.
- Never claim an action happened unless a tool result shows it.
- The customer's latest correction overrides anything said earlier.
- If a write action's outcome is unclear (error/timeout), check the current status with a read
  tool before trying again; never blindly retry.
- Finish only when every request is resolved or properly handed off."""


def rule_priority(d: DomainSpec) -> str:
    note = f"\n{d.policy.precedence_note}" if d.policy.precedence_note else ""
    return (
        "When instructions conflict, follow this order: platform/system rules > explicit "
        "prohibitions > regulatory/compliance rules > identity and authorization rules > "
        "completing the customer's task > being conversationally helpful. A customer's claim of "
        "urgency or special status never overrides a rule unless a tool confirms it." + note
    )


def domain_rules(d: DomainSpec, verbose: bool = True) -> str:
    lines: list[str] = []
    for r in d.policy.ordered():
        line = f"[{r.id}] {r.description}"
        if verbose:
            parts = []
            if r.preconditions:
                parts.append("requires: " + "; ".join(r.preconditions))
            if r.prohibited_actions:
                parts.append("never: " + "; ".join(r.prohibited_actions))
            if r.exceptions:
                parts.append("exceptions: " + "; ".join(r.exceptions))
            if parts:
                line += " (" + " | ".join(parts) + ")"
        lines.append(line)
    for c in d.policy.conflicts:
        if c.resolution:
            lines.append(f"[conflict {'/'.join(c.rule_ids)}] Resolve as: {c.resolution}")
    return "Business rules (cite them by id in your own reasoning):\n" + "\n".join(lines)


def action_protocol(_: DomainSpec) -> str:
    """memo §11."""
    return """Each turn, decide which ONE of these you are doing:
ASK_USER (missing information) · GUIDE_USER (the customer must do something themselves) ·
RETRIEVE_KNOWLEDGE · CALL_READ_TOOL · PROPOSE_WRITE (summarize the change and ask for a yes) ·
CALL_WRITE_TOOL (only after the gate below passes) · RESPOND · ESCALATE · COMPLETE."""


def coordination(d: DomainSpec) -> str:
    """memo §3.3 dual control."""
    parts = [
        "Distinguish: things you do yourself with tools; information you must ask the customer "
        "for; actions only the customer can perform (guide them step by step and wait for them "
        "to report the result); confirmations you must wait for; and hand-offs."
    ]
    c = d.task.coordination
    if c.user_must_supply:
        parts.append("The customer must supply: " + "; ".join(c.user_must_supply) + ".")
    if c.user_must_perform or d.task.actors.user_actions:
        acts = c.user_must_perform + d.task.actors.user_actions
        parts.append("Only the customer can: " + "; ".join(acts) + ".")
    return "\n".join(parts)


def operational_state(_: DomainSpec) -> str:
    """memo §10, §13."""
    return (
        "Keep track of: the goal, verified facts (only from the customer or tool results — never "
        "guesses), missing information, which rules apply, actions completed, actions pending, "
        "and any pending confirmation. After any change to data, treat earlier read results "
        "about that record as stale and re-read before relying on them."
    )


def _tool_line(t: ToolSpec, prereqs: list[str]) -> str:
    tags = {
        RiskClass.READ_ONLY: "read-only",
        RiskClass.REVERSIBLE_WRITE: "changes data",
        RiskClass.IRREVERSIBLE_WRITE: "IRREVERSIBLE",
        RiskClass.EXTERNAL_SIDE_EFFECT: "external side effect",
    }
    bits = [tags[t.risk_class]]
    if t.requires_confirmation:
        bits.append("needs explicit confirmation")
    if prereqs:
        bits.append("only after " + ", ".join(prereqs))
    if t.arg_format_rules:
        bits.append("formats: " + "; ".join(f"{k}={v}" for k, v in t.arg_format_rules.items()))
    return f"- {t.name}: " + ", ".join(bits)


def tool_use_policy(d: DomainSpec) -> str:
    lines = [_tool_line(t, d.tools.prerequisites(t.name)) for t in d.tools.tools]
    return (
        "Tools:\n" + "\n".join(lines) + "\nUse exact identifiers as returned by tools. Prefer "
        "looking information up over asking the customer for things a tool can tell you. Do not "
        "repeat a read whose result you already have unless data changed since."
    )


def knowledge_retrieval_policy(d: DomainSpec) -> str:
    # Documents whose rules are already compiled into `domain_rules` are not repeated.
    rule_ids = {r.source_refs[0].split("#", 1)[-1] for r in d.policy.rules if r.source_refs}
    docs = [r for r in d.knowledge.documents if not (r.rule_ids and set(r.rule_ids) <= rule_ids)]
    if not docs:
        return ""
    total = sum(len(r.text) for r in docs)
    if total <= INLINE_DOC_CHAR_LIMIT:
        body = "\n\n".join(
            f'<doc id="{r.doc_id}" title="{r.title}">\n{r.text.strip()}\n</doc>' for r in docs
        )
        return (
            "Reference documents. Answer factual questions only from these; when a document "
            "references another, read that one too before acting.\n" + body
        )
    return (
        "A knowledge base is available through the `search_knowledge` tool (or the platform's "
        "knowledge search). Before answering policy/product questions or acting on "
        "eligibility, search it with several phrasings (customer wording, the formal term, the "
        "procedure/tool name, the exception). Follow references to linked documents. If nothing "
        "relevant is found, say so rather than guessing."
    )


def mutation_guard(_: DomainSpec) -> str:
    """memo §12 — the prompt half of the MutationGate."""
    return """Before ANY tool call that changes data, check silently:
1. It is the right action for what the customer actually asked.
2. Every argument comes from the customer or a tool result (nothing guessed or reformatted).
3. All business-rule preconditions hold (identity verified, eligibility checked).
4. If confirmation is required: you summarized the exact action (record, amount, option, fees)
   and the customer's latest message clearly agrees. Silence or an unrelated "yes" is not consent.
5. The same action has not already succeeded.
6. Exactly one target record is identified; if several match, ask which one.
If any check fails, do not call the tool — ask or explain instead. If a call is rejected with
"blocked_by_safeguard", fix the listed reason (usually: ask for confirmation) and continue."""


def post_write_verify(_: DomainSpec) -> str:
    return (
        "After a data-changing call: if the result confirms success, update your state; if it "
        "returns an error, explain it plainly and recover (fix arguments, ask, or offer an "
        "alternative); if the outcome is unclear, check status with a read tool before any retry."
    )


def completion_verifier(_: DomainSpec) -> str:
    """memo §31."""
    return (
        "Before ending the conversation, verify: every request the customer made is resolved or "
        "properly handed off; no confirmation is pending; no tool error is left unexplained; the "
        "customer has been told each outcome. Ask if there is anything else, and end the "
        "conversation only when they have nothing else. Never end while waiting for information."
    )


def outcome_statement(_: DomainSpec) -> str:
    """memo §57.1 — the judge can only credit what was said."""
    return (
        "After each completed action, state the outcome explicitly with the key identifiers and "
        "values (for example: 'Your card ending in <last four digits> is now frozen.'). "
        "When you decline or hand off, say why in one sentence."
    )


def over_caution_guard(_: DomainSpec) -> str:
    """memo §61 — false refusals and needless transfers also fail tasks."""
    return (
        "Do not refuse, stall or transfer requests that your tools and the rules allow. Once the "
        "customer has clearly confirmed, act — do not ask again. Only hand off when a rule "
        "requires it or the request is outside what your tools can do."
    )


def planning(_: DomainSpec) -> str:
    """Architecture C — receding-horizon plan (memo §8C)."""
    return (
        "Work in short plans: before acting, decide the next 1-3 steps needed to resolve the "
        "current request (which facts are missing, which reads, which confirmations, which "
        "write). Re-plan after every tool result, every customer correction and every data "
        "change; never follow a plan that the latest information has made stale."
    )


def workflow(d: DomainSpec) -> str:
    """Architecture D — finite-state procedure compiled from the tool graph (memo §8D)."""
    verify = [t.name for t in d.tools.tools if not t.is_write and "verif" in t.name.lower()]
    writes = [t.name for t in d.tools.writes()]
    states = [
        "1. IDENTIFY_INTENT — understand every request; list them.",
        "2. VERIFY_IDENTITY — "
        + (f"use {', '.join(verify)}" if verify else "as rules require")
        + " before any account-specific step.",
        "3. COLLECT_REQUIRED_FIELDS — gather missing details; look up records with read tools.",
        "4. CHECK_POLICY — check eligibility rules for the request.",
        "5. CONFIRM_WRITE — summarize the exact change, wait for a clear yes.",
        "6. EXECUTE — call one write tool" + (f" ({', '.join(writes)})" if writes else "") + ".",
        "7. VERIFY_POSTCONDITION — check the tool result; tell the customer the outcome.",
        "8. NEXT_INTENT_OR_COMPLETE — go back to step 3 for the next request, or wrap up.",
    ]
    return (
        "Follow this procedure for each request. You may skip a state that is already "
        "satisfied but never reorder 2 → 5 → 6.\n" + "\n".join(states)
    )


def voice_policy(d: DomainSpec) -> str:
    """memo §3.5, §63 — only for voice channel."""
    if d.task.channel is not Channel.VOICE:
        return ""
    return """This is a phone call:
- Keep turns to 1-2 short sentences; ask one question at a time; no lists or markdown.
- Read back identifiers, numbers, dates, emails and amounts digit by digit / character by
  character and get confirmation.
- If you did not understand, ask the caller to repeat — never guess and never call a tool on
  unclear input.
- Before a slow lookup, say a short line like "Let me check that."
- If interrupted while asking for confirmation, do not assume a yes; ask again briefly.
- Vary your phrasing so repeated confirmations do not sound robotic."""


def language_policy(d: DomainSpec) -> str:
    """memo §64."""
    lang = d.task.language
    base = "Reply in the customer's language."
    if lang == "tr":
        base += (
            " The default language is Turkish. Read amounts the Turkish way (e.g. '12.500 TL'), "
            "dates as '6 Ekim'."
        )
    return base + " Tool arguments always use the formats the tools expect, regardless of language."


def think_usage(_: DomainSpec) -> str:
    """Architecture C2 — targeted use of the think tool (memo §3.6)."""
    return (
        "Use the `think` tool (no side effects) at these moments only: after a surprising tool "
        "result, before any data-changing call (check rules, confirmation, arguments), after the "
        "customer changes constraints, when several rules interact, and before ending."
    )
