"""Behaviour of the code nodes under the Northwind spec (a real model extraction of the raw
material in tests/metamorphic/northwind). No practice bank exists for it, so this drives the
engine directly. Expected dates are worked out by hand from the Northwind material after its memo:
holidays 2026-10-12, 2026-10-30, 2026-11-02, 2026-12-25; stage 1 (1-14 days) deadline 10th
business day, 1 attempt; stage 2 (15-60) 5th business day, 2 attempts; attempt 1 = full arrears on
the 3rd business day; attempt 2 = minimum on the deadline."""

import json
from pathlib import Path

from fb.nodecode.embed import namespace
from fb.spec_extract import to_generator

NS = namespace()
SPEC = json.loads(
    (Path(__file__).parent / "fixtures" / "spec_northwind_extracted.json").read_text(
        encoding="utf-8"
    )
)
CFG = to_generator(SPEC)["cfg"]
NEG = CFG["neg"]
NOW = "2026-10-14T11:00"
PRODUCTS = [
    {
        "account_ref": "A1",
        "product": "overdraft",
        "account_name": "your overdraft",
        "arrears": 300,
        "days_in_arrears": 20,
        "min_due": 50,
    },
    {
        "account_ref": "A2",
        "product": "auto_loan",
        "account_name": "your car finance",
        "arrears": 800,
        "days_in_arrears": 5,
        "monthly_payment": 200,
    },
]


def start():
    pf = NS["product_facts"](PRODUCTS, NOW, CFG)
    return pf, NS["new_state"](pf, NEG, NOW)


def step(st, kind, **kw):
    cal = NS["calendar_facts"](NOW, CFG)
    NS["step"](st, {"kind": kind, **kw}, NEG, cal, NOW, CFG)
    return st


def test_renamed_product_fields_and_stages():
    pf, _ = start()
    assert (pf["p1_id"], pf["p1_name"], pf["p1_past_due"], pf["p1_floor"]) == (
        "A1",
        "your overdraft",
        "300.00",
        "50.00",
    )
    assert (pf["p1_deadline"], pf["p1_attempts"]) == ("2026-10-21", "2")
    assert (pf["p2_id"], pf["p2_floor"], pf["p2_deadline"], pf["p2_attempts"]) == (
        "A2",
        "200.00",
        "2026-10-28",
        "1",
    )


def test_attempt_plan_after_the_memo():
    _, st = start()
    step(st, "no")
    assert "300.00 for your overdraft on Monday 19 October 2026" in st["say"]
    step(st, "no")
    assert "50.00" in st["say"] and "Wednesday 21 October 2026" in st["say"]
    step(st, "no")  # overdraft refused -> car finance, attempt 1 only
    assert "800.00" in st["say"] and "Monday 19 October 2026" in st["say"]
    step(st, "no")
    assert st["action"] == "finish"
    assert (st["final_outcome"], st["final_reason"]) == ("NO_PTP", "DECLINED")
    assert st["notes"] == [["A1", "DECLINED"], ["A2", "DECLINED"]]


def test_most_serious_note_decides_the_reason():
    _, st = start()
    step(st, "dispute", product=1)  # the car finance is disputed, the overdraft goes on
    for _ in range(3):
        step(st, "no")
    assert (st["final_outcome"], st["final_reason"]) == ("NO_PTP", "DISPUTE")


def test_person_transfers_at_once_and_notes_are_kept():
    _, st = start()
    step(st, "human")
    assert st["action"] == "transfer" and st["transfer_queue"] == "SERVICE"
    assert (st["final_outcome"], st["final_reason"]) == ("XFER", "AGENT")
    assert st["notes"] == [["A1", "DECLINED"], ["A2", "DECLINED"]]


def test_hardship_transfers_at_once():
    _, st = start()
    step(st, "hardship")
    assert st["transfer_queue"] == "HARDSHIP_TEAM"
    assert (st["final_outcome"], st["final_reason"]) == ("XFER", "HARDSHIP")


def test_callback_window_two_business_days_until_five():
    f = NS["calendar_facts"]("2026-10-14T16:30", CFG)
    assert f["callback_days"] == "2026-10-14,2026-10-15,2026-10-16"
    assert "to 17:00" in f["callback_window_text"]
