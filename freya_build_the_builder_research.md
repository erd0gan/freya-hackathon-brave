# Freya × Y Combinator “Build the Builder” Hackathon
## Agent Builder Research Memo, Architecture Options, Experiment Plan, and Implementation Blueprint

**Prepared:** 6 October 2026  
**Event:** Freya (YC S25) — Build the Builder, 10–11 October 2026  
**Purpose:** A living engineering/research document for building an AI system that automatically creates, tests, improves, and selects other agents for unseen tasks.

---

# 0. Executive summary

The challenge is not “write one good agent.” It is much closer to **automated agent engineering under a fixed evaluation budget**:

> **Input:** a new task, tools, requirements/policies, and possibly documents  
> **Output:** a working agent that performs well on unseen benchmark simulations  
> **Constraint:** no manual intervention during final evaluation

The strongest strategy is therefore not a single prompt template. It is a **builder that behaves like a compiler + test engineer + optimizer**:

```text
task + tools + policies + documents
                │
                ▼
        normalize / compile
                │
                ▼
         typed intermediate
          representation
                │
        ┌───────┴────────┐
        ▼                ▼
candidate agents    synthetic tests
        │                │
        └───────┬────────┘
                ▼
          simulations
                │
                ▼
      deterministic scoring
       + failure diagnosis
                │
                ▼
       targeted optimization
                │
                ├──────────────┐
                ▼              │
       improved candidates     │
                │              │
                └──────────────┘
                ▼
      robustness selection
                │
                ▼
         final agent spec
```

The research reviewed below points in the same direction from several independent angles:

- **τ-bench / τ² / τ-Knowledge / τ-Voice:** real agents fail on policy following, tool use, state changes, long horizons, knowledge retrieval, and consistency—not merely on natural-language quality.
- **Anthropic’s “think” tool:** adding a dedicated reflection point around tool-heavy policy tasks can materially improve reliability.
- **SABER:** mutating actions are disproportionately dangerous; targeted verification immediately before state-changing calls is high-leverage.
- **Learning on the Job:** external natural-language memory distilled from prior failures/corrections can improve frozen agents without weight updates.
- **GEPA / TextGrad / OPRO / PromptBreeder / Self-Refine / Reflexion:** natural-language feedback and evolutionary/reflection loops can optimize prompts and compound systems efficiently.
- **ADAS / AgentSquare / AFlow:** the search space should eventually include **agent architecture**, not only prompt wording.

My recommended competition architecture is a **hybrid**:

1. Parse inputs into a domain-agnostic `TaskSpec`, `PolicySpec`, `ToolSpec`, and `KnowledgeSpec`.
2. Compile **4–8 structurally different AgentSpecs**, not 4–8 paraphrases of the same prompt.
3. Add a **mutation gate** for irreversible/state-changing tools.
4. Generate synthetic adversarial tests from the policy/tool graph.
5. Evaluate with deterministic end-state/assertion checks whenever possible.
6. Diagnose failures into a fixed taxonomy.
7. Use a **GEPA-lite / evolutionary reflection loop** to mutate only the failing module or instruction block.
8. Select by **reliability and validation performance**, not average practice score alone.
9. Keep a simple baseline agent as an always-valid fallback.
10. Treat Freya as an adapter boundary so the core system is ready before the private API/rules arrive.

This document deliberately separates **known facts**, **reasonable hypotheses**, and **unknowns**. The largest current unknown is the private Freya builder API and resource/scoring rules; the engineering plan below is built to survive those unknowns.

---

# 1. What is actually known vs. inferred

## 1.1 Known from the event brief

The event description states that:

- The event is fully online on **October 10–11, 2026**.
- The challenge is to **build an AI agent that builds other agents**.
- The builder receives a new task and creates an agent to solve it.
- Final evaluation uses **tasks the builder has not seen before**.
- Generated agents run through benchmark simulations.
- Final ranking is based on generated-agent performance.
- There is no finalist pitch/presentation score in the published event description.
- Solo participation is encouraged; teams are limited to two people.
- Submission must include exact code/dependencies and reproducible run instructions.
- First place receives a YC interview; top placements include additional prizes/interviews.

The implication is critical: **the artifact being judged is the builder’s ability to generalize**, not a hand-tuned agent for a known demo task.

## 1.2 Known from Freya’s public product information

Freya’s public YC profile and website describe:

- Voice AI for **banks, mortgage lenders, fintechs, insurers, and regulated customer-service environments**.
- Integrations with CRMs, core banking, policy/claims systems, internal services, and knowledge bases.
- Agents that can retrieve live data and trigger/update workflows.
- Policy/compliance grounding over internal manuals, regulatory documents, and procedures.
- A **Workflow Builder**.
- A **Simulation Suite** for synthetic scenarios before live deployment.
- Public speech endpoints for TTS/STT/live transcription.
- HTTP calls during conversations and webhooks after calls.

The public site currently exposes concrete TTS/STT/WebSocket integration details, but **does not publicly document the private hackathon “agent builder” API/output format**. We should not assume that the public speech API is the same API used by the competition.

## 1.3 Strong hypothesis: τ-family benchmark shape

The event language, Freya’s product shape, and Freya’s industry make a τ-bench-like hidden evaluation plausible:

```text
policy / requirements
        +
tool schemas / backend state
        +
possibly unstructured knowledge
        +
simulated user
        ↓
multi-turn agent trajectory
        ↓
verifiable end-state / assertions
```

This is a hypothesis, not a confirmed rule.

The reason it is worth preparing for is that it maps unusually well to Freya’s public product:

- policy-heavy regulated workflows,
- backend API calls,
- knowledge manuals,
- synthetic simulation,
- long conversations,
- transactional state changes.

If the actual format differs, the proposed `PlatformAdapter` and typed intermediate representation should isolate the change.

---

# 2. Competitive framing: what is the builder actually optimizing?

A useful formalization is:

```text
Given:
  task description T
  policy / requirement set P
  tool set U
  document corpus K
  resource budget B

Find:
  agent program A

Such that:
  expected hidden benchmark reward R(A | T,P,U,K)
  is maximized,

subject to:
  latency / token / tool / simulation / wall-clock budget B.
```

We do not observe the final hidden distribution directly. We therefore need a surrogate loop:

```text
hidden task distribution
        ≈
synthetic scenario generator
+ practice benchmark
+ cross-domain holdout
+ adversarial perturbations
```

This immediately suggests five separable engineering problems:

1. **Understanding:** convert messy inputs into a precise internal representation.
2. **Synthesis:** generate candidate agent programs/configurations.
3. **Evaluation:** produce useful tests and reliable scores.
4. **Optimization:** improve candidates using failure evidence.
5. **Selection:** choose the candidate most likely to generalize to unseen tasks.

A builder that only solves problem #2 is incomplete.

---

# 3. Research review

# 3.1 τ-bench — why outcome/state evaluation matters

**Paper:** “τ-bench: A Benchmark for Tool-Agent-User Interaction in Real-World Domains”  
**Authors:** Yao, Shinn, Razavi, Narasimhan  
**Source:** https://arxiv.org/abs/2406.12045

τ-bench tests conversational agents that must:

- interact with an LLM-simulated user,
- follow domain-specific policy,
- call domain tools,
- modify or inspect an environment/database,
- produce the correct final outcome.

The paper’s key design choice is **outcome-based evaluation**: the final environment/database state is compared to a target state.

It also introduces **pass^k**, intentionally different from pass@k:

- `pass@k`: at least one of k attempts succeeds.
- `pass^k`: all k independent trials must succeed.

For customer-service systems, the latter is much more meaningful because a system that succeeds 60% of the time can still be unacceptable when the same request is repeated in production.

The original results show major inconsistency: even strong function-calling models can succeed on fewer than half the tasks, and repeated-trial reliability falls sharply.

### Design lesson for our builder

Do not optimize for “the transcript looks reasonable.” Optimize for:

1. correct environment state,
2. required information communicated,
3. policy compliance,
4. repeatability.

If Freya exposes environment state or structured expected assertions, they should dominate the score. LLM-as-a-judge should be a fallback, not the primary correctness mechanism.

---

# 3.2 τ-bench’s current evaluator — do not overfit one reference trajectory

The current `sierra-research/tau2-bench` documentation contains an important nuance.

For airline/retail/telecom, the default reward basis is effectively outcome-oriented (`DB` + `COMMUNICATE`). A listed reference `actions` sequence is generally **one valid trajectory**, not necessarily the only valid trajectory. The evaluator replays the reference actions to derive the target database state and then checks whether the agent reached an equivalent final state.

This is valuable for our own evaluator design.

### Bad evaluator

```text
Expected:
  call get_customer
  call get_card
  call freeze_card

Agent:
  call get_card
  call freeze_card

=> FAIL because exact action sequence differs
```

### Better evaluator

```text
final_db.card.status == "frozen"
required_communication_done == true
policy_constraints_satisfied == true

=> PASS
```

Only force exact tool trajectories when the task explicitly requires a unique action sequence.

### Design lesson

Represent expected behavior as **invariants and target state**, not as a brittle script whenever possible.

---

# 3.3 τ²-bench — dual control and guiding the user

**Paper:** “τ²-Bench: Evaluating Conversational Agents in a Dual-Control Environment”  
**Source:** https://arxiv.org/abs/2506.07982

τ² extends the problem beyond a passive user. In its telecom domain:

- both the agent and user can use tools,
- both can alter a shared environment,
- the agent may need to **instruct the user to perform actions**,
- tasks are generated compositionally,
- the system can separate pure reasoning failures from communication/coordination failures.

This matters because hidden tasks may not all be “agent calls API and returns result.” They may require:

- customer confirmation,
- user-side setup,
- OTP/verification flow,
- “open your app and do X” coordination,
- correction after a failed step.

### Design lesson

`TaskSpec` should not assume the agent is the only actor.

Add:

```text
actor_capabilities:
  agent_tools
  user_actions
  external_system_actions

coordination_requirements:
  user_must_confirm
  user_must_supply
  user_must_perform
```

The generated agent needs a distinction between:

```text
DO_IT_MYSELF
ASK_USER_FOR_INFO
GUIDE_USER_ACTION
WAIT_FOR_CONFIRMATION
ESCALATE
```

---

# 3.4 τ-Knowledge / τ³ banking — retrieval is not enough

**Paper:** “τ-Knowledge: Evaluating Conversational Agents over Unstructured Knowledge”  
**ICML 2026 / PMLR:** https://proceedings.mlr.press/v306/shi26ah.html  
**arXiv:** https://arxiv.org/abs/2603.04370  
**Current code/docs:** https://github.com/sierra-research/tau2-bench

This is probably the most strategically relevant public benchmark for a Freya-like problem.

The τ-Banking environment contains approximately:

- **698 documents**
- ~195K tokens
- 21 knowledge/product categories
- **51 discoverable tools**
- 14 permanent agent tools
- 97 tasks
- roughly 18.6 required documents per task on average
- roughly 9.5 required tool calls per task on average, with some much longer

Agents must combine:

```text
unstructured policy/document retrieval
+
tool discovery
+
multi-step tool execution
+
evolving database state
+
conversation
```

The paper reports that, in its evaluated configurations, even frontier models with high reasoning effort topped out around **25.5% pass^1**. A golden-retrieval setting improves results but still leaves a large gap, implying that retrieval alone is not the bottleneck: **policy reasoning and execution remain hard even when the correct documents are supplied**.

The current repository supports multiple retrieval modes:

- BM25
- dense embeddings
- BM25 + dense
- optional LLM reranker
- grep
- read-only shell search
- combined “alltools”
- golden retrieval for analysis

### Design lessons

#### Lesson A — do not throw all documents into context

Long context is not the same as reliable knowledge use.

Build a knowledge layer that distinguishes:

```text
product facts
definitions
eligibility
process/procedure
policy constraints
exceptions
escalation
tool documentation
```

#### Lesson B — discover procedures, not only answers

Sometimes a document is valuable because it tells the agent **which tool/procedure exists**, not because it contains the final user-facing text.

#### Lesson C — use hybrid retrieval

A practical pipeline:

```text
task/turn
  ↓
query rewrite / intent expansion
  ↓
BM25 + dense retrieval
  ↓
deduplicate
  ↓
rerank
  ↓
cross-document relationship expansion
  ↓
minimal evidence bundle
```

#### Lesson D — precompile stable policy where allowed

If the competition gives the entire corpus before evaluation, preprocess policy/process documents into a rule graph. Runtime retrieval can then use both:

- raw evidence,
- structured rule summaries.

Example:

```yaml
rule_id: CARD_REPLACEMENT_04
trigger: replacement_card
requires:
  - identity_verified
  - lost_or_stolen_status_confirmed
  - explicit_replacement_consent
prohibits:
  - active_fraud_lock_conflict
source_refs:
  - kb/card-replacement.md#section-3
```

The structured rule is not a replacement for evidence; it is a navigation and execution aid.

---

# 3.5 τ-Voice — voice changes the failure distribution

**Paper:** “τ-Voice: Benchmarking Full-Duplex Voice Agents on Real-World Domains”  
**ICML 2026:** https://proceedings.mlr.press/v306/ray26a.html  
**arXiv:** https://arxiv.org/abs/2603.13686

τ-Voice extends τ² into full-duplex audio with:

- policy/tool-grounded tasks,
- simultaneous listening/speaking,
- diverse accents,
- realistic noise,
- turn-taking dynamics,
- direct comparison against text agents.

Reported results are a warning: a strong text-reasoning baseline performs far better than evaluated voice agents. The paper reports voice task completion around **31–51% in clean conditions** and **26–38% under more realistic audio conditions**, preserving only a fraction of the text system’s capability. The authors’ qualitative analysis attributes most failures to agent behavior in the evaluated setup, not simply audio corruption.

### Design lesson if final eval is voice

Do not simply wrap a text agent in TTS/STT.

Voice-specific AgentSpec should add:

- shorter utterance plans,
- explicit repair/repetition policy,
- confirmation for critical names/numbers/amounts,
- barge-in/interruption recovery,
- no giant enumerated lists,
- avoid asking multiple independent questions in a single turn,
- distinguish ASR uncertainty from task uncertainty,
- confirmation before irreversible writes,
- state summaries that survive interruptions.

Example voice policy:

```text
For irreversible actions:
1. summarize action in one short sentence;
2. repeat critical value(s);
3. request explicit yes/no confirmation;
4. if interrupted, do not infer confirmation;
5. perform one write;
6. report result only after tool confirmation.
```

### Competition recommendation

Do not spend large effort on voice before rules confirm voice scoring. Keep voice behavior as a **feature flag / compile target**.

---

# 3.6 Anthropic “think” tool — targeted thinking around policy/tool use

**Source:** Anthropic Engineering, “The ‘think’ tool: Enabling Claude to stop and think”  
https://www.anthropic.com/engineering/claude-think-tool

Anthropic evaluated a dedicated `think` tool on τ-bench.

For the airline domain, it reports a meaningful gain when the think tool is paired with a targeted optimized prompt. The reported pass^1 comparison is around **0.37 baseline → ~0.57 with think + optimized prompting** (the blog contains slightly different displayed values depending on the summarized comparison/table).

The important insight is not the exact number. It is the mechanism:

- the model gets an explicit non-environment-changing reasoning action,
- it can pause after tool outputs,
- it can re-check policies and constraints before acting.

### Design lesson

If Freya supports a non-side-effect “think”/scratch tool or an equivalent internal step, use it strategically—not on every token.

High-value trigger points:

```text
after receiving a surprising tool result
before mutating state
after user changes constraints
when multiple policy rules intersect
before claiming completion
```

If hidden chain-of-thought is not available or not appropriate, the equivalent can be an internal structured checklist:

```json
{
  "preconditions_satisfied": true,
  "consent_obtained": true,
  "policy_rule_ids": ["R14", "R22"],
  "write_is_reversible": false,
  "expected_postcondition": "card.status=frozen"
}
```

---

# 3.7 SABER — guard the writes, not every thought

**Paper:** “SABER: Small Actions, Big Errors — Safeguarding Mutating Steps in LLM Agents”  
**arXiv:** https://arxiv.org/abs/2512.07850  
**Amazon Science:** https://www.amazon.science/publications/saber-small-actions-big-errors-safe-guarding-mutating-steps-in-llm-agents

SABER analyzes long-horizon tool-agent traces and distinguishes:

- non-mutating actions: reads/searches/reasoning,
- mutating actions: actions that change external state.

The paper reports a striking pattern: deviations at mutating steps are much more predictive of failure. In its analysis, each additional mutating deviation severely reduces success odds, whereas read-only deviations often have much lower impact.

SABER’s safeguard contains three ideas:

1. **mutation-gated verification**
2. **targeted reflection before mutations**
3. **block-based context cleaning**

This is directly actionable.

### Recommended implementation: MutationGate

Every `ToolSpec` should contain:

```python
risk_class:
    READ_ONLY
    REVERSIBLE_WRITE
    IRREVERSIBLE_WRITE
    EXTERNAL_SIDE_EFFECT

requires_confirmation: bool
idempotent: bool
```

Before a write:

```text
MutationGate:
  - Is the tool semantically correct for the user goal?
  - Are required parameters grounded?
  - Are policy preconditions satisfied?
  - Is explicit confirmation required and present?
  - Is this action already completed?
  - Could this be a duplicate side effect?
  - Is the current state fresh enough?
  - What postcondition should result?
```

After a write:

```text
PostWriteVerifier:
  - Did the tool report success?
  - Did returned state match expected postcondition?
  - Update internal state.
  - Never claim completion on tool error/ambiguity.
```

### Why this should be P0

It adds small runtime overhead but protects the highest-risk operations. It is also domain-independent.

---

# 3.8 Learning on the Job — external memory from failures

**Paper:** “Learning on the Job: Continual Learning from Deployment Feedback for Frozen-Weights Agents”  
**arXiv:** https://arxiv.org/abs/2607.22157

This 2026 preprint asks whether a frozen model can improve from deployment feedback without weight updates.

The reported approach stores concise natural-language rules in external memory. Feedback can be as weak as a one-bit success/failure outcome or stronger post-hoc corrections.

On the banking domain used by the study:

- learning from an outcome verdict improves single-trial success to about **1.6× baseline**,
- learning from corrections reaches roughly **2.6× baseline**,
- the memory can transfer across model families.

### Design lesson for this hackathon

This is interesting in two different places.

## A. Builder memory across practice tasks

After each simulation:

```text
failure:
  replacement was created without explicit consent

lesson:
  WHEN an action creates a new financial instrument,
  THEN require explicit current-turn consent immediately
  before the mutating call.
```

Store these as reusable, searchable rules with provenance:

```yaml
lesson_id: L023
failure_type: MISSING_CONFIRMATION
applies_to:
  - irreversible_write
  - financial_product_creation
rule: >
  Require explicit current-turn user confirmation immediately
  before creating or replacing an account-linked instrument.
evidence:
  - run_0182
confidence: 0.83
```

Candidate generation retrieves relevant lessons when creating a new agent.

## B. Generated agent memory

Only use persistent runtime memory if rules allow it and if hidden tasks recur in a meaningful way. Otherwise it can become contamination/overfitting.

### Important caveat

The paper is a recent preprint. We should treat the result as evidence for experimentation, not guaranteed performance.

---

# 3.9 GEPA — best fit for targeted prompt evolution

**Paper:** “GEPA: Reflective Prompt Evolution Can Outperform Reinforcement Learning”  
https://arxiv.org/abs/2507.19457  
**DSPy docs:** https://dspy.ai/3.3.1/api/optimizers/GEPA/overview/

GEPA is particularly relevant because it uses:

- execution traces,
- scalar scores,
- **textual feedback**,
- targeted component mutation,
- a Pareto frontier of candidates,
- optional merging/crossover.

The authors report large sample-efficiency advantages over rollout-heavy RL in their tasks.

### Why it maps well to our challenge

Our evaluator can produce rich text:

```text
Score: 0
Failure: TOOL_CALLED_TOO_EARLY
Rule violated: AUTH_02
At turn 7 the agent called close_account before verify_identity.
The user had not supplied the second verification factor.
```

A GEPA-like optimizer can propose:

```diff
Before any account-closing action:
+ verify both identity factors
+ if the second factor is absent, ask for it
+ never infer verification from prior successful calls
```

This is far more actionable than optimizing only from `reward=0`.

### Recommended competition version: GEPA-lite

Do not require the entire framework unless integration is easy.

Implement:

```text
candidate pool
+ trace collection
+ failure text
+ targeted component selection
+ reflection mutation
+ validation scoring
+ Pareto retention
+ optional crossover
```

Optimize components separately:

```text
global_policy_prompt
tool_use_policy
knowledge_retrieval_policy
mutation_guard_prompt
completion_verifier_prompt
voice_style_prompt
few_shot_examples
```

This avoids rewriting the entire system because of one localized failure.

---

# 3.10 TextGrad — textual credit assignment for compound systems

**Paper:** “TextGrad: Automatic ‘Differentiation’ via Text”  
https://arxiv.org/abs/2406.07496

TextGrad treats textual feedback like a gradient over components of a compound AI system.

The conceptual benefit is **credit assignment**:

```text
bad final result
  ↓
which module caused it?
  ↓
what textual change should improve that module?
```

For this project, a TextGrad-like decomposition is more valuable than literal framework adoption.

Example:

```text
Failure: incorrect tool argument
Possible sources:
  TaskCompiler?       no
  ToolSemanticParser? yes
  PromptCompiler?     maybe
  RuntimePlanner?     yes
```

Then mutate only implicated components.

### Design lesson

The optimizer should operate over a dependency graph of components, not a single monolithic prompt string.

---

# 3.11 OPRO — simple score-conditioned prompt optimization

**Paper:** “Large Language Models as Optimizers”  
https://arxiv.org/abs/2309.03409

OPRO’s pattern is simple:

```text
candidate A → score
candidate B → score
candidate C → score
        ↓
LLM sees prior candidates and outcomes
        ↓
proposes better candidate
```

It is a very useful fallback optimizer because implementation is trivial.

### Use in our system

If GEPA-lite is too expensive or unstable:

```text
top candidates + failure summaries + scores
→ optimizer LLM
→ 2–3 new candidates
```

Do not feed only aggregate scores. Include failure clusters.

---

# 3.12 PromptBreeder — evolve the mutation operator too

**Paper:** “Promptbreeder: Self-Referential Self-Improvement Via Prompt Evolution”  
https://arxiv.org/abs/2309.16797

PromptBreeder evolves both:

- task prompts,
- the prompts that mutate task prompts.

This suggests an optional advanced feature: mutation operators can themselves adapt.

Example mutation operators:

```text
clarify prerequisites
reduce verbosity
add negative constraint
add failure recovery
reorder policy priority
turn prose into checklist
add tool-specific invariant
generalize
```

If one operator repeatedly produces good candidates for policy-heavy tasks, its selection probability can rise.

### Competition position

Useful as a second-stage improvement, not MVP.

---

# 3.13 Self-Refine and Reflexion — builder-side reflection

**Self-Refine:** https://arxiv.org/abs/2303.17651  
**Reflexion:** https://arxiv.org/abs/2303.11366

Self-Refine:

```text
generate → critique → refine
```

Reflexion:

```text
attempt → environment feedback → verbal reflection → memory → next attempt
```

Both reinforce the same practical lesson:

> Reflection is most useful when grounded in actual failure evidence.

### Where to use them

Prefer builder-time reflection:

```text
build
→ simulate
→ inspect trace
→ reflect
→ rebuild
```

rather than making every production-agent turn invoke three extra LLM calls.

The generated agent should stay as simple as possible unless the benchmark rewards extra reasoning enough to offset latency/failure surface.

---

# 3.14 ADAS — automate the design of the agent itself

**Paper:** “Automated Design of Agentic Systems”  
https://arxiv.org/abs/2408.08435

ADAS argues that agent design itself can be a search problem. Its Meta Agent Search experiment has a meta-agent generate new agent programs based on an archive of previous designs and results.

### Strategic importance

The Freya challenge is almost an applied ADAS problem.

However, unrestricted code generation is risky for a 48-hour contest:

- unsafe execution,
- difficult reproducibility,
- dependency explosions,
- syntax/runtime failure,
- unpredictable latency.

### Recommended adaptation

Use a typed DSL instead of arbitrary code:

```python
AgentSpec(
    planning="structured",
    memory="turn_state",
    retrieval="hybrid",
    verifier="mutation_only",
    completion_check=True,
    tool_parallelism=False,
)
```

Then compile AgentSpec into the platform format.

This keeps the search space expressive but valid.

---

# 3.15 AgentSquare — modular architecture search

**Paper:** “AgentSquare: Automatic LLM Agent Search in Modular Design Space”  
https://arxiv.org/abs/2410.06153

AgentSquare abstracts agent design into modules such as:

- planning,
- reasoning,
- tool use,
- memory,

then searches through combinations/evolutions of modules.

The paper reports sizable average improvements over hand-designed agents in its benchmark suite.

### Design lesson

Our candidate diversity should be **architectural**, not cosmetic.

Bad population:

```text
A: “You are a helpful agent…”
B: “You are an expert helpful agent…”
C: “Act as a professional agent…”
```

Good population:

```text
A: ReAct + mutation gate
B: plan → act → verify
C: explicit state machine
D: policy-first agent
E: tool-first planner
F: retrieval-first + structured execution
```

---

# 3.16 AFlow — workflow search with MCTS

**Paper:** “AFlow: Automating Agentic Workflow Generation”  
**ICLR 2025:** https://proceedings.iclr.cc/paper_files/paper/2025/hash/5492ecbce4439401798dcd2c90be94cd-Abstract-Conference.html

AFlow represents workflows as code graphs and uses Monte Carlo Tree Search to explore modifications.

The paper reports average gains over strong baselines and demonstrates that workflow architecture can matter enough for smaller models to outperform larger ones on some tasks/cost settings.

### Design lesson

If Freya’s private platform exposes a workflow graph, architecture search may be very powerful.

Search operators could include:

```text
add verifier before write
add retry after tool error
insert retrieval before planner
split planner and executor
add confirmation state
remove redundant reflection
add completion check
```

### Competition position

Do not implement full MCTS first.

Start with:

- a small architecture template library,
- evolutionary mutation,
- successive halving.

MCTS can be added only if evaluation is cheap and the platform allows enough simulations.

---

# 3.17 Newer 2026 evidence: be open to few-shot optimization

A recent preprint, **FLARE** (arXiv:2608.02919), reports that reflective few-shot optimization can outperform GEPA in its evaluated tasks, including a tool-calling benchmark.

This is useful as a caution:

> Do not assume instruction-only evolution is always superior to example selection.

Our search space should include a small set of **few-shot behavioral examples** as mutable components.

Possible example families:

```text
missing-information example
ambiguous-entity example
write-confirmation example
tool-error recovery example
user-correction example
policy refusal example
```

The optimizer can choose which examples to include under a prompt-length budget.

Because FLARE is very new and task transfer is uncertain, treat it as an experiment rather than the primary design.

---

# 4. Principles extracted from the research

The papers differ, but the high-level engineering conclusions are consistent.

## Principle 1 — Optimize outcomes, not beautiful transcripts

When possible:

```text
DB state / environment assertion
    >
tool-action invariant
    >
required communication
    >
LLM judge
```

## Principle 2 — Writes deserve more reasoning than reads

Spend compute near irreversible actions.

## Principle 3 — Retrieval is a subsystem, not a prompt line

Knowledge-heavy tasks need deliberate search, reranking, evidence packaging, and cross-document reasoning.

## Principle 4 — Reliability matters more than a lucky high score

A candidate with 0.85 mean and huge variance may be worse than a stable 0.78 candidate.

## Principle 5 — Use rich failure feedback

`score=0` tells the optimizer almost nothing.

## Principle 6 — Separate train/optimization, validation, and final test

Practice score overfitting is one of the largest competition risks.

## Principle 7 — Search over architecture as well as wording

Prompt paraphrases are not sufficient exploration.

## Principle 8 — Keep the final agent simpler than the builder

The builder can be expensive and multi-agent. The generated agent should be lean unless the benchmark explicitly rewards deeper runtime workflows.

## Principle 9 — Always have a fallback

If optimization times out, return a valid robust baseline.

## Principle 10 — Make every expensive behavior budget-aware

Every loop needs a stop condition.

---

# 5. Proposed typed intermediate representation

The private Freya API is unknown. That makes a neutral IR essential.

# 5.1 `TaskSpec`

```python
class TaskSpec(BaseModel):
    task_id: str | None
    objective: str
    success_conditions: list[str]
    required_outputs: list[str]

    intents: list[str]
    entities: list[str]
    required_information: list[str]
    optional_information: list[str]

    user_can_change_goal: bool = True
    agent_can_ask_questions: bool = True

    termination_conditions: list[str]
    escalation_conditions: list[str]

    ambiguities: list[str]
```

# 5.2 `PolicyRule`

```python
class PolicyRule(BaseModel):
    id: str
    description: str

    trigger: str
    preconditions: list[str]
    required_actions: list[str]
    prohibited_actions: list[str]
    exceptions: list[str]

    priority: int
    source_refs: list[str]
```

# 5.3 `ToolSpec`

```python
class ToolSpec(BaseModel):
    name: str
    description: str

    inputs: dict
    outputs: dict

    preconditions: list[str]
    postconditions: list[str]

    risk_class: Literal[
        "READ_ONLY", "REVERSIBLE_WRITE", "IRREVERSIBLE_WRITE", "EXTERNAL_SIDE_EFFECT"
    ]

    idempotent: bool | None
    requires_confirmation: bool
    retryable: bool | None

    failure_modes: list[str]
    related_tools: list[str]
```

# 5.4 `KnowledgeSpec`

```python
class KnowledgeSpec(BaseModel):
    corpus_size: int
    categories: list[str]

    retrieval_modes: list[str]
    policy_documents: list[str]
    procedure_documents: list[str]
    tool_documents: list[str]

    requires_cross_document_reasoning: bool
```

# 5.5 `AgentSpec`

```python
class AgentSpec(BaseModel):
    architecture: str

    system_prompt_sections: dict[str, str]
    few_shot_examples: list[str]

    planning_mode: str
    memory_mode: str
    retrieval_mode: str
    mutation_guard: str
    completion_verifier: str

    temperature: float
    max_steps: int

    tool_policy: dict
    voice_policy: dict | None
```

# 5.6 `ScenarioSpec`

```python
class ScenarioSpec(BaseModel):
    scenario_id: str
    category: str

    initial_state: dict
    user_goal: str
    user_behavior: str

    expected_state_assertions: list[str]
    forbidden_state_assertions: list[str]
    required_communications: list[str]

    seed: int
```

# 5.7 `FailureReport`

```python
class FailureReport(BaseModel):
    failure_type: str
    severity: int

    evidence_turns: list[int]
    implicated_rules: list[str]
    implicated_tools: list[str]
    implicated_components: list[str]

    explanation: str
    suggested_mutation_targets: list[str]
```

This typed boundary is the single most important defense against API uncertainty.

---

# 6. Tool semantic compilation

A tool name should not be trusted as sufficient meaning.

Example tool:

```json
{
  "name": "issue_replacement",
  "parameters": {
    "account_id": "string",
    "reason": "string",
    "shipping": "string"
  }
}
```

The builder should infer:

```yaml
purpose: create a replacement physical card
risk: irreversible_write
requires_confirmation: true
preconditions:
  - customer authenticated
  - target account/card uniquely identified
  - replacement reason established
likely_postconditions:
  - replacement order exists
  - old card state may change
failure_modes:
  - duplicate order
  - invalid shipping mode
```

## Tool graph

Build edges for:

```text
requires-before
reads-produced-data-for
mutually-exclusive-with
reverses
verifies
```

Example:

```text
verify_identity
    ↓
lookup_accounts
    ↓
select_card
    ↓
freeze_card
    ↓
issue_replacement
```

This graph supports:

- planning,
- test generation,
- precondition checks,
- mutation safety,
- recovery.

---

# 7. Policy compilation

Raw policy text should be transformed into atomic rules.

## 7.1 Rule extraction pass

For each policy statement extract:

```text
trigger
preconditions
required behavior
prohibition
exception
priority
source
```

## 7.2 Rule contradiction pass

Find:

```text
same trigger + conflicting required action
exception overrides base rule
different source versions
```

## 7.3 Rule-to-tool mapping

Map policy rules to affected operations.

Example:

```text
AUTH_02 → close_account, freeze_card, update_address
CONSENT_04 → create_replacement, transfer_funds
LIMIT_03 → transfer_funds
```

## 7.4 Rule priority

A generated agent should not treat all instructions equally.

Possible precedence:

```text
system/platform constraints
> explicit policy prohibitions
> regulatory/compliance rules
> user authorization/consent
> task success
> conversational helpfulness
```

The exact precedence should be compiled from competition rules, not hard-coded forever.

---

# 8. Candidate architecture designs

This section intentionally provides several architectures because the correct one depends on the unpublished platform constraints.

---

# Architecture A — One-shot Prompt Compiler

```text
TaskSpec + PolicySpec + ToolSpec
           ↓
      prompt compiler
           ↓
       one agent
```

### Pros

- simplest
- low latency
- low cost
- easy to reproduce
- likely compatible with any platform

### Cons

- no self-testing
- no adaptation
- weak robustness
- high risk of hidden-task failure

### Use

Always implement as **baseline/fallback**.

---

# Architecture B — Guarded ReAct Agent

```text
observe
  ↓
think / update state
  ↓
choose:
  ask | read-tool | write-tool | respond
  ↓
if write → MutationGate
  ↓
tool
  ↓
verify
  ↓
repeat
```

### Pros

- strong general baseline
- lightweight
- compatible with unknown task domains
- mutation safety is evidence-backed

### Cons

- prompt can become monolithic
- state may drift in long conversations
- retrieval behavior can be inconsistent

### Recommended status

**P0 runtime architecture.**

---

# Architecture C — Planner → Executor → Verifier

```text
       Task + current state
               ↓
            Planner
               ↓
         structured plan
               ↓
            Executor
               ↓
        proposed action
               ↓
  read ────────┴────── write
                         ↓
                    Verifier
                         ↓
                       tool
```

### Pros

- clearer separation of responsibilities
- easier credit assignment
- easier prompt optimization by component
- naturally supports mutation verification

### Cons

- extra model calls
- latency
- planner/executor disagreement
- if plan goes stale, executor may blindly follow it

### Improvement

Plans must be **receding-horizon**, not fixed at conversation start.

Only plan the next 1–3 steps and replan after:

- tool results,
- user correction,
- policy conflict,
- state mutation.

### Recommended status

**P1 candidate architecture.**

---

# Architecture D — Explicit Finite-State / Workflow Compiler

Builder compiles policy/tool graph into a workflow.

```text
START
  ↓
IDENTIFY_INTENT
  ↓
VERIFY_IDENTITY
  ↓
COLLECT_REQUIRED_FIELDS
  ↓
CHECK_POLICY
  ↓
CONFIRM_WRITE
  ↓
EXECUTE
  ↓
VERIFY_POSTCONDITION
  ↓
COMPLETE
```

### Pros

- deterministic
- highly auditable
- excellent policy compliance
- lower stochastic variance
- ideal for recurring regulated workflows

### Cons

- poor fit for highly open-ended tasks
- workflow synthesis can be brittle
- unseen tasks may need states we did not model

### Best use

When the input task clearly describes:

- one/few intents,
- strong procedural policy,
- explicit tools,
- transactional outcome.

### Recommended status

**P1, selected automatically by architecture classifier.**

---

# Architecture E — Policy Graph + Dynamic Executor

```text
documents
   ↓
policy graph
   ↓
relevant rules ─────┐
                    ↓
user/task → planner → executor → tool
                    ↑
             current DB state
```

### Pros

- good for knowledge-heavy regulated domains
- keeps source-grounded rules explicit
- supports complex exceptions

### Cons

- policy extraction errors can poison execution
- source graph construction costs time
- needs evidence/provenance discipline

### Recommended status

**P1 for large-document inputs.**

---

# Architecture F — Candidate Population + Evolution

```text
spec
 ↓
A B C D E F
 ↓
tests
 ↓
scores + failures
 ↓
mutation / crossover
 ↓
A' C' F'
 ↓
validation
 ↓
winner
```

This is a **builder architecture**, not necessarily the runtime agent.

### Pros

- direct match to challenge objective
- robust to prompt sensitivity
- can discover surprising solutions
- naturally combines GEPA/PromptBreeder/ADAS ideas

### Cons

- simulation budget heavy
- risk of practice overfit
- needs careful caching and parallelism

### Recommended status

**P0 builder architecture**, with strict budget control.

---

# Architecture G — Builder Multi-Agent Team

Internal builder roles:

```text
Context Analyst
Policy Compiler
Tool Analyst
Agent Architect
Test Designer
Failure Analyst
Optimizer
Final Auditor
```

### Pros

- specialization
- clearer intermediate artifacts
- easier debugging

### Cons

- expensive
- more failure boundaries
- role outputs can contradict each other
- potentially overengineered for 48 hours

### Recommendation

Implement these as **logical modules/prompts** first, not autonomous chatting agents. They can be separate LLM calls behind typed schemas.

---

# Architecture H — Fully Programmatic ADAS / Generated Code

Meta-agent writes arbitrary new agent code.

### Pros

- enormous search space
- potentially novel workflows

### Cons

- code safety
- run failures
- reproducibility
- dependency issues
- hard to fit unknown submission rules

### Recommendation

**Experimental branch only. Do not make this the main competition path.**

---

# 9. Recommended hybrid architecture

The strongest risk-adjusted design is:

```text
                    ┌──────────────────────┐
Input bundle ──────▶│ Context Compiler     │
                    │ Task/Policy/Tool/KB  │
                    └──────────┬───────────┘
                               │
                               ▼
                    Typed DomainSpec / IR
                               │
         ┌─────────────────────┼─────────────────────┐
         ▼                     ▼                     ▼
  Architecture           Test Generator       Lesson Memory
   Generator                   │                     │
         │                     │                     │
         ▼                     │                     │
  Candidate AgentSpecs        │                     │
         │                     │                     │
         └─────────────┬───────┴─────────────────────┘
                       ▼
                   Simulator
                       │
                       ▼
             Deterministic Evaluator
                       │
                       ▼
                 Failure Analyzer
                       │
                       ▼
             GEPA-lite Optimizer
                       │
                 mutate targeted
                    component
                       │
                       └─────────────┐
                                     │
                       ┌─────────────┘
                       ▼
              Robustness Selector
                       │
                       ▼
            Freya Platform Adapter
```

Runtime candidate default:

```text
structured single agent
+ hybrid retrieval if needed
+ explicit turn state
+ mutation gate
+ post-write verification
+ completion verifier
```

This gives us most of the upside without full arbitrary workflow/code search.

---

# 10. Runtime agent state design

A runtime state should be compact and explicit.

```json
{
  "goal": "freeze lost card",
  "active_intents": ["lost_card"],
  "known_facts": {
    "identity_verified": true,
    "card_id": "..."
  },
  "missing_information": [],
  "policy_rules_in_scope": ["AUTH_02", "CARD_11"],
  "completed_actions": ["verify_identity"],
  "pending_actions": ["freeze_card"],
  "last_tool_result": {
    "tool": "lookup_card",
    "status": "ok"
  },
  "awaiting_confirmation": false,
  "completion_conditions": [
    "target_card_frozen",
    "user_informed"
  ]
}
```

Important: this is not hidden chain-of-thought. It is **operational state**.

The system prompt should encourage updating only facts grounded by:

- user input,
- tool results,
- policy retrieval.

Never store guesses as facts.

---

# 11. Runtime action protocol

At every turn select exactly one action class:

```text
ASK_USER
GUIDE_USER
RETRIEVE_KNOWLEDGE
CALL_READ_TOOL
PROPOSE_WRITE
CALL_WRITE_TOOL
RESPOND
ESCALATE
COMPLETE
```

## 11.1 Read tools

Read actions can be relatively cheap.

Before calling:

- check required args,
- reuse fresh prior results if safe,
- avoid redundant lookups.

## 11.2 Write tools

Every write passes `MutationGate`.

## 11.3 Completion

Before `COMPLETE`:

```text
- Are all explicit user intents resolved?
- Are required writes confirmed by tool outputs?
- Are policy-required communications complete?
- Is any pending confirmation unresolved?
- Did any tool return an error/partial result?
```

This can eliminate premature-success errors.

---

# 12. MutationGate design

This should be a first-class component.

```python
class MutationCheck(BaseModel):
    tool_name: str

    goal_alignment: bool
    arguments_grounded: bool
    preconditions_satisfied: bool
    policy_allows: bool
    confirmation_satisfied: bool
    duplicate_risk_clear: bool
    state_is_fresh: bool

    expected_postconditions: list[str]

    approved: bool
    reason: str
```

### Hard fail rules

Never approve if:

```text
required argument is guessed
explicit prohibition applies
confirmation required but absent
same non-idempotent action already succeeded
target entity is ambiguous
```

This safeguard can be implemented:

- deterministically when rules/tool metadata allow,
- with an LLM verifier for semantic cases,
- preferably both.

---

# 13. Context cleaning

Long conversations create stale-state risk.

Borrowing the spirit of SABER:

Represent context blocks:

```text
SYSTEM/POLICY           never drop
CURRENT TASK            never drop
CURRENT STATE           refresh
ACTIVE EVIDENCE         retain
RECENT TOOL RESULTS     retain until superseded
OLD RETRIEVAL RESULTS   compress/drop
OLD CONVERSATION        summarize
FAILED PLAN             mark obsolete
```

Do not let old tool results remain implicitly authoritative after state mutations.

A simple freshness map:

```python
state_version += 1 after every write
```

Read results tied to old state version can be marked stale.

---

# 14. Knowledge architecture

# 14.1 Retrieval modes

Implement at least:

```text
BM25
dense
hybrid
```

Optional:

```text
reranker
shell/grep
```

## 14.2 Retrieval query generation

Generate multiple query views:

```text
user phrasing
canonical intent
policy term
tool/procedure term
exception/eligibility term
```

## 14.3 Evidence packing

Do not simply concatenate top-k.

Pack by role:

```text
1. governing rule
2. exception
3. procedure/tool doc
4. product fact
```

## 14.4 Cross-document expansion

If a retrieved document references:

- another policy,
- exception,
- procedure name,
- tool name,

follow that edge before action.

## 14.5 Knowledge cache

The builder can preprocess documents once:

```text
doc summary
document type
entities
rule IDs
tool mentions
cross-links
embedding
```

This saves simulation budget.

---

# 15. Synthetic test generation

This is likely the highest-leverage differentiator.

For every task/policy bundle, generate tests from the **spec**, not generic brainstorming.

# 15.1 Happy path

- all information provided,
- tools succeed,
- no policy conflict.

# 15.2 Missing information

- one required tool argument absent,
- ambiguous target entity,
- missing confirmation,
- missing authentication factor.

# 15.3 User correction

- customer changes a key fact,
- corrects ID/date/amount,
- retracts consent,
- changes intended action.

# 15.4 Policy pressure

- user requests prohibited behavior,
- tries to skip authentication,
- asks agent to “just do it,”
- claims special status that tools do not verify.

# 15.5 State dependency

- prerequisite state missing,
- action must occur in a specific order,
- previous state has changed.

# 15.6 Tool failure

- 404/not found,
- 500/retryable,
- partial result,
- timeout,
- structured validation failure.

# 15.7 Duplicate side effect

- tool response was delayed,
- agent is tempted to repeat a successful payment/order/update.

# 15.8 Multi-intent

- two legitimate requests,
- one allowed and one prohibited,
- dependency between two requests.

# 15.9 Tool/user disagreement

- user says payment succeeded,
- backend says pending/failed.

The agent should trust the authoritative source according to policy, explain uncertainty, and not invent resolution.

# 15.10 Goal shift

- user starts with A, switches to B,
- then returns to A.

# 15.11 Knowledge traps

- semantically similar wrong policy,
- obsolete-looking exception,
- linked document required,
- tool described only in documentation.

# 15.12 Long-context drift

- inject irrelevant conversation,
- repeat earlier obsolete data,
- test whether agent uses stale constraints.

# 15.13 Voice-specific

- interrupted confirmation,
- ASR number ambiguity,
- accented entity name,
- noisy amount,
- user says “yes” to a different question than the pending irreversible action.

---

# 16. Test oracle design

Every generated scenario should have a machine-checkable oracle where possible.

```yaml
expected:
  state:
    card.status: frozen

  unchanged:
    replacement_orders.count: 0

  required_communication:
    - card has been frozen

  forbidden:
    - claim replacement ordered

  policy:
    AUTH_02: satisfied
    CONSENT_04: not_triggered
```

Do not use an LLM judge to verify `card.status == frozen` if direct state is available.

---

# 17. Scoring design

A competition-agnostic internal score:

```text
hard_task_success
policy_compliance
state_correctness
communication_completeness
tool_efficiency
latency/cost
```

But do not collapse everything too early.

Maintain a vector:

```python
ScoreVector(
    task_success,
    policy,
    state,
    communication,
    reliability,
    tool_cost,
    token_cost,
    latency,
)
```

## 17.1 Lexicographic safety

For regulated/state-changing tasks:

```text
policy violation
  should often dominate
minor conversational quality.
```

For example:

```text
candidate A:
  90% task success
  8% prohibited writes

candidate B:
  82% task success
  0% prohibited writes
```

B may be the correct selection depending on official scoring.

## 17.2 Reliability

Track:

```text
mean success
worst-case success
pass^2
pass^3
failure cluster frequency
```

## 17.3 Confidence-aware selection

When runs are few, prefer a lower confidence bound rather than raw mean.

Simple version:

```text
selection_score =
  mean_success
  - lambda * standard_error
```

or use a Bayesian Beta posterior for binary pass/fail.

---

# 18. Optimizer design

# 18.1 Candidate initialization

Generate candidates across architecture axes.

```yaml
candidate_A:
  architecture: guarded_react
  retrieval: hybrid
  verifier: mutation_gate

candidate_B:
  architecture: planner_executor
  retrieval: hybrid
  verifier: mutation_gate

candidate_C:
  architecture: finite_state
  retrieval: on_demand
  verifier: deterministic

candidate_D:
  architecture: guarded_react
  retrieval: policy_graph
  verifier: mutation_gate_plus_completion
```

# 18.2 Failure clustering

Group failures:

```text
MISSING_INFORMATION
AMBIGUOUS_ENTITY
WRONG_TOOL
WRONG_ARGUMENT
WRONG_TOOL_ORDER
TOOL_CALLED_TOO_EARLY
TOOL_NOT_CALLED
DUPLICATE_SIDE_EFFECT
POLICY_VIOLATION
MISSING_CONFIRMATION
STALE_STATE
RETRIEVAL_MISS
RETRIEVAL_WRONG_POLICY
CROSS_DOC_REASONING_FAILURE
TOOL_ERROR_NOT_RECOVERED
USER_CORRECTION_IGNORED
PREMATURE_TERMINATION
REQUIRED_INFO_NOT_COMMUNICATED
VOICE_CONFIRMATION_FAILURE
```

# 18.3 Targeted mutation

Map failure to component.

```text
RETRIEVAL_MISS
  → retrieval query prompt / retrieval mode

MISSING_CONFIRMATION
  → mutation guard / policy prompt

WRONG_ARGUMENT
  → ToolSpec compiler / tool calling prompt

PREMATURE_TERMINATION
  → completion verifier

USER_CORRECTION_IGNORED
  → state manager

POLICY_VIOLATION
  → policy compiler / rule priority
```

# 18.4 Mutation operators

```text
clarify
compress
add invariant
add negative example
add positive example
reorder priority
add explicit precondition
add recovery rule
split one instruction into checklist
remove contradictory line
tighten completion criteria
```

# 18.5 Pareto retention

Do not keep only overall winner.

A candidate that solves a difficult subset may contain a valuable behavior.

Track per-scenario vector and retain candidates that are best on at least some slices.

# 18.6 Crossover

Merge complementary components:

```text
candidate X:
  best retrieval

candidate Y:
  best write safety

candidate Z =
  X.retrieval + Y.mutation_guard
```

Compile and revalidate; do not assume modules compose cleanly.

---

# 19. Budget-aware search

Resource limits are unknown, so implement a generic budget object.

```python
class OptimizationBudget(BaseModel):
    max_wall_seconds: float | None
    max_builder_llm_calls: int | None
    max_simulations: int | None
    max_generated_agents: int | None
    max_total_tokens: int | None
    max_cost_usd: float | None
```

## Successive halving

Example when budget permits:

```text
8 candidates × 3 cheap tests
            ↓
top 4 × 5 tests
            ↓
top 2 × full validation
            ↓
mutate top/Pareto candidates
            ↓
2 challengers + champion
            ↓
robustness trials
```

Never give every weak candidate the same budget as the leaders.

---

# 20. Lesson memory

Use a separate memory from raw traces.

Raw traces are huge and noisy.

Distill into:

```yaml
lesson:
  trigger_pattern: irreversible write after ambiguous target
  rule: >
    Before any irreversible write, ensure exactly one target entity
    is resolved from authoritative tool data; if multiple candidates
    remain, ask the user to disambiguate.
  positive_examples: [...]
  negative_examples: [...]
  provenance: [...]
  confidence: 0.91
```

Retrieval key:

```text
task features
tool risk classes
policy categories
failure classes
```

Prevent memory poisoning:

- only add lessons from verified failures/corrections,
- merge duplicates,
- retire contradicted rules,
- store source/provenance.

---

# 21. Architecture selector

The builder should choose architectures based on task features.

Example rules:

```text
IF
  tools <= 5
  AND task is transactional
  AND policy is procedural
THEN
  finite_state or guarded_react

IF
  documents > threshold
THEN
  policy_graph + hybrid retrieval

IF
  many interacting tool dependencies
THEN
  planner_executor + verifier

IF
  task highly open-ended
THEN
  guarded_react

IF
  voice=true
THEN
  enable voice interaction policy

IF
  resource budget tiny
THEN
  one-shot compiler + mutation gate
```

This can start rule-based and later become learned/search-based.

---

# 22. Freya adapter boundary

Because private details are missing, define an interface now.

```python
class PlatformAdapter(Protocol):
    def validate_agent(self, spec: AgentSpec) -> ValidationResult: ...

    def deploy_agent(self, spec: AgentSpec) -> AgentHandle: ...

    def run_simulation(
        self,
        agent: AgentHandle,
        scenario: ScenarioSpec,
    ) -> SimulationTrace: ...

    def export_submission(self, agent: AgentHandle) -> SubmissionArtifact: ...
```

If the competition later supports only prompt/config:

```text
FreyaAdapter.compile(AgentSpec)
  → prompt + model params + tools
```

If it supports workflow graphs:

```text
FreyaAdapter.compile(AgentSpec)
  → nodes + conditions + tools + prompts
```

If it provides a platform-native simulator:

```text
run_simulation()
  → Freya API call
```

If no simulator API exists:

```text
run_simulation()
  → our mock / τ-bench harness
```

The rest of the builder remains unchanged.

---

# 23. Mock platform before the event

Build a local mock with:

```text
task
policy
tools
database
user simulator
evaluator
```

A minimal domain could be:

```text
customer accounts
cards
address updates
refunds
appointment booking
```

The purpose is not realism; it is to test the full pipeline:

```text
ingest
→ compile
→ candidate generation
→ simulation
→ evaluation
→ failure
→ mutation
→ selection
```

If this loop works before Saturday, Freya integration becomes an adapter problem rather than an architecture problem.

---

# 24. Use τ-bench as a pre-hackathon training ground

The current open-source repository is:

https://github.com/sierra-research/tau2-bench

Despite the repository name, the current project includes τ³-era voice/knowledge features.

Current README requirements include Python 3.12+ and `uv`.

Example text run from the project docs:

```bash
git clone https://github.com/sierra-research/tau2-bench
cd tau2-bench

uv sync

tau2 run \
  --domain airline \
  --agent-llm <model> \
  --user-llm <model> \
  --num-trials 1 \
  --num-tasks 5
```

Knowledge extras:

```bash
uv sync --extra knowledge
```

Voice extras:

```bash
uv sync --extra voice
```

## Recommended experimental protocol

### Development domains

Use:

```text
airline
retail
telecom
```

for implementation/debugging.

### Holdout

Use:

```text
banking_knowledge
```

as a more challenging “unseen style” holdout.

Then reverse the split for selected components to ensure we are not banking-specific.

### Do not tune to every public task

The goal is not to climb the public τ leaderboard before the event. It is to learn which builder mechanisms generalize.

---

# 25. Experiment matrix

Every feature should justify itself with an ablation.

| ID | Variant | Purpose |
|---|---|---|
| B0 | one-shot prompt | floor baseline |
| B1 | B0 + explicit operational state | state consistency |
| B2 | B1 + think/checklist | deliberate tool reasoning |
| B3 | B2 + MutationGate | protect writes |
| B4 | B3 + completion verifier | reduce premature success |
| B5 | B4 + hybrid retrieval | knowledge performance |
| B6 | B5 + synthetic test generator | builder self-evaluation |
| B7 | B6 + GEPA-lite | targeted optimization |
| B8 | B7 + lesson memory | cross-task learning |
| B9 | B8 + architecture population | architecture search |
| B10 | B9 + crossover | component recombination |

Measure:

```text
task success
pass^k
policy violations
wrong writes
duplicate writes
retrieval misses
avg tool calls
avg tokens
wall time
```

Do not assume B10 is best. Extra complexity can hurt.

---

# 26. Highest-priority ablations

If time is short, answer these questions first.

## Q1 — Does MutationGate materially improve success/reliability?

Compare B1 vs B3.

## Q2 — Does self-generated testing predict held-out success?

Correlation:

```text
synthetic validation score
vs
public/held-out benchmark score
```

If correlation is weak, the synthetic test generator needs work.

## Q3 — Does optimization improve validation or only training?

GEPA/OPRO gains that disappear on holdout are useless.

## Q4 — Does planner/executor beat guarded single-agent enough to justify latency?

## Q5 — Is hybrid retrieval better than simple BM25/dense under the actual corpus?

## Q6 — Do few-shot examples improve tool discipline?

## Q7 — Does lesson memory transfer across domains or overfit?

---

# 27. Search over few-shot examples

Few-shot demonstrations are a distinct optimization axis.

Candidate examples should represent **behavioral invariants**, not domain trivia.

Good generic example:

```text
User: “Close whichever account has the smallest balance.”
Tool result: two accounts tie for smallest balance.

Correct behavior:
Ask the user to disambiguate.
Do not choose arbitrarily.
```

Another:

```text
User gives consent.
Later changes mind before tool call.

Correct behavior:
Treat latest intent as authoritative.
Do not execute old consent.
```

These can transfer across domains.

Avoid examples containing exact practice-task answers.

---

# 28. Failure analysis pipeline

For each failed run:

```text
1. locate first decisive deviation
2. classify failure
3. connect to policy/tool/state
4. identify component responsible
5. propose minimal mutation
6. generate regression test
```

This is analogous to software debugging.

Example:

```yaml
failure:
  first_bad_turn: 11
  type: DUPLICATE_SIDE_EFFECT
  tool: submit_payment

cause:
  agent interpreted a timeout as failure
  despite tool documentation marking request idempotency unknown

fix:
  after uncertain write outcome,
  read transaction status before retrying

regression_test:
  tool returns timeout after successful side effect
```

The regression test is as important as the fix.

---

# 29. Adversarial test generation

A second generator should try to break the agent.

Its objective:

```text
Find a user trajectory that causes:
- policy violation
- wrong state mutation
- unsupported claim
- premature completion
```

Constraints:

- must remain realistic,
- must not exploit nonsense parser bugs,
- should vary phrasing and order.

This adversarial generator can produce high-value tests with fewer scenarios than random generation.

---

# 30. Red-team categories for regulated workflows

Focus disproportionately on:

```text
authentication bypass
authorization confusion
consent ambiguity
wrong entity/account
financial amount/date mistakes
duplicate transactions
policy exception misuse
PII disclosure
tool result contradiction
unverified claims
escalation failures
```

Even if Freya’s hidden tasks are not banking, these map broadly to enterprise workflows.

---

# 31. Completion verifier

Many agents fail by stopping early.

Implement a separate `CompletionSpec`:

```yaml
must_hold:
  - all_required_intents_resolved
  - no_pending_write
  - no_unresolved_tool_error
  - required_communication_sent
  - expected_state_verified

must_not_hold:
  - waiting_for_user_info
  - ambiguous_target
  - pending_confirmation
```

Before final response, run:

```text
COMPLETE?
  yes/no + missing conditions
```

If no, continue.

---

# 32. Model routing

Official rules will determine what is allowed.

Conceptually separate:

```text
Builder model
Reflection/optimizer model
Generated agent model
Judge model
User simulator model
Embedding model
```

They need not be the same.

## Builder/reflection

Use the strongest reasoning model budget permits because it runs offline during construction.

## Runtime agent

Optimize for:

```text
task success
latency
tool reliability
cost/resource limits
```

## Judge

Prefer deterministic evaluators. Use a strong judge only for semantic criteria not directly measurable.

## User simulator

High diversity matters more than “helpfulness.”

---

# 33. Temperature strategy

No universal answer.

A reasonable starting point:

```text
builder candidate generation: moderate/high diversity
optimizer mutation: moderate
runtime transactional agent: low
user simulator: moderate/high
judge: low
```

But always verify empirically because some reasoning/tool models behave differently under sampling.

---

# 34. Prevent practice-set overfitting

This is a major danger.

## 34.1 Split generated tests

```text
train/optimization
validation
challenge-style holdout
```

## 34.2 Freeze validation

The optimizer must not read validation failure traces in the main search loop if we want a clean generalization estimate.

## 34.3 Cross-domain holdout

Optimize on some τ domains, evaluate another.

## 34.4 Mutation constraints

Reject candidate prompt changes that encode:

- exact account IDs,
- exact task phrasings,
- known practice answers,
- domain-specific loopholes unrelated to rules.

## 34.5 Prompt lint

Detect suspicious literal overlap with practice tasks.

---

# 35. Reproducibility

Submission explicitly asks for reproducible instructions.

Capture:

```text
git commit
Python version
dependency lock
model/provider identifiers
prompt versions
random seeds
retrieval index version
document hashes
tool schema hash
optimizer config
selected candidate ID
all simulation results
```

Write a machine-readable manifest:

```json
{
  "builder_version": "...",
  "commit": "...",
  "seed": 42,
  "platform_adapter_version": "...",
  "selected_candidate": "cand_027",
  "input_hash": "...",
  "generated_at": "..."
}
```

A one-command runner:

```bash
python -m builder.run \
  --input challenge.json \
  --output submission/
```

---

# 36. Observability

Every run needs a trace.

```text
run_id
candidate_id
scenario_id
seed

user messages
agent messages
retrieval queries/results
tool calls/results
state diffs
policy checks
mutation checks
score vector
failure report
latency
token usage
```

Without this, the optimizer cannot learn and we cannot debug Saturday night.

---

# 37. Suggested repository layout

```text
freya-agent-builder/
│
├── README.md
├── pyproject.toml
├── uv.lock
├── .env.example
├── Makefile
│
├── docs/
│   ├── RESEARCH.md
│   ├── ARCHITECTURE.md
│   └── RUNBOOK.md
│
├── src/
│   └── builder/
│       ├── cli.py
│       ├── config.py
│       │
│       ├── schema/
│       │   ├── task.py
│       │   ├── policy.py
│       │   ├── tool.py
│       │   ├── knowledge.py
│       │   ├── agent.py
│       │   └── eval.py
│       │
│       ├── ingest/
│       │   ├── task_compiler.py
│       │   ├── policy_compiler.py
│       │   ├── tool_compiler.py
│       │   └── knowledge_compiler.py
│       │
│       ├── knowledge/
│       │   ├── bm25.py
│       │   ├── dense.py
│       │   ├── hybrid.py
│       │   ├── rerank.py
│       │   └── policy_graph.py
│       │
│       ├── synth/
│       │   ├── architecture_selector.py
│       │   ├── candidate_generator.py
│       │   ├── prompt_compiler.py
│       │   └── fewshot.py
│       │
│       ├── runtime/
│       │   ├── state.py
│       │   ├── mutation_gate.py
│       │   ├── completion.py
│       │   └── context_cleaner.py
│       │
│       ├── testsynth/
│       │   ├── scenario_generator.py
│       │   ├── adversarial.py
│       │   └── oracle_generator.py
│       │
│       ├── eval/
│       │   ├── simulator.py
│       │   ├── state_eval.py
│       │   ├── policy_eval.py
│       │   ├── communication_eval.py
│       │   ├── judge.py
│       │   └── metrics.py
│       │
│       ├── failure/
│       │   ├── taxonomy.py
│       │   ├── analyzer.py
│       │   └── lessons.py
│       │
│       ├── optimize/
│       │   ├── budget.py
│       │   ├── tournament.py
│       │   ├── gepa_lite.py
│       │   ├── mutations.py
│       │   ├── pareto.py
│       │   └── crossover.py
│       │
│       ├── adapters/
│       │   ├── base.py
│       │   ├── mock.py
│       │   ├── tau.py
│       │   └── freya.py
│       │
│       └── storage/
│           ├── experiments.py
│           ├── traces.py
│           └── cache.py
│
├── tests/
│
├── evals/
│   ├── local/
│   ├── regression/
│   └── holdout/
│
├── experiments/
│
└── submission/
```

Do not build every file before the event. The tree expresses boundaries.

---

# 38. Minimal P0 implementation

The minimum serious version should contain:

```text
Task/Policy/Tool compiler
Candidate generator
Guarded ReAct runtime
MutationGate
Synthetic scenario generator
State/assertion evaluator
Failure taxonomy
Tournament selection
Platform adapter
Trace logging
```

If these work, we have a viable submission.

---

# 39. P1 improvements

```text
hybrid retrieval
completion verifier
GEPA-lite targeted optimization
lesson memory
planner-executor candidate
finite-state candidate
few-shot search
```

---

# 40. P2 experiments

```text
architecture crossover
policy graph
adversarial test generator
Pareto candidate selection
dynamic optimizer allocation
voice-specific compiler
```

---

# 41. P3 / only if everything else is stable

```text
MCTS/AFlow-like workflow search
arbitrary generated code
RL/fine-tuning
complex multi-agent runtime
full self-evolving mutation operators
```

---

# 42. End-to-end pseudocode

```python
def build_agent(bundle, budget):
    spec = compile_domain(bundle)

    lessons = lesson_store.retrieve(spec)

    scenarios = test_generator.generate(
        spec=spec,
        lessons=lessons,
        budget=budget.test_budget,
    )

    train, validation = split_scenarios(scenarios)

    candidates = candidate_generator.generate(
        spec=spec,
        lessons=lessons,
        architectures=architecture_selector(spec),
        n=budget.initial_candidates,
    )

    champion = robust_baseline(spec)
    champion_score = evaluate(champion, validation)

    while budget.can_continue():
        sampled = tournament.sample(candidates, train, budget)

        for candidate in sampled:
            traces = simulate(candidate, train, budget)

            results = deterministic_evaluator.score(
                spec=spec,
                traces=traces,
            )

            failures = failure_analyzer.analyze(
                spec=spec,
                traces=traces,
                results=results,
            )

            children = optimizer.propose_mutations(
                candidate=candidate,
                failures=failures,
                spec=spec,
            )

            for child in children:
                if not validate_agent_spec(child):
                    continue

                score = evaluate(child, validation)

                candidates.add(child, score)

                if robustness_better(score, champion_score):
                    champion = child
                    champion_score = score

        candidates = pareto_prune(candidates)

    final = final_audit(champion, spec)

    return platform_adapter.export_submission(final)
```

---

# 43. Final audit

Before submission candidate is accepted:

```text
syntax/config valid
all referenced tools exist
no forbidden tool names
no unresolved placeholder
prompt length within limits
all required dependencies pinned
no practice-task literals
mutation guard enabled where applicable
completion verifier configured
run instructions verified from clean environment
```

If any fail:

```text
fall back to previous champion
```

Never let the optimizer destroy the only working agent at the end.

---

# 44. Hackathon timing plan

## Before October 10

### October 6

- create repo and project skeleton
- implement typed schemas
- implement mock platform
- implement baseline compiler
- implement trace format

### October 7

- implement scenario generator
- implement deterministic state/policy assertions
- implement MutationGate
- integrate a small τ-bench slice

### October 8

- implement failure analyzer
- implement tournament/successive halving
- implement GEPA-lite reflection mutation
- add train/validation split

### October 9

- run cross-domain holdouts
- profile time/cost
- build fallback mode
- write reproducible CLI/runbook
- prepare Freya adapter stub

## October 10 — when private rules arrive

First 60–120 minutes should be spent **reading and mapping the rules**, not immediately coding random features.

Fill this table:

| Question | Answer |
|---|---|
| Builder input schema | |
| Agent output schema | |
| Is workflow graph allowed? | |
| What agent models are allowed? | |
| External LLM APIs allowed? | |
| Internet/retrieval allowed? | |
| Documents supplied? | |
| Voice or text? | |
| Builder wall-clock limit? | |
| Max simulations? | |
| Token/API/resource limits? | |
| Official score components? | |
| Tie-breakers? | |
| Can builder test its own agent? | |
| Can it create multiple candidates? | |
| Can generated agents use persistent memory? | |
| Is hidden final distribution same domains as practice? | |

Then modify only adapters/config/search budget.

## October 10 afternoon

Get an end-to-end submission running as early as possible.

Do not wait for the optimizer.

Priority:

```text
valid submission
> robust baseline
> automatic tests
> optimizer
> architecture search
```

## October 11

- freeze a known-good candidate early
- make improvements on a branch/candidate lineage
- compare with clean validation
- stop risky architectural changes near deadline
- rerun reproducibility from a clean environment
- package exact versions

---

# 45. Rule-dependent decision tree

## If only a system prompt/config may be generated

Focus on:

```text
TaskSpec compiler
prompt section synthesis
few-shot selection
MutationGate instructions
GEPA/OPRO prompt evolution
```

## If workflows are allowed

Add:

```text
workflow template search
explicit states
conditional edges
retry/recovery
write verifier nodes
```

## If arbitrary code is allowed

Still prefer typed DSL first.

Only then consider generated code modules in a sandbox.

## If simulation budget is tiny

Use:

```text
strong baseline
+ 2–3 architecture candidates
+ synthetic local evaluator
+ one validation tournament
```

## If simulation budget is large

Use:

```text
population search
Pareto selection
crossover
adversarial scenario generation
repeated reliability trials
```

## If final tasks include large document corpora

Prioritize:

```text
knowledge preprocessing
hybrid retrieval
policy graph
cross-document expansion
```

## If voice is scored

Enable voice compiler and validate:

```text
confirmation
interruptions
ASR ambiguity
turn length
latency
```

---

# 46. What I would *not* do first

## Do not start with fine-tuning/RL

Two-day event + unknown rules + data scarcity is a poor fit.

## Do not build a beautiful UI

Leaderboard performance dominates; the event description explicitly says no pitch score.

## Do not build a huge multi-agent runtime by default

Every LLM call is another stochastic failure point.

## Do not optimize only on a public/practice score

Hidden-task overfitting is likely fatal.

## Do not use LLM judge for things a database assertion can prove

## Do not hard-code bank-specific tool names

The builder must generalize.

## Do not retry non-idempotent writes blindly

## Do not let the agent claim success before tool/postcondition verification

---

# 47. Risk register

| Risk | Impact | Mitigation |
|---|---:|---|
| Freya API radically different | High | adapter boundary + typed IR |
| Very low builder time limit | High | fast baseline mode |
| No self-simulation API | High | local mock + practice harness |
| Voice unexpectedly required | Medium/High | feature-flagged voice policy |
| Very small model only | High | workflow/structure over raw reasoning |
| Large document corpus | High | hybrid retrieval + policy graph |
| Hidden tasks unlike banking | High | domain-agnostic schemas + cross-domain holdout |
| Optimizer overfits practice | High | frozen validation + holdout |
| LLM judge noisy | Medium | deterministic assertions first |
| Search too expensive | High | successive halving + cache |
| Candidate mutation breaks valid config | High | schema validation + fallback champion |
| Duplicate irreversible actions | High | mutation gate + idempotency logic |
| Long-context drift | Medium/High | context cleaning + state versioning |
| Prompt becomes enormous | Medium | section budgets + compression |
| Generated tests are unrealistic | High | calibrate against τ-style public tasks |

---

# 48. Competition strategy

The builder should compete on three dimensions.

## 48.1 Generalization

Make task/policy/tool compilation domain-independent.

## 48.2 Reliability

Protect writes, verify completion, and select based on repeated runs.

## 48.3 Search efficiency

Do not brute-force dozens of similar prompts. Use failure-directed mutation.

A likely winning narrative is:

> We treat agent creation as compilation and verification, not prompt writing.  
> The builder converts requirements/tools/knowledge into a typed task model, generates diverse agent architectures, synthesizes tests, evaluates environment outcomes, diagnoses failures, and evolves the weakest components under a fixed budget.

That is both technically coherent and directly aligned with the challenge.

---

# 49. A concrete first version

If I had to choose a single implementation right now, before private rules:

## Builder

```text
TaskCompiler
PolicyCompiler
ToolCompiler
CandidateGenerator(n=6)
ScenarioGenerator(n≈12–30)
TournamentEvaluator
FailureAnalyzer
GEPA-lite mutator
RobustnessSelector
FreyaAdapter(stub)
```

## Candidates

```text
C1 guarded ReAct
C2 guarded ReAct + think/checklist
C3 planner/executor + mutation verifier
C4 finite-state workflow
C5 policy-first planner
C6 retrieval-first + completion verifier
```

## Runtime invariants

```text
never guess tool arguments
ask when required information is missing
one irreversible action at a time
explicit confirmation when required
verify policy before writes
verify tool result after writes
never claim success from intention alone
latest user correction overrides stale state
do not silently retry unknown non-idempotent writes
complete only when all success conditions hold
```

## Search

```text
cheap train tournament
→ failure-directed mutations
→ frozen validation
→ repeated champion trials
```

This gives a very strong P0/P1 system without betting the weekend on exotic research.

---

# 50. Open questions to answer immediately when acceptance materials arrive

1. What exact object is the builder expected to output?
2. Can it create multiple agents internally before selecting one?
3. Can it invoke generated agents for self-testing?
4. Are external LLM providers allowed?
5. Is the builder itself run in a networked environment?
6. Is there a hard wall-clock limit?
7. Are token/cost limits scored or merely constrained?
8. Are agents allowed arbitrary workflows or only prompt/tool configs?
9. Can the generated agent include a “think” tool?
10. Are tools labeled read/write or must we infer this?
11. Are tool side effects reversible/idempotent?
12. Are policies provided as one document or a corpus?
13. Is knowledge retrieval provided by Freya or built by us?
14. Is the final benchmark text, voice, or mixed?
15. Are user interruptions modeled?
16. Are final tasks generated from the same domains as practice?
17. Is score binary task completion or weighted sub-metrics?
18. Is policy violation separately penalized?
19. Are required communications checked?
20. Does exact tool trajectory matter or only end state?
21. How many final tasks?
22. How many trials per task?
23. Is reliability/pass^k effectively part of ranking?
24. What are tie-breakers?
25. Can persistent memory cross tasks during final evaluation?
26. Is builder state reset between tasks?
27. Can builder access previous final-task results?
28. Are there hidden adversarial or policy-bypass cases?
29. Are exact dependency versions required in submission?
30. Does the submission run in Docker or a provided runtime?

---

# 51. Research-source map

This document prefers primary papers, official docs, and official code over secondary summaries.

## Event / Freya

- YC Event — Build the Builder  
  https://events.ycombinator.com/AI-FDE-Build-the-Builder26

- Freya YC profile  
  https://www.ycombinator.com/companies/freya

- Freya public site  
  https://www.freyavoice.ai/

- Freya integrations  
  https://www.freyavoice.ai/integrations

- Freya pricing/product capabilities (Workflow Builder, Simulation Suite)  
  https://www.freyavoice.ai/pricing

- Freya careers / engineering context  
  https://www.freyavoice.ai/careers

## τ benchmark family

- τ-bench paper  
  https://arxiv.org/abs/2406.12045

- τ²-bench paper  
  https://arxiv.org/abs/2506.07982

- Current τ-bench / τ² / τ³ codebase  
  https://github.com/sierra-research/tau2-bench

- Current evaluation documentation  
  https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md

- Current knowledge retrieval documentation  
  https://github.com/sierra-research/tau2-bench/blob/main/src/tau2/knowledge/README.md

- τ-Knowledge / τ-Banking, ICML 2026  
  https://proceedings.mlr.press/v306/shi26ah.html  
  https://arxiv.org/abs/2603.04370

- τ-Voice, ICML 2026  
  https://proceedings.mlr.press/v306/ray26a.html  
  https://arxiv.org/abs/2603.13686

## Reliability / tool-use methods

- Anthropic — The “think” tool  
  https://www.anthropic.com/engineering/claude-think-tool

- SABER  
  https://arxiv.org/abs/2512.07850  
  https://www.amazon.science/publications/saber-small-actions-big-errors-safe-guarding-mutating-steps-in-llm-agents

- Learning on the Job  
  https://arxiv.org/abs/2607.22157

## Automated optimization / agent design

- GEPA  
  https://arxiv.org/abs/2507.19457  
  https://dspy.ai/3.3.1/api/optimizers/GEPA/overview/

- TextGrad  
  https://arxiv.org/abs/2406.07496

- OPRO  
  https://arxiv.org/abs/2309.03409

- PromptBreeder  
  https://arxiv.org/abs/2309.16797

- Self-Refine  
  https://arxiv.org/abs/2303.17651

- Reflexion  
  https://arxiv.org/abs/2303.11366

- Automated Design of Agentic Systems (ADAS)  
  https://arxiv.org/abs/2408.08435

- AgentSquare  
  https://arxiv.org/abs/2410.06153

- AFlow, ICLR 2025  
  https://proceedings.iclr.cc/paper_files/paper/2025/hash/5492ecbce4439401798dcd2c90be94cd-Abstract-Conference.html

- FLARE (recent preprint; optional experimental reference)  
  https://arxiv.org/abs/2608.02919

---

# 52. Evidence quality / caveats

Not all sources have the same status.

## Peer-reviewed / conference proceedings

- AFlow — ICLR 2025
- τ-Knowledge — ICML 2026
- τ-Voice — ICML 2026

## Official benchmark code/documentation

- `sierra-research/tau2-bench`

This is especially valuable for understanding the *current implementation*, which may differ from older paper descriptions.

## Company engineering source

- Anthropic think-tool article

Useful empirical engineering evidence, but not a neutral benchmark paper.

## Preprints / recent research

- GEPA
- SABER
- Learning on the Job
- ADAS
- AgentSquare
- TextGrad
- OPRO
- PromptBreeder
- Self-Refine
- Reflexion
- FLARE

Treat reported gains as evidence that a technique is worth testing—not a guarantee it transfers to Freya.

---

# 53. Final recommendation

The core idea should be:

## **Agent Builder = Compiler + Test Generator + Optimizer**

Not:

```text
LLM(task) → system prompt
```

But:

```text
context
  ↓
typed understanding
  ↓
multiple architectures
  ↓
generated tests
  ↓
environment-grounded evaluation
  ↓
failure localization
  ↓
targeted reflection/evolution
  ↓
robust selection
```

The implementation should especially emphasize:

1. **typed task/tool/policy representation**
2. **MutationGate around writes**
3. **machine-checkable state/assertion evaluation**
4. **synthetic edge-case generation**
5. **failure taxonomy and regression tests**
6. **validation-separated GEPA-lite evolution**
7. **hybrid knowledge retrieval when documents are large**
8. **reliability/pass^k style selection**
9. **adapter isolation for the unpublished Freya API**
10. **a simple always-valid fallback agent**

The single most important engineering principle is:

> **Use the builder’s intelligence to remove uncertainty from the generated agent.**

The builder can spend extra reasoning, run simulations, compare alternatives, and compile policies so that the final agent has fewer decisions to make under pressure.

That is likely a better competition strategy than simply putting the strongest available model behind the longest possible system prompt.

---

# 54. Immediate next implementation sequence

Once a repository is available, I would implement in this order:

```text
01 schemas
02 mock platform
03 task/policy/tool compiler
04 baseline prompt compiler
05 guarded runtime + MutationGate
06 trace logging
07 deterministic evaluator
08 synthetic scenario generator
09 failure taxonomy/analyzer
10 tournament search
11 GEPA-lite mutation
12 τ-bench adapter
13 hybrid retrieval
14 lesson memory
15 planner-executor / finite-state candidates
16 Freya adapter when private docs arrive
```

At step 10 we already have a credible builder. Everything after that increases ceiling.

---

**End of research memo.**

This should be treated as a living document. The moment the private rules/API arrive, the “unknown” assumptions should be replaced with exact platform contracts and the experiment plan reprioritized against the official scoring function.


---

# 55. Headline: the Freya platform API is publicly documented

The memo (§1.2, §22) assumes that Freya's agent builder API is private and unknown. **That assumption is wrong.** Freya publishes a full API reference with an OpenAPI spec:

- Docs index: https://docs.freyavoice.ai/llms.txt
- OpenAPI: https://docs.freyavoice.ai/api-reference/openapi.json

It covers almost every part of a builder loop: creating agents, versions and branches, simulations with personas, unit tests with assertions, and a turn-by-turn chat API. Most likely the hackathon platform is this same API, or a sandboxed copy of it. **Read it today and build the real `FreyaAdapter` before Saturday instead of a stub.**

## 55.1 What the agent object looks like (*Create agent*)

The builder's output is very likely a `config` JSON with these blocks:

| Block | Key fields | Relevance to the builder |
|---|---|---|
| `llmConfig` | `provider`, `model`, `systemPrompt` (≤200K chars), `temperature` (default 0.7), `maxTokens` (default 1000), `firstMessage`, `firstMessageMode`, `tools[]`, `farewellMessage`, `retryExhaustionMessage` | The main optimization surface |
| `llmConfig.tools[]` | `function.name`, `function.handler`, `function.description`, `function.parameters` (JSON Schema), `function.transition_to`, `config`, `response_mapping`, `extraction_schema`, **`pre_process_code`**, **`post_process_code`** | Tool descriptions are prompt surface too. `pre_process_code` may allow **deterministic argument validation** (see §56). |
| `guardrailsConfig` | rules of type `exact` / `regex` / `dictionary` / `structured` / `classifier`, with actions `refuse` / `regenerate` / `log_only`, plus `max_regenerate_retries` | Output guardrail. Can block forbidden claims. |
| `sttConfig` | `language` (default `multi`), **`confidenceReask`** (0–1 threshold) | Built-in "ask again when the speech-to-text confidence is low" |
| `ttsConfig` | `speechNormalization` (numbers, dates, emails…), `textSubstitutions` | Voice readability of numbers and IDs |
| `callTimeoutSettings` | `silenceTimeout`, `maximumDuration`, … | Long tasks can be cut off by `maximumDuration` |
| `routingLlmConfig`, `extractionLlmConfig` | separate LLMs | Workflow routing and data extraction |
| `complianceConfig`, `piiRedactionConfig`, `privacyConfig` | compliance flags | Probably not scored, but banking-flavoured |

The *Get default agent config* endpoint returns the platform default config. **That is the natural baseline (B0).**

## 55.2 Workflows

Workflows are visual graphs with these node types: **Agent**, **Function** (API call), **Condition**, **Transfer**, **Wait** and **Loop**. Edges can be sequential, conditional, parallel or loop. Typed variables persist across nodes. Read-only workflow endpoints and workflow templates are documented (list, get, list templates). A public create-workflow endpoint was not visible in the index, so **ask the organizers whether the builder may emit workflows.**

Architecture D (finite-state) maps directly onto Freya workflows. MetaAgent (ICML 2025) is evidence that automatically generated FSM-based agent systems can match hand-crafted ones: https://arxiv.org/abs/2507.22606

## 55.3 Simulations, personas and unit tests: the platform's own evaluator

- **Personas** (*Create persona*): `prompt`, `systemPrompt`, **`successCriteria`**, **`evaluationPrompt`**, `tags`. Up to 200 per request. There is also a *Generate persona preview* endpoint, which generates personas from a prompt.
- **Simulations** (*Create simulation*): `agentId`, `agentVersionId`, `selectedPersonaIds` (1–1000), **`mode`: `voice` | `chat`**, `runsPerPersona` (1–50), `maxConcurrentCalls` (≤1000), `maxCallDurationSeconds` (60–3600, **default 60**), `assertions` (unit-test IDs), and LLM overrides for the assistant, persona and analysis models.
- **Unit tests** (*Create unit test*): an input snapshot of messages plus up to 50 assertions:
  - **Routing:** `node_entered` / `route_target`, can be negated
  - **Extraction:** `exists` / `equals` / `contains` / `regex` / `absent`, with a confidence threshold
  - **Tool call:** arguments matched as a **subset or exactly**, can be negated
- Results per persona: `passed` / `failed` / `running` / `not_run`. Iteration history is available.

**STRONG HYPOTHESIS — not confirmed:** the hidden "benchmark simulations" may be Freya simulations, meaning personas with success criteria and an evaluation prompt (**LLM-judged**) plus unit-test assertions (tool calls with arguments, extractions, routing). This differs from τ-bench's database-state oracle. See §57.

## 55.4 Chat API: cheap, branchable self-testing

*Send a chat message* drives an agent or workflow turn by turn over text. Two features matter:

- `fromMessageId` **forks** the conversation from an earlier assistant turn.
- *Fork a chat call at an anchor* copies a conversation up to an anchor and **retargets it to a different version, agent or workflow**.

This allows **counterfactual testing at the first decisive turn** (§28 of the memo). Take a failed trace, fork it just before the bad action, replay it against the mutated candidate, and see whether the decision changes. That is much cheaper and less noisy than re-running the whole conversation. The response includes `endedByAgent`. Whether tool calls appear in the chat response is not documented, so check *Get call details* (transcript and messages).

## 55.5 Versions and branches: a ready-made candidate store

Versions, branches, commits, three-way merge, and branch activity (which simulations and unit tests ran on each branch) map one-to-one onto the memo's candidate population (§18). Each candidate becomes a branch, the winner gets merged, and the version graph doubles as the reproducibility log (§35).

## 55.6 AI Studio: Freya's own prompt improver

AI Studio is Freya's chat-based prompt editor (evaluate, plan, execute). The organizers' internal tool presumably does something similar. **Our builder has to beat "AI Studio applied once."** Treat that as an informal baseline.

## 55.7 Knowledge base

The knowledge base takes PDF, TXT, Markdown and DOCX files (≤10 MB), runs hybrid semantic and keyword search, and returns results in under 200 ms with the title and heading path. It is in beta. If the hidden tasks ship documents, **the platform retrieval may be the only one allowed at runtime.** In that case, builder-side preprocessing (§14.5) should shape the *documents* themselves: split them, add headings, and add rule summaries as separate documents. It cannot replace the retriever.

## 55.8 Stack hints

*Close a chat call* mentions **pipecat**. A public Freya FDE take-home from 2025 used LiveKit, a streaming STT→LLM→TTS pipeline and a latency target. The runtime is a pipecat/LiveKit-style cascaded pipeline, so latency per LLM turn matters in voice mode.

---

# 56. Map the memo's runtime components to Freya primitives

The memo designs MutationGate, state tracking and the completion verifier as *our code*. On Freya they have to be expressed through platform primitives:

| Memo component | Freya realization (most to least deterministic) |
|---|---|
| MutationGate: argument grounding and precondition checks | `pre_process_code` on write tools, if it can reject or return an error (**verify**); otherwise a workflow Condition node before a Function node; otherwise a prompt checklist |
| MutationGate: explicit confirmation | Prompt protocol (summarize, read back, yes/no), plus a unit test asserting the **negated tool call** before confirmation |
| Post-write verification | `post_process_code` / `response_mapping` to normalize the tool result into an explicit `status`, plus a prompt rule: never claim success without `status=ok` |
| Forbidden claims ("refund issued" without a tool call) | `guardrailsConfig` regex or classifier rule with `regenerate` |
| Operational state | Workflow variables, or a short state block the prompt asks the model to keep |
| ASR uncertainty | `sttConfig.confidenceReask` threshold |
| Numbers and IDs in voice | `speechNormalization`, `textSubstitutions`, and a char-by-char readback rule |
| Completion verifier | Prompt checklist before `farewellMessage` / end-call; tune `maxCallDurationSeconds` so long tasks don't time out |
| Determinism | `temperature` low (0–0.3) for transactional agents; test whether the platform default of 0.7 hurts |

**Rule:** prefer the most deterministic primitive available. Each prompt-only safeguard is a stochastic safeguard.

---

# 57. Two scoring regimes: build evaluators for both

| | τ-style (memo's assumption) | Freya-native (new hypothesis) |
|---|---|---|
| Oracle | Final DB state × required communication | Persona `successCriteria` / `evaluationPrompt` (LLM judge) + tool-call / extraction / routing assertions |
| Reward | Product: one miss → 0 | Probably pass/fail per persona run |
| What wins | Correct writes and saying the required facts | Same, plus **the judge must be able to see it**: explicit confirmations, explicit outcome statements, exact tool arguments |

Implications:

1. **Say the outcome explicitly.** Example: "Your card ending 4417 is now frozen." An LLM judge can't credit what wasn't said, and τ's `COMMUNICATE` check works the same way.
2. **Tool arguments must be canonical.** Assertions match arguments (subset or exact), so normalize formats: dates as ISO, amounts as numbers, IDs exactly as the user gave them after confirmation. Put format rules in the tool `description` and `parameters`.
3. **Default call duration is 60 s.** If the evaluators keep the default, long verbose agents fail by timeout. Brevity becomes a correctness property, not just style.
4. **Write the internal judge like Freya's.** For each synthetic scenario, write `successCriteria` in the same style the organizers would, then use the platform's own simulation to score. Our evaluator is then biased the same way as theirs.

---

# 58. Synthetic test generation: use IntellAgent's recipe

**IntellAgent** (ICML 2025, https://arxiv.org/abs/2501.11067) is a close match to §15 of the memo and has evidence behind it:

1. Extract atomic policies from the system prompt or docs. Each one becomes a node with a **complexity weight**.
2. Edges are **co-occurrence likelihoods** between policies.
3. Sample policy combinations with **random walks**, controlling complexity uniformly. In the paper the range was roughly 2–11.
4. For each sample, generate an event: user request, initial DB state and target policies.
5. Simulate the dialog. A **critique agent** labels which policies were tested and which were violated.

Results: IntellAgent's model rankings correlated with τ-bench at **Pearson 0.98 (airline)** and **0.92 (retail)**, using 1,000 generated events per domain versus τ-bench's 50–115 hand-written tasks.

**Use:** implement a lite version as `testsynth/`: policy graph, random walk, complexity buckets, and Freya personas (`prompt` + `successCriteria`). The per-policy violation labels feed the failure taxonomy directly (§18.2).

**Oracle validation (missing from the memo):** generated oracles share errors with the generator. Mitigations:

- When a mock DB exists, derive the expected state by **executing** reference actions (the τ method), not by having an LLM write it.
- Cross-check each `successCriteria` with a second model. Drop scenarios where the two disagree.
- Calibrate: synthetic-score ranking of candidates vs. practice-benchmark ranking (memo Q2). If Spearman correlation is below ~0.6, fix the generator before optimizing anything.

---

# 59. User-simulator robustness

**"Lost in Simulation"** (ICLR 2026 / ACL 2026, https://arxiv.org/abs/2601.17087) found:

- Agent success rates vary by **up to 9 percentage points depending on which LLM plays the user**.
- Simulated users **underestimate** performance on hard tasks and **overestimate** it on medium tasks.
- Simulated users behave less naturally than real ones and expose different failure modes.

τ²-bench also shows large drops from no-user to dual-control settings (https://arxiv.org/abs/2506.07982).

**Implications:**

- During selection, evaluate each candidate with **at least two persona LLMs** (`personaLlmOverride`) and several persona styles (terse, chatty, confused, impatient, non-native speaker). Prefer the candidate that wins across simulators, not the one that wins on one simulator.
- Don't over-tune to a single simulator's quirks, such as always giving the full ID at the start.
- Make sure the agent ends conversations cleanly (`endedByAgent`) and does not end them early.

---

# 60. Statistics: how many runs do you actually need?

From Anthropic's "A statistical approach to model evals" (https://www.anthropic.com/research/statistical-approach-to-model-evals, arXiv 2411.00640):

- Report the **standard error** next to every score.
- **Cluster** related items. Clustered standard errors can be **over 3× larger** than naive ones. Several runs of the same persona form one cluster.
- Use **paired differences**: both candidates run on the same scenarios, compare per scenario.
- Use **power analysis** to size the eval before running it.

Worked numbers for this hackathon:

```text
n = 30 scenarios, success ≈ 0.7
SE ≈ sqrt(0.7·0.3/30) ≈ 0.084
95% CI half-width ≈ ±0.16
```

With only a few dozen unpaired scenarios, the uncertainty around a candidate score is large enough that small prompt improvements can easily disappear in noise. The exact detectable difference depends on pairing, clustering, task difficulty and stochasticity, so candidate comparisons should be paired whenever possible.

**Protocol:**

1. Always compare candidates **paired** on the same scenario set and seeds.
2. Use `runsPerPersona ≥ 3` for finalists, and treat runs of one persona as a cluster.
3. Accept a mutation only if it wins a paired comparison (for example, more scenarios flipped from fail to pass than the reverse, by a margin decided up front). Otherwise keep the simpler parent (**simplicity prior**).
4. Use the fork API (§55.4) for cheap, low-variance checks at specific decision points.

---

# 61. The flip side of safety: over-caution costs points

The memo's lexicographic-safety stance (§17.1) can backfire. In τ-bench, unnecessary transfers to a human, unnecessary refusals and stalling on confirmations all fail tasks. Correct non-action only scores when *non-action* is the right answer.

Add to the scenario generator (§15):

- **Legitimate requests that look risky**: these must be completed, not refused or escalated.
- **"Already confirmed" cases**: the user confirmed clearly. The agent must not keep asking again.
- **Transfer-not-needed cases**: the request is within the agent's tools and policy.

Track two error rates separately: **false compliance** (violated policy) and **false refusal or escalation** (blocked a valid request). Optimize their sum, weighted only if the official scoring says so.

---

# 62. Industry precedent: what "builder" means to the judges

| Company | Product | What it does |
|---|---|---|
| Sierra | **Ghostwriter** (https://sierra.ai/product/ghostwriter) | Builds agents from natural-language descriptions, SOPs, transcripts and audio. **Auto-generates simulations and runs tests on every build.** Diagnoses failures and proposes fixes. |
| Decagon | **Agent Operating Procedures** (https://decagon.ai/blog/from-sops-to-agent-operating-procedures) | Natural-language procedures **compiled into validated workflows**, with code-level guardrails for identity verification and payments |
| Freya | AI Studio + Simulation Suite + its internal builder | "Our AI takes [customer documents, tools, requirements] and builds the agents, while we supervise." |

These products converge on the memo's thesis: **compile SOPs into structured procedures, then test them with generated simulations.** That is good evidence the architecture matches what the organizers built internally. Their internal builder is the bar to beat.

---

# 63. Voice-specific prompt guidance

From OpenAI's realtime/voice prompting guide (https://developers.openai.com/docs/guides/realtime-models-prompting). These are general practices, not Freya-specific:

- Use labeled prompt sections: Role, Personality, Tools, Entity Capture, Escalation. Separate hard rules from defaults.
- **Unclear audio:** ask the user to repeat. Don't guess, and don't call a tool on unclear input. On Freya this pairs with `confidenceReask`.
- **Alphanumerics:** read IDs, emails, phone numbers and amounts back **character by character or digit by digit** and get confirmation.
- **Tool preambles:** say a short spoken line ("Let me check that") before slow tool calls to cover latency.
- **Length rules per state:** 1–2 sentences for direct answers, step-by-step for troubleshooting. Prefer explicit rules over "be concise."
- **Vary phrasing** so repeated confirmations don't sound robotic.

Make these a `voice_policy` prompt block that is turned on when `mode=voice` or the input says the channel is phone (memo §3.5).

---

# 64. Language: Turkish is a real possibility

Freya is Istanbul-based, sells in Turkey, and ships a Turkish TTS model. Its STT and TTS configs default to `language: "multi"`.

- Evidence from another language: an Arabic tool-calling benchmark (https://arxiv.org/abs/2601.05101) found lower tool-calling accuracy in Arabic. Treat this only as evidence that multilingual tool use should be measured; **do not assume the same numerical penalty transfers to Turkish.**
- **Experiment:** system prompt in English with "reply in the user's language," versus a fully localized prompt. Run it on Turkish personas.
- Turkish voice specifics: number and currency reading ("1.250,50 TL"), dates and IBANs via `speechNormalization` and `textSubstitutions`, and Turkish personas in the scenario set.
- Make the test generator **language-aware**: scenarios should be in the same language as the task.

---

# 65. Practice data and optimizer evidence

- **Use τ-Bench Verified** from SABER (https://arxiv.org/abs/2512.07850) for airline and retail practice. It fixes benchmark artifacts that cap or distort scores. τ³ also shipped **75+ task corrections**. On the original tasks, part of the "failures" are bugs in the benchmark, which would mislead the optimizer.
- SABER's own gains are a useful prior for how much a mutation gate is worth: **+28% relative on airline and +11% on retail for Qwen3-Thinking; +9% and +7% for Claude.** Expect single-digit gains on strong models. Prioritize accordingly.
- GEPA (ICLR 2026 oral, https://arxiv.org/abs/2507.19457): **+6% on average, up to +20%, over GRPO with up to 35× fewer rollouts**, and more than 10% over MIPROv2. Sample efficiency is what matters under a simulation budget.
- MAS-PromptBench (https://arxiv.org/abs/2606.23664): prompt-optimization gains in multi-agent systems **depend heavily on configuration**. That is one more reason to keep the runtime agent single and simple, and to optimize one component at a time.

---

# 66. Corrections to the original memo

| § | Original statement | Correction |
|---|---|---|
| 1.2 | "does not publicly document the private hackathon agent builder API" | The agent, workflow, simulation, persona, unit-test, chat and version APIs are public (§55). Only the *hackathon-specific* input format and scoring are unknown. |
| 3.2 | Exact action sequence only matters "when the task explicitly requires" it | Concretely: about 9 of ~100 banking_knowledge tasks use `ACTION` reward (an assumed-unique trajectory). Freya's tool-call assertions may also use **exact argument match**. |
| 10–13 | Runtime components designed as our code | Must be compiled to Freya primitives (§56) |
| 17.1 | Lexicographic safety | Balance against false refusal and escalation (§61) |
| 22 | `PlatformAdapter` is a stub | Implement against the real OpenAPI now |
| 23 | Build a mock platform | Use Freya simulations as the main evaluator. Keep τ²-bench for oracle-grounded cross-checks. A custom mock is low priority. |
| 3.17 | FLARE (arXiv 2608.02919) | Could not be verified (no content returned). Keep it out of the plan. |

---

# 67. Revised plan (6–9 October)

**Today (6 Oct)**

- Create a Freya account and API key. Download `openapi.json`. Generate a typed client.
- Smoke test: create agent (from the default config) → 3 chat turns → get call details. Confirm whether tool calls show up in the transcript.
- Smoke test: create 2 personas with `successCriteria` → create simulation (mode `chat`, `runsPerPersona` 2) → read the results and the iteration detail.

**7 Oct**

- `FreyaAdapter`: compile AgentSpec into a config JSON (prompt sections, tools with descriptions and format rules, temperature, guardrails, STT/TTS knobs). Deploy as a version or branch.
- Check whether `pre_process_code` can reject a call. If it can, the MutationGate becomes deterministic.
- IntellAgent-lite generator → Freya personas.

**8 Oct**

- Failure analyzer on simulation transcripts. Fork-based counterfactual check (§55.4).
- Paired comparison with a simplicity prior (§60). A GEPA-lite loop over prompt sections.
- τ²-bench (Verified) cross-check on 1–2 domains to confirm the generator's rankings match oracle-grounded rankings.

**9 Oct**

- End-to-end dry run on a *new* invented task (for example insurance claims or loan payment deferral), with no manual edits.
- Budget and timeouts, fallback to the default-config baseline, one-command runner.
- Voice-mode and Turkish smoke tests.

**10 Oct, first hour:** fill in the memo's §44 table. Most of it is now pre-answered by the docs. The open items are below.

---

# 68. Updated open questions for the organizers

Answered by the public docs (no need to ask): agent config format, tool schema, the simulation API, chat vs voice mode, whether self-testing is possible on the platform, whether versions and branches exist.

Still to ask:

1. Is the hidden benchmark **Freya simulations** (personas + evaluation prompt + assertions)? Which `mode`? Which persona and analysis LLMs?
2. Which `llmConfig.provider` / `model` values are allowed for generated agents? Can `baseUrl`/`apiKey` point to our own endpoint? Can we use `routingLlmConfig` / `extractionLlmConfig`?
3. May the builder emit **workflows**, or only agents? Is there a create-workflow API for participants?
4. Does `pre_process_code` run arbitrary code, and can it block or return an error to the model?
5. How are tools provided in the hidden tasks: real HTTP endpoints, mocks, or `handler` types we must reference?
6. What `maxCallDurationSeconds` and `runsPerPersona` does the final evaluation use?
7. Are tasks in English, Turkish, or both?
8. Simulation and credit budget per builder run. Rate limits on the API key.
9. Is the knowledge base available, and can the builder upload or reshape documents?

---

# 69. Additional sources

**Freya platform**

- Docs index: https://docs.freyavoice.ai/llms.txt
- Create agent: https://docs.freyavoice.ai/api-reference/agents/create-agent.md
- Create simulation: https://docs.freyavoice.ai/api-reference/simulations/create-simulation.md
- Create persona: https://docs.freyavoice.ai/api-reference/personas/create-persona.md
- Create unit test: https://docs.freyavoice.ai/api-reference/unit-tests/create-unit-test.md
- Chat API: https://docs.freyavoice.ai/api-reference/chat/send-a-chat-message.md
- Workflows: https://docs.freyavoice.ai/guides/fundamentals/workflows.md
- Knowledge base: https://docs.freyavoice.ai/guides/features/knowledgebase.md
- AI Studio: https://docs.freyavoice.ai/getting-started/iterating-agents.md
- Best practices: https://docs.freyavoice.ai/guides/learning-freyavoice-ai/best-practices.md
- 2025 FDE take-home (community repo, stack hint): https://github.com/05sanjaykumar/Freya-Voice-YC25-Assessment

**Research**

- IntellAgent (ICML 2025): https://arxiv.org/abs/2501.11067
- Lost in Simulation: https://arxiv.org/abs/2601.17087
- Anthropic, statistical approach to evals: https://www.anthropic.com/research/statistical-approach-to-model-evals
- MetaAgent (ICML 2025): https://arxiv.org/abs/2507.22606
- MAS-PromptBench: https://arxiv.org/abs/2606.23664
- Arabic tool-calling benchmark: https://arxiv.org/abs/2601.05101
- SABER / τ-Bench Verified: https://arxiv.org/abs/2512.07850
- GEPA: https://arxiv.org/abs/2507.19457
- OpenAI realtime prompting guide: https://developers.openai.com/docs/guides/realtime-models-prompting

**Industry**

- Sierra Ghostwriter: https://sierra.ai/product/ghostwriter
- Decagon AOPs: https://decagon.ai/blog/from-sops-to-agent-operating-procedures

**Caveat:** endpoint details were read from the docs and OpenAPI-derived pages on 6 Oct 2026. Some fields (for example `pre_process_code` behaviour, and whether tool calls appear in chat responses) are not fully specified there and must be verified against the live API.
---

# 70. Status labels and final implementation priority

Use these labels throughout the addendum when converting research claims into implementation decisions:

- **CONFIRMED:** directly supported by current public Freya documentation.
- **STRONG HYPOTHESIS:** strongly suggested by the platform design, but not confirmed for the hackathon.
- **UNKNOWN:** requires organizer clarification.
- **VERIFY:** the field/capability exists in documentation, but its runtime behavior must be tested.

Examples:

```text
Freya has persona/simulation APIs        → CONFIRMED
Hidden benchmark uses that exact system → STRONG HYPOTHESIS
Final judge model and reward weights     → UNKNOWN
pre_process_code can reject tool calls   → VERIFY
```

The revised architecture priority is:

```text
P0
Freya API smoke test
→ real FreyaAdapter
→ TaskSpec / PolicySpec / ToolSpec
→ baseline agent compiler
→ persona + assertion generator
→ simulation runner
→ failure analyzer
→ paired candidate comparison
→ GEPA-lite targeted optimizer
→ deterministic mutation safeguards where supported

P1
policy graph / IntellAgent-lite
→ counterfactual fork testing
→ multilingual tests
→ voice policy
→ workflow/FSM compiler

P2
architecture search
→ multi-agent generation
→ advanced Pareto/crossover search
```

Core engineering rule:

> **What can be deterministic should not be delegated to an LLM.**

Core builder rule:

> **Keep runtime agents simple; put experimentation, reflection and search into the builder.**

Core evaluation rule:

> **Optimize for unseen-task reliability, not practice-set memorization.**
