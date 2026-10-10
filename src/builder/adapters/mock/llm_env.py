"""LLM-simulated tool backend for domains without a hand-written mock (memo §23, §58).

Turns any InputBundle into a DomainPack: an LLM invents a small consistent seed
database once, then answers each tool call against the current state and
returns a state patch. Lets the builder self-test unseen tasks when the
platform offers no simulator. Deterministic via cache + temperature 0.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from builder.adapters.mock.env import DomainPack, Environment, ToolError
from builder.llm import LLMClient, Message, Role
from builder.schema.bundle import DomainSpec, InputBundle
from builder.testsynth.statediff import set_path


class SeedDB(BaseModel):
    database: dict[str, Any]


class ToolAnswer(BaseModel):
    ok: bool = True
    result: Any = None
    error: str = ""
    state_patch: list[dict[str, Any]] = Field(default_factory=list)


SEED_PROMPT = """Create a small, realistic, internally consistent backend database (JSON) for
the customer-service domain below, so every tool can be exercised: 3-5 customers/accounts with
ids, related records each tool reads or writes, some edge cases (an inactive record, an
ineligible item, an expired item). Also include a top-level "session": {} object. Return JSON
{"database": {...}} only."""

CALL_PROMPT = """You are the backend for a tool call. Answer strictly from DATABASE and the
TOOL definition and BUSINESS RULES that the backend itself enforces (e.g. not found, invalid
state). Never be lenient: missing/invalid arguments → ok=false with an error. If the call
changes data, return the minimal state_patch as [{"path": "a.b.c", "value": ...}] (dotted
paths into DATABASE; append to a list with its next index). Record successful identity checks
under "session".
You cannot see the conversation. NEVER reject a call for conversational rules (customer
confirmation or consent, reading details back, explanations, disclosures, tone): those are
the agent's job and are checked elsewhere. Reject only for data-level reasons visible in
DATABASE or ARGS (not found, wrong status, limit exceeded, ineligible, invalid argument).
Return JSON only."""


def make_llm_pack(bundle: InputBundle, domain: DomainSpec, llm: LLMClient) -> DomainPack:
    tools_desc = json.dumps(
        [
            {"name": t.name, "description": t.description, "parameters": t.inputs}
            for t in domain.tools.tools
        ],
        ensure_ascii=False,
    )
    seed = llm.complete_structured(
        Role.BUILDER,
        SeedDB,
        [
            Message(role="system", content=SEED_PROMPT),
            Message(
                role="user",
                content=f"TASK:\n{bundle.task_description}\n\nPOLICY:\n"
                f"{bundle.policy_text[:20000]}\n\nTOOLS:\n{tools_desc}",
            ),
        ],
        temperature=0.0,
        max_tokens=6000,
    ).database

    def make_impl(tool_name: str) -> Any:
        spec = domain.tools.get(tool_name)

        tool_json = json.dumps(
            spec.model_dump(include={"name", "description", "inputs"}) if spec else tool_name,
            ensure_ascii=False,
        )

        def impl(env: Environment, args: dict[str, Any]) -> Any:
            ans = llm.complete_structured(
                Role.ANALYZER,
                ToolAnswer,
                [
                    Message(role="system", content=CALL_PROMPT),
                    Message(
                        role="user",
                        content=(
                            f"TOOL: {tool_json}\n"
                            f"ARGS: {json.dumps(args, ensure_ascii=False)}\n"
                            f"BUSINESS RULES:\n{bundle.policy_text[:8000]}\n"
                            f"DATABASE:\n{json.dumps(env.db, ensure_ascii=False)[:30000]}"
                        ),
                    ),
                ],
                temperature=0.0,
                cache_salt=env.salt,
            )
            if not ans.ok:
                raise ToolError(ans.error or "request failed")
            for op in ans.state_patch:
                if "path" in op:
                    set_path(env.db, str(op["path"]), op.get("value"))
            return ans.result

        return impl

    return DomainPack(
        name=f"llm:{bundle.task_id or 'task'}",
        bundle=bundle,
        seed_db=seed,
        tools={t.name: make_impl(t.name) for t in domain.tools.tools},
        deterministic=False,
    )
