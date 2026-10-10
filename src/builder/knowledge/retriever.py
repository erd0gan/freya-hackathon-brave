"""Retrieval facade: multi-query BM25 + cross-document expansion (memo §14.2–14.4).

Dense/hybrid retrieval plugs in here once an embedding provider is configured;
until then BM25 over several query views is the default (cheap, offline).
"""

from __future__ import annotations

from builder.knowledge.bm25 import BM25Index
from builder.knowledge.chunk import Chunk, chunk_document
from builder.schema.knowledge import KnowledgeSpec


class Retriever:
    def __init__(self, knowledge: KnowledgeSpec) -> None:
        self.k = knowledge
        self.chunks = [c for d in knowledge.documents for c in chunk_document(d)]
        self.index = BM25Index(self.chunks)
        self.links = {d.doc_id: d.links for d in knowledge.documents}

    def search(self, queries: list[str], k: int = 5, expand: bool = True) -> list[Chunk]:
        scored: dict[str, tuple[Chunk, float]] = {}
        for q in queries:
            for c, s in self.index.search(q, k):
                if c.chunk_id not in scored or scored[c.chunk_id][1] < s:
                    scored[c.chunk_id] = (c, s)
        top = [c for c, _ in sorted(scored.values(), key=lambda x: -x[1])[:k]]
        if expand:
            seen = {c.doc_id for c in top}
            for c in list(top):
                for linked in self.links.get(c.doc_id, []):
                    if linked not in seen:
                        seen.add(linked)
                        top += [x for x in self.chunks if x.doc_id == linked][:1]
        return top

    def render(self, chunks: list[Chunk]) -> list[dict[str, str]]:
        return [
            {"doc": c.doc_id, "section": " > ".join(c.heading_path) or c.title, "text": c.text}
            for c in chunks
        ]
