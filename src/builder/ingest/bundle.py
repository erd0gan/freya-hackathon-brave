"""Format-neutral bundle loading (PLAN Faz 3).

Supported now: a single JSON/YAML file shaped like `InputBundle`, or a directory
with `task.md`, optional `policy.md`, `tools.json|yaml` and `docs/`. When the
hackathon input format is published, add one parser here — nothing else changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from builder.schema.bundle import InputBundle, RawDocument

DOC_SUFFIXES = {".md", ".txt", ".markdown", ".rst"}


def _read_structured(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yaml", ".yml"}:
        return yaml.safe_load(text)
    return json.loads(text)


def _normalize_keys(data: dict[str, Any]) -> dict[str, Any]:
    """Map common alternative key names onto InputBundle fields."""
    aliases = {
        "task": "task_description",
        "description": "task_description",
        "instructions": "task_description",
        "requirements": "policy_text",
        "policy": "policy_text",
        "policies": "policy_text",
        "functions": "tools",
        "knowledge": "documents",
        "docs": "documents",
    }
    out: dict[str, Any] = {}
    for k, v in data.items():
        key = aliases.get(k, k)
        if key == "policy_text" and isinstance(v, list):
            v = "\n\n".join(str(x) for x in v)
        if key == "documents" and isinstance(v, list):
            v = [
                d if isinstance(d, dict) else {"doc_id": f"doc{i}", "text": str(d)}
                for i, d in enumerate(v)
            ]
        out[key] = v
    known = set(InputBundle.model_fields)
    extra = {k: v for k, v in out.items() if k not in known}
    out = {k: v for k, v in out.items() if k in known}
    if extra:
        out.setdefault("extra", {}).update(extra)
    return out


def load_bundle(path: Path) -> InputBundle:
    if path.is_dir():
        return _load_dir(path)
    return InputBundle.model_validate(_normalize_keys(_read_structured(path)))


def _load_dir(root: Path) -> InputBundle:
    task = next((root / n for n in ("task.md", "task.txt") if (root / n).exists()), None)
    if task is None:
        raise FileNotFoundError(f"{root}: needs task.md")
    data: dict[str, Any] = {"task_description": task.read_text(encoding="utf-8")}
    for n in ("policy.md", "policy.txt", "requirements.md"):
        if (root / n).exists():
            data["policy_text"] = (root / n).read_text(encoding="utf-8")
            break
    for n in ("tools.json", "tools.yaml", "tools.yml"):
        if (root / n).exists():
            tools = _read_structured(root / n)
            data["tools"] = tools.get("tools", tools) if isinstance(tools, dict) else tools
            break
    meta = root / "meta.json"
    if meta.exists():
        data.update(json.loads(meta.read_text()))
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        data["documents"] = [
            RawDocument(
                doc_id=p.relative_to(docs_dir).as_posix(),
                title=p.stem.replace("_", " "),
                path=str(p),
                text=p.read_text(encoding="utf-8", errors="replace"),
            ).model_dump()
            for p in sorted(docs_dir.rglob("*"))
            if p.is_file() and p.suffix.lower() in DOC_SUFFIXES
        ]
    return InputBundle.model_validate(data)
