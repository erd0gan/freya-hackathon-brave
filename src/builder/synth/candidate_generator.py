"""Candidate generation (memo §8, §18.1, §49): structurally different AgentSpecs.

Candidates differ in architecture (block set, block order, guard mode, tools),
not in wording (memo §3.15). B0 and A are always produced: B0 is the floor
baseline (memo §25), A is the always-valid fallback (memo §8A, §43).
"""

from __future__ import annotations

from collections.abc import Callable

from builder.schema.agent import AgentSpec, Architecture, GuardMode, RetrievalMode
from builder.schema.bundle import DomainSpec
from builder.schema.task import Channel
from builder.synth import fewshot
from builder.synth import prompt_blocks as pb
from builder.synth.architecture_selector import select_architectures

Block = Callable[[DomainSpec], str]

CORE: list[tuple[str, Block]] = [
    ("role", pb.role),
    ("global_policy", pb.global_policy),
    ("rule_priority", pb.rule_priority),
    ("domain_rules", pb.domain_rules),
    ("action_protocol", pb.action_protocol),
    ("coordination", pb.coordination),
    ("operational_state", pb.operational_state),
    ("tool_use_policy", pb.tool_use_policy),
    ("knowledge_retrieval_policy", pb.knowledge_retrieval_policy),
    ("mutation_guard", pb.mutation_guard),
    ("post_write_verify", pb.post_write_verify),
    ("completion_verifier", pb.completion_verifier),
    ("outcome_statement", pb.outcome_statement),
    ("over_caution_guard", pb.over_caution_guard),
    ("voice_policy", pb.voice_policy),
    ("language_policy", pb.language_policy),
]

EXTRA: dict[Architecture, list[tuple[str, Block]]] = {
    Architecture.GUARDED_REACT_THINK: [("planning", pb.think_usage)],
    Architecture.PLANNER_EXECUTOR: [("planning", pb.planning)],
    Architecture.FINITE_STATE: [("workflow", pb.workflow)],
}

SHORT_ID = {
    Architecture.PLATFORM_DEFAULT: "B0",
    Architecture.ONE_SHOT: "A",
    Architecture.GUARDED_REACT: "C1",
    Architecture.GUARDED_REACT_THINK: "C2",
    Architecture.PLANNER_EXECUTOR: "C3",
    Architecture.FINITE_STATE: "C4",
    Architecture.POLICY_FIRST: "C5",
    Architecture.RETRIEVAL_FIRST: "C6",
}


def _sections(d: DomainSpec, blocks: list[tuple[str, Block]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, fn in blocks:
        text = fn(d).strip()
        if text:
            out[name] = text
    return out


def _retrieval_mode(d: DomainSpec) -> RetrievalMode:
    if not d.knowledge.documents:
        return RetrievalMode.NONE
    if d.knowledge.total_chars > pb.INLINE_DOC_CHAR_LIMIT:
        return RetrievalMode.HYBRID
    return RetrievalMode.ON_DEMAND


def _base(d: DomainSpec, arch: Architecture, **kw: object) -> AgentSpec:
    spec = AgentSpec(
        candidate_id=SHORT_ID[arch],
        architecture=arch,
        language=d.task.language,
        retrieval_mode=_retrieval_mode(d),
        temperature=0.2,
        voice_policy={"confidence_reask": 0.6} if d.task.channel is Channel.VOICE else None,
    )
    return spec.model_copy(update=kw)


def platform_default(d: DomainSpec) -> AgentSpec:
    """B0: task + raw rules only, no safeguards — the floor (memo §25, §55.1)."""
    sections = {"role": pb.role(d), "domain_rules": pb.domain_rules(d, verbose=False)}
    return _base(
        d,
        Architecture.PLATFORM_DEFAULT,
        system_prompt_sections=sections,
        mutation_guard=GuardMode.NONE,
        completion_verifier=False,
        temperature=0.7,
        lineage_note="platform-default baseline",
    )


def one_shot(d: DomainSpec) -> AgentSpec:
    """A: compact compiled prompt + deterministic gate. Always valid; the fallback."""
    blocks = [
        ("role", pb.role),
        ("global_policy", pb.global_policy),
        ("domain_rules", lambda x: pb.domain_rules(x, verbose=False)),
        ("tool_use_policy", pb.tool_use_policy),
        ("outcome_statement", pb.outcome_statement),
        ("voice_policy", pb.voice_policy),
        ("language_policy", pb.language_policy),
    ]
    return _base(
        d,
        Architecture.ONE_SHOT,
        system_prompt_sections=_sections(d, blocks),
        mutation_guard=GuardMode.PROMPT_AND_DETERMINISTIC,
        lineage_note="one-shot compiled fallback",
    )


def build_candidate(d: DomainSpec, arch: Architecture) -> AgentSpec:
    if arch is Architecture.PLATFORM_DEFAULT:
        return platform_default(d)
    if arch is Architecture.ONE_SHOT:
        return one_shot(d)
    blocks = list(CORE)
    if arch is Architecture.POLICY_FIRST:
        # Rules and precedence first, plus an explicit per-turn rule check.
        first = {"domain_rules", "rule_priority"}
        blocks = [b for b in CORE if b[0] in first] + [b for b in CORE if b[0] not in first]
        blocks.insert(0, ("role", pb.role))
        blocks = list(dict(blocks).items())
        blocks.append(
            (
                "planning",
                lambda _: (
                    "Before every action, name to yourself which rule ids apply and "
                    "check each one; if a rule blocks the action, explain that to the customer."
                ),
            )
        )
    if arch is Architecture.RETRIEVAL_FIRST:
        first = {"knowledge_retrieval_policy"}
        blocks = (
            [("role", pb.role)]
            + [b for b in CORE if b[0] in first]
            + [b for b in CORE if b[0] not in first and b[0] != "role"]
        )
    blocks += EXTRA.get(arch, [])
    reordered = arch in (Architecture.POLICY_FIRST, Architecture.RETRIEVAL_FIRST)
    shots = (
        fewshot.examples()
        if arch
        in (
            Architecture.GUARDED_REACT,
            Architecture.POLICY_FIRST,
        )
        else []
    )
    return _base(
        d,
        arch,
        system_prompt_sections=_sections(d, blocks),
        few_shot_examples=shots,
        planning_mode="receding_horizon" if arch is Architecture.PLANNER_EXECUTOR else "none",
        section_order=[name for name, _ in blocks] if reordered else [],
        mutation_guard=GuardMode.PROMPT_AND_DETERMINISTIC,
        lineage_note=f"initial {arch.value}",
    )


def generate_candidates(
    d: DomainSpec, n: int = 6, include_baselines: bool = True
) -> list[AgentSpec]:
    """n structurally different candidates (+ B0 and A when include_baselines)."""
    archs = select_architectures(d, n)
    cands = [build_candidate(d, a) for a in archs]
    if include_baselines:
        cands = [platform_default(d), one_shot(d), *cands]
    return cands
