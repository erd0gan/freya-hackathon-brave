"""Leaf-level state diffs used to derive and check state oracles (memo §3.2, §16)."""

from __future__ import annotations

from typing import Any

FREE_TEXT_KEYS = {
    "summary",
    "description",
    "notes",
    "note",
    "comment",
    "message",
    "details",
    "reason_text",
    "explanation",
}
FREE_TEXT_LEN = 60


def flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        if not obj and prefix:
            out[prefix] = {}
        for k, v in obj.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(obj, list):
        out[f"{prefix}.__len__" if prefix else "__len__"] = len(obj)
        for i, v in enumerate(obj):
            out.update(flatten(v, f"{prefix}.{i}" if prefix else str(i)))
    else:
        out[prefix] = obj
    return out


# Conversation/session bookkeeping is not business state (e.g. "verified caller").
IGNORED_ROOTS = ("session",)


def diff(
    before: dict[str, Any], after: dict[str, Any], ignore: tuple[str, ...] = IGNORED_ROOTS
) -> dict[str, tuple[Any, Any]]:
    a = {k: v for k, v in flatten(before).items() if k.split(".", 1)[0] not in ignore}
    b = {k: v for k, v in flatten(after).items() if k.split(".", 1)[0] not in ignore}
    keys = set(a) | set(b)
    missing = object()
    out: dict[str, tuple[Any, Any]] = {}
    for k in sorted(keys):
        va, vb = a.get(k, missing), b.get(k, missing)
        if va != vb:
            out[k] = (None if va is missing else va, None if vb is missing else vb)
    return out


def is_free_text(path: str, value: Any) -> bool:
    leaf = path.rsplit(".", 1)[-1]
    return leaf in FREE_TEXT_KEYS or (isinstance(value, str) and len(value) > FREE_TEXT_LEN)


def list_index(items: list[Any], part: str) -> int:
    """A list element by position ("0") or by an id field's value ("C003"): generated
    state patches address records by id (TR run, 9 Oct: `musteriler.C003.durum`)."""
    if part.lstrip("-").isdigit():
        return int(part)
    for i, item in enumerate(items):
        if isinstance(item, dict) and any(
            str(v) == part
            for k, v in item.items()
            if k == "id"
            or (k.endswith(("_id", "_number", "_no")) and not isinstance(v, dict | list))
        ):
            return i
    raise KeyError(part)


def set_path(obj: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    cur: Any = obj
    for p in parts[:-1]:
        cur = cur[list_index(cur, p)] if isinstance(cur, list) else cur.setdefault(p, {})
    last = parts[-1]
    if isinstance(cur, list):
        idx = list_index(cur, last) if not last.isdigit() else int(last)
        if idx == len(cur):
            cur.append(value)
        else:
            cur[idx] = value
    elif value is None and last in cur:
        del cur[last]
    else:
        cur[last] = value


def get_path(obj: Any, path: str) -> Any:
    """Resolve a dotted path; list elements may be addressed by index."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                raise KeyError(path)
            cur = cur[part]
        elif isinstance(cur, list):
            if part == "__len__":
                return len(cur)
            cur = cur[list_index(cur, part)]
        else:
            raise KeyError(path)
    return cur
