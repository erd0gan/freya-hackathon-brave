"""Bugs the stress lab found on Freya on 11 Oct (BRAVE workflows for synthetic briefs C-H), one
test (or two) each, through the code nodes. Replies are extraction values plus the words."""

from fb.harness.engine_sim import simulate
from test_engine import CASES, CFG, CFG_A

Y = {"kind": "yes", "text": "Yes."}
BYE = {"kind": "unclear", "text": "Thanks, bye."}


def promises(r):
    return [(p["product_id"], p["amount"], p["date"]) for p in r.ledger.get("promises") or []]


# R03 (brief E): "Yes, I can hear you. Does that take money now?" after the read-back was taken as
# the confirmation and the promise was written without consent
def test_a_yes_that_only_hears_and_asks_is_no_consent():
    hear = {
        "kind": "yes",
        "question": "yes",
        "text": "Yes, I can hear you. Does that take money now?",
    }
    r = simulate(CASES["PUB-01"], [Y, hear], CFG_A)
    assert promises(r) == [], r.says
    assert "To confirm" in r.says[2], r.says
    r = simulate(CASES["PUB-01"], [Y, hear, Y, BYE], CFG_A)
    assert promises(r) == [("p1", 420.0, "2026-10-14")], r.says


def test_a_yes_that_only_hears_is_no_consent():
    hear = {"kind": "yes", "text": "Yes, I can hear you."}
    r = simulate(CASES["PUB-01"], [Y, hear], CFG_A)
    assert promises(r) == [], r.says


def test_a_confirmation_with_a_question_is_still_a_confirmation():
    yes_q = {"kind": "yes", "question": "yes", "text": "Yes, I confirm. Will I get a text?"}
    r = simulate(CASES["PUB-01"], [Y, yes_q, BYE], CFG_A)
    assert promises(r) == [("p1", 420.0, "2026-10-14")], r.says


# R04 / F-05 (brief F): "five hundred altogether" went whole onto the first product ("I have
# noted the credit card for 500.00"), and "300 to the card and 200 to the loan" was "Sorry, I
# did not catch the amount" again and again
def test_a_total_for_several_products_asks_how_to_split_it():
    total = {"kind": "offer", "amount": "five hundred", "text": "I have five hundred altogether."}
    r = simulate(CASES["PUB-13"], [total], CFG)
    assert r.says[1].startswith("How would you like to split 500.00 between"), r.says
    assert "credit card" in r.says[1] and "personal loan" in r.says[1]


def test_amounts_per_product_in_one_message_go_to_each_product():
    total = {"kind": "offer", "amount": "five hundred", "text": "I have five hundred altogether."}
    split = {
        "kind": "offer",
        "amount": "200 and 300",
        "date": "today",
        "text": "200 to the credit card and 300 to the personal loan, both today.",
    }
    r = simulate(CASES["PUB-13"], [total, split, Y, BYE], CFG)
    assert sorted(promises(r)) == [("p1", 200.0, "2026-10-14"), ("p2", 300.0, "2026-10-14")], r.says


def test_one_amount_for_each_product():
    each = {
        "kind": "offer",
        "amount": "three hundred",
        "date": "today",
        "text": "Three hundred to each of them today.",
    }
    r = simulate(CASES["PUB-13"], [each, Y, BYE], CFG)
    assert sorted(promises(r)) == [("p1", 300.0, "2026-10-14"), ("p2", 300.0, "2026-10-14")], r.says


def test_an_amount_for_the_product_in_turn_is_still_that_products():
    # one by one, "I can pay 300 on Friday" is about the product just asked about
    no = {"kind": "no", "text": "No, not today."}
    low = {"kind": "offer", "amount": "300", "date": "Friday", "text": "I can pay 300 on Friday."}
    r = simulate(CASES["PUB-13"], [no, low], CFG)
    assert "split" not in r.says[2], r.says


# F-08 (brief F, a person at once): "I want to speak to a human" at the opening was answered
# "Before I transfer you, can I help you with this myself?": pre_human_count is not set before
# the setup node, so the count test of the immediate transfer was false
def test_a_person_at_once_is_transferred_from_the_opening():
    import copy
    import json

    from fb.generate import build
    from fb.openapi_tools import parse_openapi
    from fb.validate import validate
    from spec_fixtures import SPEC_A

    from conftest import KIT

    tools = parse_openapi(json.loads((KIT / "tools" / "openapi.json").read_text()))
    spec = copy.deepcopy(SPEC_A)
    spec["cfg"] = {**spec["cfg"], "neg": {**spec["cfg"]["neg"], "human_requests_to_transfer": 1}}
    wf, policy = build(spec, tools)
    assert validate(wf, tools, policy).ok
    t = next(x for x in wf["nodes"]["opening"]["transitions"] if x["target_node"] == "pre_transfer")
    plain = {"variable": "who1", "operator": "equals", "operand": "wants_human"}
    assert plain in t["condition"]["conditions"]
    # the brief's "ask once whether you can help" (count 2) keeps the count test only
    wf2, _ = build(copy.deepcopy(SPEC_A), tools)
    t2 = next(
        x for x in wf2["nodes"]["opening"]["transitions"] if x["target_node"] == "pre_transfer"
    )
    if (SPEC_A["cfg"]["neg"].get("human_requests_to_transfer") or 1) > 1:
        assert plain not in t2["condition"]["conditions"]


def _built(neg_extra):
    import copy
    import json

    from fb.generate import build
    from fb.openapi_tools import parse_openapi
    from fb.validate import validate
    from spec_fixtures import SPEC_A

    from conftest import KIT

    tools = parse_openapi(json.loads((KIT / "tools" / "openapi.json").read_text()))
    spec = copy.deepcopy(SPEC_A)
    spec["cfg"] = {**spec["cfg"], "neg": {**spec["cfg"]["neg"], **neg_extra}}
    wf, policy = build(spec, tools)
    assert validate(wf, tools, policy).ok
    return wf


def _targets(wf, node):
    return [t["target_node"] for t in wf["nodes"][node]["transitions"]]


# F-02 / F-03 / R06 (brief F: "Flush notes only at the final disposition, then transfer"): the
# notes were written after transfer_to_agent
def test_notes_on_a_transfer_come_before_it():
    wf = _built({"notes": True, "notes_on_transfer": True})
    decide = [t for t in wf["nodes"]["decide"]["transitions"] if "transfer" in t["description"]]
    assert decide[0]["target_node"] == "wrap"
    assert _targets(wf, "wrap") == ["note", "transfer", "goodbye"]
    assert "transfer" in _targets(wf, "after_note")
    assert "wrap" not in _targets(wf, "after_transfer")


def test_a_brief_without_notes_on_a_transfer_keeps_the_old_order():
    wf = _built({"notes": True, "notes_on_transfer": False})
    decide = [t for t in wf["nodes"]["decide"]["transitions"] if "transfer" in t["description"]]
    assert decide[0]["target_node"] == "transfer"
    assert "transfer" not in _targets(wf, "wrap")


# F-03: a product with no reason of its own was noted F_REFUSED on a transfer, not F_TRANSFERRED
def test_an_open_product_is_noted_transferred():
    cfg = {
        **CFG,
        "neg": {
            **CFG["neg"],
            "notes": True,
            "notes_on_transfer": True,
            "reasons": {**(CFG["neg"].get("reasons") or {}), "transferred": "transferred"},
        },
    }
    human = {"kind": "human", "text": "Put me through to a person now."}
    r = simulate(CASES["PUB-13"], [human, {"kind": "no", "text": "No."}], cfg)
    reasons = [n["reason"] for n in r.ledger.get("notes") or []]
    assert reasons and all(x == "transferred" for x in reasons), (r.says, r.ledger)


# R17 (brief D): "only on the 23rd" (after the deadline) said again and again to "The latest
# date I can record is 22 October. Could you pay 900.00 on that day?" got that same offer nine
# times; the late day said again is a no to it, then the attempt plan, never the offer again
def test_a_late_day_said_again_is_a_no_to_the_deadline_offer():
    late = {"kind": "offer", "date": "30 November", "text": "I can only pay on 30 November."}
    r = simulate(CASES["PUB-01"], [late, late, late, late, late, late], CFG_A)
    assert "latest date" in r.says[1], r.says
    assert sum("latest date" in s for s in r.says) == 1, r.says
    assert r.ledger.get("promises") in ([], None), r.says


# F-02 (brief F: hardship -> sympathy and immediate SUPPORT transfer "even if a voluntary amount
# appears in the same turn"): "I lost my job yesterday, but I could pay three hundred on the
# card today" was an offer; persuasion went on and the transfer was GENERAL / human
def test_hardship_with_an_amount_transfers_when_the_brief_says_so():
    cfg = {**CFG, "neg": {**CFG["neg"], "hardship": "transfer", "hardship_queue": "support"}}
    said = {
        "kind": "offer",
        "amount": "three hundred",
        "date": "today",
        "product": "p1",
        "text": "I'm afraid I can't. I lost my job yesterday, but I could pay three hundred on "
        "the card today.",
    }
    r = simulate(CASES["PUB-13"], [said], cfg)
    assert r.vars.get("neg_action") == "transfer", r.says
    assert r.ledger["transfers"] == [{"queue": "support"}], r.ledger
    assert promises(r) == []


def test_hardship_with_an_amount_is_still_recorded_where_the_brief_allows_it():
    # Brief A rule 32: "If the customer still names a date or an amount themselves, record"
    said = {
        "kind": "offer",
        "amount": "200",
        "date": "today",
        "text": "I lost my job, but I can pay 200 today.",
    }
    r = simulate(CASES["PUB-01"], [said], CFG_A)
    assert r.vars.get("neg_action") != "transfer", r.says


# C-05 (brief C): a time in the first message of a third party was turned down by the model
# itself, so the code never counted it and a second invalid time got a third ask
def test_the_third_party_model_does_not_judge_a_time_itself():
    wf = _built({})
    assert (
        "Never decide yourself whether a day or time" in wf["nodes"]["third_party"]["task_message"]
    )
