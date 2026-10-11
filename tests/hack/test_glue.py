"""What the engine reads from the parser and spec fixes of 11 Oct."""

import datetime

from fb.harness.engine_sim import simulate
from fb.nodecode.embed import namespace
from test_engine import CASES, CFG, CFG_A

NS = namespace()
TODAY = datetime.date(2026, 10, 14)
Y = {"kind": "yes"}
BYE = {"kind": "unclear"}


def pf(attempts="1"):
    return {
        "product_count": 1,
        "p1_id": "p1",
        "p1_past_due": 420,
        "p1_floor": 126,
        "p1_deadline": "2026-10-30",
        "p1_attempts": attempts,
    }


def test_all_paid_closes_with_the_tables_paid_reason():
    neg = {**CFG["neg"], "close_reasons": {"paid": "already_paid"}}
    st = NS["new_state"](pf(), neg, "2026-10-14T11:00")
    st["p"][0]["status"] = "paid"
    NS["_finish"](st, neg)
    assert st["final_reason"] == "already_paid"
    st = NS["new_state"](pf(), CFG["neg"], "2026-10-14T11:00")
    st["p"][0]["status"] = "paid"
    NS["_finish"](st, CFG["neg"])
    assert st["final_reason"] == ""  # Brief A: paid has no reason


def test_a_band_with_no_persuasion_keeps_zero():
    st = NS["new_state"](pf(attempts="0"), CFG["neg"], "2026-10-14T11:00")
    assert st["p"][0]["attempts"] == 0


def test_price_style_amount_read_with_the_products_range():
    said = {"kind": "offer", "amount": "three fifty", "date": "Friday"}
    r = simulate(CASES["PUB-01"], [said, Y, BYE], CFG_A)
    assert [(p["amount"], p["date"]) for p in r.ledger["promises"]] == [(350.0, "2026-10-16")]


def test_unreadable_amount_is_not_the_models_guess():
    v = {"reply_kind": "offer", "said_amount_text": "200 or maybe 300", "said_amount": "300"}
    assert NS["read_reply"](v, TODAY)["amount"] is None
