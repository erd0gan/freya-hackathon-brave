from __future__ import annotations

from builder.adapters.mock.adapter import MockAdapter
from builder.adapters.mock.domains.retail_bank import make_pack
from builder.ingest.tool_compiler import compile_tools_deterministic
from builder.llm import LLMRequest, LLMResponse, ToolCall
from builder.llm.fake import FakeProvider, scripted
from builder.runtime.mutation_gate import ConversationLedger, check_mutation, is_affirmative
from builder.schema.agent import AgentSpec, Architecture
from builder.schema.scenario import Persona, ScenarioSpec
from builder.schema.tool import RiskClass
from builder.schema.trace import EndReason, EventKind


def _graph():
    pack = make_pack()
    return pack, compile_tools_deterministic(pack.bundle.tools, pack.bundle.policy_text)


def test_lexicon_risk_classes():
    _, g = _graph()
    risk = {t.name: t.risk_class for t in g.tools}
    assert risk["list_cards"] is RiskClass.READ_ONLY
    assert risk["freeze_card"] is RiskClass.REVERSIBLE_WRITE
    assert risk["issue_refund"] is RiskClass.IRREVERSIBLE_WRITE
    assert risk["transfer_to_human"] is RiskClass.EXTERNAL_SIDE_EFFECT
    conf = {t.name: t.requires_confirmation for t in g.tools}
    assert conf["freeze_card"] and conf["update_address"] and conf["issue_refund"]
    assert not conf["transfer_to_human"] and not conf["list_cards"]


def test_affirmation_detection():
    assert is_affirmative("Yes please, go ahead")
    assert is_affirmative("evet, onaylıyorum")
    assert not is_affirmative("No, wait")
    assert not is_affirmative("I don't know")


def test_gate_blocks_ungrounded_and_unconfirmed():
    _, g = _graph()
    freeze = g.get("freeze_card")
    led = ConversationLedger()
    led.on_user("I lost my card")
    chk = check_mutation(freeze, {"card_id": "K-9"}, led, g)
    assert not chk.approved
    assert not chk.arguments_grounded and not chk.confirmation_satisfied

    led.on_tool("list_cards", {"customer_id": "C-1001"}, True, [{"card_id": "K-1"}])
    led.request_confirmation("freeze_card", {"card_id": "K-1"})
    led.on_agent("Shall I freeze your card ending 4417?")
    led.on_user("yes")
    unverified = check_mutation(freeze, {"card_id": "K-1"}, led, g)
    assert not unverified.preconditions_satisfied  # AUTH-01: verify before any change
    led.on_tool(
        "verify_identity",
        {"customer_id": "C-1001", "date_of_birth": "1985-12-10"},
        True,
        {"verified": True},
    )
    led.request_confirmation("freeze_card", {"card_id": "K-1"})
    led.on_agent("Shall I freeze your card ending 4417?")
    led.on_user("yes")
    assert check_mutation(freeze, {"card_id": "K-1"}, led, g).approved
    led.on_tool("freeze_card", {"card_id": "K-1"}, True, {"status": "frozen"})
    dup = check_mutation(freeze, {"card_id": "K-1"}, led, g)
    assert not dup.duplicate_risk_clear


def _agent_script():
    """A scripted agent: verify → list → ask → freeze → end."""
    steps = [
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="1",
                    name="verify_identity",
                    arguments={"customer_id": "C-1001", "date_of_birth": "1985-12-10"},
                )
            ]
        ),
        LLMResponse(
            tool_calls=[ToolCall(id="2", name="list_cards", arguments={"customer_id": "C-1001"})]
        ),
        LLMResponse(
            tool_calls=[
                ToolCall(
                    id="confirm",
                    name="request_action_confirmation",
                    arguments={
                        "tool": "freeze_card",
                        "arguments": {"card_id": "K-1"},
                        "summary": "Freeze your card ending 4417?",
                    },
                )
            ]
        ),
        LLMResponse(
            tool_calls=[ToolCall(id="3", name="freeze_card", arguments={"card_id": "K-1"})]
        ),
        LLMResponse(content="Your card ending 4417 is now frozen."),
        LLMResponse(tool_calls=[ToolCall(id="4", name="end_conversation", arguments={})]),
    ]

    def handler(req: LLMRequest) -> LLMResponse:
        return steps.pop(0)

    return handler


def test_mock_simulation_end_to_end(make_client):
    pack, g = _graph()
    provider = FakeProvider(
        by_role={
            "agent": _agent_script(),
            "user_sim": scripted(
                "Hi, I lost my debit card. My ID is C-1001, born 1985-12-10.",
                "Yes, go ahead.",
                "Thanks, that's all.",
            ),
        }
    )
    adapter = MockAdapter(pack, g, make_client(provider, cache=False))
    spec = AgentSpec(
        candidate_id="c1",
        architecture=Architecture.GUARDED_REACT,
        system_prompt_sections={"role": "You are a bank agent."},
    )
    assert adapter.validate_agent(spec).ok
    h = adapter.deploy_agent(spec)
    sc = ScenarioSpec(scenario_id="s1", persona=Persona(prompt="lost card"))
    tr = adapter.run_simulation(h, sc)
    assert tr.end_reason is EndReason.AGENT_ENDED
    assert tr.final_state["cards"]["K-1"]["status"] == "frozen"
    gates = [e for e in tr.events if e.kind is EventKind.MUTATION_CHECK]
    assert len(gates) == 1 and gates[0].ok
    assert "frozen" in tr.transcript()


def test_fault_injection_apply_then_fail(make_client):
    from builder.adapters.mock.env import Environment

    pack, _ = _graph()
    env = Environment.from_pack(
        pack, None, {"freeze_card": {"error": "timeout", "apply_then_fail": True}}
    )
    env.session["verified_customer"] = "C-1001"
    ok, res = env.call("freeze_card", {"card_id": "K-1"})
    assert not ok and res["error"] == "timeout"
    assert env.db["cards"]["K-1"]["status"] == "frozen"
    ok, _ = env.call("freeze_card", {"card_id": "K-1"})
    assert ok


def test_gate_grounding_scope_and_consent_edge_cases():
    from builder.runtime.mutation_gate import needs_grounding

    assert needs_grounding("K-1") and needs_grounding("a@b.co") and needs_grounding(349.99)
    for v in (False, True, 3, "2026-10-01", "rear-ended at the lights", "express"):
        assert not needs_grounding(v), v
    assert not is_affirmative("That's not right")
    assert not is_affirmative("ok, but what is the fee?")
    assert not is_affirmative("I'm not sure")
    _, g = _graph()
    led = ConversationLedger()
    led.on_user("my number is 555 123-4567")
    from builder.schema.tool import ToolSpec

    t = ToolSpec(name="update_phone", inputs={"required": ["phone", "verified"]})
    chk = check_mutation(t, {"phone": "5551234567", "verified": False}, led, g)
    assert chk.arguments_grounded, chk.reasons
