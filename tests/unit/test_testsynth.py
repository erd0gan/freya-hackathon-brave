from __future__ import annotations

import json
import re
from collections import Counter

from builder.adapters.mock.adapter import MockAdapter
from builder.adapters.mock.domains.retail_bank import make_pack
from builder.ingest.compile import compile_domain
from builder.llm.fake import FakeProvider, scripted
from builder.schema.scenario import Persona, ScenarioCategory, ScenarioSpec, Split, StateOp
from builder.testsynth.categories import applicable, quotas
from builder.testsynth.policy_graph_walk import sample_combinations
from builder.testsynth.scenario_generator import ScenarioGenerator
from builder.testsynth.split import split_scenarios
from builder.testsynth.statediff import diff, set_path


def _domain():
    pack = make_pack()
    return pack, compile_domain(pack.bundle)


def test_quotas_cover_overcaution_and_skip_inapplicable():
    _, d = _domain()
    cats = applicable(d)
    assert ScenarioCategory.VOICE not in cats  # text channel
    q = quotas(d, 24)
    assert len(q) == 24
    for c in (
        ScenarioCategory.RISKY_LOOKING_LEGIT,
        ScenarioCategory.ALREADY_CONFIRMED,
        ScenarioCategory.TRANSFER_NOT_NEEDED,
        ScenarioCategory.POLICY_PRESSURE,
    ):
        assert c in q
    assert Counter(q)[ScenarioCategory.HAPPY_PATH] >= 2


def test_policy_walk_spans_complexity():
    _, d = _domain()
    combos = sample_combinations(d.policy, 10, seed=1)
    sizes = sorted(len(c) for c in combos)
    assert sizes[0] >= 1 and sizes[-1] > sizes[0]
    assert all(len(set(c)) == len(c) for c in combos)


def test_statediff_and_patch():
    a = {"cards": {"K": {"status": "active"}}, "orders": []}
    b = json.loads(json.dumps(a))
    set_path(b, "cards.K.status", "frozen")
    set_path(b, "orders.0", {"id": "R-1"})
    d = diff(a, b)
    assert d["cards.K.status"] == ("active", "frozen")
    assert d["orders.__len__"] == (0, 1) and d["orders.0.id"] == (None, "R-1")


def test_split_is_stratified_and_disjoint():
    sc = [
        ScenarioSpec(
            scenario_id=f"s{i}", category=list(ScenarioCategory)[i % 4], persona=Persona(prompt="x")
        )
        for i in range(30)
    ]
    parts = split_scenarios(sc, validation_fraction=0.34, holdout_fraction=0.1, seed=3)
    ids = [s.scenario_id for p in parts.values() for s in p]
    assert sorted(ids) == sorted(s.scenario_id for s in sc)
    assert 8 <= len(parts[Split.VALIDATION]) <= 12
    assert len(parts[Split.HOLDOUT]) == 3
    assert all(s.split is Split.VALIDATION for s in parts[Split.VALIDATION])


GOOD = {
    "user_goal": "freeze lost debit card",
    "persona_prompt": "You are Ada. You lost your debit card ending 4417 and want it frozen.",
    "hidden_facts": {"customer_id": "C-1001", "date_of_birth": "1985-12-10"},
    "reference_actions": [
        {
            "tool": "verify_identity",
            "args": {"customer_id": "C-1001", "date_of_birth": "1985-12-10"},
        },
        {"tool": "freeze_card", "args": {"card_id": "K-1"}},
    ],
    "expected_tool_calls": [{"tool": "freeze_card", "args": {"card_id": "K-1"}}],
    "forbidden_tool_calls": ["issue_replacement"],
    "required_communication": ["card ending 4417 is frozen"],
    "target_policies": ["CARD-01", "AUTH-01"],
}


def test_generator_derives_state_oracle_by_execution(make_client):
    pack, d = _domain()
    bad = dict(GOOD, reference_actions=[{"tool": "freeze_card", "args": {"card_id": "K-1"}}])
    unknown = dict(GOOD, expected_tool_calls=[{"tool": "nuke", "args": {}}])
    provider = _scenario_provider(GOOD, bad, unknown)
    llm = make_client(provider)
    backend = MockAdapter(pack, d.tools, llm)
    gen = ScenarioGenerator(d, llm, backend=backend, seed=0, max_attempts=1)
    out = gen.generate(3, categories=[ScenarioCategory.HAPPY_PATH] * 3)
    assert len(out) == 1, gen.rejected
    s = out[0]
    assert [(a.path, a.op, a.value) for a in s.oracle.state] == [
        ("cards.K-1.status", StateOp.EQ, "frozen")
    ]
    assert any(a.negated and a.tool == "issue_replacement" for a in s.oracle.tool_calls)
    reasons = " ".join(r for _, r in gen.rejected)
    assert "reference actions failed" in reasons and "unknown tools" in reasons


def test_generator_drops_described_forbidden_calls_instead_of_rejecting(make_client):
    pack, d = _domain()
    draft = dict(GOOD, forbidden_tool_calls=["issue_replacement", "freeze_card with card_id K-2"])
    llm = make_client(_scenario_provider(draft))
    gen = ScenarioGenerator(d, llm, backend=MockAdapter(pack, d.tools, llm), max_attempts=1)
    out = gen.generate(1, categories=[ScenarioCategory.HAPPY_PATH])
    assert len(out) == 1, gen.rejected
    assert any(a.negated and a.tool == "issue_replacement" for a in out[0].oracle.tool_calls)


def test_generator_accepts_extra_rules_but_not_missing_the_sample(make_client):
    pack, d = _domain()
    extra = next(r.id for r in d.policy.rules)
    llm = make_client(_scenario_provider(GOOD, extra_rules=[extra]))
    gen = ScenarioGenerator(d, llm, backend=MockAdapter(pack, d.tools, llm), max_attempts=1)
    assert len(gen.generate(1, categories=[ScenarioCategory.HAPPY_PATH])) == 1, gen.rejected


def test_generator_repairs_invalid_draft_once(make_client):
    pack, d = _domain()
    bad = dict(GOOD, reference_actions=[{"tool": "freeze_card", "args": {"card_id": "K-1"}}])
    provider = _scenario_provider(bad, GOOD)
    llm = make_client(provider)
    gen = ScenarioGenerator(d, llm, backend=MockAdapter(pack, d.tools, llm))
    out = gen.generate(1, categories=[ScenarioCategory.HAPPY_PATH])
    assert len(out) == 1 and gen.repaired == 1 and not gen.rejected
    assert "invalid" in (
        [c for c in provider.calls if c.role.value == "builder"][-1].messages[-2].content or ""
    )


def _scenario_provider(*drafts, extra_rules=()):
    draft_script = scripted(*(json.dumps(d) for d in drafts))

    def generate(req):
        response = draft_script(req)
        body = next(m.content for m in req.messages if m.role == "user")
        data = json.loads(response)
        sampled = body.split("TARGET RULES:", 1)[1].split("PERSONA STYLE:", 1)[0]
        data["target_policies"] = re.findall(r"\[([^]]+)\]", sampled) + list(extra_rules)
        return json.dumps(data)

    return FakeProvider(
        by_role={"builder": generate, "judge": lambda req: json.dumps({"consistent": True})}
    )


def test_simulated_backend_oracle_asserts_only_agent_values_exactly():
    """Live run 9 Oct (insurance): a regenerated claim id and a reworded description made
    every candidate fail. Only agent-supplied values are exact on simulated backends."""
    from builder.eval.state_eval import evaluate_state
    from builder.schema.scenario import Oracle
    from builder.testsynth.scenario_generator import derive_state_assertions

    initial = {"claims": []}
    final = {
        "claims": [
            {
                "claim_id": "CLM-7",
                "policy_number": "HM-1",
                "injuries": False,
                "description": "Reversed into a bollard, rear bumper dented",
                "inspection_booked": None,
            }
        ]
    }
    actions = [
        {
            "tool": "open_claim",
            "args": {
                "policy_number": "HM-1",
                "injuries": False,
                "description": "Reversed into a bollard, rear bumper dented",
            },
        }
    ]
    ops = {a.path: a.op for a in derive_state_assertions(initial, final, actions)}
    assert ops["claims.0.policy_number"] is StateOp.EQ and ops["claims.0.injuries"] is StateOp.EQ
    assert ops["claims.0.claim_id"] is StateOp.EXISTS  # generated by the backend
    assert ops["claims.0.description"] is StateOp.EXISTS  # free text may be paraphrased
    assert ops["claims.__len__"] is StateOp.EQ
    assert "claims.0.inspection_booked" not in ops  # a null placeholder is not "absent"

    oracle = Oracle(
        state=derive_state_assertions(initial, final, actions),
        unchanged=["*"],
        tolerate_new_fields=True,
    )
    rerun = {
        "claims": [
            {
                "claim_id": "CLM-9",
                "policy_number": "HM-1",
                "injuries": False,
                "description": "Hit a bollard while reversing",
                "opened_on": "2025-03-10",  # extra field the backend added this time
                "inspection_booked": False,
            }
        ]
    }
    checks, unexpected = evaluate_state(oracle, initial, rerun)
    assert all(c.passed for c in checks) and not unexpected, checks
    wrong = {"claims": [dict(rerun["claims"][0], policy_number="HM-2")]}
    checks, _ = evaluate_state(oracle, initial, wrong)
    assert not all(c.passed for c in checks)
    # deterministic backends keep exact assertions
    det = {a.path: a.op for a in derive_state_assertions(initial, final)}
    assert det["claims.0.claim_id"] is StateOp.EQ and det["claims.0.policy_number"] is StateOp.EQ
    assert det["claims.0.description"] is StateOp.EXISTS  # agent's own words
    removed = derive_state_assertions({"a": {"x": 1, "y": 2}}, {"a": {"y": 2}})
    assert [(a.path, a.op) for a in removed] == [("a.x", StateOp.ABSENT)]


def test_simulated_backend_keeps_only_assertions_a_second_replay_reproduces(make_client):
    """Loan rehearsal, 9 Oct: a simulated backend wrote an audit-log record only sometimes."""
    _pack, d = _domain()

    class Flaky:
        deterministic = False

        def seed_state(self):
            return {"cards": {"K-1": {"status": "active"}}, "audit": []}

        def replay(self, initial, actions, salt=""):
            final = json.loads(json.dumps(initial))
            final["cards"]["K-1"]["status"] = "frozen"
            if not salt:
                final["audit"].append({"card": "K-1"})  # only in the first replay
            return True, final, []

    llm = make_client(_scenario_provider(GOOD))
    gen = ScenarioGenerator(d, llm, backend=Flaky(), max_attempts=1)
    out = gen.generate(1, categories=[ScenarioCategory.HAPPY_PATH])
    assert len(out) == 1, gen.rejected
    paths = {a.path for a in out[0].oracle.state}
    assert "cards.K-1.status" in paths
    assert not any(p.startswith("audit") for p in paths)
    assert out[0].oracle.unchanged == []


def test_state_patch_addresses_list_records_by_id():
    from builder.testsynth.statediff import get_path

    db = {"musteriler": [{"musteri_id": "C001", "durum": "aktif"}, {"musteri_id": "C003"}]}
    set_path(db, "musteriler.C003.durum", "pasif")
    assert db["musteriler"][1]["durum"] == "pasif"
    assert get_path(db, "musteriler.C001.durum") == "aktif"
    set_path(db, "musteriler.2", {"musteri_id": "C004"})
    assert len(db["musteriler"]) == 3
