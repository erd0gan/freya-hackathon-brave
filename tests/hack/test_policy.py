"""Every policy knob of the negotiation engine changes behaviour when the spec changes it.
The public values live in test_engine.CFG; here each knob is flipped to a value a hidden brief
could state, and the ledger (not the public scorer) is checked."""

import datetime

from fb.harness.engine_sim import simulate
from fb.nodecode.embed import namespace
from test_engine import CASES, CFG, CFG_A, SCENARIOS

NS = namespace()
Y = {"kind": "yes"}
N = {"kind": "no"}
BYE = {"kind": "unclear"}


def neg(base, **kw):
    return {**base, "neg": {**base["neg"], **kw}}


def test_public_config_sets_every_knob():
    assert NS["missing_policy"](CFG["neg"]) == []
    assert NS["missing_policy"](CFG_A["neg"]) == []
    assert NS["missing_policy"]({}) == NS["POLICY_KEYS"]


def test_human_transfer_needs_two_requests_by_default():
    r = simulate(CASES["PUB-01"], [{"kind": "human"}], CFG_A)
    assert r.ledger["transfers"] == []
    assert "help you with this myself" in r.says[-1]
    r = simulate(CASES["PUB-01"], [{"kind": "human"}, {"kind": "human"}], CFG_A)
    assert r.ledger["transfers"] == [{"queue": "general"}]
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("transferred", "human")


def test_human_transfer_at_first_request():
    r = simulate(CASES["PUB-01"], [{"kind": "human"}], neg(CFG_A, human_requests_to_transfer=1))
    assert r.ledger["transfers"] == [{"queue": "general"}]
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("transferred", "human")


def test_stop_limit():
    r = simulate(CASES["PUB-01"], [{"kind": "stop"}], CFG_A)
    assert r.ledger["outcome"] is None or r.ledger["outcome"] == ""  # still talking
    r = simulate(CASES["PUB-01"], [{"kind": "stop"}], neg(CFG_A, stop_requests_to_close=1))
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("no_promise", "refused")


def test_promise_attempts():
    # PUB-10: the first create_promise call fails.
    ok = simulate(CASES["PUB-10"], SCENARIOS["PUB-10"], CFG_A)
    assert ok.ledger["outcome"] == "promise"
    one = simulate(CASES["PUB-10"], SCENARIOS["PUB-10"], neg(CFG_A, promise_attempts=1))
    assert one.ledger["promises"] == []
    assert (one.ledger["outcome"], one.ledger["reason"]) == ("no_promise", "service_error")


def test_record_failure_transfers():
    cfg = neg(CFG_A, promise_attempts=1, record_failure="transfer", record_failure_queue="human")
    r = simulate(CASES["PUB-10"], SCENARIOS["PUB-10"], cfg)
    assert r.ledger["transfers"] == [{"queue": "general"}]
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("transferred", "human")


def test_hardship_offer_by_default():
    r = simulate(CASES["PUB-05"], [{"kind": "hardship"}], CFG_A)
    assert r.ledger["transfers"] == []
    assert "support team" in r.says[-1]


def test_hardship_transfers_at_once():
    r = simulate(CASES["PUB-05"], [{"kind": "hardship"}], neg(CFG_A, hardship="transfer"))
    assert r.ledger["transfers"] == [{"queue": "support"}]
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("transferred", "support")


def test_hardship_closes_at_once():
    cfg = neg(CFG_A, hardship="close", lines={**CFG_A["neg"]["lines"], "sympathy": "So sorry."})
    r = simulate(CASES["PUB-05"], [{"kind": "hardship"}], cfg)
    assert r.ledger["transfers"] == []
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("no_promise", "hardship")
    assert r.says[-1].startswith("So sorry.")


def _finish_with(statuses, **kw):
    pf = {"product_count": str(len(statuses))}
    for i in range(len(statuses)):
        pf.update({f"p{i + 1}_id": f"p{i + 1}", f"p{i + 1}_past_due": "100.00"})
    n = {**CFG["neg"], **kw}
    st = NS["new_state"](pf, n, "2026-10-14T11:00")
    for q, s in zip(st["p"], statuses, strict=True):
        q["status"] = s
    NS["_finish"](st, n)
    return st


def test_no_promise_reason_first_product():
    st = _finish_with(["paid", "refused", "dispute"])
    assert (st["final_outcome"], st["final_reason"]) == ("no_promise", "refused")


def test_no_promise_reason_priority():
    st = _finish_with(["paid", "refused", "dispute"], no_promise_reason="priority")
    assert (st["final_outcome"], st["final_reason"]) == ("no_promise", "dispute")
    st = _finish_with(
        ["refused", "hardship"], no_promise_reason="priority", reason_priority=["hardship"]
    )
    assert st["final_reason"] == "hardship"


def test_notes_on_transfer():
    r = simulate(CASES["PUB-12"], [{"kind": "restructure"}], CFG)
    assert r.ledger["notes"] == []
    r = simulate(CASES["PUB-12"], [{"kind": "restructure"}], neg(CFG, notes_on_transfer=True))
    assert len(r.ledger["notes"]) == 3
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("transferred", "restructuring")


def test_vague_date_asks():
    first = [{"kind": "vague", "date": "the week after next"}]
    r = simulate(CASES["PUB-07"], first, CFG_A)
    assert "exact day" in r.says[-1]
    r = simulate(CASES["PUB-07"], first, neg(CFG_A, vague_date_asks=0))
    assert "latest date I can record" in r.says[-1]


def test_floor_offers():
    r = simulate(CASES["PUB-06"], [{"kind": "too_much"}], CFG_A)
    assert "minimum" in r.says[-1]
    r = simulate(CASES["PUB-06"], [{"kind": "too_much"}], neg(CFG_A, floor_offers=0))
    assert "minimum" not in r.says[-1]


TODAY = datetime.date(2026, 10, 14)  # a Wednesday


def test_attempt1_rule():
    assert NS["attempt1_date"](TODAY, CFG).isoformat() == "2026-10-16"
    cfg = {**CFG, "attempt1": {"rule": "nth_business_day", "n": 3}}
    assert NS["attempt1_date"](TODAY, cfg).isoformat() == "2026-10-19"
    assert NS["calendar_facts"]("2026-10-14T11:00", cfg)["attempt1_date"] == "2026-10-19"


def test_deadline_unit():
    band = {"deadline_business_days": 7, "deadline_unit": "business"}
    assert NS["deadline_for"](TODAY, band, CFG).isoformat() == "2026-10-23"
    # 10 calendar days -> Saturday 24 October -> back to Friday 23 October.
    band = {"deadline_days": 10, "deadline_unit": "calendar"}
    assert NS["deadline_for"](TODAY, band, CFG).isoformat() == "2026-10-23"
    band = {"deadline_days": 7, "deadline_unit": "calendar"}
    assert NS["deadline_for"](TODAY, band, CFG).isoformat() == "2026-10-21"
