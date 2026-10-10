"""Compare the local bank (fb.harness.bank) with the real practice bank. No model calls.

    FREYA_BANK_TEAM_KEY=... python3 builder/tools/bank_parity.py [--only PUB-01,PUB-10]

For each public case it opens one real practice session and one local session, sends the same
tool calls to both (probes for the identity rules, then the golden sequence) and prints every
difference in HTTP status, response fields and the final ledger. Exit code 1 on a difference.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))

from fb.harness.bank import Bank  # noqa: E402
from fb.harness.scoring import golden_calls, score_ledger  # noqa: E402
from fb.openapi_tools import parse_openapi  # noqa: E402

HOST = os.environ.get("FREYA_BANK_HOST", "https://freya-bank-mock.freyavoice.workers.dev")
UA = "Mozilla/5.0 (agent-builder parity)"
KEYS = ("ok", "error", "verified", "wrong_answers_left", "all_verified", "hint")


def http(url: str, body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    h = {
        "user-agent": UA,
        "content-type": "application/json",
        "x-team-key": os.environ["FREYA_BANK_TEAM_KEY"],
    }
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:200].decode(errors="replace")}


def _num(x: Any) -> Any:
    return float(x) if isinstance(x, (int, float)) else x


def _same_keys(real: Any, local: Any) -> Any:
    """The real ledger rows carry extra ids (promise_id, type) and integer amounts; compare
    only the fields the local ledger (and score.py) has."""
    if not (isinstance(real, list) and isinstance(local, list)) or len(real) != len(local):
        return real
    out = []
    for r, lo in zip(real, local, strict=True):
        if not (isinstance(r, dict) and isinstance(lo, dict)):
            return real
        out.append({k: _num(r.get(k)) if isinstance(lo.get(k), float) else r.get(k) for k in lo})
    return out


def probes(case: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Identity edge cases first: they do not change what the golden sequence needs."""
    ident = case["identity"]
    out: list[tuple[str, dict[str, Any]]] = [("get_account", {})]
    if case["expected"]["outcome"] in ("not_reached", "not_verified"):
        return []
    dob = ident.get("date_of_birth")
    if dob:
        y, m, d = dob.split("-")
        out.append(("verify_identity", {"field": "date_of_birth", "answer": f"{int(d)} March {y}"}))
        if int(d) <= 12 and d != m:
            out.append(("verify_identity", {"field": "date_of_birth", "answer": f"{y}-{d}-{m}"}))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only")
    a = ap.parse_args()
    cases = json.loads((ROOT / "kit" / "public-test-set.json").read_text())
    if a.only:
        cases = [c for c in cases if c["id"] in a.only.split(",")]
    tools = parse_openapi(json.loads((ROOT / "kit" / "tools" / "openapi.json").read_text()))
    bank = Bank()
    diffs = 0
    for case in cases:
        st, sess = http(HOST + "/v1/practice/sessions", {"case": case["id"]})
        if st != 200 or "variables" not in sess:
            print(f"{case['id']}: cannot open a session: {st} {sess}")
            diffs += 1
            continue
        rv = sess["variables"]
        lv = bank.open_session(case)
        for k in ("first_name", "last_name", "title", "now"):
            if str(rv.get(k)) != str(lv.get(k)):
                print(f"{case['id']}: variable {k}: real {rv.get(k)!r} local {lv.get(k)!r}")
                diffs += 1
        rc, lc = rv["customer_id"], lv["customer_id"]
        for tool, body in probes(case) + golden_calls(case, "x"):
            body = {k: v for k, v in body.items() if k != "customer_id"}
            for attempt in range(2):
                rs, rb = http(tools[tool].url, {**body, "customer_id": rc})
                ls, lb = bank.call(lc, tool, {**body, "customer_id": lc})
                d = [k for k in KEYS if rb.get(k) != lb.get(k)]
                if rs != ls:
                    d.append("status")
                if d:
                    diffs += 1
                    print(f"{case['id']} {tool} {json.dumps(body)}")
                    print(f"   real  {rs} {rb}\n   local {ls} {lb}")
                if rs < 500 or attempt:
                    break
        rl = http(HOST + "/v1/practice/ledger/" + rc)[1]
        ll = bank.ledger(lc)
        rscore, lscore = score_ledger(case, rl), score_ledger(case, ll)
        for k in ("outcome", "reason", "promises", "notes", "callbacks", "transfers"):
            if json.dumps(_same_keys(rl.get(k), ll.get(k)), sort_keys=True) != json.dumps(
                ll.get(k), sort_keys=True
            ):
                diffs += 1
                print(f"{case['id']} ledger.{k}\n   real  {rl.get(k)}\n   local {ll.get(k)}")
        print(f"{case['id']}: real score {rscore['pass']} local score {lscore['pass']}")
        if set(rl) - set(ll):
            print(f"{case['id']}: real ledger has extra keys {sorted(set(rl) - set(ll))}")
    print(f"{diffs} differences")
    return 1 if diffs else 0


if __name__ == "__main__":
    sys.exit(main())
