"""Round trips: a known amount, day or time said in many spoken forms is read back as itself.
The forms come from the value (covlib), not from hand-written labels."""

import datetime as dt
import random

import covlib as C
import pytest
from fb.nodecode.embed import namespace

NS = namespace()
BASE = (
    "0.5 1 9.99 10 19 20 99 100 101 110 126 199.99 200 250.5 300 350 420 450 637.5 999.99 1000 "
    "1001 1100 1320 1500 2125.1 2999.99 3000"
)


def _amounts(n=1200, seed=1):
    rng = random.Random(seed)
    vals = {float(x) for x in BASE.split()}
    while len(vals) < n:
        vals.add(
            round(rng.choice([rng.uniform(1, 3000), rng.randint(1, 3000)]), rng.choice([0, 1, 2]))
        )
    return sorted(vals)


def test_amounts_read_back_in_every_form():
    bad = []
    for v in _amounts():
        for form in C.amount_forms(v):
            # 'seven hundred ninety four' is 794, never 700.94: pence said as a bare number
            # after a round hundred is no form a customer uses for pence
            if int(v) % 100 == 0 and v != int(v) and form.endswith(C.words(round(v % 1 * 100))):
                continue
            if int(v) == 0 and "zero" in form:
                continue
            got = NS["parse_amount"](form)
            if got is None or abs(got - v) > 0.005:
                bad.append((v, form, got))
    assert not bad, bad[:10]


@pytest.mark.parametrize("today", [dt.date(2026, 10, 11), dt.date(2026, 12, 24)])
def test_days_read_back_in_every_form(today):
    bad = []
    for k in range(0, 100):
        d = today + dt.timedelta(k)
        for i, form in enumerate(C.date_forms(d)):
            if i == 8 and k > 27:
                continue  # 'Wednesday the 11th' points at the coming weeks only
            got, _ = NS["parse_spoken_date"](form, today)
            if got != d.isoformat():
                bad.append((form, d.isoformat(), got))
    assert not bad, bad[:10]


def test_times_read_back_in_every_form():
    bad = []
    for h in range(8, 20):
        for m in (0, 15, 20, 30, 45):
            for form in C.time_forms(h, m):
                got = NS["parse_spoken_time"](form)
                if got != f"{h:02d}:{m:02d}":
                    bad.append((form, h, m, got))
    assert not bad, bad[:10]


@pytest.mark.parametrize("v", [126.0, 350.0, 637.5, 1320.0, 2100.0])
def test_amount_forms_reach_the_ledger(v):
    """Said in an offer, every form becomes the recorded amount (PUB-09: 2100 past due, 700
    floor; amounts under the floor are raised to it)."""
    want = max(v, 700.0)
    for form in C.amount_forms(v):
        offer = {"kind": "offer", "amount": form, "date": "Friday"}
        _, _, res = C.run_public("PUB-09", [offer, C.Y, C.Y])
        got = [p[1] for p in C.promises(res)]
        assert got == [want], (form, got, res.says)
