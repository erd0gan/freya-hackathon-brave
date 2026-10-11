"""Speech parsers against what the kit's customers say (kit/README.md: numbers in two parts,
self-corrections, amounts and dates in words; kit/tools/run_cases.py STYLES)."""

import datetime

import pytest
from fb.nodecode.embed import namespace

NS = namespace()
TODAY = datetime.date(2026, 10, 14)  # a Wednesday, the public cases' `now`


def date(text, today=TODAY):
    return NS["parse_spoken_date"](text, today)[0]


# ---- 1. corrections: the last value wins, separate numbers are never glued together
@pytest.mark.parametrize(
    "text,want",
    [
        ("the 21st, no, the 20th", "2026-10-20"),
        ("the 23rd, I meant the 22nd", "2026-10-22"),
        ("the 21st, make it the 20th", "2026-10-20"),
        ("the 21st, or rather the 20th", "2026-10-20"),
        ("the 21st, oops, the 20th", "2026-10-20"),
        ("the 21st, my bad, the 20th", "2026-10-20"),
        ("the 21st, sorry, I mean the 20th", "2026-10-20"),
        ("the 21st, no", "2026-10-21"),
    ],
)
def test_date_corrections(text, want):
    assert date(text) == want


@pytest.mark.parametrize(
    "text,want",
    [
        ("400, no, 450", 450.0),
        ("four hundred, no, four hundred and fifty", 450.0),
        ("No, 450.", 450.0),
        ("I can do 450, no problem", 450.0),
        ("200 or maybe 300", None),
        ("between 100 and 200", None),
        ("1,320", 1320.0),
        ("1 320", 1320.0),
        ("1,320.50", 1320.5),
        ("210 pounds and 50 pence", 210.5),
    ],
)
def test_amount_groups_are_not_glued(text, want):
    assert NS["parse_amount"](text) == want
