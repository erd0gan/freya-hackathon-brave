"""Generic optimization budget (memo §19). Every loop must consult it."""

from __future__ import annotations

import time

from pydantic import BaseModel, Field, PrivateAttr


class OptimizationBudget(BaseModel):
    max_wall_seconds: float | None = None
    max_builder_llm_calls: int | None = None
    max_simulations: int | None = None
    max_generated_agents: int | None = None
    max_total_tokens: int | None = None
    max_cost_usd: float | None = None

    initial_candidates: int = 6
    scenarios: int = 24
    validation_fraction: float = 0.34
    holdout_fraction: float = Field(default=0.1, ge=0, lt=1)
    trials_final: int = Field(default=3, ge=1)
    max_generations: int = 3
    children_per_parent: int = 2

    used_llm_calls: int = 0
    used_simulations: int = 0
    used_agents: int = 0
    used_tokens: int = 0
    used_cost_usd: float = 0.0
    _start: float = PrivateAttr(default_factory=time.monotonic)
    notes: list[str] = Field(default_factory=list)
    _reserved_simulations: int = PrivateAttr(default=0)
    _reserved_wall: float = PrivateAttr(default=0.0)
    _final_evaluation: bool = PrivateAttr(default=False)

    def begin_final_evaluation(self) -> None:
        self._final_evaluation = True

    def reserve_simulations(self, n: int) -> None:
        if self.max_simulations is not None and self.used_simulations + n > self.max_simulations:
            raise ValueError("insufficient budget for complete final evaluation")
        self._reserved_simulations = n

    def release_simulations(self) -> None:
        self._reserved_simulations = 0

    def reserve_wall(self, seconds: float) -> None:
        """Keep wall-clock time for final evaluation; search stops this much earlier."""
        self._reserved_wall = max(0.0, seconds)

    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def exhausted_reason(self) -> str | None:
        checks: list[tuple[float | int | None, float | int, str]] = [
            (
                None
                if self.max_wall_seconds is None
                else self.max_wall_seconds
                - (0.0 if self._final_evaluation else self._reserved_wall),
                self.elapsed(),
                "wall",
            ),
            (self.max_builder_llm_calls, self.used_llm_calls, "llm_calls"),
            (self.max_simulations, self.used_simulations, "simulations"),
            (self.max_generated_agents, self.used_agents, "agents"),
            (self.max_total_tokens, self.used_tokens, "tokens"),
            (self.max_cost_usd, self.used_cost_usd, "cost"),
        ]
        for limit, used, name in checks:
            if self._final_evaluation and name == "llm_calls":
                continue
            if limit is not None and used >= limit:
                return name
        return None

    def can_continue(self) -> bool:
        return self.exhausted_reason() is None

    def can_simulate(self, n: int = 1) -> bool:
        if not self.can_continue():
            return False
        return self.max_simulations is None or (
            self.used_simulations + n + self._reserved_simulations <= self.max_simulations
        )

    def charge(
        self,
        *,
        sims: int = 0,
        llm_calls: int = 0,
        tokens: int = 0,
        cost: float = 0.0,
        agents: int = 0,
    ) -> None:
        self.used_simulations += sims
        self.used_llm_calls += llm_calls
        self.used_tokens += tokens
        self.used_cost_usd += cost
        self.used_agents += agents
