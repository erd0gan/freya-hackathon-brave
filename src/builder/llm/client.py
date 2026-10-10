"""LLM client: provider protocol + a wrapper adding cache, budget and roles.

`LLMClient.complete()` is the only entry point the rest of the builder uses.
Providers only translate an `LLMRequest` into a wire call.
"""

from __future__ import annotations

import threading
import time
from typing import Protocol, TypeVar

from pydantic import BaseModel

from builder.llm.cache import DiskCache, request_key
from builder.llm.errors import BudgetExceeded, StructuredOutputError
from builder.llm.jsonutil import extract_json
from builder.llm.ledger import CostLedger
from builder.llm.roles import DEFAULT_TEMPERATURE, Role
from builder.llm.types import LLMRequest, LLMResponse, Message
from builder.schema.budget import OptimizationBudget

T = TypeVar("T", bound=BaseModel)


class Provider(Protocol):
    def complete(self, model: str, request: LLMRequest) -> LLMResponse: ...


class LLMClient:
    def __init__(
        self,
        provider: Provider,
        models: dict[Role, str],
        ledger: CostLedger,
        cache: DiskCache | None = None,
    ) -> None:
        self.provider = provider
        self.models = models
        self.ledger = ledger
        self.cache = cache
        self.budget: OptimizationBudget | None = None
        self._budget_lock = threading.RLock()
        self._inflight_cost = 0.0
        self._inflight_tokens = 0
        self._observed: dict[str, tuple[int, float, int]] = {}

    def model_for(self, role: Role) -> str:
        return self.models[role]

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Cache/budget checks and charging happen under the lock; the provider call does not.

        Holding the lock across the network call serialized every simulator thread
        (GAP_ANALYSIS A4: 8 calls x 0.5 s on 4 threads took 4.0 s). Calls in flight reserve
        their expected cost/tokens so concurrent calls cannot jointly overshoot a cap.
        """
        model = request.model or self.models[request.role]
        if request.temperature is None:
            request = request.model_copy(update={"temperature": DEFAULT_TEMPERATURE[request.role]})
        key = request_key(model, request)
        with self._budget_lock:
            if self.cache is not None and (hit := self.cache.get(key)) is not None:
                return hit
            reserve_cost, reserve_tokens = self._expected(model)
            self._admit(request, reserve_cost, reserve_tokens)
            self._inflight_cost += reserve_cost
            self._inflight_tokens += reserve_tokens
        try:
            start = time.monotonic()
            response = self.provider.complete(model, request)
            response.latency_s = time.monotonic() - start
            response.model = response.model or model
        finally:
            with self._budget_lock:
                self._inflight_cost -= reserve_cost
                self._inflight_tokens -= reserve_tokens
        with self._budget_lock:
            self.ledger.record(request.role.value, model, response.usage)
            tokens = response.usage.prompt_tokens + response.usage.completion_tokens
            n, cost_sum, tok_sum = self._observed.get(model, (0, 0.0, 0))
            self._observed[model] = (n + 1, cost_sum + response.usage.cost_usd, tok_sum + tokens)
            if self.budget is not None:
                self.budget.charge(tokens=tokens, cost=response.usage.cost_usd)
            if self.cache is not None:
                self.cache.put(key, response)
        return response

    def _expected(self, model: str) -> tuple[float, int]:
        """Mean observed cost/tokens per call for this model (0 until one call returns)."""
        n, cost_sum, tok_sum = self._observed.get(model, (0, 0.0, 0))
        return (cost_sum / n, tok_sum // n) if n else (0.0, 0)

    def _admit(self, request: LLMRequest, reserve_cost: float, reserve_tokens: int) -> None:
        """Raise if this call (plus calls already in flight) would exceed a cap. Lock held."""
        if self.ledger.total_usd + self._inflight_cost >= self.ledger.max_cost_usd:
            self.ledger.check()
            raise BudgetExceeded(
                f"LLM spend ${self.ledger.total_usd:.2f} + in-flight "
                f"${self._inflight_cost:.2f} reached cap ${self.ledger.max_cost_usd:.2f}"
            )
        self.ledger.check()
        budget = self.budget
        if budget is None:
            return
        builder_call = request.role in {Role.BUILDER, Role.OPTIMIZER, Role.ANALYZER}
        reason = next(
            (
                name
                for limit, used, name in [
                    (budget.max_wall_seconds, budget.elapsed(), "wall"),
                    (
                        budget.max_total_tokens,
                        budget.used_tokens + self._inflight_tokens + reserve_tokens,
                        "tokens",
                    ),
                    (
                        budget.max_cost_usd,
                        budget.used_cost_usd + self._inflight_cost + reserve_cost,
                        "cost",
                    ),
                    (
                        budget.max_builder_llm_calls if builder_call else None,
                        budget.used_llm_calls,
                        "llm_calls",
                    ),
                ]
                if limit is not None and used >= limit
            ),
            None,
        )
        if reason:
            raise BudgetExceeded(f"optimization budget: {reason}")
        budget.charge(llm_calls=int(builder_call))

    def complete_structured(
        self,
        role: Role,
        schema: type[T],
        messages: list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int = 4000,
        repair_attempts: int = 1,
        cache_salt: str = "",
    ) -> T:
        """Ask for JSON matching `schema`; validate and repair once on failure."""
        # A user turn, not a second system message: some providers only accept a leading system.
        instruction = Message(
            role="user",
            content=(
                "Respond with a single JSON object only, no prose, matching this JSON Schema:\n"
                + _schema_text(schema)
            ),
        )
        convo = [*messages, instruction]
        last_error = ""
        for attempt in range(repair_attempts + 1):
            resp = self.complete(
                LLMRequest(
                    role=role,
                    messages=convo,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=True,
                    cache_salt=f"{cache_salt}#{attempt}" if attempt else cache_salt,
                )
            )
            try:
                return schema.model_validate(extract_json(resp.content or ""))
            except (ValueError, TypeError) as exc:
                last_error = str(exc)
                convo = [
                    *convo,
                    Message(role="assistant", content=resp.content or ""),
                    Message(
                        role="user",
                        content=f"That output was invalid: {last_error[:2000]}\n"
                        "Return corrected JSON only.",
                    ),
                ]
        raise StructuredOutputError(f"{schema.__name__}: {last_error[:500]}")


def _schema_text(schema: type[BaseModel]) -> str:
    import json

    return json.dumps(schema.model_json_schema(), ensure_ascii=False)
