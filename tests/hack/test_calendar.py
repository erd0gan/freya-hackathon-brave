import json

import pytest
from fb.nodecode import collections_lib as L
from fb.nodecode.embed import snippet

from conftest import KIT

# Brief A values, typed by hand for the test (the builder reads them from the brief).
CFG = {
    "holidays": ["2026-10-26", "2026-11-11", "2026-11-26", "2026-12-25", "2027-01-01"],
    "bands": [
        {
            "name": "early",
            "min_days_late": 1,
            "max_days_late": 7,
            "deadline_business_days": 12,
            "attempts": 1,
        },
        {
            "name": "late",
            "min_days_late": 8,
            "max_days_late": 45,
            "deadline_business_days": 7,
            "attempts": 2,
        },
    ],
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
}
NOW = "2026-10-14T11:00"


def test_brief_a_dates_match_public_cases():
    f = L.calendar_facts(NOW, CFG)
    assert f["deadline_late"] == "2026-10-23"  # PUB-04
    assert f["deadline_early"] == "2026-11-02"
    assert f["attempt1_date"] == "2026-10-16"  # PUB-06, PUB-10, PUB-11
    assert "2026-10-15" in f["callback_days"]  # PUB-02
    assert "2026-10-26 (holiday)" in f["date_table"]
    assert "[next Friday]" in f["date_table"].split("2026-10-23")[1].splitlines()[0]


def test_attempt1_on_friday_moves_to_next_week():
    assert L.calendar_facts("2026-10-16T09:00", CFG)["attempt1_date"] == "2026-10-23"


def test_promise_date_checks():
    dl = L.calendar_facts(NOW, CFG)["deadline_early"]
    assert (
        L.check_promise_date("2026-10-26", dl, NOW, CFG) == "not_business_day:2026-10-27"
    )  # PUB-07
    assert L.check_promise_date("2026-11-06", "2026-10-23", NOW, CFG) == "after_deadline:2026-10-23"
    assert L.check_promise_date("2026-10-13", dl, NOW, CFG) == "past"
    assert L.check_promise_date("2026-10-30", dl, NOW, CFG) == "ok"


def test_products_from_public_case():
    cases = {c["id"]: c for c in json.loads((KIT / "public-test-set.json").read_text())}
    prods = [dict(p, product_id=f"p{i + 1}") for i, p in enumerate(cases["PUB-13"]["products"])]
    f = L.product_facts(L.load_products(json.dumps(prods)), NOW, CFG)
    assert (f["p1_band"], f["p1_deadline"]) == ("early", "2026-11-02")
    assert (f["p2_band"], f["p2_deadline"]) == ("late", "2026-10-23")
    assert f["p1_name"].startswith("credit card ending ")
    assert L.load_products(repr(prods)) == prods


@pytest.mark.parametrize(
    "said,iso",
    [
        ("14 March 1988", "1988-03-14"),
        ("March 14th, 1988", "1988-03-14"),
        ("14/03/1988", "1988-03-14"),
        ("1988-03-14", "1988-03-14"),
    ],
)
def test_dob(said, iso):
    assert L.normalize_dob(said) == iso


def test_snippet_runs_like_a_code_node():
    src = snippet("vars.update(calendar_facts(vars.get('now'), CFG))", CFG)
    env = {"vars": {"now": NOW}}
    exec(src, env)
    assert env["vars"]["deadline_late"] == "2026-10-23" and "code_error" not in env["vars"]
    env = {"vars": {"now": None}}
    exec(src, env)  # never raises
    assert env["vars"].get("calendar_state") in (None, "unknown")
