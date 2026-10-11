"""Coverage matrix over four specs (hand-written A and B, the extracted A+B and Northwind
fixtures): synthetic customers at band edges, every product type, dates around the deadline
and the non-business days, amounts around the floor and the full amount, tool faults, the
semantic exits, two and three products, the callback window and seeded random calls. The
expected ledger is worked out from the spec (fb/harness/selftest.py), never hand-labelled."""

import random

import covlib as C
import pytest
from fb.harness import selftest as S

SPECS = sorted(C.SPECS)


def _fail_report(bad):
    return "\n".join(bad[:12])


@pytest.mark.parametrize("name", SPECS)
def test_matrix(name):
    cfg = S.gen_cfg(C.SPECS[name])
    bad, n = [], 0
    for m, cell in S.all_cells(cfg, C.NOWS):
        n += 1
        _, _, problems = S.run_cell(m, cell)
        bad += [f"{m.now[:10]} {cell.name}: {p}" for p in problems]
    assert n > 250
    assert not bad, _fail_report(bad)


@pytest.mark.parametrize("name", ["B", "ABfix", "NW"])
def test_three_product_combos(name):
    cfg = S.gen_cfg(C.SPECS[name])
    m = S.Model(cfg, C.NOWS[0])
    bad, n = [], 0
    for cell in S.combo_cells(m, (3,)):
        n += 1
        _, _, problems = S.run_cell(m, cell)
        bad += [f"{cell.name}: {p}" for p in problems]
    assert n > 60
    assert not bad, _fail_report(bad)


@pytest.mark.parametrize("name", ["A", "NW"])
def test_callback_window(name):
    cfg = S.gen_cfg(C.SPECS[name])
    nows = [*C.NOWS, "2026-10-14T09:00", "2026-10-14T18:59", "2026-10-14T19:30"]
    bad, n = [], 0
    for now in nows:
        for c in S.cb_cells(cfg, now):
            n += 1
            bad += S.cb_problems(cfg, *c)
    assert n > 300
    assert not bad, _fail_report(bad)


@pytest.mark.parametrize("name", SPECS)
def test_random_calls_with_tool_faults(name):
    cfg = S.gen_cfg(C.SPECS[name])
    rng = random.Random(20261011)
    bad = []
    for i in range(300):
        m = S.Model(cfg, rng.choice(C.NOWS))
        bad += [f"#{i} {m.now[:10]}: {p}" for p in S.fuzz_one(rng, m)]
    assert not bad, _fail_report(bad)


@pytest.mark.parametrize("name", ["B", "NW"])
def test_cells_through_the_generated_snippet(name):
    """The same cells through the real code-node source (library subset, packed when big)."""
    cfg = S.gen_cfg(C.SPECS[name])
    real = S.Engine(real=True)
    cells = [x for x in S.all_cells(cfg, C.NOWS[:1]) if len(x[1].replies) <= 8]
    pick = cells[:: max(1, len(cells) // 10)][:10]
    bad = []
    for m, cell in pick:
        _, _, problems = S.run_cell(m, cell, real)
        bad += [f"{cell.name}: {p}" for p in problems]
    assert len(pick) == 10
    assert not bad, _fail_report(bad)


def test_selftest_entry_point():
    out = S.run_selftest(C.SPECS["NW"], budget_s=1.5, fuzz_runs=30)
    assert not out["skipped"] and out["cells"] > 100, out
    assert out["failures"] == [], out["failures"]
    out = S.run_selftest(S.gen_cfg(C.SPECS["B"]), budget_s=0.2)
    assert out["budget_hit"] and not out["skipped"] and out["seconds"] < 1.0, out


def test_selftest_never_raises():
    out = S.run_selftest({"bands": "nonsense"}, budget_s=0.5)
    assert out["skipped"] and out["failures"], out
