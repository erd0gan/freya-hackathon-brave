from __future__ import annotations

from pathlib import Path

import pytest

from builder.adapters.freya.compile import compile_agent
from builder.ingest.bundle import load_bundle
from builder.ingest.compile import compile_domain
from builder.schema.agent import Architecture, GuardMode
from builder.synth.architecture_selector import select_architectures
from builder.synth.candidate_generator import generate_candidates

BUNDLES = Path(__file__).parents[2] / "examples" / "bundles"
NAMES = ["retail_bank.json", "insurance_claims", "loan_deferral.json", "tr_card_limit.yaml"]


@pytest.fixture(scope="module", params=NAMES)
def domain(request):
    return compile_domain(load_bundle(BUNDLES / request.param))


def test_candidates_are_structurally_distinct(domain):
    cands = generate_candidates(domain, n=6)
    ids = [c.candidate_id for c in cands]
    assert ids[:2] == ["B0", "A"] and len(set(ids)) == len(ids)
    # Retrieval-first only exists when there are documents to retrieve (else it duplicated C1).
    assert len(ids) == (8 if domain.knowledge.documents else 7)
    block_sets = {tuple(c.system_prompt_sections) for c in cands}
    assert len(block_sets) >= 6, "diversity must be architectural, not cosmetic"
    rendered = {(c.render_system_prompt(), len(c.few_shot_examples)) for c in cands}
    assert len(rendered) == len(cands)
    c5 = next(c for c in cands if c.candidate_id == "C5")
    heads = [ln for ln in c5.render_system_prompt().splitlines() if ln.startswith("## ")]
    assert heads[1:3] == ["## Rule Priority", "## Domain Rules"]  # policy-first order survives
    b0 = cands[0]
    assert b0.mutation_guard is GuardMode.NONE
    assert all(c.mutation_guard is not GuardMode.NONE for c in cands[1:])


def test_prompts_valid_and_compile_to_freya(domain):
    for c in generate_candidates(domain, n=6):
        text = c.render_system_prompt()
        assert 200 < len(text) < 60_000
        assert "{{" not in text and "TODO" not in text
        cfg = compile_agent(c, domain.tools)
        assert len(cfg["llmConfig"]["tools"]) == len(domain.tools.tools)


def test_voice_and_language_blocks():
    tr = compile_domain(load_bundle(BUNDLES / "tr_card_limit.yaml"))
    c = generate_candidates(tr, n=1, include_baselines=False)[0]
    assert "voice_policy" in c.system_prompt_sections
    assert "12.500 TL" in c.system_prompt_sections["language_policy"]
    bank = compile_domain(load_bundle(BUNDLES / "retail_bank.json"))
    c = generate_candidates(bank, n=1, include_baselines=False)[0]
    assert "voice_policy" not in c.system_prompt_sections
    # Policy doc is compiled into domain_rules, not repeated as a reference doc.
    assert "knowledge_retrieval_policy" not in c.system_prompt_sections


def test_selector_prefers_fsm_for_small_transactional():
    d = compile_domain(load_bundle(BUNDLES / "loan_deferral.json"))
    top = select_architectures(d, 3)
    assert Architecture.FINITE_STATE in top


def test_fewshots_name_no_concrete_tools_and_audit_checks_rule_coverage(domain):
    import re

    from builder.select.final_audit import audit
    from builder.synth import fewshot

    for ex in fewshot.examples(list(fewshot.LIBRARY)):
        assert not re.search(r"calls [a-z]+_[a-z_]+\(", ex.text), ex.family
    c1 = next(c for c in generate_candidates(domain, n=6) if c.candidate_id == "C1")
    assert audit(c1, domain).ok
    first = domain.policy.rules[0].id
    sections = {k: v.replace(first, "R-X") for k, v in c1.system_prompt_sections.items()}
    dropped = c1.model_copy(update={"system_prompt_sections": sections})
    assert any("domain rules missing" in p for p in audit(dropped, domain).problems)
