from __future__ import annotations

import json

import httpx

from builder.adapters.freya.adapter import FreyaAdapter
from builder.adapters.freya.client import FreyaClient
from builder.adapters.freya.compile import (
    compile_agent,
    compile_assertion,
    compile_persona,
    preprocess_guard_code,
)
from builder.adapters.mock.domains.retail_bank import make_pack
from builder.ingest.tool_compiler import compile_tools_deterministic
from builder.schema.agent import AgentSpec, Architecture
from builder.schema.scenario import Oracle, Persona, ScenarioSpec, ToolCallAssertion
from builder.schema.task import Channel


def _graph():
    pack = make_pack()
    return compile_tools_deterministic(pack.bundle.tools, pack.bundle.policy_text)


def _spec(**kw):
    return AgentSpec(
        candidate_id="c",
        architecture=Architecture.GUARDED_REACT,
        system_prompt_sections={"role": "R"},
        **kw,
    )


def test_compile_agent_overlays_default_config():
    base = {
        "llmConfig": {"provider": "openai", "model": "base-model", "temperature": 0.7},
        "other": {"keep": 1},
    }
    cfg = compile_agent(_spec(temperature=0.1), _graph(), base_config=base)
    assert cfg["other"] == {"keep": 1}
    llm = cfg["llmConfig"]
    assert llm["model"] == "base-model" and llm["temperature"] == 0.1
    freeze = next(t for t in llm["tools"] if t["function"]["name"] == "freeze_card")
    assert "explicit yes" in freeze["function"]["description"]
    assert "sttConfig" not in cfg
    assert base["llmConfig"]["temperature"] == 0.7  # base not mutated


def test_voice_and_overrides():
    cfg = compile_agent(
        _spec(platform_overrides={"callTimeoutSettings.silenceTimeout": 9}),
        _graph(),
        channel=Channel.VOICE,
        max_call_seconds=300,
    )
    assert cfg["sttConfig"]["confidenceReask"] == {"enabled": True, "threshold": 0.6}
    assert cfg["callTimeoutSettings"] == {"maximumDuration": 300, "silenceTimeout": 9}


def test_preprocess_code_only_for_writes():
    g = _graph()
    assert preprocess_guard_code(g.get("list_cards")) is None
    code = preprocess_guard_code(g.get("issue_replacement"))
    assert '"express"' in code and "missing argument" in code


def test_persona_and_assertion():
    s = ScenarioSpec(
        scenario_id="s1",
        persona=Persona(prompt="lost card", hidden_facts={"dob": "1985-12-10"}),
        oracle=Oracle(
            tool_calls=[ToolCallAssertion(tool="freeze_card", args={"card_id": "K-1"})],
            required_communication=["the card is frozen"],
        ),
    )
    p = compile_persona(s)
    assert "freeze_card" in p["successCriteria"] and "dob" in p["prompt"]
    assert compile_assertion(s.oracle.tool_calls[0]) == {
        "type": "tool_call",
        "tool": "freeze_card",
        "args": {"card_id": "K-1"},
        "argsMatch": "subset",
        "negate": False,
    }


def _ok(result):
    return httpx.Response(200, json={"success": True, "data": {"message": "ok", "result": result}})


def fake_freya(messages=None, status="passed", call_extra=None):
    """In-memory Freya following the OpenAPI paths and response envelope.

    The default messages carry a guessed `toolCalls` field: the spec's message schema has no
    tool-call field, so tests built on it only check our parser, not platform compatibility.
    Replace with a recorded response after the first live smoke run (GAP_ANALYSIS A2)."""
    state: dict = {"personas": [], "sims": {}, "calls": [], "agents": []}
    msgs = (
        messages
        if messages is not None
        else [
            {"role": "user", "message": "lost my card"},
            {
                "role": "assistant",
                "message": "Shall I freeze it?",
                "toolCalls": [
                    {"function": {"name": "freeze_card", "arguments": '{"card_id": "K-1"}'}}
                ],
            },
            {"role": "tool_call_result", "name": "freeze_card", "result": '{"status": "frozen"}'},
            {"role": "assistant", "message": "Your card is now frozen."},
        ]
    )

    def handler(req: httpx.Request) -> httpx.Response:
        path, m = req.url.path.removeprefix("/api/v2"), req.method
        state["calls"].append((m, path, dict(req.url.params)))
        body = json.loads(req.content) if req.content else {}
        if path == "/agents/defaults":
            assert req.url.params["workspaceId"] == "ws"
            return _ok({"config": {"llmConfig": {"provider": "openai", "model": "m0"}}})
        if path == "/agents" and m == "POST":
            state["agents"].append(body)
            return _ok({"agent": {"id": f"a{len(state['agents'])}", "workspaceId": "ws"}})
        if path == "/personas" and m == "POST":
            out = []
            for p in body["personas"]:
                state["personas"].append(p)
                out.append({"id": f"p{len(state['personas'])}", **p})
            return _ok({"personas": out})
        if path == "/simulations" and m == "POST":
            sid = f"s{len(state['sims']) + 1}"
            assert body["name"] and body["start"] is True
            state["sims"][sid] = body
            return _ok({"simulation": {"id": sid, "status": "running"}})
        if path.startswith("/simulations/") and path.endswith("/iterations"):
            return _ok({"iterations": [{"iterationIndex": 0, "agentVersionId": "v1"}], "total": 1})
        if path == "/simulations/version-persona-runs":
            runs = [
                {"personaId": pid, "simulationId": sid, "status": status, "runId": f"c-{pid}"}
                for sid, b in state["sims"].items()
                for pid in b["selectedPersonaIds"]
            ]
            return _ok({"runs": runs})
        if path.startswith("/simulations/"):
            return _ok({"simulation": {"id": path.split("/")[2], "status": "completed"}})
        if path.startswith("/call/"):
            return _ok(
                {
                    "call": {
                        "id": path.split("/")[2],
                        "messages": msgs,
                        "endedReason": "assistant-ended-call",
                        **(call_extra or {}),
                    }
                }
            )
        return httpx.Response(404, json={"success": False, "data": {"message": path}})

    client = FreyaClient(
        "k",
        "https://app.freyavoice.ai/api/v2",
        workspace_id="ws",
        transport=httpx.MockTransport(handler),
    )
    return client, state


def test_adapter_deploy_uses_default_config():
    client, state = fake_freya()
    h = FreyaAdapter(client, _graph()).deploy_agent(_spec())
    assert h.platform_id == "a1"
    cfg = state["agents"][0]["config"]
    assert cfg["llmConfig"]["model"] == "m0"
    assert all(t["function"]["handler"] for t in cfg["llmConfig"]["tools"])
    assert [c[:2] for c in state["calls"]] == [("GET", "/agents/defaults"), ("POST", "/agents")]


def test_adapter_simulation_maps_runs_to_traces():
    from builder.eval.evaluator import Evaluator
    from builder.schema.trace import EventKind

    client, state = fake_freya()
    g = _graph()
    ad = FreyaAdapter(client, g)
    h = ad.deploy_agent(_spec())
    sc = [
        ScenarioSpec(
            scenario_id=f"s{i}",
            persona=Persona(prompt="lost card"),
            oracle=Oracle(tool_calls=[ToolCallAssertion(tool="freeze_card")]),
        )
        for i in range(2)
    ]
    traces = ad.run_batch(h, sc)
    assert sorted(t.scenario_id for t in traces) == ["s0", "s1"]
    t = traces[0]
    assert [e.kind for e in t.events][:3] == [EventKind.USER, EventKind.AGENT, EventKind.TOOL_CALL]
    assert t.events[2].args == {"card_id": "K-1"}
    assert t.platform_result["passed"] and h.version_id == "v1"
    assert len(state["personas"]) == 2  # batch-created once
    r = Evaluator(g).evaluate(t, sc[0])
    assert any(c.name == "platform_verdict" and c.passed for c in r.checks)


def test_envelope_failure_raises():
    import pytest

    from builder.adapters.freya.client import FreyaError

    def handler(req):
        return httpx.Response(200, json={"success": False, "data": {"message": "bad"}})

    c = FreyaClient("k", workspace_id="ws", transport=httpx.MockTransport(handler))
    with pytest.raises(FreyaError, match="bad"):
        c.default_config()


def _default_config():
    return {
        "llmConfig": {"provider": "openai", "model": "gpt-x", "systemPrompt": "default"},
        "ttsConfig": {"provider": "freya", "voiceId": "v1"},
        "sttConfig": {"provider": "freya"},
        "complianceConfig": {},
        "callTimeoutSettings": {"maximumDuration": 600},
    }


def test_shipped_prompt_has_no_local_only_tools_for_any_candidate():
    """GAP_ANALYSIS A1: think/search_knowledge/blocked_by_safeguard exist only locally."""
    from pathlib import Path

    from builder.adapters.freya.compile import local_tool_references, platform_system_prompt
    from builder.ingest.bundle import load_bundle
    from builder.ingest.compile import compile_domain
    from builder.synth import prompt_blocks
    from builder.synth.candidate_generator import generate_candidates

    root = Path(__file__).resolve().parents[2] / "examples" / "bundles"
    for name in ("retail_bank.json", "insurance_claims", "loan_deferral.json"):
        d = compile_domain(load_bundle(root / name), None)
        for c in generate_candidates(d, n=6):
            local = c.render_system_prompt()
            shipped = platform_system_prompt(c)
            assert not local_tool_references(shipped), (name, c.candidate_id)
            assert "blocked_by_safeguard" not in shipped
            if c.architecture is Architecture.GUARDED_REACT_THINK:
                assert "`think`" in local and "checklist" in shipped
    # Large corpora get the search_knowledge wording; the shipped prompt must not.
    d = compile_domain(load_bundle(root / "retail_bank.json"), None)
    original = prompt_blocks.INLINE_DOC_CHAR_LIMIT
    prompt_blocks.INLINE_DOC_CHAR_LIMIT = 10
    try:
        docs = [doc.model_copy(update={"rule_ids": []}) for doc in d.knowledge.documents]
        big = d.model_copy(update={"knowledge": d.knowledge.model_copy(update={"documents": docs})})
        text = prompt_blocks.knowledge_retrieval_policy(big)
    finally:
        prompt_blocks.INLINE_DOC_CHAR_LIMIT = original
    assert "`search_knowledge`" in text
    spec = _spec().model_copy(update={"system_prompt_sections": {"knowledge": text}})
    assert not local_tool_references(platform_system_prompt(spec))


def test_single_submission_path_is_valid_and_has_no_simulation_settings():
    """GAP_ANALYSIS A5: offline export had model null and simulation-only call duration."""
    from builder.adapters.freya.compile import submission_config, validate_config

    overlay, notes = submission_config(_spec(), _graph())
    assert "model" not in overlay["llmConfig"] and "overlay only" in notes[0]
    assert "callTimeoutSettings" not in overlay

    cfg, problems = submission_config(_spec(), _graph(), base_config=_default_config())
    assert problems == [], problems
    assert cfg["llmConfig"]["model"] == "gpt-x"
    assert cfg["callTimeoutSettings"]["maximumDuration"] == 600  # platform value kept
    assert validate_config({"llmConfig": {"model": None}})  # missing fields are reported


def test_card_number_guardrail_only_when_task_mentions_cards():
    no_cards = compile_agent(_spec(), _graph())["guardrailsConfig"]["rules"]
    assert not any(r["id"] == "no_full_pan" for r in no_cards)
    cards = AgentSpec(
        candidate_id="c",
        architecture=Architecture.GUARDED_REACT,
        system_prompt_sections={"role": "Never read out a full card number."},
    )
    rules = compile_agent(cards, _graph())["guardrailsConfig"]["rules"]
    assert any(r["id"] == "no_full_pan" for r in rules)


def test_spec_shaped_call_uses_native_results_and_keeps_raw():
    """Spec messages carry no tool calls: tool checks become unknown, not failed, and the
    platform's own assertion/criteria results decide (GAP_ANALYSIS A2/A3)."""
    from builder.eval.evaluator import Evaluator

    spec_msgs = [
        {"role": "user", "message": "lost my card"},
        {"role": "assistant", "message": "Your card ending 4417 is now frozen."},
        {"role": "assistant", "message": "x", "wasBlocked": True, "blockedContent": "4111..."},
    ]
    extra = {
        "assertionResults": [{"name": "freeze called", "success": True, "reason": "ok"}],
        "successCriteriaResult": {"success": True, "reason": "frozen and told"},
    }
    client, state = fake_freya(messages=spec_msgs, call_extra=extra)
    g = _graph()
    ad = FreyaAdapter(client, g, persona_llms=[{"provider": "openai", "model": "u1"}])
    h = ad.deploy_agent(_spec())
    sc = ScenarioSpec(
        scenario_id="s0",
        persona=Persona(prompt="lost card"),
        oracle=Oracle(tool_calls=[ToolCallAssertion(tool="freeze_card")]),
    )
    t = ad.run_simulation(h, sc)
    body = next(iter(state["sims"].values()))
    assert body["maxRetries"] == 0
    assert body["personaLlmOverride"] == {"provider": "openai", "model": "u1"}
    assert not t.tool_calls_observable
    assert t.platform_result["raw"]["call"]["messages"] == spec_msgs
    assert t.platform_result["successCriteria"]["success"] is True
    assert t.platform_result["guardrailBlocks"] == ["4111..."]
    r = Evaluator(g).evaluate(t, sc)
    assert r.success and not r.false_refusal, r.checks
    assert any(c.name.startswith("platform_assertion:") for c in r.checks)
    assert not any(c.evaluator == "tool_call" for c in r.checks)
