# Agent Builder — Freya "Build the Builder"

A builder that turns an unseen task (description, policy, tools, documents) into a tested,
selected customer-service agent. It treats agent creation as **compilation + test
generation + optimization**, not prompt writing.

```text
bundle → typed IR (TaskSpec/PolicySpec/ToolGraph/KnowledgeSpec)
       → structurally different candidates (B0 floor, A fallback, C1–C6)
       → synthetic scenarios with executed, independently audited oracles
       → train / frozen validation / untouched holdout
       → simulate → deterministic evaluation (+ judge for semantics)
       → failure diagnosis → GEPA-lite block mutation → paired acceptance
       → robustness selection → final audit → champion + fallback holdout → export (+ manifest)
```

Design rationale: `freya_build_the_builder_research.md` (the memo); the phased plan and every decision: [`docs/PLAN.md`](docs/PLAN.md).

## Quick start

```bash
uv sync                      # Python ≥3.12
make check                   # ruff + mypy (core) + pytest — no network or keys needed
cp .env.example .env         # add OPENROUTER_API_KEY (and FREYA_API_KEY when available)

# Compile-only (no key needed): audited fallback agent
uv run python -m builder run -i examples/bundles/insurance_claims -o out/claims --adapter none

# Full search on the hand-written mock bank (needs OPENROUTER_API_KEY)
uv run python -m builder run -i examples/bundles/retail_bank.json -o out/bank \
    --scenarios 16 --candidates 6 --generations 2 --trials 2 --max-simulations 200

# Any unseen bundle: the mock adapter simulates its tools with an LLM backend
uv run python -m builder run -i examples/bundles/loan_deferral.json -o out/loan

# Freya platform (VERIFY: run scripts/smoke_freya.py first)
uv run python -m builder run -i <bundle> -o out/freya --adapter freya
```

Outputs in `--output`: `agent_spec.json`, `system_prompt.md` (the prompt as shipped to the
platform), `domain_spec.json`, `scenarios.jsonl`, `runs/{traces,results}.jsonl`, `report.md`,
`manifest.json` (commit, lock hash, models, prompt/tool/document hashes, scores, spend), and
the Freya config:

- `--adapter freya`: `freya_agent_config.json`, the platform default config (`GET
  /agents/defaults`) plus the builder's fields, validated against the vendored OpenAPI spec.
  This is the deployable submission.
- other adapters: `freya_config_overlay.json`, only the builder's fields. It is not deployable
  on its own; merge it onto the platform default (`builder.adapters.freya.compile.merge_overlay`).

## Input bundles

`builder.ingest.bundle.load_bundle` accepts a JSON/YAML file shaped like `InputBundle`
(aliases such as `task`, `policy`, `functions`, `docs` are mapped) or a directory with
`task.md`, `policy.md`, `tools.json|yaml`, optional `docs/` and `meta.json`. When the
official input format is published, add one parser there.

## Budget and safety rails

- Hard LLM spend cap across runs: `MAX_COST_USD` (persistent ledger in `.builder/`).
- `OptimizationBudget`: wall clock, simulations, agents, tokens, cost — every loop checks it.
- Complete validation and holdout simulation counts are reserved before optimization. Incomplete
  validation batches cannot enter selection. Holdout defaults to 10% (`--holdout-fraction`)
  and evaluates only the audited champion and fallback, without changing selection.
- Non-cached builder/optimizer/analyzer calls and all local LLM token/cost usage are charged
  directly by `LLMClient`; simulation traces do not charge that usage a second time.
- MutationGate consent is tied to an exact tool/argument pair and consumed once. Local agents
  can use `request_action_confirmation`; canonical action summaries also support trace replay.
  Tool evidence carries its source, and prerequisite checks match entity identifiers.
- Content-addressed LLM cache: re-runs are free and reproducible.
- Candidate **A** (compiled prompt + deterministic MutationGate) is always produced and is
  exported if search fails, is out of budget, or the champion fails the final audit.

## Layout

| Path | Purpose (memo §) |
|---|---|
| `schema/` | typed IR (§5) |
| `llm/` | OpenRouter client, cache, cost cap, roles (§32–33) |
| `ingest/` | tool/policy/task/knowledge compilers (§6–7, §14.5) |
| `synth/` | prompt blocks, architecture selector, candidates, few-shot (§8, §11–12, §21, §27, §31, §57, §61, §63–64) |
| `runtime/` | MutationGate, reference agent, context cleaner (§10–13) |
| `knowledge/` | chunking, BM25, retriever, doc shaper (§14, §55.7) |
| `testsynth/` | policy-graph walks, categories, generator, oracles, splits, adversarial (§15, §29–30, §58) |
| `eval/` | state / tool-call / policy evaluators, judge, stats, simulator (§16–17, §57, §60) |
| `failure/` | localisation, analyzer, regression, lessons (§18, §20, §28) |
| `optimize/` | halving, GEPA-lite, OPRO, acceptance, Pareto, crossover, lint, fork probe (§18–19, §34, §55.4) |
| `select/` | robustness selection, final audit (§17, §43) |
| `adapters/` | mock (+ LLM tool backend), Freya (VERIFY), τ² (VERIFY, unrun) (§22–24, §55–56) |

## Status labels

Platform facts carry `CONFIRMED / STRONG_HYPOTHESIS / UNKNOWN / VERIFY` (memo §70). Every
Freya field and endpoint is **VERIFY** until `scripts/smoke_freya.py` runs against a real
account; see `docs/RULES_MAPPING.md`.

Freya currently advertises no fork support and uses concurrency 1. Trial variants change
persona instructions, not a platform simulator model (that API is still unverified).
Only simulation counts are reserved: global wall-clock, token and spend caps still stop final
evaluation. The local deterministic gate is not installed on Freya; shared tool descriptions
carry confirmation rules there. Free-text state assertions use canonical equality, which is
conservative for valid paraphrases. See [the correctness review](docs/CORRECTNESS_REVIEW.md).

## Optional τ²-bench

`src/builder/adapters/tau.py` is written but has not been run. Install tau2-bench manually
(`uv pip install git+https://github.com/sierra-research/tau2-bench`), then verify the API
names listed at the top of that file.
