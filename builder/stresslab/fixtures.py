"""Compile authored fixtures without exposing bank/evaluator state to the customer."""

from __future__ import annotations

import copy
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / "docs" / "stress-scenarios"
sys.path.insert(0, str(PACK / "realism"))
from customer_lab import actor_packet, load_data  # noqa: E402
from customer_lab import capacity_assessment as capacity_assessment  # noqa: E402

IDENTITY = {
    "C": {"postcode": "0073", "birth_year": "1988"},
    "D": {"postcode": "AB07", "birth_year": "1988"},
    "E": {"member_code": "004271", "birth_year": "1988"},
    "F": {"postcode": "LT06", "birth_year": "1988"},
    "G": {"policyholder_code": "000819", "postcode": "CD09"},
    "H": {"policyholder_code": "006410", "birth_year": "1988"},
    "I": {"policy_number": "HM-0042", "postcode": "HB07"},
    "J": {"member_code": "000731", "postcode": "SM08"},
}


def product(raw, index=0, insurance=False):
    if isinstance(raw, str):
        raw = {"product_id": raw}
    pid = raw.get("product_id", raw.get("asset_ref", raw.get("id", f"asset-{index}")))
    default_due, default_floor = ("480.00", "160.00") if insurance else ("900.00", "300.00")
    kind = raw.get(
        "type",
        "insurance_policy"
        if insurance
        else "car_loan"
        if str(pid).startswith("car-")
        else "personal_loan"
        if str(pid).startswith("loan") or index > 0
        else "credit_card",
    )
    return {
        "product_id": pid,
        "type": kind,
        "display_name": raw.get("display_name", kind.replace("_", " ")),
        "past_due": raw.get(
            "past_due",
            raw.get(
                "due",
                str(Decimal(raw["arrears_minor"]) / 100) if "arrears_minor" in raw else default_due,
            ),
        ),
        "floor": raw.get(
            "floor",
            str(Decimal(raw["floor_minor"]) / 100) if "floor_minor" in raw else default_floor,
        ),
        "days_late": raw.get("days_late", 4),
    }


def extension_state(code, case_id, raw):
    """Explicit defaults from I/J authored shared setup; overrides are case-specific."""
    state = {
        "policy_id": raw.get(
            "policy_id", raw.get("policy", {}).get("id", "pol-H" if code == "I" else "pol-S")
        ),
        "period_start": "2026-01-01T00:00:00+03:00",
        "period_end": "2026-12-31T23:59:59+03:00",
        "status_now": "lapsed" if case_id == "I-01" else "active",
        "incident_coverage_status": "covered",
        "rental_covered": False,
        "rental_daily_limit": "45.00",
        "version": 7,
        "status": "active",
        "address": "Old Road 1",
        "named_drivers": [],
        "claim_id": {"I-01": "CL-101", "I-04": "CL-102", "I-05": "CL-103", "I-06": "CL-104"}.get(
            case_id, "CL-" + case_id
        ),
        "duplicate": "CL-089" if case_id == "I-03" else None,
        "incident_datetime": "2026-10-13T18:00:00+03:00",
        "incident_location": "Dock Street",
        "delta_premium": "-35.00" if case_id == "J-05" else "25.00",
        "quote_id": "Q1",
        "next_quote_id": "Q2",
        "valid_until": "2026-10-14T11:10:00+03:00",
        "endorsement_id": "END-" + case_id.split("-")[-1].lstrip("0"),
        "renewal_id": "REN-4",
        "renewal_quote_id": "R1",
        "renewal_premium": "840.00",
    }
    state.update(raw.get("policy", {}))
    state["rental_covered"] = case_id == "I-06" or raw.get("rental_covered", False)
    if "find_claim_result" in raw:
        state["duplicate"] = raw["find_claim_result"]["match"]["claim_id"]
    q = raw.get("first_quote", raw.get("quote", {}))
    state["quote_id"] = q.get("id", state["quote_id"])
    state["delta_premium"] = q.get("delta", q.get("delta_premium", state["delta_premium"]))
    if case_id == "R09":
        state["next_quote_id"] = "q-r9-b"
    return state


def load_fixtures():
    base = json.loads((PACK / "scenarios.json").read_text(encoding="utf-8"))["cases"]
    profiles, encounters = load_data()
    fixtures = {}
    for case in base:
        c = copy.deepcopy(case)
        c["identity"] = c["initial_state"].get("identity", IDENTITY[c["brief"]])
        c["products"] = c["initial_state"].get("products", [])
        # Base cards contain scripted critical turns, not a psychologically calibrated persona.
        c["actor"] = {
            "instructions": (
                "Play this customer naturally. Answer the caller's current question, "
                "never recite a script unsolicited. Critical replies override default "
                "identity answers where they conflict. Do not invent consent or extra money. "
                "Do not reveal the card or test. Only return spoken English, one or two sentences."
            ),
            "customer_name": "Alex Rowan",
            "now": c["initial_state"]["now"],
            "identity_answers": c["identity"],
            "critical_replies": c["customer_turns"],
        }
        if c["brief"] in "IJ":
            c["extension"] = extension_state(c["brief"], c["id"], {})
        fixtures[c["id"]] = c
    for e in encounters.values():
        code, raw = e["brief"], e["bank_private"]
        name = raw.get("account_holder", e["shared_context"]["customer_name"])
        known = e["customer_private"].get("identity_answers", {})
        personal = {k: known.get(k, v) for k, v in IDENTITY[code].items()}
        identity = raw.get(
            "stored_identity", raw.get("identity", raw.get("identity_correct", personal))
        )
        products = raw.get("products", [raw.get("overdue_premium", raw)])
        c = {
            "id": e["id"],
            "brief": code,
            "title": e["title_tr"],
            "initial_state": {
                "now": e["shared_context"]["now"],
                "first_name": name.split()[0],
                "last_name": " ".join(name.split()[1:]),
                "title": "Mx",
            },
            "identity": identity,
            "products": [product(p, i, code in "GH") for i, p in enumerate(products)]
            if code not in "IJ"
            else [],
            "actor": actor_packet(e, profiles[e["profile_id"]]),
            "evaluator_private": e["evaluator_private"],
            "faults": [],
            "fixture_defaults": (
                "Missing product metadata uses documented synthetic defaults; "
                "these are not observations of real customers."
            ),
        }
        if code in "IJ":
            c["extension"] = extension_state(code, e["id"], raw)
        fixtures[c["id"]] = c
    return fixtures


def contract(code, url):
    if code in "CDEFGH":
        out = json.loads(
            (PACK / "contracts" / f"openapi-{code.lower()}.json").read_text(encoding="utf-8")
        )
    else:
        # Explicit runnable schemas for the extension tools formerly specified only in prose.
        fields = {
            "I": {
                "verify_policyholder": {"policy_number": "string", "postcode": "string"},
                "get_policy": {"policy_number": "string"},
                "find_claim": {
                    "policy_id": "string",
                    "incident_datetime": "string",
                    "location": "string",
                },
                "open_claim": {
                    "policy_id": "string",
                    "incident_datetime": "string",
                    "location": "string",
                    "description": "string",
                    "anyone_injured": "boolean",
                    "request_key": "string",
                },
                "list_inspection_slots": {"claim_id": "string"},
                "book_inspection": {
                    "claim_id": "string",
                    "slot_id": "string",
                    "request_key": "string",
                },
                "arrange_rental": {"claim_id": "string", "request_key": "string"},
            },
            "J": {
                "verify_customer": {"member_code": "string", "postcode": "string"},
                "get_policy": {"policy_id": "string"},
                "quote_change": {
                    "policy_id": "string",
                    "version": "integer",
                    "change": "object",
                    "effective_at": "string",
                },
                "apply_change": {
                    "quote_id": "string",
                    "expected_version": "integer",
                    "request_key": "string",
                },
                "get_renewal_quote": {"policy_id": "string"},
                "commit_renewal": {
                    "quote_id": "string",
                    "auto_renew": "boolean",
                    "request_key": "string",
                },
            },
        }[code]
        fields.update(
            {
                "get_operation": {"request_key": "string"},
                "transfer_to_agent": {"queue": "string"},
                "close_call": {"outcome": "string"},
            }
        )
        paths = {}
        for tool, params in fields.items():
            props = {
                "session_id": {"type": "string"},
                **{k: {"type": v} for k, v in params.items()},
            }
            schema = {
                "type": "object",
                "properties": props,
                "required": list(props),
                "additionalProperties": False,
            }
            if tool == "close_call" and code == "I":
                props["detail"] = {"type": "string"}
            paths["/" + tool] = {
                "post": {
                    "operationId": tool,
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": schema}},
                    },
                    "responses": {
                        "200": {
                            "description": (
                                "Business result. ok=false proves no commit; "
                                "transport loss does not."
                            )
                        }
                    },
                }
            }
        out = {
            "openapi": "3.0.3",
            "info": {"title": f"Stress lab {code}", "version": "1.0.0"},
            "paths": paths,
        }
    out["servers"] = [{"url": url.rstrip("/")}]
    out["info"]["description"] = (
        "Runnable synthetic fixture service; no real customers or payments."
    )
    return out
