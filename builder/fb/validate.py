"""Structural workflow validator. No model calls; runs before every save and export.

Checks (docs/HACKATHON_REALIGN.md §3.2): integrity of the JSON the platform would silently
strip or reject, graph shape, policy (which tools are reachable before identity is verified)
and scope (only the tools, URLs, fields and enum values of the OpenAPI file).

    python3 -m fb.validate <workflow.json> <openapi.json>     (from builder/)
"""

from __future__ import annotations

import ast
import base64
import binascii
import json
import re
import sys
import zlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fb.openapi_tools import ToolDef, parse_openapi

try:
    from fb.nodecode.embed import CODE_BUDGET, CODE_HARD_CAP
except ImportError:  # the validator stays usable without the node-code package
    CODE_BUDGET, CODE_HARD_CAP = 30_000, 36_000

NODE_TYPES = {"conversation", "api_request", "code", "end_call"}
OTHER_NODE_TYPES = {
    "transfer_call",
    "integration",
    "knowledge_base",
    "reschedule_call",
    "invoke_agent",
    "exit",
}
TOP_KEYS = {
    "initial_node",
    "nodes",
    "system_prompt",
    "global_nodes",
    "test_variables",
    "context_variables",
    "tools",
    "nodePositions",
    "agent_config",
}
DETERMINISTIC_OPS = {
    "equals",
    "not_equals",
    "greater_than",
    "less_than",
    "contains",
    "is_set",
    "is_not_set",
}
NEEDS_OPERAND = {"equals", "not_equals", "greater_than", "less_than", "contains"}
EXTRACTION_TYPES = {
    "string",
    "integer",
    "float",
    "boolean",
    "date",
    "datetime",
    "name",
    "phone_number",
}
API_PARAM_KEYS = {"url", "method", "headers", "body", "connect_retries"}
HOOK_HANDLERS = {"__function__:api_call"}
LIFECYCLE_POINTS = {"start_of_call", "end_of_call"}
PREVIOUS = "__previous__"
# The platform's export writes null for unset optional node fields.
EXPORT_NULLABLE = {"max_turn_transition", "passthrough_default_transition", "max_node_turns"}

# code_exec takes at most 131072 bytes of code and data together; the data (variables and the
# whole conversation) needs room too (~1.3 KB per customer turn, see fb/nodecode/embed.py).
# Over CODE_MAX a ~45-turn call (the longest seen) no longer fits: an error. Between
# CODE_HARD_CAP and CODE_MAX the agent still works for ~70 turns minus 1 per 1.3 KB: a warning,
# since stopping the build there would publish nothing for a workflow that mostly works.
CODE_DATA_LIMIT = 131_072
CODE_DATA_RESERVE = 60_000
CODE_MAX = CODE_DATA_LIMIT - CODE_DATA_RESERVE

# The phone agent may call only the bank's tool API (kit/README.md, Rules): code nodes and
# hooks must not reach the network or start processes.
FORBIDDEN_MODULES = {
    "socket",
    "ssl",
    "select",
    "selectors",
    "urllib",
    "urllib2",
    "urllib3",
    "http",
    "httplib",
    "httpx",
    "requests",
    "aiohttp",
    "asyncio",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "telnetlib",
    "nntplib",
    "xmlrpc",
    "websocket",
    "websockets",
    "pycurl",
    "subprocess",
    "multiprocessing",
    "pty",
    "ctypes",
    "importlib",
}
FORBIDDEN_OS_CALLS = re.compile(
    r"^(system|popen|fork|forkpty|kill|exec\w*|spawn\w*|posix_spawn\w*)$"
)
DYNAMIC_CALLS = {"eval", "exec", "compile"}
# Names a snippet gets from the platform: code nodes `vars`, hooks `vars` and `config`.
SNIPPET_INPUTS = {"vars", "config"}

# Call variables of the kit's cases (public-test-set.json, kit/README.md).
KIT_CALL_VARS = {"customer_id", "first_name", "last_name", "full_name", "title", "now"}
# Variables a node reachable before the identity check may show in its text: the
# customer's name, and the lines the workflow sets for those nodes (a closing line, the
# callback offer and its window).
PRE_VERIFY_OK = KIT_CALL_VARS | {
    "final_say",
    "final_outcome",
    "final_reason",
    "cb_say",
    "callback_window_text",
}
# Variable names that carry account data (amounts, dates, products, the negotiation).
DEBT_VAR = re.compile(
    r"past_due|amount|balance|floor|deadline|days_late|arrear|instal|minimum_pay|debt|owed"
    r"|product|neg_say|neg_state|^final_(?!say$|outcome$|reason$)",
    re.I,
)
# Keys the platform adds after a tool or code node runs (tools-and-code.md).
AUTO_SUFFIXES = ("_response", "_status_code", "_success_count", "_failure_count")
PLACEHOLDER = re.compile(r"\{\{\s*([^{}\s]+)\s*\}\}")
IDENT = re.compile(r"^[A-Za-z_][\w.\-]*$")


@dataclass
class Issue:
    level: str  # "error" | "warn"
    code: str
    where: str
    msg: str

    def __str__(self) -> str:
        return f"{self.level.upper():<5} {self.code:<24} {self.where}: {self.msg}"


@dataclass
class Policy:
    """What the workflow must respect. Built from the spec; defaults fit the kit's briefs."""

    # Tools that must not be reachable before identity is verified (score.py + brief rules).
    gated_tools: set[str] = field(
        default_factory=lambda: {"get_account", "create_promise", "add_note"}
    )
    # A transition is a verification gate when every OR branch requires one of these
    # variables to equal one of `verified_values`.
    verified_vars: set[str] = field(
        default_factory=lambda: {
            "identity_state",
            "verify_identity_response.data.all_verified",
        }
    )
    verified_values: set[str] = field(default_factory=lambda: {"true", "True", "verified"})
    close_tool: str = "close_call"
    # The tool that checks identity; a workflow holding a gated tool must also hold this one.
    verify_tool: str = "verify_identity"
    # Variables passed with the call (spec call_variables); the kit's names are always known.
    call_vars: set[str] = field(default_factory=lambda: set(KIT_CALL_VARS))
    # Code (node or hook) bigger than this is an error; see CODE_MAX.
    code_error_bytes: int = CODE_MAX
    # Nodes allowed to hold the promise tool; empty means "any gated node".
    promise_tool: str = "create_promise"
    promise_nodes: set[str] = field(default_factory=set)
    require_turn_caps: bool = True
    # Passthrough nodes of a retry loop whose exit is decided by code with a counter
    # (create_promise retry, one add_note per product). Not reported as passthrough cycles.
    bounded_loops: set[str] = field(default_factory=set)
    # A platform export carries agent_config and nulls on optional node fields; accept those.
    exported: bool = False


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.level == "warn"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def codes(self) -> set[str]:
        return {i.code for i in self.issues}

    def summary(self) -> str:
        return f"{len(self.errors)} errors, {len(self.warnings)} warnings"


_BLOB = re.compile(r"b64decode\(\s*['\"]([A-Za-z0-9+/=\s]+)['\"]")


def _packed_blobs(src: str) -> list[str]:
    """The base64 parts of `exec(zlib.decompress(base64.b64decode('...')) ...)` (embed.packed)."""
    if "zlib.decompress" not in src:
        return []
    return _BLOB.findall(src)


def _unpack(blob: str) -> str | None:
    try:
        return zlib.decompress(base64.b64decode(blob)).decode("utf-8")
    except (binascii.Error, zlib.error, UnicodeDecodeError, ValueError):
        return None


def placeholders(text: Any) -> set[str]:
    return set(PLACEHOLDER.findall(text)) if isinstance(text, str) else set()


# Tools that change the bank's records or the call: what they send must come from code (or the
# call's own variables), never straight from what a model heard.
WRITE_TOOLS = {
    "create_promise",
    "add_note",
    "schedule_callback",
    "transfer_to_agent",
    "close_call",
    "verify_identity",
}
# Variables whose literal values in code are checked against the OpenAPI enum of the request
# field they fill (close_call outcome / reason, transfer queue).
ENUM_VARS = {"final_outcome", "final_reason", "transfer_queue"}


def _key_of(node: ast.AST, consts: dict[str, str]) -> str | None:
    """The string key of `x['key']` or `x[NAME]` with NAME = 'key' in the same code."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id)
    return None


def _targets(node: ast.AST) -> list[ast.AST]:
    if isinstance(node, (ast.Tuple, ast.List)):
        return [x for e in node.elts for x in _targets(e)]
    return [node]


def _trees(src: str, depth: int = 0) -> list[ast.AST]:
    """The code and every packed part of it (embed.packed), parsed; what does not parse is
    left out."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    out: list[ast.AST] = [tree]
    if depth < 3:
        for blob in _packed_blobs(src):
            inner = _unpack(blob)
            if inner:
                out += _trees(inner, depth + 1)
    return out


@lru_cache(maxsize=256)
def code_writes(src: str) -> tuple[frozenset[str], tuple[tuple[str, str], ...]]:
    """(keys the code assigns by subscript, `x['k'] = ...`, packed parts included, plus keys of
    `.update({...})` / `.update(k=...)` / `.setdefault('k', ...)`; the (key, string literal)
    pairs of those assignments). Generous on purpose: anything that may write a variable."""
    keys: set[str] = set()
    lits: list[tuple[str, str]] = []
    for tree in _trees(src):
        consts: dict[str, str] = {}
        for x in ast.walk(tree):
            if (
                isinstance(x, ast.Assign)
                and len(x.targets) == 1
                and isinstance(x.targets[0], ast.Name)
                and isinstance(x.value, ast.Constant)
                and isinstance(x.value.value, str)
            ):
                consts[x.targets[0].id] = x.value.value
        for x in ast.walk(tree):
            if isinstance(x, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                tgts = x.targets if isinstance(x, ast.Assign) else [x.target]
                value = x.value
                for tgt in tgts:
                    flat = _targets(tgt)
                    vals: list[ast.AST | None] = [value] * len(flat)
                    if len(flat) > 1:
                        elts = value.elts if isinstance(value, (ast.Tuple, ast.List)) else []
                        vals = list(elts) if len(elts) == len(flat) else [None] * len(flat)
                    for t, v in zip(flat, vals, strict=True):
                        if not isinstance(t, ast.Subscript):
                            continue
                        k = _key_of(t.slice, consts)
                        if k is None:
                            continue
                        keys.add(k)
                        if isinstance(v, ast.Constant) and isinstance(v.value, str):
                            lits.append((k, v.value))
            elif isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute):
                if x.func.attr == "setdefault" and x.args:
                    k = _key_of(x.args[0], consts)
                    if k is not None:
                        keys.add(k)
                elif x.func.attr == "update":
                    keys |= {kw.arg for kw in x.keywords if kw.arg}
                    for a in x.args:
                        if isinstance(a, ast.Dict):
                            keys |= {k for k in (_key_of(e, consts) for e in a.keys if e) if k}
    return frozenset(keys), tuple(lits)


class _V:
    def __init__(self, wf: dict[str, Any], tools: dict[str, ToolDef], policy: Policy) -> None:
        self.wf = wf
        self.api = tools
        self.p = policy
        self.r = Report()
        self.nodes: dict[str, Any] = wf.get("nodes") if isinstance(wf.get("nodes"), dict) else {}
        gn = wf.get("global_nodes")
        self.globals: dict[str, Any] = gn if isinstance(gn, dict) else {}
        # Names any code node or hook may write (string constants of the code, packed parts
        # included): a generous set, used only for warnings about undefined variables.
        self.code_names: set[str] = set()

    def err(self, code: str, where: str, msg: str) -> None:
        self.r.issues.append(Issue("error", code, where, msg))

    def warn(self, code: str, where: str, msg: str) -> None:
        self.r.issues.append(Issue("warn", code, where, msg))

    def guarded(self, where: str, fn: Any, *args: Any) -> None:
        """Run a check that must never stop the build by crashing on a shape it did not
        expect: a crash is a warning."""
        try:
            fn(*args)
        except Exception as e:
            self.warn("validator_internal", where, f"{fn.__name__}: {type(e).__name__}: {e}")

    # ---------------------------------------------------------------- integrity
    def nulls(self, x: Any, path: str) -> None:
        if self.p.exported and path == "agent_config":
            return  # the platform's own config block, never sent by the builder
        if x is None:
            if self.p.exported and path.rsplit(".", 1)[-1] in EXPORT_NULLABLE:
                return
            self.err("null", path or "$", "null value (the schema is strict; omit the field)")
        elif isinstance(x, dict):
            for k, v in x.items():
                self.nulls(v, f"{path}.{k}" if path else str(k))
        elif isinstance(x, list):
            for i, v in enumerate(x):
                self.nulls(v, f"{path}[{i}]")

    def top(self) -> None:
        wf = self.wf
        for k in wf:
            if k == "agent_config":
                if self.p.exported:
                    continue
                self.err("agent_config", k, "omit agent_config; the event fixes the model")
            elif k not in TOP_KEYS:
                self.warn("unknown_key", k, "unknown top-level key")
        if not self.nodes:
            self.err("no_nodes", "nodes", "workflow has no nodes")
        init = wf.get("initial_node")
        if init not in self.nodes:
            self.err("initial_node", "initial_node", f"{init!r} is not a node")
        if "context_variables" in wf:
            self.warn("context_variables", "context_variables", "dropped by the API on save")
        for i, t in enumerate(wf.get("tools") or []):
            where = f"tools[{i}]"
            self.tool(t, where, placement="lifecycle")
            pts = ((t.get("config") or {}).get("alwaysRunsAt")) or []
            if not pts:
                self.err(
                    "lifecycle_missing",
                    where,
                    "workflow-level tool without alwaysRunsAt never runs",
                )
            for pt in pts:
                if pt not in LIFECYCLE_POINTS:
                    self.err("lifecycle_point", where, f"{pt!r} never fires")

    def node(self, key: str, n: dict[str, Any], is_global: bool = False) -> None:
        where = ("global_nodes." if is_global else "nodes.") + key
        nt = n.get("node_type")
        if nt not in NODE_TYPES:
            if nt in OTHER_NODE_TYPES:
                self.warn("node_type", where, f"{nt!r} is not used by this builder")
            else:
                self.err("node_type", where, f"unknown node_type {nt!r}")
        if n.get("name") != key:
            self.warn("node_name", where, f"name {n.get('name')!r} differs from key")
        if n.get("tools") and nt != "conversation":
            self.err("tools_placement", where, "tools are stripped from non-conversation nodes")
        if n.get("pre_actions") and nt not in ("api_request", "code"):
            self.err("pre_actions_placement", where, "pre_actions are stripped from this node type")
        if n.get("response_mapping") and not n.get("pre_actions"):
            self.warn(
                "node_mapping", where, "node-level response_mapping only applies to pre_actions"
            )
        for i, t in enumerate(n.get("tools") or []):
            self.tool(t, f"{where}.tools[{i}]", placement="tool")
        for i, t in enumerate(n.get("pre_actions") or []):
            self.tool(t, f"{where}.pre_actions[{i}]", placement="pre_action")
            if isinstance(t.get("response_mapping"), list) and t["response_mapping"]:
                self.err(
                    "mapping_in_pre_action",
                    f"{where}.pre_actions[{i}]",
                    "response_mapping inside a pre_action is ignored; put it on the node",
                )
        for m in n.get("response_mapping") or []:
            self.mapping(m, where)
        trs = n.get("transitions")
        if not isinstance(trs, list):
            self.err("transitions", where, "transitions must be a list")
            trs = []
        if nt == "end_call":
            if trs:
                self.err("end_transitions", where, "end_call nodes have no transitions")
            term = n.get("terminal") or {}
            fn = term.get("function") or {}
            if fn.get("handler") != "__function__:end_call":
                self.err(
                    "terminal", where, "end_call node needs a terminal with the end_call handler"
                )
            props = (fn.get("parameters") or {}).get("properties") or {}
            mode = (props.get("message_mode") or {}).get("default")
            ent = (n.get("on_enter") or {}).get("type")
            if mode == "none" and ent == "tts":
                self.warn(
                    "silent_farewell", where, "on_enter line with message_mode none is never said"
                )
        elif n.get("terminal"):
            self.warn("terminal", where, "terminal is only used on end_call nodes")
        for i, t in enumerate(trs):
            self.transition(t, f"{where}.transitions[{i}]", is_global)
        self.dead_transitions(trs, where)
        if n.get("passthrough"):
            if not trs or (trs[-1].get("condition") or {}).get("operator") != "always":
                self.err(
                    "passthrough_always",
                    where,
                    "a passthrough node must end with an always transition",
                )
            pdt = n.get("passthrough_default_transition")
            if not pdt:
                self.warn("passthrough_default", where, "no passthrough_default_transition")
            elif pdt not in self.nodes and not (is_global and pdt == PREVIOUS):
                self.err(
                    "bad_target", where, f"passthrough_default_transition {pdt!r} is not a node"
                )
        if nt == "conversation" and not is_global:
            mtt = n.get("max_turn_transition")
            if not mtt:
                if self.p.require_turn_caps:
                    self.err("turn_cap", where, "conversation node without max_turn_transition")
            elif mtt not in self.nodes:
                self.err("bad_target", where, f"max_turn_transition {mtt!r} is not a node")
            elif mtt == key:
                self.err(
                    "turn_cap_self",
                    where,
                    "max_turn_transition points to the node itself: the turn cap never ends it",
                )
            if (
                not n.get("tools")
                and n.get("task_message")
                and "tool" not in n["task_message"].lower()
            ):
                self.warn(
                    "no_tool_rule", where, "node without tools should say no tool call is allowed"
                )
        for i, x in enumerate(n.get("extraction_schema") or []):
            self.extraction(x, f"{where}.extraction_schema[{i}]")
        if is_global:
            excl = set(n.get("excluded_nodes") or [])
            for k in excl - set(self.nodes):
                self.err("bad_target", where, f"excluded node {k!r} does not exist")
            ends = {k for k, v in self.nodes.items() if v.get("node_type") == "end_call"}
            if ends - excl:
                self.warn("global_terminal", where, "terminal nodes are not excluded")
            if "condition" not in n:
                self.err("global_condition", where, "global node without condition never fires")
            else:
                self.condition(n["condition"], where + ".condition")

    def transition(self, t: dict[str, Any], where: str, is_global: bool) -> None:
        tgt = t.get("target_node")
        if tgt not in self.nodes and not (tgt == PREVIOUS and is_global):
            self.err("bad_target", where, f"target {tgt!r} is not a node")
        if "condition" not in t:
            self.err("no_condition", where, "transition without condition")
            return
        self.condition(t["condition"], where + ".condition")

    def condition(self, c: Any, where: str, depth: int = 0) -> None:
        if not isinstance(c, dict):
            self.err("condition", where, "condition must be an object")
            return
        if "logic" in c or "conditions" in c:
            logic = c.get("logic")
            if depth == 0 and logic != "or":
                self.err("top_logic", where, "the top-level group must use logic 'or'")
            if logic not in ("or", "and"):
                self.err("logic", where, f"logic {logic!r}")
            subs = c.get("conditions")
            if not isinstance(subs, list) or not subs:
                self.err("empty_group", where, "condition group without conditions")
                return
            for i, s in enumerate(subs):
                self.condition(s, f"{where}.conditions[{i}]", depth + 1)
            self.bool_spellings(subs, where)
            return
        op = c.get("operator")
        if op == "always":
            return
        if op == "matches_intent":
            if not c.get("description"):
                self.err("intent_description", where, "matches_intent needs a description")
            return
        if op not in DETERMINISTIC_OPS:
            self.err("operator", where, f"unknown operator {op!r}")
            return
        if not c.get("variable"):
            self.err("variable", where, f"{op} needs a variable")
        if op in NEEDS_OPERAND and "operand" not in c:
            self.err("operand", where, f"{op} needs an operand")
        if op in ("greater_than", "less_than"):
            try:
                float(c.get("operand"))
            except (TypeError, ValueError):
                self.err("operand", where, f"{op} operand must be a number")

    def bool_spellings(self, subs: list[Any], where: str) -> None:
        seen: dict[str, set[str]] = {}
        for s in subs:
            if isinstance(s, dict) and s.get("operator") == "equals":
                seen.setdefault(str(s.get("variable")), set()).add(str(s.get("operand")))
        for var, ops in seen.items():
            if ("true" in ops) != ("True" in ops) or ("false" in ops) != ("False" in ops):
                self.warn("bool_spelling", where, f"{var}: test both 'true' and 'True'")

    def dead_transitions(self, trs: list[Any], where: str) -> None:
        for i, t in enumerate(trs):
            if isinstance(t, dict) and (t.get("condition") or {}).get("operator") == "always":
                if i < len(trs) - 1:
                    self.err("dead_transition", where, f"transitions after index {i} never fire")
                targets_before = {x.get("target_node") for x in trs[:i]}
                if t.get("target_node") in targets_before:
                    self.err(
                        "merged_always",
                        where,
                        f"same target {t.get('target_node')!r} as an earlier transition: "
                        "the engine merges them into one unconditional transition",
                    )
                break

    def extraction(self, x: dict[str, Any], where: str) -> None:
        if not x.get("name"):
            self.err("extraction", where, "extraction variable without name")
        if x.get("type") not in EXTRACTION_TYPES:
            self.err("extraction", where, f"type {x.get('type')!r}")
        if "min_confidence" not in x:
            self.warn("min_confidence", where, "default 0.95 discards most values")
        if "extraction_prompt" in x:
            ep = str(x["extraction_prompt"])
            if "{{extractor.speech}}" not in ep and "extractor.conversation" not in ep:
                self.err("extraction_prompt", where, "custom prompt never shows the user's message")

    def mapping(self, m: Any, where: str) -> None:
        if not isinstance(m, dict) or not m.get("path") or not m.get("variable"):
            self.err("mapping", where, "response_mapping entries need path and variable")
            return
        if not str(m["path"]).startswith("data."):
            self.warn("mapping_path", where, f"path {m['path']!r} should start with 'data.'")

    def code(self, src: Any, where: str) -> None:
        if not isinstance(src, str) or not src.strip():
            return
        if "{{" in src or "}}" in src:
            self.err("code_braces", where, "code contains '{{' or '}}'")
        size = len(src.encode("utf-8"))
        if size > self.p.code_error_bytes:
            self.err(
                "code_size",
                where,
                f"{size} bytes of code; over {self.p.code_error_bytes} the call's data no longer "
                f"fits in code_exec's {CODE_DATA_LIMIT} bytes in a long call",
            )
        elif size > CODE_HARD_CAP:
            self.warn(
                "code_size",
                where,
                f"{size} bytes of code, over the {CODE_HARD_CAP} cap: a long call may run out "
                f"of code_exec's {CODE_DATA_LIMIT} bytes",
            )
        elif size > CODE_BUDGET and not _packed_blobs(src):
            self.warn("code_size", where, f"{size} bytes of code, over the {CODE_BUDGET} budget")
        try:
            tree = compile(src, where, "exec", ast.PyCF_ONLY_AST)
        except SyntaxError as e:
            self.err("code_syntax", where, f"line {e.lineno}: {e.msg}")
            tree = None
        if tree is not None:
            self.guarded(where, self.scan_code, tree, where)
        if "try:" not in src:
            self.warn("code_try", where, "snippet is not wrapped in try/except")

    def scan_code(self, tree: ast.AST, where: str, depth: int = 0) -> None:
        """No network, no process, no code built from call data (the agent may call only the
        bank's tool API). A packed library part (embed.packed) is unpacked and scanned too.
        Collects the names the code may write as variables."""
        for x in ast.walk(tree):
            if isinstance(x, ast.Constant) and isinstance(x.value, str):
                if IDENT.match(x.value) and len(x.value) <= 80:
                    self.code_names.add(x.value)
            elif isinstance(x, ast.keyword) and x.arg:
                self.code_names.add(x.arg)
            if isinstance(x, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in x.names] if isinstance(x, ast.Import) else [x.module or ""]
                for m in mods:
                    top = m.split(".")[0]
                    if top in FORBIDDEN_MODULES:
                        self.err("code_forbidden", where, f"imports {m!r} (network or process)")
                    elif top == "os" and isinstance(x, ast.ImportFrom):
                        bad = [a.name for a in x.names if FORBIDDEN_OS_CALLS.match(a.name)]
                        if bad:
                            self.err("code_forbidden", where, f"imports os.{bad[0]}")
            elif isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name):
                if x.value.id == "os" and FORBIDDEN_OS_CALLS.match(x.attr):
                    self.err("code_forbidden", where, f"uses os.{x.attr} (starts a process)")
            elif isinstance(x, ast.Call) and isinstance(x.func, ast.Name):
                fn = x.func.id
                if fn == "__import__":
                    self.err("code_forbidden", where, "uses __import__")
                elif fn in DYNAMIC_CALLS:
                    self.dynamic_call(x, fn, where, depth)

    def dynamic_call(self, call: ast.Call, fn: str, where: str, depth: int) -> None:
        arg = call.args[0] if call.args else None
        names = {n.id for n in ast.walk(arg) if isinstance(n, ast.Name)} if arg else set()
        if names & SNIPPET_INPUTS:
            self.err("code_forbidden", where, f"{fn}() of code built from call data")
            return
        blobs = _packed_blobs(ast.unparse(call)) if names <= {"zlib", "base64"} else []
        if not blobs:
            self.warn("code_dynamic", where, f"{fn}() of code the validator cannot read")
            return
        for blob in blobs:
            inner = _unpack(blob)
            if inner is None or depth > 2:
                self.warn("code_dynamic", where, f"{fn}() of a packed part that does not unpack")
                continue
            try:
                self.scan_code(ast.parse(inner), where + " (packed)", depth + 1)
            except SyntaxError as e:
                self.err("code_syntax", where + " (packed)", f"line {e.lineno}: {e.msg}")

    # ---------------------------------------------------------------- tools / scope
    def tool(self, t: dict[str, Any], where: str, placement: str) -> None:
        if not isinstance(t, dict) or t.get("type") != "function":
            self.err("tool_shape", where, "tool must be {type: function, function: {...}}")
            return
        fn = t.get("function") or {}
        name = fn.get("name")
        h = fn.get("handler")
        if not name:
            self.err("tool_shape", where, "tool without function.name")
        params = fn.get("parameters") or {}
        props = params.get("properties") or {}
        for k in ("pre_process_code", "post_process_code"):
            if t.get(k) and h not in HOOK_HANDLERS:
                self.err("hook_handler", where, f"{k} is removed from {h} tools")
            self.code(t.get(k), f"{where}.{k}")
        if t.get("response_mapping") and placement == "pre_action":
            pass  # reported by node()
        for m in t.get("response_mapping") or []:
            self.mapping(m, where)
        if h == "__function__:code_exec":
            self.code((props.get("code") or {}).get("default"), f"{where}.code")
            return
        if h == "__function__:end_call":
            if placement == "pre_action":
                self.err("end_call_placement", where, "end_call is not a pre_action")
            return
        if h != "__function__:api_call":
            self.err("handler", where, f"handler {h!r} is not used by this builder")
            return
        self.api_call(t, name, props, where, placement)

    def api_call(
        self, t: dict[str, Any], name: str, props: dict[str, Any], where: str, placement: str
    ) -> None:
        for k, v in props.items():
            if k not in API_PARAM_KEYS:
                self.err("param_outside_body", where, f"{k!r} must be a leaf under body.properties")
            if isinstance(v, dict) and "const" in v:
                self.err("const", where, f"{k}: const never bakes; use default")
        url = (props.get("url") or {}).get("default")
        method = (props.get("method") or {}).get("default")
        if not url:
            self.err("url", where, "url needs a default")
        if not method:
            self.err("method", where, "method needs a default")
        api = self.api.get(name)
        if api is None:
            self.err("unknown_tool", where, f"{name!r} is not in the OpenAPI file")
            return
        if url and url != api.url:
            if urlparse(url).netloc != urlparse(api.url).netloc:
                self.err("foreign_host", where, f"{url} is not the OpenAPI server")
            else:
                self.err("url_mismatch", where, f"{url} != {api.url}")
        if method and str(method).upper() != api.method:
            self.err("method_mismatch", where, f"{method} != {api.method}")
        body = props.get("body") or {}
        if "default" in body and not body.get("properties"):
            self.err(
                "body_template", where, "body as one default object hides fields from the model"
            )
        bprops = body.get("properties") or {}
        req = set(body.get("required") or [])
        for f in req - set(bprops):
            self.err("required_missing", where, f"required {f!r} has no property")
        for f in api.fields:
            leaf = bprops.get(f.name)
            if leaf is None:
                if f.required:
                    self.err("required_field", where, f"OpenAPI requires {f.name!r}")
                continue
            if "const" in leaf:
                self.err("const", where, f"{f.name}: const never bakes; use default")
            if "default" not in leaf:
                self.warn("leaf_default", where, f"{f.name}: no default")
            enum = [str(e) for e in leaf.get("enum") or []]
            if f.enum and enum and not set(enum) <= set(f.enum):
                self.err(
                    "enum_scope",
                    where,
                    f"{f.name}: {sorted(set(enum) - set(f.enum))} not in OpenAPI",
                )
            dflt = leaf.get("default")
            if f.enum and isinstance(dflt, str) and "{{" not in dflt and dflt not in f.enum:
                self.err("enum_scope", where, f"{f.name}: baked {dflt!r} not in OpenAPI enum")
        for k in set(bprops) - {f.name for f in api.fields}:
            self.warn("extra_field", where, f"{k!r} is not an OpenAPI field")

    # ---------------------------------------------------------------- graph
    def edges(self, key: str, n: dict[str, Any]) -> list[tuple[str, dict[str, Any] | None]]:
        out: list[tuple[str, dict[str, Any] | None]] = []
        for t in n.get("transitions") or []:
            if isinstance(t, dict) and t.get("target_node") in self.nodes:
                out.append((t["target_node"], t.get("condition")))
        for k in ("max_turn_transition", "passthrough_default_transition"):
            tgt = n.get(k)
            if tgt in self.nodes:
                out.append((tgt, {"operator": "always"} if k == "max_turn_transition" else None))
        return out

    def is_gate(self, cond: Any) -> bool:
        """True when the condition can only hold after identity is verified."""
        if not isinstance(cond, dict):
            return False
        if "conditions" in cond:
            subs = cond.get("conditions") or []
            if cond.get("logic") == "and":
                return any(self.is_gate(s) for s in subs)
            return bool(subs) and all(self.is_gate(s) for s in subs)
        return (
            cond.get("operator") == "equals"
            and cond.get("variable") in self.p.verified_vars
            and str(cond.get("operand")) in self.p.verified_values
        )

    def tool_names(self, n: dict[str, Any]) -> set[str]:
        return {
            (t.get("function") or {}).get("name")
            for t in (n.get("tools") or []) + (n.get("pre_actions") or [])
            if isinstance(t, dict)
        }

    def ends_call(self, n: dict[str, Any]) -> bool:
        if n.get("node_type") == "end_call":
            return True
        return any(
            (t.get("function") or {}).get("handler") == "__function__:end_call"
            for t in n.get("tools") or []
            if isinstance(t, dict)
        )

    def graph(self) -> None:
        init = self.wf.get("initial_node")
        if init not in self.nodes:
            return
        adj = {k: self.edges(k, n) for k, n in self.nodes.items()}
        g_entries = {
            t.get("target_node") or g.get("passthrough_default_transition")
            for g in self.globals.values()
            for t in (g.get("transitions") or [])
        }
        reach = self.closure({init}, adj, lambda c: True)
        reach |= self.closure({x for x in g_entries if x in self.nodes}, adj, lambda c: True)
        for k in set(self.nodes) - reach:
            self.err("unreachable", f"nodes.{k}", "not reachable from initial_node")
        # every node can reach an end of call
        ends = {k for k, n in self.nodes.items() if self.ends_call(n)}
        if not ends:
            self.err("no_end", "nodes", "no end_call node and no end_call tool")
        rev: dict[str, set[str]] = {k: set() for k in self.nodes}
        for k, es in adj.items():
            for tgt, _ in es:
                rev[tgt].add(k)
        can_end = set(ends)
        stack = list(ends)
        while stack:
            for src in rev[stack.pop()]:
                if src not in can_end:
                    can_end.add(src)
                    stack.append(src)
        for k in reach - can_end:
            self.err("no_path_to_end", f"nodes.{k}", "no path to an end of call")
        # a cycle of passthrough nodes spins without user input
        pt = {k for k, n in self.nodes.items() if n.get("passthrough")}
        for k in sorted(pt - self.p.bounded_loops):
            seen: set[str] = set()
            stack = [t for t, _ in adj[k] if t in pt]
            while stack:
                x = stack.pop()
                if x == k:
                    self.err(
                        "passthrough_cycle",
                        f"nodes.{k}",
                        "passthrough nodes loop without user input",
                    )
                    break
                if x not in seen:
                    seen.add(x)
                    stack.extend(t for t, _ in adj[x] if t in pt)
        self.policy(init, adj)

    @staticmethod
    def closure(start: set[str], adj: dict[str, list[tuple[str, Any]]], follow: Any) -> set[str]:
        seen = set(start)
        stack = list(start)
        while stack:
            for tgt, cond in adj.get(stack.pop(), []):
                if tgt not in seen and follow(cond):
                    seen.add(tgt)
                    stack.append(tgt)
        return seen

    # ---------------------------------------------------------------- policy
    def policy(self, init: str, adj: dict[str, list[tuple[str, Any]]]) -> None:
        pre = self.closure({init}, adj, lambda c: not self.is_gate(c))
        for k in sorted(pre):
            bad = self.tool_names(self.nodes[k]) & self.p.gated_tools
            if bad:
                self.err(
                    "gated_before_verify",
                    f"nodes.{k}",
                    f"{sorted(bad)} reachable before identity is verified",
                )
            self.guarded(f"nodes.{k}", self.exposure, self.nodes[k], f"nodes.{k}", True)
        for gk, g in self.globals.items():
            bad = self.tool_names(g) & self.p.gated_tools
            if not self.is_gate(g.get("condition")):
                excl = set(g.get("excluded_nodes") or [])
                if bad and pre - excl:
                    self.err(
                        "gated_global",
                        f"global_nodes.{gk}",
                        f"{sorted(bad)} reachable before verification",
                    )
                if pre - excl:
                    self.guarded(gk, self.exposure, g, f"global_nodes.{gk}", False)
        holders = [n for n in [*self.nodes.values(), *self.globals.values()] if isinstance(n, dict)]
        gated = set().union(*(self.tool_names(n) for n in holders)) & self.p.gated_tools
        if gated and not any(self.p.verify_tool in self.tool_names(n) for n in holders):
            self.err(
                "no_verify_node",
                "nodes",
                f"{sorted(gated)} present but no node holds {self.p.verify_tool}",
            )
        if self.p.promise_nodes:
            for k, n in self.nodes.items():
                if self.p.promise_tool in self.tool_names(n) and k not in self.p.promise_nodes:
                    self.err(
                        "promise_node",
                        f"nodes.{k}",
                        f"{self.p.promise_tool} outside the confirmation node",
                    )
        close = self.p.close_tool
        life = [
            t for t in self.wf.get("tools") or [] if (t.get("function") or {}).get("name") == close
        ]
        if len(life) != 1:
            self.err(
                "close_lifecycle", "tools", f"need exactly one lifecycle {close}, found {len(life)}"
            )
        elif "end_of_call" not in ((life[0].get("config") or {}).get("alwaysRunsAt") or []):
            self.err("close_lifecycle", "tools", f"{close} must run at end_of_call")
        for k, n in self.nodes.items():
            if close in self.tool_names(n):
                self.warn(
                    "close_in_node",
                    f"nodes.{k}",
                    f"{close} in a node as well as lifecycle: may run twice",
                )

    @staticmethod
    def texts(n: dict[str, Any]) -> list[tuple[str, str]]:
        """(field, text) of what a node puts in front of the model or says aloud."""
        out = []
        if isinstance(n.get("task_message"), str):
            out.append(("task_message", n["task_message"]))
        oe = n.get("on_enter")
        if isinstance(oe, dict):
            out += [(f"on_enter.{k}", v) for k, v in oe.items() if isinstance(v, str)]
        props = (((n.get("terminal") or {}).get("function") or {}).get("parameters") or {}).get(
            "properties"
        ) or {}
        msg = (props.get("spoken_message") or {}).get("default")
        if isinstance(msg, str):
            out.append(("terminal.spoken_message", msg))
        return out

    def exposure(self, n: dict[str, Any], where: str, certain: bool) -> None:
        """Account data in the text of a node reachable before identity is verified."""
        given = set(self.p.call_vars) | set(self.wf.get("test_variables") or {})
        workflow_set, _ = self.set_by_workflow()
        for fld, text in self.texts(n):
            for var in sorted(placeholders(text)):
                if var in PRE_VERIFY_OK or var.startswith("system.") or not DEBT_VAR.search(var):
                    continue
                msg = f"{fld} shows {{{{{var}}}}} before identity is verified"
                # an error only for a variable the workflow itself fills (code, mapping): a
                # call variable or a name nothing sets is not known to hold account data
                own = var in workflow_set or var.split(".")[0] in workflow_set
                if certain and own and var not in given:
                    self.err("data_before_verify", where, msg)
                else:
                    self.warn("data_before_verify", where, msg)

    def set_by_workflow(self) -> tuple[set[str], set[str]]:
        """(names the workflow writes: code, extraction, response_mapping; the platform's
        automatic result keys of its tools and nodes)."""
        defined = set(self.code_names)
        auto: set[str] = set()
        holders = [("nodes", k, n) for k, n in self.nodes.items()]
        holders += [("global_nodes", k, n) for k, n in self.globals.items()]
        tools = list(self.wf.get("tools") or [])
        for _, k, n in holders:
            if not isinstance(n, dict):
                continue
            auto.add(k)
            defined |= {x.get("name") for x in n.get("extraction_schema") or [] if x.get("name")}
            for m in n.get("response_mapping") or []:
                if isinstance(m, dict) and m.get("variable"):
                    defined.add(m["variable"])
            tools += (n.get("tools") or []) + (n.get("pre_actions") or [])
        for t in tools:
            if not isinstance(t, dict):
                continue
            auto.add(str((t.get("function") or {}).get("name") or ""))
            for m in t.get("response_mapping") or []:
                if isinstance(m, dict) and m.get("variable"):
                    defined.add(m["variable"])
        return defined, {f"{a}{s}" for a in auto for s in AUTO_SUFFIXES}

    def variables(self) -> None:
        """Conditions and texts that read a variable nothing defines (warnings: a call
        variable or a platform key the validator does not know may still be set)."""
        defined, auto_keys = self.set_by_workflow()
        defined |= set(self.p.call_vars) | set(self.wf.get("test_variables") or {})
        holders = [("nodes", k, n) for k, n in self.nodes.items()]
        holders += [("global_nodes", k, n) for k, n in self.globals.items()]

        def known(var: str) -> bool:
            base = var.split(".")[0]
            return (
                var in defined
                or base in defined
                or base in auto_keys
                or base in ("system", "call", "extractor")
            )

        def conds(c: Any, where: str) -> None:
            if not isinstance(c, dict):
                return
            for s in c.get("conditions") or []:
                conds(s, where)
            v = c.get("variable")
            if isinstance(v, str) and v and not known(v):
                self.warn("undefined_var", where, f"condition reads {v!r}, which nothing sets")

        for kind, k, n in holders:
            if not isinstance(n, dict):
                continue
            where = f"{kind}.{k}"
            conds(n.get("condition"), where + ".condition")
            for i, t in enumerate(n.get("transitions") or []):
                if isinstance(t, dict):
                    conds(t.get("condition"), f"{where}.transitions[{i}]")
            for fld, text in self.texts(n):
                for var in sorted(placeholders(text)):
                    if not known(var):
                        self.warn(
                            "undefined_var", where, f"{fld} shows {{{{{var}}}}}, which nothing sets"
                        )
        for var in sorted(placeholders(self.wf.get("system_prompt"))):
            if not known(var):
                self.warn("undefined_var", "system_prompt", f"{{{{{var}}}}} is never set")

    # ---------------------------------------------------------------- write provenance
    def tool_runs(self) -> list[tuple[dict[str, Any], bool, str]]:
        """(tool, called by the model, where) for every tool the workflow holds: node tools
        are called by the model; pre_actions and lifecycle tools run without it."""
        out: list[tuple[dict[str, Any], bool, str]] = []
        for kind, nodes in (("nodes", self.nodes), ("global_nodes", self.globals)):
            for k, n in nodes.items():
                if not isinstance(n, dict):
                    continue
                for i, t in enumerate(n.get("tools") or []):
                    out.append((t, True, f"{kind}.{k}.tools[{i}]"))
                for i, t in enumerate(n.get("pre_actions") or []):
                    out.append((t, False, f"{kind}.{k}.pre_actions[{i}]"))
        for i, t in enumerate(self.wf.get("tools") or []):
            out.append((t, False, f"tools[{i}]"))
        return [(t, m, w) for t, m, w in out if isinstance(t, dict)]

    @staticmethod
    def sources(t: dict[str, Any]) -> list[str]:
        fn = t.get("function") or {}
        props = (fn.get("parameters") or {}).get("properties") or {}
        code = (props.get("code") or {}).get("default") if isinstance(props, dict) else None
        srcs = [t.get("pre_process_code"), t.get("post_process_code"), code]
        return [s for s in srcs if isinstance(s, str) and s.strip()]

    def provenance(self) -> None:
        """Where each value a write tool sends comes from. A body leaf `{{v}}` where v is set
        only by an extraction (what a model heard, never checked by code) is an error; a
        model-called tool's argument no code rewrites, and a required field nothing fills on
        an engine-run tool, are warnings. String literals the code puts in final_outcome /
        final_reason / transfer_queue must be values of the field's OpenAPI enum (warning)."""
        runs = self.tool_runs()
        llm: set[str] = set()
        for nodes in (self.nodes, self.globals):
            for n in nodes.values():
                if isinstance(n, dict):
                    llm |= {
                        str(x.get("name"))
                        for x in n.get("extraction_schema") or []
                        if isinstance(x, dict) and x.get("name")
                    }
        coded = set(self.p.call_vars) | set(self.wf.get("test_variables") or {})
        lits: list[tuple[str, str]] = []
        for t, _, _ in runs:
            for src in self.sources(t):
                keys, pairs = code_writes(src)
                coded |= keys
                lits += pairs
            coded |= {
                str(m["variable"])
                for m in t.get("response_mapping") or []
                if isinstance(m, dict) and m.get("variable")
            }
        for nodes in (self.nodes, self.globals):
            for n in nodes.values():
                if isinstance(n, dict):
                    coded |= {
                        str(m["variable"])
                        for m in n.get("response_mapping") or []
                        if isinstance(m, dict) and m.get("variable")
                    }
        _, auto = self.set_by_workflow()
        platform = auto | {"system", "call", "extractor"}

        def from_code(var: str) -> bool:
            return var in coded or var.split(".")[0] in coded or var.split(".")[0] in platform

        enums: dict[str, set[str]] = {}
        for t, model_called, where in runs:
            fn = t.get("function") or {}
            name = fn.get("name")
            if name not in WRITE_TOOLS:
                continue
            body = ((fn.get("parameters") or {}).get("properties") or {}).get("body") or {}
            leaves = body.get("properties") if isinstance(body, dict) else None
            if not isinstance(leaves, dict):
                continue
            pre = t.get("pre_process_code")
            hooked_keys = code_writes(pre)[0] if isinstance(pre, str) and pre.strip() else set()
            api = self.api.get(str(name))
            for f, leaf in leaves.items():
                if not isinstance(leaf, dict):
                    continue
                fd = api.field(f) if api else None
                vs = sorted(placeholders(leaf.get("default")))
                if fd and fd.enum:
                    for v in vs:
                        if v in ENUM_VARS:
                            enums.setdefault(v, set()).update(str(e) for e in fd.enum)
                hooked = bool(leaf.get("enum")) or f in hooked_keys
                for v in vs:
                    if from_code(v):
                        continue
                    at = f"{where}.{name}.{f}"
                    if v in llm and not hooked:
                        self.err(
                            "llm_write_arg",
                            at,
                            f"{{{{{v}}}}} is set only by an extraction (what the model "
                            "heard); a write tool must send a value code has checked",
                        )
                    elif model_called and not hooked and v not in llm:
                        self.warn(
                            "model_write_arg",
                            at,
                            f"{{{{{v}}}}} is filled by the agent model and no pre_process "
                            f"hook rewrites {f!r}",
                        )
                    elif not model_called and v not in llm and fd is not None and fd.required:
                        self.warn(
                            "unfilled_required",
                            at,
                            f"required field: {{{{{v}}}}} is set by nothing in the workflow",
                        )
        for var, allowed in sorted(enums.items()):
            bad = sorted({lit for k, lit in lits if k == var and lit and lit not in allowed})
            for lit in bad:
                self.warn(
                    "enum_literal",
                    "code",
                    f"{var} = {lit!r} in code is not a value of the OpenAPI enum {sorted(allowed)}",
                )

    def run(self) -> Report:
        self.nulls(self.wf, "")
        self.top()
        for k, n in self.nodes.items():
            if isinstance(n, dict):
                self.node(k, n)
            else:
                self.err("node_shape", f"nodes.{k}", "node must be an object")
        for k, n in self.globals.items():
            if isinstance(n, dict):
                self.node(k, n, is_global=True)
        self.graph()
        self.guarded("workflow", self.variables)
        self.guarded("workflow", self.provenance)
        return self.r


def validate(
    workflow: dict[str, Any], tools: dict[str, ToolDef], policy: Policy | None = None
) -> Report:
    wf = workflow.get("workflow", workflow) if "nodes" not in workflow else workflow
    return _V(wf, tools, policy or Policy()).run()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    wf = json.loads(Path(argv[1]).read_text())
    tools = parse_openapi(json.loads(Path(argv[2]).read_text()))
    rep = validate(wf, tools)
    for i in rep.issues:
        print(i)
    print(rep.summary())
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
