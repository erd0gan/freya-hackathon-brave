# Correctness review — 7 October 2026

The review's concrete code findings were reproduced and addressed on
`ccr-71703b65-4vc8wn`. Architecture/readiness scores are opinions, not measured results.

| Finding | Change |
|---|---|
| Generic yes grants write consent | Pending consent records exact tool and canonical arguments; approval is single use. Corrections invalidate approval. |
| Historical substring grounding | Token boundaries and source-bearing structured tool facts replace concatenated historical evidence. Latest substantive user input invalidates old identifier facts. |
| Prerequisite for another customer | Successful calls are matched against shared entity IDs or entity relationships returned by tools; failed verification cannot satisfy a prerequisite. Missing relationships fail closed. |
| Mock/Freya tool instructions differ | Both consume `runtime/tool_prompt.py`, including overrides, risk, confirmation, duplicate prevention and formats. |
| Freya silently ignores variants/fork claims | Persona variants are separate; traces preserve trial identity. Fork is disabled, concurrency is 1, malformed result batches fail and unknown/error/timeout endings cannot pass. |
| Wrapped metadata/Turkish names | Root platform metadata survives normalization; Turkish letters normalize before risk classification. |
| LLM inference becomes mandatory policy | Inferred prerequisites are advisory FEEDS edges, with an unverified note. Explicit REQUIRES_BEFORE edges remain enforceable. No automatic promotion without evidence. |
| Search consumes final simulation allowance | Reserve complete frozen validation and champion/fallback holdout counts before search; reduce finalist count if necessary. Incomplete validation is excluded. |
| Holdout never runs | Default 10% holdout runs once after final audit for the exported champion and fallback. Scores do not alter ranking and holdout literals do not enter audit. |
| Executable but wrong synthetic oracle | Reject unknown/out-of-sample policy IDs and inconsistent reference/expected/forbidden calls. A separate JUDGE-role call audits business policy, trajectory and arguments before accepting the oracle. |
| Free-text errors pass | Derived assertions use canonical equality; unexpected free-text edits fail the unchanged-state check. |
| LLM counters disconnected | LLMClient checks and charges the shared build budget, including concurrent calls; local trace accounting avoids double charges. Cache hits incur no actual-call/token/spend charge. |
| Policy precedence/source granularity | Prohibitions precede regulatory/authentication matches; top-level bullet and numbered rules get separate sections. |

## Limits and remaining verification

- No `FREYA_API_KEY` is configured, so live smoke verification was unavailable. Freya's
  endpoints and fields remain VERIFY. Persona instruction variants are not confirmed
  simulator-model diversity; deterministic conversation guards remain local.
- Final simulation counts are reserved. Wall-clock, LLM token and spend caps remain global
  and can still interrupt final evaluation; partial results are logged and excluded from
  selection. Token/cost limits stop subsequent calls after reported usage reaches the cap;
  provider usage is only known after a response.
- A JUDGE-role audit is independent of generation but is a model judgment, not a proof.
  The configured judge model may be changed separately from the builder model.
- Grounding is deliberately conservative: a new substantive user turn may require reading
  fresh tool evidence. Entity relationships must be supplied by tool outputs or matching IDs.
  Dates, booleans and free-text mutation arguments retain the existing grounding exemptions.
- Canonical equality can reject legitimate free-text paraphrases. A domain-specific semantic
  comparator should be added only with an explicit field contract, rather than silently
  ignoring those fields.

Regression coverage is in `tests/unit/test_correctness.py`, plus the updated runtime,
oracle, evaluator and end-to-end fixtures. No merge or live deployment was performed.

## Local validation

- `PYTHONPATH=src py -3.12 -m pytest`: **94 passed** using installed Python packages (7 Oct;
  the suite has grown since, see `make check`).
- Ruff **0.16.4**: lint and formatting checks passed; `git diff --check` passed.
- Locked-environment bootstrap and Mypy installation did not finish downloading dependencies;
  those processes were stopped. No successful Mypy or lock-file environment check is claimed.
- Freya authentication was checked without exposing its value: no key is configured.
