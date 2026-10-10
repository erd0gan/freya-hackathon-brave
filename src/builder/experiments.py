"""Ablation ladder (memo §25–26, PLAN Faz 10).

Runtime ladder B0→B5 is derived from candidate C1 by removing components, so
each step isolates one mechanism. Builder-level steps B6–B10 (test generator,
GEPA-lite, lessons, population, crossover) are pipeline flags and are compared
with `builder run` on the same scenario file.
"""

from __future__ import annotations

from builder.schema.agent import AgentSpec, Architecture, GuardMode, RetrievalMode
from builder.schema.bundle import DomainSpec
from builder.synth.candidate_generator import build_candidate, platform_default

LADDER_DOC = {
    "B0": "platform-default prompt (floor)",
    "B1": "B0 + explicit operational state + global invariants",
    "B2": "B1 + think/checklist",
    "B3": "B2 + MutationGate (prompt + deterministic)",
    "B4": "B3 + completion verifier + outcome statement",
    "B5": "B4 + retrieval policy (hybrid/BM25 when documents exist)",
}


def ladder(d: DomainSpec) -> list[AgentSpec]:
    full = build_candidate(d, Architecture.GUARDED_REACT_THINK)
    s = full.system_prompt_sections
    b0 = platform_default(d).model_copy(update={"candidate_id": "B0"})

    def mk(cid: str, blocks: list[str], **kw: object) -> AgentSpec:
        return full.model_copy(
            update={
                "candidate_id": cid,
                "system_prompt_sections": {k: s[k] for k in blocks if k in s},
                "lineage_note": LADDER_DOC[cid],
                "few_shot_examples": [],
                **kw,
            },
            deep=True,
        )

    base = [
        "role",
        "rule_priority",
        "domain_rules",
        "tool_use_policy",
        "voice_policy",
        "language_policy",
    ]
    b1 = [*base, "global_policy", "operational_state", "action_protocol", "coordination"]
    b2 = [*b1, "planning"]
    b3 = [*b2, "mutation_guard", "post_write_verify"]
    b4 = [*b3, "completion_verifier", "outcome_statement", "over_caution_guard"]
    b5 = [*b4, "knowledge_retrieval_policy"]
    no_gate = {
        "mutation_guard": GuardMode.NONE,
        "completion_verifier": False,
        "retrieval_mode": RetrievalMode.NONE,
    }
    return [
        b0,
        mk("B1", b1, architecture=Architecture.GUARDED_REACT, **no_gate),
        mk("B2", b2, **no_gate),
        mk(
            "B3",
            b3,
            mutation_guard=GuardMode.PROMPT_AND_DETERMINISTIC,
            completion_verifier=False,
            retrieval_mode=RetrievalMode.NONE,
        ),
        mk("B4", b4, retrieval_mode=RetrievalMode.NONE),
        mk("B5", b5),
    ]
