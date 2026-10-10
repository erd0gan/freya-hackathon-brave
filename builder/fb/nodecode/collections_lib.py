# Runs INSIDE Freya code nodes (code_exec sandbox: json, re, datetime) and in local tests.
# Rules for this file: stdlib only, never raise to the caller, and never two braces in a row
# (the platform may treat them as a template placeholder).
import datetime
import json
import re

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def parse_now(now):
    s = str(now or "").strip().replace(" ", "T")
    try:
        return datetime.datetime.fromisoformat(s[:16])
    except Exception:
        try:
            return datetime.datetime.fromisoformat(s[:10] + "T00:00")
        except Exception:
            return None


def say_date(d):
    return WEEKDAYS[d.weekday()] + " " + str(d.day) + " " + MONTHS[d.month - 1] + " " + str(d.year)


def is_business_day(d, cfg):
    workdays = cfg.get("workdays") or [0, 1, 2, 3, 4]
    return d.weekday() in workdays and d.isoformat() not in set(cfg.get("holidays") or [])


def next_business_day_on_or_after(d, cfg):
    for _ in range(60):
        if is_business_day(d, cfg):
            return d
        d = d + datetime.timedelta(days=1)
    return d


def nth_business_day_after(today, n, cfg):
    d = today
    count = 0
    for _ in range(400):
        d = d + datetime.timedelta(days=1)
        if is_business_day(d, cfg):
            count += 1
            if count >= n:
                return d
    return d


def last_business_day_of_week(d, cfg):
    monday = d - datetime.timedelta(days=d.weekday())
    best = None
    for i in range(7):
        day = monday + datetime.timedelta(days=i)
        if is_business_day(day, cfg):
            best = day
    return best


def band_for(days_late, cfg):
    try:
        n = int(float(days_late))
    except Exception:
        return None
    for b in cfg.get("bands") or []:
        lo = b.get("min_days_late")
        hi = b.get("max_days_late")
        if (lo is None or n >= lo) and (hi is None or n <= hi):
            return b
    return None


def calendar_facts(now, cfg):
    """Every date fact the agent needs, as strings (routing compares strings)."""
    out = {"calendar_state": "unknown"}
    dt = parse_now(now)
    if dt is None:
        return out
    today = dt.date()
    out["calendar_state"] = "known"
    out["today_iso"] = today.isoformat()
    out["today_text"] = say_date(today)
    out["now_time"] = dt.strftime("%H:%M")
    out["today_is_business_day"] = "yes" if is_business_day(today, cfg) else "no"
    this_last = last_business_day_of_week(today, cfg)
    next_last = last_business_day_of_week(today + datetime.timedelta(days=7), cfg)
    out["last_business_day_this_week"] = this_last.isoformat() if this_last else ""
    out["last_business_day_next_week"] = next_last.isoformat() if next_last else ""
    if this_last is not None and this_last > today:  # noqa: SIM108
        a1 = this_last
    else:
        a1 = next_last
    out["attempt1_date"] = a1.isoformat() if a1 else ""
    out["attempt1_date_text"] = say_date(a1) if a1 else ""
    for b in cfg.get("bands") or []:
        dl = nth_business_day_after(today, int(b.get("deadline_business_days") or 0), cfg)
        key = "deadline_" + str(b.get("name"))
        out[key] = dl.isoformat()
        out[key + "_text"] = say_date(dl)
    # Callback window (third party).
    cb = cfg.get("callback") or {}
    start = str(cb.get("start") or "10:00")
    end = str(cb.get("end") or "19:00")
    days = []
    if is_business_day(today, cfg) and out["now_time"] < end:
        days.append(("today", today, max(start, out["now_time"])))
    nb = nth_business_day_after(today, 1, cfg)
    days.append(("next business day", nb, start))
    out["callback_window_text"] = " or ".join(
        name + " (" + say_date(d) + ", " + d.isoformat() + ") from " + s + " to " + end
        for name, d, s in days
    )
    out["callback_days"] = ",".join(d.isoformat() for _, d, _ in days)
    # A table of the coming days so the model never computes a date itself.
    monday = today - datetime.timedelta(days=today.weekday())
    rows = []
    for i in range(0, 28):
        d = today + datetime.timedelta(days=i)
        labels = []
        if i == 0:
            labels.append("today")
        if i == 1:
            labels.append("tomorrow")
        week = (d - monday).days // 7
        if week == 0:
            labels.append("this " + WEEKDAYS[d.weekday()])
        elif week == 1:
            labels.append("next " + WEEKDAYS[d.weekday()])
        status = (
            "business day"
            if is_business_day(d, cfg)
            else ("holiday" if d.isoformat() in set(cfg.get("holidays") or []) else "weekend")
        )
        rows.append(
            say_date(d)
            + " = "
            + d.isoformat()
            + " ("
            + status
            + ")"
            + (" [" + ", ".join(labels) + "]" if labels else "")
        )
    out["date_table"] = "\n".join(rows)
    return out


def load_products(raw):
    """get_account products arrive as a list, a JSON string or a python repr string."""
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, dict)]
    if isinstance(raw, str) and raw.strip():
        for loader in (
            json.loads,
            lambda s: json.loads(
                s.replace("'", '"')
                .replace("True", "true")
                .replace("False", "false")
                .replace("None", "null")
            ),
        ):
            try:
                v = loader(raw)
                if isinstance(v, list):
                    return [p for p in v if isinstance(p, dict)]
                if isinstance(v, dict) and isinstance(v.get("products"), list):
                    return [p for p in v["products"] if isinstance(p, dict)]
            except Exception:
                pass
    return []


def money(x):
    try:
        return "%.2f" % float(x)  # noqa: UP031
    except Exception:
        return str(x)


def product_facts(products, now, cfg):
    dt = parse_now(now)
    today = dt.date() if dt else None
    out = {
        "product_count": str(len(products)),
        "products_state": "known" if products else "unknown",
    }
    lines = []
    for i, p in enumerate(products):
        k = "p" + str(i + 1)
        ptype = str(p.get("type") or "")
        floor_field = (cfg.get("floor_field") or {}).get(ptype, "")
        floor = p.get(floor_field) if floor_field else None
        name_tpl = (cfg.get("product_names") or {}).get(ptype) or ptype.replace("_", " ")
        try:
            spoken = name_tpl.format(**{kk: str(vv) for kk, vv in p.items()})
        except Exception:
            spoken = ptype.replace("_", " ")
        band = band_for(p.get("days_late"), cfg)
        deadline = ""
        attempts = ""
        if band is not None and today is not None:
            deadline = nth_business_day_after(
                today, int(band.get("deadline_business_days") or 0), cfg
            ).isoformat()
            attempts = str(band.get("attempts") or "")
        out[k + "_id"] = str(p.get("product_id") or "")
        out[k + "_name"] = spoken
        out[k + "_past_due"] = money(p.get("past_due"))
        out[k + "_days_late"] = str(p.get("days_late") or "")
        out[k + "_floor"] = money(floor) if floor is not None else ""
        out[k + "_band"] = str(band.get("name")) if band else ""
        out[k + "_deadline"] = deadline
        out[k + "_attempts"] = attempts
        lines.append(
            "Product "
            + str(i + 1)
            + ": "
            + spoken
            + " (product_id "
            + out[k + "_id"]
            + "), past due "
            + out[k + "_past_due"]
            + ", "
            + out[k + "_days_late"]
            + " days late"
            + (", floor " + out[k + "_floor"] if out[k + "_floor"] else "")
            + (
                ", latest promise date "
                + deadline
                + " ("
                + say_date(datetime.date.fromisoformat(deadline))
                + ")"
                if deadline
                else ""
            )
            + (", persuasion attempts " + attempts if attempts else "")
        )
    out["product_facts"] = "\n".join(lines)
    return out


def check_promise_date(date_iso, deadline_iso, now, cfg):
    """'ok' | 'past' | 'not_business_day:<next bd>' | 'after_deadline:<deadline>' | 'unreadable'."""
    dt = parse_now(now)
    try:
        d = datetime.date.fromisoformat(str(date_iso)[:10])
        dl = datetime.date.fromisoformat(str(deadline_iso)[:10])
    except Exception:
        return "unreadable"
    if dt is None:
        return "unreadable"
    today = dt.date()
    if d < today:
        return "past"
    if d > dl:
        return "after_deadline:" + dl.isoformat()
    if not is_business_day(d, cfg):
        nb = next_business_day_on_or_after(d, cfg)
        if nb > dl:
            return "after_deadline:" + dl.isoformat()
        return "not_business_day:" + nb.isoformat()
    return "ok"


_WORD_MONTHS = dict((m.lower(), i + 1) for i, m in enumerate(MONTHS))
_WORD_MONTHS.update(dict((m[:3].lower(), i + 1) for i, m in enumerate(MONTHS)))


def normalize_dob(text):
    """'14 March 1988', 'March 14th, 1988', '14/03/1988' (day first), '1988-03-14' -> ISO."""
    s = str(text or "").strip().lower().replace(",", " ")
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        nums = re.findall(r"\d+", s)
        mon = None
        for w in re.findall(r"[a-z]+", s):
            if w in _WORD_MONTHS:
                mon = _WORD_MONTHS[w]
        if mon and len(nums) >= 2:
            d, y, mo = int(nums[0]), int(nums[-1]), mon
        elif len(nums) == 3:
            d, mo, y = int(nums[0]), int(nums[1]), int(nums[2])
        else:
            return s
    if y < 100:
        y += 1900 if y > 30 else 2000
    try:
        return datetime.date(y, mo, d).isoformat()
    except Exception:
        return s
