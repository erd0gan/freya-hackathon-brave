from __future__ import annotations

from builder.schema.agent import AgentSpec, Architecture, FewShotExample
from builder.schema.budget import OptimizationBudget
from builder.schema.policy import PolicyLevel, PolicyRule, PolicySpec
from builder.schema.scenario import Persona, ScenarioSpec
from builder.schema.state import MutationCheck
from builder.schema.tool import EdgeKind, RiskClass, ToolEdge, ToolGraph, ToolSpec, riskier


def test_policy_ordering_and_lookup():
    spec = PolicySpec(
        rules=[
            PolicyRule(id="H", description="be nice", level=PolicyLevel.HELPFULNESS),
            PolicyRule(
                id="P",
                description="never X",
                level=PolicyLevel.PROHIBITION,
                affected_tools=["x"],
                source_refs=["doc#1"],
            ),
        ]
    )
    assert [r.id for r in spec.ordered()] == ["P", "H"]
    assert [r.id for r in spec.for_tool("x")] == ["P"]
    assert [r.id for r in spec.unsourced()] == ["H"]


def test_tool_graph_depth_and_risk():
    g = ToolGraph(
        tools=[ToolSpec(name=n) for n in ("auth", "lookup", "freeze")],
        edges=[
            ToolEdge(src="auth", dst="lookup", kind=EdgeKind.REQUIRES_BEFORE),
            ToolEdge(src="lookup", dst="freeze", kind=EdgeKind.REQUIRES_BEFORE),
        ],
    )
    assert g.depth() == 3
    assert (
        riskier(RiskClass.READ_ONLY, RiskClass.IRREVERSIBLE_WRITE) is RiskClass.IRREVERSIBLE_WRITE
    )


def test_mutation_check_approval():
    ok = MutationCheck(tool_name="t")
    assert ok.approved
    bad = MutationCheck(tool_name="t", confirmation_satisfied=False, reasons=["no consent"])
    assert not bad.approved and "consent" in bad.reason


def test_agent_prompt_render_orders_blocks():
    a = AgentSpec(
        candidate_id="c",
        architecture=Architecture.GUARDED_REACT,
        system_prompt_sections={"mutation_guard": "G", "role": "R", "custom": "Z"},
        few_shot_examples=[FewShotExample(family="f", text="T")],
    )
    text = a.render_system_prompt()
    assert text.index("## Role") < text.index("## Mutation Guard") < text.index("## Custom")
    assert "### Example (f)" in text


def test_roundtrip_scenario():
    s = ScenarioSpec(scenario_id="s1", persona=Persona(prompt="I lost my card"))
    assert ScenarioSpec.model_validate_json(s.model_dump_json()) == s


def test_budget_exhaustion():
    b = OptimizationBudget(max_simulations=3)
    assert b.can_simulate(3)
    b.charge(sims=3)
    assert not b.can_continue() and b.exhausted_reason() == "simulations"
