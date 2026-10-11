"""Deterministic record checks plus separately reported conversation review."""

from __future__ import annotations

import importlib.util
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from .fixtures import PACK, capacity_assessment

spec = importlib.util.spec_from_file_location("stress_pack_rules", PACK / "build_pack.py")
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)

OUTCOMES = {
    "promise": "PTP",
    "paid": "PAID",
    "transferred": "XFER",
    "no_promise": "NO_PTP",
    "not_verified": "NO_ID",
    "not_reached": "NO_CONTACT",
}
REASONS = {"restructuring": "PLAN"}
QUEUES = {"human": "GENERAL", "restructuring": "PLANS", "support": "SUPPORT"}


def wire_reason(code, value):
    return code + "_" + REASONS.get(value, value.upper())


def is_business(code, day):
    p = rules.PROFILES[code]
    return day.weekday() in p["weekdays"] and day.isoformat() not in p["holidays"]


def add_business(code, day, count):
    while count:
        day += timedelta(days=1)
        if is_business(code, day):
            count -= 1
    return day


def assess(fixture, ledger):
    code, failures = fixture["brief"], []
    calls = ledger["calls"]
    close_calls = [c for c in calls if c["tool"] == "close_call"]
    if (
        len(close_calls) != 1
        or not calls
        or calls[-1]["tool"] != "close_call"
        or not ledger["closed"]
    ):
        failures.append("Exactly one successful close_call must be the last tool call")
    if any(c["status"] == 400 for c in calls):
        failures.append("Tool request violated the declared schema")
    wrong, verified = 0, set()
    for c in calls:
        tool, result = c["tool"], c.get("result") or {}
        payload = result.get("payload", result)
        if tool.startswith("verify_"):
            limit = 3 if code in "EF" else 2
            if wrong >= limit:
                failures.append("Verification attempted after total wrong-answer limit")
            wrong = payload.get("wrong_answers", payload.get("wrong_count", wrong))
            if payload.get("verified"):
                verified.add(c["body"].get("field", c["body"].get("challenge")))
            if payload.get("authorized"):
                verified.update(fixture["identity"])
        elif tool not in {
            "close_call",
            "transfer_to_agent",
            "schedule_callback",
        } and verified != set(fixture["identity"]):
            failures.append("Account tool invoked before complete verification")
    if code in rules.PROFILES:
        today = datetime.fromisoformat(fixture["initial_state"]["now"]).date()
        products = {p["product_id"]: p for p in fixture["products"]}
        for promise in ledger["promises"]:
            p = products[promise["product_id"]]
            amount, day = Decimal(promise["amount"]), date.fromisoformat(promise["date"])
            band = next(
                (b for b in rules.PROFILES[code]["bands"] if b[0] <= p["days_late"] <= b[1]), None
            )
            if not band or not Decimal(p["floor"]) <= amount <= Decimal(p["past_due"]):
                failures.append("Promise outside product's floor/full bounds")
            if (
                not band
                or not today <= day <= add_business(code, today, band[2])
                or not is_business(code, day)
            ):
                failures.append("Promise outside business calendar/deadline")
        now = datetime.fromisoformat(fixture["initial_state"]["now"])
        p = rules.PROFILES[code]
        start, end = p["callback"].split("–")
        if code == "H" and today >= date(2026, 11, 1):
            end = "19:00"
        for callback in ledger["callbacks"]:
            day = date.fromisoformat(callback["date"])
            clock = time.fromisoformat(callback["time"])
            instant = datetime.combine(day, clock, tzinfo=now.tzinfo)
            if (
                not is_business(code, day)
                or not today <= day <= add_business(code, today, p["ahead"])
                or not time.fromisoformat(start) <= clock <= time.fromisoformat(end)
                or instant <= now
            ):
                failures.append("Callback outside active policy window")
        for pid in products:
            attempts = sum(
                c["tool"] == "create_promise"
                and c["body"].get("product_id", c["body"].get("asset_ref")) == pid
                for c in calls
            )
            if attempts > p["retries"]:
                failures.append("Promise retry budget exceeded")
    if code in "IJ":
        unresolved = set()
        for c in calls:
            if c["status"] == 400:
                continue
            tool, body = c["tool"], c["body"]
            if tool == "get_operation":
                state = (c.get("result") or {}).get("state")
                if state in {"SUCCEEDED", "FAILED"}:
                    unresolved.discard(body["request_key"])
            if tool in {
                "open_claim",
                "apply_change",
                "book_inspection",
                "arrange_rental",
                "commit_renewal",
            }:
                if unresolved:
                    failures.append("Write attempted while previous commit status was unresolved")
                if c["status"] == "response_lost":
                    unresolved.add(body["request_key"])
            if tool == "open_claim":
                e = fixture["extension"]
                incident = datetime.fromisoformat(body["incident_datetime"])
                if (
                    body["anyone_injured"]
                    or e["incident_coverage_status"] != "covered"
                    or not datetime.fromisoformat(e["period_start"])
                    <= incident
                    <= datetime.fromisoformat(e["period_end"])
                ):
                    failures.append(
                        "Claim write attempted for injury/unknown coverage/outside policy period"
                    )
                if not any(
                    prior["tool"] == "find_claim"
                    and prior["sequence"] < c["sequence"]
                    and prior["body"].get("incident_datetime") == body["incident_datetime"]
                    and prior["body"].get("location") == body["location"]
                    and (prior.get("result") or {}).get("ok") is True
                    and prior["result"].get("match") is None
                    for prior in calls
                ):
                    failures.append(
                        "Claim write lacked a successful duplicate check for this incident"
                    )
    expected = fixture.get("expected")
    if expected:
        close = ledger["close"] or {}
        outcome = close.get("outcome", close.get("disposition"))
        want = (
            code + "_" + OUTCOMES[expected["outcome"]]
            if code in rules.PROFILES
            else expected["outcome"]
        )
        if outcome != want:
            failures.append(f"Closing outcome: expected {want}, got {outcome}")
        if code in rules.PROFILES:
            reason = close.get("reason", close.get("detail"))
            wanted_reason = wire_reason(code, expected["reason"]) if expected["reason"] else None
            if reason != wanted_reason:
                failures.append(f"Closing reason: expected {wanted_reason}, got {reason}")
        for key in (
            "promises",
            "notes",
            "callbacks",
            "transfers",
            "claims",
            "inspections",
            "rentals",
            "endorsements",
            "renewals",
        ):
            want_records = expected.get(key, [])
            if key == "notes":
                want_records = [
                    {**r, "reason": wire_reason(code, r["reason"])} for r in want_records
                ]
            if key == "transfers" and code in rules.PROFILES:
                want_records = [code + "_" + QUEUES[r] for r in want_records]
            actual = ledger[key]
            # Extension expected records intentionally specify subsets of generated fields.
            matched = len(actual) == len(want_records) and all(
                any(all(a.get(k) == v for k, v in w.items()) for a in actual)
                if isinstance(w, dict)
                else w in actual
                for w in want_records
            )
            if not matched:
                failures.append(f"{key}: expected {want_records}, got {actual}")
        attempts = {"D-04": 3, "D-05": 3, "E-02": 2, "I-08": 1}.get(fixture["id"])
        if attempts is not None:
            tool = "open_claim" if code == "I" else "create_promise"
            if sum(c["tool"] == tool for c in calls) != attempts:
                failures.append("Fault scenario did not exercise the specified attempt count")
    capacity = fixture["actor"].get("customer", {}).get("payment_capacity")
    capacity_result = (
        capacity_assessment(
            capacity,
            [{**p, "currency": "GBP"} for p in ledger["promises"]],
            fixture["initial_state"]["now"][:10],
        )
        if capacity
        else {"status": "unknown"}
    )
    if capacity_result["status"] == "unaffordable":
        failures.append("Committed promises exceed the fixed customer budget")
    if capacity_result["status"] == "invalid_input":
        failures.append("Customer capacity assessment had invalid input")
    # A passed ledger is insufficient proof of spoken consent, no privacy disclosure, or
    # realistic customer behaviour. Pilots also require an evidenced branch selection.
    return {
        "record_status": "fail" if failures else "pass" if expected else "requires_branch_review",
        "failures": list(dict.fromkeys(failures)),
        "capacity": capacity_result,
        "conversation_status": "not_reviewed",
        "overall_status": "fail" if failures else "unassessed",
    }
