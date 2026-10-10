"""Counter-examples from docs/COMPETITION_RESEARCH_2026-10-10.md (F03-F07, F10), as regressions."""

import datetime

from fb.harness.engine_sim import simulate
from fb.nodecode import bodies
from fb.nodecode.embed import LIB_CALENDAR, namespace, snippet
from test_engine import CASES, CFG_A

NS = namespace()
TODAY = datetime.date(2026, 10, 14)
Y = {"kind": "yes"}
BYE = {"kind": "unclear"}


def run(body, v):
    exec(compile(snippet(body, CFG_A, LIB_CALENDAR), "code_node", "exec"), {"vars": v})
    assert not v.get("code_error"), v.get("code_error")


def cb_turns(*turns):
    v = {"now": "2026-10-14T11:00"}
    run(bodies.SETUP_TP, v)
    for day, time in turns:
        v["cb_day_text"], v["cb_time_text"] = day, time
        run(bodies.CB_CHECK, v)
    return v


# F03: a day and a time said in different turns belong together
def test_callback_day_then_time():
    v = cb_turns(("tomorrow", ""), ("", "three pm"))
    assert (v["cb_state"], v["cb_date"], v["cb_time"]) == ("ok", "2026-10-15", "15:00")


def test_callback_time_then_day():
    v = cb_turns(("", "around eleven"), ("tomorrow", ""))
    assert (v["cb_date"], v["cb_time"]) == ("2026-10-15", "11:00")


def test_callback_correction_replaces_only_its_part():
    v = cb_turns(("tomorrow", "at nine"), ("", "eleven"))  # 09:00 is outside, then 11:00
    assert v["cb_state"] == "partial" or (v["cb_date"], v["cb_time"]) == ("2026-10-15", "11:00")


def test_callback_same_turn_still_works():
    v = cb_turns(("tomorrow morning", "around eleven"))
    assert (v["cb_state"], v["cb_date"], v["cb_time"]) == ("ok", "2026-10-15", "11:00")


# F04: a question after the support offer is not a refusal
def test_question_after_support_offer_keeps_offer_open():
    r = simulate(CASES["PUB-05"], [{"kind": "hardship"}, {"kind": "question"}, Y], CFG_A)
    assert r.ledger["transfers"] == [{"queue": "support"}]
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("transferred", "support")


def test_no_after_support_offer_closes_as_hardship():
    r = simulate(CASES["PUB-05"], [{"kind": "hardship"}, {"kind": "no"}], CFG_A)
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("no_promise", "hardship")


# F05: hardship with a date the customer names: the promise is still recorded, no persuasion
def test_hardship_with_own_date_records_promise():
    seq = [{"kind": "hardship", "date": "this Friday"}, Y, BYE]
    r = simulate(CASES["PUB-05"], seq, CFG_A)
    assert r.ledger["promises"] == [{"product_id": "p1", "amount": 780.0, "date": "2026-10-16"}]
    assert r.says[1].startswith("I am sorry")


def test_hardship_then_refusal_makes_no_attempt():
    seq = [{"kind": "hardship", "date": "this Friday"}, {"kind": "no"}]
    r = simulate(CASES["PUB-05"], seq, CFG_A)
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("no_promise", "hardship")
    assert not any("Could you pay" in s for s in r.says[2:])


# F06: an evasive answer uses an attempt, noise does not
def test_evasive_answer_uses_attempt():
    r = simulate(CASES["PUB-07"], [{"kind": "evasive"}, {"kind": "evasive"}], CFG_A)
    assert "Could you pay 540.00" in r.says[1]
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("no_promise", "refused")


def test_noise_does_not_use_attempt():
    r = simulate(CASES["PUB-07"], [{"kind": "unclear"}, {"kind": "unclear"}], CFG_A)
    assert all(s.startswith("Sorry") for s in r.says[1:])
    assert r.ledger["outcome"] in (None, "")


# F07: the correction wins, month-first dates
def test_date_correction_wins_over_iso():
    assert NS["parse_spoken_date"]("2026-10-16, sorry I mean tomorrow", TODAY)[0] == "2026-10-15"


def test_month_first_may():
    assert NS["parse_spoken_date"]("May 5", TODAY)[0] == "2027-05-05"
    assert NS["parse_spoken_date"]("I may pay on Friday", TODAY)[0] == "2026-10-16"


# F10: a recorded promise is not changed; the customer hears where to change it
def test_change_after_recording_is_explained_not_recorded():
    seq = [Y, Y, {"kind": "offer", "date": "this Friday"}, BYE]
    r = simulate(CASES["PUB-01"], seq, CFG_A)
    assert len(r.ledger["promises"]) == 1
    assert any("mobile app" in s for s in r.says)


# talkative: small talk after an offer must not repeat the amount (judge: "offered twice")
def test_unclear_after_floor_offer_does_not_repeat_it():
    r = simulate(CASES["PUB-06"], [{"kind": "too_much"}, {"kind": "unclear"}], CFG_A)
    assert "637.50" in r.says[1]
    assert r.says[2] == "Sorry, I did not catch that. Can you do that?"
