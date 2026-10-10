"""Platform adapter boundary (memo §22, PLAN Faz 2).

Everything platform-specific lives behind this protocol so the rest of the
builder survives changes to the (still unverified) Freya API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from builder.schema.agent import AgentSpec
from builder.schema.scenario import ScenarioSpec
from builder.schema.status import Status
from builder.schema.trace import SimulationTrace


class AdapterCapabilities(BaseModel):
    name: str
    supports_state_oracle: bool = False
    supports_native_judge: bool = False
    supports_fork: bool = False
    supports_workflows: bool = False
    supports_preprocess_reject: bool = False
    supports_voice: bool = False
    max_concurrency: int = 4
    status: dict[str, Status] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AgentHandle(BaseModel):
    candidate_id: str
    platform_id: str = ""
    version_id: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class PlatformAdapter(Protocol):
    name: str

    def capabilities(self) -> AdapterCapabilities: ...

    def validate_agent(self, spec: AgentSpec) -> ValidationResult: ...

    def deploy_agent(self, spec: AgentSpec) -> AgentHandle: ...

    def run_simulation(
        self, handle: AgentHandle, scenario: ScenarioSpec, trial: int = 0, user_variant: int = 0
    ) -> SimulationTrace: ...

    def export_submission(self, spec: AgentSpec, out_dir: Path) -> Path: ...
