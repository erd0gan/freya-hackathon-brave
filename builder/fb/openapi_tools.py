"""OpenAPI file -> Freya `api_call` tool functions. Pure code: every field, required
flag and enum comes from the file (hidden briefs change fields and allowed values)."""

from __future__ import annotations

import copy
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


def parse_openapi(spec: dict[str, Any]) -> dict[str, ToolDef]:
    servers = spec.get("servers") or [{"url": ""}]
    base = str(servers[0].get("url", "")).rstrip("/")
    tools: dict[str, ToolDef] = {}
    for path, ops in (spec.get("paths") or {}).items():
        for method, op in ops.items():
            if method.lower() not in ("post", "put", "get", "patch"):
                continue
            name = op.get("operationId") or path.strip("/").split("/")[-1]
            body = _resolve(spec, op.get("requestBody") or {})
            schema = (
                _resolve(
                    spec, ((body.get("content") or {}).get("application/json") or {}).get("schema")
                )
                or {}
            )
            required = set(schema.get("required") or [])
            fields = []
            for fname, fs in (schema.get("properties") or {}).items():
                fs = _resolve(spec, fs) or {}
                fields.append(
                    ToolField(
                        name=fname,
                        type=str(fs.get("type", "string")),
                        description=str(fs.get("description", "")),
                        enum=[str(e) for e in fs.get("enum") or []],
                        required=fname in required,
                        format=str(fs.get("format", "")),
                        default=fs.get("default"),
                    )
                )
            ok = ((op.get("responses") or {}).get("200") or {}).get("content") or {}
            resp = _resolve(spec, (ok.get("application/json") or {}).get("schema")) or {}
            tools[name] = ToolDef(
                name=name,
                url=base + path,
                method=method.upper(),
                summary=str(op.get("summary") or op.get("description") or ""),
                fields=fields,
                response=resp,
                response_props=response_props(spec, resp),
            )
    return tools


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
) -> dict[str, Any]:
    """One `api_call` function. `bake` maps a field to a call variable that is always set
    (the workflow supplies it; the model never sees it). Other fields are model-filled with
    `default: "{{<model_name>}}"` (docs: a default naming an unset variable is model-filled).
    `model_filled=False` (a call the engine runs: no model fills it): an optional field no
    role binds is left out, so a variable of the same name never fills it."""
    bake = bake or {}
    model_names = model_names or {}
    field_notes = field_notes or {}
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in t.fields:
        leaf: dict[str, Any] = {
            "type": f.type if f.type in ("string", "number", "integer", "boolean") else "string"
        }
        if not model_filled and f.name not in bake and not f.required:
            continue
        if f.name in bake:
            leaf["default"] = "{{" + bake[f.name] + "}}"
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
