"""Reviewer findings: spoken decimals, callback corrections, policy zero values."""

from fb.nodecode import bodies
from fb.nodecode.embed import namespace
from test_fixes import run

NS = namespace()


# ---- 1. spoken decimals: the words after 'point' are a digit sequence
def test_point_reads_digits():
    pa = NS["parse_amount"]
    assert pa("two hundred point zero five") == 200.05
    assert pa("two hundred point five") == 200.5
    assert pa("two hundred point two five") == 200.25
    assert pa("two hundred point twenty five") == 200.25
    assert pa("four hundred and twenty point five zero") == 420.50
    assert pa("200 point 05") == 200.05
    assert pa("two hundred point five pounds") == 200.5


def test_point_keeps_other_amounts():
    pa = NS["parse_amount"]
    assert pa("two hundred pounds and five pence") == 200.05
    assert pa("four hundred and twenty pounds fifty") == 420.50
    assert pa("six hundred and thirty-seven fifty") == 637.50
    assert pa("at this point I can pay 200") == 200.0


# ---- 2. a callback correction keeps the part that was right
def cb_at(now, *turns):
    v = {"now": now}
    run(bodies.SETUP_TP, v)
    for day, time in turns:
        v["cb_day_text"], v["cb_time_text"] = day, time
        run(bodies.CB_CHECK, v)
    return v


def test_callback_time_outside_keeps_the_day():
    # Wednesday 10:00; tomorrow at nine is before the window, then 'No, eleven.'
    v = cb_at("2026-10-14T10:00", ("tomorrow", "nine am"))
    assert v["cb_state"] == "outside"
    assert (v["cb_pending_date"], v["cb_pending_time"]) == ("2026-10-15", "")
    assert v["cb_say"].startswith("That time is not possible on that day.")
    v = cb_at("2026-10-14T10:00", ("tomorrow", "nine am"), ("", "eleven"))
    assert (v["cb_state"], v["cb_date"], v["cb_time"]) == ("ok", "2026-10-15", "11:00")
    assert v["cb_tries"] == "1"


def test_callback_day_outside_keeps_the_time():
    # Saturday is not a callback day; 11:00 is fine; then 'Tomorrow then.'
    v = cb_at("2026-10-14T10:00", ("saturday", "eleven"))
    assert v["cb_state"] == "outside"
    assert (v["cb_pending_date"], v["cb_pending_time"]) == ("", "11:00")
    assert v["cb_say"].startswith("That day is not possible")
    v = cb_at("2026-10-14T10:00", ("saturday", "eleven"), ("tomorrow", ""))
    assert (v["cb_state"], v["cb_date"], v["cb_time"]) == ("ok", "2026-10-15", "11:00")


def test_callback_kept_time_does_not_pick_a_day_alone():
    # after a rejected day, a turn without a day or a time must not book a default day
    v = cb_at("2026-10-14T10:00", ("saturday", "eleven"), ("", ""))
    assert v["cb_state"] == "partial"
    assert not v.get("cb_date")


def test_callback_both_wrong_clears_both_and_counts():
    v = cb_at("2026-10-14T10:00", ("saturday", "eight pm"))
    assert v["cb_state"] == "outside"
    assert (v["cb_pending_date"], v["cb_pending_time"]) == ("", "")
    v = cb_at("2026-10-14T10:00", ("saturday", "eight pm"), ("tomorrow", "nine am"))
    assert (v["cb_state"], v["cb_tries"]) == ("outside", "2")


def test_callback_today_past_time_keeps_today():
    v = cb_at("2026-10-14T15:00", ("today", "eleven"))
    assert v["cb_state"] == "outside"
    assert (v["cb_pending_date"], v["cb_pending_time"]) == ("2026-10-14", "")
    v = cb_at("2026-10-14T15:00", ("today", "eleven"), ("", "five pm"))
    assert (v["cb_state"], v["cb_date"], v["cb_time"]) == ("ok", "2026-10-14", "17:00")
