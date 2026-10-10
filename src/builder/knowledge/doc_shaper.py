"""Reshape documents for a platform-owned retriever (memo §55.7).

When only the platform KB is allowed at runtime, the builder cannot change the
retriever — but it can change the documents: split by section, add explicit
headings, and add compiled rule summaries as their own documents.
"""

from __future__ import annotations

from builder.knowledge.chunk import chunk_document
from builder.schema.bundle import DomainSpec


def shaped_documents(d: DomainSpec, max_chars: int = 3000) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for doc in d.knowledge.documents:
        for c in chunk_document(doc, max_chars):
            heading = " > ".join([doc.title, *c.heading_path])
            out.append(
                {
                    "filename": f"{c.chunk_id.replace('#', '_')}.md",
                    "content": f"# {heading}\n\n{c.text}\n",
                }
            )
    if d.policy.rules:
        body = "\n".join(f"- [{r.id}] {r.description}" for r in d.policy.ordered())
        out.append(
            {
                "filename": "compiled_rules.md",
                "content": f"# Compiled business rules (summary)\n\n{body}\n",
            }
        )
    return out
