"""OpenAPI file -> Freya `api_call` tool functions. Pure code: every field, required
flag and enum comes from the file (hidden briefs change fields and allowed values)."""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any


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

    def field(self, name: str) -> ToolField | None:
        return next((f for f in self.fields if f.name == name), None)


def _resolve(spec: dict[str, Any], node: Any) -> Any:
    if isinstance(node, dict) and "$ref" in node:
        cur: Any = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            cur = cur[part]
        return _resolve(spec, cur)
    return node


def response_props(spec: dict[str, Any], node: Any, depth: int = 0) -> dict[str, Any]:
    """The properties a response schema lists: $refs followed, allOf/oneOf/anyOf parts merged
    (create_promise 200 = allOf[{$ref Error}, {properties: {promise_id}}]). A $ref that does
    not resolve adds nothing."""
    if depth > 20:
        return {}
    try:
        node = _resolve(spec, node)
    except (KeyError, TypeError, IndexError, RecursionError):
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
    except (KeyError, TypeError, IndexError, RecursionError):
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
    return ToolField(
        name=str(fname),
        type=str(fs.get("type", "string")),
        description=str(fs.get("description", "")),
        enum=[str(e) for e in fs.get("enum") or [] if e is not None],
        required=required,
        format=str(fs.get("format", "")),
        default=fs.get("default"),
        pattern=str(fs.get("pattern", "")),
        location=where,
    )


def parse_openapi(spec: dict[str, Any]) -> dict[str, ToolDef]:
    servers = spec.get("servers") or [{"url": ""}]
    base = str(servers[0].get("url", "")).rstrip("/")
    tools: dict[str, ToolDef] = {}
    for path, ops in (spec.get("paths") or {}).items():
        ops = _safe(spec, ops)
        if not isinstance(ops, dict):
            continue
        for method, op in ops.items():
            if method.lower() not in ("post", "put", "get", "patch", "delete"):
                continue
            if not isinstance(op, dict):
                continue
            name = op.get("operationId") or path.strip("/").split("/")[-1]
            body = _safe(spec, op.get("requestBody") or {}) or {}
            schema = json_content(body.get("content")).get("schema")
            props, required = body_schema(spec, schema)
            fields = [_field(spec, n, fs, n in required, "body") for n, fs in props.items()]
            # query and path parameters (path level, then operation level overriding them)
            params: dict[tuple[str, str], Any] = {}
            for p in list(ops.get("parameters") or []) + list(op.get("parameters") or []):
                p = _safe(spec, p)
                if isinstance(p, dict) and p.get("in") in ("query", "path") and p.get("name"):
                    params[(str(p["in"]), str(p["name"]))] = p
            for (where, pname), p in params.items():
                if any(f.name == pname for f in fields):
                    continue
                f = _field(spec, pname, p.get("schema") or {}, bool(p.get("required")), where)
                if not f.description:
                    f.description = str(p.get("description") or "")
                fields.append(f)
            ok = ok_response(spec, op.get("responses"))
            resp = _safe(spec, json_content(ok.get("content")).get("schema")) or {}
            tools[name] = ToolDef(
                name=name,
                url=base + path,
                method=method.upper(),
                summary=str(op.get("summary") or op.get("description") or ""),
                fields=fields,
                response=resp if isinstance(resp, dict) else {},
                response_props=deep_props(spec, resp),
            )
    return tools


# ---------------------------------------------------------------- value formats
_DATE_TPL = re.compile(r"\b(DD|MM|YYYY)([/.\-])(DD|MM|YYYY)\2(DD|MM|YYYY)\b", re.I)
_DATE_PAT = re.compile(r"\\d\{(2|4)\}([/.\-]|\\[/.\-])\\d\{2\}\2\\d\{(2|4)\}")
_MINOR = re.compile(r"\b(cents?|pence|penny|pennies|minor (currency )?units?)\b", re.I)


def wire_format(f: ToolField | None, role: str, date_order: str = "DMY") -> str:
    """How the tool API wants a value the workflow holds in its own format (dates ISO
    YYYY-MM-DD, amounts in decimal units, times HH:MM): '' when that format fits, else a
    conversion for fmt_value: a date template ('DD/MM/YYYY'), 'datetime', 'cents', 'hms'.
    Read from the field's format, pattern and description."""
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
        return "cents" if _MINOR.search(text) else ""
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
    tool API names (conv: field -> wire_format), and query/path parameters moved from the body
    into the URL. '' when neither is needed (the kit's tool API)."""
    query = [f.name for f in t.fields if f.location == "query"]
    path = [f.name for f in t.fields if f.location == "path"]
    if not conv and not query and not path:
        return ""
    src = "import re\n\n" + FMT_VALUE + "\n\n"
    src += f"CONV = {conv!r}\nQUERY = {query!r}\nPATH = {path!r}\n"
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
                            "Content-Type": {"type": "string", "default": "application/json"}
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
