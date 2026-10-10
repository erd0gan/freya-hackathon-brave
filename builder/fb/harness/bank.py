"""Local stand-in for the practice bank (freya-bank-mock). Stdlib only.

Semantics come from the OpenAPI file, the briefs and kit/tools/score.py:
- identity fields and their answers come from the case; three wrong answers in total lock the
  check; an unreadable date is rejected and not counted; a day/month swap returns
  hint "day_month_swapped" and is not counted;
- get_account and create_promise answer `error: "identity not verified"` before both
  questions pass (score.py looks for that exact string);
- `faults: [{tool, fail_first_n}]` makes the first n calls of a tool fail with HTTP 500 and
  record nothing;
- nothing is accepted after close_call.

Exact error strings of the real mock other than "identity not verified" and
"unknown product_id" are not known; `tools/bank_parity.py` compares the two once a team key is
available. The ledger has the shape score.py reads.
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

NOT_VERIFIED = "identity not verified"
MAX_WRONG = 3


def _date(s: Any) -> datetime.date | None:
    m = re.fullmatch(r"\s*(\d{4})-(\d{2})-(\d{2})\s*", str(s or ""))
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _norm(field_name: str, s: Any) -> str:
    t = str(s or "").strip()
    if field_name in ("phone_last4", "postcode", "company_number"):
        return re.sub(r"[\s-]", "", t).upper()
    return t.lower()


@dataclass
class Session:
    case_id: str
    customer_id: str
    variables: dict[str, str]
    identity: dict[str, str]
    products: list[dict[str, Any]]
    faults: dict[str, int]
    verified: set[str] = field(default_factory=set)
    wrong: int = 0
    closed: bool = False
    ledger: dict[str, Any] = field(default_factory=dict)
    fault_hits: dict[str, int] = field(default_factory=dict)

    @property
    def all_verified(self) -> bool:
        return set(self.identity) <= self.verified

    @property
    def locked(self) -> bool:
        return self.wrong >= MAX_WRONG


class Bank:
    """In-process bank. `call(customer_id, tool, body) -> (http_status, json_body)`."""

    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    # ------------------------------------------------------------------ sessions
    def open_session(self, case: dict[str, Any], seed: str = "") -> dict[str, str]:
        """Like POST /v1/practice/sessions {"case": id}: returns the call variables."""
        h = hashlib.sha1(f"{case['id']}:{seed}:{len(self.sessions)}".encode()).hexdigest()
        cid = "c_" + h[:12]
        products = []
        for i, p in enumerate(case.get("products") or []):
            q = copy.deepcopy(p)
            q.setdefault("product_id", f"p{i + 1}")
            products.append(q)
        variables = {"customer_id": cid, **{k: str(v) for k, v in case["variables"].items()}}
        s = Session(
            case_id=case["id"],
            customer_id=cid,
            variables=variables,
            identity={k: str(v) for k, v in (case.get("identity") or {}).items()},
            products=products,
            faults={f["tool"]: int(f.get("fail_first_n", 0)) for f in case.get("faults") or []},
            ledger={
                "outcome": None,
                "reason": None,
                "promises": [],
                "notes": [],
                "callbacks": [],
                "transfers": [],
                "calls": [],
            },
        )
        self.sessions[cid] = s
        return dict(variables)

    def ledger(self, customer_id: str) -> dict[str, Any]:
        return copy.deepcopy(self.sessions[customer_id].ledger)

    # ------------------------------------------------------------------ dispatch
    def call(self, customer_id: str, tool: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        s = self.sessions.get(str(body.get("customer_id") or customer_id))
        if s is None:
            return 404, {"ok": False, "error": "unknown customer_id"}
        handler = getattr(self, "_" + tool, None)
        if handler is None:
            return 404, {"ok": False, "error": f"unknown tool {tool}"}
        if s.closed:
            status, out = 409, {"ok": False, "error": "call already closed"}
        elif s.fault_hits.get(tool, 0) < s.faults.get(tool, 0):
            s.fault_hits[tool] = s.fault_hits.get(tool, 0) + 1
            status, out = 500, {"ok": False, "error": "service unavailable"}
        else:
            status, out = handler(s, body)
        s.ledger["calls"].append(
            {
                "tool": tool,
                "args": copy.deepcopy(body),
                "status": status,
                "result": copy.deepcopy(out),
            }
        )
        return status, out

    # ------------------------------------------------------------------ tools
    def _verify_identity(self, s: Session, b: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        f = str(b.get("field") or "")
        if s.locked:
            return 200, {"ok": False, "error": "identity check locked", "wrong_answers_left": 0}
        if f not in s.identity:
            return 200, {"ok": False, "error": f"unknown field {f}"}
        ans = b.get("answer")
        truth = s.identity[f]
        base = {"ok": True}
        if f == "date_of_birth":
            d = _date(ans)
            if d is None:
                return 200, {"ok": False, "error": "answer is not a date (YYYY-MM-DD)"}
            t = _date(truth)
            if d == t:
                s.verified.add(f)
            elif (
                t is not None
                and d.day != d.month
                and (d.year, d.month, d.day) == (t.year, t.day, t.month)
            ):
                return 200, {
                    **base,
                    "verified": False,
                    "hint": "day_month_swapped",
                    "wrong_answers_left": MAX_WRONG - s.wrong,
                    "all_verified": s.all_verified,
                }
            else:
                s.wrong += 1
        elif _norm(f, ans) == _norm(f, truth):
            s.verified.add(f)
        else:
            s.wrong += 1
        return 200, {
            **base,
            "verified": f in s.verified,
            "wrong_answers_left": max(0, MAX_WRONG - s.wrong),
            "all_verified": s.all_verified,
        }

    def _get_account(self, s: Session, b: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not s.all_verified:
            return 200, {"ok": False, "error": NOT_VERIFIED}
        return 200, {"ok": True, "products": copy.deepcopy(s.products)}

    def _product(self, s: Session, pid: Any) -> dict[str, Any] | None:
        return next((p for p in s.products if p["product_id"] == str(pid)), None)

    def _create_promise(self, s: Session, b: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if not s.all_verified:
            return 200, {"ok": False, "error": NOT_VERIFIED}
        if self._product(s, b.get("product_id")) is None:
            return 200, {"ok": False, "error": "unknown product_id"}
        if any(p["product_id"] == str(b.get("product_id")) for p in s.ledger["promises"]):
            return 200, {"ok": False, "error": "a promise is already recorded for this product"}
        try:
            amount = float(b.get("amount"))
        except (TypeError, ValueError):
            return 200, {"ok": False, "error": "amount is not a number"}
        if _date(b.get("date")) is None:
            return 200, {"ok": False, "error": "date is not YYYY-MM-DD"}
        pid = f"pr_{len(s.ledger['promises']) + 1}"
        s.ledger["promises"].append(
            {"product_id": str(b["product_id"]), "amount": amount, "date": str(b["date"]).strip()}
        )
        return 200, {"ok": True, "promise_id": pid}

    def _add_note(self, s: Session, b: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self._product(s, b.get("product_id")) is None:
            return 200, {"ok": False, "error": "unknown product_id"}
        s.ledger["notes"].append(
            {
                "product_id": str(b["product_id"]),
                "reason": str(b.get("reason") or ""),
                "text": str(b.get("text") or ""),
            }
        )
        return 200, {"ok": True}

    def _schedule_callback(self, s: Session, b: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if _date(b.get("date")) is None:
            return 200, {"ok": False, "error": "date is not YYYY-MM-DD"}
        if not re.fullmatch(r"\d{2}:\d{2}", str(b.get("time") or "").strip()):
            return 200, {"ok": False, "error": "time is not HH:MM"}
        s.ledger["callbacks"].append(
            {"date": str(b["date"]).strip(), "time": str(b["time"]).strip()}
        )
        return 200, {"ok": True}

    def _transfer_to_agent(self, s: Session, b: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        s.ledger["transfers"].append({"queue": str(b.get("queue") or "")})
        return 200, {"ok": True}

    def _close_call(self, s: Session, b: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        s.ledger["outcome"] = str(b.get("outcome") or "") or None
        r = b.get("reason")
        s.ledger["reason"] = str(r) if r not in (None, "") else None
        s.closed = True
        return 200, {"ok": True}


def envelope(
    status: int, body: dict[str, Any], url: str = "", method: str = "POST"
) -> dict[str, Any]:
    """What a Freya `api_call` tool hands to hooks and the model (tools-and-code.md)."""
    ok = 200 <= status < 300
    return {
        "status": "success" if ok else "error",
        "data": body,
        "message": f"Successfully executed {method} request"
        if ok
        else f"Request failed with status {status}",
        "status_code": status,
        "header": {"Content-Type": "application/json"},
    }


def flatten(prefix: str, x: Any, out: dict[str, str] | None = None) -> dict[str, str]:
    """Flattened `<tool>_response.*` keys. Booleans become lower-case strings, as seen in trials."""
    out = {} if out is None else out
    if isinstance(x, dict):
        for k, v in x.items():
            flatten(f"{prefix}.{k}" if prefix else str(k), v, out)
    elif isinstance(x, list):
        out[prefix] = json.dumps(x)
    elif isinstance(x, bool):
        out[prefix] = "true" if x else "false"
    elif x is None:
        out[prefix] = ""
    else:
        out[prefix] = str(x)
    return out
