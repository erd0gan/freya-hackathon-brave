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
    "nought": 0,
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
    "end of",
    "weekend",
    "month from",
    "in a month",
    "few days",
    "couple of days",
    "salary",
    "payday",
    "pay day",
    "paid next",
    "get paid",
    "soon",
    "later this month",
    "next month",
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
    big = ("hundred", "thousand")

    def lead(j):  # 'a' or '2' before 'hundred' / 'thousand'
        return j + 1 < len(toks) and toks[j + 1] in big and re.fullmatch(r"a|\d{1,3}", toks[j])

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
        if t in _UNITS or t in _TENS or t in big or lead(i):
            total = 0
            cur = 0
            j = i
            used = False
            while j < len(toks):
                w = toks[j]
                if lead(j) and not cur:
                    cur = 1 if w == "a" else int(w)
                elif w in _UNITS:
                    if cur % 10 or (cur % 100 and _UNITS[w] >= 10):
                        break  # 'thirty seven fifty': a new number starts
                    cur += _UNITS[w]
                elif w in _TENS:
                    if cur % 100:
                        break
                    cur += _TENS[w]
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


_ABBR = dict(mon=0, tue=1, tues=1, wed=2, thu=3, thur=3, thurs=3, fri=4, sat=5, sun=6)


def _abbr(s):
    # 'fri', 'on sat', 'mon 19th' -> the weekday; 'I sat down' stays
    def f(m):
        if m.group(1) or m.group(3) or m.group(0).strip(" .") == s.strip(" .?!"):
            return (m.group(1) or "") + _WDAYS[_ABBR[m.group(2)]] + (m.group(3) or "")
        return m.group(0)

    return re.sub(
        r"\b((?:on|this|next|by|until|till|coming|following)\s+)?("
        + "|".join(_ABBR)
        + r")\b\.?(\s+(?:the\s+)?\d)?",
        f,
        s,
    )


def _day_n(today, n, nxt=False):
    # day n of this month (the next one when past or `nxt`); None: no such day, asked again
    y, mo = today.year, today.month
    if nxt or n < today.day:
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return _safe_date(y, mo, n)


def _lang(lang=None):
    # `lang`, else the call's language (LOCALE, from CFG), else English
    return str(lang or _locale("lang", "en") or "en").strip().lower()[:2]


def _locale(key, default=None):
    try:
        v = LOCALE.get(key)  # noqa: F821  (collections_lib, same code node)
        return default if v is None else v
    except Exception:
        return default


def _rewrite(text, mode, lang=None):
    # another language in the English forms the readers take, by its rules (LOCALE["rules"],
    # Turkish: _tr; a node embeds them only in a call of that language, see LANG_HOOKS)
    lg = _lang(lang)
    fn = (_locale("rules") or dict()).get(lg) if lg != "en" else None
    return fn(text, mode) if fn else text


def parse_spoken_date(text, today, lang=None):
    """-> (iso or '', 'vague' | 'date' | 'unclear' | 'none'). `today` is a datetime.date.
    'unclear' ('Friday the 20th' on a Tuesday, 'the 31st' in November) is asked again.
    `lang` "tr" reads Turkish ('haftaya salı', 'ayın 15'i'); None: the call's language."""
    text = _rewrite(text, "date", lang)
    # a correction ('..., sorry I mean tomorrow', 'the 21st, no, the 20th') wins over
    # everything said before it; split before the commas are dropped (', no,' is the mark)
    text = _abbr(str(text or "").lower().replace("’", "'"))
    seg = _last_segment(text, _DATE_WORDS)
    iso = re.findall(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", seg)
    if iso:
        y, mo, dd = iso[-1]
        d = _safe_date(int(y), int(mo), int(dd))
        return (d.isoformat(), "date") if d else ("", "none")
    dmy = re.findall(r"\b(\d{1,2})-(\d{1,2})-(\d{4})\b", seg)  # '20-10-2026', day first
    if dmy:
        d = _safe_date(int(dmy[-1][2]), int(dmy[-1][1]), int(dmy[-1][0]))
        return (d.isoformat(), "date") if d else ("", "unclear")
    raw = _norm_text(text)
    if not raw:
        return "", "none"
    seg = re.sub(r"\bfirst thing\b", " ", seg)  # 'first thing Monday' is Monday
    m = re.search(
        r"\b(first|second|third|fourth|last)\s+("
        + "|".join(_WDAYS)
        + r")\b(?:\s+(?:in|of)\s+(\w+))?",
        seg,
    )
    if m:
        # 'first Monday in November'; 'second Friday' is asked again, 'last Friday' is past
        mo = _month_of(m.group(3) or "")
        if mo and m.group(1) != "last":
            d = _safe_date(today.year + (mo < today.month), mo, 1)
            n = ["first", "second", "third", "fourth"].index(m.group(1))
            d += datetime.timedelta(days=(_WDAYS.index(m.group(2)) - d.weekday()) % 7 + 7 * n)
            if d.month == mo and d >= today:
                return d.isoformat(), "date"
        return "", "none" if m.group(1) == "last" else "unclear"
    s = _norm_text(seg, drop_times=True)
    hits = sorted(
        (m.start(), m.end(), i)
        for i, w in enumerate(_WDAYS)
        for m in re.finditer(r"\b" + w + r"\b", s)
    )
    # 'not Monday, Friday', 'I can't do Monday but Friday works': a refused day is no
    # answer; when one was refused the last day left wins, else the first one named
    ok = [h for h in hits if not _refused(s, h[0], h[1])]
    rel = _relative_days(s)
    if rel is not None and not hits:
        return (today + datetime.timedelta(days=rel)).isoformat(), "date"
    # 'in 3 business days', 'three working days from now': counted on the call's calendar
    bd = re.search(
        r"\b(?:in\s+(\d{1,2}|a)\s+(?:business|working)\s+days?"
        r"|(\d{1,2}|a)\s+(?:business|working)\s+days?\s+(?:from\s+)?(?:today|now))\b",
        s,
    )
    if bd and not hits:
        n = int((bd.group(1) or bd.group(2)).replace("a", "1"))
        cal = _locale("cal") or dict()
        return nth_business_day_after(today, n, cal).isoformat(), "date"  # noqa: F821
    wk = re.search(r"\b(a|\d)\s+weeks?\s+from\s+tomorrow\b", s)
    if wk and not hits:
        return (
            today + datetime.timedelta(days=1 + 7 * int(wk.group(1).replace("a", "1")))
        ).isoformat(), "date"
    if "day after tomorrow" in s:
        return (today + datetime.timedelta(days=2)).isoformat(), "date"
    if "tomorrow" in s:
        return (today + datetime.timedelta(days=1)).isoformat(), "date"
    # 'after the 20th', 'before Friday', 'a month from now': a period, not a day (rule: ask
    # once for the day)
    if re.search(r"\b(?:after|before)\s+(?:the\s+)?(?:\d|(?:" + _DAY_NAMES + r")\b)", s) or (
        re.search(r"\b(?:from|after)\s+(?:today|now)\b", s)
    ):
        return "", "vague"
    if re.search(r"\b(?:today|tonight|right now|now|this (?:evening|afternoon|morning))\b", s):
        if not re.search(r"\d", s):
            return today.isoformat(), "date"
    nxt = bool(re.search(r"\bnext\s+month\b", s))
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
    wd = (ok[-1] if len(ok) < len(hits) else ok[0])[2] if ok else None
    if month and day:
        y = year or today.year
        d = _safe_date(y, month, day)
        if d and not year and d < today:
            d = _safe_date(y + 1, month, day)
        if d and wd is not None and d.weekday() != wd:
            return "", "unclear"  # 'Friday 20 October' when the 20th is a Tuesday
        return (d.isoformat(), "date") if d else ("", "unclear")
    # weekday, with this / next / a week on / after next
    if hits:
        if not ok:
            return "", "none"
        if len({h[2] for h in ok}) > 1 and re.search(r"\bor\b", s):
            return "", "unclear"  # 'Thursday or Friday'
        i = wd
        w = _WDAYS[i]
        # the week starts on the call's week_start (a Sunday-Thursday week on Sunday)
        ws = int(_locale("week_start", 0) or 0) % 7
        first = today - datetime.timedelta(days=(today.weekday() - ws) % 7)
        this_day = first + datetime.timedelta(days=(i - ws) % 7)
        coming = this_day if this_day >= today else this_day + datetime.timedelta(days=7)
        week = datetime.timedelta(days=7)
        wk = re.search(r"\b(\d|a)\s+weeks?\b", s)
        if re.search(r"\b" + w + r"\s+after\s+next\b|\bweek\s+after\s+next\b", s):
            d = this_day + 2 * week  # 'the Friday after next'
        elif wk:
            # 'two weeks from Friday', 'Friday in two weeks', 'a week on Friday'
            d = coming + week * (1 if wk.group(1) == "a" else int(wk.group(1)))
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
        num = None if wk else re.search(r"\b(\d{1,2})\b", s)
        if num:
            # 'Monday the 26th': the number when the weekday matches it, else asked again
            dd = _day_n(today, int(num.group(1)), nxt)
            if dd is None or dd.weekday() != i:
                return "", "unclear"
            return dd.isoformat(), "date"
        return d.isoformat(), "date"
    # 'the 26th' alone
    m = re.search(r"\b(?:the\s+)?(\d{1,2})\b", s)
    if (m and any(v in raw for v in ("the ",))) or (
        m and re.fullmatch(r"(?:on\s+)?(?:the\s+)?\d{1,2}", s)
    ):
        n = int(m.group(1))
        if 1 <= n <= 31:
            d = _day_n(today, n, nxt)
            return (d.isoformat(), "date") if d else ("", "unclear")
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
    v = float(m.group(1) or m.group(2)) * 1000
    return str(int(v)) if v == int(v) else str(v)


def parse_amount(text, lo=None, hi=None, lang=None):
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
    Without `lo` the amount is read as said. A share ('50%', '20 percent') and a European
    '1.250,50' are None.

    `lang` "tr" (None: the call's language) reads Turkish: '1.250' is 1250, '1.250,50' and
    '1250,5' have a decimal comma, 'bin iki yüz elli lira', 'yüz elli lira kırk kuruş',
    'iki bin buçuk'; 'yarısı' and 'yüzde elli' are None (amount_like says they name one)."""
    text = _rewrite(text, "amount", lang)
    raw = str(text or "").lower().replace("’", "'")
    if re.search(r"%|\bper\s*cent|\d\.\d{3},\d", raw):
        return None
    raw = re.sub(r"[£€$]", " ", raw)
    raw = re.sub(r"(\d),(\d{2})(?!\d)", r"\1.\2", raw)
    raw = re.sub(
        r"\b(\d{1,3})((?:[ ,]\d{3})+)(?![\d,])",
        lambda m: m.group(1) + re.sub(r"[ ,]", "", m.group(2)),
        raw,
    )
    if not raw.strip():
        return None
    # '2 thousand 5 hundred' is read with the words below
    raw = re.sub(
        r"(\d+(?:\.\d+)?)\s*(?:k|grand)\b|(\d+\.\d+)\s*thousand\b", _thousands, _drop_dates(raw)
    )
    raw = re.sub(r"\bgrand\b", "thousand", raw)
    s = _last_segment(raw)
    if re.search(r"\bpoint\b", s):
        # a spoken decimal point: what follows is a digit sequence, not a number
        # ('point zero five' is .05, not .5); only when a number stands on both sides
        left, right = re.split(r"\bpoint\b", s, maxsplit=1)
        digits = _point_digits(re.sub(r"\b(pounds?|euros?|dollars?|eur|gbp|usd)\b", " ", right))
        whole = parse_amount(left, lang="en") if digits else None  # already rewritten
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
            cur = any(re.fullmatch(_CUR, t) for t in toks)
            if re.fullmatch(_SUB, nxt) and v == int(v) and not cur:
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


def _hour_of(x, default):
    try:
        return int(str(x).strip().split(":")[0])
    except Exception:
        return default


def parse_spoken_time(text, day_start=None, day_end=None, lang=None):
    """'around eleven', '11am', 'half past two', '14:30', 'two thirty in the afternoon',
    'ten to five', 'twenty past ten', 'half five' (half past five) -> 'HH:MM', or ''. A bare
    hour before the window that fits it in the afternoon is afternoon ('three' 15:00 for
    10-19, 'eight' 20:00 for 13-21; window: `day_start` / `day_end`, else the call's callback
    window, else 10-19); 'morning' / 'afternoon' / 'evening' / am / pm decide when given. A day
    of the month is no time ('the 15th', '15 October'): the callback check also reads the day
    text for a time. A range or a bound ('between 2 and 4', '11 or 12', 'after 5', '2-4pm') is
    no time: '' (asked again)."""
    if day_start is None or day_end is None:
        cb = (_locale("cal") or dict()).get("callback") or dict()
        day_start = cb.get("start") if day_start is None else day_start
        day_end = cb.get("end") if day_end is None else day_end
    ds, de = _hour_of(day_start, 10), _hour_of(day_end, 19)
    text = _rewrite(text, "time", lang)
    raw = str(text or "").lower()
    if not raw.strip() or re.search(r"\d\s*-\s*\d", raw):
        return ""
    raw = raw.replace(".", " ").replace("-", " ")
    s = re.sub(r"(\d)\s*(?:st|nd|rd|th)\b", r"\1th", _last_segment(raw))
    if re.search(r"\b(?:between|after|before|until|till|or(?! so\b)|from \S+ to)\b", s):
        return ""
    s = re.sub(r"(\d)(?!th\b|h\d)([a-z])", r"\1 \2", s)
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
    if (pm and hour < 12) or (not am and 1 <= hour < min(ds, 12) and hour + 12 <= de):
        hour += 12
    elif am and hour == 12:
        hour = 0
    return "%02d:%02d" % (hour, minute)


def digits_from_speech(text, lang=None):
    """'four four seven one', '44 71', 'forty-four seventy-one' -> '4471'; 'two thousand and
    five' -> '2005', 'four hundred' -> '400' (a number with hundred / thousand is read whole).
    `lang` "tr": 'sıfır sıfır yedi üç' -> '0073', 'kırk dört yetmiş bir' -> '4471'."""
    text = _rewrite(text, "digits", lang)
    raw = str(text or "").lower().replace("-", " ")
    s = _last_segment(raw)
    if re.search(r"\b(?:hundred|thousand)\b", s):
        s = re.sub(
            r"(?:\b(?:a|\d{1,3}|" + "|".join(list(_UNITS) + list(_TENS)) + r")\s+)*"
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


# ---- Turkish ------------------------------------------------------------------------------
# In a Turkish call (CFG["lang"] "tr") what the customer said is folded to ASCII and rewritten
# into the English forms the readers above take ('haftaya salı' -> 'next week tuesday', 'ayın
# on beşi' -> 'the 15th', 'bin iki yüz elli lira' -> '1250 pounds'), so one tested reading
# serves both languages. Language only here: month, weekday and number words, suffixes, idioms.
_TRK_FOLD = str.maketrans("İIıŞşĞğÜüÖöÇçÂâÎîÛû’‘`´", "iiissgguuooccaaiiuu''''")


def fold(text):
    """Turkish-aware lower case folded to ASCII: 'İ' and 'I' -> 'i', 'ş' -> 's', 'ğ' -> 'g'
    ('KREDİ KARTI' -> 'kredi karti'); the combining marks of decomposed text are dropped. The
    sandbox has no unicodedata, so the table is explicit. Compare folded text with folded
    words: word boundaries and [a-z] then work on Turkish too."""
    return re.sub("[̀-ͯ]", "", str(text or "").translate(_TRK_FOLD).lower())


_TRK_MONTHS = [
    "ocak",
    "subat",
    "mart",
    "nisan",
    "mayis",
    "haziran",
    "temmuz",
    "agustos",
    "eylul",
    "ekim",
    "kasim",
    "aralik",
]
_TRK_WDAYS = ["pazartesi", "sali", "carsamba", "persembe", "cuma", "cumartesi", "pazar"]
_TRK_NUM = dict(
    sifir=0,
    bir=1,
    iki=2,
    uc=3,
    dort=4,
    dord=4,  # 'dördü', 'dörde', 'dördüncü'
    bes=5,
    alti=6,
    yedi=7,
    sekiz=8,
    dokuz=9,
    on=10,
    yirmi=20,
    otuz=30,
    kirk=40,
    elli=50,
    altmis=60,
    yetmis=70,
    seksen=80,
    doksan=90,
    yuz=100,
    bin=1000,
    milyon=1000000,
)
_TRK_NUMRX = "|".join(sorted(_TRK_NUM, key=len, reverse=True))
# What may follow a word, folded: ordinal (-inci), possessive (-i, -si), case (-de, -den, -e,
# -i, -in), '-ki' ('yarınki'). 'önce' is not 'on' + a suffix, nor is 'onay'.
_TRK_SFX = r"(?:[iu]?nc[iu])?(?:[sy]?[iu])?(?:n?[iu]n|n?[dt][ae]n?|[ny]?[ae]|k[iu]|[ny][iu])?"
_TRK_WORDS = sorted(
    [(m, _MONTHS[i]) for i, m in enumerate(_TRK_MONTHS)]
    + [("ocag", "january"), ("aralig", "december")]  # 'ocağın', 'aralığa'
    + [(w, _WDAYS[i]) for i, w in enumerate(_TRK_WDAYS)]
    + [("yarin", "tomorrow"), ("bugun", "today"), ("lira", "pounds"), ("tl", "pounds")]
    + [("try", "pounds"), ("kurus", "pence"), ("cift", "double"), ("ceyrek", "quarter")]
    + [("yarim", "half"), ("yari", "half")],
    key=lambda x: -len(x[0]),
)
_TRK_DAYS_EN = "|".join(_WDAYS)
_TRK_MONTHS_EN = "|".join(_MONTHS)
# Idioms first, on the folded text (order matters: 'yarından sonra' before 'yarın').
_TRK_PHRASES = [
    (r"\byuzde\b|%", " per cent "),
    (r"\b(?:bu |o )?yuz(?:un)?den\b", " "),  # 'bu yüzden': 'that is why', no hundred
    (r"\byarindan sonra\w*|\bobur ?gun\w*|\bobursu ?gun\w*", " day after tomorrow "),
    (r"\bhafta ?sonu\w*", " weekend "),
    (r"\bay(?:in)? ?sonu\w*|\bay bit\w*", " end of the month "),
    (r"\bay(?:in)? ?basi\w*", " beginning of the month "),
    (r"\bay(?:in)? ?ortasi\w*", " middle of the month "),
    (r"\bmaas\w*(?: (?:yat|gel|al|gun|cik)\w*)?", " payday "),
    (r"\bbir ?kac (?:gun|hafta)\w*", " a few days "),
    (r"\byakinda\b|\ben kisa (?:zamanda|surede)\b", " soon "),
    (r"\b(?:bir ara|ileride|ilerde|param ol\w*|elime para gec\w*)", " sometime "),
    (r"\b(?:gelecek|onumuzdeki|bir sonraki|sonraki|haftaya) ay(?:in|a|da)?\b", " next month "),
    (r"\b(?:bu )?hafta ?ic(?:i|inde)\b", " sometime this week "),
    (r"\bobur hafta\w*", " week after next "),
    (
        r"\b(?:gelecek|onumuzdeki|bir sonraki|sonraki) hafta(?:ya|nin)?\b|\bhaftaya\b",
        " next week ",
    ),
    (r"\bbu hafta(?:nin)?\b", " this week "),
    (r"\b(?:gelecek|onumuzdeki|bir sonraki|sonraki)\b", " next "),
    (r"\bbu (?=(?:pazar|sali|carsamba|persembe|cuma|aksam|sabah|ogle|gece))", " this "),
    (r"\bogleden sonra\b|\bikindi\w*", " afternoon "),
    (r"\bogleden once\b|\bsabah\w*", " morning "),
    (r"\bogle\w*", " midday "),
    # 'geçe' (past, in 'üçü çeyrek geçe') folds to 'gece' (night): after a number it is 'past'
    (r"\b(ceyrek|\d+|(?:" + _TRK_NUMRX + r")\w*) gece\b", r"\1 xgecex"),
    (r"\baksam\w*|\bgece(?:leyin|si)?\b", " evening "),
    (r"\bsaat\b", " at "),
    (r"\b(?:civari\w*|sularinda|gibi|falan|filan)\b", " around "),
    (r"\bis gun\w*", " business days "),
    (r"\bgunu\b", " "),  # 'cuma günü' is Friday
    # a refused day ('yarın değil', 'cuma olmaz', 'bugün ödeyemem') is dropped after the rewrite
    (
        r"\b(?:uygun degil\w*|musait degil\w*|degil(?! mi)|olmaz|olmuyor|uymaz|uymuyor|yapamam"
        r"|odeyemem|odeyemiyorum|veremem)\b",
        " xnotx ",
    ),
    (r"\bdegil mi\b", " "),
    (
        r"\b(?:yok|pardon|aslinda|daha dogrusu|ozur dilerim|affedersin\w*|kusura bakma\w*"
        r"|yanlis soyledim|duzelt\w*)\b",
        " sorry ",
    ),
    (r"\bhayir\b", " no "),
    (r"\b(?:ya da|veya|yahut)\b", " or "),  # 'salı ya da çarşamba': asked again
]
# Words of an amount that is no number (amount_like reads them).
_TRK_AMOUNT = [
    (r"\b(?:asgari|en az|en dusuk)\w*", " minimum "),
    (r"\b(?:tamami\w*|tam (?:tutar|borc|miktar)\w*|hepsi\w*|tumu\w*|butun\w*)", " full "),
    (r"\b(?:biraz|bir kism\w*|kismen|kismi)\b", " some "),
]
# A number word with a suffix ('onu', 'biri', 'ikisi', 'beşte') is a number only where one is
# expected: after these words, inside a longer number, or as an ordinal.
_TRK_MARK = ("at", "morning", "evening", "afternoon", "midday", "month")


def _tr_marks(s):
    """Turkish number marks: '.' before exactly three digits groups thousands ('1.250',
    '12.500'), ',' is the decimal mark ('1.250,50', '1250,5'). '1,250.50' stays English."""
    s = str(s or "").replace("₺", " ")
    if re.search(r"\d,\d{3}\.\d", s):
        return s
    s = re.sub(
        r"(?<![\d.])(\d{1,3})((?:\.\d{3})+)(?![\d.])",
        lambda m: m.group(1) + m.group(2).replace(".", ""),
        s,
    )
    return re.sub(r"(\d),(\d{1,2})(?!\d)", r"\1.\2", s)


def _tr_groups(atoms):
    """[(value, suffix, is_digits, word)] -> numbers: dicts with val, suf, n (atoms), src
    (words), last (the last atom's value). Turkish numbers add up left to right ('bin iki yüz
    elli' 1250, 'on iki yüz' 1200); a unit after a unit or a tens after a tens starts a new
    number ('kırk dört yetmiş bir' is 44 and 71, 'dört elli' 4 and 50); 'sıfır' stands alone;
    a suffix ends a number."""
    out = []
    g = None
    for v, suf, dig, word in atoms:
        if g is not None:
            cur, tot = g["cur"], g["tot"]
            fits = not (dig and g["dig"]) and v != 0 and g["last"] != 0
            if v < 10:
                fits = fits and cur % 10 == 0
            elif v < 100:
                fits = fits and cur % 100 == 0
            elif v == 100:
                fits = fits and cur < 100
            elif v == 1000:
                fits = fits and tot % 1000000 == 0
            elif v == 1000000:
                fits = fits and tot == 0
            else:
                fits = fits and cur == 0 and tot > 0  # '2 bin 500'
            if word == "on" and suf and g["dig"]:
                fits = False  # '500 onu cuma': 'onu' is 'it' there, judged alone
            if not fits:
                out.append(g)
                g = None
        if g is None:
            g = dict(cur=0, tot=0, n=0, src=[], dig=False, last=None, suf="")
        if v == 100:
            g["cur"] = max(g["cur"], 1) * 100
        elif v == 1000 and not dig:
            g["tot"] += max(g["cur"], 1) * 1000
            g["cur"] = 0
        elif v == 1000000 and not dig:
            g["tot"] = (g["tot"] + max(g["cur"], 1)) * 1000000
            g["cur"] = 0
        else:
            g["cur"] += v
        g["n"] += 1
        g["src"].append(word)
        g["dig"] = dig
        g["last"] = v
        g["suf"] = suf
        g["val"] = g["tot"] + g["cur"]
        if suf or v == 0 or (dig and v >= 1000):
            out.append(g)
            g = None
    if g is not None:
        out.append(g)
    return out


def _tr_word(w):
    """A folded Turkish word that is no number: its English form, or the word itself."""
    for stem, en in _TRK_WORDS:
        if w.startswith(stem) and re.fullmatch(_TRK_SFX, w[len(stem) :]):
            return en
    return w


def _tr_num(g, prev, nxt, nxt2, mode):
    """One number as the English readers take it, or its words when it is no number here."""
    suf, v, src = g["suf"], g["val"], g["src"]
    if isinstance(v, str):
        return v
    # digits as written ('0073' keeps its zeros)
    plain = src[0] if g["n"] == 1 and g["dig"] else str(v)
    ordinal = bool(re.match(r"[iu]?nc[iu]", suf))
    if suf and g["n"] == 1 and not g["dig"] and not ordinal and prev not in _TRK_MARK:
        small = v in (2, 3, 4, 5, 6, 7, 8, 9, 20, 30) and mode in ("date", "time", "dob")
        big = mode == "amount" and ((v >= 20 and src[0] != "yuz") or v == 1000)
        month = mode != "amount" and re.fullmatch(_TRK_MONTHS_EN, prev)  # 'kasımın biri'
        if not (small or big or month or prev.startswith("ayin")):
            return " ".join(src) + suf  # the word as said ('onu', 'ikisini')
    if mode == "digits":
        return plain
    if g.get("half"):
        if mode == "amount":
            v += 500 if g["last"] == 1000 else 500000 if g["last"] == 1000000 else 0.5
            return ("%.2f" % v).rstrip("0").rstrip(".")
        return "%d:30" % v
    if src == ["bir"] and not suf:
        return "1" if re.fullmatch(_TRK_MONTHS_EN, nxt) else "a"  # 'bir hafta': a week
    if mode == "dob" and not g["dig"] and g["last"] == 100 and v >= 1000:
        return str(v // 100)  # 'bin dokuz yüz', the rest not said yet: '19', incomplete
    months = re.fullmatch(_TRK_MONTHS_EN, prev) or re.fullmatch(_TRK_MONTHS_EN, nxt)
    clock = nxt in ("quarter", "xgecex", "var") or nxt2 in ("xgecex", "var") or prev == "at"
    poss = re.match(r"s?[iu](?:n|$)", suf)
    day = 1 <= v <= 31 and not months and not clock
    if day and (prev.startswith("ayin") or ordinal or (poss and mode != "amount")):
        return ("on the %dth" if mode == "amount" else "the %dth") % v
    if ordinal:
        return "%dth" % v
    return plain


def _tr(text, mode="date"):
    """Turkish speech rewritten into the English forms the readers above take. `mode`: "date",
    "time", "amount", "digits" or "dob" (a number with a suffix is a day in a date, an hour in
    a time, nothing special in an amount)."""
    s = " " + fold(_tr_marks(text) if mode == "amount" else text) + " "
    if mode == "amount":
        for rx, rep in _TRK_AMOUNT:
            s = re.sub(rx, rep, s)
    if mode == "time":
        s = re.sub(r"\b(?:saat )?yarim(?:da)?\b", " 12:30 ", s)  # 'yarımda': half past twelve
    for rx, rep in _TRK_PHRASES:
        s = re.sub(rx, rep, s)
    toks = re.findall(r"\d+(?:[-.,:/]\d+)*(?:'?[a-z]+)?|[a-z]+(?:'[a-z]+)*|[^\sa-z\d]", s)
    items = []
    atoms = []

    def flush():
        for g in _tr_groups(atoms):
            items.append(["n", g])
        del atoms[:]

    for t in toks:
        m = re.fullmatch(r"(\d+(?:[-.,:/]\d+)*)'?([a-z]*)", t)
        if m:
            if re.fullmatch(r"\d+", m.group(1)):
                atoms.append((int(m.group(1)), m.group(2), True, m.group(1)))
            else:  # '11:30', '1250.50', '14.03.1988' as written
                flush()
                items.append(["n", dict(val=m.group(1), suf="", n=1, src=[t], dig=True)])
            if m.group(2):
                flush()
            continue
        w = t.replace("'", "")
        m = re.fullmatch("((?:" + _TRK_NUMRX + ")+)(" + _TRK_SFX + ")", w)
        if m:
            ws = re.findall(_TRK_NUMRX, m.group(1))
            for k, x in enumerate(ws):
                atoms.append((_TRK_NUM[x], m.group(2) if k == len(ws) - 1 else "", False, x))
            if m.group(2):
                flush()
            continue
        flush()
        if w.startswith("bucuk") and items and items[-1][0] == "n":
            items[-1][1]["half"] = True  # 'on bir buçuk', 'iki bin buçuk'
            continue
        items.append(["w", _tr_word(w)])
    flush()
    out = []
    for k, (kind, x) in enumerate(items):
        if kind == "w":
            out.append(x)
            continue
        near = [
            items[j][1] if 0 <= j < len(items) and items[j][0] == "w" else ""
            for j in (k - 1, k + 1, k + 2)
        ]
        out.append(_tr_num(x, near[0], near[1], near[2], mode))
    r = " " + " ".join(out) + " "
    # Turkish order to English: 'üçü çeyrek geçe' (quarter past three), 'üçe on var' (ten to
    # three), 'iki gün sonra' (in two days), 'cumadan sonra' (after Friday)
    r = re.sub(r"\b(\d{1,2}) (quarter|\d{1,2}) xgecex", r"\2 past \1", r)
    r = re.sub(r"\b(\d{1,2}) (quarter|\d{1,2}) var\b", r"\2 to \1", r)
    r = re.sub(r"\ba (gun|business days)", r"1 \1", r)
    unit = dict(gun="days", hafta="weeks", ay="months")
    r = re.sub(
        r"\b(\d+|a) (gun|hafta|ay|business days)\w* sonra\w*",
        lambda m: "in " + m.group(1) + " " + unit.get(m.group(2), m.group(2)),
        r,
    )
    r = re.sub(r"\bin a (week|month)s\b", r"in a \1", r)  # 'bir hafta sonra': in a week
    day = r"(?:" + _TRK_DAYS_EN + r"|tomorrow|the \d+th)"
    r = re.sub(r"\b(" + day + r") once\w*", r"before \1", r)
    r = re.sub(r"\b(" + day + r") sonra\w*", r"after \1", r)
    r = re.sub(r"\b(" + _TRK_MONTHS_EN + r") ay\w*", r"\1", r)  # 'ekim ayının 20'si'
    refused = (
        r"(?:day after tomorrow|tomorrow|today|(?:(?:this|next) (?:week )?)?(?:"
        + _TRK_DAYS_EN
        + r")|(?:on )?the \d+th|\d+ (?:"
        + _TRK_MONTHS_EN
        + r")(?: \d{4})?|(?:"
        + _TRK_MONTHS_EN
        + r") \d+)\s*xnotx"
    )
    r = re.sub(refused, " , ", r)
    r = re.sub(r"\d+(?:\.\d+)?(?: pounds| pence)?\s*xnotx", " sorry ", r)
    r = re.sub(r"\b(?:xnotx|xgecex|ayin\w*|kadar|dek)\b", " ", r)
    return re.sub(r"\s+", " ", r).strip()


# Words of a number or a share in an amount text: one that cannot be read is asked again
# (negotiation_lib.AMOUNT_WORDS, in the call's language).
_AMOUNT_EN = re.compile(
    r"\d|\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty|thirty|"
    r"forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|grand|half|quarter|third|"
    r"percent|minimum|lowest|least|full|whole)\b",
    re.I,
)


def amount_like(text, lang=None):
    """Does an amount text name an amount, read or not ('half of it', '50%', 'the minimum';
    Turkish 'yarısı', 'yüzde elli', 'dört yüz')? With parse_amount None it is an amount that
    could not be read: asked again ('Which amount do you mean?'), never the full amount."""
    s = str(text or "")
    if _AMOUNT_EN.search(s) or "%" in s:
        return True
    r = _rewrite(s, "amount", lang)
    return r != s and bool(re.search(r"\d|\b(?:per cent|half|quarter|minimum|full|some)\b", r))


def _rules(lang, fn):
    """Register a language's rewrite for the readers (LOCALE["rules"])."""
    try:  # noqa: SIM105  (no contextlib in the sandbox)
        LOCALE.setdefault("rules", dict())[lang] = fn  # noqa: F821  (collections_lib)
    except Exception:
        pass
    return fn


# Run where the whole library runs (tests, identity hooks); a code node built from a subset of
# the library gets this line from embed for a call in that language (an unused name: the
# subset never pulls the Turkish rules into an English node).
_TRK_HOOK = _rules("tr", _tr)
LANG_HOOKS = dict(tr="_rules('tr', _tr)")
