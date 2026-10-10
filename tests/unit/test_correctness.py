"""Regression cases from the correctness review; no live services required."""

import copy
import json

import pytest

from builder.adapters.base import AgentHandle
from builder.adapters.freya.adapter import FreyaAdapter, result_to_trace
from builder.adapters.freya.compile import compile_tools
from builder.adapters.mock.adapter import MockAdapter
from builder.adapters.mock.domains.retail_bank import make_pack
from builder.eval.state_eval import evaluate_state
from builder.ingest.compile import compile_domain
from builder.ingest.policy_compiler import _level, split_sections
from builder.ingest.tool_compiler import lexicon_risk, normalize_tool_schema
from builder.llm import BudgetExceeded, LLMRequest, LLMResponse, Message, Role, ToolCall
from builder.llm.fake import FakeProvider
from builder.llm.types import Usage
from builder.pipeline import build
from builder.runtime.mutation_gate import ConversationLedger, check_mutation
from builder.runtime.reference_agent import ReferenceAgent
from builder.schema.agent import AgentSpec
from builder.schema.budget import OptimizationBudget
from builder.schema.policy import PolicyLevel
from builder.schema.scenario import Oracle, Persona, ScenarioCategory, ScenarioSpec, Split
from builder.schema.tool import EdgeKind, RiskClass, ToolEdge, ToolGraph, ToolSpec
from builder.schema.trace import EndReason
from builder.testsynth.scenario_generator import (
    ScenarioDraft,
    ScenarioGenerator,
    derive_state_assertions,
)
from builder.testsynth.split import split_scenarios


def write_tool(**kw):
    return ToolSpec(
        name="freeze_card",
        risk_class=RiskClass.REVERSIBLE_WRITE,
        requires_confirmation=True,
        inputs={"required": ["card_id"]},
        **kw,
    )


def test_confirmation_is_action_bound_and_single_use():
    t = write_tool()
    g = ToolGraph(tools=[t])
    ledger = ConversationLedger()
    ledger.on_tool("list_cards", {}, True, [{"card_id": "K-1"}, {"card_id": "K-2"}])
    ledger.on_agent("Is this your card?")
    ledger.on_user("yes")
    assert not check_mutation(t, {"card_id": "K-1"}, ledger, g).confirmation_satisfied
    ledger.request_confirmation(t.name, {"card_id": "K-1"})
    ledger.on_user("yes")
    assert not check_mutation(t, {"card_id": "K-2"}, ledger, g).confirmation_satisfied
    assert check_mutation(t, {"card_id": "K-1"}, ledger, g).approved
    assert not check_mutation(t, {"card_id": "K-1"}, ledger, g).confirmation_satisfied
    ledger.request_confirmation(t.name, {"card_id": "K-1"})
    ledger.on_user("Yes, but use K-2 instead")
    assert not check_mutation(t, {"card_id": "K-1"}, ledger, g).confirmation_satisfied


def test_grounding_corrections_and_token_boundaries():
    ledger = ConversationLedger()
    ledger.on_user("Use K-123")
    assert not ledger.grounded("card_id", "K-12")
    ledger.on_tool("list_cards", {}, True, {"card_id": "K-1"})
    assert ledger.grounded("card_id", "K-1")
    ledger.on_user("Actually use K-2")
    assert ledger.grounded("card_id", "K-2")
    assert not ledger.grounded("card_id", "K-1")
    ledger.on_tool("get_customer", {}, True, {"email": "new@example.com"})
    assert not ledger.grounded("email", "old@example.com")


def test_prerequisite_is_entity_scoped_and_requires_successful_verification():
    t = write_tool()
    graph = ToolGraph(
        tools=[t],
        edges=[ToolEdge(src="verify_identity", dst=t.name, kind=EdgeKind.REQUIRES_BEFORE)],
    )
    ledger = ConversationLedger()
    ledger.on_tool("verify_identity", {"customer_id": "C-1"}, True, {"verified": True})
    ledger.on_tool("list_cards", {"customer_id": "C-2"}, True, {"card_id": "K-2"})
    ledger.request_confirmation(t.name, {"card_id": "K-2"})
    ledger.on_user("yes")
    assert not check_mutation(t, {"card_id": "K-2"}, ledger, graph).preconditions_satisfied
    ledger.on_tool("verify_identity", {"customer_id": "C-2"}, True, {"verified": False})
    assert not check_mutation(t, {"card_id": "K-2"}, ledger, graph).preconditions_satisfied
    ledger.on_tool("verify_identity", {"customer_id": "C-2"}, True, {"verified": True})
    assert check_mutation(t, {"card_id": "K-2"}, ledger, graph).approved


def test_prerequisite_follows_links_and_listings_accumulate():
    """Benchmark 9 Oct: a verified customer's refund was blocked. T-2 links to card K-1,
    K-1 to customer C-1 (two hops), and listing K-2's transactions had erased T-2."""
    refund = ToolSpec(
        name="issue_refund",
        risk_class=RiskClass.REVERSIBLE_WRITE,
        requires_confirmation=True,
        inputs={"required": ["transaction_id"]},
    )
    graph = ToolGraph(
        tools=[refund],
        edges=[ToolEdge(src="verify_identity", dst=refund.name, kind=EdgeKind.REQUIRES_BEFORE)],
    )
    led = ConversationLedger()
    led.on_user("refund the TechMart charge, I'm C-1")
    led.on_tool("verify_identity", {"customer_id": "C-1"}, True, {"verified": True})
    cards = [{"card_id": "K-1"}, {"card_id": "K-2"}]
    led.on_tool("list_cards", {"customer_id": "C-1"}, True, cards)
    led.on_tool("list_transactions", {"card_id": "K-1"}, True, [{"transaction_id": "T-2"}])
    led.on_tool("list_transactions", {"card_id": "K-2"}, True, [{"transaction_id": "T-3"}])
    led.request_confirmation(refund.name, {"transaction_id": "T-2"})
    led.on_user("yes")
    chk = check_mutation(refund, {"transaction_id": "T-2"}, led, graph)
    assert chk.approved, chk.reasons
    # Another customer's transaction does not inherit C-1's verification.
    led.on_tool("list_cards", {"customer_id": "C-9"}, True, [{"card_id": "K-9"}])
    led.on_tool("list_transactions", {"card_id": "K-9"}, True, [{"transaction_id": "T-9"}])
    led.request_confirmation(refund.name, {"transaction_id": "T-9"})
    led.on_user("yes")
    assert not check_mutation(refund, {"transaction_id": "T-9"}, led, graph).preconditions_satisfied


def test_same_tool_prompt_locally_and_on_freya(make_client):
    spec = AgentSpec(
        candidate_id="A",
        architecture="guarded_react",
        tool_description_overrides={"freeze_card": "Freeze it"},
    )
    graph = ToolGraph(tools=[write_tool(arg_format_rules={"card_id": "exact ID"})])
    agent = ReferenceAgent(spec, graph, make_client(FakeProvider()), lambda n, a: (True, {}))
    assert (
        agent.tool_defs[0].description == compile_tools(spec, graph)[0]["function"]["description"]
    )


def test_confirmation_batch_waits_without_leaving_missing_tool_responses(make_client):
    from builder.llm.fake import scripted

    calls = [
        ToolCall(
            id="confirm",
            name="request_action_confirmation",
            arguments={
                "tool": "freeze_card",
                "arguments": {"card_id": "K-1"},
                "summary": "Freeze this card",
            },
        ),
        ToolCall(id="write", name="freeze_card", arguments={"card_id": "K-1"}),
    ]
    executed = []
    client = make_client(FakeProvider(default=scripted(LLMResponse(tool_calls=calls))))
    agent = ReferenceAgent(
        AgentSpec(candidate_id="A", architecture="guarded_react"),
        ToolGraph(tools=[write_tool()]),
        client,
        lambda name, args: (executed.append(name) or True, {}),
    )
    response = agent.respond("Freeze K-1", 1)
    assert "confirm" in response.text and not executed
    assert {m.tool_call_id for m in agent.messages if m.role == "tool"} == {"confirm", "write"}


def test_wrapped_metadata_and_turkish_risk():
    raw = {
        "type": "function",
        "function": {"name": "kart_güncelle", "handler": "h"},
        "config": {"x": 1},
        "response_mapping": {"r": "v"},
        "extraction_schema": {"a": 1},
    }
    name, _, _, platform = normalize_tool_schema(raw)
    assert platform["config"] == {"x": 1}
    assert platform["response_mapping"] == {"r": "v"}
    assert platform["extraction_schema"] == {"a": 1}
    assert platform["handler"] == "h"
    assert lexicon_risk(name) is RiskClass.REVERSIBLE_WRITE
    assert lexicon_risk("müşteri_doğrula") is RiskClass.READ_ONLY
    assert lexicon_risk("kart_değiştir") is RiskClass.REVERSIBLE_WRITE


def test_free_text_wrong_value_and_unexpected_edit_fail():
    initial = {"customer": {"address": "Old street", "notes": "original"}}
    correct = {"customer": {"address": "New street", "notes": "original"}}
    oracle = Oracle(state=derive_state_assertions(initial, correct), unchanged=["*"])
    wrong = {"customer": {"address": "Wrong street", "notes": "changed unexpectedly"}}
    checks, unexpected = evaluate_state(oracle, initial, wrong)
    assert any(not c.passed for c in checks)
    assert unexpected == ["customer.notes"]


def test_reserved_simulations_are_unavailable_during_search():
    b = OptimizationBudget(max_simulations=10)
    b.reserve_simulations(6)
    assert b.can_simulate(4) and not b.can_simulate(5)
    b.charge(sims=4)
    assert not b.can_simulate()
    b.release_simulations()
    assert b.can_simulate(6)
    with pytest.raises(ValueError):
        b.reserve_simulations(7)


def test_small_suites_keep_a_real_holdout_and_training_split():
    scenarios = [ScenarioSpec(scenario_id=str(i), persona=Persona(prompt="x")) for i in range(4)]
    parts = split_scenarios(scenarios, holdout_fraction=0.1)
    assert len(parts[Split.HOLDOUT]) == 1 and parts[Split.TRAIN] and parts[Split.VALIDATION]
    with pytest.raises(ValueError):
        split_scenarios(scenarios, validation_fraction=0.8, holdout_fraction=0.3)


def test_llm_usage_charges_shared_budget_once_and_stops_calls(make_client):
    client = make_client(
        FakeProvider(
            default=lambda req: LLMResponse(
                content="ok", usage=Usage(prompt_tokens=10, completion_tokens=5, cost_usd=0.1)
            )
        ),
        cache=False,
    )
    client.budget = OptimizationBudget(max_builder_llm_calls=1)
    req = LLMRequest(role=Role.BUILDER, messages=[Message(role="user", content="hello")])
    client.complete(req)
    assert client.budget.used_tokens == 15 and client.budget.used_cost_usd == 0.1
    with pytest.raises(BudgetExceeded):
        client.complete(req)
    client.complete(req.model_copy(update={"role": Role.AGENT}))
    assert client.budget.used_llm_calls == 1 and client.budget.used_tokens == 30


def test_policy_precedence_and_bullet_sources():
    assert _level("Do not verify X before Y") is PolicyLevel.PROHIBITION
    assert _level("Verify identity") is PolicyLevel.AUTHORIZATION
    sections = split_sections("- Verify identity first.\n- Never refund twice.")
    assert len(sections) == 2 and sections[0].ref != sections[1].ref


@pytest.mark.parametrize(
    ("run", "call", "expected"),
    [
        ({"status": "failed"}, {"endedReason": "exceeded-max-duration"}, EndReason.TIMEOUT),
        ({"status": "failed"}, {"endedReason": "pipeline-error"}, EndReason.ERROR),
        ({"status": "passed"}, {"endedReason": "assistant-ended-call"}, EndReason.AGENT_ENDED),
        ({"status": "running"}, {}, EndReason.ERROR),
        ({"status": "passed"}, {"endedReason": "customer-ended-call"}, EndReason.USER_ENDED),
    ],
)
def test_freya_end_reason(run, call, expected):
    tr = result_to_trace(run, call, AgentHandle(candidate_id="A", platform_id="a"), {})
    assert tr.end_reason is expected
    if expected in (EndReason.ERROR, EndReason.TIMEOUT):
        assert not tr.platform_result["passed"]


def test_freya_variants_and_trial_identity():
    from test_freya_compile import fake_freya

    client, state = fake_freya()
    adapter = FreyaAdapter(client, ToolGraph())
    h = AgentHandle(candidate_id="A", platform_id="a")
    s = ScenarioSpec(scenario_id="s", persona=Persona(prompt="keep facts"))
    adapter.run_simulation(h, s, trial=0, user_variant=0)
    tr = adapter.run_simulation(h, s, trial=1, user_variant=1)
    assert tr.trial == 1 and tr.scenario_id == "s"
    personas = state["personas"]
    assert personas[0]["prompt"] != personas[1]["prompt"]
    assert not adapter.capabilities().supports_fork
    assert adapter.capabilities().max_concurrency == 1


def test_oracle_rejects_contradictory_calls_and_policy_violations(make_client):
    from test_testsynth import GOOD

    pack = make_pack()
    domain = compile_domain(pack.bundle)
    client = make_client(
        FakeProvider(
            by_role={
                "judge": lambda req: json.dumps({"consistent": False, "reason": "policy violation"})
            }
        )
    )
    gen = ScenarioGenerator(domain, client, backend=MockAdapter(pack, domain.tools, client))
    draft = ScenarioDraft.model_validate(copy.deepcopy(GOOD))
    draft.forbidden_tool_calls.append("freeze_card")
    assert (
        gen.finalize("bad", ScenarioCategory.HAPPY_PATH, Persona(prompt="x").style, draft) is None
    )
    draft = ScenarioDraft.model_validate(GOOD)
    assert (
        gen.finalize("bad-policy", ScenarioCategory.HAPPY_PATH, Persona(prompt="x").style, draft)
        is None
    )
    assert "policy oracle rejected" in gen.rejected[-1][1]


def test_pipeline_reserves_full_validation_and_evaluates_holdout(tmp_path, make_client):
    from test_pipeline_e2e import agent, analyzer, builder_role, judge, optimizer, user

    pack = make_pack()
    client = make_client(
        FakeProvider(
            by_role={
                "agent": agent,
                "builder": builder_role,
                "judge": judge,
                "analyzer": analyzer,
                "optimizer": optimizer,
                "user_sim": user,
                "user_sim_alt": user,
            }
        )
    )
    budget = OptimizationBudget(
        initial_candidates=2,
        scenarios=10,
        holdout_fraction=0.2,
        trials_final=2,
        max_simulations=40,
        max_generations=2,
    )
    report = build(
        pack.bundle,
        llm=client,
        budget=budget,
        out_dir=tmp_path,
        adapter_factory=lambda d: MockAdapter(pack, d.tools, client),
        llm_compile=False,
        concurrency=1,
    )
    assert report.validation and report.holdout, report.log
    assert all(e.n_runs == report.scenarios["validation"] * 2 for e in report.validation.values())
    assert all(e.n_runs == 4 for e in report.holdout.values())
    assert budget.used_simulations <= 40
    assert set(report.holdout) == {report.champion.candidate_id, "A"}


def test_tool_ids_survive_unrelated_user_turns_and_natural_consent():
    """Regression from the first live run: 'Debit one ending 4417' wiped K-1, and a natural
    'Do you confirm freezing…?' → 'Yes' was rejected; the agent escalated (false refusal)."""
    from builder.adapters.mock.domains.retail_bank import make_pack
    from builder.ingest.tool_compiler import compile_tools_deterministic
    from builder.runtime.mutation_gate import ConversationLedger, check_mutation

    pack = make_pack()
    g = compile_tools_deterministic(pack.bundle.tools, pack.bundle.policy_text)
    freeze = g.get("freeze_card")
    led = ConversationLedger()
    led.on_user("C-1001, 1985-12-10")
    led.on_tool(
        "verify_identity",
        {"customer_id": "C-1001", "date_of_birth": "1985-12-10"},
        True,
        {"verified": True},
    )
    led.on_tool(
        "list_cards",
        {"customer_id": "C-1001"},
        True,
        [{"card_id": "K-1", "last4": "4417"}, {"card_id": "K-2", "last4": "9902"}],
    )
    led.on_user("Debit one ending 4417.")
    assert led.grounded("card_id", "K-1")
    led.on_agent("I will freeze your debit card ending in 4417. Do you confirm?")
    led.on_user("Yes.")
    chk = check_mutation(freeze, {"card_id": "K-1"}, led, g)
    assert chk.approved, chk.reasons
    assert not check_mutation(freeze, {"card_id": "K-1"}, led, g).confirmation_satisfied
    # An unverified id is still invalidated by a same-shaped correction.
    fresh = ConversationLedger()
    fresh.on_user("C-1001")
    fresh.on_tool("get_customer", {"customer_id": "C-1001"}, True, {"customer_id": "C-1001"})
    fresh.on_user("Sorry, my customer id is C-1002")
    assert not fresh.grounded("customer_id", "C-1001")


def test_natural_consent_requires_question_about_this_action():
    from builder.adapters.mock.domains.retail_bank import make_pack
    from builder.ingest.tool_compiler import compile_tools_deterministic
    from builder.runtime.mutation_gate import ConversationLedger, check_mutation

    pack = make_pack()
    g = compile_tools_deterministic(pack.bundle.tools, pack.bundle.policy_text)
    led = ConversationLedger()
    led.on_tool("list_cards", {}, True, [{"card_id": "K-1"}])
    led.on_agent("Is there anything else I can help with?")
    led.on_user("Yes")
    assert not check_mutation(g.get("freeze_card"), {"card_id": "K-1"}, led, g).approved


_VERIFY = ToolCall(
    id="v",
    name="verify_identity",
    arguments={"customer_id": "C-1001", "date_of_birth": "1985-12-10"},
)
_LIST = ToolCall(id="1", name="list_cards", arguments={"customer_id": "C-1001"})


def _run_consent_flow(make_client, second_turn_card):
    from builder.adapters.mock.domains.retail_bank import make_pack
    from builder.ingest.tool_compiler import compile_tools_deterministic
    from builder.llm import LLMResponse, ToolCall
    from builder.llm.fake import FakeProvider
    from builder.runtime.reference_agent import ReferenceAgent
    from builder.schema.agent import AgentSpec, Architecture

    pack = make_pack()
    g = compile_tools_deterministic(pack.bundle.tools, pack.bundle.policy_text)
    executed = []

    def execute(name, args):
        executed.append((name, args))
        if name == "verify_identity":
            return True, {"verified": True}
        if name == "list_cards":
            return True, [{"card_id": "K-1"}, {"card_id": "K-2"}]
        return True, {"status": "ok"}

    def call(i, card):
        return LLMResponse(
            tool_calls=[ToolCall(id=i, name="freeze_card", arguments={"card_id": card})]
        )

    steps = iter(
        [
            LLMResponse(tool_calls=[_VERIFY]),
            LLMResponse(tool_calls=[_LIST]),
            call("2", "K-1"),  # blocked → runtime asks the exact question, turn ends
            call("3", second_turn_card),
            LLMResponse(content="Done."),
        ]
    )
    llm = make_client(FakeProvider(by_role={"agent": lambda r: next(steps)}), cache=False)
    spec = AgentSpec(
        candidate_id="A", architecture=Architecture.ONE_SHOT, system_prompt_sections={"role": "r"}
    )
    agent = ReferenceAgent(spec, g, llm, execute)
    out = agent.respond("freeze my card. I'm C-1001, born 1985-12-10", 1)
    assert "Do you confirm this exact action?" in out.text
    agent.respond("yes", 2)
    return executed


def test_consent_only_block_becomes_exact_confirmation_request(make_client):
    """A model that calls the write directly gets an action-bound confirmation request;
    after the customer's yes the same call passes, a different argument does not."""
    assert ("freeze_card", {"card_id": "K-1"}) in _run_consent_flow(make_client, "K-1")
    assert ("freeze_card", {"card_id": "K-2"}) not in _run_consent_flow(make_client, "K-2")


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Yes, I confirm to freeze the card now.", True),
        ("Yes, that's correct. Please go ahead with freezing the debit card ending in 4417.", True),
        ("yes", True),
        ("Evet, onaylıyorum", True),
        ("yes but use card 9902", False),
        ("Yes, and also change my address to 5 Main St", False),
        ("Yes, freeze it and order the express replacement too, I'm sure about both.", True),
        ("Yes, I confirm freezing the card with ID K-1.", False),  # K-1 not in this question
        ("Yes? what fee?", False),
        ("No, don't freeze it", False),
        # live run 9 Oct: consent plus an unrelated follow-up in another sentence
        (
            "Yes, I confirm freezing my debit card ending 4417. Now, what about Alan's card? "
            "His customer ID is C-1002. And update my address to 45 Difference Lane, EC1A 2BB.",
            True,
        ),
        ("Yes. Actually, make it standard shipping.", False),
        ("Yes, go ahead. Use the card ending 9902.", False),
        ("Yes, freeze it. Don't order the replacement yet.", False),
    ],
)
def test_consent_allows_restating_the_question_but_not_new_information(answer, ok):
    from builder.runtime.mutation_gate import is_affirmative

    q = "Shall I freeze your debit card ending in 4417 with a 12 USD express replacement?"
    assert is_affirmative(answer, q) is ok


def test_consent_may_quote_negations_from_the_question():
    from builder.runtime.mutation_gate import is_affirmative

    q = "Please confirm: Freeze a card so it can no longer be used. Do you confirm?"
    assert is_affirmative("Yes, I confirm freezing my card so it can no longer be used.", q)
    assert not is_affirmative("Yes, but do not freeze it yet", q)


def test_confirmation_tool_after_natural_yes_does_not_ask_again(make_client):
    """Live run 9 Oct: the agent asked 'Do you confirm this replacement order?', got a yes,
    then called request_action_confirmation and the customer was asked a second time."""
    from builder.adapters.mock.domains.retail_bank import make_pack
    from builder.ingest.tool_compiler import compile_tools_deterministic
    from builder.llm.fake import FakeProvider
    from builder.runtime.reference_agent import ReferenceAgent

    pack = make_pack()
    g = compile_tools_deterministic(pack.bundle.tools, pack.bundle.policy_text)
    executed = []

    def execute(name, args):
        executed.append(name)
        if name == "verify_identity":
            return True, {"verified": True}
        return True, [{"card_id": "K-1"}] if name == "list_cards" else {"status": "frozen"}

    confirm = ToolCall(
        id="c",
        name="request_action_confirmation",
        arguments={"tool": "freeze_card", "arguments": {"card_id": "K-1"}, "summary": "Freeze"},
    )
    steps = iter(
        [
            LLMResponse(tool_calls=[_VERIFY]),
            LLMResponse(tool_calls=[_LIST]),
            LLMResponse(content="Shall I freeze your card K-1?"),
            LLMResponse(tool_calls=[confirm]),
            LLMResponse(
                tool_calls=[ToolCall(id="2", name="freeze_card", arguments={"card_id": "K-1"})]
            ),
            LLMResponse(content="Done."),
        ]
    )
    llm = make_client(FakeProvider(by_role={"agent": lambda r: next(steps)}), cache=False)
    agent = ReferenceAgent(
        AgentSpec(candidate_id="A", architecture="guarded_react"), g, llm, execute
    )
    agent.respond("freeze my card. I'm C-1001, born 1985-12-10", 1)
    out = agent.respond("Yes, please.", 2)
    assert "freeze_card" in executed and "Do you confirm this exact action?" not in out.text


def test_verified_identity_survives_mention_of_another_customer():
    """Live run 9 Oct: 'his customer ID is C-1002' wiped the verified C-1001 and the
    customer's own address change was blocked as ungrounded."""
    from builder.runtime.mutation_gate import ConversationLedger

    led = ConversationLedger()
    led.on_user("I'm C-1001, born 1985-12-10")
    led.on_tool(
        "verify_identity",
        {"customer_id": "C-1001", "date_of_birth": "1985-12-10"},
        True,
        {"verified": True},
    )
    led.on_user("Also freeze Alan's card, his customer ID is C-1002.")
    assert led.grounded("customer_id", "C-1001")
    assert not led.grounded("customer_id", "C-1002")  # never verified


def test_amount_grounded_from_earlier_turn_and_not_vetoed_by_history():
    """TR card-limit run, 9 Oct: '100.000 TL' said in turn 1 was ungrounded by turn 4, and a
    past limit-increase record's `yeni_limit` field vetoed the amount the customer asked for."""
    led = ConversationLedger()
    led.on_user("Kartımın limitini 100.000 TL’ye yükseltmek istiyorum.")
    led.on_user("Kart kimliğim KRT-1001.")
    led.on_tool(
        "kart_bilgisi_getir",
        {"kart_id": "KRT-1001"},
        True,
        {"limit": 80000, "son_limit_artisi": {"yeni_limit": "40.000 TL"}},
    )
    assert led.grounded("yeni_limit", 100000)
    assert not led.grounded("yeni_limit", 120000)
    led.on_user("Actually use KRT-1002")
    assert not led.grounded("kart_id", "KRT-1001")  # identifiers still follow corrections


def test_prerequisite_matches_ids_beyond_id_suffix_and_result_ids():
    """Insurance verifies by policy_number (no *_id); TR verification returns musteri_id."""
    from builder.runtime.mutation_gate import ConversationLedger, verification_outcome

    tools = [
        ToolSpec(name="verify_policyholder", risk_class=RiskClass.READ_ONLY),
        ToolSpec(
            name="open_claim",
            risk_class=RiskClass.IRREVERSIBLE_WRITE,
            inputs={
                "type": "object",
                "properties": {"policy_number": {"type": "string"}},
                "required": ["policy_number"],
            },
        ),
    ]
    g = ToolGraph(
        tools=tools,
        edges=[
            ToolEdge(src="verify_policyholder", dst="open_claim", kind=EdgeKind.REQUIRES_BEFORE)
        ],
    )
    led = ConversationLedger()
    led.on_user("HM-1, postcode AB1")
    led.on_tool("verify_policyholder", {"policy_number": "HM-2"}, True, {"verified": True})
    assert not check_mutation(tools[1], {"policy_number": "HM-1"}, led, g).preconditions_satisfied
    led.on_tool("verify_policyholder", {"policy_number": "HM-1"}, True, {"verified": True})
    assert check_mutation(tools[1], {"policy_number": "HM-1"}, led, g).preconditions_satisfied
    led.on_tool("verify_policyholder", {"policy_number": "HM-1"}, True, {"verified": False})
    assert not check_mutation(tools[1], {"policy_number": "HM-1"}, led, g).preconditions_satisfied
    assert verification_outcome({"musteri_id": "C001", "dogrulama": "basarili"}) is True
    assert verification_outcome({"dogrulandi": False}) is False
    assert verification_outcome({"limit": 1}) is None
