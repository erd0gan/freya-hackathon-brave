"""End-to-end FULL mode on the mock bank with a scripted fake LLM (no network).

The fake agent behaves well when its prompt has a mutation-guard block and
writes without confirmation otherwise (B0). Selection must not pick B0.
"""

from __future__ import annotations

import json
import re

from test_testsynth import GOOD

from builder.adapters.mock.adapter import MockAdapter
from builder.adapters.mock.domains.retail_bank import make_pack
from builder.llm import LLMRequest, LLMResponse, ToolCall
from builder.llm.fake import FakeProvider
from builder.pipeline import Mode, build
from builder.schema.budget import OptimizationBudget


def _called(req: LLMRequest, tool: str) -> bool:
    return any(tc.name == tool for m in req.messages for tc in m.tool_calls)


def agent(req: LLMRequest) -> LLMResponse:
    guarded = "## Global Policy" in (req.messages[0].content or "")
    last_user = next(m.content for m in reversed(req.messages) if m.role == "user")
    last = req.messages[-1]
    asked = any(
        m.role == "assistant" and m.content and "Shall I" in m.content for m in req.messages
    )
    call = lambda n, a: LLMResponse(tool_calls=[ToolCall(id=n, name=n, arguments=a)])  # noqa: E731
    if not _called(req, "verify_identity"):
        return call("verify_identity", {"customer_id": "C-1001", "date_of_birth": "1985-12-10"})
    if not _called(req, "list_cards"):
        return call("list_cards", {"customer_id": "C-1001"})
    if not _called(req, "freeze_card"):
        if guarded and not asked:
            return LLMResponse(
                content="Shall I freeze your card ending 4417?\n"
                'freeze_card: {"card_id": "K-1"}\nDo you confirm this exact action?'
            )
        if guarded and last.role == "assistant":
            return LLMResponse(content="")
        if not guarded or "yes" in (last_user or "").lower():
            return call("freeze_card", {"card_id": "K-1"})
        return LLMResponse(content="Okay.")
    said = any(
        m.role == "assistant" and m.content and "now frozen" in m.content for m in req.messages
    )
    if not said:
        return LLMResponse(content="Your card ending 4417 is now frozen.")
    if last.role == "assistant":
        return LLMResponse(content="")
    return call("end_conversation", {})


def user(req: LLMRequest) -> str:
    agent_said = req.messages[-1].content or ""
    if len(req.messages) <= 2:
        return "Hi, I lost my debit card. My ID is C-1001, born 1985-12-10."
    if "now frozen" in agent_said:
        return "###STOP###"
    if "?" in agent_said:
        return "Yes, go ahead."
    return "Okay."


def builder_role(req: LLMRequest) -> str:
    sys = req.messages[0].content or ""
    if "ONE realistic" in sys:
        body = req.messages[1].content
        sampled = body.split("TARGET RULES:", 1)[1].split("PERSONA STYLE:", 1)[0]
        return json.dumps(dict(GOOD, target_policies=re.findall(r"\[([^]]+)\]", sampled)))
    return json.dumps({"tools": []})


def judge(req: LLMRequest) -> str:
    if "audit a generated" in req.messages[0].content:
        return json.dumps({"consistent": True})
    body = req.messages[-2].content or ""
    return json.dumps(
        {
            "communicated": [{"item": "card frozen", "ok": "now frozen" in body}],
            "criteria_met": "now frozen" in body,
        }
    )


def analyzer(req: LLMRequest) -> str:
    return json.dumps(
        {
            "failure_type": "MISSING_CONFIRMATION",
            "root_cause": "wrote first",
            "minimal_fix": "Ask for explicit confirmation before changing data.",
        }
    )


def optimizer(req: LLMRequest) -> str:
    return json.dumps({"new_text": "Always ask for explicit confirmation before any change."})


def test_full_build_on_mock(tmp_path, make_client):
    provider = FakeProvider(
        by_role={
            "agent": agent,
            "user_sim": user,
            "user_sim_alt": user,
            "builder": builder_role,
            "judge": judge,
            "analyzer": analyzer,
            "optimizer": optimizer,
        }
    )
    llm = make_client(provider)
    pack = make_pack()
    budget = OptimizationBudget(
        initial_candidates=2, scenarios=6, max_generations=1, trials_final=1, max_simulations=200
    )
    rep = build(
        pack.bundle,
        llm=llm,
        adapter_factory=lambda d: MockAdapter(pack, d.tools, llm),
        budget=budget,
        out_dir=tmp_path,
        mode=Mode.FULL,
        llm_compile=False,
        concurrency=1,
    )
    assert rep.scenarios["train"] >= 3 and rep.scenarios["validation"] >= 1
    assert rep.train["B0"].mean == 0.0, "B0 writes without confirmation"
    guarded = [cid for cid, e in rep.train.items() if cid != "B0"]
    assert all(rep.train[c].mean == 1.0 for c in guarded), rep.log
    assert rep.champion.candidate_id != "B0"
    for f in (
        "agent_spec.json",
        "freya_config_overlay.json",
        "manifest.json",
        "report.md",
        "scenarios.jsonl",
        "runs/traces.jsonl",
    ):
        assert (tmp_path / f).exists(), f
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["selected_candidate"] == rep.champion.candidate_id
    # The paired benchmark (scripts/benchmark.py) re-runs these exact specs.
    for cid in ("B0", "A", rep.champion.candidate_id):
        assert (tmp_path / "candidates" / f"{cid}.json").exists(), cid
    sel = json.loads((tmp_path / "selection.json").read_text())
    assert sel["champion"] == rep.champion.candidate_id and "B0" in sel["train_means"]


def test_crashing_adapter_still_exports_fallback(tmp_path, make_client):
    class Boom(MockAdapter):
        def run_simulation(self, *a, **k):
            raise RuntimeError("platform down")

    provider = FakeProvider(by_role={"builder": builder_role, "judge": judge})
    llm = make_client(provider)
    pack = make_pack()
    rep = build(
        pack.bundle,
        llm=llm,
        adapter_factory=lambda d: Boom(pack, d.tools, llm),
        budget=OptimizationBudget(initial_candidates=2, scenarios=6),
        out_dir=tmp_path,
        llm_compile=False,
        concurrency=1,
    )
    assert rep.champion.candidate_id == "A" and rep.fallback_used
    assert (tmp_path / "freya_config_overlay.json").exists()
    assert any("platform down" in line for line in rep.log)
