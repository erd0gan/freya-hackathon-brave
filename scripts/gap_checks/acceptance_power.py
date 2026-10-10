"""Gap-analysis check, see docs/GAP_ANALYSIS.md. Run: uv run python scripts/gap_checks/acceptance_power.py"""
# ruff: noqa: E501, B905, E402

import random

from builder.eval.stats import beta_lower_bound, required_n
from builder.optimize.acceptance import AcceptancePolicy

pol = AcceptancePolicy()


def accepted(wins, losses, diff):
    n_net = wins - losses
    from builder.eval.stats import sign_test_p

    return n_net >= pol.min_net_wins and diff > 0 and sign_test_p(wins, losses) <= pol.max_sign_p


rng = random.Random(1)


def run(n_train, trials, trials_ok=True, reps=20000):
    acc = 0
    for _ in range(reps):
        # latent per-scenario pass probability (heterogeneous difficulty); parent == child (null mutation)
        ps = [rng.betavariate(1.2, 0.8) for _ in range(n_train)]
        a = [sum(rng.random() < p for _ in range(trials)) / trials for p in ps]  # child
        b = [sum(rng.random() < p for _ in range(trials)) / trials for p in ps]  # parent
        wins = sum(x > y for x, y in zip(a, b))
        losses = sum(x < y for x, y in zip(a, b))
        diff = sum(x - y for x, y in zip(a, b)) / n_train
        acc += accepted(wins, losses, diff)
    return acc / reps


for n in (9, 12, 20, 30):
    print(f"train n={n:2d}, 1 trial/scenario: P(accept a NULL mutation) = {run(n, 1):.2f}")
print()
print("scenarios needed (unpaired normal approx) to detect delta at p=0.7:")
for d in (0.05, 0.10, 0.15, 0.20):
    print(
        f"  delta={d:.2f}: unpaired n={required_n(d)}, paired rho=0.5 n={required_n(d, paired_rho=0.5)}"
    )
print()
# validation: 16 scenarios default -> 5 val scenarios x 2 trials
for k in (5, 10, 20):
    print(
        f"val scenarios={k}: Beta 5% lower bound at 100% pass = {beta_lower_bound(k, k):.2f}; at 80% = {beta_lower_bound(0.8 * k, k):.2f}"
    )
# default split for --scenarios 16 and 24
from builder.schema.scenario import Persona, ScenarioCategory, ScenarioSpec
from builder.testsynth.split import split_scenarios

for n in (16, 24, 30):
    sc = [
        ScenarioSpec(
            scenario_id=f"s{i}", persona=Persona(prompt="x"), category=list(ScenarioCategory)[i % 8]
        )
        for i in range(n)
    ]
    parts = split_scenarios(sc, 0.34, 0.1, 42)
    print(f"--scenarios {n}:", {k.value: len(v) for k, v in parts.items()})
