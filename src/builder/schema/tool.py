"""ToolSpec and ToolGraph (memo §5.3, §6)."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RiskClass(StrEnum):
    READ_ONLY = "READ_ONLY"
    REVERSIBLE_WRITE = "REVERSIBLE_WRITE"
    IRREVERSIBLE_WRITE = "IRREVERSIBLE_WRITE"
    EXTERNAL_SIDE_EFFECT = "EXTERNAL_SIDE_EFFECT"

    @property
    def is_write(self) -> bool:
        return self is not RiskClass.READ_ONLY

    @property
    def rank(self) -> int:
        return list(RiskClass).index(self)


def riskier(a: RiskClass, b: RiskClass) -> RiskClass:
    return a if a.rank >= b.rank else b


class ToolSpec(BaseModel):
    name: str
    description: str = ""
    inferred_purpose: str = ""

    inputs: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}},
        description="JSON Schema of the arguments",
    )
    outputs: dict[str, Any] = Field(default_factory=dict)

    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)

    risk_class: RiskClass = RiskClass.READ_ONLY
    # How the deterministic pass decided: "name_verb" (a known verb in the name, strong),
    # "description" or "default" (weak). Only weak evidence may be overruled downward.
    risk_evidence: str = ""
    idempotent: bool | None = None
    requires_confirmation: bool = False
    retryable: bool | None = None

    failure_modes: list[str] = Field(default_factory=list)
    related_tools: list[str] = Field(default_factory=list)
    arg_format_rules: dict[str, str] = Field(
        default_factory=dict, description="arg -> canonical format (memo §57.2)"
    )
    # Platform-specific passthrough (e.g. Freya handler/config); never interpreted here.
    platform: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_write(self) -> bool:
        return self.risk_class.is_write

    @property
    def required_args(self) -> list[str]:
        return list(self.inputs.get("required", []))


class EdgeKind(StrEnum):
    REQUIRES_BEFORE = "requires-before"
    FEEDS = "reads-produced-data-for"
    MUTUALLY_EXCLUSIVE = "mutually-exclusive-with"
    REVERSES = "reverses"
    VERIFIES = "verifies"


class EdgeSource(StrEnum):
    """Why an edge exists. Only evidence-backed sources may create enforced prerequisites."""

    POLICY = "policy"  # explicit policy text (quoted in `evidence`)
    TOOL_SCHEMA = "tool_schema"  # the tool's own description/schema says so
    PLATFORM = "platform"  # platform metadata
    DATAFLOW = "dataflow"  # argument/result name matching (heuristic)
    LLM = "llm"  # model inference: advisory only


class ToolEdge(BaseModel):
    src: str
    dst: str
    kind: EdgeKind
    note: str = ""
    source: EdgeSource = EdgeSource.DATAFLOW
    evidence: str = ""


class ToolGraph(BaseModel):
    tools: list[ToolSpec] = Field(default_factory=list)
    edges: list[ToolEdge] = Field(default_factory=list)

    def get(self, name: str) -> ToolSpec | None:
        return next((t for t in self.tools if t.name == name), None)

    def names(self) -> list[str]:
        return [t.name for t in self.tools]

    def writes(self) -> list[ToolSpec]:
        return [t for t in self.tools if t.is_write]

    def prerequisites(self, name: str) -> list[str]:
        return [e.src for e in self.edges if e.dst == name and e.kind is EdgeKind.REQUIRES_BEFORE]

    def depth(self) -> int:
        """Longest requires-before chain; feeds the architecture selector (§21)."""
        memo: dict[str, int] = {}

        def d(n: str, seen: frozenset[str]) -> int:
            if n in memo:
                return memo[n]
            pre = [p for p in self.prerequisites(n) if p not in seen]
            memo[n] = 1 + max((d(p, seen | {n}) for p in pre), default=0)
            return memo[n]

        return max((d(t.name, frozenset()) for t in self.tools), default=0)
