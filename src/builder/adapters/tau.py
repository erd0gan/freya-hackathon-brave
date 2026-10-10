"""τ²-bench adapter (memo §24, §65; PLAN Faz 10). Status: VERIFY — written, not run.

Per the user's decision the code exists but is not executed yet (GitHub access
and LLM spend). The tau2 API names below follow the public repo as remembered
on 6 Oct 2026 and MUST be checked on first use (`uv sync --extra tau`):

    from tau2.registry import registry
    env = registry.get_env_constructor(domain)()
    tasks = registry.get_tasks_loader(domain)()
    env.get_policy(); env.get_tools() -> [Tool(openai_schema=...)]
    env.make_tool_call(name, requestor="assistant", **args)
    env.get_db_hash()

Design: our ReferenceAgent + UserSimulator drive the conversation; τ's own
environment executes tools; the oracle is τ's method — replay the task's
reference actions on a fresh env and compare DB hashes (memo §3.2).
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from builder.adapters.base import AdapterCapabilities, AgentHandle, ValidationResult
from builder.adapters.mock.user_sim import UserSimulator
from builder.llm import LLMClient, LLMError
from builder.runtime.reference_agent import ReferenceAgent
from builder.schema.agent import AgentSpec
from builder.schema.bundle import InputBundle
from builder.schema.scenario import Oracle, Persona, ScenarioSpec, Split
from builder.schema.status import Status
from builder.schema.tool import ToolGraph
from builder.schema.trace import EndReason, EventKind, SimulationTrace, TraceEvent

DEV_DOMAINS = ("airline", "retail", "telecom")
HOLDOUT_DOMAINS = ("banking_knowledge",)


def _registry() -> Any:
    try:
        from tau2.registry import registry  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("tau2 is not installed: `uv sync --extra tau`") from exc
    return registry


def bundle_from_tau(domain: str) -> InputBundle:
    reg = _registry()
    env = reg.get_env_constructor(domain)()
    tools = [t.openai_schema for t in env.get_tools()]
    return InputBundle(
        task_id=f"tau-{domain}",
        task_description=f"Customer service agent for the τ-bench '{domain}' domain.",
        policy_text=env.get_policy(),
        tools=tools,
        channel="text",
        language="en",
    )


def scenarios_from_tau(
    domain: str, split: Split = Split.HOLDOUT, limit: int | None = None
) -> list[ScenarioSpec]:
    reg = _registry()
    out: list[ScenarioSpec] = []
    for t in reg.get_tasks_loader(domain)()[:limit]:
        us = t.user_scenario
        instr = getattr(us, "instructions", us)
        crit = t.evaluation_criteria
        actions = [{"tool": a.name, "args": dict(a.arguments)} for a in (crit.actions or [])]
        out.append(
            ScenarioSpec(
                scenario_id=f"tau-{domain}-{t.id}",
                persona=Persona(prompt=str(instr)),
                oracle=Oracle(
                    reference_actions=actions,
                    required_communication=list(crit.communicate_info or []),
                    success_criteria="\n".join(getattr(crit, "nl_assertions", None) or []),
                ),
                split=split,
                provenance=f"tau2:{domain}:{t.id}",
            )
        )
    return out


class TauAdapter:
    name = "tau2"

    def __init__(self, domain: str, tools: ToolGraph, llm: LLMClient) -> None:
        self.domain = domain
        self.tools = tools
        self.llm = llm
        self._agents: dict[str, AgentSpec] = {}

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=self.name, supports_state_oracle=True, status={"tau2_api": Status.VERIFY}
        )

    def validate_agent(self, spec: AgentSpec) -> ValidationResult:
        return ValidationResult(ok=bool(spec.render_system_prompt().strip()))

    def deploy_agent(self, spec: AgentSpec) -> AgentHandle:
        self._agents[spec.candidate_id] = spec
        return AgentHandle(candidate_id=spec.candidate_id, platform_id=f"tau:{spec.candidate_id}")

    def _env(self) -> Any:
        return _registry().get_env_constructor(self.domain)()

    def _call(self, env: Any, name: str, args: dict[str, Any]) -> tuple[bool, Any]:
        try:
            return True, env.make_tool_call(name, requestor="assistant", **args)
        except Exception as exc:
            return False, {"error": str(exc)}

    def expected_hash(self, scenario: ScenarioSpec) -> str:
        env = self._env()
        for a in scenario.oracle.reference_actions:
            self._call(env, a["tool"], a.get("args", {}))
        return str(env.get_db_hash())

    def run_simulation(
        self, handle: AgentHandle, scenario: ScenarioSpec, trial: int = 0, user_variant: int = 0
    ) -> SimulationTrace:
        spec = self._agents[handle.candidate_id]
        env = self._env()
        salt = f"{spec.candidate_id}:{scenario.scenario_id}:{trial}:{user_variant}"
        agent = ReferenceAgent(
            spec, self.tools, self.llm, lambda n, a: self._call(env, n, a), cache_salt=salt
        )
        user = UserSimulator(self.llm, scenario, salt=salt)
        tr = SimulationTrace(
            run_id=uuid.uuid4().hex[:12],
            candidate_id=spec.candidate_id,
            scenario_id=scenario.scenario_id,
            trial=trial,
            adapter=self.name,
        )
        start = time.monotonic()
        text = agent.opening()
        tr.end_reason = EndReason.MAX_TURNS
        try:
            for turn in range(1, scenario.max_turns + 1):
                msg, stop, _ = user.next(text, turn)
                if stop:
                    tr.end_reason = EndReason.USER_ENDED
                    break
                tr.events.append(TraceEvent(turn=turn, kind=EventKind.USER, content=msg))
                out = agent.respond(msg, turn)
                tr.events.extend(out.events)
                text = out.text
                if out.ended:
                    tr.end_reason = EndReason.AGENT_ENDED
                    break
        except LLMError as exc:
            tr.end_reason, tr.error = EndReason.ERROR, str(exc)
        tr.latency_s = time.monotonic() - start
        ok = str(env.get_db_hash()) == self.expected_hash(scenario)
        tr.platform_result = {"passed": ok, "db_match": ok}
        return tr

    def export_submission(self, spec: AgentSpec, out_dir: Path) -> Path:
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "agent_spec.json"
        p.write_text(spec.model_dump_json(indent=2))
        return p
