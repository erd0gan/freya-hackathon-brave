"""Paired B0 / A / C benchmark on frozen scenarios (GAP_ANALYSIS §10, PLAN §9 open item 1).

    uv run python scripts/benchmark.py --bundle examples/bundles/retail_bank.json \
        --run-dir out/bank6 --fresh 12 --trials 2 --out experiments/bench_bank.md

Takes the agents a finished `builder run` produced (run-dir/candidates/*.json) and
re-runs three of them on scenarios the optimizer never trained on:

* B0: platform-default baseline (task + raw rules, no safeguards)
* A:  deterministic compiled fallback
* C:  the optimizer's best non-baseline candidate (champion, else best on validation,
      else best on train)

Sets: FRESH (newly generated with another seed — primary, unbiased) and FROZEN
(the run's validation + holdout — C was *selected* on validation, so it is mildly
biased in C's favour). Same scenarios, same trials, same user-simulator alternation,
same evaluator for all three. LOCAL/MOCK runtime only: not a Freya-native score.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from builder.adapters.factory import make_adapter_factory
from builder.config import load_config
from builder.eval.evaluator import Evaluator
from builder.eval.metrics import aggregate, scenario_success
from builder.eval.simulator import Simulator
from builder.eval.stats import PairedComparison, paired
from builder.ingest.bundle import load_bundle
from builder.llm import build_client
from builder.llm.roles import Role
from builder.manifest import sha256_text
from builder.schema.agent import AgentSpec
from builder.schema.budget import OptimizationBudget
from builder.schema.bundle import DomainSpec
from builder.schema.eval import CandidateEvaluation
from builder.schema.scenario import ScenarioSpec, Split
from builder.schema.trace import SimulationTrace
from builder.testsynth.scenario_generator import ScenarioGenerator

BASELINES = ("B0", "A")
MIN_DIFF = 0.05
MAX_P = 0.1


def pick_c(run_dir: Path, ids: set[str]) -> tuple[str, str]:
    sel = json.loads((run_dir / "selection.json").read_text())
    if sel["champion"] not in BASELINES:
        return sel["champion"], "champion"
    order = (("validation_means", "best on validation"), ("train_means", "best on train"))
    for key, label in order:
        scores = {c: v for c, v in sel[key].items() if c not in BASELINES and c in ids}
        if scores:
            return max(scores, key=lambda c: scores[c]), f"{label} (champion was {sel['champion']})"
    raise SystemExit("no non-baseline candidate in the run")


def git_sha() -> str:
    sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout
    dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout
    return sha.strip()[:12] + ("+dirty" if dirty.strip() else "")


def scen_hash(scen: list[ScenarioSpec]) -> str:
    return sha256_text("\n".join(sorted(s.model_dump_json() for s in scen)))[:16]


def verdict(c: PairedComparison) -> str:
    if c.mean_diff >= MIN_DIFF and c.sign_test_p <= MAX_P:
        return ">"
    if c.mean_diff <= -MIN_DIFF and c.losses >= c.wins and _p(c.losses, c.wins) <= MAX_P:
        return "<"
    return "≈"


def _p(wins: int, losses: int) -> float:
    from builder.eval.stats import sign_test_p

    return sign_test_p(wins, losses)


def story(a_b0: str, c_a: str, c_b0: str) -> str:
    if c_a == "<":
        return "4. C < A: optimizer still hurts — go back to acceptance/search, add no features."
    if c_a == ">" and a_b0 == ">":
        return "2. C > A > B0: both compile and optimize add uplift."
    if c_a == ">":
        return "3. C > A, A ≈ B0: the optimizer is where the value is."
    if a_b0 == ">":
        return "1. A > B0, C ≈ A: the compiler helps; the optimizer adds nothing measurable yet."
    if c_b0 == ">":
        return "C > B0 but neither step is separately significant: inconclusive split."
    return "5. All ≈: candidate/search complexity is not paying for itself at this n."


def table(evals: dict[str, CandidateEvaluation]) -> list[str]:
    b0 = scenario_success(evals["B0"]) if "B0" in evals else {}
    rows = [
        "| candidate | success | clustered SE | pass^k | FC | FR | n scen / runs | Δ vs B0 |",
        "|---|---:|---:|---|---:|---:|---|---:|",
    ]
    for cid, e in evals.items():
        phk = ", ".join(f"{k}:{v:.2f}" for k, v in e.pass_hat_k.items())
        d = "—" if cid.startswith("B0") else f"{paired(scenario_success(e), b0).mean_diff:+.2f}"
        rows.append(
            f"| {cid} | {e.mean:.2f} | {e.clustered_std_err:.2f} | {phk} | "
            f"{e.false_compliance_rate:.2f} | {e.false_refusal_rate:.2f} | "
            f"{e.n_scenarios} / {e.n_runs} | {d} |"
        )
    return rows


def comparisons(evals: dict[str, CandidateEvaluation], c_id: str) -> tuple[list[str], str]:
    s = {k: scenario_success(v) for k, v in evals.items()}
    pairs = [("A", "B0"), (c_id, "A"), (c_id, "B0")]
    rows = [
        "| comparison | Δ | SE | 95% CI | wins / losses / ties | sign p (1-sided) | verdict |",
        "|---|---:|---:|---|---|---:|:-:|",
    ]
    v: dict[tuple[str, str], str] = {}
    for x, y in pairs:
        c = paired(s[x], s[y])
        v[(x, y)] = verdict(c)
        lo, hi = c.ci95
        rows.append(
            f"| {x} vs {y} | {c.mean_diff:+.2f} | {c.se_diff:.2f} | [{lo:+.2f}, {hi:+.2f}] | "
            f"{c.wins} / {c.losses} / {c.ties} | {c.sign_test_p:.3f} | {v[(x, y)]} |"
        )
    return rows, story(v[("A", "B0")], v[(c_id, "A")], v[(c_id, "B0")])


def _load(path: Path) -> list[ScenarioSpec]:
    lines = path.read_text().splitlines()
    return [ScenarioSpec.model_validate_json(x) for x in lines if x.strip()]


def _rescore(
    exp_dir: Path,
    evaluator: Evaluator,
    sets: dict[str, list[ScenarioSpec]],
    specs: dict[str, AgentSpec],
    trials: int,
) -> tuple[dict[str, dict[str, CandidateEvaluation]], list[str]]:
    """Same traces, current evaluator: label fixes apply without new simulations."""
    traces = [
        SimulationTrace.model_validate_json(x)
        for x in (exp_dir / "runs" / "traces.jsonl").read_text().splitlines()
        if x.strip()
    ]
    results: dict[str, dict[str, CandidateEvaluation]] = {}
    incomplete: list[str] = []
    for name, scen in sets.items():
        if not scen:
            continue
        by_id = {s.scenario_id: s for s in scen}
        results[name] = {}
        for cid in specs:
            rs = [
                evaluator.evaluate(t, by_id[t.scenario_id])
                for t in traces
                if t.candidate_id == cid and t.scenario_id in by_id
            ]
            if len(rs) != len(scen) * trials:
                incomplete.append(f"{name}/{cid}: {len(rs)} stored runs")
            results[name][cid] = aggregate(cid, rs)
    return results, incomplete


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bundle", type=Path, required=True)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--fresh", type=int, default=12, help="new scenarios to generate (0 = none)")
    ap.add_argument(
        "--fresh-file", type=Path, help="reuse a fresh_scenarios.jsonl instead of generating"
    )
    ap.add_argument("--trials", type=int, default=2)
    ap.add_argument("--seed", type=int, default=4242)
    ap.add_argument("--max-cost", type=float, default=8.0, help="$ cap for this benchmark")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", type=Path, default=Path("experiments/benchmark.md"))
    ap.add_argument(
        "--rescore",
        action="store_true",
        help="re-evaluate the stored traces with the current evaluator (no new simulations; "
        "judge calls hit the LLM cache)",
    )
    a = ap.parse_args()

    cfg = load_config()
    llm = build_client(cfg)
    budget = OptimizationBudget(max_cost_usd=a.max_cost)
    llm.budget = budget
    cost0 = llm.ledger.total_usd  # the ledger is cumulative across runs
    bundle = load_bundle(a.bundle)
    domain = DomainSpec.model_validate_json((a.run_dir / "domain_spec.json").read_text())
    adapter = make_adapter_factory("mock", bundle, llm, cfg)(domain)  # type: ignore[misc]

    cands = {
        p.stem: AgentSpec.model_validate_json(p.read_text())
        for p in sorted((a.run_dir / "candidates").glob("*.json"))
    }
    c_id, c_why = pick_c(a.run_dir, set(cands))
    specs = {cid: cands[cid] for cid in ("B0", "A", c_id)}

    frozen = [s for s in _load(a.run_dir / "scenarios.jsonl") if s.split is not Split.TRAIN]
    exp_dir = a.out.with_suffix("")
    exp_dir.mkdir(parents=True, exist_ok=True)
    fresh: list[ScenarioSpec] = []
    gen_log: list[str] = []
    meta_path = exp_dir / "meta.json"
    if a.rescore:
        fresh = _load(exp_dir / "fresh_scenarios.jsonl")
        meta = json.loads(meta_path.read_text())
        gen_log = meta["generation_log"]
    elif a.fresh_file:
        fresh = _load(a.fresh_file)
        (exp_dir / "fresh_scenarios.jsonl").write_text(a.fresh_file.read_text())
    elif a.fresh:
        gen = ScenarioGenerator(domain, llm, backend=adapter, seed=a.seed)  # type: ignore[arg-type]
        for s in gen.generate(a.fresh):
            fresh.append(s.model_copy(update={"scenario_id": f"fresh-{s.scenario_id}"}))
        gen_log = [f"rejected {sid}: {why}" for sid, why in gen.rejected]
        (exp_dir / "fresh_scenarios.jsonl").write_text(
            "".join(s.model_dump_json() + "\n" for s in fresh)
        )
    print(f"C={c_id} ({c_why}); fresh={len(fresh)} frozen={len(frozen)}")

    evaluator = Evaluator(domain.tools, llm, use_judge=True)
    sets = {"FRESH (primary)": fresh, "FROZEN val+holdout (C selected here)": frozen}
    results: dict[str, dict[str, CandidateEvaluation]] = {}
    incomplete: list[str] = []
    if a.rescore:
        results, incomplete = _rescore(exp_dir, evaluator, sets, specs, a.trials)
        sim_cost = float(meta["simulation_cost_usd"])
    sim = Simulator(
        adapter, evaluator, budget, store_dir=exp_dir / "runs", concurrency=a.concurrency
    )
    for name, scen in sets.items() if not a.rescore else []:
        if not scen:
            continue
        results[name] = {}
        for cid, spec in specs.items():  # sequential: one candidate at a time
            batch = sim.run(spec, scen, trials=a.trials)
            if len(batch.results) != len(scen) * a.trials or batch.stopped_reason:
                n = len(batch.results)
                incomplete.append(f"{name}/{cid}: {n} runs, {batch.stopped_reason}")
            results[name][cid] = aggregate(cid, batch.results)
            e = results[name][cid]
            cost = llm.ledger.total_usd - cost0
            print(f"{name} {cid}: {e.mean:.2f} ± {e.clustered_std_err:.2f}  ${cost:.2f}")
    if not a.rescore:
        sim_cost = llm.ledger.total_usd - cost0
        meta_path.write_text(
            json.dumps({"simulation_cost_usd": sim_cost, "generation_log": gen_log}, indent=2)
        )

    models = {r.value: llm.model_for(r) for r in Role}
    lines = [
        f"# B0 / A / C benchmark — {domain.task.task_id or a.bundle.stem}",
        "",
        "> **LOCAL / MOCK RUNTIME.** Scores come from this repo's simulator and mock backend,",
        "> with the builder's local safety gate. They are a pre-screen, **not** Freya-native",
        "> scores: the shipped agent runs on Freya's runtime (PLAN §9, GAP_ANALYSIS A1).",
        "",
        f"- Run dir: `{a.run_dir}` · bundle `{a.bundle}`",
        f"- Commit: `{git_sha()}`",
        f"- C = **{c_id}** ({c_why})",
        f"- Trials per scenario: {a.trials} (trial 0 = `{models['user_sim']}`, "
        f"trial 1 = `{models['user_sim_alt']}`)",
        f"- Agent `{models['agent']}` · judge `{models['judge']}` · builder `{models['builder']}`",
        f"- Scenario hashes: fresh `{scen_hash(fresh) if fresh else '-'}` "
        f"(seed {a.seed}), frozen `{scen_hash(frozen)}`",
        f"- Benchmark cost: ${sim_cost:.2f}"
        + (
            f" (rescored from stored traces, +${llm.ledger.total_usd - cost0:.2f})"
            if a.rescore
            else ""
        ),
        "",
        "Verdicts: `>` needs Δ ≥ 0.05 and one-sided sign-test p ≤ 0.1 over per-scenario means;",
        "SE is over scenarios (clustered). Train scenarios are excluded everywhere.",
    ]
    for name, evals in results.items():
        comp, st = comparisons(evals, c_id)
        lines += ["", f"## {name}", "", *table(evals), "", *comp, "", f"**Reading:** {st}"]
    if incomplete:
        lines += ["", "## Incomplete", *[f"- {x}" for x in incomplete]]
    if gen_log:
        lines += ["", "## Fresh scenario generation", *[f"- {x}" for x in gen_log]]
    a.out.write_text("\n".join(lines) + "\n")
    (exp_dir / "summary.json").write_text(
        json.dumps(
            {
                name: {cid: e.model_dump(exclude={"results"}) for cid, e in ev.items()}
                for name, ev in results.items()
            },
            indent=2,
        )
    )
    print(f"wrote {a.out}; spent ${llm.ledger.total_usd - cost0:.2f}")


if __name__ == "__main__":
    main()
