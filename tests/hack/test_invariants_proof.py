"""Write-value provenance and enum literals (fb.validate), the brief's proof map (fb.proof),
the extraction observer (fb.spec_extract.extract_observer / observe) and build.py's proof
lines and run manifest. No network, no model: chat and extract are stand-ins."""

from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from pathlib import Path

import pytest
from fb import spec_extract
from fb.generate import build
from fb.openapi_tools import parse_openapi
from fb.proof import _quotes, proof_map, rule_units, summary, trace_lines
from fb.spec_extract import (
    extract_observer,
    observe,
    policy_fields,
    to_generator,
)
from fb.trace import Trace
from fb.validate import code_writes, validate
from test_invariants_guard import GENERATED, OPENAPI, load_build, repack

from conftest import KIT, ROOT

HERE = Path(__file__).parent
PROV = {"llm_write_arg", "model_write_arg", "unfilled_required", "enum_literal"}
SPEC_AB = json.loads((HERE / "fixtures" / "spec_ab_extracted.json").read_text())
SPEC_NW = json.loads(
    (HERE / "fixtures" / "spec_northwind_extracted.json").read_text(encoding="utf-8")
)
NW_DIR = ROOT / "tests" / "metamorphic" / "northwind"
NW_TOOLS = parse_openapi(json.loads((NW_DIR / "openapi.json").read_text()))
BRIEF_A = (KIT / "briefs" / "brief-a.md").read_text(encoding="utf-8")
BRIEF_B = (KIT / "briefs" / "brief-b.md").read_text(encoding="utf-8")
BRIEF_NW = (NW_DIR / "brief.md").read_text(encoding="utf-8")
WF = {g[0]: g for g in GENERATED}


def issues(rep, *codes):
    return [i for i in rep.issues if i.code in codes]


def body(t):
    return t["function"]["parameters"]["properties"]["body"]["properties"]


def code_of(wf, node):
    return wf["nodes"][node]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]


# ---------------------------------------------------------------- 1. provenance
@pytest.mark.parametrize("name", list(WF))
def test_generated_workflows_have_no_provenance_issues(name):
    _, wf, policy, tools = WF[name]
    rep = validate(wf, tools, policy)
    assert rep.ok
    assert [str(i) for i in issues(rep, *PROV)] == []


def test_an_amount_sent_straight_from_extraction_is_an_error():
    _, wf, policy, tools = copy.deepcopy(WF["SPEC_A"])
    body(wf["nodes"]["record"]["pre_actions"][0])["amount"]["default"] = "{{said_amount_text}}"
    rep = validate(wf, tools, policy)
    errs = issues(rep, "llm_write_arg")
    assert len(errs) == 1 and errs[0].level == "error"
    assert "record" in errs[0].where and "said_amount_text" in errs[0].msg


def test_an_extracted_name_that_code_also_sets_is_not_an_error():
    _, wf, policy, tools = copy.deepcopy(WF["SPEC_A"])
    # rec_amount is set by the packed library: adding it to an extraction changes nothing
    wf["nodes"]["negotiate"]["extraction_schema"].append(
        {"name": "rec_amount", "type": "string", "description": "x", "min_confidence": 0.5}
    )
    assert issues(validate(wf, tools, policy), *PROV) == []


def test_verify_answer_without_its_rewriting_hook_is_a_warning():
    _, wf, policy, tools = copy.deepcopy(WF["SPEC_A"])
    wf["nodes"]["identity"]["tools"][0].pop("pre_process_code")
    rep = validate(wf, tools, policy)
    found = issues(rep, "model_write_arg")
    assert found and all(i.level == "warn" for i in found)
    assert any("identity.tools[0].verify_identity.answer" in i.where for i in found)
    # the field leaf has an enum: the model can only pick a listed value
    assert not any(i.where.endswith(".field") for i in found)
    assert rep.ok


def test_a_required_field_nothing_fills_is_a_warning_not_an_error():
    _, wf, policy, tools = copy.deepcopy(WF["SPEC_A"])
    body(wf["nodes"]["cb_book"]["pre_actions"][0])["date"]["default"] = "{{nobody_sets_this}}"
    rep = validate(wf, tools, policy)
    found = issues(rep, "unfilled_required")
    assert len(found) == 1 and found[0].level == "warn"
    assert "schedule_callback.date" in found[0].where and rep.ok


def test_an_optional_field_nothing_fills_is_not_reported():
    _, wf, policy, tools = copy.deepcopy(WF["SPEC_A"])
    leaf = body(wf["tools"][0])
    leaf["reason"]["default"] = "{{nobody_sets_this}}"  # close_call reason: optional
    assert issues(validate(wf, tools, policy), "unfilled_required") == []


def test_close_outcome_literal_outside_the_enum_is_a_warning():
    _, wf, policy, tools = copy.deepcopy(WF["SPEC_A"])
    leaf = code_of(wf, "fin_unclear")
    leaf["default"] = leaf["default"].replace("'not_reached'", "'not_reachd'", 1)
    rep = validate(wf, tools, policy)
    found = issues(rep, "enum_literal")
    assert len(found) == 1 and found[0].level == "warn" and "'not_reachd'" in found[0].msg
    assert rep.ok


def test_enum_literal_in_the_packed_library_is_found():
    _, wf, policy, tools = copy.deepcopy(WF["SPEC_A"])
    leaf = code_of(wf, "decide")
    leaf["default"] = repack(leaf["default"], "_v = {}\n_v['transfer_queue'] = 'billing'\n")
    found = issues(validate(wf, tools, policy), "enum_literal")
    assert len(found) == 1 and "'billing'" in found[0].msg and "general" in found[0].msg


def test_code_writes_reads_subscripts_names_tuples_and_updates():
    src = (
        "F = 'outcome'\n"
        "try:\n"
        "    b = {}\n"
        "    b[F], b['reason'] = 'paid', 'x'\n"
        "    vars['a'] = 1\n"
        "    vars.update({'u1': 1}, u2=2)\n"
        "    vars.setdefault('s1', 0)\n"
        "    vars[key] = 3\n"
        "except Exception:\n"
        "    pass\n"
    )
    keys, lits = code_writes(src)
    assert {"outcome", "reason", "a", "u1", "u2", "s1"} <= keys
    assert ("outcome", "paid") in lits and ("reason", "x") in lits
    assert code_writes("this is not python (") == (frozenset(), ())


def test_provenance_never_crashes_on_strange_shapes():
    _, wf, policy, tools = copy.deepcopy(WF["SPEC_A"])
    wf["nodes"]["identity"]["tools"].append("junk")
    wf["tools"][0]["pre_process_code"] = 12
    body(wf["nodes"]["record"]["pre_actions"][0])["amount"]["default"] = ["{{x}}"]
    wf["nodes"]["record"]["response_mapping"].append("junk")
    rep = validate(wf, tools, policy)
    assert [i for i in rep.issues if "provenance" in i.msg] == []


# ---------------------------------------------------------------- 2. proof map
def counts(brief, spec, gen, wf):
    return summary(proof_map(brief, spec, gen, wf))


def test_proof_map_brief_a():
    units = proof_map(BRIEF_A, SPEC_AB, to_generator(SPEC_AB), WF["AB_extracted"][1])
    s = summary(units)
    assert s["units"] == 63 and s["values_missing"] == 0 and s["missing_quotes"] == 0
    by = {u["rule_id"]: u for u in units}
    assert by["6"]["status"] == "values_ok" and "callback" in by["6"]["spec_keys"]
    assert by["38"]["status"] == "values_ok" and by["38"]["nodes"]
    assert {by[r]["status"] for r in ("4", "33", "43", "44")} == {"prompt_only"}
    assert by["para:53"]["status"] == "values_ok"  # the holidays


def test_proof_map_brief_b_and_northwind():
    sb = counts(BRIEF_B, SPEC_AB, to_generator(SPEC_AB), WF["AB_extracted"][1])
    assert sb["units"] == 12 and sb["unmapped"] == 0 and sb["values_missing"] == 0
    sn = counts(BRIEF_NW, SPEC_NW, to_generator(SPEC_NW), WF["northwind"][1])
    assert sn["units"] == 26 and sn["unmapped"] == 0 and sn["values_missing"] == 0


def test_proof_map_finds_what_the_spec_lost():
    s = copy.deepcopy(SPEC_NW)
    s["calendar"]["holidays"].remove("2026-10-30")  # the memo's holiday
    s["lines"]["recorded"] = "Thanks."  # 4.7's mandatory sentence
    s["bands"][1]["max_days_late"] = 61
    s["neg"]["queues"]["human"] = "HUMANS"
    gen = to_generator(s)
    wf, _ = build(gen, NW_TOOLS)
    units = proof_map(BRIEF_NW, s, gen, wf)
    by = {u["rule_id"]: u for u in units}
    assert by["2"]["status"] == "values_missing" and by["2"]["missing"] == ["2026-10-30"]
    assert by["4.7"]["missing_quotes"] == ["A confirmation letter is on its way."]
    assert "SERVICE" in by["4.6"]["missing"]
    assert any("60" in u["missing"] for u in units if u.get("text", "").startswith("| 15"))
    lines = trace_lines(units)
    assert any(x.startswith("proof: 4.7 values_missing") for x in lines)
    assert "mandatory sentences missing from the workflow: 1" in lines[-1]


def test_overridden_values_are_not_missing():
    # the mail's 17:30 and the policy's 3 attempts are replaced by the memo ('not 17:30')
    by = {
        u["rule_id"]: u
        for u in proof_map(BRIEF_NW, SPEC_NW, to_generator(SPEC_NW), WF["northwind"][1])
    }
    assert by["para:22"]["missing"] == [] and by["1"]["missing"] == []


def test_forbidden_words_are_checked_against_the_spec():
    s = copy.deepcopy(SPEC_AB)
    s["forbidden_before_verify"] = ["debt"]
    by = {u["rule_id"]: u for u in proof_map(BRIEF_A, s, to_generator(s), WF["AB_extracted"][1])}
    assert by["1"]["status"] == "values_missing"
    assert any("'collections'" in m for m in by["1"]["missing"])


def test_rule_units_split_numbers_bold_items_tables_and_paragraphs():
    text = (
        "# T\n\nIntro prose here.\n\n1. First rule\n    - sub bullet\n2. Second rule\n\n"
        "**4.1 Calendar.** Holidays: 2026-01-01.\n\n| a | b |\n|---|---|\n| 1 | x |\n\n"
        "1. Again one\n"
    )
    ids = [u["id"] for u in rule_units(text)]
    assert ids == ["para:3", "1@L5", "2", "4.1", "table:13", "1@L15"]
    assert "sub bullet" in rule_units(text)[1]["text"]


def test_quote_roles():
    roles = dict(
        (q, r)
        for r, q in _quotes(
            'Ask "Am I speaking with {title}?". Do not say "debt", "payment" or the product. '
            'Only "yes" counts. Never ask "how much can you pay?". "I already paid": say '
            'thanks. First ask: "Can you pay today?". returns `hint: "day_swapped"`.'
        )
    )
    assert roles["Am I speaking with {title}?"] == "say"
    assert roles["debt"] == roles["payment"] == "forbid"
    assert roles["yes"] == "customer" and roles["I already paid"] == "customer"
    assert roles["how much can you pay?"] == "never"
    assert roles["Can you pay today?"] == "say"
    assert roles["day_swapped"] == "value"


def test_proof_map_never_raises():
    assert proof_map("", {}, None, {}) == []
    assert proof_map("1. Close as `x`.", None, None, None)[0]["status"] == "values_missing"
    out = proof_map(BRIEF_A, {"bands": "nonsense"}, {"cfg": 3}, {"nodes": "x"})
    assert out and all(isinstance(u["status"], str) for u in out)


# ---------------------------------------------------------------- 3. observer
OBS_TOOLS = parse_openapi(OPENAPI)


def other_spec():
    s = copy.deepcopy(SPEC_AB)
    s["callback"]["end"] = "18:00"
    s["bands"][0]["attempts"] = 2
    s["forbidden_before_verify"] = [*s["forbidden_before_verify"], "Arrears", "debt"]
    s["notes"] = ["a different note"]
    s["lines"]["opening"] = "Other words."
    return s


@pytest.fixture
def two_readings(monkeypatch):
    """chat answers the main thread with SPEC_AB and the observer's thread with another
    reading; each call is traced as a real call would be."""
    main, other = copy.deepcopy(SPEC_AB), other_spec()

    def chat(trace, purpose, messages, **k):
        trace.model("m/test", purpose, 0.15, 1000)
        obs = threading.current_thread().name == "extraction-observer"
        return json.dumps(other if obs else main)

    monkeypatch.setattr(spec_extract, "chat", chat)
    monkeypatch.delenv("BUILDER_OBSERVER", raising=False)
    return main, other


def test_observer_logs_disputed_fields_and_merges_only_forbidden_words(two_readings, tmp_path):
    t = Trace(tmp_path / "t.log", echo=False)
    oa = OPENAPI
    obs = extract_observer(BRIEF_A, oa, OBS_TOOLS, t, deadline=time.monotonic() + 600)
    spec = spec_extract.extract(BRIEF_A, oa, OBS_TOOLS, t, deadline=time.monotonic() + 600)
    before = copy.deepcopy(spec)
    info = observe(t, obs, spec, BRIEF_A, oa, 30)
    log = (tmp_path / "t.log").read_text()
    assert info["status"] == "done"
    assert set(info["disputed"]) == {"callback.end", "bands", "forbidden_before_verify"}
    line = next(x for x in log.splitlines() if "uncertainty: callback.end" in x)
    assert 'main="19:00" other="18:00"' in line and "grounded main=y other=n" in line
    assert "uncertainty: 3 of " in log and "1 added from the observer ['Arrears']" in log
    # only the forbidden words change: the union, main order first
    assert spec["forbidden_before_verify"] == [*before["forbidden_before_verify"], "Arrears"]
    assert {k: v for k, v in spec.items() if k != "forbidden_before_verify"} == {
        k: v for k, v in before.items() if k != "forbidden_before_verify"
    }
    assert any("purpose: extraction cross-check: extract the spec" in x for x in log.splitlines())
    assert t.model_calls >= 2 and abs(t.cost_usd - 0.15 * t.model_calls) < 1e-9


def test_policy_fields_ignore_wording():
    a, b = copy.deepcopy(SPEC_AB), copy.deepcopy(SPEC_AB)
    b["notes"] = ["x"]
    b["lines"]["goodbye"] = "Bye."
    b["identity"]["questions"][0]["ask"] = "Your birthday?"
    b["identity"]["questions"].reverse()
    b["forbidden_before_verify"] = [w.upper() for w in b["forbidden_before_verify"]][::-1]
    b["calendar"]["holidays"].reverse()
    assert policy_fields(a) == policy_fields(b)


def test_observer_not_finished_is_logged_and_ignored(tmp_path):
    t = Trace(tmp_path / "t.log", echo=False)
    gate = threading.Event()

    def slow(*a, **k):
        gate.wait(5)
        return other_spec()

    obs = extract_observer(BRIEF_A, OPENAPI, OBS_TOOLS, t, extract_fn=slow)
    spec = copy.deepcopy(SPEC_AB)
    info = observe(t, obs, spec, BRIEF_A, OPENAPI, 0.05)
    gate.set()
    assert info["status"] == "running" and spec == SPEC_AB
    assert "uncertainty: observer not finished" in (tmp_path / "t.log").read_text()


@pytest.mark.parametrize(
    "fail",
    [
        lambda *a, **k: (_ for _ in ()).throw(spec_extract.SpecError("no JSON")),
        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()),
        lambda *a, **k: "not a dict",
    ],
)
def test_observer_failure_is_logged_and_ignored(tmp_path, fail):
    t = Trace(tmp_path / "t.log", echo=False)
    obs = extract_observer(BRIEF_A, OPENAPI, OBS_TOOLS, t, extract_fn=fail)
    spec = copy.deepcopy(SPEC_AB)
    info = observe(t, obs, spec, BRIEF_A, OPENAPI, 5)
    assert info["status"] == "failed" and spec == SPEC_AB
    assert "uncertainty: observer failed" in (tmp_path / "t.log").read_text()


def test_observer_can_be_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("BUILDER_OBSERVER", "0")
    t = Trace(tmp_path / "t.log", echo=False)
    called = []
    assert extract_observer(BRIEF_A, OPENAPI, OBS_TOOLS, t, extract_fn=called.append) is None
    assert observe(t, None, {}, BRIEF_A, OPENAPI, 1) == {"status": "off"}
    assert called == [] and "observer disabled" in (tmp_path / "t.log").read_text()


# ---------------------------------------------------------------- 4. build.py
def run_build(monkeypatch, tmp_path, extract_fn, **patch):
    b = load_build()
    monkeypatch.setattr(b, "ROOT", tmp_path)
    monkeypatch.setattr(b, "extract", extract_fn)
    monkeypatch.setattr(b.freya, "validate_workflow", lambda *a, **k: {})
    sent = []

    def save(slot, wf, **k):
        sent.append(wf)
        return {"workflow_id": "w1", "agent_id": "a1", "version": 3}

    def export(wid, path):
        Path(path).write_text(json.dumps({"updated_at": "t", "workflow": sent[-1]}))

    monkeypatch.setattr(b.freya, "save_workflow", save)
    monkeypatch.setattr(b.freya, "api", lambda *a, **k: {"response": "Hello."})
    monkeypatch.setattr(b.freya, "export_workflow", export)
    for k, v in patch.items():
        monkeypatch.setattr(b, k, v)
    monkeypatch.delenv("BUILDER_OBSERVER", raising=False)
    brief, api = KIT / "briefs" / "brief-a.md", KIT / "tools" / "openapi.json"
    t = Trace(tmp_path / "t.log", echo=False)
    rc = b.run(t, brief, api, "x", time.monotonic() + 900)
    return rc, (tmp_path / "t.log").read_text(), tmp_path / "submission"


def by_thread(*a, **k):
    obs = threading.current_thread().name == "extraction-observer"
    return other_spec() if obs else copy.deepcopy(SPEC_AB)


def test_build_logs_uncertainty_proof_map_and_writes_the_manifest(monkeypatch, tmp_path):
    rc, log, sub = run_build(monkeypatch, tmp_path, by_thread)
    assert rc == 0
    assert "uncertainty: callback.end" in log and "uncertainty: 3 of " in log
    assert "proof map: 63 rule units" in log
    spec = json.loads((sub / "x.spec.json").read_text())
    assert "Arrears" in spec["forbidden_before_verify"]  # the union is what was built
    end = log.splitlines()[-1]
    assert "manifest=submission/x.manifest.json" in end
    m = json.loads((sub / "x.manifest.json").read_text())
    brief, api = KIT / "briefs" / "brief-a.md", KIT / "tools" / "openapi.json"
    assert m["inputs"]["brief_sha256"] == hashlib.sha256(brief.read_bytes()).hexdigest()
    assert m["inputs"]["tool_api_sha256"] == hashlib.sha256(api.read_bytes()).hexdigest()
    assert m["status"] == "ok" and m["smoke"] == "passed"
    assert m["export"]["mode"] == "platform" and m["export"]["check"] == "ok"
    out = sub / "x.workflow.json"
    assert m["export"]["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert m["freya"] == {"workflow_id": "w1", "agent_id": "a1", "version": 3}
    assert m["validator"]["errors"] == 0 and m["validator"]["warnings"] > 0
    assert len(m["workflow_sha256"]) == 64
    assert m["proof_map"]["units"] == 63
    assert m["spec"]["counts"]["bands"] == 2 and m["spec"]["problems"] == []
    assert m["observer"]["status"] == "done" and "callback.end" in m["observer"]["disputed"]
    assert set(m["model"]) == {"calls", "cost_usd", "failures"}


def test_manifest_has_no_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-very-secret-123456")
    monkeypatch.setenv("FREYA_API_KEY", "fak_secret_abcdef")
    _, _, sub = run_build(monkeypatch, tmp_path, by_thread)
    text = (sub / "x.manifest.json").read_text()
    assert "very-secret" not in text and "fak_secret" not in text


def test_a_failing_observer_never_changes_the_build(monkeypatch, tmp_path):
    def flaky(*a, **k):
        if threading.current_thread().name == "extraction-observer":
            raise RuntimeError("model down")
        return copy.deepcopy(SPEC_AB)

    rc, log, sub = run_build(monkeypatch, tmp_path, flaky)
    assert rc == 0 and "uncertainty: observer failed (RuntimeError: model down)" in log
    assert "status=ok " in log.splitlines()[-1]
    spec = json.loads((sub / "x.spec.json").read_text())
    assert spec["forbidden_before_verify"] == SPEC_AB["forbidden_before_verify"]


def test_manifest_failure_is_only_a_note(monkeypatch, tmp_path):
    def boom(text):
        raise OSError("disk full")

    rc, log, _ = run_build(monkeypatch, tmp_path, by_thread, redact=boom)
    assert rc == 0
    assert "manifest not written: OSError: disk full" in log
    assert "manifest=" not in log.splitlines()[-1] and "status=ok" in log.splitlines()[-1]


def test_proof_map_crash_is_only_a_note(monkeypatch, tmp_path):
    class Broken:
        @staticmethod
        def proof_map(*a):
            raise ValueError("bad")

    rc, log, _ = run_build(monkeypatch, tmp_path, by_thread, proof=Broken)
    assert rc == 0 and "proof map failed: ValueError: bad" in log


def test_invalid_workflow_still_writes_the_manifest(monkeypatch, tmp_path):
    b_build = load_build().build

    def bad_build(gen, tools):
        wf, pol = b_build(gen, tools)
        wf["nodes"]["opening"]["max_turn_transition"] = "nowhere"
        return wf, pol

    rc, log, sub = run_build(monkeypatch, tmp_path, by_thread, build=bad_build)
    assert rc == 1 and "nothing published" in log.splitlines()[-1]
    m = json.loads((sub / "x.manifest.json").read_text())
    assert m["validator"]["errors"] >= 1 and m["export"]["mode"] == "none"
