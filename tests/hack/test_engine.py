"""The deterministic half of the agent on the public cases: replies as extraction values,
code-node bodies, local bank, kit scorer."""

import json

import pytest
from fb.harness.engine_sim import simulate

from conftest import KIT

CASES = {c["id"]: c for c in json.loads((KIT / "public-test-set.json").read_text())}

# What a spec extractor should produce for Brief A + B (the generator turns it into this).
CFG = {
    "holidays": ["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"],
    "workdays": [0, 1, 2, 3, 4],
    "bands": [
        {
            "name": "early",
            "min_days_late": 1,
            "max_days_late": 7,
            "deadline_business_days": 12,
            "deadline_unit": "business",
            "attempts": 1,
        },
        {
            "name": "late",
            "min_days_late": 8,
            "max_days_late": 45,
            "deadline_business_days": 7,
            "deadline_unit": "business",
            "attempts": 2,
        },
    ],
    "attempt1": {"rule": "last_business_day_of_week"},
    "callback": {"start": "10:00", "end": "19:00"},
    "floor_field": {
        "credit_card": "minimum_payment",
        "personal_loan": "instalment_amount",
        "car_loan": "instalment_amount",
    },
    "product_names": {
        "credit_card": "credit card ending {card_last4}",
        "personal_loan": "personal loan",
        "car_loan": "car loan",
    },
    "neg": {
        "attempt_plan": [
            {"amount": "current", "date": "attempt1"},
            {"amount": "floor", "date": "deadline"},
        ],
        "outcomes": {
            "promise": "promise",
            "paid": "paid",
            "transferred": "transferred",
            "no_promise": "no_promise",
        },
        "reasons": {
            "refused": "refused",
            "hardship": "hardship",
            "dispute": "dispute",
            "service_error": "service_error",
            "paid": "paid",
        },
        "queues": {"restructuring": "restructuring", "human": "general", "support": "support"},
        "transfer_reasons": {
            "restructuring": "restructuring",
            "human": "human",
            "support": "support",
        },
        "notes": True,
        # Every policy knob, set explicitly (A42, A45, A37, A32, B closing, B10, A21, A26/27).
        "human_requests_to_transfer": 2,
        "stop_requests_to_close": 2,
        "promise_attempts": 2,
        "record_failure": "continue",
        "record_failure_queue": "support",
        "hardship": "offer_support",
        "hardship_queue": "support",
        "no_promise_reason": "first_product",
        "reason_priority": ["dispute", "hardship", "service_error", "refused"],
        "notes_on_transfer": False,
        "vague_date_asks": 1,
        "floor_offers": 1,
        "lines": {
            "today_question": "Can you make this payment today?",
            "confirm_question": "Do you confirm?",
            "recorded": "You will receive a text message confirming this promise.",
            "paid": "Payments can take one business day to appear.",
        },
    },
}
CFG_A = {**CFG, "neg": {**CFG["neg"], "notes": False}}

Y = {"kind": "yes"}
N = {"kind": "no"}
BYE = {"kind": "unclear"}

SCENARIOS = {
    "PUB-01": [Y, Y, BYE],
    "PUB-04": [
        {"kind": "offer", "date": "the sixth of November"},
        {"kind": "question"},
        Y,
        Y,
        BYE,
    ],
    "PUB-05": [{"kind": "hardship"}, {"kind": "question"}, N],
    "PUB-06": [
        {"kind": "too_much"},
        {"kind": "unclear"},
        {"kind": "offer", "date": "this Friday"},
        Y,
        BYE,
    ],
    "PUB-06b": [{"kind": "too_much"}, N, Y, Y, BYE],
    "PUB-07": [
        {"kind": "vague", "date": "the week after next"},
        {"kind": "offer", "date": "Monday the 26th"},
        Y,
        Y,
        BYE,
    ],
    "PUB-07b": [
        N,
        {"kind": "vague", "date": "the week after next"},
        {"kind": "offer", "date": "Monday the 26th"},
        Y,
        Y,
        BYE,
    ],
    "PUB-08": [{"kind": "paid"}],
    "PUB-09": [{"kind": "restructure"}],
    "PUB-10": [N, Y, Y, BYE],
    "PUB-11": [N, {"kind": "offer", "date": "this Friday", "all": True}, Y, BYE],
    "PUB-12": [{"kind": "offer", "date": "tomorrow", "product": "p1"}, N, N, N, Y, BYE],
    "PUB-13": [N, {"kind": "offer", "date": "30 October", "all": True}, Y, BYE],
    "PUB-13b": [{"kind": "offer", "date": "the 30th of October"}, Y, BYE],
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_public_case_through_engine(name):
    case = CASES[name[:6]]
    cfg = CFG if case["brief"] == "B" else CFG_A
    r = simulate(case, SCENARIOS[name], cfg)
    assert not r.code_errors, r.code_errors
    assert r.score["pass"], (r.score["failed"], r.says)


def test_attempt_counts_pub12():
    r = simulate(CASES["PUB-12"], SCENARIOS["PUB-12"], CFG)
    says = " | ".join(r.says)
    assert "1320.00 for the personal loan on Friday 16 October 2026" in says
    assert "440.00 for the personal loan on Friday 23 October 2026" in says
    assert "car loan on Friday 16 October 2026" in says


def test_pub13_readback_names_both_dates():
    r = simulate(CASES["PUB-13"], SCENARIOS["PUB-13"], CFG)
    rb = next(s for s in r.says if "Read back" in s)
    assert "30 October" in rb and "23 October" in rb


def test_paid_line_reaches_final_say():
    r = simulate(CASES["PUB-08"], SCENARIOS["PUB-08"], CFG_A)
    assert "one business day" in r.says[-1]


def test_floor_offered_once_with_rest_overdue():
    r = simulate(CASES["PUB-06"], SCENARIOS["PUB-06"], CFG_A)
    offers = [s for s in r.says if not s.startswith("Repeat")]
    assert sum("637.50" in s and "rest stays overdue" in s for s in offers) == 1
