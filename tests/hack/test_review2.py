"""Second review (9970faa): nine scenarios reproduced locally, one test each."""

import copy
import json

from fb.generate import build
from fb.harness.engine_sim import simulate
from fb.nodecode import bodies
from fb.nodecode.embed import namespace, snippet
from fb.openapi_tools import parse_openapi
from fb.spec_extract import bindings
from spec_fixtures import SPEC_A, SPEC_B
from test_engine import CASES, CFG, CFG_A

from conftest import KIT

NS = namespace()
Y = {"kind": "yes"}
N = {"kind": "no"}
BYE = {"kind": "unclear"}
OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text())


def promises(r):
    return [(p["product_id"], p["amount"], p["date"]) for p in r.ledger.get("promises") or []]


def run(body, v, cfg=CFG, on_error=""):
    exec(compile(snippet(body, cfg, on_error=on_error), "n", "exec"), {"vars": v})
    return v


# 1. "Yes, next Friday" to "Friday 16 October" is the Friday after (23 October)
def test_yes_next_friday_keeps_the_new_day():
    said = {"kind": "yes", "date": "next Friday", "text": "Yes, next Friday."}
    r = simulate(CASES["PUB-01"], [N, said, Y, BYE], CFG_A)
    assert promises(r) == [("p1", 420.0, "2026-10-23")], r.says


# 2. "Yes, the credit card only." agrees the card; the loan is still talked about
def test_yes_for_one_product_only():
    said = {"kind": "yes", "product": "p1", "text": "Yes, the credit card only."}
    r = simulate(CASES["PUB-13"], [said, N, N, Y, BYE], CFG)
    assert promises(r) == [("p1", 300.0, "2026-10-14")], r.says


def test_yes_naming_both_products_is_for_both():
    said = {"kind": "yes", "product": "p1", "text": "Yes, the credit card and the loan."}
    r = simulate(CASES["PUB-13"], [said, Y, BYE], CFG)
    assert [p[0] for p in promises(r)] == ["p1", "p2"], r.says


# 3. a note that fails twice is kept apart, not dropped as if it were written
def test_failed_note_is_kept_in_failed_notes():
    v = {"neg_state": json.dumps({"notes": [["p1", "refused"]]}), "note_ok": ""}
    NS["pop_note"](v, True)
    NS["pop_note"](v, True)
    assert v["note_count"] == "0"
    assert json.loads(v["neg_state"])["failed_notes"] == [["p1", "refused"]]
    assert v["failed_notes"] == "p1:refused"


# 4. a failed transfer after a recorded promise closes as a promise
def test_failed_transfer_keeps_a_recorded_promise():
    st = {"recorded": ["p1"], "p": [{"id": "p1", "status": "recorded"}]}
    v = {"final_outcome": "transferred", "transfer_ok": "", "neg_state": json.dumps(st)}
    run(bodies.AFTER_TRANSFER, v)
    run(bodies.AFTER_TRANSFER, v)
    assert (v["final_outcome"], v["final_reason"]) == ("promise", "")


# 5. "I cannot pay today, but I can pay both on the thirtieth": both, each on its own rules
def test_no_to_today_does_not_cancel_both():
    said = "I cannot pay today, but I can pay both on the thirtieth of October."
    first = {"kind": "offer", "date": "the thirtieth of October", "all": True, "text": said}
    r = simulate(CASES["PUB-13"], [first, Y, Y, BYE], CFG)
    got = dict((p[0], p[2]) for p in promises(r))
    assert got == {"p1": "2026-10-30", "p2": "2026-10-23"}, r.says


# 6. clear refusals at the security questions count without a full stop
def test_clear_refusals_without_punctuation_count_at_identity():
    for text in ("I do not want to talk", "Please stop calling me", "I won't give you that"):
        v = {"id1": "declines", "system.conversation": [{"role": "user", "content": text}]}
        run(bodies.decline_check("id1"), v)
        assert v["decline_real"] == "yes", text


def openapi_with(change):
    o = copy.deepcopy(OPENAPI)
    change(o)
    return o


def schema(o, path):
    return o["paths"][path]["post"]


# 7. create_promise answering only promise_id: success is the id being there
def test_promise_without_ok_flag_reads_success_from_the_id():
    def f(o):
        r = schema(o, "/create_promise")["responses"]["200"]["content"]["application/json"]
        r["schema"] = {"type": "object", "properties": {"promise_id": {"type": "string"}}}

    tools = parse_openapi(openapi_with(f))
    wf, _ = build(copy.deepcopy(SPEC_A), tools)
    assert wf["nodes"]["record"]["response_mapping"][0]["path"] == "data.promise_id"
    assert NS["call_ok"]("pr_123", "present") and not NS["call_ok"]("", "present")
    st = {"rec": [0], "rec_try": 0, "p": [{"id": "p1", "status": "agreed", "name": "card"}]}
    st["p"][0].update(amount=420.0, date="2026-10-16")
    v = {"neg_state": json.dumps(st), "rec_ok": "pr_123", "now": "2026-10-14T11:00"}
    run(bodies.AFTER_RECORD, v, {**CFG, "rec_mode": "present"})
    assert json.loads(v["neg_state"])["p"][0]["status"] == "recorded"


# 8. close_call's reason comes from neg.close_reasons when add_note uses another value
def test_close_reason_separate_from_note_reason():
    neg = {**CFG["neg"], "close_reasons": {"refused": "declined_payment"}}
    st = NS["new_state"](
        {"product_count": 1, "p1_id": "p1", "p1_past_due": 100, "p1_deadline": "2026-10-30"},
        neg,
        "2026-10-14T11:00",
    )
    st["p"][0]["status"] = "refused"
    NS["_finish"](st, neg)
    assert st["final_reason"] == "declined_payment"
    assert st["notes"] == ([["p1", "refused"]] if neg.get("notes") else [])


# 9. a required field no role fills gets the API's own single value; else a warning
def test_required_field_with_one_value_is_filled():
    def f(o):
        b = schema(o, "/create_promise")["requestBody"]["content"]["application/json"]["schema"]
        b["properties"]["currency"] = {"type": "string", "enum": ["GBP"]}
        b["required"] = [*b["required"], "currency"]

    tools = parse_openapi(openapi_with(f))
    wf, _ = build(copy.deepcopy(SPEC_A), tools)
    body = wf["nodes"]["record"]["pre_actions"][0]["function"]["parameters"]["properties"]["body"]
    assert body["properties"]["currency"]["default"] == "GBP"


def test_required_field_nobody_fills_is_reported():
    def f(o):
        b = schema(o, "/create_promise")["requestBody"]["content"]["application/json"]["schema"]
        b["properties"]["channel"] = {"type": "string"}
        b["required"] = [*b["required"], "channel"]

    out = bindings(copy.deepcopy(SPEC_A), parse_openapi(openapi_with(f)))
    assert any("channel" in msg for msg, _hard in out)


def test_unchanged_kit_api_still_uses_the_ok_flag():
    for spec in (SPEC_A, SPEC_B):
        wf, _ = build(copy.deepcopy(spec), parse_openapi(OPENAPI))
        assert wf["nodes"]["record"]["response_mapping"][0]["path"] == "data.ok"
