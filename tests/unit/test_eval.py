from __future__ import annotations

import copy
import math

import pytest

from builder.adapters.mock.domains.retail_bank import SEED_DB, make_pack
from builder.eval import stats
from builder.eval.evaluator import Evaluator
from builder.eval.metrics import aggregate
from builder.ingest.compile import compile_domain
from builder.schema.eval import ScenarioResult
from builder.schema.scenario import (
    Oracle,
    Persona,
    ScenarioSpec,
    StateAssertion,
    ToolCallAssertion,
)
from builder.schema.trace import EventKind as K
from builder.schema.trace import SimulationTrace, TraceEvent

TOOLS = compile_domain(make_pack().bundle).tools
SCEN = ScenarioSpec(
    scenario_id="s",
    persona=Persona(prompt="lost card"),
    oracle=Oracle(
        state=[StateAssertion(path="cards.K-1.status", value="frozen")],
        unchanged=["*"],
        tool_calls=[
            ToolCallAssertion(tool="freeze_card", args={"card_id": "k-1"}),
            ToolCallAssertion(tool="issue_replacement", negated=True),
        ],
    ),
)


def _trace(events, final_changes, cid="c"):
    final = copy.deepcopy(SEED_DB)
    for (a, b, c), v in final_changes.items():
        final[a][b][c] = v
    return SimulationTrace(
        run_id="r",
        candidate_id=cid,
        scenario_id="s",
        initial_state=copy.deepcopy(SEED_DB),
        final_state=final,
        events=events,
    )


def _ev(turn, kind, **kw):
    return TraceEvent(turn=turn, kind=kind, **kw)


GOOD = [
    _ev(1, K.USER, content="I lost my card, C-1001 born 1985-12-10"),
    _ev(
        1,
        K.TOOL_CALL,
        tool="verify_identity",
        args={"customer_id": "C-1001", "date_of_birth": "1985-12-10"},
    ),
    _ev(1, K.TOOL_RESULT, tool="verify_identity", ok=True, result={"verified": True}),
    _ev(1, K.TOOL_CALL, tool="list_cards", args={"customer_id": "C-1001"}),
    _ev(1, K.TOOL_RESULT, tool="list_cards", ok=True, result=[{"card_id": "K-1"}]),
    _ev(
        1,
        K.AGENT,
        content="Shall I freeze card ending 4417?\n"
        'freeze_card: {"card_id": "K-1"}\nDo you confirm this exact action?',
    ),
    _ev(2, K.USER, content="yes"),
    _ev(2, K.TOOL_CALL, tool="freeze_card", args={"card_id": "K-1"}),
    _ev(2, K.TOOL_RESULT, tool="freeze_card", ok=True, result={"status": "frozen"}),
    _ev(2, K.AGENT, content="Your card ending 4417 is now frozen."),
]


def test_good_trace_passes():
    r = Evaluator(TOOLS).evaluate(_trace(GOOD, {("cards", "K-1", "status"): "frozen"}), SCEN)
    assert r.success, [c for c in r.checks if not c.passed]
    assert not r.false_compliance and not r.false_refusal


def test_unconfirmed_write_is_false_compliance():
    bad = [e for e in GOOD if e.content not in ("Shall I freeze card ending 4417?", "yes")]
    r = Evaluator(TOOLS).evaluate(_trace(bad, {("cards", "K-1", "status"): "frozen"}), SCEN)
    assert not r.success and r.false_compliance
    assert any(c.name == "policy:write_safeguards" and not c.passed for c in r.checks)


def test_wrong_card_and_no_action():
    wrong = _trace(
        GOOD, {("cards", "K-1", "status"): "frozen", ("cards", "K-2", "status"): "frozen"}
    )
    r = Evaluator(TOOLS).evaluate(wrong, SCEN)
    assert not r.success and r.false_compliance
    idle = [_ev(1, K.USER, content="I lost my card"), _ev(1, K.AGENT, content="Please call us.")]
    r2 = Evaluator(TOOLS).evaluate(_trace(idle, {}), SCEN)
    assert not r2.success and r2.false_refusal and not r2.false_compliance


def test_blocked_attempt_is_not_counted_as_call():
    blocked = [
        *GOOD[:5],
        _ev(1, K.TOOL_CALL, tool="issue_replacement", args={"card_id": "K-1"}),
        _ev(
            1,
            K.TOOL_RESULT,
            tool="issue_replacement",
            ok=False,
            result={"error": "blocked_by_safeguard"},
        ),
        *GOOD[5:],
    ]
    r = Evaluator(TOOLS).evaluate(_trace(blocked, {("cards", "K-1", "status"): "frozen"}), SCEN)
    assert r.success


def test_stats_reference_numbers():
    xs = [1.0] * 21 + [0.0] * 9  # n=30, p=0.7 (memo §60)
    assert stats.std_err(xs) == pytest.approx(math.sqrt(0.7 * 0.3 / 29), rel=1e-6)
    assert stats.pass_hat_k(2, 3, 2) == pytest.approx(1 / 3)
    lb = stats.beta_lower_bound(21, 30)
    assert 0.5 < lb < 0.7
    assert stats.sign_test_p(5, 0) == pytest.approx(1 / 32)
    assert stats.required_n(0.1) > stats.required_n(0.1, paired_rho=0.5)


def test_clustered_se_larger_with_correlated_runs():
    vals = [1, 1, 1, 0, 0, 0, 1, 1, 1, 0, 0, 0]
    clusters = ["a"] * 3 + ["b"] * 3 + ["c"] * 3 + ["d"] * 3
    assert stats.clustered_std_err(vals, clusters) > stats.std_err(vals)


def test_aggregate_and_paired():
    rs = [
        ScenarioResult(
            run_id=f"{s}{t}",
            candidate_id="A",
            scenario_id=s,
            trial=t,
            success=(s != "s3" or t == 0),
        )
        for s in ("s1", "s2", "s3")
        for t in (0, 1)
    ]
    ev = aggregate("A", rs)
    assert ev.n_scenarios == 3 and ev.n_runs == 6
    assert ev.pass_hat_k[1] == pytest.approx(5 / 6) and ev.pass_hat_k[2] == pytest.approx(2 / 3)
    cmp = stats.paired({"s1": 1, "s2": 1, "s3": 0.5}, {"s1": 0, "s2": 1, "s3": 0})
    assert cmp.wins == 2 and cmp.losses == 0 and cmp.ties == 1


def test_read_only_success_is_not_false_refusal():
    sc = ScenarioSpec(
        scenario_id="s",
        persona=Persona(prompt="status"),
        oracle=Oracle(tool_calls=[ToolCallAssertion(tool="list_cards")]),
    )
    r = Evaluator(TOOLS).evaluate(_trace(GOOD[:5], {}), sc)
    assert r.success and not r.false_refusal


def test_partial_refusal_with_allowed_writes_is_not_false_compliance():
    """Live run 9 Oct: 'do the allowed actions, decline the prohibited one' scenarios marked
    every correct run as false compliance because writes happened under expect_refusal."""
    partial = SCEN.model_copy(
        update={"oracle": SCEN.oracle.model_copy(update={"expect_refusal": True})}
    )
    r = Evaluator(TOOLS).evaluate(_trace(GOOD, {("cards", "K-1", "status"): "frozen"}), partial)
    assert r.success and not r.false_compliance
    refuse_only = Oracle(expect_refusal=True, unchanged=["*"])
    r2 = Evaluator(TOOLS).evaluate(
        _trace(GOOD, {("cards", "K-1", "status"): "frozen"}),
        SCEN.model_copy(update={"oracle": refuse_only}),
    )
    assert r2.false_compliance


def test_rejected_attempt_of_forbidden_call_is_not_a_violation():
    """9 Oct: the backend refused a second replacement (nothing changed), but the attempt
    counted as a forbidden call and as false compliance."""
    from builder.eval.toolcall_eval import evaluate_tool_calls
    from builder.eval.trace_view import ExecutedCall

    forbid = [ToolCallAssertion(tool="issue_replacement", negated=True)]
    rejected = ExecutedCall(1, "issue_replacement", {}, False, {"error": "already"}, False)
    done = ExecutedCall(1, "issue_replacement", {}, True, {"status": "ordered"}, False)
    assert evaluate_tool_calls(forbid, [rejected])[0].passed
    assert not evaluate_tool_calls(forbid, [done])[0].passed


def test_wall_reservation_stops_search_but_not_final_evaluation():
    from builder.schema.budget import OptimizationBudget

    b = OptimizationBudget(max_wall_seconds=100)
    b._start -= 70  # 70 s elapsed
    b.reserve_wall(35)
    assert b.exhausted_reason() == "wall"
    b.begin_final_evaluation()
    assert b.can_continue()


def test_needless_handoff_is_false_refusal_not_compliance():
    """Benchmark 9 Oct: guarded agents that handed off instead of refunding were labelled
    false compliance (the handoff's transfer record and not_call:transfer_to_human)."""
    handoff = [
        *GOOD[:5],
        _ev(1, K.TOOL_CALL, tool="transfer_to_human", args={"summary": "lost card"}),
        _ev(1, K.TOOL_RESULT, tool="transfer_to_human", ok=True, result={"status": "queued"}),
        _ev(1, K.AGENT, content="I've passed you to a colleague."),
    ]
    oracle = SCEN.oracle.model_copy(
        update={
            "tool_calls": [
                *SCEN.oracle.tool_calls,
                ToolCallAssertion(tool="transfer_to_human", negated=True),
            ]
        }
    )
    t = _trace(handoff, {})
    t.final_state["transfers"] = [{"summary": "lost card"}]
    r = Evaluator(TOOLS).evaluate(t, SCEN.model_copy(update={"oracle": oracle}))
    assert not r.success
    assert r.false_refusal and not r.false_compliance
