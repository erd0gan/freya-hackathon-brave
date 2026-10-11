"""Run the generated code-node bodies against the local bank, with the customer's replies
given as the extraction values the platform would produce. No model calls.

This checks the deterministic half of the agent (calendar, negotiation, recording, notes,
outcome) end to end with the kit's scorer. The model's half (classifying a reply, phrasing
`neg_say`) is out of scope here."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fb.harness.bank import Bank
from fb.harness.scoring import score_ledger
from fb.nodecode import bodies
from fb.nodecode.embed import snippet


@dataclass
class SimResult:
    vars: dict[str, Any]
    says: list[str] = field(default_factory=list)
    ledger: dict[str, Any] = field(default_factory=dict)
    score: dict[str, Any] = field(default_factory=dict)
    code_errors: list[str] = field(default_factory=list)


def run_body(body: str, cfg: dict[str, Any], v: dict[str, Any], errors: list[str]) -> None:
    v.pop("code_error", None)
    exec(compile(snippet(body, cfg), "code_node", "exec"), {"vars": v})
    if v.get("code_error"):
        errors.append(str(v["code_error"]))


def reply_vars(r: dict[str, Any]) -> dict[str, str]:
    return {
        "reply_kind": r.get("kind", ""),
        "said_date_text": r.get("date", ""),
        "said_amount_text": r.get("amount", ""),
        "all_products": "yes" if r.get("all") else "",
        "about_product": r.get("product", ""),
    }


def simulate(case: dict[str, Any], replies: list[dict[str, Any]], cfg: dict[str, Any]) -> SimResult:
    bank = Bank()
    v: dict[str, Any] = dict(bank.open_session(case))
    cid = v["customer_id"]
    res = SimResult(vars=v)
    run_body(bodies.SETUP, cfg, v, res.code_errors)
    for f, ans in case["identity"].items():
        bank.call(cid, "verify_identity", {"customer_id": cid, "field": f, "answer": ans})
    _, acc = bank.call(cid, "get_account", {"customer_id": cid})
    v["products"] = acc.get("products")
    run_body(bodies.FACTS, cfg, v, res.code_errors)
    res.says.append(str(v.get("neg_say")))
    for r in replies:
        if v.get("neg_action") in ("finish", "transfer"):
            break
        v.update(reply_vars(r))
        # the customer's words, as the platform hands them to code nodes
        v["system.conversation"] = [{"role": "user", "content": r.get("text", "")}]
        run_body(bodies.DECIDE, cfg, v, res.code_errors)
        while v.get("neg_action") == "record":
            st, out = bank.call(
                cid,
                "create_promise",
                {
                    "customer_id": cid,
                    "product_id": v["rec_product_id"],
                    "amount": v["rec_amount"],
                    "date": v["rec_date"],
                },
            )
            v["rec_ok"] = "true" if st == 200 and out.get("ok") else "false"
            run_body(bodies.AFTER_RECORD, cfg, v, res.code_errors)
        if v.get("neg_action") == "say":
            res.says.append(str(v.get("neg_say")))
    if v.get("neg_action") == "transfer":
        bank.call(cid, "transfer_to_agent", {"customer_id": cid, "queue": v["transfer_queue"]})
    if v.get("neg_action") in ("finish", "transfer"):
        while int(v.get("note_count") or 0) > 0:
            bank.call(
                cid,
                "add_note",
                {
                    "customer_id": cid,
                    "product_id": v["note_product_id"],
                    "reason": v["note_reason"],
                },
            )
            run_body(bodies.AFTER_NOTE, cfg, v, res.code_errors)
        res.says.append(str(v.get("final_say")))
    close = {"customer_id": cid, "outcome": v.get("final_outcome") or ""}
    if v.get("final_reason"):
        close["reason"] = v["final_reason"]
    bank.call(cid, "close_call", close)
    res.ledger = bank.ledger(cid)
    res.score = score_ledger(case, res.ledger)
    return res
