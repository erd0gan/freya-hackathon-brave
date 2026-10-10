"""Compile AgentSpec / ScenarioSpec to Freya platform objects (memo §55–57).

Field names and limits follow the published OpenAPI spec (`vendor/freya/openapi.json`,
9 Oct 2026): their *shape* is CONFIRMED. Runtime *behaviour* (e.g. whether
pre_process_code can reject a call, valid `handler` values) stays VERIFY until a
live key is used.
"""

from __future__ import annotations

import json
import re
from typing import Any

from builder.runtime.tool_prompt import tool_description
from builder.schema.agent import AgentSpec
from builder.schema.scenario import ArgMatch, ScenarioSpec, ToolCallAssertion
from builder.schema.status import Status
from builder.schema.task import Channel
from builder.schema.tool import ToolGraph, ToolSpec

FIELD_STATUS: dict[str, Status] = {
    "llmConfig.systemPrompt": Status.CONFIRMED,  # spec: ≤200000 chars
    "llmConfig.maxTokens/temperature": Status.CONFIRMED,  # spec: 1..4000 / 0..2
    "llmConfig.tools[].function": Status.CONFIRMED,  # name, handler*, description, parameters
    "llmConfig.tools[].function.handler": Status.VERIFY,  # required; valid values undocumented
    "llmConfig.tools[].pre_process_code": Status.VERIFY,  # field exists; can it reject? (§68 Q4)
    "llmConfig.tools[].post_process_code": Status.VERIFY,
    "guardrailsConfig": Status.CONFIRMED,
    "sttConfig.confidenceReask": Status.CONFIRMED,  # object {enabled, threshold, ...}
    "ttsConfig.speechNormalization": Status.CONFIRMED,
    "callTimeoutSettings.maximumDuration": Status.CONFIRMED,
    # name, prompt, successCriteria, systemPrompt, evaluationPrompt, tags
    "persona fields": Status.CONFIRMED,
    "unitTest.assertions(tool_call)": Status.CONFIRMED,  # tool, args, argsMatch, negate
    "simulation result mapping": Status.VERIFY,  # version-persona-runs + call messages format
}

SYSTEM_PROMPT_LIMIT = 200_000  # spec
MAX_TOKENS_LIMIT = 4000  # spec: llmConfig.maxTokens maximum


class FreyaCompileError(ValueError):
    pass


# Tools that exist only in our local ReferenceAgent (runtime/reference_agent.py). The shipped
# agent must not be told to call them (GAP_ANALYSIS A1).
LOCAL_ONLY_TOOLS = ("think", "search_knowledge", "request_action_confirmation", "end_conversation")
_PLATFORM_REWRITES: list[tuple[str, str]] = [
    # local MutationGate feedback loop: no such error on the platform
    (r"\s*If a call is rejected with\s+\"blocked_by_safeguard\"[^.]*\.(\s*\w[^.]*\.)?", ""),
    # C2: the think tool becomes an internal checklist (memo §3.6, last paragraph)
    (
        r"Use the `think` tool \(no side effects\) at these moments only:",
        "Silently re-run your checklist (rules, confirmation, arguments) at these moments only:",
    ),
    (
        r"through the `search_knowledge` tool \(or the platform's knowledge search\)",
        "through the platform's knowledge base search",
    ),
]


def platform_system_prompt(spec: AgentSpec) -> str:
    """The prompt actually shipped: local-runtime affordances rewritten for the platform."""
    prompt = spec.render_system_prompt()
    for pattern, repl in _PLATFORM_REWRITES:
        prompt = re.sub(pattern, repl, prompt)
    return prompt


def local_tool_references(prompt: str) -> list[str]:
    """Local-only tool names still referenced as tools in a platform prompt."""
    return [t for t in LOCAL_ONLY_TOOLS if re.search(rf"`{t}`|\b{t}\(|\b{t} tool\b", prompt)]


def preprocess_guard_code(t: ToolSpec, language: str = "javascript") -> str | None:
    """Deterministic argument check for write tools, if pre_process_code may reject (VERIFY).

    Only checks things that need no conversation context: required args present
    and enum values valid. Confirmation/grounding stay in the prompt on Freya.
    """
    if not t.is_write:
        return None
    required = t.required_args
    enums = {
        k: v["enum"]
        for k, v in (t.inputs.get("properties") or {}).items()
        if isinstance(v, dict) and "enum" in v
    }
    if language == "javascript":
        return (
            "// generated guard (VERIFY semantics of pre_process_code)\n"
            f"const required = {json.dumps(required)};\n"
            f"const enums = {json.dumps(enums)};\n"
            "for (const k of required) { if (args[k] === undefined || args[k] === null || "
            "args[k] === '') { throw new Error(`missing argument ${k}; ask the customer`); } }\n"
            "for (const [k, vals] of Object.entries(enums)) { if (args[k] !== undefined && "
            "!vals.includes(args[k])) { throw new Error(`${k} must be one of ${vals}`); } }\n"
            "return args;\n"
        )
    return (
        "# generated guard (VERIFY semantics of pre_process_code)\n"
        f"required = {required!r}\nenums = {enums!r}\n"
        "for k in required:\n"
        "    if args.get(k) in (None, ''):\n"
        "        raise ValueError(f'missing argument {k}; ask the customer')\n"
        "for k, vals in enums.items():\n"
        "    if k in args and args[k] not in vals:\n"
        "        raise ValueError(f'{k} must be one of {vals}')\n"
        "result = args\n"
    )


def compile_tools(
    spec: AgentSpec, tools: ToolGraph, preprocess_language: str | None = None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in tools.tools:
        params = t.inputs if t.inputs.get("type") else {"type": "object", **t.inputs}
        entry: dict[str, Any] = {
            "type": "function",
            "function": {
                "name": t.name,
                # `handler` is required by the spec; valid values are undocumented (VERIFY).
                # Task-supplied handlers win; otherwise the tool name is used.
                "handler": str(t.platform.get("handler") or t.name),
                "description": tool_description(t, spec),
                "parameters": params,
            },
        }
        # Pass through platform-native fields provided with the task (handler, config, ...).
        for k in ("transition_to",):
            if k in t.platform:
                entry["function"][k] = t.platform[k]
        for k in ("config", "response_mapping", "extraction_schema"):
            if k in t.platform:
                entry[k] = t.platform[k]
        if preprocess_language:
            code = preprocess_guard_code(t, preprocess_language)
            if code:
                entry["pre_process_code"] = code
        out.append(entry)
    return out


_CARD_NUMBERS = re.compile(
    r"card number|full card|\bPAN\b|last (four|4) digits|kart numara|son (4|dört) hane",
    re.IGNORECASE,
)


def default_guardrails(spec: AgentSpec, prompt: str = "") -> list[dict[str, Any]]:
    """Output guardrails (§56). The full-card-number regex is added only when the task talks
    about card numbers: elsewhere it blocks legitimate 13+ digit policy/claim/tracking
    numbers and loops on `regenerate` (GAP_ANALYSIS A5, PLAN principle 9)."""
    rules: list[dict[str, Any]] = []
    if _CARD_NUMBERS.search(prompt):
        rules.append(
            {
                "id": "no_full_pan",
                "type": "regex",
                "pattern": r"\b(?:\d[ -]?){13,19}\b",
                "action": "regenerate",
                "enabled": True,
                "description": "Never read out a full card number.",
            }
        )
    return [*rules, *spec.guardrails]


def compile_agent(
    spec: AgentSpec,
    tools: ToolGraph,
    *,
    provider: str = "openai",
    model: str | None = None,
    channel: Channel = Channel.TEXT,
    base_config: dict[str, Any] | None = None,
    preprocess_language: str | None = None,
    max_call_seconds: int | None = None,
) -> dict[str, Any]:
    """AgentSpec → Freya `config` (memo §55.1, §56). Starts from the platform default (B0).

    Without `base_config` the result is an *overlay*: only the fields the builder sets, to be
    merged onto `GET /agents/defaults` (see `submission_config`). `max_call_seconds` belongs
    to simulation deployments only; submissions keep the platform's call duration.
    """
    prompt = platform_system_prompt(spec)
    if len(prompt) > SYSTEM_PROMPT_LIMIT:
        raise FreyaCompileError(f"system prompt too long: {len(prompt)} > {SYSTEM_PROMPT_LIMIT}")
    cfg: dict[str, Any] = json.loads(json.dumps(base_config or {}))
    llm = cfg.setdefault("llmConfig", {})
    chosen_model = spec.model or model or llm.get("model")
    llm.update(
        {
            "provider": spec.platform_overrides.get("provider", llm.get("provider", provider)),
            "systemPrompt": prompt,
            "temperature": min(2.0, max(0.0, spec.temperature)),
            "maxTokens": min(MAX_TOKENS_LIMIT, max(1, spec.max_tokens)),
            "tools": compile_tools(spec, tools, preprocess_language),
        }
    )
    if chosen_model:
        llm["model"] = chosen_model
    else:
        llm.pop("model", None)  # never emit `model: null`; the platform default supplies it
    if spec.first_message:
        llm["firstMessage"] = spec.first_message
        llm.setdefault("firstMessageMode", "assistant-speaks-first")
    if spec.farewell_message:
        llm["farewellMessage"] = spec.farewell_message
    cfg["guardrailsConfig"] = {
        **(cfg.get("guardrailsConfig") or {}),
        "guardrails_enabled": True,
        "custom_rules_enabled": True,
        "rules": default_guardrails(spec, prompt),
        "max_regenerate_retries": 2,
    }
    if channel is Channel.VOICE or spec.voice_policy:
        vp = spec.voice_policy or {}
        stt = cfg.setdefault("sttConfig", {})
        stt["confidenceReask"] = {
            **(stt.get("confidenceReask") or {}),
            "enabled": True,
            "threshold": float(vp.get("confidence_reask", 0.6)),
        }
        stt.setdefault("language", spec.language if spec.language != "en" else "multi")
        tts = cfg.setdefault("ttsConfig", {})
        tts["speechNormalization"] = vp.get(
            "speech_normalization",
            {
                **(tts.get("speechNormalization") or {}),
                "emails": True,
                "phoneNumbers": True,
                "identityNumbers": True,
                "generalNumbers": True,
                "advanced": {"numbers": True, "money": True, "dates": True, "times": True},
            },
        )
    if max_call_seconds:
        cto = cfg.get("callTimeoutSettings") or {}
        cto["maximumDuration"] = max(0, min(14400, int(max_call_seconds)))
        cfg["callTimeoutSettings"] = cto
    for path, value in spec.platform_overrides.items():
        if "." in path:
            _set_path(cfg, path, value)
    return cfg


CONFIG_REQUIRED = {  # vendor/freya/openapi.json, POST /agents body.config
    "": ["llmConfig", "ttsConfig", "sttConfig", "complianceConfig"],
    "llmConfig": ["provider", "model", "systemPrompt"],
    "ttsConfig": ["provider", "voiceId"],
    "sttConfig": ["provider"],
}


def validate_config(cfg: dict[str, Any]) -> list[str]:
    """Required-field and limit check of a full agent config against the vendored spec."""
    problems: list[str] = []
    for block, fields in CONFIG_REQUIRED.items():
        obj = cfg if not block else cfg.get(block)
        if not isinstance(obj, dict):
            problems.append(f"missing {block}")
            continue
        for f in fields:
            v = obj.get(f)
            if v is None or v == "":
                problems.append(f"missing {block + '.' if block else ''}{f}")
    llm = cfg.get("llmConfig") or {}
    if len(str(llm.get("systemPrompt", ""))) > SYSTEM_PROMPT_LIMIT:
        problems.append("llmConfig.systemPrompt exceeds 200000 characters")
    leftover = local_tool_references(str(llm.get("systemPrompt", "")))
    if leftover:
        problems.append(f"prompt references local-only tools: {leftover}")
    return problems


def submission_config(
    spec: AgentSpec,
    tools: ToolGraph,
    *,
    channel: Channel = Channel.TEXT,
    base_config: dict[str, Any] | None = None,
    model: str | None = None,
    preprocess_language: str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """The one export path for files and API deploys (GAP_ANALYSIS A5).

    With the platform default config this returns a full config and its validation problems.
    Without it, an overlay (`complete=False` in `_builder`) that must be merged onto
    `GET /agents/defaults` before `POST /agents`; it is never presented as deployable.
    """
    cfg = compile_agent(
        spec,
        tools,
        model=model,
        channel=channel,
        base_config=base_config,
        preprocess_language=preprocess_language,
    )
    if base_config is None:
        return cfg, ["overlay only: merge onto GET /agents/defaults before POST /agents"]
    return cfg, validate_config(cfg)


def merge_overlay(default: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge an exported overlay onto the platform default config (lists replace)."""
    out = json.loads(json.dumps(default))
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = merge_overlay(out[k], v)
        else:
            out[k] = v
    return out


def _set_path(obj: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur = obj
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
    cur[parts[-1]] = value


# ---------------------------------------------------------------- scenarios → personas/tests


def compile_persona(s: ScenarioSpec) -> dict[str, Any]:
    """ScenarioSpec → Freya persona (memo §55.3, §57.4)."""
    facts = "\n".join(f"- {k}: {v}" for k, v in s.persona.hidden_facts.items())
    prompt = s.persona.prompt + (f"\n\nFacts you can share when asked:\n{facts}" if facts else "")
    criteria = s.oracle.success_criteria or _criteria_from_oracle(s)
    return {
        "name": f"{s.scenario_id}",
        "prompt": prompt,
        "systemPrompt": s.persona.system_prompt
        or f"Speak {s.persona.language}. Style: {s.persona.style.value}.",
        "successCriteria": criteria,
        "evaluationPrompt": s.oracle.evaluation_prompt or _evaluation_prompt(s, criteria),
        "tags": [s.category.value, f"split:{s.split.value}", f"complexity:{s.complexity}"],
    }


def _criteria_from_oracle(s: ScenarioSpec) -> str:
    lines: list[str] = []
    for a in s.oracle.tool_calls:
        verb = "must NOT call" if a.negated else "must call"
        lines.append(f"The agent {verb} {a.tool}" + (f" with {a.args}" if a.args else "") + ".")
    lines += [f"The agent tells the customer: {c}." for c in s.oracle.required_communication]
    lines += [f"The agent never claims: {c}." for c in s.oracle.forbidden_claims]
    if s.oracle.expect_refusal:
        lines.append("The agent correctly declines the prohibited request.")
    if s.oracle.expect_escalation:
        lines.append("The agent transfers to a human.")
    return "\n".join(lines) or s.user_goal


def _evaluation_prompt(s: ScenarioSpec, criteria: str) -> str:
    return (
        "Judge the transcript strictly. PASS only if every criterion holds and the agent did not "
        "violate the business policy or claim an action succeeded without it happening.\n"
        f"Criteria:\n{criteria}"
    )


def compile_assertion(a: ToolCallAssertion) -> dict[str, Any]:
    """ToolCallAssertion → Freya unit-test `tool_call` assertion (shape from the spec)."""
    return {
        "type": "tool_call",
        "tool": a.tool,
        "args": a.args,
        "argsMatch": "exact" if a.match is ArgMatch.EXACT else "subset",
        "negate": a.negated,
    }
