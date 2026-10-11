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


# ---- 4. amounts said like prices, pounds and pence, day words next to an amount
@pytest.mark.parametrize(
    "text,want",
    [
        # as said, without a hint (unchanged)
        ("three fifty", 3.5),
        ("four twenty", 4.2),
        ("one twenty six", 1.26),
        ("twelve fifty", 12.5),
        ("six hundred thirty seven fifty", 637.5),
        ("210 pounds 50", 210.5),
        ("210 pounds and 50 pence", 210.5),
        ("two hundred pounds and five pence", 200.05),
        ("100 and 50 pence", 100.5),
        ("fifty pence", 0.5),
        ("300 on the 20th", 300.0),
        ("300 on the twentieth of October", 300.0),
        ("100 pounds on Friday the 23rd", 100.0),
        ("200 tomorrow", 200.0),
        ("50 pounds a week", 50.0),
        ("200 at 3pm", 200.0),
        ("two grand", 2000.0),
        ("2 grand", 2000.0),
        ("1.5k", 1500.0),
        ("a grand and a half", None),  # two numbers: unreadable, asked again
    ],
)
def test_amount_as_said(text, want):
    assert NS["parse_amount"](text) == want


@pytest.mark.parametrize(
    "text,lo,hi,want",
    [
        ("three fifty", 200, 637.5, 350.0),
        ("four twenty", 200, 637.5, 420.0),
        ("one twenty six", 100, 637.5, 126.0),
        ("twelve fifty", 1000, 2125.1, 1250.0),
        ("three hundred, no wait, three fifty", 200, 637.5, 350.0),
        ("three fifty", 400, 637.5, 3.5),  # 350 is below the floor too: as said
        ("twelve fifty", 200, 637.5, 12.5),  # 1250 is over the full amount: as said
        ("three fifty", None, None, 3.5),
        ("three fifty", 200, None, 350.0),
        ("six hundred thirty seven fifty", 200, 637.5, 637.5),
        ("three pounds fifty", 200, 637.5, 3.5),  # pounds said: no other reading
        ("350", 200, 637.5, 350.0),
    ],
)
def test_amount_price_style_with_a_range(text, lo, hi, want):
    assert NS["parse_amount"](text, lo, hi) == want


# ---- 5. clock times: past / to, British 'half five', corrections; a day number is no time
@pytest.mark.parametrize(
    "text,want",
    [
        ("ten to five", "16:50"),
        ("twenty past ten", "10:20"),
        ("ten past eleven", "11:10"),
        ("five past two", "14:05"),
        ("ten to eleven", "10:50"),
        ("quarter to one", "12:45"),
        ("quarter past two", "14:15"),
        ("half five", "17:30"),
        ("half past ten", "10:30"),
        ("twenty five to three", "14:35"),
        ("4, no, 5", "17:00"),
        ("midday", "12:00"),
        ("noon", "12:00"),
        ("tomorrow, the 15th", ""),
        ("15th of October", ""),
        ("the fifteenth", ""),
        ("Thursday the 15th at 11", "11:00"),
        ("October 15 at 2", "14:00"),
        ("at 11", "11:00"),
        ("11 am", "11:00"),
        ("eleven o'clock", "11:00"),
        ("from 2 to 4", "14:00"),
        # unchanged
        ("nine am", "09:00"),
        ("eight pm", "20:00"),
        ("two thirty in the afternoon", "14:30"),
        ("14:30", "14:30"),
        ("eleven", "11:00"),
    ],
)
def test_parse_spoken_time(text, want):
    assert NS["parse_spoken_time"](text) == want


def test_callback_day_text_with_a_day_number_is_no_time():
    from fb.nodecode import bodies
    from test_fixes import run

    def cb(day, time):
        v = {"now": "2026-10-14T10:00"}
        run(bodies.SETUP_TP, v)
        v["cb_day_text"], v["cb_time_text"] = day, time
        run(bodies.CB_CHECK, v)
        return v

    v = cb("Thursday the 15th at 11", "")
    assert (v["cb_state"], v["cb_date"], v["cb_time"]) == ("ok", "2026-10-15", "11:00")
    v = cb("tomorrow, the 15th", "")
    assert v["cb_state"] == "partial" and v["cb_pending_time"] == ""


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


# ---- 6. find_products: the list and field names of the brief, not Brief A's
RENAMED = {
    "products_list": "accounts",
    "product_fields": {"past_due": "amount_overdue", "days_late": "overdue_days"},
    "floor_field": {"loan": "min_pay"},
}
FLAT = {
    "get_account_response.data.accounts[0].product_id": "L-1",
    "get_account_response.data.accounts[0].amount_overdue": "420.50",
    "get_account_response.data.accounts[0].overdue_days": "12",
    "get_account_response.data.accounts[0].min_pay": "200",
    "get_account_response.data.accounts[1].product_id": "L-2",
    "get_account_response.data.accounts[1].amount_overdue": "99",
}


def test_find_products_flattened_fields_with_renamed_names():
    got = NS["find_products"](FLAT, RENAMED)
    assert got == [
        {"product_id": "L-1", "amount_overdue": 420.5, "overdue_days": 12.0, "min_pay": 200.0},
        {"product_id": "L-2", "amount_overdue": 99.0},
    ]
    # the list name may also sit in product_fields
    assert NS["find_products"](FLAT, {"product_fields": {"list": "accounts"}})[0]["product_id"]
    # Brief A names stay the default
    assert NS["find_products"](FLAT, {}) == []
    brief_a = {"x.data.products[0].past_due": "5", "x.data.products[0].days_late": "3"}
    assert NS["find_products"](brief_a, {}) == [{"past_due": 5.0, "days_late": 3.0}]


def test_find_products_flattened_list_and_object_with_renamed_name():
    rows = [{"product_id": "L-1", "amount_overdue": 420.5}]
    v = {"get_account_response.data.accounts": repr(rows)}
    assert NS["find_products"](v, RENAMED) == rows
    v = {"products": '{"accounts": [{"product_id": "L-1"}]}'}
    assert NS["find_products"](v, RENAMED) == [{"product_id": "L-1"}]


def test_find_products_uses_the_code_node_cfg():
    from fb.nodecode.embed import snippet

    src = snippet("vars['found'] = repr(find_products(vars))", RENAMED)
    env = {"vars": dict(FLAT)}
    exec(src, env)
    assert "'L-2'" in env["vars"]["found"] and "code_error" not in env["vars"]
    env = {"vars": {"products": [{"product_id": "P"}]}}
    exec(snippet("vars['found'] = repr(find_products(vars))", {}), env)
    assert env["vars"]["found"] == "[{'product_id': 'P'}]"
