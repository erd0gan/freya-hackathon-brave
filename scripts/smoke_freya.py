"""Freya live smoke test (PLAN Faz 2, memo §67). Needs FREYA_API_KEY + FREYA_WORKSPACE_ID:

    uv run python scripts/smoke_freya.py

Exercises every endpoint the adapter uses, in order, and records what came back
so the VERIFY items in docs/RULES_MAPPING.md can become CONFIRMED:
default config → create agent (with one tool) → chat turn → call details (are
tool calls visible? message shape?) → 2 personas → simulation → iteration →
persona runs → call messages. Never run in CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from builder.adapters.freya.client import FreyaClient, FreyaError
from builder.config import load_config

OUT = Path(".builder/smoke_freya.json")


def step(report: dict[str, Any], name: str, fn: Any) -> Any:
    try:
        report[name] = {"ok": True, "result": fn()}
    except (FreyaError, KeyError, ValueError, IndexError) as exc:
        report[name] = {"ok": False, "error": str(exc)}
    print(f"[{'ok' if report[name]['ok'] else 'FAIL'}] {name}")
    return report[name].get("result")


def main() -> int:
    cfg = load_config()
    if not (cfg.freya_api_key and cfg.freya_workspace_id):
        print("FREYA_API_KEY and FREYA_WORKSPACE_ID are required")
        return 2
    c = FreyaClient(cfg.freya_api_key, cfg.freya_base_url, workspace_id=cfg.freya_workspace_id)
    rep: dict[str, Any] = {}
    base = step(rep, "1_default_config", c.default_config) or {}
    llm = base.setdefault("llmConfig", {})
    llm["systemPrompt"] = "You are a test agent. Use get_time when asked for the time."
    llm["tools"] = [
        {
            "type": "function",
            "function": {
                "name": "get_time",
                "handler": "get_time",
                "description": "Current time.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    agent = step(rep, "2_create_agent_with_tool", lambda: c.create_agent("smoke-agent", base)) or {}
    aid = str(agent.get("id", ""))
    chat = step(rep, "3_chat_turn", lambda: c.chat_message("What time is it?", agent_id=aid)) or {}
    call_id = str(chat.get("callId", ""))
    step(rep, "4_call_details_message_shape", lambda: c.get_call(call_id))
    step(rep, "4b_chat_end", lambda: c.chat_end(call_id))
    personas = (
        step(
            rep,
            "5_personas",
            lambda: c.create_personas(
                [
                    {
                        "name": f"smoke-{i}",
                        "prompt": "You want to know the current time.",
                        "successCriteria": "The agent tells the time.",
                        "evaluationPrompt": "PASS if the agent told the time.",
                    }
                    for i in range(2)
                ]
            ),
        )
        or []
    )
    sim = (
        step(
            rep,
            "6_simulation",
            lambda: c.create_simulation(
                {
                    "name": "smoke",
                    "agentId": aid,
                    "selectedPersonaIds": [p["id"] for p in personas],
                    "mode": "chat",
                    "runsPerPersona": 1,
                    "start": True,
                }
            ),
        )
        or {}
    )
    sid = str(sim.get("id", ""))
    step(rep, "7_wait", lambda: c.wait_simulation(sid, timeout_s=900))
    it = step(rep, "8_latest_iteration", lambda: c.latest_iteration(sid)) or {}
    runs = (
        step(rep, "9_persona_runs", lambda: c.persona_runs(str(it.get("agentVersionId", "")))) or []
    )
    if runs and runs[0].get("runId"):
        step(rep, "10_run_call_messages", lambda: c.get_call(str(runs[0]["runId"])))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rep, indent=2, default=str))
    print(
        f"report: {OUT}. Check: tool-call message shape (step 4/10), endedReason values, "
        "and whether pre_process_code can reject (manual)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
