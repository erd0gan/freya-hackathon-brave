"""Raw builder input and the compiled domain IR (memo §2, §9)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from builder.schema.knowledge import KnowledgeSpec
from builder.schema.policy import PolicySpec
from builder.schema.task import TaskSpec
from builder.schema.tool import ToolGraph


class RawDocument(BaseModel):
    doc_id: str
    title: str = ""
    path: str = ""
    text: str


class InputBundle(BaseModel):
    """Format-neutral builder input; every hackathon format is parsed into this."""

    task_id: str | None = None
    task_description: str
    policy_text: str = ""
    tools: list[dict[str, Any]] = Field(
        default_factory=list, description="OpenAI-style function schemas or similar"
    )
    documents: list[RawDocument] = Field(default_factory=list)
    channel: str | None = None
    language: str | None = None
    examples: list[dict[str, Any]] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class DomainSpec(BaseModel):
    """The typed intermediate representation everything downstream consumes."""

    task: TaskSpec
    policy: PolicySpec = Field(default_factory=PolicySpec)
    tools: ToolGraph = Field(default_factory=ToolGraph)
    knowledge: KnowledgeSpec = Field(default_factory=KnowledgeSpec)
    input_hash: str = ""
