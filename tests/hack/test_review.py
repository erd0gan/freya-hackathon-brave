"""Review of 11 Oct (9ac6f6d): wrong records and wrong outcomes reproduced on the engine.
Each test is the reviewer's scenario; the ledger is what the bank would hold."""

import datetime

from fb.harness.engine_sim import simulate
from fb.nodecode import bodies
from fb.nodecode.embed import namespace, snippet
from test_engine import CASES, CFG, CFG_A

NS = namespace()
TODAY = datetime.date(2026, 10, 14)
Y = {"kind": "yes"}
N = {"kind": "no"}
BYE = {"kind": "unclear"}


def promises(r):
    return [(p["product_id"], p["amount"], p["date"]) for p in r.ledger.get("promises") or []]


# 1. A counter-offer made during the read-back: the next yes records the new values, not the
# read-back the customer just changed
def test_counter_offer_at_confirm_records_the_new_values():
    seq = [Y, {"kind": "offer", "date": "Friday", "amount": "50"}, Y, Y, BYE]
    r = simulate(CASES["PUB-01"], seq, CFG_A)
    assert promises(r) == [("p1", 126.0, "2026-10-16")], r.says


def test_weekend_day_at_confirm_records_the_next_business_day():
    seq = [Y, {"kind": "offer", "date": "Saturday"}, Y, Y, BYE]
    r = simulate(CASES["PUB-01"], seq, CFG_A)
    assert promises(r) == [("p1", 420.0, "2026-10-19")], r.says


# 2. The floor offered after an amount below it keeps the date rules (deadline, business day)
def test_floor_offer_after_deadline_uses_the_deadline():
    seq = [{"kind": "offer", "date": "1 December", "amount": "50"}, Y, Y, BYE]
    r = simulate(CASES["PUB-01"], seq, CFG_A)
    (_pid, amount, date) = promises(r)[0]
    assert (amount, date) == (126.0, "2026-11-02"), r.says
    assert "latest date" in r.says[1]


def test_nothing_outside_the_rules_is_ever_recorded():
    st = NS["new_state"](
        {
            "product_count": 1,
            "p1_id": "p1",
            "p1_name": "card",
            "p1_past_due": 420,
            "p1_floor": 126,
            "p1_deadline": "2026-10-30",
            "p1_days_late": 6,
            "p1_attempts": 1,
        },
        {},
        "2026-10-14T11:00",
    )
    cal = NS["calendar_facts"]("2026-10-14T11:00", CFG)
    p = st["p"][0]
    p.update(status="agreed", amount=50.0, date="2026-12-01")
    st["phase"] = "confirm"
    NS["step"](st, {"kind": "yes"}, CFG["neg"], cal, "2026-10-14T11:00", CFG)
    assert st["action"] != "record", st["say"]
    assert "126.00" in st["say"]


# 3. An amount and its day in different turns: the amount is kept
def test_amount_then_day_keeps_the_amount():
    seq = [{"kind": "offer", "amount": "200"}, {"kind": "offer", "date": "Friday"}, Y, BYE]
    r = simulate(CASES["PUB-01"], seq, CFG_A)
    assert promises(r) == [("p1", 200.0, "2026-10-16")], r.says


# 4. A recorded promise stays a promise whatever is said after it
def test_paid_after_a_recorded_promise_keeps_the_promise():
    r = simulate(CASES["PUB-01"], [Y, Y, {"kind": "paid"}], CFG_A)
    assert (r.ledger["outcome"], r.ledger.get("reason")) == ("promise", None)


def test_dispute_after_a_recorded_promise_keeps_the_promise():
    r = simulate(CASES["PUB-01"], [Y, Y, {"kind": "dispute"}], CFG_A)
    assert r.ledger["outcome"] == "promise"


# 5. An offer for another product than the current one: the next yes accepts that offer
def test_yes_goes_to_the_product_of_the_last_offer():
    seq = [N, {"kind": "too_much", "product": "p2"}, Y, Y, Y, BYE]
    r = simulate(CASES["PUB-13"], seq, CFG)
    loan = [p for p in promises(r) if p[0] == "p2"]
    assert loan == [("p2", 300.0, "2026-10-14")], r.says


# 8. Several agreed products, the customer refuses one of them at the read-back
def test_refusing_one_product_at_the_readback():
    said = "I will not pay the personal loan."
    seq = [Y, {"kind": "no", "product": "p2", "text": said}, N, N, Y, BYE]
    r = simulate(CASES["PUB-13"], seq, CFG)
    assert [p[0] for p in promises(r)] == ["p1"], r.says
    assert r.says.count("Which promise would you like to change?") == 0


def test_no_without_a_product_at_the_readback_does_not_loop():
    seq = [Y, N, N, N, N, BYE]
    r = simulate(CASES["PUB-13"], seq, CFG)
    assert r.says.count("Which promise would you like to change?") <= 1, r.says
    assert promises(r) == []


# 9. "both" inside a refusal does not mean all products
def test_both_in_a_refusal_is_not_all_products():
    said = "I cannot pay both. I will pay the credit card Friday but not the loan."
    seq = [{"kind": "offer", "date": "Friday", "product": "p1", "text": said}, N, N, Y, BYE]
    r = simulate(CASES["PUB-13"], seq, CFG)
    assert [p[0] for p in promises(r)] == ["p1"], r.says


# 10. One day for all products: every product is checked against it before anything is asked
def test_one_day_for_all_products_checks_each_product():
    said = "I will pay both on the 26th of October."
    seq = [{"kind": "offer", "date": "26 October", "all": True, "text": said}, Y, Y, Y, BYE]
    r = simulate(CASES["PUB-13"], seq, CFG)
    got = dict((p[0], p[2]) for p in promises(r))
    # the card's next business day after the holiday; the loan's own latest date (23 Oct)
    assert got == {"p1": "2026-10-27", "p2": "2026-10-23"}, r.says
    assert not any("16 October" in s for s in r.says), r.says


# 13. A cut-off "no" does not use an attempt; a whole "stop" without a full stop still counts
def read(kind, text, **extra):
    v = {"reply_kind": kind, "system.conversation": [{"role": "user", "content": text}]}
    v.update(extra)
    return NS["read_reply"](v, TODAY)


def test_cut_no_is_not_a_refusal_yet():
    assert read("no", "I cannot pay because my")["kind"] == "unclear"
    assert read("too_much", "That is way too")["kind"] == "unclear"
    assert read("no", "No, I cannot pay that.")["kind"] == "no"


def test_clear_stop_without_punctuation_counts():
    assert read("stop", "I do not want to talk")["kind"] == "stop"
    assert read("stop", "Please stop calling me")["kind"] == "stop"


def test_cut_no_after_two_unusable_turns_is_taken_as_said():
    cut = {"kind": "no", "text": "No I really cannot because my"}
    r = simulate(CASES["PUB-01"], [cut, cut, cut], CFG_A)
    assert "Friday" in r.says[-1] or "16 October" in r.says[-1], r.says


# 6. A code error never leaves the last action standing (a second create_promise)
def test_code_error_does_not_keep_the_record_action():
    v = {"neg_state": "null", "neg_action": "record", "rec_ok": "true", "now": "2026-10-14T11:00"}
    exec(
        compile(snippet(bodies.AFTER_RECORD, CFG, on_error=bodies.NEG_ERROR), "n", "exec"),
        {"vars": v},
    )
    assert v.get("code_error")
    assert v["neg_action"] != "record"


def test_repeated_code_error_in_decide_ends_the_call():
    v = {"neg_state": "{bad", "neg_action": "say", "now": "2026-10-14T11:00"}
    for _ in range(3):
        exec(
            compile(snippet(bodies.DECIDE, CFG, on_error=bodies.NEG_ERROR), "n", "exec"),
            {"vars": v},
        )
    assert v["neg_action"] == "finish"


# 7. No products, or a product without an amount or a deadline: no negotiation on made-up values
def test_no_products_closes_with_a_service_error():
    v = {"products": [], "now": "2026-10-14T11:00"}
    exec(compile(snippet(bodies.FACTS, CFG), "n", "exec"), {"vars": v})
    assert v["neg_action"] == "finish", v.get("code_error")
    assert v["final_reason"] == "service_error"


def test_product_without_past_due_is_not_offered_as_zero():
    p = dict(CASES["PUB-01"]["products"][0], product_id="p1")
    p.pop("past_due")
    v = {"products": [p], "now": "2026-10-14T11:00"}
    exec(compile(snippet(bodies.FACTS, CFG), "n", "exec"), {"vars": v})
    assert v["neg_action"] == "finish", v.get("neg_say")
    assert "0.00" not in str(v.get("neg_say"))


# 15. Questions after the support offer are not a refusal of it
def test_questions_after_support_offer_do_not_close_as_hardship():
    q = {"kind": "question"}
    r = simulate(CASES["PUB-05"], [{"kind": "hardship"}, q, q, q, Y], CFG_A)
    assert r.ledger["transfers"] == [{"queue": "support"}], r.says


# 12. A failed transfer is tried once more, then closed as a service error (not `transferred`)
def run(body, v, cfg=CFG, lib=None):
    src = snippet(body, cfg) if lib is None else snippet(body, cfg, lib)
    exec(compile(src, "n", "exec"), {"vars": v})
    return v


def test_failed_transfer_is_not_closed_as_transferred():
    v = {"final_outcome": "transferred", "transfer_ok": ""}
    run(bodies.AFTER_TRANSFER, v)
    assert v["transfer_next"] == "retry"
    run(bodies.AFTER_TRANSFER, v)
    assert v["transfer_next"] == "failed"
    assert (v["final_outcome"], v["final_reason"]) == ("no_promise", "service_error")


def test_transfer_that_works_keeps_its_outcome():
    v = {"final_outcome": "transferred", "transfer_ok": "true"}
    run(bodies.AFTER_TRANSFER, v)
    assert v["transfer_next"] == "done" and v["final_outcome"] == "transferred"


# 12. A failed add_note is tried once more before the next note
def test_failed_note_is_tried_once_more():
    import json as _json

    st = {"notes": [["p1", "refused"], ["p2", "dispute"]]}
    v = {"neg_state": _json.dumps(st), "note_ok": ""}
    NS["pop_note"](v, True)
    assert v["note_count"] == "2"  # same note again
    NS["pop_note"](v, True)
    assert v["note_count"] == "1" and v["note_product_id"] == "p2"
    v["note_ok"] = "true"
    NS["pop_note"](v, True)
    assert v["note_count"] == "0"


def test_workflow_routes_failed_transfers_and_checks_notes():
    import copy
    import json as _json

    from fb.generate import build
    from fb.openapi_tools import parse_openapi
    from fb.validate import validate
    from spec_fixtures import SPEC_A, SPEC_B

    from conftest import KIT

    tools = parse_openapi(_json.loads((KIT / "tools" / "openapi.json").read_text()))
    for spec in (SPEC_A, SPEC_B):
        wf, policy = build(copy.deepcopy(spec), tools)
        assert validate(wf, tools, policy).ok
        n = wf["nodes"]
        assert n["transfer"]["transitions"][0]["target_node"] == "after_transfer"
        assert n["transfer"]["response_mapping"][0]["variable"] == "transfer_ok"
        assert n["facts"]["transitions"][0]["target_node"] == "wrap"
        if "note" in n:
            assert n["note"]["response_mapping"][0]["variable"] == "note_ok"


# 15. Before verification the brief's count of requests for a person applies to an insist too
def test_pre_verification_insist_respects_the_count():
    import copy
    import json as _json

    from fb.generate import build
    from fb.openapi_tools import parse_openapi
    from spec_fixtures import SPEC_A

    from conftest import KIT

    tools = parse_openapi(_json.loads((KIT / "tools" / "openapi.json").read_text()))
    spec = copy.deepcopy(SPEC_A)
    spec["cfg"]["neg"]["human_requests_to_transfer"] = 3
    wf, _ = build(spec, tools)
    tr = wf["nodes"]["pre_human"]["transitions"]
    first = tr[0]
    assert first["target_node"] == "pre_transfer"
    # the insist after one more help offer, not the first one
    assert '"pre_human_count", "operator": "greater_than", "operand": 0}' in _json.dumps(first)
    assert tr[1]["target_node"] == "pre_human_more"


# research T1: a `declines` label is checked against the customer's own words
def decline(text):
    v = {"id1": "declines", "system.conversation": [{"role": "user", "content": text}]}
    run(bodies.decline_check("id1"), v)
    return v["decline_real"], v["id1"]


def test_cut_or_small_talk_is_not_a_refusal_of_the_security_question():
    assert decline("I've been so busy at work because my") == ("no", "unclear")
    assert decline("Work has been crazy today, the traffic was awful.") == ("no", "unclear")


def test_real_refusals_still_count():
    assert decline("I won't give you that.")[0] == "yes"
    assert decline("I don't want to talk about this.")[0] == "yes"
    assert decline("What is this about?")[0] == "yes"
    assert decline("Why do you need that?")[0] == "yes"


# a tool without a success flag gets no success check (every call would read as failed)
def test_tools_without_ok_flag_build_without_success_checks():
    import copy
    import json as _json

    from fb.generate import build
    from fb.openapi_tools import parse_openapi
    from fb.validate import validate
    from spec_fixtures import SPEC_B

    from conftest import KIT

    o = _json.loads((KIT / "tools" / "openapi.json").read_text())
    for path in ("/transfer_to_agent", "/add_note"):
        r = o["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]
        r["schema"] = {"type": "object", "properties": {"queued": {"type": "string"}}}
    tools = parse_openapi(o)
    wf, policy = build(copy.deepcopy(SPEC_B), tools)
    assert validate(wf, tools, policy).ok
    n = wf["nodes"]
    assert "after_transfer" not in n and n["transfer"]["transitions"][-1]["target_node"] == "wrap"
    assert n["note"]["response_mapping"] == []
    assert "'note_check': False" in str(n["after_note"]) or '"note_check": false' in str(
        n["after_note"]
    )


# a brief may order the products (most overdue first); "first_product" stays get_account order
def test_product_order_from_the_brief():
    pf = {"product_count": "2"}
    for i, (due, late) in enumerate(((100, 5), (900, 30))):
        k = f"p{i + 1}"
        pf.update({f"{k}_id": k, f"{k}_past_due": due, f"{k}_days_late": late})
        pf[f"{k}_deadline"] = "2026-10-30"
    st = NS["new_state"](pf, {**CFG["neg"], "product_order": "days_late_desc"}, "2026-10-14")
    assert [q["id"] for q in st["p"]] == ["p2", "p1"]
    st = NS["new_state"](pf, CFG["neg"], "2026-10-14")
    assert [q["id"] for q in st["p"]] == ["p1", "p2"]


# readable workflow: every node has a place in the editor; code is plain unless too big
def built_workflows():
    import copy
    import json as _json

    from fb.generate import build
    from fb.openapi_tools import parse_openapi
    from spec_fixtures import SPEC_A, SPEC_B

    from conftest import KIT

    tools = parse_openapi(_json.loads((KIT / "tools" / "openapi.json").read_text()))
    return [build(copy.deepcopy(s), tools)[0] for s in (SPEC_A, SPEC_B)]


def test_every_node_has_an_editor_position():
    for wf in built_workflows():
        pos = [(n["position"]["x"], n["position"]["y"]) for n in wf["nodes"].values()]
        assert len(pos) == len(wf["nodes"]) == len(set(pos))
        assert wf["nodes"][wf["initial_node"]]["position"]["x"] == 0.0


def test_code_nodes_fit_a_long_call_and_only_big_ones_are_packed():
    from fb.nodecode.embed import CODE_BUDGET

    for wf in built_workflows():
        for k, n in wf["nodes"].items():
            for a in n.get("pre_actions") or []:
                if a.get("function", {}).get("name") != "code_exec":
                    continue
                code = a["function"]["parameters"]["properties"]["code"]["default"]
                assert len(code.encode()) <= CODE_BUDGET, k
                if k != "decide":
                    assert "b64decode" not in code, k
