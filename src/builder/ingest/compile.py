"""Top-level context compiler: InputBundle → DomainSpec (memo §9)."""

from __future__ import annotations

from builder.ingest.knowledge_compiler import compile_knowledge
from builder.ingest.policy_compiler import compile_policy
from builder.ingest.task_compiler import compile_task
from builder.ingest.tool_compiler import compile_tools
from builder.llm import LLMClient
from builder.manifest import sha256_json
from builder.schema.bundle import DomainSpec, InputBundle


def compile_domain(bundle: InputBundle, llm: LLMClient | None = None) -> DomainSpec:
    """Deterministic when `llm` is None; otherwise LLM passes refine each spec."""
    tools = compile_tools(bundle.tools, bundle.policy_text, llm)
    policy = compile_policy(bundle.policy_text, tools, llm)
    # Policy may mention tools the tool pass could not tie to confirmation.
    task = compile_task(bundle, tools, llm)
    knowledge = compile_knowledge(bundle, tools)
    return DomainSpec(
        task=task,
        policy=policy,
        tools=tools,
        knowledge=knowledge,
        input_hash=sha256_json(bundle.model_dump(mode="json")),
    )
