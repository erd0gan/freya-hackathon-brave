"""Spec + tool API -> Freya workflow JSON. Deterministic template, no model calls.

Every value that reaches the bank comes from code nodes and baked parameters (docs/GENERATOR.md):
the model talks, classifies what the customer said (extraction) and fills only the two
verify_identity arguments, which a pre-hook normalises.

Spec shape (the extractor produces it; tests/hack/spec_fixtures.py has Brief A and B):

    cfg        the node-code config: calendar, bands, attempt1, callback, floor_field,
               product_names, neg (policy knobs, outcomes, reasons, queues, lines)
    bank_name  "Freya Bank"
    lines      opening, who_question ("Am I speaking with {{title}} {{last_name}}?"),
               ai_answer, scam_answer, deceased, identity_intro, identity_why,
               identity_failed, no_info, callback_booked, goodbye
    forbidden_before_verify   words never said before identity is verified
    identity   questions [{field, ask, kind: date|digits|text}], swap_hint, swap_instruction
    outcomes   pre-negotiation endings: unclear, callback, no_callback, wrong_number,
               deceased, failed, declined -> [outcome, reason]
    callback_reasks           times to ask again after a time outside the window

Call variables are referenced as {{name}} (customer_id, title, last_name, first_name, now).
"""

from __future__ import annotations

import copy
from typing import Any

from fb.nodecode import bodies
from fb.nodecode.embed import LIB, LIB_CALENDAR, snippet
from fb.openapi_tools import ToolDef, freya_tool
from fb.validate import Policy

ALWAYS = {"operator": "always"}
TRUE = ("true", "True")


# ---------------------------------------------------------------- small builders
def eq(var: str, *vals: str) -> dict[str, Any]:
    return {
        "logic": "or",
        "conditions": [{"variable": var, "operator": "equals", "operand": v} for v in vals],
    }


def all_of(*conds: dict[str, Any]) -> dict[str, Any]:
    return {"logic": "or", "conditions": [{"logic": "and", "conditions": list(conds)}]}


def gt(var: str, n: int) -> dict[str, Any]:
    return {
        "logic": "or",
        "conditions": [{"variable": var, "operator": "greater_than", "operand": n}],
    }


def tr(target: str, desc: str, cond: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"target_node": target, "description": desc, "condition": cond or ALWAYS}


def code_node(
    name: str,
    src: str,
    transitions: list[dict[str, Any]],
) -> dict[str, Any]:
    default = transitions[-1]["target_node"]
    return {
        "name": name,
        "node_type": "code",
        "passthrough": True,
        "passthrough_default_transition": default,
        "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True,
        "pre_actions": [
            {
                "type": "function",
                "function": {
                    "name": "code_exec",
                    "handler": "__function__:code_exec",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "default": src},
                            "timeout_ms": {"type": "number", "default": 20000},
                        },
                    },
                },
            }
        ],
        "transitions": transitions,
    }


def api_node(
    name: str,
    fn: dict[str, Any],
    transitions: list[dict[str, Any]],
    mapping: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    fn = copy.deepcopy(fn)
    fn.pop("response_mapping", None)
    return {
        "name": name,
        "node_type": "api_request",
        "passthrough": True,
        "passthrough_default_transition": transitions[-1]["target_node"],
        "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True,
        "pre_actions": [fn],
        "response_mapping": mapping or [],
        "transitions": transitions,
    }


def conv(
    name: str,
    task: str,
    transitions: list[dict[str, Any]],
    *,
    on_enter: dict[str, Any] | None = None,
    extraction: list[dict[str, Any]] | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_turns: int = 10,
    cap_to: str = "",
    first_entry_only: bool = False,
) -> dict[str, Any]:
    n: dict[str, Any] = {
        "name": name,
        "node_type": "conversation",
        "task_message": task,
        "on_enter": on_enter or {"type": "llm"},
        "transitions": transitions,
        "max_node_turns": max_turns,
        "max_turn_transition": cap_to or transitions[-1]["target_node"],
    }
    if first_entry_only:
        n["on_enter_first_entry_only"] = True
    if extraction:
        n["extraction_schema"] = extraction
    if tools:
        n["tools"] = tools
    return n


def enum_var(name: str, values: list[str], desc: str, conf: float = 0.5) -> dict[str, Any]:
    return {
        "name": name,
        "type": "string",
        "enum_values": values,
        "description": desc,
        "min_confidence": conf,
    }


def text_var(name: str, desc: str, conf: float = 0.5) -> dict[str, Any]:
    return {"name": name, "type": "string", "description": desc, "min_confidence": conf}


def find_field(t: ToolDef, *cands: str) -> str:
    """The tool's field for a role: exact name first, then a field containing the word."""
    names = [f.name for f in t.fields]
    for c in cands:
        if c in names:
            return c
    for c in cands:
        for n in names:
            if c in n:
                return n
    return ""


# Field names per tool role from the spec ("tool_fields"); hidden tool APIs can rename fields.
_TF: dict[str, dict[str, str]] = {}


def role_field(t: ToolDef, *cands: str) -> str:
    """The field playing role cands[0]: the spec's choice when it names a real field, else
    the first candidate name found in the tool."""
    names = [f.name for f in t.fields]
    explicit = (_TF.get(t.name) or {}).get(cands[0])
    if explicit in names:
        return str(explicit)
    return find_field(t, *cands)


def resp(tool: str, role: str) -> str:
    """Response field name for a role (spec "tool_fields.<tool>.response.<role>")."""
    r = ((_TF.get(tool) or {}).get("response") or {}) if isinstance(_TF.get(tool), dict) else {}
    return str(r.get(role) or role) if isinstance(r, dict) else role


def bake(t: ToolDef, roles: dict[tuple[str, ...], str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for cands, var in roles.items():
        f = role_field(t, *cands)
        if f:
            out[f] = var
    return out


def q(s: str) -> str:
    return s.replace('"', "'")


# ---------------------------------------------------------------- hooks
def _lit(x: Any) -> str:
    # repr of a flat dict/list never contains two braces in a row
    return repr(x)


def identity_hooks(
    spec: dict[str, Any], f_field: str = "field", f_answer: str = "answer"
) -> tuple[str, str]:
    """verify_identity hooks. `f_field` / `f_answer`: the tool's names for the question
    discriminator and the answer (bound from the spec; hidden tool APIs can rename them)."""
    ident = spec.get("identity") or {}
    qs = ident.get("questions") or []
    kinds = {x["field"]: x.get("kind", "text") for x in qs}
    order = [x["field"] for x in qs]
    asks = {x["field"]: x.get("ask", "") for x in qs}
    swap_hint = str(ident.get("swap_hint") or "day_month_swapped")
    swap_say = str(ident.get("swap_instruction") or "Ask the customer to say the month as a word.")
    failed = str((spec.get("lines") or {}).get("identity_failed") or "")
    order_dm = str(ident.get("date_order") or "DMY").upper()
    pre = (
        LIB_CALENDAR
        + "\n\ntry:\n"
        + "    _kinds = "
        + _lit(kinds)
        + "\n"
        + "    _b = config.get('body')\n"
        + "    if isinstance(_b, dict):\n"
        + "        _f = str(_b.get("
        + repr(f_field)
        + ") or '')\n"
        + "        _k = _kinds.get(_f, 'text')\n"
        + "        _a = _b.get("
        + repr(f_answer)
        + ")\n"
        + "        if _k == 'date':\n"
        + "            _b["
        + repr(f_answer)
        + "] = normalize_dob(_a, "
        + repr(order_dm)
        + ")\n"
        + "        elif _k in ('digits', 'year'):\n"
        + "            _d = digits_from_speech(_a)\n"
        # pieces: the model can join the parts in the wrong order ('ninety-nine' then
        # 'zero-four' sent as 0499); the same digits in the heard order win
        + "            _h = digits_from_speech(vars.get('id_heard'))\n"
        + "            _l = digits_from_speech(vars.get('id_last'))\n"
        + "            if _d and _h and _h != _d and _l != _d and sorted(_h) == sorted(_d):\n"
        + "                _d = _h\n"
        + "            _b["
        + repr(f_answer)
        + "] = _d or str(_a or '').strip()\n"
        + "        else:\n"
        + "            _b["
        + repr(f_answer)
        + "] = str(_a or '').strip()\n"
        + "        vars['id_last_field'] = _f\n"
        + "except Exception:\n"
        + "    pass\n"
    )
    post = (
        "try:\n"
        + "    _order = "
        + _lit(order)
        + "\n"
        + "    _asks = "
        + _lit(asks)
        + "\n"
        + "    if isinstance(result, dict):\n"
        + "        result.pop('header', None)\n"
        + "        _d = result.get('data')\n"
        + "        if isinstance(_d, dict):\n"
        + "            _t = lambda x: str(x).strip().lower() == 'true'\n"
        + "            _last = ''\n"
        + "            try:\n"
        + "                _last = str(vars.get('id_last_field') or '')\n"
        + "            except Exception:\n"
        + "                _last = ''\n"
        + "            _left = str(_d.get("
        + repr(resp("verify_identity", "wrong_answers_left"))
        + "))\n"
        + "            if _d.get("
        + repr(resp("verify_identity", "hint"))
        + ") == "
        + repr(swap_hint)
        + ":\n"
        + "                _m = "
        + repr(swap_say + " This does not count as a wrong answer.")
        + "\n"
        + "            elif _t(_d.get("
        + repr(resp("verify_identity", "all_verified"))
        + ")):\n"
        + "                _m = 'Both answers are correct. Thank the customer in a few words.'\n"
        + "            elif not _t(_d.get('ok')) and _left != '0':\n"
        + "                _m = 'The answer could not be read. Ask for it again.'\n"
        + "            elif _left == '0':\n"
        + "                _m = "
        + repr("The identity check has failed. Do not ask anything else. " + failed)
        + "\n"
        + "            elif _t(_d.get("
        + repr(resp("verify_identity", "verified"))
        + ")):\n"
        + "                _nxt = [f for f in _order if f != _last]\n"
        + "                _i = _order.index(_last) + 1 if _last in _order else len(_order)\n"
        + "                _rest = _order[_i:] or _nxt\n"
        + "                _m = 'Correct. Now ask: ' + _asks.get(_rest[0], 'the next security question.')\n"
        + "            else:\n"
        + "                _m = 'That answer is not correct. Ask the same question again.'\n"
        + "            _d['what_to_do_next'] = _m\n"
        + "except Exception:\n"
        + "    pass\n"
    )
    return pre, post


CLOSE_PRE = (
    "try:\n"
    "    b = config.get('body')\n"
    "    if isinstance(b, dict):\n"
    "        r = str(b.get('reason') or '').strip()\n"
    "        if not r or r.startswith('{') or b.get('outcome') in NO_REASON:\n"
    "            b.pop('reason', None)\n"
    "except Exception:\n"
    "    pass\n"
)


# ---------------------------------------------------------------- the workflow
def build(spec: dict[str, Any], tools: dict[str, ToolDef]) -> tuple[dict[str, Any], Policy]:
    global _TF
    _TF = spec.get("tool_fields") or {}
    cfg = spec["cfg"]
    neg = cfg.get("neg") or {}
    L = spec.get("lines") or {}
    oc = spec.get("outcomes") or {}
    bank = spec.get("bank_name") or "the bank"
    forbidden = ", ".join(f"'{w}'" for w in spec.get("forbidden_before_verify") or [])
    cv = spec.get("call_variables") or {}
    cid = str(cv.get("customer_id") or "customer_id")
    if cv.get("title") and cv.get("last_name"):
        who_name = "{{" + str(cv["title"]) + "}} {{" + str(cv["last_name"]) + "}}"
    elif cv.get("full_name"):
        who_name = "{{" + str(cv["full_name"]) + "}}"
    elif cv.get("first_name") or cv.get("last_name"):
        who_name = " ".join(
            "{{" + str(cv[k]) + "}}" for k in ("first_name", "last_name") if cv.get(k)
        )
    elif not cv:
        who_name = "{{title}} {{last_name}}"
    else:
        who_name = "the customer"
    who_q = L.get("who_question") or ("Am I speaking with " + who_name + "?")
    now_var = str(cv.get("now") or "now")
    # the code reads vars['now']; a brief that names the call's date differently is copied
    now_alias = (
        f"vars['now'] = vars.get({now_var!r}) or vars.get('now')\n" if now_var != "now" else ""
    )
    ai = q(L.get("ai_answer") or f"I am {bank}'s digital assistant.")
    scam = q(L.get("scam_answer") or "")
    no_info = q(L.get("no_info") or "I do not have that information.")
    goodbye = L.get("goodbye") or "Thank you for your time. Goodbye."
    ident = spec.get("identity") or {}
    questions = ident.get("questions") or []

    def fin(key: str, say: str) -> str:
        o, r = (oc.get(key) or [key, ""])[:2]
        return bodies.finish_pre(o or "", r or "", say)

    # ---- tools
    t_ver = tools["verify_identity"]
    pre_id, post_id = identity_hooks(
        spec, role_field(t_ver, "field", "question"), role_field(t_ver, "answer", "value")
    )
    field_desc = "Which question this answer is for. " + " ".join(
        f"'{x['field']}' for: {x.get('ask', '')}" for x in questions
    )
    verify_fn = freya_tool(
        t_ver,
        "Check one security answer. CALL it every time the customer answers a security "
        "question, once per answer. After the result, follow its what_to_do_next.",
        bake=bake(t_ver, {("customer_id",): cid}),
        model_names={
            role_field(t_ver, "field", "question"): "id_field",
            role_field(t_ver, "answer", "value"): "id_answer",
        },
        field_notes={
            role_field(t_ver, "field", "question"): field_desc,
            role_field(
                t_ver, "answer", "value"
            ): "The customer's answer in their own words, complete.",
        },
        pre_code=pre_id,
        post_code=post_id,
    )
    t_acc = tools["get_account"]
    account_fn = freya_tool(
        t_acc,
        "Get the late products.",
        bake=bake(t_acc, {("customer_id",): cid}),
        max_retries=1,
    )
    t_pr = tools["create_promise"]
    promise_fn = freya_tool(
        t_pr,
        "Record one promise.",
        bake=bake(
            t_pr,
            {
                ("customer_id",): cid,
                ("product_id", "product", "account", "ref"): "rec_product_id",
                ("amount",): "rec_amount",
                ("date", "day"): "rec_date",
            },
        ),
    )
    t_note = tools.get("add_note")
    note_fn = (
        freya_tool(
            t_note,
            "Record why a product has no promise.",
            bake=bake(
                t_note,
                {
                    ("customer_id",): cid,
                    ("product_id", "product", "account", "ref"): "note_product_id",
                    ("reason", "code"): "note_reason",
                },
            ),
        )
        if t_note
        else None
    )
    t_cb = tools["schedule_callback"]
    cb_fn = freya_tool(
        t_cb,
        "Book a callback.",
        bake=bake(
            t_cb, {("customer_id",): cid, ("date", "day"): "cb_date", ("time", "hour"): "cb_time"}
        ),
    )
    t_tr = tools["transfer_to_agent"]
    transfer_fn = freya_tool(
        t_tr,
        "Hand the call to a person.",
        bake=bake(
            t_tr, {("customer_id",): cid, ("queue", "team", "department", "desk"): "transfer_queue"}
        ),
    )
    t_close = tools["close_call"]
    no_reason = [o for o in (neg.get("outcomes") or {}).values()]
    no_reason = [
        o
        for o in (neg.get("outcomes") or {}).values()
        if o
        in ((neg.get("outcomes") or {}).get("promise"), (neg.get("outcomes") or {}).get("paid"))
    ]
    close_fn = freya_tool(
        t_close,
        "Record how the call ended.",
        bake=bake(
            t_close,
            {
                ("customer_id",): cid,
                ("outcome", "result", "status"): "final_outcome",
                ("reason", "code", "detail"): "final_reason",
            },
        ),
        pre_code="NO_REASON = " + _lit(no_reason) + "\n" + CLOSE_PRE,
        extra_config={"alwaysRunsAt": ["end_of_call"]},
    )

    # ---- extraction schemas
    who_values = ["confirms", "someone_else", "wrong_number", "deceased", "question", "unclear"]
    who_desc = (
        "How the person answered '" + who_q + "'. "
        "confirms: they say yes, that's me, speaking, or say their own name. "
        "someone_else: another person answered (for example a relative) or the customer is "
        "not available. wrong_number: the number does not belong to the customer or they do "
        "not know the customer. deceased: they say the customer has died. question: they ask a "
        "question (who is calling, is this a robot, is this a scam, what is it about). "
        "unclear: hello, go ahead, who is this, or anything else."
    )
    id_desc = (
        "declines: the customer refuses to answer a security question, says they do not want "
        "to talk, or asks what the call is about instead of answering. answers: gives an "
        "answer. wants_human: asks to speak to a person or a human agent. question: asks "
        "something else. unclear: anything else."
    )
    reply_desc = (
        "What the customer's LAST message means. yes: agrees to what was just proposed, or "
        "confirms the read-back. no: refuses or cannot pay, naming no day and no amount. "
        "offer: names a day or an amount they will pay. too_much: says the amount is too much "
        "or they cannot afford it, naming no other amount. vague: names a vague time to pay "
        "(next week, end of the month, when the salary comes) and no exact day. paid: says "
        "they already paid it. dispute: says the debt or product is not theirs, or was closed "
        "or paid off in the past. hardship: mentions illness, a death in the family or losing "
        "their job. restructure: asks for a new payment plan or lower instalments. human: "
        "asks for a person or a human agent. stop: says they do not want to talk. question: "
        "asks a question. evasive: answers the payment question but gives no yes, no clear "
        "no, no day and no amount (for example 'I don't know', 'we will see', 'maybe "
        "later'). unclear: anything else, such as small talk, a sentence cut off before the "
        "answer, or a message that does not respond to the question."
    )
    neg_extract = [
        enum_var(
            "reply_kind",
            [
                "yes",
                "no",
                "offer",
                "too_much",
                "vague",
                "paid",
                "dispute",
                "hardship",
                "restructure",
                "human",
                "stop",
                "question",
                "evasive",
                "unclear",
            ],
            reply_desc,
        ),
        text_var(
            "said_date_text",
            "The words of the day or date the customer says they will PAY on, copied from "
            "their LAST message exactly as said (for example 'this Friday', 'the 30th of "
            "October', 'tomorrow'). Not dates of other events. Empty if none.",
        ),
        text_var(
            "said_amount_text",
            "The words of the amount the customer says they will pay, copied from their LAST "
            "message exactly as said (for example '637.50', 'four hundred and twenty'). Empty "
            "if none.",
        ),
        enum_var(
            "all_products",
            ["yes"],
            "yes when the customer's last message is about all of their products together.",
        ),
        enum_var(
            "has_question",
            ["yes"],
            "yes when the customer's last message asks the agent a question (for example "
            "whether it is a robot, for a discount, or about the account), even if it also "
            "answers. Empty otherwise.",
        ),
        enum_var(
            "about_product",
            ["p1", "p2", "p3"],
            "Which product the customer's last message is about when they name one: p1 is the "
            "first product stated, p2 the second, p3 the third. Empty if not named.",
        ),
    ]

    sys_prompt = (
        f"You are the digital assistant of {bank}, on a phone call. Speak English. Say one or "
        "two short sentences per turn. Never invent amounts, dates, products, plans or rules. "
        "Call a tool only when your current task says so. After every tool result, speak to "
        "the customer or call the next tool; never end a turn with nothing."
    )

    nodes: dict[str, Any] = {}
    # ---- opening: who is on the line (the initial node must not be a code node: G0)
    opening_line = ((L.get("opening") or "") + " " + who_q).strip()
    opening_task = (
        # The chat route can skip the entry line; then this sentence still opens the call.
        f'If you have not spoken yet in this call, say exactly: "{q(opening_line)}" '
        "Find out whether you are speaking with " + who_name + ". Do not call any tool. "
        f"Never say why you are calling and never say {forbidden}. If asked whether you are "
        f'a robot or an AI, say: "{ai}"'
        + (f' If asked whether this is a scam, say: "{scam}"' if scam else "")
        + f" If asked who is calling or what it is about, say you are {bank}'s digital "
        "assistant and can only discuss it with "
        + who_name
        + '. Then ask exactly: "'
        + q(who_q)
        + '"'
    )
    who_routes = lambda var, unclear_to: [  # noqa: E731
        tr("setup_id", "the customer confirmed", eq(var, "confirms")),
        tr("setup_tp", "someone else answered", eq(var, "someone_else")),
        tr("fin_wrong_number", "wrong number", eq(var, "wrong_number")),
        tr("fin_deceased", "the customer has died", eq(var, "deceased")),
        tr(unclear_to, "not confirmed"),
    ]
    nodes["opening"] = conv(
        "opening",
        opening_task,
        who_routes("who1", "opening2"),
        on_enter={"type": "tts", "text": opening_line},
        extraction=[enum_var("who1", who_values, who_desc)],
        max_turns=3,
        cap_to="opening2",
        first_entry_only=True,
    )
    nodes["opening2"] = conv(
        "opening2",
        opening_task,
        who_routes("who2", "fin_unclear"),
        on_enter={
            "type": "llm",
            "prompt": "If the person asked something, answer it in one short sentence as your "
            'task says. Then ask exactly: "' + q(who_q) + '"',
        },
        extraction=[enum_var("who2", who_values, who_desc)],
        max_turns=2,
        cap_to="fin_unclear",
    )

    # ---- identity
    nodes["setup_id"] = code_node(
        "setup_id",
        snippet(now_alias + "vars['pre_human_count'] = '0'\n" + bodies.SETUP, cfg, LIB_CALENDAR),
        [tr("identity", "always")],
    )
    q_list = " ".join(f"{i + 1}. {x.get('ask', '')}" for i, x in enumerate(questions))
    id_task = (
        "You are checking the customer's identity before anything else. Ask these security "
        f"questions one at a time, in this order: {q_list} Each time the customer answers, "
        "CALL verify_identity with the field of that question and the answer in the "
        "customer's own words. If an answer comes only in part (a date without the year, "
        "fewer digits than asked for), do not repeat the question and do not call the tool "
        "yet: ask only for the missing part, for example 'And the year?' or 'And the "
        "remaining digits?'. Then call verify_identity once with all the parts together, in "
        "the order the customer said them. After "
        "every tool result, follow its what_to_do_next. Never say why you are calling and "
        f"never say {forbidden}, an amount or a product. If the customer does not want to "
        f'answer or asks what the call is about, explain once: "{q(L.get("identity_why") or "")}" '
        f'and ask the question again. If asked whether you are a robot or an AI: "{ai}"'
        + (f' If asked whether this is a scam: "{scam}"' if scam else "")
    )
    id_vals = ["answers", "declines", "wants_human", "question", "unclear"]
    heard_var = text_var(
        "id_heard",
        "The customer's answer to the security question the agent asked last: every word of "
        "it from all the customer's messages since that question, joined in the order they were "
        "said (for example 'ninety-nine' and then 'zero-four' -> 'ninety-nine zero-four'). "
        "Empty if no answer yet.",
    )
    last_var = text_var(
        "id_last",
        "The words of the customer's last message only that answer the security question, "
        "exactly as said. Empty if the last message has no answer.",
    )
    verified = eq("verify_identity_response.data." + resp("verify_identity", "all_verified"), *TRUE)
    locked = eq(
        "verify_identity_response.data." + resp("verify_identity", "wrong_answers_left"), "0"
    )
    # A request for a person applies before verification too (Brief A rules 42, 45): ask once
    # whether you can help (the spec's count), then transfer. Nothing about the account is said.
    human_limit = int(neg.get("human_requests_to_transfer") or 1)

    def human_routes(var: str) -> list[dict[str, Any]]:
        return [
            tr(
                "pre_transfer",
                "the customer insists on a person",
                all_of(
                    {"variable": var, "operator": "equals", "operand": "wants_human"},
                    {
                        "variable": "pre_human_count",
                        "operator": "greater_than",
                        "operand": human_limit - 2,
                    },
                ),
            ),
            tr("pre_human", "the customer asks for a person", eq(var, "wants_human")),
        ]

    nodes["identity"] = conv(
        "identity",
        id_task,
        [
            tr("account", "both answers verified", verified),
            tr("fin_failed", "no wrong answers left", locked),
            *human_routes("id1"),
            tr("identity2", "the customer declines", eq("id1", "declines")),
        ],
        on_enter={
            "type": "tts",
            "text": (L.get("identity_intro") or "")
            + " "
            + (questions[0].get("ask", "") if questions else ""),
        },
        extraction=[enum_var("id1", id_vals, id_desc, 0.6), heard_var, last_var],
        tools=[verify_fn],
        max_turns=16,
        cap_to="fin_declined",
        first_entry_only=True,
    )
    nodes["identity2"] = conv(
        "identity2",
        id_task,
        [
            tr("account", "both answers verified", verified),
            tr("fin_failed", "no wrong answers left", locked),
            *human_routes("id2"),
            tr("fin_declined", "the customer declines again", eq("id2", "declines")),
        ],
        on_enter={
            "type": "llm",
            "prompt": f'Explain once: "{q(L.get("identity_why") or "")}" Then ask the security '
            "question that is still open again.",
        },
        extraction=[enum_var("id2", id_vals, id_desc, 0.6), heard_var, last_var],
        tools=[verify_fn],
        max_turns=14,
        cap_to="fin_declined",
        first_entry_only=True,
    )

    help_first = (
        (neg.get("lines") or {}).get("help_first")
        or L.get("help_first")
        or ("Before I transfer you, can I help you with this myself?")
    )
    nodes["pre_human"] = conv(
        "pre_human",
        "The customer asked for a person. Do not call any tool. Never say why you are calling "
        f"and never say {forbidden}, an amount or a product.",
        [
            tr("pre_transfer", "the customer insists", eq("hum1", "insists")),
            tr("pre_human_back", "the customer goes on"),
        ],
        on_enter={"type": "tts", "text": help_first},
        extraction=[
            enum_var(
                "hum1",
                ["insists", "continues"],
                "insists: still wants a person. continues: agrees to go on, or anything else.",
            )
        ],
        max_turns=2,
        cap_to="pre_human_back",
    )
    nodes["pre_human_back"] = code_node(
        "pre_human_back",
        "c = vars.get('pre_human_count') or '0'\n"
        "vars['pre_human_count'] = str(int(c) + 1) if str(c).isdigit() else '1'\n"
        "vars['id1'] = ''\nvars['id2'] = ''\nvars['hum1'] = ''\n",
        [tr("identity", "always")],
    )
    n_oc = neg.get("outcomes") or {}
    nodes["pre_transfer"] = code_node(
        "pre_transfer",
        f"vars['transfer_queue'] = {str((neg.get('queues') or {}).get('human') or 'human')!r}\n"
        + bodies.finish_pre(
            str(n_oc.get("transferred") or "transferred"),
            str((neg.get("transfer_reasons") or {}).get("human") or "human"),
            (
                (neg.get("lines") or {}).get("transfer_human")
                or "I am connecting you with a colleague now."
            )
            + " "
            + goodbye,
        ),
        [tr("transfer_pre", "always")],
    )

    # ---- account and negotiation
    nodes["account"] = api_node(
        "account",
        account_fn,
        [tr("facts", "always")],
        [{"path": "data." + str(spec.get("products_list") or "products"), "variable": "products"}],
    )
    nodes["facts"] = code_node("facts", snippet(bodies.FACTS, cfg), [tr("negotiate_say", "always")])
    neg_task = (
        "Your instruction for this turn: {{neg_say}}\n\n"
        "Follow that instruction, in one or two short sentences. Do not call any tool. Never "
        "compute a date or an amount yourself and never name a date, an amount or a term that "
        "is not in the instruction. Never ask how much the customer can pay. Never offer a "
        "discount, a waiver or an instalment plan. If asked whether you are a robot or an AI: "
        f'"{ai}"'
        + (f' If asked whether this is a scam: "{scam}"' if scam else "")
        + f' A question you cannot answer from the facts below: "{no_info}"\n\n'
        "Facts:\n{{product_brief}}"
    )
    # Two nodes share the extraction: `negotiate_say` speaks the engine's exact sentence
    # (amounts, dates, fixed lines); `negotiate` lets the model answer a question.
    nodes["negotiate_say"] = conv(
        "negotiate_say",
        "Your last sentence was: {{neg_say}}\nIf the customer asks you to repeat, say it again "
        "exactly. Do not call any tool. Never name an amount, a date or a term of your own.",
        [tr("decide", "always")],
        on_enter={"type": "tts", "text": "{{neg_say}}"},
        extraction=neg_extract,
        max_turns=6,
        cap_to="decide",
    )
    nodes["negotiate"] = conv(
        "negotiate",
        neg_task,
        [tr("decide", "always")],
        extraction=neg_extract,
        max_turns=6,
        cap_to="decide",
    )
    after = lambda: [  # noqa: E731
        tr("record", "record a promise", eq("neg_action", "record")),
        tr("transfer", "transfer", eq("neg_action", "transfer")),
        tr("wrap", "finish", eq("neg_action", "finish")),
        tr("negotiate", "the model answers a question", eq("neg_mode", "llm")),
        tr("negotiate_say", "continue"),
    ]
    nodes["decide"] = code_node("decide", snippet(bodies.DECIDE, cfg), after())
    nodes["record"] = api_node(
        "record",
        promise_fn,
        [tr("after_record", "always")],
        [{"path": "data.ok", "variable": "rec_ok"}],
    )
    nodes["after_record"] = code_node("after_record", snippet(bodies.AFTER_RECORD, cfg), after())
    nodes["transfer"] = api_node("transfer", transfer_fn, [tr("wrap", "always")])
    # before verification: no notes are possible, straight to the goodbye
    nodes["transfer_pre"] = api_node("transfer_pre", transfer_fn, [tr("goodbye", "always")])
    notes_on = bool(neg.get("notes")) and note_fn is not None
    wrap_routes = [tr("goodbye", "always")]
    if notes_on:
        wrap_routes.insert(0, tr("note", "a note to add", gt("note_count", 0)))
    nodes["wrap"] = code_node("wrap", bodies.WRAP, wrap_routes)
    if notes_on:
        nodes["note"] = api_node("note", note_fn, [tr("after_note", "always")])
        nodes["after_note"] = code_node(
            "after_note",
            snippet(bodies.AFTER_NOTE, cfg),
            [tr("note", "another note", gt("note_count", 0)), tr("goodbye", "always")],
        )

    # ---- third party and callback
    nodes["setup_tp"] = code_node(
        "setup_tp",
        snippet(now_alias + bodies.SETUP_TP, cfg, LIB_CALENDAR),
        [tr("third_party", "always")],
    )
    tp_task = (
        "The person on the line is not " + who_name + ". Do not call any tool. Never "
        f"say why you are calling and never say {forbidden}, an account, an amount or a "
        "product, even if they ask or offer to pay. Do not ask security questions. If they "
        "ask why you are calling or what it is about, say only that you can discuss it with "
        + who_name
        + " alone. Ask when "
        + who_name
        + " can be reached. Possible callback times: "
        "{{callback_window_text}}. {{cb_say}}"
    )
    nodes["third_party"] = conv(
        "third_party",
        tp_task,
        [
            tr("fin_wrong_number", "wrong number", eq("tp_reply", "wrong_number")),
            tr("fin_deceased", "the customer has died", eq("tp_reply", "deceased")),
            tr("fin_no_callback", "no callback wanted", eq("tp_reply", "refuses")),
            tr("cb_check", "check what was said"),
        ],
        extraction=[
            enum_var(
                "tp_reply",
                ["gives_time", "wrong_number", "deceased", "refuses", "question", "other"],
                "gives_time: names a day or a time when the customer can be reached. "
                "wrong_number: the number does not belong to the customer. deceased: the "
                "customer has died. refuses: clearly says they will not tell when the customer "
                "can be reached or do not want a call back. question: asks something (why you "
                "call, who you are, what it is about); a question is not a refusal. "
                "other: anything else.",
            ),
            text_var(
                "cb_day_text",
                "The words of the day when the customer can be reached, from the last message "
                "(for example 'tomorrow', 'Friday'). Empty if none.",
            ),
            text_var(
                "cb_time_text",
                "The words of the time of day when the customer can be reached, from the last "
                "message (for example 'around eleven', '3 pm'). Empty if none.",
            ),
        ],
        max_turns=8,
        cap_to="fin_no_callback",
    )
    reasks = int(spec.get("callback_reasks", 1))
    nodes["cb_check"] = code_node(
        "cb_check",
        snippet(bodies.CB_CHECK, cfg, LIB_CALENDAR),
        [
            tr("cb_book", "a time inside the window", eq("cb_state", "ok")),
            tr(
                "fin_no_callback",
                "outside the window again",
                all_of(
                    {"variable": "cb_state", "operator": "equals", "operand": "outside"},
                    {"variable": "cb_tries", "operator": "greater_than", "operand": reasks},
                ),
            ),
            tr("third_party", "keep talking"),
        ],
    )
    # F11: say "booked" only when the bank booked it; a failed booking closes without one
    nodes["cb_book"] = api_node(
        "cb_book",
        cb_fn,
        [
            tr(
                "fin_no_callback",
                "the booking failed",
                {
                    "logic": "or",
                    "conditions": [
                        {
                            "variable": "cb_book_failure_count",
                            "operator": "greater_than",
                            "operand": 0,
                        },
                        {"variable": "cb_ok", "operator": "equals", "operand": "false"},
                        {"variable": "cb_ok", "operator": "equals", "operand": "False"},
                    ],
                },
            ),
            tr("fin_callback", "always"),
        ],
        [{"path": "data.ok", "variable": "cb_ok"}],
    )

    # ---- endings before the negotiation
    nodes["fin_unclear"] = code_node(
        "fin_unclear", fin("unclear", goodbye), [tr("goodbye", "always")]
    )
    nodes["fin_wrong_number"] = code_node(
        "fin_wrong_number",
        fin("wrong_number", "Sorry to have bothered you. Goodbye."),
        [tr("goodbye", "always")],
    )
    nodes["fin_deceased"] = code_node(
        "fin_deceased",
        fin("deceased", (L.get("deceased") or "") + " Goodbye."),
        [tr("goodbye", "always")],
    )
    nodes["fin_callback"] = code_node(
        "fin_callback",
        fin("callback", (L.get("callback_booked") or "") + " " + goodbye),
        [tr("goodbye", "always")],
    )
    nodes["fin_no_callback"] = code_node(
        "fin_no_callback", fin("no_callback", goodbye), [tr("goodbye", "always")]
    )
    nodes["fin_failed"] = code_node(
        "fin_failed",
        fin("failed", (L.get("identity_failed") or "") + " " + goodbye),
        [tr("goodbye", "always")],
    )
    nodes["fin_declined"] = code_node(
        "fin_declined", fin("declined", goodbye), [tr("goodbye", "always")]
    )
    nodes["goodbye"] = {
        "name": "goodbye",
        "node_type": "end_call",
        "on_enter": {"type": "silent"},
        "disable_inactivity_monitor": True,
        "transitions": [],
        "terminal": {
            "type": "function",
            "function": {
                "name": "call_closed",
                "handler": "__function__:end_call",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message_mode": {"type": "string", "default": "custom"},
                        "spoken_message": {"type": "string", "default": "{{final_say}}"},
                    },
                },
            },
        },
    }
    wf = {
        "initial_node": "opening",
        "system_prompt": sys_prompt,
        "nodes": nodes,
        "global_nodes": {},
        "tools": [close_fn],
    }
    policy = Policy(
        verified_vars={"verify_identity_response.data." + resp("verify_identity", "all_verified")},
        promise_nodes={"record"},
        bounded_loops={"record", "after_record", "note", "after_note"},
    )
    return wf, policy


__all__ = ["LIB", "build"]
