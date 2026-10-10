"""OPRO-style fallback optimizer (memo §3.11): score-conditioned block proposals."""

from __future__ import annotations

from pydantic import BaseModel

from builder.llm import LLMClient, Message, Role
from builder.schema.agent import AgentSpec
from builder.schema.eval import CandidateEvaluation
from builder.schema.failure import FailureType


class Proposal(BaseModel):
    new_text: str
    rationale: str = ""


OPRO_PROMPT = """Below are versions of one section of an agent's system prompt with their test
success rates and their most common failure types. Write a NEW version likely to score higher.
Generalize; never include specific ids, names or amounts. Return JSON only."""


def propose(
    llm: LLMClient,
    block: str,
    history: list[tuple[AgentSpec, CandidateEvaluation, list[FailureType]]],
    salt: str = "",
) -> Proposal:
    rows = []
    for spec, ev, fails in sorted(history, key=lambda h: h[1].mean)[-5:]:
        text = spec.system_prompt_sections.get(block, "")
        rows.append(f"### score={ev.mean:.2f} failures={[f.value for f in fails[:4]]}\n{text}")
    return llm.complete_structured(
        Role.OPTIMIZER,
        Proposal,
        [
            Message(role="system", content=OPRO_PROMPT),
            Message(role="user", content=f"SECTION: {block}\n\n" + "\n\n".join(rows)),
        ],
        cache_salt=salt,
    )
