"""Test helpers for the coverage matrix, the reply transformations and the round trips. The
oracle, the synthetic cases and the call simulator live in fb/harness/selftest.py (the build
runs them too); this adds the public cases and spoken forms of amounts, dates and times."""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
from typing import Any

from fb.harness import selftest as S
from fb.harness.scoring import score_ledger
from spec_fixtures import SPEC_A, SPEC_B
from test_engine import CASES, CFG, CFG_A

FIX = Path(__file__).parent / "fixtures"
SPECS = {
    "A": SPEC_A,
    "B": SPEC_B,
    "ABfix": json.loads((FIX / "spec_ab_extracted.json").read_text(encoding="utf-8")),
    "NW": json.loads((FIX / "spec_northwind_extracted.json").read_text(encoding="utf-8")),
}
NOWS = ["2026-10-14T11:00", "2026-10-16T11:00", "2026-10-17T11:00", "2026-12-24T11:00"]
Y, N, BYE, K = S.Y, S.N, S.BYE, S.K


# ------------------------------------------------------------------ public cases
def texted(r: dict[str, Any]) -> dict[str, Any]:
    """A scripted reply with the words a customer would say."""
    r = dict(r)
    if "text" not in r:
        k = r.get("kind", "unclear")
        if k in ("offer", "vague"):
            said = " ".join(x for x in (r.get("amount", ""), r.get("date", "")) if x)
            r["text"] = "I can pay " + said + "."
        elif k == "question":
            r["text"] = "Is this a scam?"
        else:
            r["text"] = {"yes": "Yes.", "no": "No."}.get(k) or S.TXT.get(k, k + ".")
    return r


def cfg_of(cid: str) -> dict[str, Any]:
    return CFG if CASES[cid[:6]]["brief"] == "B" else CFG_A


def run_public(cid: str, replies: list[dict[str, Any]], faults=None, engine=None):
    """A public case with these replies (three goodbyes after them)."""
    c = copy.deepcopy(CASES[cid[:6]])
    for i, p in enumerate(c["products"]):
        p.setdefault("product_id", f"p{i + 1}")
    if faults is not None:
        c["faults"] = faults
    cfg = cfg_of(cid)
    res = S.sim(c, [texted(r) for r in replies] + [BYE] * 3, cfg, engine)
    return c, cfg, res


def verdict(cid: str, replies: list[dict[str, Any]]) -> tuple[Any, list[str]]:
    """Invariants (oracle-free) plus the kit's scorer on a public case."""
    c, cfg, res = run_public(cid, replies)
    m = S.Model(cfg, c["variables"]["now"])
    bad = S.invariants(m, c, res)
    sc = score_ledger(c, res.ledger)
    if not sc["pass"]:
        bad.append("kit score: " + str(sc["failed"])[:200])
    return res, bad


def promises(res) -> list[tuple[str, float, str]]:
    return sorted((p["product_id"], p["amount"], p["date"]) for p in res.ledger["promises"])


def interrupt(replies: list[dict], k: int) -> list[dict]:
    return [*replies[:k], {"kind": "question", "text": "Wait, is this a scam?"}, *replies[k:]]


def human_at(replies: list[dict], k: int, n: int) -> list[dict]:
    return replies[:k] + [K("human")] * n + replies[k:]


# ------------------------------------------------------------------ spoken forms
ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen"
)
ONES = ONES.split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()  # noqa: SIM905
ORD = (
    "_ first second third fourth fifth sixth seventh eighth ninth tenth eleventh "
    "twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth "
    "nineteenth twentieth"
)
ORD = ORD.split()


def words(n: int, hyph: bool = False, and_: bool = True) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return TENS[t] + (("-" if hyph else " ") + ONES[o] if o else "")
    if n < 1000:
        h, r = divmod(n, 100)
        return (
            ONES[h] + " hundred" + ((" and " if and_ else " ") + words(r, hyph, and_) if r else "")
        )
    th, r = divmod(n, 1000)
    sep = " and " if and_ and r < 100 else " "
    return words(th, hyph, and_) + " thousand" + (sep + words(r, hyph, and_) if r else "")


def amount_forms(a: float) -> list[str]:
    """Ways a customer says an amount with at most two decimals."""
    whole = int(a)
    pence = round((a - whole) * 100)
    f = [f"{a:.2f}", f"£{a:.2f}"]
    if pence == 0:
        f += [
            str(whole),
            f"£{whole}",
            f"{whole} pounds",
            words(whole),
            words(whole, hyph=True),
            words(whole, and_=False) + " pounds",
        ]
        if 1100 <= whole < 2000 and whole % 100 == 0:
            f.append(words(whole // 100) + " hundred")  # 'fifteen hundred'
        if whole % 1000 == 0:
            f.append(f"{whole // 1000}k")
    else:
        f += [
            words(whole) + f" pounds and {words(pence)} pence",
            words(whole, True, False) + " point " + " ".join(ONES[int(c)] for c in f"{pence:02d}"),
            f"{whole} pounds {pence}",
        ]
        if pence >= 10:
            f.append(words(whole) + " " + words(pence))  # 'six hundred and thirty-seven fifty'
    return f


def ordinal(day: int) -> str:
    if day <= 20:
        return ORD[day]
    t, o = divmod(day, 10)
    return (TENS[t] + "-" + ORD[o]) if o else ("thirtieth" if t == 3 else "twentieth")


def date_forms(d: dt.date) -> list[str]:
    day = d.day
    suf = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    mon = S.MONTHS[d.month - 1]
    wd = S.WEEKDAYS[d.weekday()]
    fs = [
        d.isoformat(),
        f"{day} {mon}",
        f"{mon} {day}",
        f"{day}{suf} {mon}",
        f"{mon} {day}{suf}",
        f"the {day}{suf} of {mon}",
        f"the {ordinal(day)} of {mon}",
        f"{ordinal(day)} of {mon}",
        f"{wd} the {day}{suf}",
        f"{wd} {day} {mon}",
        f"{day} {mon[:3]}",
        f"{mon[:3]} {day}",
        f"{day} {mon} {d.year}",
        f"{day}/{d.month}/{d.year}",
    ]
    if day > 12:
        fs.append(f"{day}/{d.month}")
    return fs


def time_forms(h: int, m: int) -> list[str]:
    h12 = (h - 1) % 12 + 1
    ap = "am" if h < 12 else "pm"
    fs = [f"{h:02d}:{m:02d}", f"{h12}:{m:02d} {ap}", f"{h12}:{m:02d}{ap}"]
    if m == 0:
        fs += [f"{h12} {ap}", f"{h12}{ap}", f"{words(h12)} {ap}"]
        if h == 12:
            fs.append("noon")
        elif h < 12:
            fs.append(f"{words(h12)} in the morning")
        else:
            fs.append(f"{words(h12)} in the {'afternoon' if h < 18 else 'evening'}")
    if m == 30:
        fs.append(f"half past {words(h12)}")
    if m == 15:
        fs.append(f"quarter past {words(h12)}")
    if m == 45:
        fs.append(f"quarter to {words(h12 % 12 + 1)}")
    return fs
