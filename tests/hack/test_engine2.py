"""Third engine review: fifteen negotiation bugs reproduced through the code nodes, one test (or
two) each. Replies are extraction values plus the customer's words (`text`)."""

import copy

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


# ---------------------------------------------------------------- state-machine audit (06, SM-*)
# and hardcoded assumptions (03, HA-*), through the bodies with every extraction variable
def engine_call(case, replies, cfg):
    from test_engine_tr import call

    says, led, errs = call(case, replies, cfg)
    assert not errs, errs
    return says, led


def led_promises(led):
    return [(p["product_id"], p["amount"], p["date"]) for p in led.get("promises") or []]


def led_notes(led):
    return [(n["product_id"], n["reason"]) for n in led.get("notes") or []]


def changed(cfg, change):
    c = copy.deepcopy(cfg)
    change(c)
    return c


def case_with(name, change):
    c = copy.deepcopy(CASES[name])
    change(c)
    return c


TOO_MUCH = {"kind": "too_much", "text": "That is too much."}


# SM-1: "I lost my job, I need lower instalments" is hardship first (A32 before A41)
def test_hardship_said_with_a_plan_request_is_hardship():
    plan = {"kind": "restructure", "also_hardship": "yes", "text": "I lost my job, I need a plan."}
    says, led = engine_call(CASES["PUB-05"], [plan, N], CFG_A)
    assert (led["outcome"], led["reason"]) == ("no_promise", "hardship"), says
    assert led["transfers"] == []
    says, led = engine_call(CASES["PUB-05"], [plan, Y], CFG_A)
    assert led["transfers"] == [{"queue": "support"}]


def test_plan_request_alone_still_transfers():
    plan = {"kind": "restructure", "text": "Can I get a payment plan?"}
    _, led = engine_call(CASES["PUB-09"], [plan], CFG_A)
    assert (led["outcome"], led["reason"]) == ("transferred", "restructuring")


# SM-2: a whole short refusal at the security questions counts ("No.", "Nope.")
def test_short_whole_refusal_at_identity_counts():
    from fb.nodecode.embed import namespace

    real = namespace()["decline_real"]
    for text in ("No.", "Nope.", "No thanks.", "None of your business.", "Forget it."):
        assert real(text), text
    assert not real("I have been so busy at work because my")
    assert not real("Work has been crazy today, the traffic was awful.")


# SM-3 / HA-8: a band with no persuasion attempt gets none, also as the second product
def test_zero_attempt_band_makes_no_offer_on_a_later_product():
    cfg = changed(CFG, lambda c: c["bands"][1].update(attempts=0))  # late: 8-45 days
    says, led = engine_call(CASES["PUB-12"], [N, N, BYE], cfg)
    assert not any("personal loan" in s or "credit card" in s for s in says[1:]), says
    assert {("p1", "refused"), ("p2", "refused")} <= set(led_notes(led)), says


# SM-4: "Yes, 365 on Friday" at the read-back restates the values: it is the yes
def test_yes_restating_the_readback_records_at_once():
    same = {"kind": "yes", "amount": "365", "date": "Friday", "text": "Yes, 365 on Friday."}
    says, led = engine_call(CASES["PUB-10"], [N, Y, same, BYE], CFG_A)
    assert led_promises(led) == [("p1", 365.0, "2026-10-16")], says
    assert sum(s.startswith("To confirm") for s in says) == 1


def test_yes_with_another_amount_at_the_readback_is_a_change():
    other = {"kind": "yes", "amount": "200", "date": "Friday", "text": "Yes, 200 on Friday."}
    says, led = engine_call(CASES["PUB-10"], [N, Y, other, Y, BYE], CFG_A)
    assert led_promises(led) == [("p1", 200.0, "2026-10-16")], says


# SM-5: "I already paid all of them" is about every product
def test_paid_all_marks_every_product():
    paid = {"kind": "paid", "all": True, "text": "I already paid all of them."}
    says, led = engine_call(CASES["PUB-11"], [paid], CFG)
    assert led["outcome"] == "paid", says
    assert sorted(r for _, r in led_notes(led)) == ["paid"] * 3
    assert len(says) == 2  # no offer on the next product


# SM-7: hardship after the recording never reopens the recorded promise
def test_hardship_after_recording_keeps_the_recorded_promise():
    hard = {"kind": "hardship", "text": "Actually things are hard, I lost my job."}
    other = {"kind": "offer", "amount": "300", "date": "Thursday", "text": "300 on Thursday."}
    says, led = engine_call(CASES["PUB-01"], [Y, Y, hard, other, Y, BYE, BYE], CFG_A)
    assert led_promises(led) == [("p1", 420.0, "2026-10-14")], says
    assert [c["tool"] for c in led["calls"]].count("create_promise") == 1
    assert any(s.startswith("A recorded promise cannot be changed") for s in says), says


def test_promise_the_bank_refused_twice_is_not_tried_a_third_time():
    case = case_with(
        "PUB-01", lambda c: c.update(faults=[{"tool": "create_promise", "fail_first_n": 2}])
    )
    hard = {"kind": "hardship", "text": "I lost my job."}
    other = {"kind": "offer", "date": "tomorrow", "text": "I can pay tomorrow."}
    says, led = engine_call(case, [Y, Y, hard, other, Y, BYE, BYE], CFG_A)
    assert [c["tool"] for c in led["calls"]].count("create_promise") == 2, says
    assert led_promises(led) == []


# SM-8: after the support offer, "yes" to "can I help you myself?" repeats the support offer
def test_support_offer_is_the_open_question_after_hardship():
    hard = {"kind": "hardship", "text": "I was in hospital."}
    human = {"kind": "human", "text": "Can I talk to a person?"}
    says, _ = engine_call(CASES["PUB-10"], [N, hard, human, Y], CFG_A)
    assert says[-1] == "Would you like me to connect you with our customer support team?", says


# SM-9: vague again after the deadline was proposed is a non-answer, not the same question
def test_vague_again_after_the_deadline_offer_is_no_loop():
    vague = {"kind": "vague", "date": "sometime next month", "text": "Sometime next month."}
    says, _ = engine_call(CASES["PUB-07"], [vague] * 5, CFG_A)
    assert sum(s.startswith("The latest date I can record") for s in says) == 1, says


# SM-10: only questions: after five in a row the next one is a non-answer (A30)
def test_questions_only_do_not_go_on_forever():
    q = {"kind": "question", "text": "Who are you again?"}
    says, _ = engine_call(CASES["PUB-10"], [q] * 7, CFG_A)
    assert says[6].startswith("Could you pay"), says  # the 6th question used attempt 1


# SM-12: a transfer that does not happen closes by the products' own ends
def test_failed_transfer_closes_with_the_first_products_reason():
    case = case_with(
        "PUB-13", lambda c: c.update(faults=[{"tool": "transfer_to_agent", "fail_first_n": 3}])
    )
    dispute = {"kind": "dispute", "product": "p1", "text": "The card is not mine."}
    human = {"kind": "human", "text": "I want a person."}
    _, led = engine_call(case, [dispute, human, human], CFG)
    assert (led["outcome"], led["reason"]) == ("no_promise", "dispute")
    assert ("p2", "service_error") in led_notes(led)


# HA-4: an account without a product type
def test_product_without_type_takes_the_only_configured_type():
    case = case_with("PUB-01", lambda c: c["products"][0].pop("type"))

    def one_type(c):
        c["product_names"] = {"credit_card": "credit card ending {card_last4}"}
        c["floor_field"] = {"credit_card": "minimum_payment"}

    says, led = engine_call(case, [TOO_MUCH, Y, Y, BYE], changed(CFG_A, one_type))
    assert "Your credit card ending 4821 has 420.00 past due" in says[0], says
    assert led_promises(led) == [("p1", 126.0, "2026-10-14")], says


def test_product_without_type_among_several_gets_a_neutral_name():
    case = case_with("PUB-01", lambda c: c["products"][0].pop("type"))
    says, led = engine_call(case, [Y, Y, BYE], CFG_A)
    assert "Your account has 420.00 past due" in says[0], says
    assert led_promises(led) == [("p1", 420.0, "2026-10-14")]


# HA-5: an account without days late: from a due date, else the product is kept
def test_days_late_from_a_due_date():
    def due(c):
        c["products"][0].pop("days_late")
        c["products"][0]["due_date"] = "2026-10-08"

    says, led = engine_call(case_with("PUB-01", due), [Y, Y, BYE], CFG_A)
    assert "is 6 days late" in says[0], says
    assert led_promises(led) == [("p1", 420.0, "2026-10-14")]


def test_product_without_any_days_late_is_kept():
    case = case_with("PUB-01", lambda c: c["products"][0].pop("days_late"))
    says, led = engine_call(case, [N, Y, Y, BYE], CFG_A)
    assert "Your credit card ending 4821 has 420.00 past due. Can" in says[0], says
    # the band with the earliest deadline (late: 7 business days) and the fewest attempts
    assert led["outcome"] == "promise" and led_promises(led)[0][2] <= "2026-10-23", says


# HA-6: the floor as a share of a field
def test_floor_rule_percent_of_a_field():
    cfg = changed(CFG_A, lambda c: c.update(floor_rule={"percent_of": "past_due", "percent": 20}))
    says, led = engine_call(CASES["PUB-01"], [TOO_MUCH, Y, Y, BYE], cfg)
    assert says[1].startswith("The minimum for the credit card ending 4821 is 84.00."), says
    assert led_promises(led) == [("p1", 84.0, "2026-10-14")]


# summary I: amounts in minor units in the account answer
def test_amounts_in_cents_are_scaled_to_major_units():
    case = case_with(
        "PUB-01", lambda c: c["products"][0].update(past_due=42000, minimum_payment=12600)
    )
    scale = {"products[].past_due": 0.01, "data.products.minimum_payment": 0.01}
    cfg = changed(CFG_A, lambda c: c.update(amount_scale=scale))
    says, led = engine_call(case, [TOO_MUCH, Y, Y, BYE], cfg)
    assert "has 420.00 past due" in says[0] and "is 126.00" in says[1], says
    assert led_promises(led) == [("p1", 126.0, "2026-10-14")]


# HA-10: a brief with no plan transfer (optional knob): a plan request is a no
def test_restructure_as_a_no_when_the_brief_has_no_plan_transfer():
    plan = {"kind": "restructure", "text": "Can I get a payment plan?"}
    cfg = changed(CFG_A, lambda c: c["neg"].update(restructure="no"))
    says, led = engine_call(CASES["PUB-10"], [plan], cfg)
    assert led["transfers"] == [] and says[1].startswith("Could you pay"), says


# a floor equal to the full amount leaves no rest to mention
def test_no_rest_sentence_when_the_floor_is_the_full_amount():
    cfg = changed(CFG_A, lambda c: c.update(floor_field={"credit_card": ""}))
    low = {"kind": "offer", "amount": "100", "date": "Friday", "text": "100 on Friday."}
    says, _ = engine_call(CASES["PUB-01"], [low], cfg)
    assert says[1].startswith("I cannot record 100.00. The minimum is 420.00."), says
    assert "rest stays overdue" not in says[1]


# stress R01 on Freya, 11 Oct: "I can't pay the full 480, I have 120" is an offer below the
# floor; the floor today was refused and attempt 1 came for the full 480 (rule 30: the floor
# when the customer already said the full amount is too much)
def test_an_offer_below_the_floor_makes_attempt_one_the_floor():
    low = {"kind": "offer", "amount": "100", "text": "I can't pay it all. I have 100 today."}
    r = simulate(CASES["PUB-01"], [low, N, Y, BYE], CFG_A)
    assert r.says[1].startswith("I cannot record 100.00. The minimum is 126.00."), r.says
    assert "126.00" in r.says[2] and "420.00" not in r.says[2], r.says


# stress D-06 on Freya, 11 Oct: one message says the card is paid, the personal loan is not
# the customer's and the car loan is refused; the card and the loan got persuasion offers
def test_paid_and_dispute_in_one_message_get_no_offer():
    said = (
        "No, I paid the credit card yesterday, and the personal loan isn't mine. "
        "As for the car loan, I won't be paying that."
    )
    for kind in ("no", "paid", "dispute"):
        says, led = engine_call(CASES["PUB-12"], [{"kind": kind, "text": said}, N, N], CFG)
        assert not any("for the credit card" in s or "for the personal loan" in s for s in says)
        assert "for the car loan" in says[1], says
        assert led_notes(led) == [("p1", "paid"), ("p2", "dispute"), ("p3", "refused")], kind


# stress D-08: "that credit card isn't mine" next to an offer for the loan
def test_dispute_next_to_an_offer_for_the_other_product():
    offer = {
        "kind": "offer",
        "amount": "nine hundred",
        "date": "tomorrow",
        "product": "p2",
        "text": "I can't pay it all today, and that credit card isn't mine. "
        "I could pay nine hundred on the loan tomorrow.",
    }
    says, led = engine_call(CASES["PUB-13"], [offer, Y, BYE], CFG)
    assert not any("for the credit card" in s for s in says), says
    assert led_promises(led) == [("p2", 900.0, "2026-10-15")]
    assert led_notes(led) == [("p1", "dispute")]


def test_a_paid_word_about_the_future_is_no_claim():
    from fb.nodecode.embed import namespace

    claims = namespace()["_claims"]
    st = {"p": [{"name": "credit card", "status": "open"}, {"name": "car loan", "status": "open"}]}
    assert claims(st, "I'll have the credit card paid by Friday.") == {}
    assert claims(st, "The credit card is not paid yet.") == {}
    assert claims(st, "I paid the credit card this morning.") == {0: "paid"}
    assert claims(st, "I paid the credit card and the car loan.") == {0: "paid"}


# stress D-05: create_promise failed every time, then a new offer at the end got "A recorded
# promise cannot be changed in this call"; nothing was recorded
def test_failed_promise_is_never_called_recorded():
    case = case_with(
        "PUB-01", lambda c: c.update(faults=[{"tool": "create_promise", "fail_first_n": 9}])
    )
    again = {"kind": "offer", "amount": "420", "date": "today", "text": "Then 420 today."}
    says, _ = engine_call(case, [Y, Y, again, BYE], CFG_A)
    assert not any("cannot be changed" in s or "is recorded" in s for s in says), says
    assert says[3].startswith("I could not record the promise"), says


# stress R11: "I can pay the full 900 tomorrow. Would that work?" got "I am sorry, I do not
# have that information." before the read-back
def test_would_that_work_is_answered_by_the_readback():
    from fb.nodecode.embed import namespace

    only = namespace()["_only_offer_check"]
    assert only("I can pay 900 tomorrow. Would that work?", "confirm")
    assert only("Tomorrow, is that okay?", "confirm")
    assert not only("Would that work? And are you a robot?", "confirm")
    assert not only("Would that work?", "offer")


# stress G-06 on Freya, 11 Oct: "Yes, the home premium on the sixteenth and the motor premium
# on the twentieth" to a two-promise read-back was taken as one new day for both
def test_yes_restating_each_agreed_day_confirms():
    yes = {
        "kind": "offer",
        "date": "the thirtieth",
        "text": "Yes, the card on the thirtieth and the loan on the twenty-third.",
    }
    says, led = engine_call(CASES["PUB-13"], [BOTH_30, yes, BYE], CFG)
    assert led_promises(led) == [("p1", 300.0, "2026-10-30"), ("p2", 900.0, "2026-10-23")], says
    sums = {"kind": "offer", "amount": "300 and 900", "text": "Yes, correct: 300 on the 30th "
            "and 900 on the 23rd."}
    _, led = engine_call(CASES["PUB-13"], [BOTH_30, sums, BYE], CFG)
    assert len(led_promises(led)) == 2


def test_a_change_at_a_two_promise_readback_is_still_a_change():
    for said in ("No, the loan on the thirtieth too.", "Yes, but the card on the 23rd."):
        change = {"kind": "offer", "date": "the 23rd", "text": said}
        says, _ = engine_call(CASES["PUB-13"], [BOTH_30, change], CFG)
        assert "Your promise is recorded" not in says[2], (said, says)


def _with_holiday(cfg, day="2026-10-16"):
    return changed(cfg, lambda c: c.update(holidays=[*list(c.get("holidays") or []), day]))


# stress C-01 on Freya, 11 Oct: "both on Sunday the 18th" (a holiday) moved the card only,
# called the holiday "not a business day" and the loan was asked again and again
def test_one_day_for_all_on_a_holiday_moves_every_product_in_one_readback():
    both = {"kind": "offer", "date": "Friday the 16th", "all": True, "text": "Both on Friday."}
    says, led = engine_call(CASES["PUB-13"], [both, Y, BYE], _with_holiday(CFG))
    assert says[1].count("Monday 19 October 2026, the next business day") == 2, says
    assert led_promises(led) == [("p1", 300.0, "2026-10-19"), ("p2", 900.0, "2026-10-19")]


def test_a_holiday_is_called_a_holiday():
    fri = {"kind": "offer", "date": "Friday the 16th", "text": "Friday the 16th."}
    says, _ = engine_call(CASES["PUB-10"], [fri], _with_holiday(CFG_A))
    assert says[1].startswith("Friday 16 October 2026 is a holiday."), says
