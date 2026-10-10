"""Central builder configuration.

All settings come from environment variables (or a `.env` file) so the same code
runs locally, in CI and on competition day with only config changes (PLAN §0.1).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from builder.llm.roles import Role

HAIKU = "anthropic/claude-haiku-5.5"
SONNET = "anthropic/claude-sonnet-5.5"
LUNA = "openai/gpt-6-luna-pro"

# Model sets per profile. `test` is cheap enough for many iterations before the event;
# `competition` only upgrades the low-volume, high-leverage roles (builder/optimizer).
# Judge and user simulator stay on other model families than the agent (self-preference).
PROFILES: dict[str, dict[str, str]] = {
    "test": {
        "builder": HAIKU,
        "optimizer": HAIKU,
        "analyzer": HAIKU,
        "judge": LUNA,
        "user_sim": LUNA,
        "user_sim_alt": "google/gemini-2.5-flash",
        "agent": HAIKU,
        "embedding": "openai/text-embedding-3-small",
    },
}
PROFILES["competition"] = {**PROFILES["test"], "builder": SONNET, "optimizer": SONNET}


class RoleModels(BaseSettings):
    """Model id per LLM role (PLAN §2, memo §32).

    `BUILDER_PROFILE` (test | competition) picks the defaults; any
    `BUILDER_MODEL_<ROLE>` variable overrides a single role.
    """

    model_config = SettingsConfigDict(env_prefix="BUILDER_MODEL_", env_file=".env", extra="ignore")

    profile: Literal["test", "competition"] = Field("test", validation_alias="BUILDER_PROFILE")
    builder: str = ""
    optimizer: str = ""
    analyzer: str = ""
    judge: str = ""
    user_sim: str = ""
    user_sim_alt: str = ""
    agent: str = ""
    embedding: str = ""

    @model_validator(mode="after")
    def _fill_from_profile(self) -> RoleModels:
        for role, model in PROFILES[self.profile].items():
            if not getattr(self, role):
                setattr(self, role, model)
        return self

    def for_role(self, role: Role) -> str:
        return str(getattr(self, role.value))


class BuilderConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    freya_api_key: str | None = None
    freya_base_url: str = "https://app.freyavoice.ai/api/v2"  # OpenAPI `servers`
    freya_workspace_id: str | None = None  # required by GET endpoints (spec)
    # JSON list of simulation persona LLMs, one per user variant (memo §59), e.g.
    # [{"provider":"openai","model":"gpt-x"},{"provider":"google","model":"gemini-y"}]. VERIFY.
    freya_persona_llms: str = ""

    data_dir: Path = Path(".builder")
    max_cost_usd: float = 50.0
    llm_timeout_s: float = 120.0
    seed: int = 42

    models: RoleModels = Field(default_factory=RoleModels)

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "llm_cache"

    @property
    def ledger_path(self) -> Path:
        return self.data_dir / "cost_ledger.jsonl"

    @property
    def traces_dir(self) -> Path:
        return self.data_dir / "traces"


def load_config() -> BuilderConfig:
    return BuilderConfig()
