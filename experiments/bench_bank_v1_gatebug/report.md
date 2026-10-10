# B0 / A / C benchmark — mock-retail-bank

> **INVALID FOR A AND C3 — kept as evidence of a bug.** The local MutationGate followed
> entity links one hop only and forgot earlier listings, so verified customers' refunds were
> blocked (`prerequisite step(s) not completed: verify_identity`) in 16/38 A runs and 14/38
> C3 runs; B0 has no gate. FC/FR here are already relabelled (handoff = false refusal).
> Fixed in the commit that adds this note; see `experiments/bench_bank.md` for the rerun.

> **LOCAL / MOCK RUNTIME.** Scores come from this repo's simulator and mock backend,
> with the builder's local safety gate. They are a pre-screen, **not** Freya-native
> scores: the shipped agent runs on Freya's runtime (PLAN §9, GAP_ANALYSIS A1).

- Run dir: `out/bank6` · bundle `examples/bundles/retail_bank.json`
- Commit: `05d938e66761+dirty`
- C = **C3** (champion)
- Trials per scenario: 2 (trial 0 = `openai/gpt-6-luna-pro`, trial 1 = `google/gemini-2.5-flash`)
- Agent `anthropic/claude-haiku-5.5` · judge `openai/gpt-6-luna-pro` · builder `anthropic/claude-haiku-5.5`
- Scenario hashes: fresh `ab0de4f20745b94a` (seed 4242), frozen `714f11cf5b507fc2`
- Benchmark cost: $0.86 (rescored from stored traces, +$0.00)

Verdicts: `>` needs Δ ≥ 0.05 and one-sided sign-test p ≤ 0.1 over per-scenario means;
SE is over scenarios (clustered). Train scenarios are excluded everywhere.

## FRESH (primary)

| candidate | success | clustered SE | pass^k | FC | FR | n scen / runs | Δ vs B0 |
|---|---:|---:|---|---:|---:|---|---:|
| B0 | 0.20 | 0.11 | 1:0.20, 2:0.10 | 0.60 | 0.10 | 10 / 20 | — |
| A | 0.35 | 0.15 | 1:0.35, 2:0.30 | 0.10 | 0.25 | 10 / 20 | +0.15 |
| C3 | 0.20 | 0.11 | 1:0.20, 2:0.10 | 0.20 | 0.30 | 10 / 20 | +0.00 |

| comparison | Δ | SE | 95% CI | wins / losses / ties | sign p (1-sided) | verdict |
|---|---:|---:|---|---|---:|:-:|
| A vs B0 | +0.15 | 0.13 | [-0.11, +0.41] | 3 / 1 / 6 | 0.312 | ≈ |
| C3 vs A | -0.15 | 0.08 | [-0.30, -0.00] | 0 / 3 / 7 | 1.000 | ≈ |
| C3 vs B0 | +0.00 | 0.07 | [-0.15, +0.15] | 1 / 1 / 8 | 0.750 | ≈ |

**Reading:** 5. All ≈: candidate/search complexity is not paying for itself at this n.

## FROZEN val+holdout (C selected here)

| candidate | success | clustered SE | pass^k | FC | FR | n scen / runs | Δ vs B0 |
|---|---:|---:|---|---:|---:|---|---:|
| B0 | 0.39 | 0.14 | 1:0.39, 2:0.22 | 0.39 | 0.00 | 9 / 18 | — |
| A | 0.39 | 0.11 | 1:0.39, 2:0.11 | 0.17 | 0.17 | 9 / 18 | +0.00 |
| C3 | 0.50 | 0.14 | 1:0.50, 2:0.33 | 0.11 | 0.17 | 9 / 18 | +0.11 |

| comparison | Δ | SE | 95% CI | wins / losses / ties | sign p (1-sided) | verdict |
|---|---:|---:|---|---|---:|:-:|
| A vs B0 | +0.00 | 0.14 | [-0.28, +0.28] | 1 / 2 / 6 | 0.875 | ≈ |
| C3 vs A | +0.11 | 0.16 | [-0.21, +0.43] | 4 / 1 / 4 | 0.188 | ≈ |
| C3 vs B0 | +0.11 | 0.07 | [-0.03, +0.26] | 2 / 0 / 7 | 0.250 | ≈ |

**Reading:** 5. All ≈: candidate/search complexity is not paying for itself at this n.

## Fresh scenario generation
- rejected mock-retail-bank-user_correction-005: policy oracle rejected: The expected outcome does not require the agent to offer standard (free) or express shipping (with its $12 fee) before ordering the replacement, as CARD-02b requires. Naming standard shipping and its no-fee outcome does not ensure that required offer and fee disclosure occurred.
- rejected mock-retail-bank-tool_failure-006: policy oracle rejected: The expected outcome says the replacement attempt returns a 503 error but also requires telling the customer the replacement was ordered. The reference trajectory contains only one issue_replacement call and no successful retry, so that success claim is unsupported. It also omits list_transactions, the tool needed to check whether T-2 was already refunded before reporting that status.
