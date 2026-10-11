"""Turn the node-code libraries + a body into the source of one code_exec snippet.

A snippet is readable Python: a header, the library functions this node uses (and only those),
the config as indented JSON, then the body. Only when that is too big for the sandbox is the
library part packed (zlib + base64) - see CODE_BUDGET."""

from __future__ import annotations

import ast
import base64
import functools
import json
import textwrap
import zlib
from pathlib import Path
from typing import Any

LIB_FILES = ("collections_lib.py", "speech_lib.py", "negotiation_lib.py")
_SRC = {f: (Path(__file__).parent / f).read_text(encoding="utf-8") for f in LIB_FILES}
LIB = "\n\n".join(_SRC[f] for f in LIB_FILES)
# Smaller embeds for code that needs no negotiation engine (dates, speech, callbacks).
LIB_CALENDAR = "\n\n".join(_SRC[f] for f in LIB_FILES[:2])

# code_exec takes at most 131072 bytes of code and data together, and the data (variables and
# the whole conversation) grows ~1.3 KB per customer turn (measured 11 Oct: the platform's
# "Code and data for code_exec total 131136 bytes, over the 131072-byte limit" at turn 51 with
# 66 KB of code; +39 KB over the next 29 turns). A snippet up to 30 KB leaves room for ~75
# turns; the longest real calls had 45. Bigger snippets get their library part packed.
CODE_BUDGET = 30_000


def namespace() -> dict[str, Any]:
    """The libraries executed the way a code node executes them, for local runs and tests."""
    ns: dict[str, Any] = {}
    exec(compile(LIB, "nodecode", "exec"), ns)
    return ns


def config_literal(cfg: dict[str, Any]) -> str:
    # indent puts closing braces on separate lines; repr escapes the newlines, so the
    # snippet never holds two braces in a row (the platform's placeholder syntax).
    return repr(json.dumps(cfg, indent=1, ensure_ascii=False))


def config_block(cfg: dict[str, Any]) -> str:
    """CFG as indented JSON in a raw string, one key per line (readable in the editor). Falls
    back to the one-line literal when the JSON itself holds the raw string's quotes."""
    text = json.dumps(cfg, indent=1, ensure_ascii=False)
    if "'''" in text or text.endswith("\\"):
        return "CFG = json.loads(" + config_literal(cfg) + ")\n"
    return "CFG = json.loads(r'''\n" + text + "\n''')\n"


def _names(node: ast.AST) -> set[str]:
    return {x.id for x in ast.walk(node) if isinstance(x, ast.Name)}


def _defines(node: ast.stmt) -> set[str]:
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return {node.name}
    if isinstance(node, ast.Assign):
        return {t.id for t in node.targets if isinstance(t, ast.Name)}
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return {(a.asname or a.name).split(".")[0] for a in node.names}
    return set()


@functools.cache
def subset(lib: str, *sources: str) -> str:
    """The parts of `lib` the given sources use, in their order: every function, constant and
    import reached from a name in the sources, plus top-level statements that change a kept
    name (`_WORD_MONTHS.update(...)`). Static names only; the libraries look nothing up by
    string (no globals(), getattr or eval)."""
    tree = ast.parse(lib)
    need: set[str] = set()
    todo = set().union(*(_names(ast.parse(s)) for s in sources if s.strip()))
    changed = True
    while changed:
        changed = False
        while todo:
            name = todo.pop()
            if name in need:
                continue
            need.add(name)
            for st in tree.body:
                if name in _defines(st):
                    todo |= _names(st) - need
        for st in tree.body:
            if not _defines(st) and _names(st) & need and not _names(st) <= need:
                todo |= _names(st) - need
                changed = True
    kept = []
    for st in tree.body:
        d = _defines(st)
        if (d and d & need) or (not d and _names(st) & need):
            kept.append(ast.get_source_segment(lib, st, padded=True) or "")
    return "\n\n\n".join(kept) + "\n"


@functools.cache
def packed(lib: str) -> str:
    """The lib compressed and unpacked at run time (the sandbox has zlib, base64 and exec,
    measured 11 Oct). Base64 holds no braces."""
    blob = base64.b64encode(zlib.compress(lib.encode("utf-8"), 9)).decode("ascii")
    return (
        "import base64\nimport zlib\n"
        "exec(zlib.decompress(base64.b64decode('" + blob + "')).decode('utf-8'), globals())\n"
    )


def _defs_listed(code: str) -> str:
    names = [st.name for st in ast.parse(code).body if isinstance(st, ast.FunctionDef)]
    return "\n# ".join(textwrap.wrap(", ".join(names), 96)) if names else "(none)"


def snippet(body: str, cfg: dict[str, Any], lib: str = LIB, on_error: str = "") -> str:
    """`body` runs inside try/except with `CFG`, the lib and `vars` in scope. `on_error` runs
    after an exception, so no variable keeps the value of the turn before (a stale
    neg_action=record would call create_promise again)."""
    if "{{" in lib or "}}" in lib:
        raise ValueError("code library contains a template brace pair")
    inner = "\n".join("    " + line for line in body.strip().splitlines())
    handler = ""
    if on_error.strip():
        handler = (
            "    try:\n"
            + "\n".join("        " + line for line in on_error.strip().splitlines())
            + "\n    except Exception:\n        pass\n"
        )
    main = (
        "\n# ---- this node\n"
        + config_block(cfg)
        + "\ntry:\n"
        + inner
        + "\nexcept Exception as _e:\n    vars['code_error'] = str(_e)[:200]\n"
        + handler
    )
    code = subset(lib, body, on_error)
    rule = "# " + "-" * 70 + "\n"
    head = (
        rule + "# Library functions this node uses (from builder/fb/nodecode/*_lib.py):\n"
        "# " + _defs_listed(code) + "\n" + rule + "import json\n\n"
    )
    src = head + code + main
    if len(src.encode("utf-8")) > CODE_BUDGET:
        head = (
            rule + "# Library functions this node uses (from builder/fb/nodecode/*_lib.py),\n"
            "# packed: in plain text this node would leave too little of code_exec's\n"
            "# 131072 bytes for the call's data in a long call.\n"
            "# " + _defs_listed(code) + "\n" + rule + "import json\n"
        )
        src = head + packed(code) + main
    if "{{" in src or "}}" in src:
        raise ValueError("code snippet contains a template brace pair")
    return src
