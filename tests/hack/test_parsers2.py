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


# ---- 2. dates: weeks, refused days, a time next to a day, month first with 'the'
@pytest.mark.parametrize(
    "text,want",
    [
        ("Friday next week", ("2026-10-23", "date")),
        ("next week Friday", ("2026-10-23", "date")),
        ("next week on Friday", ("2026-10-23", "date")),
        ("a week on Friday", ("2026-10-23", "date")),
        ("Friday week", ("2026-10-23", "date")),
        ("Friday after next", ("2026-10-30", "date")),
        ("the Friday after next", ("2026-10-30", "date")),
        ("a week from today", ("2026-10-21", "date")),
        ("in a week", ("2026-10-21", "date")),
        ("in a week's time", ("2026-10-21", "date")),
        ("in two weeks", ("2026-10-28", "date")),  # 14 days: a day, not a period
        ("in ten days", ("2026-10-24", "date")),
        ("in a couple of weeks", ("", "vague")),
        ("not Monday, Friday", ("2026-10-16", "date")),
        ("I can't do Monday but Friday works", ("2026-10-16", "date")),
        ("Monday? No, Friday.", ("2026-10-16", "date")),
        ("Monday is no good, Friday", ("2026-10-16", "date")),
        ("Monday, not Friday", ("2026-10-19", "date")),
        ("I can't pay on Friday", ("", "none")),
        ("I can't pay until Friday", ("2026-10-16", "date")),
        ("Friday at 3", ("2026-10-16", "date")),
        ("Friday at 10am", ("2026-10-16", "date")),
        ("tomorrow at 3", ("2026-10-15", "date")),
        ("today at half past two", ("2026-10-14", "date")),
        ("Friday the 23rd at 3pm", ("2026-10-23", "date")),
        ("the 23rd at 10", ("2026-10-23", "date")),
        ("December the third", ("2026-12-03", "date")),
        ("this coming Monday", ("2026-10-19", "date")),
        ("this coming Friday", ("2026-10-16", "date")),
        ("this Monday", ("2026-10-12", "date")),  # rule 22: this week's Monday (past)
        ("after the 20th", ("", "vague")),  # a period: ask once for the day (rule 21)
        ("before Friday", ("", "vague")),
        ("the day after tomorrow", ("2026-10-16", "date")),
        ("the week after next", ("", "vague")),
        ("next week", ("", "vague")),
    ],
)
def test_dates_weeks_and_refusals(text, want):
    assert NS["parse_spoken_date"](text, TODAY) == want


def test_week_forms_said_on_a_friday():
    fri = datetime.date(2026, 10, 16)
    assert date("Friday week", fri) == "2026-10-23"
    assert date("next Friday", fri) == "2026-10-23"
    assert date("Friday after next", fri) == "2026-10-30"
    assert date("this coming Friday", fri) == "2026-10-23"


# ---- 3. dates of birth and years in words, corrections, pieces
@pytest.mark.parametrize(
    "said,iso",
    [
        ("fourteen March one nine eight eight", "1988-03-14"),
        ("fourteenth March 1988", "1988-03-14"),
        ("March the fourteenth 1988", "1988-03-14"),
        ("the fourteenth of March nineteen eighty-eight", "1988-03-14"),
        ("the fourteenth of March, nineteen eighty-eight", "1988-03-14"),
        ("14 March, sorry, 15 March 1988", "1988-03-15"),
        ("1988, March 14th", "1988-03-14"),
        ("03/14/1988", "1988-03-14"),  # a month over 12: said month first
        ("14 March nineteen oh five", "1905-03-14"),
        ("14 March two thousand and five", "2005-03-14"),
        ("14 March 88", "1988-03-14"),
        ("14/03/88", "1988-03-14"),
        # the year is not whole: unreadable for the bank, not a wrong answer
        ("14 March 19", "incomplete"),
        ("14 March 14", "incomplete"),
        ("14 March", "incomplete"),
        # unchanged
        ("14 March 1988", "1988-03-14"),
        ("March 14th, 1988", "1988-03-14"),
        ("14/03/1988", "1988-03-14"),
        ("1988-03-14", "1988-03-14"),
    ],
)
def test_normalize_dob_spoken(said, iso):
    assert NS["normalize_dob"](said) == iso


def test_normalize_dob_month_first_order():
    assert NS["normalize_dob"]("03/14/1988", "MDY") == "1988-03-14"
    assert NS["normalize_dob"]("14/03/1988", "MDY") == "1988-03-14"
    assert NS["normalize_dob"]("04/03/1988", "MDY") == "1988-04-03"


@pytest.mark.parametrize(
    "said,want",
    [
        ("two thousand and five", "2005"),
        ("two thousand", "2000"),
        ("two thousand and twelve", "2012"),
        ("nineteen eighty eight", "1988"),
        ("nineteen oh five", "1905"),
        ("four four seven one", "4471"),
        ("forty-four seventy-one", "4471"),
        ("double four seven one", "4471"),
        ("four four seven one, no, four four seven two", "4472"),
    ],
)
def test_digits_from_speech_whole_numbers(said, want):
    assert NS["digits_from_speech"](said) == want


def test_identity_hook_reads_a_spoken_date_of_birth():
    from fb.generate import identity_hooks

    spec = {"identity": {"questions": [{"field": "date_of_birth", "kind": "date"}]}}
    pre, _post = identity_hooks(spec)
    for said, last, want in [
        ("the fourteenth of March, nineteen eighty-eight", "eighty-eight", "1988-03-14"),
        ("14 March, sorry, 15 March 1988", "15 March 1988", "1988-03-15"),
        ("14 March 19", "14 March 19", "incomplete"),
    ]:
        env = {"config": {"body": {"field": "date_of_birth", "answer": said}}}
        env["vars"] = {"id_last": last}
        exec(pre, env)
        assert env["config"]["body"]["answer"] == want, said
