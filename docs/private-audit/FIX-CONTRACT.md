# Fix contract: shared interfaces and file ownership (11 Oct 2026)

Five agents fix the findings of `00-SUMMARY.md` and `01..08-*.md` in parallel, in the same
working tree. This file pins who owns which file and the interfaces between the parts, so the
parts fit without anyone editing another agent's file.

## Ground rules (all agents)

- **Never write anything from the final case into the builder.** The final case is unknown
  (a Turkish credit-card statement-debt line). General Turkish *language* support (month names,
  number words, suffixes, yes/no words) is fine; bank policy values, rule numbers, customer
  names, amounts, dates or field names "guessed" for the final are not.
- `kit/` is the organizer's: never modify it. The kit and the hackathon page
  (`docs/private-audit/hackathon-page-2026-10-11.txt`) are ground truth.
- English behaviour must stay identical: every existing test in `tests/hack` must still pass
  (baseline 966 passed). The 13 public scenarios and the 1896-cell selftest must still pass.
- Never trade correctness for latency.
- Stdlib only in `builder/` (the build runs with plain `python3`). Code-node libraries
  (`builder/fb/nodecode/*_lib.py`) run inside Freya's `code_exec` sandbox: stdlib `json`, `re`,
  `datetime` only, never raise, never two braces in a row in the source, stay small
  (`embed.CODE_BUDGET` 30 KB per node, hard cap 36 KB).
- Do not run `git commit`, `git stash`, `git checkout` or anything that rewrites the tree. Another
  session may have uncommitted edits in `generate.py` (`VERIFY_RETRIES`): keep them.
- No network calls (no Freya, no OpenRouter, no mock URL). Tests use fakes.
- Write code like the surrounding code: comment density, naming, idiom; comments say why.
- Only edit files you own (table below). If you need a change in a file you do not own, write
  it under `REQUESTS` in your final report with the exact change; the integrator applies it.
- Run tests with: `uv run pytest tests/hack --confcutdir=tests/hack -q -p no:cacheprovider`
  (the root `tests/conftest.py` is broken). Other agents edit at the same time: a failure in a
  file you do not own may be their work in progress; rerun later, report it if it stays.

## Ownership

| Agent | Files it may edit | Findings |
|---|---|---|
| INPUT | `builder/build.py`, `builder/fb/case_input.py` (new), `builder/fb/contract.py` (new), `builder/fb/openapi_tools.py`, `builder/fb/trace.py`, `README.md`, new tests `tests/hack/test_case_input.py`, `tests/hack/test_contract.py`, existing `tests/hack/test_openapi_tools.py`, `tests/hack/test_build_retry.py`, `tests/hack/test_fuzz_openapi.py`, fixtures under `tests/hack/fixtures/case_*` | 01 (IC-*), 08 (TA-*), summary A, B, E, I (detection), K; HA-3, HA-12 (request side), LP-7, TR-11 (smoke) |
| SPEECH | `builder/fb/nodecode/speech_lib.py`, `builder/fb/nodecode/collections_lib.py`, `langpack.FORMAT`, new `tests/hack/test_speech_tr.py`, `tests/hack/test_calendar_tr.py`, existing `test_speech.py`, `test_parsers2.py`, `test_calendar.py`, `test_call_days.py` | 02 TR-3, TR-4, TR-5, TR-9 (lib side), 05 (CAL-*), summary L, HA-9 (calendar side), HA-15 |
| ENGINE | `builder/fb/nodecode/negotiation_lib.py`, `builder/fb/nodecode/bodies.py`, `langpack.ENGINE_LINES`, new `tests/hack/test_engine_tr.py`, existing `test_engine.py`, `test_engine2.py`, `test_invariants_*.py`, `test_coverage_matrix.py` | 06 (SM-*), 02 TR-2, TR-7 (engine side), HA-4, HA-5, HA-6 (engine side), HA-8, HA-10, summary I (apply) |
| GENERATE | `builder/fb/generate.py`, `builder/fb/validate.py`, `builder/fb/nodecode/embed.py`, `langpack.GEN_LINES`, `langpack.LANGUAGE_NAMES`, new `tests/hack/test_generate_lang.py`, existing `test_generate.py`, `test_validate.py`, `test_hidden2.py`, `test_workflow_scan.py`, `test_glue.py` | 02 TR-1 (prompt), TR-2 (generate strings), TR-7/TR-8 (generate gates), 03 HA-1/summary F, HA-2, HA-11, HA-14, 07 (LP-*) in generate/validate, summary B (validate side) |
| SPEC | `builder/fb/spec_extract.py`, `builder/fb/llm.py`, `builder/fb/proof.py`, new `tests/hack/test_spec_lang.py`, existing `test_spec_checks.py`, `test_spec_mapping.py`, `test_invariants_proof.py`, `test_llm_providers.py`, `tests/hack/spec_fixtures.py` | 04 (SX-*), summary G, H, J; HA-13, HA-6 (spec side), TR-10, TR-1 (extraction prompt) |

`builder/fb/langpack.py` is shared: each agent edits only its own section (re-read the file right
before each edit; never rewrite the whole file).

## Interfaces

### 1. Spec keys (SPEC produces them in `extract()` / `check()` / `structure()`)

- `language`: short code (`"en"`, `"tr"`, ...). Model field, cross-checked by a deterministic
  detector on the documents (Turkish letters and stopwords); a confident detector wins.
  Normalise with `langpack.norm_language`.
- `currency`: `{"code": "TRY", "symbol": "TL", "word": "lira", "minor_word": "kuruş"}`, any key
  may be missing; only from the documents, never guessed.
- `identity.max_wrong_answers`: int or null (null: the brief gives no limit).
- `calendar.week_start`: 0..6 (Monday = 0) or null.
- `floor_rule` (optional): `{"field": "<response field>"}` (today's behaviour) or
  `{"percent_of": "<response field>", "percent": 20}`; ENGINE computes the floor.
- `dated_changes`: list of `{"effective_from": "YYYY-MM-DD", "path": "<spec path>", "value": ...,
  "source": "<quote>"}` for values an announcement changes from a date. The main field keeps the
  value valid before that date.

### 2. `to_generator(spec)` output (SPEC)

- `gen["language"]` = code; `gen["cfg"]["lang"]` = code.
- `gen["cfg"]["fmt"]` = `langpack.fmt(lang)` with `currency` from the spec merged in.
- `gen["cfg"]["week_start"]` = int or absent (absent: derived from `workdays` by collections_lib).
- `gen["cfg"]["dated"]` = `[{"from": "YYYY-MM-DD", "set": {"<dotted cfg path>": value}}]`,
  paths in cfg terms (e.g. `"neg.lines.recorded"`, `"bands.0.attempts"`, `"callback.end"`).
- `gen["cfg"]["neg"]["lines"]`: every key of `langpack.engine_lines(lang)` (plus the existing
  `ENGINE_LINES` keys), set to the brief's line or else the language pack's neutral line.
- `gen["cfg"]["floor_rule"]` when the spec has one.
- `gen["gen_lines"]` = `langpack.gen_lines(lang)`; generate.py uses `L.get(k) or gen_lines[k]`
  instead of English literals.
- `gen["identity"]["max_wrong_answers"]` as in the spec.

### 3. Code-node libraries (SPEECH provides, everyone calls)

In `collections_lib.py`:
- `LOCALE = {"lang": "en", "fmt": None}` (module-level dict, mutated in place) and
  `set_locale(cfg)` that copies `cfg.get("lang")` / `cfg.get("fmt")` into it.
- `prepare_cfg(cfg, now)`: applies `cfg["dated"]` entries whose `from` <= the call's date
  (from `now`) to a copy of cfg, calls `set_locale`, returns the copy. Never raises.
- `say_date(d, fmt=None)`, `money(x, fmt=None)`: English output unchanged ("Friday 23 October
  2026", "1250.00"); other languages from `fmt` or `LOCALE["fmt"]`.
In `speech_lib.py`: `parse_spoken_date`, `parse_amount`, `parse_spoken_time`,
`digits_from_speech` (and the DOB helpers in collections_lib) take an optional `lang=None`
keyword; `None` means `LOCALE["lang"]`. English results unchanged.

SPEECH adds these signatures first (no-op defaults) so the others can call them early.

Every code-node body (bodies.py: ENGINE; inline code in generate.py: GENERATE) starts with
`CFG = prepare_cfg(CFG, <now>)` (or `set_locale(CFG)` where no `now` is at hand).

### 4. Tool definitions (INPUT)

- `parse_openapi(doc, base_url=None)`: a tool recognised as one of the seven kit roles is keyed
  and named by the kit name (`verify_identity`, `get_account`, `create_promise`, `add_note`,
  `schedule_callback`, `transfer_to_agent`, `close_call`) whatever the operation is called;
  `ToolDef.api_name` keeps the original. Unrecognised tools keep their own name.
- `base_url` (the `mock_url`): replaces scheme + host of every tool URL (keeps the servers[0]
  path prefix unless `base_url` has its own path). URLs are always absolute.
- `ToolDef.response_scale: dict[str, float]`: response leaf path -> factor to major units
  (0.01 for a field in cents/kuruş). Empty when nothing says minor units.

### 5. Extraction variables (GENERATE adds them, ENGINE reads them)

- `said_amount`: the amount the customer named, as digits (`"1250.50"`), empty if none.
- `said_date_iso`: the date the customer named as `YYYY-MM-DD` (the prompt carries a table of
  the next weeks' dates with weekday names), empty if none.
ENGINE uses them only when its own parser reads nothing, and validates them with the same
date/amount checks. A non-empty amount text that nothing can read is `bad` (ask "which
amount"), never silently the full amount.

### 6. Identity lockout (GENERATE, with SPEC's `max_wrong_answers`)

The workflow keeps its own wrong-answer counter (a code-node variable), incremented on a
verify result that is a wrong answer (not on a hint such as `day_month_swapped`, not on an
error). Lockout when the counter reaches `max_wrong_answers` (missing: 3, with a warning) or when
the API says locked / zero left. An API count field is read as "left" only when its name or
description says left/remaining; otherwise as "used".

### 7. Build command (INPUT)

`python3 builder/build.py <case_folder | case.zip | brief.md> [<openapi.json> | <mock_url>]`
- Folder or zip: documents read (md, txt, docx, xlsx, pdf, csv, json, yaml); README.md's
  description of the files gives each document its role; policy text, announcements, Q&A,
  test customers and example conversations are kept apart.
- Tool API: an OpenAPI file in the folder; else `GET <mock_url>/openapi.json` (our own mock);
  else the service contract document turned into OpenAPI by one model call, checked
  deterministically.
- The slot is named after the folder (or the brief's stem).
- Smoke test in the call's language; an empty agent reply fails it.
