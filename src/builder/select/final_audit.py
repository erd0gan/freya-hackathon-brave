"""Final audit before export (memo §43). Any failure → fall back to the next candidate."""

from __future__ import annotations

from pydantic import BaseModel, Field

from builder.adapters.base import PlatformAdapter
from builder.adapters.freya.compile import (
    SYSTEM_PROMPT_LIMIT,
    FreyaCompileError,
    compile_agent,
    local_tool_references,
)
from builder.optimize.overfit_lint import lint_text
from builder.schema.agent import AgentSpec, GuardMode
from builder.schema.bundle import DomainSpec


class AuditResult(BaseModel):
    ok: bool
    problems: list[str] = Field(default_factory=list)


def audit(
    spec: AgentSpec,
    domain: DomainSpec,
    adapter: PlatformAdapter | None = None,
    practice_literals: set[str] | None = None,
) -> AuditResult:
    problems: list[str] = []
    prompt = spec.render_system_prompt()
    if not prompt.strip():
        problems.append("empty prompt")
    if len(prompt) > SYSTEM_PROMPT_LIMIT:
        problems.append(f"prompt too long ({len(prompt)})")
    names = set(domain.tools.names())
    problems += [
        f"override for unknown tool {t}" for t in spec.tool_description_overrides if t not in names
    ]
    for name, text in spec.system_prompt_sections.items():
        problems += [f"{name}: {p}" for p in lint_text(text, practice_literals or set(), domain)]
    if "domain_rules" in spec.system_prompt_sections:
        # A rewritten rules block must not silently drop rules (GAP_ANALYSIS B6).
        lost = [r.id for r in domain.policy.rules if r.id not in prompt]
        if lost:
            problems.append(f"domain rules missing from prompt: {lost[:8]}")
    if domain.tools.writes() and spec.mutation_guard is GuardMode.NONE:
        problems.append("mutation guard disabled although write tools exist")
    if not spec.completion_verifier and "completion_verifier" not in spec.system_prompt_sections:
        problems.append("completion verifier not configured")
    try:
        shipped = compile_agent(spec, domain.tools)["llmConfig"]["systemPrompt"]
        leftover = local_tool_references(shipped)
        if leftover:
            problems.append(f"shipped prompt references local-only tools: {leftover}")
    except FreyaCompileError as exc:
        problems.append(f"freya compile: {exc}")
    if adapter is not None:
        v = adapter.validate_agent(spec)
        problems += [f"adapter: {e}" for e in v.errors]
    return AuditResult(ok=not problems, problems=problems)
