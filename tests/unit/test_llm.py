from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from builder.llm import BudgetExceeded, LLMRequest, LLMResponse, Message, Role, ToolDef, Usage
from builder.llm.errors import StructuredOutputError
from builder.llm.fake import FakeProvider, scripted
from builder.llm.jsonutil import extract_json
from builder.llm.openrouter import OpenRouterProvider


class Pair(BaseModel):
    a: int
    b: str


def _req(text: str = "hi", role: Role = Role.JUDGE) -> LLMRequest:
    return LLMRequest(role=role, messages=[Message(role="user", content=text)])


def test_cache_hits_skip_provider(make_client):
    p = FakeProvider(default=scripted("one"))
    c = make_client(p)
    assert c.complete(_req()).content == "one"
    second = c.complete(_req())
    assert second.cached and len(p.calls) == 1


def test_default_temperature_by_role(make_client):
    p = FakeProvider(default=scripted("x"))
    make_client(p).complete(_req(role=Role.ANALYZER))
    assert p.calls[0].temperature == 0.0


def test_budget_cap_blocks_after_spend(make_client):
    p = FakeProvider(default=lambda r: LLMResponse(content="x", usage=Usage(cost_usd=0.6)))
    c = make_client(p, max_cost=1.0, cache=False)
    c.complete(_req("a"))
    c.complete(_req("b"))
    with pytest.raises(BudgetExceeded):
        c.complete(_req("c"))


def test_ledger_persists(tmp_path):
    from builder.llm.ledger import CostLedger

    led = CostLedger(tmp_path / "l.jsonl", 10)
    led.record("judge", "m", Usage(cost_usd=1.5))
    assert CostLedger(tmp_path / "l.jsonl", 10).total_usd == pytest.approx(1.5)


def test_structured_output_repairs_once(make_client):
    p = FakeProvider(default=scripted("not json", '```json\n{"a": 1, "b": "x"}\n```'))
    out = make_client(p).complete_structured(
        Role.BUILDER, Pair, [Message(role="user", content="go")]
    )
    assert out == Pair(a=1, b="x")
    assert len(p.calls) == 2


def test_structured_output_gives_up(make_client):
    p = FakeProvider(default=scripted('{"a": "nope"}'))
    with pytest.raises(StructuredOutputError):
        make_client(p).complete_structured(
            Role.BUILDER, Pair, [Message(role="user", content="go")], repair_attempts=1
        )


def test_extract_json_with_prose():
    assert extract_json('Sure! {"k": [1, 2]} hope that helps') == {"k": [1, 2]}


def test_openrouter_wire_roundtrip():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {"name": "lookup", "arguments": '{"id": "A"}'},
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.001},
            },
        )

    prov = OpenRouterProvider("k", transport=httpx.MockTransport(handler))
    req = LLMRequest(
        role=Role.AGENT,
        messages=[Message(role="user", content="hi")],
        tools=[ToolDef(name="lookup", description="d")],
        temperature=0.0,
    )
    resp = prov.complete("m", req)
    assert resp.tool_calls[0].arguments == {"id": "A"}
    assert resp.usage.cost_usd == pytest.approx(0.001)
    assert seen["body"]["tools"][0]["function"]["name"] == "lookup"


def test_openrouter_retries_then_fails(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, text="slow down")

    prov = OpenRouterProvider("k", transport=httpx.MockTransport(handler), max_retries=2)
    with pytest.raises(Exception, match="429"):
        prov.complete("m", _req())
    assert calls["n"] == 3


def test_payload_drops_empty_assistant_and_json_mode_for_anthropic():
    from builder.llm.openrouter import build_payload

    req = LLMRequest(
        role=Role.BUILDER,
        json_mode=True,
        messages=[
            Message(role="user", content="a"),
            Message(role="assistant", content=""),
            Message(role="user", content="b"),
        ],
    )
    p = build_payload("anthropic/claude-x", req)
    assert [m["role"] for m in p["messages"]] == ["user", "user"]
    assert "response_format" not in p
    assert build_payload("openai/gpt-x", req)["response_format"] == {"type": "json_object"}


def test_model_profiles(monkeypatch: pytest.MonkeyPatch) -> None:
    from builder.config import HAIKU, SONNET, RoleModels

    for k in ("BUILDER_PROFILE", "BUILDER_MODEL_BUILDER", "BUILDER_MODEL_AGENT"):
        monkeypatch.delenv(k, raising=False)
    m = RoleModels(_env_file=None)
    assert m.builder == m.optimizer == m.agent == HAIKU
    assert m.judge != m.agent and m.user_sim != m.agent  # other families than the agent
    monkeypatch.setenv("BUILDER_PROFILE", "competition")
    monkeypatch.setenv("BUILDER_MODEL_AGENT", "openai/gpt-6-luna-pro")
    m = RoleModels(_env_file=None)
    assert m.builder == m.optimizer == SONNET
    assert m.agent == "openai/gpt-6-luna-pro"  # per-role override wins


def test_provider_calls_run_in_parallel_and_inflight_cost_is_reserved(tmp_path):
    """GAP_ANALYSIS A4: the network call was made under the budget lock (4 threads = 1)."""
    import time
    from concurrent.futures import ThreadPoolExecutor

    from builder.llm import CostLedger, LLMClient
    from builder.schema.budget import OptimizationBudget

    class Slow:
        def complete(self, model, request):
            time.sleep(0.3)
            return LLMResponse(content="ok", usage=Usage(prompt_tokens=1, cost_usd=0.1))

    c = LLMClient(Slow(), {r: "m" for r in Role}, CostLedger(None, 50.0), cache=None)
    t = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: c.complete(_req(str(i))), range(8)))
    assert time.monotonic() - t < 1.2  # serial would be 2.4 s

    # With a known per-call cost, concurrent calls cannot jointly overshoot the build cap.
    c.budget = OptimizationBudget(max_cost_usd=1.05)
    ok, blocked = [], []

    def call(i):
        try:
            c.complete(_req(f"cap{i}"))
            ok.append(i)
        except BudgetExceeded:
            blocked.append(i)

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(call, range(16)))
    assert c.budget.used_cost_usd <= 1.05 + 1e-9, c.budget.used_cost_usd
    assert len(ok) == 10 and blocked
