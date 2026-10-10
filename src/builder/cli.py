"""Command-line entry point (memo §35: one-command runner)."""

from __future__ import annotations

import json
from pathlib import Path

import typer

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def info() -> None:
    """Show effective configuration (secrets redacted) and spend so far."""
    from builder.config import load_config
    from builder.llm.ledger import CostLedger

    cfg = load_config()
    ledger = CostLedger(cfg.ledger_path, cfg.max_cost_usd)
    data = cfg.model_dump(mode="json")
    for k in ("openrouter_api_key", "freya_api_key"):
        data[k] = "<set>" if data.get(k) else None
    data["spent_usd"] = round(ledger.total_usd, 4)
    typer.echo(json.dumps(data, indent=2))


@app.command()
def schema(out: Path = typer.Option(Path("docs/schemas"), help="Output directory")) -> None:
    """Export JSON Schemas of the typed IR."""
    from builder.schema.agent import AgentSpec
    from builder.schema.bundle import DomainSpec, InputBundle
    from builder.schema.failure import FailureReport
    from builder.schema.scenario import ScenarioSpec
    from builder.schema.trace import SimulationTrace

    out.mkdir(parents=True, exist_ok=True)
    for model in (InputBundle, DomainSpec, AgentSpec, ScenarioSpec, SimulationTrace, FailureReport):
        (out / f"{model.__name__}.json").write_text(
            json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False)
        )
    typer.echo(f"wrote schemas to {out}")


@app.command()
def run(
    input: Path = typer.Option(..., "--input", "-i", help="Bundle file or directory"),
    output: Path = typer.Option(Path("out"), "--output", "-o"),
    adapter: str = typer.Option("mock", help="mock | freya | none"),
    mode: str = typer.Option("full", help="full | fast"),
    scenarios: int = typer.Option(16, help="Synthetic scenarios to generate"),
    candidates: int = typer.Option(6, help="Initial structurally-different candidates"),
    generations: int = typer.Option(2, help="Optimizer generations"),
    max_simulations: int = typer.Option(200, help="Hard cap on simulated conversations"),
    max_wall_seconds: float = typer.Option(3600, help="Wall-clock cap for the search"),
    trials: int = typer.Option(2, help="Trials per validation scenario"),
    holdout_fraction: float = typer.Option(
        0.1, min=0, max=0.65, help="Fraction reserved for final holdout"
    ),
    seed: int = typer.Option(42),
    concurrency: int = typer.Option(4),
) -> None:
    """Build an agent for a task bundle (memo §35 one-command runner)."""
    from builder.adapters.factory import make_adapter_factory
    from builder.config import load_config
    from builder.ingest.bundle import load_bundle
    from builder.llm import LLMError, build_client
    from builder.pipeline import Mode, build
    from builder.schema.budget import OptimizationBudget

    cfg = load_config()
    bundle = load_bundle(input)
    try:
        llm = build_client(cfg)
    except LLMError as exc:
        typer.echo(f"[warn] {exc} — running in FAST mode (deterministic compile, no search)")
        llm = None
    budget = OptimizationBudget(
        initial_candidates=candidates,
        scenarios=scenarios,
        max_generations=generations,
        max_simulations=max_simulations,
        max_wall_seconds=max_wall_seconds,
        trials_final=trials,
        holdout_fraction=holdout_fraction,
        max_cost_usd=cfg.max_cost_usd,
    )
    factory = None if adapter == "none" else make_adapter_factory(adapter, bundle, llm, cfg)
    report = build(
        bundle,
        llm=llm,
        adapter_factory=factory,
        budget=budget,
        out_dir=output,
        mode=Mode(mode),
        seed=seed,
        lessons_path=cfg.data_dir / "lessons.jsonl",
        concurrency=concurrency,
    )
    typer.echo(
        f"champion={report.champion.candidate_id} fallback={report.fallback_used} "
        f"files={report.exported} out={output}"
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
