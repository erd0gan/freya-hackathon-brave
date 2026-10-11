"""Third engine review: fifteen negotiation bugs reproduced through the code nodes, one test (or
two) each. Replies are extraction values plus the customer's words (`text`)."""

from fb.harness.engine_sim import simulate
from test_engine import CASES, CFG, CFG_A

Y = {"kind": "yes", "text": "Yes."}
N = {"kind": "no", "text": "No."}
BYE = {"kind": "unclear", "text": "Thanks, bye."}
CARD = {"kind": "offer", "date": "tomorrow", "product": "p1", "text": "The credit card tomorrow."}
BOTH_30 = {"kind": "offer", "date": "30 October", "all": True, "text": "Both on the 30th."}


def promises(r):
    return [(p["product_id"], p["amount"], p["date"]) for p in r.ledger.get("promises") or []]


def notes(r):
    return [(n["product_id"], n["reason"]) for n in r.ledger.get("notes") or []]


def offers(r, text):
    return sum(text in s for s in r.says)


# 1. "already paid" about a product refused earlier changes that product, not the agreed card
def test_paid_claim_about_a_refused_product_keeps_the_agreed_promise():
    paid = {"kind": "paid", "product": "p3", "text": "Actually I already paid the car loan."}
    r = simulate(CASES["PUB-12"], [CARD, N, N, N, paid, Y, BYE], CFG)
    assert promises(r) == [("p1", 510.0, "2026-10-15")], r.says
    assert notes(r) == [("p2", "refused"), ("p3", "paid")]
    assert r.ledger["outcome"] == "promise"


def test_dispute_about_a_refused_product_is_noted_as_dispute():
    said = {"kind": "dispute", "product": "p2", "text": "The personal loan is not even mine."}
    r = simulate(CASES["PUB-12"], [CARD, N, N, said, N, Y, BYE], CFG)
    assert promises(r) == [("p1", 510.0, "2026-10-15")], r.says
    assert ("p2", "dispute") in notes(r)


# 2. the answer to "can I help you with this myself?" is not an answer to the question before it
def test_yes_after_help_first_repeats_the_open_question():
    human = {"kind": "human", "text": "Let me talk to a person."}
    r = simulate(CASES["PUB-13"], [BOTH_30, human, Y, BYE], CFG)
    assert promises(r) == [], r.says
    assert r.says[3] == r.says[1] and r.says[1].startswith("To confirm")


def test_no_after_help_first_transfers():
    human = {"kind": "human", "text": "I want a real person."}
    r = simulate(CASES["PUB-01"], [human, N], CFG_A)
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("transferred", "human")
    assert r.ledger["transfers"] == [{"queue": "general"}]


def test_help_first_then_an_offer_goes_on():
    human = {"kind": "human", "text": "Can I speak to someone?"}
    fri = {"kind": "offer", "date": "Friday", "text": "Fine, Friday then."}
    r = simulate(CASES["PUB-01"], [human, fri, Y, BYE], CFG_A)
    assert promises(r) == [("p1", 420.0, "2026-10-16")], r.says


# 3. "Yes, but only the credit card" at the read-back is a yes to the card alone
def test_yes_to_one_product_at_the_readback():
    only = {"kind": "yes", "product": "p1", "text": "Yes, but only the credit card."}
    r = simulate(CASES["PUB-13"], [BOTH_30, only, N, N, Y, BYE], CFG)
    assert promises(r) == [("p1", 300.0, "2026-10-30")], r.says
    assert notes(r) == [("p2", "refused")]


# 4. a day offered for a product refused before is for that product
def test_offer_for_a_refused_product_reopens_it():
    loan = {
        "kind": "offer",
        "date": "Friday",
        "product": "p2",
        "text": "Actually I can pay the personal loan on Friday.",
    }
    r = simulate(CASES["PUB-12"], [CARD, N, N, loan, N, Y, BYE], CFG)
    got = {p[0]: (p[1], p[2]) for p in promises(r)}
    assert got == {"p1": (510.0, "2026-10-15"), "p2": (1320.0, "2026-10-16")}, r.says
    assert notes(r) == [("p3", "refused")]


# 5. "Yes, that Friday" at the read-back is the Friday read back, not the nearest one
def test_yes_that_friday_at_the_readback_keeps_the_agreed_day():
    that = {"kind": "yes", "date": "that Friday", "text": "Yes, that Friday is fine."}
    r = simulate(CASES["PUB-10"], [N, N, Y, that, BYE], CFG_A)
    assert promises(r) == [("p1", 109.5, "2026-10-23")], r.says


# 6. hardship after an agreed promise: the promise is still read back and recorded
def test_hardship_keeps_an_agreed_promise():
    hard = {"kind": "hardship", "text": "I lost my job last month."}
    r = simulate(CASES["PUB-12"], [CARD, hard, N, Y, BYE], CFG)
    assert promises(r) == [("p1", 510.0, "2026-10-15")], r.says
    assert notes(r) == [("p2", "hardship"), ("p3", "hardship")]
    assert r.ledger["outcome"] == "promise"


def test_hardship_close_policy_keeps_an_agreed_promise():
    cfg = {**CFG, "neg": {**CFG["neg"], "hardship": "close"}}
    hard = {"kind": "hardship", "text": "I lost my job last month."}
    r = simulate(CASES["PUB-12"], [CARD, hard, Y, BYE], cfg)
    assert promises(r) == [("p1", 510.0, "2026-10-15")], r.says
    assert r.says[2].startswith("I am sorry") and "To confirm" in r.says[2]


# 7. a yes for one product with a refusal naming the others agrees that product only
def test_yes_for_one_product_refusing_the_others():
    said = "Yes for the credit card, but I won't pay the personal loan or the car loan."
    r = simulate(CASES["PUB-12"], [{"kind": "yes", "product": "p1", "text": said}], CFG)
    assert "personal loan" in r.says[1] and "To confirm" not in r.says[1], r.says


# 8. hardship with the customer's own date: no persuasion attempt on the other products
def test_hardship_with_own_date_makes_no_attempt_on_the_others():
    said = "I lost my job, but I can pay the credit card tomorrow."
    hard = {"kind": "hardship", "date": "tomorrow", "product": "p1", "text": said}
    r = simulate(CASES["PUB-12"], [hard, N, Y, BYE], CFG)
    assert not any("Could you pay" in s for s in r.says), r.says
    assert promises(r) == [("p1", 510.0, "2026-10-15")]
    assert notes(r) == [("p2", "hardship"), ("p3", "hardship")]


def test_hardship_with_own_date_then_support_accepted_transfers():
    said = "I lost my job, but I can pay the credit card tomorrow."
    hard = {"kind": "hardship", "date": "tomorrow", "product": "p1", "text": said}
    r = simulate(CASES["PUB-12"], [hard, Y], CFG)
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("transferred", "support")


# 9. a no at the read-back about a product that is not in it does not reopen the agreed one
def test_no_about_a_refused_product_at_the_readback_asks_again():
    said = {"kind": "no", "product": "p2", "text": "No, I told you I am not paying the loan."}
    r = simulate(CASES["PUB-12"], [CARD, N, N, N, said, Y, BYE], CFG)
    assert promises(r) == [("p1", 510.0, "2026-10-15")], r.says
    assert "To confirm" in r.says[5]


# 10. the answer to "Which promise would you like to change?" may be the product alone
def test_product_alone_answers_which_promise():
    loan = {"kind": "unclear", "product": "p2", "text": "The loan."}
    r = simulate(CASES["PUB-13"], [BOTH_30, N, loan, N, N, Y, BYE], CFG)
    assert r.says[2] == "Which promise would you like to change?"
    assert "personal loan" in r.says[3], r.says
    assert promises(r) == [("p1", 300.0, "2026-10-30")]


# 11. an offer the customer refused is never made again (A31)
def test_floor_refused_as_attempt_two_is_not_offered_again():
    much = {"kind": "too_much", "text": "That is still too much for me."}
    r = simulate(CASES["PUB-10"], [N, N, much], CFG_A)
    assert offers(r, "109.50") == 1, r.says
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("no_promise", "refused")


def test_floor_refused_after_a_low_amount_is_not_attempt_two():
    low = {"kind": "offer", "amount": "50", "date": "23 October", "text": "50 on the 23rd."}
    r = simulate(CASES["PUB-10"], [low, N, N], CFG_A)
    assert offers(r, "Could you pay 109.50") == 1, r.says
    assert (r.ledger["outcome"], r.ledger["reason"]) == ("no_promise", "refused")


# 12. a new amount at the read-back keeps the agreed day
def test_new_amount_at_the_readback_keeps_the_day():
    fri = {"kind": "offer", "date": "Friday", "text": "On Friday."}
    more = {"kind": "offer", "amount": "200", "text": "Make it 200 instead."}
    r = simulate(CASES["PUB-01"], [fri, more, Y, BYE], CFG_A)
    assert promises(r) == [("p1", 200.0, "2026-10-16")], r.says


# 13. "Not today, but both on the 30th" is for both; an exact day after "both next week" too
def test_not_today_but_both_is_for_both():
    for said in (
        "Not today, but I can pay both on the thirtieth of October.",
        "I can't do it today, but both on the thirtieth of October.",
        "I won't be able to pay today, but both on the thirtieth of October.",
    ):
        first = {"kind": "offer", "date": "the thirtieth of October", "all": True, "text": said}
        r = simulate(CASES["PUB-13"], [first, Y, BYE], CFG)
        assert promises(r) == [("p1", 300.0, "2026-10-30"), ("p2", 900.0, "2026-10-23")], said


def test_exact_day_after_both_next_week_is_for_both():
    vague = {"kind": "vague", "date": "next week", "all": True, "text": "Both next week."}
    day = {"kind": "offer", "date": "the 21st", "text": "The 21st."}
    r = simulate(CASES["PUB-13"], [vague, day, Y, BYE], CFG)
    assert promises(r) == [("p1", 300.0, "2026-10-21"), ("p2", 900.0, "2026-10-21")], r.says


# 14. after the recording: an offer or a paid claim about a product without a promise is heard
def test_offer_after_recording_for_a_refused_product_is_recorded():
    loan = {
        "kind": "offer",
        "date": "Friday",
        "product": "p2",
        "text": "Actually, I can pay the personal loan on Friday after all.",
    }
    r = simulate(CASES["PUB-12"], [CARD, N, N, N, Y, loan, Y, BYE], CFG)
    got = {p[0]: (p[1], p[2]) for p in promises(r)}
    assert got == {"p1": (510.0, "2026-10-15"), "p2": (1320.0, "2026-10-16")}, r.says
    assert notes(r) == [("p3", "refused")]
    last = next(s for s in reversed(r.says) if s.startswith("Your promise is recorded"))
    assert "personal loan" in last and "credit card" not in last


def test_paid_claim_after_recording_is_noted_once():
    paid = {"kind": "paid", "product": "p2", "text": "Oh and I already paid the personal loan."}
    r = simulate(CASES["PUB-12"], [CARD, N, N, N, Y, paid, BYE], CFG)
    assert notes(r) == [("p2", "paid"), ("p3", "refused")]
    assert r.ledger["outcome"] == "promise"
    assert sum("one business day" in s for s in r.says) == 1, r.says


def test_paid_sentence_is_not_said_again_at_the_goodbye():
    paid = {"kind": "paid", "product": "p1", "text": "I already paid the credit card."}
    disp = {"kind": "dispute", "product": "p2", "text": "The personal loan is not mine."}
    r = simulate(CASES["PUB-12"], [paid, disp, N], CFG)
    assert sum("one business day" in s for s in r.says) == 1, r.says
    assert notes(r) == [("p1", "paid"), ("p2", "dispute"), ("p3", "refused")]


# 15. an amount given in two parts is joined before anything is decided
def test_amount_in_two_parts_is_joined():
    first = {"kind": "offer", "amount": "six hundred and", "text": "I could pay six hundred and"}
    rest = {
        "kind": "offer",
        "amount": "thirty-seven fifty",
        "date": "Friday",
        "text": "thirty-seven fifty, on Friday.",
    }
    r = simulate(CASES["PUB-06"], [first, rest, Y, BYE], CFG_A)
    assert not any("600.00" in s or "cannot record" in s for s in r.says), r.says
    assert promises(r) == [("p1", 637.5, "2026-10-16")], r.says


def test_cut_amount_with_its_day_in_the_first_part():
    first = {
        "kind": "offer",
        "amount": "six hundred",
        "date": "Friday",
        "text": "On Friday I could pay six hundred",
    }
    rest = {"kind": "offer", "amount": "thirty-seven fifty", "text": "thirty-seven fifty."}
    r = simulate(CASES["PUB-06"], [first, rest, Y, BYE], CFG_A)
    assert promises(r) == [("p1", 637.5, "2026-10-16")], r.says


def test_amount_said_again_after_a_cut_is_not_added():
    first = {"kind": "offer", "amount": "200", "text": "I can pay 200"}
    again = {"kind": "offer", "amount": "200", "date": "Friday", "text": "200 on Friday."}
    r = simulate(CASES["PUB-01"], [first, again, Y, BYE], CFG_A)
    assert promises(r) == [("p1", 200.0, "2026-10-16")], r.says


def test_digit_pieces_are_added():
    first = {"kind": "offer", "amount": "600", "text": "I could pay 600 and"}
    rest = {"kind": "offer", "amount": "37.50", "date": "Friday", "text": "37.50 on Friday."}
    r = simulate(CASES["PUB-06"], [first, rest, Y, BYE], CFG_A)
    assert promises(r) == [("p1", 637.5, "2026-10-16")], r.says


def test_whole_amount_is_not_held_back():
    said = {"kind": "offer", "amount": "200", "date": "Friday", "text": "200 on Friday."}
    r = simulate(CASES["PUB-01"], [said, Y, BYE], CFG_A)
    assert promises(r) == [("p1", 200.0, "2026-10-16")], r.says
