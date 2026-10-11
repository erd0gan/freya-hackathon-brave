"""Build a Freya phone agent from one brief and its tool API file.

    python3 builder/build.py <brief_path> <tool_api_path>

Stdlib only. Steps: read -> spec (model) -> workflow (deterministic template) -> validate ->
save on Freya (one slot per brief, updated in place) -> smoke test over chat -> export.
Writes traces/<brief>.log and submission/<brief>.workflow.json. Needs FREYA_API_KEY and
OPENROUTER_API_KEY in the environment (or in a .env / kit/env.sh file next to the repo).

Once a workflow is saved, a submission file is always written: the platform export, or the
workflow as sent if the export fails. The trace's END line says how far the run got
(status=ok only when the chat smoke test passed; smoke=passed|failed|skipped).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

BUDGET_S = 30 * 60
EXPORT_RESERVE_S = 3 * 60
SMOKE_RESERVE_S = 60
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
from fb.spec_extract import check, extract, to_generator  # noqa: E402
from fb.trace import Trace  # noqa: E402
from fb.validate import validate  # noqa: E402


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
        try:
            with urllib.request.urlopen(req, timeout=min(FREYA_CALL_TIMEOUT_S, left)) as r:
                out = json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:1500]}") from None
        except (OSError, ValueError) as e:  # URLError, timeouts, a body that is not JSON
            raise RuntimeError(f"{method} {path} -> {type(e).__name__}: {e}") from None
        return out.get("data", {}).get("result", out) if isinstance(out, dict) else out

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
    ]
    old = [f for f in files if f.exists()]
    if not old:
        return
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(old[0].stat().st_mtime))
    dest = ROOT / "runs" / slot / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for f in old:
        f.replace(dest / f.name)


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
    trace.start(brief=brief_path, tool_api=api_path, commit=git_commit())
    try:
        return run(trace, brief_path, api_path, slot, deadline)
    except Exception as e:  # the trace always gets its end line
        trace.end(status=f"error: {type(e).__name__}: {str(e)[:300]}")
        return 1


def run(trace: Trace, brief_path: Path, api_path: Path, slot: str, deadline: float) -> int:
    brief = brief_path.read_text(encoding="utf-8")
    openapi = json.loads(api_path.read_text(encoding="utf-8"))
    tools = parse_openapi(openapi)
    trace.note(f"brief: {len(brief)} chars; tools: {', '.join(sorted(tools))}")

    spec = extract(brief, openapi, tools, trace, deadline=deadline)
    (ROOT / "submission").mkdir(exist_ok=True)
    (ROOT / "submission" / f"{slot}.spec.json").write_text(json.dumps(spec, indent=1))
    problems = check(spec, tools, brief, openapi)
    if problems:
        # A structural error left after the repair would publish a broken agent: stop here
        # and run the builder again (the extraction is a model call and can differ).
        for p in problems:
            trace.note(f"spec error: {p}")
        trace.end(status=f"spec invalid ({len(problems)} errors), nothing published")
        return 1
    trace.note("spec checks: 0 errors")

    gen = to_generator(spec)
    wf, policy = build(gen, tools)
    rep = validate(wf, tools, policy)
    for i in rep.errors:
        trace.note(f"validator: {i}")
    trace.note(
        f"workflow: {len(wf['nodes'])} steps, {count_tools(wf)} tools, "
        f"{len(json.dumps(wf))} bytes; validator {rep.summary()}"
    )
    if not rep.ok:
        trace.end(status="invalid workflow, nothing published")
        return 1
    try:
        freya.validate_workflow(f"{slot} dry run", wf)
        trace.freya("dry run: ok")
    except RuntimeError as e:
        trace.freya(f"dry run FAILED: {str(e)[:500]}")
        trace.end(status="dry run failed, nothing published")
        return 1
    saved = freya.save_workflow(slot, wf, state_file=str(ROOT / ".freya-agents.json"))
    trace.freya(
        f"save_workflow slot={slot} -> workflow_id={saved['workflow_id']} "
        f"agent_id={saved['agent_id']} version={saved.get('version', 1)} (published)"
    )
    if deadline - time.monotonic() > SMOKE_RESERVE_S:
        result = smoke(
            trace,
            saved["workflow_id"],
            gen.get("call_variables") or {},
            gen.get("forbidden_before_verify") or [],
            str(gen.get("bank_name") or ""),
        )
    else:
        trace.test("chat smoke skipped: not enough time left")
        result = "skipped"
    # A saved workflow is always written out: the platform's export, else what was saved.
    out = ROOT / "submission" / f"{slot}.workflow.json"
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
    status = "ok" + ("" if result == "passed" else f"_smoke_{result}")
    status += "" if export == "platform" else "_export_failed"
    trace.end(
        status=status,
        smoke=result,
        export=export,
        workflow_id=saved["workflow_id"],
        agent_id=saved["agent_id"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
