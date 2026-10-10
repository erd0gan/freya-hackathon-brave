"""PolicyRule / PolicySpec (memo §5.2, §7)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class PolicyLevel(StrEnum):
    """Precedence classes (memo §7.4), highest first."""

    PLATFORM = "platform"
    PROHIBITION = "prohibition"
    REGULATORY = "regulatory"
    AUTHORIZATION = "authorization"
    TASK = "task"
    HELPFULNESS = "helpfulness"


LEVEL_ORDER: dict[PolicyLevel, int] = {lvl: i for i, lvl in enumerate(PolicyLevel)}


class PolicyRule(BaseModel):
    id: str
    description: str

    trigger: str = ""
    preconditions: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)

    level: PolicyLevel = PolicyLevel.TASK
    priority: int = Field(default=50, description="Lower = more important, within a level")
    affected_tools: list[str] = Field(default_factory=list, description="Rule→tool map (§7.3)")
    source_refs: list[str] = Field(default_factory=list)
    complexity: int = Field(default=1, ge=1, le=5, description="IntellAgent weight (§58)")
    category: str = ""

    @field_validator("id")
    @classmethod
    def _id_nonempty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("rule id must be non-empty")
        return v.strip()

    def sort_key(self) -> tuple[int, int, str]:
        return (LEVEL_ORDER[self.level], self.priority, self.id)


class RuleConflict(BaseModel):
    rule_ids: list[str]
    kind: str = Field(description="conflicting_action | exception_override | version_mismatch")
    explanation: str = ""
    resolution: str = ""


class PolicySpec(BaseModel):
    rules: list[PolicyRule] = Field(default_factory=list)
    conflicts: list[RuleConflict] = Field(default_factory=list)
    precedence_note: str = ""

    def ordered(self) -> list[PolicyRule]:
        return sorted(self.rules, key=PolicyRule.sort_key)

    def get(self, rule_id: str) -> PolicyRule | None:
        return next((r for r in self.rules if r.id == rule_id), None)

    def for_tool(self, tool_name: str) -> list[PolicyRule]:
        return [r for r in self.ordered() if tool_name in r.affected_tools]

    def unsourced(self) -> list[PolicyRule]:
        return [r for r in self.rules if not r.source_refs]
