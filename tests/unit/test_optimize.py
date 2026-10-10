from __future__ import annotations

from collections import Counter

from test_eval import TOOLS  # noqa: F401  (ensures domain compiles)

from builder.eval.metrics import aggregate
from builder.optimize.acceptance import accept
from builder.optimize.crossover import crossover
from builder.optimize.halving import successive_halving
from builder.optimize.mutations import OperatorStats, apply_block, toggle_fewshot
from builder.optimize.overfit_lint import lint_text, practice_literals
from builder.optimize.pareto import pareto_front
from builder.schema.agent import AgentSpec, Architecture
from builder.schema.eval import ScenarioResult
from builder.schema.scenario import Persona, ScenarioSpec
from builder.select.robustness import select


def _res(cid, outcomes):
    return [
        ScenarioResult(run_id=f"{cid}{i}", candidate_id=cid, scenario_id=f"s{i}", success=o)
        for i, o in enumerate(outcomes)
    ]


def _spec(cid, **sections):
    return AgentSpec(
        candidate_id=cid,
        architecture=Architecture.GUARDED_REACT,
        system_prompt_sections=sections or {"role": "r"},
    )


def test_paired_acceptance_and_simplicity_prior():
    parent = aggregate("P", _res("P", [1, 0, 0, 1, 0, 0, 0, 1]))
    better = aggregate("C", _res("C", [1, 1, 1, 1, 1, 1, 0, 1]))
    same = aggregate("S", _res("S", [1, 0, 0, 1, 0, 0, 0, 1]))
    assert accept(better, parent).accepted
    lucky = aggregate("L", _res("L", [1, 1, 1, 1, 0, 0, 0, 1]))  # +2 of 8 is noise-sized
    assert not accept(lucky, parent).accepted
    d = accept(same, parent)
    assert not d.accepted and "simpler parent" in d.reason
    risky = aggregate("R", _res("R", [1, 1, 1, 1, 1, 1, 0, 1]))
    risky.false_compliance_rate = 0.2
    assert not accept(risky, parent).accepted


def test_pareto_keeps_slice_specialists():
    a = aggregate("a", _res("a", [1, 1, 0]))
    b = aggregate("b", _res("b", [0, 0, 1]))
    c = aggregate("c", _res("c", [0, 0, 0]))
    assert {e.candidate_id for e in pareto_front([a, b, c])} == {"a", "b"}


def test_halving_spends_less_on_losers():
    cands = [_spec(c) for c in ("good", "bad1", "bad2", "fallback")]
    scen = [ScenarioSpec(scenario_id=f"s{i}", persona=Persona(prompt="x")) for i in range(12)]
    calls: Counter[str] = Counter()

    def run(spec, batch):
        calls[spec.candidate_id] += len(batch)
        return [
            ScenarioResult(
                run_id=f"{spec.candidate_id}{s.scenario_id}",
                candidate_id=spec.candidate_id,
                scenario_id=s.scenario_id,
                success=spec.candidate_id == "good",
            )
            for s in batch
        ]

    store: dict[str, list[ScenarioResult]] = {}

    def run_store(spec, batch):
        out = run(spec, batch)
        store.setdefault(spec.candidate_id, []).extend(out)
        return out

    alive, _evals, _log = successive_halving(
        cands,
        scen,
        run_store,
        lambda cid, _r: aggregate(cid, store[cid]),
        first_rung=3,
        keep_min=2,
        protected={"fallback"},
    )
    ids = [c.candidate_id for c in alive]
    assert ids[0] == "good" and "fallback" in ids
    assert calls["good"] > calls["bad1"] or calls["good"] > calls["bad2"]


def test_lint_rejects_practice_literals():
    from test_eval import SCEN

    from builder.adapters.mock.domains.retail_bank import make_pack
    from builder.ingest.compile import compile_domain

    d = compile_domain(make_pack().bundle)
    facts = {"id": "C-7777", "example": "C-1001"}  # C-1001 is the tool docs' own example
    s = SCEN.model_copy(update={"persona": Persona(prompt="x", hidden_facts=facts)})
    lits = practice_literals([s], d)
    assert "c-7777" in lits and "c-1001" not in lits
    assert lint_text("Verify customer C-7777 first", lits, d)
    assert not lint_text("customer_id=string, e.g. C-1001", lits, d)
    d.tools.tools[0].arg_format_rules = {"pin": 'exactly 4 digits, e.g. "0427"'}
    assert not lint_text('pin: exactly 4 digits, e.g. "0427"', lits, d)
    assert not lint_text("Verify the customer first (AUTH-01).", lits, d)


def test_mutation_helpers_and_crossover():
    p = _spec("C1", role="r", mutation_guard="old")
    c = apply_block(p, "mutation_guard", "new", "g0:mg")
    assert c.parent_ids == ["C1"] and c.system_prompt_sections["mutation_guard"] == "new"
    assert c.candidate_id != p.candidate_id
    f = toggle_fewshot(p, "policy_refusal")
    assert [e.family for e in f.few_shot_examples] == ["policy_refusal"]
    b = _spec("C2", role="r2", mutation_guard="better")
    x = crossover(p, b, Counter({"mutation_guard": 3}), Counter({"mutation_guard": 1}))
    assert x is not None and x.system_prompt_sections["mutation_guard"] == "better"
    assert x.system_prompt_sections["role"] == "r"
    stats = OperatorStats()
    stats.record("clarify", False)
    stats.record("add_invariant", True)
    assert stats.rank(["clarify", "add_invariant"])[0] == "add_invariant"


def test_selection_falls_back_when_champion_loses():
    a = aggregate("A", _res("A", [1, 1, 1, 0, 0, 0]))
    c = aggregate("C", _res("C", [0, 0, 0, 1, 1, 1]))
    c.lower_bound = 0.9  # pretend C ranks first
    sel = select({"A": a, "C": c}, "A")
    assert sel.champion == "C" or sel.fallback_used
    tie = select(
        {"A": aggregate("A", _res("A", [1, 1])), "B": aggregate("B", _res("B", [1, 1]))},
        "A",
        sizes={"A": 100, "B": 50},
    )
    assert tie.champion == "B"


def test_halving_does_not_break_ties_with_the_leader_by_list_order():
    cands = [_spec(c) for c in ("B0", "A", "C5", "C1")]
    scen = [ScenarioSpec(scenario_id=f"s{i}", persona=Persona(prompt="x")) for i in range(6)]
    store: dict[str, list[ScenarioResult]] = {}

    def run(spec, batch):
        # all tie on the first rung; C5 wins the rest
        out = [
            ScenarioResult(
                run_id=f"{spec.candidate_id}{s.scenario_id}",
                candidate_id=spec.candidate_id,
                scenario_id=s.scenario_id,
                success=int(s.scenario_id[1:]) < 2 or spec.candidate_id == "C5",
            )
            for s in batch
        ]
        store.setdefault(spec.candidate_id, []).extend(out)
        return out

    alive, _e, log = successive_halving(
        cands, scen, run, lambda cid, _r: aggregate(cid, store[cid]), first_rung=2, keep_min=2
    )
    assert alive[0].candidate_id == "C5", log


def test_null_mutation_is_rarely_accepted():
    """Optimizer invariant (GAP_ANALYSIS A8): a child that behaves exactly like its parent
    must almost never be accepted. The old rule accepted it 38-43% of the time."""
    import random

    from builder.eval.metrics import aggregate as agg

    rng = random.Random(7)

    def rate(n: int, trials: int, delta: float = 0.0, reps: int = 1500) -> float:
        hits = 0
        for _ in range(reps):
            ps = [rng.betavariate(1.2, 0.8) for _ in range(n)]

            def results(cid: str, d: float, ps: list[float] = ps) -> list[ScenarioResult]:
                return [
                    ScenarioResult(
                        run_id=f"{cid}{i}.{t}",
                        candidate_id=cid,
                        scenario_id=f"s{i}",
                        success=rng.random() < min(1.0, p + d),
                    )
                    for i, p in enumerate(ps)
                    for t in range(trials)
                ]

            hits += accept(agg("C", results("C", delta)), agg("P", results("P", 0.0))).accepted
        return hits / reps

    for n in (9, 20):
        assert rate(n, 2) < 0.07, n
    assert rate(20, 2, delta=0.3) > 0.6  # a real +0.3 improvement is still found
