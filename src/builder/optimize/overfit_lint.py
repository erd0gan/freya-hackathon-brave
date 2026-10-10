"""Reject prompt mutations that memorize practice data (memo §34.4–34.5, §43)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from builder.schema.bundle import DomainSpec
from builder.schema.scenario import ScenarioSpec

_ID_LIKE = re.compile(r"\b(?:[A-Z]{1,5}-\d+|[A-Z]{1,5}\d{2,}|\d{3,}(?:\.\d+)?|\d+\.\d{2})\b")


def _strings(v: Any) -> Iterable[str]:
    if isinstance(v, dict):
        for x in v.values():
            yield from _strings(x)
    elif isinstance(v, list):
        for x in v:
            yield from _strings(x)
    elif isinstance(v, str | int | float) and not isinstance(v, bool):
        yield str(v)


def domain_text(domain: DomainSpec) -> str:
    """Everything the task definition itself contains — literals from here are legitimate."""
    import json

    return " ".join(
        [
            domain.task.description,
            *(f"{r.id} {r.description} {' '.join(r.source_refs)}" for r in domain.policy.rules),
            # The whole compiled tool spec, incl. compiler-written format examples
            # ('ssn_last4=4 digits, e.g. "0427"' failed every candidate's audit, 9 Oct).
            *(
                json.dumps(t.model_dump(mode="json"), ensure_ascii=False)
                for t in domain.tools.tools
            ),
            *(d.text for d in domain.knowledge.documents),
        ]
    )


def practice_literals(scenarios: list[ScenarioSpec], domain: DomainSpec) -> set[str]:
    """Values that appear in scenarios but NOT in the task/policy/tool definitions."""
    allowed_text = domain_text(domain).lower()  # same whitelist as lint_text
    lits: set[str] = set()
    for s in scenarios:
        for v in _strings(s.persona.hidden_facts):
            v = v.strip()
            if len(v) >= 3 and v.lower() not in allowed_text:
                lits.add(v.lower())
        for a in s.oracle.reference_actions:
            for v in _strings(a.get("args", {})):
                if len(v) >= 3 and v.lower() not in allowed_text:
                    lits.add(v.lower())
    return lits


def lint_text(text: str, literals: set[str], domain: DomainSpec) -> list[str]:
    problems: list[str] = []
    low = text.lower()
    for lit in sorted(literals):
        if re.search(rf"(?<![\w-]){re.escape(lit)}(?![\w-])", low):
            problems.append(f"contains practice literal '{lit}'")
    allowed = domain_text(domain)
    for m in _ID_LIKE.findall(text):
        if m not in allowed:
            problems.append(f"contains id/amount-like literal '{m}'")
    if "{{" in text or "TODO" in text:
        problems.append("unresolved placeholder")
    return problems
