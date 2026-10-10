"""KnowledgeSpec and per-document records (memo §5.4, §14.5)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class DocType(StrEnum):
    PRODUCT_FACT = "product_fact"
    DEFINITION = "definition"
    ELIGIBILITY = "eligibility"
    PROCEDURE = "procedure"
    POLICY = "policy"
    EXCEPTION = "exception"
    ESCALATION = "escalation"
    TOOL_DOC = "tool_doc"
    OTHER = "other"


class DocumentRecord(BaseModel):
    doc_id: str
    title: str = ""
    path: str = ""
    sha256: str = ""
    text: str = ""
    doc_type: DocType = DocType.OTHER
    summary: str = ""
    category: str = ""
    entities: list[str] = Field(default_factory=list)
    rule_ids: list[str] = Field(default_factory=list)
    tool_mentions: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list, description="doc_ids referenced (§14.4)")


class KnowledgeSpec(BaseModel):
    corpus_size: int = 0
    total_chars: int = 0
    categories: list[str] = Field(default_factory=list)
    retrieval_modes: list[str] = Field(default_factory=list)
    policy_documents: list[str] = Field(default_factory=list)
    procedure_documents: list[str] = Field(default_factory=list)
    tool_documents: list[str] = Field(default_factory=list)
    requires_cross_document_reasoning: bool = False
    documents: list[DocumentRecord] = Field(default_factory=list)
