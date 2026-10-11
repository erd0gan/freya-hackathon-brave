"""Calendar findings of the private-final audit (05-calendar.md CAL-*, summary L, 03 HA-9 /
HA-15): another weekend, a week of holidays, other `now` formats, rules that change from a date,
deadlines counted from a date field. Expected dates are worked out by hand."""

import copy
import datetime

import pytest
from fb.nodecode.embed import namespace
from test_engine import CFG_A

NS = namespace()
D = datetime.date

# Sunday-Thursday working week (a Friday-Saturday weekend), no holidays
SUN_THU = dict(CFG_A, workdays=[6, 0, 1, 2, 3], holidays=[])


@pytest.fixture(autouse=True)
def english_after():
    yield
    NS["set_locale"](dict())


# ---- summary L / CAL-3 / HA-15: the week starts after the weekend
@pytest.mark.parametrize(
    "workdays,want",
    [
        ([0, 1, 2, 3, 4], 0),  # Monday-Friday
        ([6, 0, 1, 2, 3], 6),  # Sunday-Thursday
        ([0, 1, 2, 3, 4, 5], 0),  # Monday-Saturday
        ([5, 6, 0, 1, 2, 3], 5),  # Saturday-Thursday (Friday off)
        ([0, 1, 2, 3, 4, 5, 6], 0),
        ([], 0),
    ],
)
def test_week_start_from_the_workdays(workdays, want):
    assert NS["week_start"](dict(workdays=workdays)) == want


def test_week_start_given():
    assert NS["week_start"](dict(workdays=[0, 1, 2, 3, 4], week_start=6)) == 6
    assert NS["week_start"](dict(workdays=[0, 1, 2, 3, 4], week_start=None)) == 0


def test_attempt1_in_a_sunday_thursday_week():
    # CAL-3: Thursday 1 Oct is the week's last business day: next week's Thursday (not Sunday)
    f = NS["calendar_facts"]("2026-10-01T11:00", SUN_THU)
    assert f["attempt1_date"] == "2026-10-08"
    assert f["last_business_day_this_week"] == "2026-10-01"
    # HA-15: Wednesday 14 Oct: this week's Thursday
    f = NS["calendar_facts"]("2026-10-14T11:00", SUN_THU)
    assert f["attempt1_date"] == "2026-10-15"
    assert "Sunday 18 October 2026 = 2026-10-18 (business day) [next Sunday]" in f["date_table"]


def test_monday_week_unchanged():
    f = NS["calendar_facts"]("2026-10-14T11:00", CFG_A)
    assert (f["attempt1_date"], f["last_business_day_next_week"]) == ("2026-10-16", "2026-10-23")


def test_this_and_next_weekday_in_a_sunday_thursday_week():
    NS["set_locale"](SUN_THU)
    wed = D(2026, 10, 14)
    p = NS["parse_spoken_date"]
    assert p("this Sunday", wed) == ("2026-10-11", "date")  # the week began on Sunday 11th
    assert p("next Sunday", wed) == ("2026-10-18", "date")
    assert p("Sunday", wed) == ("2026-10-18", "date")
    assert p("next Friday", wed) == ("2026-10-23", "date")
    NS["set_locale"](CFG_A)
    assert p("next Sunday", wed) == ("2026-10-25", "date")  # Monday week: as before


# ---- CAL-4: a week of holidays never leaves attempt 1 empty
def test_attempt1_after_a_holiday_block():
    hol = [str(D(2026, 12, 24) + datetime.timedelta(days=i)) for i in range(12)]
    cfg = dict(CFG_A, holidays=hol)
    f = NS["calendar_facts"]("2026-12-23T11:00", cfg)
    assert f["attempt1_date"] == "2027-01-08"
    assert f["attempt1_date_text"] == "Friday 8 January 2027"


# ---- CAL-5 / CAL-6: `now` formats
@pytest.mark.parametrize(
    "now,want",
    [
        ("2026-10-14T11:00", "2026-10-14 11:00"),
        ("2026-10-14T11:00:00+03:00", "2026-10-14 11:00"),
        ("2026-10-14T11:00:00Z", "2026-10-14 11:00"),
        ("2026-10-14 11:00", "2026-10-14 11:00"),
        ("2026-10-14", "2026-10-14 00:00"),
        ("2026-10-14T9:30", "2026-10-14 09:30"),  # CAL-5: a single-digit hour
        ("2026-10-14 9:05", "2026-10-14 09:05"),
        ("14.10.2026 11:00", "2026-10-14 11:00"),  # CAL-6
        ("14/10/2026 11:00", "2026-10-14 11:00"),
        ("2026/10/14 11:00", "2026-10-14 11:00"),
        ("Wed, 14 Oct 2026 11:00:00 GMT", "2026-10-14 11:00"),
        ("14 Ekim 2026 11:00", "2026-10-14 11:00"),
        ("14 Şubat 2026", "2026-02-14 00:00"),
        ("2026-10-14T24:00", "2026-10-14 00:00"),
    ],
)
def test_now_formats(now, want):
    assert NS["parse_now"](now).strftime("%Y-%m-%d %H:%M") == want


@pytest.mark.parametrize("now", [None, "", "soon", "32.13.2026"])
def test_now_unreadable(now):
    assert NS["parse_now"](now) is None


def test_single_digit_hour_callback_window():
    # CAL-5: 09:30 is before the window: today from 10:00, not "today at 00:00"
    f = NS["calendar_facts"]("2026-10-14T9:30", CFG_A)
    assert f["now_time"] == "09:30"
    assert "from 10:00 to 19:00" in f["callback_window_text"]


# ---- CAL-7: the first callback slot today is later than now
def test_callback_window_starts_after_now():
    f = NS["calendar_facts"]("2026-10-14T18:59", CFG_A)
    assert f["callback_days"].startswith("2026-10-14,")
    assert "from 19:00 to 19:00" in f["callback_window_text"]
    f = NS["calendar_facts"]("2026-10-14T19:00", CFG_A)
    assert f["callback_days"] == "2026-10-15"


# ---- CAL-1 / summary G: a rule that changes from a date (CFG["dated"])
DATED = dict(
    copy.deepcopy(CFG_A),
    dated=[
        {"from": "2026-10-19", "set": {"callback.end": "17:30", "bands.1.attempts": 1}},
        {"from": "2026-10-21", "set": {"bands.1.deadline_business_days": 5}},
        {"from": "garbage", "set": {"callback.end": "08:00"}},
    ],
)


def test_dated_changes_apply_from_their_day():
    before = NS["prepare_cfg"](DATED, "2026-10-16T11:00")
    assert before["callback"]["end"] == "19:00" and before["bands"][1]["attempts"] == 2
    mid = NS["prepare_cfg"](DATED, "2026-10-19T09:00")
    assert mid["callback"]["end"] == "17:30" and mid["bands"][1]["attempts"] == 1
    assert mid["bands"][1]["deadline_business_days"] == 7
    after = NS["prepare_cfg"](DATED, "2026-10-21T11:00")
    assert after["bands"][1]["deadline_business_days"] == 5
    assert DATED["callback"]["end"] == "19:00"  # the built config is never changed
    f = NS["calendar_facts"]("2026-10-21T11:00", after)
    assert "to 17:30" in f["callback_window_text"]


def test_prepare_cfg_never_raises_and_sets_the_locale():
    assert NS["prepare_cfg"](dict(dated=[{"from": "2026-01-01", "set": {"a.0.b": 1}}]), "x")
    assert NS["prepare_cfg"](None, "2026-10-14") is None
    cfg = NS["prepare_cfg"](dict(lang="tr", fmt=dict(decimal=",")), "2026-10-14T11:00")
    assert NS["LOCALE"]["lang"] == "tr" and NS["LOCALE"]["fmt"] == dict(decimal=",")
    assert cfg["lang"] == "tr"
    # a bad path is skipped, the others still apply
    c = dict(
        callback=dict(end="19:00"),
        dated=[{"from": "2026-01-01", "set": {"x.5.y": 1, "callback.end": "18:00"}}],
    )
    assert NS["prepare_cfg"](c, "2026-10-14")["callback"]["end"] == "18:00"


# ---- CAL-10: holidays written another way still count
@pytest.mark.parametrize("h", ["20261026", "2026/10/26", "26.10.2026", "2026-10-26T00:00"])
def test_holiday_forms(h):
    cfg = NS["prepare_cfg"](dict(CFG_A, holidays=[h]), "2026-10-14T11:00")
    assert "2026-10-26 (holiday)" in NS["calendar_facts"]("2026-10-14T11:00", cfg)["date_table"]


# ---- CAL-11 / HA-9: date rules and deadlines
def test_rule_date():
    wed = D(2026, 10, 14)
    r = NS["rule_date"]
    assert r(wed, "next_business_day", CFG_A) == D(2026, 10, 15)
    assert r(wed, "nth_business_day:2", CFG_A) == D(2026, 10, 16)
    assert r(wed, "last_business_day_of_week", CFG_A) == D(2026, 10, 16)
    assert r(D(2026, 10, 16), "last_business_day_of_week", CFG_A) == D(2026, 10, 23)
    assert r(wed, "last_business_day_of_month", CFG_A) == D(2026, 10, 30)
    assert r(D(2026, 10, 31), "last_business_day_of_month", CFG_A) == D(2026, 11, 30)
    assert r(wed, "attempt1", CFG_A) == D(2026, 10, 16)
    assert r(wed, "deadline", CFG_A, "2026-10-23") == D(2026, 10, 23)
    assert r(wed, "last_business_day_of_month", CFG_A, "2026-10-23") == D(2026, 10, 23)
    assert r(D(2026, 10, 17), "today", CFG_A) == D(2026, 10, 19)
    assert r(wed, "whenever", CFG_A) is None


def test_attempt1_last_business_day_of_month():
    cfg = dict(CFG_A, attempt1={"rule": "last_business_day_of_month"})
    assert NS["calendar_facts"]("2026-10-14T11:00", cfg)["attempt1_date"] == "2026-10-30"


def test_deadline_from_a_date_field():
    wed = D(2026, 10, 14)
    band = dict(deadline_days=5, deadline_unit="calendar", deadline_from_field="due_date")
    assert NS["deadline_for"](wed, band, CFG_A, dict(due_date="2026-10-20")) == D(2026, 10, 23)
    band = dict(deadline_business_days=3, deadline_from_field="due_date")
    assert NS["deadline_for"](wed, band, CFG_A, dict(due_date="2026-10-20")) == D(2026, 10, 23)
    # a due date long past: never before today
    assert NS["deadline_for"](wed, band, CFG_A, dict(due_date="2026-09-01")) == wed
    # no such field: from today, as before
    assert NS["deadline_for"](wed, band, CFG_A, dict()) == D(2026, 10, 19)
    end = dict(deadline_unit="end_of_month")
    assert NS["deadline_for"](wed, end, CFG_A) == D(2026, 10, 30)


def test_product_deadline_from_its_field():
    cfg = copy.deepcopy(CFG_A)
    for b in cfg["bands"]:
        b.update(deadline_unit="end_of_month", deadline_from_field="statement_due")
    p = dict(CFG_A_PRODUCT, statement_due="2026-11-10")
    pf = NS["product_facts"]([p], "2026-10-14T11:00", cfg)
    assert pf["p1_deadline"] == "2026-11-30"


CFG_A_PRODUCT = dict(type="credit_card", product_id="p1", past_due=420, days_late=10)


# ---- HA-15: a bare hour is read in the callback window
def test_bare_hour_in_the_window():
    t = NS["parse_spoken_time"]
    assert t("three") == "15:00" and t("eight") == "08:00"  # 10-19 (English as before)
    assert t("eight", 13, 21) == "20:00"
    assert t("eight", "13:00", "21:00") == "20:00"
    NS["set_locale"](dict(callback=dict(start="13:00", end="21:00")))
    assert t("eight") == "20:00"
    assert t("eight in the morning") == "08:00"


# ---- CAL-9: rare spoken days
def test_rare_spoken_days():
    wed = D(2026, 10, 14)
    p = NS["parse_spoken_date"]
    assert p("a week from tomorrow", wed) == ("2026-10-22", "date")
    assert p("20-10-2026", wed) == ("2026-10-20", "date")
    assert p("tomorrow", wed) == ("2026-10-15", "date")


def test_business_days_from_today():
    # CAL-9: counted on the call's calendar, in either language
    wed = D(2026, 10, 14)
    p = NS["parse_spoken_date"]
    assert p("in three working days", wed) == ("2026-10-19", "date")
    assert p("in 3 business days", wed) == ("2026-10-19", "date")
    assert p("three business days from now", wed) == ("2026-10-19", "date")
    assert p("üç iş günü sonra", wed, lang="tr") == ("2026-10-19", "date")
    NS["prepare_cfg"](dict(CFG_A, holidays=["2026-10-19"]), "2026-10-14T11:00")
    assert p("in 3 business days", wed) == ("2026-10-20", "date")
    assert p("in 3 days", wed) == ("2026-10-17", "date")  # calendar days: as before
    NS["prepare_cfg"](SUN_THU, "2026-10-14T11:00")
    assert p("in two business days", wed) == ("2026-10-18", "date")  # Thursday, then Sunday


def test_business_days_in_a_code_node():
    from fb.nodecode.embed import snippet

    body = "vars['d'] = parse_spoken_date(vars['t'], parse_now(vars['now']).date())[0]\n"
    v = dict(t="in 3 business days", now="2026-10-14T11:00")
    exec(snippet(body, dict(CFG_A, holidays=["2026-10-19"])), dict(vars=v))
    assert v["d"] == "2026-10-20"


def test_far_business_day_is_not_cut_short():
    # CAL-10: the scan grows with N (it stopped at 400 days, so N = 300 and N = 1000 matched)
    wed = D(2026, 10, 14)
    nth = NS["nth_business_day_after"]
    assert nth(wed, 300, CFG_A) < nth(wed, 1000, CFG_A)
    assert nth(wed, 300, CFG_A).weekday() < 5
    assert nth(wed, 3, dict(CFG_A, workdays=[], holidays=[])) == D(2026, 10, 19)
