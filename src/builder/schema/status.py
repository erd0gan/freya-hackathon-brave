"""Evidence labels for claims about the platform (memo §70)."""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    CONFIRMED = "CONFIRMED"
    STRONG_HYPOTHESIS = "STRONG_HYPOTHESIS"
    UNKNOWN = "UNKNOWN"
    VERIFY = "VERIFY"
