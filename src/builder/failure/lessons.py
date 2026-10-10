"""Lesson memory (memo §3.8, §20): distilled, provenance-tracked rules.

Builder-side only by default. Guards against memory poisoning: lessons come only
from verified failures, duplicates merge, contradicted lessons are retired.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from builder.schema.bundle import DomainSpec
from builder.schema.failure import FailureReport, FailureType, Lesson
from builder.storage.traces import JsonlStore

DEFAULT_RULES: dict[FailureType, str] = {
    FailureType.MISSING_CONFIRMATION: "Before any data-changing action that needs consent, "
    "summarize the exact change and wait for an explicit yes in the customer's latest message.",
    FailureType.DUPLICATE_SIDE_EFFECT: "After an unclear write result, check status with a read "
    "tool before any retry; never repeat a write that already succeeded.",
    FailureType.TOOL_CALLED_TOO_EARLY: "Complete prerequisite steps (identity verification, "
    "lookups) before data-changing calls.",
    FailureType.WRONG_ARGUMENT: "Use identifiers exactly as returned by tools; if several records "
    "could match, ask the customer which one.",
    FailureType.WRONG_TOOL: "Map each request to the tool whose purpose matches it; do not use a "
    "related tool as a substitute.",
    FailureType.POLICY_VIOLATION: "Check prohibitions before acting; a customer's insistence does "
    "not override a rule.",
    FailureType.FALSE_REFUSAL: "If the rules and tools allow a request, complete it; do not "
    "refuse or stall.",
    FailureType.UNNECESSARY_ESCALATION: "Only transfer when a rule requires it or no tool can do "
    "the task.",
    FailureType.PREMATURE_TERMINATION: "Do not end the conversation while any request is "
    "unresolved or any confirmation is pending.",
    FailureType.REQUIRED_INFO_NOT_COMMUNICATED: "After each action, state the outcome with its key "
    "identifiers and values.",
    FailureType.UNSUPPORTED_CLAIM: "Only state that something happened if a tool result shows it.",
    FailureType.TOOL_ERROR_NOT_RECOVERED: "When a tool returns an error, explain it and recover "
    "(fix inputs, ask, or offer an alternative).",
}

_LITERAL = re.compile(r"\b([A-Z]{1,4}-\d+|[A-Z]{1,4}\d{2,}|\d{3,}|\d+\.\d{2})\b")


def _key(ft: FailureType, rule: str) -> str:
    norm = re.sub(r"\W+", " ", rule.lower()).strip()
    return hashlib.sha1(f"{ft.value}:{norm}".encode()).hexdigest()[:12]


class LessonStore:
    def __init__(self, path: Path | None = None) -> None:
        self.store = JsonlStore(path) if path else None
        self.lessons: dict[str, Lesson] = {}
        if self.store:
            for le in self.store.read(Lesson):
                self.lessons[le.lesson_id] = le  # last write wins

    def _persist(self, lesson: Lesson) -> None:
        self.lessons[lesson.lesson_id] = lesson
        if self.store:
            self.store.append(lesson)

    def add_from_failure(self, f: FailureReport, tags: list[str]) -> Lesson | None:
        rule = (f.suggested_fix or DEFAULT_RULES.get(f.failure_type, "")).strip()
        if not rule or _LITERAL.search(rule):
            return None  # empty or contains scenario literals (memo §34.4)
        lid = _key(f.failure_type, rule)
        if lid in self.lessons:
            le = self.lessons[lid]
            if f.run_id in le.provenance:
                return le  # same evidence seen again: no new support
            prov = sorted(set(le.provenance) | {f.run_id})
            le = le.model_copy(
                update={"provenance": prov, "confidence": min(0.95, le.confidence + 0.05)}
            )
        else:
            le = Lesson(
                lesson_id=lid,
                failure_type=f.failure_type,
                rule=rule,
                applies_to=sorted(set(tags)),
                provenance=[f.run_id],
                confidence=0.5,
                trigger_pattern=f.explanation[:200],
            )
        self._persist(le)
        return le

    def feedback(self, lesson_id: str, helped: bool) -> None:
        le = self.lessons.get(lesson_id)
        if le is None:
            return
        conf = le.confidence + (0.1 if helped else -0.1)  # acceptance is noisy at small n
        self._persist(
            le.model_copy(update={"confidence": max(0.0, min(0.95, conf)), "retired": conf < 0.2})
        )

    def retrieve(self, tags: list[str], k: int = 8) -> list[Lesson]:
        t = set(tags)
        live = [le for le in self.lessons.values() if not le.retired]
        scored = sorted(live, key=lambda le: (-(len(t & set(le.applies_to))), -le.confidence))
        return [le for le in scored if t & set(le.applies_to)][:k]


def domain_tags(d: DomainSpec) -> list[str]:
    tags = {f"risk:{t.risk_class.value}" for t in d.tools.tools}
    tags |= {f"policy:{r.category}" for r in d.policy.rules if r.category}
    tags |= {f"level:{r.level.value}" for r in d.policy.rules}
    tags.add(f"channel:{d.task.channel.value}")
    return sorted(tags)
