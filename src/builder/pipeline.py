"""End-to-end builder (memo §42, PLAN Faz 9): bundle → compiled, tested, selected agent.

    compile → candidates → scenarios → split → GEPA-lite on TRAIN
    → robustness selection on VALIDATION → final audit → export + manifest

Every stage degrades gracefully: on budget exhaustion or errors the always-valid
fallback (candidate A) is exported (memo §43).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from builder.adapters.base import PlatformAdapter
from builder.eval.evaluator import Evaluator
from builder.eval.metrics import aggregate
from builder.eval.simulator import Simulator
from builder.eval.stats import required_n
from builder.failure.lessons import LessonStore
from builder.ingest.compile import compile_domain
from builder.llm import LLMClient, LLMError, Role
from builder.manifest import Manifest, sha256_json, sha256_text
from builder.optimize.gepa_lite import GepaLite
from builder.optimize.overfit_lint import practice_literals
from builder.schema.agent import AgentSpec
from builder.schema.budget import OptimizationBudget
from builder.schema.bundle import DomainSpec, InputBundle
from builder.schema.eval import CandidateEvaluation
from builder.schema.scenario import ScenarioSpec, Split
from builder.select.final_audit import audit
from builder.select.robustness import Selection, select
from builder.synth.candidate_generator import generate_candidates
from builder.testsynth.scenario_generator import ScenarioGenerator, StateBackend
from builder.testsynth.split import split_scenarios

FALLBACK_ID = "A"
FINAL_WALL_SHARE = 0.35  # of max_wall_seconds kept for frozen validation + holdout


class Mode(StrEnum):
    FAST = "fast"  # compile only: audited fallback, no simulation (tiny budget, memo §21)
    FULL = "full"


@dataclass
class BuildReport:
    champion: AgentSpec
    domain: DomainSpec
    selection: Selection | None = None
    validation: dict[str, CandidateEvaluation] = field(default_factory=dict)
    holdout: dict[str, CandidateEvaluation] = field(default_factory=dict)
    holdout_scenarios: list[ScenarioSpec] = field(default_factory=list, repr=False)
    final_simulator: Simulator | None = field(default=None, repr=False)
    train: dict[str, CandidateEvaluation] = field(default_factory=dict)
    scenarios: dict[str, int] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    fallback_used: bool = False
    exported: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    pool: dict[str, AgentSpec] = field(default_factory=dict)
    practice_literals: set[str] = field(default_factory=set)


def _is_backend(obj: Any) -> bool:
    return hasattr(obj, "seed_state") and hasattr(obj, "replay")


def build(
    bundle: InputBundle,
    *,
    llm: LLMClient | None,
    adapter_factory: Any = None,
    budget: OptimizationBudget | None = None,
    out_dir: Path = Path("out"),
    mode: Mode = Mode.FULL,
    seed: int = 42,
    llm_compile: bool = True,
    lessons_path: Path | None = None,
    concurrency: int = 4,
) -> BuildReport:
    """`adapter_factory(domain) -> PlatformAdapter` (None → FAST mode)."""
    t0 = time.monotonic()
    cost0 = llm.ledger.total_usd if llm else 0.0  # the ledger is cumulative across runs
    budget = budget or OptimizationBudget()
    if llm is not None:
        llm.budget = budget
    log: list[str] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        domain = compile_domain(bundle, llm if llm_compile else None)
    except Exception as exc:
        log.append(f"LLM compile failed ({type(exc).__name__}: {exc}); using deterministic compile")
        domain = compile_domain(bundle, None)
    (out_dir / "domain_spec.json").write_text(domain.model_dump_json(indent=2))

    candidates = generate_candidates(domain, n=budget.initial_candidates)
    by_id = {c.candidate_id: c for c in candidates}
    fallback = by_id[FALLBACK_ID]
    report = BuildReport(champion=fallback, domain=domain, log=log)

    adapter: PlatformAdapter | None = None
    if mode is Mode.FULL and adapter_factory is not None and llm is not None:
        try:
            adapter = adapter_factory(domain)
        except Exception as exc:
            log.append(f"adapter setup failed: {type(exc).__name__}: {exc}")

    if adapter is not None and llm is not None:
        try:
            report = _search(
                domain,
                candidates,
                adapter,
                llm,
                budget,
                seed,
                out_dir,
                lessons_path,
                concurrency,
                report,
            )
        except Exception as exc:
            log.append(f"search aborted ({type(exc).__name__}: {exc}); exporting fallback")
            report.champion = fallback
            report.fallback_used = True
    else:
        log.append("no simulation available: FAST mode exports the audited fallback")

    # Final audit with fallback chain (memo §43).
    chain = [report.champion.candidate_id]
    if report.selection:
        chain += [c for c in report.selection.ranking if c not in chain]
    chain += [FALLBACK_ID] if FALLBACK_ID not in chain else []
    pool = {**by_id, **report.pool}
    report.pool = pool
    for cid in chain:
        spec = pool.get(cid)
        if spec is None:
            continue
        res = audit(spec, domain, adapter, report.practice_literals)
        if res.ok:
            if cid != report.champion.candidate_id:
                log.append(f"audit rejected {report.champion.candidate_id}; using {cid}")
                report.fallback_used = cid == FALLBACK_ID
            report.champion = spec
            break
        log.append(f"audit {cid}: {res.problems[:5]}")
    else:
        log.append("WARNING: no candidate passed audit; exporting fallback anyway")
        report.champion = fallback
        report.fallback_used = True

    if report.final_simulator is not None and report.holdout_scenarios:
        sim = report.final_simulator
        budget.release_simulations()
        for cid in dict.fromkeys([report.champion.candidate_id, FALLBACK_ID]):
            try:
                batch = sim.run(pool[cid], report.holdout_scenarios, trials=budget.trials_final)
                expected = len(report.holdout_scenarios) * budget.trials_final
                if len(batch.results) == expected and not batch.stopped_reason:
                    report.holdout[cid] = aggregate(cid, batch.results)
                else:
                    log.append(f"holdout of {cid} incomplete: {batch.stopped_reason}")
            except Exception as exc:
                log.append(f"holdout of {cid} failed: {exc}")

    report.elapsed_s = time.monotonic() - t0
    _export(report, adapter, llm, budget, out_dir, seed, bundle, cost0)
    return report


def _search(
    domain: DomainSpec,
    candidates: list[AgentSpec],
    adapter: PlatformAdapter,
    llm: LLMClient,
    budget: OptimizationBudget,
    seed: int,
    out_dir: Path,
    lessons_path: Path | None,
    concurrency: int,
    report: BuildReport,
) -> BuildReport:
    log = report.log
    backend: StateBackend | None = adapter if _is_backend(adapter) else None  # type: ignore[assignment]
    gen = ScenarioGenerator(domain, llm, backend=backend, seed=seed)
    scenarios = gen.generate(budget.scenarios)
    log += [f"scenario rejected {sid}: {why}" for sid, why in gen.rejected]
    if len(scenarios) < 4:
        raise LLMError(f"only {len(scenarios)} valid scenarios generated")
    parts = split_scenarios(scenarios, budget.validation_fraction, budget.holdout_fraction, seed)
    train, val = parts[Split.TRAIN], parts[Split.VALIDATION]
    holdout = parts[Split.HOLDOUT]
    report.scenarios = {sp.value: len(v) for sp, v in parts.items()}
    _dump(out_dir / "scenarios.jsonl", [s for part in parts.values() for s in part])

    evaluator = Evaluator(domain.tools, llm, use_judge=True)
    # GEPA returns at most three finalists; fallback is always included. Reserve
    # complete repeated validation and champion/fallback holdout before search.
    final_slots = min(4, len(candidates))
    hold_runs = len(holdout) * budget.trials_final * 2
    val_runs = len(val) * budget.trials_final
    if budget.max_simulations is not None and val_runs:
        available = budget.max_simulations - budget.used_simulations - hold_runs
        final_slots = min(final_slots, max(0, available // val_runs))
    if final_slots < 1:
        raise LLMError("insufficient budget for complete validation and holdout")
    budget.reserve_simulations(final_slots * val_runs + hold_runs)
    if budget.max_wall_seconds:
        # 9 Oct: the search used the whole wall budget and validation never ran.
        budget.reserve_wall(FINAL_WALL_SHARE * budget.max_wall_seconds)
    sim = Simulator(adapter, evaluator, budget, store_dir=out_dir / "runs", concurrency=concurrency)
    lessons = LessonStore(lessons_path) if lessons_path else LessonStore()
    opt = GepaLite(domain, sim, llm, lessons=lessons)
    res = opt.run(candidates, train, protected={FALLBACK_ID})
    log += res.log
    if res.stopped_reason:
        log.append(f"optimizer stopped: {res.stopped_reason}")
    report.train = res.train_evals

    # Frozen validation: finalists + fallback, repeated trials alternate user models (§59, §60).
    finalists = [FALLBACK_ID, *[c for c in res.finalists if c != FALLBACK_ID][: final_slots - 1]]
    budget.release_simulations()
    budget.begin_final_evaluation()
    budget.reserve_simulations(hold_runs)
    val_evals: dict[str, CandidateEvaluation] = {}
    for cid in finalists:
        spec = res.pool[cid]
        batch = sim.run(spec, val, trials=budget.trials_final)
        if len(batch.results) == val_runs and not batch.stopped_reason:
            val_evals[cid] = aggregate(cid, batch.results)
        elif batch.results:
            log.append(f"excluded incomplete validation of {cid}")
        if batch.stopped_reason:
            log.append(f"validation of {cid} stopped: {batch.stopped_reason}")
    report.validation = val_evals
    sizes = {cid: len(res.pool[cid].render_system_prompt()) for cid in val_evals}
    selection = select(val_evals, FALLBACK_ID, sizes)
    report.selection = selection
    report.fallback_used = selection.fallback_used or selection.champion == FALLBACK_ID
    report.champion = res.pool[selection.champion]
    report.pool = res.pool
    report.practice_literals = practice_literals(train + val, domain)
    log += selection.reasons
    report.holdout_scenarios = holdout
    report.final_simulator = sim
    return report


def _dump(path: Path, items: list[ScenarioSpec]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for s in items:
            f.write(s.model_dump_json() + "\n")


def _export(
    report: BuildReport,
    adapter: PlatformAdapter | None,
    llm: LLMClient | None,
    budget: OptimizationBudget,
    out_dir: Path,
    seed: int,
    bundle: InputBundle,
    cost0: float = 0.0,
) -> None:
    from builder.adapters.freya.compile import platform_system_prompt, submission_config

    spec = report.champion
    (out_dir / "agent_spec.json").write_text(spec.model_dump_json(indent=2))
    # The prompt that is shipped, not the local-runtime one (GAP_ANALYSIS A1).
    (out_dir / "system_prompt.md").write_text(platform_system_prompt(spec))
    report.exported = ["agent_spec.json", "system_prompt.md"]
    if adapter is not None and adapter.name == "freya":
        try:  # one path: platform default config + builder fields, validated
            p = adapter.export_submission(spec, out_dir)
            report.exported.append(str(p.relative_to(out_dir)))
        except Exception as exc:
            report.log.append(f"freya submission export failed: {exc}")
    else:
        overlay, notes = submission_config(
            spec, report.domain.tools, channel=report.domain.task.channel
        )
        (out_dir / "freya_config_overlay.json").write_text(
            json.dumps(overlay, indent=2, ensure_ascii=False)
        )
        report.exported.append("freya_config_overlay.json")
        report.log += [f"freya export: {n}" for n in notes]
        if adapter is not None:
            try:
                p = adapter.export_submission(spec, out_dir / "platform")
                report.exported.append(str(p.relative_to(out_dir)))
            except Exception as exc:
                report.log.append(f"platform export failed: {exc}")
    manifest = Manifest(
        seed=seed,
        adapter=adapter.name if adapter else "none",
        models={r.value: llm.model_for(r) for r in Role} if llm else {},
        input_hash=report.domain.input_hash,
        tool_schema_hash=sha256_json([t.inputs for t in report.domain.tools.tools]),
        document_hashes={d.doc_id: sha256_text(d.text) for d in bundle.documents},
        prompt_hashes={k: sha256_text(v) for k, v in spec.system_prompt_sections.items()},
        optimizer_config=budget.model_dump(mode="json", exclude={"notes"}),
        selected_candidate=spec.candidate_id,
        fallback_used=report.fallback_used,
        candidate_scores={
            cid: {
                "val_mean": e.mean,
                "val_lb": e.lower_bound,
                "fc": e.false_compliance_rate,
                "fr": e.false_refusal_rate,
            }
            for cid, e in report.validation.items()
        },
        holdout_scores={
            cid: {"mean": e.mean, "lower_bound": e.lower_bound, "n_runs": e.n_runs}
            for cid, e in report.holdout.items()
        },
        llm_cost_usd=round(llm.ledger.total_usd - cost0, 4) if llm else 0.0,
        ledger_total_usd=round(llm.ledger.total_usd, 4) if llm else 0.0,
        notes=report.log[-50:],
    )
    (out_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2))
    (out_dir / "report.md").write_text(render_report(report))
    _export_candidates(report, out_dir)


def _export_candidates(report: BuildReport, out_dir: Path) -> None:
    """Every evaluated spec + the selection record, so a later paired benchmark
    (scripts/benchmark.py) re-runs exactly the agents the search produced."""
    cdir = out_dir / "candidates"
    cdir.mkdir(exist_ok=True)
    pool = {**report.pool, report.champion.candidate_id: report.champion}
    for cid, spec in pool.items():
        (cdir / f"{cid}.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    record = {
        "champion": report.champion.candidate_id,
        "fallback_used": report.fallback_used,
        "ranking": report.selection.ranking if report.selection else [],
        "train_means": {cid: e.mean for cid, e in report.train.items()},
        "validation_means": {cid: e.mean for cid, e in report.validation.items()},
    }
    (out_dir / "selection.json").write_text(json.dumps(record, indent=2))


def render_report(r: BuildReport) -> str:
    lines = [
        f"# Build report — {r.domain.task.task_id or 'task'}",
        "",
        f"- Champion: **{r.champion.candidate_id}** ({r.champion.architecture.value})"
        + (" — fallback" if r.fallback_used else ""),
        f"- Elapsed: {r.elapsed_s:.1f}s",
        f"- Scenarios: {r.scenarios}",
        "",
        "## Validation (frozen)",
        "| candidate | mean | lower bound | pass^k | false compliance | false refusal | n |",
        "|---|---|---|---|---|---|---|",
    ]
    for cid, e in sorted(r.validation.items(), key=lambda kv: -kv[1].lower_bound):
        phk = ", ".join(f"{k}:{v:.2f}" for k, v in e.pass_hat_k.items())
        lines.append(
            f"| {cid} | {e.mean:.2f} | {e.lower_bound:.2f} | {phk} | "
            f"{e.false_compliance_rate:.2f} | {e.false_refusal_rate:.2f} | {e.n_runs} |"
        )
    n_val = max((e.n_scenarios for e in r.validation.values()), default=0)
    detectable = next(
        (d for d in (0.1, 0.15, 0.2, 0.3, 0.4, 0.5) if required_n(d, paired_rho=0.5) <= n_val),
        None,
    )
    lines += [
        "",
        f"Statistical power: {n_val} validation scenarios; smallest paired difference "
        "detectable at 80% power: "
        + (f"~{detectable:.2f}." if detectable else "none below 0.5 (differences are noise).")
        + " Treat smaller gaps as ties.",
    ]
    n_hold = max((e.n_scenarios for e in r.holdout.values()), default=0)
    lines += ["", "## Holdout (after selection)", "| candidate | mean | n |", "|---|---|---|"]
    for cid, e in r.holdout.items():
        lines.append(f"| {cid} | {e.mean:.2f} | {e.n_runs} |")
    if 0 < n_hold < 10:
        lines.append(
            f"\n_{n_hold} holdout scenarios: diagnostic only, not a generalization estimate._"
        )
    lines += ["", "## Train", "| candidate | mean | n |", "|---|---|---|"]
    for cid, e in sorted(r.train.items(), key=lambda kv: -kv[1].mean):
        lines.append(f"| {cid} | {e.mean:.2f} | {e.n_runs} |")
    lines += ["", "## Log", *[f"- {x}" for x in r.log]]
    return "\n".join(lines) + "\n"
