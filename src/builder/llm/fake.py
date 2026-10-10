"""Deterministic offline provider for tests and dry runs without an API key.

A `FakeProvider` routes each request to a handler. Handlers receive the request
and return either a string (assistant content) or a full `LLMResponse`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from builder.llm.types import LLMRequest, LLMResponse

Handler = Callable[[LLMRequest], "str | LLMResponse"]


@dataclass
class FakeProvider:
    default: Handler | None = None
    by_role: dict[str, Handler] = field(default_factory=dict)
    calls: list[LLMRequest] = field(default_factory=list)

    def complete(self, model: str, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        handler = self.by_role.get(request.role.value, self.default)
        if handler is None:
            raise AssertionError(f"FakeProvider: no handler for role {request.role}")
        out = handler(request)
        if isinstance(out, LLMResponse):
            return out
        return LLMResponse(content=out, model=f"fake/{model}")


def scripted(*responses: str | LLMResponse) -> Handler:
    """Handler returning the given responses in order (last one repeats)."""
    queue = list(responses)

    def handler(_: LLMRequest) -> str | LLMResponse:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return handler
