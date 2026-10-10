"""Dev tool: talk to a saved workflow turn by turn as one practice customer, then print the
ledger and the route/tool events.

    python3 builder/tools/chat_case.py <slot> <case_id> "msg 1" "msg 2" ... [--events]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "kit"))
sys.path.insert(0, str(ROOT / "kit" / "tools"))

import freya  # noqa: E402
from score import http, score_ledger  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("slot")
    ap.add_argument("case")
    ap.add_argument("msgs", nargs="+")
    ap.add_argument("--events", action="store_true")
    ap.add_argument("--dump", help="write the call json here")
    a = ap.parse_args()
    state = json.loads((ROOT / "kit" / ".freya-agents.json").read_text())
    wf_id = state[a.slot]["workflow_id"]
    cases = {c["id"]: c for c in json.loads((ROOT / "kit" / "public-test-set.json").read_text())}
    sess = http("/v1/practice/sessions", {"case": a.case})
    cv = sess["variables"]
    call_id = None
    for msg in ["Hello?"] + a.msgs:
        body = {"workflowId": wf_id, "message": msg, "customerVariables": cv}
        if call_id:
            body["callId"] = call_id
        t0 = time.time()
        try:
            r = freya.api("POST", "/api/v2/chat/message", body)
            call_id = call_id or r.get("callId")
            out = r.get("response")
        except RuntimeError as e:
            out = "ERROR " + str(e)[:200]
        print(f"USER  {msg}\nAGENT ({time.time() - t0:.1f}s) {out}\n")
    if call_id:
        try:
            freya.api("POST", "/api/v2/chat/end", {"callId": call_id})
        except RuntimeError:
            pass
    time.sleep(6)
    led = http("/v1/practice/ledger/" + cv["customer_id"])
    print("TOOLS:", " > ".join(c["tool"] for c in led.get("calls", [])))
    for c in led.get("calls", []):
        print(
            "   ",
            c["tool"],
            json.dumps(c.get("args") or c.get("body") or {})[:160],
            "->",
            json.dumps(c.get("result"))[:120],
        )
    print("SCORE:", json.dumps(score_ledger(cases[a.case], led)))
    if (a.events or a.dump) and call_id:
        call = freya.get_call(call_id)
        if a.dump:
            Path(a.dump).resolve().write_text(json.dumps(call, indent=1))
        for m in call.get("messages", []):
            if m.get("role") != "system":
                continue
            ev = json.loads(m.get("message") or "{}")
            k = ev.get("event")
            if k == "node_enter":
                print("  enter", ev.get("node"), "<-", ev.get("from_node"))
            elif k == "error":
                print("  ERROR", json.dumps(ev)[:300])
            elif k == "function_call_start" and ev.get("function") != "code_exec":
                print("  call", ev.get("function"), json.dumps(ev.get("args"))[:250])
            elif k == "variable_extracted":
                print("  extracted", json.dumps({x: ev[x] for x in ev if x != "event"})[:250])
    return 0


if __name__ == "__main__":
    sys.exit(main())
