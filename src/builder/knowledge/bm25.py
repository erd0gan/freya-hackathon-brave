"""Dependency-free BM25 with light multilingual tokenization (memo §14.1)."""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

from builder.knowledge.chunk import Chunk

_STOP = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "to",
    "in",
    "is",
    "for",
    "on",
    "with",
    "be",
    "ve",
    "bir",
    "bu",
    "ile",
    "için",
    "da",
    "de",
}


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c)).replace("ı", "i")
    toks = re.findall(r"[a-z0-9]+", text)
    return [t[:6] for t in toks if t not in _STOP]  # crude stemming by prefix


class BM25Index:
    def __init__(self, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> None:
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.docs = [
            Counter(tokenize(" ".join([*c.heading_path, c.title, c.text]))) for c in chunks
        ]
        self.lens = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lens) / max(len(self.lens), 1)
        df: Counter[str] = Counter()
        for d in self.docs:
            df.update(d.keys())
        n = len(chunks)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int = 5) -> list[tuple[Chunk, float]]:
        q = tokenize(query)
        scores: list[tuple[int, float]] = []
        for i, d in enumerate(self.docs):
            s = 0.0
            for t in q:
                tf = d.get(t, 0)
                if tf:
                    s += (
                        self.idf.get(t, 0.0)
                        * tf
                        * (self.k1 + 1)
                        / (tf + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
                    )
            if s > 0:
                scores.append((i, s))
        scores.sort(key=lambda x: -x[1])
        return [(self.chunks[i], s) for i, s in scores[:k]]
