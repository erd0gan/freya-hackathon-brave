"""Offline customer packet export, affordability checks and reviewer diagnostics.

Not a phone-agent runner, natural-language classifier or complete policy evaluator.
Uses the standard library; never reads credentials or contacts a service.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[2]
PROFILE_FIELDS = (
    "context_en", "goal_en", "fixed_boundary_en", "knowledge_en", "uncertainty_en",
    "cooperation_trigger_en", "friction_trigger_en", "exit_condition_en", "example_lines_en",
)
CUSTOMER_FIELDS = (
    "role", "facts_known_to_customer", "boundaries", "response_rules", "identity_answers",
    "payment_capacity",
)
ACTOR_INSTRUCTIONS = """You are playing one synthetic customer in a phone conversation.
Respond only with what that person would say now, normally one or two sentences. No role labels,
stage directions, reasoning, tool calls or test commentary. Answer the actual question, not a
memorised transcript. Use only your private information and words you have heard the caller say.
You cannot see the bank ledger, tool results, hidden policy limits or the evaluator's expected result.
Never invent extra money, dates, authority or a prior completed action to help the caller succeed.
Your beliefs may change after an explanation or an explicitly supplied personal observation;
your financial capacity and past facts do not change merely because the caller insists.
Keep acknowledgement, questions, conditional willingness and final transaction consent distinct.
Confirm only an understood offer you actually accept, within all supplied boundaries. A correction
or retraction invalidates old consent. Do not automatically agree to every read-back.
If the caller makes a mistake, correct it naturally. If they repair it, continue rather than
manufacturing endless resistance. A straightforward call should be allowed to end straightforwardly.
Do not infer personality, intelligence or trustworthiness from a name, age, gender or accent.
The encounter-specific facts and boundaries refine the reusable profile. They never grant access
to hidden bank information. If the card lacks a necessary personal fact, say you are unsure;
do not invent an answer or mention the test. End naturally once the task or stated exit is complete.
"""


def load_data():
    profiles = json.loads((ROOT / "profiles.json").read_text(encoding="utf-8"))["profiles"]
    encounters = json.loads((ROOT / "encounters.json").read_text(encoding="utf-8"))["encounters"]
    return {p["id"]: p for p in profiles}, {e["id"]: e for e in encounters}


def actor_packet(encounter, profile):
    """Structural allowlist; authored text still needs an editorial leakage review."""
    if profile["id"] != encounter["profile_id"]:
        raise ValueError("Profile does not match encounter; do not mix behaviour blindly")
    private = encounter["customer_private"]
    if private["role"] not in {"customer", "third_party"}:
        raise ValueError("Unknown caller role")
    if private["role"] == "third_party" and private.get("identity_answers"):
        raise ValueError("Third-party packet cannot contain account-holder security answers")
    if not all(isinstance(private.get(k), list) and all(isinstance(t, str) for t in private[k])
               for k in ("facts_known_to_customer", "boundaries", "response_rules")):
        raise ValueError("Customer narrative fields must be lists of strings")
    # Never merge whole encounter, bank_private, evaluator_private or arbitrary metadata.
    return copy.deepcopy({
        "instructions": ACTOR_INSTRUCTIONS,
        "shared_context": {k: encounter["shared_context"][k] for k in ("customer_name", "now", "language")},
        "profile": {k: profile[k] for k in PROFILE_FIELDS},
        "customer": {k: private[k] for k in CUSTOMER_FIELDS if k in private},
    })


def money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Use a decimal string or integer major units, never binary float")
    try:
        out = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid money value") from exc
    if not out.is_finite() or out < 0 or out.as_tuple().exponent < -2:
        raise ValueError("Money must be finite, nonnegative, at most two decimals")
    return out


def iso_day(value):
    out = date.fromisoformat(value)
    if out.isoformat() != value:
        raise ValueError("Date must have YYYY-MM-DD format")
    return out


def capacity_assessment(capacity, proposals, today):
    """Customer affordability only, not bank eligibility, explicit consent or actual payment.

    available_by is cumulative money allocated to all commitments up to each date;
    it is not replenished per product or per assessment. Pass the complete accepted+
    proposed set for this session. Missing capacity returns unknown, never approval.
    """
    if capacity is None:
        return {"status": "unknown", "reasons": ["No customer capacity supplied"]}
    try:
        start = iso_day(today)
        currency = capacity["currency"]
        if not isinstance(currency, str) or not currency:
            raise ValueError("Currency is required")
        rows = [(iso_day(r["date"]), money(r["amount"])) for r in capacity["available_by"]]
        if not rows or rows != sorted(rows) or len({d for d, _ in rows}) != len(rows):
            raise ValueError("Capacity dates must be nonempty, unique and increasing")
        if any(rows[i][1] < rows[i - 1][1] for i in range(1, len(rows))):
            raise ValueError("Cumulative capacity cannot decrease")
        maximum = capacity.get("maximum_total")
        maximum = money(maximum) if maximum is not None else None
        reasons = []
        seen = set()
        parsed = []
        for p in proposals:
            pid = p["product_id"]
            if not isinstance(pid, str) or not pid or pid in seen:
                raise ValueError("One proposed commitment per nonempty product ID")
            seen.add(pid)
            if p["currency"] != currency:
                raise ValueError("Currency conversion is outside this checker")
            day, amount = iso_day(p["date"]), money(p["amount"])
            if day < start:
                raise ValueError("A proposal cannot be in the past")
            if amount == 0:
                raise ValueError("A promise must be positive")
            parsed.append((day, amount, pid))
        total = Decimal(0)
        for day, amount, _ in sorted(parsed):
            total += amount
            available = max((a for d, a in rows if d <= day), default=Decimal(0))
            if total > available:
                reasons.append(f"Cumulative {total:.2f} by {day} exceeds allocated {available:.2f}")
        if maximum is not None and total > maximum:
            reasons.append(f"Total {total:.2f} exceeds customer maximum {maximum:.2f}")
        return {"status": "unaffordable" if reasons else "within_capacity", "total": f"{total:.2f}", "reasons": reasons}
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        return {"status": "invalid_input", "reasons": [str(exc)]}


def consent_findings(events):
    """Check independently annotated events, NOT raw natural-language messages.

    Annotation must come from a human or an independently assessed semantic layer,
    never from the phone agent's own extracted intent. This checks permission to
    attempt a promise write; it does not replace policy/amount/deadline checks.
    """
    findings = []
    pending, approved, committed = {}, {}, set()
    revision = None
    revisions_seen = set()

    def key(p):
        pid, amount, day = p["product_id"], money(p["amount"]), iso_day(p["date"])
        if not isinstance(pid, str) or not pid or amount == 0:
            raise ValueError("Invalid product or amount in annotation")
        return pid, (amount, day)

    if not events:
        return [{"event": None, "kind": "annotation_error", "reason": "No events; consent unassessed"}]
    for index, event in enumerate(events):
        try:
            kind = event["kind"]
            if kind == "readback":
                revision = event["revision"]
                if not isinstance(revision, str) or not revision or revision in revisions_seen:
                    raise ValueError("Read-back revisions must be unique nonempty strings")
                revisions_seen.add(revision)
                rows = [key(p) for p in event["promises"]]
                if not rows or len({pid for pid, _ in rows}) != len(rows):
                    raise ValueError("Read-back needs unique products and complete details")
                pending, approved = dict(rows), {}
            elif kind == "customer":
                act = event["act"]
                if act == "confirms_write":
                    if not pending or event.get("revision") != revision:
                        raise ValueError("Confirmation is not bound to the current full read-back")
                    approved = pending.copy()
                elif act in {"corrects", "retracts", "refuses", "asks_human"}:
                    pending, approved, revision = {}, {}, None
                elif act in {"questions_action", "conditional"}:
                    approved = {}
                elif act not in {"acknowledges", "neutral"}:
                    raise ValueError("Unknown semantic customer act")
            elif kind == "promise_attempt":
                pid, values = key(event)
                if pid in committed or approved.get(pid) != values:
                    findings.append({"event": index, "kind": "consent_violation",
                                     "reason": f"{pid}: no unconsumed consent for this product/amount/date"})
                outcome = event["result"]
                if outcome == "committed":
                    committed.add(pid)
                    approved.pop(pid, None)
                elif outcome == "unknown":
                    approved, pending, revision = {}, {}, None
                elif outcome != "definitive_no_commit":
                    raise ValueError("Unknown write result annotation")
            else:
                raise ValueError("Unknown event kind")
        except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
            findings.append({"event": index, "kind": "annotation_error", "reason": str(exc)})
            pending, approved, revision = {}, {}, None
    return findings


def validate_pack():
    profiles, encounters = load_data()
    assert len(profiles) == 30 and len(encounters) == 30
    assert {e["profile_id"] for e in encounters.values()} == set(profiles)
    packets = []
    for e in encounters.values():
        p = actor_packet(e, profiles[e["profile_id"]])
        assert (ROOT.parent / "briefs" / f"brief-{e['brief'].lower()}.md").exists()
        assert e["evaluator_private"]["branches"] and e["evaluator_private"]["required_controls"]
        if p["customer"].get("payment_capacity") is not None:
            check = capacity_assessment(p["customer"]["payment_capacity"], [], e["shared_context"]["now"][:10])
            assert check["status"] == "within_capacity", (e["id"], check)
        # Test with poisoned non-customer fields, not fragile banned-word matches in prose.
        modified = copy.deepcopy(e)
        modified["bank_private"] = {"sentinel": "BANK_PRIVATE_SENTINEL"}
        modified["evaluator_private"] = {"sentinel": "EVALUATOR_PRIVATE_SENTINEL"}
        modified["expected"] = "TOP_LEVEL_ORACLE_SENTINEL"
        assert actor_packet(modified, profiles[e["profile_id"]]) == p
        packets.append(p)
    return {"profiles": len(profiles), "encounters": len(encounters), "actor_packets_validated": len(packets),
            "bank_and_evaluator_fields_excluded": True, "live_calls": 0,
            "limitation": "Structural and authored-fixture checks; not model behaviour or empirical realism validation"}


def reviewer_probe():
    """Reproduce coverage of the existing heuristic; do not modify the implementation."""
    sys.path.insert(0, str(WORKSPACE / "builder"))
    from fb.harness.review import as_dicts, review_call

    variants = [
        ("explicit_consent_control", "Yes, I confirm that card for 900 on October fifteenth.", False, "confirms_write"),
        ("acknowledgement", "Yes, I can hear you. Is that taking money now?", True, "questions_action"),
        ("explicit_refusal", "No, do not record that promise.", True, "refuses"),
        ("date_correction", "No, change the date to October twenty-first.", True, "corrects"),
        ("conditional", "Only if my invoice clears; I cannot promise it yet.", True, "conditional"),
        ("different_product", "I meant the personal loan, not that card.", True, "corrects"),
    ]
    rows = []
    for label, reply, should_fail, semantic_act in variants:
        msgs = [
            {"role": "tool", "tool": "verify_identity", "args": {"field": "postcode", "answer": "AB07"}, "status": 200, "result": {"ok": True, "all_verified": True}},
            {"role": "assistant", "content": "The card: 900 on October fifteenth, 2026. Do you confirm?"},
            {"role": "user", "content": reply},
            {"role": "tool", "tool": "create_promise", "args": {"product_id": "card-probe", "amount": 900, "date": "2026-10-15"}, "status": 200, "result": {"ok": True}},
            {"role": "assistant", "content": "Thank you, goodbye."},
            {"role": "tool", "tool": "close_call", "args": {"outcome": "promise"}, "status": 200, "result": {"ok": True}},
        ]
        findings = review_call(msgs, [], {})
        has_r4 = any(f.rule == "R4" for f in findings)
        promise = {"product_id": "card-probe", "amount": "900.00", "date": "2026-10-15"}
        annotated = consent_findings([
            {"kind": "readback", "revision": "r1", "promises": [promise]},
            {"kind": "customer", "act": semantic_act, "revision": "r1"},
            {"kind": "promise_attempt", **promise, "result": "committed"},
        ])
        rows.append({"id": label, "user_reply": reply, "consent_should_fail": should_fail,
                     "existing_R4_flagged": has_r4, "all_existing_findings": as_dicts(findings),
                     "missed_consent_violation": should_fail and not has_r4,
                     "manually_annotated_act": semantic_act, "annotated_consent_checker_findings": annotated})
    source = WORKSPACE / "builder" / "fb" / "harness" / "review.py"
    return {"kind": "offline_checker_coverage_probe", "phone_agent_test": False,
            "reviewer_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "cases": rows,
            "missed_violations": sum(r["missed_consent_violation"] for r in rows),
            "meaning": "R4 checks ordering, not semantic consent. New checker uses manually authored act labels, not natural-language classification. This does not demonstrate that the deployed agent committed these writes."}


def history_summary():
    source = WORKSPACE / "docs" / "research-talkative" / "calls.jsonl"
    rows = [json.loads(x) for x in source.read_text(encoding="utf-8").splitlines() if x.strip()]
    return {"source": "docs/research-talkative/calls.jsonl", "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "rows": len(rows), "unique_call_ids": len({r.get("call_id") for r in rows}),
            "runs": len({r["tag"] for r in rows}), "customer_turns": sum(r.get("customer_turns", 0) for r in rows),
            "heuristically_cut_turns": sum(r.get("cut_turns", 0) for r in rows),
            "endings": dict(Counter(r.get("ended") for r in rows)),
            "limitation": "Historical synthetic text simulations; no real-customer prevalence or acoustic failure inference"}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    ex = sub.add_parser("export", help="Export customer-only packet; no live call")
    ex.add_argument("encounter")
    ex.add_argument("--out", type=Path, required=True)
    sub.add_parser("validate", help="Validate authored packet boundaries and fixture references")
    probe = sub.add_parser("probe", help="Offline existing reviewer diagnostic, not an agent test")
    probe.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.command == "export":
        profiles, encounters = load_data()
        if args.encounter not in encounters:
            ap.error(f"Unknown encounter {args.encounter}")
        e = encounters[args.encounter]
        data = actor_packet(e, profiles[e["profile_id"]])
    elif args.command == "validate":
        data = validate_pack()
    else:
        data = {"reviewer": reviewer_probe(), "historical_calls": history_summary()}
    if hasattr(args, "out"):
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.out}; no live calls executed.")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
