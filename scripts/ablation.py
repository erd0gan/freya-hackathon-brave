"""Run the runtime ablation ladder on a fixed scenario file (memo §25–26).

    uv run python scripts/ablation.py --bundle examples/bundles/retail_bank.json \
        --scenarios out/scenarios.jsonl --trials 2 --out experiments/ablation_bank.md

Answers Q1 (does MutationGate help? B2 vs B3) with paired comparisons and
clustered SEs. Needs OPENROUTER_API_KEY; respects the global $ cap.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from builder.adapters.factory import make_adapter_factory
from builder.config import load_config
from builder.eval.evaluator import Evaluator
from builder.eval.metrics import aggregate, scenario_success
from builder.eval.simulator import Simulator
from builder.eval.stats import paired
from builder.experiments import LADDER_DOC, ladder
from builder.ingest.bundle import load_bundle
from builder.ingest.compile import compile_domain
from builder.llm import build_client
from builder.schema.budget import OptimizationBudget
from builder.schema.scenario import ScenarioSpec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--scenarios", type=Path, required=True)
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--max-sims", type=int, default=300)
    ap.add_argument("--out", type=Path, default=Path("experiments/ablation.md"))
    a = ap.parse_args()

    cfg = load_config()
    llm = build_client(cfg)
    bundle = load_bundle(a.bundle)
    domain = compile_domain(bundle, None)
    adapter = make_adapter_factory("mock", bundle, llm, cfg)(domain)  # type: ignore[misc]
    scen = [
        ScenarioSpec.model_validate_json(x)
        for x in a.scenarios.read_text().splitlines()
        if x.strip()
    ]
    sim = Simulator(
        adapter,
        Evaluator(domain.tools, llm),
        OptimizationBudget(max_simulations=a.max_sims),
        user_variants=2,
    )
    evals = {}
    for spec in ladder(domain):
        batch = sim.run(spec, scen, trials=a.trials)
        evals[spec.candidate_id] = aggregate(spec.candidate_id, batch.results)
        e = evals[spec.candidate_id]
        print(f"{spec.candidate_id}: {e.mean:.2f} ± {e.clustered_std_err:.2f}")
    lines = [
        "| step | variant | mean | clustered SE | pass^k | FC | FR |",
        "|---|---|---|---|---|---|---|",
    ]
    for cid, e in evals.items():
        phk = ", ".join(f"{k}:{v:.2f}" for k, v in e.pass_hat_k.items())
        lines.append(
            f"| {cid} | {LADDER_DOC[cid]} | {e.mean:.2f} | {e.clustered_std_err:.2f} | "
            f"{phk} | {e.false_compliance_rate:.2f} | {e.false_refusal_rate:.2f} |"
        )
    if "B2" in evals and "B3" in evals:
        c = paired(scenario_success(evals["B3"]), scenario_success(evals["B2"]))
        lines += [
            "",
            f"Q1 MutationGate (B3 vs B2): Δ={c.mean_diff:+.2f} ± {c.se_diff:.2f}, "
            f"wins {c.wins} / losses {c.losses}, sign-test p={c.sign_test_p:.3f}",
        ]
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text("\n".join(lines) + "\n")
    print(f"wrote {a.out}; spent ${llm.ledger.total_usd:.2f}")


if __name__ == "__main__":
    main()
