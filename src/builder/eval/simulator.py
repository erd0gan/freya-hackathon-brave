"""Parallel simulation runner with budget control and trace persistence (memo §19, §36)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from builder.adapters.base import AgentHandle, PlatformAdapter
from builder.eval.evaluator import Evaluator
from builder.llm import BudgetExceeded
from builder.schema.agent import AgentSpec
from builder.schema.budget import OptimizationBudget
from builder.schema.eval import ScenarioResult
from builder.schema.scenario import ScenarioSpec
from builder.schema.trace import EndReason, SimulationTrace
from builder.storage.traces import JsonlStore


@dataclass
class RunBatch:
    traces: list[SimulationTrace] = field(default_factory=list)
    results: list[ScenarioResult] = field(default_factory=list)
    skipped: int = 0
    stopped_reason: str | None = None


class Simulator:
    def __init__(
        self,
        adapter: PlatformAdapter,
        evaluator: Evaluator,
        budget: OptimizationBudget,
        store_dir: Path | None = None,
        concurrency: int = 4,
        user_variants: int = 2,
    ) -> None:
        self.adapter = adapter
        self.evaluator = evaluator
        self.budget = budget
        self.concurrency = max(1, min(concurrency, adapter.capabilities().max_concurrency))
        self.user_variants = user_variants
        self.traces = JsonlStore(store_dir / "traces.jsonl") if store_dir else None
        self.results = JsonlStore(store_dir / "results.jsonl") if store_dir else None
        self._handles: dict[str, AgentHandle] = {}

    def handle(self, spec: AgentSpec) -> AgentHandle:
        if spec.candidate_id not in self._handles:
            self._handles[spec.candidate_id] = self.adapter.deploy_agent(spec)
            self.budget.charge(agents=1)
        return self._handles[spec.candidate_id]

    def run(
        self,
        spec: AgentSpec,
        scenarios: list[ScenarioSpec],
        trials: int = 1,
        first_trial: int = 0,
    ) -> RunBatch:
        """Run every (scenario, trial). Trials alternate user-simulator models (memo §59);
        `first_trial` continues a scenario's trial sequence (trial 1 = the other simulator)."""
        h = self.handle(spec)
        jobs = [(s, t) for s in scenarios for t in range(first_trial, first_trial + trials)]
        batch = RunBatch()
        if not self.budget.can_simulate(len(jobs)):
            allowed = 0
            while allowed < len(jobs) and self.budget.can_simulate(allowed + 1):
                allowed += 1
            batch.skipped = len(jobs) - allowed
            jobs = jobs[:allowed]
            batch.stopped_reason = self.budget.exhausted_reason() or "simulations"
        self.budget.charge(sims=len(jobs))

        def one(job: tuple[ScenarioSpec, int]) -> tuple[SimulationTrace, ScenarioResult]:
            s, t = job
            tr = self.adapter.run_simulation(h, s, trial=t, user_variant=t % self.user_variants)
            return tr, self.evaluator.evaluate(tr, s)

        try:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                for tr, res in pool.map(one, jobs):
                    batch.traces.append(tr)
                    batch.results.append(res)
                    # Local LLM usage is already charged by LLMClient; external platform
                    # usage must be charged here when available in the returned trace.
                    if self.adapter.name not in {"mock", "tau2"}:
                        self.budget.charge(
                            cost=tr.cost_usd, tokens=tr.prompt_tokens + tr.completion_tokens
                        )
                    if self.traces:
                        self.traces.append(tr)
                    if self.results:
                        self.results.append(res)
        except BudgetExceeded as exc:
            batch.stopped_reason = f"llm budget: {exc}"
        return batch


def timed_out(trace: SimulationTrace) -> bool:
    return trace.end_reason in (EndReason.TIMEOUT, EndReason.MAX_TURNS)
