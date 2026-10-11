"""Audit of the calculators (12 Oct): an amount, a day or a time the code cannot read for sure
is asked again, never guessed or defaulted; every re-ask loop ends."""

import datetime

import pytest
from fb.harness.engine_sim import simulate
from fb.nodecode.embed import namespace
from test_engine import CASES, CFG, CFG_A

NS = namespace()
TODAY = datetime.date(2026, 10, 14)  # a Wednesday
Y = {"kind": "yes"}
BYE = {"kind": "unclear"}


def promises(r):
    return [(p["amount"], p["date"]) for p in r.ledger["promises"]]


# ---- 1. an amount we cannot read is asked again, not the full amount
@pytest.mark.parametrize(
    "said", ["700 or maybe 800", "half", "half of it", "six thirty seven fifty"]
)
def test_unreadable_amount_with_a_day_is_asked_again(said):
    r = simulate(
        CASES["PUB-01"],
        [
            {"kind": "offer", "amount": said, "date": "Friday"},
            {"kind": "offer", "amount": "200"},
            Y,
        ],
        CFG_A,
    )
    assert "which amount" in r.says[1].lower(), r.says
    assert "how much" not in " ".join(r.says).lower(), r.says  # brief A rule 24
    assert promises(r) == [(200.0, "2026-10-16")]


@pytest.mark.parametrize(
    "said,want",
    [
        ("the minimum", 126.0),
        ("minimum payment", 126.0),
        ("the lowest", 126.0),
        ("the least I can", 126.0),
        ("that minimum amount", 126.0),  # A pieces PUB-06: was asked 'how much can you pay?'
        ("this minimum payment", 126.0),
        ("all of it", 420.0),
        ("everything", 420.0),
        ("the full amount", 420.0),
        ("the whole thing", 420.0),
        ("that full amount", 420.0),
        ("in full", 420.0),
    ],
)
def test_minimum_and_full_words(said, want):
    r = simulate(CASES["PUB-01"], [{"kind": "offer", "amount": said, "date": "Friday"}, Y], CFG_A)
    assert promises(r) == [(want, "2026-10-16")], r.says


def test_unreadable_amount_reasks_end():
    bad = {"kind": "offer", "amount": "half", "date": "Friday"}
    r = simulate(CASES["PUB-01"], [bad, bad, bad], CFG_A)
    assert [s for s in r.says if "which amount" in s.lower()] == [r.says[1], r.says[2]]
    assert "Could you pay" in r.says[3], r.says  # a non-answer: the persuasion attempt
    assert not r.ledger["promises"]


def test_a_day_text_pointing_at_the_offer_is_no_vague_day():
    v = {"reply_kind": "offer", "said_date_text": "that day", "said_amount_text": "200"}
    assert NS["read_reply"](v, TODAY)["date_kind"] != "vague"
    v["said_date_text"] = "this weekend"
    assert NS["read_reply"](v, TODAY)["date_kind"] == "vague"


@pytest.mark.parametrize("kind", ["too_much", "no", "hardship"])
def test_cannot_pay_the_full_amount_is_no_offer(kind):
    v = {"reply_kind": kind, "said_amount_text": "the full amount"}
    r = NS["read_reply"](v, TODAY)
    assert r["kind"] == kind and r["amount"] is None and not r["amount_is"]


def test_yes_with_the_minimum_is_an_offer_of_the_floor():
    r = simulate(CASES["PUB-01"], [{"kind": "yes", "amount": "the minimum"}, Y], CFG_A)
    assert "126.00" in r.says[1], r.says


def test_minimum_word_inside_a_number_is_no_floor():
    v = {"reply_kind": "offer", "said_amount_text": "the minimum of two hundred"}
    assert NS["read_reply"](v, TODAY).get("amount_is") is None


# ---- 2. a day text we cannot read is a vague day: asked once, then the deadline
@pytest.mark.parametrize(
    "said", ["the end of this week", "this weekend", "in a couple of days", "in a month"]
)
def test_unreadable_day_is_vague_and_ends(said):
    turn = {"kind": "offer", "date": said}
    r = simulate(CASES["PUB-01"], [turn, turn, Y, Y], CFG_A)
    assert "exact day" in r.says[1], r.says
    assert "latest date" in r.says[2], r.says
    assert promises(r) == [(420.0, r.vars["p1_deadline"])]


def test_amount_only_reask_for_a_day_ends():
    turn = {"kind": "offer", "amount": "200"}
    r = simulate(CASES["PUB-01"], [turn] * 4 + [Y, Y], CFG_A)
    assert any("latest date" in s for s in r.says), r.says
    assert promises(r) == [(200.0, r.vars["p1_deadline"])]


# ---- 3. parse_spoken_date
def pd(text, today=TODAY):
    return tuple(NS["parse_spoken_date"](text, today))


@pytest.mark.parametrize(
    "text,want",
    [
        ("the 15th of next month", "2026-11-15"),
        ("Monday the 26th", "2026-10-26"),
        ("first Monday in November", "2026-11-02"),
        ("first thing Monday", "2026-10-19"),
        ("two weeks from Friday", "2026-10-30"),
        ("Friday in two weeks", "2026-10-30"),
        ("in 2 weeks on Friday", "2026-10-30"),
        ("tonight", "2026-10-14"),
        ("this evening", "2026-10-14"),
        ("this afternoon", "2026-10-14"),
        ("mon", "2026-10-19"),
        ("tues", "2026-10-20"),
        ("Thurs", "2026-10-15"),
        ("next fri", "2026-10-23"),
        ("on sat", "2026-10-17"),
        ("Fri 16th", "2026-10-16"),
        ("in two weeks", "2026-10-28"),
        ("Friday the 23rd", "2026-10-23"),
    ],
)
def test_dates(text, want):
    assert pd(text) == (want, "date")


@pytest.mark.parametrize(
    "text",
    [
        "a month from now",
        "Friday the 20th",
        "Monday the 27th",
        "second Friday",
        "last Friday",
        "I sat down",
        "the sun is out",
        "Thursday or Friday",
    ],
)
def test_no_day_guessed(text):
    assert pd(text)[0] == ""


def test_weekday_and_day_that_do_not_match_ask():
    assert pd("Friday the 20th") == ("", "unclear")


def test_31st_in_a_30_day_month_asks():
    assert pd("the 31st", datetime.date(2026, 11, 14)) == ("", "unclear")
    assert pd("the 31st", datetime.date(2026, 9, 14)) == ("", "unclear")
    assert pd("the 5th", datetime.date(2026, 11, 14)) == ("2026-12-05", "date")


# ---- 4. parse_spoken_time
@pytest.mark.parametrize(
    "text,want",
    [
        ("between ten and twelve", ""),
        ("between 2 and 4", ""),
        ("11 or 12", ""),
        ("after 5", ""),
        ("before 5", ""),
        ("2-4pm", ""),
        ("16h30", "16:30"),
        ("eleven or so", "11:00"),
        ("in the afternoon", ""),
        ("ten to five", "16:50"),
        ("half five", "17:30"),
        ("midday", "12:00"),
        ("14:30", "14:30"),
    ],
)
def test_times(text, want):
    assert NS["parse_spoken_time"](text) == want


# ---- 5. parse_amount and cents
@pytest.mark.parametrize(
    "text,want",
    [
        ("50%", None),
        ("20 percent", None),
        ("hundred twenty six", 126.0),
        ("2 thousand 5 hundred", 2500.0),
        ("nought point five", 0.5),
        ("£210.50p", 210.5),
        ("1.250,50", None),
        ("1,250.50", 1250.5),
        ("50p", 0.5),
        ("two grand", 2000.0),
        ("1.5k", 1500.0),
        ("four hundred and fifty", 450.0),
    ],
)
def test_amounts(text, want):
    assert NS["parse_amount"](text) == want


def test_agreed_amount_rounds_half_up_to_cents():
    p = {}
    NS["_agree"](p, 2.675, "2026-10-16")
    assert p["amount"] == 2.68
    NS["_agree"](p, 1.005, "2026-10-16")
    assert p["amount"] == 1.01


# ---- 6. product facts with amounts as text
@pytest.mark.parametrize("due", ["1,250.00", "£1,250.00", "1250"])
def test_amount_text_in_product_facts(due):
    pf = {
        "product_count": 1,
        "p1_id": "p1",
        "p1_past_due": due,
        "p1_floor": "£375.00",
        "p1_deadline": "2026-10-30",
        "p1_attempts": "1",
    }
    st = NS["new_state"](pf, CFG["neg"], "2026-10-14T11:00")
    assert [(q["full"], q["floor"]) for q in st["p"]] == [(1250.0, 375.0)]
