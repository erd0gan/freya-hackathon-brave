"""The builder's general path: a case the collections template cannot express.

The deterministic template (fb.generate) builds one kind of call: an outbound collections call
with an identity check, a negotiation and a promise. A case whose tool API plays none of those
roles (no verify_identity / get_account / create_promise operation) used to stop the build with
"generation failed". Here such a case is handed to a coding agent (Claude Code, headless) that
reads every document of the case, writes the workflow in Freya's format, checks it with the
platform's dry run, saves it and talks to it over the chat route against the mock, then fixes
what fails. The builder around it stays the same: it reads the case, finds the tool API, gives
the agent a clock, then validates, saves, exports and smoke-tests what the agent wrote.

Nothing about any one case is written here: the task text is the same for every case, the
agent gets the case only as the documents in its work folder.

The agent's full session (every model call, every tool use) is written to
traces/<slot>.agent.jsonl next to the builder's own trace; the trace gets one line per tool
use of the agent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

ROLES_NEEDED = ("verify_identity", "get_account", "create_promise")
AGENT_MODEL = os.environ.get("BUILDER_AGENT_MODEL", "opus")


def fits_template(tools: dict) -> bool:
    """True when the tool API plays the roles the collections template is built on."""
    return all(r in tools for r in ROLES_NEEDED)


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name).strip("_") or "doc"


# The helper the agent uses to reach Freya. It runs in the work folder with the builder's
# environment (FREYA_API_KEY), imports the kit's client and works on the build's own slot.
HELPER = r'''"""Freya helper for the build. Usage:
  python ft.py dryrun                      validate workflow.json on the platform (no save)
  python ft.py save                        save + publish workflow.json in this build's slot
  python ft.py chat VARS_JSON MSG [MSG...] talk to the SAVED agent, one message per turn;
                                           prints each reply and the tool calls of the call
"""
import json, sys, time
sys.path.insert(0, {kit!r})
import freya

SLOT = {slot!r}
STATE = {state!r}


def load():
    return json.load(open("workflow.json", encoding="utf-8"))


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "dryrun":
        try:
            freya.validate_workflow(SLOT + " dry run", load())
            print("DRY RUN OK")
        except RuntimeError as e:
            print("DRY RUN FAILED:", str(e)[:3000])
            return 1
    elif cmd == "save":
        s = freya.save_workflow(SLOT, load(), state_file=STATE)
        print("SAVED", json.dumps(s))
    elif cmd == "chat":
        state = json.load(open(STATE, encoding="utf-8"))
        wf_id = state[SLOT]["workflow_id"]
        cv = json.loads(argv[2])
        call_id = None
        for msg in argv[3:]:
            body = {{"workflowId": wf_id, "message": msg, "customerVariables": cv}}
            if call_id:
                body["callId"] = call_id
            t = time.time()
            try:
                r = freya.api("POST", "/api/v2/chat/message", body)
            except RuntimeError as e:
                print(f"USER: {{msg}}\n  !! chat error after {{time.time()-t:.0f}}s: {{str(e)[:400]}}")
                continue
            call_id = r.get("callId") or call_id
            print(f"USER: {{msg}}\nAGENT: {{r.get('response')}}" + (f"  [error: {{r.get('error')}}]" if r.get("error") else ""))
            if r.get("ended") or r.get("callEnded"):
                print("  (call ended)")
        if call_id:
            try:
                freya.api("POST", "/api/v2/chat/end", {{"callId": call_id}})
            except RuntimeError:
                pass
            try:
                c = freya.get_call(call_id)
                for m in c.get("messages") or []:
                    if m.get("role") in ("tool", "function") or m.get("tool_calls") or m.get("toolCalls"):
                        print("  TOOL:", json.dumps(m, ensure_ascii=False)[:600])
            except Exception as e:
                print("  (call events unavailable:", str(e)[:200], ")")
    elif cmd == "sim":
        # personas.json: [{{"name", "prompt", "successCriteria", "variables": {{...}}}}]
        state = json.load(open(STATE, encoding="utf-8"))
        agent_id = state[SLOT]["agent_id"]
        ps = json.load(open(argv[2] if len(argv) > 2 else "personas.json", encoding="utf-8"))
        made = freya.create_personas([{{k: p[k] for k in ("name", "prompt", "successCriteria")}}
                                      | {{"tags": ["builder-sim"]}} for p in ps])["personas"]
        vmap = {{m["id"]: p["variables"] for m, p in zip(made, ps)}}
        names = {{m["id"]: p["name"] for m, p in zip(made, ps)}}
        t = time.time()
        sim_id, it_id, status = freya.run_simulation(agent_id, SLOT + " self-test", vmap,
                                                     max_seconds=300, concurrency=len(ps))
        runs = freya.get_iteration(sim_id, it_id)["runs"]
        ok = 0
        for r in runs:
            good = r.get("outcome") == "pass"
            ok += good
            print(("PASS" if good else "FAIL"), names.get(r.get("personaId")),
                  f"turns={{r.get('turnCount')}}", "|", " ".join(str(r.get("reason")).split())[:700])
            if not good and r.get("callId"):
                try:
                    c = freya.get_call(r["callId"])
                    for m in (c.get("messages") or [])[:40]:
                        print("    ", m.get("role"), ":", str(m.get("content") or m.get("message") or "")[:300])
                except Exception as e:
                    print("    (transcript unavailable:", str(e)[:150], ")")
        print(f"SIM {{ok}}/{{len(runs)}} passed, status {{status}}, {{time.time()-t:.0f}}s")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
'''

TASK = """You are the construction step of an agent-builder. A bank sent the documents in
`docs/` (plain text of every file; the originals are under `case/`). Build the phone agent they
describe as one Freya workflow and write it to `workflow.json` in this folder.

Time: you must be finished, with the final version saved, by {deadline_utc} UTC. It is now
{now_utc} UTC. Keep at least 5 minutes in hand.

## Inputs
- `docs/INDEX.md` lists every document with its original path. A document marked
  unreadable (a drawing, a scan) is still in `case/`: read the original file (the Read tool
  shows PDFs and images).
- `openapi.json` is the tool API. Every tool URL is `servers[0].url` + the path, exactly; the
  services run on our mock at {mock_url} and answer the bank's test customers.
- `kit/workflow-format.md`, `kit/tools-and-code.md`, `kit/api.md`: Freya's workflow format,
  tools, code steps and API. `kit/base-agent.json` is a small working example (a pattern for the
  JSON, not an answer). Read the format documents fully before writing.

## How to work
1. Read every document. Sources were written at different times and disagree. Write
   `rules.md`: every rule the agent must follow, each with its source file and date. Where
   sources disagree the latest decision wins (a later mail, meeting decision or correction beats
   an earlier analysis, and a correction beats the summary it corrects). A public web page, an
   old menu script or a drawing marked as outdated is not policy where a later decision says
   otherwise. Keep the exact sentences the documents give for the agent to say.
2. Design the workflow from `rules.md`: one step per stage of the call, transitions on tool
   results and on what the caller says, global handling for things that can happen at any
   step (requests for a person, silence, out-of-scope questions, other languages, anger) as the
   documents define them. Use the call variables the documents name (`README`), never ask the
   caller for them, and bake them into tool bodies with `{{{{name}}}}`.
3. Put every decision that can be computed into code and routes, not into the prompt: which
   branch applies from the service's numbers, what to say for each currency/amount/date, dates
   relative to the call's own date-time variable (never the system clock), digit and amount
   wording. The phone agent runs on a small model (Freya LLM): give it short, exact
   instructions and the exact sentences to say, prepared by code where possible.
4. Tools: one api_call tool per operation you use, request body exactly as `openapi.json`
   (field names, nesting, envelope), URLs on the mock. Set `timeoutMs` above the slowest answer
   the documents mention. Handle every error status the contract lists the way the documents
   say. Never let the agent say a thing the services did not return, or that something was done
   when the call failed.
5. Check and test, in a loop:
   - `python ft.py dryrun` until the platform accepts the workflow;
   - `python ft.py save` to publish it;
   - `python ft.py chat '<variables JSON>' "<caller turn 1>" "<caller turn 2>" ...` to talk to
     it (it runs against the mock with the test customers from the documents; the first
     message is the caller's first turn). Test the main path and the edge cases you find in the
     test data and the example conversations: several cards, the error customers, the
     no-data/empty fields, the follow-up actions, transfers and exits. Turns are in the
     documents' language.
   Also test the requests that can come at any moment (a person/agent, someone else's card or
   account, payment, an out-of-scope question, a complaint) both as the caller's very first
   turn and right after an action has finished: each must take the exact path and sentence the
   documents give it. A specific rule always beats a generic one (a request for a person is
   never answered with the out-of-scope question), and a finished action never stops the
   agent from handling what the caller says next.
   - Then the real test, the way the bank scores: write `personas.json`, a list of simulated
     callers, one per example conversation in the documents plus the edge cases above (each
     `{{"name", "prompt", "successCriteria", "variables"}}`: `prompt` tells the simulated
     caller, in the call's language, who they are and what they say; `successCriteria` lists
     what the agent must do, from the documents; `variables` are the call variables). Run
     `python ft.py sim` (about 2 minutes, all callers at once). A caller with `turns=0` means
     the workflow does not even start in a real call: fix that first (chat can work when a
     real call does not). Read every FAIL reason and transcript, fix, save, run it again.
   Fix what fails, save again. Prefer a working, tested simpler agent to an untested big one.
   The last `python ft.py sim` must run on the saved version you finish with.
6. Write `call_variables.json`: one example of the call variables (a real test customer) the
   builder uses for its own smoke test.

Never hard-code test customers' answers into the workflow: the agent must read them from the
tools. End with one line: `DONE workflow.json steps=<n> tools=<n> tests=<what passed>`.
"""


def prepare(ws: Path, case, openapi: dict, mock_url: str | None, slot: str, kit: Path,
            state_file: Path, deadline_utc: str) -> str:
    """Fill the work folder; returns the task text."""
    if ws.exists():
        shutil.rmtree(ws, ignore_errors=True)
    (ws / "docs").mkdir(parents=True)
    (ws / "kit").mkdir()
    # no roles here: the agent judges each document by its name, place and content
    index = ["| file | original path in the case |", "|---|---|"]
    for i, d in enumerate(getattr(case, "documents", []) or []):
        fname = f"{i:02d}-{_safe(d.name)}.txt"
        text = d.text if not d.error else f"(unreadable: {d.error})"
        (ws / "docs" / fname).write_text(text, encoding="utf-8")
        index.append(f"| {fname} | {d.name} |")
    (ws / "docs" / "INDEX.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    root = getattr(case, "root", "")
    if root and Path(root).is_dir():
        shutil.copytree(root, ws / "case")
    (ws / "openapi.json").write_text(json.dumps(openapi, indent=1, ensure_ascii=False),
                                     encoding="utf-8")
    for f in ("workflow-format.md", "tools-and-code.md", "api.md"):
        shutil.copy(kit / "docs" / f, ws / "kit" / f)
    shutil.copy(kit / "examples" / "base-agent.json", ws / "kit" / "base-agent.json")
    (ws / "ft.py").write_text(
        HELPER.format(kit=str(kit), slot=slot, state=str(state_file)), encoding="utf-8")
    return TASK.format(deadline_utc=deadline_utc, now_utc=time.strftime("%H:%M", time.gmtime()),
                       mock_url=mock_url or "(see openapi.json)")


def _claude() -> str:
    exe = shutil.which("claude") or shutil.which("claude.exe") or shutil.which("claude.cmd")
    if not exe:
        raise RuntimeError("the coding agent (claude CLI) is not installed")
    return exe


def _brief(block: dict) -> str:
    """One trace line for a tool use of the agent."""
    name = block.get("name", "?")
    inp = block.get("input") or {}
    for k in ("command", "file_path", "pattern", "path", "description"):
        if inp.get(k):
            return f"{name}: {str(inp[k])[:160]}"
    return name


def run_agent(trace, ws: Path, task: str, session_log: Path, seconds: float) -> int:
    """Run the coding agent in the work folder until it ends or the clock runs out. Every
    event goes to session_log; the trace gets the model, each tool use and the cost."""
    # Scoped permissions: the agent edits files in its work folder and runs only the build's
    # Freya helper; any other shell command, web access or tool is refused (no prompt in -p).
    cmd = [_claude(), "-p", task, "--output-format", "stream-json", "--verbose",
           "--model", AGENT_MODEL, "--permission-mode", "acceptEdits",
           "--allowedTools", "Read", "Write", "Edit", "Glob", "Grep", "Bash(python ft.py:*)",
           "--disallowedTools", "WebFetch", "WebSearch", "Task",
           "--add-dir", str(ws)]
    trace.model(f"claude-code/{AGENT_MODEL}",
                "coding agent: read the case documents, write workflow.json, dry-run, save, "
                "chat-test against the mock, fix")
    session_log.parent.mkdir(parents=True, exist_ok=True)
    t_end = time.monotonic() + seconds
    with open(session_log, "w", encoding="utf-8") as log:
        p = subprocess.Popen(cmd, cwd=str(ws), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding="utf-8", errors="replace")
        assert p.stdout is not None
        for line in p.stdout:
            log.write(line)
            log.flush()
            try:
                ev = json.loads(line)
            except ValueError:
                trace.note("agent: " + line.strip()[:200])
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                trace.note(f"agent session {ev.get('session_id')} model={ev.get('model')}")
            elif ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_use":
                        trace.note("agent tool: " + _brief(b))
                    elif b.get("type") == "text" and b.get("text", "").strip():
                        trace.note("agent: " + b["text"].strip().replace("\n", " ")[:300])
            elif ev.get("type") == "result":
                cost = ev.get("total_cost_usd") or 0.0
                trace.cost_usd += float(cost or 0)
                trace.note(f"agent result: {ev.get('subtype')} turns={ev.get('num_turns')} "
                           f"cost_usd={cost} :: {str(ev.get('result') or '')[:300]}")
            if time.monotonic() > t_end:
                trace.note("agent: out of time, stopped")
                p.kill()
                break
        p.wait()
    return p.returncode or 0
