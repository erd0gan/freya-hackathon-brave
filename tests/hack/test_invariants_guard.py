"""Invariant guards: validator checks found missing by the mutation audit (code size, turn cap
to itself, undefined variables, forbidden code, account data before the identity check, no
verify node), the build trace (warnings, hashes, spec summary, export re-check, failed model
calls) and the deterministic post-call reviewer. Every check is also run over every workflow
the builder makes from the specs in this repo, which must stay free of new errors."""

import base64
import copy
import json
import time
import zlib
from pathlib import Path

import pytest
from fb.generate import build
from fb.harness.bank import Bank
from fb.harness.engine_sim import simulate
from fb.harness.review import (
    critical,
    enums_from_tools,
    events_from_messages,
    review_call,
    review_sim,
)
from fb.harness.scoring import case_report, format_report, golden_calls, replay
from fb.openapi_tools import parse_openapi
from fb.spec_extract import to_generator
from fb.validate import CODE_MAX, Policy, validate
from spec_fixtures import SPEC_A, SPEC_B
from test_engine import CASES, CFG, CFG_A, SCENARIOS
from test_validate import TOOLS, code_node, good

from conftest import KIT, ROOT

HERE = Path(__file__).parent
OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text())
NW_OPENAPI = json.loads((ROOT / "tests" / "metamorphic" / "northwind" / "openapi.json").read_text())
NEW_CODES = {
    "code_size",
    "turn_cap_self",
    "undefined_var",
    "code_forbidden",
    "code_dynamic",
    "data_before_verify",
    "no_verify_node",
    "validator_internal",
    "llm_write_arg",
    "model_write_arg",
    "unfilled_required",
    "enum_literal",
}


def generated():
    """(name, workflow, policy, tools) for every spec the repo has."""
    oa = parse_openapi(OPENAPI)
    out = [
        ("SPEC_A", *build(copy.deepcopy(SPEC_A), oa), oa),
        ("SPEC_B", *build(copy.deepcopy(SPEC_B), oa), oa),
    ]
    ab = json.loads((HERE / "fixtures" / "spec_ab_extracted.json").read_text())
    out.append(("AB_extracted", *build(to_generator(ab), oa), oa))
    nw = json.loads(
        (HERE / "fixtures" / "spec_northwind_extracted.json").read_text(encoding="utf-8")
    )
    nwt = parse_openapi(NW_OPENAPI)
    out.append(("northwind", *build(to_generator(nw), nwt), nwt))
    return out


GENERATED = generated()


def codes(rep, level="error"):
    return {i.code for i in rep.issues if i.level == level}


# ---------------------------------------------------------------- no regressions
@pytest.mark.parametrize("name,wf,policy,tools", GENERATED, ids=[g[0] for g in GENERATED])
def test_generated_workflows_have_no_new_errors_or_warnings(name, wf, policy, tools):
    rep = validate(wf, tools, policy)
    assert rep.ok, [str(i) for i in rep.errors]
    new = [str(i) for i in rep.issues if i.code in NEW_CODES]
    assert new == []


@pytest.mark.parametrize("name,wf,policy,tools", GENERATED, ids=[g[0] for g in GENERATED])
def test_generated_workflows_pass_export_mode(name, wf, policy, tools):
    """What build.py's export re-check sees when the platform hands the workflow back
    with agent_config and nulls on unset node fields."""
    exp = copy.deepcopy(wf)
    exp["agent_config"] = {"llmConfig": {"apiKey": None, "model": "x"}, "syncSource": None}
    for n in exp["nodes"].values():
        n.setdefault("max_node_turns", None)
        n.setdefault("max_turn_transition", None)
    pol = copy.copy(policy)
    pol.exported = True
    rep = validate({"updated_at": None, "workflow": exp}, tools, pol)
    assert rep.ok, [str(i) for i in rep.errors]


def test_generated_code_is_scanned_including_the_packed_part():
    _, wf, policy, tools = GENERATED[0]
    src = wf["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]
    assert "zlib.decompress" in src["default"]  # decide is packed; its library is still read
    bad = copy.deepcopy(wf)
    leaf = bad["nodes"]["decide"]["pre_actions"][0]["function"]["parameters"]["properties"]
    leaf["code"]["default"] = repack(leaf["code"]["default"], "import socket\n")
    rep = validate(bad, tools, policy)
    assert "code_forbidden" in codes(rep)


def repack(src, extra):
    """The packed blob of `src` with `extra` code prepended inside it."""
    i = src.index("b64decode('") + len("b64decode('")
    j = src.index("'", i)
    inner = zlib.decompress(base64.b64decode(src[i:j])).decode()
    blob = base64.b64encode(zlib.compress((extra + inner).encode(), 9)).decode()
    return src[:i] + blob + src[j:]


# ---------------------------------------------------------------- 1. code size
def with_code(src, node="setup"):
    wf = good()
    wf["nodes"][node] = code_node(node, "identity", src)
    return wf


def body_of(size):
    pad = "# " + "x" * 96 + "\n"
    head = "try:\n    vars['x'] = 'y'\nexcept Exception:\n    pass\n"
    return head + pad * ((size - len(head)) // len(pad) + 1)


def test_code_over_the_data_limit_is_an_error():
    rep = validate(with_code(body_of(CODE_MAX + 100)), TOOLS)
    assert "code_size" in codes(rep)


def test_code_over_the_hard_cap_is_a_warning():
    rep = validate(with_code(body_of(37_000)), TOOLS)
    assert "code_size" in codes(rep, "warn") and "code_size" not in codes(rep)


def test_hook_code_size_is_checked():
    wf = good()
    t = wf["nodes"]["identity"]["tools"][0]
    t["pre_process_code"] = body_of(CODE_MAX + 100)
    assert "code_size" in codes(validate(wf, TOOLS))


# ---------------------------------------------------------------- 2. turn cap to itself
def test_turn_cap_to_itself_is_an_error():
    wf = good()
    wf["nodes"]["talk"]["max_turn_transition"] = "talk"
    assert "turn_cap_self" in codes(validate(wf, TOOLS))


# ---------------------------------------------------------------- 3. undefined variables
def cond(var, op="equals", operand="yes"):
    c = {"variable": var, "operator": op}
    if op != "is_set":
        c["operand"] = operand
    return {"logic": "or", "conditions": [c]}


def undefined(rep):
    return [i.msg for i in rep.warnings if i.code == "undefined_var"]


def test_condition_on_a_variable_nothing_sets_is_a_warning():
    wf = good()
    wf["nodes"]["talk"]["transitions"].insert(
        0, {"target_node": "end_done", "description": "x", "condition": cond("ghost_flag")}
    )
    rep = validate(wf, TOOLS)
    assert rep.ok and any("ghost_flag" in m for m in undefined(rep))


@pytest.mark.parametrize(
    "define",
    [
        lambda wf: wf["nodes"]["setup"].update(
            code_node(
                "setup", "identity", "try:\n    vars['flag2'] = 'a'\nexcept Exception:\n    pass\n"
            )
        ),
        lambda wf: wf["nodes"]["setup"].update(
            code_node(
                "setup", "identity", 'try:\n    vars["flag2"] = "a"\nexcept Exception:\n    pass\n'
            )
        ),
        lambda wf: wf["nodes"]["talk"].update(
            extraction_schema=[{"name": "flag2", "type": "string", "min_confidence": 0.5}]
        ),
        lambda wf: wf["nodes"]["account"]["response_mapping"].append(
            {"path": "data.ok", "variable": "flag2"}
        ),
        lambda wf: wf.update(test_variables={"flag2": "x"}),
    ],
    ids=["code_single", "code_double", "extraction", "mapping", "test_variables"],
)
def test_defined_variables_raise_no_warning(define):
    wf = good()
    define(wf)
    wf["nodes"]["talk"]["transitions"].insert(
        0, {"target_node": "end_done", "description": "x", "condition": cond("flag2")}
    )
    assert not any("flag2" in m for m in undefined(validate(wf, TOOLS)))


def test_tool_response_keys_and_system_vars_are_known():
    wf = good()
    wf["nodes"]["talk"]["transitions"].insert(
        0,
        {
            "target_node": "end_done",
            "description": "x",
            "condition": cond("get_account_response.data.ok", operand="true"),
        },
    )
    wf["nodes"]["talk"]["task_message"] += " Today: {{system.todays_date_en_est}}."
    assert (
        undefined(validate(wf, TOOLS))
        == ["condition reads 'identity_state', which nothing sets"] * 2
    )


def test_placeholder_in_a_prompt_nothing_sets_is_a_warning():
    wf = good()
    wf["nodes"]["talk"]["task_message"] += " Say {{nowhere_var}}."
    rep = validate(wf, TOOLS)
    assert rep.ok and any("nowhere_var" in m for m in undefined(rep))


# ---------------------------------------------------------------- 4. forbidden code
@pytest.mark.parametrize(
    "line",
    [
        "import socket",
        "import urllib.request",
        "from http import client",
        "import requests",
        "import subprocess",
        "from subprocess import run",
        "import os\n    os.system('ls')",
        "__import__('socket')",
        "eval(vars.get('expr'))",
        "exec(vars['code'])",
    ],
)
def test_network_process_and_dynamic_code_are_errors(line):
    # source text handed to the validator only; never executed
    src = f"try:\n    {line}\nexcept Exception:\n    pass\n"
    assert "code_forbidden" in codes(validate(with_code(src), TOOLS))


def test_forbidden_import_in_a_hook_is_an_error():
    wf = good()
    t = wf["nodes"]["identity"]["tools"][0]
    t["pre_process_code"] = "try:\n    import requests\nexcept Exception:\n    pass\n"
    assert "code_forbidden" in codes(validate(wf, TOOLS))


def test_standard_modules_are_allowed():
    src = (
        "import json\nimport re\nimport datetime\nimport base64\nimport zlib\n"
        "try:\n    vars['d'] = datetime.date.today().isoformat()\nexcept Exception:\n    pass\n"
    )
    rep = validate(with_code(src), TOOLS)
    assert not {"code_forbidden", "code_dynamic"} & (codes(rep) | codes(rep, "warn"))


def test_packed_library_is_unpacked_and_scanned():
    def packed(lib):
        blob = base64.b64encode(zlib.compress(lib.encode(), 9)).decode()
        return (
            "import base64\nimport zlib\n"
            f"exec(zlib.decompress(base64.b64decode('{blob}')).decode('utf-8'), globals())\n"
            "try:\n    vars['x'] = f()\nexcept Exception:\n    pass\n"
        )

    clean = validate(with_code(packed("def f():\n    return 1\n")), TOOLS)
    assert not {"code_forbidden", "code_dynamic"} & (codes(clean) | codes(clean, "warn"))
    dirty = validate(with_code(packed("import socket\ndef f():\n    return 1\n")), TOOLS)
    assert "code_forbidden" in codes(dirty)


# ---------------------------------------------------------------- 5. data before verify
@pytest.mark.parametrize(
    "var", ["p1_past_due", "neg_say", "product_brief", "amount_due", "p1_deadline", "final_amount"]
)
def test_account_data_before_verification_is_an_error(var):
    wf = good()
    src = "try:\n    vars['" + var + "'] = '1'\nexcept Exception:\n    pass\n"
    wf["nodes"]["setup"] = code_node("setup", "identity", src)
    wf["nodes"]["identity"]["task_message"] += " Mention {{" + var + "}}."
    assert "data_before_verify" in codes(validate(wf, TOOLS))


def test_account_data_name_nothing_sets_is_only_a_warning():
    wf = good()
    wf["nodes"]["identity"]["task_message"] += " Mention {{amount_due}}."
    rep = validate(wf, TOOLS)
    assert rep.ok and "data_before_verify" in codes(rep, "warn")


@pytest.mark.parametrize(
    "var", ["final_say", "cb_say", "title", "last_name", "callback_window_text", "full_name"]
)
def test_lines_and_names_before_verification_are_allowed(var):
    wf = good()
    wf["nodes"]["identity"]["task_message"] += " Say {{" + var + "}}."
    rep = validate(wf, TOOLS)
    assert "data_before_verify" not in codes(rep) | codes(rep, "warn")


def test_debt_named_call_variable_before_verification_is_only_a_warning():
    wf = good()
    wf["nodes"]["identity"]["task_message"] += " Mention {{amount_band}}."
    rep = validate(wf, TOOLS, Policy(call_vars={"customer_id", "amount_band"}))
    assert rep.ok and "data_before_verify" in codes(rep, "warn")


def test_account_data_after_verification_is_fine():
    wf = good()
    wf["nodes"]["talk"]["task_message"] += " Products: {{product_brief}}."
    rep = validate(wf, TOOLS)
    assert "data_before_verify" not in codes(rep) | codes(rep, "warn")


def test_generated_opening_with_account_data_is_caught():
    _, wf, policy, tools = GENERATED[0]
    bad = copy.deepcopy(wf)
    bad["nodes"]["opening"]["task_message"] += " {{neg_say}}"
    assert "data_before_verify" in codes(validate(bad, tools, policy))


# ---------------------------------------------------------------- 6. verify node
def test_gated_tools_without_a_verify_node_is_an_error():
    wf = good()
    wf["nodes"]["identity"]["tools"] = []
    assert "no_verify_node" in codes(validate(wf, TOOLS))
    assert "no_verify_node" not in codes(validate(good(), TOOLS))


# ---------------------------------------------------------------- 7. build trace
def load_build():
    import importlib.util

    spec = importlib.util.spec_from_file_location("builder_main", ROOT / "builder" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SPEC_AB = json.loads((HERE / "fixtures" / "spec_ab_extracted.json").read_text())


def run_build(monkeypatch, tmp_path, export_edit=None, export_raises=False):
    from fb.trace import Trace

    b = load_build()
    monkeypatch.setattr(b, "ROOT", tmp_path)
    monkeypatch.setattr(b, "extract", lambda *a, **k: copy.deepcopy(SPEC_AB))
    monkeypatch.setattr(b.freya, "validate_workflow", lambda *a, **k: {})
    sent = []

    def save(slot, wf, **k):
        sent.append(wf)
        return {"workflow_id": "w1", "agent_id": "a1"}

    def export(wid, path):
        if export_raises:
            raise RuntimeError("boom")
        wf = copy.deepcopy(sent[-1])
        wf["agent_config"] = {"llmConfig": {"apiKey": None}}
        if export_edit:
            export_edit(wf)
        Path(path).write_text(json.dumps({"updated_at": "t", "workflow": wf}))

    monkeypatch.setattr(b.freya, "save_workflow", save)
    monkeypatch.setattr(b.freya, "api", lambda *a, **k: {"response": "Hello."})
    monkeypatch.setattr(b.freya, "export_workflow", export)
    log = tmp_path / "t.log"
    rc = b.run(
        Trace(log, echo=False),
        KIT / "briefs" / "brief-a.md",
        KIT / "tools" / "openapi.json",
        "x",
        time.monotonic() + 600,
    )
    return rc, log.read_text(), tmp_path / "submission" / "x.workflow.json"


def test_build_logs_every_validator_warning_and_a_spec_summary(monkeypatch, tmp_path):
    rc, log, _ = run_build(monkeypatch, tmp_path)
    assert rc == 0
    assert "validator: WARN  code_try" in log
    assert "spec summary: identity factors=['date_of_birth', 'phone_last4']; bands=2" in log
    assert "holidays=5" in log and "spec warnings=" in log
    assert "close outcomes=['no_promise', 'paid', 'promise', 'transferred']" in log


def test_build_rechecks_the_export_and_keeps_it(monkeypatch, tmp_path):
    rc, log, out = run_build(monkeypatch, tmp_path)
    assert rc == 0 and "export check: the exported workflow validates" in log
    assert "export_check=ok" in log.splitlines()[-1]

    def break_it(wf):
        wf["nodes"]["opening"]["max_turn_transition"] = "nowhere"

    rc, log, out = run_build(monkeypatch, tmp_path, break_it)
    assert rc == 0 and "EXPORT CHECK FAILED" in log
    assert "export_check=1_errors" in log.splitlines()[-1]
    assert json.loads(out.read_text())["updated_at"] == "t"  # the export is kept


def test_failed_export_skips_the_check(monkeypatch, tmp_path):
    rc, log, _ = run_build(monkeypatch, tmp_path, export_raises=True)
    assert rc == 0 and "export_check=skipped" in log.splitlines()[-1]


def test_build_start_line_has_file_hashes(monkeypatch, tmp_path):
    import hashlib

    b = load_build()
    monkeypatch.setattr(b, "ROOT", tmp_path)
    monkeypatch.setattr(b, "run", lambda *a, **k: 0)
    brief = KIT / "briefs" / "brief-a.md"
    api = KIT / "tools" / "openapi.json"
    assert b.main(["build.py", str(brief), str(api)]) == 0
    start = (tmp_path / "traces" / "brief-a.log").read_text().splitlines()[0]
    assert "brief_sha256=" + hashlib.sha256(brief.read_bytes()).hexdigest() in start
    assert "tool_api_sha256=" + hashlib.sha256(api.read_bytes()).hexdigest() in start
    assert "commit=" in start


def test_failed_model_calls_are_traced(monkeypatch, tmp_path):
    import urllib.error

    from fb import llm
    from fb.trace import Trace

    def fail(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-secret-123456")
    monkeypatch.setattr(llm.urllib.request, "urlopen", fail)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    t = Trace(tmp_path / "t.log", echo=False)
    with pytest.raises(llm.LLMError):
        llm.chat(t, "extract the spec", [{"role": "user", "content": "x"}], model="m/x", retries=1)
    t.end(status="x")
    log = (tmp_path / "t.log").read_text()
    lines = [x for x in log.splitlines() if "MODEL" in x and "FAILED" in x]
    assert len(lines) == 2 and "m/x" in lines[0] and "extract the spec (attempt 1)" in lines[0]
    assert "connection refused" in lines[0] and "sk-test" not in log
    assert "model_failures=2" in log.splitlines()[-1]


# ---------------------------------------------------------------- 8. reviewer
ENUMS = enums_from_tools(parse_openapi(OPENAPI))


def test_enums_come_from_the_tool_api():
    # the kit's close_call has an outcome enum; its reason is free text (the brief's table)
    assert "promise" in ENUMS["outcomes"] and "reasons" not in ENUMS


def test_reason_outside_the_brief_list_is_caught():
    cfg = {**ENUMS, "reasons": sorted(CFG["neg"]["reasons"].values())}
    found = ledger("PUB-05", lambda c: c[-1][1].__setitem__("reason", "bored"))
    assert "R3" not in rules(found)  # no reasons list given to replay: not checked
    b = Bank()
    v = b.open_session(CASES["PUB-05"])
    calls = golden_calls(CASES["PUB-05"], v["customer_id"])
    calls[-1][1]["reason"] = "bored"
    r = replay(Bank(), CASES["PUB-05"], calls, review_cfg=cfg)
    assert "R3" in rules(r["review"])


@pytest.mark.parametrize("cid", sorted(CASES))
def test_golden_replay_of_every_public_case_has_no_critical_finding(cid):
    r = replay(Bank(), CASES[cid], review_cfg=ENUMS)
    assert [f for f in r["review"] if f["severity"] == "critical"] == []


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_engine_sim_of_every_public_scenario_has_no_critical_finding(name):
    case = CASES[name[:6]]
    r = simulate(case, SCENARIOS[name], CFG if case["brief"] == "B" else CFG_A)
    assert critical(review_sim(r, ENUMS)) == []


def ledger(cid, edit):
    b = Bank()
    v = b.open_session(CASES[cid])
    calls = golden_calls(CASES[cid], v["customer_id"])
    edit(calls)
    return replay(Bank(), CASES[cid], calls, review_cfg=ENUMS)["review"]


def rules(found):
    return {f["rule"] if isinstance(f, dict) else f.rule for f in found}


def test_reviewer_catches_injected_bad_ledgers():
    assert "R1" in rules(ledger("PUB-01", lambda c: c.insert(0, ("get_account", {}))))
    dup = ledger("PUB-01", lambda c: c.insert(4, c[3]))
    assert "R2" in rules(dup)
    assert "R3" in rules(ledger("PUB-01", lambda c: c.pop()))
    assert "R3" in rules(
        ledger("PUB-01", lambda c: c[-1][1].__setitem__("outcome", "made_up_outcome"))
    )


def test_retry_after_a_failed_promise_is_not_a_duplicate():
    calls = [
        {
            "tool": "verify_identity",
            "args": {"field": "a"},
            "status": 200,
            "result": {"ok": True, "verified": True, "all_verified": True},
        },
        {
            "tool": "create_promise",
            "args": {"product_id": "p1", "amount": 1, "date": "2026-10-14"},
            "status": 500,
            "result": {"ok": False, "error": "service unavailable"},
        },
        {
            "tool": "create_promise",
            "args": {"product_id": "p1", "amount": 1, "date": "2026-10-14"},
            "status": 200,
            "result": {"ok": True},
        },
        {
            "tool": "close_call",
            "args": {"outcome": "promise"},
            "status": 200,
            "result": {"ok": True},
        },
    ]
    assert review_call([], calls, ENUMS) == []
    calls.insert(3, dict(calls[2]))
    assert rules(review_call([], calls, ENUMS)) == {"R2"}


def test_reviewer_checks_promise_limits():
    r = simulate(CASES["PUB-01"], SCENARIOS["PUB-01"], CFG_A)
    led = copy.deepcopy(r)
    led.ledger["calls"][3]["args"]["amount"] = 1.0
    assert "R5" in rules(review_sim(led, ENUMS))
    late = copy.deepcopy(r)
    late.ledger["calls"][3]["args"]["date"] = "2027-06-01"
    assert any("after deadline" in f.evidence for f in review_sim(late, ENUMS))


def msgs_ok():
    """A promise call in message form, with read-back and the customer's yes."""
    return [
        {"role": "assistant", "content": "Hello, this is Freya Bank. Am I speaking with Ms Rossi?"},
        {"role": "user", "content": "Yes."},
        {
            "role": "tool",
            "tool": "verify_identity",
            "args": {"field": "date_of_birth"},
            "status": 200,
            "result": {"ok": True, "verified": True, "all_verified": True},
        },
        {"role": "assistant", "content": "I am calling about a late payment on your loan."},
        {"role": "user", "content": "I can pay 1,200 on Friday."},
        {"role": "assistant", "content": "To confirm: 1,200.00 on Friday 16 October. Correct?"},
        {"role": "user", "content": "Yes."},
        {
            "role": "tool",
            "tool": "create_promise",
            "args": {"product_id": "p1", "amount": 1200.0, "date": "2026-10-16"},
            "status": 200,
            "result": {"ok": True},
        },
        {"role": "assistant", "content": "Your promise is recorded. Goodbye."},
        {
            "role": "tool",
            "tool": "close_call",
            "args": {"outcome": "promise"},
            "status": 200,
            "result": {"ok": True},
        },
    ]


def test_reviewer_messages_clean_call():
    assert review_call(msgs_ok(), None, ENUMS) == []


def test_reviewer_catches_promise_without_read_back_and_answer():
    m = msgs_ok()
    del m[6]  # no customer turn after the read-back
    assert "R4" in rules(review_call(m, None, ENUMS))
    m = msgs_ok()
    m[5]["content"] = "Shall I record it?"
    assert "R4" in rules(review_call(m, None, ENUMS))


def test_reviewer_catches_claim_before_the_tool_and_words_before_verify():
    m = msgs_ok()
    m.insert(6, {"role": "assistant", "content": "Your promise has been recorded."})
    found = review_call(m, None, ENUMS)
    assert any(f.rule == "R6" and f.severity == "warning" for f in found)
    m = msgs_ok()
    m[0]["content"] = "Hello, this is the collections team of Freya Bank."
    assert "R7" in rules(review_call(m, None, {**ENUMS, "bank_name": "Freya Bank"}))


def test_reviewer_reads_freya_get_call_messages():
    def ev(**k):
        return {"role": "system", "message": json.dumps(k)}

    body = {"customer_id": "c1", "product_id": "p1", "amount": 420, "date": "2026-10-14"}
    msgs = [
        {"role": "assistant", "message": "Hello, am I speaking with Mr Reyes?"},
        {"role": "user", "message": "Yes."},
        ev(
            event="function_call_start",
            function="verify_identity",
            args={"url": "u", "method": "POST", "body": {"field": "date_of_birth"}},
        ),
        ev(
            event="function_call_complete",
            function="verify_identity",
            result={
                "status": "success",
                "status_code": 200,
                "data": {"ok": True, "verified": True, "all_verified": True},
            },
        ),
        {"role": "assistant", "message": "To confirm: 420.00 today. Do you confirm?"},
        {"role": "user", "message": "Yes."},
        ev(
            event="function_call_start",
            function="create_promise",
            args={"url": "u", "method": "POST", "body": body},
        ),
        ev(
            event="function_call_complete",
            function="create_promise",
            result={"status": "success", "status_code": 200, "data": {"ok": True}},
        ),
        ev(
            event="function_call_start",
            function="close_call",
            args={"body": {"outcome": "promise"}},
        ),
        ev(
            event="function_call_complete",
            function="close_call",
            result={"status": "success", "status_code": 200, "data": {"ok": True}},
        ),
    ]
    events = events_from_messages(msgs)
    tools = [x["tool"] for k, x in events if k == "tool"]
    assert tools == ["verify_identity", "create_promise", "close_call"]
    assert events[5][1]["args"]["amount"] == 420 and events[5][1]["status"] == 200
    assert review_call(msgs, None, ENUMS) == []
    msgs[4]["message"] = "Shall I record that?"
    assert "R4" in rules(review_call(msgs, None, ENUMS))


def test_case_report_counts_critical_findings():
    found = {"A": [], "B": [{"rule": "R1", "severity": "critical", "turn": 0, "evidence": "x"}]}
    rep = case_report({"A": [True], "B": [True]}, found)
    assert rep["passed"] == 1 and rep["rows"][1]["critical"] == 1
    assert "1 critical review findings" in format_report(rep)
