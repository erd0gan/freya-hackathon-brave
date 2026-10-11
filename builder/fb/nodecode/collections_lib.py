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


# The call's language, spoken formats (CFG["lang"], CFG["fmt"]) and calendar, set by
# prepare_cfg / set_locale at the top of a code node and read by the speech readers, say_date
# and money. Changed in place, never rebound: the node's functions share this one dict.
LOCALE = {"lang": "en", "fmt": None}


def set_locale(cfg):
    try:
        LOCALE["lang"] = str(cfg.get("lang") or "en").lower()[:2]
        LOCALE["fmt"] = cfg.get("fmt") if isinstance(cfg.get("fmt"), dict) else None
        LOCALE["week_start"] = week_start(cfg)
        LOCALE["cal"] = cfg  # 'in 3 business days' needs the workdays and holidays
    except Exception:
        pass


def _iso_day(x):
    """'2026-10-26', '20261026', '2026/10/26', '26.10.2026' (day first) -> date, or None."""
    s = str(x or "").strip()
    m = re.match(r"(\d{4})[-/.]?(\d\d?)[-/.]?(\d\d?)(?!\d)", s)
    n = re.match(r"(\d\d?)[-/.](\d\d?)[-/.](\d{4})(?!\d)", s)
    try:
        return datetime.date(*[int(x) for x in (m.groups() if m else n.groups()[::-1])])
    except Exception:
        return None


def prepare_cfg(cfg, now=None):
    """CFG on the call's day: the CFG["dated"] entries ({"from": "YYYY-MM-DD", "set": dotted
    path -> value}) from that day or before applied in date order to a copy (a memo's rule holds
    from its date only), holidays as ISO days, LOCALE set. Never raises."""
    try:
        out = json.loads(json.dumps(cfg))
        dt = parse_now(now)
        dated = [(_iso_day(c.get("from")), c) for c in out.get("dated") or [] if dt and c]
        for frm, c in sorted([x for x in dated if x[0]], key=lambda x: x[0]):
            for path, val in (c.get("set") or dict()).items() if frm <= dt.date() else []:
                try:  # 'bands.0.attempts': a number indexes a list
                    o, keys = out, str(path).split(".")
                    for k in keys[:-1]:
                        o = o[int(k)] if isinstance(o, list) else o.setdefault(k, dict())
                    o[int(keys[-1]) if isinstance(o, list) else keys[-1]] = val
                except Exception:
                    pass
        out["holidays"] = [str(_iso_day(h) or h) for h in out.get("holidays") or []]
        set_locale(out)
        return out
    except Exception:
        set_locale(cfg if isinstance(cfg, dict) else dict())
        return cfg


# Month prefixes in a `now`: English, then Turkish.
_MON3 = "jan feb mar apr may jun jul aug sep oct nov dec oca [sş]ub mar nis may haz tem a[gğ]u"
_MON3 += " eyl eki kas ara"


def parse_now(now):
    """`now` as a naive datetime of its wall time: ISO ('Z', an offset dropped), '2026-10-14
    9:30', '14.10.2026 11:00' (day first), 'Wed, 14 Oct 2026 11:00', '14 Ekim 2026'; or None."""
    s = str(now or "").strip()
    try:
        return datetime.datetime.fromisoformat(s.replace(" ", "T")[:16])
    except Exception:
        pass
    try:
        d = _iso_day(s)
        if d is None:
            m = re.search(r"(\d\d?)\.?\s+([^\W\d_]+)\.?,?\s+(\d{4})", s.lower())
            mo = [i % 12 + 1 for i, p in enumerate(_MON3.split()) if re.match(p, m.group(2))]
            d = datetime.date(int(m.group(3)), mo[0], int(m.group(1)))
        t = re.search(
            r"\d{4}[-/.]\d\d?[-/.]\d\d?\D+(\d\d?)[:.](\d\d)|\d{4}\D+?(\d\d?)[:.](\d\d)", s
        )
        h, mi = [int(x) for x in t.groups() if x] if t else (0, 0)
        return datetime.datetime(
            d.year, d.month, d.day, *((h, mi) if h < 24 and mi < 60 else (0, 0))
        )
    except Exception:
        return None


def _fmt(fmt=None):
    f = fmt if isinstance(fmt, dict) else LOCALE.get("fmt")
    return f if isinstance(f, dict) else dict()


def say_date(d, fmt=None):
    """'Friday 23 October 2026'; another language from `fmt` (or LOCALE's): `weekdays`,
    `months`, `date` ('{day} {month} {year} {weekday}'). A key it lacks is English."""
    f = _fmt(fmt)
    wd = (f.get("weekdays") or WEEKDAYS)[d.weekday()]
    mo = (f.get("months") or MONTHS)[d.month - 1]
    try:
        return str(f["date"]).format(weekday=wd, day=d.day, month=mo, year=d.year)
    except Exception:
        return wd + " " + str(d.day) + " " + mo + " " + str(d.year)


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
    for _ in range(400 + 2 * n):  # bounded (a calendar of holidays only), never cut short
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


def last_business_day_of_month(d, cfg):
    """The last business day of d's month (None when the month has none)."""
    nxt = datetime.date(d.year + (d.month == 12), d.month % 12 + 1, 1)
    last = prev_business_day_on_or_before(nxt - datetime.timedelta(days=1), cfg)
    return last if (last.year, last.month) == (d.year, d.month) else None


def deadline_for(today, band, cfg, product=None):
    """Band deadline from today, or from a date field of the product (`deadline_from_field`,
    e.g. a statement's due date): `deadline_unit` "business" (default) the Nth business day
    after, "calendar" N days after moved back to a business day, "end_of_month" that month's
    last business day. 0 is the day itself (the next business day when it is not one); never
    before today."""
    n = count_or(band.get("deadline_business_days"), None)
    if n is None:
        n = count_or(band.get("deadline_days"), 0)
    unit = str(band.get("deadline_unit") or "business")
    base = today
    fld = band.get("deadline_from_field")
    if fld and isinstance(product, dict) and _iso_day(product.get(fld)):
        base = _iso_day(product.get(fld))
    if unit == "end_of_month":
        d = last_business_day_of_month(base, cfg)
    elif unit == "calendar":
        d = prev_business_day_on_or_before(base + datetime.timedelta(days=n), cfg)
    else:
        d = nth_business_day_after(base, n, cfg)
    if d is None or d < today:
        return next_business_day_on_or_after(today, cfg)
    return d


def rule_date(today, rule, cfg, deadline=None):
    """A date named by a rule word: "today" (the next business day when today is not one),
    "next_business_day", "nth_business_day:<n>", "last_business_day_of_week" (this week's, or
    next week's when today is that day or later: attempt 1 of Brief A), "last_business_day_of_
    month", "attempt1", "deadline" (`deadline`, a date or ISO text). Never after `deadline` when
    one is given. None when the rule is unknown."""
    r = str(rule or "").strip().lower()
    dl = _iso_day(deadline) if not isinstance(deadline, datetime.date) else deadline
    if r == "deadline":
        return dl
    if r == "attempt1":
        d = attempt1_date(today, cfg)
    elif r == "today":
        d = next_business_day_on_or_after(today, cfg)
    elif r == "next_business_day":
        d = nth_business_day_after(today, 1, cfg)
    elif r.startswith("nth_business_day"):
        d = nth_business_day_after(today, count_or(r.split(":")[-1] if ":" in r else 1, 1), cfg)
    elif r == "last_business_day_of_week":
        d = last_business_day_of_week(today, cfg)
        if d is None or d <= today:
            d = _next_week_last(today, cfg)
    elif r == "last_business_day_of_month":
        d = last_business_day_of_month(today, cfg)
        if d is None or d < today:
            d = last_business_day_of_month(today + datetime.timedelta(days=32 - today.day), cfg)
    else:
        return None
    if d is not None and dl is not None and d > dl:
        return dl
    return d


def _next_week_last(today, cfg):
    # the last business day of the next week that has one (a week of holidays is skipped:
    # attempt 1 is never empty)
    for k in range(1, 9):
        d = last_business_day_of_week(today + datetime.timedelta(days=7 * k), cfg)
        if d is not None:
            return d
    return None


def attempt1_date(today, cfg):
    """Date of persuasion attempt 1 (CFG["attempt1"]):
    rule "last_business_day_of_week" (Brief A rule 30: this week's last business day, or next
    week's when today is that day or later), "nth_business_day" with `n`, or
    "last_business_day_of_month" (this month's, next month's when it has passed)."""
    a = cfg.get("attempt1") or dict()
    rule = str(a.get("rule") or "last_business_day_of_week")
    if rule == "nth_business_day":
        return nth_business_day_after(today, count_or(a.get("n"), 1), cfg)
    if rule == "last_business_day_of_month":
        return rule_date(today, rule, cfg)
    this_last = last_business_day_of_week(today, cfg)
    if this_last is not None and this_last > today:
        return this_last
    return _next_week_last(today, cfg)


def week_start(cfg):
    # the calendar week's first day (Monday = 0): CFG["week_start"], else the working day after
    # the longest run of days off (Monday for Monday-Friday, Sunday for Sunday-Thursday)
    try:
        return int(cfg["week_start"]) % 7
    except Exception:
        pass
    wd = [int(x) % 7 for x in cfg.get("workdays") or [0, 1, 2, 3, 4]]
    off = lambda d: next((n for n in range(7) if (d - 1 - n) % 7 in wd), 7)  # noqa: E731
    return max([d for d in range(7) if d in wd] or [0], key=lambda d: (off(d), -d))


def week_of(d, cfg):
    """The first day of d's calendar week."""
    return d - datetime.timedelta(days=(d.weekday() - week_start(cfg)) % 7)


def last_business_day_of_week(d, cfg):
    first = week_of(d, cfg)
    best = None
    for i in range(7):
        day = first + datetime.timedelta(days=i)
        if is_business_day(day, cfg):
            best = day
    return best


def band_for(days_late, cfg, nearest=False):
    """The band holding `days_late`; with `nearest`, outside every band (0 days, more than the
    last band) the nearest one, so the product still gets a deadline and attempts."""
    try:
        n = int(float(days_late))
    except Exception:
        return None
    best, gap = None, None
    for b in cfg.get("bands") or []:
        lo = b.get("min_days_late")
        hi = b.get("max_days_late")
        d = max((lo - n) if lo is not None else 0, (n - hi) if hi is not None else 0, 0)
        if d == 0:
            return b
        if nearest and (gap is None or d < gap):
            best, gap = b, d
    return best


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
    # a callback must be later than now: today's first slot is the next minute
    first = (dt + datetime.timedelta(minutes=1)).strftime("%H:%M")
    if dt.hour == 23 and dt.minute == 59:
        first = "24:00"
    if cb.get("include_today", True) is not False and first <= end and out["now_time"] < end:
        if is_business_day(today, cfg) or not bd_only:
            days.append(("today", today, max(start, first)))
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
    monday = week_of(today, cfg)  # the week's first day (a Sunday for a Sunday-Thursday week)
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


def load_products(raw, key="products"):
    """get_account products arrive as a list, a JSON string or a python repr string (or an
    object holding the list under `key`)."""
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
                if isinstance(v, dict) and isinstance(v.get(key), list):
                    return [p for p in v[key] if isinstance(p, dict)]
            except Exception:
                pass
    return []


def to_float(x):
    """1250, '1250.5', '1,250.00', '£1,250.00' -> float; a decimal comma only where nothing else
    is meant ('1.250,00', '₺1.250,00', '1250,5 TL'), '1.250' as 1250 in a Turkish call. Raises
    on anything else."""
    s = re.sub(
        r"^(?:[£€$₺]|(?:gbp|eur|usd|try|tl)\s*)|\s*(?:tl|try|lira)$", "", str(x).strip(), flags=re.I
    ).strip()
    if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+,\d+|-?\d+,\d\d?", s):
        return float(s.replace(".", "").replace(",", "."))
    if LOCALE.get("lang") == "tr" and re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", s):
        return float(s.replace(".", ""))
    if re.search(r"\.\d{3},", s):
        raise ValueError(s)
    return float(re.sub(r",(?=\d{3}\b)", "", s))


def money(x, fmt=None):
    """'1250.00' (also with fmt={}: amounts kept as data); another language from `fmt` (or
    LOCALE's): `thousands` / `decimal` marks, `minor_if_any` (no ',00': TTS reads '1.250 lira'
    cleanly, '1.250,00' with 'virgül sıfır sıfır'), with `say_currency` the currency word after
    the amount (fmt.currency word / symbol / code, else `currency_word`)."""
    try:
        v = to_float(x)
    except Exception:
        return str(x)
    f = _fmt(fmt)
    if not (f.get("decimal") or f.get("thousands") or f.get("say_currency")):
        return "%.2f" % v
    s = f"{v:,.2f}"
    if f.get("minor_if_any") and s.endswith(".00"):
        s = s[:-3]
    s = (
        s.replace(",", "_")
        .replace(".", str(f.get("decimal") or "."))
        .replace("_", str(f.get("thousands") or ""))
    )
    cur = f.get("currency")
    word = (
        (cur.get("word") or cur.get("symbol") or cur.get("code")) if isinstance(cur, dict) else cur
    )
    word = word or f.get("currency_word")
    return s + " " + str(word) if f.get("say_currency") and word else s


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
        band = band_for(pf(p, "days_late"), cfg, True)
        # days late outside every band: the nearest band's rules, noted (never no deadline)
        out[k + "_band_nearest"] = "yes" if band and not band_for(pf(p, "days_late"), cfg) else ""
        deadline = ""
        attempts = ""
        if band is not None and today is not None:
            deadline = deadline_for(today, band, cfg, p).isoformat()
            attempts = str(band.get("attempts") if band.get("attempts") is not None else "")
        out[k + "_id"] = str(pf(p, "product_id") or "")
        out[k + "_name"] = spoken
        # amounts here are data (the engine reads them back with to_float): the machine form,
        # whatever language the call speaks
        out[k + "_past_due"] = money(pf(p, "past_due"), dict())
        dl = pf(p, "days_late")
        try:
            dl = str(int(float(dl))) if float(dl) == int(float(dl)) else str(dl)
        except Exception:
            dl = str(dl or "")
        out[k + "_days_late"] = dl
        out[k + "_floor"] = money(floor, dict()) if floor is not None else ""
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


def dob_year(y):
    """A year of birth from what was said: 4 digits as said; 2 digits only when the century is
    clear for a customer (30-99 -> 19xx, 00-08 -> 20xx); None otherwise ('19' or '20' is the
    first half of a year said in two parts, '14' could be 1914 or 2014)."""
    ys = str(y)
    if len(ys) == 4:
        return int(ys)
    if len(ys) == 2:
        n = int(ys)
        if n >= 30:
            return 1900 + n
        if n <= 8:
            return 2000 + n
    return None


def _dob_words(s, lang=None):
    """Words read as numbers for a date of birth, after a correction: 'the fourteenth of March,
    nineteen eighty-eight' -> '14th of march 19 88'; Turkish ('on dört mart bin dokuz yüz
    seksen sekiz' -> '14 march 1988'; 'bin dokuz yüz' alone -> '19', a year not whole yet) with
    `lang` "tr" (None: the call's language). Uses speech_lib (in the same code node); without
    it (this file imported alone) the text stays as it is."""
    try:
        s = _rewrite(s, "dob", lang)  # noqa: F821
        s = _last_segment(s)  # noqa: F821  ('14 March, sorry, 15 March 1988')
        s = re.sub(r"[-,.]", " ", s)
        s = re.sub(r"\b(?:oh|o|zero)\b", " 0 ", s)  # 'nineteen oh five' is 1905, not 195
        return _words_to_number_tokens(s)  # noqa: F821
    except Exception:
        return re.sub(r"[,.]", " ", s)


def dob_digits(text, lang=None):
    """The digits of a date-of-birth piece as normalize_dob reads them: 'nineteen' -> '19', and
    in Turkish 'bin dokuz yüz' -> '19' (a year whose rest is not said yet), 'seksen sekiz' ->
    '88'. For the pieces check (a lone first half of a year is not an answer yet)."""
    return re.sub(r"\D", "", _dob_words(str(text or "").lower(), lang))


def normalize_dob(text, order="DMY", lang=None):
    """'14 March 1988', 'March 14th, 1988', 'the fourteenth of March nineteen eighty-eight',
    'fourteen March one nine eight eight', '1988, March 14th', '14/03/1988' (day first; order
    "MDY" reads month first; a month over 12 is read the other way round), '1988-03-14' ->
    ISO. A date whose year is not whole yet ('14 March 19', '14 March 14') -> 'incomplete',
    which the bank reads as unreadable, not as a wrong answer. Other text is returned as it is.
    `lang` "tr" (None: the call's language): '14 Mart 1988', 'on dört mart bin dokuz yüz seksen
    sekiz'; '14 mart bin dokuz yüz' is incomplete."""
    s = str(text or "").strip().lower()
    said = s
    try:  # noqa: SIM105  (the sandbox's module list is not documented)
        s = _rewrite(s, "dob", lang)  # noqa: F821  (speech_lib, same code node)
    except Exception:
        pass
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b", s)
        toks = re.findall(r"[a-z]+|\d+(?:st|nd|rd|th)?", _dob_words(s, "en"))
        mi = next((i for i, t in enumerate(toks) if t in _WORD_MONTHS), None)
        if m:
            a, b, ys = int(m.group(1)), int(m.group(2)), m.group(3)
        elif mi is not None:
            mo = _WORD_MONTHS[toks[mi]]
            before = [re.sub(r"\D", "", t) for t in toks[:mi] if t[0].isdigit()]
            after = [re.sub(r"\D", "", t) for t in toks[mi + 1 :] if t[0].isdigit()]
            if len(before) == 1 and int(before[0]) <= 31:
                d, ys = int(before[0]), "".join(after)  # '14 March 1988'
            elif after and int(after[0]) <= 31 and not before:
                d, ys = int(after[0]), "".join(after[1:])  # 'March 14th 1988'
            elif after and int(after[-1]) <= 31 and before:
                d, ys = int(after[-1]), "".join(before + after[:-1])  # '1988, March 14th'
            else:
                return said
        else:
            n3 = re.findall(r"\d+", s)
            if len(n3) != 3:
                return said
            a, b, ys = int(n3[0]), int(n3[1]), n3[2]
        if mi is None or m:
            d, mo = (b, a) if str(order).upper().startswith("M") else (a, b)
            if mo > 12 and d <= 12:
                d, mo = mo, d  # '03/14/1988' said month first
        if not ys:
            return "incomplete"
        y = dob_year(ys)
        if y is None:
            return "incomplete"
    try:
        return datetime.date(y, mo, d).isoformat()
    except Exception:
        return said


def find_products(v, cfg=None):
    """get_account products, wherever the platform put them: the mapped variable, a
    flattened `<x>.data.<list>` key, or one flattened key per field. The list name and the
    number fields come from the config (`products_list` or `product_fields.list`;
    `product_fields`, `floor_field`), Brief A's names when it has none. Without `cfg` the
    code node's CFG is used."""
    if cfg is None:
        try:
            cfg = CFG  # noqa: F821  (defined by the code node around this library)
        except Exception:
            cfg = dict()
    if not isinstance(cfg, dict):
        cfg = dict()
    fmap = cfg.get("product_fields") or dict()
    lst = str(cfg.get("products_list") or fmap.get("list") or "products")
    tail = ".data." + lst
    for key in ["products"] + [k for k in v if str(k).endswith(tail)]:
        got = load_products(v.get(key), lst.split(".")[-1])
        if got:
            return got
    rows = {}
    rx = re.escape(tail) + r"[.\[](\d+)\]?\.(\w+)$"
    for k in list(v):
        m = re.search(rx, str(k))
        if m:
            rows.setdefault(int(m.group(1)), {})[m.group(2)] = v[k]
    money_fields = ["minimum_payment", "instalment_amount"]
    money_fields += [str(fmap.get(r) or r) for r in ("past_due", "days_late")]
    money_fields += [str(f) for f in (cfg.get("floor_field") or dict()).values() if f]
    out = []
    for i in sorted(rows):
        p = rows[i]
        for f in money_fields:
            if f in p:
                try:  # noqa: SIM105  (the sandbox's module list is not documented)
                    p[f] = to_float(p[f])
                except Exception:
                    pass
        out.append(p)
    return out
