"""Self-test of the deterministic half of a built agent, with an oracle worked out from the spec
alone (no brief values in this file): calendar, bands, attempt plan, amounts, notes, closing.

    run_selftest(spec_or_cfg, tools=None, budget_s=4.0) -> {"cells", "failures", "skipped"}

Synthetic customers (one product per type and band edge, two or three products when the spec
writes notes) talk to the code-node bodies through the local bank (fb/harness/bank.py), the
replies given as extraction values. Every call is checked against
- the oracle: what the spec says the ledger must hold (promise amount/date, notes, outcome),
- invariants: no empty sentence, a promise within [floor, full] on a business day on or before
  the product's deadline, a bounded call, a close outcome, notes per the spec, and the
  post-call reviewer (fb/harness/review.py) with no critical finding,
then random calls (seeded) with tool faults are checked against the invariants. Log only: it
never raises (an exception gives skipped=True) and stops when the time budget is spent."""

from __future__ import annotations

import datetime as dt
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, ClassVar

from fb.harness.bank import Bank
from fb.harness.engine_sim import SimResult, reply_vars
from fb.harness.review import critical, enums_from_tools, review_sim
from fb.nodecode import bodies
from fb.nodecode.embed import namespace, snippet

NL = chr(10)
MONTHS = "January February March April May June July August September October November December"
MONTHS = MONTHS.split()
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
Y = {"kind": "yes", "text": "Yes."}
N = {"kind": "no", "text": "No."}
BYE = {"kind": "unclear", "text": "Bye."}
TXT = {
    "too_much": "That is too much for me right now.",
    "paid": "I already paid it.",
    "dispute": "This is not my debt.",
    "hardship": "I lost my job.",
    "restructure": "I need lower instalments.",
    "human": "I want to talk to a person.",
    "stop": "I do not want to talk.",
    "question": "What is this about?",
    "unclear": "Hmm.",
    "evasive": "I do not know.",
    "vague": "Maybe later.",
}
MAX_REPLIES = 60


def K(kind: str, **kw: Any) -> dict[str, Any]:
    return {"kind": kind, "text": TXT.get(kind, kind + "."), **kw}


def offer(date: str | None = None, amount: Any = None, all_: bool = False) -> dict[str, Any]:
    said = " ".join(x for x in (str(amount if amount is not None else ""), str(date or "")) if x)
    r: dict[str, Any] = {"kind": "offer", "text": "I can pay " + said + "."}
    if date:
        r["date"] = date
    if amount is not None:
        r["amount"] = str(amount)
    if all_:
        r["all"] = True
    return r


# ------------------------------------------------------------------ running the bodies
class Engine:
    """Runs a body the way its code node does. fast: the libraries executed once, only the
    body compiled (same try/except); else the real generated snippet."""

    _lib: ClassVar[dict[str, Any] | None] = None
    _code: ClassVar[dict[Any, Any]] = {}
    _facts: ClassVar[dict[Any, Any]] = {}

    def __init__(self, real: bool = False) -> None:
        self.real = real

    @staticmethod
    def _calendar_facts(now: Any, cfg: dict[str, Any]) -> dict[str, Any]:
        """calendar_facts is pure and the slowest call of a turn: kept per (now, cfg)."""
        key = (str(now), json.dumps(cfg, sort_keys=True))
        if key not in Engine._facts:
            if len(Engine._facts) > 500:
                Engine._facts.clear()
            Engine._facts[key] = Engine._lib["calendar_facts"](now, cfg)  # type: ignore[index]
        return dict(Engine._facts[key])

    def run(self, body: str, cfg: dict[str, Any], v: dict[str, Any], errors: list[str]) -> None:
        v.pop("code_error", None)
        if self.real:
            key = (body, json.dumps(cfg, sort_keys=True))
            code = Engine._code.get(key)
            if code is None:
                code = Engine._code[key] = compile(snippet(body, cfg), "code_node", "exec")
            exec(code, {"vars": v})
        else:
            if Engine._lib is None:
                Engine._lib = namespace()
            code = Engine._code.get(body)
            if code is None:
                inner = NL.join("    " + s for s in body.strip().splitlines())
                src = "try:" + NL + inner + NL + "except Exception as _e:" + NL
                src += "    vars['code_error'] = str(_e)[:200]" + NL
                code = Engine._code[body] = compile(src, "body", "exec")
            ns = dict(Engine._lib)
            ns["vars"], ns["CFG"] = v, cfg
            ns["calendar_facts"] = Engine._calendar_facts
            exec(code, ns)
        if v.get("code_error"):
            errors.append(str(v["code_error"]))


# ------------------------------------------------------------------ calendar oracle
class Cal:
    """The spec's calendar, computed independently of collections_lib."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.hol = {dt.date.fromisoformat(h) for h in cfg.get("holidays") or []}
        self.wd = set(cfg.get("workdays") or [0, 1, 2, 3, 4])

    def bd(self, d: dt.date) -> bool:
        return d.weekday() in self.wd and d not in self.hol

    def next_bd(self, d: dt.date) -> dt.date:  # on or after
        while not self.bd(d):
            d += dt.timedelta(1)
        return d

    def add_bd(self, d: dt.date, n: int) -> dt.date:
        if n <= 0:
            return self.next_bd(d)
        while n > 0:
            d += dt.timedelta(1)
            if self.bd(d):
                n -= 1
        return d

    def last_bd_of_week(self, d: dt.date) -> dt.date:
        sun = d + dt.timedelta(6 - d.weekday())
        while not self.bd(sun):
            sun -= dt.timedelta(1)
        return sun

    def attempt1(self, today: dt.date, rule: dict[str, Any]) -> dt.date:
        if rule.get("rule") == "nth_business_day":
            n = rule.get("n")
            return self.add_bd(today, 1 if n in (None, "") else int(n))
        lw = self.last_bd_of_week(today)
        if today >= lw:  # today is that day (or later): next week's
            lw = self.last_bd_of_week(today + dt.timedelta(7))
        return lw


def spoken(d: dt.date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


def gen_cfg(spec: dict[str, Any]) -> dict[str, Any]:
    """A spec (extractor output), a to_generator() result or its cfg -> the generator cfg."""
    if "neg" in spec and "holidays" in spec and "floor_field" in spec and "calendar" not in spec:
        return spec
    if isinstance(spec.get("cfg"), dict):
        return spec["cfg"]
    from fb.spec_extract import to_generator

    return to_generator(spec)["cfg"]


class Model:
    """What the spec says about one call, computed without the engine."""

    def __init__(self, cfg: dict[str, Any], now: str) -> None:
        self.cfg = cfg
        self.neg = cfg["neg"]
        self.now = now
        self.today = dt.datetime.fromisoformat(now).date()
        self.cal = Cal(cfg)
        self.notes = bool(self.neg.get("notes"))
        self.bands = sorted(cfg["bands"], key=lambda b: b["min_days_late"])

    def band(self, dl: int) -> dict[str, Any]:
        """The band of `dl`; outside every band the nearest one (the engine's rule)."""
        return min(
            self.bands,
            key=lambda b: max(b["min_days_late"] - dl, dl - b["max_days_late"], 0),
        )

    def deadline(self, dl: int) -> dt.date:
        b = self.band(dl)
        n = b.get("deadline_business_days")
        n = b.get("deadline_days", 0) if n is None else n
        if b.get("deadline_unit") == "calendar":
            d = self.today + dt.timedelta(int(n))
            while not self.cal.bd(d) and d >= self.today:
                d -= dt.timedelta(1)
            return d if d >= self.today else self.cal.next_bd(self.today)
        return self.cal.add_bd(self.today, int(n))

    def attempts(self, dl: int) -> int:
        return int(self.band(dl).get("attempts") or 0)

    def a1(self) -> dt.date:
        return self.cal.attempt1(self.today, self.cfg.get("attempt1") or {})

    def o(self, k: str) -> str:
        return self.neg["outcomes"].get(k, k)

    def r(self, k: str) -> str:  # close_call reason
        return (self.neg.get("close_reasons") or {}).get(k) or self.neg["reasons"].get(k, k)

    def nr(self, k: str) -> str:  # add_note reason
        return self.neg["reasons"].get(k, k)

    def pol(self, k: str, default: Any) -> Any:
        v = self.neg.get(k)
        return default if v is None else v


# ------------------------------------------------------------------ case synthesis
def make_case(m: Model, products: list[dict[str, Any]], cid: str = "SYN", faults=None) -> dict:
    """products: [{type, past_due, days_late, floor}], written with the spec's field names."""
    cfg = m.cfg
    pf = cfg.get("product_fields") or {}
    ff = cfg["floor_field"]
    plist = []
    for i, p in enumerate(products):
        q = {"product_id": f"p{i + 1}", "type": p["type"]}
        q[pf.get("type") or "type"] = p["type"]
        q[pf.get("product_id") or "product_id"] = f"p{i + 1}"
        q[pf.get("past_due") or "past_due"] = p["past_due"]
        q[pf.get("days_late") or "days_late"] = p["days_late"]
        q[ff[p["type"]]] = p["floor"]
        for k in ("card_last4", "last4", "account_name", "name"):
            q.setdefault(k, "4821" if "4" in k else "account")
        plist.append(q)
    return {
        "id": cid,
        "brief": "X",
        "variables": {"first_name": "A", "last_name": "B", "title": "Mr", "now": m.now},
        "identity": {"date_of_birth": "1988-03-14", "phone_last4": "4471"},
        "products": plist,
        "faults": faults or [],
    }


# ------------------------------------------------------------------ the call
def sim(case: dict, replies, cfg: dict[str, Any], engine: Engine | None = None) -> SimResult:
    """One call through the bodies: FACTS, DECIDE per reply (create_promise / AFTER_RECORD on a
    record), transfer with AFTER_TRANSFER retries, add_note / AFTER_NOTE, close_call.
    `replies`: a list, or f(i, says) -> reply."""
    eng = engine or Engine()
    bank = Bank()
    v: dict[str, Any] = dict(bank.open_session(case))
    cid = v["customer_id"]
    res = SimResult(vars=v)
    eng.run(bodies.SETUP, cfg, v, res.code_errors)
    for f, ans in case["identity"].items():
        bank.call(cid, "verify_identity", {"customer_id": cid, "field": f, "answer": ans})
    st, acc = bank.call(cid, "get_account", {"customer_id": cid})
    if st != 200 or not acc.get("products"):
        v["products"] = None
        eng.run(bodies.FACTS_ERROR, cfg, v, res.code_errors)
    else:
        v["products"] = acc["products"]
        eng.run(bodies.FACTS, cfg, v, res.code_errors)
    res.says.append(str(v.get("neg_say")))
    used = 0

    def _gen():
        if callable(replies):
            for i in range(MAX_REPLIES):
                yield replies(i, res.says)
        else:
            yield from replies

    for r in _gen():
        if v.get("neg_action") in ("finish", "transfer"):
            break
        used += 1
        v.update(reply_vars(r))
        v["has_question"] = "yes" if r.get("kind") == "question" or r.get("q") else "no"
        v["system.conversation"] = [{"role": "user", "content": r.get("text", "")}]
        eng.run(bodies.DECIDE, cfg, v, res.code_errors)
        if v.get("code_error"):
            eng.run(bodies.NEG_ERROR, cfg, v, res.code_errors)
        guard = 0
        while v.get("neg_action") == "record" and guard < 20:
            guard += 1
            st, out = bank.call(
                cid,
                "create_promise",
                {
                    "customer_id": cid,
                    "product_id": v["rec_product_id"],
                    "amount": v["rec_amount"],
                    "date": v["rec_date"],
                },
            )
            v["rec_ok"] = "true" if st == 200 and out.get("ok") else "false"
            v["rec_err"] = "" if st == 200 and out.get("ok") else str(out.get("error") or "")
            eng.run(bodies.AFTER_RECORD, cfg, v, res.code_errors)
        if v.get("neg_action") == "say":
            res.says.append(str(v.get("neg_say")))
    res.used = used  # type: ignore[attr-defined]
    res.finished = v.get("neg_action") in ("finish", "transfer")  # type: ignore[attr-defined]
    if v.get("neg_action") == "transfer":
        for _ in range(3):
            st, out = bank.call(
                cid, "transfer_to_agent", {"customer_id": cid, "queue": v["transfer_queue"]}
            )
            v["transfer_ok"] = "true" if st == 200 and out.get("ok") else "false"
            eng.run(bodies.AFTER_TRANSFER, cfg, v, res.code_errors)
            if v.get("transfer_next") != "retry":
                break
    if res.finished:  # type: ignore[attr-defined]
        n = 0
        while int(v.get("note_count") or 0) > 0 and n < 12:
            n += 1
            st, out = bank.call(
                cid,
                "add_note",
                {
                    "customer_id": cid,
                    "product_id": v["note_product_id"],
                    "reason": v["note_reason"],
                },
            )
            v["note_ok"] = "true" if st == 200 and out.get("ok") else "false"
            eng.run(bodies.AFTER_NOTE, {**cfg, "note_check": True}, v, res.code_errors)
        res.says.append(str(v.get("final_say")))
    close = {"customer_id": cid, "outcome": v.get("final_outcome") or ""}
    if v.get("final_reason"):
        close["reason"] = v["final_reason"]
    bank.call(cid, "close_call", close)
    res.ledger = bank.ledger(cid)
    return res


def summarize(led: dict[str, Any]) -> dict[str, Any]:
    return {
        "outcome": led["outcome"],
        "reason": led["reason"],
        "promises": sorted((p["product_id"], p["amount"], p["date"]) for p in led["promises"]),
        "notes": sorted((n["product_id"], n["reason"]) for n in led["notes"]),
        "transfers": [t["queue"] for t in led["transfers"]],
    }


# ------------------------------------------------------------------ oracle cells
@dataclass
class Cell:
    name: str
    products: list[dict[str, Any]]
    replies: list[dict[str, Any]]
    exp: dict[str, Any] | None  # None: invariants only
    now: str
    faults: list[dict[str, Any]] = field(default_factory=list)
    used: int | None = None  # replies the call must take


def expect(outcome, reason=None, promises=None, notes=None, transfers=None) -> dict[str, Any]:
    return dict(outcome=outcome, reason=reason, promises=promises, notes=notes, transfers=transfers)


def no_promise_close(m: Model, status: list[str]) -> dict[str, Any]:
    """The close for products that all end without a promise, with these statuses."""
    notes = [(f"p{i + 1}", m.nr(s)) for i, s in enumerate(status)] if m.notes else []
    if all(s == "paid" for s in status):
        paid_reason = (m.neg.get("close_reasons") or {}).get("paid")
        return expect(m.o("paid"), paid_reason or None, [], notes, [])
    left = [s for s in status if s != "paid"]
    first = left[0]
    if m.pol("no_promise_reason", "first_product") == "priority":
        first = next((s for s in m.pol("reason_priority", []) if s in left), first)
    return expect(m.o("no_promise"), m.r(first), [], notes, [])


def _prod(t: str, dl: int, full: float = 1000.0, floor: float = 300.0) -> dict[str, Any]:
    return dict(type=t, past_due=full, days_late=dl, floor=floor)


def _transfer_notes(m: Model, what: str, n: int, status: list[str] | None = None):
    """Notes on a transferred call: none, or (notes_on_transfer) one per product. An untouched
    one takes the transfer's code when that is a note code too (add_note's codes are the
    reasons' values), else refused."""
    if not m.notes:
        return None
    if not m.pol("notes_on_transfer", False):
        return []
    rs = m.neg["reasons"]
    tr = (m.neg.get("transfer_reasons") or {}).get(what)
    code = rs.get(what) or (tr if tr in rs.values() else None) or m.nr("refused")
    st = status or ["open"] * n
    return [(f"p{i + 1}", code if s == "open" else m.nr(s)) for i, s in enumerate(st)]


def single_cells(m: Model, quick: bool = True):
    """One product: band edges x product types; ladder, dates, amounts, faults; semantic exits."""
    cfg, today, cal = m.cfg, m.today, m.cal
    types = list(cfg["floor_field"])
    bands = m.bands
    now = m.now
    if quick:
        dls = sorted({bands[0]["min_days_late"], bands[-1]["max_days_late"]})
    else:
        dls = sorted({d for b in bands for d in (b["min_days_late"], b["max_days_late"])})
    a1 = m.a1()
    plan = m.neg.get("attempt_plan") or []
    pa = int(m.pol("promise_attempts", 2))
    for t in types:
        for dl in dls:
            p = _prod(t, dl)
            D, A = m.deadline(dl), m.attempts(dl)
            tag = f"{t}/dl{dl}"
            tp = cal.next_bd(today)
            pr = m.o("promise")
            if tp <= D:
                yield Cell(
                    f"today_full {tag}",
                    [p],
                    [Y, Y, Y, Y, BYE],
                    expect(pr, None, [("p1", 1000.0, tp.isoformat())]),
                    now,
                )
            for k in range(1, A + 2):
                ex = None
                if k == 1 and A >= 1 and a1 <= D:
                    ex = expect(pr, None, [("p1", 1000.0, a1.isoformat())])
                elif 1 < k <= A and k - 1 < len(plan):
                    st = plan[k - 1]
                    amt = 1000.0 if st.get("amount") in ("full", "current") else 300.0
                    dd = {"attempt1": a1, "deadline": D}.get(st.get("date"))
                    if dd and dd <= D:
                        ex = expect(pr, None, [("p1", amt, dd.isoformat())])
                elif k == A + 1:
                    ex = no_promise_close(m, ["refused"])
                if ex:
                    tail = [Y, Y, Y, Y, BYE] if k <= A else [N, N, N]
                    yield Cell(f"ladder{k} {tag}", [p], [N] * k + tail, ex, now)
            yield Cell(
                f"ladder_count {tag}",
                [p],
                [N] * 30,
                no_promise_close(m, ["refused"]),
                now,
                used=A + 1,
            )
            days = {
                "D-1": D - dt.timedelta(1),
                "D": D,
                "D+1": D + dt.timedelta(1),
                "D+1bd": cal.add_bd(D, 1),
                "today": today,
                "tomorrow": today + dt.timedelta(1),
            }
            wk = next(
                today + dt.timedelta(i)
                for i in range(14)
                if not cal.bd(today + dt.timedelta(i)) and (today + dt.timedelta(i)) > today
            )
            days["non_bd"] = wk
            for nm, d in days.items():
                if d < today:
                    continue
                dd = D if d > D else min(cal.next_bd(d), D)
                yield Cell(
                    f"date[{nm}={d}] {tag}",
                    [p],
                    [offer(spoken(d)), Y, Y, Y, Y, BYE],
                    expect(pr, None, [("p1", 1000.0, dd.isoformat())]),
                    now,
                )
            yield Cell(
                f"date[yesterday] {tag}",
                [p],
                [offer(spoken(today - dt.timedelta(1))), Y, Y, Y, Y, BYE],
                None,
                now,
            )
            if a1 <= D:
                for nm, a in (
                    ("floor-.01", 299.99),
                    ("floor", 300.0),
                    ("mid", 650.0),
                    ("full", 1000.0),
                    ("full+.01", 1000.01),
                    ("zero", 0),
                    ("big", 5000),
                ):
                    ex = None
                    if 300.0 <= a <= 1000.0:
                        ex = expect(pr, None, [("p1", a, a1.isoformat())])
                    elif a == 299.99:
                        ex = expect(pr, None, [("p1", 300.0, a1.isoformat())])
                    yield Cell(
                        f"amount[{nm}] {tag}",
                        [p],
                        [offer(spoken(a1), a), Y, Y, Y, Y, BYE],
                        ex,
                        now,
                    )
            if tp <= D:
                yield Cell(
                    f"too_much {tag}",
                    [p],
                    [K("too_much"), Y, Y, Y, Y, BYE],
                    expect(pr, None, [("p1", 300.0, tp.isoformat())]),
                    now,
                )
            yield Cell(f"too_much_then_no {tag}", [p], [K("too_much")] + [N] * 6, None, now)
            yield Cell(
                f"floor==full {tag}",
                [_prod(t, dl, 300.0, 300.0)],
                [K("too_much"), Y, Y, Y, Y, BYE],
                None,
                now,
            )
            for f in (pa - 1, pa):
                ex = None
                if A < 1 or a1 > D:
                    pass
                elif f < pa:
                    ex = expect(pr, None, [("p1", 1000.0, a1.isoformat())])
                elif m.pol("record_failure", "continue") == "continue":
                    ex = no_promise_close(m, ["service_error"])
                yield Cell(
                    f"create_promise_fails_{f} {tag}",
                    [p],
                    [N, Y, Y, Y, Y, BYE],
                    ex,
                    now,
                    faults=[{"tool": "create_promise", "fail_first_n": f}],
                )
            if quick:
                break
    # semantic exits, one product per band
    oc, trs, qs = m.o("transferred"), m.neg.get("transfer_reasons") or {}, m.neg.get("queues") or {}
    for b in bands:
        p = _prod(types[0], b["min_days_late"])
        tag = f"band {b.get('name')}"
        yield Cell(f"paid {tag}", [p], [K("paid"), BYE], no_promise_close(m, ["paid"]), now)
        yield Cell(
            f"dispute {tag}", [p], [K("dispute"), BYE], no_promise_close(m, ["dispute"]), now
        )
        stops = int(m.pol("stop_requests_to_close", 2))
        yield Cell(
            f"stop x{stops} {tag}",
            [p],
            [K("stop")] * stops + [BYE],
            no_promise_close(m, ["refused"]),
            now,
        )
        yield Cell(
            f"restructure {tag}",
            [p],
            [K("restructure"), BYE],
            expect(
                oc,
                trs.get("restructuring"),
                [],
                _transfer_notes(m, "restructuring", 1),
                [qs.get("restructuring")],
            ),
            now,
        )
        hn = int(m.pol("human_requests_to_transfer", 2))
        yield Cell(
            f"human x{hn} {tag}",
            [p],
            [K("human")] * hn + [BYE],
            expect(oc, trs.get("human"), [], _transfer_notes(m, "human", 1), [qs.get("human")]),
            now,
        )
        hs, hq = m.pol("hardship", "offer_support"), m.pol("hardship_queue", "support")
        if hs == "offer_support":
            yield Cell(
                f"hardship accept {tag}",
                [p],
                [K("hardship"), Y, BYE],
                expect(oc, trs.get(hq), [], _transfer_notes(m, hq, 1, ["hardship"]), [qs.get(hq)]),
                now,
            )
            yield Cell(
                f"hardship decline {tag}",
                [p],
                [K("hardship"), N, N, BYE],
                no_promise_close(m, ["hardship"]),
                now,
            )
        elif hs == "transfer":
            yield Cell(
                f"hardship transfer {tag}",
                [p],
                [K("hardship"), BYE],
                expect(oc, trs.get(hq), [], _transfer_notes(m, hq, 1, ["hardship"]), [qs.get(hq)]),
                now,
            )
        else:
            yield Cell(
                f"hardship close {tag}",
                [p],
                [K("hardship"), BYE],
                no_promise_close(m, ["hardship"]),
                now,
            )
        yield Cell(
            f"transfer fails {tag}",
            [p],
            [K("restructure"), BYE],
            None,
            now,
            faults=[{"tool": "transfer_to_agent", "fail_first_n": 3}],
        )
        yield Cell(f"unclear forever {tag}", [p], [K("unclear")] * 40, None, now)
    for dl in (bands[0]["min_days_late"] - 1, bands[-1]["max_days_late"] + 1):
        p = _prod(types[0], max(dl, 0))
        yield Cell(f"out_of_band dl{dl}", [p], [Y, Y, Y, Y, BYE], None, now)


def _pool(m: Model) -> list[dict[str, Any]]:
    types, b = list(m.cfg["floor_field"]), m.bands
    picks = [
        (types[0], b[0]["min_days_late"]),
        (types[-1], b[-1]["max_days_late"]),
        (types[1 % len(types)], b[-1]["min_days_late"]),
    ]
    return [_prod(t, dl, 1000.0 + 100 * i, 300.0 + 10 * i) for i, (t, dl) in enumerate(picks)]


def multi_cells(m: Model):
    """Two and three products (a spec that writes notes, Brief B shape; every product with at
    least one persuasion attempt, which the scripts below rely on)."""
    if not m.notes or any(m.attempts(p["days_late"]) < 1 for p in _pool(m)):
        return
    cal, today, now, a1 = m.cal, m.today, m.now, m.a1()
    pool = _pool(m)
    pr = m.o("promise")
    for n in (2, 3):
        sub = pool[:n]
        ds = [m.deadline(p["days_late"]) for p in sub]
        tag = f"multi{n}"
        tp = cal.next_bd(today)
        if tp <= min(ds):
            yield Cell(
                f"all_today {tag}",
                sub,
                [Y, Y, Y, Y, BYE],
                expect(
                    pr,
                    None,
                    [(f"p{i + 1}", p["past_due"], tp.isoformat()) for i, p in enumerate(sub)],
                    [],
                ),
                now,
            )
            chat = {"kind": "yes", "text": "Work is so busy I can't even think, honestly. Yes."}
            yield Cell(
                f"all_today_chatty {tag}",
                sub,
                [chat, Y, Y, Y, BYE],
                expect(
                    pr,
                    None,
                    [(f"p{i + 1}", p["past_due"], tp.isoformat()) for i, p in enumerate(sub)],
                    [],
                ),
                now,
            )
        for nm, dd in (("max_deadline", max(ds)), ("min_deadline", min(ds))):
            exp_p = []
            for i, p in enumerate(sub):
                x = ds[i] if dd > ds[i] else min(cal.next_bd(dd), ds[i])
                exp_p.append((f"p{i + 1}", p["past_due"], x.isoformat()))
            yield Cell(
                f"one_date[{nm}] {tag}",
                sub,
                [N, offer(spoken(dd), all_=True), Y, Y, Y, Y, BYE],
                expect(pr, None, exp_p, []),
                now,
            )
        tot = 1 + sum(m.attempts(p["days_late"]) for p in sub)
        yield Cell(
            f"refuse_all {tag}", sub, [N] * 40, no_promise_close(m, ["refused"] * n), now, used=tot
        )
        yield Cell(
            f"restructure_after_agree {tag}",
            sub,
            [N, offer(spoken(a1), all_=True), K("restructure"), BYE],
            expect(
                m.o("transferred"),
                (m.neg.get("transfer_reasons") or {}).get("restructuring"),
                [],
                _transfer_notes(m, "restructuring", n),
                [(m.neg.get("queues") or {}).get("restructuring")],
            ),
            now,
        )
        yield Cell(
            f"paid_all {tag}", sub, [K("paid", all=True)] + [K("paid")] * 3 + [BYE], None, now
        )
        yield Cell(
            f"transfer_fails {tag}",
            sub,
            [K("restructure"), BYE],
            None,
            now,
            faults=[{"tool": "transfer_to_agent", "fail_first_n": 3}],
        )


STATUSES = ("promise", "promise2", "refuse", "dispute", "paid")


def combo_cells(m: Model, ks=(2, 3)):
    """Every mix of per-product endings for two and three products."""
    if not m.notes or any(m.attempts(p["days_late"]) < 1 for p in _pool(m)):
        return
    import itertools

    a1, pool, plan = m.a1(), _pool(m), m.neg.get("attempt_plan") or []
    for k in ks:
        for stats in itertools.product(STATUSES, repeat=k):
            ps = pool[:k]
            replies, exp_p, notes, ok = [N], [], [], True
            for i, (p, s) in enumerate(zip(ps, stats, strict=True)):
                pid, dl = f"p{i + 1}", p["days_late"]
                D, A = m.deadline(dl), m.attempts(dl)
                if s == "promise":
                    if A < 1 or a1 > D:
                        ok = False
                    replies.append(Y)
                    exp_p.append((pid, p["past_due"], a1.isoformat()))
                elif s == "promise2":
                    if A < 2 or len(plan) < 2:
                        ok = False
                        break
                    replies += [N, Y]
                    amt = (
                        p["past_due"]
                        if plan[1].get("amount") in ("full", "current")
                        else (p["floor"])
                    )
                    dd = a1 if plan[1].get("date") == "attempt1" else D
                    exp_p.append((pid, amt, dd.isoformat()))
                elif s == "refuse":
                    replies += [N] * A
                    notes.append((pid, m.nr("refused")))
                else:
                    replies.append(K(s))
                    notes.append((pid, m.nr(s)))
            if not ok:
                continue
            replies += [Y, BYE, BYE]
            if exp_p:
                ex = expect(m.o("promise"), None, exp_p, notes, [])
            else:
                ex = no_promise_close(m, ["refused" if s == "refuse" else s for s in stats])
            yield Cell("combo " + "/".join(stats), ps, replies, ex, m.now)


# ------------------------------------------------------------------ checks
def invariants(m: Model, case: dict, res: SimResult, tools=None) -> list[str]:
    """Rules every call keeps, whatever was said. Problem strings."""
    bad = [f"code_error: {e[:120]}" for e in res.code_errors]
    led = res.ledger
    prods = {q["product_id"]: q for q in case["products"]}
    pf = m.cfg.get("product_fields") or {}
    ff = m.cfg["floor_field"]
    for i, s in enumerate(res.says):
        if s.strip() in ("", "None", "[]"):
            bad.append(f"empty say #{i}")
    if not getattr(res, "finished", True):
        bad.append("not finished (loop or stall)")
    if not led["outcome"]:
        bad.append("no close outcome")
    seen = set()
    for p in led["promises"]:
        q = prods[p["product_id"]]
        full, floor = q[pf.get("past_due") or "past_due"], q[ff[q["type"]]]
        d = dt.date.fromisoformat(p["date"])
        dead = m.deadline(q[pf.get("days_late") or "days_late"])
        if p["product_id"] in seen:
            bad.append("two promises for one product")
        seen.add(p["product_id"])
        if not min(floor, full) - 1e-9 <= p["amount"] <= full + 1e-9:
            bad.append(f"promise amount {p['amount']} outside [{floor}, {full}]")
        if d < m.today or d > dead or not m.cal.bd(d):
            bad.append(f"promise date {d} (today {m.today}, deadline {dead})")
    oc = m.neg["outcomes"]
    if led["promises"] and led["outcome"] not in (oc.get("promise"), oc.get("transferred")):
        bad.append(f"promise recorded but close {led['outcome']}")
    if led["outcome"] == oc.get("promise") and not led["promises"]:
        bad.append("close=promise without a promise")
    if led["outcome"] == oc.get("transferred") and not led["transfers"]:
        bad.append("close=transferred without a transfer")
    noted = {n["product_id"]: n["reason"] for n in led["notes"]}
    promised = {p["product_id"] for p in led["promises"]}
    transferred = led["outcome"] == oc.get("transferred")
    if not m.notes and led["notes"]:
        bad.append("notes in a spec without notes")
    if transferred and not m.pol("notes_on_transfer", False) and led["notes"]:
        bad.append("notes on a transferred call")
    if len(noted) != len(led["notes"]):
        bad.append("two notes for one product")
    if noted.keys() & promised:
        bad.append("note for a promised product")
    codes = set(m.neg["reasons"].values())
    if any(n["reason"] not in codes for n in led["notes"]):
        bad.append(f"note reason outside the spec's codes {sorted(noted.values())}")
    want_notes = m.notes and (not transferred or m.pol("notes_on_transfer", False))
    if want_notes and getattr(res, "finished", True):
        tried = {c["args"].get("product_id") for c in led["calls"] if c["tool"] == "add_note"}
        miss = [p for p in prods if p not in tried and p not in promised]
        if miss:
            bad.append(f"product(s) {miss} neither promised nor noted")
    if getattr(res, "used", 0) > 45:
        bad.append("unbounded turns")
    cfg = dict(enums_from_tools(tools)) if tools else {}
    for f in critical(review_sim(res, cfg)):
        bad.append(f"review {f.rule}: {f.evidence[:100]}")
    return bad


def compare(exp: dict[str, Any], led: dict[str, Any]) -> list[str]:
    s = summarize(led)
    bad = [f"{k}: want {exp[k]!r} got {s[k]!r}" for k in ("outcome", "reason") if exp[k] != s[k]]
    for k in ("promises", "notes", "transfers"):
        if exp[k] is not None:
            e = exp[k] if k == "transfers" else sorted(exp[k])
            if e != s[k]:
                bad.append(f"{k}: want {e} got {s[k]}")
    return bad


def run_cell(m: Model, cell: Cell, engine: Engine | None = None, tools=None):
    case = make_case(m, cell.products, cid=cell.name, faults=cell.faults)
    res = sim(case, cell.replies, m.cfg, engine)
    bad = invariants(m, case, res, tools)
    if cell.exp is not None:
        bad += ["oracle " + b for b in compare(cell.exp, res.ledger)]
    if cell.used is not None and res.used != cell.used:  # type: ignore[attr-defined]
        bad.append(f"oracle replies used {res.used}, want {cell.used}")  # type: ignore
    return case, res, bad


# ------------------------------------------------------------------ callback window
def cb_cells(cfg: dict[str, Any], now: str):
    """(now, day text, time, want 'ok'/'outside', day): the spec's callback window, computed
    independently."""
    cb = cfg.get("callback") or {}
    start, end = str(cb.get("start") or "10:00"), str(cb.get("end") or "19:00")
    cal = Cal(cfg)
    now_dt = dt.datetime.fromisoformat(now)
    today = now_dt.date()
    ahead = cb.get("business_days_ahead")
    ahead = 1 if ahead in (None, "") else int(ahead)
    days = [today] if cb.get("include_today", True) is not False and cal.bd(today) else []
    days += [cal.add_bd(today, n) for n in range(1, ahead + 1)]

    def hm(t, d):
        x = dt.datetime.combine(today, dt.time.fromisoformat(t)) + dt.timedelta(minutes=d)
        return x.strftime("%H:%M")

    times = sorted({hm(start, -1), start, hm(start, 1), hm(end, -1), end, hm(end, 1)})
    for i in range(-1, 6):
        d = today + dt.timedelta(i)
        if d == today and not cal.bd(today):
            continue
        for t in times:
            ok = d in days and start <= t <= end
            ok = ok and dt.datetime.combine(d, dt.time.fromisoformat(t)) > now_dt
            for text in (d.isoformat(), spoken(d)):
                yield now, text, t, "ok" if ok else "outside", d


def run_cb(cfg, now, day_text, time_text, engine: Engine | None = None):
    eng, errs = engine or Engine(), []
    v: dict[str, Any] = {"now": now}
    eng.run(bodies.SETUP_TP, cfg, v, errs)
    v["cb_day_text"], v["cb_time_text"] = day_text, time_text
    eng.run(bodies.CB_CHECK, cfg, v, errs)
    return (v.get("cb_state"), v.get("cb_date"), v.get("cb_time")), errs


def cb_problems(cfg, now, text, t, want, d, engine=None) -> list[str]:
    got, errs = run_cb(cfg, now, text, t, engine)
    bad = [f"code_error {e[:80]}" for e in errs]
    if got[0] != want:
        bad.append(f"callback {now} {text} {t}: want {want} got {got[0]}")
    elif want == "ok" and (got[1], got[2]) != (d.isoformat(), t):
        bad.append(f"callback {now} {text} {t}: booked {got[1]} {got[2]}")
    return bad


# ------------------------------------------------------------------ random calls
KINDS = [
    "yes",
    "no",
    "offer",
    "too_much",
    "vague",
    "paid",
    "dispute",
    "hardship",
    "restructure",
    "human",
    "stop",
    "question",
    "evasive",
    "unclear",
]
WEIGHTS = [8, 8, 10, 3, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2]


def _rdate(rng: random.Random, today: dt.date) -> str:
    d = today + dt.timedelta(rng.randint(0, 40))
    return rng.choice(
        [
            d.isoformat(),
            spoken(d),
            WEEKDAYS[d.weekday()],
            "next " + WEEKDAYS[d.weekday()],
            "tomorrow",
            "today",
            f"the {d.day}th",
            rng.choice(["next week", "end of the month", "when my salary comes"]),
        ]
    )


def _ramount(rng: random.Random, full: float, floor: float) -> str:
    return rng.choice(
        [
            f"{full:.2f}",
            f"{floor:.2f}",
            str(int((full + floor) / 2)),
            str(int(floor * 0.6)),
            str(int(full * 1.5)),
            "three hundred",
            "four hundred and fifty",
            "200 or maybe 300",
            "about 250",
            "half",
        ]
    )


def random_call(rng: random.Random, m: Model):
    cfg = m.cfg
    types = list(cfg["floor_field"])
    n = rng.randint(2, 3) if m.notes and rng.random() < 0.7 else 1
    ps = []
    for _ in range(n):
        b = rng.choice(m.bands)
        dl = rng.choice(
            [
                b["min_days_late"],
                b["max_days_late"],
                rng.randint(b["min_days_late"], b["max_days_late"]),
            ]
        )
        full = round(rng.uniform(50, 3000), 2)
        ps.append(
            dict(
                type=rng.choice(types),
                past_due=full,
                days_late=dl,
                floor=round(full * rng.choice([0.2, 0.3, 0.5, 1.0]), 2),
            )
        )
    faults = []
    for tool, pr, hi in (
        ("create_promise", 0.15, 4),
        ("transfer_to_agent", 0.08, 3),
        ("add_note", 0.06, 2),
    ):
        if rng.random() < pr:
            faults.append({"tool": tool, "fail_first_n": rng.randint(1, hi)})
    replies = []
    for _ in range(rng.randint(1, 14)):
        kind = rng.choices(KINDS, WEIGHTS)[0]
        p = rng.choice(ps)
        r: dict[str, Any] = {"kind": kind, "text": TXT.get(kind, kind.capitalize() + ".")}
        if kind in ("offer", "vague") or (kind in ("yes", "no", "too_much") and rng.random() < 0.1):
            if rng.random() < 0.8:
                r["date"] = _rdate(rng, m.today)
            if rng.random() < 0.5:
                r["amount"] = _ramount(rng, p["past_due"], p["floor"])
            r["text"] = "I can pay " + " ".join([r.get("amount", ""), r.get("date", "")]) + "."
        if n > 1 and rng.random() < 0.5:
            r["product"] = f"p{rng.randint(1, n)}"
        if n > 1 and rng.random() < 0.15:
            r["all"] = True
        if rng.random() < 0.08:
            r["q"] = True
        replies.append(r)
    return ps, faults, replies + [BYE] * 40


def fuzz_one(rng: random.Random, m: Model, engine=None, tools=None) -> list[str]:
    ps, faults, replies = random_call(rng, m)
    case = make_case(m, ps, cid="FUZZ", faults=faults)
    res = sim(case, replies, m.cfg, engine)
    return invariants(m, case, res, tools)


# ------------------------------------------------------------------ entry point
def nows_for(cfg: dict[str, Any]) -> list[str]:
    """'Now' values that matter for this spec: a Wednesday, the last business day of a week,
    a weekend day, the day before a holiday. From the first holiday's year, else 2026."""
    hol = sorted(dt.date.fromisoformat(h) for h in cfg.get("holidays") or [])
    base = hol[0] - dt.timedelta(20) if hol else dt.date(2026, 10, 14)
    cal = Cal(cfg)
    wed = base + dt.timedelta((2 - base.weekday()) % 7)
    out = [wed, cal.last_bd_of_week(wed), wed + dt.timedelta(3)]  # Wed, last bd, Saturday
    if hol:
        out.append(hol[0] - dt.timedelta(1))
    return [d.isoformat() + "T11:00" for d in out]


def all_cells(cfg: dict[str, Any], nows: list[str], quick: bool = True):
    for now in nows:
        m = Model(cfg, now)
        for gen in (single_cells(m, quick), multi_cells(m), combo_cells(m, (2,))):
            for c in gen:
                yield m, c


def run_selftest(
    spec_or_cfg: dict[str, Any],
    tools=None,
    budget_s: float = 4.0,
    fuzz_runs: int = 200,
    seed: int = 7,
) -> dict[str, Any]:
    """Oracle matrix, callback window and seeded random calls for this spec, within budget_s
    seconds. Never raises. {"cells": n run, "failures": [str] (at most 50), "skipped": bool,
    "budget_hit": bool}."""
    t0 = time.monotonic()
    out: dict[str, Any] = {"cells": 0, "failures": [], "skipped": False, "budget_hit": False}
    try:
        cfg = gen_cfg(spec_or_cfg)
        nows = nows_for(cfg)
        eng = Engine()

        def left() -> bool:
            if time.monotonic() - t0 > budget_s:
                out["budget_hit"] = True
                return False
            return True

        def fail(msg: str) -> None:
            if len(out["failures"]) < 50:
                out["failures"].append(msg)

        for m, cell in all_cells(cfg, nows):
            if not left():
                break
            out["cells"] += 1
            _, _, bad = run_cell(m, cell, eng, tools)
            if bad:
                fail(f"{m.now[:10]} {cell.name}: " + "; ".join(bad[:2]))
        if cfg.get("callback") and left():
            for now in nows:
                for c in cb_cells(cfg, now):
                    if not left():
                        break
                    out["cells"] += 1
                    for b in cb_problems(cfg, *c, engine=eng):
                        fail(b)
        rng = random.Random(seed)
        for i in range(fuzz_runs):
            if not left():
                break
            m = Model(cfg, rng.choice(nows))
            out["cells"] += 1
            bad = fuzz_one(rng, m, eng, tools)
            if bad:
                fail(f"fuzz seed={seed} #{i} {m.now[:10]}: " + "; ".join(bad[:2]))
    except Exception as e:  # log only: the build goes on
        out["skipped"] = True
        out["failures"].append(f"selftest error: {type(e).__name__}: {str(e)[:200]}")
    out["seconds"] = round(time.monotonic() - t0, 2)
    return out
