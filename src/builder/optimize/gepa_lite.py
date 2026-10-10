"""GEPA-lite optimizer (memo §3.9, §18, §42; PLAN Faz 8).

Loop: successive halving on TRAIN → for the best (Pareto) parents, diagnose
failures → mutate only the most-implicated prompt block with a reflective
operator → paired acceptance on the same TRAIN scenarios → regression tests
join TRAIN → repeat while budget allows. VALIDATION is never shown to the
optimizer; it is used only by the selector afterwards (memo §34.2).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from builder.eval.metrics import aggregate
from builder.eval.simulator import Simulator
from builder.failure.analyzer import analyze, component_pressure
from builder.failure.lessons import LessonStore, domain_tags
from builder.failure.regression import regression_suite
from builder.llm import BudgetExceeded, LLMClient, StructuredOutputError
from builder.optimize.acceptance import AcceptancePolicy, accept
from builder.optimize.crossover import crossover
from builder.optimize.fork_probe import prefilter, supports_probe
from builder.optimize.halving import rank_key, successive_halving
from builder.optimize.mutations import (
    OPERATOR_PRIOR,
    OPERATORS,
    OperatorStats,
    apply_block,
    reflect_block,
)
from builder.optimize.overfit_lint import lint_text, practice_literals
from builder.optimize.pareto import prune
from builder.schema.agent import AgentSpec
from builder.schema.bundle import DomainSpec
from builder.schema.eval import CandidateEvaluation, ScenarioResult
from builder.schema.failure import FailureReport
from builder.schema.scenario import ScenarioSpec
from builder.schema.trace import SimulationTrace


@dataclass
class OptimizationResult:
    pool: dict[str, AgentSpec]
    train_evals: dict[str, CandidateEvaluation]
    finalists: list[str]
    log: list[str] = field(default_factory=list)
    failures: list[FailureReport] = field(default_factory=list)
    stopped_reason: str | None = None


class GepaLite:
    def __init__(
        self,
        domain: DomainSpec,
        sim: Simulator,
        llm: LLMClient | None,
        *,
        lessons: LessonStore | None = None,
        use_llm_analyzer: bool = True,
        acceptance: AcceptancePolicy | None = None,
        max_failures_analyzed: int = 6,
        enable_crossover: bool = False,
        search_trials: int = 2,
    ) -> None:
        self.d = domain
        self.sim = sim
        self.llm = llm
        self.lessons = lessons or LessonStore()
        self.use_llm_analyzer = use_llm_analyzer
        self.acceptance = acceptance or AcceptancePolicy()
        self.max_failures = max_failures_analyzed
        self.enable_crossover = enable_crossover
        # Parent/child comparisons use >= 2 trials per scenario, alternating user simulators,
        # so acceptance is not tuned to one simulator's quirks (memo §59, GAP_ANALYSIS B8).
        self.search_trials = max(1, search_trials)
        self.ops = OperatorStats()
        self.runs: dict[str, tuple[SimulationTrace, ScenarioSpec]] = {}
        self.results: dict[str, list[ScenarioResult]] = {}
        self.log: list[str] = []

    # ------------------------------------------------------------------ plumbing

    def _run(
        self, spec: AgentSpec, scenarios: list[ScenarioSpec], first_trial: int = 0
    ) -> list[ScenarioResult]:
        by_id = {s.scenario_id: s for s in scenarios}
        batch = self.sim.run(spec, scenarios, trials=1, first_trial=first_trial)
        for tr in batch.traces:
            self.runs[tr.run_id] = (tr, by_id[tr.scenario_id])
        self.results.setdefault(spec.candidate_id, []).extend(batch.results)
        if batch.stopped_reason:
            self.log.append(f"sim stopped: {batch.stopped_reason}")
        return batch.results

    def _eval(self, cid: str) -> CandidateEvaluation:
        return aggregate(cid, self.results.get(cid, []))

    def _ensure(
        self, spec: AgentSpec, scenarios: list[ScenarioSpec], trials: int = 1
    ) -> CandidateEvaluation:
        """Top up every scenario to `trials` runs (trial k uses user simulator k % 2)."""
        for k in range(trials):
            done = Counter(r.scenario_id for r in self.results.get(spec.candidate_id, []))
            todo = [s for s in scenarios if done[s.scenario_id] <= k]
            if todo:
                self._run(spec, todo, first_trial=k)
        return self._eval(spec.candidate_id)

    def _diagnose(self, cid: str) -> list[FailureReport]:
        # Most recent failures first: older ones were already diagnosed in earlier generations.
        failed = [r for r in reversed(self.results.get(cid, [])) if not r.success]
        failed = failed[: self.max_failures]
        out: list[FailureReport] = []
        for r in failed:
            tr, sc = self.runs[r.run_id]
            llm = self.llm if self.use_llm_analyzer else None
            try:
                out.append(analyze(tr, sc, r, self.d.tools, llm))
            except StructuredOutputError:
                out.append(analyze(tr, sc, r, self.d.tools, None))
        return out

    # ------------------------------------------------------------------ mutation

    def _mutate(
        self, parent: AgentSpec, failures: list[FailureReport], literals: set[str], gen: int
    ) -> list[tuple[AgentSpec, str, list[str]]]:
        """Returns (child, operator, ids of lessons shown to the mutator)."""
        if self.llm is None or not failures:
            return []
        pressure = component_pressure(failures)
        tags = domain_tags(self.d)
        children: list[tuple[AgentSpec, str, list[str]]] = []
        for block, _ in pressure.most_common(self.sim.budget.children_per_parent):
            target = "tool_use_policy" if block == "tool_descriptions" else block
            related = [f for f in failures if block in f.implicated_components]
            for f in related:
                self.lessons.add_from_failure(f, tags)
            prior = OPERATOR_PRIOR.get(related[0].failure_type.value, list(OPERATORS))
            op = self.ops.rank(prior)[0]
            lessons = self.lessons.retrieve([*tags, f"type:{related[0].failure_type.value}"])
            try:
                rw = reflect_block(
                    self.llm,
                    parent,
                    target,
                    related,
                    lessons,
                    op,
                    salt=f"g{gen}:{parent.candidate_id}:{target}",
                )
            except StructuredOutputError as exc:
                self.log.append(f"mutation failed {parent.candidate_id}/{target}: {exc}")
                continue
            problems = lint_text(rw.new_text, literals, self.d)
            if problems:
                self.log.append(f"lint rejected {parent.candidate_id}/{target}: {problems[:3]}")
                self.ops.record(op, False)
                continue
            child = apply_block(parent, target, rw.new_text, f"g{gen}:{target}:{op}")
            children.append((child, op, [le.lesson_id for le in lessons]))
        return children

    # ------------------------------------------------------------------ main loop

    def run(
        self,
        candidates: list[AgentSpec],
        train: list[ScenarioSpec],
        protected: set[str] | None = None,
    ) -> OptimizationResult:
        budget = self.sim.budget
        pool = {c.candidate_id: c for c in candidates}
        literals = practice_literals(train, self.d)
        stopped: str | None = None
        all_failures: list[FailureReport] = []
        try:
            survivors, _, hlog = successive_halving(
                candidates,
                train,
                self._run,
                lambda cid, _r: self._eval(cid),
                first_rung=max(2, len(train) // 4),
                keep_min=2,
                protected=protected,
            )
            self.log += hlog
            parents = survivors[:2]
            for gen in range(budget.max_generations):
                if not budget.can_continue():
                    stopped = budget.exhausted_reason()
                    break
                new_parents: list[AgentSpec] = []
                for parent in parents:
                    p_eval = self._ensure(parent, train, self.search_trials)
                    failures = self._diagnose(parent.candidate_id)
                    all_failures += failures
                    for child, op, lesson_ids in self._mutate(parent, failures, literals, gen):
                        if not budget.can_simulate(len(train) * self.search_trials):
                            stopped = "simulation budget"
                            break
                        if supports_probe(self.sim.adapter):
                            cases = [
                                (*self.runs[f.run_id], f) for f in failures if f.run_id in self.runs
                            ]
                            keep, outcomes = prefilter(self.sim.adapter, child, cases)
                            if not keep:
                                self.ops.record(op, False)
                                self.log.append(
                                    f"g{gen} {child.candidate_id}: fork probe shows "
                                    f"the same mistake {outcomes}; skipped"
                                )
                                continue
                        c_eval = self._ensure(child, train, self.search_trials)
                        dec = accept(c_eval, p_eval, self.acceptance)
                        self.ops.record(op, dec.accepted)
                        for lid in lesson_ids:  # lessons that led nowhere lose confidence (§20)
                            self.lessons.feedback(lid, helped=dec.accepted)
                        self.log.append(
                            f"g{gen} {child.candidate_id} ({child.lineage_note}) "
                            f"{c_eval.mean:.2f} vs {p_eval.mean:.2f}: "
                            f"{'ACCEPT' if dec.accepted else 'reject'} — {dec.reason}"
                        )
                        if dec.accepted:
                            pool[child.candidate_id] = child
                            new_parents.append(child)
                    # Regression variants of this parent's failures join TRAIN (memo §28).
                    pairs = [(self.runs[f.run_id][1], f) for f in failures if f.run_id in self.runs]
                    train = train + [
                        s
                        for s in regression_suite(pairs, max_new=2)
                        if s.scenario_id not in {t.scenario_id for t in train}
                    ]
                if self.enable_crossover and len(parents) >= 2:
                    a, b = parents[0], parents[1]
                    fa = [f for f in all_failures if f.candidate_id == a.candidate_id]
                    fb = [f for f in all_failures if f.candidate_id == b.candidate_id]
                    x = crossover(a, b, component_pressure(fa), component_pressure(fb))
                    if x is not None and budget.can_simulate(len(train) * self.search_trials):
                        xe = self._ensure(x, train, self.search_trials)
                        a_eval = self._ensure(a, train, self.search_trials)
                        if accept(xe, a_eval, self.acceptance).accepted:
                            pool[x.candidate_id] = x
                            new_parents.append(x)
                gen_evals = [self._eval(cid) for cid in pool if self.results.get(cid)]
                front = prune(gen_evals, keep=2)
                parents = [pool[e.candidate_id] for e in front] if front else parents
                if not new_parents:
                    self.log.append(f"g{gen}: no accepted children")
        except BudgetExceeded as exc:
            stopped = f"llm budget: {exc}"
        final_evals = {cid: self._eval(cid) for cid in pool if self.results.get(cid)}
        finalists = sorted(final_evals, key=lambda c: rank_key(final_evals[c]))[:3]
        return OptimizationResult(
            pool=pool,
            train_evals=final_evals,
            finalists=finalists,
            log=self.log,
            failures=all_failures,
            stopped_reason=stopped,
        )
