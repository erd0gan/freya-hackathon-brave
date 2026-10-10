"""Turn the node-code libraries + a body into the source of one code_exec snippet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

LIB_FILES = ("collections_lib.py", "speech_lib.py", "negotiation_lib.py")
_SRC = {f: (Path(__file__).parent / f).read_text(encoding="utf-8") for f in LIB_FILES}
LIB = "\n\n".join(_SRC[f] for f in LIB_FILES)
# Smaller embeds for code that needs no negotiation engine (dates, speech, callbacks).
LIB_CALENDAR = "\n\n".join(_SRC[f] for f in LIB_FILES[:2])


def namespace() -> dict[str, Any]:
    """The libraries executed the way a code node executes them, for local runs and tests."""
    ns: dict[str, Any] = {}
    exec(compile(LIB, "nodecode", "exec"), ns)
    return ns


def config_literal(cfg: dict[str, Any]) -> str:
    # indent puts closing braces on separate lines; repr escapes the newlines, so the
    # snippet never holds two braces in a row (the platform's placeholder syntax).
    return repr(json.dumps(cfg, indent=1, ensure_ascii=False))


def snippet(body: str, cfg: dict[str, Any], lib: str = LIB) -> str:
    """`body` runs inside try/except with `CFG`, the lib and `vars` in scope."""
    inner = "\n".join("    " + line for line in body.strip().splitlines())
    src = (
        lib
        + "\n\nCFG = json.loads("
        + config_literal(cfg)
        + ")\ntry:\n"
        + inner
        + "\nexcept Exception as _e:\n    vars['code_error'] = str(_e)[:200]\n"
    )
    if "{{" in src or "}}" in src:
        raise ValueError("code snippet contains a template brace pair")
    return src
