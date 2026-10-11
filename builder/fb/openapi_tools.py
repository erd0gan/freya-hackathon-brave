"""OpenAPI file -> Freya `api_call` tool functions. Pure code: every field, required
flag and enum comes from the file (hidden briefs change fields and allowed values).

`parse_openapi(doc, base_url=None)`:
- a tool recognised as one of the seven kit roles (KIT_TOOLS) is keyed and named by the kit
  name whatever the operation is called (verifyIdentity, bank_get_account, /identity/verify, a
  Turkish name or summary); `ToolDef.api_name` keeps the original. Unrecognised tools keep
  their own name.
- `base_url` (the mock_url of the build command) replaces scheme and host of every tool URL;
  the servers[0] path prefix (/v1) is kept unless base_url has a path of its own. Without
  servers and without base_url the URLs would be relative: ToolApiError.
- Swagger 2.0 files are read as OpenAPI 3 (body/formData parameters, host/basePath).
- `ToolDef.response_scale`: response leaf path -> factor to major units (0.01 for an amount
  the contract gives in cents / kuruş / minor units).
- A single date-time field of a promise or callback call is split into `date` and `time`
  parts the workflow binds; the request hook joins them back (ToolField.part_of)."""

from __future__ import annotations

import copy
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

# The seven tool roles the generated workflow calls (kit/README.md: "the seven tool names stay
# the same"); a contract may still spell them its own way.
KIT_TOOLS = (
    "verify_identity",
    "get_account",
    "create_promise",
    "add_note",
    "schedule_callback",
    "transfer_to_agent",
    "close_call",
)


class ToolApiError(ValueError):
    """The tool API cannot give the workflow working tools (relative URLs)."""


@dataclass
class ToolField:
    name: str
    type: str = "string"
    description: str = ""
    enum: list[str] = field(default_factory=list)
    required: bool = False
    format: str = ""
    default: Any = None
    pattern: str = ""
    # where the API takes it: "body" (the JSON body), "query" or "path" (a URL parameter,
    # carried in the body like the rest and moved into the URL by request_hook)
    location: str = "body"
    # a part of another field: the `date` / `time` halves of a single date-time field, sent
    # joined as that field by request_hook ('' for a real field)
    part_of: str = ""


def fixed_value(f: ToolField) -> Any:
    """A value the tool API itself fixes for a field: its `default`, or the only value of a
    one-value enum. None when the API leaves it open."""
    if f.default is not None:
        return f.default
    if len(f.enum) == 1:
        return f.enum[0]
    return None


@dataclass
class ToolDef:
    name: str
    url: str
    method: str
    summary: str
    fields: list[ToolField]
    response: dict[str, Any] = field(default_factory=dict)
    # The 200 response's fields with $refs resolved and allOf/oneOf/anyOf parts merged
    # (`response` keeps the schema as written); {} when the schema lists none.
    response_props: dict[str, Any] = field(default_factory=dict)
    # the operation's own name (operationId or last path segment); `name` is the kit role's
    # name when the operation was recognised as one
    api_name: str = ""
    # response leaf path (dots; an array is entered without an index: "products.past_due")
    # -> factor that turns the value into major units (0.01 for cents / kuruş)
    response_scale: dict[str, float] = field(default_factory=dict)
    # header parameters with the value the contract fixes for them (default, single enum)
    headers: dict[str, str] = field(default_factory=dict)
    # what parse_openapi could not read for this tool (for the trace)
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.api_name:
            self.api_name = self.name

    def field(self, name: str) -> ToolField | None:
        return next((f for f in self.fields if f.name == name), None)


def _ref_parts(ref: Any) -> list[str]:
    """A local JSON pointer's parts ("#/components/schemas/a~1b%20c" -> [..., "a/b c"]); an
    external reference (another file) raises KeyError: it cannot be read here."""
    ref = str(ref)
    if not ref.startswith("#"):
        raise KeyError(ref)
    parts = urllib.parse.unquote(ref[1:]).lstrip("/").split("/")
    return [p.replace("~1", "/").replace("~0", "~") for p in parts if p != ""]


def _resolve(spec: dict[str, Any], node: Any, depth: int = 0) -> Any:
    if isinstance(node, dict) and "$ref" in node:
        if depth > 40:  # a $ref cycle
            raise RecursionError(str(node["$ref"]))
        cur: Any = spec
        for part in _ref_parts(node["$ref"]):
            cur = cur[int(part)] if isinstance(cur, list) else cur[part]
        return _resolve(spec, cur, depth + 1)
    return node


def unresolved_refs(spec: Any) -> list[str]:
    """Every $ref in the document that does not resolve (another file, a typo, a cycle)."""
    bad: list[str] = []
    seen: set[str] = set()

    def walk(x: Any, depth: int) -> None:
        if depth > 60:
            return
        if isinstance(x, dict):
            ref = x.get("$ref")
            if isinstance(ref, str) and ref not in seen:
                seen.add(ref)
                try:
                    _resolve(spec, {"$ref": ref})
                except (KeyError, TypeError, IndexError, ValueError, RecursionError):
                    bad.append(ref)
            for v in x.values():
                walk(v, depth + 1)
        elif isinstance(x, list):
            for v in x:
                walk(v, depth + 1)

    walk(spec, 0)
    return bad


def response_props(spec: dict[str, Any], node: Any, depth: int = 0) -> dict[str, Any]:
    """The properties a response schema lists: $refs followed, allOf/oneOf/anyOf parts merged
    (create_promise 200 = allOf[{$ref Error}, {properties: {promise_id}}]). A $ref that does
    not resolve adds nothing."""
    if depth > 20:
        return {}
    try:
        node = _resolve(spec, node)
    except (KeyError, TypeError, IndexError, ValueError, RecursionError):
        return {}
    if not isinstance(node, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("allOf", "oneOf", "anyOf"):
        for part in node.get(key) or []:
            out.update(response_props(spec, part, depth + 1))
    if isinstance(node.get("properties"), dict):
        out.update(node["properties"])
    return out


def _safe(spec: dict[str, Any], node: Any) -> Any:
    try:
        return _resolve(spec, node)
    except (KeyError, TypeError, IndexError, ValueError, RecursionError):
        return None


def _flat(spec: dict[str, Any], node: Any, depth: int = 0) -> dict[str, Any]:
    """A schema with its $ref followed and its allOf parts merged into one dict (a field
    written as allOf[{$ref Outcome}] with a description of its own). oneOf/anyOf parts only
    fill keys the schema leaves out."""
    node = _safe(spec, node)
    if not isinstance(node, dict) or depth > 20:
        return {}
    out: dict[str, Any] = {}

    def merge(k: str, v: Any) -> None:
        if k == "properties" and isinstance(out.get(k), dict) and isinstance(v, dict):
            out[k] = {**out[k], **v}
        elif k == "required":
            have = list(out.get(k) or [])
            out[k] = have + [x for x in v or [] if x not in have]
        else:
            out[k] = v

    for part in node.get("allOf") or []:
        for k, v in _flat(spec, part, depth + 1).items():
            merge(k, v)
    for k, v in node.items():
        if k != "allOf":
            merge(k, v)
    for key in ("oneOf", "anyOf"):
        parts = [_flat(spec, p, depth + 1) for p in node.get(key) or []]
        parts = [p for p in parts if p.get("type") != "null"]
        for p in parts:
            for k, v in p.items():
                if k not in ("properties", "required"):
                    out.setdefault(k, v)
    if isinstance(out.get("type"), list):  # OpenAPI 3.1: ["string", "null"]
        out["type"] = next((t for t in out["type"] if t != "null"), "string")
    return out


def body_schema(spec: dict[str, Any], node: Any, depth: int = 0) -> tuple[dict[str, Any], list]:
    """(properties, required) of a request body schema: $refs followed, allOf parts merged;
    oneOf/anyOf parts give their properties, and a field is required there only when every
    part requires it (any part may be the one sent)."""
    node = _safe(spec, node)
    if not isinstance(node, dict) or depth > 20:
        return {}, []
    props: dict[str, Any] = {}
    req: list[str] = []
    for part in node.get("allOf") or []:
        p, r = body_schema(spec, part, depth + 1)
        props.update(p)
        req += [x for x in r if x not in req]
    for key in ("oneOf", "anyOf"):
        parts = [body_schema(spec, p, depth + 1) for p in node.get(key) or []]
        for p, _ in parts:
            for k, v in p.items():
                props.setdefault(k, v)
        if parts:
            common = set.intersection(*(set(r) for _, r in parts))
            req += [x for x in parts[0][1] if x in common and x not in req]
    if isinstance(node.get("properties"), dict):
        props.update(node["properties"])
    req += [str(x) for x in node.get("required") or [] if x not in req]
    return props, req


def json_content(content: Any) -> dict[str, Any]:
    """The JSON media type entry of a `content` map: application/json, else one with
    parameters (application/json; charset=utf-8) or a vendor type (+json), else the only one."""
    if not isinstance(content, dict) or not content:
        return {}
    if isinstance(content.get("application/json"), dict):
        return content["application/json"]
    for k, v in content.items():
        if str(k).lower().split(";")[0].strip() == "application/json" and isinstance(v, dict):
            return v
    for k, v in content.items():
        if "json" in str(k).lower() and isinstance(v, dict):
            return v
    only = next(iter(content.values()))
    return only if len(content) == 1 and isinstance(only, dict) else {}


def ok_response(spec: dict[str, Any], responses: Any) -> dict[str, Any]:
    """The success response: 200, else the first 2xx (201, 202, 2XX), else `default`.
    A response given as a $ref (#/components/responses/...) is followed."""
    if not isinstance(responses, dict):
        return {}
    by = {str(k).upper(): v for k, v in responses.items()}
    two = sorted(k for k in by if k.startswith("2") and k != "200")
    for k in ["200", *two, "DEFAULT"]:
        r = _safe(spec, by.get(k))
        if isinstance(r, dict):
            return r
    return {}


def deep_props(spec: dict[str, Any], node: Any, depth: int = 0) -> dict[str, Any]:
    """response_props, with every nested object's and array item's schema resolved the same
    way (a verify result nested under data.result, a $ref'd product item)."""
    out = {}
    for k, v in response_props(spec, node, depth).items():
        out[k] = _deep(spec, v, depth + 1)
    return out


def _deep(spec: dict[str, Any], node: Any, depth: int) -> Any:
    if depth > 8:
        return node
    flat = _flat(spec, node)
    if not flat:
        return node
    sub = response_props(spec, node, depth)
    if sub:
        flat["properties"] = {k: _deep(spec, v, depth + 1) for k, v in sub.items()}
    for key in ("oneOf", "anyOf", "allOf"):
        flat.pop(key, None)
    if isinstance(flat.get("items"), dict):
        flat["items"] = _deep(spec, flat["items"], depth + 1)
    return flat


def path_schema(props: dict[str, Any], path: str) -> Any:
    """The schema at a dotted path of a deep_props tree (`result.verified`), None if absent."""
    cur: Any = {"properties": props}
    for part in str(path).split("."):
        nxt = cur.get("properties") if isinstance(cur, dict) else None
        if not isinstance(nxt, dict) or part not in nxt:
            return None
        cur = nxt[part]
    return cur


def leaf_paths(props: dict[str, Any], prefix: str = "", depth: int = 0) -> dict[str, Any]:
    """Every dotted path of a deep_props tree (objects only, not into arrays) -> its schema."""
    out: dict[str, Any] = {}
    for k, v in (props or {}).items():
        p = prefix + str(k)
        out[p] = v
        sub = v.get("properties") if isinstance(v, dict) else None
        if isinstance(sub, dict) and depth < 6:
            out.update(leaf_paths(sub, p + ".", depth + 1))
    return out


def _field(spec: dict[str, Any], fname: str, fs: Any, required: bool, where: str) -> ToolField:
    fs = _flat(spec, fs)
    enum = fs.get("enum")
    if not enum and "const" in fs and fs["const"] is not None:  # OpenAPI 3.1 const
        enum = [fs["const"]]
    return ToolField(
        name=str(fname),
        type=str(fs.get("type", "string")),
        description=str(fs.get("description", "")),
        enum=[str(e) for e in enum or [] if e is not None],
        required=required,
        format=str(fs.get("format", "")),
        default=fs.get("default"),
        pattern=str(fs.get("pattern", "")),
        location=where,
    )


# ---------------------------------------------------------------- servers
def _absolute(url: str) -> bool:
    u = urllib.parse.urlparse(url)
    return u.scheme in ("http", "https") and bool(u.netloc)


def server_url(servers: Any) -> str:
    """servers[0].url with its variables set to their defaults ('' when there is none)."""
    if not isinstance(servers, list) or not servers or not isinstance(servers[0], dict):
        return ""
    s = servers[0]
    url = str(s.get("url") or "")
    for k, v in (s.get("variables") or {}).items():
        if isinstance(v, dict) and v.get("default") is not None:
            url = url.replace("{" + str(k) + "}", str(v["default"]))
    return url.rstrip("/")


def tool_base(server: str, base_url: str | None) -> str:
    """The base of the tool URLs. base_url (the mock) replaces scheme and host of the
    contract's server; the server's path prefix (/v1) is kept unless base_url has a path of
    its own. Raises ToolApiError when the result is not an absolute http(s) URL."""
    if base_url:
        b = urllib.parse.urlparse(str(base_url).strip())
        if b.scheme not in ("http", "https") or not b.netloc:
            raise ToolApiError(f"mock_url {base_url!r} is not an http(s) URL")
        own = b.path.rstrip("/")
        prefix = own if own else urllib.parse.urlparse(server).path.rstrip("/")
        return f"{b.scheme}://{b.netloc}{prefix}"
    if not _absolute(server):
        raise ToolApiError(
            f"the tool API gives no absolute server URL (servers[0].url={server!r}) and no "
            "mock_url was given: the tools would call relative URLs. Run the build with the "
            "mock's URL as the second argument."
        )
    return server


# ---------------------------------------------------------------- Swagger 2.0
def swagger2_to_openapi3(doc: dict[str, Any]) -> dict[str, Any]:
    """A Swagger 2.0 document read as OpenAPI 3: host/basePath/schemes -> servers, the body
    parameter (or formData parameters) -> requestBody, response schemas -> content. Local
    $refs to #/definitions keep resolving (definitions stay where they are)."""
    d = copy.deepcopy(doc)
    host, base = d.get("host"), str(d.get("basePath") or "")
    scheme = (d.get("schemes") or ["https"])[0]
    if host:
        d["servers"] = [{"url": f"{scheme}://{host}{base}"}]
    elif base:
        d["servers"] = [{"url": base}]

    def move(params: list[Any], op: dict[str, Any]) -> list[Any]:
        keep, form = [], {}
        for p in params:
            q = _safe(d, p)
            if not isinstance(q, dict):
                keep.append(p)
                continue
            if q.get("in") == "body":
                op["requestBody"] = {
                    "required": bool(q.get("required")),
                    "content": {"application/json": {"schema": q.get("schema") or {}}},
                }
            elif q.get("in") == "formData":
                form[str(q.get("name"))] = q
            else:
                if "schema" not in q:
                    q = dict(q)
                    q["schema"] = {
                        k: q[k] for k in ("type", "enum", "format", "default", "pattern") if k in q
                    }
                keep.append(q)
        if form and "requestBody" not in op:
            schema = {
                "type": "object",
                "properties": {
                    n: {k: v for k, v in q.items() if k not in ("in", "name", "required")}
                    for n, q in form.items()
                },
                "required": [n for n, q in form.items() if q.get("required")],
            }
            op["requestBody"] = {
                "content": {"application/x-www-form-urlencoded": {"schema": schema}}
            }
        return keep

    for _, ops in (d.get("paths") or {}).items():
        if not isinstance(ops, dict):
            continue
        if isinstance(ops.get("parameters"), list):
            ops["parameters"] = move(ops["parameters"], {})
        for m, op in ops.items():
            if not isinstance(op, dict) or m == "parameters":
                continue
            op["parameters"] = move(list(op.get("parameters") or []), op)
            for _, r in (op.get("responses") or {}).items():
                if isinstance(r, dict) and "schema" in r and "content" not in r:
                    r["content"] = {"application/json": {"schema": r.pop("schema")}}
    return d


# ---------------------------------------------------------------- kit role of an operation
_FOLD = str.maketrans("ıİşŞğĞüÜöÖçÇ", "iissgguuoocc")


def _fold(text: Any) -> str:
    s = str(text or "").translate(_FOLD).lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _tokens(text: Any) -> list[str]:
    """'bankVerifyIdentity' / 'bank-verify_identity' -> ['bank', 'verify', 'identity']."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(text or ""))
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return [w for w in re.split(r"[^a-z0-9]+", _fold(s)) if w]


# Words around an operation's own name that say nothing about what it does
_NOISE = {"api", "bank", "v1", "v2", "v3", "tool", "tools", "op", "svc", "service", "do",
          "action", "endpoint", "freya", "mock", "the", "a", "an"}  # fmt: skip
# Words (folded, English and Turkish) that point to a kit role, in the operation's name or
# path (strong) or its summary/description (weak). Language words, not a bank's names.
_ROLE_WORDS: dict[str, tuple[str, ...]] = {
    "verify_identity": ("verify", "verification", "identity", "identify", "authenticate",
                        "authentication", "auth", "kyc", "dogrula", "dogrulama", "kimlik",
                        "security question", "guvenlik sorusu"),
    "get_account": ("account", "accounts", "debt", "debts", "balance", "statement", "products",
                    "lookup", "hesap", "borc", "borclar", "ekstre", "bakiye", "sorgula", "sorgu",
                    "musteri bilgi", "customer details", "account details"),
    "create_promise": ("promise", "promises", "ptp", "commitment", "pledge", "arrangement",
                       "soz", "sozu", "odeme sozu", "taahhut"),
    "add_note": ("note", "notes", "comment", "remark", "annotate", "annotation", "not ekle",
                 "notu", "aciklama ekle"),
    "schedule_callback": ("callback", "call back", "callbacks", "recall", "appointment",
                          "geri arama", "geri ara", "randevu"),
    "transfer_to_agent": ("transfer", "handoff", "hand off", "escalate", "escalation", "agent",
                          "human", "operator", "representative", "aktar", "aktarim",
                          "temsilci", "yonlendir"),
    "close_call": ("close", "end call", "finish", "hangup", "hang up", "wrap up", "wrapup",
                   "disposition", "kapat", "kapanis", "sonlandir", "bitir", "end"),
}  # fmt: skip


def _has(words: tuple[str, ...], text: str) -> int:
    """How many of `words` the text has. A word of four letters or more may carry a suffix
    (Turkish: randevu -> randevusu, temsilci -> temsilcisine; English plurals); a shorter one
    must stand alone ('not' the Turkish note, never 'nothing')."""
    n = 0
    for w in words:
        tail = "" if len(w) >= 4 else r"(?![a-z0-9])"
        if re.search(r"(?<![a-z0-9])" + re.escape(w) + tail, text):
            n += 1
    return n


def _shape_score(role: str, fields: list[ToolField], resp: dict[str, Any], method: str) -> int:
    """Field shapes that fit a role (an amount and a date for a promise, a queue for a
    transfer): only added to a score some word already gave."""
    names = " ".join(" ".join(_tokens(f.name)) for f in fields)

    def has(*ws: str) -> bool:
        return any(re.search(r"(?<![a-z0-9])" + w, names) for w in ws)

    amount = has("amount", "tutar", "miktar", "sum")
    when = has("date", "day", "tarih", "gun") or any(f.format == "date-time" for f in fields)
    if role == "verify_identity":
        both = has("answer", "cevap", "yanit", "value") and has("field", "question", "soru", "type")
        return 4 if both else 0
    if role == "get_account":
        arrays = any(isinstance(v, dict) and v.get("type") == "array" for v in resp.values())
        return (3 if arrays else 0) + (1 if len(fields) <= 2 else 0) + (method == "GET")
    if role == "create_promise":
        return 5 if amount and when else 0
    if role == "add_note":
        note = has("note", "reason", "text", "comment", "aciklama", "neden", "sebep")
        return 2 if note and not amount else 0
    if role == "schedule_callback":
        return 4 if when and has("time", "hour", "saat", "at") and not amount else 0
    if role == "transfer_to_agent":
        return 4 if has("queue", "team", "department", "desk", "kuyruk", "ekip", "birim") else 0
    if role == "close_call":
        return 4 if has("outcome", "result", "disposition", "sonuc", "status") else 0
    return 0


def kit_roles(ops: list[dict[str, Any]]) -> tuple[dict[int, str], list[str]]:
    """Operation index -> kit role, for the operations recognised as one; and notes on roles
    two operations fit equally (left to neither). `ops` items: name, path, text (summary and
    description), fields, resp (response properties), method, x_role (an explicit
    x-kit-role, which contract.py writes)."""
    scores: list[tuple[int, str, int]] = []
    for i, op in enumerate(ops):
        toks = [t for t in _tokens(op["name"]) if t not in _NOISE]
        path_toks = [
            t
            for seg in op["path"].split("/")
            if seg and not seg.startswith("{")
            for t in _tokens(seg)
            if t not in _NOISE
        ]
        strong = " ".join(toks + path_toks)
        weak = _fold(op["text"])
        for role in KIT_TOOLS:
            if op.get("x_role") == role:
                sc = 2000
            elif op["name"] == role:
                sc = 1000
            elif "_".join(toks) == role or "_".join(path_toks[-3:]) == role:
                sc = 500
            else:
                # the name and path count most; the summary/description is enough with a
                # field shape that fits ("Not ekle" + a reason field)
                sc = 10 * _has(_ROLE_WORDS[role], strong)
                sc += 5 * min(_has(_ROLE_WORDS[role], weak), 2)
                if sc:
                    sc += _shape_score(role, op["fields"], op["resp"], op["method"])
            if sc >= 7:
                scores.append((sc, role, i))
    scores.sort(key=lambda x: (-x[0], KIT_TOOLS.index(x[1]), x[2]))
    out: dict[int, str] = {}
    taken: set[str] = set()
    notes: list[str] = []
    for sc, role, i in scores:
        if role in taken or i in out:
            continue
        rivals = [j for s2, r2, j in scores if r2 == role and s2 == sc and j != i and j not in out]
        if rivals and sc < 500:
            names = [ops[j]["name"] for j in [i, *rivals]]
            notes.append(f"{role}: operations {names} fit it equally; none is used as {role}")
            taken.add(role)
            continue
        out[i] = role
        taken.add(role)
    return out, notes


# ---------------------------------------------------------------- money in minor units
_MINOR = re.compile(
    r"\b(cents?|pence|penny|pennies|minor (currency )?units?|smallest (currency )?unit|"
    r"hundredths)\b",
    re.I,
)
_MINOR_TR = re.compile(r"\bkuru[sş]\w*\s+(cinsinden|olarak|biriminde)|\bin kuru[sş]\b", re.I)
_MINOR_NAMES = {"cents", "cent", "pence", "penny", "minor", "kurus", "kurush"}


def says_minor_units(name: str, type_: str, description: str, pattern: str = "") -> bool:
    """A money field given in hundredths of the currency (cents, pence, kuruş, minor units):
    said by its name (amount_cents, bakiye_kurus), by its description ("Integer cents",
    "kuruş cinsinden"), by kuruş on an integer field, or by a worked example in the
    description ("42050 means 420.50"). "TL, kuruş after the dot" on a number field is not."""
    text = f"{pattern} {description}"
    if _MINOR.search(text) or _MINOR_TR.search(text):
        return True
    if set(_tokens(name)) & _MINOR_NAMES:
        return True
    if type_ == "integer" and re.search(r"\bkuru[sş]", text, re.I):
        return True
    for m in re.finditer(r"\b(\d{3,})\b\D{1,24}?\b(\d+)[.,](\d{2})\b", description):
        if int(m.group(1)) == int(m.group(2)) * 100 + int(m.group(3)):
            return True
    return False


def response_scale(props: dict[str, Any], prefix: str = "", depth: int = 0) -> dict[str, float]:
    """Response leaf path -> 0.01 for a number given in minor units (see says_minor_units);
    nested objects by dotted path, array items under the array's name."""
    out: dict[str, float] = {}
    if depth > 8 or not isinstance(props, dict):
        return out
    for k, v in props.items():
        if not isinstance(v, dict):
            continue
        p = prefix + str(k)
        t = str(v.get("type") or "")
        if t in ("integer", "number", "") and not v.get("properties") and not v.get("items"):
            desc, pat = str(v.get("description") or ""), str(v.get("pattern") or "")
            if says_minor_units(str(k), t, desc, pat):
                out[p] = 0.01
        if isinstance(v.get("properties"), dict):
            out.update(response_scale(v["properties"], p + ".", depth + 1))
        items = v.get("items")
        if isinstance(items, dict) and isinstance(items.get("properties"), dict):
            out.update(response_scale(items["properties"], p + ".", depth + 1))
    return out


# ---------------------------------------------------------------- one date-time field
def _is_datetime(f: ToolField) -> bool:
    if f.format == "date-time":
        return True
    if re.search(r"datetime|timestamp", f.name, re.I):
        return True
    text = f"{f.pattern} {f.description}"
    return bool(re.search(r"YYYY-MM-DD[T ]HH|date[- ]and[- ]time|ISO.?8601 date.?time", text, re.I))


def split_datetime(fields: list[ToolField]) -> list[ToolField]:
    """A call that takes its day and time as one date-time field (callback_at, scheduled_at)
    and no other date or time field: `date` and `time` parts are added for the workflow's
    roles to bind, the field itself is no longer required (request_hook sends it, joined)."""
    dt = [f for f in fields if f.location == "body" and _is_datetime(f)]
    if len(dt) != 1:
        return fields
    f = dt[0]
    low = f.name.lower()
    has_d, has_t = bool(re.search(r"date|day", low)), bool(re.search(r"time|hour", low))
    if has_d != has_t:  # 'date' (date-time) or 'start_time': one role binds it as it is
        return fields
    if any(g is not f and re.search(r"date|day|time|hour", g.name, re.I) for g in fields):
        return fields
    parts = [
        ToolField(
            "date",
            "string",
            f"The day of {f.name}, YYYY-MM-DD (sent inside {f.name}).",
            required=f.required,
            format="date",
            part_of=f.name,
        ),
        ToolField(
            "time",
            "string",
            f"The time of {f.name}, HH:MM (sent inside {f.name}).",
            part_of=f.name,
        ),
    ]
    f.required = False
    return fields + parts


# ---------------------------------------------------------------- parse
def _operation(spec: dict[str, Any], path: str, path_item: dict, method: str, op: dict) -> dict:
    segs = [s for s in path.strip("/").split("/") if s]
    plain = [s for s in segs if not s.startswith("{")]
    name = str(op.get("operationId") or (plain or segs or [method.lower()])[-1])
    warns: list[str] = []
    body = _safe(spec, op.get("requestBody") or {}) or {}
    content = body.get("content") if isinstance(body, dict) else None
    if isinstance(content, dict) and content and not any("json" in str(k).lower() for k in content):
        warns.append(f"the request body is {sorted(content)}; the tool sends JSON")
    schema = json_content(content).get("schema")
    props, required = body_schema(spec, schema)
    fields = [_field(spec, n, fs, n in required, "body") for n, fs in props.items()]
    # query and path parameters (path level, then operation level overriding them); header
    # parameters only when the contract fixes their value
    params: dict[tuple[str, str], Any] = {}
    headers: dict[str, str] = {}
    for p in list(path_item.get("parameters") or []) + list(op.get("parameters") or []):
        p = _safe(spec, p)
        if not isinstance(p, dict) or not p.get("name"):
            continue
        if p.get("in") in ("query", "path"):
            params[(str(p["in"]), str(p["name"]))] = p
        elif p.get("in") == "header":
            hf = _field(
                spec, str(p["name"]), p.get("schema") or {}, bool(p.get("required")), "header"
            )
            val = fixed_value(hf) if fixed_value(hf) is not None else p.get("example")
            if val is not None:
                headers[hf.name] = str(val)
            elif hf.required:
                warns.append(f"header {hf.name!r} is required and the contract gives no value")
    for (where, pname), p in params.items():
        if any(f.name == pname for f in fields):
            continue
        f = _field(spec, pname, p.get("schema") or {}, bool(p.get("required")), where)
        if not f.description:
            f.description = str(p.get("description") or "")
        fields.append(f)
    ok = ok_response(spec, op.get("responses"))
    resp = _safe(spec, json_content(ok.get("content")).get("schema")) or {}
    return {
        "name": name,
        "path": path,
        "method": method.upper(),
        "server": server_url(op.get("servers")) or server_url(path_item.get("servers")),
        "summary": str(op.get("summary") or op.get("description") or ""),
        "text": f"{op.get('summary') or ''} {op.get('description') or ''}",
        "fields": fields,
        "resp": resp if isinstance(resp, dict) else {},
        "x_role": op.get("x-kit-role"),
        "headers": headers,
        "warnings": warns,
    }


def parse_openapi(spec: dict[str, Any], base_url: str | None = None) -> dict[str, ToolDef]:
    """Every operation as a ToolDef, keyed by its kit role's name when it is one (see the
    module docstring). Raises ToolApiError when a tool URL would not be absolute."""
    if str(spec.get("swagger") or "").startswith("2"):
        spec = swagger2_to_openapi3(spec)
    root_server = server_url(spec.get("servers"))
    ops: list[dict[str, Any]] = []
    for path, path_item in (spec.get("paths") or {}).items():
        path_item = _safe(spec, path_item)
        if not isinstance(path_item, dict):
            continue
        for method, op in path_item.items():
            if method.lower() not in ("post", "put", "get", "patch", "delete"):
                continue
            if isinstance(op, dict):
                ops.append(_operation(spec, str(path), path_item, method, op))
    roles, notes = kit_roles([{**o, "resp": response_props(spec, o["resp"])} for o in ops])
    tools: dict[str, ToolDef] = {}
    for i, o in enumerate(ops):
        name = roles.get(i) or o["name"]
        if name in tools or (i not in roles and name in roles.values()):
            name = f"{o['name']}_{o['method'].lower()}"  # two operations, one name
        fields = o["fields"]
        if roles.get(i) in ("create_promise", "schedule_callback"):
            fields = split_datetime(fields)
        rprops = deep_props(spec, o["resp"])
        t = ToolDef(
            name=name,
            url=tool_base(o["server"] or root_server, base_url) + o["path"],
            method=o["method"],
            summary=o["summary"],
            fields=fields,
            response=o["resp"],
            response_props=rprops,
            api_name=o["name"],
            response_scale=response_scale(rprops),
            headers=o["headers"],
            warnings=list(o["warnings"]),
        )
        if name in KIT_TOOLS and not [f for f in fields if f.location in ("body", "query", "path")]:
            t.warnings.append("no request field could be read (check its schema and $refs)")
        tools[name] = t
    first = next(iter(tools.values()), None)
    bad = unresolved_refs(spec)
    if first is not None:
        if bad:
            first.warnings.append(f"$refs that do not resolve (read as no fields): {bad[:8]}")
        first.warnings.extend(notes)
    return tools


# ---------------------------------------------------------------- value formats
_DATE_TPL = re.compile(r"\b(DD|MM|YYYY)([/.\-])(DD|MM|YYYY)\2(DD|MM|YYYY)\b", re.I)
_DATE_PAT = re.compile(r"\\d\{(2|4)\}([/.\-]|\\[/.\-])\\d\{2\}\2\\d\{(2|4)\}")


def wire_format(f: ToolField | None, role: str, date_order: str = "DMY") -> str:
    """How the tool API wants a value the workflow holds in its own format (dates ISO
    YYYY-MM-DD, amounts in decimal units, times HH:MM): '' when that format fits, else a
    conversion for fmt_value: a date template ('DD/MM/YYYY'), 'datetime', 'cents', 'dec2'
    (a decimal string, "420.50"), 'hms'. Read from the field's name, format, pattern and
    description (minor units: says_minor_units, which kuruş is part of)."""
    if f is None:
        return ""
    text = " ".join((f.pattern, f.description))
    if role == "date":
        if f.format == "date-time":
            return "datetime"
        m = _DATE_TPL.search(text)
        if m:
            tpl = m.group(0).upper()
            parts = {m.group(1).upper(), m.group(3).upper(), m.group(4).upper()}
            if parts == {"DD", "MM", "YYYY"} and tpl != "YYYY-MM-DD":
                return tpl
            return ""
        m = _DATE_PAT.search(f.pattern)
        if m:
            sep = m.group(2)[-1]
            if m.group(1) == "4":
                return "" if sep == "-" else f"YYYY{sep}MM{sep}DD"
            first = "MM" if str(date_order).upper() == "MDY" else "DD"
            second = "DD" if first == "MM" else "MM"
            return f"{first}{sep}{second}{sep}YYYY"
        return ""
    if role == "amount":
        if says_minor_units(f.name, f.type, f.description, f.pattern):
            return "cents"
        # an amount the API takes as a string ("420.50"): sent with two decimals, not 420.5
        return "dec2" if f.type == "string" else ""
    if role == "time":
        if f.format == "time" or re.search(r"HH:MM:SS", text, re.I):
            return "hms"
        if re.search(r"\\d\{2\}:\\d\{2\}:\\d\{2\}", f.pattern):
            return "hms"
    return ""


# Embedded in a pre hook when a field needs a conversion (wire_format): the workflow's value
# in, the tool API's format out; anything it cannot read goes out unchanged.
FMT_VALUE = (
    "def fmt_value(v, how):\n"
    "    s = str(v if v is not None else '').strip()\n"
    "    try:\n"
    "        if how == 'cents':\n"
    "            return int(round(float(s) * 100)) if s else v\n"
    "        if how == 'dec2':\n"
    "            return '%.2f' % float(s) if s else v\n"
    "        if how == 'hms':\n"
    "            return s + ':00' if re.fullmatch(r'\\d{1,2}:\\d{2}', s) else v\n"
    "        m = re.fullmatch(r'(\\d{4})-(\\d{2})-(\\d{2})', s)\n"
    "        if not m:\n"
    "            return v\n"
    "        if how == 'datetime':\n"
    "            return s + 'T00:00:00'\n"
    "        return how.replace('YYYY', m.group(1)).replace('MM', m.group(2)).replace(\n"
    "            'DD', m.group(3))\n"
    "    except Exception:\n"
    "        return v\n"
)


def request_hook(t: ToolDef, conv: dict[str, str]) -> str:
    """Pre hook for a call whose body the workflow fills: values converted to the formats the
    tool API names (conv: field -> wire_format), the `date`/`time` parts of a single date-time
    field joined into it (split_datetime), and query/path parameters moved from the body into
    the URL. '' when none is needed (the kit's tool API)."""
    query = [f.name for f in t.fields if f.location == "query"]
    path = [f.name for f in t.fields if f.location == "path"]
    compose: dict[str, list[str]] = {}
    for f in t.fields:
        if f.part_of:
            compose.setdefault(f.part_of, ["", ""])[0 if f.format == "date" else 1] = f.name
    if not conv and not query and not path and not compose:
        return ""
    src = "import re\n\n" + FMT_VALUE + "\n\n"
    src += f"CONV = {conv!r}\nQUERY = {query!r}\nPATH = {path!r}\n"
    if compose:
        src += f"COMPOSE = {compose!r}\n"
    src += (
        "\n\ndef _q(x):\n"
        "    return ''.join(c if c.isalnum() or c in '-_.~' else '%%%02X' % ord(c)\n"
        "                   for c in str(x if x is not None else ''))\n"
        "\n\ntry:\n"
        "    b = config.get('body')\n"
        "    if isinstance(b, dict):\n"
        "        for k, how in CONV.items():\n"
        "            if k in b:\n"
        "                b[k] = fmt_value(b.get(k), how)\n"
    )
    if compose:
        # the day (a part, or the field itself when a role bound it) and the time, as one
        # ISO date-time; a day the hook cannot read leaves the body as it is
        src += (
            "        for k, (dp, tp) in COMPOSE.items():\n"
            "            d = b.pop(dp, None) if dp else None\n"
            "            tm = str(b.pop(tp, None) or '').strip() if tp else ''\n"
            "            d = str(d if d not in (None, '') else b.get(k) or '').strip()[:10]\n"
            "            if re.fullmatch(r'\\d{4}-\\d{2}-\\d{2}', d):\n"
            "                if re.fullmatch(r'\\d{1,2}:\\d{2}', tm):\n"
            "                    tm = tm.zfill(5) + ':00'\n"
            "                elif not re.fullmatch(r'\\d{2}:\\d{2}:\\d{2}', tm):\n"
            "                    tm = '00:00:00'\n"
            "                b[k] = d + 'T' + tm\n"
        )
    src += (
        "        u = str(config.get('url') or '')\n"
        "        for k in PATH:\n"
        "            u = u.replace('{' + k + '}', _q(b.pop(k, '')))\n"
        "        qs = '&'.join(k + '=' + _q(b.pop(k)) for k in QUERY if k in b)\n"
        "        if qs:\n"
        "            u = u + ('&' if '?' in u else '?') + qs\n"
        "        if PATH or QUERY:\n"
        "            config['url'] = u\n"
        "except Exception:\n"
        "    pass\n"
    )
    return src


def freya_tool(
    t: ToolDef,
    description: str,
    *,
    bake: dict[str, str] | None = None,
    model_names: dict[str, str] | None = None,
    field_notes: dict[str, str] | None = None,
    timeout_ms: int = 12000,
    max_retries: int = 0,
    pre_code: str = "",
    post_code: str = "",
    response_mapping: list[dict[str, str]] | None = None,
    extra_config: dict[str, Any] | None = None,
    model_filled: bool = True,
    consts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One `api_call` function. `bake` maps a field to a call variable that is always set
    (the workflow supplies it; the model never sees it). `consts` maps a field to a constant
    (the spec's value for a required field no role fills). Other fields are model-filled with
    `default: "{{<model_name>}}"` (docs: a default naming an unset variable is model-filled).
    `model_filled=False` (a call the engine runs: no model fills it): an optional field no
    role binds is left out, so a variable of the same name never fills it."""
    bake = bake or {}
    consts = consts or {}
    model_names = model_names or {}
    field_notes = field_notes or {}
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in t.fields:
        leaf: dict[str, Any] = {
            "type": f.type if f.type in ("string", "number", "integer", "boolean") else "string"
        }
        if not model_filled and f.name not in bake and f.name not in consts and not f.required:
            continue
        if f.name in bake:
            leaf["default"] = "{{" + bake[f.name] + "}}"
        elif f.name in consts:
            leaf["default"] = consts[f.name]
        elif f.required and fixed_value(f) is not None:
            # a required field no role fills (currency: GBP): the API's own value. An
            # api_request node has no model that could fill a "{{name}}" default.
            leaf["default"] = fixed_value(f)
        else:
            leaf["default"] = "{{" + model_names.get(f.name, f.name) + "}}"
            desc = " ".join(x for x in (f.description, field_notes.get(f.name, "")) if x)
            if f.enum:
                desc = (desc + " " if desc else "") + "One of: " + ", ".join(f.enum) + "."
                leaf["enum"] = list(f.enum)
            if desc:
                leaf["description"] = desc
            if f.required:
                required.append(f.name)
        props[f.name] = leaf
    out: dict[str, Any] = {
        "type": "function",
        "config": {"timeoutMs": timeout_ms, "maxRetries": max_retries, **(extra_config or {})},
        "function": {
            "name": t.name,
            "handler": "__function__:api_call",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "default": t.url},
                    "method": {"type": "string", "default": t.method},
                    "headers": {
                        "type": "object",
                        "properties": {
                            "Content-Type": {"type": "string", "default": "application/json"},
                            **{
                                k: {"type": "string", "default": v}
                                for k, v in t.headers.items()
                                if k.lower() != "content-type"
                            },
                        },
                    },
                    "body": {"type": "object", "properties": props, "required": required},
                },
            },
        },
    }
    if pre_code:
        out["pre_process_code"] = pre_code
    if post_code:
        out["post_process_code"] = post_code
    if response_mapping:
        out["response_mapping"] = copy.deepcopy(response_mapping)
    return out
