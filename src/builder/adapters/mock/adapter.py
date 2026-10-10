"""Mock platform adapter: local simulation with a state oracle (PLAN Faz 2)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from builder.adapters.base import AdapterCapabilities, AgentHandle, ValidationResult
from builder.adapters.mock.env import DomainPack, Environment
from builder.adapters.mock.user_sim import UserSimulator
from builder.knowledge.retriever import Retriever
from builder.llm import BudgetExceeded, LLMClient, LLMError, Role
from builder.runtime.reference_agent import ReferenceAgent
from builder.schema.agent import AgentSpec
from builder.schema.knowledge import KnowledgeSpec
from builder.schema.scenario import ScenarioSpec
from builder.schema.tool import ToolGraph
from builder.schema.trace import EndReason, EventKind, SimulationTrace, TraceEvent

USER_ROLES = (Role.USER_SIM, Role.USER_SIM_ALT)


class MockAdapter:
    name = "mock"

    def __init__(
        self,
        pack: DomainPack,
        tools: ToolGraph,
        llm: LLMClient,
        knowledge: KnowledgeSpec | None = None,
    ) -> None:
        self.pack = pack
        self.deterministic = pack.deterministic
        self.tools = tools
        self.llm = llm
        self.retriever = Retriever(knowledge) if knowledge and knowledge.documents else None
        self._agents: dict[str, AgentSpec] = {}

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=self.name, supports_state_oracle=True, supports_preprocess_reject=True
        )

    def validate_agent(self, spec: AgentSpec) -> ValidationResult:
        errors: list[str] = []
        names = set(self.tools.names())
        for t in spec.tool_description_overrides:
            if t not in names:
                errors.append(f"override for unknown tool '{t}'")
        prompt = spec.render_system_prompt()
        if not prompt.strip():
            errors.append("empty system prompt")
        if "{{" in prompt or "TODO" in prompt:
            errors.append("unresolved placeholder in prompt")
        return ValidationResult(ok=not errors, errors=errors)

    def deploy_agent(self, spec: AgentSpec) -> AgentHandle:
        self._agents[spec.candidate_id] = spec
        return AgentHandle(candidate_id=spec.candidate_id, platform_id=f"mock:{spec.candidate_id}")

    def run_simulation(
        self, handle: AgentHandle, scenario: ScenarioSpec, trial: int = 0, user_variant: int = 0
    ) -> SimulationTrace:
        spec = self._agents[handle.candidate_id]
        run_id = uuid.uuid4().hex[:12]
        salt = f"{spec.candidate_id}:{scenario.scenario_id}:{trial}:{user_variant}"
        env = Environment.from_pack(self.pack, scenario.initial_state or None, scenario.tool_faults)
        trace = SimulationTrace(
            run_id=run_id,
            candidate_id=spec.candidate_id,
            scenario_id=scenario.scenario_id,
            seed=scenario.seed,
            trial=trial,
            adapter=self.name,
            user_model=self.llm.model_for(USER_ROLES[user_variant % len(USER_ROLES)]),
            initial_state=json.loads(json.dumps(env.db)),
        )
        agent = ReferenceAgent(
            spec, self.tools, self.llm, env.call, cache_salt=salt, retriever=self.retriever
        )
        user = UserSimulator(
            self.llm, scenario, role=USER_ROLES[user_variant % len(USER_ROLES)], salt=salt
        )
        start = time.monotonic()
        agent_text = agent.opening()
        if agent_text:
            trace.events.append(TraceEvent(turn=0, kind=EventKind.AGENT, content=agent_text))
        trace.end_reason = EndReason.MAX_TURNS
        try:
            for turn in range(1, scenario.max_turns + 1):
                user_text, stop, ucost = user.next(agent_text, turn)
                trace.cost_usd += ucost
                if stop and not user_text:
                    trace.end_reason = EndReason.USER_ENDED
                    break
                trace.events.append(TraceEvent(turn=turn, kind=EventKind.USER, content=user_text))
                if stop:
                    trace.end_reason = EndReason.USER_ENDED
                    break
                out = agent.respond(user_text, turn)
                trace.events.extend(out.events)
                trace.prompt_tokens += out.prompt_tokens
                trace.completion_tokens += out.completion_tokens
                trace.cost_usd += out.cost_usd
                agent_text = out.text
                if out.ended:
                    trace.end_reason = EndReason.AGENT_ENDED
                    break
        except BudgetExceeded:
            raise
        except LLMError as exc:
            trace.end_reason = EndReason.ERROR
            trace.error = str(exc)
        trace.latency_s = time.monotonic() - start
        trace.final_state = env.db
        return trace

    def replay_prefix(
        self, spec: AgentSpec, scenario: ScenarioSpec, user_messages: list[str], salt: str = ""
    ) -> list[TraceEvent]:
        """Counterfactual probe (memo §55.4): feed recorded user turns verbatim to `spec`.

        Returns the events of the final replayed turn — where the original went wrong.
        """
        env = Environment.from_pack(self.pack, scenario.initial_state or None, scenario.tool_faults)
        agent = ReferenceAgent(
            spec,
            self.tools,
            self.llm,
            env.call,
            cache_salt=f"probe:{salt}",
            retriever=self.retriever,
        )
        agent.opening()
        events: list[TraceEvent] = []
        for turn, msg in enumerate(user_messages, start=1):
            out = agent.respond(msg, turn)
            events = out.events
            if out.ended:
                break
        return events

    # StateBackend (testsynth): seed state + reference-action replay (memo §3.2, §58)

    def seed_state(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.pack.seed_db))

    def replay(
        self, initial_state: dict[str, Any], actions: list[dict[str, Any]], salt: str = ""
    ) -> tuple[bool, dict[str, Any], list[str]]:
        env = Environment.from_pack(self.pack, initial_state, {}, salt=salt)
        errors: list[str] = []
        for a in actions:
            ok, res = env.call(str(a.get("tool")), dict(a.get("args") or {}))
            if not ok:
                errors.append(f"{a.get('tool')}: {res}")
        return not errors, env.db, errors

    def export_submission(self, spec: AgentSpec, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "agent_spec.json"
        path.write_text(spec.model_dump_json(indent=2))
        return path
