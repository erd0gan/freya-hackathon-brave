"""Mutation operators (memo §18.4, §3.12) and the reflective block mutator (GEPA §3.9)."""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, Field

from builder.llm import LLMClient, Message, Role
from builder.schema.agent import AgentSpec
from builder.schema.failure import FailureReport, Lesson
from builder.synth import fewshot

OPERATORS: dict[str, str] = {
    "clarify": "Make the existing instruction more precise and unambiguous.",
    "compress": "Say the same thing in fewer words; remove redundancy.",
    "add_invariant": "Add one invariant that must always hold.",
    "add_negative_example": "Add a short 'do not do X' example of the observed mistake (generic).",
    "add_positive_example": "Add a short example of the correct behaviour (generic).",
    "reorder_priority": "Reorder instructions so the most violated one comes first.",
    "add_precondition": "Add an explicit precondition check before the risky step.",
    "add_recovery_rule": "Add what to do when a tool errors or the outcome is unclear.",
    "to_checklist": "Turn prose into a short numbered checklist.",
    "remove_contradiction": "Remove or reconcile lines that contradict each other or cause "
    "over-caution.",
    "tighten_completion": "Tighten when the conversation may be considered complete.",
    "generalize": "Rewrite so it generalizes to any similar domain (no specific names/ids).",
}

# Which operators tend to fit which failure (prior; adapted by OperatorStats).
OPERATOR_PRIOR: dict[str, list[str]] = {
    "MISSING_CONFIRMATION": ["add_precondition", "to_checklist", "add_negative_example"],
    "DUPLICATE_SIDE_EFFECT": ["add_recovery_rule", "add_invariant"],
    "TOOL_CALLED_TOO_EARLY": ["add_precondition", "reorder_priority"],
    "WRONG_ARGUMENT": ["clarify", "add_invariant"],
    "WRONG_TOOL": ["clarify", "add_positive_example"],
    "POLICY_VIOLATION": ["reorder_priority", "add_invariant", "add_negative_example"],
    "FALSE_REFUSAL": ["remove_contradiction", "add_positive_example"],
    "UNNECESSARY_ESCALATION": ["remove_contradiction", "clarify"],
    "PREMATURE_TERMINATION": ["tighten_completion"],
    "TIMEOUT": ["compress", "remove_contradiction"],
    "REQUIRED_INFO_NOT_COMMUNICATED": ["add_positive_example", "clarify"],
    "UNSUPPORTED_CLAIM": ["add_invariant", "add_negative_example"],
    "TOOL_ERROR_NOT_RECOVERED": ["add_recovery_rule"],
}


class OperatorStats(BaseModel):
    """PromptBreeder-lite: track which operators produced accepted children."""

    tried: dict[str, int] = Field(default_factory=dict)
    accepted: dict[str, int] = Field(default_factory=dict)

    def record(self, op: str, ok: bool) -> None:
        self.tried[op] = self.tried.get(op, 0) + 1
        if ok:
            self.accepted[op] = self.accepted.get(op, 0) + 1

    def rank(self, candidates: list[str]) -> list[str]:
        def score(op: str) -> float:  # Laplace-smoothed acceptance rate
            return (self.accepted.get(op, 0) + 1) / (self.tried.get(op, 0) + 2)

        return sorted(candidates, key=lambda o: -score(o))


class BlockRewrite(BaseModel):
    new_text: str
    rationale: str = ""


REFLECT_PROMPT = """You improve ONE section of a customer-service agent's system prompt.
You get the current section, the failures attributed to it (with evidence), relevant lessons,
and a mutation operator to apply. Produce the full replacement text for this section only.
Constraints:
- Fix the root cause in a way that generalizes to unseen customers and domains.
- Never include specific ids, names, amounts, dates or phrasings from the evidence.
- Do not make the agent more cautious than the business rules require (false refusals and
  needless transfers also fail).
- Keep it concise; do not exceed 1.5x the current length unless the section is empty.
Return JSON only."""


def child_id(parent: AgentSpec, tag: str) -> str:
    h = hashlib.sha1(f"{parent.candidate_id}:{tag}".encode()).hexdigest()[:6]
    return f"{parent.candidate_id}.{h}"


def reflect_block(
    llm: LLMClient,
    parent: AgentSpec,
    block: str,
    failures: list[FailureReport],
    lessons: list[Lesson],
    operator: str,
    salt: str = "",
) -> BlockRewrite:
    current = parent.system_prompt_sections.get(block, "")
    evidence = "\n".join(
        f"- {f.failure_type.value}: {f.explanation} | proposed fix: {f.suggested_fix}"
        for f in failures[:8]
    )
    lesson_txt = "\n".join(f"- {le.rule}" for le in lessons[:6]) or "(none)"
    body = (
        f"SECTION NAME: {block}\nCURRENT TEXT:\n{current or '(empty)'}\n\n"
        f"FAILURES:\n{evidence}\n\nLESSONS:\n{lesson_txt}\n\n"
        f"OPERATOR: {operator} — {OPERATORS[operator]}"
    )
    return llm.complete_structured(
        Role.OPTIMIZER,
        BlockRewrite,
        [Message(role="system", content=REFLECT_PROMPT), Message(role="user", content=body)],
        cache_salt=salt,
    )


def apply_block(parent: AgentSpec, block: str, text: str, tag: str) -> AgentSpec:
    sections = dict(parent.system_prompt_sections)
    if block == "tool_descriptions":
        block = "tool_use_policy"
    sections[block] = text.strip()
    return parent.model_copy(
        update={
            "candidate_id": child_id(parent, tag),
            "parent_ids": [parent.candidate_id],
            "system_prompt_sections": sections,
            "lineage_note": f"{tag}",
        },
        deep=True,
    )


def toggle_fewshot(parent: AgentSpec, family: str) -> AgentSpec:
    fams = [e.family for e in parent.few_shot_examples]
    fams = [f for f in fams if f != family] if family in fams else [*fams, family]
    return parent.model_copy(
        update={
            "candidate_id": child_id(parent, f"fewshot:{family}"),
            "parent_ids": [parent.candidate_id],
            "few_shot_examples": fewshot.examples(fams),
            "lineage_note": f"toggle few-shot {family}",
        },
        deep=True,
    )
