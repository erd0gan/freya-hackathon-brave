<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="static/brave-logo-light.png">
    <img src="static/brave-logo-dark.png" alt="BRAVE" width="420">
  </picture>
</p>

<h3 align="center">Brief to Reliable Agent Validation Engine</h3>

<p align="center">
  A bank's documents and its tool API in, one tested Freya phone agent out.
</p>

---

BRAVE is our entry for Freya's "Build the Builder" hackathon. It reads a bank's collections
material (a case folder of Markdown, Word, PDF and Excel documents, or one brief) and the
bank's tool API, and publishes a validated Freya workflow for a debt-collection phone agent.
The model is used where language is needed; everything the agent writes to the bank comes
from deterministic code.

```text
case folder (or brief.md) + mock_url (or openapi.json)
  → documents    read every file, give each a role (README.md says what each one is)
  → tools        OpenAPI file, else the mock's /openapi.json, else the service contract
                 (one model call, checked); tools named by role, URLs on the mock
  → spec         one model call extracts the policy; deterministic checks, one repair round
  → workflow     deterministic template: conversation, api_request, code and end_call nodes
  → validate     graph, gates (no account data before identity), turn caps, tool scope
  → Freya        dry run → save and publish → chat smoke test → export
```

The whole algorithm, stage by stage with diagrams: [`docs/brave-algorithm.html`](docs/brave-algorithm.html)
(one self-contained file; open it in a browser).

## Final submission: Freya Bank credit card statement debt (11 Oct 2026)

The final case was not a collections call. It was an inbound call about a credit card
statement: pick the card, read the debt in four currencies, change the statement week, e-mail
the statement. The deterministic template only builds the collections call, so the builder
now has a **general path** ([`builder/fb/agentic.py`](builder/fb/agentic.py)). It is taken
when the tool API plays none of the collections roles. Nothing about the final case is
written into the builder.

```text
case folder + mock_url
  → documents, tools         same as above (the mock serves /openapi.json)
  → general path             a coding agent (Claude Code, headless, scoped permissions:
                             files in its work folder + the build's Freya helper only) reads
                             every document, resolves where they disagree (latest decision
                             wins), writes rules.md and a generator for the workflow, then
                             dry-runs, saves, chat-tests and simulates it against the mock
                             and fixes what fails
  → builder                  dry run → save and publish → export → chat smoke → simulation
                             smoke, all inside the rules' 30 minutes
```

| What | Where |
|---|---|
| Mock of the bank's 7 services (Cloudflare Worker) | [`mock/`](mock/), live at https://freyabank-statement-mock.burak-erd0g4n.workers.dev |
| Mock self-test (all 617 test-customer answers byte-exact, 645 checks) | `node mock/test_mock.mjs` |
| Submission | [`submission.json`](submission.json) |
| Exported workflow | [`submission/final.workflow.json`](submission/final.workflow.json) (37 steps, 7 tools) |
| Builder trace | [`traces/final.log`](traces/final.log) |
| Coding agent's session log | [`traces/final.agent.jsonl`](traces/final.agent.jsonl) |
| Rules the agent derived, with sources | [`submission/final.rules.md`](submission/final.rules.md) |

Run command: `python3 builder/build.py {case_folder} {mock_url}`. The submitted run built
the case folder in 20 min 52 s (status ok, smoke passed). On Freya's own simulation with 12
callers (the six example scenarios plus six edge cases), it passed 10, 11, 11 and 11 of 12 in
four runs. The one repeated miss: a caller who asks for a person in the first sentence is
offered the main menu instead of being transferred at once.

## Quick start

```bash
cp .env.example .env    # FREYA_API_KEY, OPENROUTER_API_KEY
python3 builder/build.py FreyaBank-Case/ https://our-mock.example.workers.dev   # the final's form
python3 builder/build.py kit/briefs/brief-a.md kit/tools/openapi.json           # one brief, one file
```

The command is `build.py <case_folder | case.zip | brief.md> [<openapi.json> | <mock_url>]`.
A second argument starting with `http://` or `https://` is the mock's URL.

### Inputs

| Input | How it is read |
|---|---|
| Case folder or zip | Every file, recursively (`__MACOSX`, lock files and hidden files skipped). Markdown/text, Word (`.docx`: paragraphs, tables as pipe rows, header/footer, tracked deletions dropped, struck text marked), Excel (`.xlsx`: every sheet, dates by cell style), PDF (text layer with ToUnicode maps; a scan is reported), CSV, JSON, YAML, SVG/draw.io labels. UTF-8, UTF-16 and cp1254 text |
| Document roles | From README.md's description of each file (or of its folder), else the file name, else the content: policy (BRIEF.md), analysis, flow, announcement, contract, test customers, Q&A, example conversation |
| What the spec model gets | BRIEF first, then README, analysis, flow drawing, announcements (oldest first, with their dates), Q&A, each under `## <file> (<role>)`; the example conversations apart, marked as illustrations, not rules. Test customers are never given to it (they are the mock's data; the trace lists their columns) |
| Tool API | An OpenAPI file (JSON or YAML, OpenAPI 3 or Swagger 2) given as the second argument or found in the folder; else `GET <mock_url>/openapi.json` (one request, 10 s); else the service contract document, turned into OpenAPI by one model call and checked against its text (paths, methods, field names, allowed values, the seven tool roles), with one repair round |
| Tool names | An operation that plays one of the seven kit roles (`verify_identity`, `get_account`, `create_promise`, `add_note`, `schedule_callback`, `transfer_to_agent`, `close_call`) is used under that name whatever the contract calls it (camelCase, prefixes, Turkish paths or summaries); the trace shows `tool: <role> <- <contract's name>` |
| `mock_url` | Replaces scheme and host of every tool URL; the contract's path prefix (`/v1`) is kept unless the mock URL has a path of its own. Without a mock URL the contract's server must be absolute, else the build stops |

A file that cannot be read (an image, `.doc`, an encrypted PDF) is listed in the trace as
`case: UNREAD <file>: <reason>` and the build goes on.

`builder/build.py` uses only the standard library (Python 3.10+). Keys are read from the
environment, `.env`, `env.sh` or `kit/env.sh`; the environment wins. Optional:
`OPENROUTER_BASE_URL`, `BUILDER_MODEL_BUILDER` (overrides the spec model).

Each case gets one Freya slot, named after the folder (or the zip's or brief's name without
its extension) and updated in place.

### Outputs

| Path | Content |
|---|---|
| `traces/<slot>.log` | Start/end, every document read (role, size, reader) and every unread one, where each tool goes, every model call with tokens and cost, every Freya action and test |
| `submission/<slot>.spec.json` | The extracted spec |
| `submission/<slot>.workflow.json` | The platform export, or the workflow as sent if the export fails |
| `submission/<slot>.manifest.json` | Inputs and hashes, document roles, checks, Freya ids, model cost |
| `runs/<slot>/<time>/` | The previous run's trace, spec and export (moved, never overwritten) |

The trace's `END` line says how far the run got: `ok` only when the chat smoke test passed,
`ok_spec_problems` when checks were left open after the repair round (logged, the build still
publishes: an imperfect agent scores more than none), with `_smoke_failed`,
`_smoke_skipped` or `_export_failed` suffixes. A run that cannot produce a workflow ends with
`no usable tool API`, `spec invalid`, `generation failed`, `invalid workflow` or `dry run
refused`. The chat smoke test sends the customer's first reply in the call's language
(`Hello?`, Turkish `Alo, evet benim.`); an error, an empty answer or a debt word before the
identity check fails it.

The whole run keeps to a 30-minute budget (3 minutes reserved for save and export). Freya
calls retry 5xx, 429 and dropped connections with backoff; a 4xx is an answer.

## How it works

| Stage | Module | What it does |
|---|---|---|
| Documents | `builder/fb/case_input.py` | Case folder / zip readers (md, docx, xlsx, pdf, csv, json, yaml), document roles, policy / examples / customers / contract kept apart |
| Contract | `builder/fb/contract.py` | A service contract document → OpenAPI: one model call, checks against the contract's text, one repair round |
| Tools | `builder/fb/openapi_tools.py` | Tool definitions from the OpenAPI file (resolved `allOf` / `$ref`, Swagger 2), kit role names, mock URL, amounts in minor units, date-time fields split for the workflow |
| Spec | `builder/fb/spec_extract.py` | Model extraction, then `check()`, `structure()`, `grounding()` (are the values in the brief?), one repair call, deterministic overrides |
| Generator input | `to_generator()` | Engine config: bands, attempt plans, reasons, queues, lines; neutral lines where the brief is silent |
| Workflow | `builder/fb/generate.py` | Deterministic template, no model calls (design: `docs/GENERATOR.md`) |
| Code nodes | `builder/fb/nodecode/` | `negotiation_lib` (state machine), `speech_lib` (spoken dates, times, amounts, corrections), `collections_lib` (calendar, business days, bands, deadlines) |
| Validator | `builder/fb/validate.py` | Integrity, graph shape, gated tools unreachable before identity, turn caps, scope |
| Platform | `kit/freya.py` | Dry run, save, chat smoke test, export |

Design rules:

- **Deterministic skeleton around the model.** Two model calls at build time; at call time the
  model talks and classifies, code decides. Every value sent to the bank comes from a code node
  or a baked parameter.
- **Nothing hardcoded per brief.** Bands, offers, reasons and wording come from the extracted
  spec; a line the brief does not give falls back to a neutral sentence, never to another
  brief's policy.
- **Speech is parsed in code.** Spoken dates, times, amounts and dates of birth (with
  self-corrections and refused days) are read against the call's own clock.
- **Fits the platform.** `code_exec` takes 131072 bytes of code plus call data; each node
  carries only the library functions it uses, packed only above 30 KB.

## Tests

```bash
uv sync
uv run pytest tests/hack        # the builder: engine, speech, spec checks, generator, validator
make check                      # ruff + mypy + the full test suite
```

`builder/fb/harness/` runs the code-node bodies against a local mock bank with no model
(`engine_sim.py`, `bank.py`) and scores with the kit's scorer.

## Results

Recorded on the public practice set (13 customers, no partial credit: outcome, policy and
honesty must all pass):

- `builder/build.py` with the model-extracted spec: **Brief A 10/10** and **Brief B 3/3**,
  every customer 3/3 runs ([`docs/baseline-2026-10-10`](docs/baseline-2026-10-10/README.md)).
- Talkative customers (about half the messages arrive cut off): Brief A from 7/10 to 10/10,
  Brief B from 0/3 to 3/3 on three runs ([`docs/research-talkative`](docs/research-talkative/README.md)).
- 11 Oct, commit `f3d74fa`, while the platform's agent model was slow: Brief B 3/3; Brief A
  5/10, then 3 of the 5 failures passed when run again. Every call that reached its end passed.
  Each failure was the simulated customer hanging up after about 60 s of silence. The model
  steps were slow, not wrong: reading "Yes, this is Owen Park" took 4–6 minutes in some calls
  and 2–4 s in others, on the same node with the same input. In the calls that ran slow but
  did not stall, each step took 10–20 s instead of 1–2 s. Removing model steps from the
  identity turn would make it faster, but it would lose answers that come in pieces, so the
  flow is unchanged.
- 11 Oct, every customer style on `f3d74fa`: B 3/3 talkative, pieces and in_words, 2/3
  corrects and asks_back; A 10/10 corrects and asks_back, 9/10 pieces and in_words, 8/10
  talkative. Two of the failures were ours. A refusal that also asked something ("No, I don't
  want to be transferred. Can I get a discount?") was read as a question only, and a full date
  said in words was asked for its year. After the fix to those two descriptions: A 10/10
  standard and talkative, B 3/3 standard and asks_back.

## Developer tools

| Tool | Use |
|---|---|
| `builder/tools/extract_spec.py <brief> <openapi> [--expect A\|B]` | Compare an extracted spec with a hand-written one |
| `builder/tools/deploy_spec.py A\|B [--slot] [--dry]` | Generate and deploy from a hand-written spec |
| `builder/tools/chat_case.py <slot> <case> "msg"...` | Talk to a saved workflow turn by turn |
| `builder/tools/call_dump.py <results.json>...` | Step-by-step timelines of simulated calls |
| `builder/tools/bank_parity.py` | Compare the local mock bank with the practice bank |
| `kit/tools/run_cases.py <agent_id> --brief A` | Run the practice customers (organizer kit) |

## Layout

| Path | Content |
|---|---|
| `builder/` | BRAVE: `build.py`, `fb/` (pipeline, code-node libraries, harness), `tools/` |
| `tests/hack/` | Builder tests and spec fixtures |
| `kit/` | Organizer kit: briefs, tool API, practice customers, Freya client, scorer |
| `docs/` | Generator design, baselines, research notes |
| `static/` | Logos |
| `src/builder/`, `examples/`, `scripts/` | Earlier generic pipeline, not used by `builder/build.py` |
