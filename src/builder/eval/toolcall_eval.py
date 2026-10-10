"""Tool-call assertions, Freya unit-test style (memo §55.3, §57.2)."""

from __future__ import annotations

from typing import Any

from builder.eval.trace_view import ExecutedCall
from builder.schema.eval import CheckResult
from builder.schema.scenario import ArgMatch, ToolCallAssertion


def _norm(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, int | float):
        return round(float(v), 6)
    if isinstance(v, str):
        s = v.strip().lower()
        try:
            return round(float(s), 6)
        except ValueError:
            return s
    return v


def args_match(expected: dict[str, Any], actual: dict[str, Any], mode: ArgMatch) -> bool:
    if mode is ArgMatch.EXACT and set(expected) != set(actual):
        return False
    return all(k in actual and _norm(actual[k]) == _norm(v) for k, v in expected.items())


def evaluate_tool_calls(
    assertions: list[ToolCallAssertion], calls: list[ExecutedCall]
) -> list[CheckResult]:
    out: list[CheckResult] = []
    for a in assertions:
        n = sum(1 for c in calls if c.tool == a.tool and args_match(a.args, c.args, a.match))
        if a.negated:
            # An attempt the backend rejected changed nothing; the agent often cannot know
            # (e.g. an earlier order no tool shows) without trying (live run 9 Oct).
            n = sum(
                1
                for c in calls
                if c.tool == a.tool and c.ok is not False and args_match(a.args, c.args, a.match)
            )
            ok = n == 0
            detail = f"{a.tool} called {n}x but must not be"
        else:
            ok = n >= a.min_count and (a.max_count is None or n <= a.max_count)
            detail = f"{a.tool}{a.args or ''} called {n}x"
        name = f"{'not_' if a.negated else ''}call:{a.tool}"
        out.append(CheckResult(name=name, passed=ok, detail=detail, evaluator="tool_call"))
    return out
