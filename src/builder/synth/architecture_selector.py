"""Rule-based architecture selector (memo §21). Returns architectures ranked by fit."""

from __future__ import annotations

from builder.schema.agent import Architecture
from builder.schema.bundle import DomainSpec
from builder.schema.knowledge import DocType

LARGE_CORPUS_CHARS = 40_000


def features(d: DomainSpec) -> dict[str, float]:
    tools = d.tools.tools
    proc_docs = sum(1 for r in d.knowledge.documents if r.doc_type is DocType.PROCEDURE)
    return {
        "n_tools": len(tools),
        "n_writes": len(d.tools.writes()),
        "tool_depth": d.tools.depth(),
        "n_rules": len(d.policy.rules),
        "rule_complexity": sum(r.complexity for r in d.policy.rules) / max(len(d.policy.rules), 1),
        "corpus_chars": d.knowledge.total_chars,
        "n_intents": len(d.task.intents),
        "procedural": float(
            proc_docs > 0 or any("step" in r.description.lower() for r in d.policy.rules)
        ),
        "voice": float(d.task.channel.value == "voice"),
    }


def select_architectures(d: DomainSpec, n: int = 6) -> list[Architecture]:
    f = features(d)
    score: dict[Architecture, float] = {
        Architecture.GUARDED_REACT: 3.0,  # strong general default (memo §8B, P0)
        Architecture.GUARDED_REACT_THINK: 2.5,
        Architecture.POLICY_FIRST: 2.0,
        Architecture.PLANNER_EXECUTOR: 1.5,
        Architecture.FINITE_STATE: 1.5,
        Architecture.RETRIEVAL_FIRST: 1.0,
    }
    if f["n_tools"] <= 5 and f["n_writes"] >= 1:
        score[Architecture.FINITE_STATE] += 1.5
    if f["procedural"]:
        score[Architecture.FINITE_STATE] += 1.0
    if f["corpus_chars"] > LARGE_CORPUS_CHARS:
        score[Architecture.RETRIEVAL_FIRST] += 3.0
        score[Architecture.POLICY_FIRST] += 1.0
    if f["tool_depth"] >= 3 or f["n_writes"] >= 4:
        score[Architecture.PLANNER_EXECUTOR] += 1.5
    if f["n_rules"] >= 8 or f["rule_complexity"] >= 2.5:
        score[Architecture.POLICY_FIRST] += 1.5
        score[Architecture.GUARDED_REACT_THINK] += 0.5
    if f["voice"]:
        # Voice favours lean single-agent prompts (memo §3.5, latency §55.8).
        score[Architecture.PLANNER_EXECUTOR] -= 1.0
    if not d.knowledge.documents:
        # Retrieval-first without documents is guarded ReAct minus few-shots: a duplicate,
        # not an architecture (GAP_ANALYSIS A7).
        score.pop(Architecture.RETRIEVAL_FIRST)
    ranked = sorted(score, key=lambda a: (-score[a], a.value))
    return ranked[: max(1, n)]
