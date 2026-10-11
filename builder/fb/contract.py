"""A service contract written as a document (Markdown, Word, PDF tables) -> an OpenAPI 3
document the rest of the builder reads like any tool API file. Stdlib only.

One model call writes the OpenAPI JSON from the contract's text; deterministic checks then
hold it to the text: every path segment, field name and enum value must be in the contract,
methods too when the contract names them, every required field must be a property, and the
seven kit roles must each map to one operation (`x-kit-role`, which parse_openapi honours).
What the checks find goes back to the model once (one repair round); what is left is
returned with the document, for the trace. Nothing here holds a bank's names or values.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from fb.openapi_tools import KIT_TOOLS, ToolApiError, parse_openapi

ROLE_HELP = {
    "verify_identity": "checks one identity/security answer of the customer",
    "get_account": "reads the customer's debts, products or account details",
    "create_promise": "records a promise to pay (an amount on a date)",
    "add_note": "records a note or the reason a debt has no promise",
    "schedule_callback": "books a callback at a day and time",
    "transfer_to_agent": "hands the call to a person / a queue",
    "close_call": "records how the call ended (outcome, reason)",
}

PROMPT = """You turn a bank's service contract into one OpenAPI 3.0 JSON document. The contract
is a document for people (tables, lists, prose), possibly in a language other than English.
A phone agent's tools will call these services, so the document must say exactly what the
contract says.

Rules:
- Only what the contract says. Every path, HTTP method, field name and allowed value comes
  from the contract, written exactly as there (same spelling, case and language). Invent
  nothing; leave out what the contract does not give.
- One operation per service. operationId: the contract's own name for the service.
- Every operation gets "x-kit-role": the role it plays for a debt-collection phone agent, one of
{roles}
  or null when it plays none of them. Each role is used at most once. If the contract has no
  service for a role, add the role to the top-level "x-missing-roles" object with a short
  reason.
- Request: requestBody application/json with an object schema: properties (type, format,
  enum, description from the contract) and "required" as the contract marks it. A value the
  contract puts in the URL is a path or query parameter.
- Response: responses."200" application/json with the response fields the contract lists,
  nested objects and arrays (items) as it shows them.
- Descriptions: copy what the contract says a field means: its unit (an amount in minor units
  such as cents or kuruş says so), its format (dates, times), whether a count is the number
  used or the number left.
- servers: [{{"url": "..."}}] only when the contract gives a base URL.

Reply with the JSON document only.

Service contract:
<<<
{contract}
>>>"""

HTTP = ("get", "post", "put", "patch", "delete")


def _fold(s: Any) -> str:
    return str(s).translate(str.maketrans("ıİşŞğĞüÜöÖçÇ", "iissgguuoocc")).lower()


def _in_text(word: str, text: str) -> bool:
    """`word` written in the contract (case-insensitive, not inside a longer name)."""
    w = _fold(word).strip()
    if not w:
        return True
    return bool(re.search(r"(?<![\w])" + re.escape(w) + r"(?![\w])", text))


def _props(doc: dict[str, Any], node: Any, depth: int = 0) -> list[tuple[str, dict[str, Any]]]:
    """(name, schema) of every property in a schema, nested objects and array items too."""
    out: list[tuple[str, dict[str, Any]]] = []
    if depth > 10 or not isinstance(node, dict):
        return out
    if "$ref" in node:
        cur: Any = doc
        try:
            for part in str(node["$ref"]).lstrip("#/").split("/"):
                cur = cur[part]
        except (KeyError, TypeError):
            return out
        return _props(doc, cur, depth + 1)
    for key in ("allOf", "oneOf", "anyOf"):
        for part in node.get(key) or []:
            out += _props(doc, part, depth + 1)
    for k, v in (node.get("properties") or {}).items():
        if isinstance(v, dict):
            out.append((str(k), v))
            out += _props(doc, v, depth + 1)
    if isinstance(node.get("items"), dict):
        out += _props(doc, node["items"], depth + 1)
    return out


def check_openapi(doc: Any, contract_text: str) -> list[str]:
    """What in `doc` the contract does not back, and what the build needs that is missing."""
    if not isinstance(doc, dict) or not isinstance(doc.get("paths"), dict) or not doc["paths"]:
        return ["the reply is not an OpenAPI document with paths"]
    text = _fold(contract_text)
    lines = text.splitlines()
    names_methods = bool(re.search(r"\b(get|post|put|patch|delete)\b", text))
    bad: list[str] = []
    roles: dict[str, list[str]] = {}
    for path, item in doc["paths"].items():
        if not isinstance(item, dict):
            bad.append(f"path {path}: not an object")
            continue
        segs = [s for s in str(path).split("/") if s and not s.startswith("{")]
        missing = [s for s in segs if not _in_text(s, text)]
        if missing:
            bad.append(f"path {path}: {missing} not in the contract")
        for method, op in item.items():
            if method.lower() not in HTTP or not isinstance(op, dict):
                continue
            where = f"{method.upper()} {path}"
            if names_methods and segs:
                last = _fold(segs[-1])
                if not any(method.lower() in ln and last in ln for ln in lines):
                    bad.append(f"{where}: the contract does not give {method.upper()} for it")
            role = op.get("x-kit-role")
            if role:
                if role not in KIT_TOOLS:
                    bad.append(f"{where}: x-kit-role {role!r} is not one of {list(KIT_TOOLS)}")
                else:
                    roles.setdefault(str(role), []).append(where)
            fields: list[tuple[str, dict[str, Any]]] = []
            body = op.get("requestBody") or {}
            for media in (body.get("content") or {}).values() if isinstance(body, dict) else []:
                schema = (media or {}).get("schema") or {}
                fields += _props(doc, schema)
                for r in schema.get("required") or [] if isinstance(schema, dict) else []:
                    if r not in (schema.get("properties") or {}):
                        bad.append(f"{where}: required {r!r} is not a property of the body")
            for p in op.get("parameters") or []:
                if isinstance(p, dict) and p.get("name"):
                    fields.append((str(p["name"]), p.get("schema") or {}))
            for resp in (op.get("responses") or {}).values():
                for media in ((resp or {}).get("content") or {}).values():
                    fields += _props(doc, (media or {}).get("schema") or {})
            unknown = sorted({n for n, _ in fields if not _in_text(n, text)})
            if unknown:
                bad.append(f"{where}: field names not in the contract: {unknown}")
            values = sorted(
                {
                    str(e)
                    for _, s in fields
                    for e in (s.get("enum") or [] if isinstance(s, dict) else [])
                    if isinstance(e, str) and not _in_text(e, text)
                }
            )
            if values:
                bad.append(f"{where}: allowed values not in the contract: {values}")
    for role, where in roles.items():
        if len(where) > 1:
            bad.append(f"x-kit-role {role} is given to {where}; one operation per role")
    declared = doc.get("x-missing-roles") if isinstance(doc.get("x-missing-roles"), dict) else {}
    unmapped = [r for r in KIT_TOOLS if r not in roles and r not in declared]
    if unmapped:
        bad.append(
            f"roles with no operation: {unmapped}; give each the operation that plays it "
            "(x-kit-role), or list it in x-missing-roles with the reason"
        )
    try:
        tools = parse_openapi(doc, base_url="https://contract-check.invalid")
    except (ToolApiError, ValueError, TypeError, KeyError, AttributeError) as e:
        bad.append(f"the document cannot be read as tools: {type(e).__name__}: {e}")
    else:
        for name, t in tools.items():
            for w in t.warnings:
                bad.append(f"{name}: {w}")
    return bad


def contract_to_openapi(
    contract_text: str,
    trace: Any,
    *,
    deadline: float | None = None,
    chat_fn: Callable[..., str] | None = None,
    parse_fn: Callable[[str], Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """(OpenAPI dict or None, problems left). One model call, the checks, one repair round
    when they find something; every step is a trace line. None when no reply could be read
    as JSON."""
    if chat_fn is None or parse_fn is None:
        from fb import llm

        chat_fn = chat_fn or llm.chat
        parse_fn = parse_fn or llm.parse_json
    roles = "\n".join(f"  {r}: {h}" for r, h in ROLE_HELP.items())
    msgs = [{"role": "user", "content": PROMPT.format(roles=roles, contract=contract_text.strip())}]
    doc: Any = None
    problems: list[str] = []
    for attempt in (1, 2):
        purpose = (
            "turn the service contract into OpenAPI"
            if attempt == 1
            else "repair the OpenAPI written from the service contract"
        )
        reply = chat_fn(trace, purpose, msgs, deadline=deadline)
        try:
            new = parse_fn(reply)
        except (ValueError, TypeError) as e:
            new = None
            problems = [f"the reply is not JSON: {type(e).__name__}: {str(e)[:200]}"]
        if new is not None:
            doc = new
            if isinstance(doc, dict):
                doc.setdefault("openapi", "3.0.3")
            problems = check_openapi(doc, contract_text)
        trace.note(
            f"contract: OpenAPI {'written' if attempt == 1 else 'repaired'}, "
            f"{len((doc or {}).get('paths') or {}) if isinstance(doc, dict) else 0} paths, "
            f"{len(problems)} check problems"
        )
        for p in problems:
            trace.note(f"contract check: {p}")
        if not problems or attempt == 2:
            break
        msgs += [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": "These checks of your document against the contract failed:\n- "
                + "\n- ".join(problems)
                + "\nFix them from the contract's own words (never invent a name or value) "
                "and reply with the whole corrected JSON document only.",
            },
        ]
    if not isinstance(doc, dict):
        return None, problems
    return doc, problems


def describe(doc: dict[str, Any]) -> str:
    """One line: the operations and their roles, for the trace."""
    out = []
    for path, item in (doc.get("paths") or {}).items():
        for m, op in (item or {}).items():
            if isinstance(op, dict) and m.lower() in HTTP:
                out.append(f"{m.upper()} {path} -> {op.get('x-kit-role') or '-'}")
    return "; ".join(out) + (
        f"; missing roles: {json.dumps(doc.get('x-missing-roles'), ensure_ascii=False)}"
        if doc.get("x-missing-roles")
        else ""
    )
