"""Hidden briefs come with their own tool API file: "The seven tool names stay the same. The
fields and the allowed values can change." (kit/README.md). Each mutation of the kit's OpenAPI
is compiled twice: with the spec written for the kit (stale) and with the spec adapted to the
change. Pipeline: parse_openapi -> check -> autocorrect -> to_generator -> build -> validate.

Oracle: every body leaf is an OpenAPI field; every required leaf has a source (a constant, a call
variable, a variable the workflow sets, the model for verify_identity's answer); every response
mapping path is in the response; outcome/queue literals in the code are enum values; the identity
hooks and the facts node work on mock responses in the mutated shape; the request hooks send
values in the formats the API names. Adapted: no problems. Stale: the mutation needs no spec
change (clean), or autocorrect fixes it (fixed), or check() reports it (reported); never silent.
"""

from __future__ import annotations

import ast
import copy
import functools
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fb import generate
from fb.generate import build, match_enum
from fb.openapi_tools import parse_openapi, path_schema, wire_format
from fb.spec_extract import TOOL_ROLES, _bind, autocorrect, bindings, check, to_generator
from fb.validate import validate

from conftest import KIT

HERE = Path(__file__).parent
OA = json.loads((KIT / "tools" / "openapi.json").read_text())
SPEC = json.loads((HERE / "fixtures" / "spec_ab_extracted.json").read_text())
BRIEF = (KIT / "briefs" / "brief-a.md").read_text() + (KIT / "briefs" / "brief-b.md").read_text()
P = {
    "product_id": "p1",
    "type": "credit_card",
    "card_last4": "1234",
    "past_due": 420.0,
    "days_late": 12,
    "minimum_payment": 126.0,
}
# values the workflow's variables hold when a write is sent (ISO date, decimal amount, HH:MM)
VALUES = {
    "customer_id": "c1",
    "rec_product_id": "p1",
    "rec_amount": 420.5,
    "rec_date": "2026-10-16",
    "cb_date": "2026-10-16",
    "cb_time": "11:00",
    "note_product_id": "p1",
    "note_reason": "refused",
    "transfer_queue": "support",
    "final_outcome": "promise",
    "final_reason": "",
    "last_name": "Smith",
}
# variables the workflow's code sets for the calls it sends (generate.build's roles)
CODE_VARS = {k for k in VALUES if k not in ("customer_id", "last_name")}


def req(oa, tool):
    path = next(p for p in oa["paths"] if p.strip("/").split("/")[-1] == tool)
    op = next(iter(oa["paths"][path].values()))
    return op["requestBody"]["content"]["application/json"]["schema"]


def resp_(oa, tool):
    op = next(iter(oa["paths"]["/" + tool].values()))
    return op["responses"]["200"]["content"]["application/json"]["schema"]


def rename_prop(schema, old, new):
    schema["properties"] = {(new if k == old else k): v for k, v in schema["properties"].items()}
    if "required" in schema:
        schema["required"] = [new if r == old else r for r in schema["required"]]


def tf(spec, tool, **kw):
    spec.setdefault("tool_fields", {}).setdefault(tool, {}).update(kw)


# name -> (mutate(oa, spec, adapt), expectations). Expectations: stale = clean | fixed |
# reported; verify / account / product reshape a kit-shaped mock response; body: expected
# value per workflow variable after the request hook; url: tool -> expected URL suffix.
M: dict[str, tuple[Any, dict[str, Any]]] = {}


def mut(name, stale="clean", **exp):
    def d(f):
        M[name] = (f, {"stale": stale, **exp})
        return f

    return d


@mut("01 customer_id -> client_id", "fixed")
def m01(oa, spec, adapt):
    for t in TOOL_ROLES:
        rename_prop(req(oa, t), "customer_id", "client_id")
        if adapt:
            tf(spec, t, customer_id="client_id")


@mut("02 amount -> promised_amount")
def m02(oa, spec, adapt):
    rename_prop(req(oa, "create_promise"), "amount", "promised_amount")
    if adapt:
        tf(spec, "create_promise", amount="promised_amount")


@mut("03 date -> payment_date / callback_date")
def m03(oa, spec, adapt):
    rename_prop(req(oa, "create_promise"), "date", "payment_date")
    rename_prop(req(oa, "schedule_callback"), "date", "callback_date")
    if adapt:
        tf(spec, "create_promise", date="payment_date")
        tf(spec, "schedule_callback", date="callback_date")


@mut("04 verify field -> challenge, answer -> response_text", "fixed")
def m04(oa, spec, adapt):
    rename_prop(req(oa, "verify_identity"), "field", "challenge")
    rename_prop(req(oa, "verify_identity"), "answer", "response_text")
    if adapt:
        tf(spec, "verify_identity", field="challenge", answer="response_text")


@mut("05 close_call outcomes renamed (not_verified, no_promise)", "reported")
def m05(oa, spec, adapt):
    e = req(oa, "close_call")["properties"]["outcome"]["enum"]
    e[:] = [
        {"not_verified": "identity_failed", "no_promise": "declined_to_pay"}.get(x, x) for x in e
    ]
    if adapt:
        for v in spec["outcomes"].values():
            if v[0] == "not_verified":
                v[0] = "identity_failed"
        spec["neg"]["outcomes"]["no_promise"] = "declined_to_pay"


@mut("06 transfer queue general -> human", "fixed")
def m06(oa, spec, adapt):
    req(oa, "transfer_to_agent")["properties"]["queue"]["enum"] = [
        "restructuring",
        "human",
        "support",
    ]
    if adapt:
        spec["neg"]["queues"]["human"] = "human"


@mut("07 close_call reason required, create_promise amount optional")
def m07(oa, spec, adapt):
    req(oa, "close_call")["required"].append("reason")
    req(oa, "create_promise")["required"].remove("amount")


def _nest_products(d):
    d = dict(d)
    d["accounts"] = {"items": d.pop("products")}
    return d


@mut("08 products nested: data.accounts.items", "fixed", account=_nest_products)
def m08(oa, spec, adapt):
    r = resp_(oa, "get_account")
    r["properties"]["accounts"] = {
        "type": "object",
        "properties": {"items": r["properties"].pop("products")},
    }
    if adapt:
        spec["product_fields"]["list"] = "accounts.items"


VERIFY_KEYS = ("verified", "wrong_answers_left", "all_verified", "hint")


def _nest_verify(d):
    d = dict(d)
    d["result"] = {k: d.pop(k) for k in VERIFY_KEYS if k in d}
    return d


@mut("09 verify flags nested under data.result", "fixed", verify=_nest_verify)
def m09(oa, spec, adapt):
    p = resp_(oa, "verify_identity")["properties"]
    p["result"] = {"type": "object", "properties": {k: p.pop(k) for k in VERIFY_KEYS}}
    if adapt:
        tf(spec, "verify_identity", response={k: "result." + k for k in VERIFY_KEYS})


@mut(
    "10 ok removed from every response", verify=lambda d: {k: v for k, v in d.items() if k != "ok"}
)
def m10(oa, spec, adapt):
    for t in TOOL_ROLES:
        resp_(oa, t)["properties"].pop("ok", None)


def _success(d):
    d = dict(d)
    d["success"] = d.pop("ok")
    return d


@mut("11 ok -> success everywhere", "fixed", verify=_success)
def m11(oa, spec, adapt):
    for t in TOOL_ROLES:
        r = resp_(oa, t)["properties"]
        r["success"] = r.pop("ok")
    if adapt:
        for t in ("verify_identity", "create_promise", "schedule_callback"):
            tf(spec, t, response={"ok": "success"})


@mut(
    "12 product_id -> account_id (response and requests)",
    "fixed",
    product=lambda p: {("account_id" if k == "product_id" else k): v for k, v in p.items()},
)
def m12(oa, spec, adapt):
    it = resp_(oa, "get_account")["properties"]["products"]["items"]
    it["properties"]["account_id"] = it["properties"].pop("product_id")
    rename_prop(req(oa, "create_promise"), "product_id", "account_id")
    rename_prop(req(oa, "add_note"), "product_id", "account_id")
    if adapt:
        spec["product_fields"]["product_id"] = "account_id"
        tf(spec, "create_promise", product_id="account_id")
        tf(spec, "add_note", product_id="account_id")


@mut("13 question enum date_of_birth -> dob, phone_last4 -> phone_tail", "fixed")
def m13(oa, spec, adapt):
    req(oa, "verify_identity")["properties"]["field"]["enum"] = ["dob", "phone_tail"]
    if adapt:
        for q in spec["identity"]["questions"]:
            q["field"] = {"date_of_birth": "dob", "phone_last4": "phone_tail"}[q["field"]]


@mut("14 every schema behind a $ref")
def m14(oa, spec, adapt):
    cs = oa.setdefault("components", {})["schemas"] = {}
    for p in list(oa["paths"]):
        op = oa["paths"][p]["post"]
        n = p.strip("/")
        c = op["requestBody"]["content"]["application/json"]
        cs[n + "_req"] = c["schema"]
        c["schema"] = {"$ref": f"#/components/schemas/{n}_req"}
        r = op["responses"]["200"]["content"]["application/json"]
        if n == "get_account":
            cs["Product"] = r["schema"]["properties"]["products"]["items"]
            r["schema"]["properties"]["products"]["items"] = {
                "$ref": "#/components/schemas/Product"
            }
        cs[n + "_resp"] = r["schema"]
        r["schema"] = {"$ref": f"#/components/schemas/{n}_resp"}


@mut("15 extra optional request and response fields")
def m15(oa, spec, adapt):
    req(oa, "create_promise")["properties"]["channel"] = {"type": "string"}
    req(oa, "close_call")["properties"]["agent_note"] = {"type": "string"}
    item = resp_(oa, "get_account")["properties"]["products"]["items"]
    item["properties"]["currency"] = {"type": "string"}


@mut("16 new required one-value enum (currency GBP)")
def m16(oa, spec, adapt):
    s = req(oa, "create_promise")
    s["properties"]["currency"] = {"type": "string", "enum": ["GBP"]}
    s["required"].append("currency")


@mut("17 new required free string (create_promise.channel)", "reported")
def m17(oa, spec, adapt):
    s = req(oa, "create_promise")
    s["properties"]["channel"] = {"type": "string"}
    s["required"].append("channel")
    if adapt:
        tf(spec, "create_promise", extras={"channel": "phone"})


@mut("18 new required field with a default (close_call.source)")
def m18(oa, spec, adapt):
    s = req(oa, "close_call")
    s["properties"]["source"] = {"type": "string", "default": "voice"}
    s["required"].append("source")


@mut(
    "19 products -> late_products, product type mortgage added",
    "reported",
    account=lambda d: {("late_products" if k == "products" else k): v for k, v in d.items()},
)
def m19(oa, spec, adapt):
    r = resp_(oa, "get_account")["properties"]
    r["late_products"] = r.pop("products")
    r["late_products"]["items"]["properties"]["type"]["enum"].append("mortgage")
    if adapt:
        spec["product_fields"]["list"] = "late_products"
        spec["floor_field"]["mortgage"] = ""
        spec["product_names"]["mortgage"] = "mortgage"


def _overdue(p):
    p = dict(p)
    p["amount_overdue"] = p.pop("past_due")
    p["days_overdue"] = p.pop("days_late")
    return p


@mut("20 past_due -> amount_overdue, days_late -> days_overdue", "fixed", product=_overdue)
def m20(oa, spec, adapt):
    it = resp_(oa, "get_account")["properties"]["products"]["items"]["properties"]
    it["amount_overdue"] = it.pop("past_due")
    it["days_overdue"] = it.pop("days_late")
    if adapt:
        spec["product_fields"].update(past_due="amount_overdue", days_late="days_overdue")


@mut("21 transfer queue -> team")
def m21(oa, spec, adapt):
    rename_prop(req(oa, "transfer_to_agent"), "queue", "team")
    if adapt:
        tf(spec, "transfer_to_agent", queue="team")


@mut("22 close_call outcome -> call_result, reason -> detail_code")
def m22(oa, spec, adapt):
    rename_prop(req(oa, "close_call"), "reason", "detail_code")
    rename_prop(req(oa, "close_call"), "outcome", "call_result")
    if adapt:
        tf(spec, "close_call", reason="detail_code", outcome="call_result")


# ---------------------------------------------------------------- shapes the audit found
@mut("23 allOf request bodies with a $ref part")
def m23(oa, spec, adapt):
    cs = oa.setdefault("components", {}).setdefault("schemas", {})
    cs["Customer"] = {
        "type": "object",
        "properties": {"customer_id": {"type": "string"}},
        "required": ["customer_id"],
    }
    for t in TOOL_ROLES:
        c = next(iter(oa["paths"]["/" + t].values()))["requestBody"]["content"]["application/json"]
        s = c["schema"]
        s["properties"].pop("customer_id")
        s["required"].remove("customer_id")
        c["schema"] = {"allOf": [{"$ref": "#/components/schemas/Customer"}, s]}


@mut("24 oneOf request body, field schemas behind allOf/$ref")
def m24(oa, spec, adapt):
    cs = oa.setdefault("components", {}).setdefault("schemas", {})
    c = oa["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]
    s = c["schema"]
    cs["Outcome"] = s["properties"]["outcome"]
    s["properties"]["outcome"] = {"allOf": [{"$ref": "#/components/schemas/Outcome"}]}
    other = copy.deepcopy(s)
    other["required"] = ["customer_id", "outcome", "reason"]
    c["schema"] = {"oneOf": [s, other]}


@mut("25 charset content types and responses behind $ref")
def m25(oa, spec, adapt):
    rs = oa.setdefault("components", {}).setdefault("responses", {})
    for t in TOOL_ROLES:
        op = oa["paths"]["/" + t]["post"]
        c = op["requestBody"]["content"]
        c["application/json; charset=utf-8"] = c.pop("application/json")
        ok = op["responses"]["200"]
        ok["content"] = {"application/json; charset=utf-8": ok["content"]["application/json"]}
        rs[t + "_ok"] = ok
        op["responses"]["200"] = {"$ref": f"#/components/responses/{t}_ok"}


@mut("26 success as 201 / 202 / default")
def m26(oa, spec, adapt):
    for t, code in (("create_promise", "201"), ("schedule_callback", "202")):
        r = oa["paths"]["/" + t]["post"]["responses"]
        r[code] = r.pop("200")
    r = oa["paths"]["/verify_identity"]["post"]["responses"]
    r["default"] = r.pop("200")


@mut("27 get_account as GET with a query parameter", url={"get_account": "?customer_id=c1"})
def m27(oa, spec, adapt):
    op = oa["paths"]["/get_account"].pop("post")
    op.pop("requestBody")
    op["parameters"] = [
        {"name": "customer_id", "in": "query", "required": True, "schema": {"type": "string"}}
    ]
    oa["paths"]["/get_account"]["get"] = op


@mut("28 new required enum, no default (create_promise.channel)", "reported")
def m28(oa, spec, adapt):
    s = req(oa, "create_promise")
    s["properties"]["channel"] = {"type": "string", "enum": ["voice", "sms"]}
    s["required"].append("channel")
    if adapt:
        tf(spec, "create_promise", extras={"channel": "voice"})


@mut("29 new required field named like a call variable (close_call.lastName)")
def m29(oa, spec, adapt):
    s = req(oa, "close_call")
    s["properties"]["lastName"] = {"type": "string"}
    s["required"].append("lastName")


@mut(
    "30 formats: DD/MM/YYYY and MM/DD/YYYY dates, pence, HH:MM:SS, DOB DD/MM/YYYY",
    body={
        "rec_amount": 42050,
        "rec_date": "16/10/2026",
        "cb_date": "10/16/2026",
        "cb_time": "11:00:00",
    },
    dob="14/03/1985",
)
def m30(oa, spec, adapt):
    p = req(oa, "create_promise")["properties"]
    p["amount"] = {"type": "integer", "description": "Amount in pence."}
    p["date"] = {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$", "description": "DD/MM/YYYY"}
    c = req(oa, "schedule_callback")["properties"]
    c["date"] = {"type": "string", "description": "The day, MM/DD/YYYY."}
    c["time"] = {"type": "string", "description": "HH:MM:SS"}
    a = req(oa, "verify_identity")["properties"]["answer"]
    a["description"] = "For date_of_birth: the date as DD/MM/YYYY. For phone_last4: four digits."


@mut("31 callback date as date-time", body={"cb_date": "2026-10-16T00:00:00"})
def m31(oa, spec, adapt):
    req(oa, "schedule_callback")["properties"]["date"]["format"] = "date-time"


@mut("32 enum values in capitals", "fixed")
def m32(oa, spec, adapt):
    for t, f in (("close_call", "outcome"), ("add_note", "reason"), ("transfer_to_agent", "queue")):
        e = req(oa, t)["properties"][f]["enum"]
        e[:] = [x.upper() for x in e]
    if adapt:
        for v in spec["outcomes"].values():
            v[0] = v[0].upper()
        neg = spec["neg"]
        for k in ("outcomes", "reasons", "queues"):
            neg[k] = {a: (b.upper() if isinstance(b, str) else b) for a, b in neg[k].items()}


@mut("33 customer_id as a path parameter", url={"create_promise": "/promises/c1"})
def m33(oa, spec, adapt):
    op = oa["paths"].pop("/create_promise")["post"]
    op["operationId"] = "create_promise"
    s = op["requestBody"]["content"]["application/json"]["schema"]
    s["properties"].pop("customer_id")
    s["required"].remove("customer_id")
    op["parameters"] = [
        {"name": "customer_id", "in": "path", "required": True, "schema": {"type": "string"}}
    ]
    oa["paths"]["/promises/{customer_id}"] = {"post": op}


@mut("34 operations named otherwise: camelCase ids, a bank_ prefix, Turkish paths")
def m34(oa, spec, adapt):
    # audit 08 TA-5: the tools are still the kit's seven roles (parse_openapi keys them by the
    # kit name), so neither spec needs a change
    for p, item in list(oa["paths"].items()):
        tool = p.strip("/")
        if tool == "close_call":
            oa["paths"]["/gorusme/kapat"] = oa["paths"].pop(p)
            item["post"]["summary"] = "Görüşmeyi kapat ve sonucu kaydet"
        elif tool == "add_note":
            oa["paths"]["/bank_add_note"] = oa["paths"].pop(p)
        else:
            words = tool.split("_")
            item["post"]["operationId"] = words[0] + "".join(w.capitalize() for w in words[1:])


# the bank's amount in cents: the engine must still say 420.00 (response_scale, applied by
# the facts code)
@mut(
    "35 response amounts in minor units",
    product=lambda p: {**p, "past_due": int(p["past_due"] * 100)},
)
def m35(oa, spec, adapt):
    # audit 08 TA-4: the description says cents; parse_openapi gives the path a 0.01 scale
    items = resp_(oa, "get_account")["properties"]["products"]["items"]
    items = (
        items
        if "properties" in items
        else oa["components"]["schemas"][items["$ref"].split("/")[-1]]
    )
    items["properties"]["past_due"]["description"] = "Integer cents (42050 means 420.50)."


# ---------------------------------------------------------------- the pipeline
def run(name: str, adapt: bool) -> dict[str, Any]:
    oa, spec = copy.deepcopy(OA), copy.deepcopy(SPEC)
    M[name][0](oa, spec, adapt)
    # a mutation that needs no spec change builds the same workflow both ways: built once
    return _run(json.dumps(oa), json.dumps(spec))


@functools.cache
def _run(oa_text: str, spec_text: str) -> dict[str, Any]:
    oa, spec = json.loads(oa_text), json.loads(spec_text)
    tools = parse_openapi(oa)
    first = check(spec, tools, BRIEF, oa)
    notes = autocorrect(spec, tools, oa)
    left = check(spec, tools, BRIEF, oa)
    gen = to_generator(spec)
    wf, pol = build(gen, tools)
    pol.call_vars |= {str(v) for v in (gen.get("call_variables") or {}).values() if v}
    rep = validate(wf, tools, pol)
    return {
        "oa": oa,
        "spec": spec,
        "tools": tools,
        "first": first,
        "notes": notes,
        "left": left,
        "wf": wf,
        "errors": [str(i) for i in rep.errors],
        "fixes": list(generate.FIXES),
    }


def calls(wf):
    for k, n in wf["nodes"].items():
        for t in (n.get("tools") or []) + (n.get("pre_actions") or []):
            if t["function"]["handler"] == "__function__:api_call":
                yield k, n, t
    for t in wf.get("tools") or []:
        yield "lifecycle", None, t


def code_of(wf, node):
    return wf["nodes"][node]["pre_actions"][0]["function"]["parameters"]["properties"]["code"][
        "default"
    ]


def enums(r):
    tools, spec = r["tools"], r["spec"]

    def of(tool, *cands):
        t = tools[tool]
        f = t.field(_bind(spec, t, cands))
        return list(f.enum) if f else []

    return (
        of("close_call", "outcome", "result", "status"),
        of("transfer_to_agent", "queue", "team", "department", "desk"),
    )


def oracle(r) -> list[str]:
    bad: list[str] = []
    wf, tools, spec = r["wf"], r["tools"], r["spec"]
    cvars = {str(v) for v in (spec.get("call_variables") or {}).values() if v}
    for k, n, t in calls(wf):
        fn = t["function"]
        api = tools[fn["name"]]
        names = {f.name for f in api.fields}
        body = fn["parameters"]["properties"]["body"]
        leaves = body["properties"]
        bad += [f"{k}: {fn['name']}.{x} is not an API field" for x in set(leaves) - names]
        model = set(body.get("required") or [])
        for f in api.fields:
            if not f.required:
                continue
            leaf = leaves.get(f.name)
            if leaf is None:
                bad.append(f"{k}: required {fn['name']}.{f.name} missing")
                continue
            d = leaf.get("default")
            m = re.fullmatch(r"\{\{(\w+)\}\}", str(d)) if isinstance(d, str) else None
            if d is None or d == "":
                bad.append(f"{k}: required {fn['name']}.{f.name} has no value")
            elif (
                m
                and m.group(1) not in cvars | CODE_VARS
                # verify_identity's answer: the model fills it
                and not (n and n.get("node_type") == "conversation" and f.name in model)
            ):
                bad.append(f"{k}: required {fn['name']}.{f.name} = {d} nothing sets")
    for k, n in wf["nodes"].items():
        for m in n.get("response_mapping") or []:
            for t in n.get("pre_actions") or []:
                api = tools[t["function"]["name"]]
                path = m["path"].removeprefix("data.")
                if api.response_props and path_schema(api.response_props, path) is None:
                    bad.append(f"{k}: mapping {m['path']} not in the {api.name} response")
    outs, queues = enums(r)
    lits = []
    for k in wf["nodes"]:
        if k.startswith("fin_") or k == "pre_transfer":
            lits += re.findall(r"vars\['final_outcome'\] = '([^']*)'", code_of(wf, k))
    close = wf["tools"][0]["pre_process_code"]
    hang = ast.literal_eval(re.search(r"^HANGUP = (.*)$", close, re.M).group(1))
    lits += [hang["promise"], hang["no_promise"][0], hang["unreached"][0], hang["paid"]]
    m = re.search(r"CFG = json\.loads\(r'''\n(.*?)\n'''\)", code_of(wf, "decide"), re.S)
    neg = json.loads(m.group(1))["neg"]
    lits += [v for v in neg["outcomes"].values() if v]
    bad += [f"outcome {x!r} not in {outs}" for x in lits if outs and x not in outs]
    q = re.findall(r"vars\['transfer_queue'\] = '([^']*)'", code_of(wf, "pre_transfer"))
    q += [v for v in neg["queues"].values() if v]
    bad += [f"queue {x!r} not in {queues}" for x in q if queues and x not in queues]
    return bad


# ---------------------------------------------------------------- behaviour on mock data
def substitute(body: dict, values: dict) -> dict:
    out = {}
    for k, leaf in body["properties"].items():
        d = leaf.get("default")
        m = re.fullmatch(r"\{\{(\w+)\}\}", d) if isinstance(d, str) else None
        out[k] = values.get(m.group(1), "") if m else d
    return out


def send(t, values):
    """The request a write sends: defaults substituted, then its pre hook."""
    p = t["function"]["parameters"]["properties"]
    cfg = {"url": p["url"]["default"], "body": substitute(p["body"], values)}
    if t.get("pre_process_code"):
        exec(t["pre_process_code"], {"vars": dict(values), "config": cfg})
    return cfg


def check_writes(r, exp) -> list[str]:
    bad = []
    want = {**VALUES, **(exp.get("body") or {})}
    for k, _, t in calls(r["wf"]):
        name = t["function"]["name"]
        if name == "verify_identity":
            continue
        cfg = send(t, VALUES)
        leaves = t["function"]["parameters"]["properties"]["body"]["properties"]
        for f, leaf in leaves.items():
            m = re.fullmatch(r"\{\{(\w+)\}\}", str(leaf.get("default")))
            # final_reason: close_call's hook drops an empty one on purpose
            if m and m.group(1) in want and m.group(1) != "final_reason" and f in cfg["body"]:
                got, exp_v = cfg["body"][f], want[m.group(1)]
                if got != exp_v:
                    bad.append(f"{k}: {name}.{f} sent {got!r}, want {exp_v!r}")
        suffix = (exp.get("url") or {}).get(name)
        if suffix:
            if not cfg["url"].endswith(suffix):
                bad.append(f"{k}: {name} url {cfg['url']}")
            if "customer_id" in cfg["body"]:
                bad.append(f"{k}: {name} still sends customer_id in the body")
    return bad


def flat(d, pre=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flat(v, pre + k + "."))
        else:
            out[pre + k] = "true" if v is True else "false" if v is False else str(v)
    return out


def fires(cond, table) -> bool:
    if "conditions" in cond:
        got = [fires(c, table) for c in cond["conditions"]]
        return all(got) if cond.get("logic") == "and" else any(got)
    return str(table.get(cond.get("variable"))) == str(cond.get("operand"))


def check_identity(r, exp) -> list[str]:
    bad = []
    wf = r["wf"]
    t = next(
        x for x in wf["nodes"]["identity"]["tools"] if x["function"]["name"] == "verify_identity"
    )
    leaves = t["function"]["parameters"]["properties"]["body"]["properties"]
    f_field = next(k for k, v in leaves.items() if v.get("default") == "{{id_field}}")
    f_answer = next(k for k, v in leaves.items() if v.get("default") == "{{id_answer}}")
    dob = match_enum("date_of_birth", leaves[f_field].get("enum") or [])
    body = substitute(t["function"]["parameters"]["properties"]["body"], VALUES)
    body.update({f_field: dob, f_answer: "the fourteenth of March 1985"})
    v = {"id_last": "", "id_heard": ""}
    exec(t["pre_process_code"], {"vars": v, "config": {"body": body}})
    if body[f_answer] != exp.get("dob", "1985-03-14"):
        bad.append(f"dob sent as {body[f_answer]!r}")
    shape = exp.get("verify") or (lambda d: d)
    trans = wf["nodes"]["identity"]["transitions"]
    for data, say, target in (
        (
            {"ok": True, "verified": True, "wrong_answers_left": 3, "all_verified": True},
            "Both answers",
            "account",
        ),
        (
            {"ok": True, "verified": False, "wrong_answers_left": 0, "all_verified": False},
            "has failed",
            "fin_failed",
        ),
        (
            {"ok": True, "verified": False, "wrong_answers_left": 2, "all_verified": False},
            "not correct",
            None,
        ),
    ):
        res = {"data": shape(data), "status_code": 200}
        vv = dict(v, id_ok_fields="date_of_birth,phone_last4")
        exec(t["post_process_code"], {"vars": vv, "result": res})
        if say not in str(res["data"].get("what_to_do_next")):
            bad.append(f"{data}: hook said {res['data'].get('what_to_do_next')!r}")
        table = {"verify_identity_response.data." + k: x for k, x in flat(res["data"]).items()}
        table.update({k: str(x) for k, x in vv.items()})
        fired = next((x["target_node"] for x in trans if fires(x["condition"], table)), None)
        if target and fired != target:
            bad.append(f"{data}: identity went to {fired}, want {target}")
    return bad


def check_facts(r, exp) -> list[str]:
    wf = r["wf"]
    prod = (exp.get("product") or (lambda p: p))(dict(P))
    data = (exp.get("account") or (lambda d: d))({"ok": True, "products": [prod]})
    path = wf["nodes"]["account"]["response_mapping"][0]["path"].removeprefix("data.")
    got: Any = data
    for part in path.split("."):
        got = got.get(part) if isinstance(got, dict) else None
    v = {"products": got, "now": "2026-10-14T11:00"}
    exec(code_of(wf, "facts"), {"vars": v})
    want = {"product_count": "1", "p1_id": "p1", "p1_past_due": "420.00", "p1_days_late": "12"}
    return [f"facts {k}={v.get(k)!r}, want {x!r}" for k, x in want.items() if v.get(k) != x]


NAMES = list(M)


@pytest.mark.parametrize("name", NAMES)
def test_adapted_spec_compiles_clean(name):
    r = run(name, True)
    exp = M[name][1]
    assert r["left"] == [], r["left"]
    assert r["notes"] == [] and r["fixes"] == [], (r["notes"], r["fixes"])
    assert r["errors"] == []
    assert oracle(r) == []
    assert check_writes(r, exp) == []
    assert check_identity(r, exp) == []
    assert check_facts(r, exp) == []


@pytest.mark.parametrize("name", NAMES)
def test_stale_spec_is_never_silent(name):
    r = run(name, False)
    exp = M[name][1]
    assert r["errors"] == []
    if exp["stale"] == "reported":
        assert r["first"] and r["left"], "a stale spec the workflow cannot use must be reported"
        return
    if exp["stale"] == "fixed":
        assert r["first"] or r["fixes"], "the stale spec was wrong but nothing said so"
        assert r["notes"] or r["fixes"]
    else:
        assert r["first"] == [] and r["notes"] == [], (r["first"], r["notes"])
    assert r["left"] == [], r["left"]
    assert oracle(r) == []
    assert check_writes(r, exp) == []
    assert check_identity(r, exp) == []
    assert check_facts(r, exp) == []


# ---------------------------------------------------------------- single findings
def test_extract_auto_corrects_after_the_repair_and_traces_it(monkeypatch, tmp_path):
    import fb.spec_extract as se
    from fb.trace import Trace

    oa, spec = copy.deepcopy(OA), copy.deepcopy(SPEC)
    M["20 past_due -> amount_overdue, days_late -> days_overdue"][0](oa, spec, False)
    # the model repeats the stale names in its repair too
    monkeypatch.setattr(se, "chat", lambda *a, **k: json.dumps(spec))
    log = tmp_path / "t.log"
    out = se.extract(BRIEF, oa, parse_openapi(oa), Trace(log, echo=False))
    assert out["product_fields"]["past_due"] == "amount_overdue"
    assert out["product_fields"]["days_late"] == "days_overdue"
    assert "spec auto-correct: product_fields.past_due" in log.read_text()
    assert any("auto-correct" in n for n in out["notes"])
    assert not [p for p in check(out, parse_openapi(oa), BRIEF, oa) if "product_fields" in p]


def test_operations_named_otherwise_keep_their_urls_under_the_kit_names():
    r = run("34 operations named otherwise: camelCase ids, a bank_ prefix, Turkish paths", False)
    tools = r["tools"]
    assert tools["close_call"].url.endswith("/gorusme/kapat")
    assert tools["add_note"].api_name == "bank_add_note"
    assert tools["verify_identity"].api_name == "verifyIdentity"
    urls = {t["function"]["name"]: send(t, VALUES)["url"] for _, _, t in calls(r["wf"])}
    assert urls["close_call"].endswith("/gorusme/kapat")
    assert urls["add_note"].endswith("/bank_add_note")
    r = run("35 response amounts in minor units", False)
    assert r["tools"]["get_account"].response_scale == {"products.past_due": 0.01}


def test_kit_openapi_needs_no_correction():
    spec = copy.deepcopy(SPEC)
    assert autocorrect(spec, parse_openapi(OA), OA) == []
    assert spec == SPEC
    build(to_generator(SPEC), parse_openapi(OA))
    assert generate.FIXES == []


def test_allof_request_body_keeps_its_fields():
    r = run("23 allOf request bodies with a $ref part", False)
    t = r["tools"]["create_promise"]
    assert [f.name for f in t.fields] == ["customer_id", "product_id", "amount", "date"]
    assert all(f.required for f in t.fields)


def test_oneof_body_requires_only_what_every_variant_requires():
    t = run("24 oneOf request body, field schemas behind allOf/$ref", False)["tools"]["close_call"]
    assert t.field("outcome").enum and t.field("outcome").required
    assert not t.field("reason").required


def test_response_found_behind_ref_charset_and_2xx():
    for name in (
        "25 charset content types and responses behind $ref",
        "26 success as 201 / 202 / default",
    ):
        tools = run(name, False)["tools"]
        assert "promise_id" in tools["create_promise"].response_props
        assert "all_verified" in tools["verify_identity"].response_props


def test_query_parameter_is_moved_into_the_url_and_warned():
    r = run("27 get_account as GET with a query parameter", False)
    t = r["tools"]["get_account"]
    assert t.method == "GET" and t.field("customer_id").location == "query"
    assert any("URL parameters" in p and not hard for p, hard in bindings(r["spec"], r["tools"]))


def test_required_field_filled_from_extras_or_call_variable():
    r = run("28 new required enum, no default (create_promise.channel)", True)
    leaf = r["wf"]["nodes"]["record"]["pre_actions"][0]["function"]["parameters"]["properties"]
    assert leaf["body"]["properties"]["channel"]["default"] == "voice"
    stale = run("28 new required enum, no default (create_promise.channel)", False)
    assert any("extras" in p and "voice" in p for p in stale["left"])
    r = run("29 new required field named like a call variable (close_call.lastName)", False)
    close = r["wf"]["tools"][0]["function"]["parameters"]["properties"]["body"]["properties"]
    assert close["lastName"]["default"] == "{{last_name}}"


def test_wire_formats():
    from fb.openapi_tools import ToolField

    f = ToolField
    assert wire_format(f("d", description="the date as YYYY-MM-DD"), "date") == ""
    assert wire_format(f("d", format="date"), "date") == ""
    assert wire_format(f("d", description="DD/MM/YYYY"), "date") == "DD/MM/YYYY"
    assert wire_format(f("d", pattern=r"^\d{2}/\d{2}/\d{4}$"), "date", "MDY") == "MM/DD/YYYY"
    assert wire_format(f("d", pattern=r"^\d{2}\.\d{2}\.\d{4}$"), "date") == "DD.MM.YYYY"
    assert wire_format(f("d", format="date-time"), "date") == "datetime"
    assert wire_format(f("a", type="number"), "amount") == ""
    assert wire_format(f("a", type="integer", description="in cents"), "amount") == "cents"
    assert wire_format(f("t", description="HH:MM"), "time") == ""
    assert wire_format(f("t", description="HH:MM:SS"), "time") == "hms"


def test_match_enum():
    assert match_enum("date_of_birth", ["dob", "phone_tail"]) == "dob"
    assert match_enum("phone_last4", ["dob", "phone_tail"]) == "phone_tail"
    assert match_enum("not_verified", ["NOT_VERIFIED", "promise"]) == "NOT_VERIFIED"
    assert match_enum("not_verified", ["identity_failed", "promise"]) == ""
    assert match_enum("general", ["restructuring", "human", "support"]) == ""
    # never across a negation
    assert match_enum("not_verified", ["not_reached", "verified"]) == ""
    assert match_enum("no_promise", ["promise", "declined_to_pay"]) == ""
