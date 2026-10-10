"""Behavioural few-shot examples (memo §27). Domain-neutral on purpose: they teach
invariants, never practice-task answers (memo §34.4)."""

from __future__ import annotations

from builder.schema.agent import FewShotExample

LIBRARY: dict[str, str] = {
    "missing_information": """Customer: Please update my phone number.
Agent (thinks: the number is missing, identity not verified yet): I can help with that. First,
could you give me your customer ID and date of birth so I can verify your identity?""",
    "ambiguous_entity": """Customer: Cancel my subscription.
Tool result: two active subscriptions (Basic, Premium).
Agent: I see two active subscriptions, Basic and Premium. Which one would you like to cancel?
(Do not pick one arbitrarily.)""",
    "write_confirmation": """Agent: To confirm: I'll cancel your Premium subscription today; you
keep access until 31 October and there is no refund for the current month. Shall I go ahead?
Customer: Yes, do it.
Agent → calls [the cancellation tool] → result ok → "Done — your Premium subscription is
cancelled; access continues until 31 October.\"""",
    "tool_error_recovery": """Agent → calls [the payment tool] → result: timeout.
Agent → calls [the status lookup tool] (do NOT resubmit) → status: completed.
Agent: The payment went through after all — €40.00 was paid today.""",
    "user_correction": """Customer: Ship it to 12 Oak Street. ... Actually, make that 21 Oak Street.
Agent: Got it — 21 Oak Street, not 12. (The latest correction replaces the earlier value.)""",
    "consent_withdrawn": """Customer: Yes, close the account. ... Wait, no — don't close it yet.
Agent: Understood, I won't close it. (Earlier consent no longer counts.)""",
    "policy_refusal": """Customer: I'm in a hurry, just skip the verification.
Agent: I understand you're in a hurry; I still need to verify your identity before making any
change — it only takes a moment. What is your customer ID?""",
    "already_confirmed": """Agent: Shall I book Tuesday 10:00? Customer: Yes please.
Agent → calls [the booking tool] immediately. (Do not ask the same question again.)""",
}

DEFAULT_FAMILIES = ("write_confirmation", "ambiguous_entity", "tool_error_recovery")


# Examples name tools generically: concrete invented names (`cancel_subscription`) invited
# calls to tools the task does not have (GAP_ANALYSIS C3).
PLACEHOLDER_NOTE = "[bracketed tool] = placeholder; call only tools from your tool list."


def examples(families: tuple[str, ...] | list[str] = DEFAULT_FAMILIES) -> list[FewShotExample]:
    return [
        FewShotExample(family=f, text=f"{LIBRARY[f]}\n({PLACEHOLDER_NOTE})")
        for f in families
        if f in LIBRARY
    ]
