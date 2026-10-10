"""Tiny language / channel heuristics (memo §64, §3.5) — no dependencies."""

from __future__ import annotations

import re

_TR_CHARS = set("çğıöşüÇĞİÖŞÜ")
_TR_WORDS = {"ve", "bir", "bu", "için", "ile", "müşteri", "değil", "olarak", "gibi", "kart"}
# Channel phrases, not words: "update their phone number" or "may call back" are text tasks
# (GAP_ANALYSIS B5). An explicit bundle `channel` always wins over this heuristic.
_VOICE = re.compile(
    r"\b(voice (agent|assistant|channel|call)s?|phone (agent|line|support|call)s?|"
    r"over the phone|by phone|on the phone|(inbound|outbound|incoming) calls?|callers?\b|"
    r"ivr|telefonda|telefon (hattı|görüşmesi)|sesli (asistan|kanal|görüşme)|arayan müşteri)",
    re.IGNORECASE,
)


def detect_language(text: str) -> str:
    if not text:
        return "en"
    chars = sum(1 for c in text if c in _TR_CHARS)
    words = re.findall(r"\w+", text.lower())
    tr_hits = sum(1 for w in words if w in _TR_WORDS)
    if chars / max(len(text), 1) > 0.005 or tr_hits >= 3:
        return "tr"
    return "en"


def looks_like_voice(text: str) -> bool:
    return bool(_VOICE.search(text))
