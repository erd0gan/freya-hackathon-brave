"""Ledger scoring with the kit's own score.py, plus the case-level k/n report (a case counts
only when every repetition passes), plus a golden tool sequence per case for self-checks.
Every replayed case is also reviewed by fb.harness.review (brief rules on the tool calls)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from fb.harness.bank import Bank
from fb.harness.review import as_dicts, review_call

KIT_SCORE = Path(__file__).resolve().parents[3] / "kit" / "tools" / "score.py"
NOT_REACHED = ("not_reached", "no_contact", "unreached")


def _kit_score() -> Any:
    spec = importlib.util.spec_from_file_location("kit_score", KIT_SCORE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.score_ledger


score_ledger = _kit_score()


def case_report(
    results: dict[str, list[bool]], findings: dict[str, list[Any]] | None = None
) -> dict[str, Any]:
    """results: case id -> pass per repetition. A case passes only with all reps passing.
    findings (optional): case id -> review findings of its runs (Finding or dict); a critical
    finding fails the case too."""
    rows = []
    for cid, passes in results.items():
        found = [f if isinstance(f, dict) else vars(f) for f in (findings or {}).get(cid, [])]
        crit = sum(f.get("severity") == "critical" for f in found)
        rows.append(
            {
                "id": cid,
                "passes": sum(passes),
                "runs": len(passes),
                "critical": crit,
                "findings": found,
                "pass": bool(passes) and all(passes) and not crit,
            }
        )
    n = sum(r["pass"] for r in rows)
    return {"passed": n, "total": len(rows), "rows": rows}


def format_report(rep: dict[str, Any]) -> str:
    lines = []
    for r in rep["rows"]:
        line = f"{'PASS' if r['pass'] else 'FAIL'} {r['id']} {r['passes']}/{r['runs']}"
        if r.get("critical"):
            line += f" ({r['critical']} critical review findings)"
        lines.append(line)
        for f in r.get("findings") or []:
            lines.append(f"    {f['severity']} {f['rule']} turn {f['turn']}: {f['evidence']}")
    lines.append(f"{rep['passed']} / {rep['total']} cases pass every run")
    return "\n".join(lines)


def golden_calls(case: dict[str, Any], customer_id: str) -> list[tuple[str, dict[str, Any]]]:
    """The tool calls an ideal agent makes for this case, derived from `expected`. Used to
    check that the local bank and the scorer agree with each other (and later with the
    real mock). Covers the ledger, not the conversation."""
    exp = case["expected"]
    cid = {"customer_id": customer_id}
    calls: list[tuple[str, dict[str, Any]]] = []
    if exp["outcome"] in NOT_REACHED:
        for cb in exp["callbacks"]:
            calls.append(("schedule_callback", {**cid, "date": cb["date"], "time": cb["time"]}))
    elif exp["outcome"] == "not_verified" and exp.get("reason") == "failed":
        field, truth = next(iter(case["identity"].items()))
        wrong = "1900-01-01" if "-" in str(truth) else "0" * len(str(truth))
        for _ in range(3):
            calls.append(("verify_identity", {**cid, "field": field, "answer": wrong}))
    elif exp["outcome"] == "not_verified":
        pass
    else:
        for f, v in case["identity"].items():
            calls.append(("verify_identity", {**cid, "field": f, "answer": v}))
        calls.append(("get_account", dict(cid)))
        for p in exp["promises"]:
            calls.append(("create_promise", {**cid, **p}))
        for n in exp["notes"]:
            calls.append(
                ("add_note", {**cid, "product_id": n.get("product_id"), "reason": n["reason"]})
            )
        for cb in exp["callbacks"]:
            calls.append(("schedule_callback", {**cid, "date": cb["date"], "time": cb["time"]}))
        for t in exp["transfers"]:
            calls.append(("transfer_to_agent", {**cid, "queue": t["queue"]}))
    close = {**cid, "outcome": exp["outcome"]}
    if exp.get("reason"):
        close["reason"] = exp["reason"]
    calls.append(("close_call", close))
    return calls


def replay(
    bank: Bank,
    case: dict[str, Any],
    calls: list[tuple[str, dict[str, Any]]] | None = None,
    review_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Open a session, run the calls (golden by default), score the ledger and review its
    calls (`review`: findings as dicts; review_cfg as in fb.harness.review)."""
    v = bank.open_session(case)
    cid = v["customer_id"]
    for tool, body in calls if calls is not None else golden_calls(case, cid):
        status, _ = bank.call(cid, tool, {**body, "customer_id": cid})
        if status >= 500:  # briefs: a failed write is tried once more
            bank.call(cid, tool, {**body, "customer_id": cid})
    led = bank.ledger(cid)
    review = as_dicts(review_call([], led["calls"], review_cfg))
    return {"customer_id": cid, "ledger": led, "review": review, **score_ledger(case, led)}
