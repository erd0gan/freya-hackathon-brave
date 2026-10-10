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


@dataclass
class ToolDef:
    name: str
    url: str
    method: str
    summary: str
    fields: list[ToolField]
    response: dict[str, Any] = field(default_factory=dict)

    def field(self, name: str) -> ToolField | None:
        return next((f for f in self.fields if f.name == name), None)


def _resolve(spec: dict[str, Any], node: Any) -> Any:
    if isinstance(node, dict) and "$ref" in node:
        cur: Any = spec
        for part in node["$ref"].lstrip("#/").split("/"):
            cur = cur[part]
        return _resolve(spec, cur)
    return node


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
) -> dict[str, Any]:
    """One `api_call` function. `bake` maps a field to a call variable that is always set
    (the workflow supplies it; the model never sees it). Other fields are model-filled with
    `default: "{{<model_name>}}"` (docs: a default naming an unset variable is model-filled)."""
    bake = bake or {}
    model_names = model_names or {}
    field_notes = field_notes or {}
    props: dict[str, Any] = {}
    required: list[str] = []
    for f in t.fields:
        leaf: dict[str, Any] = {
            "type": f.type if f.type in ("string", "number", "integer", "boolean") else "string"
        }
        if f.name in bake:
            leaf["default"] = "{{" + bake[f.name] + "}}"
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
