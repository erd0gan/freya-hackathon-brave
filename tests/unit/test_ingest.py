from __future__ import annotations

import json
from pathlib import Path

import pytest

from builder.ingest.bundle import load_bundle
from builder.ingest.compile import compile_domain
from builder.ingest.lang import detect_language
from builder.ingest.policy_compiler import ExtractedPolicy, ExtractedRule, merge_extracted
from builder.ingest.tool_compiler import ToolSemantics, ToolSemanticsBatch, merge_semantics
from builder.llm.fake import FakeProvider
from builder.schema.policy import PolicyLevel
from builder.schema.tool import EdgeKind, EdgeSource, RiskClass

BUNDLES = Path(__file__).parents[2] / "examples" / "bundles"

# Hand-labelled golden risk classes (PLAN Faz 3: ≥95% overall, 100% write recall).
GOLDEN = {
    "verify_identity": False,
    "get_customer": False,
    "list_cards": False,
    "list_transactions": False,
    "freeze_card": True,
    "issue_replacement": True,
    "update_address": True,
    "issue_refund": True,
    "transfer_to_human": True,
    "verify_policyholder": False,
    "get_policy": False,
    "open_claim": True,
    "get_claim_status": False,
    "list_inspection_slots": False,
    "book_inspection": True,
    "arrange_rental": True,
    "transfer_to_adjuster": True,
    "verify_borrower": False,
    "get_loan": False,
    "submit_deferral": True,
    "transfer_to_hardship_team": True,
    "musteri_dogrula": False,
    "kart_bilgisi_getir": False,
    "limit_artir": True,
}


@pytest.fixture(scope="module")
def domains():
    paths = ["retail_bank.json", "insurance_claims", "loan_deferral.json", "tr_card_limit.yaml"]
    return {p: compile_domain(load_bundle(BUNDLES / p)) for p in paths}


def test_all_bundles_compile(domains):
    for d in domains.values():
        assert d.tools.tools and d.policy.rules
        assert not d.policy.unsourced()
        assert d.input_hash


def test_golden_risk_accuracy(domains):
    pred = {t.name: t.is_write for d in domains.values() for t in d.tools.tools}
    assert set(pred) == set(GOLDEN)
    correct = sum(pred[k] == v for k, v in GOLDEN.items())
    assert correct / len(GOLDEN) >= 0.95
    assert all(pred[k] for k, v in GOLDEN.items() if v), "every write must be detected"


def test_language_and_channel(domains):
    assert domains["tr_card_limit.yaml"].task.language == "tr"
    assert domains["insurance_claims"].task.channel.value == "voice"
    assert detect_language("Please freeze my card") == "en"


def test_rule_ids_and_levels(domains):
    rules = {r.id: r for r in domains["retail_bank.json"].policy.rules}
    assert {"AUTH-01", "CARD-02", "REF-01"} <= set(rules)
    assert rules["AUTH-01"].level is PolicyLevel.PROHIBITION
    assert "issue_refund" in rules["REF-02"].affected_tools


def test_policy_merge_drops_unsourced(domains):
    base = domains["retail_bank.json"].policy
    out = ExtractedPolicy(
        rules=[
            ExtractedRule(
                id="AUTH-01a",
                description="verify first",
                source_section="AUTH-01",
                level=PolicyLevel.AUTHORIZATION,
                affected_tools=["verify_identity", "nope"],
            ),
            ExtractedRule(id="X-1", description="invented", source_section="MADE-UP"),
        ]
    )
    merged = merge_extracted(base, out, domains["retail_bank.json"].tools)
    ids = {r.id for r in merged.rules}
    assert "AUTH-01a" in ids and "X-1" not in ids
    assert "AUTH-01" not in ids  # replaced by its atomic split
    assert "CARD-01" in ids  # untouched sections kept verbatim
    assert next(r for r in merged.rules if r.id == "AUTH-01a").affected_tools == ["verify_identity"]


def test_tool_semantics_riskier_wins_and_edges(domains):
    g = domains["retail_bank.json"].tools
    batch = ToolSemanticsBatch(
        tools=[
            ToolSemantics(
                name="freeze_card",
                risk_class=RiskClass.READ_ONLY,
                must_run_after=["verify_identity", "ghost"],
            ),
            ToolSemantics(name="list_cards", risk_class=RiskClass.REVERSIBLE_WRITE),
        ]
    )
    m = merge_semantics(g, batch)
    assert m.get("freeze_card").risk_class is RiskClass.REVERSIBLE_WRITE
    assert m.get("list_cards").risk_class is RiskClass.REVERSIBLE_WRITE
    # Enforced prerequisites come only from policy evidence (AUTH-01 "before ... any change"),
    # never from the model's must_run_after ("ghost" is unknown, verify_identity stays advisory).
    assert m.prerequisites("freeze_card") == ["verify_identity"]
    enforced = [e for e in m.edges if e.kind is EdgeKind.REQUIRES_BEFORE]
    assert enforced and all(e.source is EdgeSource.POLICY and e.evidence for e in enforced)
    assert any(
        e.src == "verify_identity" and e.source is EdgeSource.LLM and e.kind is EdgeKind.FEEDS
        for e in m.edges
    )
    assert all(e.kind in EdgeKind for e in m.edges)


def test_llm_compile_path_with_fake(make_client):
    """compile_domain with an LLM: every pass runs, outputs merged and validated."""
    bundle = load_bundle(BUNDLES / "loan_deferral.json")

    def handler(req):
        sys = req.messages[0].content
        if "analyse the tools" in sys:
            return json.dumps(
                {
                    "tools": [
                        {"name": "get_loan", "risk_class": "READ_ONLY"},
                        {
                            "name": "submit_deferral",
                            "risk_class": "IRREVERSIBLE_WRITE",
                            "requires_confirmation": True,
                            "must_run_after": ["verify_borrower"],
                        },
                    ]
                }
            )
        if "ATOMIC rules" in sys:
            return json.dumps(
                {
                    "rules": [
                        {
                            "id": "LD-03a",
                            "description": "disclose interest",
                            "source_section": "LD-03",
                            "affected_tools": ["submit_deferral"],
                        }
                    ]
                }
            )
        return json.dumps({"objective": "Handle deferrals", "intents": ["request_deferral"]})

    d = compile_domain(bundle, make_client(FakeProvider(default=handler)))
    assert d.tools.prerequisites("submit_deferral") == []
    assert any(r.id == "LD-03a" for r in d.policy.rules)
    assert d.task.intents == ["request_deferral"]


def test_prerequisites_need_policy_evidence_covering_writes():
    """GAP_ANALYSIS A6: verification gates writes only where the policy says so."""
    from pathlib import Path

    from builder.ingest.bundle import load_bundle
    from builder.ingest.compile import compile_domain

    root = Path(__file__).resolve().parents[2] / "examples" / "bundles"
    bank = compile_domain(load_bundle(root / "retail_bank.json"), None).tools
    assert set(bank.prerequisites("issue_refund")) == {"verify_identity"}
    assert bank.prerequisites("transfer_to_human") == []  # hand-offs are never gated
    assert bank.depth() == 2
    ins = compile_domain(load_bundle(root / "insurance_claims"), None).tools
    assert ins.prerequisites("open_claim") == ["verify_policyholder"]
    # "verify ... before sharing loan details" / "bilgi vermeden önce": reads only.
    for name in ("loan_deferral.json", "tr_card_limit.yaml"):
        g = compile_domain(load_bundle(root / name), None).tools
        assert not any(g.prerequisites(t.name) for t in g.writes()), name


def test_model_may_downgrade_only_weak_lexicon_evidence():
    """GAP_ANALYSIS C1/C2: compound verbs, and read tools mislabelled as writes."""
    from builder.ingest.tool_compiler import compile_tools_deterministic, lexicon_risk

    assert lexicon_risk("send_money") is RiskClass.IRREVERSIBLE_WRITE
    g = compile_tools_deterministic(
        [
            {"name": "fee_lookup2", "description": "Shows the fee for a transfer."},
            {"name": "transfer_funds", "description": "Moves money."},
        ]
    )
    assert g.get("fee_lookup2").risk_class is RiskClass.IRREVERSIBLE_WRITE  # weak evidence
    batch = ToolSemanticsBatch(
        tools=[
            ToolSemantics(name="fee_lookup2", risk_class=RiskClass.READ_ONLY),
            ToolSemantics(name="transfer_funds", risk_class=RiskClass.READ_ONLY),
        ]
    )
    m = merge_semantics(g, batch)
    assert m.get("fee_lookup2").risk_class is RiskClass.READ_ONLY
    assert not m.get("fee_lookup2").requires_confirmation
    assert m.get("transfer_funds").risk_class is RiskClass.IRREVERSIBLE_WRITE  # strong evidence


def test_voice_heuristic_needs_channel_phrases():
    from builder.ingest.lang import looks_like_voice

    assert not looks_like_voice("Customers may update their phone number or email.")
    assert not looks_like_voice("If unresolved, the customer may call back later.")
    assert looks_like_voice("You are a voice agent answering inbound calls.")
    assert looks_like_voice("Telefonda arayan müşterilere yardım edersin.")
