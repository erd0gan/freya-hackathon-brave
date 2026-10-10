"""TaskSpec (memo §5.1) with the τ² dual-control extensions (memo §3.3)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Channel(StrEnum):
    TEXT = "text"
    VOICE = "voice"


class ActorCapabilities(BaseModel):
    agent_tools: list[str] = Field(default_factory=list)
    user_actions: list[str] = Field(
        default_factory=list, description="Things only the user can do, e.g. 'open app'"
    )
    external_system_actions: list[str] = Field(default_factory=list)


class CoordinationRequirements(BaseModel):
    user_must_confirm: list[str] = Field(default_factory=list)
    user_must_supply: list[str] = Field(default_factory=list)
    user_must_perform: list[str] = Field(default_factory=list)


class TaskSpec(BaseModel):
    task_id: str | None = None
    objective: str
    description: str = Field(default="", description="Full task text as given")
    domain: str = ""
    success_conditions: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)

    intents: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    required_information: list[str] = Field(default_factory=list)
    optional_information: list[str] = Field(default_factory=list)

    user_can_change_goal: bool = True
    agent_can_ask_questions: bool = True

    termination_conditions: list[str] = Field(default_factory=list)
    escalation_conditions: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)

    actors: ActorCapabilities = Field(default_factory=ActorCapabilities)
    coordination: CoordinationRequirements = Field(default_factory=CoordinationRequirements)

    channel: Channel = Channel.TEXT
    language: str = Field(default="en", description="BCP-47 code of the task language")
