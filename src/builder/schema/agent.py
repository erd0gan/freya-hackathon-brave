"""AgentSpec: the typed agent program the builder searches over (memo §5.5, §8, §14)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Architecture(StrEnum):
    ONE_SHOT = "one_shot"  # A — fallback (§8A)
    PLATFORM_DEFAULT = "platform_default"  # B0 (§55.1)
    GUARDED_REACT = "guarded_react"  # B / C1
    GUARDED_REACT_THINK = "guarded_react_think"  # C2 (§3.6)
    PLANNER_EXECUTOR = "planner_executor"  # C / C3
    FINITE_STATE = "finite_state"  # D / C4
    POLICY_FIRST = "policy_first"  # E / C5
    RETRIEVAL_FIRST = "retrieval_first"  # C6


class RetrievalMode(StrEnum):
    NONE = "none"
    ON_DEMAND = "on_demand"
    BM25 = "bm25"
    DENSE = "dense"
    HYBRID = "hybrid"
    POLICY_GRAPH = "policy_graph"
    PLATFORM = "platform"  # e.g. Freya knowledge base (§55.7)


class GuardMode(StrEnum):
    NONE = "none"
    PROMPT = "prompt"
    DETERMINISTIC = "deterministic"
    PROMPT_AND_DETERMINISTIC = "prompt_and_deterministic"


# Canonical prompt block names; each is an independent optimization target (§3.9).
PROMPT_BLOCKS: tuple[str, ...] = (
    "role",
    "global_policy",
    "rule_priority",
    "domain_rules",
    "action_protocol",
    "coordination",
    "operational_state",
    "tool_use_policy",
    "knowledge_retrieval_policy",
    "mutation_guard",
    "post_write_verify",
    "completion_verifier",
    "outcome_statement",
    "over_caution_guard",
    "planning",
    "workflow",
    "voice_policy",
    "language_policy",
    "few_shot",
)


class FewShotExample(BaseModel):
    family: str
    text: str


class AgentSpec(BaseModel):
    candidate_id: str
    parent_ids: list[str] = Field(default_factory=list)
    architecture: Architecture
    lineage_note: str = ""

    system_prompt_sections: dict[str, str] = Field(default_factory=dict)
    few_shot_examples: list[FewShotExample] = Field(default_factory=list)

    planning_mode: str = "none"
    memory_mode: str = "turn_state"
    retrieval_mode: RetrievalMode = RetrievalMode.NONE
    mutation_guard: GuardMode = GuardMode.PROMPT_AND_DETERMINISTIC
    completion_verifier: bool = True

    model: str | None = None
    temperature: float = 0.2
    max_tokens: int = 1000
    max_steps: int = 30

    first_message: str | None = None
    farewell_message: str | None = None
    tool_policy: dict[str, Any] = Field(default_factory=dict)
    tool_description_overrides: dict[str, str] = Field(default_factory=dict)
    voice_policy: dict[str, Any] | None = None
    language: str = "en"
    guardrails: list[dict[str, Any]] = Field(default_factory=list)
    platform_overrides: dict[str, Any] = Field(default_factory=dict)
    section_order: list[str] = Field(
        default_factory=list, description="explicit block order; empty = canonical"
    )

    def render_system_prompt(self) -> str:
        parts: list[str] = []
        # An explicit order is part of the architecture (policy-first, retrieval-first);
        # rendering used to force the canonical order and erased it (GAP_ANALYSIS A7).
        order = [b for b in self.section_order if b in self.system_prompt_sections]
        order += [b for b in PROMPT_BLOCKS if b in self.system_prompt_sections and b not in order]
        order += [b for b in self.system_prompt_sections if b not in order]
        for name in order:
            text = self.system_prompt_sections[name].strip()
            if text:
                parts.append(f"## {name.replace('_', ' ').title()}\n{text}")
        if self.few_shot_examples:
            ex = "\n\n".join(f"### Example ({e.family})\n{e.text}" for e in self.few_shot_examples)
            parts.append(f"## Examples\n{ex}")
        return "\n\n".join(parts)
