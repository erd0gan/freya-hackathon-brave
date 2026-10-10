from __future__ import annotations

from test_eval import GOOD, SCEN, TOOLS, _ev, _trace

from builder.eval.evaluator import Evaluator
from builder.failure.analyzer import cluster, component_pressure
from builder.failure.first_deviation import localize
from builder.failure.lessons import LessonStore
from builder.failure.regression import regression_suite
from builder.schema.failure import FailureType as F
from builder.schema.trace import EndReason
from builder.schema.trace import EventKind as K

FROZEN = {("cards", "K-1", "status"): "frozen"}


def _diag(events, changes, end=EndReason.AGENT_ENDED):
    tr = _trace(events, changes)
    tr.end_reason = end
    res = Evaluator(TOOLS).evaluate(tr, SCEN)
    assert not res.success
    return localize(tr, SCEN, res, TOOLS)


def test_injected_failures_are_classified():
    """PLAN Faz 7 acceptance: ≥5 of 6 injected failure types classified correctly."""
    no_confirm = [
        e for e in GOOD if not e.content.startswith("Shall I freeze") and e.content != "yes"
    ]
    dup = [
        *GOOD,
        _ev(2, K.TOOL_CALL, tool="freeze_card", args={"card_id": "K-1"}),
        _ev(2, K.TOOL_RESULT, tool="freeze_card", ok=True, result={"status": "frozen"}),
    ]
    wrong_tool = [
        *GOOD,
        _ev(3, K.USER, content="ok"),
        _ev(3, K.TOOL_CALL, tool="update_address", args={"customer_id": "C-1001", "address": "x"}),
        _ev(3, K.TOOL_RESULT, tool="update_address", ok=True, result={}),
    ]
    error = [
        *GOOD[:7],
        _ev(2, K.TOOL_CALL, tool="freeze_card", args={"card_id": "K-1"}),
        _ev(2, K.TOOL_RESULT, tool="freeze_card", ok=False, result={"error": "500"}),
    ]
    timeout = GOOD[:6]
    idle = [_ev(1, K.USER, content="I lost my card"), _ev(1, K.AGENT, content="Sorry, no.")]
    cases = [
        (no_confirm, FROZEN, EndReason.AGENT_ENDED, F.MISSING_CONFIRMATION),
        (dup, FROZEN, EndReason.AGENT_ENDED, F.DUPLICATE_SIDE_EFFECT),
        (
            wrong_tool,
            {**FROZEN, ("customers", "C-1001", "address"): "x"},
            EndReason.AGENT_ENDED,
            F.WRONG_TOOL,
        ),
        (error, {}, EndReason.AGENT_ENDED, F.TOOL_ERROR_NOT_RECOVERED),
        (timeout, {}, EndReason.MAX_TURNS, F.TIMEOUT),
        (idle, {}, EndReason.AGENT_ENDED, F.FALSE_REFUSAL),
    ]
    got = [_diag(ev, ch, end).failure_type for ev, ch, end, _ in cases]
    correct = sum(g is exp for g, (*_, exp) in zip(got, cases, strict=True))
    assert correct >= 5, got
    reports = [_diag(ev, ch, end) for ev, ch, end, _ in cases]
    assert cluster(reports)[0][1] >= 1
    assert component_pressure(reports)["mutation_guard"] > 0


def test_regression_only_from_train():
    rep = _diag(GOOD[:6], {}, EndReason.MAX_TURNS)
    val = SCEN.model_copy(update={"split": "validation"})
    assert regression_suite([(val, rep)]) == []
    out = regression_suite([(SCEN, rep), (SCEN, rep)])
    assert len(out) == 1 and out[0].scenario_id.endswith("~reg0")
    assert out[0].persona.style != SCEN.persona.style


def test_lessons_merge_reject_literals_and_retire(tmp_path):
    store = LessonStore(tmp_path / "lessons.jsonl")
    rep = _diag(GOOD[:6], {}, EndReason.MAX_TURNS)
    rep1 = rep.model_copy(
        update={"failure_type": F.MISSING_CONFIRMATION, "suggested_fix": "", "run_id": "r1"}
    )
    a = store.add_from_failure(rep1, ["risk:IRREVERSIBLE_WRITE"])
    b = store.add_from_failure(
        rep1.model_copy(update={"run_id": "r2"}), ["risk:IRREVERSIBLE_WRITE"]
    )
    assert a and b and a.lesson_id == b.lesson_id and b.provenance == ["r1", "r2"]
    lit = rep1.model_copy(update={"suggested_fix": "Always freeze card K-1 first"})
    assert store.add_from_failure(lit, ["x"]) is None
    assert store.retrieve(["risk:IRREVERSIBLE_WRITE"])[0].lesson_id == a.lesson_id
    for _ in range(4):
        store.feedback(a.lesson_id, helped=False)
    assert store.retrieve(["risk:IRREVERSIBLE_WRITE"]) == []
    assert LessonStore(tmp_path / "lessons.jsonl").lessons[a.lesson_id].retired
