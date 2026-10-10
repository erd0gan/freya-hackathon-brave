"""Component crossover (memo §18.6): take each block from the parent that failed less on it."""

from __future__ import annotations

from collections import Counter

from builder.optimize.mutations import child_id
from builder.schema.agent import AgentSpec


def crossover(
    a: AgentSpec, b: AgentSpec, pressure_a: Counter[str], pressure_b: Counter[str]
) -> AgentSpec | None:
    sections = dict(a.system_prompt_sections)
    took = []
    for name, text in b.system_prompt_sections.items():
        if pressure_b.get(name, 0) < pressure_a.get(name, 0) or name not in sections:
            sections[name] = text
            took.append(name)
    if not took:
        return None
    return a.model_copy(
        update={
            "candidate_id": child_id(a, f"x:{b.candidate_id}:{','.join(sorted(took))}"),
            "parent_ids": [a.candidate_id, b.candidate_id],
            "system_prompt_sections": sections,
            "lineage_note": f"crossover {a.candidate_id}+{b.candidate_id} blocks={took}",
        },
        deep=True,
    )
