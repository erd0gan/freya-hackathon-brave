"""Platform gates: deploy a small probe workflow and talk to it over /chat/message.

    FREYA_API_KEY=... FREYA_BANK_TEAM_KEY=... python3 builder/tools/gate_probe.py [--amount plain|json]

What one run answers (docs/GENERATOR.md, platform assumptions):
  G6  a code node that embeds the full node-code library (~57 KB) is accepted and runs
  G0  the chat route runs a passthrough code node that is the initial node
  G2  an extracted variable keeps its value when a later turn extracts nothing
  G1  on the capped turn, does a matching transition win over max_turn_transition?
  G4  code variables bake into an api_request body (number type: plain vs :json)
  G5  a 500 from an api_request node raises <node>_failure_count / status_code
  G3  the end_of_call close_call bakes {{final_outcome}} written by code
The customer is a PUB-10 practice session: its first create_promise call fails with 500.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "kit"))
sys.path.insert(0, str(ROOT / "kit" / "tools"))

import freya  # noqa: E402
from fb.nodecode import bodies  # noqa: E402
from fb.nodecode.embed import snippet  # noqa: E402
from fb.openapi_tools import freya_tool, parse_openapi  # noqa: E402
from score import http  # noqa: E402

sys.path.insert(0, str(ROOT / "tests" / "hack"))
from test_engine import CFG_A  # noqa: E402

ALWAYS = {"operator": "always"}


def grp(*conds: dict[str, Any], logic: str = "or") -> dict[str, Any]:
    if logic == "or":
        return {"logic": "or", "conditions": list(conds)}
    return {"logic": "or", "conditions": [{"logic": "and", "conditions": list(conds)}]}


def code_node(name: str, src: str, nxt: str) -> dict[str, Any]:
    return {
        "name": name,
        "node_type": "code",
        "passthrough": True,
        "passthrough_default_transition": nxt,
        "on_enter": {"type": "silent"},
        "pre_actions": [
            {
                "type": "function",
                "function": {
                    "name": "code_exec",
                    "handler": "__function__:code_exec",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "code": {"type": "string", "default": src},
                            "timeout_ms": {"type": "number", "default": 20000},
                        },
                    },
                },
            }
        ],
        "transitions": [{"target_node": nxt, "description": "always", "condition": ALWAYS}],
    }


def conv(name: str, line: str, task: str, transitions: list[dict[str, Any]], **kw: Any):
    n = {
        "name": name,
        "node_type": "conversation",
        "on_enter": {"type": "tts", "text": line},
        "task_message": task + " Do not call any tool.",
        "transitions": transitions,
    }
    n.update(kw)
    return n


def probe_workflow(tools: dict[str, Any], amount_mode: str) -> dict[str, Any]:
    setup_body = (
        bodies.SETUP
        + "vars['rec_amount'] = 365.0\n"
        + "vars['rec_product_id'] = 'p1'\n"
        + "vars['rec_date'] = vars.get('attempt1_date') or ''\n"
        + "vars['final_outcome'] = 'promise'\n"
        + "vars['final_reason'] = ''\n"
        + "vars['probe_setup'] = 'ran'\n"
        + "vars['probe_try'] = '0'\n"
    )
    amount_ref = "rec_amount:json" if amount_mode == "json" else "rec_amount"
    record_fn = freya_tool(
        tools["create_promise"],
        "Record the promise.",
        bake={
            "customer_id": "customer_id",
            "product_id": "rec_product_id",
            "amount": amount_ref,
            "date": "rec_date",
        },
    )
    close_fn = freya_tool(
        tools["close_call"],
        "Record how the call ended.",
        bake={"customer_id": "customer_id", "outcome": "final_outcome", "reason": "final_reason"},
        pre_code=(
            "try:\n"
            "    b = config.get('body')\n"
            "    if isinstance(b, dict) and (b.get('outcome') in ('promise', 'paid')"
            " or not str(b.get('reason') or '').strip()"
            " or str(b.get('reason')).startswith('{')):\n"
            "        b.pop('reason', None)\n"
            "except Exception:\n"
            "    pass\n"
        ),
        extra_config={"alwaysRunsAt": ["end_of_call"]},
    )
    color = {
        "name": "color",
        "type": "string",
        "enum_values": ["red", "blue", "green"],
        "description": "The colour the user named. Empty if none.",
        "min_confidence": 0.5,
    }
    animal = {
        "name": "animal",
        "type": "string",
        "enum_values": ["cat", "dog", "bird"],
        "description": "The animal the user named in their last message. Empty if none.",
        "min_confidence": 0.5,
    }
    nodes = {
        "setup": code_node("setup", snippet(setup_body, CFG_A), "ask1"),
        "ask1": conv(
            "ask1",
            "Probe one. Name a colour: red, blue or green.",
            "Ask the user to name a colour: red, blue or green.",
            [
                {
                    "target_node": "ask2",
                    "description": "colour given",
                    "condition": grp({"variable": "color", "operator": "is_set"}),
                },
                {"target_node": "ask1", "description": "stay", "condition": ALWAYS},
            ],
            extraction_schema=[color],
            max_node_turns=4,
            max_turn_transition="capped",
        ),
        "ask2": conv(
            "ask2",
            "Probe two. Name an animal: cat, dog or bird.",
            "Ask the user to name an animal: cat, dog or bird.",
            [
                {
                    "target_node": "record",
                    "description": "the animal is cat",
                    "condition": grp(
                        {"variable": "animal", "operator": "equals", "operand": "cat"}
                    ),
                },
                {"target_node": "ask2", "description": "stay", "condition": ALWAYS},
            ],
            extraction_schema=[color, animal],
            max_node_turns=2,
            max_turn_transition="capped",
        ),
        "capped": code_node("capped", "vars['probe_capped'] = 'yes'\n", "record"),
        "record": {
            "name": "record",
            "node_type": "api_request",
            "passthrough": True,
            "passthrough_default_transition": "done",
            "on_enter": {"type": "silent"},
            "pre_actions": [record_fn],
            "response_mapping": [{"path": "data.ok", "variable": "rec_ok"}],
            "transitions": [
                {
                    "target_node": "retry_count",
                    "description": "failed, seen by the counter",
                    "condition": grp(
                        {
                            "variable": "record_failure_count",
                            "operator": "greater_than",
                            "operand": 0,
                        },
                        {"variable": "probe_try", "operator": "equals", "operand": "0"},
                        logic="and",
                    ),
                },
                {
                    "target_node": "retry_status",
                    "description": "failed, seen by the status code",
                    "condition": grp(
                        {"variable": "record_status_code", "operator": "equals", "operand": "500"},
                        {"variable": "probe_try", "operator": "equals", "operand": "0"},
                        logic="and",
                    ),
                },
                {"target_node": "done", "description": "always", "condition": ALWAYS},
            ],
        },
        "retry_count": code_node(
            "retry_count", "vars['probe_try'] = '1'\nvars['probe_fail_seen'] = 'count'\n", "record"
        ),
        "retry_status": code_node(
            "retry_status",
            "vars['probe_try'] = '1'\nvars['probe_fail_seen'] = 'status'\n",
            "record",
        ),
        "done": conv(
            "done",
            "Probe done. Say goodbye.",
            "Say one short sentence.",
            [{"target_node": "bye", "description": "always", "condition": ALWAYS}],
        ),
        "bye": {
            "name": "bye",
            "node_type": "end_call",
            "on_enter": {"type": "silent"},
            "disable_inactivity_monitor": True,
            "transitions": [],
            "terminal": {
                "type": "function",
                "function": {
                    "name": "probe_end",
                    "handler": "__function__:end_call",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "message_mode": {"type": "string", "default": "custom"},
                            "spoken_message": {"type": "string", "default": "Goodbye."},
                        },
                    },
                },
            },
        },
    }
    return {
        "initial_node": "setup",
        "system_prompt": "You are a test assistant. Speak English. One short sentence per turn.",
        "nodes": nodes,
        "global_nodes": {},
        "tools": [close_fn],
    }


def chat(wf_id: str, msg: str, cv: dict[str, Any], call_id: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {"workflowId": wf_id, "message": msg, "customerVariables": cv}
    if call_id:
        body["callId"] = call_id
    t0 = time.time()
    try:
        r = freya.api("POST", "/api/v2/chat/message", body)
    except RuntimeError as e:
        return {"error": str(e)[:400], "seconds": round(time.time() - t0, 1)}
    r["seconds"] = round(time.time() - t0, 1)
    return r


KEYS = (
    "probe_setup",
    "calendar_state",
    "attempt1_date",
    "code_error",
    "color",
    "animal",
    "probe_capped",
    "rec_amount",
    "rec_date",
    "record_failure_count",
    "record_success_count",
    "record_status_code",
    "rec_ok",
    "probe_try",
    "probe_fail_seen",
    "final_outcome",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amount", choices=["plain", "json"], default="plain")
    ap.add_argument("--case", default="PUB-10")
    ap.add_argument("--save", help="write the call json here")
    ap.add_argument(
        "--setup-after", action="store_true", help="initial node is ask1, setup runs after it"
    )
    a = ap.parse_args()
    save = Path(a.save).resolve() if a.save else None
    tools = parse_openapi(json.loads((ROOT / "kit" / "tools" / "openapi.json").read_text()))
    wf = probe_workflow(tools, a.amount)
    if a.setup_after:
        n = wf["nodes"]
        wf["initial_node"] = "ask1"
        n["ask1"]["transitions"][0]["target_node"] = "setup"
        n["setup"]["passthrough_default_transition"] = "ask2"
        n["setup"]["transitions"][0]["target_node"] = "ask2"
    size = len(json.dumps(wf))
    print(f"workflow json: {size} bytes")
    try:
        freya.validate_workflow("probe", wf)
        print("dry run: ok")
    except RuntimeError as e:
        print("dry run FAILED:", str(e)[:1500])
        return 1
    os.chdir(ROOT / "kit")
    saved = freya.save_workflow("probe", wf)
    print("saved:", saved)
    stored = freya.api(
        "GET", f"/api/v2/workflow/{saved['workflow_id']}", workspaceId=freya.workspace_id()
    )
    stored_wf = stored["workflow"]["workflow"]
    code = stored_wf["nodes"]["setup"]["pre_actions"][0]["function"]["parameters"]["properties"]
    print("stored setup code chars:", len(code["code"].get("default", "")))
    sess = http("/v1/practice/sessions", {"case": a.case})
    cv = sess["variables"]
    print("session:", {k: cv[k] for k in ("customer_id", "now")})
    call_id = None
    for msg in ["Hello?", "Red.", "Dog.", "Cat.", "Bye."]:
        r = chat(saved["workflow_id"], msg, cv, call_id)
        call_id = call_id or r.get("callId")
        print(
            f"USER {msg!r} -> {r.get('seconds')}s AGENT {str(r.get('response') or r.get('error'))[:200]!r}"
        )
    if call_id:
        try:
            freya.api("POST", "/api/v2/chat/end", {"callId": call_id})
        except RuntimeError as e:
            print("chat/end:", str(e)[:200])
    time.sleep(8)
    led = http("/v1/practice/ledger/" + cv["customer_id"])
    print("LEDGER calls:")
    for c in led.get("calls", []):
        print(
            "   ",
            c.get("tool"),
            json.dumps(c.get("args") or c.get("body"))[:200],
            "->",
            json.dumps(c.get("result"))[:160],
        )
    print(
        "LEDGER promises:", led.get("promises"), "outcome:", led.get("outcome"), led.get("reason")
    )
    if not call_id:
        return 1
    call = freya.get_call(call_id)
    if save:
        save.write_text(json.dumps(call, indent=1))
    table: dict[str, Any] = {}
    print("EVENTS:")
    for m in call.get("messages", []):
        if m.get("role") != "system":
            snap = (m.get("workflowSnapshot") or {}).get("variableTable") or {}
            table.update(
                {k: (v.get("value") if isinstance(v, dict) else v) for k, v in snap.items()}
            )
            continue
        try:
            ev = json.loads(m.get("message") or "{}")
        except json.JSONDecodeError:
            continue
        kind = ev.get("event")
        if kind == "route_evaluated":
            vt = ev.get("variable_table") or {}
            table.update({k: (v.get("value") if isinstance(v, dict) else v) for k, v in vt.items()})
            print(
                f"   route {ev.get('from_node')} -> {ev.get('target')} matched={ev.get('matched')} reason={str(ev.get('reason'))[:120]}"
            )
        elif kind == "node_enter":
            print(f"   enter {ev.get('node')} from {ev.get('from_node')} ({ev.get('reason')})")
        elif kind in ("function_call_start", "function_call_complete", "error"):
            detail = (
                ev.get("args")
                if kind == "function_call_start"
                else ev.get("result") or ev.get("message")
            )
            print(f"   {kind} {ev.get('function')} {json.dumps(detail)[:300]}")
        else:
            print(f"   {kind}")
    print("VARIABLES:")
    for k in KEYS:
        print(f"   {k} = {table.get(k)!r}")
    print("endedReason:", call.get("endedReason"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
