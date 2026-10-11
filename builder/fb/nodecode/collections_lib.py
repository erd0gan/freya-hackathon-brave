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


def count_or(value, default):
    """A whole number >= 0 from a policy value; `default` only when it is missing (None or
    ''). 0 is a real value: `x or 1` would turn it into 1."""
    if value is None or str(value).strip() == "":
        return default
    try:
        return max(0, int(float(value)))
    except Exception:
        return default


def nth_business_day_after(today, n, cfg):
    """The Nth business day after today; N = 0 is today, or the next business day when today
    is not one (a deadline or a promise day is always a business day)."""
    if n <= 0:
        return next_business_day_on_or_after(today, cfg)
    d = today
    count = 0
    for _ in range(400):
        d = d + datetime.timedelta(days=1)
        if is_business_day(d, cfg):
            count += 1
            if count >= n:
                return d
    return d


def prev_business_day_on_or_before(d, cfg):
    for _ in range(60):
        if is_business_day(d, cfg):
            return d
        d = d - datetime.timedelta(days=1)
    return d


def deadline_for(today, band, cfg):
    """Band deadline: the Nth business day after today (`deadline_unit` "business", default),
    or N calendar days after today moved back to a business day ("calendar"). 0 means today
    (the next business day when today is not one); a deadline is never before today."""
    n = count_or(band.get("deadline_business_days"), None)
    if n is None:
        n = count_or(band.get("deadline_days"), 0)
    if str(band.get("deadline_unit") or "business") == "calendar":
        d = prev_business_day_on_or_before(today + datetime.timedelta(days=n), cfg)
        return d if d >= today else next_business_day_on_or_after(today, cfg)
    return nth_business_day_after(today, n, cfg)


def attempt1_date(today, cfg):
    """Date of persuasion attempt 1 (CFG["attempt1"]):
    rule "last_business_day_of_week" (Brief A rule 30: this week's last business day, or next
    week's when today is that day or later), or rule "nth_business_day" with `n`."""
    a = cfg.get("attempt1") or dict()
    if str(a.get("rule") or "last_business_day_of_week") == "nth_business_day":
        return nth_business_day_after(today, int(a.get("n") or 1), cfg)
    this_last = last_business_day_of_week(today, cfg)
    if this_last is not None and this_last > today:
        return this_last
    return last_business_day_of_week(today + datetime.timedelta(days=7), cfg)


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
    a1 = attempt1_date(today, cfg)
    out["attempt1_date"] = a1.isoformat() if a1 else ""
    out["attempt1_date_text"] = say_date(a1) if a1 else ""
    for b in cfg.get("bands") or []:
        dl = deadline_for(today, b, cfg)
        key = "deadline_" + str(b.get("name"))
        out[key] = dl.isoformat()
        out[key + "_text"] = say_date(dl)
    # Callback window (third party).
    cb = cfg.get("callback") or {}
    start = str(cb.get("start") or "10:00")
    end = str(cb.get("end") or "19:00")
    # CFG["callback"]: include_today (default true), business_days_ahead (default 1; 0 = only
    # today), business_days_only (default true: a callback day must be a business day).
    days = []
    bd_only = cb.get("business_days_only", True) is not False
    if cb.get("include_today", True) is not False and out["now_time"] < end:
        if is_business_day(today, cfg) or not bd_only:
            days.append(("today", today, max(start, out["now_time"])))
    for n in range(1, count_or(cb.get("business_days_ahead"), 1) + 1):
        nb = (
            nth_business_day_after(today, n, cfg) if bd_only else today + datetime.timedelta(days=n)
        )
        days.append(("next business day" if n == 1 else say_date(nb), nb, start))
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
        return "%.2f" % float(x)
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
    # CFG["product_fields"]: the names the tool API uses for these roles (hidden briefs can
    # rename them).
    fmap = cfg.get("product_fields") or dict()

    def pf(p, role):
        return p.get(fmap.get(role) or role)

    for i, p in enumerate(products):
        k = "p" + str(i + 1)
        ptype = str(pf(p, "type") or "")
        floor_field = (cfg.get("floor_field") or {}).get(ptype, "")
        floor = p.get(floor_field) if floor_field else None
        name_tpl = (cfg.get("product_names") or {}).get(ptype) or ptype.replace("_", " ")
        try:
            spoken = name_tpl.format(**{kk: str(vv) for kk, vv in p.items()})
        except Exception:
            spoken = ptype.replace("_", " ")
        band = band_for(pf(p, "days_late"), cfg)
        deadline = ""
        attempts = ""
        if band is not None and today is not None:
            deadline = deadline_for(today, band, cfg).isoformat()
            attempts = str(band.get("attempts") or "")
        out[k + "_id"] = str(pf(p, "product_id") or "")
        out[k + "_name"] = spoken
        out[k + "_past_due"] = money(pf(p, "past_due"))
        dl = pf(p, "days_late")
        try:
            dl = str(int(float(dl))) if float(dl) == int(float(dl)) else str(dl)
        except Exception:
            dl = str(dl or "")
        out[k + "_days_late"] = dl
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
    # What the model may say about the products: no floor, deadline or attempt count, so it
    # cannot make an offer of its own.
    out["product_brief"] = "\n".join(
        out["p" + str(i + 1) + "_name"]
        + ": "
        + out["p" + str(i + 1) + "_past_due"]
        + " past due, "
        + out["p" + str(i + 1) + "_days_late"]
        + " days late"
        for i in range(len(products))
    )
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


def normalize_dob(text, order="DMY"):
    """'14 March 1988', 'March 14th, 1988', '14/03/1988' (day first; order "MDY" reads month
    first), '1988-03-14' -> ISO."""
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
        elif len(nums) == 3 and str(order).upper().startswith("M"):
            mo, d, y = int(nums[0]), int(nums[1]), int(nums[2])
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


def find_products(v):
    """get_account products, wherever the platform put them: the mapped variable, a
    flattened `<x>.data.products` key, or one flattened key per field."""
    for key in ["products"] + [k for k in v if str(k).endswith(".data.products")]:
        got = load_products(v.get(key))
        if got:
            return got
    rows = {}
    for k in list(v):
        m = re.search(r"\.data\.products[.\[](\d+)\]?\.(\w+)$", str(k))
        if m:
            rows.setdefault(int(m.group(1)), {})[m.group(2)] = v[k]
    out = []
    for i in sorted(rows):
        p = rows[i]
        for f in ("past_due", "days_late", "minimum_payment", "instalment_amount"):
            if f in p:
                try:  # noqa: SIM105  (the sandbox's module list is not documented)
                    p[f] = float(p[f])
                except Exception:
                    pass
        out.append(p)
    return out
