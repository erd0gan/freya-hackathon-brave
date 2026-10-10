import json

import pytest
from fb.harness.bank import Bank, envelope, flatten
from fb.harness.scoring import case_report, golden_calls, replay

from conftest import KIT

CASES = {c["id"]: c for c in json.loads((KIT / "public-test-set.json").read_text())}


@pytest.mark.parametrize("cid", sorted(CASES))
def test_golden_sequence_passes_kit_scorer(cid):
    r = replay(Bank(), CASES[cid])
    assert r["pass"], r["failed"]


def session(cid="PUB-01"):
    b = Bank()
    v = b.open_session(CASES[cid])
    return b, v["customer_id"]


def vi(b, c, field, answer):
    return b.call(c, "verify_identity", {"customer_id": c, "field": field, "answer": answer})[1]


def test_session_variables_match_case():
    b, c = session()
    v = b.sessions[c].variables
    assert v["customer_id"] == c and v["now"] == CASES["PUB-01"]["variables"]["now"]


def test_identity_flow():
    b, c = session()
    assert vi(b, c, "date_of_birth", "the fourteenth")["ok"] is False  # unreadable, not counted
    r = vi(b, c, "date_of_birth", "1988-14-03")
    assert r["ok"] is False  # not a calendar date either
    # The real mock reads a spoken date too (bank_parity, 10 Oct).
    r = vi(b, c, "date_of_birth", "14 March 1988")
    assert r["verified"] and not r["all_verified"] and r["wrong_answers_left"] == 3
    r = vi(b, c, "phone_last4", "4471")
    assert r["all_verified"]


def test_day_month_swap_hint_is_not_a_wrong_answer():
    b, c = session("PUB-11")  # date of birth 1987-06-03
    r = vi(b, c, "date_of_birth", "1987-03-06")
    assert r.get("hint") == "day_month_swapped" and r["wrong_answers_left"] == 3


def test_three_wrong_answers_lock():
    b, c = session()
    for left in (2, 1, 0):
        assert vi(b, c, "phone_last4", "0000")["wrong_answers_left"] == left
    r = vi(b, c, "phone_last4", "4471")
    assert r["ok"] is False and not r.get("verified")


def test_account_and_promise_refused_before_verification():
    b, c = session()
    assert b.call(c, "get_account", {"customer_id": c})[1]["error"] == "identity not verified"
    out = b.call(
        c,
        "create_promise",
        {"customer_id": c, "product_id": "p1", "amount": 1, "date": "2026-10-16"},
    )
    assert out[1]["error"] == "identity not verified"


def verified(cid="PUB-01"):
    b, c = session(cid)
    for f, v in CASES[cid]["identity"].items():
        vi(b, c, f, v)
    return b, c


def test_promise_rules():
    b, c = verified()
    pr = {"customer_id": c, "product_id": "p9", "amount": 420, "date": "2026-10-16"}
    assert b.call(c, "create_promise", pr)[1]["error"] == "unknown product_id"
    pr["product_id"] = "p1"
    assert b.call(c, "create_promise", pr)[1]["ok"]
    assert b.call(c, "create_promise", pr)[1]["ok"] is False  # one per product


def test_fault_injection_then_success():
    b, c = verified("PUB-10")
    pr = {"customer_id": c, "product_id": "p1", "amount": 365, "date": "2026-10-16"}
    st, out = b.call(c, "create_promise", pr)
    assert st == 500 and out["ok"] is False and not b.ledger(c)["promises"]
    assert b.call(c, "create_promise", pr)[1]["ok"]


def test_nothing_after_close():
    b, c = verified()
    b.call(c, "close_call", {"customer_id": c, "outcome": "no_promise", "reason": "refused"})
    _, out = b.call(c, "add_note", {"customer_id": c, "product_id": "p1", "reason": "refused"})
    assert out["ok"] is False
    assert b.ledger(c)["notes"] == []


def bad_run(cid, edit):
    b = Bank()
    v = b.open_session(CASES[cid])
    calls = golden_calls(CASES[cid], v["customer_id"])
    edit(calls)
    b2 = Bank()
    return replay(b2, CASES[cid], calls)


def test_scorer_catches_account_before_verify():
    r = bad_run("PUB-01", lambda calls: calls.insert(0, ("get_account", {})))
    assert any("before identity" in f for f in r["failed"])


def test_scorer_catches_identity_tools_when_not_reached():
    r = bad_run(
        "PUB-02",
        lambda calls: calls.insert(
            0, ("verify_identity", {"field": "date_of_birth", "answer": "1979-11-02"})
        ),
    )
    assert any("not on the line" in f for f in r["failed"])


def test_scorer_catches_missing_close_and_wrong_amount():
    r = bad_run("PUB-01", lambda calls: calls.pop())
    assert any("close_call" in f for f in r["failed"])
    r = bad_run("PUB-06", lambda calls: calls[3][1].__setitem__("amount", 2125.0))
    assert any(f.startswith("promises") for f in r["failed"])


def test_case_report_needs_every_run():
    rep = case_report({"A": [True, True, True], "B": [True, False, True]})
    assert rep["passed"] == 1 and rep["rows"][1]["passes"] == 2


def test_envelope_and_flatten():
    env = envelope(200, {"ok": True, "verified": False, "wrong_answers_left": 2})
    flat = flatten("verify_identity_response", env)
    assert flat["verify_identity_response.data.ok"] == "true"
    assert flat["verify_identity_response.data.verified"] == "false"
    assert flat["verify_identity_response.data.wrong_answers_left"] == "2"
    assert flat["verify_identity_response.status"] == "success"
