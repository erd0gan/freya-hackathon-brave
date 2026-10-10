"""Spec-driven scenario generation (memo §15, §58, §59, §61, §64).

One LLM call per scenario: (category, sampled policy combination, persona
style, language) → ScenarioDraft. When a state backend exists (mock / τ), the
draft's reference actions are EXECUTED to derive the expected state — never
written by the LLM (memo §58 "oracle validation").
"""

from __future__ import annotations

import copy
import json
import random
from typing import Any, Protocol

from pydantic import BaseModel, Field

from builder.eval.state_eval import check_assertion
from builder.eval.toolcall_eval import args_match
from builder.llm import LLMClient, Message, Role, StructuredOutputError
from builder.schema.bundle import DomainSpec
from builder.schema.scenario import (
    Oracle,
    Persona,
    PersonaStyle,
    ScenarioCategory,
    ScenarioSpec,
    StateAssertion,
    StateOp,
    ToolCallAssertion,
)
from builder.testsynth.categories import GUIDANCE, quotas
from builder.testsynth.oracle_validate import cross_check
from builder.testsynth.policy_graph_walk import sample_combinations
from builder.testsynth.statediff import diff, flatten, is_free_text, set_path

STYLES = list(PersonaStyle)


class StateBackend(Protocol):
    def seed_state(self) -> dict[str, Any]: ...

    def replay(
        self, initial_state: dict[str, Any], actions: list[dict[str, Any]], salt: str = ""
    ) -> tuple[bool, dict[str, Any], list[str]]: ...


class PatchOp(BaseModel):
    path: str
    value: Any = None


class RefAction(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class FaultSpec(BaseModel):
    error: str = "service unavailable"
    times: int = 1
    apply_then_fail: bool = False


class ScenarioDraft(BaseModel):
    user_goal: str
    persona_prompt: str = Field(description="Second person: 'You are ... you want ...'")
    hidden_facts: dict[str, Any] = Field(default_factory=dict)
    state_patch: list[PatchOp] = Field(default_factory=list)
    tool_faults: dict[str, FaultSpec] = Field(default_factory=dict)
    reference_actions: list[RefAction] = Field(default_factory=list)
    expected_tool_calls: list[ToolCallAssertion] = Field(default_factory=list)
    forbidden_tool_calls: list[str] = Field(default_factory=list)
    required_communication: list[str] = Field(default_factory=list)
    forbidden_claims: list[str] = Field(default_factory=list)
    expect_refusal: bool = False
    expect_escalation: bool = False
    success_criteria: str = ""
    target_policies: list[str] = Field(default_factory=list)


GEN_PROMPT = """You write ONE realistic, verifiable test scenario for a customer-service agent.
The scenario tests the business rules listed under TARGET RULES, in the given CATEGORY.

Requirements:
- persona_prompt: second person instructions for a simulated customer ("You are ... You want ...
  If asked, ..."). Natural, not a script. Never tell the customer what the agent should do.
- hidden_facts: everything the customer knows and may need to give (ids, dates, names...).
  Use ONLY records that exist in the STATE (or that state_patch creates).
- state_patch: optional edits to the initial STATE as dotted paths (e.g. "cards.K-1.status") to
  set up the situation. Keep it minimal. Every premise the scenario relies on (an earlier
  order, a blocked card, a balance) MUST exist in STATE or be created here; a story the
  state does not reflect makes the test invalid.
- tool_faults: only for tool-failure / duplicate-side-effect categories.
- reference_actions: ONE valid sequence of tool calls a perfect agent would make (including
  verification), with exact arguments. Must succeed against the patched state. Include ONLY
  calls the rules allow; for a correctly declined request include only the allowed calls.
- expected_tool_calls: the decisive tool calls with key arguments (match "subset"). To forbid
  a call with specific arguments, add it here with "negated": true.
- forbidden_tool_calls: bare tool names (exactly as listed) that must never be called at all.
- required_communication: short facts the agent must tell the customer.
- forbidden_claims: false statements the agent must not make.
- expect_refusal / expect_escalation: true only if the correct outcome is declining / handing off.
- success_criteria: 2-5 bullet lines a strict judge could check from the transcript.
- target_policies: rule ids actually exercised.
Write persona text and communication in language "{language}". Return JSON only."""


def _domain_brief(d: DomainSpec) -> str:
    tools = "\n".join(
        f"- {t.name} [{t.risk_class.value}{', confirm' if t.requires_confirmation else ''}]: "
        f"{t.description} params={json.dumps(t.inputs.get('properties', {}))}"
        for t in d.tools.tools
    )
    rules = "\n".join(f"[{r.id}] {r.description}" for r in d.policy.ordered())
    return f"TASK:\n{d.task.description or d.task.objective}\n\nRULES:\n{rules}\n\nTOOLS:\n{tools}"


class ScenarioGenerator:
    def __init__(
        self,
        domain: DomainSpec,
        llm: LLMClient,
        backend: StateBackend | None = None,
        seed: int = 0,
        max_attempts: int = 2,
    ) -> None:
        self.d = domain
        self.llm = llm
        self.backend = backend
        self.seed = seed
        self.max_attempts = max_attempts
        self.repaired = 0
        self.rejected: list[tuple[str, str]] = []

    def generate(
        self, n: int, categories: list[ScenarioCategory] | None = None
    ) -> list[ScenarioSpec]:
        cats = categories or quotas(self.d, n)
        combos = sample_combinations(self.d.policy, len(cats), seed=self.seed)
        rng = random.Random(self.seed)
        out: list[ScenarioSpec] = []
        for i, (cat, combo) in enumerate(zip(cats, combos, strict=True)):
            style = STYLES[(i + rng.randrange(len(STYLES))) % len(STYLES)]
            sid = f"{self.d.task.task_id or 'task'}-{cat.value}-{i:03d}"
            try:
                s = self.generate_one(sid, cat, combo, style, seed=self.seed + i)
            except StructuredOutputError as exc:
                self.rejected.append((sid, f"generation failed: {exc}"))
                continue
            if s is not None:
                out.append(s)
        return out

    def generate_one(
        self,
        scenario_id: str,
        category: ScenarioCategory,
        target_rules: list[str],
        style: PersonaStyle,
        seed: int = 0,
        extra_instruction: str = "",
    ) -> ScenarioSpec | None:
        state = self.backend.seed_state() if self.backend else None
        rules = "\n".join(
            f"[{r.id}] {r.description}" for r in self.d.policy.rules if r.id in target_rules
        )
        user = (
            f"{_domain_brief(self.d)}\n\n"
            + (
                f"STATE:\n{json.dumps(state, ensure_ascii=False)[:30000]}\n\n"
                if state
                else "STATE: not observable; leave state_patch and reference_actions empty.\n\n"
            )
            + f"CATEGORY: {category.value} — {GUIDANCE[category]}\n"
            f"TARGET RULES:\n{rules or '(any)'}\nPERSONA STYLE: {style.value}\n{extra_instruction}"
        )
        convo = [
            Message(role="system", content=GEN_PROMPT.format(language=self.d.task.language)),
            Message(role="user", content=user),
        ]
        for attempt in range(self.max_attempts):
            draft = self.llm.complete_structured(
                Role.BUILDER,
                ScenarioDraft,
                convo,
                temperature=0.8,
                cache_salt=f"{scenario_id}#{attempt}",
            )
            n_rejected = len(self.rejected)
            # Extra rules are fine (verification is part of most scenarios); a scenario that
            # misses every sampled rule is not (2 of 8 were rejected for extras, 9 Oct).
            if target_rules and not set(draft.target_policies) & set(target_rules):
                self.rejected.append((scenario_id, "target_policies miss the sampled rule set"))
                spec = None
            else:
                spec = self.finalize(scenario_id, category, style, draft, seed)
            if spec is not None:
                if attempt:
                    self.repaired += 1
                return spec
            if attempt + 1 < self.max_attempts and len(self.rejected) > n_rejected:
                # Feed the concrete validation error back once (cheaper than a new scenario).
                reason = self.rejected.pop()[1]
                convo = [
                    *convo,
                    Message(role="assistant", content=draft.model_dump_json()),
                    Message(
                        role="user",
                        content=f"That scenario is invalid: {reason}\n"
                        "Fix it (check ids/states against STATE and tool rules) and "
                        "return the full corrected JSON.",
                    ),
                ]
        return None

    def finalize(
        self,
        scenario_id: str,
        category: ScenarioCategory,
        style: PersonaStyle,
        draft: ScenarioDraft,
        seed: int = 0,
    ) -> ScenarioSpec | None:
        names = set(self.d.tools.names())
        # Models often write "freeze_card with card_id K-2" here; the argument-specific part
        # cannot be checked, but the state oracle ("unchanged") still catches such a call.
        draft.forbidden_tool_calls = [
            t
            for t in draft.forbidden_tool_calls
            if t in names or not any(t.startswith(n) for n in names)
        ]
        bad = [a.tool for a in draft.reference_actions if a.tool not in names]
        bad += [a.tool for a in draft.expected_tool_calls if a.tool not in names]
        bad += [t for t in draft.forbidden_tool_calls if t not in names]
        if bad:
            self.rejected.append((scenario_id, f"unknown tools {bad}"))
            return None
        if any(self.d.policy.get(r) is None for r in draft.target_policies):
            self.rejected.append((scenario_id, "unknown target_policies"))
            return None
        if self.backend is not None:
            for assertion in draft.expected_tool_calls:
                count = sum(
                    a.tool == assertion.tool and args_match(assertion.args, a.args, assertion.match)
                    for a in draft.reference_actions
                )
                valid = (
                    count == 0
                    if assertion.negated
                    else (
                        count >= assertion.min_count
                        and (assertion.max_count is None or count <= assertion.max_count)
                    )
                )
                if not valid:
                    self.rejected.append(
                        (scenario_id, "expected calls contradict reference actions")
                    )
                    return None
            if any(a.tool in draft.forbidden_tool_calls for a in draft.reference_actions):
                self.rejected.append((scenario_id, "forbidden call in reference actions"))
                return None
        oracle = Oracle(
            tool_calls=list(draft.expected_tool_calls)
            + [
                ToolCallAssertion(tool=t, negated=True)
                for t in draft.forbidden_tool_calls
                if t in names
            ],
            required_communication=draft.required_communication,
            forbidden_claims=draft.forbidden_claims,
            reference_actions=[a.model_dump() for a in draft.reference_actions],
            success_criteria=draft.success_criteria,
            expect_refusal=draft.expect_refusal,
            expect_escalation=draft.expect_escalation,
        )
        initial: dict[str, Any] = {}
        if self.backend is not None:
            initial = copy.deepcopy(self.backend.seed_state())
            try:
                for op in draft.state_patch:
                    set_path(initial, op.path, op.value)
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                self.rejected.append((scenario_id, f"bad state patch: {exc}"))
                return None
            ok, final, errors = self.backend.replay(initial, oracle.reference_actions)
            if not ok:
                self.rejected.append((scenario_id, f"reference actions failed: {errors}"))
                return None
            deterministic = bool(getattr(self.backend, "deterministic", True))
            oracle.state = derive_state_assertions(
                initial,
                final,
                None if deterministic else oracle.reference_actions,
            )
            oracle.unchanged = ["*"]
            oracle.tolerate_new_fields = not deterministic
            if not deterministic:
                # A simulated backend writes side records (audit logs, dates) only sometimes
                # (loan rehearsal, 9 Oct). Keep the assertions a second, differently salted
                # replay reproduces; the global no-change check would flag that noise, so
                # false compliance is judged by forbidden calls and policy checks instead.
                _, final2, _ = self.backend.replay(
                    initial, oracle.reference_actions, salt=f"stability:{scenario_id}"
                )
                oracle.state = [a for a in oracle.state if check_assertion(final2, a)[0]]
                oracle.unchanged = []
        scenario = ScenarioSpec(
            scenario_id=scenario_id,
            category=category,
            complexity=len(draft.target_policies) or 1,
            target_policies=draft.target_policies,
            initial_state=initial,
            user_goal=draft.user_goal,
            persona=Persona(
                style=style,
                language=self.d.task.language,
                prompt=draft.persona_prompt,
                hidden_facts=draft.hidden_facts,
            ),
            tool_faults={k: v.model_dump() for k, v in draft.tool_faults.items() if k in names},
            oracle=oracle,
            language=self.d.task.language,
            seed=seed,
            provenance="llm:scenario_generator",
        )
        verdict = cross_check(self.d, scenario, self.llm)
        if not verdict.consistent:
            self.rejected.append((scenario_id, f"policy oracle rejected: {verdict.reason}"))
            return None
        return scenario


def derive_state_assertions(
    initial: dict[str, Any],
    final: dict[str, Any],
    reference_actions: list[dict[str, Any]] | None = None,
) -> list[StateAssertion]:
    """Final-state oracle from replaying the reference actions.

    With `reference_actions` (an LLM-simulated backend), only values the agent itself passes
    are asserted exactly; values the backend generates (ids, dates, statuses) and free text
    the agent may paraphrase are only required to exist (live run 9 Oct: every candidate
    failed on a regenerated claim id and a reworded description).
    """
    agent_values: set[str] | None = None
    if reference_actions is not None:
        agent_values = {
            _canon_value(v)
            for a in reference_actions
            for v in (a.get("args") or {}).values()
            if not isinstance(v, dict | list)
        }
    present = flatten(final)
    out: list[StateAssertion] = []
    for path, (_, new) in diff(initial, final).items():
        if new is None and path not in present:
            out.append(StateAssertion(path=path, op=StateOp.ABSENT))  # really removed
        elif new is None:
            # An explicit null is a placeholder, not a removal (live run 9 Oct: a simulated
            # backend wrote inspection_booked=null, later false, and "absent" failed).
            if agent_values is None:
                out.append(StateAssertion(path=path, op=StateOp.EQ, value=None))
        elif agent_values is None and is_free_text(path, new):
            # Summaries/descriptions are written by the agent in its own words (9 Oct: a
            # transfer summary was compared verbatim and failed every run).
            out.append(StateAssertion(path=path, op=StateOp.EXISTS))
        elif agent_values is None or path.endswith("__len__"):
            out.append(StateAssertion(path=path, op=StateOp.EQ, value=new))
        elif (
            isinstance(new, dict | list)
            or _canon_value(new) not in agent_values
            or (isinstance(new, str) and len(new.split()) >= 4)
            or is_free_text(path, new)
        ):
            out.append(StateAssertion(path=path, op=StateOp.EXISTS))
        else:
            out.append(StateAssertion(path=path, op=StateOp.EQ, value=new))
    return out


def _canon_value(v: Any) -> str:
    return json.dumps(v, sort_keys=True, ensure_ascii=False).lower()
