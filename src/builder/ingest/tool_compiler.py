"""Tool semantic compilation (memo §6).

Two passes:
  1. Deterministic lexicon pass — always runs, needs no LLM.
  2. LLM semantic pass — purpose, pre/postconditions, failure modes, edges.
Reconciliation: when the passes disagree on risk, the riskier class wins.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import Any

from pydantic import BaseModel, Field

from builder.llm import LLMClient, Message, Role
from builder.schema.tool import (
    EdgeKind,
    EdgeSource,
    RiskClass,
    ToolEdge,
    ToolGraph,
    ToolSpec,
    riskier,
)

READ_VERBS = {
    "get",
    "list",
    "search",
    "find",
    "lookup",
    "look",
    "fetch",
    "read",
    "query",
    "check",
    "view",
    "show",
    "retrieve",
    "describe",
    "calculate",
    "compute",
    "estimate",
    "verify",
    "validate",
    "count",
    "status",
    "think",
    "explain",
    "preview",
    "quote",
    "inspect",
    "sorgula",
    "listele",
    "getir",
}
REVERSIBLE_VERBS = {
    "update",
    "set",
    "change",
    "modify",
    "edit",
    "freeze",
    "unfreeze",
    "lock",
    "unlock",
    "enable",
    "disable",
    "pause",
    "resume",
    "toggle",
    "rename",
    "add",
    "assign",
    "move",
    "reschedule",
    "hold",
    "block",
    "unblock",
    "suspend",
    "activate",
    "save",
    "guncelle",
}
IRREVERSIBLE_VERBS = {
    "create",
    "issue",
    "order",
    "transfer",
    "pay",
    "refund",
    "charge",
    "close",
    "cancel",
    "delete",
    "remove",
    "submit",
    "book",
    "purchase",
    "buy",
    "sell",
    "approve",
    "reject",
    "open",
    "apply",
    "file",
    "withdraw",
    "deposit",
    "terminate",
    "replace",
    "exchange",
    "return",
    "send_money",
    "olustur",
    "iptal",
    "sil",
    "ode",
}
# Turkish verbs (memo §64); Turkish tool names usually put the verb last.
READ_VERBS |= {"dogrula", "goster", "hesapla", "bul", "kontrol", "sorgula", "listele", "getir"}
REVERSIBLE_VERBS |= {"guncelle", "degistir", "artir", "azalt", "dondur", "kilitle", "ertele"}
IRREVERSIBLE_VERBS |= {"olustur", "iptal", "sil", "ode", "aktar", "iade", "kapat", "basvur"}
SIDE_EFFECT_VERBS = {"send", "notify", "email", "sms", "call", "message", "escalate", "page"}
HANDOFF_HINTS = {"human", "agent", "transfer_to", "handoff", "escalat"}

CONFIRM_RE = re.compile(
    r"(confirm|confirmation|consent|explicit(ly)? (agree|approval)|onay)", re.IGNORECASE
)


def _tokens(name: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    s = s.translate(str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU"))
    return [t for t in re.split(r"[^a-zA-Z0-9]+", s.lower()) if t]


def lexicon_risk(name: str, description: str = "") -> RiskClass:
    return lexicon_risk_evidence(name, description)[0]


def lexicon_risk_evidence(name: str, description: str = "") -> tuple[RiskClass, str]:
    """Risk class plus how it was decided: "name_verb" (strong) or "description"/"default"
    (weak; the LLM pass may overrule these downward, GAP_ANALYSIS C2)."""
    toks = _tokens(name)
    lowered = name.lower()
    if any(h in lowered for h in HANDOFF_HINTS) and ("transfer" in toks or "escalate" in toks):
        return RiskClass.EXTERNAL_SIDE_EFFECT, "name_verb"
    # Compound verbs first ("send_money" is a transfer, not a notification).
    for a, b in pairwise(toks):
        if f"{a}_{b}" in IRREVERSIBLE_VERBS:
            return RiskClass.IRREVERSIBLE_WRITE, "name_verb"
    for tok in toks:
        if tok in IRREVERSIBLE_VERBS:
            return RiskClass.IRREVERSIBLE_WRITE, "name_verb"
        if tok in SIDE_EFFECT_VERBS:
            return RiskClass.EXTERNAL_SIDE_EFFECT, "name_verb"
        if tok in REVERSIBLE_VERBS:
            return RiskClass.REVERSIBLE_WRITE, "name_verb"
        if tok in READ_VERBS:
            return RiskClass.READ_ONLY, "name_verb"
    desc = description.lower()
    if re.search(r"\b(creates?|issues?|cancels?|deletes?|charges?|refunds?|transfers?)\b", desc):
        return RiskClass.IRREVERSIBLE_WRITE, "description"
    if re.search(r"\b(updates?|changes?|modif(y|ies)|sets?)\b", desc):
        return RiskClass.REVERSIBLE_WRITE, "description"
    # Unknown verb: assume it writes. Over-guarding a read costs a turn;
    # under-guarding a write can fail the task (memo §3.7).
    if desc.startswith(("get", "return")):
        return RiskClass.READ_ONLY, "default"
    return RiskClass.REVERSIBLE_WRITE, "default"


def _policy_paragraphs(policy_text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n|\n(?=#)", policy_text) if p.strip()]


def _stem(tok: str) -> str:
    return tok[:5] if len(tok) > 5 else tok


WRITE_VERBS = REVERSIBLE_VERBS | IRREVERSIBLE_VERBS | SIDE_EFFECT_VERBS


def _mentions(paragraph: str, tool_name: str) -> bool:
    """Does a policy paragraph talk about this tool?

    Literal name, or every noun of the name plus the verb (or, for write tools,
    any write verb — "ordered" for `issue_replacement`).
    """
    p = paragraph.lower()
    if tool_name.lower() in p:
        return True
    toks = _tokens(tool_name)
    if len(toks) < 2:
        return False
    verb, nouns = toks[0], [t for t in toks[1:] if len(t) > 3 and t not in {"with", "from"}]
    if not nouns or not all(re.search(rf"\b{re.escape(_stem(n))}", p) for n in nouns):
        return False
    if re.search(rf"\b{re.escape(_stem(verb))}", p):
        return True
    if verb in WRITE_VERBS:
        return any(re.search(rf"\b{re.escape(_stem(v))}", p) for v in WRITE_VERBS if len(v) > 3)
    return False


def policy_requires_confirmation(tool_name: str, policy_text: str) -> bool:
    return any(
        _mentions(p, tool_name) and CONFIRM_RE.search(p) for p in _policy_paragraphs(policy_text)
    )


def normalize_tool_schema(raw: dict[str, Any]) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    """Accept OpenAI `{type: function, function: {...}}`, bare functions, or Freya-ish tools."""
    fn = raw.get("function", raw)
    name = str(fn.get("name", "")).strip()
    desc = str(fn.get("description", "") or "")
    params = (
        fn.get("parameters")
        or fn.get("input_schema")
        or fn.get("inputs")
        or {
            "type": "object",
            "properties": {},
        }
    )
    extra = (
        {k: v for k, v in raw.items() if k not in {"type", "function"}} if "function" in raw else {}
    )
    extra.update(
        {
            k: v
            for k, v in fn.items()
            if k not in {"name", "description", "parameters", "input_schema", "inputs"}
        }
    )
    return name, desc, params, extra


def compile_tools_deterministic(tools: list[dict[str, Any]], policy_text: str = "") -> ToolGraph:
    specs: list[ToolSpec] = []
    for raw in tools:
        name, desc, params, extra = normalize_tool_schema(raw)
        if not name:
            continue
        risk, evidence = lexicon_risk_evidence(name, desc)
        confirm = risk is RiskClass.IRREVERSIBLE_WRITE or (
            risk.is_write and policy_requires_confirmation(name, policy_text)
        )
        if risk is RiskClass.EXTERNAL_SIDE_EFFECT and any(h in name.lower() for h in HANDOFF_HINTS):
            confirm = False  # hand-offs are governed by escalation policy, not consent
        specs.append(
            ToolSpec(
                name=name,
                description=desc,
                inputs=params,
                risk_class=risk,
                risk_evidence=evidence,
                requires_confirmation=confirm,
                idempotent=True if risk is RiskClass.READ_ONLY else None,
                retryable=True if risk is RiskClass.READ_ONLY else None,
                platform=extra,
            )
        )
    return ToolGraph(
        tools=specs,
        edges=_dedupe(_infer_edges(specs, policy_text) + _policy_prerequisites(specs, policy_text)),
    )


# Verification tools by name; a policy sentence must still require them before changes.
_VERIFY_NAME = re.compile(r"verif|auth|identif|kimlik|dogrula", re.IGNORECASE)
_BEFORE = re.compile(
    r"\b(before|prior to|first|until|only after|must be verified)\b|önce|olmadan|sonra",
    re.IGNORECASE,
)
# Generic "any change/action" wording: the requirement covers every write tool.
_ANY_CHANGE = re.compile(
    r"\b(any|every|all)\s+(change|changes|action|actions|update|updates|write|writes|"
    r"transaction|transactions|request|requests)\b|\bchanging anything\b|"
    r"\b(performing|making|taking)\s+(any|an)\b|herhangi bir (işlem|değişiklik)|"
    r"tüm işlemler|işlem yapmadan",
    re.IGNORECASE,
)


def _policy_prerequisites(specs: list[ToolSpec], policy_text: str) -> list[ToolEdge]:
    """Enforced `REQUIRES_BEFORE` edges, only with explicit policy evidence (GAP_ANALYSIS A6).

    A paragraph must (a) name a verification tool or talk about verifying, (b) say "before",
    and (c) cover writes: either mention the write tool or say "any change/action". Policies
    that only require verification before *sharing information* do not gate writes. Model
    inferences never become enforced prerequisites (false refusals, memo §61).
    """
    verifiers = [t for t in specs if not t.is_write and _VERIFY_NAME.search(t.name)]
    if not verifiers:
        return []
    writes = [
        t for t in specs if t.is_write and not any(h in t.name.lower() for h in HANDOFF_HINTS)
    ]
    edges: list[ToolEdge] = []
    for para in _policy_paragraphs(policy_text):
        if not _BEFORE.search(para):
            continue
        named = [v for v in verifiers if v.name.lower() in para.lower()]
        if not named and not re.search(r"verif|authenticat|doğrula|dogrula", para, re.I):
            continue
        for v in named or (verifiers if len(verifiers) == 1 else []):
            for w in writes:
                if _ANY_CHANGE.search(para) or _mentions(para, w.name):
                    edges.append(
                        ToolEdge(
                            src=v.name,
                            dst=w.name,
                            kind=EdgeKind.REQUIRES_BEFORE,
                            source=EdgeSource.POLICY,
                            evidence=" ".join(para.split())[:300],
                        )
                    )
    return edges


def _infer_edges(specs: list[ToolSpec], policy_text: str) -> list[ToolEdge]:
    """Cheap data-flow edges: a write that needs `x_id` is fed by a read that returns/lists x."""
    edges: list[ToolEdge] = []
    reads = [t for t in specs if not t.is_write]
    for t in specs:
        for arg in t.required_args:
            noun = arg.removesuffix("_id").removesuffix("Id").lower()
            if not noun or noun == arg.lower():
                continue
            for r in reads:
                if r.name == t.name:
                    continue
                rtoks = _tokens(r.name)
                if any(tok.startswith(noun[:5]) for tok in rtoks[1:]):
                    edges.append(
                        ToolEdge(
                            src=r.name, dst=t.name, kind=EdgeKind.FEEDS, source=EdgeSource.DATAFLOW
                        )
                    )
    return edges


# ---------------------------------------------------------------- LLM semantic pass


class ToolSemantics(BaseModel):
    name: str
    inferred_purpose: str = ""
    risk_class: RiskClass
    requires_confirmation: bool = False
    idempotent: bool | None = None
    preconditions: list[str] = Field(default_factory=list)
    postconditions: list[str] = Field(default_factory=list)
    failure_modes: list[str] = Field(default_factory=list)
    arg_format_rules: dict[str, str] = Field(default_factory=dict)
    must_run_after: list[str] = Field(
        default_factory=list, description="tools that must succeed before this one"
    )


class ToolSemanticsBatch(BaseModel):
    tools: list[ToolSemantics]


SEMANTIC_PROMPT = """You analyse the tools of a customer-facing agent.
For EACH tool decide, grounded only in the tool schema and the policy:
- inferred_purpose: one sentence
- risk_class: READ_ONLY | REVERSIBLE_WRITE | IRREVERSIBLE_WRITE | EXTERNAL_SIDE_EFFECT
  (anything that changes backend state or the outside world is a write; when unsure pick the
  riskier class)
- requires_confirmation: true if the policy (or common sense for irreversible financial/account
  actions) requires explicit user confirmation right before calling it
- idempotent: true/false/null (unknown)
- preconditions / postconditions: short, checkable statements
- failure_modes: likely errors
- arg_format_rules: canonical format per argument (e.g. "ISO date YYYY-MM-DD", "number, no
  currency symbol", "exactly as returned by list_x")
- must_run_after: names of tools that must have succeeded earlier (e.g. identity verification)
Return only facts supported by the inputs. Do not invent tools."""


def compile_tools(
    tools: list[dict[str, Any]], policy_text: str, llm: LLMClient | None = None
) -> ToolGraph:
    graph = compile_tools_deterministic(tools, policy_text)
    if llm is None or not graph.tools:
        return graph
    listing = "\n".join(f"- {t.name}: {t.description}\n  params: {t.inputs}" for t in graph.tools)
    batch = llm.complete_structured(
        Role.BUILDER,
        ToolSemanticsBatch,
        [
            Message(role="system", content=SEMANTIC_PROMPT),
            Message(role="user", content=f"POLICY:\n{policy_text[:60000]}\n\nTOOLS:\n{listing}"),
        ],
        temperature=0.0,
    )
    return merge_semantics(graph, batch)


def merge_semantics(graph: ToolGraph, batch: ToolSemanticsBatch) -> ToolGraph:
    by_name = {s.name: s for s in batch.tools}
    names = set(graph.names())
    tools: list[ToolSpec] = []
    edges = list(graph.edges)
    for t in graph.tools:
        s = by_name.get(t.name)
        if s is None:
            tools.append(t)
            continue
        # The riskier class wins, except that a model's READ_ONLY may overrule weak lexicon
        # evidence ("preview_fee: shows the fee for a transfer" is not a transfer).
        weak = t.risk_evidence in {"description", "default"}
        risk = (
            s.risk_class
            if weak and s.risk_class is RiskClass.READ_ONLY
            else riskier(t.risk_class, s.risk_class)
        )
        tools.append(
            t.model_copy(
                update={
                    "inferred_purpose": s.inferred_purpose,
                    "risk_class": risk,
                    # Policy-derived confirmation from either pass is kept (safer).
                    "requires_confirmation": (t.requires_confirmation or s.requires_confirmation)
                    and risk.is_write,
                    "idempotent": True if risk is RiskClass.READ_ONLY else s.idempotent,
                    "preconditions": s.preconditions,
                    "postconditions": s.postconditions,
                    "failure_modes": s.failure_modes,
                    "arg_format_rules": s.arg_format_rules,
                }
            )
        )
        for pre in s.must_run_after:
            if pre in names and pre != t.name:
                edges.append(
                    ToolEdge(
                        src=pre,
                        dst=t.name,
                        kind=EdgeKind.FEEDS,
                        source=EdgeSource.LLM,
                        note="Unverified LLM prerequisite; advisory only",
                    )
                )
    return ToolGraph(tools=tools, edges=_dedupe(edges))


def _dedupe(edges: list[ToolEdge]) -> list[ToolEdge]:
    seen: set[tuple[str, str, str]] = set()
    out: list[ToolEdge] = []
    for e in edges:
        k = (e.src, e.dst, e.kind.value)
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out
