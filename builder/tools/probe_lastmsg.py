"""Probe: can a code node see the customer's raw last message?

    python builder/tools/probe_lastmsg.py

Deploys a 4-node workflow in its own slot ("probe-lastmsg") and talks to it over /chat/message.
Answers three questions (no bank calls):
  L1  does `vars` hold any system / conversation key a code node can read?
  L2  is a {{system.conversation.last-N}} placeholder inside code_exec source filled in?
  L3  is the same placeholder filled in a tts line?
A cut message ("...because my") is sent so the raw text, punctuation included, can be compared.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "builder"))
import build  # noqa: E402,F401  (loads .env)
import freya  # noqa: E402

ALWAYS = {"operator": "always"}
OPEN, CLOSE = "{" * 2, "}" * 2


def code_node(name: str, src: str, nxt: str) -> dict:
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


PH1 = OPEN + "system.conversation.last-1" + CLOSE
PH5 = OPEN + "system.conversation.last-5" + CLOSE

SRC = (
    "import json\n"
    "keys = sorted(str(k) for k in vars.keys())\n"
    "vars['probe_keys'] = ','.join(k for k in keys if 'system' in k or 'conv' in k or 'message' in k or 'user' in k)[:300]\n"
    "vars['probe_nkeys'] = str(len(keys))\n"
    "raw1 = r'''" + PH1 + "'''\n"
    "raw5 = r'''" + PH5 + "'''\n"
    "vars['probe_raw1'] = json.dumps(raw1)[:400]\n"
    "vars['probe_raw5_len'] = str(len(raw5))\n"
    "c = vars.get('system.conversation')\n"
    "vars['probe_ctype'] = type(c).__name__\n"
    "vars['probe_ctail'] = json.dumps(c)[-260:]\n"
)


def workflow() -> dict:
    nodes = {
        "ask": {
            "name": "ask",
            "node_type": "conversation",
            "on_enter": {"type": "tts", "text": "Probe. Say anything."},
            "task_message": "Say one short sentence. Do not call any tool.",
            "transitions": [{"target_node": "peek", "description": "always", "condition": ALWAYS}],
        },
        "peek": code_node("peek", SRC, "show"),
        "show": {
            "name": "show",
            "node_type": "conversation",
            "on_enter": {
                "type": "tts",
                "text": "K="
                + OPEN
                + "probe_keys"
                + CLOSE
                + " N="
                + OPEN
                + "probe_nkeys"
                + CLOSE
                + " R1="
                + OPEN
                + "probe_raw1"
                + CLOSE
                + " L5="
                + OPEN
                + "probe_raw5_len"
                + CLOSE
                + " T1="
                + PH1
                + " CT="
                + OPEN
                + "probe_ctype"
                + CLOSE
                + " TAIL="
                + OPEN
                + "probe_ctail"
                + CLOSE,
            },
            "task_message": "Say one short sentence. Do not call any tool.",
            "transitions": [{"target_node": "show", "description": "always", "condition": ALWAYS}],
        },
    }
    return {
        "initial_node": "ask",
        "system_prompt": "You are a test assistant. Speak English. One short sentence per turn.",
        "nodes": nodes,
        "global_nodes": {},
        "tools": [],
    }


def main() -> int:
    wf = workflow()
    freya.validate_workflow("probe-lastmsg", wf)
    print("dry run: ok")
    saved = freya.save_workflow("probe-lastmsg", wf, state_file=str(ROOT / ".freya-agents.json"))
    print("saved:", saved)
    cv = {"customer_id": "c_probe", "now": time.strftime("%Y-%m-%dT%H:%M")}
    call_id = None
    for msg in ["Hello?", "I have been so busy today because my"]:
        body = {"workflowId": saved["workflow_id"], "message": msg, "customerVariables": cv}
        if call_id:
            body["callId"] = call_id
        r = freya.api("POST", "/api/v2/chat/message", body)
        call_id = call_id or r.get("callId")
        print(f"USER {msg!r}\n  AGENT {r.get('response')!r}")
    if call_id:
        try:
            freya.api("POST", "/api/v2/chat/end", {"callId": call_id})
        except RuntimeError:
            pass
        time.sleep(5)
        call = freya.get_call(call_id)
        out = ROOT / "docs" / "research-talkative" / "raw" / "probe-lastmsg.json"
        out.write_text(json.dumps(call, indent=1, ensure_ascii=False), encoding="utf-8")
        for m in call.get("messages", []):
            if m["role"] == "system" and "code_exec" in m["message"] and "complete" in m["message"]:
                print("  code_exec:", m["message"][:400])
        snap = [
            m.get("workflowSnapshot") for m in call.get("messages", []) if m.get("workflowSnapshot")
        ]
        if snap:
            vt = snap[-1].get("variableTable") or {}
            print(
                "  probe vars:",
                {k: (v or {}).get("value") for k, v in vt.items() if k.startswith("probe")},
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
