"""Brief (one Markdown file: a clean specification or raw bank material) + tool API -> spec.

One model call extracts a JSON spec; deterministic checks compare it with the tool API and the
brief; a second call repairs what the checks reject. `to_generator` turns the spec into the
input of fb.generate.build. Nothing here knows any particular brief.
"""

from __future__ import annotations

import copy
import datetime
import itertools
import json
import re
import string
from typing import Any

from fb.generate import find_field, paid_close_reason
from fb.llm import chat, parse_json
from fb.nodecode.embed import namespace
from fb.openapi_tools import ToolDef, fixed_value
from fb.trace import Trace

POLICY_DEFAULTS: dict[str, Any] = namespace()["POLICY_DEFAULTS"]

LINE_KEYS = {
    "opening": "First sentence of the call, exactly what the brief requires (who is calling, "
    "for example that the call is recorded). It must not mention the reason for the call. If "
    "the brief gives the exact words, use them exactly. Otherwise avoid the word 'recorded' "
    "(say for example 'we are recording this call'): a transcript judge can read it as a "
    "claim that a promise was recorded.",
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
    "cannot_change": "What to say when the customer wants to change a promise that is already "
    "recorded ('' if the brief is silent).",
    "transfer_human": "Short sentence when handing the call to a person.",
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
        "business_days_only": "bool: true when the brief limits callbacks to business days, as "
        "'today or the next business day' does; false only when the brief explicitly allows "
        "callbacks on weekends or holidays",
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
                "ok": "response field that is true when the call was carried out",
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
            "response": {"ok": "..."},
        },
        "add_note": {"customer_id": "...", "product_id": "...", "reason": "..."},
        "schedule_callback": {
            "customer_id": "...",
            "date": "...",
            "time": "...",
            "response": {"ok": "..."},
        },
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
    "floor_field": {
        "<product type>": "<response field holding the floor (minimum) amount, '' if the "
        "brief gives none>; one entry per value of the get_account type enum, here and in "
        "product_names"
    },
    "product_names": {
        "<product type>": "spoken name, response fields in single braces, "
        "for example 'credit card ending {card_last4}'"
    },
    "neg": {
        "outcomes": {"promise": "...", "paid": "...", "transferred": "...", "no_promise": "..."},
        "reasons": {
            "refused": "...",
            "hardship": "... ('' when the tool API has no value for it; same for the others)",
            "dispute": "...",
            "service_error": "...",
            "paid": "reason used in a note for an already-paid product",
        },
        "close_reasons": {
            "_": "only when close_call's reason values differ from add_note's for the same "
            "reason: the close_call value per key of reasons (refused, hardship, dispute, "
            "service_error); else leave those keys out",
            "paid": "the close_call reason when the call closes because every product is "
            "already paid, exactly as the brief's outcome table gives it; '' when the table "
            "lists no reason for that case",
        },
        "queues": {"restructuring": "...", "human": "...", "support": "..."},
        "transfer_reasons": {
            k: "close_call reason for this transfer, '' when the outcome table lists none"
            for k in ("restructuring", "human", "support")
        },
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
- reason_priority [...]: order of reasons when no_promise_reason is "priority", written as the
  semantic keys refused, hardship, dispute, service_error (not the bank's codes).
- notes_on_transfer [false]: true if notes are still added on a transferred call.
- vague_date_asks [1]: times to ask for the exact day after a vague answer before proposing
  the deadline.
- floor_offers [1]: times the floor may be offered after "too much" / an amount below it.
- product_order ["get_account"]: optional. The order products are stated and handled in:
  "get_account" (the order the tool returns them), "days_late_desc" (most days late first) or
  "past_due_desc" (largest past-due amount first). Set it only when the brief orders them.
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
  in neg.product_order (customers can have up to three products even if the brief shows one).

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
            shown = t.response
            if not isinstance(shown.get("properties"), dict) and t.response_props:
                # allOf / $ref: the model sees the resolved fields, not the reference
                shown = {"type": "object", "properties": t.response_props}
            out.append("    response: " + json.dumps(shown)[:1500])
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


def warnings(spec: dict[str, Any], brief: str) -> list[str]:
    """Soft findings for the trace; they never stop the build."""
    out = []
    opening = str((spec.get("lines") or {}).get("opening") or "")
    if "recorded" in opening.lower() and opening not in brief:
        out.append("lines.opening says 'recorded' without the brief requiring those words")
    return out


def check(
    spec: dict[str, Any],
    tools: dict[str, ToolDef],
    brief: str,
    openapi: dict[str, Any] | None = None,
) -> list[str]:
    """Deterministic checks against the tool API. Returns problems as sentences. `openapi`
    (the parsed file) resolves $refs nested in response schemas."""
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
            if v == "" and k not in note_reasons:
                continue  # add_note has no value for this reason: none is required
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
    for k, v in lines.items():
        if k != "who_question" and ("{{" in str(v) or "}}" in str(v)):
            bad.append(f"lines.{k} must not contain a placeholder")
    for tpl in (spec.get("product_names") or {}).values():
        if "{{" in str(tpl):
            bad.append("product_names use single braces")
    bad += close_reasons(spec, tools)
    bad += [p for p, hard in bindings(spec, tools) if hard]
    bad += responses(spec, tools, openapi)
    return bad + structure(spec)


# ---------------------------------------------------------------- the spec against the tool API
# Request roles fb.generate binds per tool (its bake(...) / role_field(...) calls in build()).
# The first name of each tuple is the role's key in tool_fields; the rest are the fallback
# names generate.find_field tries (exact, then substring). A required field no role binds is
# sent as "{{field}}" (model-filled), which the api_request steps never fill.
TOOL_ROLES: dict[str, tuple[tuple[str, ...], ...]] = {
    "verify_identity": (("customer_id",), ("field", "question"), ("answer", "value")),
    "get_account": (("customer_id",),),
    "create_promise": (
        ("customer_id",),
        ("product_id", "product", "account", "ref"),
        ("amount",),
        ("date", "day"),
    ),
    "add_note": (
        ("customer_id",),
        ("product_id", "product", "account", "ref"),
        ("reason", "code"),
    ),
    "schedule_callback": (("customer_id",), ("date", "day"), ("time", "hour")),
    "transfer_to_agent": (("customer_id",), ("queue", "team", "department", "desk")),
    "close_call": (("customer_id",), ("outcome", "result", "status"), ("reason", "code", "detail")),
}
# Response fields the workflow reads: verify_identity's post hook (resp() roles and data.ok),
# the create_promise and schedule_callback steps (data.ok -> rec_ok / cb_ok).
RESPONSE_ROLES: dict[str, tuple[str, ...]] = {
    "verify_identity": ("ok", "verified", "all_verified", "wrong_answers_left"),
    "create_promise": ("ok",),
    "schedule_callback": ("ok",),
}
# neg.reasons keys that can become a close_call reason (negotiation_lib._finish: a product
# that is not recorded and not paid). "paid" is a note reason; its close reason, when the
# outcome table gives one, is neg.close_reasons.paid (generate.paid_close_reason).
CLOSE_REASON_KEYS = ("refused", "hardship", "dispute", "service_error")


def _tf(spec: dict[str, Any], tool: str) -> dict[str, Any]:
    tf = (spec.get("tool_fields") or {}).get(tool)
    return tf if isinstance(tf, dict) else {}


def _bind(spec: dict[str, Any], t: ToolDef, cands: tuple[str, ...]) -> str:
    """generate.role_field without its module state: the spec's name when it is a real field,
    else the first candidate found in the tool."""
    explicit = _tf(spec, t.name).get(cands[0])
    if explicit in {f.name for f in t.fields}:
        return str(explicit)
    return find_field(t, *cands)


def bindings(spec: dict[str, Any], tools: dict[str, ToolDef]) -> list[tuple[str, bool]]:
    """Required request fields no role binds. Hard (True) when a role of the same tool found no
    field: the tool API renamed it and tool_fields can fix that. A required field with no role
    at all (every role bound) cannot be fixed in the spec and is only a warning."""
    out = []
    for tool, roles in TOOL_ROLES.items():
        t = tools.get(tool)
        if not t:
            continue
        bound = {_bind(spec, t, cands) for cands in roles} - {""}
        loose = [
            f.name
            for f in t.fields
            if f.required and f.name not in bound and fixed_value(f) is None
        ]
        if not loose:
            continue
        missing = [cands[0] for cands in roles if not _bind(spec, t, cands)]
        if missing:
            out.append(
                (
                    f"{tool}: required field(s) {loose} are bound to no role and role(s) "
                    f"{missing} found no field: set "
                    + ", ".join(f"tool_fields.{tool}.{r}" for r in missing)
                    + f" to the real field name (fields of {tool}: "
                    f"{sorted(f.name for f in t.fields)})",
                    True,
                )
            )
        else:
            out.append(
                (
                    f"{tool}: required field(s) {loose} play no role the workflow fills and "
                    "the tool API gives no default or single value for them; a call from a "
                    "workflow step (no model) sends them empty",
                    False,
                )
            )
    return out


def close_reasons(spec: dict[str, Any], tools: dict[str, ToolDef]) -> list[str]:
    """Every value the workflow can send as close_call's reason, when that field has an enum."""
    t = tools.get("close_call")
    if not t:
        return []
    f = t.field(_bind(spec, t, ("reason", "code", "detail")))
    allowed = list(f.enum) if f else []
    if not allowed:
        return []
    bad = []
    for k, v in (spec.get("outcomes") or {}).items():
        if isinstance(v, list) and len(v) > 1 and v[1] and v[1] not in allowed:
            bad.append(f"outcomes.{k}: reason {v[1]!r} is not a close_call reason {allowed}")
    neg = spec.get("neg") or {}
    for k, v in (neg.get("transfer_reasons") or {}).items():
        if v and v not in allowed:
            bad.append(f"neg.transfer_reasons.{k}: {v!r} is not a close_call reason {allowed}")
    rs = neg.get("reasons") or {}
    cr = neg.get("close_reasons") or {}
    paid = paid_close_reason(neg, spec.get("outcomes") or {})
    if paid and paid not in allowed:
        bad.append(
            f"neg.close_reasons.paid: {paid!r} is not a close_call reason {allowed}; it is the "
            "close reason when every product is already paid ('' when the table lists none)"
        )
    for k in CLOSE_REASON_KEYS:
        # what negotiation_lib.close_reason sends: neg.close_reasons first, then neg.reasons
        v = cr.get(k) or rs.get(k)
        if not v or v in allowed:
            continue
        bad.append(
            f"neg.reasons.{k}: {v!r} is not a close_call reason {allowed}; it is the close "
            "reason when no promise is recorded. If add_note and close_call use different "
            f"values for it, keep the add_note value in neg.reasons.{k} and set "
            f"neg.close_reasons.{k} to the close_call value"
        )
    return bad


def _deref(node: Any, doc: dict[str, Any] | None) -> Any:
    for _ in range(20):
        if not (isinstance(node, dict) and "$ref" in node):
            return node
        if doc is None:
            return None
        cur: Any = doc
        try:
            for part in str(node["$ref"]).lstrip("#/").split("/"):
                cur = cur[part]
        except (KeyError, TypeError):
            return None
        node = cur
    return None


def _props(node: Any, doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """The properties a response schema lists, or None when it does not say (no properties,
    additional properties allowed, an unresolved $ref, oneOf/anyOf): then nothing is flagged."""
    node = _deref(node, doc)
    if not isinstance(node, dict) or node.get("additionalProperties") not in (None, False):
        return None
    props: dict[str, Any] = {}
    for part in node.get("allOf") or []:
        sub = _props(part, doc)
        if sub is None:
            return None
        props.update(sub)
    if node.get("oneOf") or node.get("anyOf"):
        return None
    if isinstance(node.get("properties"), dict):
        props.update(node["properties"])
    return props or None


def _placeholders(tpl: str) -> list[str]:
    try:
        parsed = list(string.Formatter().parse(tpl))
    except ValueError:
        return []
    return [m.group(0) for _, name, _, _ in parsed if name and (m := re.match(r"\w+", name))]


def responses(
    spec: dict[str, Any], tools: dict[str, ToolDef], openapi: dict[str, Any] | None = None
) -> list[str]:
    """Response fields the workflow reads must be in the tool's 200 response, when that response
    lists its properties. `openapi` resolves nested $refs (without it they count as unknown)."""
    bad: list[str] = []
    for tool, roles in RESPONSE_ROLES.items():
        t = tools.get(tool)
        top = _props(t.response, openapi) if t else None
        if not top:
            continue
        rmap = _tf(spec, tool).get("response")
        rmap = rmap if isinstance(rmap, dict) else {}
        want = list(roles)
        swap = str((spec.get("identity") or {}).get("swap_hint") or "")
        if tool == "verify_identity" and swap:
            want.append("hint")
        others = {str(rmap.get(r) or r) for r in want if r != "ok"}
        for role in want:
            name = str(rmap.get(role) or role)
            if name in top:
                continue
            if role == "ok" and not any(
                isinstance(x, dict) and x.get("type") == "boolean" and k not in others
                for k, x in top.items()
            ):
                continue  # no flag the spec could name: not a spec problem, the build goes on
            if (
                tool == "verify_identity"
                and role == "all_verified"
                and role not in top
                and str(rmap.get("verified") or "verified") in top
            ):
                continue  # no such flag: the identity hook counts the verified answers itself
            what = {
                "ok": "says the call was carried out (true/false)",
                "hint": "carries the hint, or set identity.swap_hint to '' if the tool has none",
            }.get(role, f"holds {role}")
            bad.append(
                f"tool_fields.{tool}.response.{role}: {name!r} is not a field of the {tool} "
                f"response {sorted(top)}; set it to the response field that {what}"
            )
        if tool == "verify_identity" and swap:
            hint = _deref(top.get(str(rmap.get("hint") or "hint")), openapi)
            enum = (
                [str(e) for e in (hint or {}).get("enum") or []] if isinstance(hint, dict) else []
            )
            if enum and swap not in enum:
                bad.append(f"identity.swap_hint {swap!r} is not a value of the hint field {enum}")
    t = tools.get("get_account")
    top = _props(t.response, openapi) if t else None
    if not top:
        return bad
    pf = spec.get("product_fields") or {}
    lst = str(pf.get("list") or "products")
    props: dict[str, Any] | None = top
    *path, last = lst.split(".")
    for part in path:
        props = _props((props or {}).get(part), openapi) if props else None
    if not props:
        return bad
    if last not in props:
        bad.append(
            f"product_fields.list: {lst!r} is not a field of the get_account response "
            f"{sorted(props)}; set it to the array of products"
        )
        return bad
    arr = _deref(props[last], openapi)
    items = _props(arr.get("items"), openapi) if isinstance(arr, dict) else None
    if not items:
        return bad
    where = f"of the get_account {lst} items {sorted(items)}"
    for role in ("product_id", "type", "past_due", "days_late"):
        name = str(pf.get(role) or role)
        if name not in items:
            bad.append(
                f"product_fields.{role}: {name!r} is not a field {where}; set it to the "
                f"product field that holds {role}"
            )
    for ptype, name in (spec.get("floor_field") or {}).items():
        if name and str(name) not in items:
            bad.append(f"floor_field.{ptype}: {name!r} is not a field {where}")
    # every product type the tool can return needs a floor field and a spoken name: a type
    # left out never gets a floor offer and is named after its code ('car_loan' -> 'car loan')
    tdef = _deref(items.get(str(pf.get("type") or "type")), openapi)
    types = [str(e) for e in (tdef.get("enum") or [])] if isinstance(tdef, dict) else []
    for key, what in (
        ("floor_field", "the response field holding its floor (minimum) amount, '' if the "
         "brief gives that product no floor"),
        ("product_names", "its spoken name"),
    ):  # fmt: skip
        got = spec.get(key) if isinstance(spec.get(key), dict) else {}
        missing = [x for x in types if x not in got]
        if missing:
            bad.append(
                f"{key} has no entry for product type(s) {missing} of the get_account type "
                f"enum {types}: set {what}"
            )
    for ptype, tpl in (spec.get("product_names") or {}).items():
        for ph in _placeholders(str(tpl)):
            if ph not in items:
                bad.append(f"product_names.{ptype}: {{{ph}}} is not a field {where}")
    return bad


def _is_int(x: Any, lo: int | None = None) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and (lo is None or x >= lo)


def semantic_priority(neg: dict[str, Any]) -> tuple[list[str], list[str]]:
    """neg.reason_priority as the semantic keys negotiation_lib compares (refused, hardship,
    dispute, service_error): a bank code (a value of neg.close_reasons or neg.reasons) is
    mapped back to its key. Returns (keys, entries that are neither)."""
    back: dict[str, str] = {}
    for src in ("reasons", "close_reasons"):
        for k, v in (neg.get(src) or {}).items():
            if k in CLOSE_REASON_KEYS and isinstance(v, str) and v:
                back.setdefault(v, k)
    keys: list[str] = []
    unknown: list[str] = []
    pr = neg.get("reason_priority")
    for x in pr if isinstance(pr, list) else []:
        k = str(x) if x in CLOSE_REASON_KEYS else back.get(str(x), "")
        if not k:
            unknown.append(str(x))
        elif k not in keys:
            keys.append(k)
    return keys, unknown


def blocking(spec: dict[str, Any]) -> list[str]:
    """The problems no workflow can be built around: build.py publishes despite every other
    problem check() reports (an imperfect agent scores more than none), but not without a
    security question to ask or a band to place a late product in."""
    out = []
    qs = (spec.get("identity") or {}).get("questions") or []
    if not [x for x in qs if isinstance(x, dict) and x.get("field")]:
        out.append("identity.questions is empty")
    bands = [
        b
        for b in spec.get("bands") or []
        if isinstance(b, dict)
        and _is_int(b.get("min_days_late"), 0)
        and _is_int(b.get("max_days_late"), 0)
    ]
    if not bands:
        out.append("no band with whole-number min_days_late and max_days_late")
    return out


def _hhmm(x: Any) -> bool:
    m = re.fullmatch(r"(\d{2}):(\d{2})", str(x or ""))
    return bool(m) and int(m.group(1)) < 24 and int(m.group(2)) < 60


ENUMS = {
    "hardship": ("offer_support", "transfer", "close"),
    "record_failure": ("continue", "transfer"),
    "no_promise_reason": ("first_product", "priority"),
    "record_failure_queue": ("restructuring", "human", "support"),
    "hardship_queue": ("restructuring", "human", "support"),
    "product_order": ("get_account", "days_late_desc", "past_due_desc"),
}


def structure(spec: dict[str, Any]) -> list[str]:
    """Types, ranges and internal consistency. These say nothing about whether a value is the
    brief's; `grounding` looks at that."""
    bad: list[str] = []
    neg = spec.get("neg") or {}
    bands = spec.get("bands") or []
    spans = []
    for b in bands:
        name = b.get("name")
        lo, hi = b.get("min_days_late"), b.get("max_days_late")
        if not (_is_int(lo, 0) and _is_int(hi, 0) and lo <= hi):
            bad.append(f"band {name!r}: min_days_late/max_days_late must be integers, min <= max")
        else:
            spans.append((lo, hi, name))
        if not _is_int(b.get("deadline_days"), 0):
            bad.append(f"band {name!r}: deadline_days must be a whole number >= 0")
        if not _is_int(b.get("attempts"), 0):
            bad.append(f"band {name!r}: attempts must be a whole number >= 0")
        if b.get("deadline_unit", "business") not in ("business", "calendar"):
            bad.append(f"band {name!r}: deadline_unit must be business or calendar")
    spans.sort()
    for (_, hi1, n1), (lo2, _, n2) in itertools.pairwise(spans):
        if lo2 <= hi1:
            bad.append(f"bands {n1!r} and {n2!r} overlap")
        elif lo2 > hi1 + 1:
            # band_for finds no band for these days: no deadline, no attempts for the product
            gap = f"{hi1 + 1}" if lo2 == hi1 + 2 else f"{hi1 + 1} to {lo2 - 1}"
            bad.append(
                f"bands {n1!r} and {n2!r} leave days_late {gap} without a band "
                "(the next band's min_days_late must be the previous max_days_late + 1)"
            )
    for k in ("notes", "notes_on_transfer"):
        if k in neg and not isinstance(neg.get(k), bool):
            bad.append(f"neg.{k} must be true or false")
    for k, lo in (
        ("human_requests_to_transfer", 1),
        ("stop_requests_to_close", 1),
        ("promise_attempts", 1),
        ("vague_date_asks", 0),
        ("floor_offers", 0),
    ):
        if k in neg and not _is_int(neg.get(k), lo):
            bad.append(f"neg.{k} must be a whole number >= {lo}")
    for k, allowed in ENUMS.items():
        if k in neg and neg.get(k) not in allowed:
            bad.append(f"neg.{k} must be one of {list(allowed)}")
    pr = neg.get("reason_priority")
    if pr is not None and not isinstance(pr, list):
        bad.append("neg.reason_priority must be a list")
    keys, unknown = semantic_priority(neg)
    if unknown:
        bad.append(
            f"neg.reason_priority: {unknown} are not reasons: use the semantic keys "
            f"{list(CLOSE_REASON_KEYS)} (or values of neg.reasons)"
        )
    if neg.get("no_promise_reason") == "priority" and not keys:
        bad.append(
            "neg.no_promise_reason is priority but neg.reason_priority names no reason: list "
            f"{list(CLOSE_REASON_KEYS)} in the brief's order"
        )
    # A reason the tool API has no value for (an add_note enum without hardship) is '': only
    # refused, the reason of every product left without a promise, must be named.
    rs = neg.get("reasons")
    if not isinstance(rs, dict) or any(
        not isinstance(rs.get(x), str) for x in ("hardship", "dispute", "service_error")
    ):
        bad.append(
            "neg.reasons needs a value for each of ['refused', 'hardship', 'dispute', "
            "'service_error'] ('' when the tool API has no value for one)"
        )
    for k, keys in (
        ("outcomes", ("promise", "paid", "transferred", "no_promise")),
        ("reasons", ("refused",)),
        ("queues", ("restructuring", "human", "support")),
    ):
        got = neg.get(k)
        if not isinstance(got, dict) or any(not str(got.get(x) or "").strip() for x in keys):
            bad.append(f"neg.{k} needs a value for each of {list(keys)}")
    # A table whose `transferred` row lists no reason is valid: '' means none is sent.
    tr_keys = ("restructuring", "human", "support")
    got = neg.get("transfer_reasons")
    if not isinstance(got, dict) or any(not isinstance(got.get(x), str) for x in tr_keys):
        bad.append(
            f"neg.transfer_reasons needs a value for each of {list(tr_keys)} ('' when the "
            "outcome table lists no reason for a transfer)"
        )
    outcomes = spec.get("outcomes") or {}
    for k in (
        "unclear",
        "callback",
        "no_callback",
        "wrong_number",
        "deceased",
        "failed",
        "declined",
    ):
        v = outcomes.get(k)
        if not (
            isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v) and v[0]
        ):
            bad.append(f"outcomes.{k} must be [outcome, reason]")
    cb = spec.get("callback") or {}
    if not (_hhmm(cb.get("start")) and _hhmm(cb.get("end")) and cb["start"] < cb["end"]):
        bad.append("callback.start/end must be HH:MM with start before end")
    if "business_days_ahead" in cb and not _is_int(cb.get("business_days_ahead"), 0):
        bad.append("callback.business_days_ahead must be a whole number >= 0")
    for k in ("include_today", "business_days_only"):
        if k in cb and not isinstance(cb.get(k), bool):
            bad.append(f"callback.{k} must be true or false")
    if "reasks" in cb and not _is_int(cb.get("reasks"), 0):
        bad.append("callback.reasks must be a whole number >= 0")
    a1 = spec.get("attempt1") or {}
    if a1.get("rule") not in ("last_business_day_of_week", "nth_business_day"):
        bad.append("attempt1.rule must be last_business_day_of_week or nth_business_day")
    elif a1.get("rule") == "nth_business_day" and not _is_int(a1.get("n"), 1):
        bad.append("attempt1.n must be a whole number >= 1")
    plan = spec.get("attempt_plan") or []
    if not plan:
        bad.append("attempt_plan is empty")
    most = max((b.get("attempts") for b in bands if _is_int(b.get("attempts"), 0)), default=0)
    if plan and len(plan) < most:
        bad.append(
            f"attempt_plan has {len(plan)} step(s) but a band has {most} persuasion attempts: "
            "give one step per attempt, in order (an attempt past the plan would repeat an "
            "offer the customer refused, so the builder caps a band's attempts at the plan's "
            "length)"
        )
    for i, s in enumerate(plan):
        if s.get("amount") not in ("current", "full", "floor") or s.get("date") not in (
            "attempt1",
            "deadline",
        ):
            bad.append(
                f"attempt_plan[{i}] must use amount current|full|floor, date attempt1|deadline"
            )
    wd = (spec.get("calendar") or {}).get("workdays")
    if wd is not None and not (
        isinstance(wd, list) and wd and all(_is_int(x, 0) and x <= 6 for x in wd)
    ):
        bad.append("calendar.workdays must be a list of 0..6")
    return bad


_NUMWORDS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
]
_ORDWORDS = [
    "zeroth",
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
    "fourteenth",
    "fifteenth",
    "sixteenth",
    "seventeenth",
    "eighteenth",
    "nineteenth",
    "twentieth",
]
_MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]


def _number_in(n: int, text: str) -> bool:
    if re.search(r"(?<![\d.])" + str(n) + r"(?!\d)", text):
        return True
    return 0 <= n < len(_NUMWORDS) and bool(
        re.search(r"\b(" + _NUMWORDS[n] + "|" + _ORDWORDS[n] + r")\b", text)
    )


def _date_in(iso: str, text: str) -> bool:
    try:
        d = datetime.date.fromisoformat(iso)
    except ValueError:
        return False
    mon = _MONTHS[d.month - 1]
    forms = [
        iso,
        rf"\b0?{d.day}(st|nd|rd|th)?\s+(of\s+)?{mon[:3]}",
        rf"\b{mon[:3]}[a-z]*\.?\s+0?{d.day}(st|nd|rd|th)?\b",
        rf"\b0?{d.day}[/.]0?{d.month}[/.]",
        rf"\b0?{d.month}/0?{d.day}/",
    ]
    return any(re.search(f, text) for f in forms)


def _callback_business_days(text: str) -> bool:
    """A sentence of the brief (lower case) about a callback says 'business day' and nothing
    about weekends: callbacks are limited to business days."""
    for sent in re.split(r"(?<=[.!?])\s+|\n", text):
        if (
            re.search(r"call\s*-?\s*back|callback", sent)
            and "business day" in sent
            and not re.search(r"weekend|saturday|sunday|holiday", sent)
        ):
            return True
    return False


def grounding(spec: dict[str, Any], brief: str) -> list[str]:
    """Values that score must come from the brief: dates, day counts, limits, close codes.
    A heuristic: a failure asks the model to look again; it does not stop the build."""
    text = brief.lower()
    bad: list[str] = []
    for h in (spec.get("calendar") or {}).get("holidays") or []:
        if not _date_in(str(h), text):
            bad.append(f"holiday {h} is not found in the brief")
    for b in spec.get("bands") or []:
        for k in ("min_days_late", "max_days_late", "deadline_days", "attempts"):
            v = b.get(k)
            if _is_int(v) and not _number_in(v, text):
                bad.append(f"band {b.get('name')!r} {k}={v} is not found in the brief")
    cb = spec.get("callback") or {}
    for k in ("start", "end"):
        v = str(cb.get(k) or "")
        if _hhmm(v):
            hh, mm = int(v[:2]), v[3:]
            h12 = hh - 12 if hh > 12 else hh
            if not re.search(rf"\b0?({hh}|{h12})(:{mm}|\s*(am|pm|a\.m\.|p\.m\.|o'clock)|h)", text):
                bad.append(f"callback {k} {v} is not found in the brief")
    if cb.get("business_days_only") is False and _callback_business_days(text):
        bad.append(
            "callback.business_days_only is false but the brief's callback rule speaks of "
            "business days ('today or the next business day' limits callbacks to business "
            "days): set it to true unless the brief explicitly allows weekend callbacks"
        )
    codes = set()
    for v in (spec.get("outcomes") or {}).values():
        if isinstance(v, list):
            codes.update(x for x in v if isinstance(x, str) and x)
    neg = spec.get("neg") or {}
    for k in ("outcomes", "reasons", "transfer_reasons"):
        codes.update(x for x in (neg.get(k) or {}).values() if isinstance(x, str) and x)
    for c in sorted(codes):
        if not re.search(r"(?<![a-z0-9_])" + re.escape(c.lower()) + r"(?![a-z0-9_])", text):
            bad.append(f"close/note code {c!r} is not found in the brief")
    return bad


def extract(
    brief: str,
    openapi: dict[str, Any],
    tools: dict[str, ToolDef],
    trace: Trace,
    deadline: float | None = None,
) -> dict[str, Any]:
    prompt = PROMPT.format(
        policy_help=POLICY_HELP,
        line_help="\n".join(f"- {k}: {v}" for k, v in LINE_KEYS.items()),
        tools=tool_summary(openapi, tools),
        schema=json.dumps(SCHEMA, indent=1),
        brief=brief,
    )
    msgs = [{"role": "user", "content": prompt}]
    reply = chat(trace, "extract the spec from the brief", msgs, deadline=deadline)
    spec = _parse(reply)
    problems = check(spec, tools, brief, openapi) + grounding(spec, brief)
    if problems:
        trace.note(f"spec check: {len(problems)} problems: " + "; ".join(problems)[:800])
        msgs += [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": "These checks failed:\n- "
                + "\n- ".join(problems)
                + "\nFix them using the brief and the tool API. A value 'not found in the "
                "brief' may still be right (for example said in words the check does not "
                "know): keep it only if the brief really says it, and add a note quoting where."
                " Output the whole JSON again.",
            },
        ]
        reply = chat(trace, "repair the spec after failed checks", msgs, deadline=deadline)
        spec = _parse(reply)
        for w in grounding(spec, brief):
            trace.note("spec warning (kept, check the brief): " + w)
    cb = spec.get("callback")
    if (
        isinstance(cb, dict)
        and cb.get("business_days_only") is False
        and _callback_business_days(brief.lower())
    ):
        # the brief's callback rule names business days and no weekend: a Saturday callback
        # would break it whatever the model read
        cb["business_days_only"] = True
        trace.note("spec override: callback.business_days_only false -> true")
    for w in warnings(spec, brief) + [p for p, hard in bindings(spec, tools) if not hard]:
        trace.note("spec warning: " + w)
    for n in spec.get("notes") or []:
        trace.note("spec decision: " + str(n)[:300])
    return spec


def _parse(reply: str) -> dict[str, Any]:
    try:
        out = parse_json(reply)
    except (ValueError, json.JSONDecodeError) as e:
        raise SpecError(f"the model did not return JSON: {e}") from e
    if not isinstance(out, dict):
        raise SpecError("the model did not return a JSON object")
    return out


class SpecError(RuntimeError):
    pass


# Every key negotiation_lib (`_line(neg, key, default)`) and the code-node bodies read from
# neg.lines, with what is said when the brief gives no words for it. _line falls back to its
# own default for a missing or empty value, and some of those defaults are Brief A's policy
# ("You will receive a text message confirming this promise.", "You can change it in the
# mobile app.", "Payments can take one business day to appear."): a hidden brief that says
# nothing must not get them, so every key is set, to the brief's line or to a neutral sentence
# that promises nothing.
ENGINE_LINES: dict[str, str] = {
    "no_account": "I am sorry, I cannot see your account details right now.",
    "present_intro": "I am calling about a late payment.",
    "today_question": "Can you make this payment today?",
    "multi_today_question": "Can you pay all of them today?",
    "confirm_question": "Do you confirm?",
    "anything_else": "Is there anything else I can help you with?",
    "support_reask": "Would you like that?",
    "rest_overdue": "The rest stays overdue.",
    "vague_ask": "On which exact day will you pay?",
    "offer_day_question": "Can you do that, or is another day better for you?",
    "help_first": "Before I transfer you, can I help you with this myself?",
    "support_offer": "Would you like me to connect you with a colleague who can help?",
    "sympathy": "I am sorry to hear that.",
    "paid": "Thank you for letting me know.",
    "dispute_noted": "I have noted that.",
    "which_change": "Which promise would you like to change?",
    "one_by_one": "Let us go through them one at a time.",
    "recorded": "Thank you.",
    "cannot_change": "A recorded promise cannot be changed in this call.",
    "transfer_human": "I am connecting you with a colleague now.",
    "transfer_restructuring": "I am connecting you with a colleague now.",
    "transfer_support": "I am connecting you with a colleague now.",
    "transfer_failed": "I am sorry, I could not connect you.",
    "goodbye": "Thank you for your time. Goodbye.",
}


def _var(spec: dict[str, Any], role: str) -> str:
    return str((spec.get("call_variables") or {}).get(role) or role)


def to_generator(spec: dict[str, Any]) -> dict[str, Any]:
    """Spec -> input of fb.generate.build (cfg for the code nodes + conversation parts)."""
    s = copy.deepcopy(spec)
    neg = dict(s.get("neg") or {})
    for k, v in POLICY_DEFAULTS.items():  # check() reported missing ones to the trace
        neg.setdefault(k, v)
    lines = dict(s.get("lines") or {})
    # every line the engine reads: the brief's words, else a neutral sentence (never the
    # engine's built-in default, which is Brief A's wording for some)
    neg["lines"] = {
        k: str(lines.get(k) or "").strip() or neutral for k, neutral in ENGINE_LINES.items()
    }
    # close_call reasons per semantic key; "paid" only when the outcome table gives one
    # (negotiation_lib._finish must read close_reasons.paid for a call where all is paid)
    cr = {
        k: v
        for k, v in (neg.get("close_reasons") or {}).items()
        if k not in ("_", "paid") and isinstance(v, str) and v.strip()
    }
    paid = paid_close_reason(neg, s.get("outcomes") or {})
    if paid:
        cr["paid"] = paid
    neg["close_reasons"] = cr
    # negotiation_lib compares semantic keys: bank codes in the priority are mapped back (a
    # code list was silently ignored and the first product's reason won)
    if isinstance(neg.get("reason_priority"), list):
        neg["reason_priority"] = semantic_priority(neg)[0]
    # a reason the tool API has no value for is '': left out, the engine uses the semantic key
    neg["reasons"] = {
        k: v for k, v in (neg.get("reasons") or {}).items() if isinstance(v, str) and v.strip()
    }
    neg.setdefault("attempt_plan", s.get("attempt_plan") or [])
    if s.get("attempt_plan"):
        neg["attempt_plan"] = s["attempt_plan"]
    # negotiation_lib takes step min(used, len(plan)): an attempt past the plan repeats the
    # last offer, one the customer already refused (A31). A plan shorter than a band's attempts
    # (structure() reports it to the repair round) caps the attempts instead; extending the
    # plan would need offers the brief does not give. No plan: the engine's two-step default.
    steps = len(neg.get("attempt_plan") or []) or 2
    bands = []
    for b in s.get("bands") or []:
        att = b.get("attempts")
        bands.append(
            {
                "name": b.get("name"),
                "min_days_late": b.get("min_days_late"),
                "max_days_late": b.get("max_days_late"),
                "deadline_business_days": b.get("deadline_days"),
                "deadline_days": b.get("deadline_days"),
                "deadline_unit": b.get("deadline_unit") or "business",
                "attempts": min(att, steps) if _is_int(att, 0) else att,
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
            # 0 is a value (only today), not a missing one
            "business_days_ahead": (
                cb["business_days_ahead"] if _is_int(cb.get("business_days_ahead"), 0) else 1
            ),
            # only an explicit false allows weekend callbacks (a real extraction of Brief A,
            # 'today or the next business day', gave false: a Saturday callback was offered)
            "business_days_only": cb.get("business_days_only") is not False,
        },
        "product_fields": {k: v for k, v in pf.items() if k != "list" and v},
        # find_products' fallback reads the list name from the config (code nodes see CFG only)
        "products_list": pf.get("list") or "products",
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


# ---------------------------------------------------------------- extraction observer
# A second extraction of the same brief, run beside the main one, to show where the model is
# unsure (the two readings differ). Logged only; the one value it changes is
# forbidden_before_verify (the union of both: saying less before the identity check is safe).
OBSERVER_PURPOSE = "extraction cross-check"
OBSERVER_WAIT_MAX_S = 120.0
OBSERVER_RESERVE_S = 300.0


class _ObserverTrace:
    """The trace the observer's extract() writes to: its model calls (and failed calls) go to
    the run's trace with the purpose 'extraction cross-check: ...', so every model call and its
    cost is in the trace; its notes stay here (the main extraction's notes are the run's)."""

    def __init__(self, trace: Trace) -> None:
        import threading

        self.trace = trace
        self.lock = threading.Lock()
        self.notes: list[str] = []

    def model(self, model: str, purpose: str, cost_usd: float = 0.0, tokens: int = 0) -> None:
        with self.lock:
            self.trace.model(model, f"{OBSERVER_PURPOSE}: {purpose}", cost_usd, tokens)

    def model_failed(self, model: str, purpose: str, seconds: float, error: str) -> None:
        with self.lock:
            self.trace.model_failed(model, f"{OBSERVER_PURPOSE}: {purpose}", seconds, error)

    def note(self, msg: str) -> None:
        self.notes.append(str(msg))

    test = freya = note

    def line(self, kind: str, msg: str) -> None:
        self.notes.append(f"{kind} {msg}")


class ExtractionObserver:
    """A second extract() of the same inputs in a daemon thread. Nothing it does can reach the
    build: every exception is kept as text, and `wait` never raises."""

    def __init__(self, run: Any) -> None:
        import threading
        import time

        self.spec: dict[str, Any] | None = None
        self.error = ""
        self.started = time.monotonic()
        self.thread = threading.Thread(
            target=self._run, args=(run,), name="extraction-observer", daemon=True
        )
        self.thread.start()

    def _run(self, run: Any) -> None:
        try:
            out = run()
            if isinstance(out, dict):
                self.spec = out
            else:
                self.error = f"the observer's extraction returned {type(out).__name__}"
        except BaseException as e:
            self.error = f"{type(e).__name__}: {str(e)[:300]}"

    def wait(self, timeout: float) -> str:
        """'done', 'failed' or 'running' (not finished within `timeout` seconds)."""
        try:
            self.thread.join(max(0.0, float(timeout)))
        except Exception as e:
            self.error = self.error or f"{type(e).__name__}: {e}"
            return "failed"
        if self.thread.is_alive():
            return "running"
        return "done" if self.spec is not None else "failed"


def extract_observer(
    brief: str,
    openapi: dict[str, Any],
    tools: dict[str, ToolDef],
    trace: Trace,
    deadline: float | None = None,
    extract_fn: Any = None,
) -> ExtractionObserver | None:
    """Start the cross-check extraction (same inputs, same deadline) in the background, right
    when the main extraction starts. None when BUILDER_OBSERVER=0 or it could not start."""
    import os

    if str(os.environ.get("BUILDER_OBSERVER", "1")).strip() == "0":
        trace.note("uncertainty: observer disabled (BUILDER_OBSERVER=0)")
        return None
    fn = extract_fn or extract
    proxy = _ObserverTrace(trace)
    try:
        return ExtractionObserver(lambda: fn(brief, openapi, tools, proxy, deadline=deadline))
    except Exception as e:
        trace.note(f"uncertainty: observer not started: {type(e).__name__}: {str(e)[:200]}")
        return None


# Values of the spec that are the builder's own vocabulary, not the brief's: never grounded.
_OWN_WORDS = {
    *(v for vals in ENUMS.values() for v in vals),
    *CLOSE_REASON_KEYS,
    "paid",
    "current",
    "full",
    "floor",
    "attempt1",
    "deadline",
    "business",
    "calendar",
    "last_business_day_of_week",
    "nth_business_day",
    "date",
    "digits",
    "year",
    "text",
    "DMY",
    "MDY",
}


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in sorted(x.items()) if k != "_"}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x.strip() if isinstance(x, str) else x


def policy_fields(spec: Any) -> dict[str, Any]:
    """The policy of a spec as {field: value}, without its wording (notes, lines, how the
    identity questions are asked)."""
    s = spec if isinstance(spec, dict) else {}
    out: dict[str, Any] = {}
    bands = [
        {
            k: b.get(k)
            for k in (
                "min_days_late",
                "max_days_late",
                "deadline_days",
                "deadline_unit",
                "attempts",
            )
        }
        for b in s.get("bands") or []
        if isinstance(b, dict)
    ]
    out["bands"] = sorted(bands, key=lambda b: json.dumps(b.get("min_days_late")).rjust(8))
    cal = s.get("calendar") if isinstance(s.get("calendar"), dict) else {}
    out["calendar.holidays"] = sorted({str(h) for h in cal.get("holidays") or []})
    wd = cal.get("workdays")
    out["calendar.workdays"] = sorted(wd, key=str) if isinstance(wd, list) else wd
    for key in ("callback", "outcomes"):
        d = s.get(key) if isinstance(s.get(key), dict) else {}
        for k, v in d.items():
            if k != "_":
                out[f"{key}.{k}"] = _clean(v)
    out["attempt1"] = _clean(s.get("attempt1"))
    out["attempt_plan"] = _clean(s.get("attempt_plan"))
    neg = s.get("neg") if isinstance(s.get("neg"), dict) else {}
    for k, v in neg.items():
        if k in ("_", "lines"):
            continue
        if isinstance(v, dict):
            for k2, v2 in v.items():
                if k2 != "_":
                    out[f"neg.{k}.{k2}"] = _clean(v2)
        else:
            out[f"neg.{k}"] = _clean(v)
    tf = s.get("tool_fields") if isinstance(s.get("tool_fields"), dict) else {}
    for k, v in tf.items():
        if k != "_":
            out[f"tool_fields.{k}"] = _clean(v)
    out["product_fields"] = _clean(s.get("product_fields"))
    out["floor_field"] = _clean(s.get("floor_field"))
    ident = s.get("identity") if isinstance(s.get("identity"), dict) else {}
    out["identity.factors"] = sorted(
        f"{q.get('field')}:{q.get('kind')}"
        for q in ident.get("questions") or []
        if isinstance(q, dict)
    )
    out["identity.date_order"] = _clean(ident.get("date_order"))
    out["identity.swap_hint"] = _clean(ident.get("swap_hint"))
    out["forbidden_before_verify"] = sorted(
        {str(w).strip().lower() for w in s.get("forbidden_before_verify") or [] if str(w).strip()}
    )
    return out


def _grounded(value: Any, brief: str, api_text: str) -> str:
    """'y' when every value of `value` the brief (or, for a field name, the tool API) can show
    is there, 'n' when one is not, '-' when nothing can be checked (flags, own vocabulary)."""
    leaves: list[Any] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        else:
            leaves.append(x)

    walk(value)
    text = brief.lower()
    seen = False
    for x in leaves:
        if x is None or isinstance(x, bool) or x == "" or x in _OWN_WORDS:
            continue
        seen = True
        if isinstance(x, int | float):
            ok = _number_in(int(x), text)
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(x)):
            ok = _date_in(str(x), text)
        elif _hhmm(x):
            hh, mm = int(str(x)[:2]), str(x)[3:]
            h12 = hh - 12 if hh > 12 else hh
            ok = bool(re.search(rf"\b0?({hh}|{h12})(:{mm}|\s*(am|pm|o'clock)|h)", text))
        else:
            sx = str(x).split(":")[0].lower()
            pat = r"(?<![a-z0-9_])" + re.escape(sx) + r"(?![a-z0-9_])"
            ok = bool(re.search(pat, text) or re.search(pat, api_text))
        if not ok:
            return "n"
    return "y" if seen else "-"


def _short(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False)[:160]


def observe(
    trace: Trace,
    observer: ExtractionObserver | None,
    spec: dict[str, Any],
    brief: str,
    openapi: dict[str, Any] | None,
    wait_s: float,
) -> dict[str, Any]:
    """Wait up to `wait_s` for the observer, log where its reading differs from the main spec
    and apply the one merge: forbidden_before_verify := union of both (main order first).
    Everything else stays the main spec's. Never raises; returns a summary for the manifest."""
    info: dict[str, Any] = {"status": "off"}
    if observer is None:
        return info
    try:
        state = observer.wait(wait_s)
        info["status"] = state
        if state == "running":
            trace.note(
                f"uncertainty: observer not finished after {max(0.0, wait_s):.0f}s; "
                "building with the main spec"
            )
            return info
        if state != "done" or not isinstance(observer.spec, dict):
            trace.note(f"uncertainty: observer failed ({observer.error or 'no spec'}); ignored")
            return info
        other = observer.spec
        api_text = json.dumps(openapi or {}).lower()
        a, b = policy_fields(spec), policy_fields(other)
        disputed = []
        for k in sorted(set(a) | set(b)):
            va, vb = a.get(k), b.get(k)
            if json.dumps(va, sort_keys=True) == json.dumps(vb, sort_keys=True):
                continue
            disputed.append(k)
            trace.note(
                f"uncertainty: {k} | main={_short(va)} other={_short(vb)} | grounded "
                f"main={_grounded(va, brief, api_text)} other={_grounded(vb, brief, api_text)}"
            )
        mine = spec.get("forbidden_before_verify")
        theirs = other.get("forbidden_before_verify")
        main_words = [str(w) for w in mine if str(w).strip()] if isinstance(mine, list) else []
        have = {w.strip().lower() for w in main_words}
        added = []
        mergeable = isinstance(theirs, list) and (mine is None or isinstance(mine, list))
        for w in theirs if mergeable else []:
            w = str(w).strip()
            if w and w.lower() not in have:
                have.add(w.lower())
                added.append(w)
        if added:
            spec["forbidden_before_verify"] = main_words + added
        trace.note(
            f"uncertainty: {len(disputed)} of {len(set(a) | set(b))} policy fields disputed "
            f"(the main spec is kept); forbidden_before_verify: union applied, {len(added)} "
            f"added from the observer {added[:10]}"
        )
        info.update(disputed=disputed, forbidden_added=added)
    except Exception as e:
        trace.note(f"uncertainty: comparison failed: {type(e).__name__}: {str(e)[:200]}")
        info["status"] = "error"
    return info
