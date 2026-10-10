"""Gap-analysis check, see docs/GAP_ANALYSIS.md. Run: uv run python scripts/gap_checks/tool_graph_edges.py"""
# ruff: noqa: E501

from collections import Counter
from pathlib import Path

from builder.ingest.bundle import load_bundle
from builder.ingest.compile import compile_domain

for b in [
    "examples/bundles/retail_bank.json",
    "examples/bundles/insurance_claims",
    "examples/bundles/loan_deferral.json",
    "examples/bundles/tr_card_limit.yaml",
]:
    d = compile_domain(load_bundle(Path(b)), None)
    g = d.tools
    print(
        b.split("/")[-1],
        "| edges:",
        dict(Counter(e.kind.value for e in g.edges)),
        "| depth:",
        g.depth(),
        "| tools with prerequisites:",
        [t.name for t in g.tools if g.prerequisites(t.name)],
        "| writes needing confirm:",
        [t.name for t in g.writes() if t.requires_confirmation],
        "| rules w/ affected_tools:",
        sum(1 for r in d.policy.rules if r.affected_tools),
        "/",
        len(d.policy.rules),
    )
    # which tools have identity-verification-like name
    print(
        "   verify-like tools:",
        [t.name for t in g.tools if "verif" in t.name.lower() or "auth" in t.name.lower()],
    )
