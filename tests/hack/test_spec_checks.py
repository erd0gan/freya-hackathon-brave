"""F01: structural spec errors stop the build; values not grounded in the brief are flagged.
The fixture is a real model extraction of the public Brief A + B (10 Oct 2026)."""

import copy
import json
from pathlib import Path

import pytest
from fb.openapi_tools import parse_openapi
from fb.spec_extract import check, grounding

from conftest import KIT

SPEC = json.loads((Path(__file__).parent / "fixtures" / "spec_ab_extracted.json").read_text())
BRIEF = (
    (KIT / "briefs" / "brief-a.md").read_text() + "\n" + (KIT / "briefs" / "brief-b.md").read_text()
)
TOOLS = parse_openapi(json.loads((KIT / "tools" / "openapi.json").read_text()))


def mutate(f):
    s = copy.deepcopy(SPEC)
    f(s)
    return s


def test_clean_extraction_passes():
    assert check(SPEC, TOOLS, BRIEF) == []
    assert grounding(SPEC, BRIEF) == []


HARD = {
    "overlapping bands": lambda s: s["bands"][1].update(min_days_late=5),
    "negative attempts": lambda s: s["bands"][0].update(attempts=-1),
    "notes as a string": lambda s: s["neg"].update(notes="false"),
    "impossible callback window": lambda s: s["callback"].update(start="25:99", end="01:00"),
}
SOFT = {
    "holiday not in the brief": lambda s: s["calendar"].update(
        holidays=["2030-01-01", *s["calendar"]["holidays"][1:]]
    ),
    "deadline not in the brief": lambda s: s["bands"][0].update(deadline_days=999),
    "made-up close reason": lambda s: s["outcomes"].update(unclear=["not_reached", "mumbled"]),
}


@pytest.mark.parametrize("name", sorted(HARD))
def test_structural_errors_stop(name):
    assert check(mutate(HARD[name]), TOOLS, BRIEF), name


@pytest.mark.parametrize("name", sorted(SOFT))
def test_ungrounded_values_are_flagged(name):
    s = mutate(SOFT[name])
    assert grounding(s, BRIEF), name


def test_model_call_respects_the_deadline(monkeypatch):
    import time

    from fb.llm import LLMError, chat
    from fb.trace import Trace

    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    with pytest.raises(LLMError, match="no time left"):
        chat(Trace(None, echo=False), "test", [], deadline=time.monotonic() + 5)
