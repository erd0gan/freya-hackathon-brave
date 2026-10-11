# Runs INSIDE Freya code nodes and in local tests. Stdlib only (json, re, datetime), never
# raises to the caller, never two braces in a row.
# Turns what a customer said into an ISO date relative to the call's `now` (the platform's
# extractor anchors dates to the real clock, not to `now`) and into an amount.
import datetime
import re

_MONTHS = [
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
]
_WDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_UNITS = {
    "zero": 0,
    "oh": 0,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}
_ORD = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
    "eleventh": 11,
    "twelfth": 12,
    "thirteenth": 13,
    "fourteenth": 14,
    "fifteenth": 15,
    "sixteenth": 16,
    "seventeenth": 17,
    "eighteenth": 18,
    "nineteenth": 19,
    "twentieth": 20,
    "thirtieth": 30,
}
_VAGUE = [
    "next week",
    "week after next",
    "end of the month",
    "end of month",
    "end of the week",
    "salary",
    "payday",
    "pay day",
    "paid next",
    "get paid",
    "soon",
    "later this month",
    "next month",
    "in a few days",
    "in two weeks",
    "couple of weeks",
    "a few weeks",
    "sometime",
    "some time",
    "when i can",
    "beginning of",
    "middle of",
    "mid ",
]
_CORRECTION = re.compile(
    r"\b(?:sorry|i mean|no wait|wait|actually|rather|correction|make that|scratch that)\b"
)


def _words_to_number_tokens(s):
    """'twenty-second' -> '22nd', 'thirty first' -> '31st', 'four hundred and fifty-five'
    -> '455'. Leaves other text alone."""
    s = s.replace("-", " ")
    toks = s.split()
    out = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in _TENS and i + 1 < len(toks) and toks[i + 1] in _ORD and _ORD[toks[i + 1]] < 10:
            out.append(str(_TENS[t] + _ORD[toks[i + 1]]) + "th")
            i += 2
            continue
        if t in _ORD:
            out.append(str(_ORD[t]) + "th")
            i += 1
            continue
        if (
            t in _UNITS
            or t in _TENS
            or (t == "a" and i + 1 < len(toks) and toks[i + 1] in ("hundred", "thousand"))
        ):
            total = 0
            cur = 0
            j = i
            used = False
            while j < len(toks):
                w = toks[j]
                if w in _UNITS:
                    if cur % 10 or (cur % 100 and _UNITS[w] >= 10):
                        break  # 'thirty seven fifty': a new number starts
                    cur += _UNITS[w]
                elif w in _TENS:
                    if cur % 100:
                        break
                    cur += _TENS[w]
                elif w == "a" and j + 1 < len(toks) and toks[j + 1] in ("hundred", "thousand"):
                    cur += 1
                elif w == "hundred":
                    cur = max(cur, 1) * 100
                elif w == "thousand":
                    total += max(cur, 1) * 1000
                    cur = 0
                elif (
                    w == "and"
                    and used
                    and j + 1 < len(toks)
                    and (toks[j + 1] in _UNITS or toks[j + 1] in _TENS)
                ):
                    pass
                else:
                    break
                used = True
                j += 1
            out.append(str(total + cur))
            i = j
            continue
        out.append(t)
        i += 1
    return " ".join(out)


def _norm_text(text):
    s = str(text or "").lower()
    s = s.replace(",", " ").replace("’", "'")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
    s = _words_to_number_tokens(s)
    s = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


_NUMWORD = "|".join(list(_UNITS) + list(_TENS) + list(_ORD) + ["hundred", "thousand"])


def _last_segment(s):
    """After 'sorry, I mean ...' the last part that holds a value wins."""
    parts = _CORRECTION.split(s)
    for p in reversed(parts):
        if re.search(r"\d|\b(?:" + _NUMWORD + "|" + "|".join(_WDAYS) + r"|today|tomorrow)\b", p):
            return p
    return s


def _month_of(word):
    for i, m in enumerate(_MONTHS):
        if word == m or (len(word) >= 3 and m.startswith(word)):
            return i + 1
    return None


def _safe_date(y, m, d):
    try:
        return datetime.date(y, m, d)
    except Exception:
        return None


def parse_spoken_date(text, today):
    """-> (iso or '', 'vague' | 'date' | 'none'). `today` is a datetime.date."""
    # a correction ('..., sorry I mean tomorrow') wins over everything said before it
    iso = re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", _last_segment(str(text or "").lower()))
    if iso:
        y, mo, dd = iso[-1]
        d = _safe_date(int(y), int(mo), int(dd))
        return (d.isoformat(), "date") if d else ("", "none")
    raw = _norm_text(text)
    if not raw:
        return "", "none"
    s = _last_segment(raw)
    if "day after tomorrow" in s:
        return (today + datetime.timedelta(days=2)).isoformat(), "date"
    if "tomorrow" in s:
        return (today + datetime.timedelta(days=1)).isoformat(), "date"
    if re.search(r"\btoday\b|\bright now\b|\bnow\b", s) and not re.search(r"\d", s):
        return today.isoformat(), "date"
    # day + month name, either order
    toks = s.split()
    day = None
    month = None
    year = None
    for i, t in enumerate(toks):
        mo = _month_of(t) if re.fullmatch(r"[a-z]+", t) else None
        # 'may' is a month only next to a day number ('5 May', 'May 5'), not in 'I may pay'
        if (mo and t not in ("may",)) or (
            t == "may"
            and (
                (i > 0 and re.fullmatch(r"\d{1,2}", toks[i - 1] or ""))
                or (i + 1 < len(toks) and re.fullmatch(r"\d{1,2}(?:st|nd|rd|th)?", toks[i + 1]))
            )
        ):
            month = mo
            if i > 0 and re.fullmatch(r"\d{1,2}", toks[i - 1]):
                day = int(toks[i - 1])
            elif i > 1 and toks[i - 1] == "of" and re.fullmatch(r"\d{1,2}", toks[i - 2]):
                day = int(toks[i - 2])
            elif i + 1 < len(toks) and re.fullmatch(r"\d{1,2}", toks[i + 1]):
                day = int(toks[i + 1])
            if i + 1 < len(toks) and re.fullmatch(r"\d{4}", toks[-1]):
                year = int(toks[-1])
    m = re.search(r"\b(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b", s)
    if month is None and m:
        day, month = int(m.group(1)), int(m.group(2))
        if m.group(3):
            year = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
    if month and day:
        y = year or today.year
        d = _safe_date(y, month, day)
        if d and not year and d < today:
            d = _safe_date(y + 1, month, day)
        return (d.isoformat(), "date") if d else ("", "none")
    # weekday, with this / next
    for i, w in enumerate(_WDAYS):
        if re.search(r"\b" + w + r"\b", s):
            monday = today - datetime.timedelta(days=today.weekday())
            this_day = monday + datetime.timedelta(days=i)
            if re.search(r"\bnext\s+" + w, s) or "following" in s:
                d = this_day + datetime.timedelta(days=7)
            elif re.search(r"\bthis\s+" + w, s) or re.search(r"\bthis coming\s+" + w, s):
                d = this_day
            else:
                d = this_day if this_day >= today else this_day + datetime.timedelta(days=7)
            num = re.search(r"\b(\d{1,2})\b", s)
            if num:
                # 'Monday the 26th': trust the number, it is more specific
                n = int(num.group(1))
                dd = _safe_date(today.year, today.month, n) if 1 <= n <= 31 else None
                if dd and dd < today:
                    nm = today.month % 12 + 1
                    dd = _safe_date(today.year + (1 if nm == 1 else 0), nm, n)
                if dd:
                    return dd.isoformat(), "date"
            return d.isoformat(), "date"
    # 'the 26th' alone
    m = re.search(r"\b(?:the\s+)?(\d{1,2})\b", s)
    if (m and any(v in raw for v in ("the ",))) or (
        m and re.fullmatch(r"(?:on\s+)?(?:the\s+)?\d{1,2}", s)
    ):
        n = int(m.group(1))
        if 1 <= n <= 31:
            d = _safe_date(today.year, today.month, n)
            if d is None or d < today:
                nm = today.month % 12 + 1
                d = _safe_date(today.year + (1 if nm == 1 else 0), nm, n)
            if d:
                return d.isoformat(), "date"
    for v in _VAGUE:
        if v in raw:
            return "", "vague"
    return "", "none"


def _point_digits(s):
    """The words after 'point' read as a digit sequence: 'zero five' -> '05', 'five zero'
    -> '50', 'two five' -> '25', 'twenty five' -> '25', '05' -> '05'. Stops at the first
    word that is not a digit; '' when the part does not start with one."""
    toks = s.replace("-", " ").split()
    out = ""
    i = 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"\d+", t):
            out += t
        elif t in _UNITS:
            out += str(_UNITS[t])
        elif t in _TENS:
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if nxt in _UNITS and 0 < _UNITS[nxt] < 10:
                out += str(_TENS[t] + _UNITS[nxt])
                i += 1
            else:
                out += str(_TENS[t])
        else:
            break
        i += 1
    return out


def parse_amount(text):
    """'637.50', '637,50', 'six hundred and thirty-seven fifty', 'two thousand one hundred
    twenty-five pounds and ten pence', 'two hundred point zero five' -> float, or None."""
    raw = str(text or "").lower().replace("’", "'")
    raw = re.sub(r"(\d),(\d{2})(?!\d)", r"\1.\2", raw)
    if not raw.strip():
        return None
    s = _last_segment(raw)
    if re.search(r"\bpoint\b", s):
        # a spoken decimal point: what follows is a digit sequence, not a number
        # ('point zero five' is .05, not .5); only when a number stands on both sides
        left, right = re.split(r"\bpoint\b", s, maxsplit=1)
        digits = _point_digits(re.sub(r"\b(pounds?|euros?|dollars?|eur|gbp|usd)\b", " ", right))
        whole = parse_amount(left) if digits else None
        if whole is not None:
            try:
                return round(whole + float("0." + digits), len(digits))
            except Exception:
                return None
    m = re.search(r"(\d[\d ,]*\d|\d)(?:[.,](\d{1,2}))?(?!\d)", s)
    if m and not re.search(
        r"[a-z]", re.sub(r"(pounds?|euros?|dollars?|eur|gbp|usd|and|cents?|pence|p\b)", "", s)
    ):
        whole = re.sub(r"[ ,]", "", m.group(1))
        frac = m.group(2) or ""
        try:
            return float(whole + ("." + frac if frac else ""))
        except Exception:
            return None
    s2 = re.sub(r"\b(pounds?|euros?|dollars?)\b", " | ", s)
    s2 = re.sub(r"\b(cents?|pence|p)\b", " ", s2)
    s2 = re.sub(r"\bpoint\b", " | ", s2)
    parts = [p for p in s2.split("|")]
    nums = []
    for p in parts:
        t = _words_to_number_tokens(p.replace("-", " "))
        found = re.findall(r"\d+(?:\.\d+)?", t)
        if found:
            nums.append(found)
    if not nums:
        return None
    try:
        whole = float(nums[0][0]) if len(nums[0]) == 1 else float("".join(nums[0]))
        if len(nums) > 1:
            cents = nums[1][0]
            if "point" in s and len(cents) == 1:
                return whole + float("0." + cents)
            return whole + float(cents) / 100.0
        if len(nums[0]) == 2 and float(nums[0][1]) < 100:
            # 'six hundred thirty seven fifty' -> 637.50
            return float(nums[0][0]) + float(nums[0][1]) / 100.0
        return whole
    except Exception:
        return None


def parse_spoken_time(text, day_start=10, day_end=19):
    """'around eleven', '11am', 'half past two', '14:30', 'two thirty in the afternoon' ->
    'HH:MM', or ''. A bare hour that cannot be a working hour as said (1 to 7) is read as
    afternoon; 'morning' / 'afternoon' / 'evening' / am / pm decide when given."""
    raw = str(text or "").lower().replace(".", " ").replace("-", " ")
    if not raw.strip():
        return ""
    s = re.sub(r"(\d)([a-z])", r"\1 \2", _last_segment(raw))
    if "noon" in s and "after" not in s:
        return "12:00"
    pm = bool(re.search(r"\b(pm|p m|afternoon|evening|tonight)\b", s))
    am = bool(re.search(r"\b(am|a m|morning)\b", s))
    s = re.sub(r"\b(o'?clock|oclock|ish|around|about|at|by|maybe)\b", " ", s)
    t = _words_to_number_tokens(s)
    hour, minute = None, 0
    m = re.search(r"\b(\d{1,2})[:h ](\d{2})\b", t)
    if m and int(m.group(2)) < 60:
        hour, minute = int(m.group(1)), int(m.group(2))
    else:
        m = re.search(r"\bhalf past (\d{1,2})\b", t)
        q = re.search(r"\bquarter past (\d{1,2})\b", t)
        q2 = re.search(r"\bquarter to (\d{1,2})\b", t)
        if m:
            hour, minute = int(m.group(1)), 30
        elif q:
            hour, minute = int(q.group(1)), 15
        elif q2:
            hour, minute = int(q2.group(1)) - 1, 45
        else:
            m = re.search(r"\b(\d{1,2}) (\d{2})\b", t)
            if m and int(m.group(2)) < 60 and int(m.group(1)) <= 23:
                hour, minute = int(m.group(1)), int(m.group(2))
            else:
                m = re.search(r"\b(\d{1,2})\b", t)
                if m:
                    hour = int(m.group(1))
    if hour is None or hour > 23:
        return ""
    if (pm and hour < 12) or (not am and 1 <= hour <= 7):
        hour += 12
    elif am and hour == 12:
        hour = 0
    return "%02d:%02d" % (hour, minute)


def digits_from_speech(text):
    """'four four seven one', '44 71', 'forty-four seventy-one' -> '4471'."""
    raw = str(text or "").lower().replace("-", " ")
    s = _last_segment(raw)
    out = []
    for w in re.findall(r"[a-z]+|\d+", s):
        if w.isdigit():
            out.append(w)
        elif w in ("oh", "o", "zero"):
            out.append("0")
        elif w in _UNITS:
            out.append(str(_UNITS[w]))
        elif w in _TENS:
            out.append(str(_TENS[w]))
        elif w in ("double", "triple"):
            out.append(w)
    # 'double four' -> '44'; 'forty four' -> '44' (a tens word followed by a unit)
    res = []
    i = 0
    while i < len(out):
        x = out[i]
        if x in ("double", "triple") and i + 1 < len(out):
            res.append(out[i + 1] * (2 if x == "double" else 3))
            i += 2
            continue
        if len(x) == 2 and x.endswith("0") and i + 1 < len(out) and len(out[i + 1]) == 1:
            res.append(x[0] + out[i + 1])
            i += 2
            continue
        res.append(x)
        i += 1
    return "".join(r for r in res if r.isdigit())
