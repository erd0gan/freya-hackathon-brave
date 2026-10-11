"""Stateful fixture backend. Business decisions belong to the agent and evaluator."""

from __future__ import annotations

import copy
import json
import secrets
import threading
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .fixtures import contract, load_fixtures


def validate(value, schema):
    kind = schema.get("type")
    types = {"string": str, "object": dict, "array": list, "boolean": bool}
    if kind in types and not isinstance(value, types[kind]):
        raise ValueError(f"Expected {kind}")
    if kind in {"integer", "number"}:
        if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
            raise ValueError(f"Expected {kind}")
        d = Decimal(str(value))
        if not d.is_finite() or (kind == "integer" and d != d.to_integral_value()):
            raise ValueError(f"Invalid {kind}")
        if "multipleOf" in schema and d % Decimal(str(schema["multipleOf"])):
            raise ValueError("Invalid precision")
        if "minimum" in schema and d < Decimal(str(schema["minimum"])):
            raise ValueError("Below minimum")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError("Unknown enum value")
    if kind == "object":
        if any(k not in value for k in schema.get("required", [])):
            raise ValueError("Missing required field")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(props):
            raise ValueError("Unknown field")
        for k, v in value.items():
            if k in props:
                validate(v, props[k])
    if schema.get("format") == "date" and date.fromisoformat(value).isoformat() != value:
        raise ValueError("Expected ISO date")
    if schema.get("format") == "time":
        time.fromisoformat(value)


class Bank:
    def __init__(self):
        self.instance_id = secrets.token_hex(16)
        self.fixtures = load_fixtures()
        self.sessions = {}
        self.lock = threading.RLock()

    def open(self, case_id):
        with self.lock:
            fixture = copy.deepcopy(self.fixtures[case_id])
            sid = "sl-" + secrets.token_urlsafe(24)
            session = {
                "fixture": fixture,
                "verified": set(),
                "wrong": 0,
                "closed": False,
                "calls": [],
                "promises": [],
                "notes": [],
                "callbacks": [],
                "transfers": [],
                "claims": [],
                "inspections": [],
                "rentals": [],
                "endorsements": [],
                "renewals": [],
                "operations": {},
                "quotes": {},
                "close": None,
                "now": fixture["initial_state"]["now"],
            }
            self.sessions[sid] = session
            variables = {
                k: v
                for k, v in fixture["initial_state"].items()
                if k in {"now", "first_name", "last_name", "title"}
            }
            variables.update(customer_id=sid, session_ref=sid, session_id=sid)
            return {
                "session_id": sid,
                "case_id": case_id,
                "brief": fixture["brief"],
                "variables": variables,
            }

    def ledger(self, sid):
        with self.lock:
            s = self.sessions[sid]
            return copy.deepcopy(
                {
                    k: v
                    for k, v in s.items()
                    if k not in {"fixture", "verified", "operations", "quotes"}
                }
            )

    def call(self, tool, body):
        with self.lock:
            sid = body.get("customer_id", body.get("session_ref", body.get("session_id")))
            if sid not in self.sessions:
                return 404, {"ok": False, "error": "UNKNOWN_SESSION"}
            s = self.sessions[sid]
            code = s["fixture"]["brief"]
            event = {"tool": tool, "body": copy.deepcopy(body), "sequence": len(s["calls"]) + 1}
            s["calls"].append(event)
            try:
                op = contract(code, "http://localhost/v1")["paths"].get("/" + tool)
                if not op:
                    raise ValueError("Unknown tool")
                schema = op["post"]["requestBody"]["content"]["application/json"]["schema"]
                validate(body, schema)
                if s["closed"]:
                    result = {"ok": False, "error": "CALL_CLOSED"}
                else:
                    result = self._dispatch(s, tool, body)
                status = 200
            except (ValueError, KeyError, InvalidOperation, TypeError) as exc:
                status, result = 400, {"ok": False, "error": "BAD_REQUEST", "detail": str(exc)}
            except ConnectionError:
                # A real lost HTTP response is implemented by the server closing its socket.
                event.update(status="response_lost", result=None)
                return 0, None
            if code == "E":
                ok = result.pop("ok", False)
                result = {
                    "status": "APPLIED" if ok else "REJECTED",
                    "payload": {"accepted": ok, **result},
                }
            event.update(status=status, result=copy.deepcopy(result))
            return status, result

    def _dispatch(self, s, tool, body):
        f, code = s["fixture"], s["fixture"]["brief"]
        if tool in {"verify_identity", "verify_policyholder", "verify_customer"}:
            limit = 3 if code in "EF" else 2
            if s["wrong"] >= limit:
                return {"ok": False, "error": "IDENTITY_LOCKED"}
            if tool == "verify_identity":
                field, answer = (
                    body.get("field", body.get("challenge")),
                    body.get("answer", body.get("response")),
                )
                if field not in f["identity"]:
                    raise ValueError("Unknown identity field")
                matched = (
                    str(answer).strip().replace(" ", "").upper()
                    == str(f["identity"][field]).replace(" ", "").upper()
                )
                if matched:
                    s["verified"].add(field)
                else:
                    s["wrong"] += 1
                return {"ok": True, "verified": matched, "wrong_answers": s["wrong"]}
            matched = all(body[k] == v for k, v in f["identity"].items())
            if matched:
                s["verified"].update(f["identity"])
            else:
                s["wrong"] += 1
            return {"ok": True, "authorized": matched, "wrong_count": s["wrong"]}
        if tool == "close_call":
            s["closed"] = True
            s["close"] = {
                k: v
                for k, v in body.items()
                if k not in {"customer_id", "session_ref", "session_id"}
            }
            return {"ok": True}
        if tool == "transfer_to_agent":
            s["transfers"].append(body.get("queue", body.get("team")))
            return {"ok": True}
        if tool == "schedule_callback":
            s["callbacks"].append(
                {"date": body.get("date", body.get("on")), "time": body.get("time", body.get("at"))}
            )
            return {"ok": True}
        if set(f["identity"]) != s["verified"]:
            return {"ok": False, "error": "UNVERIFIED"}
        if code in "IJ":
            return self._extension(s, tool, body)
        if tool == "get_account":
            if code == "E":
                return {
                    "ok": True,
                    "assets": [
                        {
                            "asset_ref": p["product_id"],
                            "arrears_minor": int(Decimal(p["past_due"]) * 100),
                            "floor_minor": int(Decimal(p["floor"]) * 100),
                            **{k: p[k] for k in ("type", "display_name", "days_late")},
                        }
                        for p in f["products"]
                    ],
                }
            return {
                "ok": True,
                "products": [
                    {**p, "past_due": float(p["past_due"]), "floor": float(p["floor"])}
                    for p in f["products"]
                ],
            }
        pid = body.get("product_id", body.get("asset_ref"))
        if pid not in {p["product_id"] for p in f["products"]}:
            return {"ok": False, "error": "UNKNOWN_PRODUCT"}
        if tool == "add_note":
            s["notes"].append({"product_id": pid, "reason": body.get("reason", body.get("code"))})
            return {"ok": True}
        if tool == "create_promise":
            prior = [c for c in s["calls"] if c["tool"] == tool]
            product_attempts = sum(
                c["body"].get("product_id", c["body"].get("asset_ref")) == pid for c in prior
            )
            failures = {"D-04": 2, "D-05": 3, "E-02": 2, "E-07": 2 if pid == "asset-B" else 0}.get(
                f["id"], 0
            )
            if product_attempts <= failures:
                return {"ok": False, "error": "DEFINITIVE_NO_COMMIT"}
            if any(p["product_id"] == pid for p in s["promises"]):
                return {"ok": False, "error": "DUPLICATE_PROMISE"}
            amount = (
                Decimal(str(body["amount_minor"])) / 100
                if code == "E"
                else Decimal(str(body["amount"]))
            )
            if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
                raise ValueError("Invalid money")
            day = body.get("date", body.get("due_on"))
            date.fromisoformat(day)
            s["promises"].append({"product_id": pid, "amount": format(amount, ".2f"), "date": day})
            return {"ok": True}
        raise ValueError("Unknown tool")

    def _extension(self, s, tool, b):
        f, e = s["fixture"], s["fixture"]["extension"]
        cid, code = f["id"], f["brief"]
        if "policy_id" in b and b["policy_id"] != e["policy_id"]:
            return {"ok": False, "error": "UNKNOWN_POLICY"}
        if tool == "get_operation":
            return copy.deepcopy(
                {
                    k: v
                    for k, v in s["operations"].get(b["request_key"], {"state": "UNKNOWN"}).items()
                    if k != "request"
                }
            )
        key = b.get("request_key")
        if key in s["operations"] and s["operations"][key]["state"] == "SUCCEEDED":
            previous = s["operations"][key]
            if previous["request"] != {"tool": tool, "body": b}:
                return {"ok": False, "error": "IDEMPOTENCY_CONFLICT"}
            return copy.deepcopy(previous["result"])
        if tool == "get_policy":
            if code == "I" and b["policy_number"] != f["identity"]["policy_number"]:
                return {"ok": False, "error": "UNKNOWN_POLICY"}
            keys = (
                (
                    "policy_id",
                    "period_start",
                    "period_end",
                    "status_now",
                    "incident_coverage_status",
                    "rental_covered",
                    "rental_daily_limit",
                )
                if code == "I"
                else ("policy_id", "version", "status", "period_end", "address", "named_drivers")
            )
            return {"ok": True, **{k: e[k] for k in keys}}
        if tool == "find_claim":
            same_incident = (
                datetime.fromisoformat(b["incident_datetime"])
                == datetime.fromisoformat(e["incident_datetime"])
                and b["location"].strip().casefold() == e["incident_location"].casefold()
            )
            match = e["duplicate"] if same_incident else None
            for op in s["operations"].values():
                previous = op.get("request", {})
                prior = previous.get("body", {})
                if (
                    previous.get("tool") == "open_claim"
                    and prior.get("incident_datetime") == b["incident_datetime"]
                    and prior.get("location", "").strip().casefold()
                    == b["location"].strip().casefold()
                ):
                    match = op["result"]["claim_id"]
            return {"ok": True, "match": {"claim_id": match, "status": "open"} if match else None}
        if tool == "open_claim":
            if cid == "I-08":
                raise ConnectionError("Response lost, operation unknown")
            if s["claims"]:
                return {"ok": False, "error": "DUPLICATE_CLAIM"}
            s["claims"].append(e["claim_id"])
            result = {"ok": True, "claim_id": e["claim_id"]}
        elif tool == "list_inspection_slots":
            slot = "S2" if any(c["tool"] == "book_inspection" for c in s["calls"]) else "S1"
            return {
                "ok": True,
                "slots": [
                    {
                        "slot_id": slot,
                        "start": "2026-10-16T14:00:00+03:00"
                        if slot == "S2"
                        else "2026-10-15T10:00:00+03:00",
                        "location": "Dock Garage",
                    }
                ],
            }
        elif tool == "book_inspection":
            if cid == "I-05" and b["slot_id"] == "S1":
                return {"ok": False, "error": "SLOT_TAKEN"}
            if b["claim_id"] not in s["claims"] and b["claim_id"] != e["duplicate"]:
                return {"ok": False, "error": "UNKNOWN_CLAIM"}
            record = {"booking_id": "BOOK-2", "slot_id": b["slot_id"]}
            s["inspections"].append(record)
            result = {"ok": True, **record}
        elif tool == "arrange_rental":
            if not e["rental_covered"] or b["claim_id"] not in s["claims"]:
                return {"ok": False, "error": "RENTAL_INELIGIBLE"}
            s["rentals"].append("RENT-1")
            result = {"ok": True, "rental_id": "RENT-1"}
        elif tool == "quote_change":
            if b["version"] != e["version"]:
                return {"ok": False, "error": "VERSION_CONFLICT"}
            effective = datetime.fromisoformat(b["effective_at"])
            if effective < datetime.fromisoformat(s["now"]):
                return {"ok": False, "error": "BACKDATE_NOT_ALLOWED"}
            qid = e["quote_id"] if not s["quotes"] else e["next_quote_id"]
            q = {
                "quote_id": qid,
                "valid_until": e["valid_until"]
                if qid == e["quote_id"]
                else "2026-10-14T11:20:00+03:00",
                "delta_premium": e["delta_premium"],
                "currency": "GBP",
                "effective_at": b["effective_at"],
                "change": b["change"],
                "version": e["version"],
            }
            s["quotes"][qid] = q
            return {"ok": True, **copy.deepcopy(q)}
        elif tool == "get_renewal_quote":
            q = {
                "quote_id": e["renewal_quote_id"],
                "valid_until": e["valid_until"],
                "premium": e["renewal_premium"],
                "currency": "GBP",
                "term_start": "2027-01-01",
                "term_end": "2027-12-31",
                "auto_renew_supported": True,
            }
            s["quotes"][q["quote_id"]] = q
            return {"ok": True, **q}
        elif tool in {"apply_change", "commit_renewal"}:
            q = s["quotes"].get(b["quote_id"])
            if not q:
                return {"ok": False, "error": "UNKNOWN_QUOTE"}
            if datetime.fromisoformat(s["now"]) >= datetime.fromisoformat(q["valid_until"]):
                return {"ok": False, "error": "QUOTE_EXPIRED"}
            if tool == "apply_change":
                if "change" not in q:
                    return {"ok": False, "error": "WRONG_QUOTE_KIND"}
                if cid in {"J-02", "R09"} and e["version"] == 7:
                    e.update(version=8, delta_premium="40.00")
                    return {"ok": False, "error": "VERSION_CONFLICT"}
                if b["expected_version"] != e["version"] or q["version"] != e["version"]:
                    return {"ok": False, "error": "VERSION_CONFLICT"}
                e["version"] += 1
                s["endorsements"].append(
                    {
                        "id": e["endorsement_id"],
                        "effective_at": q["effective_at"],
                        "quote_id": q["quote_id"],
                        "delta_premium": q["delta_premium"],
                    }
                )
                result = {
                    "ok": True,
                    "new_version": e["version"],
                    "endorsement_id": e["endorsement_id"],
                }
            else:
                if "premium" not in q:
                    return {"ok": False, "error": "WRONG_QUOTE_KIND"}
                s["renewals"].append({"id": e["renewal_id"], "auto_renew": b["auto_renew"]})
                result = {"ok": True, "renewal_id": e["renewal_id"]}
        else:
            raise ValueError("Unknown extension tool")
        if key:
            s["operations"][key] = {
                "state": "SUCCEEDED",
                "result": copy.deepcopy(result),
                "request": {"tool": tool, "body": copy.deepcopy(b)},
            }
            if "claim_id" in result:
                s["operations"][key]["claim_id"] = result["claim_id"]
        if (cid == "I-04" and tool == "open_claim") or (cid == "J-08" and tool == "apply_change"):
            raise ConnectionError("Committed, response lost")
        return result


def make_server(bank, host="127.0.0.1", port=8765, admin_token=None, public_url=None):
    """Only tool calls may cross a tunnel. Fixture/admin/ledger routes require a token."""
    token = admin_token or secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass  # Never log capability session IDs or authorization headers.

        def reply(self, status, value):
            data = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def handle_request(self):
            path = self.path.split("?")[0]
            admin = path.startswith("/admin/")
            if admin and self.headers.get("Authorization") != "Bearer " + token:
                self.reply(401, {"error": "UNAUTHORIZED"})
                return
            try:
                body = {}
                if self.command == "POST":
                    size = int(self.headers.get("Content-Length", 0))
                    if not 0 < size <= 65536:
                        raise ValueError("Invalid body size")
                    body = json.loads(self.rfile.read(size))
                    if not isinstance(body, dict):
                        raise ValueError("Expected object")
                if path == "/health" and self.command == "GET":
                    self.reply(
                        200, {"ok": True, "synthetic": True, "instance_id": bank.instance_id}
                    )
                elif path == "/admin/sessions" and self.command == "POST":
                    self.reply(201, bank.open(body["case_id"]))
                elif path.startswith("/admin/ledger/") and self.command == "GET":
                    self.reply(200, bank.ledger(path.rsplit("/", 1)[1]))
                elif path.startswith("/admin/contracts/") and self.command == "GET":
                    code = path.rsplit("/", 1)[1].upper()
                    base = public_url or f"http://{host}:{self.server.server_port}"
                    self.reply(200, contract(code, base.rstrip("/") + "/v1"))
                elif path.startswith("/v1/") and self.command == "POST":
                    status, value = bank.call(path.rsplit("/", 1)[1], body)
                    if status == 0:
                        self.close_connection = True
                        self.connection.shutdown(2)
                        self.connection.close()
                    else:
                        self.reply(status, value)
                else:
                    self.reply(404, {"error": "NOT_FOUND"})
            except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                self.reply(400, {"error": "BAD_REQUEST"})

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.admin_token = token
    return server
