"""Gap-analysis check, see docs/GAP_ANALYSIS.md. Run: uv run python scripts/gap_checks/candidate_similarity.py"""
# ruff: noqa: E501, E741

import difflib
import itertools
from pathlib import Path

from builder.ingest.bundle import load_bundle
from builder.ingest.compile import compile_domain
from builder.synth.candidate_generator import generate_candidates

for bundle in [
    "examples/bundles/retail_bank.json",
    "examples/bundles/insurance_claims",
    "examples/bundles/loan_deferral.json",
    "examples/bundles/tr_card_limit.yaml",
]:
    d = compile_domain(load_bundle(Path(bundle)), None)
    cands = generate_candidates(d, n=6)
    print(
        "=====",
        bundle,
        "| tools",
        len(d.tools.tools),
        "writes",
        len(d.tools.writes()),
        "rules",
        len(d.policy.rules),
        "docs",
        len(d.knowledge.documents),
        "lang",
        d.task.language,
        "ch",
        d.task.channel.value,
    )
    prompts = {c.candidate_id: c.render_system_prompt() for c in cands}
    for c in cands:
        p = prompts[c.candidate_id]
        print(
            f"  {c.candidate_id:3s} {c.architecture.value:20s} len={len(p):6d} blocks={len(c.system_prompt_sections):2d} fewshot={len(c.few_shot_examples)} guard={c.mutation_guard.value} temp={c.temperature}"
        )
    ids = list(prompts)
    # pairwise similarity
    sims = []
    for a, b in itertools.combinations(ids, 2):
        r = difflib.SequenceMatcher(None, prompts[a], prompts[b], autojunk=False).quick_ratio()
        r = difflib.SequenceMatcher(None, prompts[a].split("\n"), prompts[b].split("\n")).ratio()
        sims.append((r, a, b))
    sims.sort(reverse=True)
    print("  most similar pairs (line-level ratio):", [(f"{r:.3f}", a, b) for r, a, b in sims[:5]])
    # identical block sets / orders for C6 vs C1?
    by = {c.candidate_id: c for c in cands}
    if "C1" in by and "C6" in by:
        print(
            "  C1 vs C6 same block names:",
            set(by["C1"].system_prompt_sections) == set(by["C6"].system_prompt_sections),
            "| differ only by:",
            [
                k
                for k in by["C1"].system_prompt_sections
                if by["C1"].system_prompt_sections[k] != by["C6"].system_prompt_sections.get(k)
            ],
            "| fewshot C1/C6:",
            len(by["C1"].few_shot_examples),
            len(by["C6"].few_shot_examples),
        )
    if "C1" in by and "C5" in by:
        s1, s5 = by["C1"].system_prompt_sections, by["C5"].system_prompt_sections
        print(
            "  C1 vs C5 block diff:",
            set(s5) - set(s1),
            set(s1) - set(s5),
            "| rendered order identical for shared blocks:",
            [k for k in s1 if k in s5] == [k for k in s5 if k in s1],
            "| dict-insertion order C5:",
            list(s5)[:4],
            "| rendered order:",
            [l[3:] for l in by["C5"].render_system_prompt().split("\n") if l.startswith("## ")][:4],
        )
