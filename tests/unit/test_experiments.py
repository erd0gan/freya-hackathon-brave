from __future__ import annotations

from itertools import pairwise

from builder.adapters.mock.domains.retail_bank import make_pack
from builder.experiments import ladder
from builder.ingest.compile import compile_domain
from builder.schema.agent import GuardMode


def test_ladder_is_monotone():
    steps = ladder(compile_domain(make_pack().bundle))
    assert [s.candidate_id for s in steps] == ["B0", "B1", "B2", "B3", "B4", "B5"]
    blocks = [set(s.system_prompt_sections) for s in steps[1:]]
    assert all(a <= b for a, b in pairwise(blocks))
    assert steps[2].mutation_guard is GuardMode.NONE
    assert steps[3].mutation_guard is GuardMode.PROMPT_AND_DETERMINISTIC
    assert "mutation_guard" in steps[3].system_prompt_sections
