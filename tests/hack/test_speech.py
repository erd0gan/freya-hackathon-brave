import datetime

import pytest
from fb.nodecode.speech_lib import parse_amount, parse_spoken_date

WED = datetime.date(2026, 10, 14)  # the public cases' `now`
FRI = datetime.date(2026, 10, 16)


@pytest.mark.parametrize(
    "text,today,want",
    [
        ("this Friday", WED, ("2026-10-16", "date")),
        ("Friday", WED, ("2026-10-16", "date")),
        ("next Friday", WED, ("2026-10-23", "date")),
        ("next Friday", FRI, ("2026-10-23", "date")),
        ("this Friday", FRI, ("2026-10-16", "date")),
        ("tomorrow", WED, ("2026-10-15", "date")),
        ("today", WED, ("2026-10-14", "date")),
        ("tomorrow morning around eleven", WED, ("2026-10-15", "date")),
        ("Monday the 26th", WED, ("2026-10-26", "date")),
        ("the 26th", WED, ("2026-10-26", "date")),
        ("on the 30th of October", WED, ("2026-10-30", "date")),
        ("30 October", WED, ("2026-10-30", "date")),
        ("October 30th", WED, ("2026-10-30", "date")),
        ("6 November", WED, ("2026-11-06", "date")),
        ("the twenty-second of October", WED, ("2026-10-22", "date")),
        ("the thirty first of October", WED, ("2026-10-31", "date")),
        ("the 21st, sorry, I mean the 20th", WED, ("2026-10-20", "date")),
        ("the 5th", WED, ("2026-11-05", "date")),
        ("2026-10-23", WED, ("2026-10-23", "date")),
        ("23/10", WED, ("2026-10-23", "date")),
        ("the week after next", WED, ("", "vague")),
        ("when my salary comes", WED, ("", "vague")),
        ("end of the month", WED, ("", "vague")),
        ("next week", WED, ("", "vague")),
        ("I don't know", WED, ("", "none")),
        ("", WED, ("", "none")),
    ],
)
def test_parse_spoken_date(text, today, want):
    assert parse_spoken_date(text, today) == want


@pytest.mark.parametrize(
    "text,want",
    [
        ("637.50", 637.5),
        ("637,50", 637.5),
        ("2,125.10", 2125.1),
        ("420", 420.0),
        ("four hundred and twenty", 420.0),
        ("four hundred and fifty-five", 455.0),
        ("six hundred and thirty-seven pounds fifty", 637.5),
        ("six hundred thirty seven fifty", 637.5),
        ("two thousand one hundred and twenty-five pounds and ten pence", 2125.1),
        ("a hundred", 100.0),
        ("three hundred, sorry, three hundred and fifty", 350.0),
        ("nothing", None),
        ("", None),
    ],
)
def test_parse_amount(text, want):
    assert parse_amount(text) == want
