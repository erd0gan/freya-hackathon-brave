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
    "couple of weeks",
    "a few weeks",
    "sometime",
    "some time",
    "when i can",
    "beginning of",
    "middle of",
    "mid ",
]
# A self-correction: what follows replaces what came before ('the 21st, no, the 20th').
# A bare 'no' counts only at the start or after a pause mark, and not in 'no problem',
# 'no more than', 'no to Monday' (a negation, read by the weekday rule).
_CORRECTION = re.compile(
    r"\b(?:sorry|i mean|i meant|no wait|wait|actually|or rather|rather|correction|make that"
    r"|make it|scratch that|oops|my bad|let me correct that)\b"
    r"|(?:^|[,.;:!?])\s*no\b(?!\s+(?:problem|worries|way|idea|money|more|less|later|earlier"
    r"|sooner|longer|chance|need|to|i can'?t|i cannot)\b)"
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


# A clock time inside a day text ('Friday at 3', 'the 20th, 2pm', 'half past ten'); a number
# marked as an ordinal ('at the 3rd', '23th' after the words are read) is a day, not a time.
_TIMES = [
    r"\b(?:at|around|about)\s+\d{1,2}(?:[:.]\d{2}|\s+\d{2})?(?!\d|th)"
    r"(?:\s*(?:am|pm|a m|p m|o'?clock))?",
    r"\b\d{1,2}(?:[:.]\d{2})?\s*(?:am|pm|a m|p m|o'?clock)\b",
    r"\b\d{1,2}:\d{2}\b",
    r"\b(?:half|quarter|\d{1,2})\s+(?:past|to)\s+\d{1,2}(?!\d|th)",
    r"\bhalf\s+\d{1,2}(?!\d|th)",
]


def _norm_text(text, drop_times=False):
    s = str(text or "").lower()
    s = s.replace(",", " ").replace("’", "'")
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1th", s)
    s = _words_to_number_tokens(s)
    if drop_times:
        for rx in _TIMES:
            s = re.sub(rx, " ", s)
    s = re.sub(r"(\d+)(st|nd|rd|th)\b", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()


_NUMWORD = "|".join(list(_UNITS) + list(_TENS) + list(_ORD) + ["hundred", "thousand"])


def _last_segment(s, more=""):
    """After 'sorry, I mean ...' the last part that holds a value wins. `more`: extra words
    that count as a value (dates: month names, 'week')."""
    parts = _CORRECTION.split(s)
    rx = r"\d|\b(?:" + _NUMWORD + "|" + "|".join(_WDAYS) + r"|today|tomorrow" + more + r")\b"
    for p in reversed(parts):
        if re.search(rx, p):
            return p
    return s


_DATE_WORDS = "|" + "|".join(_MONTHS) + "|week|fortnight|month"


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


_DAY_NAMES = "|".join(_WDAYS + _MONTHS)
# 'not Monday', 'I can't do Monday', 'no to Monday' / 'Monday is no good', 'Monday doesn't work'
_REFUSED_BEFORE = re.compile(
    r"(?:\bnot|\bno(?:\s+to)?|\bnever|\bexcept|\bcan'?t\s+(?:do|make|manage|pay)"
    r"|\bcannot\s+(?:do|make|manage|pay)|\bcan\s+not\s+(?:do|make|pay)"
    r"|\bwon'?t\s+(?:be\s+able\s+to\s+)?(?:do|make|pay))\s+(?:(?:on|it|this|next)\s+)*$"
)
_REFUSED_AFTER = re.compile(
    r"^(?:'s|\s+is)?\s+(?:no good|not good|bad|out|impossible|not possible|not ok)\b"
    r"|^\s+(?:doesn'?t|does not|won'?t|will not|isn'?t|is not)\s+(?:work|suit|good|possible|ok)"
)


def _refused(s, start, end):
    return bool(_REFUSED_BEFORE.search(s[:start]) or _REFUSED_AFTER.search(s[end:]))


def _relative_days(s):
    """Days from today for 'in a week', 'a week from today', 'in 2 weeks', 'a fortnight',
    'in 10 days'; None when the text says none of these. 'In two weeks' is a day (14 days),
    'in a couple of weeks' stays vague."""
    if re.search(r"\b(?:in\s+(?:a|1)\s+week|(?:a|1)\s+week\s+(?:from\s+)?(?:today|now))\b", s):
        return 7
    if re.search(r"\bthis\s+time\s+next\s+week\b", s):
        return 7
    if re.search(r"\b(?:in\s+)?(?:a|1)\s+fortnight\b", s):
        return 14
    m = re.search(r"\b(?:in\s+(\d)\s+weeks?|(\d)\s+weeks?\s+(?:from\s+)?(?:today|now))\b", s)
    if m:
        return 7 * int(m.group(1) or m.group(2))
    m = re.search(
        r"\b(?:in\s+(\d{1,2})\s+days?|(\d{1,2})\s+days?\s+(?:from\s+)?(?:today|now))\b", s
    )
    if m:
        return int(m.group(1) or m.group(2))
    return None


def parse_spoken_date(text, today):
    """-> (iso or '', 'vague' | 'date' | 'none'). `today` is a datetime.date."""
    # a correction ('..., sorry I mean tomorrow', 'the 21st, no, the 20th') wins over
    # everything said before it; split before the commas are dropped (', no,' is the mark)
    seg = _last_segment(str(text or "").lower().replace("’", "'"), _DATE_WORDS)
    iso = re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", seg)
    if iso:
        y, mo, dd = iso[-1]
        d = _safe_date(int(y), int(mo), int(dd))
        return (d.isoformat(), "date") if d else ("", "none")
    raw = _norm_text(text)
    if not raw:
        return "", "none"
    s = _norm_text(seg, drop_times=True)
    rel = _relative_days(s)
    if rel is not None:
        return (today + datetime.timedelta(days=rel)).isoformat(), "date"
    if "day after tomorrow" in s:
        return (today + datetime.timedelta(days=2)).isoformat(), "date"
    if "tomorrow" in s:
        return (today + datetime.timedelta(days=1)).isoformat(), "date"
    if re.search(r"\btoday\b|\bright now\b|\bnow\b", s) and not re.search(r"\d", s):
        return today.isoformat(), "date"
    # 'after the 20th', 'before Friday': a period, not a day (rule: ask once for the day)
    if re.search(r"\b(?:after|before)\s+(?:the\s+)?(?:\d|(?:" + _DAY_NAMES + r")\b)", s):
        return "", "vague"
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
            elif (
                i + 2 < len(toks) and toks[i + 1] == "the" and re.fullmatch(r"\d{1,2}", toks[i + 2])
            ):
                day = int(toks[i + 2])  # 'December the third'
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
    # weekday, with this / next / a week on / after next
    hits = sorted(
        (m.start(), m.end(), i)
        for i, w in enumerate(_WDAYS)
        for m in re.finditer(r"\b" + w + r"\b", s)
    )
    if hits:
        # 'not Monday, Friday', 'I can't do Monday but Friday works': a refused day is no
        # answer; when one was refused the last day left wins, else the first one named
        ok = [h for h in hits if not _refused(s, h[0], h[1])]
        if not ok:
            return "", "none"
        _start, _end, i = ok[-1] if len(ok) < len(hits) else ok[0]
        w = _WDAYS[i]
        monday = today - datetime.timedelta(days=today.weekday())
        this_day = monday + datetime.timedelta(days=i)
        coming = this_day if this_day >= today else this_day + datetime.timedelta(days=7)
        week = datetime.timedelta(days=7)
        if re.search(r"\b" + w + r"\s+after\s+next\b|\bweek\s+after\s+next\b", s):
            d = this_day + 2 * week  # 'the Friday after next'
        elif (
            re.search(r"\bnext\s+(?:week\s+(?:on\s+)?)?" + w, s)
            or re.search(r"\b" + w + r"\s+(?:of\s+)?next\s+week\b", s)
            or "following" in s
        ):
            d = this_day + week  # rule 22: next <weekday> is in the following calendar week
        elif re.search(r"\bweek\s+(?:on|from)\s+" + w + r"|\b" + w + r"\s+week\b", s):
            d = coming + week  # 'a week on Friday', 'Friday week' (British)
        elif re.search(r"\bcoming\s+" + w, s):
            d = this_day if this_day > today else this_day + week  # 'this coming Monday'
        elif re.search(r"\bthis\s+" + w, s):
            d = this_day
        else:
            d = coming
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


_CUR = r"(?:pounds?|quid|euros?|dollars?|bucks|eur|gbp|usd)"
_SUB = r"(?:pence|pennies|cents?|p)"


def _amount_groups(s):
    """The separate numbers in `s`, after the number words are read: [(token index, value)]
    and the tokens."""
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s.replace("-", " "))
    toks = re.findall(r"\d+(?:\.\d+)?|[a-z']+", _words_to_number_tokens(s))
    nums = [(k, float(t)) for k, t in enumerate(toks) if re.fullmatch(r"\d+(?:\.\d+)?", t)]
    return nums, toks


_SMALL = "a|an|one|two|three|four|five|six|seven|eight|nine|ten|\\d+"


def _drop_dates(s):
    """Day, date and time words next to an amount are not part of it: '300 on the 20th',
    '100 pounds on Friday the 23rd', '50 in two weeks', '200 at 3pm'."""
    months = "|".join(_MONTHS)
    s = re.sub(r"\b(?:(?:twenty|thirty)[\s-]+)?(?:" + "|".join(_ORD) + r")\b", " ", s)
    s = re.sub(r"\b\d{1,2}\s*(?:st|nd|rd|th)\b", " ", s)
    s = re.sub(r"\b(?:on|by|before|after|until|till|from)\s+the\s+\d{1,2}\b(?![.\d])", " ", s)
    s = re.sub(r"\b\d{1,2}\s+(?:of\s+)?(?:" + months + r")\b", " ", s)
    s = re.sub(r"\b(?:" + months + r")\s+(?:the\s+)?\d{1,2}\b(?![.\d])", " ", s)
    s = re.sub(r"\b(?:" + "|".join(_WDAYS) + r"|today|tomorrow)\b", " ", s)
    s = re.sub(r"\b(?:in\s+)?(?:" + _SMALL + r")\s+(?:days?|weeks?|months?)\b", " ", s)
    s = re.sub(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.|o'?clock)", " ", s)
    s = re.sub(r"\bat\s+\d{1,2}(?::\d{2})?\b(?![.\d]|\s*" + _CUR + ")", " ", s)
    return s


def _thousands(m):
    v = float(m.group(1)) * 1000
    return str(int(v)) if v == int(v) else str(v)


def parse_amount(text, lo=None, hi=None):
    """'637.50', '637,50', 'six hundred and thirty-seven fifty', 'two thousand one hundred
    twenty-five pounds and ten pence', 'two hundred point zero five', 'two grand' -> float, or
    None. Separate numbers are never glued together: after a correction ('400, no, 450') the
    last one wins; two amounts without one ('200 or maybe 300', 'between 100 and 200') are
    None, an amount that cannot be read, so the caller asks again instead of guessing. The
    only pair read as one amount is pounds and pence ('210 pounds 50', 'six hundred thirty
    seven fifty'). Thousands written with a comma or a space ('1,320', '1 320') are one
    number; day and time words are dropped first ('300 on the 20th' is 300).

    `lo` / `hi` (optional, e.g. the floor and the full amount): a number said like a price,
    'three fifty' or 'one twenty six', reads as 3.50 / 1.26 as said; when that is below `lo`
    and the hundreds reading (350 / 126) lies in [lo, hi], the hundreds reading is taken.
    Without `lo` the amount is read as said."""
    raw = str(text or "").lower().replace("’", "'")
    raw = re.sub(r"[£€$]", " ", raw)
    raw = re.sub(r"(\d),(\d{2})(?!\d)", r"\1.\2", raw)
    raw = re.sub(
        r"\b(\d{1,3})((?:[ ,]\d{3})+)(?![\d,])",
        lambda m: m.group(1) + re.sub(r"[ ,]", "", m.group(2)),
        raw,
    )
    if not raw.strip():
        return None
    raw = re.sub(r"(\d+(?:\.\d+)?)\s*(?:k|grand|thousand)\b", _thousands, _drop_dates(raw))
    raw = re.sub(r"\bgrand\b", "thousand", raw)
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
    try:
        nums, toks = _amount_groups(s)
        if "half" in toks:
            return None  # 'a grand and a half', 'half of the 400': not read, asked again
        if len(nums) == 1:
            k, v = nums[0]
            nxt = toks[k + 1] if k + 1 < len(toks) else ""
            if re.fullmatch(_SUB, nxt) and not any(re.fullmatch(_CUR, t) for t in toks):
                return round(v / 100.0, 2)  # '50p', 'fifty pence'
            return v
        if len(nums) != 2:
            return None
        (i, a), (j, b) = nums
        glue = toks[i + 1 : j]
        nxt = toks[j + 1] if j + 1 < len(toks) else ""
        if b >= 100 or b != int(b):
            return None
        # pounds and pence: '210 pounds 50', '200 pounds and 5 pence', '100 and 50 pence',
        # 'six hundred thirty seven fifty' (nothing between the two numbers)
        cur = bool(glue) and re.fullmatch(_CUR, glue[0]) and all(g == "and" for g in glue[1:])
        if cur or not glue or (glue == ["and"] and re.fullmatch(_SUB, nxt)):
            said = round(a + b / 100.0, 2)
            hundreds = a * 100 + b
            if (
                not glue
                and a < 100
                and a == int(a)
                and lo is not None
                and said < float(lo)
                and float(lo) <= hundreds <= (float(hi) if hi is not None else hundreds)
            ):
                return hundreds  # 'three fifty' for a floor of 200: 350
            return said
        return None
    except Exception:
        return None


def parse_spoken_time(text, day_start=10, day_end=19):
    """'around eleven', '11am', 'half past two', '14:30', 'two thirty in the afternoon',
    'ten to five', 'twenty past ten', 'half five' (half past five) -> 'HH:MM', or ''. A bare
    hour that cannot be a working hour as said (1 to 7) is read as afternoon; 'morning' /
    'afternoon' / 'evening' / am / pm decide when given. A day of the month is no time ('the
    15th', '15 October'): the callback check also reads the day text for a time."""
    raw = str(text or "").lower().replace(".", " ").replace("-", " ")
    if not raw.strip():
        return ""
    s = re.sub(r"(\d)\s*(?:st|nd|rd|th)\b", r"\1th", _last_segment(raw))
    s = re.sub(r"(\d)(?!th\b)([a-z])", r"\1 \2", s)
    if re.search(r"\bmidday\b", s) or ("noon" in s and "after" not in s):
        return "12:00"
    pm = bool(re.search(r"\b(pm|p m|afternoon|evening|tonight)\b", s))
    am = bool(re.search(r"\b(am|a m|morning)\b", s))
    s = re.sub(r"\b(o'?clock|oclock|ish|around|about|at|by|maybe|minutes?)\b", " ", s)
    t = _words_to_number_tokens(s)
    # days, not times: 'the 15th', '15th of October', 'October 15', 'the 20', a year
    months = "|".join(_MONTHS)
    t = re.sub(r"\bthe\s+\d{1,2}\b", " ", t)  # before the ordinals: 'the 15th 11' keeps 11
    t = re.sub(r"\b\d{1,2}th\b(?:\s+of\s+[a-z]+)?", " ", t)
    t = re.sub(r"\b\d{1,2}\s+(?:of\s+)?(?:" + months + r")\b", " ", t)
    t = re.sub(r"\b(?:" + months + r")\s+\d{1,2}\b", " ", t)
    t = re.sub(r"\b(?:19|20)\d{2}\b|\b\d+\s+(?:days?|weeks?)\b", " ", t)
    t = re.sub(r"\s+", " ", t)
    hour, minute = None, 0
    m = re.search(r"\b(\d{1,2})[:h](\d{2})\b", t)
    past = re.search(r"\b(half|quarter|\d{1,2}) past (\d{1,2})\b", t)
    to = re.search(r"(?<!from )(?<!between )\b(quarter|5|10|20|25) to (\d{1,2})\b", t)
    half = re.search(r"\bhalf (\d{1,2})\b", t)
    if m and int(m.group(2)) < 60:
        hour, minute = int(m.group(1)), int(m.group(2))
    elif past and (not past.group(1).isdigit() or 1 <= int(past.group(1)) <= 30):
        minute = dict(half=30, quarter=15).get(past.group(1)) or int(past.group(1))
        hour = int(past.group(2))
    elif to:
        # 'ten to five' is 4:50; 'quarter to one' is 12:45 (not 0:45)
        hour = (int(to.group(2)) - 1) % 12 or 12
        minute = 60 - (15 if to.group(1) == "quarter" else int(to.group(1)))
    elif half:
        hour, minute = int(half.group(1)), 30  # British 'half five' is half past five
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
    """'four four seven one', '44 71', 'forty-four seventy-one' -> '4471'; 'two thousand and
    five' -> '2005', 'four hundred' -> '400' (a number with hundred / thousand is read whole)."""
    raw = str(text or "").lower().replace("-", " ")
    s = _last_segment(raw)
    if re.search(r"\b(?:hundred|thousand)\b", s):
        s = re.sub(
            r"(?:\b(?:a|" + "|".join(list(_UNITS) + list(_TENS)) + r")\s+)*"
            r"(?:hundred|thousand)\b(?:\s+(?:and|hundred|thousand|"
            + "|".join(list(_UNITS) + list(_TENS))
            + r")\b)*",
            lambda m: " " + _words_to_number_tokens(m.group(0)) + " ",
            s,
        )
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
