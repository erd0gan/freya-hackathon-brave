"""LLM roles (memo §32) and their default sampling temperatures (memo §33)."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    BUILDER = "builder"
    OPTIMIZER = "optimizer"
    ANALYZER = "analyzer"
    JUDGE = "judge"
    USER_SIM = "user_sim"
    USER_SIM_ALT = "user_sim_alt"
    AGENT = "agent"
    EMBEDDING = "embedding"


DEFAULT_TEMPERATURE: dict[Role, float] = {
    Role.BUILDER: 0.7,
    Role.OPTIMIZER: 0.5,
    Role.ANALYZER: 0.0,
    Role.JUDGE: 0.0,
    Role.USER_SIM: 0.8,
    Role.USER_SIM_ALT: 0.8,
    Role.AGENT: 0.2,
    Role.EMBEDDING: 0.0,
}
