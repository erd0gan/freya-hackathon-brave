"""Deterministic MutationGate (memo §3.7 SABER, §12).

Runs before every state-changing tool call. It only enforces what can be
checked mechanically; semantic policy checks stay in the prompt (memo §56:
prefer the most deterministic primitive available).

Hard-fail rules (memo §12):
  - a required argument is not grounded in user input or tool output
  - confirmation is required but absent
  - the same non-idempotent action already succeeded
  - a requires-before prerequisite has not succeeded
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from builder.schema.state import MutationCheck
from builder.schema.tool import ToolGraph, ToolSpec

_AFFIRM = re.compile(
    r"\b(yes|yeah|yep|yup|sure|correct|confirm(?:ed)?|go ahead|please do|do it|ok(?:ay)?|"
    r"that'?s right|right|absolutely|proceed|evet|tamam|onayl[ıi]yorum|olur|aynen|do[ğg]ru)\b",
    re.IGNORECASE,
)
_NEGATE = re.compile(
    r"\b(no|nope|not|don'?t|do not|wait|stop|cancel|hold on|hay[ıi]r|iptal|bekle|vazge[çc]|"
    r"de[ğg]il|istemiyorum)\b",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([T ][\d:.]+Z?)?$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


_CORRECTION = {
    "but",
    "instead",
    "actually",
    "change",
    "different",
    "rather",
    "other",
    "another",
    "except",
    "only",
    "ama",
    "fakat",
    "yerine",
    "aslında",
    "değiştir",
    "sadece",
}


def is_affirmative(text: str, question: str = "") -> bool:
    """Clear consent: an affirmation, no negation, not itself a question, and nothing new.

    Restating the asked action ("Yes, I confirm freezing the card ending 4417") is consent;
    adding information that was not in the question ("yes, but use card 9902") is not.
    Judged per sentence: the sentence that affirms is checked strictly; other sentences may
    raise unrelated requests or questions ("Now, what about Alan's card?", live run 9 Oct)
    but must not correct or negate the asked action.
    """
    asked = {_canon(w) for w in re.findall(r"[\w@.+-]+", question.lower())}
    sentences = [x for x in re.split(r"(?<=[.!?])\s+|\n+", text.strip()) if x.strip()]
    head = next((i for i, x in enumerate(sentences) if _AFFIRM.search(x)), None)
    if head is None or not _affirming_sentence_ok(sentences[head], asked):
        return False
    content = {w for w in asked if len(w) > 3 and not w.isdigit()}
    # Short numbers ("45" in an address vs "12" USD) are too generic to be corrections.
    asked_shapes = {_shape(w) for w in asked if len(w) > 2 and any(ch.isdigit() for ch in w)}
    for i, sentence in enumerate(sentences):
        if i == head:
            continue
        words = [w for w in re.findall(r"[\w'@.+-]+", sentence.lower())]
        canon = [_canon(w) for w in words]
        if canon and canon[0] in _CORRECTION:
            return False  # "Actually, make it standard shipping."
        if _NEGATE.search(sentence) and content & set(canon):
            return False  # "Don't order the replacement yet."
        for w, c in zip(words, canon, strict=True):
            if c and c not in asked and needs_grounding(w) and _shape(c) in asked_shapes:
                return False  # a same-shaped new value is a correction ("card 9902")
    return True


def _affirming_sentence_ok(sentence: str, asked: set[str]) -> bool:
    # Negations quoted from the question ("…can no longer be used") are restatements.
    own_words = " ".join(
        w for w in re.findall(r"[\w'@.+-]+", sentence.lower()) if _canon(w) not in asked
    )
    if _NEGATE.search(own_words) or "?" in sentence:
        return False
    remainder = _AFFIRM.sub(" ", sentence.lower())
    # Reject corrections ("yes, but…", "actually…") and new identifiers/amounts that were not
    # in the question; an additional request ("…and order the replacement too") is still
    # consent to the asked action and is handled as its own step.
    for word in re.findall(r"[\w@.+-]+", remainder):
        c = _canon(word)
        if not c or c in asked:
            continue
        if c in _CORRECTION or (any(ch.isdigit() for ch in c) and needs_grounding(word)):
            return False
    return True


def _canon(s: str) -> str:
    """Lowercase alphanumerics only, so '555 123-4567' matches '5551234567'."""
    return re.sub(r"[^0-9a-zçğıöşü]", "", s.lower())


_VERIFIED_KEYS = ("verified", "is_verified", "authenticated", "dogrulandi", "match", "valid")
_STATUS_KEYS = ("verification", "verification_status", "dogrulama", "status")
_OK_WORDS = {"verified", "success", "successful", "ok", "passed", "basarili", "dogrulandi"}
_FAIL_WORDS = {"failed", "failure", "not_verified", "mismatch", "denied", "basarisiz"}


def verification_outcome(result: Any) -> bool | None:
    """True/False when a tool result reports a verification outcome, else None."""
    if not isinstance(result, dict):
        return None
    for k in _VERIFIED_KEYS:
        if isinstance(result.get(k), bool):
            return bool(result[k])
    for k in _STATUS_KEYS:
        v = result.get(k)
        if isinstance(v, str):
            w = v.strip().lower().replace("ş", "s").replace("ı", "i").replace(" ", "_")
            if w in _OK_WORDS:
                return True
            if w in _FAIL_WORDS:
                return False
    return None


def _entity_key(name: str) -> bool:
    """Argument/result keys that name a record: `card_id`, `policy_number`, `loan_no`."""
    n = name.lower()
    return n == "id" or n.endswith(("_id", "_number", "_no", "_num", "_numarasi", "_kodu"))


def _is_identifier(name: str) -> bool:
    return _entity_key(name) or name in {"phone", "phone_number", "email"}


def needs_grounding(v: Any) -> bool:
    """Only identifier-like values must be copied from evidence.

    Free text (descriptions, reasons, addresses) is legitimately paraphrased,
    dates are legitimately normalized to ISO, booleans and small counts are
    the agent's own reading of the conversation. Guarding those would cause
    false refusals (memo §61); confirmation covers them instead.
    """
    if isinstance(v, bool) or v is None:
        return False
    if isinstance(v, int | float):
        return not float(v).is_integer() or abs(v) >= 100
    if not isinstance(v, str):
        return False
    s = v.strip()
    if _EMAIL.match(s):
        return True
    if not s or any(c.isspace() for c in s) or _ISO_DATE.match(s):
        return False
    return any(c.isdigit() for c in s) and len(s) >= 2


def _value_variants(v: Any) -> list[str]:
    if isinstance(v, int | float) and not isinstance(v, bool):
        f = float(v)
        out = {str(v), f"{f:g}", f"{f:.2f}"}
        if f.is_integer():
            out.add(str(int(f)))
        return sorted({_canon(x) for x in out})
    return [_canon(str(v))]


@dataclass
class ConversationLedger:
    """What the gate needs to know about the conversation so far."""

    user_messages: list[str] = field(default_factory=list)
    agent_messages: list[str] = field(default_factory=list)
    tool_outputs: list[str] = field(default_factory=list)
    successful_calls: list[tuple[str, str]] = field(default_factory=list)  # (tool, args_json)
    latest_user_affirmative: bool = False
    pending_confirmation: tuple[str, str] | None = None
    confirmed_action: tuple[str, str] | None = None
    facts: dict[str, dict[str, str]] = field(default_factory=dict)
    entities: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    user_evidence: str = ""
    verified_ids: set[str] = field(default_factory=set)
    call_entities: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    answered_question: str = ""
    structured_pending: bool = False
    natural_consent_used: set[str] = field(default_factory=set)

    def request_confirmation(self, tool: str, args: dict[str, Any]) -> None:
        self.pending_confirmation = (tool, _args_key(args))
        self.confirmed_action = None

    def grounded(self, name: str, value: Any) -> bool:
        variants = _value_variants(value)
        identifier = _is_identifier(name)
        if name in self.facts:
            if set(variants) & self.facts[name].keys():
                return True
            # Latest structured identifier wins; a corrected field invalidates its old value.
            # Other fields (amounts, limits) may coincide with a historical record's key:
            # a past `yeni_limit` must not veto the amount the customer asks for (TR run, 9 Oct).
            if identifier:
                return False
        # Identifiers follow the latest user statement (corrections); other values may come
        # from any earlier user turn ("raise it to 100.000 TL" ... later "Evet").
        evidence = self.user_evidence if identifier else " ".join(self.user_messages)
        tokens = re.findall(r"[\w@.+-]+", evidence)
        return any(v in {_canon(t) for t in tokens} for v in variants) or (
            name in {"phone", "phone_number"}
            and _canon(str(value))
            in {_canon(t) for t in re.findall(r"\+?\d[\d ()-]{5,}\d", evidence)}
        )

    def on_user(self, text: str) -> None:
        question = (
            self.agent_messages[-1]
            if self.agent_messages and "?" in self.agent_messages[-1]
            else ""
        )
        self.latest_user_affirmative = is_affirmative(text, question)
        # Exact structured consent was requested: no natural-language fallback this turn.
        self.structured_pending = self.pending_confirmation is not None
        self.confirmed_action = self.pending_confirmation if self.latest_user_affirmative else None
        self.pending_confirmation = None
        # The question this answer responds to (for natural-language consent).
        self.answered_question = (
            self.agent_messages[-1]
            if self.agent_messages and "?" in self.agent_messages[-1]
            else ""
        )
        self.natural_consent_used = set()
        if not self.latest_user_affirmative:
            self.user_evidence = text
            # A user correction invalidates identifiers of the same shape with another value
            # ("C-1002" replaces "C-1001"); unrelated mentions ("ending 4417") keep tool facts.
            user_tokens = {_canon(t): _shape(_canon(t)) for t in re.findall(r"[\w@.+-]+", text)}
            for name in list(self.facts):
                # A verified identity stays: another customer's id ("his ID is C-1002") is not
                # a correction, and acting on that customer needs its own verification.
                if name in self.verified_ids:
                    continue
                if not _is_identifier(name):
                    continue
                known = self.facts[name]
                shapes = {_shape(v) for v in known}
                if any(sh in shapes and tok not in known for tok, sh in user_tokens.items()):
                    self.facts.pop(name)
        self.user_messages.append(text)

    def on_agent(self, text: str) -> None:
        if text:
            self.agent_messages.append(text)
            # Canonical action summary is also usable by prompt-only platform runtimes.
            match = re.search(
                r"(?:^|\n)(\w+): (\{[^\n]*\})\nDo you confirm this exact action\?", text
            )
            if match:
                try:
                    self.request_confirmation(match[1], json.loads(match[2]))
                except (ValueError, TypeError):
                    self.pending_confirmation = None

    def on_tool(self, tool: str, args: dict[str, Any], ok: bool, result: Any) -> None:
        self.tool_outputs.append(json.dumps(result, default=str, ensure_ascii=False))
        verified = verification_outcome(result)
        if verified is not None:
            self.successful_calls = [(t, a) for t, a in self.successful_calls if t != tool]
            self.call_entities = [(t, e) for t, e in self.call_entities if t != tool]
            ok = ok and verified
        if ok:
            self.successful_calls.append((tool, _args_key(args)))
            # Records this call acted on: its id-like args plus ids the result returned
            # ("musteri_dogrula(tckn, dob) -> {musteri_id}"), for entity-scoped prerequisites.
            top = result if isinstance(result, dict) else {}
            self.call_entities.append(
                (
                    tool,
                    {
                        **{
                            k: v
                            for k, v in top.items()
                            if _entity_key(k) and not isinstance(v, dict | list)
                        },
                        **{k: v for k, v in args.items() if _entity_key(k)},
                    },
                )
            )
            collected: dict[str, dict[str, str]] = {}

            def collect(obj: Any) -> None:
                if isinstance(obj, dict):
                    identifiers = {
                        k: v
                        for k, v in obj.items()
                        if _entity_key(k) and not isinstance(v, dict | list)
                    }
                    identifiers = {
                        **{k: v for k, v in args.items() if _entity_key(k)},
                        **identifiers,
                    }
                    for key, value in identifiers.items():
                        # Merge: listing K-1's transactions must keep K-1's link to its
                        # customer from list_cards (benchmark 9 Oct).
                        ek = (key, _args_key({key: value}))
                        self.entities[ek] = {**self.entities.get(ek, {}), **identifiers}
                    for name, value in obj.items():
                        if isinstance(value, dict | list):
                            collect(value)
                        else:
                            for variant in _value_variants(value):
                                collected.setdefault(name, {})[variant] = tool
                elif isinstance(obj, list):
                    for item in obj:
                        collect(item)

            collect(result)
            # Results accumulate: listing card K-2's transactions must not forget K-1's
            # (benchmark 9 Oct). User corrections still drop stale ids (on_user).
            for name, seen in collected.items():
                self.facts[name] = {**self.facts.get(name, {}), **seen}
            if verified is True:  # a verified identity replaces, it does not accumulate
                for k, v in args.items():
                    if _entity_key(k) and not isinstance(v, dict | list):
                        self.facts[k] = {variant: tool for variant in _value_variants(v)}
                        self.verified_ids.add(k)


_ACTION_ASK = re.compile(
    r"\b(shall i|should i|may i|can i go ahead|do you want me to|would you like me to|"
    r"proceed|go ahead|confirm|place the order|submit|process|onayl|yapay[ıi]m m[ıi]|"
    r"ister misiniz)\b"
)


def _shape(token: str) -> str:
    """'C-1001' -> 'A-9999': same-shaped tokens are candidate corrections of each other."""
    return re.sub(r"[^\W\d_]", "A", re.sub(r"\d", "9", token))


def natural_consent(tool: ToolSpec, ledger: ConversationLedger) -> bool:
    """Fallback consent when no structured confirmation was requested: the agent's previous
    message asked about this action (tool verb or noun appears) and the user clearly agreed.
    Single use per tool per user turn. Without it, a natural "Shall I freeze the card?" →
    "Yes" was blocked and agents gave up and escalated (false refusal, memo §61)."""
    if (
        not ledger.latest_user_affirmative
        or ledger.structured_pending
        or tool.name in ledger.natural_consent_used
    ):
        return False
    q = ledger.answered_question.lower()
    if not q:
        return False
    toks = [t for t in re.split(r"[^a-zA-Z0-9]+", tool.name.lower()) if len(t) > 2]
    if not toks:
        return False
    verb, nouns = toks[0][:5], [t[:5] for t in toks[1:]]
    if verb in q:
        return True  # "…freeze your card…?"
    # Noun plus an explicit action request ("Shall I order the replacement?"), never a bare
    # identification question ("Is this your card?").
    return any(n in q for n in nouns) and bool(_ACTION_ASK.search(q))


def _args_key(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, default=str)


def _enum_args(tool: ToolSpec) -> set[str]:
    props = tool.inputs.get("properties", {}) or {}
    return {k for k, v in props.items() if isinstance(v, dict) and "enum" in v}


def check_mutation(
    tool: ToolSpec, args: dict[str, Any], ledger: ConversationLedger, graph: ToolGraph
) -> MutationCheck:
    chk = MutationCheck(tool_name=tool.name, expected_postconditions=list(tool.postconditions))

    exempt = _enum_args(tool)
    for name in dict.fromkeys([*tool.required_args, *args]):
        if name in exempt:
            continue
        if args.get(name) in (None, ""):
            chk.arguments_grounded = False
            chk.reasons.append(f"argument '{name}' is missing")
            continue
        if not needs_grounding(args[name]):
            continue
        if not ledger.grounded(name, args[name]):
            chk.arguments_grounded = False
            chk.reasons.append(
                f"argument '{name}'={args[name]!r} is not grounded in user input or tool results"
            )

    natural = tool.requires_confirmation and natural_consent(tool, ledger)
    if (
        tool.requires_confirmation
        and ledger.confirmed_action != (tool.name, _args_key(args))
        and not natural
    ):
        chk.confirmation_satisfied = False
        chk.reasons.append(
            "explicit confirmation required: use request_action_confirmation with this tool "
            "and these exact arguments; call only after the user agrees"
        )

    if tool.idempotent is not True and (tool.name, _args_key(args)) in ledger.successful_calls:
        chk.duplicate_risk_clear = False
        chk.reasons.append("this exact action already succeeded; do not repeat it")

    def completed(pre: str) -> bool:
        scoped = {k: v for k, v in args.items() if _entity_key(k)}
        # Follow links to a fixpoint: transaction T-2 -> card K-1 (listed by card) ->
        # customer C-1001 (cards listed by customer). One hop missed the verified
        # customer and blocked every refund (benchmark 9 Oct).
        frontier = dict(scoped)
        while frontier:
            found: dict[str, Any] = {}
            for k, v in frontier.items():
                for k2, v2 in ledger.entities.get((k, _args_key({k: v})), {}).items():
                    if k2 not in scoped:
                        found[k2] = v2
            scoped.update(found)
            frontier = found
        for name, previous in ledger.call_entities:
            if name != pre:
                continue
            shared = scoped.keys() & previous.keys()
            if shared and all(scoped[k] == previous[k] for k in shared):
                return True
        return False

    missing = [p for p in graph.prerequisites(tool.name) if not completed(p)]
    if missing:
        chk.preconditions_satisfied = False
        chk.reasons.append(f"prerequisite step(s) not completed: {', '.join(missing)}")

    if chk.approved and tool.requires_confirmation:
        ledger.confirmed_action = None  # single use, including failed/ambiguous executions
        ledger.natural_consent_used.add(tool.name)
    return chk
