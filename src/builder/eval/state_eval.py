"""Deterministic state oracle (memo §3.1–3.2, §16): outcome over trajectory."""

from __future__ import annotations

from typing import Any

from builder.schema.eval import CheckResult
from builder.schema.scenario import Oracle, StateAssertion, StateOp
from builder.testsynth.statediff import diff, get_path


def _eq(a: Any, b: Any) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float):
        return abs(float(a) - float(b)) < 1e-6
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().lower() == b.strip().lower()
    return bool(a == b)


def check_assertion(state: dict[str, Any], a: StateAssertion) -> tuple[bool, str]:
    try:
        v = get_path(state, a.path)
        present = True
    except (KeyError, IndexError, ValueError):
        v, present = None, False
    if a.op is StateOp.ABSENT:
        return not present, f"{a.path} should be absent"
    if a.op is StateOp.EXISTS:
        return present, f"{a.path} should exist"
    if not present:
        return False, f"{a.path} missing (expected {a.op.value} {a.value!r})"
    if a.op is StateOp.EQ:
        return _eq(v, a.value), f"{a.path}={v!r}, expected {a.value!r}"
    if a.op is StateOp.NE:
        return not _eq(v, a.value), f"{a.path}={v!r}, expected != {a.value!r}"
    if a.op is StateOp.CONTAINS:
        return a.value in v, f"{a.path} should contain {a.value!r}"
    if a.op is StateOp.COUNT_EQ:
        return len(v) == a.value, f"len({a.path})={len(v)}, expected {a.value}"
    return False, f"unknown op {a.op}"


def evaluate_state(
    oracle: Oracle, initial: dict[str, Any], final: dict[str, Any] | None
) -> tuple[list[CheckResult], list[str]]:
    """Returns (checks, unexpected_change_paths)."""
    if final is None or (not oracle.state and not oracle.unchanged):
        return [], []
    checks = []
    for a in oracle.state:
        ok, detail = check_assertion(final, a)
        checks.append(
            CheckResult(name=f"state:{a.path}", passed=ok, detail=detail, evaluator="state")
        )
    unexpected: list[str] = []
    if "*" in oracle.unchanged:
        expected = {a.path for a in oracle.state}
        changed_records = {p.rsplit(".", 1)[0] for p in expected if "." in p}
        for path in diff(initial, final):
            if path in expected:
                continue
            if oracle.tolerate_new_fields and path.rsplit(".", 1)[0] in changed_records:
                continue
            unexpected.append(path)
        checks.append(
            CheckResult(
                name="state:no_unexpected_changes",
                passed=not unexpected,
                detail=", ".join(unexpected[:10]),
                evaluator="state",
            )
        )
    else:
        for path in oracle.unchanged:
            try:
                same = _eq(get_path(initial, path), get_path(final, path))
            except (KeyError, IndexError, ValueError):
                same = False
            if not same:
                unexpected.append(path)
            checks.append(CheckResult(name=f"unchanged:{path}", passed=same, evaluator="state"))
    return checks, unexpected
