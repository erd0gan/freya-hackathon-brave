"""Turn collections_lib + a call into the source of one code_exec snippet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LIB = (Path(__file__).parent / "collections_lib.py").read_text()


def config_literal(cfg: dict[str, Any]) -> str:
    # indent puts closing braces on separate lines; repr escapes the newlines, so the
    # snippet never holds two braces in a row (the platform's placeholder syntax).
    return repr(json.dumps(cfg, indent=1, ensure_ascii=False))


def snippet(body: str, cfg: dict[str, Any]) -> str:
    """`body` runs inside try/except with `CFG`, the lib and `vars` in scope."""
    inner = "\n".join("    " + line for line in body.strip().splitlines())
    src = (
        LIB
        + "\n\nCFG = json.loads("
        + config_literal(cfg)
        + ")\ntry:\n"
        + inner
        + "\nexcept Exception as _e:\n    vars['code_error'] = str(_e)[:200]\n"
    )
    if "{{" in src or "}}" in src:
        raise ValueError("code snippet contains a template brace pair")
    return src
