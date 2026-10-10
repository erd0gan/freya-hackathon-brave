"""Heading-aware chunking (memo §14.3, §55.7: title + heading path per chunk)."""

from __future__ import annotations

import re

from pydantic import BaseModel

from builder.schema.knowledge import DocumentRecord

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    heading_path: list[str]
    text: str


def chunk_document(doc: DocumentRecord, max_chars: int = 1500) -> list[Chunk]:
    out: list[Chunk] = []
    path: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        body = "\n".join(buf).strip()
        if not body:
            return
        for j in range(0, len(body), max_chars):
            out.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}#{len(out)}",
                    doc_id=doc.doc_id,
                    title=doc.title,
                    heading_path=list(path),
                    text=body[j : j + max_chars],
                )
            )

    for line in doc.text.splitlines():
        m = _HEADING.match(line)
        if m:
            flush()
            buf = []
            level = len(m.group(1))
            path = [*path[: level - 1], m.group(2).strip()]
        else:
            buf.append(line)
    flush()
    return out
