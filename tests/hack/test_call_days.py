"""The final runs on several call dates. Expected dates below are worked out by hand from the
Brief A rules (business days Mon-Fri minus 2026-10-26, 2026-11-11, 2026-11-26, 2026-12-25,
2027-01-01; late deadline = 7th business day after today, early = 12th; attempt 1 = this week's
last business day, next week's when today is that day)."""

import datetime

import pytest
from fb.nodecode.embed import namespace
from test_engine import CFG_A

NS = namespace()


def facts(now):
    return NS["calendar_facts"](now, CFG_A)


@pytest.mark.parametrize(
    "now, attempt1, late, early",
    [
        # Wednesday, the public cases' day
        ("2026-10-14T11:00", "2026-10-16", "2026-10-23", "2026-11-02"),
        # Friday: attempt 1 moves to next week's Friday; the 26th holiday is skipped
        ("2026-10-16T11:00", "2026-10-23", "2026-10-28", "2026-11-04"),
        # Saturday: today is not a business day, this week's Friday has passed
        ("2026-10-17T11:00", "2026-10-23", "2026-10-28", "2026-11-04"),
        # Thursday before Christmas: Friday is a holiday, so today is the last business day
        ("2026-12-24T11:00", "2026-12-31", "2027-01-06", "2027-01-13"),
        # month end, a Friday
        ("2026-10-30T11:00", "2026-11-06", "2026-11-10", "2026-11-18"),
    ],
)
def test_dates_on_other_call_days(now, attempt1, late, early):
    f = facts(now)
    assert (f["attempt1_date"], f["deadline_late"], f["deadline_early"]) == (attempt1, late, early)


def test_this_and_next_friday_said_on_a_friday():
    fri = datetime.date(2026, 10, 16)
    assert NS["parse_spoken_date"]("this Friday", fri)[0] == "2026-10-16"
    assert NS["parse_spoken_date"]("next Friday", fri)[0] == "2026-10-23"


def test_named_holiday_moves_to_next_business_day_within_deadline():
    # said on Friday 16 Oct: Monday 26 is a holiday, deadline (late) is 28 Oct
    assert NS["check_promise_date"]("2026-10-26", "2026-10-28", "2026-10-16T11:00", CFG_A) == (
        "not_business_day:2026-10-27"
    )


def test_weekend_date_past_deadline_offers_deadline():
    assert NS["check_promise_date"]("2026-10-24", "2026-10-23", "2026-10-14T11:00", CFG_A) == (
        "after_deadline:2026-10-23"
    )


def test_callback_window_on_saturday_has_no_today():
    f = facts("2026-10-17T11:00")
    assert f["callback_days"] == "2026-10-19"


def test_callback_window_late_in_the_day():
    f = facts("2026-10-14T18:30")
    assert f["callback_days"] == "2026-10-14,2026-10-15"
    # a callback must be later than now: today's first slot is the next minute (CAL-7)
    assert "from 18:31 to 19:00" in f["callback_window_text"]
    f = facts("2026-10-14T19:30")
    assert f["callback_days"] == "2026-10-15"


def test_callback_before_christmas_skips_the_holiday():
    f = facts("2026-12-24T12:00")
    assert f["callback_days"] == "2026-12-24,2026-12-28"
