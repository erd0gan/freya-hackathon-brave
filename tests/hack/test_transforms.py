"""Transformations of the public scripts (tests/hack/test_engine.SCENARIOS) and of single
replies: a question in between, a person asked for, corrections, a change at the read-back,
talkative filler words. The oracle is the base run and the invariants (fb/harness/selftest.py),
plus the kit's scorer. Also the bugs the coverage matrix found."""

import copy

import covlib as C
import pytest
from fb.harness import selftest as S
from test_engine import CASES, CFG, CFG_A, SCENARIOS

Y = {"kind": "yes", "text": "Yes."}


def ledger(res):
    return S.summarize(res.ledger)


# ---------------------------------------------------------------- interruptions
@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_a_question_anywhere_changes_nothing(name):
    """A question before any reply is answered and the same question asked again: the ledger
    is the one of the script without it."""
    base, bad = C.verdict(name, SCENARIOS[name])
    assert not bad, bad
    for k in range(len(SCENARIOS[name])):
        res, bad = C.verdict(name, C.interrupt(SCENARIOS[name], k))
        assert not bad, (k, bad, res.says)
        assert ledger(res) == ledger(base), (k, res.says)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_a_person_asked_for_twice_anywhere(name):
    """Brief A: ask once, transfer on the second request; a call still going is transferred as
    `human`, one already over is not changed."""
    sc = SCENARIOS[name]
    for k in range(len(sc) + 1):
        case, cfg, res = C.run_public(name, C.human_at(sc, k, 2))
        bad = S.invariants(S.Model(cfg, case["variables"]["now"]), case, res)
        assert not bad, (k, bad)
        _, _, before = C.run_public(name, sc[:k])
        if before.used == k and not before.finished and before.ledger["transfers"] == []:
            assert (res.ledger["outcome"], res.ledger["reason"]) == ("transferred", "human"), k


# ---------------------------------------------------------------- talkative customers
FILLERS = [
    "Work is so busy I can't even think, honestly.",
    "Sorry, I don't know where the time goes these days.",
    "Oh, it's never easy, my kids are shouting.",
]


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_filler_words_change_nothing(name):
    """Chatter before the answer, with a negative word in it, is not a refusal of a part."""
    base, _ = C.verdict(name, SCENARIOS[name])
    for filler in FILLERS:
        replies = [dict(C.texted(r)) for r in SCENARIOS[name]]
        for r in replies:
            r["text"] = filler + " " + r["text"]
        res, bad = C.verdict(name, replies)
        assert not bad, (filler, bad, res.says)
        assert ledger(res) == ledger(base), (filler, res.says)


# ---------------------------------------------------------------- corrections, read-back
# PUB-09: 2100.00 past due, floor 700.00, deadline Friday 23 October; today Wednesday 14th.
CORRECTIONS = [
    ("1000, no, 1200", "Friday", (1200.0, "2026-10-16")),
    ("one thousand, no sorry, twelve hundred", "Friday", (1200.0, "2026-10-16")),
    ("1200", "Friday, no sorry, Monday", (1200.0, "2026-10-19")),
    ("1200", "Monday, no Tuesday", (1200.0, "2026-10-20")),
    ("1200", "the 16th, no the 15th", (1200.0, "2026-10-15")),
    ("700, sorry 1,300", "tomorrow", (1300.0, "2026-10-15")),
    ("twelve hundred, I mean eleven hundred", "tomorrow", (1100.0, "2026-10-15")),
]


@pytest.mark.parametrize("amount,date,want", CORRECTIONS)
def test_the_corrected_value_is_recorded(amount, date, want):
    r = {"kind": "offer", "amount": amount, "date": date}
    _, _, res = C.run_public("PUB-09", [r, Y, Y, Y])
    assert [(p[1], p[2]) for p in C.promises(res)] == [want], res.says


FRIDAY = {"kind": "offer", "date": "Friday", "text": "I can pay Friday."}
READBACK = [
    ("Actually Monday.", {"date": "Monday"}, (2100.0, "2026-10-19")),
    ("Actually 30 October.", {"date": "30 October"}, (2100.0, "2026-10-23")),
    ("Actually I can pay 1500.", {"amount": "1500"}, (1500.0, "2026-10-16")),
    ("Actually 500.", {"amount": "500"}, (700.0, "2026-10-16")),
    ("Make it Saturday.", {"date": "Saturday"}, (2100.0, "2026-10-19")),
]


@pytest.mark.parametrize("text,said,want", READBACK)
def test_a_change_at_the_read_back(text, said, want):
    change = {"kind": "offer", "text": text, **said}
    _, _, res = C.run_public("PUB-09", [FRIDAY, change, Y, Y])
    assert [(p[1], p[2]) for p in C.promises(res)] == [want], res.says


def test_yes_with_a_new_day_at_the_read_back():
    yes = {"kind": "yes", "date": "Monday", "text": "Yes, but make it Monday."}
    _, _, res = C.run_public("PUB-09", [FRIDAY, yes, Y, Y])
    assert C.promises(res) == [("p1", 2100.0, "2026-10-19")], res.says


@pytest.mark.parametrize(
    "after",
    [
        [{"kind": "offer", "date": "Monday", "text": "Can I change it to Monday?"}, Y],
        [{"kind": "no", "text": "Actually cancel it."}, C.K("stop")],
    ],
)
def test_a_recorded_promise_stays(after):
    _, _, res = C.run_public("PUB-09", [FRIDAY, Y, *after])
    assert C.promises(res) == [("p1", 2100.0, "2026-10-16")], res.says
    assert res.ledger["outcome"] == "promise"


# ---------------------------------------------------------------- bugs the matrix found
def test_chatty_yes_with_a_negative_word_agrees_every_product_today():
    """PUB-11: 'I can't even think ... Yes.' to 'all of them today?' was a partial yes (p1
    today, p2/p3 Friday)."""
    chat = {"kind": "yes", "text": "Work is so busy I can't even think, honestly. Yes."}
    _, _, res = C.run_public("PUB-11", [chat, Y, Y, Y])
    assert {p[2] for p in C.promises(res)} == {"2026-10-14"}, res.says
    assert len(C.promises(res)) == 3


def test_a_refusal_naming_a_product_is_still_a_partial_yes():
    for said in (
        "Yes for the credit card, but not the loans.",
        "Only the card, I can't do the rest.",
    ):
        _, _, res = C.run_public("PUB-11", [{"kind": "yes", "text": said}])
        assert "personal loan" in res.says[1] and "To confirm" not in res.says[1], res.says


N_ = {"kind": "no", "text": "No."}


def test_attempt1_on_the_nth_business_day_with_n_zero():
    cfg = copy.deepcopy(CFG_A)
    cfg["attempt1"] = {"rule": "nth_business_day", "n": 0}
    case = copy.deepcopy(CASES["PUB-01"])
    res = S.sim(case, [N_, Y, Y, S.BYE], cfg)
    assert [p["date"] for p in res.ledger["promises"]] == ["2026-10-14"], res.says


# PUB-09 with floor 100 and full 2000: the amount said in two turns without a joiner
SPLIT = [
    (("4", "50"), 450.0),
    (("3", "20"), 320.0),
    (("three", "hundred"), 300.0),
    (("two", "fifty"), 250.0),
    (("2", "grand"), 2000.0),
    (("12", "50"), 1250.0),
    # the joiner behaviour stays
    (("four hundred and", "fifty"), 450.0),
    (("two hundred", "and fifty"), 250.0),
    (("four hundred", "fifty"), 450.0),
    (("637", "50"), 637.5),
    (("two hundred and fifty", "point five"), 250.5),
    (("one thousand and", "fifty"), 1050.0),
]


def _split_case():
    c = copy.deepcopy(CASES["PUB-09"])
    c["products"][0]["past_due"] = 2000.0
    c["products"][0]["instalment_amount"] = 100.0
    return c


@pytest.mark.parametrize("parts,want", SPLIT)
def test_an_amount_said_in_two_parts(parts, want):
    a, b = parts
    r1 = {"kind": "offer", "amount": a, "text": f"I can pay {a}"}
    r2 = {"kind": "offer", "amount": b, "date": "Friday", "text": f"{b} on Friday."}
    res = S.sim(_split_case(), [r1, r2, Y, Y, Y, S.BYE], CFG_A)
    assert [p["amount"] for p in res.ledger["promises"]] == [want], res.says


def test_two_parts_that_read_both_ways_are_asked_again():
    """'1' ... '250': 250 and 1250 are both possible, so neither is guessed."""
    r1 = {"kind": "offer", "amount": "1", "text": "I can pay 1"}
    r2 = {"kind": "offer", "amount": "250", "date": "Friday", "text": "250 on Friday."}
    again = {"kind": "offer", "amount": "1250", "text": "1250."}
    res = S.sim(_split_case(), [r1, r2, again, Y, Y, S.BYE], CFG_A)
    assert res.says[2] == "Sorry, I did not catch the amount. Which amount do you mean?", res.says
    assert [(p["amount"], p["date"]) for p in res.ledger["promises"]] == [(1250.0, "2026-10-16")]


HMM = {"kind": "unclear", "text": "Hmm."}
CUT = {"kind": "unclear", "text": "I can pay on"}
QUESTION = {"kind": "question", "text": "What?"}


@pytest.mark.parametrize(
    "cid,lead",
    [
        ("PUB-01", []),  # ask_today
        ("PUB-09", [N_]),  # offer
        ("PUB-09", [FRIDAY]),  # confirm
        ("PUB-05", [C.K("hardship")]),  # hardship_offer
        ("PUB-01", [C.K("human")]),  # help_open
        ("PUB-12", [N_]),  # three products
    ],
)
def test_unclear_replies_forever_end_the_call(cid, lead):
    _, _, res = C.run_public(cid, lead + [HMM] * 40)
    assert res.finished and res.used <= 40, res.says
    assert res.ledger["outcome"] in ("no_promise", "transferred", "promise"), res.ledger


def test_three_unclear_then_the_fourth_uses_an_attempt():
    """PUB-09 (two attempts): the fourth 'Hmm.' in a row is a non-answer, not the third."""
    _, _, res = C.run_public("PUB-09", [N_, HMM, HMM, HMM])
    assert "700.00" not in res.says[-2], res.says
    _, _, res = C.run_public("PUB-09", [N_, HMM, HMM, HMM, HMM])
    assert "700.00" in res.says[5], res.says


@pytest.mark.parametrize("other", [CUT, QUESTION])
def test_cut_off_turns_and_questions_are_not_counted(other):
    _, _, res = C.run_public("PUB-09", [N_, HMM, HMM, HMM, other])
    assert "700.00" not in res.says[5], res.says
    assert not res.ledger["promises"] and res.ledger["outcome"] != "no_promise"


def test_failed_transfer_writes_a_note_for_every_product_without_a_promise():
    """Brief B rule 8: not transferred after all, so each product gets its note."""
    faults = [{"tool": "transfer_to_agent", "fail_first_n": 2}]
    _, _, res = C.run_public("PUB-12", [C.K("restructure")], faults=faults)
    assert res.ledger["transfers"] == [] or res.ledger["outcome"] == "no_promise"
    assert (res.ledger["outcome"], res.ledger["reason"]) == ("no_promise", "service_error")
    assert sorted(n["product_id"] for n in res.ledger["notes"]) == ["p1", "p2", "p3"]


def test_failed_transfer_keeps_a_recorded_promise_and_notes_the_rest():
    card = {"kind": "offer", "date": "tomorrow", "product": "p1"}
    faults = [{"tool": "transfer_to_agent", "fail_first_n": 2}]
    seq = [card, N_, N_, N_, Y, C.K("restructure")]
    _, _, res = C.run_public("PUB-12", seq, faults=faults)
    assert [p[0] for p in C.promises(res)] == ["p1"], res.says
    assert res.ledger["outcome"] == "promise"
    assert sorted(n["product_id"] for n in res.ledger["notes"]) == ["p2", "p3"]


@pytest.mark.parametrize("days_late", [0, 46, 400])
def test_days_late_outside_every_band_takes_the_nearest_band(days_late):
    case = copy.deepcopy(CASES["PUB-01"])
    case["products"][0]["days_late"] = days_late
    res = S.sim(case, [Y, Y, S.BYE], CFG_A)
    assert res.says[0].strip(), res.says
    assert res.ledger["promises"] and res.ledger["outcome"] == "promise", res.says
    st = res.vars["neg_state"]
    assert '"band_nearest": true' in st


def _nw():
    spec = C.SPECS["NW"]
    cfg = S.gen_cfg(spec)
    m = S.Model(cfg, "2026-10-14T11:00")
    ps = [
        dict(type=t, past_due=500.0, days_late=20, floor=100.0)
        for t in list(cfg["floor_field"])[:2]
    ]
    return m, S.make_case(m, ps)


def test_northwind_hardship_transfer_notes_hardship_not_declined():
    m, case = _nw()
    res = S.sim(case, [C.K("hardship"), S.BYE], m.cfg)
    assert res.ledger["outcome"] == "XFER"
    assert {n["reason"] for n in res.ledger["notes"]} == {"HARDSHIP"}


@pytest.mark.parametrize("kind", ["restructure", "human"])
def test_northwind_transfer_notes_use_a_note_code(kind):
    """A plan or a person has no add_note code in Northwind (DECLINED, HARDSHIP, DISPUTE, PAID,
    SYS_ERR): the transfer's code (PLAN / AGENT) would be rejected by add_note's enum."""
    m, case = _nw()
    res = S.sim(case, [C.K(kind), S.BYE], m.cfg)
    assert res.ledger["outcome"] == "XFER"
    codes = set(m.neg["reasons"].values())
    assert len(res.ledger["notes"]) == 2
    assert all(n["reason"] in codes for n in res.ledger["notes"]), res.ledger["notes"]


def test_brief_b_cfg_is_the_one_under_test():
    assert CFG["neg"]["notes"] and not CFG_A["neg"]["notes"]
