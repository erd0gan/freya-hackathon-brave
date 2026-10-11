"""Deterministic post-call reviewer: brief rules a finished call must keep, read from its tool
calls and, when the transcript is available, from what was said in between. No model calls.

    review_call(says, calls, cfg) -> [Finding(rule, severity, turn, evidence)]

Inputs, as they exist in this repo:
- `calls`: the bank ledger's `calls` list, one {tool, args, status, result} per call
  (fb/harness/bank.py Bank.call), e.g. `SimResult.ledger["calls"]` of engine_sim.simulate.
- `says`: either the agent's lines as plain strings (`SimResult.says`; their order relative to
  the tool calls is unknown, so the rules about what was said *before* a call are skipped), or
  a message list in order. Message entries may be
    {"role": "assistant" | "user", "content" | "message": text}
    {"role": "tool", "tool": name, "args": {...}, "status": int, "result": {...}}
    Freya's get_call()["messages"] (kit/docs/api.md): role "system" entries whose `message`
    is a JSON string with event function_call_start {function, args} and
    function_call_complete {function, result}. For an api_call the args are the request
    ({url, method, headers, body}) and the result the tool envelope
    ({status, data, status_code}); both are unwrapped here. Workflow-level end_of_call
    tools (close_call) may not appear in the messages: pass the ledger's calls as `calls`
    for R1-R3/R5 and keep the messages for the ordering rules.
  When `calls` is empty the tool calls are taken from the messages.

Rules (severity "critical" breaks a brief rule or a scorer check; "warning" is a heuristic):
  R1 no gated tool (get_account, create_promise, add_note) before a successful verify
  R2 no second create_promise for a product after one succeeded (a retry after a failed call
     is fine)
  R3 the last tool call is close_call, with outcome / reason in the allowed enums
  R4 create_promise only after an assistant read-back holding the amount and a customer turn
     after it (messages only)
  R5 a promise lies within [floor, full past due] and on or before the product's deadline
     (when cfg["products"] gives them)
  R6 no assistant line claims a promise / callback / transfer / note is done before the
     matching successful tool call (messages only; warning)
  R7 no forbidden word before identity is verified (messages only)

cfg keys (all optional): verify_tool, gated_tools, promise_tool, close_tool, outcomes,
reasons, products {product_id: {floor, full, deadline}}, identity_fields,
forbidden_before_verify, bank_name.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any

CRITICAL = "critical"
WARNING = "warning"
GATED = ("get_account", "create_promise", "add_note")
BASE_FORBIDDEN = ("debt", "collections", "past due", "overdue")

CLAIMS = {
    "create_promise": re.compile(
        r"\b(promise|payment|arrangement)\b[^.?!]{0,60}?\b(is|has been|was|have been)\s+"
        r"(recorded|saved|registered|logged|set up)\b"
        r"|\bI(?:'ve| have)\s+(recorded|saved|registered|logged)\b",
        re.I,
    ),
    "schedule_callback": re.compile(
        r"\b(call ?back|call)\b[^.?!]{0,60}?\b(is|has been|was)\s+(scheduled|booked|arranged)\b"
        r"|\bI(?:'ve| have)\s+(scheduled|booked|arranged)\b",
        re.I,
    ),
    "transfer_to_agent": re.compile(
        r"\b(has been|have been|was|were|I(?:'ve| have))\s+transferred\b", re.I
    ),
    "add_note": re.compile(
        r"\bnotes?\b[^.?!]{0,40}?\b(is|are|has been|have been|was|were)\s+(added|recorded|saved)\b",
        re.I,
    ),
}
NUMBER = re.compile(r"\d+(?:\.\d+)?")


@dataclass
class Finding:
    rule: str
    severity: str
    turn: int
    evidence: str


# ---------------------------------------------------------------- inputs
def _text(m: dict[str, Any]) -> str:
    v = m.get("content", m.get("message", ""))
    return v if isinstance(v, str) else json.dumps(v)


def _unwrap(args: Any, result: Any) -> tuple[dict[str, Any], int, dict[str, Any]]:
    """Freya's function_call args / result -> (body, http status, response body)."""
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    args = args if isinstance(args, dict) else {}
    body = args.get("body") if isinstance(args.get("body"), dict) else args
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            result = {"raw": result}
    result = result if isinstance(result, dict) else {}
    if "status_code" in result or ("status" in result and "data" in result):
        try:
            status = int(result.get("status_code") or 0)
        except (TypeError, ValueError):
            status = 0
        if not status:
            status = 200 if result.get("status") == "success" else 500
        data = result.get("data")
        return body, status, data if isinstance(data, dict) else {"data": data}
    return body, 200, result


def _fname(f: Any) -> str:
    if isinstance(f, dict):
        return str(f.get("name") or "")
    return str(f or "")


def events_from_messages(msgs: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    """Ordered events: ("assistant", text), ("user", text), ("tool", call dict)."""
    out: list[tuple[str, Any]] = []
    open_calls: dict[str, list[int]] = {}
    for m in msgs:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role in ("assistant", "user"):
            out.append((role, _text(m)))
        elif role == "tool":
            out.append(
                (
                    "tool",
                    {
                        "tool": str(m.get("tool") or m.get("name") or ""),
                        "args": m.get("args") or {},
                        "status": int(m.get("status") or 200),
                        "result": m.get("result") or {},
                    },
                )
            )
        elif role == "system":
            try:
                ev = json.loads(_text(m))
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            name = _fname(ev.get("function"))
            if ev.get("event") == "function_call_start":
                body, _, _ = _unwrap(ev.get("args"), {})
                call = {"tool": name, "args": body, "status": 0, "result": {}}
                open_calls.setdefault(name, []).append(len(out))
                out.append(("tool", call))
            elif ev.get("event") == "function_call_complete":
                _, status, res = _unwrap({}, ev.get("result"))
                waiting = open_calls.get(name) or []
                if waiting:
                    call = out[waiting.pop(0)][1]
                    call["status"], call["result"] = status, res
                else:
                    out.append(
                        ("tool", {"tool": name, "args": {}, "status": status, "result": res})
                    )
    return out


# ---------------------------------------------------------------- helpers
def ok(call: dict[str, Any]) -> bool:
    try:
        status = int(call.get("status") or 0)
    except (TypeError, ValueError):
        status = 0
    res = call.get("result") if isinstance(call.get("result"), dict) else {}
    return 200 <= status < 300 and res.get("ok") is not False and not res.get("error")


def _truthy(x: Any) -> bool:
    return x is True or str(x).lower() == "true"


class _Verify:
    """Tracks when identity counts as verified: a verify answer with all_verified true, or,
    for an API without that flag, every asked (or configured) field verified."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.fields = set(cfg.get("identity_fields") or [])
        self.passed: set[str] = set()
        self.asked: set[str] = set()
        self.flag_seen = False
        self.done = False

    def see(self, call: dict[str, Any]) -> None:
        res = call.get("result") if isinstance(call.get("result"), dict) else {}
        args = call.get("args") if isinstance(call.get("args"), dict) else {}
        fld = str(args.get("field") or args.get("question") or len(self.asked))
        self.asked.add(fld)
        if "all_verified" in res:
            self.flag_seen = True
            if ok(call) and _truthy(res.get("all_verified")):
                self.done = True
        if ok(call) and _truthy(res.get("verified")):
            self.passed.add(fld)
        if not self.flag_seen and self.passed:
            need = self.fields or self.asked
            if need <= self.passed:
                self.done = True


def _numbers(text: str) -> set[float]:
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    return {float(x) for x in NUMBER.findall(t)}


def _num(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- review
def review_call(
    says: list[Any] | None,
    calls: list[dict[str, Any]] | None = None,
    cfg: dict[str, Any] | None = None,
) -> list[Finding]:
    cfg = cfg or {}
    says = list(says or [])
    ordered = bool(says) and all(isinstance(s, dict) for s in says)
    events = events_from_messages(says) if ordered else [("assistant", str(s)) for s in says]
    msg_calls = [c for kind, c in events if kind == "tool"]
    tool_calls = list(calls) if calls else msg_calls
    # the ordering rules read the message events; without tool events in them they cannot
    ordered = ordered and bool(msg_calls)
    out: list[Finding] = []
    out += _tool_rules(tool_calls, cfg)
    if ordered:
        out += _order_rules(events, cfg)
    return out


def _tool_rules(calls: list[dict[str, Any]], cfg: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    verify_tool = cfg.get("verify_tool", "verify_identity")
    gated = set(cfg.get("gated_tools") or GATED)
    promise = cfg.get("promise_tool", "create_promise")
    close = cfg.get("close_tool", "close_call")
    products = cfg.get("products") or {}
    v = _Verify(cfg)
    promised: set[str] = set()
    for i, c in enumerate(calls):
        tool = c.get("tool")
        args = c.get("args") if isinstance(c.get("args"), dict) else {}
        if tool == verify_tool:
            v.see(c)
        elif tool in gated and not v.done:
            out.append(Finding("R1", CRITICAL, i, f"{tool} before identity was verified"))
        if tool == promise:
            pid = str(args.get("product_id"))
            if pid in promised:
                out.append(
                    Finding("R2", CRITICAL, i, f"second {promise} for {pid} after one succeeded")
                )
            if ok(c):
                promised.add(pid)
            lim = products.get(pid) or {}
            amt = _num(args.get("amount"))
            lo, hi = _num(lim.get("floor")), _num(lim.get("full"))
            if amt is not None and lo is not None and amt < lo - 0.005:
                out.append(Finding("R5", CRITICAL, i, f"{pid}: {amt:.2f} below floor {lo:.2f}"))
            if amt is not None and hi is not None and amt > hi + 0.005:
                out.append(Finding("R5", CRITICAL, i, f"{pid}: {amt:.2f} above full {hi:.2f}"))
            date, dl = str(args.get("date") or ""), str(lim.get("deadline") or "")
            if date and dl and date[:10] > dl[:10]:
                out.append(Finding("R5", CRITICAL, i, f"{pid}: date {date} after deadline {dl}"))
    closes = [i for i, c in enumerate(calls) if c.get("tool") == close]
    if not calls or calls[-1].get("tool") != close:
        last = calls[-1].get("tool") if calls else "no call"
        out.append(Finding("R3", CRITICAL, len(calls), f"last tool call is {last}, not {close}"))
    if len(closes) > 1:
        out.append(Finding("R3", WARNING, closes[1], f"{close} called {len(closes)} times"))
    if closes:
        a = calls[closes[-1]].get("args") or {}
        outcomes, reasons = cfg.get("outcomes"), cfg.get("reasons")
        if outcomes and str(a.get("outcome")) not in set(map(str, outcomes)):
            out.append(Finding("R3", CRITICAL, closes[-1], f"outcome {a.get('outcome')!r}"))
        r = a.get("reason")
        if reasons and r not in (None, "") and str(r) not in set(map(str, reasons)):
            out.append(Finding("R3", CRITICAL, closes[-1], f"reason {r!r}"))
    return out


def _order_rules(events: list[tuple[str, Any]], cfg: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    verify_tool = cfg.get("verify_tool", "verify_identity")
    promise = cfg.get("promise_tool", "create_promise")
    bank = str(cfg.get("bank_name") or "").lower()
    words = set(BASE_FORBIDDEN) | {
        str(w).lower() for w in cfg.get("forbidden_before_verify") or [] if str(w).strip()
    }
    v = _Verify(cfg)
    done: set[str] = set()
    said: list[tuple[int, str]] = []
    users: list[int] = []
    for i, (kind, x) in enumerate(events):
        if kind == "tool":
            tool = x.get("tool")
            if tool == verify_tool:
                v.see(x)
            if tool == promise:
                amt = _num((x.get("args") or {}).get("amount"))
                rb = [j for j, t in said if amt is not None and amt in _numbers(t)]
                if amt is None or not rb or not any(rb[-1] < k < i for k in users):
                    why = "no read-back with the amount" if not rb else "no customer turn after"
                    out.append(Finding("R4", CRITICAL, i, f"{promise} {amt}: {why}"))
            if ok(x):
                done.add(str(tool))
            continue
        if kind == "user":
            users.append(i)
            continue
        text = str(x)
        said.append((i, text))
        low = text.lower().replace(bank, " ") if bank else text.lower()
        if not v.done:
            hit = sorted(w for w in words if re.search(r"\b" + re.escape(w) + r"\b", low))
            if hit:
                out.append(Finding("R7", CRITICAL, i, f"said {hit} before verification"))
        for tool, pat in CLAIMS.items():
            m = pat.search(text)
            if m and tool not in done:
                out.append(Finding("R6", WARNING, i, f"claims {m.group(0)!r} before {tool}"))
    return out


# ---------------------------------------------------------------- glue
def critical(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == CRITICAL]


def as_dicts(findings: list[Finding]) -> list[dict[str, Any]]:
    return [asdict(f) for f in findings]


def limits_from_vars(v: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{product_id: {floor, full, deadline}} from the facts code node's p<i>_* variables."""
    out: dict[str, dict[str, Any]] = {}
    i = 1
    while f"p{i}_id" in v:
        out[str(v[f"p{i}_id"])] = {
            "floor": v.get(f"p{i}_floor"),
            "full": v.get(f"p{i}_past_due"),
            "deadline": v.get(f"p{i}_deadline"),
        }
        i += 1
    return out


def enums_from_tools(tools: dict[str, Any], close_tool: str = "close_call") -> dict[str, Any]:
    """Allowed close_call outcomes / reasons from parse_openapi's tools."""
    t = tools.get(close_tool)
    out: dict[str, Any] = {}
    if t is None:
        return out
    for key, names in (("outcomes", ("outcome",)), ("reasons", ("reason",))):
        f = next((f for f in t.fields if f.name in names), None)
        if f is not None and f.enum:
            out[key] = list(f.enum)
    return out


def review_sim(res: Any, cfg: dict[str, Any] | None = None) -> list[Finding]:
    """Review an engine_sim.SimResult: its ledger calls, with the product limits the facts
    node computed."""
    c = dict(cfg or {})
    c.setdefault("products", limits_from_vars(getattr(res, "vars", {}) or {}))
    return review_call(res.says, (res.ledger or {}).get("calls") or [], c)
