"""Build a Freya phone agent from a bank's documents and its tool API.

    python3 builder/build.py <case_folder | case.zip | brief.md> [<openapi.json> | <mock_url>]

    python3 builder/build.py FreyaBank-Case/ https://my-mock.workers.dev   (the final's form)
    python3 builder/build.py kit/briefs/brief-a.md kit/tools/openapi.json  (one brief, one file)

A folder (or a zip of one) is read by fb.case_input: every Markdown, Word, Excel, PDF, CSV,
JSON and YAML document, each given a role from README.md's description of the files (else its
name or content). The spec extractor gets the policy documents (brief, analysis, flow,
announcements with their dates, Q&A) and, apart and marked as illustrations, the example
conversations; test customers stay out of it (trace only).

The tool API, first found wins: an OpenAPI file given as the second argument or found in the
folder; else GET <mock_url>/openapi.json (our own mock; one short request); else the service
contract document turned into OpenAPI by one model call and checked against its text
(fb.contract). With a mock_url every tool URL goes to the mock (its scheme and host, the
contract's path prefix); without one the contract's server must be an absolute URL.

Stdlib only. Steps: read -> spec (model) -> workflow (deterministic template) -> validate ->
save on Freya (one slot per case, updated in place) -> smoke test over chat -> export. The
slot is the folder's name (or the zip's / brief's without extension). Writes
traces/<slot>.log and submission/<slot>.workflow.json. Needs FREYA_API_KEY and
OPENROUTER_API_KEY in the environment (or in a .env / kit/env.sh file next to the repo).

Once a workflow is saved, a submission file is always written: the platform export, or the
workflow as sent if the export fails. The trace's END line says how far the run got
(status=ok only when the chat smoke test passed; smoke=passed|failed|skipped). Spec problems
left after the repair round do not stop the build (status ok_spec_problems, the problems in
the trace) unless the spec cannot make a workflow at all.

Beside the main extraction a second one runs in the background (BUILDER_OBSERVER=0 turns it
off): where the two readings differ is logged as "uncertainty:" lines, and only the words not
to say before the identity check are merged (their union). After validation the brief's rules
are mapped to the spec and workflow (fb.proof, "proof:" lines). Every run ends by writing
submission/<brief>.manifest.json (inputs, hashes, checks, Freya ids, model cost), named in the
END line. None of these can stop or change the build.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import inspect
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

BUDGET_S = 30 * 60
EXPORT_RESERVE_S = 3 * 60
SMOKE_RESERVE_S = 60
# The smoke test's whole time, retries included.
SMOKE_MAX_S = 150
# The offline engine selftest's time (fb.harness.selftest).
SELFTEST_S = 6.0
# Kept back from the budget for the trace's last lines and a local copy of the workflow.
END_MARGIN_S = 20
# The kit client's own timeout per HTTP call.
FREYA_CALL_TIMEOUT_S = 120


def load_env() -> None:
    """KEY=value lines from .env / env.sh (export allowed); real environment wins."""
    for p in (ROOT / ".env", ROOT / "env.sh", ROOT / "kit" / "env.sh"):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*)$", line)
            if m and m.group(1) not in os.environ:
                val = m.group(2).split(" #")[0].strip().strip('"').strip("'")
                if val:
                    os.environ[m.group(1)] = val


load_env()
sys.path.insert(0, str(ROOT / "kit"))

import freya  # noqa: E402
from fb import contract  # noqa: E402
from fb.case_input import (  # noqa: E402
    decode_text,
    is_openapi,
    load_case,
    load_json_or_yaml,
    read_document,
    slot_name,
)
from fb.generate import build  # noqa: E402
from fb.openapi_tools import KIT_TOOLS, ToolApiError, parse_openapi  # noqa: E402
from fb.spec_extract import blocking, check, extract, to_generator, warnings  # noqa: E402
from fb.trace import Trace, redact  # noqa: E402
from fb.validate import Policy, validate  # noqa: E402

try:  # the cross-check and the proof map only add trace lines: never a reason not to build
    from fb import proof
    from fb.harness.selftest import run_selftest
    from fb.spec_extract import (
        OBSERVER_RESERVE_S,
        OBSERVER_WAIT_MAX_S,
        extract_observer,
        observe,
    )
except Exception:  # pragma: no cover
    proof = run_selftest = None  # type: ignore[assignment]
    extract_observer = observe = None  # type: ignore[assignment]
    OBSERVER_RESERVE_S, OBSERVER_WAIT_MAX_S = 300.0, 120.0
try:
    from fb.langpack import norm_language
except Exception:  # pragma: no cover

    def norm_language(x: object) -> str:  # type: ignore[misc]
        return str(x or "en").strip().lower()[:2] or "en"


# GET <mock_url>/openapi.json: one request, this long at most (the mock is ours and close)
MOCK_OPENAPI_TIMEOUT_S = 10
# Policy + examples longer than this get a trace note (IC-11); about 50k tokens
LONG_INPUT_CHARS = 200_000
# The smoke test's first customer turn, in the call's language: the reply to "am I speaking
# with <name>?". Language words only; a language without one here uses English.
SMOKE_HELLO = {"en": "Hello?", "tr": "Alo, evet benim."}
SMOKE_SAMPLE = {
    "en": {"first_name": "Alex", "last_name": "Morgan", "title": "Ms"},
    "tr": {"first_name": "Deniz", "last_name": "Kaya", "title": "Hanım"},
}
# Words the opening must not say before the identity check, per language; for Turkish they
# are stems matched at a word's start (borç -> borcunuz, gecikme -> gecikmiş).
SMOKE_WORDS = {
    "en": {"debt", "collections", "past due", "overdue"},
    "tr": {"borc", "gecikm", "tahsilat", "odenmemis", "odenmeyen"},
}


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True
        )
        commit = out.stdout.strip() or "unknown"
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return commit + ("-dirty" if dirty else "")
    except OSError:
        return "unknown"


def sha256(path: Path | None) -> str:
    """The file's hash; for a folder, the hash of its files' names and hashes."""
    if path is None:
        return "-"
    try:
        if path.is_dir():
            h = hashlib.sha256()
            for p in sorted(x for x in path.rglob("*") if x.is_file()):
                rel = p.relative_to(path).as_posix()
                h.update(f"{rel}\0{hashlib.sha256(p.read_bytes()).hexdigest()}\n".encode())
            return h.hexdigest()
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as e:
        return f"unreadable({type(e).__name__})"


def spec_summary(spec: dict, gen: dict | None, brief: str) -> list[str]:
    """Short lines on what the spec says: enough to see from the trace alone what was built.
    Never raises (a summary never stops the build)."""
    try:
        return _spec_summary(spec, gen, brief)
    except Exception as e:
        return [f"spec summary failed: {type(e).__name__}: {str(e)[:200]}"]


def _spec_summary(spec: dict, gen: dict | None, brief: str) -> list[str]:
    s = spec if isinstance(spec, dict) else {}
    cfg = (gen or {}).get("cfg") or {}
    qs = (s.get("identity") or {}).get("questions") or []
    fields = [str(q.get("field")) for q in qs if isinstance(q, dict)]
    bands = cfg.get("bands") or s.get("bands") or []
    hol = cfg.get("holidays") or (s.get("calendar") or {}).get("holidays") or []
    neg = cfg.get("neg") or s.get("neg") or {}
    outs = s.get("outcomes") or {}
    warns = warnings(s, brief)
    cvars = sorted(str(v) for v in (s.get("call_variables") or {}).values() if v)
    band_txt = ", ".join(
        f"{b.get('name')} {b.get('min_days_late')}-{b.get('max_days_late')}d x{b.get('attempts')}"
        for b in bands
        if isinstance(b, dict)
    )
    return [
        f"spec summary: identity factors={fields}; bands={len(bands)} ({band_txt}); "
        f"holidays={len(hol)}; call variables={cvars}",
        f"spec summary: close outcomes={sorted(map(str, (neg.get('outcomes') or {}).values()))} "
        f"reasons={sorted(map(str, (neg.get('reasons') or {}).values()))} "
        f"other endings={sorted(outs)}; spec warnings={len(warns)}"
        + (f" ({'; '.join(warns)[:300]})" if warns else ""),
    ]


def check_export(trace: Trace, out: Path, tools: dict, policy: Policy) -> str:
    """Validate the file the platform exported (what is scored), in export mode. Logged only:
    the file is kept whatever the result."""
    try:
        exported = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        trace.test(f"export check skipped: {type(e).__name__}: {str(e)[:200]}")
        return "skipped"
    rep = validate(exported, tools, dataclasses.replace(policy, exported=True))
    for i in rep.issues:
        trace.test(f"export check: {i}")
    if rep.ok:
        trace.test(f"export check: the exported workflow validates ({rep.summary()})")
        return "ok"
    trace.test(
        f"EXPORT CHECK FAILED: {rep.summary()} in {out.name}; the file is kept as exported, "
        "read the lines above"
    )
    return f"{len(rep.errors)}_errors"


def count_tools(wf: dict) -> int:
    names = {
        (t.get("function") or {}).get("name")
        for n in wf["nodes"].values()
        for t in (n.get("tools") or []) + (n.get("pre_actions") or [])
    }
    names |= {(t.get("function") or {}).get("name") for t in wf.get("tools") or []}
    return len({n for n in names if n and n != "code_exec"})


def bounded_api(hard_deadline: float):
    """freya.api with each HTTP call's timeout cut to the time left in the run's budget (the kit
    client waits up to 120 s per call; saving and publishing make about six calls). Any failure,
    a timeout included, is a RuntimeError as the kit raises for HTTP errors."""

    def api(method, path, body=None, **params):
        left = hard_deadline - time.monotonic()
        if left < 3:
            raise RuntimeError(f"{method} {path}: no time left in the {BUDGET_S // 60} min budget")
        q = "?" + urllib.parse.urlencode(params) if params else ""
        req = urllib.request.Request(
            freya.URL + path + q,
            data=json.dumps(body).encode() if body is not None else None,
            headers=freya.HEADERS,
            method=method,
        )
        # A gateway error or a dropped connection is tried again (the platform answered 504 to
        # a dry run on 11 Oct and the build ended with nothing published); a 4xx is an answer.
        wait = 4.0
        while True:
            left = hard_deadline - time.monotonic()
            try:
                with urllib.request.urlopen(req, timeout=min(FREYA_CALL_TIMEOUT_S, left)) as r:
                    out = json.load(r)
                break
            except urllib.error.HTTPError as e:
                msg = f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:1500]}"
                if not (e.code >= 500 or e.code == 429):
                    raise RuntimeError(msg) from None
            except (OSError, ValueError) as e:  # URLError, timeouts, a body that is not JSON
                msg = f"{method} {path} -> {type(e).__name__}: {e}"
            left = hard_deadline - time.monotonic()
            if left < wait + 10:
                raise RuntimeError(msg + " (no time left to try again)")
            print(f"freya: {msg[:200]}; trying again in {wait:.0f} s", file=sys.stderr)
            time.sleep(wait)
            wait = min(wait * 2, 30.0)
        return out.get("data", {}).get("result", out) if isinstance(out, dict) else out

    api.bounded = True  # type: ignore[attr-defined]
    return api


def _fold(text: str) -> str:
    """Turkish-aware lower case with the letters folded to ASCII (İ -> i, ç -> c)."""
    return text.translate(str.maketrans("İIıŞşĞğÜüÖöÇç", "iiissgguuoocc")).lower()


def _stem(word: str) -> str:
    """A Turkish word's stem for matching its suffixed forms: folded, and a final p/ç/t/k
    dropped (it softens before a vowel: borç -> borcu, kitap -> kitabı)."""
    w = _fold(word.strip())
    return w[:-1] if len(w) > 4 and w[-1] in "pctk" else w


def smoke(
    trace: Trace,
    wf_id: str,
    names: dict,
    forbidden: list | None = None,
    bank: str = "",
    lang: str = "en",
) -> str:
    """One chat turn in the call's language: the workflow starts a call, gets the customer's
    first reply and answers. Returns "passed" or "failed": an error, an empty reply (LP-7:
    the agent must say something to "hello, it's me") or a word that must wait for the
    identity check fails it. A liveness check, not a dialogue test."""
    lang = norm_language(lang)
    names_tbl = SMOKE_SAMPLE.get(lang) or SMOKE_SAMPLE["en"]
    sample = {
        "customer_id": "c_smoke_test",
        "first_name": names_tbl["first_name"],
        "last_name": names_tbl["last_name"],
        "full_name": f"{names_tbl['first_name']} {names_tbl['last_name']}",
        "title": names_tbl["title"],
        "now": time.strftime("%Y-%m-%dT%H:%M"),
    }
    cv = {
        str(names.get(role) or role): val for role, val in sample.items() if names.get(role, role)
    }
    hello = SMOKE_HELLO.get(lang) or SMOKE_HELLO["en"]
    try:
        r = freya.api(
            "POST",
            "/api/v2/chat/message",
            {"workflowId": wf_id, "message": hello, "customerVariables": cv},
        )
        r = r if isinstance(r, dict) else {}
        said = str(r.get("response") or "")
        trace.test(f"chat smoke ({lang}): sent {hello!r}, agent said: {said[:160]!r}")
        if r.get("callId"):
            try:
                freya.api("POST", "/api/v2/chat/end", {"callId": r["callId"]})
            except RuntimeError:
                pass
    except Exception as e:  # a smoke test never stops the export
        trace.test(f"chat smoke FAILED: {type(e).__name__}: {str(e)[:300]}")
        return "failed"
    if r.get("error"):
        trace.test(f"chat smoke FAILED: the chat answered an error: {str(r['error'])[:300]}")
        return "failed"
    if not said.strip():
        trace.test("chat smoke FAILED: the agent said nothing to the customer's first reply")
        return "failed"
    extra = {str(w) for w in forbidden or [] if str(w).strip()}
    if lang == "tr":
        text = _fold(said)
        if bank:
            text = text.replace(_fold(bank), " ")
        stems = SMOKE_WORDS["tr"] | {_stem(w) for w in extra}
        leak = sorted(w for w in stems if re.search(r"(?<![\w])" + re.escape(w), text))
    else:
        words = (SMOKE_WORDS.get(lang) or SMOKE_WORDS["en"]) | {w.lower() for w in extra}
        text = said.lower().replace(bank.lower(), " ") if bank else said.lower()  # "X Loans"
        leak = sorted(w for w in words if re.search(r"\b" + re.escape(w) + r"\b", text))
    if leak:
        trace.test(f"chat smoke FAILED: words said before the identity check: {leak}")
        return "failed"
    trace.test("chat smoke: passed")
    return "passed"


def keep_previous_run(slot: str) -> None:
    """A new run of the same brief must not overwrite the trace, spec and export of an earlier
    run that may be the one submitted: move them to runs/<slot>/<time of that run>/."""
    files = [
        ROOT / "traces" / f"{slot}.log",
        ROOT / "submission" / f"{slot}.spec.json",
        ROOT / "submission" / f"{slot}.workflow.json",
        ROOT / "submission" / f"{slot}.manifest.json",
    ]
    old = [f for f in files if f.exists()]
    if not old:
        return
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(old[0].stat().st_mtime))
    dest = ROOT / "runs" / slot / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for f in old:
        f.replace(dest / f.name)


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _spec_counts(spec: dict | None) -> dict:
    s = spec if isinstance(spec, dict) else {}
    neg = s.get("neg") if isinstance(s.get("neg"), dict) else {}
    ident = s.get("identity") if isinstance(s.get("identity"), dict) else {}
    cal = s.get("calendar") if isinstance(s.get("calendar"), dict) else {}

    def n(x: object) -> int:
        return len(x) if isinstance(x, list | dict) else 0

    return {
        "identity_factors": n(ident.get("questions")),
        "bands": n(s.get("bands")),
        "holidays": n(cal.get("holidays")),
        "attempt_plan": n(s.get("attempt_plan")),
        "outcomes": n(s.get("outcomes")),
        "neg_outcomes": n(neg.get("outcomes")),
        "neg_reasons": n(neg.get("reasons")),
        "queues": n(neg.get("queues")),
        "forbidden_before_verify": n(s.get("forbidden_before_verify")),
        "lines": n(s.get("lines")),
        "notes": n(s.get("notes")),
    }


def write_manifest(trace: Trace, slot: str, facts: dict, end: dict) -> str:
    """submission/<slot>.manifest.json: what this run read, built, checked and published, for a
    reader who has only the files. No secrets (the text goes through the trace's redaction).
    Returns its path relative to the repo, or '' when it could not be written (a trace note)."""
    try:
        path = ROOT / "submission" / f"{slot}.manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        out = Path(facts["out_path"]) if facts.get("out_path") else None
        written = out is not None and out.exists()
        data = {
            "slot": slot,
            "status": end.get("status"),
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(trace.t0)),
            "duration_s": round(trace.elapsed(), 1),
            "commit": facts.get("commit"),
            "inputs": facts.get("inputs") or {},
            "spec": facts.get("spec") or {},
            "observer": facts.get("observer") or {},
            "validator": facts.get("validator") or {},
            "proof_map": facts.get("proof_map") or {},
            "selftest": facts.get("selftest") or {},
            "workflow_sha256": facts.get("workflow_sha256"),
            "export": {
                "mode": end.get("export") or facts.get("export_mode") or "none",
                "file": out.name if written and out else None,
                "sha256": sha256(out) if written and out else None,
                "check": end.get("export_check"),
            },
            "freya": facts.get("freya") or {},
            "smoke": end.get("smoke") or facts.get("smoke") or "not run",
            "model": {
                "calls": trace.model_calls,
                "cost_usd": round(trace.cost_usd, 4),
                "failures": trace.model_failures,
            },
        }
        path.write_text(redact(json.dumps(data, indent=1, default=str)), encoding="utf-8")
        try:
            return str(path.relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            return str(path)
    except Exception as e:
        try:
            trace.note(f"manifest not written: {type(e).__name__}: {str(e)[:200]}")
        except Exception:
            pass
        return ""


def finish(trace: Trace, slot: str, facts: dict | None, **info: object) -> None:
    """The trace's END line, with the manifest written right before it."""
    if facts is not None:
        path = write_manifest(trace, slot, facts, dict(info))
        if path:
            info["manifest"] = path
    trace.end(**info)


def log_proof(trace: Trace, brief: str, spec: dict, gen: dict, wf: dict, facts: dict) -> None:
    """The brief's rules against the spec and the workflow (fb.proof). Lines only."""
    try:
        if proof is None:
            return
        units = proof.proof_map(brief, spec, gen, wf)
        for line in proof.trace_lines(units):
            trace.note(line)
        facts["proof_map"] = proof.summary(units)
    except Exception as e:
        trace.note(f"proof map failed: {type(e).__name__}: {str(e)[:200]}")


def log_selftest(trace: Trace, gen: dict, tools: dict, facts: dict) -> None:
    """The engine run offline on cases worked out from this spec (fb.harness.selftest): every
    band edge, date and amount edge, callback window, random calls with tool faults. A few
    seconds; lines only, never a reason not to publish."""
    try:
        if run_selftest is None:
            return
        out = run_selftest(gen, tools, budget_s=SELFTEST_S)
        fails = list(out.get("failures") or [])
        state = "skipped" if out.get("skipped") else ("passed" if not fails else "failed")
        trace.test(
            f"engine selftest: {state}, {out.get('cells', 0)} cases in "
            f"{float(out.get('seconds') or 0):.1f} s, {len(fails)} failures"
            + (" (time budget reached)" if out.get("budget_hit") else "")
        )
        for f in fails[:20]:
            trace.test(f"engine selftest FAILED: {str(f)[:300]}")
        facts["selftest"] = {"state": state, "cases": out.get("cells", 0), "failures": len(fails)}
    except Exception as e:
        trace.note(f"engine selftest failed to run: {type(e).__name__}: {str(e)[:200]}")


def extract_spec(
    brief: str,
    openapi: dict,
    tools: dict,
    trace: Trace,
    deadline: float | None = None,
    examples: str = "",
) -> dict:
    """The spec extractor on the policy text, with the example conversations kept apart: as
    its `examples=` keyword when it takes one, else appended after the policy under their own
    "Example conversations (illustrations, not rules)" header (case_input.examples_text).
    `extract` is looked up here at call time: a test's stand-in is used."""
    fn = extract
    if examples:
        try:
            takes = "examples" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            takes = False
        if takes:
            return fn(brief, openapi, tools, trace, deadline=deadline, examples=examples)
        brief = brief.rstrip() + "\n\n" + examples
    return fn(brief, openapi, tools, trace, deadline=deadline)


def start_observer(
    trace: Trace, brief: str, openapi: dict, tools: dict, deadline: float, examples: str = ""
):
    try:
        if extract_observer is None:
            return None
        # the same call as the main extraction (`extract` of this module, so a test's
        # stand-in reaches the cross-check too)
        fn = functools.partial(extract_spec, examples=examples)
        return extract_observer(brief, openapi, tools, trace, deadline=deadline, extract_fn=fn)
    except Exception as e:
        trace.note(f"uncertainty: observer not started: {type(e).__name__}: {str(e)[:200]}")
        return None


def join_observer(
    trace: Trace, observer, spec: dict, brief: str, openapi: dict, deadline: float
) -> dict:
    if observer is None or observe is None:
        return {"status": "off"}
    try:
        left = deadline - time.monotonic() - OBSERVER_RESERVE_S
        return observe(
            trace, observer, spec, brief, openapi, max(0.0, min(left, OBSERVER_WAIT_MAX_S))
        )
    except Exception as e:
        trace.note(f"uncertainty: observer skipped: {type(e).__name__}: {str(e)[:200]}")
        return {"status": "error"}


class Inputs:
    """What the build works from: the policy text for the spec extractor, the example
    conversations kept apart, the tool API and where it came from. (A plain class: tests load
    this file as a module of no package, where a dataclass cannot find its module.)"""

    def __init__(self, brief: str, examples: str = "", case: object | None = None) -> None:
        self.brief = brief
        self.examples = examples
        self.case = case
        self.openapi: dict | None = None
        self.api_source = ""


def parse_args(argv: list[str]) -> tuple[Path, Path | None, str | None] | None:
    """(input, OpenAPI file or None, mock_url or None); None when the command is wrong. A
    second argument starting with http:// or https:// is the mock's URL, else a file."""
    if len(argv) not in (2, 3):
        return None
    api: Path | None = None
    mock: str | None = None
    if len(argv) == 3:
        a = argv[2].strip()
        if re.match(r"(?i)https?://", a):
            mock = a
        else:
            api = Path(a)
    return Path(argv[1]), api, mock


def fetch_mock_openapi(trace: Trace, mock_url: str) -> dict | None:
    """GET <mock_url>/openapi.json: one request with a short timeout (our own mock may serve
    the contract it was written from). None, with a trace line, on any failure."""
    url = mock_url.rstrip("/") + "/openapi.json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=MOCK_OPENAPI_TIMEOUT_S) as r:
            text, _ = decode_text(r.read())
        doc = load_json_or_yaml(text)
    except Exception as e:
        trace.note(f"tool API: GET {url} -> {type(e).__name__}: {str(e)[:200]}")
        return None
    if not is_openapi(doc):
        trace.note(f"tool API: GET {url} -> not an OpenAPI document")
        return None
    trace.note(f"tool API: GET {url} -> OpenAPI with {len(doc.get('paths') or {})} paths")
    return doc


def _read_text_file(path: Path) -> str:
    """A text file as read_text would give it (universal newlines), any encoding."""
    text, _ = decode_text(path.read_bytes())
    return text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")


def read_inputs(
    trace: Trace,
    brief_path: Path,
    api_path: Path | None,
    mock_url: str | None,
    deadline: float | None,
) -> Inputs:
    """The policy text, the examples and the tool API (see the module docstring for the
    order). Raises ToolApiError when no tool API can be found."""
    p = Path(brief_path)
    if p.is_dir() or p.suffix.lower() == ".zip":
        case = load_case(p)
        for line in case.trace_lines():
            trace.note(line)
        inp = Inputs(brief=case.policy_text, examples=case.examples_text, case=case)
        if case.openapi is not None:
            inp.openapi, inp.api_source = case.openapi, f"{case.openapi_source} in the case"
    elif p.suffix.lower() in (".md", ".markdown", ".txt", ""):
        inp = Inputs(brief=_read_text_file(p))
    else:  # one brief as a Word / PDF / other document
        doc = read_document(p.name, p.read_bytes())
        if doc.error:
            raise ValueError(f"{p}: {doc.error}")
        trace.note(f"case: read {p.name} ({doc.size} bytes, reader {doc.reader})")
        inp = Inputs(brief=doc.text)
    if api_path is not None:
        inp.openapi = load_json_or_yaml(_read_text_file(Path(api_path)))
        inp.api_source = str(api_path)
    if inp.openapi is None and mock_url:
        inp.openapi = fetch_mock_openapi(trace, mock_url)
        if inp.openapi is not None:
            inp.api_source = mock_url.rstrip("/") + "/openapi.json"
    contract_text = getattr(inp.case, "contract_text", "") if inp.case is not None else ""
    if inp.openapi is None and contract_text:
        trace.note(
            f"tool API: no OpenAPI file; the service contract ({len(contract_text)} chars) "
            "is turned into OpenAPI by the model and checked against its text"
        )
        doc, left = contract.contract_to_openapi(contract_text, trace, deadline=deadline)
        if doc is not None:
            inp.openapi, inp.api_source = doc, "the service contract (model-written, checked)"
            trace.note("contract: " + contract.describe(doc))
            if left:
                trace.note(
                    f"contract: {len(left)} check problems left after the repair round; "
                    "building on them (see the lines above)"
                )
    if not is_openapi(inp.openapi) and not (
        isinstance(inp.openapi, dict) and isinstance(inp.openapi.get("paths"), dict)
    ):
        raise ToolApiError(
            "no tool API: give an OpenAPI file as the second argument or in the folder, a "
            "mock_url that serves /openapi.json, or a service contract document the README "
            "names"
        )
    return inp


def log_tools(trace: Trace, tools: dict, source: str, mock_url: str | None) -> None:
    """Where every tool goes, under which name, and what parse_openapi could not read."""
    trace.note(
        f"tool API: from {source or 'the given file'}"
        + (f"; every tool URL on the mock {mock_url}" if mock_url else "")
    )
    for name, t in tools.items():
        renamed = f" <- {t.api_name}" if t.api_name != name else ""
        trace.note(f"tool: {name}{renamed} ({t.method} {t.url}), {len(t.fields)} fields")
        if t.response_scale:
            trace.note(
                f"tool: {name} response amounts in minor units (x0.01): {sorted(t.response_scale)}"
            )
        if t.headers:
            trace.note(f"tool: {name} sends headers {sorted(t.headers)}")
        for w in t.warnings:
            trace.note(f"tool warning: {name}: {w}")
    missing = [r for r in KIT_TOOLS if r not in tools]
    if missing:
        trace.note(f"tool warning: no operation of the tool API plays {missing}")


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args is None:
        print(__doc__, file=sys.stderr)
        return 2
    brief_path, api_path, mock_url = args
    slot = slot_name(brief_path)
    keep_previous_run(slot)
    trace = Trace(ROOT / "traces" / f"{slot}.log")
    # Every step shares one clock; the last EXPORT_RESERVE_S stay for save and export, and no
    # Freya call runs past the budget itself.
    t0 = time.monotonic()
    deadline = t0 + BUDGET_S - EXPORT_RESERVE_S
    freya.api = bounded_api(t0 + BUDGET_S - END_MARGIN_S)
    facts: dict = {
        "commit": git_commit(),
        "inputs": {
            "brief": str(brief_path),
            "brief_sha256": sha256(brief_path),
            "tool_api": str(api_path or mock_url or "-"),
            "tool_api_sha256": sha256(api_path),
        },
    }
    if mock_url:
        facts["inputs"]["mock_url"] = mock_url
    trace.start(
        brief=brief_path,
        tool_api=api_path or "-",
        **({"mock_url": mock_url} if mock_url else {}),
        commit=facts["commit"],
        brief_sha256=facts["inputs"]["brief_sha256"],
        tool_api_sha256=facts["inputs"]["tool_api_sha256"],
    )
    try:
        return run(trace, brief_path, api_path, slot, deadline, facts, mock_url=mock_url)
    except Exception as e:  # the trace always gets its end line
        finish(trace, slot, facts, status=f"error: {type(e).__name__}: {str(e)[:300]}")
        return 1


def _wf_body(doc: object) -> dict:
    """The workflow inside a file the agent wrote: bare, or wrapped like an export."""
    if isinstance(doc, dict) and isinstance(doc.get("workflow"), dict) and "nodes" not in doc:
        return doc["workflow"]
    if not isinstance(doc, dict):
        raise ValueError("workflow.json is not a JSON object")
    return doc


def run_agentic(
    trace: Trace, inp: Inputs, slot: str, facts: dict, mock_url: str | None
) -> int:
    """The general path (fb.agentic): the coding agent writes, dry-runs, saves and tests the
    workflow in a work folder; the builder then checks it, saves it in the slot, exports it and
    smoke-tests it, as on the template path."""
    minutes = float(os.environ.get("BUILDER_AGENT_MINUTES", "50"))
    t_agent_end = time.time() + minutes * 60
    # the agent's clock replaces the template's 30 min budget; save, export and smoke get
    # their own reserve after it
    freya.api = bounded_api(time.monotonic() + minutes * 60 + EXPORT_RESERVE_S + SMOKE_MAX_S)
    ws = ROOT / ".builder" / "agentic" / slot
    state = ROOT / ".freya-agents.json"
    task = agentic.prepare(
        ws, inp.case, inp.openapi or {}, mock_url, slot, ROOT / "kit", state,
        time.strftime("%H:%M", time.gmtime(t_agent_end - 60)),
    )
    trace.note(f"agent: work folder {ws.relative_to(ROOT)}, {minutes:.0f} min, model {agentic.AGENT_MODEL}")
    session_log = ROOT / "traces" / f"{slot}.agent.jsonl"
    try:
        rc = agentic.run_agent(trace, ws, task, session_log, minutes * 60)
    except Exception as e:
        finish(trace, slot, facts, status=f"agent failed: {type(e).__name__}: {str(e)[:300]}")
        return 1
    trace.note(f"agent: exit {rc}; session log -> {session_log.relative_to(ROOT)}")
    for name in ("rules.md", "workflow.json", "call_variables.json"):
        src = ws / name
        if src.exists():
            (ROOT / "submission").mkdir(exist_ok=True)
            shutil.copy(src, ROOT / "submission" / f"{slot}.{name}")
    try:
        wf = _wf_body(json.loads((ws / "workflow.json").read_text(encoding="utf-8")))
    except Exception as e:
        finish(trace, slot, facts, status=f"no workflow from the agent: {type(e).__name__}: {str(e)[:200]}")
        return 1
    nodes = wf.get("nodes") or []
    trace.note(f"workflow: {len(nodes)} steps, {count_tools(wf)} tools, {len(json.dumps(wf))} bytes")
    hosts = sorted(set(re.findall(r'https?://[^/"\s]+', json.dumps(wf))))
    trace.note(f"workflow: tool hosts {hosts}")
    try:
        freya.validate_workflow(f"{slot} dry run", wf)
        trace.freya("dry run: ok")
    except RuntimeError as e:
        trace.freya(f"dry run FAILED: {str(e)[:500]}")
        finish(trace, slot, facts, status="dry run refused, nothing published")
        return 1
    try:
        saved = freya.save_workflow(slot, wf, state_file=str(state))
    except Exception as e:
        trace.freya(f"save_workflow FAILED: {type(e).__name__}: {str(e)[:500]}")
        finish(trace, slot, facts, status="save failed")
        return 1
    trace.freya(
        f"save_workflow slot={slot} -> workflow_id={saved['workflow_id']} "
        f"agent_id={saved['agent_id']} version={saved.get('version', 1)} (published)"
    )
    facts["freya"] = {k: saved.get(k) for k in ("workflow_id", "agent_id", "version") if k in saved}
    out = ROOT / "submission" / f"{slot}.workflow.json"
    facts["out_path"] = str(out)
    try:
        freya.export_workflow(saved["workflow_id"], str(out))
        trace.freya(f"export_workflow -> {out.relative_to(ROOT)}")
        export = "platform"
    except Exception as e:
        trace.freya(f"export_workflow FAILED: {type(e).__name__}: {str(e)[:300]}")
        ids = {"workflow_id": saved["workflow_id"], "agent_id": saved["agent_id"]}
        out.write_text(json.dumps({**ids, "updated_at": None, "workflow": wf}, indent=1))
        export = "local"
    result = "skipped"
    try:
        cv = json.loads((ws / "call_variables.json").read_text(encoding="utf-8"))
        r = freya.api(
            "POST", "/api/v2/chat/message",
            {"workflowId": saved["workflow_id"], "message": "Merhaba", "customerVariables": cv},
        )
        r = r if isinstance(r, dict) else {}
        said = str(r.get("response") or "")
        trace.test(f"chat smoke: variables {cv}, sent 'Merhaba', agent said: {said[:200]!r}")
        if r.get("callId"):
            try:
                freya.api("POST", "/api/v2/chat/end", {"callId": r["callId"]})
            except RuntimeError:
                pass
        result = "passed" if said.strip() and not r.get("error") else "failed"
    except Exception as e:
        trace.test(f"chat smoke FAILED: {type(e).__name__}: {str(e)[:300]}")
        result = "failed"
    trace.test(f"chat smoke: {result}")
    status = "ok" + ("" if result == "passed" else f"_smoke_{result}")
    status += "" if export == "platform" else "_export_failed"
    finish(trace, slot, facts, status=status, smoke=result, export=export,
           path="general (coding agent)", workflow_id=saved["workflow_id"],
           agent_id=saved["agent_id"], agent_log=str(session_log.relative_to(ROOT)))
    return 0


def run(
    trace: Trace,
    brief_path: Path,
    api_path: Path | None,
    slot: str,
    deadline: float,
    facts: dict | None = None,
    mock_url: str | None = None,
) -> int:
    if facts is None:
        facts = {
            "commit": None,
            "inputs": {
                "brief": str(brief_path),
                "brief_sha256": sha256(brief_path),
                "tool_api": str(api_path or mock_url or "-"),
                "tool_api_sha256": sha256(api_path),
            },
        }
    try:
        inp = read_inputs(trace, brief_path, api_path, mock_url, deadline)
        tools = parse_openapi(inp.openapi, base_url=mock_url)
    except ToolApiError as e:
        finish(trace, slot, facts, status=f"no usable tool API ({str(e)[:300]}), nothing published")
        return 1
    brief, examples, openapi = inp.brief, inp.examples, inp.openapi
    facts["inputs"]["tool_api_source"] = inp.api_source or str(api_path)
    if inp.case is not None:
        facts["inputs"]["documents"] = {
            d.name: (d.role if not d.error else f"unread: {d.error[:80]}")
            for d in inp.case.documents  # type: ignore[attr-defined]
        }
    trace.note(
        f"brief: {len(brief)} chars; tools: {', '.join(sorted(tools))}"
        + (f"; example conversations: {len(examples)} chars, apart" if examples else "")
    )
    log_tools(trace, tools, inp.api_source, mock_url)
    if not agentic.fits_template(tools):
        trace.note(
            "template: the tool API plays none of the collections roles "
            f"{list(agentic.ROLES_NEEDED)}; the deterministic template cannot express this "
            "call, building on the general path (coding agent)"
        )
        return run_agentic(trace, inp, slot, facts, mock_url)
    if len(brief) + len(examples) > LONG_INPUT_CHARS:
        # nothing is cut (a cut rule is a wrong agent); the trace says why the call is slow
        trace.note(
            f"input: {len(brief) + len(examples)} chars for the spec model, more than "
            f"{LONG_INPUT_CHARS}: the extraction call is long; nothing is cut"
        )

    observer = start_observer(trace, brief, openapi, tools, deadline, examples=examples)
    spec = extract_spec(brief, openapi, tools, trace, deadline=deadline, examples=examples)
    facts["observer"] = join_observer(trace, observer, spec, brief, openapi, deadline)
    (ROOT / "submission").mkdir(exist_ok=True)
    (ROOT / "submission" / f"{slot}.spec.json").write_text(json.dumps(spec, indent=1))
    problems = check(spec, tools, brief, openapi)
    try:
        facts["spec"] = {
            "counts": _spec_counts(spec),
            "problems": list(problems),
            "warnings": warnings(spec, brief),
        }
    except Exception as e:
        facts["spec"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    for p in problems:
        trace.note(f"spec error: {p}")
    if problems:
        # A submitted imperfect workflow scores more than none: problems left after the repair
        # round are kept in the trace and the build goes on, unless the spec cannot make a
        # workflow at all (no identity questions, no bands).
        hard = blocking(spec)
        if hard:
            finish(
                trace, slot, facts, status=f"spec invalid ({'; '.join(hard)}), nothing published"
            )
            return 1
        trace.note(f"spec checks: {len(problems)} problems left, building anyway")
    else:
        trace.note("spec checks: 0 errors")

    gen = None
    try:
        gen = to_generator(spec)
        for line in spec_summary(spec, gen, brief):
            trace.note(line)
        wf, policy = build(gen, tools)
    except Exception as e:
        if gen is None:
            for line in spec_summary(spec, None, brief):
                trace.note(line)
        finish(trace, slot, facts, status=f"generation failed: {type(e).__name__}: {str(e)[:300]}")
        return 1
    policy.call_vars |= {str(v) for v in (gen.get("call_variables") or {}).values() if v}
    rep = validate(wf, tools, policy)
    for i in rep.issues:
        trace.note(f"validator: {i}")
    trace.note(
        f"workflow: {len(wf['nodes'])} steps, {count_tools(wf)} tools, "
        f"{len(json.dumps(wf))} bytes; validator {rep.summary()}"
    )
    try:
        facts["validator"] = {
            "errors": len(rep.errors),
            "warnings": len(rep.warnings),
            "codes": dict(sorted(Counter(f"{i.level}:{i.code}" for i in rep.issues).items())),
        }
        facts["workflow_sha256"] = _sha_text(json.dumps(wf, sort_keys=True, ensure_ascii=False))
    except Exception as e:
        trace.note(f"manifest facts: {type(e).__name__}: {str(e)[:200]}")
    log_proof(trace, brief, spec, gen, wf, facts)
    log_selftest(trace, gen, tools, facts)
    if not rep.ok:
        finish(trace, slot, facts, status="invalid workflow, nothing published")
        return 1
    out = ROOT / "submission" / f"{slot}.workflow.json"
    facts["out_path"] = str(out)

    def write_local(why: str) -> None:
        # the submission is the workflow file: with the platform down it is still written
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"updated_at": None, "workflow": wf}, indent=1))
        trace.freya(f"{why}: wrote the workflow as built -> {out.relative_to(ROOT)}")
        facts["export_mode"] = "local"

    try:
        freya.validate_workflow(f"{slot} dry run", wf)
        trace.freya("dry run: ok")
    except RuntimeError as e:
        trace.freya(f"dry run FAILED: {str(e)[:500]}")
        if re.search(r"-> 4\d\d:", str(e)):
            # the platform read the workflow and refused it
            write_local("dry run refused")
            finish(trace, slot, facts, status="dry run refused, nothing published")
            return 1
        # the platform did not answer: saving tries again (it validates too)
    try:
        saved = freya.save_workflow(slot, wf, state_file=str(ROOT / ".freya-agents.json"))
    except Exception as e:
        trace.freya(f"save_workflow FAILED: {type(e).__name__}: {str(e)[:500]}")
        write_local("save failed")
        finish(trace, slot, facts, status="save failed, local file written")
        return 1
    trace.freya(
        f"save_workflow slot={slot} -> workflow_id={saved['workflow_id']} "
        f"agent_id={saved['agent_id']} version={saved.get('version', 1)} (published)"
    )
    try:
        facts["freya"] = {
            k: saved.get(k) for k in ("workflow_id", "agent_id", "version") if k in saved
        }
    except Exception:
        pass
    # A saved workflow is always written out first: the platform's export, else what was saved.
    # (11 Oct: the chat route kept answering 504 and the smoke test's retries ran 14 minutes; the
    # export after it would have had no time left.)
    try:
        freya.export_workflow(saved["workflow_id"], str(out))
        trace.freya(f"export_workflow -> {out.relative_to(ROOT)}")
        export = "platform"
    except Exception as e:
        trace.freya(f"export_workflow FAILED: {type(e).__name__}: {str(e)[:300]}")
        ids = {"workflow_id": saved["workflow_id"], "agent_id": saved["agent_id"]}
        out.write_text(json.dumps({**ids, "updated_at": None, "workflow": wf}, indent=1))
        trace.freya(f"wrote the saved workflow as sent -> {out.relative_to(ROOT)}")
        export = "local"
    export_check = "skipped"
    if export == "platform":
        try:
            export_check = check_export(trace, out, tools, policy)
        except Exception as e:  # a check of the file never changes or removes it
            trace.test(f"export check crashed: {type(e).__name__}: {str(e)[:200]}")
            export_check = "crashed"
    if deadline - time.monotonic() > SMOKE_RESERVE_S:
        # the smoke test gets its own short clock: retries of a slow chat route never eat the
        # rest of the budget
        full_api = freya.api
        if getattr(full_api, "bounded", False):
            freya.api = bounded_api(
                min(time.monotonic() + SMOKE_MAX_S, deadline + EXPORT_RESERVE_S - END_MARGIN_S)
            )
        try:
            result = smoke(
                trace,
                saved["workflow_id"],
                gen.get("call_variables") or {},
                gen.get("forbidden_before_verify") or [],
                str(gen.get("bank_name") or ""),
                str(gen.get("language") or spec.get("language") or "en"),
            )
        finally:
            freya.api = full_api
    else:
        trace.test("chat smoke skipped: not enough time left")
        result = "skipped"
    status = "ok_spec_problems" if problems else "ok"
    status += "" if result == "passed" else f"_smoke_{result}"
    status += "" if export == "platform" else "_export_failed"
    finish(
        trace,
        slot,
        facts,
        status=status,
        smoke=result,
        export=export,
        export_check=export_check,
        workflow_id=saved["workflow_id"],
        agent_id=saved["agent_id"],
    )
    return 0


if __name__ == "__main__":
    # a console in cp1254/cp1252 cannot print every character of a Turkish document: such a
    # character is replaced on screen, never a reason to stop (the trace file is UTF-8)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    sys.exit(main(sys.argv))
