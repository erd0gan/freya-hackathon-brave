"""Turn the node-code libraries + a body into the source of one code_exec snippet.

A snippet is readable Python: a header, the library functions this node uses (and only those),
the config as indented JSON, then the body. Only when that is too big for the sandbox is the
library part packed (zlib + base64) - see CODE_BUDGET."""

from __future__ import annotations

import ast
import base64
import functools
import json
import re
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
# No node may go over this, packed or not: 36 KB leaves ~95 KB of data, ~70 turns.
CODE_HARD_CAP = 36_000


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


def bare(code: str) -> str:
    """`code` without docstrings and comments (ast round trip, same behaviour): nobody reads
    the packed part, and the text left is code_exec room for the call's data."""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree) + "\n"


@functools.cache
def packed(lib: str) -> str:
    """The lib compressed and unpacked at run time (the sandbox has zlib, base64 and exec,
    measured 11 Oct). Base64 holds no braces."""
    blob = base64.b64encode(zlib.compress(lib.encode("utf-8"), 9)).decode("ascii")
    return (
        "import base64\nimport zlib\n"
        "exec(zlib.decompress(base64.b64decode('" + blob + "')).decode('utf-8'), globals())\n"
    )


@functools.cache
def packed_tight(code: str) -> str:
    """`code` packed as small as the sandbox allows, for a node that would be over the hard
    cap with `packed`: Ascii85 (same base64 module, a quarter of the text instead of a third
    on top of the compressed bytes; its alphabet holds no braces) in an escaped literal."""
    blob = base64.a85encode(zlib.compress(code.encode("utf-8"), 9)).decode("ascii")
    return (
        "import base64\nimport zlib\n"
        "exec(zlib.decompress(base64.a85decode(" + repr(blob) + ")).decode('utf-8'), globals())\n"
    )


def _defs_listed(code: str) -> str:
    names = [st.name for st in ast.parse(code).body if isinstance(st, ast.FunctionDef)]
    return "\n# ".join(textwrap.wrap(", ".join(names), 96)) if names else "(none)"


@functools.cache
def lang_hooks(lib: str) -> dict[str, str]:
    """speech_lib's LANG_HOOKS: language -> the line that registers that language's readers
    (`_rules('tr', _tr)`). A snippet holds only what its code names, so a call in that language
    gets the line, and with it the language's rules; an English node never holds them."""
    out: dict[str, str] = {}
    for st in ast.parse(lib).body:
        if not (
            isinstance(st, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "LANG_HOOKS" for t in st.targets)
        ):
            continue
        v = st.value
        if isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "dict":
            pairs = [(k.arg, k.value) for k in v.keywords if k.arg]
        elif isinstance(v, ast.Dict):
            pairs = [(getattr(k, "value", None), x) for k, x in zip(v.keys, v.values, strict=True)]
        else:
            pairs = []
        for k, x in pairs:
            if isinstance(k, str) and isinstance(x, ast.Constant) and isinstance(x.value, str):
                out[k] = x.value
    return out


def lang_line(lib: str, lang: Any) -> str:
    """The line registering the readers of `lang` ('' for English or a language the library
    has no readers for)."""
    code = str(lang or "en").strip().lower()[:2]
    hook = lang_hooks(lib).get(code, "") if code != "en" else ""
    return hook.strip() + "\n" if hook.strip() else ""


def locale_line(lib: str, body: str) -> str:
    """The line that makes CFG the call's config before the body runs: collections_lib's
    prepare_cfg applies the changes dated on or before the call's day (cfg["dated"]) and sets
    the language the date / money / speech helpers use. '' when the library has no prepare_cfg
    (yet) or the body already calls it (or set_locale) itself."""
    if "prepare_cfg(" in body or "set_locale(" in body:
        return ""
    if "def prepare_cfg(" in lib:
        return "CFG = prepare_cfg(CFG, vars.get('now'))\n"
    if "def set_locale(" in lib:
        return "set_locale(CFG)\n"
    return ""


def snippet(
    body: str, cfg: dict[str, Any], lib: str = LIB, on_error: str = "", pre: str = ""
) -> str:
    """`body` runs inside try/except with `CFG`, the lib and `vars` in scope. `on_error` runs
    after an exception, so no variable keeps the value of the turn before (a stale
    neg_action=record would call create_promise again). `pre` runs first, before CFG is
    prepared for the call's day (a brief that names the call's date otherwise copies it to
    vars['now'] there); both are inside their own try."""
    if "{{" in lib or "}}" in lib:
        raise ValueError("code library contains a template brace pair")
    # the call's language readers first (only in a node whose code reads what the customer
    # said: the readers look their language's rules up in LOCALE["rules"]), then the config of
    # the call's day (a config that cannot be prepared still runs the body as built)
    hook = lang_line(lib, cfg.get("lang"))
    if hook:
        own = subset(lib, body, on_error, pre)
        if not re.search(r"""_rewrite\(|["']rules["']""", own):
            hook = ""
    for extra in (hook, locale_line(lib, body)):
        if extra:
            pre = pre.strip() + ("\n" if pre.strip() else "") + extra
    prep = ""
    if pre.strip():
        prep = (
            "try:\n"
            + "\n".join("    " + line for line in pre.strip().splitlines())
            + "\nexcept Exception:\n    pass\n"
        )
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
        + prep
        + "\ntry:\n"
        + inner
        + "\nexcept Exception as _e:\n    vars['code_error'] = str(_e)[:200]\n"
        + handler
    )
    code = subset(lib, body, on_error, pre)
    rule = "# " + "-" * 70 + "\n"
    head = (
        rule + "# Library functions this node uses (from builder/fb/nodecode/*_lib.py):\n"
        "# " + _defs_listed(code) + "\n" + rule + "import json\n\n"
    )
    src = head + code + main
    if len(src.encode("utf-8")) > CODE_BUDGET:
        # still readable without the comments and docstrings, when that fits (the same code:
        # the ast round trip the packed form uses too); a pair of braces the round trip
        # brings out (the platform's placeholder) sends it to the packed form instead
        lean = bare(code)
        head = (
            rule + "# Library functions this node uses (from builder/fb/nodecode/*_lib.py),\n"
            "# without their comments and docstrings (in full this node would leave too little\n"
            "# of code_exec's 131072 bytes for the call's data in a long call).\n"
            "# " + _defs_listed(code) + "\n" + rule + "import json\n\n"
        )
        lean_src = head + lean + main
        fits = len(lean_src.encode("utf-8")) <= CODE_BUDGET
        if fits and "{{" not in lean_src and "}}" not in lean_src:
            return lean_src
        head = (
            rule + "# Library functions this node uses (from builder/fb/nodecode/*_lib.py),\n"
            "# packed: in plain text this node would leave too little of code_exec's\n"
            "# 131072 bytes for the call's data in a long call.\n"
            "# " + _defs_listed(code) + "\n" + rule + "import json\n"
        )
        src = head + packed(bare(code)) + main
        # every byte left counts now: first the list of names goes, then the library is packed
        # tighter; the config stays readable when that fits, else it goes into the packed part
        # with the library (it runs there first, as above)
        head = (
            rule + "# Library functions this node uses (from builder/fb/nodecode/*_lib.py),\n"
            "# packed to leave code_exec room for the call's data.\n" + rule + "import json\n"
        )
        if len(src.encode("utf-8")) > CODE_HARD_CAP:
            src = head + packed(bare(code)) + main
        if len(src.encode("utf-8")) > CODE_HARD_CAP:
            src = head + packed_tight(bare(code)) + main
        if len(src.encode("utf-8")) > CODE_HARD_CAP:
            head = (
                rule + "# Library functions this node uses (from builder/fb/nodecode/*_lib.py)\n"
                "# and this node's CFG, packed to leave code_exec room for the call's data.\n"
                + rule
                + "import json\n"
            )
            # the platform runs a node's code inside a function: `CFG = prepare_cfg(CFG, ...)`
            # below makes CFG a local there, so the packed part's global must be bound to it
            # first (else "cannot access local variable 'CFG'" on every turn, the error handler
            # too, and the last sentence was said again and again: stress C-08, 11 Oct)
            tail = main.replace(
                config_block(cfg),
                "# CFG: in the packed part above\nCFG = globals()['CFG']\n",
                1,
            )
            src = head + packed_tight(bare(code) + "\n" + config_block(cfg)) + tail
    if "{{" in src or "}}" in src:
        raise ValueError("code snippet contains a template brace pair")
    return src
