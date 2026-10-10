# Rules mapping: fill in during the first hour on 10 October (memo §44, §50, §68)

No code changes until this table is filled in. Afterwards, change only adapters,
config and budget (memo §45).

## A. Platform contract

| # | Question | Answer | Status | Code affected |
|---|---|---|---|---|
| 1 | Builder input schema | | UNKNOWN | `ingest/bundle.py` (add a parser) |
| 2 | Agent output schema (config, workflow, code?) | | UNKNOWN | `adapters/freya/compile.py`, `pipeline._export` |
| 3 | Is a workflow graph allowed? Is there a create-workflow API? | | UNKNOWN | `synth` C4 → Freya workflow compiler |
| 4 | Allowed `llmConfig.provider` / `model` values; `baseUrl` override? | | UNKNOWN | `.env` `BUILDER_MODEL_AGENT`, `compile_agent(provider, model)` |
| 5 | Are external LLM APIs allowed (OpenRouter) for the builder? | | UNKNOWN | `llm/` |
| 6 | Internet / retrieval at runtime? Is the platform KB the only option? | | UNKNOWN | `knowledge/doc_shaper.py`, `RetrievalMode.PLATFORM` |
| 7 | Documents supplied? Size? | | UNKNOWN | `knowledge_compiler`, selector thresholds |
| 8 | Voice or text (`mode`)? | | UNKNOWN | `TaskSpec.channel`, `voice_policy` |
| 9 | Builder wall-clock limit | | UNKNOWN | `--max-wall-seconds` |
| 10 | Max simulations / rate limits / credit budget | | UNKNOWN | `--max-simulations`, `concurrency` |
| 11 | Official score components; is policy violation penalized separately? | | UNKNOWN | `select/robustness.selection_key` weights |
| 12 | Tie-breakers | | UNKNOWN | selection |
| 13 | Can the builder test its own agent (simulations/chat on the platform)? | | STRONG_HYPOTHESIS | `--adapter freya` vs `mock` |
| 14 | Can it create multiple candidates (agents/branches)? | | STRONG_HYPOTHESIS | `FreyaAdapter.deploy_agent` |
| 15 | Persistent memory across tasks in the final evaluation? | | UNKNOWN | `failure/lessons.py` (builder-side only by default) |
| 16 | Hidden final distribution: same domains as practice? | | UNKNOWN | split/holdout strategy |
| 17 | Does `pre_process_code` run, which language, can it reject a call? | | VERIFY | `compile_agent(preprocess_language=...)` |
| 18 | How are tools provided (real HTTP, mocks, `handler` types)? | | UNKNOWN | `ToolSpec.platform` passthrough |
| 19 | `maxCallDurationSeconds` and `runsPerPersona` in the final evaluation | | UNKNOWN | `FreyaAdapter(max_call_seconds)`, `trials_final` |
| 20 | Language: English, Turkish, both? | | UNKNOWN | `language_policy`, TR personas |
| 21 | Persona / analysis LLMs used by the evaluators | | UNKNOWN | `BUILDER_MODEL_USER_SIM(_ALT)`, `JUDGE` |
| 22 | Do tool calls appear in the chat/call-details transcript? | | VERIFY | `freya/adapter.result_to_trace` |
| 23 | Does exact tool trajectory matter, or only end state / assertions? | | UNKNOWN | `ToolCallAssertion.match` |
| 24 | Number of final tasks, trials per task, pass^k in ranking? | | UNKNOWN | `trials_final`, selection on pass^k |
| 25 | Hidden adversarial / policy-bypass cases? | | UNKNOWN | `testsynth/adversarial.py` share |
| 26 | Submission runtime (Docker?), dependency pinning | | UNKNOWN | `uv.lock`, manifest |

## B. Freya API field verification (fill in from `scripts/smoke_freya.py`)

| Field / endpoint | Result | Status |
|---|---|---|
| `GET default-config` path and shape | | VERIFY |
| `POST agents` body `{name, config}` | | VERIFY |
| `llmConfig.tools[].function.{name,description,parameters}` | | VERIFY |
| `pre_process_code` / `post_process_code` | | VERIFY |
| `guardrailsConfig.rules[]` (`type`, `pattern`, `action`) | | VERIFY |
| `sttConfig.confidenceReask`, `ttsConfig.speechNormalization` | | VERIFY |
| personas `{prompt, systemPrompt, successCriteria, evaluationPrompt, tags}` | | VERIFY |
| simulations `{agentId, agentVersionId, selectedPersonaIds, mode, runsPerPersona, maxCallDurationSeconds}` | | VERIFY |
| simulation results: per-persona status + transcript fields | | VERIFY |
| unit-test assertion shape (`tool_call`, subset/exact, negate) | | VERIFY |
| chat `send` / `fork` (`fromMessageId`, anchor retarget) | | VERIFY |

Once a field is confirmed, update `adapters/freya/compile.py:FIELD_STATUS` and the
`ENDPOINTS` table in `adapters/freya/client.py`.
