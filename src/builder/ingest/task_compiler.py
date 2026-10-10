"""TaskSpec compilation (memo §5.1, §3.3)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from builder.ingest.lang import detect_language, looks_like_voice
from builder.llm import LLMClient, Message, Role
from builder.schema.bundle import InputBundle
from builder.schema.task import ActorCapabilities, Channel, CoordinationRequirements, TaskSpec
from builder.schema.tool import ToolGraph


def compile_task_deterministic(bundle: InputBundle, tools: ToolGraph) -> TaskSpec:
    text = bundle.task_description.strip()
    language = bundle.language or detect_language(text + " " + bundle.policy_text[:2000])
    if bundle.channel:
        channel = Channel(bundle.channel) if bundle.channel in Channel else Channel.TEXT
    else:
        channel = Channel.VOICE if looks_like_voice(text) else Channel.TEXT
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), text)
    return TaskSpec(
        task_id=bundle.task_id,
        objective=first[:500],
        description=text,
        actors=ActorCapabilities(agent_tools=tools.names()),
        channel=channel,
        language=language,
    )


class TaskExtraction(BaseModel):
    objective: str
    domain: str = ""
    success_conditions: list[str] = Field(default_factory=list)
    required_outputs: list[str] = Field(default_factory=list)
    intents: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    required_information: list[str] = Field(default_factory=list)
    optional_information: list[str] = Field(default_factory=list)
    termination_conditions: list[str] = Field(default_factory=list)
    escalation_conditions: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    user_actions: list[str] = Field(default_factory=list)
    user_must_confirm: list[str] = Field(default_factory=list)
    user_must_supply: list[str] = Field(default_factory=list)
    user_must_perform: list[str] = Field(default_factory=list)


TASK_PROMPT = """Extract a precise task specification for a customer-facing agent.
Ground every item in the inputs; do not invent capabilities. Intents are the distinct customer
requests the agent must handle (snake_case). Required information = what the agent must obtain
from the customer before acting. user_actions / user_must_perform = things only the customer can
do (e.g. "open the mobile app"). escalation_conditions = when to hand off to a human.
Return JSON only."""


def compile_task(bundle: InputBundle, tools: ToolGraph, llm: LLMClient | None = None) -> TaskSpec:
    base = compile_task_deterministic(bundle, tools)
    if llm is None:
        return base
    tool_list = "\n".join(f"- {t.name}: {t.description}" for t in tools.tools)
    ex = llm.complete_structured(
        Role.BUILDER,
        TaskExtraction,
        [
            Message(role="system", content=TASK_PROMPT),
            Message(
                role="user",
                content=f"TASK:\n{bundle.task_description}\n\nPOLICY (excerpt):\n"
                f"{bundle.policy_text[:20000]}\n\nTOOLS:\n{tool_list}",
            ),
        ],
        temperature=0.0,
    )
    return base.model_copy(
        update={
            **ex.model_dump(
                exclude={
                    "user_actions",
                    "user_must_confirm",
                    "user_must_supply",
                    "user_must_perform",
                }
            ),
            "actors": ActorCapabilities(agent_tools=tools.names(), user_actions=ex.user_actions),
            "coordination": CoordinationRequirements(
                user_must_confirm=ex.user_must_confirm,
                user_must_supply=ex.user_must_supply,
                user_must_perform=ex.user_must_perform,
            ),
        }
    )
