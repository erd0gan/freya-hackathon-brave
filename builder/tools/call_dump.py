"""Dump simulated calls with every step: messages, node entries, extractions, routes, tools.

    python builder/tools/call_dump.py <results.json> [<results.json> ...] [--out docs/research-talkative]
    python builder/tools/call_dump.py --sim <simulation_id> [--out ...]

For each call: <out>/raw/<tag>-<case>.json (the call as the platform returns it) and
<out>/timeline/<tag>-<case>.txt (one line per event). <out>/calls.jsonl gets one summary line
per call (case, verdict, end reason, cut customer messages, re-asks) for counting.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "builder"))
import build  # noqa: E402,F401  (loads .env)
import freya  # noqa: E402

# A customer turn that stops without an end mark, or mid-word, was cut by the simulator.
END_MARK = re.compile(r"[.!?…\"')\]]\s*$")


def cut(text: str) -> bool:
    t = text.strip()
    return bool(t) and not END_MARK.search(t)


def ev(m: dict) -> dict | None:
    try:
        return json.loads(m["message"])
    except (ValueError, TypeError):
        return None


def short(v, n=140) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def timeline(call: dict) -> list[str]:
    out = []
    for m in call.get("messages", []):
        t = f"{(m.get('secondsFromStart') or 0):6.1f}"
        node = m.get("nodeId") or ""
        if m["role"] in ("user", "assistant"):
            who = "CUSTOMER" if m["role"] == "user" else "AGENT   "
            flag = "  [CUT]" if m["role"] == "user" and cut(m["message"]) else ""
            out.append(f"{t} {who} [{node}] {m['message']}{flag}")
            continue
        e = ev(m)
        if not e:
            out.append(f"{t} {m['role']:<8} {short(m.get('message'), 300)}")
            continue
        k = e.get("event")
        if k == "node_enter":
            out.append(f"{t}   > enter {e.get('node')} (from {e.get('from_node')})")
        elif k == "variable_extracted":
            out.append(
                f"{t}   = {e.get('name')}={short(e.get('value'), 80)} conf={e.get('confidence')} "
                f"src={e.get('source')}@{e.get('source_node')}"
            )
        elif k == "route_evaluated":
            hit = [b for b in e.get("branches", []) if b.get("result")]
            out.append(
                f"{t}   ? route {e.get('from_node')} matched={e.get('matched')} -> {e.get('target')} "
                f"[{'; '.join(b.get('description', '') for b in hit)}]"
            )
        elif k and k.startswith("function_call"):
            body = {x: e[x] for x in e if x not in ("event",)}
            out.append(f"{t}   * {k} {short(body, 400)}")
        else:
            out.append(f"{t}   . {k} {short({x: e[x] for x in e if x != 'event'}, 300)}")
    return out


def summary(tag: str, row: dict, call: dict) -> dict:
    ms = call.get("messages", [])
    users = [m["message"] for m in ms if m["role"] == "user"]
    agents = [m["message"] for m in ms if m["role"] == "assistant"]
    reask = sum("didn't catch" in a or "did not catch" in a for a in agents)
    nodes = [
        ev(m).get("node")
        for m in ms
        if m["role"] == "system" and (ev(m) or {}).get("event") == "node_enter"
    ]
    return {
        "tag": tag,
        "case": row.get("id"),
        "pass": row.get("pass"),
        "failed": row.get("failed"),
        "ended": call.get("endedReason"),
        "duration": call.get("duration"),
        "customer_turns": len(users),
        "cut_turns": sum(cut(u) for u in users),
        "cut_texts": [u for u in users if cut(u)],
        "agent_reasks": reask,
        "last_node": nodes[-1] if nodes else None,
        "last_customer": users[-1] if users else None,
        "last_agent": agents[-1] if agents else None,
        "call_id": call.get("id"),
    }


def dump(tag: str, rows: list[dict], out: Path) -> None:
    (out / "raw").mkdir(parents=True, exist_ok=True)
    (out / "timeline").mkdir(parents=True, exist_ok=True)
    with open(out / "calls.jsonl", "a", encoding="utf-8") as f:
        for row in rows:
            if not row.get("call_id"):
                continue
            call = freya.get_call(row["call_id"])
            name = f"{tag}-{row['id']}"
            (out / "raw" / f"{name}.json").write_text(
                json.dumps(call, indent=1, ensure_ascii=False), encoding="utf-8"
            )
            head = [
                f"# {name} pass={row.get('pass')} ended={call.get('endedReason')} "
                f"duration={call.get('duration')}s call={call.get('id')}",
                *[f"# failed: {x}" for x in row.get("failed") or []],
                f"# judge: {short((call.get('analysis') or {}).get('successEvaluation'), 1200)}",
                "",
            ]
            (out / "timeline" / f"{name}.txt").write_text(
                "\n".join(head + timeline(call)) + "\n", encoding="utf-8"
            )
            s = summary(tag, row, call)
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
            print(
                f"{name}: pass={s['pass']} ended={s['ended']} cut={s['cut_turns']}/{s['customer_turns']} "
                f"reasks={s['agent_reasks']} last={s['last_node']}"
            )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="*")
    ap.add_argument("--sim")
    ap.add_argument("--out", default=str(ROOT / "docs" / "research-talkative"))
    a = ap.parse_args()
    out = Path(a.out)
    for p in a.results:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        dump(Path(p).stem, d["rows"], out)
    if a.sim:
        its = freya.api("GET", f"/api/v2/simulations/{a.sim}/iterations")
        its = its.get("iterations") or its.get("items") or its
        runs = freya.get_iteration(a.sim, its[0]["id"])["runs"]
        rows = [
            {
                "id": (r.get("personaName") or r.get("personaId") or "?").split(" ")[0],
                "call_id": r.get("callId"),
                "pass": r.get("outcome") == "pass",
                "failed": [],
            }
            for r in runs
        ]
        dump("sim-" + a.sim[:8], rows, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
