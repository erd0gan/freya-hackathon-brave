"""Build a Freya phone agent from one brief and its tool API file.

    python3 builder/build.py <brief_path> <tool_api_path>

Stdlib only. Steps: read -> spec (model) -> workflow (deterministic template) -> validate ->
save on Freya (one slot per brief, updated in place) -> smoke test over chat -> export.
Writes traces/<brief>.log and submission/<brief>.workflow.json. Needs FREYA_API_KEY and
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
import hashlib
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
from fb.generate import build  # noqa: E402
from fb.openapi_tools import parse_openapi  # noqa: E402
from fb.spec_extract import blocking, check, extract, to_generator, warnings  # noqa: E402
from fb.trace import Trace, redact  # noqa: E402
from fb.validate import Policy, validate  # noqa: E402

try:  # the cross-check and the proof map only add trace lines: never a reason not to build
    from fb import proof
    from fb.spec_extract import (
        OBSERVER_RESERVE_S,
        OBSERVER_WAIT_MAX_S,
        extract_observer,
        observe,
    )
except Exception:  # pragma: no cover
    proof = None  # type: ignore[assignment]
    extract_observer = observe = None  # type: ignore[assignment]
    OBSERVER_RESERVE_S, OBSERVER_WAIT_MAX_S = 300.0, 120.0


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


def sha256(path: Path) -> str:
    try:
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
    band_txt = ", ".join(
        f"{b.get('name')} {b.get('min_days_late')}-{b.get('max_days_late')}d x{b.get('attempts')}"
        for b in bands
        if isinstance(b, dict)
    )
    return [
        f"spec summary: identity factors={fields}; bands={len(bands)} ({band_txt}); "
        f"holidays={len(hol)}; call variables={sorted((s.get('call_variables') or {}).values())}",
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


def smoke(
    trace: Trace, wf_id: str, names: dict, forbidden: list | None = None, bank: str = ""
) -> str:
    """One chat turn: the workflow starts a call and says its opening. Returns "passed" or
    "failed". The chat route can skip the first entry line, so a missing opening is not a
    failure; an error, or a word that must wait for the identity check, is."""
    sample = {
        "customer_id": "c_smoke_test",
        "first_name": "Alex",
        "last_name": "Morgan",
        "full_name": "Alex Morgan",
        "title": "Ms",
        "now": time.strftime("%Y-%m-%dT%H:%M"),
    }
    cv = {
        str(names.get(role) or role): val for role, val in sample.items() if names.get(role, role)
    }
    try:
        r = freya.api(
            "POST",
            "/api/v2/chat/message",
            {"workflowId": wf_id, "message": "Hello?", "customerVariables": cv},
        )
        said = str(r.get("response") or "")
        trace.test(f"chat smoke: call started, agent said: {said[:160]!r}")
        if r.get("callId"):
            try:
                freya.api("POST", "/api/v2/chat/end", {"callId": r["callId"]})
            except RuntimeError:
                pass
    except Exception as e:  # a smoke test never stops the export
        trace.test(f"chat smoke FAILED: {type(e).__name__}: {str(e)[:300]}")
        return "failed"
    words = {"debt", "collections", "past due", "overdue"}
    words |= {str(w).lower() for w in forbidden or [] if str(w).strip()}
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


def start_observer(trace: Trace, brief: str, openapi: dict, tools: dict, deadline: float):
    try:
        if extract_observer is None:
            return None
        # `extract` of this module: a test's stand-in reaches the cross-check too
        return extract_observer(brief, openapi, tools, trace, deadline=deadline, extract_fn=extract)
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


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    brief_path, api_path = Path(argv[1]), Path(argv[2])
    slot = brief_path.stem
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
            "tool_api": str(api_path),
            "tool_api_sha256": sha256(api_path),
        },
    }
    trace.start(
        brief=brief_path,
        tool_api=api_path,
        commit=facts["commit"],
        brief_sha256=facts["inputs"]["brief_sha256"],
        tool_api_sha256=facts["inputs"]["tool_api_sha256"],
    )
    try:
        return run(trace, brief_path, api_path, slot, deadline, facts)
    except Exception as e:  # the trace always gets its end line
        finish(trace, slot, facts, status=f"error: {type(e).__name__}: {str(e)[:300]}")
        return 1


def run(
    trace: Trace,
    brief_path: Path,
    api_path: Path,
    slot: str,
    deadline: float,
    facts: dict | None = None,
) -> int:
    if facts is None:
        facts = {
            "commit": None,
            "inputs": {
                "brief": str(brief_path),
                "brief_sha256": sha256(brief_path),
                "tool_api": str(api_path),
                "tool_api_sha256": sha256(api_path),
            },
        }
    brief = brief_path.read_text(encoding="utf-8")
    openapi = json.loads(api_path.read_text(encoding="utf-8"))
    tools = parse_openapi(openapi)
    trace.note(f"brief: {len(brief)} chars; tools: {', '.join(sorted(tools))}")

    observer = start_observer(trace, brief, openapi, tools, deadline)
    spec = extract(brief, openapi, tools, trace, deadline=deadline)
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
    sys.exit(main(sys.argv))
