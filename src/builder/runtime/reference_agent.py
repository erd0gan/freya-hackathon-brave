"""Reference runtime: executes an AgentSpec with our own code.

Used by the mock adapter (and τ adapter) to simulate a candidate. On Freya the
same AgentSpec is compiled to platform primitives instead (memo §56); this
runtime mirrors those semantics so local scores stay comparable.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from builder.knowledge.retriever import Retriever
from builder.llm import LLMClient, LLMRequest, Message, Role, ToolCall, ToolDef
from builder.runtime.context_cleaner import clean
from builder.runtime.mutation_gate import ConversationLedger, check_mutation, natural_consent
from builder.runtime.tool_prompt import tool_description
from builder.schema.agent import AgentSpec, Architecture, GuardMode, RetrievalMode
from builder.schema.tool import ToolGraph
from builder.schema.trace import EventKind, TraceEvent

SEARCH_MODES = {
    RetrievalMode.BM25,
    RetrievalMode.HYBRID,
    RetrievalMode.DENSE,
    RetrievalMode.POLICY_GRAPH,
}
Executor = Callable[[str, dict[str, Any]], tuple[bool, Any]]

END_TOOL = ToolDef(
    name="end_conversation",
    description="End the conversation once the customer's requests are fully resolved "
    "and they have nothing else, or they asked to end.",
    parameters={"type": "object", "properties": {}},
)
THINK_TOOL = ToolDef(
    name="think",
    description="Think privately before acting: check policy preconditions, required "
    "confirmations and what the tool results mean. Has no side effects.",
    parameters={
        "type": "object",
        "properties": {"thought": {"type": "string"}},
        "required": ["thought"],
    },
)
SEARCH_TOOL = ToolDef(
    name="search_knowledge",
    description="Search the knowledge base. Give several phrasings (customer wording, formal "
    "term, procedure/tool name, exception). Returns the most relevant document sections.",
    parameters={
        "type": "object",
        "properties": {"queries": {"type": "array", "items": {"type": "string"}}},
        "required": ["queries"],
    },
)
MAX_INNER_STEPS = 10
CONFIRM_TOOL = ToolDef(
    name="request_action_confirmation",
    description="Before a confirmation-required write, request consent for the exact tool and "
    "arguments. This ends your turn and shows the customer the action. Wait for their reply.",
    parameters={
        "type": "object",
        "properties": {
            "tool": {"type": "string"},
            "arguments": {"type": "object"},
            "summary": {"type": "string"},
        },
        "required": ["tool", "arguments", "summary"],
    },
)


@dataclass
class TurnOutput:
    text: str
    ended: bool
    events: list[TraceEvent] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class ReferenceAgent:
    def __init__(
        self,
        spec: AgentSpec,
        tools: ToolGraph,
        llm: LLMClient,
        execute: Executor,
        *,
        cache_salt: str = "",
        retriever: Retriever | None = None,
    ) -> None:
        self.spec = spec
        self.tools = tools
        self.llm = llm
        self.execute = execute
        self.cache_salt = cache_salt
        self.retriever = retriever
        self.write_tools = {t.name for t in tools.writes()}
        self.ledger = ConversationLedger()
        self.messages: list[Message] = [Message(role="system", content=spec.render_system_prompt())]
        self.tool_defs = [
            ToolDef(
                name=t.name,
                description=tool_description(t, spec),
                parameters=t.inputs,
            )
            for t in tools.tools
        ] + [END_TOOL]
        if spec.architecture is Architecture.GUARDED_REACT_THINK:
            self.tool_defs.append(THINK_TOOL)
        if retriever is not None and spec.retrieval_mode in SEARCH_MODES:
            self.tool_defs.append(SEARCH_TOOL)
        self.gate_on = spec.mutation_guard in (
            GuardMode.DETERMINISTIC,
            GuardMode.PROMPT_AND_DETERMINISTIC,
        )
        if self.gate_on:
            self.tool_defs.append(CONFIRM_TOOL)

    def opening(self) -> str | None:
        if self.spec.first_message:
            self.messages.append(Message(role="assistant", content=self.spec.first_message))
            self.ledger.on_agent(self.spec.first_message)
        return self.spec.first_message

    def respond(self, user_text: str, turn: int) -> TurnOutput:
        self.ledger.on_user(user_text)
        self.messages.append(Message(role="user", content=user_text))
        out = TurnOutput(text="", ended=False)
        texts: list[str] = []
        for step in range(MAX_INNER_STEPS):
            resp = self.llm.complete(
                LLMRequest(
                    role=Role.AGENT,
                    model=self.spec.model,
                    messages=clean(self.messages, self.write_tools),
                    tools=self.tool_defs,
                    temperature=self.spec.temperature,
                    max_tokens=self.spec.max_tokens,
                    cache_salt=f"{self.cache_salt}:{turn}:{step}",
                )
            )
            out.prompt_tokens += resp.usage.prompt_tokens
            out.completion_tokens += resp.usage.completion_tokens
            out.cost_usd += resp.usage.cost_usd
            if resp.content and resp.content.strip():
                texts.append(resp.content.strip())
                # Keep true order: text, then the tool calls it precedes.
                out.events.append(
                    TraceEvent(turn=turn, kind=EventKind.AGENT, content=resp.content.strip())
                )
            self.messages.append(
                Message(role="assistant", content=resp.content, tool_calls=resp.tool_calls)
            )
            if not resp.tool_calls:
                break
            confirmation_question: str | None = None
            for tc in resp.tool_calls:
                if self.ledger.pending_confirmation is not None or out.ended:
                    result = {"error": "wait_for_user_before_more_calls"}
                else:
                    result = self._handle_call(tc, turn, out)
                    if self.ledger.pending_confirmation is not None:
                        confirmation_question = str(result["question"])
                self.messages.append(
                    Message(
                        role="tool",
                        tool_call_id=tc.id,
                        name=tc.name,
                        content=json.dumps(result, default=str, ensure_ascii=False),
                    )
                )
            if confirmation_question is not None:
                texts = [confirmation_question]
                self.messages.append(Message(role="assistant", content=texts[0]))
                out.events.append(TraceEvent(turn=turn, kind=EventKind.AGENT, content=texts[0]))
                break
            if out.ended:
                break
        out.text = "\n".join(texts)
        self.ledger.on_agent(out.text)
        return out

    def _handle_call(self, tc: ToolCall, turn: int, out: TurnOutput) -> Any:
        if tc.name == CONFIRM_TOOL.name and self.gate_on:
            name = tc.arguments.get("tool")
            args = tc.arguments.get("arguments")
            target = self.tools.get(str(name))
            if target is None or not target.is_write or not isinstance(args, dict):
                return {"error": "invalid confirmation action"}
            if natural_consent(target, self.ledger):
                # The customer just agreed to this action: asking again repeats the question
                # (live run 9 Oct). Nothing is consumed here; the real call is still gated.
                return {
                    "status": "already_confirmed",
                    "instruction": f"The customer already agreed. Call {target.name} now.",
                }
            self.ledger.request_confirmation(target.name, args)
            question = (
                f"{tc.arguments.get('summary', target.description)}\n"
                f"{target.name}: {json.dumps(args, ensure_ascii=False, sort_keys=True)}\n"
                "Do you confirm this exact action?"
            )
            return {"question": question}
        if tc.name == END_TOOL.name:
            out.ended = True
            return {"status": "ending"}
        if tc.name == SEARCH_TOOL.name and self.retriever is not None:
            queries = tc.arguments.get("queries") or [str(tc.arguments)]
            if isinstance(queries, str):
                queries = [queries]
            hits = self.retriever.render(self.retriever.search([str(q) for q in queries]))
            out.events.append(
                TraceEvent(
                    turn=turn,
                    kind=EventKind.RETRIEVAL,
                    args={"queries": queries},
                    result=[h["doc"] for h in hits],
                )
            )
            # Retrieved facts (e.g. product codes) count as grounding evidence for the gate.
            self.ledger.tool_outputs.append(json.dumps(hits, ensure_ascii=False))
            return {"results": hits}
        if tc.name == THINK_TOOL.name:
            out.events.append(
                TraceEvent(turn=turn, kind=EventKind.NOTE, content=str(tc.arguments)[:2000])
            )
            return {"status": "ok"}
        out.events.append(
            TraceEvent(turn=turn, kind=EventKind.TOOL_CALL, tool=tc.name, args=tc.arguments)
        )
        spec = self.tools.get(tc.name)
        if spec is not None and spec.is_write and self.gate_on:
            chk = check_mutation(spec, tc.arguments, self.ledger, self.tools)
            out.events.append(
                TraceEvent(
                    turn=turn,
                    kind=EventKind.MUTATION_CHECK,
                    tool=tc.name,
                    ok=chk.approved,
                    content=chk.reason,
                    data=chk.model_dump(mode="json"),
                )
            )
            if not chk.approved:
                only_consent_missing = not chk.confirmation_satisfied and all(
                    (
                        chk.goal_alignment,
                        chk.arguments_grounded,
                        chk.preconditions_satisfied,
                        chk.policy_allows,
                        chk.duplicate_risk_clear,
                        chk.state_is_fresh,
                        chk.target_unambiguous,
                    )
                )
                if only_consent_missing:
                    # Turn the blocked call into an exact, action-bound consent request, so a
                    # model that skips request_action_confirmation is not stuck (live run 9 Oct).
                    self.ledger.request_confirmation(spec.name, tc.arguments)
                    summary = spec.inferred_purpose or spec.description or spec.name
                    exact = json.dumps(tc.arguments, ensure_ascii=False, sort_keys=True)
                    result = {
                        "error": "blocked_by_safeguard",
                        "status": "confirmation_required",
                        "question": (
                            f"Before I do this, please confirm: {summary}\n"
                            f"{spec.name}: {exact}\n"
                            "Do you confirm this exact action?"
                        ),
                        "instruction": "The customer was asked to confirm. If they agree, call "
                        "the tool again with the same arguments.",
                    }
                else:
                    result = {"error": "blocked_by_safeguard", "reasons": chk.reasons}
                out.events.append(
                    TraceEvent(
                        turn=turn, kind=EventKind.TOOL_RESULT, tool=tc.name, ok=False, result=result
                    )
                )
                return result
        ok, result = self.execute(tc.name, tc.arguments)
        self.ledger.on_tool(tc.name, tc.arguments, ok, result)
        out.events.append(
            TraceEvent(turn=turn, kind=EventKind.TOOL_RESULT, tool=tc.name, ok=ok, result=result)
        )
        return result
