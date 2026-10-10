"""Build a Freya phone agent from one brief and its tool API file.

    python3 builder/build.py <brief_path> <tool_api_path>

Stdlib only. Steps: read -> spec (model) -> workflow (deterministic template) -> validate ->
save on Freya (one slot per brief, updated in place) -> smoke test over chat -> export.
Writes traces/<brief>.log and submission/<brief>.workflow.json. Needs FREYA_API_KEY and
OPENROUTER_API_KEY in the environment (or in a .env / kit/env.sh file next to the repo).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

BUDGET_S = 30 * 60
SMOKE_RESERVE_S = 4 * 60


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
        return out.stdout.strip() or "unknown"
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


def smoke(trace: Trace, wf_id: str, names: dict) -> None:
    """One chat turn: the workflow starts a call and says its opening. The chat route can
    skip the first entry line, so a missing opening is a warning, not a failure."""
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
        leak = [w for w in ("debt", "collections", "past due", "overdue") if w in said.lower()]
        if leak:
            trace.test(f"chat smoke WARNING: words before identity check: {leak}")
        if r.get("callId"):
            try:
                freya.api("POST", "/api/v2/chat/end", {"callId": r["callId"]})
            except RuntimeError:
                pass
    except RuntimeError as e:
        trace.test(f"chat smoke FAILED: {str(e)[:300]}")


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    brief_path, api_path = Path(argv[1]), Path(argv[2])
    slot = brief_path.stem
    trace = Trace(ROOT / "traces" / f"{slot}.log")
    trace.start(brief=brief_path, tool_api=api_path, commit=git_commit())
    brief = brief_path.read_text(encoding="utf-8")
    openapi = json.loads(api_path.read_text(encoding="utf-8"))
    tools = parse_openapi(openapi)
    trace.note(f"brief: {len(brief)} chars; tools: {', '.join(sorted(tools))}")

    spec = extract(brief, openapi, tools, trace)
    problems = check(spec, tools, brief)
    trace.note(
        f"spec checks: {len(problems)} open problems"
        + (": " + "; ".join(problems)[:400] if problems else "")
    )
    (ROOT / "submission").mkdir(exist_ok=True)
    (ROOT / "submission" / f"{slot}.spec.json").write_text(json.dumps(spec, indent=1))

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
        trace.end(status="invalid workflow")
        return 1
    try:
        freya.validate_workflow(f"{slot} dry run", wf)
        trace.freya("dry run: ok")
    except RuntimeError as e:
        trace.freya(f"dry run FAILED: {str(e)[:500]}")
        trace.end(status="dry run failed")
        return 1
    saved = freya.save_workflow(slot, wf, state_file=str(ROOT / ".freya-agents.json"))
    trace.freya(
        f"save_workflow slot={slot} -> workflow_id={saved['workflow_id']} "
        f"agent_id={saved['agent_id']} version={saved.get('version', 1)} (published)"
    )
    if trace.elapsed() < BUDGET_S - SMOKE_RESERVE_S:
        smoke(trace, saved["workflow_id"], gen.get("call_variables") or {})
    out = ROOT / "submission" / f"{slot}.workflow.json"
    freya.export_workflow(saved["workflow_id"], str(out))
    trace.freya(f"export_workflow -> {out.relative_to(ROOT)}")
    trace.end(status="ok", workflow_id=saved["workflow_id"], agent_id=saved["agent_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
