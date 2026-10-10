"""Policy compilation (memo §7): atomic rules, conflicts, rule→tool map, precedence.

Deterministic pass segments the policy into sections and keeps verbatim text as
evidence; the LLM pass extracts structured atomic rules. Every rule must carry
a source reference — unsourced rules are dropped (PLAN Faz 3 acceptance).
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from builder.ingest.tool_compiler import _mentions
from builder.llm import LLMClient, Message, Role
from builder.schema.policy import PolicyLevel, PolicyRule, PolicySpec, RuleConflict
from builder.schema.tool import ToolGraph

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_ID = re.compile(r"\b([A-Z]{2,10}[-_]\d{1,4}[A-Z]?)\b")
_PROHIBIT = re.compile(
    r"\b(never|do not|don't|must not|cannot|can't|prohibited|asla|yasak)\b", re.I
)
_AUTH = re.compile(r"\b(verify|verification|authenticat|identity|kimlik|dogrula|doğrula)", re.I)
_REG = re.compile(r"\b(regulat|law|legal|compliance|gdpr|kvkk|pci)\b", re.I)


class Section(BaseModel):
    ref: str
    title: str
    text: str


def split_sections(policy_text: str) -> list[Section]:
    sections: list[Section] = []
    title, buf = "", []
    counter = 0

    def flush() -> None:
        nonlocal counter
        body = "\n".join(buf).strip()
        if not body and not title:
            return
        counter += 1
        m = _ID.search(title) or _ID.search(body[:80])
        ref = m.group(1) if m else f"S{counter:02d}"
        sections.append(Section(ref=ref, title=title.strip(), text=body or title))

    for line in policy_text.splitlines():
        h = _HEADING.match(line)
        if h:
            flush()
            title, buf = h.group(1), []
        else:
            if re.match(r"^(?:[-*+] |\d+[.)] )", line) and any(x.strip() for x in buf):
                flush()
                buf = []
            buf.append(line)
    flush()

    # A body-less heading is a document title unless it carries a rule id or a sentence.
    def is_rule(s: Section) -> bool:
        if s.text.strip() != s.title.strip():
            return bool(s.text.strip())
        return bool(_ID.search(s.title)) or len(s.title) > 60

    return [s for s in sections if is_rule(s)]


def _level(text: str) -> PolicyLevel:
    if _PROHIBIT.search(text):
        return PolicyLevel.PROHIBITION
    if _REG.search(text):
        return PolicyLevel.REGULATORY
    if _AUTH.search(text):
        return PolicyLevel.AUTHORIZATION
    return PolicyLevel.TASK


def compile_policy_deterministic(policy_text: str, tools: ToolGraph | None = None) -> PolicySpec:
    rules: list[PolicyRule] = []
    seen: set[str] = set()
    for i, s in enumerate(split_sections(policy_text)):
        rid = s.ref if s.ref not in seen else f"{s.ref}-{i}"
        seen.add(rid)
        affected = (
            [t.name for t in tools.tools if _mentions(s.text + " " + s.title, t.name)]
            if tools
            else []
        )
        rules.append(
            PolicyRule(
                id=rid,
                description=(
                    (
                        _ID.sub("", s.title).strip(" :-") + ": "
                        if _ID.sub("", s.title).strip()
                        else ""
                    )
                    + " ".join(s.text.split())
                ),
                trigger=s.title,
                level=_level(s.text),
                priority=50,
                affected_tools=affected,
                source_refs=[f"policy#{rid}"],
                category=re.sub(r"[-_]\d+.*$", "", rid).lower(),
                complexity=min(
                    5, 1 + len(re.findall(r"\b(and|or|unless|except|only if)\b", s.text, re.I)) // 2
                ),
            )
        )
    return PolicySpec(rules=rules)


# ---------------------------------------------------------------- LLM pass


class ExtractedRule(BaseModel):
    id: str
    description: str
    trigger: str = ""
    preconditions: list[str] = Field(default_factory=list)
    required_actions: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    level: PolicyLevel = PolicyLevel.TASK
    priority: int = 50
    affected_tools: list[str] = Field(default_factory=list)
    source_section: str = Field(description="ref of the section the rule came from")
    complexity: int = 1


class ExtractedPolicy(BaseModel):
    rules: list[ExtractedRule]
    conflicts: list[RuleConflict] = Field(default_factory=list)
    precedence_note: str = ""


POLICY_PROMPT = """You compile a customer-service policy into ATOMIC rules (memo: trigger,
preconditions, required behaviour, prohibition, exception, priority, source).
Rules:
- One rule = one checkable obligation or prohibition. Split compound sentences.
- id: reuse the section ref when it is a natural id (e.g. AUTH-01); for splits append a,b,c.
- level (precedence, highest first): platform > prohibition > regulatory > authorization > task
  > helpfulness.
- affected_tools: only names from the TOOLS list.
- source_section: the [ref] of the section the rule came from. Never invent rules without a source.
- complexity 1-5: how many conditions interact.
- conflicts: rules with the same trigger and conflicting required actions, exceptions overriding a
  base rule, or version mismatches; propose a resolution.
Return JSON only."""


def compile_policy(
    policy_text: str, tools: ToolGraph | None = None, llm: LLMClient | None = None
) -> PolicySpec:
    base = compile_policy_deterministic(policy_text, tools)
    if llm is None or not base.rules:
        return base
    sections = split_sections(policy_text)
    listing = "\n\n".join(f"[{s.ref}] {s.title}\n{s.text}" for s in sections)
    tool_names = ", ".join(tools.names()) if tools else "(none)"
    out = llm.complete_structured(
        Role.BUILDER,
        ExtractedPolicy,
        [
            Message(role="system", content=POLICY_PROMPT),
            Message(role="user", content=f"TOOLS: {tool_names}\n\nPOLICY SECTIONS:\n{listing}"),
        ],
        temperature=0.0,
        max_tokens=8000,
    )
    return merge_extracted(base, out, tools)


def merge_extracted(base: PolicySpec, out: ExtractedPolicy, tools: ToolGraph | None) -> PolicySpec:
    valid_refs = {r.source_refs[0].split("#", 1)[1] for r in base.rules}
    names = set(tools.names()) if tools else set()
    rules: list[PolicyRule] = []
    covered: set[str] = set()
    for e in out.rules:
        if e.source_section not in valid_refs:
            continue  # unsourced → dropped
        covered.add(e.source_section)
        rules.append(
            PolicyRule(
                id=e.id,
                description=e.description,
                trigger=e.trigger,
                preconditions=e.preconditions,
                required_actions=e.required_actions,
                prohibited_actions=e.prohibited_actions,
                exceptions=e.exceptions,
                level=e.level,
                priority=e.priority,
                affected_tools=[t for t in e.affected_tools if t in names] if names else [],
                source_refs=[f"policy#{e.source_section}"],
                complexity=max(1, min(5, e.complexity)),
                category=re.sub(r"[-_]\d+.*$", "", e.source_section).lower(),
            )
        )
    # Keep verbatim sections the LLM skipped, so no policy text is silently lost.
    rules += [r for r in base.rules if r.source_refs[0].split("#", 1)[1] not in covered]
    ids: set[str] = set()
    for r in rules:
        while r.id in ids:
            r.id = r.id + "'"
        ids.add(r.id)
    return PolicySpec(rules=rules, conflicts=out.conflicts, precedence_note=out.precedence_note)
