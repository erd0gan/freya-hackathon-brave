"""Freya platform adapter (memo §22, §55).

Endpoints and bodies follow the published OpenAPI spec (vendor/freya/openapi.json).
Live behaviour is VERIFY until an API key is used (scripts/smoke_freya.py).

Simulation flow (memo §55.3): one persona per scenario (batch-created) → create
and start a `chat` simulation → poll → latest iteration's agent version →
per-persona run status + call messages → traces.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from pathlib import Path
from typing import Any

from builder.adapters.base import AdapterCapabilities, AgentHandle, ValidationResult
from builder.adapters.freya.client import FreyaClient, FreyaError
from builder.adapters.freya.compile import (
    FIELD_STATUS,
    SYSTEM_PROMPT_LIMIT,
    compile_agent,
    compile_persona,
    platform_system_prompt,
    submission_config,
)
from builder.schema.agent import AgentSpec
from builder.schema.scenario import ScenarioSpec
from builder.schema.task import Channel
from builder.schema.tool import ToolGraph
from builder.schema.trace import EndReason, EventKind, SimulationTrace, TraceEvent


class FreyaAdapter:
    name = "freya"

    def __init__(
        self,
        client: FreyaClient,
        tools: ToolGraph,
        *,
        channel: Channel = Channel.TEXT,
        mode: str = "chat",
        max_call_seconds: int = 300,
        max_concurrent_calls: int = 10,
        sim_timeout_s: float = 1800.0,
        language: str | None = None,
        preprocess_language: str | None = None,
        persona_llms: list[dict[str, str]] | None = None,
    ) -> None:
        self.client = client
        # Real simulator diversity (memo §59): one persona LLM per user variant, e.g.
        # [{"provider": "openai", "model": "..."}, {"provider": "google", "model": "..."}].
        # Valid provider/model values are VERIFY; empty keeps the platform default.
        self.persona_llms = list(persona_llms or [])
        self.tools = tools
        self.channel = channel
        self.mode = mode
        self.max_call_seconds = max_call_seconds
        self.max_concurrent_calls = max_concurrent_calls
        self.sim_timeout_s = sim_timeout_s
        self.language = language
        self.preprocess_language = preprocess_language
        self._base_config: dict[str, Any] | None = None
        self._agents: dict[str, AgentHandle] = {}
        self._personas: dict[str, str] = {}

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            name=self.name,
            supports_native_judge=True,
            supports_fork=False,
            supports_voice=True,
            max_concurrency=1,
            status=dict(FIELD_STATUS),
        )

    def base_config(self) -> dict[str, Any]:
        if self._base_config is None:
            self._base_config = self.client.default_config(self.language)
        return self._base_config

    def compile(self, spec: AgentSpec) -> dict[str, Any]:
        """Config for a simulation deployment (adds the simulation call-duration cap)."""
        return compile_agent(
            spec,
            self.tools,
            channel=self.channel,
            base_config=self.base_config(),
            preprocess_language=self.preprocess_language,
            max_call_seconds=self.max_call_seconds,
        )

    def validate_agent(self, spec: AgentSpec) -> ValidationResult:
        errors: list[str] = []
        prompt = platform_system_prompt(spec)
        if len(prompt) > SYSTEM_PROMPT_LIMIT:
            errors.append("system prompt exceeds platform limit")
        names = set(self.tools.names())
        errors += [
            f"unknown tool override {t}" for t in spec.tool_description_overrides if t not in names
        ]
        return ValidationResult(ok=not errors, errors=errors)

    def deploy_agent(self, spec: AgentSpec) -> AgentHandle:
        created = self.client.create_agent(
            f"builder-{spec.candidate_id}", self.compile(spec), language=self.language
        )
        h = AgentHandle(
            candidate_id=spec.candidate_id,
            platform_id=str(created.get("id", "")),
            meta={"workspaceId": created.get("workspaceId")},
        )
        if not h.platform_id:
            raise FreyaError("create_agent returned no agent id")
        self._agents[spec.candidate_id] = h
        return h

    def _persona_ids(self, scenarios: list[ScenarioSpec], user_variant: int) -> list[str]:
        missing = [s for s in scenarios if f"{s.scenario_id}:{user_variant}" not in self._personas]
        if missing:
            payloads = []
            for s in missing:
                persona = compile_persona(s)
                if user_variant and not self.persona_llms:
                    persona["name"] += f"-variant-{user_variant}"
                    persona["prompt"] += (
                        "\nAsk concise follow-up questions and give facts only when asked. "
                        "Keep your goal and all facts unchanged."
                    )
                payloads.append(persona)
            created = self.client.create_personas(payloads)
            for s, c in zip(missing, created, strict=True):
                self._personas[f"{s.scenario_id}:{user_variant}"] = str(c["id"])
        return [self._personas[f"{s.scenario_id}:{user_variant}"] for s in scenarios]

    def run_batch(
        self,
        handle: AgentHandle,
        scenarios: list[ScenarioSpec],
        runs_per_persona: int = 1,
        *,
        user_variant: int = 0,
    ) -> list[SimulationTrace]:
        """Create+start a simulation, wait, then map persona runs to traces.

        Result path (spec): latest iteration → agentVersionId →
        /simulations/version-persona-runs (status + runId per persona) → /call/{runId}.
        """
        persona_ids = self._persona_ids(scenarios, user_variant)
        sim = self.client.create_simulation(
            {
                "name": f"builder-{handle.candidate_id}-{uuid.uuid4().hex[:6]}",
                "agentId": handle.platform_id,
                **({"agentVersionId": handle.version_id} if handle.version_id else {}),
                "selectedPersonaIds": persona_ids,
                "mode": self.mode,
                "runsPerPersona": runs_per_persona,
                "maxCallDurationSeconds": max(60, min(3600, self.max_call_seconds)),
                "maxConcurrentCalls": max(1, min(1000, self.max_concurrent_calls)),
                # Spec default is 3 silent retries; a retried run would hide failures (A3).
                "maxRetries": 0,
                **(
                    {"personaLlmOverride": self.persona_llms[user_variant % len(self.persona_llms)]}
                    if self.persona_llms
                    else {}
                ),
                "start": True,
            }
        )
        sim_id = str(sim.get("id", ""))
        if str(sim.get("status", "")).lower() == "draft":
            self.client.call("start_simulation", simulation_id=sim_id)
        self.client.wait_simulation(sim_id, timeout_s=self.sim_timeout_s)
        it = self.client.latest_iteration(sim_id)
        version_id = str(it.get("agentVersionId") or "")
        if not version_id:
            raise FreyaError(f"simulation {sim_id}: iteration has no agentVersionId")
        handle.version_id = handle.version_id or version_id
        runs = [
            r for r in self.client.persona_runs(version_id) if str(r.get("simulationId")) == sim_id
        ]
        by_persona = {pid: s for pid, s in zip(persona_ids, scenarios, strict=True)}
        traces: list[SimulationTrace] = []
        for r in runs:
            call = self.client.get_call(str(r["runId"])) if r.get("runId") else {}
            traces.append(result_to_trace(r, call, handle, by_persona))
        return traces

    def run_simulation(
        self, handle: AgentHandle, scenario: ScenarioSpec, trial: int = 0, user_variant: int = 0
    ) -> SimulationTrace:
        traces = self.run_batch(handle, [scenario], user_variant=user_variant)
        if len(traces) != 1 or traces[0].scenario_id != scenario.scenario_id:
            raise ValueError("Freya returned missing, duplicate or mismatched simulation results")
        trace = traces[0]
        trace.trial = trial
        trace.user_model = (
            str(self.persona_llms[user_variant % len(self.persona_llms)].get("model"))
            if self.persona_llms
            else f"freya-persona-variant-{user_variant}"
        )
        return trace

    def export_submission(self, spec: AgentSpec, out_dir: Path) -> Path:
        """Full, validated submission config: platform default + builder fields, no
        simulation-only settings (same `submission_config` as the offline export)."""
        cfg, problems = submission_config(
            spec,
            self.tools,
            channel=self.channel,
            base_config=self.base_config(),
            preprocess_language=self.preprocess_language,
        )
        if problems:
            raise FreyaError(f"submission config invalid: {problems}")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "freya_agent_config.json"
        path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
        return path


_USER_ROLES = {"user", "customer", "persona", "human"}
_AGENT_ROLES = {"assistant", "agent", "bot", "ai"}
_TOOL_CALL_ROLES = {"tool_calls", "tool_call", "function_call"}
_TOOL_RESULT_ROLES = {"tool", "tool_call_result", "tool_result", "function_result"}


def _text(m: dict[str, Any]) -> str:
    return str(m.get("content") if m.get("content") is not None else m.get("message", "") or "")


def _calls(m: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for tc in m.get("toolCalls") or m.get("tool_calls") or []:
        fn = tc.get("function", tc)
        args = fn.get("arguments", fn.get("args", {}))
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {"__raw__": args}
        out.append((str(fn.get("name", "")), dict(args or {})))
    return out


def messages_to_events(messages: list[dict[str, Any]]) -> list[TraceEvent]:
    """Freya call `messages` → trace events (message shape is VERIFY; several are accepted)."""
    events: list[TraceEvent] = []
    turn = 0
    for m in messages or []:
        role = str(m.get("role", "")).lower()
        if role in _USER_ROLES:
            turn += 1
            events.append(TraceEvent(turn=turn, kind=EventKind.USER, content=_text(m)))
        elif role in _AGENT_ROLES or role in _TOOL_CALL_ROLES:
            if _text(m) and role in _AGENT_ROLES:
                events.append(TraceEvent(turn=turn, kind=EventKind.AGENT, content=_text(m)))
            for name, args in _calls(m):
                events.append(TraceEvent(turn=turn, kind=EventKind.TOOL_CALL, tool=name, args=args))
        elif role in _TOOL_RESULT_ROLES:
            result: Any = m.get("result", m.get("content"))
            if isinstance(result, str):
                with contextlib.suppress(json.JSONDecodeError):
                    result = json.loads(result)
            err = isinstance(result, dict) and "error" in result
            events.append(
                TraceEvent(
                    turn=turn,
                    kind=EventKind.TOOL_RESULT,
                    tool=m.get("name") or m.get("toolName"),
                    ok=not err,
                    result=result,
                )
            )
    return events


def result_to_trace(
    run: dict[str, Any],
    call: dict[str, Any],
    handle: AgentHandle,
    by_persona: dict[str, ScenarioSpec],
) -> SimulationTrace:
    """Persona-run row (+ its call) → trace. Unknown endings never count as passes."""
    pid = str(run.get("personaId", ""))
    scenario = by_persona.get(pid)
    status = str(run.get("status", "")).lower()  # passed | failed | running | not_run
    ended = str(call.get("endedReason") or "").lower()
    if status in {"running", "not_run"}:
        end = EndReason.ERROR
    elif "timeout" in ended or "max-duration" in ended or "exceeded" in ended:
        end = EndReason.TIMEOUT
    elif "error" in ended or "fail" in ended:
        end = EndReason.ERROR
    elif "assistant" in ended or "agent" in ended:
        end = EndReason.AGENT_ENDED
    else:
        end = EndReason.USER_ENDED
    events = messages_to_events(list(call.get("messages") or []))
    criteria = call.get("successCriteriaResult") or {}
    assertions = [
        {"name": a.get("name"), "success": a.get("success"), "reason": a.get("reason")}
        for a in (call.get("assertionResults") or [])
        if isinstance(a, dict)
    ]
    blocked = [
        m.get("blocked_content") or m.get("blockedContent")
        for m in call.get("messages") or []
        if isinstance(m, dict) and (m.get("was_blocked") or m.get("wasBlocked"))
    ]
    return SimulationTrace(
        run_id=str(run.get("runId") or call.get("id") or f"{pid}:{status}"),
        candidate_id=handle.candidate_id,
        scenario_id=scenario.scenario_id if scenario else pid,
        adapter="freya",
        events=events,
        end_reason=end,
        error=f"persona run {status or 'unknown'}" if end is EndReason.ERROR else None,
        latency_s=float(call.get("duration") or 0.0),
        cost_usd=float(call.get("cost") or 0.0),
        # The message schema has no tool-call field (spec); parsed calls are a bonus, not a
        # contract. Without any, tool/write checks are unknown rather than failed (A2).
        tool_calls_observable=any(e.kind is EventKind.TOOL_CALL for e in events),
        platform_result={
            "status": status,
            "passed": status == "passed" and end not in {EndReason.ERROR, EndReason.TIMEOUT},
            "endedReason": ended,
            "callId": call.get("id"),
            "assertionsSummary": call.get("assertionsSummary"),
            "successCriteria": (
                {"success": criteria.get("success"), "reason": criteria.get("reason")}
                if isinstance(criteria, dict) and criteria
                else None
            ),
            "assertionResults": assertions,
            "problems": [
                {k: p.get(k) for k in ("title", "type", "severity")}
                for p in call.get("problems") or []
                if isinstance(p, dict)
            ],
            "guardrailBlocks": blocked,
            # Raw platform objects, kept until the parser is checked against a live run.
            "raw": {"run": run, "call": call},
        },
    )
