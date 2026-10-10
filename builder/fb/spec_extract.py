"""Brief (one Markdown file: a clean specification or raw bank material) + tool API -> spec.

One model call extracts a JSON spec; deterministic checks compare it with the tool API and the
brief; a second call repairs what the checks reject. `to_generator` turns the spec into the
input of fb.generate.build. Nothing here knows any particular brief.
"""

from __future__ import annotations

import copy
import datetime
import json
import re
from typing import Any

from fb.llm import chat, parse_json
from fb.nodecode.embed import namespace
from fb.openapi_tools import ToolDef
from fb.trace import Trace

POLICY_DEFAULTS: dict[str, Any] = namespace()["POLICY_DEFAULTS"]

LINE_KEYS = {
    "opening": "First sentence of the call, exactly what the brief requires (who is calling, "
    "for example that the call is recorded). It must not mention the reason for the call. Never "
    "use the word 'recorded' in it (say for example 'we are recording this call'): that word is "
    "reserved for a recorded promise.",
    "who_question": "The question that checks who is on the line, with call variables written "
    "as {{variable_name}}, for example 'Am I speaking with {{title}} {{last_name}}?'",
    "ai_answer": "What to say when asked whether this is a robot or an AI.",
    "scam_answer": "What to say when asked whether the call is a scam ('' if the brief is silent).",
    "deceased": "What to say when told the customer has died.",
    "identity_intro": "One short sentence before the first security question (no reason for "
    "the call).",
    "identity_why": "The one-time explanation when the customer will not answer a security "
    "question.",
    "identity_failed": "What to say when the identity check failed (for example where to go "
    "instead).",
    "present_intro": "The sentence that says why you are calling, said right after identity is "
    "verified, before the amounts.",
    "today_question": "The first payment question for one product, exactly as the brief words it.",
    "multi_today_question": "The first payment question when there are several products.",
    "confirm_question": "The question after the read-back, exactly as the brief words it.",
    "recorded": "The sentence the brief requires after a promise is recorded ('' if none).",
    "paid": "What to say when the customer says they already paid.",
    "sympathy": "One short sentence of sympathy for hardship.",
    "support_offer": "The offer to connect a hardship customer with the right team, as a "
    "yes/no question.",
    "help_first": "What to say the first time a customer asks for a human, when the brief "
    "says to offer help first.",
    "callback_booked": "Short sentence after a callback is booked.",
    "goodbye": "Thank you and goodbye.",
    "no_info": "What to say to a question the agent cannot answer.",
    "rest_overdue": "The note said with any amount below the full past-due amount (for example "
    "'The rest stays overdue.'), '' if the brief has none.",
}

SCHEMA = {
    "bank_name": "string",
    "notes": [
        "every override you resolved (which source won and why) and every ambiguity "
        "with the reading you chose"
    ],
    "call_variables": {
        "customer_id": "name of the call variable holding the customer id",
        "title": "name of the variable for the form of address, or ''",
        "first_name": "...",
        "last_name": "...",
        "full_name": "name of a variable with the whole name, or ''",
        "now": "name of the variable with the current date and time",
    },
    "lines": {k: "string" for k in LINE_KEYS},
    "forbidden_before_verify": ["words never said before identity is verified"],
    "identity": {
        "questions": [
            {
                "field": "value of the verify_identity field enum",
                "ask": "the question to the customer",
                "kind": "date (a full date) | digits (a number said digit by digit, e.g. last "
                "digits of a phone) | year | text (letters and digits as written, e.g. a postcode)",
            }
        ],
        "swap_hint": "the hint value the tool returns for a day/month swap, or ''",
        "swap_instruction": "what to ask the customer when that hint comes",
        "date_order": "DMY or MDY: how to read a date said only as numbers",
    },
    "outcomes": {
        k: ["close_call outcome", "close_call reason or ''"]
        for k in (
            "unclear",
            "callback",
            "no_callback",
            "wrong_number",
            "deceased",
            "failed",
            "declined",
        )
    },
    "callback": {
        "start": "HH:MM",
        "end": "HH:MM (both included)",
        "include_today": "bool",
        "business_days_ahead": "int (1 = up to the next business day)",
        "business_days_only": "bool",
        "reasks": "int: times to ask again after a time outside the window before giving up",
    },
    "calendar": {"holidays": ["YYYY-MM-DD"], "workdays": "[0..6], 0 = Monday"},
    "bands": [
        {
            "name": "string",
            "min_days_late": "int",
            "max_days_late": "int",
            "deadline_days": "int",
            "deadline_unit": "business | calendar",
            "attempts": "int: persuasion attempts",
        }
    ],
    "attempt1": {"rule": "last_business_day_of_week | nth_business_day", "n": "int for nth"},
    "attempt_plan": [{"amount": "current | full | floor", "date": "attempt1 | deadline"}],
    "tool_fields": {
        "_": "the request field name of each role, per tool, exactly as in the tool API",
        "verify_identity": {
            "customer_id": "...",
            "field": "...",
            "answer": "...",
            "response": {
                "verified": "...",
                "all_verified": "...",
                "wrong_answers_left": "...",
                "hint": "...",
            },
        },
        "get_account": {"customer_id": "..."},
        "create_promise": {
            "customer_id": "...",
            "product_id": "...",
            "amount": "...",
            "date": "...",
        },
        "add_note": {"customer_id": "...", "product_id": "...", "reason": "..."},
        "schedule_callback": {"customer_id": "...", "date": "...", "time": "..."},
        "transfer_to_agent": {"customer_id": "...", "queue": "..."},
        "close_call": {"customer_id": "...", "outcome": "...", "reason": "..."},
    },
    "product_fields": {
        "list": "name of the products array in the get_account response",
        "product_id": "...",
        "type": "...",
        "past_due": "...",
        "days_late": "...",
    },
    "floor_field": {"<product type>": "<response field holding the floor (minimum) amount>"},
    "product_names": {
        "<product type>": "spoken name, response fields in single braces, "
        "for example 'credit card ending {card_last4}'"
    },
    "neg": {
        "outcomes": {"promise": "...", "paid": "...", "transferred": "...", "no_promise": "..."},
        "reasons": {
            "refused": "...",
            "hardship": "...",
            "dispute": "...",
            "service_error": "...",
            "paid": "reason used in a note for an already-paid product",
        },
        "queues": {"restructuring": "...", "human": "...", "support": "..."},
        "transfer_reasons": {"restructuring": "...", "human": "...", "support": "..."},
        "notes": "bool: products without a promise get add_note",
        **{k: "see the policy list" for k in POLICY_DEFAULTS},
    },
}

POLICY_HELP = """Policy knobs in "neg" (set every one from the brief; the value in brackets is
only what one example bank used, never copy it without reading the brief):
- human_requests_to_transfer [2]: 1 = transfer on the first request for a human; 2 = first ask
  once whether you can help, transfer when they insist.
- stop_requests_to_close [2]: how many times the customer says they do not want to talk before
  the call is closed.
- promise_attempts [2]: create_promise calls per product before giving up (2 = one retry).
- record_failure ["continue"]: after the last failed create_promise: "continue" (tell the
  customer, keep other promises, note/close as service error) or "transfer".
- record_failure_queue ["support"]: semantic queue (restructuring | human | support) for that.
- hardship ["offer_support"]: "offer_support" (sympathy, no persuasion, offer a transfer;
  yes -> transfer, no -> close as hardship), "transfer" (transfer at once) or "close".
- hardship_queue ["support"]: semantic queue for hardship transfers.
- no_promise_reason ["first_product"]: with no promise recorded, the close reason is the
  reason of the "first_product" (in get_account order) that is not paid, or "priority".
- reason_priority [...]: order of semantic reasons when no_promise_reason is "priority".
- notes_on_transfer [false]: true if notes are still added on a transferred call.
- vague_date_asks [1]: times to ask for the exact day after a vague answer before proposing
  the deadline.
- floor_offers [1]: times the floor may be offered after "too much" / an amount below it.
Semantic reasons/outcomes map to the exact enum values of the tool API."""

PROMPT = """You turn a bank's brief into a JSON specification for a debt-collection phone agent.
The brief may be a clean specification or raw bank material (mails, a policy extract, a memo
that changes the policy, a code list). Read ALL of it. When two sources disagree, the later or
explicitly overriding one wins (a memo that changes a policy replaces the policy's value from
its effective date; the call's date is the variable `now`, so apply what is in force for calls
being made now). Record each such decision in "notes".

Rules:
- Take every value from the brief or the tool API below. Never invent a rule, a number, a date
  or a name. If the brief is silent, use the most literal reading and say so in "notes".
- Every outcome, reason, queue and field value must be one of the tool API's enum values.
- Sentences in "lines" are spoken to the customer: short, plain English. Where the brief gives
  exact words, use them exactly.
- The agent states every late product before asking anything, then handles products one by one
  in get_account order (customers can have up to three products even if the brief shows one).

{policy_help}

Line meanings:
{line_help}

Tool API (fields, enums, responses):
{tools}

Output ONE JSON object with exactly this shape (values are descriptions of what goes there):
{schema}

BRIEF:
<<<
{brief}
>>>"""


def tool_summary(openapi: dict[str, Any], tools: dict[str, ToolDef]) -> str:
    out = []
    for name, t in tools.items():
        out.append(f"{name}  ({t.method} {t.url})  {t.summary}")
        for f in t.fields:
            bits = [f.type]
            if f.required:
                bits.append("required")
            if f.enum:
                bits.append("enum " + json.dumps(f.enum))
            out.append(f"    {f.name}: {', '.join(bits)}  {f.description}".rstrip())
        if t.response:
            out.append("    response: " + json.dumps(t.response)[:1500])
    return "\n".join(out)


def _enum(tools: dict[str, ToolDef], tool: str, *fields: str) -> list[str]:
    t = tools.get(tool)
    if not t:
        return []
    for f in t.fields:
        if f.name in fields:
            return list(f.enum)
    for f in t.fields:
        if f.enum and any(x in f.name for x in fields):
            return list(f.enum)
    return []


def check(spec: dict[str, Any], tools: dict[str, ToolDef], brief: str) -> list[str]:
    """Deterministic checks against the tool API. Returns problems as sentences."""
    bad: list[str] = []
    tf = spec.get("tool_fields") or {}

    def role(tool: str, r: str, *alts: str) -> tuple[str, ...]:
        named = (tf.get(tool) or {}).get(r) if isinstance(tf.get(tool), dict) else None
        return ((named,) if named else ()) + (r, *alts)

    outs = _enum(tools, "close_call", *role("close_call", "outcome", "result", "status"))
    note_reasons = _enum(tools, "add_note", *role("add_note", "reason", "code"))
    queues = _enum(tools, "transfer_to_agent", *role("transfer_to_agent", "queue", "team"))
    fields = _enum(tools, "verify_identity", *role("verify_identity", "field", "question"))
    neg = spec.get("neg") or {}
    for k, v in (spec.get("outcomes") or {}).items():
        if outs and (not isinstance(v, list) or v[0] not in outs):
            bad.append(f"outcomes.{k}: {v!r} is not a close_call outcome {outs}")
    for k, v in (neg.get("outcomes") or {}).items():
        if outs and v not in outs:
            bad.append(f"neg.outcomes.{k}: {v!r} is not a close_call outcome {outs}")
    if neg.get("notes") and note_reasons:
        for k, v in (neg.get("reasons") or {}).items():
            if v not in note_reasons:
                bad.append(f"neg.reasons.{k}: {v!r} is not an add_note reason {note_reasons}")
    for k, v in (neg.get("queues") or {}).items():
        if queues and v not in queues:
            bad.append(f"neg.queues.{k}: {v!r} is not a transfer queue {queues}")
    qs = (spec.get("identity") or {}).get("questions") or []
    if not qs:
        bad.append("identity.questions is empty")
    for x in qs:
        if fields and x.get("field") not in fields:
            bad.append(f"identity question field {x.get('field')!r} not in {fields}")
    for h in (spec.get("calendar") or {}).get("holidays") or []:
        try:
            datetime.date.fromisoformat(str(h))
        except ValueError:
            bad.append(f"holiday {h!r} is not YYYY-MM-DD")
    bands = spec.get("bands") or []
    if not bands:
        bad.append("bands is empty")
    for b in bands:
        for key in ("min_days_late", "max_days_late", "deadline_days", "attempts"):
            if not isinstance(b.get(key), int):
                bad.append(f"band {b.get('name')!r}: {key} must be an integer")
    for k in POLICY_DEFAULTS:
        if neg.get(k) is None:
            bad.append(f"neg.{k} is missing")
    for tool, roles in (spec.get("tool_fields") or {}).items():
        t = tools.get(tool)
        if not t or not isinstance(roles, dict):
            continue
        names = {f.name for f in t.fields}
        for role, name in roles.items():
            if role in ("response", "_") or not name:
                continue
            if name not in names:
                bad.append(
                    f"tool_fields.{tool}.{role}: {name!r} is not a field of {tool} {sorted(names)}"
                )
    lines = spec.get("lines") or {}
    for k in ("opening", "who_question", "today_question", "confirm_question", "goodbye"):
        if not str(lines.get(k) or "").strip():
            bad.append(f"lines.{k} is empty")
    if "recorded" in str(lines.get("opening") or "").lower():
        bad.append("lines.opening must not contain the word 'recorded'")
    for k, v in lines.items():
        if k != "who_question" and ("{{" in str(v) or "}}" in str(v)):
            bad.append(f"lines.{k} must not contain a placeholder")
    for tpl in (spec.get("product_names") or {}).values():
        if "{{" in str(tpl):
            bad.append("product_names use single braces")
    return bad


def extract(
    brief: str, openapi: dict[str, Any], tools: dict[str, ToolDef], trace: Trace
) -> dict[str, Any]:
    prompt = PROMPT.format(
        policy_help=POLICY_HELP,
        line_help="\n".join(f"- {k}: {v}" for k, v in LINE_KEYS.items()),
        tools=tool_summary(openapi, tools),
        schema=json.dumps(SCHEMA, indent=1),
        brief=brief,
    )
    msgs = [{"role": "user", "content": prompt}]
    reply = chat(trace, "extract the spec from the brief", msgs)
    spec = parse_json(reply)
    problems = check(spec, tools, brief)
    if problems:
        trace.note(f"spec check: {len(problems)} problems: " + "; ".join(problems)[:600])
        msgs += [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": "These checks failed:\n- "
                + "\n- ".join(problems)
                + "\nFix them using the brief and the tool API. Output the whole JSON again.",
            },
        ]
        reply = chat(trace, "repair the spec after failed checks", msgs)
        spec = parse_json(reply)
        problems = check(spec, tools, brief)
        if problems:
            trace.note("spec still has problems: " + "; ".join(problems)[:600])
    for n in spec.get("notes") or []:
        trace.note("spec decision: " + str(n)[:300])
    return spec


def _var(spec: dict[str, Any], role: str) -> str:
    return str((spec.get("call_variables") or {}).get(role) or role)


def to_generator(spec: dict[str, Any]) -> dict[str, Any]:
    """Spec -> input of fb.generate.build (cfg for the code nodes + conversation parts)."""
    s = copy.deepcopy(spec)
    neg = dict(s.get("neg") or {})
    for k, v in POLICY_DEFAULTS.items():  # check() reported missing ones to the trace
        neg.setdefault(k, v)
    lines = dict(s.get("lines") or {})
    neg["lines"] = {
        k: lines.get(k, "")
        for k in (
            "present_intro",
            "today_question",
            "multi_today_question",
            "confirm_question",
            "recorded",
            "paid",
            "sympathy",
            "support_offer",
            "help_first",
            "goodbye",
            "rest_overdue",
        )
        if lines.get(k)
    }
    neg.setdefault("attempt_plan", s.get("attempt_plan") or [])
    if s.get("attempt_plan"):
        neg["attempt_plan"] = s["attempt_plan"]
    bands = []
    for b in s.get("bands") or []:
        bands.append(
            {
                "name": b.get("name"),
                "min_days_late": b.get("min_days_late"),
                "max_days_late": b.get("max_days_late"),
                "deadline_business_days": b.get("deadline_days"),
                "deadline_days": b.get("deadline_days"),
                "deadline_unit": b.get("deadline_unit") or "business",
                "attempts": b.get("attempts"),
            }
        )
    cal = s.get("calendar") or {}
    cb = s.get("callback") or {}
    pf = s.get("product_fields") or {}
    cfg = {
        "holidays": cal.get("holidays") or [],
        "workdays": cal.get("workdays") if cal.get("workdays") is not None else [0, 1, 2, 3, 4],
        "bands": bands,
        "attempt1": s.get("attempt1") or {"rule": "last_business_day_of_week"},
        "callback": {
            "start": cb.get("start") or "10:00",
            "end": cb.get("end") or "19:00",
            "include_today": cb.get("include_today", True),
            "business_days_ahead": cb.get("business_days_ahead") or 1,
            "business_days_only": cb.get("business_days_only", True),
        },
        "product_fields": {k: v for k, v in pf.items() if k != "list" and v},
        "floor_field": s.get("floor_field") or {},
        "product_names": s.get("product_names") or {},
        "date_order": (s.get("identity") or {}).get("date_order") or "DMY",
        "neg": neg,
    }
    return {
        "cfg": cfg,
        "bank_name": s.get("bank_name") or "the bank",
        "lines": lines,
        "forbidden_before_verify": s.get("forbidden_before_verify") or [],
        "identity": s.get("identity") or {},
        "outcomes": s.get("outcomes") or {},
        "callback_reasks": int(cb.get("reasks") if cb.get("reasks") is not None else 1),
        "products_list": pf.get("list") or "products",
        "tool_fields": {k: v for k, v in (s.get("tool_fields") or {}).items() if k != "_"},
        "call_variables": s.get("call_variables") or {},
    }


def quotes_found(spec: dict[str, Any], brief: str) -> list[str]:
    """Lines the brief gives in quotes that the spec does not use (for the trace)."""
    quoted = set(re.findall(r'"([^"]{12,160})"', brief))
    used = " ".join(str(v) for v in (spec.get("lines") or {}).values())
    return [q for q in quoted if q not in used and not q.startswith("{")]
