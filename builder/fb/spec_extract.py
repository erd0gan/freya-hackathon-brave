"""Bank documents (a brief, or a case folder's policy text: one Markdown text, sections per
document) + tool API -> spec.

One model call extracts a JSON spec (asked again once when the reply is not a usable spec);
deterministic checks compare it with the tool API and the documents; a second call repairs
what the checks reject. `to_generator` turns the spec into the input of fb.generate.build.
Nothing here knows any particular brief: the documents may be in any language (the language
pack, fb.langpack, holds what the agent says where the documents give no words), and the
grounding checks read English and Turkish numbers, dates and times.
"""

from __future__ import annotations

import contextlib
import copy
import datetime
import difflib
import itertools
import json
import re
import string
import unicodedata
from typing import Any

from fb import langpack
from fb.generate import (
    DEFAULT_CALL_VARS,
    conform,
    find_field,
    match_enum,
    norm,
    paid_close_reason,
)
from fb.llm import LLMError, chat, parse_json
from fb.nodecode.embed import namespace
from fb.openapi_tools import ToolDef, fixed_value, leaf_paths, path_schema
from fb.trace import Trace

POLICY_DEFAULTS: dict[str, Any] = namespace()["POLICY_DEFAULTS"]


# ---------------------------------------------------------------- reading text in any language
# Language knowledge only (letters, number words, month names, cue words): never a bank's
# values. Everything is compared after fold(), so 'İŞ GÜNÜ', 'iş günü' and 'is gunu' meet.
_FOLD = str.maketrans(
    {
        "İ": "i", "I": "i", "ı": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g", "ç": "c",
        "Ç": "c", "ö": "o", "Ö": "o", "ü": "u", "Ü": "u", "’": "'", "‘": "'", "“": '"',
        "”": '"', "„": '"', "«": '"', "»": '"', "–": "-", "—": "-", " ": " ",
    }
)  # fmt: skip


def fold(text: Any) -> str:
    """Lower case without accents, Turkish-aware: 'İŞ GÜNÜ' -> 'is gunu', 'Iğdır' -> 'igdir'.
    Both Turkish i's become 'i' (str.lower() turns 'İ' into 'i' plus a combining dot)."""
    t = unicodedata.normalize("NFKD", str(text or "").translate(_FOLD))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


def _wordlist(text: str) -> list[str]:
    return text.split()


_TR_LETTERS = set("ğışİĞŞ")  # letters no other common Latin-script language uses this way
_TR_STOP = set(
    _wordlist(
        "ve bir bu ile icin olarak veya da de mi ne cok gibi daha sonra once kadar olan her ya "
        "degil ise gore arasi arasinda musteri gun gunu odeme tarih tarihi borc kart olur eder "
        "edilir yapilir icinde uzere tum hic sadece eger ancak oldugu olmasi bunun sizin "
        "lutfen evet hayir"
    )
)
_EN_STOP = set(
    _wordlist(
        "the and of to is in for that with on are be or not if as by this it when before after "
        "must will call customer from at an any only then than which who"
    )
)


def detect_language(text: str) -> tuple[str, bool]:
    """(code, confident) from the letters and the commonest words of `text`: 'tr' or 'en'
    (an unknown language reads as ('en', False)). Confident only with enough words and a clear
    margin; a text with Turkish letters stripped ('is gunu') is still read from its words."""
    raw = str(text or "")
    words = re.findall(r"[a-z]+", fold(raw))
    if len(words) < 15:
        return langpack.DEFAULT_LANGUAGE, False
    tr = sum(w in _TR_STOP for w in words) / len(words)
    en = sum(w in _EN_STOP for w in words) / len(words)
    letters = sum(c in _TR_LETTERS for c in raw)
    if (letters >= 3 and tr > en) or (tr >= 0.04 and tr >= 2 * en):
        return "tr", True
    if en >= 0.06 and en >= 2 * tr and letters < 3:
        return "en", True
    return ("tr" if tr > en else "en"), False


_EN_UNITS = _wordlist(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
)
_EN_TENS = _wordlist("twenty thirty forty fifty sixty seventy eighty ninety")
_EN_ORD_SMALL = _wordlist(
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth"
)
_TR_UNITS = _wordlist("sifir bir iki uc dort bes alti yedi sekiz dokuz")
_TR_TENS = _wordlist("on yirmi otuz kirk elli altmis yetmis seksen doksan")


def _en_ordinal(n: int) -> str:
    if n < len(_EN_ORD_SMALL):
        return _EN_ORD_SMALL[n]
    if n < 20:
        return _EN_UNITS[n] + "th"
    t, u = divmod(n, 10)
    return _EN_TENS[t - 2][:-1] + "ieth" if not u else f"{_EN_TENS[t - 2]}-{_en_ordinal(u)}"


def _tr_ordinal(word: str) -> str:
    # vowel harmony of the ordinal suffix (folded spelling): -inci/-uncu, after a vowel -nci/-ncu
    last = [c for c in word if c in "aeiou"][-1]
    suffix = "uncu" if last in "ou" else "inci"
    out = word + (suffix[1:] if word[-1] in "aeiou" else suffix)
    return out.replace("dortuncu", "dorduncu")  # dört -> dördüncü


def _number_words(lang: str) -> dict[str, int]:
    """Folded spoken forms of 0..100 (cardinal and ordinal) -> value, for `lang`. English
    words are always known (an English sentence in a Turkish text); Turkish ones only for a
    Turkish text ('on' is ten in Turkish and a preposition in English)."""
    out: dict[str, int] = {}
    for n in range(101):
        if n < 20:
            en = [_EN_UNITS[n]]
        elif n == 100:
            en = ["one hundred", "a hundred", "hundred"]
        else:
            t, u = divmod(n, 10)
            en = [_EN_TENS[t - 2] + (f"-{_EN_UNITS[u]}" if u else "")]
        for w in en:
            out[w] = n
            out[w.replace("-", " ")] = n
        if n < 100:
            out[_en_ordinal(n)] = n
            out[_en_ordinal(n).replace("-", " ")] = n
        if lang == "tr":
            if n < 10:
                tr = _TR_UNITS[n]
            elif n == 100:
                tr = "yuz"
            else:
                t, u = divmod(n, 10)
                tr = _TR_TENS[t - 1] + (" " + _TR_UNITS[u] if u else "")
            out[tr] = n
            out[tr.replace(" ", "")] = n
            if n:
                out[_tr_ordinal(tr)] = n
    return out


_NUMWORD_RE: dict[str, re.Pattern[str]] = {}
_WORD_VALUES: dict[str, dict[str, int]] = {}


def _numword_re(lang: str) -> tuple[re.Pattern[str], dict[str, int]]:
    lang = "tr" if lang == "tr" else "en"
    if lang not in _NUMWORD_RE:
        words = _number_words(lang)
        alts = sorted(words, key=len, reverse=True)
        _NUMWORD_RE[lang] = re.compile(
            r"(?<![a-z0-9])(" + "|".join(re.escape(w) for w in alts) + r")(?![a-z0-9])"
        )
        _WORD_VALUES[lang] = words
    return _NUMWORD_RE[lang], _WORD_VALUES[lang]


_MONTHS_EN = _wordlist(
    "january february march april may june july august september october november december"
)
_MONTHS_TR = _wordlist("ocak subat mart nisan mayis haziran temmuz agustos eylul ekim kasim aralik")
_MONTH_ALTS: list[tuple[str, int]] = sorted(
    [(m, i + 1) for i, m in enumerate(_MONTHS_EN)]
    + [(m[:3], i + 1) for i, m in enumerate(_MONTHS_EN) if m != "may"]
    + [("sept", 9)]
    + [(m, i + 1) for i, m in enumerate(_MONTHS_TR)],
    key=lambda x: -len(x[0]),
)
_MONTH_OF = dict(_MONTH_ALTS)
# a month name may carry a case ending after an apostrophe ('Ekim'de', 'Mayıs'tan')
_MON = r"(?P<mon>" + "|".join(m for m, _ in _MONTH_ALTS) + r")\.?(?:'[a-z]{1,4})?"
_ORD = r"(?:st|nd|rd|th|\.)?"
_DATE_PATTERNS = (
    # 29 Ekim 2026, 16-19 Mayıs 2027, 24 to 26 March, 1st of May
    re.compile(
        r"(?<![\d.])(?P<d1>\d{1,2})" + _ORD + r"(?:\s*(?:-|to|ile|ila|ve|and)\s*(?P<d2>\d{1,2})"
        + _ORD + r")?\s+(?:of\s+)?" + _MON + r"(?![a-z])(?:,?\s+(?P<y>\d{4}))?"
    ),
    # October 29, 2026; March 24-26
    re.compile(
        r"(?<![a-z])" + _MON + r"\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?(?:\s*(?:-|to)\s*"
        r"(?P<d2>\d{1,2})(?:st|nd|rd|th)?)?(?!\d)(?:,?\s+(?P<y>\d{4}))?"
    ),
)  # fmt: skip
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_NUM_DATE = re.compile(r"(?<![\d.])(\d{1,2})[./](\d{1,2})[./](\d{2,4})(?![\d])")


def date_mentions(text_f: str) -> list[tuple[datetime.date | None, int, int, int, int]]:
    """Dates said in folded text: [(date or None when no year, day, month, start, end)], a
    range ('16-19 Mayıs') giving every day in it. Numeric dates are read day first and, when
    that cannot be a date, month first."""
    out: list[tuple[datetime.date | None, int, int, int, int]] = []

    def add(y: int | None, m: int, d: int, a: int, b: int) -> None:
        with contextlib.suppress(ValueError):  # not a date (31 June)
            out.append(((datetime.date(y, m, d) if y else None), d, m, a, b))

    for rx in _DATE_PATTERNS:
        for m in rx.finditer(text_f):
            mon = _MONTH_OF.get(m.group("mon"))
            if not mon:
                continue
            d1 = int(m.group("d1"))
            d2 = int(m.group("d2")) if m.group("d2") else d1
            y = int(m.group("y")) if m.group("y") else None
            if not 1 <= d1 <= d2 <= 31 or d2 - d1 > 15:
                d2 = d1
            for d in range(d1, d2 + 1):
                add(y, mon, d, m.start(), m.end())
    for m in _ISO_DATE.finditer(text_f):
        add(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.start(), m.end())
    for m in _NUM_DATE.finditer(text_f):
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
        add(y, b, a, m.start(), m.end())
        if a <= 12 < b:
            add(y, a, b, m.start(), m.end())
    return out


_TIME_PATTERNS = (
    # 09:00, 9.30, 17h30 (a date such as 14.10.2026 is not a time)
    re.compile(
        r"(?<![\d.:])([01]?\d|2[0-3])[:.h]([0-5]\d)(?![\d]|\.\d)(?:\s*(am|pm|a\.m\.|p\.m\.))?"
    ),
    # 9 am, 7 pm, 9 o'clock
    re.compile(r"(?<![\d.:])(1[0-2]|0?\d)()\s*(am|pm|a\.m\.|p\.m\.|o'clock)"),
    # saat 9, saat 18'e kadar, 10'dan 19'a
    re.compile(r"(?:saat\s+)([01]?\d|2[0-3])()(?![\d.:])"),
    re.compile(r"(?<![\d.:])([01]?\d|2[0-3])()'(?:d[ae]n|t[ae]n|[ae]|y[ae]|d[ae]|t[ae])\b"),
)


def time_mentions(text_f: str) -> list[tuple[int, int, int, int]]:
    """Clock times in folded text: [(hour 0..23, minute, start, end)]; 'pm' and an evening
    word ('akşam', 'öğleden sonra') before a small hour add 12."""
    out = []
    for rx in _TIME_PATTERNS:
        for m in rx.finditer(text_f):
            hh = int(m.group(1))
            mm = int(m.group(2)) if m.group(2) else 0
            mark = m.group(3) if rx.groups >= 3 else None
            before = text_f[max(0, m.start() - 24) : m.start()]
            if hh < 12 and (
                (mark and mark.startswith("p"))
                or re.search(r"(?:aksam|ogleden sonra)\s+(?:saat\s+)?$", before)
            ):
                hh += 12
            out.append((hh, mm, m.start(), m.end()))
    return out


_NUM_TOKEN = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?)"
    r"(?![\d])(?![.,]\d)"
)
# references are not values: 'rule 23', 'madde 4', 'section 2.1', 'Belge 3', 'D-2026/41'
_REF_BEFORE = re.compile(
    r"\b(?:rules?|sections?|clauses?|articles?|items?|steps?|attachments?|documents?|version|"
    r"kural|madde|bolum|belge|duyuru|sayfa|page|no|nr|v)\s*\.?\s*$|[a-z]-$|/$"
)
_LIST_MARK = re.compile(r"(?m)^[ \t>*-]*(?:#+[ \t]*)?\d+(?:\.\d+)*[.)][ \t]")
# a count of digits is not a value either: 'the last four digits', 'son dört hanesi', '4 haneli'
_DIGITS_AFTER = re.compile(r"\s*-?\s*(?:digits?\b|hane|rakam)")


def number_mentions(text_f: str, lang: str) -> list[tuple[set[float], int, int]]:
    """Numbers in folded text, as digits ('7', '1.250,00', '12th', "12'nci") or words ('seven',
    'yedi', 'on iki', 'twelfth'): [(possible values, start, end)]. Dates, clock times, list
    markers and references ('rule 23') are left out. '1.250' may be 1250 or 1.25."""
    masked = list(text_f)
    for _, _, _, a, b in date_mentions(text_f):
        masked[a:b] = " " * (b - a)
    for _, _, a, b in time_mentions(text_f):
        masked[a:b] = " " * (b - a)
    for m in _LIST_MARK.finditer(text_f):
        masked[m.start() : m.end()] = " " * (m.end() - m.start())
    t = "".join(masked)
    out: list[tuple[set[float], int, int]] = []
    for m in _NUM_TOKEN.finditer(t):
        if _REF_BEFORE.search(t[max(0, m.start() - 14) : m.start()]):
            continue
        if _DIGITS_AFTER.match(t, m.end()):
            continue
        tok = m.group(1)
        vals: set[float] = set()
        if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", tok):
            vals.add(float(tok.replace(".", "").replace(",", ".")))
            if tok.count(".") == 1 and "," not in tok:
                vals.add(float(tok))  # 1.250 in an English text
        elif re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", tok):
            vals.add(float(tok.replace(",", "")))
            if tok.count(",") == 1 and "." not in tok:
                vals.add(float(tok.replace(",", ".")))  # 1,250 in a Turkish text
        else:
            vals.add(float(tok.replace(",", ".")))
        out.append((vals, m.start(), m.end()))
    rx, words = _numword_re(lang)
    for m in rx.finditer(t):
        if not _DIGITS_AFTER.match(t, m.end()):
            out.append(({float(words[m.group(1)])}, m.start(), m.end()))
    return out


_PERCENT = re.compile(
    r"%\s*(\d+(?:[.,]\d+)?)|(\d+(?:[.,]\d+)?)\s*(?:%|percent|per cent)|yuzde\s+(\d+(?:[.,]\d+)?)"
)


def percent_mentions(text_f: str, lang: str) -> list[tuple[float, int, int]]:
    """Percentages: '%20', '20%', '20 percent', 'yüzde 20', 'yüzde yirmi', 'twenty percent'."""
    out = []
    for m in _PERCENT.finditer(text_f):
        v = next(g for g in m.groups() if g)
        out.append((float(v.replace(",", ".")), m.start(), m.end()))
    rx, words = _numword_re(lang)
    for m in re.finditer(r"yuzde\s+([a-z ]{2,24})|([a-z -]{3,24}?)\s+(?:percent|per cent)", text_f):
        phrase = (m.group(1) or m.group(2) or "").strip()
        hit = None
        for w in rx.finditer(phrase):
            hit = w  # the longest match nearest the percent word
            if m.group(1):
                break
        if hit:
            out.append((float(words[hit.group(1)]), m.start(), m.end()))
    return out


LINE_KEYS = {
    "opening": "First sentence of the call, exactly what the brief requires (who is calling, "
    "for example that the call is recorded). It must not mention the reason for the call. If "
    "the brief gives the exact words, use them exactly. Otherwise avoid wording a transcript "
    "judge can read as a claim that a promise was recorded (in English say 'we are recording "
    "this call', not 'this call is recorded').",
    "who_question": "The question that checks who is on the line, with call variables written "
    "as {{variable_name}}, for example 'Am I speaking with {{title}} {{last_name}}?'",
    "ai_answer": "What to say when asked whether this is a robot or an AI.",
    "scam_answer": "What to say when asked whether the call is a scam ('' if the brief is silent).",
    "deceased": "What to say when told the customer has died.",
    "identity_intro": "One short sentence before the first security question (no reason for "
    "the call).",
    "identity_why": "The one-time explanation when the customer will not answer a security "
    "question.",
    "identity_failed": "What to say when the identity check failed (for example where to go "
    "instead).",
    "present_intro": "The sentence that says why you are calling, said right after identity is "
    "verified, before the amounts.",
    "today_question": "The first payment question for one product, exactly as the brief words it.",
    "multi_today_question": "The first payment question when there are several products.",
    "confirm_question": "The question after the read-back, exactly as the brief words it.",
    "recorded": "The sentence the brief requires after a promise is recorded ('' if none).",
    "paid": "What to say when the customer says they already paid.",
    "sympathy": "One short sentence of sympathy for hardship.",
    "support_offer": "The offer to connect a hardship customer with the right team, as a "
    "yes/no question.",
    "help_first": "What to say the first time a customer asks for a human, when the brief "
    "says to offer help first.",
    "callback_booked": "Short sentence after a callback is booked.",
    "goodbye": "Thank you and goodbye.",
    "no_info": "What to say to a question the agent cannot answer.",
    "cannot_change": "What to say when the customer wants to change a promise that is already "
    "recorded ('' if the brief is silent).",
    "transfer_human": "Short sentence when handing the call to a person.",
    "rest_overdue": "The note said with any amount below the full past-due amount (for example "
    "'The rest stays overdue.'), '' if the brief has none.",
}

SCHEMA = {
    "bank_name": "string",
    "language": "short code of the call's language, e.g. en or tr: the language of the "
    "documents' customer-facing parts (scripted sentences, example conversations)",
    "currency": {
        "code": "ISO code, e.g. TRY, only if the documents name the currency ('' if not)",
        "symbol": "the symbol or abbreviation the documents write, e.g. TL ('' if none)",
        "word": "the currency's word as spoken ('' if the documents do not say)",
        "minor_word": "the word for the minor unit ('' if the documents do not say)",
    },
    "notes": [
        "every override you resolved (which source won and why) and every ambiguity "
        "with the reading you chose"
    ],
    "call_variables": {
        "customer_id": "name of the call variable holding the customer id",
        "title": "name of the variable for the form of address, or ''",
        "first_name": "...",
        "last_name": "...",
        "full_name": "name of a variable with the whole name, or ''",
        "now": "name of the variable with the current date and time",
    },
    "lines": {k: "string" for k in LINE_KEYS},
    "forbidden_before_verify": ["words never said before identity is verified"],
    "identity": {
        "questions": [
            {
                "field": "value of the verify_identity field enum",
                "ask": "the question to the customer",
                "kind": "date (a full date) | digits (a number said digit by digit, e.g. last "
                "digits of a phone) | year | text (letters and digits as written, e.g. a postcode)",
            }
        ],
        "swap_hint": "the hint value the tool returns for a day/month swap, or ''",
        "swap_instruction": "what to ask the customer when that hint comes",
        "date_order": "DMY or MDY: how to read a date said only as numbers",
        "max_wrong_answers": "int: wrong answers in total that end the identity check; null "
        "when the documents give no limit",
    },
    "outcomes": {
        k: ["close_call outcome", "close_call reason or ''"]
        for k in (
            "unclear",
            "callback",
            "no_callback",
            "wrong_number",
            "deceased",
            "failed",
            "declined",
        )
    },
    "callback": {
        "start": "HH:MM",
        "end": "HH:MM (both included)",
        "include_today": "bool",
        "business_days_ahead": "int (1 = up to the next business day)",
        "business_days_only": "bool: true when the brief limits callbacks to business days, as "
        "'today or the next business day' does (a call made on a weekend then offers the next "
        "business day, not 'today'); false only when the brief explicitly allows callbacks on "
        "weekends or holidays",
        "reasks": "int: times to ask again after a time outside the window before giving up",
    },
    "calendar": {
        "holidays": ["YYYY-MM-DD"],
        "workdays": "[0..6], 0 = Monday",
        "week_start": "int 0..6 (0 = Monday): the first day of the work week when the documents "
        "say it (a Sunday-to-Thursday week starts on 6), else null",
    },
    "bands": [
        {
            "name": "string",
            "min_days_late": "int",
            "max_days_late": "int",
            "deadline_days": "int",
            "deadline_unit": "business (the Nth business day after) | calendar (N days after, "
            "moved back to a business day) | end_of_month (that month's last business day)",
            "deadline_from_field": "optional: the get_account product field holding the date "
            "the deadline counts from (e.g. a statement's due date); '' when it counts from the "
            "day of the call",
            "attempts": "int: persuasion attempts",
        }
    ],
    "attempt1": {
        "rule": "last_business_day_of_week | nth_business_day | last_business_day_of_month",
        "n": "int for nth",
    },
    "attempt_plan": [
        {
            # stress R27/R01 on Freya, 11 Oct: "full amount, except the floor if full was
            # explicitly unaffordable" was read as full, and 900 was offered after "I can't pay
            # the full 900"
            "amount": "current | full | floor. current: the full amount, or the floor when the "
            "customer already said the full amount is too much or unaffordable (use current "
            "whenever the documents make that exception); full: the full amount always",
            "date": "attempt1 | deadline | next_business_day | nth_business_day:<n> (e.g. "
            "nth_business_day:2, the second business day after the call) | "
            "last_business_day_of_week | last_business_day_of_month",
        }
    ],
    "tool_fields": {
        "_": "the request field name of each role, per tool, exactly as in the tool API (a "
        'nested response field as a dotted path: result.verified). Under any tool, "extras": '
        "{field: value}: for a required tool field none of the roles above covers, give the "
        "constant value the brief implies",
        "verify_identity": {
            "customer_id": "...",
            "field": "...",
            "answer": "...",
            "response": {
                "ok": "response field that is true when the call was carried out",
                "verified": "...",
                "all_verified": "...",
                "wrong_answers_left": "...",
                "hint": "...",
            },
        },
        "get_account": {"customer_id": "..."},
        "create_promise": {
            "customer_id": "...",
            "product_id": "...",
            "amount": "...",
            "date": "...",
            "response": {"ok": "..."},
        },
        "add_note": {"customer_id": "...", "product_id": "...", "reason": "..."},
        "schedule_callback": {
            "customer_id": "...",
            "date": "...",
            "time": "...",
            "response": {"ok": "..."},
        },
        "transfer_to_agent": {"customer_id": "...", "queue": "..."},
        "close_call": {"customer_id": "...", "outcome": "...", "reason": "..."},
    },
    "product_fields": {
        "list": "name of the products array in the get_account response",
        "product_id": "...",
        "type": "...",
        "past_due": "...",
        "days_late": "...",
        "due_date": "the product field holding its due date, when the API gives a due date "
        "instead of (or beside) a days-late count; else ''",
    },
    "floor_field": {
        "<product type>": "<response field holding the floor (minimum) amount, '' if the "
        "brief gives none>; one entry per value of the get_account type enum, here and in "
        "product_names"
    },
    "product_names": {
        "<product type>": "spoken name in the call's language, response fields in single "
        "braces, for example 'credit card ending {card_last4}'; when a customer can have two "
        "products of one type, include the field that tells them apart (last digits, number)"
    },
    "floor_rule": {
        "_": "how the floor (the minimum amount that can be promised) is found: "
        '{"field": "<get_account product field holding it>"}, or when the documents define it '
        'as a percentage of an amount, {"percent_of": "<product field of that amount>", '
        '"percent": <number, e.g. 20 for 20%>}; {} when the documents give no floor'
    },
    "unmodelled_rules": [
        "a rule of the documents that no field above can hold (an instalment offer, a "
        "half-day holiday, a date taken from an account field...), quoted exactly; the agent "
        "gets these as instructions. Never a rule a field above already holds"
    ],
    "dated_changes": [
        {
            "effective_from": "YYYY-MM-DD: the date an announcement's change takes effect",
            "path": "the spec value it changes, e.g. callback.end, bands.1.deadline_days, "
            "bands.1.attempts, calendar.holidays, attempt_plan, lines.recorded, floor_rule.percent",
            "value": "the complete new value from that date (a list is given whole)",
            "source": "the announcement's own words for this change, quoted exactly",
        }
    ],
    "neg": {
        "outcomes": {"promise": "...", "paid": "...", "transferred": "...", "no_promise": "..."},
        "reasons": {
            "refused": "...",
            "hardship": "... ('' when the tool API has no value for it; same for the others)",
            "dispute": "...",
            "service_error": "...",
            "paid": "reason used in a note for an already-paid product",
            "transferred": "note reason for a product with no other reason when the call is "
            "transferred, only if add_note has such a value ('' otherwise)",
        },
        "close_reasons": {
            "_": "only when close_call's reason values differ from add_note's for the same "
            "reason: the close_call value per key of reasons (refused, hardship, dispute, "
            "service_error); else leave those keys out",
            "paid": "the close_call reason when the call closes because every product is "
            "already paid, exactly as the brief's outcome table gives it; '' when the table "
            "lists no reason for that case",
        },
        "queues": {"restructuring": "...", "human": "...", "support": "..."},
        "transfer_reasons": {
            k: "close_call reason for this transfer, '' when the outcome table lists none"
            for k in ("restructuring", "human", "support")
        },
        "notes": "bool: products without a promise get add_note",
        "restructure": "transfer (a payment-plan request goes to the restructuring queue) or "
        "no (the documents have no plan transfer: a plan request counts as a refusal)",
        "plans_allowed": "bool: true only when the documents allow the agent to offer "
        "instalments, a payment plan or a discount; else false",
        "restructure_also": [
            "optional: other requests the documents send to the restructuring (plan) queue "
            "besides a new payment plan, each in the documents' own words (e.g. 'cancellation "
            "or renewal requests'); [] when none"
        ],
        **{k: "see the policy list" for k in POLICY_DEFAULTS},
    },
    "answers": [
        {
            "when": "optional: a question a customer may ask that the documents answer (e.g. "
            "'whether a promise renews the policy'); [] when the documents answer none",
            "say": "one short sentence saying exactly what the documents say about it",
            "source": "the documents' own words for it, quoted exactly",
        }
    ],
}

POLICY_HELP = """Policy knobs in "neg" (set every one from the brief; the value in brackets is
only what one example bank used, never copy it without reading the brief):
- human_requests_to_transfer [2]: 1 = transfer on the first request for a human; 2 = first ask
  once whether you can help, transfer when they insist.
- stop_requests_to_close [2]: how many times the customer says they do not want to talk before
  the call is closed.
- promise_attempts [2]: create_promise calls per product before giving up (2 = one retry).
- record_failure ["continue"]: after the last failed create_promise: "continue" (tell the
  customer, keep other promises, note/close as service error) or "transfer".
- record_failure_queue ["support"]: semantic queue (restructuring | human | support) for that.
- hardship ["offer_support"]: "offer_support" (sympathy, no persuasion, offer a transfer;
  yes -> transfer, no -> close as hardship), "transfer" (transfer at once) or "close".
- hardship_queue ["support"]: semantic queue for hardship transfers.
- no_promise_reason ["first_product"]: with no promise recorded, the close reason is the
  reason of the "first_product" (in get_account order) that is not paid, or "priority".
- reason_priority [...]: order of reasons when no_promise_reason is "priority", written as the
  semantic keys refused, hardship, dispute, service_error (not the bank's codes).
- notes_on_transfer [false]: true if notes are still added on a transferred call.
- vague_date_asks [1]: times to ask for the exact day after a vague answer before proposing
  the deadline.
- floor_offers [1]: times the floor may be offered after "too much" / an amount below it.
- restructure ["transfer"]: optional. "no" when the documents have no payment-plan transfer
  (a plan request is then a refusal).
- plans_allowed [false]: optional. true only when the documents let the agent offer
  instalments, a payment plan or a discount.
- product_order ["get_account"]: optional. The order products are stated and handled in:
  "get_account" (the order the tool returns them), "days_late_desc" (most days late first) or
  "past_due_desc" (largest past-due amount first). Set it only when the brief orders them.
Semantic reasons/outcomes map to the exact enum values of the tool API."""

PROMPT = """You turn a bank's brief into a JSON specification for a debt-collection phone agent.
The brief may be a clean specification or raw bank material, possibly several documents (mails,
a policy or analysis, announcements that change the policy, a Q&A, a code list, a service
contract), in any language. Read ALL of it. When two sources disagree, the later or explicitly
overriding one wins; a draft or an unapproved text never wins. Record each such decision in
"notes".

Dates: documents may carry announcements that change a value from an effective date. The date
of a call is only known when the call is made (the variable `now`); it is not today and you
must not guess it. So every field keeps the value valid BEFORE an announcement's effective
date, and "dated_changes" lists each change with its date, the spec path it changes, the
complete new value and the announcement's own words: the workflow applies it to calls made on
or after that date. List a change even when its date may already have passed.

Rules:
- Take every value from the brief or the tool API below. Never invent a rule, a number, a date
  or a name. If the brief is silent, use the most literal reading and say so in "notes".
- Every outcome, reason, queue and field value must be one of the tool API's enum values.
- "language" is the language of the call: the language of the documents' customer-facing parts
  (the sentences the agent must say, the example conversations).
- Sentences in "lines" are spoken to the customer: short, written in the call's language (the
  examples in this prompt are English; write the line in the call's language). Where the brief
  gives exact words, use them exactly: copy them, never translate or reword them.
- "currency" only from the documents; a part they do not say stays ''.
- Test customers (names, birth dates, amounts) and example conversations illustrate the
  documents; they are never a source of rules, numbers, dates or codes.
- The agent states every late product before asking anything, then handles products one by one
  in neg.product_order (customers can have up to three products even if the brief shows one).

{policy_help}

Line meanings:
{line_help}

Tool API (fields, enums, responses):
{tools}

Output ONE JSON object with exactly this shape (values are descriptions of what goes there):
{schema}

BRIEF:
<<<
{brief}
>>>{examples}"""

EXAMPLES_BLOCK = """

EXAMPLE CONVERSATIONS (illustrations only: they show how calls go and the tone of the call's
language; they are never rules, and where they differ from the brief the brief wins; never copy
their names, numbers, dates or amounts into the spec):
<<<
{examples}
>>>"""


def tool_summary(openapi: dict[str, Any], tools: dict[str, ToolDef]) -> str:
    out = []
    for name, t in tools.items():
        # a tool the case reader recognised as a kit role keeps its contract name beside it
        api = str(getattr(t, "api_name", "") or "")
        shown_name = f"{name} (contract: {api})" if api and api != name else name
        out.append(f"{shown_name}  ({t.method} {t.url})  {t.summary}")
        for f in t.fields:
            bits = [f.type]
            if f.required:
                bits.append("required")
            if f.enum:
                bits.append("enum " + json.dumps(f.enum))
            if f.location != "body":
                bits.append(f"in {f.location}")
            out.append(f"    {f.name}: {', '.join(bits)}  {f.description}".rstrip())
        if t.response:
            shown = t.response
            if t.response_props:
                # allOf / $ref (also nested): the model sees the resolved fields
                shown = {"type": "object", "properties": t.response_props}
            # whole (a statement account's response lists many fields; one cut off here was
            # invented by the model, review SX-13); a guard only against a runaway schema
            out.append("    response: " + json.dumps(shown, ensure_ascii=False)[:12000])
    return "\n".join(out)


def _enum(tools: dict[str, ToolDef], tool: str, *fields: str) -> list[str]:
    t = tools.get(tool)
    if not t:
        return []
    for f in t.fields:
        if f.name in fields:
            return list(f.enum)
    for f in t.fields:
        if f.enum and any(x in f.name for x in fields):
            return list(f.enum)
    return []


def warnings(spec: dict[str, Any], brief: str) -> list[str]:
    """Soft findings for the trace; they never stop the build. Every value the builder will
    fill in itself because the spec is silent is one (no silent defaults)."""
    s = _safe(spec)
    out = []
    opening = str((s.get("lines") or {}).get("opening") or "")
    if "recorded" in opening.lower() and opening not in brief:
        out.append("lines.opening says 'recorded' without the brief requiring those words")
    if not str(s.get("language") or "").strip():
        out.append("language is missing: the call is built in English")
    ident = s.get("identity") or {}
    if "max_wrong_answers" not in ident:
        out.append(
            "identity.max_wrong_answers is missing: the workflow locks after 3 wrong answers"
        )
    elif ident.get("max_wrong_answers") is None:
        out.append(
            "identity.max_wrong_answers is null (the documents give no limit): the workflow "
            "locks after 3 wrong answers or when the bank says the check is locked"
        )
    pf = s.get("product_fields") or {}
    if not str(pf.get("type") or "").strip():
        out.append(
            "product_fields.type is empty: floor_field and product_names are keyed by product "
            "type; with no type field every product is read with the one entry given"
        )
    no_days = not str(pf.get("days_late") or "").strip() and not str(pf.get("due_date") or "")
    if no_days and len(s.get("bands") or []) <= 1:
        out.append("product_fields.days_late is empty: with one band every product is placed in it")
    return out + [f"default (the documents are silent): {d}" for d in defaults_used(s)]


def missing_parts(spec: dict[str, Any]) -> list[str]:
    """Parts whose absence would otherwise be filled with one example bank's values or
    dropped without a word (review J / HA-13): the repair round is asked for them."""
    s = _safe(spec)
    bad = []
    cal = s.get("calendar")
    if not isinstance(cal, dict) or not cal:
        bad.append(
            "calendar is missing: give calendar.holidays ([] when the documents list none) and "
            "calendar.workdays (0 = Monday)"
        )
    elif "holidays" not in cal:
        bad.append("calendar.holidays is missing ([] when the documents list none)")
    if not s.get("forbidden_before_verify"):
        bad.append(
            "forbidden_before_verify is empty: list the words the documents forbid before "
            "identity is verified (the debt, the product, the reason for the call), in the "
            "call's language"
        )
    pf = s.get("product_fields") or {}
    no_days = not str(pf.get("days_late") or "").strip() and not str(pf.get("due_date") or "")
    if no_days and len(s.get("bands") or []) > 1:
        bad.append(
            "product_fields.days_late is empty: the band (deadline, attempts) of a product is "
            "chosen by its days late; set it to the get_account product field that holds the "
            "days late, or product_fields.due_date to the field holding the due date"
        )
    return bad


# Lines said before identity is verified: a forbidden word in one of them contradicts it.
PRE_VERIFY_LINES = (
    "opening",
    "who_question",
    "ai_answer",
    "scam_answer",
    "deceased",
    "identity_intro",
    "identity_why",
    "identity_failed",
    "callback_booked",
)


def _pre_verify_texts(spec: dict[str, Any]) -> dict[str, str]:
    s = spec if isinstance(spec, dict) else {}
    lines = s.get("lines") if isinstance(s.get("lines"), dict) else {}
    out = {f"lines.{k}": str(lines.get(k) or "") for k in PRE_VERIFY_LINES if lines.get(k)}
    ident = s.get("identity") if isinstance(s.get("identity"), dict) else {}
    for i, q in enumerate(ident.get("questions") or []):
        if isinstance(q, dict) and q.get("ask"):
            out[f"identity.questions[{i}].ask"] = str(q["ask"])
    if ident.get("swap_instruction"):
        out["identity.swap_instruction"] = str(ident["swap_instruction"])
    return out


def _says_word(word: str, text: str) -> bool:
    """`word` starts a word of `text` (folded): 'kart' is in 'kartınızın', 'loan' in 'loans'."""
    w = fold(word).strip()
    return bool(w) and bool(re.search(r"(?<![a-z0-9])" + re.escape(w), fold(text)))


def forbidden_conflicts(spec: dict[str, Any]) -> list[str]:
    """forbidden_before_verify words the spec's own pre-verification lines say (the opening
    cannot both be said exactly and never contain the word)."""
    s = _safe(spec)
    texts = _pre_verify_texts(s)
    bad = []
    for w in s.get("forbidden_before_verify") or []:
        where = [k for k, t in texts.items() if _says_word(w, t)]
        if where:
            bad.append(
                f"forbidden_before_verify {w!r} is said before identity is verified by "
                f"{', '.join(where)}: drop the word, or reword those lines if the documents allow"
            )
    return bad


def defaults_used(spec: dict[str, Any]) -> list[str]:
    """The values to_generator fills in because the spec does not give them (each one is
    reported: a silent default is one example bank's policy)."""
    s = _safe(spec)
    out = []
    neg = s.get("neg") or {}
    for k, v in POLICY_DEFAULTS.items():
        if neg.get(k) is None:
            out.append(f"neg.{k} = {json.dumps(v)}")
    cb = s.get("callback") or {}
    for k, v in (("start", "10:00"), ("end", "19:00")):
        if not _hhmm(cb.get(k)):
            out.append(f"callback.{k} = {v}")
    if not isinstance(cb.get("include_today"), bool):
        out.append("callback.include_today = true")
    if not _is_int(cb.get("business_days_ahead"), 0):
        out.append("callback.business_days_ahead = 1")
    if not _is_int(cb.get("reasks"), 0):
        out.append("callback.reasks = 1")
    cal = s.get("calendar") or {}
    if not _workdays(cal.get("workdays")):
        out.append("calendar.workdays = Monday to Friday")
    if not isinstance(cal.get("holidays"), list):
        out.append("calendar.holidays = none")
    a1 = s.get("attempt1") or {}
    if a1.get("rule") not in ATTEMPT1_RULES:
        out.append("attempt1 = the last business day of the week")
    if not s.get("attempt_plan"):
        out.append("attempt_plan = the engine's two steps (full on attempt1, floor on deadline)")
    if str((s.get("identity") or {}).get("date_order") or "") not in ("DMY", "MDY"):
        out.append("identity.date_order = DMY")
    return out


def _workdays(wd: Any) -> list[int]:
    if isinstance(wd, list) and wd and all(_is_int(x, 0) and x <= 6 for x in wd):
        return list(wd)
    return []


def check(
    spec: dict[str, Any],
    tools: dict[str, ToolDef],
    brief: str,
    openapi: dict[str, Any] | None = None,
) -> list[str]:
    """Deterministic checks against the tool API. Returns problems as sentences. `openapi`
    (the parsed file) resolves $refs nested in response schemas. Wrongly typed parts are
    problems too (and are read as absent), never an exception."""
    bad: list[str] = shape(spec)
    spec = _safe(spec)
    tf = spec.get("tool_fields") or {}

    def role(tool: str, r: str, *alts: str) -> tuple[str, ...]:
        named = (tf.get(tool) or {}).get(r) if isinstance(tf.get(tool), dict) else None
        return ((named,) if named else ()) + (r, *alts)

    outs = _enum(tools, "close_call", *role("close_call", "outcome", "result", "status"))
    note_reasons = _enum(tools, "add_note", *role("add_note", "reason", "code"))
    queues = _enum(tools, "transfer_to_agent", *role("transfer_to_agent", "queue", "team"))
    fields = _enum(tools, "verify_identity", *role("verify_identity", "field", "question"))
    neg = spec.get("neg") or {}
    for k, v in (spec.get("outcomes") or {}).items():
        if outs and (not isinstance(v, list) or v[0] not in outs):
            bad.append(f"outcomes.{k}: {v!r} is not a close_call outcome {outs}")
    for k, v in (neg.get("outcomes") or {}).items():
        if outs and v not in outs:
            bad.append(f"neg.outcomes.{k}: {v!r} is not a close_call outcome {outs}")
    if neg.get("notes") and note_reasons:
        for k, v in (neg.get("reasons") or {}).items():
            if v == "" and k not in note_reasons:
                continue  # add_note has no value for this reason: none is required
            if v not in note_reasons:
                bad.append(f"neg.reasons.{k}: {v!r} is not an add_note reason {note_reasons}")
    for k, v in (neg.get("queues") or {}).items():
        if queues and v not in queues:
            bad.append(f"neg.queues.{k}: {v!r} is not a transfer queue {queues}")
    qs = (spec.get("identity") or {}).get("questions") or []
    if not qs:
        bad.append("identity.questions is empty")
    for x in qs:
        if fields and x.get("field") not in fields:
            bad.append(f"identity question field {x.get('field')!r} not in {fields}")
    for h in (spec.get("calendar") or {}).get("holidays") or []:
        if not _iso(h):
            bad.append(f"holiday {h!r} is not YYYY-MM-DD")
    bands = spec.get("bands") or []
    if not bands:
        bad.append("bands is empty")
    for b in bands:
        for key in ("min_days_late", "max_days_late", "deadline_days", "attempts"):
            if not isinstance(b.get(key), int):
                bad.append(f"band {b.get('name')!r}: {key} must be an integer")
    for k in POLICY_DEFAULTS:
        if neg.get(k) is None:
            bad.append(f"neg.{k} is missing")
    for tool, roles in (spec.get("tool_fields") or {}).items():
        t = tools.get(tool)
        if not t or not isinstance(roles, dict):
            continue
        names = {f.name for f in t.fields}
        for role, name in roles.items():
            if role == "extras" and isinstance(name, dict):
                for k, v in name.items():
                    f = t.field(str(k))
                    if f is None:
                        bad.append(
                            f"tool_fields.{tool}.extras: {k!r} is not a field of {tool} "
                            f"{sorted(names)}"
                        )
                    elif f.enum and str(v) not in f.enum:
                        bad.append(f"tool_fields.{tool}.extras.{k}: {v!r} is not one of {f.enum}")
                continue
            if role in ("response", "_") or not name or not isinstance(name, str):
                continue
            if name not in names:
                bad.append(
                    f"tool_fields.{tool}.{role}: {name!r} is not a field of {tool} {sorted(names)}"
                )
    lines = spec.get("lines") or {}
    for k in ("opening", "who_question", "today_question", "confirm_question", "goodbye"):
        if not str(lines.get(k) or "").strip():
            bad.append(f"lines.{k} is empty")
    for k, v in lines.items():
        if k != "who_question" and ("{{" in str(v) or "}}" in str(v)):
            bad.append(f"lines.{k} must not contain a placeholder")
    for tpl in (spec.get("product_names") or {}).values():
        if "{{" in str(tpl):
            bad.append("product_names use single braces")
    bad += missing_parts(spec)
    bad += forbidden_conflicts(spec)
    bad += close_reasons(spec, tools)
    bad += [p for p, hard in bindings(spec, tools) if hard]
    bad += responses(spec, tools, openapi)
    return bad + structure(spec)


# ---------------------------------------------------------------- the spec against the tool API
# Request roles fb.generate binds per tool (its bake(...) / role_field(...) calls in build()).
# The first name of each tuple is the role's key in tool_fields; the rest are the fallback
# names generate.find_field tries (exact, then substring). A required field no role binds is
# sent as "{{field}}" (model-filled), which the api_request steps never fill.
TOOL_ROLES: dict[str, tuple[tuple[str, ...], ...]] = {
    "verify_identity": (("customer_id",), ("field", "question"), ("answer", "value")),
    "get_account": (("customer_id",),),
    "create_promise": (
        ("customer_id",),
        ("product_id", "product", "account", "ref"),
        ("amount",),
        ("date", "day"),
    ),
    "add_note": (
        ("customer_id",),
        ("product_id", "product", "account", "ref"),
        ("reason", "code"),
    ),
    "schedule_callback": (("customer_id",), ("date", "day"), ("time", "hour")),
    "transfer_to_agent": (("customer_id",), ("queue", "team", "department", "desk")),
    "close_call": (("customer_id",), ("outcome", "result", "status"), ("reason", "code", "detail")),
}
# Response fields the workflow reads: verify_identity's post hook (resp() roles and data.ok),
# the create_promise and schedule_callback steps (data.ok -> rec_ok / cb_ok).
RESPONSE_ROLES: dict[str, tuple[str, ...]] = {
    "verify_identity": ("ok", "verified", "all_verified", "wrong_answers_left"),
    "create_promise": ("ok",),
    "schedule_callback": ("ok",),
}
# neg.reasons keys that can become a close_call reason (negotiation_lib._finish: a product
# that is not recorded and not paid). "paid" is a note reason; its close reason, when the
# outcome table gives one, is neg.close_reasons.paid (generate.paid_close_reason).
CLOSE_REASON_KEYS = ("refused", "hardship", "dispute", "service_error")


def _tf(spec: dict[str, Any], tool: str) -> dict[str, Any]:
    tf = (spec.get("tool_fields") or {}).get(tool)
    return tf if isinstance(tf, dict) else {}


def _bind(spec: dict[str, Any], t: ToolDef, cands: tuple[str, ...]) -> str:
    """generate.role_field without its module state: the spec's name when it is a real field,
    else the first candidate found in the tool."""
    explicit = _tf(spec, t.name).get(cands[0])
    if explicit in {f.name for f in t.fields}:
        return str(explicit)
    return find_field(t, *cands)


def _call_vars(spec: dict[str, Any]) -> list[str]:
    cv = spec.get("call_variables") or {}
    if not isinstance(cv, dict) or not cv:
        return list(DEFAULT_CALL_VARS)
    return [str(v) for v in cv.values() if v and isinstance(v, str)]


def bindings(spec: dict[str, Any], tools: dict[str, ToolDef]) -> list[tuple[str, bool]]:
    """Required request fields nothing fills (no role, no constant in tool_fields.<tool>.extras,
    no call variable of the same name, no default or single value in the tool API). Hard
    (True) when a role of the same tool found no field (the tool API renamed it: tool_fields
    can fix that), or when a workflow step sends the call (no model fills it): extras can give
    the value. verify_identity's other fields are the model's to fill: only a warning. URL
    parameters (query/path) are a warning too: moving them into the URL is not documented."""
    out = []
    cvars = {norm(v) for v in _call_vars(spec)}
    for tool, roles in TOOL_ROLES.items():
        t = tools.get(tool)
        if not t:
            continue
        url = [f.name for f in t.fields if f.location in ("query", "path")]
        if url:
            out.append(
                (
                    f"{tool}: {url} are URL parameters (query/path); a pre hook moves them from "
                    "the body into the URL, which Freya's api_call docs do not confirm",
                    False,
                )
            )
        bound = {_bind(spec, t, cands) for cands in roles} - {""}
        extras = _tf(spec, tool).get("extras")
        bound |= set(extras) if isinstance(extras, dict) else set()
        loose = [
            f.name
            for f in t.fields
            if f.required
            and f.name not in bound
            and fixed_value(f) is None
            and norm(f.name) not in cvars
        ]
        if not loose:
            continue
        missing = [cands[0] for cands in roles if not _bind(spec, t, cands)]
        if not missing and tool != "verify_identity":
            enums = {n: t.field(n).enum for n in loose if t.field(n).enum}  # type: ignore[union-attr]
            out.append(
                (
                    f"{tool}: required field(s) {loose} play no role the workflow fills and the "
                    "tool API gives no default or single value for them; a workflow step sends "
                    f"them empty and the bank rejects the call: set tool_fields.{tool}.extras "
                    "to {field: the constant value the brief implies}"
                    + (f" (allowed values: {enums})" if enums else ""),
                    True,
                )
            )
            continue
        if missing:
            out.append(
                (
                    f"{tool}: required field(s) {loose} are bound to no role and role(s) "
                    f"{missing} found no field: set "
                    + ", ".join(f"tool_fields.{tool}.{r}" for r in missing)
                    + f" to the real field name (fields of {tool}: "
                    f"{sorted(f.name for f in t.fields)})",
                    True,
                )
            )
        else:
            out.append(
                (
                    f"{tool}: required field(s) {loose} play no role the workflow fills and "
                    "the tool API gives no default or single value for them; the model is "
                    "left to fill them",
                    False,
                )
            )
    return out


def close_reasons(spec: dict[str, Any], tools: dict[str, ToolDef]) -> list[str]:
    """Every value the workflow can send as close_call's reason, when that field has an enum."""
    t = tools.get("close_call")
    if not t:
        return []
    f = t.field(_bind(spec, t, ("reason", "code", "detail")))
    allowed = list(f.enum) if f else []
    if not allowed:
        return []
    bad = []
    for k, v in (spec.get("outcomes") or {}).items():
        if isinstance(v, list) and len(v) > 1 and v[1] and v[1] not in allowed:
            bad.append(f"outcomes.{k}: reason {v[1]!r} is not a close_call reason {allowed}")
    neg = spec.get("neg") or {}
    for k, v in (neg.get("transfer_reasons") or {}).items():
        if v and v not in allowed:
            bad.append(f"neg.transfer_reasons.{k}: {v!r} is not a close_call reason {allowed}")
    rs = neg.get("reasons") or {}
    cr = neg.get("close_reasons") or {}
    paid = paid_close_reason(neg, spec.get("outcomes") or {})
    if paid and paid not in allowed:
        bad.append(
            f"neg.close_reasons.paid: {paid!r} is not a close_call reason {allowed}; it is the "
            "close reason when every product is already paid ('' when the table lists none)"
        )
    for k in CLOSE_REASON_KEYS:
        # what negotiation_lib.close_reason sends: neg.close_reasons first, then neg.reasons
        v = cr.get(k) or rs.get(k)
        if not v or v in allowed:
            continue
        bad.append(
            f"neg.reasons.{k}: {v!r} is not a close_call reason {allowed}; it is the close "
            "reason when no promise is recorded. If add_note and close_call use different "
            f"values for it, keep the add_note value in neg.reasons.{k} and set "
            f"neg.close_reasons.{k} to the close_call value"
        )
    return bad


def _deref(node: Any, doc: dict[str, Any] | None) -> Any:
    for _ in range(20):
        if not (isinstance(node, dict) and "$ref" in node):
            return node
        if doc is None:
            return None
        cur: Any = doc
        try:
            for part in str(node["$ref"]).lstrip("#/").split("/"):
                cur = cur[part]
        except (KeyError, TypeError):
            return None
        node = cur
    return None


def _props(node: Any, doc: dict[str, Any] | None) -> dict[str, Any] | None:
    """The properties a response schema lists, or None when it does not say (no properties,
    additional properties allowed, an unresolved $ref, oneOf/anyOf): then nothing is flagged."""
    node = _deref(node, doc)
    if not isinstance(node, dict) or node.get("additionalProperties") not in (None, False):
        return None
    props: dict[str, Any] = {}
    for part in node.get("allOf") or []:
        sub = _props(part, doc)
        if sub is None:
            return None
        props.update(sub)
    if node.get("oneOf") or node.get("anyOf"):
        return None
    if isinstance(node.get("properties"), dict):
        props.update(node["properties"])
    return props or None


def _at(top: dict[str, Any] | None, name: str, doc: dict[str, Any] | None) -> Any:
    """The schema of response field `name` (a dotted path for a nested one), None if absent."""
    cur = top
    *path, last = str(name).split(".")
    for p in path:
        cur = _props(cur.get(p), doc) if cur else None
    if not cur or last not in cur:
        return None
    return _deref(cur[last], doc) or {}


def _placeholders(tpl: str) -> list[str]:
    try:
        parsed = list(string.Formatter().parse(tpl))
    except ValueError:
        return []
    return [m.group(0) for _, name, _, _ in parsed if name and (m := re.match(r"\w+", name))]


def responses(
    spec: dict[str, Any], tools: dict[str, ToolDef], openapi: dict[str, Any] | None = None
) -> list[str]:
    """Response fields the workflow reads must be in the tool's 200 response, when that response
    lists its properties. `openapi` resolves nested $refs (without it they count as unknown)."""
    bad: list[str] = []
    for tool, roles in RESPONSE_ROLES.items():
        t = tools.get(tool)
        top = _props(t.response, openapi) if t else None
        if not top:
            continue
        rmap = _tf(spec, tool).get("response")
        rmap = rmap if isinstance(rmap, dict) else {}
        want = list(roles)
        swap = str((spec.get("identity") or {}).get("swap_hint") or "")
        if tool == "verify_identity" and swap:
            want.append("hint")
        others = {str(rmap.get(r) or r) for r in want if r != "ok"}
        for role in want:
            name = str(rmap.get(role) or role)
            if _at(top, name, openapi) is not None:
                continue
            if role == "ok" and not any(
                isinstance(x, dict) and x.get("type") == "boolean" and k not in others
                for k, x in top.items()
            ):
                continue  # no flag the spec could name: not a spec problem, the build goes on
            if (
                tool == "verify_identity"
                and role == "all_verified"
                and _at(top, role, openapi) is None
                and _at(top, str(rmap.get("verified") or "verified"), openapi) is not None
            ):
                continue  # no such flag: the identity hook counts the verified answers itself
            what = {
                "ok": "says the call was carried out (true/false)",
                "hint": "carries the hint, or set identity.swap_hint to '' if the tool has none",
            }.get(role, f"holds {role}")
            bad.append(
                f"tool_fields.{tool}.response.{role}: {name!r} is not a field of the {tool} "
                f"response {sorted(top)}; set it to the response field that {what}"
            )
        if tool == "verify_identity" and swap:
            hint = _at(top, str(rmap.get("hint") or "hint"), openapi)
            enum = (
                [str(e) for e in (hint or {}).get("enum") or []] if isinstance(hint, dict) else []
            )
            if enum and swap not in enum:
                bad.append(f"identity.swap_hint {swap!r} is not a value of the hint field {enum}")
    t = tools.get("get_account")
    top = _props(t.response, openapi) if t else None
    if not top:
        return bad
    pf = spec.get("product_fields") or {}
    lst = str(pf.get("list") or "products")
    props: dict[str, Any] | None = top
    *path, last = lst.split(".")
    for part in path:
        props = _props((props or {}).get(part), openapi) if props else None
    if not props:
        return bad
    if last not in props:
        bad.append(
            f"product_fields.list: {lst!r} is not a field of the get_account response "
            f"{sorted(props)}; set it to the array of products"
        )
        return bad
    arr = _deref(props[last], openapi)
    items = _props(arr.get("items"), openapi) if isinstance(arr, dict) else None
    if not items:
        return bad
    where = f"of the get_account {lst} items {sorted(items)}"
    for role in ("product_id", "type", "past_due", "days_late"):
        name = str(pf.get(role) or role)
        if name not in items:
            bad.append(
                f"product_fields.{role}: {name!r} is not a field {where}; set it to the "
                f"product field that holds {role}"
            )
    for ptype, name in (spec.get("floor_field") or {}).items():
        if name and str(name) not in items:
            bad.append(f"floor_field.{ptype}: {name!r} is not a field {where}")
    due = str(pf.get("due_date") or "")
    if due and due not in items:
        bad.append(f"product_fields.due_date: {due!r} is not a field {where}")
    for b in spec.get("bands") or []:
        dff = b.get("deadline_from_field") if isinstance(b, dict) else None
        if isinstance(dff, str) and dff.strip() and dff.strip() not in items:
            bad.append(f"band {b.get('name')!r}: deadline_from_field {dff!r} is not a field {where}")
    fr = spec.get("floor_rule") if isinstance(spec.get("floor_rule"), dict) else {}
    for k in ("field", "percent_of"):
        if fr.get(k) and str(fr[k]) not in items:
            bad.append(f"floor_rule.{k}: {fr[k]!r} is not a field {where}")
    # every product type the tool can return needs a floor field and a spoken name: a type
    # left out never gets a floor offer and is named after its code ('car_loan' -> 'car loan')
    tdef = _deref(items.get(str(pf.get("type") or "type")), openapi)
    types = [str(e) for e in (tdef.get("enum") or [])] if isinstance(tdef, dict) else []
    for key, what in (
        ("floor_field", "the response field holding its floor (minimum) amount, '' if the "
         "brief gives that product no floor"),
        ("product_names", "its spoken name"),
    ):  # fmt: skip
        got = spec.get(key) if isinstance(spec.get(key), dict) else {}
        missing = [x for x in types if x not in got]
        if missing:
            bad.append(
                f"{key} has no entry for product type(s) {missing} of the get_account type "
                f"enum {types}: set {what}"
            )
    for ptype, tpl in (spec.get("product_names") or {}).items():
        for ph in _placeholders(str(tpl)):
            if ph not in items:
                bad.append(f"product_names.{ptype}: {{{ph}}} is not a field {where}")
    return bad


# ---------------------------------------------------------------- deterministic fallback
# Words a renamed product field keeps (amount_overdue for past_due, days_overdue for days_late).
PRODUCT_WORDS: dict[str, tuple[str, ...]] = {
    "product_id": ("id", "ref", "number"),
    "type": ("type", "kind", "category"),
    "days_late": ("day",),
    "past_due": ("due", "overdue", "arrear", "outstanding", "owed", "balance", "late"),
}


def _one(xs: list[str]) -> str:
    xs = list(dict.fromkeys(xs))
    return xs[0] if len(xs) == 1 else ""


def _guess(want: str, pool: list[str], cutoff: float = 0.75) -> str:
    """The one name of `pool` that is `want` written otherwise, or the one close spelling."""
    hit = _one([p for p in pool if norm(p) == norm(want)])
    if hit:
        return hit
    last = {p: p.split(".")[-1] for p in pool}
    hit = _one([p for p in pool if norm(last[p]) == norm(want.split(".")[-1])])
    if hit:
        return hit
    close = difflib.get_close_matches(want.split(".")[-1], list(set(last.values())), 2, cutoff)
    return _one([p for p in pool if len(close) == 1 and last[p] == close[0]])


def autocorrect(
    spec: dict[str, Any], tools: dict[str, ToolDef], openapi: dict[str, Any] | None = None
) -> list[str]:
    """After the repair round: a field name or enum value the tool API does not have (a spec
    older than the tool file) is set, in place, to the tool API's field or value it stands for
    (the same name written otherwise, a unique close name, the only unbound required field).
    Returns one note per change (also added to spec["notes"]); what it cannot place stays and
    check() reports it. Wrongly typed parts are dropped first (in place: check() and the
    trace have reported them)."""
    _fix_shape(spec)
    notes: list[str] = []

    def tool_tf(tool: str) -> dict[str, Any]:
        if not isinstance(spec.get("tool_fields"), dict):
            spec["tool_fields"] = {}
        tf = spec["tool_fields"]
        if not isinstance(tf.get(tool), dict):
            tf[tool] = {}
        return tf[tool]

    # request roles that found no field
    for tool, roles in TOOL_ROLES.items():
        t = tools.get(tool)
        if not t:
            continue
        for _ in range(len(roles)):
            missing = [c for c in roles if not _bind(spec, t, c)]
            if not missing:
                break
            bound = {_bind(spec, t, c) for c in roles} - {""}
            pool = [f for f in t.fields if f.name not in bound and fixed_value(f) is None]
            req = [f for f in pool if f.required]
            done = False
            for cands in missing:
                name = _one([f.name for f in pool if norm(f.name) in {norm(c) for c in cands}])
                name = name or _guess(cands[0], [f.name for f in pool], 0.6)
                if not name and cands[0] == "field":
                    name = _one([f.name for f in req if f.enum])
                if not name and len(missing) == 1 and len(req) == 1:
                    name = req[0].name
                if name:
                    old = tool_tf(tool).get(cands[0])
                    tool_tf(tool)[cands[0]] = name
                    notes.append(f"tool_fields.{tool}.{cands[0]}: {old!r} -> {name!r}")
                    done = True
                    break
            if not done:
                break
    # response fields the workflow reads
    for tool, roles in RESPONSE_ROLES.items():
        t = tools.get(tool)
        top = t.response_props if t else {}
        if not top:
            continue
        leaves = leaf_paths(top)
        want = list(roles)
        if tool == "verify_identity" and (spec.get("identity") or {}).get("swap_hint"):
            want.append("hint")
        got = _tf(spec, tool).get("response")
        rmap = dict(got) if isinstance(got, dict) else {}
        for role in [r for r in want if r != "ok"] + (["ok"] if "ok" in want else []):
            name = str(rmap.get(role) or role)
            if path_schema(top, name) is not None:
                continue
            # another role's field is never this one's (all_verified is not verified)
            others = {str(rmap.get(r) or r) for r in want if r != role}
            if role == "ok":
                new = _one(
                    [
                        k
                        for k, x in top.items()
                        if isinstance(x, dict) and x.get("type") == "boolean" and k not in others
                    ]
                )
            else:
                new = _guess(role, [p for p in leaves if p not in others])
            if new:
                rmap[role] = new
                tool_tf(tool)["response"] = rmap
                notes.append(f"tool_fields.{tool}.response.{role}: {name!r} -> {new!r}")
    # the products list and its fields
    t = tools.get("get_account")
    top = t.response_props if t else {}
    pf = dict(spec["product_fields"]) if isinstance(spec.get("product_fields"), dict) else {}
    n_before = len(notes)
    if top:
        lst = str(pf.get("list") or "products")
        arr = path_schema(top, lst)
        if not (isinstance(arr, dict) and isinstance(arr.get("items"), dict)):
            lists = [
                p
                for p, x in leaf_paths(top).items()
                if isinstance(x, dict)
                and isinstance((x.get("items") or {}).get("properties"), dict)
            ]
            new = _one(lists)
            if new:
                pf["list"] = new
                notes.append(f"product_fields.list: {lst!r} -> {new!r}")
                arr = path_schema(top, new)
        items = (arr.get("items") or {}).get("properties") if isinstance(arr, dict) else None
        if isinstance(items, dict) and items:
            used = {str(pf.get(r) or r) for r in PRODUCT_WORDS} & set(items)
            for role in ("days_late", "product_id", "type", "past_due"):
                name = str(pf.get(role) or role)
                if name in items:
                    continue
                pool = [k for k in items if k not in used]
                new = _guess(role, pool)
                if not new:
                    words = PRODUCT_WORDS[role]
                    hits = [k for k in pool if any(w in k.lower() for w in words)]
                    if role == "product_id":
                        hits = [k for k in pool if norm(k).endswith("id") or "ref" in k.lower()]
                    if role == "past_due":
                        hits = [
                            k
                            for k in hits
                            if "day" not in k.lower()
                            and (items[k] or {}).get("type") in ("number", "integer", None)
                        ]
                    new = _one(hits)
                if new:
                    pf[role] = new
                    used.add(new)
                    notes.append(f"product_fields.{role}: {name!r} -> {new!r}")
    if len(notes) > n_before:
        spec["product_fields"] = pf
    # outcome, reason, queue and question values
    neg = spec.get("neg") if isinstance(spec.get("neg"), dict) else {}
    ident = spec.get("identity") if isinstance(spec.get("identity"), dict) else {}
    oc = spec.get("outcomes") if isinstance(spec.get("outcomes"), dict) else {}
    notes += conform(oc, neg, ident, tools)
    swap = str(ident.get("swap_hint") or "")
    tv = tools.get("verify_identity")
    if swap and tv:
        rmap = _tf(spec, "verify_identity").get("response") or {}
        hint = path_schema(tv.response_props, str(rmap.get("hint") or "hint"))
        enum = [str(e) for e in (hint or {}).get("enum") or []] if isinstance(hint, dict) else []
        new = match_enum(swap, enum) if enum and swap not in enum else ""
        if new:
            ident["swap_hint"] = new
            notes.append(f"identity.swap_hint: {swap!r} -> {new!r}")
    if notes:
        old = spec.get("notes") if isinstance(spec.get("notes"), list) else []
        spec["notes"] = old + ["auto-correct (tool API): " + n for n in notes]
    return notes


def _is_int(x: Any, lo: int | None = None) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and (lo is None or x >= lo)


def semantic_priority(neg: dict[str, Any]) -> tuple[list[str], list[str]]:
    """neg.reason_priority as the semantic keys negotiation_lib compares (refused, hardship,
    dispute, service_error): a bank code (a value of neg.close_reasons or neg.reasons) is
    mapped back to its key. Returns (keys, entries that are neither)."""
    back: dict[str, str] = {}
    for src in ("reasons", "close_reasons"):
        for k, v in (neg.get(src) or {}).items():
            if k in CLOSE_REASON_KEYS and isinstance(v, str) and v:
                back.setdefault(v, k)
    keys: list[str] = []
    unknown: list[str] = []
    pr = neg.get("reason_priority")
    for x in pr if isinstance(pr, list) else []:
        k = str(x) if x in CLOSE_REASON_KEYS else back.get(str(x), "")
        if not k:
            unknown.append(str(x))
        elif k not in keys:
            keys.append(k)
    return keys, unknown


def blocking(spec: dict[str, Any]) -> list[str]:
    """The problems no workflow can be built around: build.py publishes despite every other
    problem check() reports (an imperfect agent scores more than none), but not without a
    security question to ask or a band to place a late product in."""
    spec = _safe(spec)
    out = []
    qs = (spec.get("identity") or {}).get("questions") or []
    if not [x for x in qs if isinstance(x, dict) and x.get("field")]:
        out.append("identity.questions is empty")
    bands = [
        b
        for b in spec.get("bands") or []
        if isinstance(b, dict)
        and _is_int(b.get("min_days_late"), 0)
        and _is_int(b.get("max_days_late"), 0)
    ]
    if not bands:
        out.append("no band with whole-number min_days_late and max_days_late")
    return out


def _hhmm(x: Any) -> bool:
    m = re.fullmatch(r"(\d{2}):(\d{2})", str(x or ""))
    return bool(m) and int(m.group(1)) < 24 and int(m.group(2)) < 60


ENUMS = {
    "hardship": ("offer_support", "transfer", "close"),
    "record_failure": ("continue", "transfer"),
    "no_promise_reason": ("first_product", "priority"),
    "record_failure_queue": ("restructuring", "human", "support"),
    "hardship_queue": ("restructuring", "human", "support"),
    "product_order": ("get_account", "days_late_desc", "past_due_desc"),
}


# ---------------------------------------------------------------- the spec's shape
# What every container of a spec must be for check(), grounding(), autocorrect() and
# to_generator() to read it. A model that squeezes a rule into the wrong type ('callback':
# '09:00-18:00', 'bands': ['early'], identity questions as plain names) gets the part dropped
# (or, for questions, turned into {"field": name}) and a problem for the repair round, never a
# crash with no agent (review SX-5: 67 such shapes raised).
_TOP_TYPES: dict[str, type] = {
    "bank_name": str,
    "language": str,
    "notes": list,
    "call_variables": dict,
    "lines": dict,
    "forbidden_before_verify": list,
    "identity": dict,
    "outcomes": dict,
    "callback": dict,
    "calendar": dict,
    "bands": list,
    "attempt1": dict,
    "attempt_plan": list,
    "tool_fields": dict,
    "product_fields": dict,
    "floor_field": dict,
    "product_names": dict,
    "neg": dict,
    "currency": dict,
    "floor_rule": dict,
    "dated_changes": list,
    "unmodelled_rules": list,
    "answers": list,
}
_INNER_TYPES: dict[str, dict[str, type]] = {
    "identity": {"questions": list},
    "calendar": {"holidays": list, "workdays": list},
    "neg": {
        "outcomes": dict,
        "reasons": dict,
        "close_reasons": dict,
        "queues": dict,
        "transfer_reasons": dict,
        "reason_priority": list,
        "attempt_plan": list,
        "restructure_also": list,
    },
}
# containers whose values are text (a number is written as text; a list or object is dropped)
_TEXT_VALUES = (
    "lines",
    "call_variables",
    "product_fields",
    "floor_field",
    "product_names",
    "currency",
    "neg.outcomes",
    "neg.reasons",
    "neg.close_reasons",
    "neg.queues",
    "neg.transfer_reasons",
)
_WHAT = {dict: "an object", list: "a list", str: "text"}
SPEC_KEYS = ("identity", "outcomes", "neg", "bands", "lines", "tool_fields", "callback")


def _fix_shape(s: Any) -> tuple[dict[str, Any], list[str]]:
    """(the spec with every wrongly typed container dropped or mended, in place when `s` is a
    dict; one problem per change)."""
    bad: list[str] = []
    if not isinstance(s, dict):
        return {}, [f"the spec must be a JSON object, not {type(s).__name__}"]

    def kind(x: Any) -> str:
        return "null" if x is None else type(x).__name__

    for k, t in _TOP_TYPES.items():
        if k in s and s[k] is not None and not isinstance(s[k], t):
            bad.append(f"{k} must be {_WHAT[t]}, not {kind(s[k])}")
            del s[k]
    for top, inner in _INNER_TYPES.items():
        d = s.get(top)
        if not isinstance(d, dict):
            continue
        for k, t in inner.items():
            if k in d and d[k] is not None and not isinstance(d[k], t):
                bad.append(f"{top}.{k} must be {_WHAT[t]}, not {kind(d[k])}")
                del d[k]
    for path in _TEXT_VALUES:
        cur: Any = s
        for part in path.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if not isinstance(cur, dict):
            continue
        for k in list(cur):
            v = cur[k]
            if isinstance(v, int | float) and not isinstance(v, bool):
                cur[k] = str(v)
            elif v is not None and not isinstance(v, str):
                bad.append(f"{path}.{k} must be text, not {kind(v)}")
                del cur[k]
    for k in ("bands", "attempt_plan", "dated_changes"):
        if isinstance(s.get(k), list):
            keep = [x for x in s[k] if isinstance(x, dict)]
            if len(keep) != len(s[k]):
                bad.append(f"every entry of {k} must be an object; {len(s[k]) - len(keep)} dropped")
                s[k] = keep
    neg = s.get("neg")
    if isinstance(neg, dict) and isinstance(neg.get("attempt_plan"), list):
        neg["attempt_plan"] = [x for x in neg["attempt_plan"] if isinstance(x, dict)]
    ident = s.get("identity")
    if isinstance(ident, dict) and isinstance(ident.get("questions"), list):
        qs = []
        for q in ident["questions"]:
            if isinstance(q, dict):
                qs.append(q)
            elif isinstance(q, str) and q.strip():
                bad.append(
                    f"identity.questions: {q!r} must be an object "
                    '{"field": ..., "ask": ..., "kind": ...}'
                )
                qs.append({"field": q.strip()})
            else:
                bad.append(f"identity.questions: {q!r} is not a question; dropped")
        ident["questions"] = qs
    oc = s.get("outcomes")
    if isinstance(oc, dict):
        for k in list(oc):
            v = oc[k]
            if not (isinstance(v, list) and v and all(isinstance(x, str) for x in v)):
                if k != "_":
                    bad.append(f"outcomes.{k} must be [outcome, reason], not {v!r}"[:200])
                del oc[k]
    for k in ("forbidden_before_verify", "notes", "unmodelled_rules"):
        if isinstance(s.get(k), list):
            s[k] = [str(x) for x in s[k] if isinstance(x, str | int | float) and str(x).strip()]
    if isinstance(s.get("answers"), list):
        s["answers"] = [
            {k: str(a.get(k) or "").strip() for k in ("when", "say", "source")}
            for a in s["answers"]
            if isinstance(a, dict)
            and str(a.get("when") or "").strip()
            and str(a.get("say") or "").strip()
        ]
    if isinstance(s.get("neg"), dict) and isinstance(s["neg"].get("restructure_also"), list):
        s["neg"]["restructure_also"] = [
            str(x).strip() for x in s["neg"]["restructure_also"] if isinstance(x, str) and x.strip()
        ]
    cal = s.get("calendar")
    if isinstance(cal, dict) and isinstance(cal.get("holidays"), list):
        cal["holidays"] = [str(h) for h in cal["holidays"] if isinstance(h, str | int)]
    tf = s.get("tool_fields")
    if isinstance(tf, dict):
        for k in list(tf):
            if k != "_" and not isinstance(tf[k], dict):
                bad.append(f"tool_fields.{k} must be an object, not {kind(tf[k])}")
                del tf[k]
    return s, bad


def shape(spec: Any) -> list[str]:
    """The containers of `spec` that have the wrong type (each one a sentence)."""
    return _fix_shape(copy.deepcopy(spec))[1]


def _safe(spec: Any) -> dict[str, Any]:
    """A copy of `spec` every reader can walk: wrongly typed containers dropped or mended."""
    return _fix_shape(copy.deepcopy(spec))[0]


def structure(spec: dict[str, Any]) -> list[str]:
    """Types, ranges and internal consistency. These say nothing about whether a value is the
    brief's; `grounding` looks at that."""
    bad: list[str] = []
    spec = _safe(spec)
    neg = spec.get("neg") or {}
    bands = spec.get("bands") or []
    spans = []
    for b in bands:
        name = b.get("name")
        lo, hi = b.get("min_days_late"), b.get("max_days_late")
        if not (_is_int(lo, 0) and _is_int(hi, 0) and lo <= hi):
            bad.append(f"band {name!r}: min_days_late/max_days_late must be integers, min <= max")
        else:
            spans.append((lo, hi, name))
        if not _is_int(b.get("deadline_days"), 0):
            bad.append(f"band {name!r}: deadline_days must be a whole number >= 0")
        if not _is_int(b.get("attempts"), 0):
            bad.append(f"band {name!r}: attempts must be a whole number >= 0")
        if b.get("deadline_unit", "business") not in DEADLINE_UNITS:
            bad.append(f"band {name!r}: deadline_unit must be one of {list(DEADLINE_UNITS)}")
        dff = b.get("deadline_from_field")
        if dff is not None and not isinstance(dff, str):
            bad.append(f"band {name!r}: deadline_from_field must be a product field name or ''")
    spans.sort()
    for (_, hi1, n1), (lo2, _, n2) in itertools.pairwise(spans):
        if lo2 <= hi1:
            bad.append(f"bands {n1!r} and {n2!r} overlap")
        elif lo2 > hi1 + 1:
            # band_for finds no band for these days: no deadline, no attempts for the product
            gap = f"{hi1 + 1}" if lo2 == hi1 + 2 else f"{hi1 + 1} to {lo2 - 1}"
            bad.append(
                f"bands {n1!r} and {n2!r} leave days_late {gap} without a band "
                "(the next band's min_days_late must be the previous max_days_late + 1)"
            )
    for k in ("notes", "notes_on_transfer", "plans_allowed"):
        if k in neg and not isinstance(neg.get(k), bool):
            bad.append(f"neg.{k} must be true or false")
    if "restructure" in neg and neg.get("restructure") not in ("transfer", "no"):
        bad.append("neg.restructure must be transfer or no")
    for k, lo in (
        ("human_requests_to_transfer", 1),
        ("stop_requests_to_close", 1),
        ("promise_attempts", 1),
        ("vague_date_asks", 0),
        ("floor_offers", 0),
    ):
        if k in neg and not _is_int(neg.get(k), lo):
            bad.append(f"neg.{k} must be a whole number >= {lo}")
    for k, allowed in ENUMS.items():
        if k in neg and neg.get(k) not in allowed:
            bad.append(f"neg.{k} must be one of {list(allowed)}")
    pr = neg.get("reason_priority")
    if pr is not None and not isinstance(pr, list):
        bad.append("neg.reason_priority must be a list")
    keys, unknown = semantic_priority(neg)
    if unknown:
        bad.append(
            f"neg.reason_priority: {unknown} are not reasons: use the semantic keys "
            f"{list(CLOSE_REASON_KEYS)} (or values of neg.reasons)"
        )
    if neg.get("no_promise_reason") == "priority" and not keys:
        bad.append(
            "neg.no_promise_reason is priority but neg.reason_priority names no reason: list "
            f"{list(CLOSE_REASON_KEYS)} in the brief's order"
        )
    # A reason the tool API has no value for (an add_note enum without hardship) is '': only
    # refused, the reason of every product left without a promise, must be named.
    rs = neg.get("reasons")
    if not isinstance(rs, dict) or any(
        not isinstance(rs.get(x), str) for x in ("hardship", "dispute", "service_error")
    ):
        bad.append(
            "neg.reasons needs a value for each of ['refused', 'hardship', 'dispute', "
            "'service_error'] ('' when the tool API has no value for one)"
        )
    for k, keys in (
        ("outcomes", ("promise", "paid", "transferred", "no_promise")),
        ("reasons", ("refused",)),
        # no plan transfer in the documents: no restructuring queue is needed
        (
            "queues",
            ("human", "support")
            if neg.get("restructure") == "no"
            else ("restructuring", "human", "support"),
        ),
    ):
        got = neg.get(k)
        if not isinstance(got, dict) or any(not str(got.get(x) or "").strip() for x in keys):
            bad.append(f"neg.{k} needs a value for each of {list(keys)}")
    # A table whose `transferred` row lists no reason is valid: '' means none is sent.
    tr_keys = ("restructuring", "human", "support")
    got = neg.get("transfer_reasons")
    if not isinstance(got, dict) or any(not isinstance(got.get(x), str) for x in tr_keys):
        bad.append(
            f"neg.transfer_reasons needs a value for each of {list(tr_keys)} ('' when the "
            "outcome table lists no reason for a transfer)"
        )
    outcomes = spec.get("outcomes") or {}
    for k in (
        "unclear",
        "callback",
        "no_callback",
        "wrong_number",
        "deceased",
        "failed",
        "declined",
    ):
        v = outcomes.get(k)
        if not (
            isinstance(v, list) and len(v) == 2 and all(isinstance(x, str) for x in v) and v[0]
        ):
            bad.append(f"outcomes.{k} must be [outcome, reason]")
    cb = spec.get("callback") or {}
    if not (_hhmm(cb.get("start")) and _hhmm(cb.get("end")) and cb["start"] < cb["end"]):
        bad.append("callback.start/end must be HH:MM with start before end")
    if "business_days_ahead" in cb and not _is_int(cb.get("business_days_ahead"), 0):
        bad.append("callback.business_days_ahead must be a whole number >= 0")
    for k in ("include_today", "business_days_only"):
        if k in cb and not isinstance(cb.get(k), bool):
            bad.append(f"callback.{k} must be true or false")
    if "reasks" in cb and not _is_int(cb.get("reasks"), 0):
        bad.append("callback.reasks must be a whole number >= 0")
    a1 = spec.get("attempt1") or {}
    if a1.get("rule") not in ATTEMPT1_RULES:
        bad.append(f"attempt1.rule must be one of {list(ATTEMPT1_RULES)}")
    elif a1.get("rule") == "nth_business_day" and not _is_int(a1.get("n"), 1):
        bad.append("attempt1.n must be a whole number >= 1")
    plan = spec.get("attempt_plan") or []
    if not plan:
        bad.append("attempt_plan is empty")
    most = max((b.get("attempts") for b in bands if _is_int(b.get("attempts"), 0)), default=0)
    if plan and len(plan) < most:
        bad.append(
            f"attempt_plan has {len(plan)} step(s) but a band has {most} persuasion attempts: "
            "give one step per attempt, in order (an attempt past the plan would repeat an "
            "offer the customer refused, so the builder caps a band's attempts at the plan's "
            "length)"
        )
    for i, s in enumerate(plan):
        if not _plan_step_ok(s):
            bad.append(
                f"attempt_plan[{i}] must use amount current|full|floor, date "
                f"{'|'.join(PLAN_DATES)}|nth_business_day:<n>"
            )
    wd = (spec.get("calendar") or {}).get("workdays")
    if wd is not None and not (
        isinstance(wd, list) and wd and all(_is_int(x, 0) and x <= 6 for x in wd)
    ):
        bad.append("calendar.workdays must be a list of 0..6")
    ws = (spec.get("calendar") or {}).get("week_start")
    if ws is not None and not (_is_int(ws, 0) and ws <= 6):
        bad.append("calendar.week_start must be 0..6 (Monday = 0) or null")
    mwa = (spec.get("identity") or {}).get("max_wrong_answers")
    if mwa is not None and not _is_int(mwa, 1):
        bad.append("identity.max_wrong_answers must be a whole number >= 1 or null")
    bad += _floor_rule_problems(spec.get("floor_rule"))
    for i, ch in enumerate(spec.get("dated_changes") or []):
        bad += [f"dated_changes[{i}]: {p}" for p in _change_problems(ch, spec)]
    return bad


# The words collections_lib.deadline_for / rule_date / attempt1_date read.
DEADLINE_UNITS = ("business", "calendar", "end_of_month")
ATTEMPT1_RULES = ("last_business_day_of_week", "nth_business_day", "last_business_day_of_month")
PLAN_DATES = (
    "attempt1",
    "deadline",
    "next_business_day",
    "last_business_day_of_week",
    "last_business_day_of_month",
)


def _plan_step_ok(step: Any) -> bool:
    if not isinstance(step, dict) or step.get("amount") not in ("current", "full", "floor"):
        return False
    d = step.get("date")
    return d in PLAN_DATES or bool(
        isinstance(d, str) and re.fullmatch(r"nth_business_day:[1-9]\d?", d)
    )


def _floor_rule_problems(fr: Any) -> list[str]:
    if isinstance(fr, dict):
        fr = {k: v for k, v in fr.items() if k != "_"}
    if not fr:
        return []
    if not isinstance(fr, dict):
        return ["floor_rule must be an object"]
    if fr.get("percent_of") or fr.get("percent") is not None:
        p = fr.get("percent")
        if not (str(fr.get("percent_of") or "").strip() and isinstance(p, int | float)):
            return ['floor_rule must be {"percent_of": "<response field>", "percent": <number>}']
        if isinstance(p, bool) or not 0 < p <= 100:
            return ["floor_rule.percent must be a number above 0 and at most 100"]
        return []
    if not str(fr.get("field") or "").strip():
        return [
            'floor_rule must be {"field": "<response field>"} or {"percent_of": "<response '
            'field>", "percent": <number>} ({} when the documents give no floor)'
        ]
    return []


def _iso(x: Any) -> str:
    """'YYYY-MM-DD' for a real date written that way, else '' (Python 3.12 also reads
    '20261029' and '2026-W44-4', which the code nodes, comparing text, never match)."""
    s = str(x or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return ""
    try:
        return datetime.date.fromisoformat(s).isoformat()
    except ValueError:
        return ""


# ---------------------------------------------------------------- grounding in the documents
# A value is grounded when the documents say it where they speak of its field: the number of
# days in a sentence (or table column) about a deadline, a holiday's date beside a holiday
# word. A value said only somewhere else (a rule number, a test customer's birth date, an
# example conversation, a draft that never took effect) does not count. Cue words are folded
# English and Turkish stems; a stem may carry Turkish endings ('gecikme', 'gecikmiş').
_CUES: dict[str, str] = {
    "days_late": r"days?[ _-]?late|\blate\b|overdue|arrears|past[ _-]?due|delinquen|\bdpd\b|"
    r"\bbands?\b|\bstages?\b|bucket|gecik|\bbant|kademe|vadesi gec",
    "deadline": r"deadline|latest|business days?|working days?|calendar days?|days? after|"
    r"within|no later|son tarih|son odeme|en gec|is gun|takvim gun|gun icinde|\bvade|\bsure",
    "attempts": r"attempt|persuasion|\btr(?:y|ies)\b|\boffers?\b|proposal|deneme|ikna|teklif|"
    r"\bhak|\bkez\b|\bdefa\b",
    "callback": r"call[ -]?backs?|call (?:them|the customer|back)|ring back|\bbetween\b|\bfrom\b|"
    r"\buntil\b|\btill\b|\bhours?\b|window|geri ara|tekrar ara|\bsaat|\baras|kadar|"
    r"baslangic|bitis|mesai",
    "holiday": r"holiday|closed|non[- ]?working|day off|business days?|working days?|tatil|"
    r"bayram|arife|\bresmi|is gun|calisma gun",
    # not 'attempt' / 'deneme' alone: those are said of persuasion too ('iki deneme yapılır')
    "wrong_answers": r"wrong|incorrect|fail|mistake|lock|yanlis|hatali|\bhata|kilit|bloke|"
    r"identity|kimlik|verif|dogrula|security|guvenlik",
    "percent": r"%|percent|per cent|yuzde|minimum|floor|asgari|en az|taban",
    "effective": r"effective|\bfrom\b|as of|starting|\bstarts?\b|\bvalid|appl(?:y|ies)|in force|"
    r"yururluk|itibaren|gecerli|baslayarak|tarihinden|\bsonra",
}
_CUE_RE = {k: re.compile(v) for k, v in _CUES.items()}
# 'attempt', 'deneme', 'hak' are said of identity questions too: in a passage about identity
# only a word of persuasion makes a number an attempt count ('iki yanlış cevap toplam hak' is
# no attempt count).
_CUE_AGAINST = {
    "attempts": re.compile(
        r"identity|kimlik|verif|dogrula|security|guvenlik|wrong answer|yanlis (?:cevap|yanit)"
    )
}
_CUE_STRONG = {"attempts": re.compile(r"persuasion|ikna|\boffers?\b|teklif|proposal")}


def _about(cue: str | None, ctx: str) -> bool:
    """The passage `ctx` speaks of the topic `cue` (None: any passage does)."""
    if cue is None:
        return True
    if not _CUE_RE[cue].search(ctx):
        return False
    against = _CUE_AGAINST.get(cue)
    return not (against and against.search(ctx)) or bool(_CUE_STRONG[cue].search(ctx))


_TOPIC = {
    "days_late": "days late",
    "deadline": "a deadline",
    "attempts": "attempts",
    "callback": "callbacks",
    "holiday": "holidays",
    "wrong_answers": "wrong answers",
    "percent": "the floor",
    "effective": "an effective date",
}
_CONTEXT_CHARS = 260

# A document (or section) that is an illustration, not a rule: test customers, examples.
_EXAMPLE_HEAD = re.compile(
    r"\bexamples?\b|\bsamples?\b|conversation|dialog|transcript|\bornek|konusma|diyalog|"
    r"\btest[ _-]*(?:customer|musteri|data|verisi|cases?|vaka|senaryo|kisi)"
)
# the same for a heading inside a text ('## 2. Opening the conversation' is a rule)
_EXAMPLE_SECTION = re.compile(
    r"\bexamples?\b|\bsamples?\b|transcript|\bornek|diyalog|"
    r"\btest[ _-]*(?:customer|musteri|data|verisi|cases?|vaka|senaryo|kisi)"
)
# A text that is not in force: a draft, an unapproved proposal.
_DRAFT_TEXT = re.compile(
    r"\bdrafts?\b|not approved|unapproved|not yet approved|no (?:operational )?authority|"
    r"\btaslak|onaylanmad|onaylanmamis"
)
_DRAFT_HEAD = re.compile(_DRAFT_TEXT.pattern + r"|gecersiz|not in force|yururlukte degil")
_HEADING_LINE = re.compile(r"^(#{1,6})[ \t]+(.*)$")
# a line that starts a rule of its own: '3. ...', '3) ...', '**4.2 ...', '- ...' (not indented)
_ITEM_START = re.compile(r"^(?:\d+(?:\.\d+)*[.)]\s|\*\*\d+(?:\.\d+)*|[-*+]\s)")
_DOC_HEADER = re.compile(r"^#{1,3}[ \t]+(?P<name>.+?)[ \t]+\((?P<role>[^()\n]+)\)[ \t]*$")
_FILE_NAME = re.compile(r"\.[a-z0-9]{1,5}$")


class _Docs:
    """The documents, folded, with what each place is: 'rule' (policy, announcements, Q&A, the
    tool contract), 'example' (example conversations, test customers) or 'draft'. Sections
    come from the case reader's headers ('## <file> (<role>)') and from headings."""

    def __init__(self, brief: str, examples: str = "", lang: str = "en") -> None:
        self.lang = lang
        parts = [fold(brief)]
        if str(examples or "").strip():
            parts.append("## examples.md (example conversations)\n" + fold(examples))
        self.text = "\n\n".join(parts)
        self.lines: list[tuple[int, str]] = []
        pos = 0
        for line in self.text.split("\n"):
            self.lines.append((pos, line))
            pos += len(line) + 1
        self.starts = [a for a, _ in self.lines]
        self.kinds = self._kinds()
        self.heads = self._heads()
        self._numbers: list[tuple[set[float], int, int]] | None = None
        self._dates: list[tuple[datetime.date | None, int, int, int, int]] | None = None
        self._times: list[tuple[int, int, int, int]] | None = None

    # -- what each line is
    def _kinds(self) -> list[str]:
        file_mode = any(
            (m := _DOC_HEADER.match(line)) and _FILE_NAME.search(m.group("name").strip())
            for _, line in self.lines
        )
        open_: list[tuple[int, str]] = []  # (level, kind) of the open sections
        kinds = []
        for _, line in self.lines:
            h = _HEADING_LINE.match(line)
            if h:
                level, title = len(h.group(1)), h.group(2)
                doc = _DOC_HEADER.match(line)
                if file_mode and doc and _FILE_NAME.search(doc.group("name").strip()):
                    level = 0  # a new document closes every section
                    title = doc.group("role")
                open_ = [x for x in open_ if x[0] < level]
                # what a document is comes from its role (the case reader's header) or from a
                # section heading ('# Example conversations (illustrations, not rules)')
                if (_EXAMPLE_HEAD if level == 0 else _EXAMPLE_SECTION).search(title):
                    open_.append((level, "example"))
                elif _DRAFT_HEAD.search(title):
                    open_.append((level, "draft"))
                elif level == 0:
                    open_.append((0, "rule"))
            here = [k for _, k in open_]
            kinds.append("example" if "example" in here else "draft" if "draft" in here else "rule")
        # a paragraph that says it is a draft
        i = 0
        while i < len(self.lines):
            if not self.lines[i][1].strip():
                i += 1
                continue
            j = i
            while j < len(self.lines) and self.lines[j][1].strip():
                j += 1
            block = " ".join(line for _, line in self.lines[i:j])
            if _DRAFT_TEXT.search(block):
                for k in range(i, j):
                    if kinds[k] == "rule":
                        kinds[k] = "draft"
            i = j
        return kinds

    def _heads(self) -> list[str]:
        out, cur = [], ""
        for _, line in self.lines:
            if _HEADING_LINE.match(line):
                cur = line.lstrip("# ")
            out.append(cur)
        return out

    def _line_at(self, pos: int) -> int:
        lo, hi = 0, len(self.starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def kind_at(self, pos: int) -> str:
        return self.kinds[self._line_at(pos)]

    def context(self, pos: int) -> str:
        """The words around `pos` that say what a value there is about: for a table cell its
        column's header and the cell; otherwise the paragraph around it (at most
        _CONTEXT_CHARS each side), the line before a list and the section heading."""
        i = self._line_at(pos)
        start, line = self.lines[i]
        head = self.heads[i]
        if line.lstrip().startswith("|"):
            j = i
            while j > 0 and self.lines[j - 1][1].lstrip().startswith("|"):
                j -= 1
            header = self.lines[j][1]
            col = line[: pos - start].count("|") - 1
            cells = line.strip().strip("|").split("|")
            hcells = header.strip().strip("|").split("|")
            if j == i or re.fullmatch(r"[\s|:-]+", header):
                return line + " " + head
            cell = cells[col] if 0 <= col < len(cells) else line
            hcell = hcells[col] if 0 <= col < len(hcells) else header
            return f"{hcell} {cell} {head}"
        # the block: one numbered rule or top-level bullet with its continuation lines, or one
        # paragraph (consecutive rules are separate blocks: 'three' in the identity rule is
        # not about the attempts of the next one). A heading is a block of its own: it says
        # what its section is about (it is added below as `head`), but the words under it do
        # not make its own values theirs ('# Notice 2026-10-01' over 'effective 15 October').
        def apart(line: str) -> bool:
            return line.lstrip().startswith("|") or bool(_HEADING_LINE.match(line))

        a = i
        while (
            a > 0
            and not apart(self.lines[a][1])
            and not _ITEM_START.match(self.lines[a][1])
            and self.lines[a - 1][1].strip()
            and not apart(self.lines[a - 1][1])
        ):
            a -= 1
        b = i
        while (
            b + 1 < len(self.lines)
            and not apart(self.lines[b][1])
            and self.lines[b + 1][1].strip()
            and not _ITEM_START.match(self.lines[b + 1][1])
            and not apart(self.lines[b + 1][1])
        ):
            b += 1
        lo = max(self.lines[a][0], pos - _CONTEXT_CHARS)
        hi = min(self.lines[b][0] + len(self.lines[b][1]), pos + _CONTEXT_CHARS)
        before = ""
        if re.match(r"\s*[-*+]\s", self.lines[a][1]):
            # a list item: the line that introduces the list ('Holidays:') says what it lists
            k = a - 1
            while k >= 0 and (
                not self.lines[k][1].strip() or re.match(r"\s*[-*+]\s", self.lines[k][1])
            ):
                k -= 1
            before = self.lines[k][1] if k >= 0 else ""
        return f"{before} {self.text[lo:hi]} {head}"

    # -- mentions, read once
    @property
    def numbers(self) -> list[tuple[set[float], int, int]]:
        if self._numbers is None:
            self._numbers = number_mentions(self.text, self.lang)
        return self._numbers

    @property
    def dates(self) -> list[tuple[datetime.date | None, int, int, int, int]]:
        if self._dates is None:
            self._dates = date_mentions(self.text)
        return self._dates

    @property
    def times(self) -> list[tuple[int, int, int, int]]:
        if self._times is None:
            self._times = time_mentions(self.text)
        return self._times

    def _verdict(self, places: list[int], cue: str | None) -> str:
        """'ok' (said in a rule, beside its field), 'elsewhere' (only away from its field),
        'example', 'draft' or 'missing'."""
        kinds = [(p, self.kind_at(p)) for p in places]
        rule = [p for p, k in kinds if k == "rule"]
        if rule and any(_about(cue, self.context(p)) for p in rule):
            return "ok"
        if rule:
            return "elsewhere"
        found = {k for _, k in kinds}
        return "example" if "example" in found else "draft" if "draft" in found else "missing"

    def number(self, n: float, cue: str | None) -> str:
        return self._verdict([a for vals, a, _ in self.numbers if float(n) in vals], cue)

    def date(self, iso: str, cue: str | None) -> str:
        try:
            d = datetime.date.fromisoformat(str(iso))
        except ValueError:
            return "missing"
        places = [
            a
            for full, day, mon, a, _ in self.dates
            if full == d or (full is None and (day, mon) == (d.day, d.month))
        ]
        return self._verdict(places, cue)

    def time(self, hhmm: str, cue: str | None) -> str:
        hh, mm = int(str(hhmm)[:2]), int(str(hhmm)[3:5])
        places = [a for h, m, a, _ in self.times if m == mm and (h == hh or h + 12 == hh)]
        return self._verdict(places, cue)

    def percent(self, p: float, cue: str | None = None) -> str:
        return self._verdict(
            [a for v, a, _ in percent_mentions(self.text, self.lang) if abs(v - float(p)) < 1e-9],
            cue,
        )

    def word(self, w: str, stem: bool = False) -> str:
        """A code or a word as written ('SOZ_YOK', 'TL', '₺'), as a whole word (`stem`: the
        start of a word, for a word that takes endings: 'lira' in 'lirası'); a code whose
        prefix and rest are both written ('prefix `D_`' and 'XFER') counts as written."""
        f = fold(w).strip()
        if not f:
            return "ok"
        places = self._word_places(f, tail=not stem)
        if not places and "_" in f.strip("_"):
            for i, c in enumerate(f):
                if c == "_" and 0 < i < len(f) - 1:
                    pre, rest = (
                        self._word_places(f[: i + 1], tail=False),
                        self._word_places(f[i + 1 :]),
                    )
                    if pre and rest:
                        places = rest
                        break
        return self._verdict(places, None)

    def _word_places(self, f: str, tail: bool = True) -> list[int]:
        lead = r"(?<![a-z0-9_])" if f[0].isalnum() or f[0] == "_" else ""
        end = r"(?![a-z0-9_])" if tail and (f[-1].isalnum() or f[-1] == "_") else ""
        return [m.start() for m in re.finditer(lead + re.escape(f) + end, self.text)]

    def quote(self, q: str) -> str:
        """A quotation, compared without case, accents, punctuation or spacing; a quote cut
        with '...' needs each of its parts."""

        def squash(x: str) -> str:
            return " ".join(re.findall(r"[a-z0-9%₺$€£]+", x))

        parts = [squash(fold(p)) for p in re.split(r"\.\.\.|…", str(q or ""))]
        parts = [p for p in parts if len(p) >= 4]
        if not parts:
            return "missing"
        squashed = self._squashed()
        kinds = []
        for p in parts:
            found = [k for k, text in squashed.items() if p in text]
            if not found:
                return "missing"
            kinds.append(found)
        if all("rule" in k for k in kinds):
            return "ok"
        flat = {x for k in kinds for x in k}
        return "example" if "example" in flat else "draft"

    def _squashed(self) -> dict[str, str]:
        if getattr(self, "_squash", None) is None:
            out: dict[str, list[str]] = {"rule": [], "example": [], "draft": []}
            for (_, line), kind in zip(self.lines, self.kinds, strict=True):
                out[kind].append(line)
            self._squash = {
                k: " ".join(re.findall(r"[a-z0-9%₺$€£]+", "\n".join(v))) for k, v in out.items()
            }
        return self._squash


def _because(verdict: str, cue: str | None) -> str:
    if verdict == "elsewhere":
        return (
            "is in the documents only in an unrelated place (never where they speak of "
            f"{_TOPIC.get(cue or '', 'it')})"
        )
    if verdict == "example":
        return (
            "appears only in the test customers / example conversations, which illustrate "
            "and are never rules"
        )
    if verdict == "draft":
        return "appears only in a draft or unapproved text, which is never in force"
    return "is not found in the documents"


# A sentence about callbacks that names business days and no weekend limits callbacks to
# business days; one that allows any day of the week does not.
_CB_WORDS = re.compile(r"call\s*-?\s*back|callback|geri ara|tekrar ara")
_BUSINESS_DAY = re.compile(r"business day|working day|is gun|calisma gun|mesai gun")
_ANY_DAY = re.compile(
    r"weekend|saturday|sunday|holiday|any day|every day|7 days|seven days|7/24|24/7|"
    r"hafta ?sonu|cumartesi|\bpazar\b|tatil|her gun|haftanin her|7 gun|yedi gun"
)


def _callback_business_days(text: str) -> bool:
    """A sentence of the documents about a callback says 'business day' and nothing about
    weekends or every day: callbacks are limited to business days."""
    for sent in re.split(r"(?<=[.!?])\s+|\n", fold(text)):
        if _CB_WORDS.search(sent) and _BUSINESS_DAY.search(sent) and not _ANY_DAY.search(sent):
            return True
    return False


def _spec_language(spec: dict[str, Any], brief: str) -> str:
    code, sure = detect_language(brief)
    if sure or not spec.get("language"):
        return code
    return langpack.norm_language(spec.get("language"))


def grounding(spec: dict[str, Any], brief: str, examples: str = "") -> list[str]:
    """Values that score must come from the documents, said where the documents speak of
    them: dates, day counts, attempts, times, limits, codes, the floor percentage, currency,
    dated changes and their quotes. A value said only in an unrelated place, in a test
    customer or an example conversation, or in a draft is flagged too. A heuristic: a failure
    asks the model to look again (the repair round); it never stops the build."""
    s = _safe(spec)
    docs = _Docs(brief, examples, _spec_language(s, brief))
    bad: list[str] = []

    def need(verdict: str, what: str, cue: str | None) -> None:
        if verdict != "ok":
            bad.append(f"{what} {_because(verdict, cue)}")

    for h in (s.get("calendar") or {}).get("holidays") or []:
        need(docs.date(str(h), "holiday"), f"holiday {h}", "holiday")
    for b in s.get("bands") or []:
        for k, cue in (
            ("min_days_late", "days_late"),
            ("max_days_late", "days_late"),
            ("deadline_days", "deadline"),
            ("attempts", "attempts"),
        ):
            v = b.get(k)
            if not _is_int(v) or (k == "max_days_late" and v >= 999):
                continue  # 999+: an open-ended band, the builder's way to say 'and more'
            if k == "deadline_days" and b.get("deadline_unit") == "end_of_month":
                continue  # the month's last business day: no count of days is read
            need(docs.number(v, cue), f"band {b.get('name')!r} {k}={v}", cue)
    cb = s.get("callback") or {}
    for k in ("start", "end"):
        v = str(cb.get(k) or "")
        if _hhmm(v):
            need(docs.time(v, "callback"), f"callback {k} {v}", "callback")
    if cb.get("business_days_only") is False and _callback_business_days(brief):
        bad.append(
            "callback.business_days_only is false but the brief's callback rule speaks of "
            "business days ('today or the next business day' limits callbacks to business "
            "days): set it to true unless the brief explicitly allows weekend callbacks"
        )
    mwa = (s.get("identity") or {}).get("max_wrong_answers")
    if _is_int(mwa, 1):
        verdict = docs.number(mwa, "wrong_answers")
        need(verdict, f"identity.max_wrong_answers={mwa}", "wrong_answers")
    fr = s.get("floor_rule") or {}
    if isinstance(fr.get("percent"), int | float) and not isinstance(fr.get("percent"), bool):
        need(docs.percent(fr["percent"]), f"floor_rule.percent={fr['percent']}", "percent")
    cur = s.get("currency") or {}
    parts = {k: str(cur.get(k) or "").strip() for k in ("code", "symbol", "word", "minor_word")}
    seen = {k: docs.word(v, stem=k != "code") == "ok" for k, v in parts.items() if v}
    for k, ok in seen.items():
        if not ok and not (k == "code" and any(seen.get(x) for x in ("symbol", "word"))):
            bad.append(
                f"currency.{k} {parts[k]!r} is not found in the documents (currency comes "
                "only from the documents: leave it '' when they do not say it)"
            )
    for i, ch in enumerate(s.get("dated_changes") or []):
        where = f"dated_changes[{i}]"
        need(docs.date(str(ch.get("effective_from") or ""), "effective"),
             f"{where}.effective_from {ch.get('effective_from')}", "effective")  # fmt: skip
        if docs.quote(str(ch.get("source") or "")) != "ok":
            bad.append(
                f"{where}.source is not a quotation of an announcement in force (quote the "
                "announcement's own words, exactly)"
            )
        bad += [f"{where}: {p}" for p in _ground_change(docs, ch)]
    for i, x in enumerate((s.get("neg") or {}).get("restructure_also") or []):
        if docs.quote(str(x)) != "ok":
            bad.append(
                f"neg.restructure_also[{i}] {str(x)[:60]!r} is not in the documents (write each "
                "request in the documents' own words; [] when they send no other request there)"
            )
    for i, a in enumerate(s.get("answers") or []):
        if isinstance(a, dict) and docs.quote(str(a.get("source") or "")) != "ok":
            bad.append(
                f"answers[{i}].source is not a quotation of the documents (quote their own "
                "words for that answer, exactly; leave the answer out when they give none)"
            )
    for i, rule in enumerate(s.get("unmodelled_rules") or []):
        if docs.quote(str(rule)) != "ok":
            bad.append(
                f"unmodelled_rules[{i}] is not a quotation of a rule in force (quote the "
                "documents' own words, exactly; the agent is told to follow it)"
            )
    codes = set()
    for v in (s.get("outcomes") or {}).values():
        if isinstance(v, list):
            codes.update(x for x in v if isinstance(x, str) and x)
    neg = s.get("neg") or {}
    for k in ("outcomes", "reasons", "transfer_reasons"):
        codes.update(x for x in (neg.get(k) or {}).values() if isinstance(x, str) and x)
    for c in sorted(codes):
        v = docs.word(c)
        if v != "ok":
            bad.append(f"close/note code {c!r} {_because(v, None)}")
    return bad


def _ground_change(docs: _Docs, ch: dict[str, Any]) -> list[str]:
    """The value of one dated change, read like the field it changes."""
    path = _spec_path(str(ch.get("path") or ""))
    v = ch.get("value")
    if not path:
        return []
    last = path[-1]
    out = []
    if path[0] == "calendar" and last == "holidays" and isinstance(v, list):
        for h in v:
            if docs.date(str(h), "holiday") != "ok" and docs.date(str(h), None) != "ok":
                out.append(f"holiday {h} is not found in the documents")
    elif path[0] == "callback" and last in ("start", "end") and _hhmm(v):
        if docs.time(str(v), "callback") != "ok":
            out.append(f"callback {last} {v} is not found in the documents")
    elif path[0] == "bands" and _is_int(v):
        cue = {"deadline_days": "deadline", "attempts": "attempts"}.get(last, "days_late")
        verdict = docs.number(v, cue)
        if verdict != "ok":
            out.append(f"{last}={v} {_because(verdict, cue)}")
    return out


def extract(
    brief: str,
    openapi: dict[str, Any],
    tools: dict[str, ToolDef],
    trace: Trace,
    deadline: float | None = None,
    *,
    examples: str = "",
    fallback: Any = None,
) -> dict[str, Any]:
    """The spec of `brief` (the documents: a brief, or a case folder's policy text).
    `examples`: example conversations, shown to the model as illustrations (never rules) and
    used by grounding to flag a value said only there. `fallback`: the cross-check extraction
    (an ExtractionObserver, or a callable returning a spec): when the main call gives no
    usable spec even when asked again, its spec is built instead of nothing."""
    lang, sure = detect_language(brief + "\n" + str(examples or ""))
    block = EXAMPLES_BLOCK.format(examples=examples) if str(examples or "").strip() else ""
    prompt = PROMPT.format(
        policy_help=POLICY_HELP,
        line_help="\n".join(f"- {k}: {v}" for k, v in LINE_KEYS.items()),
        tools=tool_summary(openapi, tools),
        schema=json.dumps(SCHEMA, indent=1),
        brief=brief,
        examples=block,
    )
    msgs = [{"role": "user", "content": prompt}]
    try:
        spec, reply = _ask_spec(trace, "extract the spec from the brief", msgs, deadline)
    except (SpecError, LLMError) as e:
        spec = _fallback_spec(fallback, trace, deadline, e)
        _set_language(spec, lang, sure, trace)
        _localize(spec, trace, deadline)
        return spec
    _set_language(spec, lang, sure, trace)
    first_check = check(spec, tools, brief, openapi)
    problems = first_check + grounding(spec, brief, examples)
    if problems:
        trace.note(f"spec check: {len(problems)} problems: " + "; ".join(problems)[:800])
        repair = [
            *msgs,
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": "These checks failed:\n- "
                + "\n- ".join(problems)
                + "\nFix them using the brief and the tool API. A value 'not found in the "
                "documents' (or found only in an unrelated place) may still be right, for "
                "example said in words the check does not know: keep it only if the brief "
                "really says it for that field, and add a note quoting where. Output the whole "
                "JSON again.",
            },
        ]
        try:
            fixed, _ = _ask_spec(trace, "repair the spec after failed checks", repair, deadline)
        except (SpecError, LLMError) as e:
            trace.note(f"spec repair failed ({str(e)[:200]}); the first reading is kept")
            fixed = None
        if fixed is not None:
            _set_language(fixed, lang, sure, None)
            after = check(fixed, tools, brief, openapi)
            if len(after) > len(first_check):
                # a repair that breaks more than it fixes is not taken (review SX-4: a noisy
                # grounding round could make the model drop right values)
                trace.note(
                    f"spec repair: kept the first reading (the repair has {len(after)} check "
                    f"problems, the first reading {len(first_check)})"
                )
            else:
                spec = fixed
        for w in grounding(spec, brief, examples):
            trace.note("spec warning (kept, check the brief): " + w)
    cb = spec.get("callback")
    if (
        isinstance(cb, dict)
        and cb.get("business_days_only") is False
        and _callback_business_days(brief)
    ):
        # the brief's callback rule names business days and no weekend: a Saturday callback
        # would break it whatever the model read
        cb["business_days_only"] = True
        trace.note("spec override: callback.business_days_only false -> true")
    # parts of the wrong type are dropped here (each one in the trace), so nothing after this
    # (autocorrect, to_generator, the build's own check) can stumble on them
    for p in shape(spec):
        trace.note("spec shape (dropped): " + p)
    _fix_shape(spec)
    # names and values the repair left unknown to the tool API: the one they stand for
    for n in autocorrect(spec, tools, openapi):
        trace.note("spec auto-correct: " + n)
    for w in warnings(spec, brief) + [p for p, hard in bindings(spec, tools) if not hard]:
        trace.note("spec warning: " + w)
    for n in spec.get("notes") or []:
        trace.note("spec decision: " + str(n)[:300])
    for r in spec.get("unmodelled_rules") or []:
        trace.note("spec UNMODELLED (given to the agent as an instruction): " + str(r)[:300])
    _localize(spec, trace, deadline)
    return spec


RETRY_NOTE = (
    "\n\n(Your previous answer could not be used: {error}. Output ONLY the complete JSON "
    "object, nothing before or after it; keep notes short so that it is not cut off.)"
)


def _ask_spec(
    trace: Trace, purpose: str, msgs: list[dict[str, Any]], deadline: float | None
) -> tuple[dict[str, Any], str]:
    """(spec, reply) from one model call; an unusable reply (no JSON, cut off, not a spec, its
    main parts of the wrong type) is asked once more with the error. Raises SpecError when the
    second reply is unusable too."""
    reply = chat(trace, purpose, msgs, deadline=deadline)
    try:
        return _parse(reply), reply
    except SpecError as e:
        trace.note(
            f"spec: {purpose}: the reply is not a usable spec ({str(e)[:200]}); asking again"
        )
        last = dict(msgs[-1])
        last["content"] = str(last.get("content") or "") + RETRY_NOTE.format(error=str(e)[:300])
        reply = chat(trace, purpose + " (asked again)", [*msgs[:-1], last], deadline=deadline)
        return _parse(reply), reply


def _parse(reply: str) -> dict[str, Any]:
    try:
        out = parse_json(reply)
    except (ValueError, json.JSONDecodeError) as e:
        raise SpecError(f"the model did not return JSON: {e}") from e
    if not isinstance(out, dict):
        raise SpecError("the model did not return a JSON object")
    have = [k for k in SPEC_KEYS if k in out]
    if len(have) < 3:
        # a cut reply can leave only an inner object parseable
        raise SpecError(
            f"the JSON is not a spec (it has {sorted(out)[:8]}; a spec has {SPEC_KEYS})"
        )
    main = [p for p in shape(out) if p.split(" ")[0] in SPEC_KEYS]
    if main:
        raise SpecError("main parts have the wrong type: " + "; ".join(main))
    return out


def _fallback_spec(fallback: Any, trace: Trace, deadline: float | None, err: Exception) -> dict:
    """The cross-check extraction's spec when the main extraction gave none (never no agent
    when a usable spec exists). Re-raises `err` when there is none."""
    import time

    if fallback is None:
        raise err
    spec = None
    try:
        if isinstance(fallback, ExtractionObserver):
            left = (deadline - time.monotonic() - OBSERVER_RESERVE_S) if deadline else 600.0
            state = fallback.wait(max(5.0, min(600.0, left)))
            spec = fallback.spec if state == "done" else None
        elif callable(fallback):
            spec = fallback()
    except Exception as e:  # the fallback never adds its own failure
        trace.note(f"spec: the cross-check extraction failed too: {type(e).__name__}: {e}")
    if not isinstance(spec, dict) or [p for p in shape(spec) if p.split(" ")[0] in SPEC_KEYS]:
        raise SpecError(f"{err}; the cross-check extraction has no usable spec either") from err
    trace.note(
        f"spec: the main extraction failed ({str(err)[:200]}); building the cross-check "
        "extraction's spec instead"
    )
    spec = copy.deepcopy(spec)
    _fix_shape(spec)
    return spec


class SpecError(RuntimeError):
    pass


def _set_language(spec: dict[str, Any], lang: str, sure: bool, trace: Trace | None) -> None:
    """spec["language"]: the detector's when it is sure, else the model's (normalised)."""
    said = spec.get("language")
    model = langpack.norm_language(said) if said else ""
    final = lang if sure or not model else model
    if trace is not None and model and model != final:
        trace.note(f"spec override: language {model!r} -> {final!r} (read from the documents)")
    elif trace is not None and not model:
        trace.note(f"spec: language {final!r} read from the documents (the model gave none)")
    spec["language"] = final


def _localize(spec: dict[str, Any], trace: Any, deadline: float | None) -> None:
    """Neutral lines in the call's language when the language pack lacks some (not for the
    cross-check extraction: only the spec that is built needs them)."""
    if isinstance(trace, _ObserverTrace):
        return
    for w in translate_neutral_lines(spec, trace, deadline):
        trace.note("spec warning: " + w)


# Every key negotiation_lib (`_line(neg, key, default)`) and the code-node bodies read from
# neg.lines, with what is said when the brief gives no words for it. _line falls back to its
# own default for a missing or empty value, and some of those defaults are Brief A's policy
# ("You will receive a text message confirming this promise.", "You can change it in the
# mobile app.", "Payments can take one business day to appear."): a hidden brief that says
# nothing must not get them, so every key is set, to the brief's line or to a neutral sentence
# that promises nothing. The language pack's English lines (fb.langpack.ENGINE_LINES["en"])
# add to and replace these; the call's language pack replaces both.
_LOCAL_ENGINE_LINES: dict[str, str] = {
    "no_account": "I am sorry, I cannot see your account details right now.",
    "present_intro": "I am calling about a late payment.",
    "today_question": "Can you make this payment today?",
    "multi_today_question": "Can you pay all of them today?",
    "confirm_question": "Do you confirm?",
    "anything_else": "Is there anything else I can help you with?",
    "support_reask": "Would you like that?",
    "rest_overdue": "The rest stays overdue.",
    "vague_ask": "On which exact day will you pay?",
    "offer_day_question": "Can you do that, or is another day better for you?",
    "help_first": "Before I transfer you, can I help you with this myself?",
    "support_offer": "Would you like me to connect you with a colleague who can help?",
    "sympathy": "I am sorry to hear that.",
    "paid": "Thank you for letting me know.",
    "dispute_noted": "I have noted that.",
    "which_change": "Which promise would you like to change?",
    "one_by_one": "Let us go through them one at a time.",
    "recorded": "Thank you.",
    "cannot_change": "A recorded promise cannot be changed in this call.",
    "transfer_human": "I am connecting you with a colleague now.",
    "transfer_restructuring": "I am connecting you with a colleague now.",
    "transfer_support": "I am connecting you with a colleague now.",
    "transfer_failed": "I am sorry, I could not connect you.",
    "goodbye": "Thank you for your time. Goodbye.",
}
ENGINE_LINES: dict[str, str] = {
    **_LOCAL_ENGINE_LINES,
    **{k: v for k, v in (langpack.ENGINE_LINES.get("en") or {}).items() if v},
}

_PLACEHOLDER = re.compile(r"\{\{?\s*[\w.]+\s*\}?\}")


def _placeholder_set(text: str) -> list[str]:
    return sorted(re.sub(r"\s", "", m) for m in _PLACEHOLDER.findall(str(text or "")))


def _missing_lines(lang: str) -> dict[str, dict[str, str]]:
    """The English neutral lines the call's language pack does not have, per group."""
    own_engine = langpack.ENGINE_LINES.get(lang) or {}
    own_gen = langpack.GEN_LINES.get(lang) or {}
    en_gen = langpack.gen_lines(langpack.DEFAULT_LANGUAGE)
    return {
        "engine": {k: v for k, v in ENGINE_LINES.items() if not own_engine.get(k)},
        "gen": {k: v for k, v in en_gen.items() if v and not own_gen.get(k)},
    }


TRANSLATE_PROMPT = """Translate these sentences of a bank's phone agent from English into
{language}. They are said to a customer on a debt-collection call where the bank's documents
give no words of their own: keep them short, polite, neutral and spoken; promise nothing that
is not in the English sentence. Keep every placeholder in braces ({{like_this}} or {{{{this}}}})
exactly as it is. Output ONE JSON object with the same groups and keys, values translated:
{lines}"""


def translate_neutral_lines(
    spec: dict[str, Any], trace: Trace, deadline: float | None = None
) -> list[str]:
    """Build-time translation of the neutral lines for a call language without a (complete)
    language pack: one model call; each translation is kept only when it carries exactly the
    English line's placeholders. Writes spec["neutral_lines"] = {"engine": {...}, "gen":
    {...}} (to_generator reads it) and returns warnings; on any failure the lines stay English
    (with a warning), never an exception."""
    lang = langpack.norm_language(spec.get("language"))
    if lang == langpack.DEFAULT_LANGUAGE:
        return []
    todo = _missing_lines(lang)
    if not any(todo.values()):
        return []
    name = langpack.language_name(lang)
    prompt = TRANSLATE_PROMPT.format(
        language=name, lines=json.dumps(todo, ensure_ascii=False, indent=1)
    )
    try:
        reply = chat(
            trace,
            f"translate the neutral lines into {name}",
            [{"role": "user", "content": prompt}],
            deadline=deadline,
            max_tokens=8000,
        )
        got = parse_json(reply)
    except Exception as e:  # LLMError, no JSON: English lines, said in the trace
        return [
            f"neutral lines stay English for {name} ({type(e).__name__}: {str(e)[:160]}): the "
            "language pack has none"
        ]
    out: dict[str, dict[str, str]] = {"engine": {}, "gen": {}}
    kept_english = []
    for group, lines in todo.items():
        tr = got.get(group) if isinstance(got, dict) and isinstance(got.get(group), dict) else {}
        for k, en in lines.items():
            t = tr.get(k)
            if isinstance(t, str) and t.strip() and _placeholder_set(t) == _placeholder_set(en):
                out[group][k] = t.strip()
            else:
                kept_english.append(f"{group}.{k}")
    spec["neutral_lines"] = out
    warns = []
    if kept_english:
        warns.append(
            f"neutral lines kept in English for {name} (no translation, or its placeholders "
            f"differ): {kept_english[:20]}"
        )
    return warns


def _var(spec: dict[str, Any], role: str) -> str:
    return str((spec.get("call_variables") or {}).get(role) or role)


# ---------------------------------------------------------------- dated changes -> cfg
# A dated change names a spec path; the code nodes apply cfg paths (collections_lib.
# prepare_cfg sets cfg["dated"] entries whose date has come). What has no place in cfg (a line
# the agent model says from its prompt, the number of identity questions) cannot change by date.
_CFG_SAME = ("floor_field", "product_names", "floor_rule", "product_fields", "attempt1")
_CALLBACK_KEYS = ("start", "end", "include_today", "business_days_ahead", "business_days_only")
_BAND_KEYS = (
    "min_days_late",
    "max_days_late",
    "deadline_days",
    "deadline_unit",
    "deadline_from_field",
    "attempts",
)


def _spec_path(path: str) -> list[str]:
    """'bands[1].deadline_days' / 'bands.1.deadline_days' -> ['bands', '1', 'deadline_days']."""
    return [p for p in re.split(r"[.\[\]]+", str(path or "").strip()) if p]


def _band_index(spec: dict[str, Any], key: str) -> int | None:
    bands = spec.get("bands") or []
    if re.fullmatch(r"\d+", key):
        i = int(key)
        return i if i < len(bands) else None
    hits = [i for i, b in enumerate(bands) if fold(b.get("name")).strip() == fold(key).strip()]
    return hits[0] if len(hits) == 1 else None


def _plan_steps(spec: dict[str, Any]) -> int:
    steps = [len(spec.get("attempt_plan") or [])]
    for ch in spec.get("dated_changes") or []:
        if _spec_path(str(ch.get("path") or "")) == ["attempt_plan"] and isinstance(
            ch.get("value"), list
        ):
            steps.append(len(ch["value"]))
    return max(steps) or 2


def _cfg_change(ch: dict[str, Any], spec: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """({cfg dotted path: value}, '') for one dated change, or ({}, why it cannot apply)."""
    p = _spec_path(str(ch.get("path") or ""))
    v = ch.get("value")
    if not p:
        return {}, "path is empty"
    head, rest = p[0], p[1:]
    if head == "calendar" and rest == ["holidays"]:
        if not isinstance(v, list) or any(not _iso(h) for h in v):
            return {}, "a calendar.holidays value is the whole list of YYYY-MM-DD dates"
        return {"holidays": [_iso(h) for h in v]}, ""
    if head == "calendar" and rest == ["workdays"]:
        return ({"workdays": v}, "") if _workdays(v) else ({}, "workdays must be a list of 0..6")
    if head == "calendar" and rest == ["week_start"]:
        return ({"week_start": v}, "") if _is_int(v, 0) and v <= 6 else ({}, "week_start is 0..6")
    if head == "callback" and len(rest) == 1 and rest[0] in _CALLBACK_KEYS:
        k = rest[0]
        ok = (
            _hhmm(v)
            if k in ("start", "end")
            else _is_int(v, 0)
            if k == "business_days_ahead"
            else isinstance(v, bool)
        )
        return ({f"callback.{k}": v}, "") if ok else ({}, f"callback.{k} has the wrong type")
    if head == "bands" and len(rest) == 2 and rest[1] in _BAND_KEYS:
        i = _band_index(spec, rest[0])
        if i is None:
            return {}, f"no band {rest[0]!r} (give the band's index or its exact name)"
        k = rest[1]
        if k == "deadline_unit":
            if v not in DEADLINE_UNITS:
                return {}, f"deadline_unit is one of {list(DEADLINE_UNITS)}"
            return {f"bands.{i}.deadline_unit": v}, ""
        if k == "deadline_from_field":
            if not isinstance(v, str):
                return {}, "deadline_from_field is a product field name or ''"
            return {f"bands.{i}.deadline_from_field": v}, ""
        if not _is_int(v, 0):
            return {}, f"bands.{i}.{k} must be a whole number"
        if k == "deadline_days":
            return {f"bands.{i}.deadline_days": v, f"bands.{i}.deadline_business_days": v}, ""
        if k == "attempts":
            return {f"bands.{i}.attempts": min(v, _plan_steps(spec))}, ""
        return {f"bands.{i}.{k}": v}, ""
    if head == "attempt_plan" and not rest:
        plan_ok = isinstance(v, list) and v and all(_plan_step_ok(s) for s in v)
        return ({"neg.attempt_plan": v}, "") if plan_ok else ({}, "attempt_plan has a bad step")
    if head == "lines" and len(rest) == 1:
        if rest[0] not in ENGINE_LINES and rest[0] not in langpack.engine_lines("en"):
            return {}, (
                f"lines.{rest[0]} is said by the agent model from its instructions, not by the "
                "code nodes: a dated change cannot reach it"
            )
        return {f"neg.lines.{rest[0]}": str(v or "")}, ""
    if head == "neg" and rest and (rest[0] in POLICY_DEFAULTS or rest[0] in (
        "outcomes", "reasons", "close_reasons", "queues", "transfer_reasons", "notes",
        "notes_on_transfer", "lines", "attempt_plan",
    )):  # fmt: skip
        return {"neg." + ".".join(rest): v}, ""
    if head in _CFG_SAME:
        return {".".join(p): v}, ""
    if head == "currency":
        return {"fmt.currency" + "".join("." + x for x in rest): v}, ""
    return {}, f"{'.'.join(p)} cannot be changed by date in the workflow"


def _change_problems(ch: Any, spec: dict[str, Any]) -> list[str]:
    if not isinstance(ch, dict):
        return ["must be an object {effective_from, path, value, source}"]
    bad = []
    if not _iso(ch.get("effective_from")):
        bad.append("effective_from must be YYYY-MM-DD")
    if "value" not in ch:
        bad.append("value is missing")
    _, why = _cfg_change(ch, spec)
    if why:
        bad.append(f"path {ch.get('path')!r}: {why}")
    return bad


def dated_cfg(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """cfg["dated"]: [{"from": "YYYY-MM-DD", "set": {cfg path: value}}], one entry per date,
    in date order. A change that cannot apply is left out (structure() reports it)."""
    by_date: dict[str, dict[str, Any]] = {}
    for ch in spec.get("dated_changes") or []:
        if not isinstance(ch, dict) or not _iso(ch.get("effective_from")):
            continue
        sets, why = _cfg_change(ch, spec)
        if sets and not why:
            by_date.setdefault(_iso(ch["effective_from"]), {}).update(sets)
    return [{"from": d, "set": by_date[d]} for d in sorted(by_date)]


def to_generator(spec: dict[str, Any]) -> dict[str, Any]:
    """Spec -> input of fb.generate.build (cfg for the code nodes + conversation parts). Every
    value the spec does not give is filled in here (defaults_used() lists them for the trace)."""
    s = _safe(spec)
    lang = langpack.norm_language(s.get("language"))
    neg = dict(s.get("neg") or {})
    also = neg.pop("restructure_also", None)
    restructure_also = (
        [str(x).strip() for x in also if str(x).strip()] if isinstance(also, list) else []
    )
    for k, v in POLICY_DEFAULTS.items():  # defaults_used() reports each to the trace
        neg.setdefault(k, v)
    lines = dict(s.get("lines") or {})
    # every line the engine reads: the brief's words, else the neutral sentence of the call's
    # language (its pack, else the build-time translation), else English (never the engine's
    # built-in default, which is Brief A's wording for some)
    own = langpack.ENGINE_LINES.get(lang) or {} if lang != langpack.DEFAULT_LANGUAGE else {}
    tx = (
        ((s.get("neutral_lines") or {}).get("engine") or {})
        if isinstance(s.get("neutral_lines"), dict)
        else {}
    )
    english = {**ENGINE_LINES, **langpack.engine_lines(langpack.DEFAULT_LANGUAGE)}
    said = {
        k: str(lines.get(k) or "").strip() or own.get(k) or tx.get(k) or english[k] for k in english
    }
    # a line equal to the engine's own default (its English words, which the English pack
    # holds) is left out: the code nodes say it anyway, and every byte of CFG is code-node
    # budget. The neutral lines always go in (the engine's default there is one bank's policy).
    engine_says = langpack.ENGINE_LINES.get(langpack.DEFAULT_LANGUAGE) or {}
    neg["lines"] = {
        k: v for k, v in said.items() if k in _LOCAL_ENGINE_LINES or v != engine_says.get(k)
    }
    # close_call reasons per semantic key; "paid" only when the outcome table gives one
    # (negotiation_lib._finish must read close_reasons.paid for a call where all is paid)
    cr = {
        k: v
        for k, v in (neg.get("close_reasons") or {}).items()
        if k not in ("_", "paid") and isinstance(v, str) and v.strip()
    }
    paid = paid_close_reason(neg, s.get("outcomes") or {})
    if paid:
        cr["paid"] = paid
    neg["close_reasons"] = cr
    # negotiation_lib compares semantic keys: bank codes in the priority are mapped back (a
    # code list was silently ignored and the first product's reason won)
    if isinstance(neg.get("reason_priority"), list):
        neg["reason_priority"] = semantic_priority(neg)[0]
    # a reason the tool API has no value for is '': left out, the engine uses the semantic key
    neg["reasons"] = {
        k: v for k, v in (neg.get("reasons") or {}).items() if isinstance(v, str) and v.strip()
    }
    neg.setdefault("attempt_plan", s.get("attempt_plan") or [])
    if s.get("attempt_plan"):
        neg["attempt_plan"] = s["attempt_plan"]
    # negotiation_lib takes step min(used, len(plan)): an attempt past the plan repeats the
    # last offer, one the customer already refused (A31). A plan shorter than a band's attempts
    # (structure() reports it to the repair round) caps the attempts instead; extending the
    # plan would need offers the brief does not give. No plan: the engine's two-step default.
    steps = len(neg.get("attempt_plan") or []) or 2
    bands = []
    for b in s.get("bands") or []:
        att = b.get("attempts")
        bands.append(
            {
                "name": b.get("name"),
                "min_days_late": b.get("min_days_late"),
                "max_days_late": b.get("max_days_late"),
                "deadline_business_days": b.get("deadline_days"),
                "deadline_days": b.get("deadline_days"),
                "deadline_unit": b.get("deadline_unit") or "business",
                "attempts": min(att, steps) if _is_int(att, 0) else att,
            }
        )
        dff = b.get("deadline_from_field")
        if isinstance(dff, str) and dff.strip():
            # the deadline counts from this product date (collections_lib.deadline_for)
            bands[-1]["deadline_from_field"] = dff.strip()
    cal = s.get("calendar") or {}
    cb = s.get("callback") or {}
    pf = s.get("product_fields") or {}
    # holidays the code nodes can match: they compare 'YYYY-MM-DD' text (review SX-14)
    holidays = [h for h in (_iso(x) for x in cal.get("holidays") or []) if h]
    cfg = {
        "holidays": list(dict.fromkeys(holidays)),
        "workdays": _workdays(cal.get("workdays")) or [0, 1, 2, 3, 4],
        "bands": bands,
        "attempt1": s.get("attempt1") or {"rule": "last_business_day_of_week"},
        "callback": {
            "start": cb.get("start") if _hhmm(cb.get("start")) else "10:00",
            "end": cb.get("end") if _hhmm(cb.get("end")) else "19:00",
            "include_today": cb.get("include_today", True),
            # 0 is a value (only today), not a missing one
            "business_days_ahead": (
                cb["business_days_ahead"] if _is_int(cb.get("business_days_ahead"), 0) else 1
            ),
            # only an explicit false allows weekend callbacks (a real extraction of Brief A,
            # 'today or the next business day', gave false: a Saturday callback was offered)
            "business_days_only": cb.get("business_days_only") is not False,
        },
        "product_fields": {k: v for k, v in pf.items() if k != "list" and v},
        # find_products' fallback reads the list name from the config (code nodes see CFG only)
        "products_list": pf.get("list") or "products",
        "floor_field": s.get("floor_field") or {},
        "product_names": s.get("product_names") or {},
        "date_order": (s.get("identity") or {}).get("date_order") or "DMY",
        "neg": neg,
        "lang": lang,
        "fmt": _fmt(lang, s.get("currency")),
    }
    ws = cal.get("week_start")
    if _is_int(ws, 0) and ws <= 6:
        cfg["week_start"] = ws
    fr = {k: v for k, v in (s.get("floor_rule") or {}).items() if k != "_"}
    if fr and not _floor_rule_problems(fr):
        cfg["floor_rule"] = fr
    dated = dated_cfg(s)
    if dated:
        cfg["dated"] = dated
    ident = dict(s.get("identity") or {})
    mwa = ident.get("max_wrong_answers")
    ident["max_wrong_answers"] = mwa if _is_int(mwa, 1) else None
    gen_lines = dict(langpack.gen_lines(lang))
    gtx = (
        ((s.get("neutral_lines") or {}).get("gen") or {})
        if isinstance(s.get("neutral_lines"), dict)
        else {}
    )
    own_gen = langpack.GEN_LINES.get(lang) or {} if lang != langpack.DEFAULT_LANGUAGE else {}
    for k, v in gtx.items():
        if isinstance(v, str) and v.strip() and not own_gen.get(k):
            gen_lines[k] = v
    reasks = cb.get("reasks")
    return {
        "cfg": cfg,
        "language": lang,
        "gen_lines": gen_lines,
        "bank_name": s.get("bank_name") or "the bank",
        "lines": lines,
        "forbidden_before_verify": s.get("forbidden_before_verify") or [],
        "identity": ident,
        "outcomes": s.get("outcomes") or {},
        # a wrongly typed value ('once') is reported by structure(); it never stops the build
        "callback_reasks": reasks if _is_int(reasks, 0) else 1,
        "products_list": pf.get("list") or "products",
        "tool_fields": {k: v for k, v in (s.get("tool_fields") or {}).items() if k != "_"},
        "call_variables": s.get("call_variables") or {},
        # rules no spec field holds, quoted from the documents: the agent model's instructions
        "extra_rules": [str(r) for r in s.get("unmodelled_rules") or [] if str(r).strip()],
        # other requests sent to the plan queue, and the documents' answers to questions: the
        # model's words only (generate), never in cfg (the decide node's code stays the same)
        "restructure_also": restructure_also,
        "answers": [
            {"when": a["when"], "say": a["say"]}
            for a in s.get("answers") or []
            if isinstance(a, dict) and a.get("when") and a.get("say")
        ],
    }


def _fmt(lang: str, currency: Any) -> dict[str, Any]:
    """langpack.fmt(lang) with the documents' currency merged in as fmt["currency"]
    ({"code", "symbol", "word", "minor_word"}, only the parts the documents give)."""
    out = copy.deepcopy(langpack.fmt(lang))
    cur = {
        k: str(v).strip()
        for k, v in (currency.items() if isinstance(currency, dict) else [])
        if k in ("code", "symbol", "word", "minor_word") and str(v or "").strip()
    }
    if cur:
        base = out.get("currency") if isinstance(out.get("currency"), dict) else {}
        out["currency"] = {**base, **cur}
    return out


def quotes_found(spec: dict[str, Any], brief: str) -> list[str]:
    """Lines the brief gives in quotes ("...", “...”, «...») that the spec does not use (for
    the trace); compared folded, so a Turkish capital or a curly apostrophe is no miss."""
    quoted = set(re.findall(r'["“«]([^"“”«»\n]{12,160})["”»]', brief))
    lines = spec.get("lines") if isinstance(spec, dict) else None
    used = fold(" ".join(str(v) for v in (lines or {}).values()))
    return [q for q in quoted if fold(q) not in used and not q.startswith("{")]


# ---------------------------------------------------------------- extraction observer
# A second extraction of the same brief, run beside the main one, to show where the model is
# unsure (the two readings differ). Logged only; the one value it changes is
# forbidden_before_verify (the union of both: saying less before the identity check is safe).
OBSERVER_PURPOSE = "extraction cross-check"
OBSERVER_WAIT_MAX_S = 120.0
OBSERVER_RESERVE_S = 300.0


class _ObserverTrace:
    """The trace the observer's extract() writes to: its model calls (and failed calls) go to
    the run's trace with the purpose 'extraction cross-check: ...', so every model call and its
    cost is in the trace; its notes stay here (the main extraction's notes are the run's)."""

    def __init__(self, trace: Trace) -> None:
        import threading

        self.trace = trace
        self.lock = threading.Lock()
        self.notes: list[str] = []

    def model(self, model: str, purpose: str, cost_usd: float = 0.0, tokens: int = 0) -> None:
        with self.lock:
            self.trace.model(model, f"{OBSERVER_PURPOSE}: {purpose}", cost_usd, tokens)

    def model_failed(self, model: str, purpose: str, seconds: float, error: str) -> None:
        with self.lock:
            self.trace.model_failed(model, f"{OBSERVER_PURPOSE}: {purpose}", seconds, error)

    def note(self, msg: str) -> None:
        self.notes.append(str(msg))

    test = freya = note

    def line(self, kind: str, msg: str) -> None:
        self.notes.append(f"{kind} {msg}")


class ExtractionObserver:
    """A second extract() of the same inputs in a daemon thread. Nothing it does can reach the
    build: every exception is kept as text, and `wait` never raises."""

    def __init__(self, run: Any) -> None:
        import threading
        import time

        self.spec: dict[str, Any] | None = None
        self.error = ""
        self.started = time.monotonic()
        self.thread = threading.Thread(
            target=self._run, args=(run,), name="extraction-observer", daemon=True
        )
        self.thread.start()

    def _run(self, run: Any) -> None:
        try:
            out = run()
            if isinstance(out, dict):
                self.spec = out
            else:
                self.error = f"the observer's extraction returned {type(out).__name__}"
        except BaseException as e:
            self.error = f"{type(e).__name__}: {str(e)[:300]}"

    def wait(self, timeout: float) -> str:
        """'done', 'failed' or 'running' (not finished within `timeout` seconds)."""
        try:
            self.thread.join(max(0.0, float(timeout)))
        except Exception as e:
            self.error = self.error or f"{type(e).__name__}: {e}"
            return "failed"
        if self.thread.is_alive():
            return "running"
        return "done" if self.spec is not None else "failed"


def extract_observer(
    brief: str,
    openapi: dict[str, Any],
    tools: dict[str, ToolDef],
    trace: Trace,
    deadline: float | None = None,
    extract_fn: Any = None,
    examples: str = "",
) -> ExtractionObserver | None:
    """Start the cross-check extraction (same inputs, same deadline) in the background, right
    when the main extraction starts. None when BUILDER_OBSERVER=0 or it could not start. Its
    spec is also extract()'s fallback (pass the observer as extract(..., fallback=...))."""
    import os

    if str(os.environ.get("BUILDER_OBSERVER", "1")).strip() == "0":
        trace.note("uncertainty: observer disabled (BUILDER_OBSERVER=0)")
        return None
    fn = extract_fn or extract
    proxy = _ObserverTrace(trace)
    try:
        more = {"examples": examples} if str(examples or "").strip() else {}
        return ExtractionObserver(
            lambda: fn(brief, openapi, tools, proxy, deadline=deadline, **more)
        )
    except Exception as e:
        trace.note(f"uncertainty: observer not started: {type(e).__name__}: {str(e)[:200]}")
        return None


# Values of the spec that are the builder's own vocabulary, not the brief's: never grounded.
_OWN_WORDS = {
    *(v for vals in ENUMS.values() for v in vals),
    *CLOSE_REASON_KEYS,
    "paid",
    "current",
    "full",
    "floor",
    "attempt1",
    "deadline",
    "business",
    "calendar",
    "last_business_day_of_week",
    "nth_business_day",
    "date",
    "digits",
    "year",
    "text",
    "DMY",
    "MDY",
}


def _clean(x: Any) -> Any:
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in sorted(x.items()) if k != "_"}
    if isinstance(x, list):
        return [_clean(v) for v in x]
    return x.strip() if isinstance(x, str) else x


def policy_fields(spec: Any) -> dict[str, Any]:
    """The policy of a spec as {field: value}, without its wording (notes, lines, how the
    identity questions are asked)."""
    s = _safe(spec)
    out: dict[str, Any] = {}
    bands = [
        {
            k: b.get(k) or (None if k == "deadline_from_field" else b.get(k))
            for k in (
                "min_days_late",
                "max_days_late",
                "deadline_days",
                "deadline_unit",
                "deadline_from_field",
                "attempts",
            )
        }
        for b in s.get("bands") or []
        if isinstance(b, dict)
    ]
    out["bands"] = sorted(bands, key=lambda b: json.dumps(b.get("min_days_late")).rjust(8))
    cal = s.get("calendar") if isinstance(s.get("calendar"), dict) else {}
    out["calendar.holidays"] = sorted({str(h) for h in cal.get("holidays") or []})
    wd = cal.get("workdays")
    out["calendar.workdays"] = sorted(wd, key=str) if isinstance(wd, list) else wd
    for key in ("callback", "outcomes"):
        d = s.get(key) if isinstance(s.get(key), dict) else {}
        for k, v in d.items():
            if k != "_":
                out[f"{key}.{k}"] = _clean(v)
    out["attempt1"] = _clean(s.get("attempt1"))
    out["attempt_plan"] = _clean(s.get("attempt_plan"))
    neg = s.get("neg") if isinstance(s.get("neg"), dict) else {}
    for k, v in neg.items():
        if k in ("_", "lines"):
            continue
        if isinstance(v, dict):
            for k2, v2 in v.items():
                if k2 != "_":
                    out[f"neg.{k}.{k2}"] = _clean(v2)
        else:
            out[f"neg.{k}"] = _clean(v)
    tf = s.get("tool_fields") if isinstance(s.get("tool_fields"), dict) else {}
    for k, v in tf.items():
        if k != "_":
            out[f"tool_fields.{k}"] = _clean(v)
    out["product_fields"] = _clean(s.get("product_fields"))
    out["floor_field"] = _clean(s.get("floor_field"))
    ident = s.get("identity") if isinstance(s.get("identity"), dict) else {}
    out["identity.factors"] = sorted(
        f"{q.get('field')}:{q.get('kind')}"
        for q in ident.get("questions") or []
        if isinstance(q, dict)
    )
    out["identity.date_order"] = _clean(ident.get("date_order"))
    out["identity.swap_hint"] = _clean(ident.get("swap_hint"))
    out["identity.max_wrong_answers"] = _clean(ident.get("max_wrong_answers"))
    out["calendar.week_start"] = _clean(cal.get("week_start"))
    out["language"] = _clean(s.get("language"))
    out["currency"] = _clean(s.get("currency"))
    out["floor_rule"] = _clean(s.get("floor_rule"))
    out["dated_changes"] = sorted(
        (_clean({k: v for k, v in ch.items() if k != "source"})
         for ch in s.get("dated_changes") or [] if isinstance(ch, dict)),
        key=lambda c: json.dumps(c, sort_keys=True),
    )  # fmt: skip
    out["forbidden_before_verify"] = sorted(
        {fold(w).strip() for w in s.get("forbidden_before_verify") or [] if str(w).strip()}
    )
    return out


def _grounded(value: Any, docs: _Docs, api_text: str) -> str:
    """'y' when every value of `value` the documents (or, for a field name, the tool API) can
    show is there, 'n' when one is not, '-' when nothing can be checked (flags, own
    vocabulary). Read like grounding() but without the field's cue words."""
    leaves: list[Any] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        else:
            leaves.append(x)

    walk(value)
    seen = False
    for x in leaves:
        if x is None or isinstance(x, bool) or x == "" or x in _OWN_WORDS:
            continue
        seen = True
        if isinstance(x, int | float):
            ok = docs.number(x, None) == "ok"
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(x)):
            ok = docs.date(str(x), None) == "ok"
        elif _hhmm(x):
            ok = docs.time(str(x), None) == "ok"
        else:
            sx = str(x).split(":")[0]
            pat = r"(?<![a-z0-9_])" + re.escape(fold(sx)) + r"(?![a-z0-9_])"
            ok = docs.word(sx) == "ok" or bool(re.search(pat, api_text))
        if not ok:
            return "n"
    return "y" if seen else "-"


def _short(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False)[:160]


def observe(
    trace: Trace,
    observer: ExtractionObserver | None,
    spec: dict[str, Any],
    brief: str,
    openapi: dict[str, Any] | None,
    wait_s: float,
) -> dict[str, Any]:
    """Wait up to `wait_s` for the observer, log where its reading differs from the main spec
    and apply the one merge: forbidden_before_verify := union of both (main order first).
    Everything else stays the main spec's. Never raises; returns a summary for the manifest."""
    info: dict[str, Any] = {"status": "off"}
    if observer is None:
        return info
    try:
        state = observer.wait(wait_s)
        info["status"] = state
        if state == "running":
            trace.note(
                f"uncertainty: observer not finished after {max(0.0, wait_s):.0f}s; "
                "building with the main spec"
            )
            return info
        if state != "done" or not isinstance(observer.spec, dict):
            trace.note(f"uncertainty: observer failed ({observer.error or 'no spec'}); ignored")
            return info
        other = observer.spec
        api_text = fold(json.dumps(openapi or {}, ensure_ascii=False))
        docs = _Docs(brief, "", _spec_language(spec if isinstance(spec, dict) else {}, brief))
        a, b = policy_fields(spec), policy_fields(other)
        disputed = []
        for k in sorted(set(a) | set(b)):
            va, vb = a.get(k), b.get(k)
            if json.dumps(va, sort_keys=True) == json.dumps(vb, sort_keys=True):
                continue
            disputed.append(k)
            trace.note(
                f"uncertainty: {k} | main={_short(va)} other={_short(vb)} | grounded "
                f"main={_grounded(va, docs, api_text)} other={_grounded(vb, docs, api_text)}"
            )
        mine = spec.get("forbidden_before_verify")
        theirs = other.get("forbidden_before_verify")
        main_words = [str(w) for w in mine if str(w).strip()] if isinstance(mine, list) else []
        have = {fold(w).strip() for w in main_words}
        added, refused = [], []
        mergeable = isinstance(theirs, list) and (mine is None or isinstance(mine, list))
        # a word the main spec's own pre-verification lines say (its opening, its identity
        # questions, the bank's name in them) would contradict them: never merged (SX-9)
        own_lines = _pre_verify_texts(spec)
        for w in theirs if mergeable else []:
            w = str(w).strip() if isinstance(w, str | int | float) else ""
            if not w or fold(w) in have:
                continue
            have.add(fold(w))
            if any(_says_word(w, t) for t in own_lines.values()):
                refused.append(w)
            else:
                added.append(w)
        if added:
            spec["forbidden_before_verify"] = main_words + added
        trace.note(
            f"uncertainty: {len(disputed)} of {len(set(a) | set(b))} policy fields disputed "
            f"(the main spec is kept); forbidden_before_verify: union applied, {len(added)} "
            f"added from the observer {added[:10]}"
            + (
                f"; not merged (said by the spec's own lines before verification) {refused[:10]}"
                if refused
                else ""
            )
        )
        info.update(disputed=disputed, forbidden_added=added, forbidden_refused=refused)
    except Exception as e:
        trace.note(f"uncertainty: comparison failed: {type(e).__name__}: {str(e)[:200]}")
        info["status"] = "error"
    return info
