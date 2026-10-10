"""Knowledge preprocessing (memo §14.5) — deterministic; LLM summaries are optional."""

from __future__ import annotations

import hashlib
import re

from builder.schema.bundle import InputBundle
from builder.schema.knowledge import DocType, DocumentRecord, KnowledgeSpec
from builder.schema.tool import ToolGraph

_TYPE_HINTS: list[tuple[DocType, re.Pattern[str]]] = [
    (DocType.EXCEPTION, re.compile(r"\b(exception|except|unless|waiver|istisna)\b", re.I)),
    (DocType.ESCALATION, re.compile(r"\b(escalat|transfer to|hand ?off|supervisor)\b", re.I)),
    (DocType.ELIGIBILITY, re.compile(r"\b(eligib|qualif|criteria|requirement)\b", re.I)),
    (DocType.PROCEDURE, re.compile(r"\b(step \d|procedure|process|how to|first,|then)\b", re.I)),
    (DocType.POLICY, re.compile(r"\b(policy|must|never|always|required|prohibited)\b", re.I)),
    (DocType.DEFINITION, re.compile(r"\b(means|is defined as|definition)\b", re.I)),
]
_RULE_ID = re.compile(r"\b[A-Z]{2,10}[-_]\d{1,4}[A-Z]?\b")
CROSS_DOC_THRESHOLD = 20


def classify(text: str) -> DocType:
    scores = {t: len(p.findall(text)) for t, p in _TYPE_HINTS}
    best = max(scores, key=lambda t: scores[t])
    return best if scores[best] else DocType.PRODUCT_FACT


def compile_knowledge(bundle: InputBundle, tools: ToolGraph) -> KnowledgeSpec:
    names = tools.names()
    titles = {d.doc_id: d.title.lower() for d in bundle.documents if d.title}
    records: list[DocumentRecord] = []
    for d in bundle.documents:
        low = d.text.lower()
        dtype = classify(d.text)
        mentions = [n for n in names if n.lower() in low]
        if mentions and dtype is DocType.PRODUCT_FACT:
            dtype = DocType.TOOL_DOC
        links = [
            other
            for other, title in titles.items()
            if other != d.doc_id and len(title) > 6 and title in low
        ]
        first = next((ln.strip("# ").strip() for ln in d.text.splitlines() if ln.strip()), "")
        records.append(
            DocumentRecord(
                doc_id=d.doc_id,
                title=d.title or first[:80],
                path=d.path,
                sha256=hashlib.sha256(d.text.encode()).hexdigest(),
                text=d.text,
                doc_type=dtype,
                summary=first[:200],
                rule_ids=sorted(set(_RULE_ID.findall(d.text))),
                tool_mentions=mentions,
                links=links,
            )
        )
    by_type = lambda t: [r.doc_id for r in records if r.doc_type is t]  # noqa: E731
    n = len(records)
    return KnowledgeSpec(
        corpus_size=n,
        total_chars=sum(len(r.text) for r in records),
        categories=sorted({r.doc_type.value for r in records}),
        retrieval_modes=["bm25"] if n else [],
        policy_documents=by_type(DocType.POLICY) + by_type(DocType.EXCEPTION),
        procedure_documents=by_type(DocType.PROCEDURE),
        tool_documents=by_type(DocType.TOOL_DOC),
        requires_cross_document_reasoning=n >= CROSS_DOC_THRESHOLD or any(r.links for r in records),
        documents=records,
    )
