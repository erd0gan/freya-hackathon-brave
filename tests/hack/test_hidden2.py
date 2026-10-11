"""Hidden-brief variants the public kit does not show (second round). Each test is one variant
a hidden brief or tool API can take and what the builder must do with it."""

import copy
import json
from pathlib import Path

from fb.generate import build
from fb.openapi_tools import parse_openapi
from fb.spec_extract import check, to_generator

from conftest import KIT, ROOT

HERE = Path(__file__).parent
OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text())
BRIEF = (
    (KIT / "briefs" / "brief-a.md").read_text() + "\n" + (KIT / "briefs" / "brief-b.md").read_text()
)
SPEC = json.loads((HERE / "fixtures" / "spec_ab_extracted.json").read_text())


def spec_with(change=None):
    s = copy.deepcopy(SPEC)
    if change:
        change(s)
    return s


def workflow(s, o=None):
    o = o or OPENAPI
    return build(to_generator(s), parse_openapi(o))[0]


def close_hook(wf):
    fn = next(t for t in wf["tools"] if t["function"]["name"] == "close_call")
    return fn["pre_process_code"]


def run_hook(src, body, v=None):
    cfg = {"body": body}
    exec(compile(src, "hook", "exec"), {"vars": v or {}, "config": cfg})
    return cfg["body"]


# ---------------------------------------------------------------- 1. paid closing as no_promise
def paid_as_no_promise(s):
    s["neg"]["outcomes"]["paid"] = "no_promise"


def test_paid_as_no_promise_keeps_every_no_promise_reason():
    wf = workflow(spec_with(paid_as_no_promise))
    hook = close_hook(wf)
    for reason in ("refused", "paid", "hardship"):
        body = run_hook(hook, {"customer_id": "c1", "outcome": "no_promise", "reason": reason})
        assert body.get("reason") == reason
    # promise still never carries a reason
    body = run_hook(hook, {"customer_id": "c1", "outcome": "promise", "reason": "x"})
    assert "reason" not in body


def test_paid_close_reason_reaches_the_engine_config():
    gen = to_generator(spec_with(paid_as_no_promise))
    assert gen["cfg"]["neg"]["close_reasons"]["paid"] == "paid"
    # an explicit close value wins over the note value
    s = spec_with(paid_as_no_promise)
    s["neg"]["close_reasons"] = {"paid": "already_paid"}
    assert to_generator(s)["cfg"]["neg"]["close_reasons"]["paid"] == "already_paid"


def test_brief_a_paid_has_no_reason():
    gen = to_generator(spec_with())
    assert "paid" not in gen["cfg"]["neg"]["close_reasons"]
    hook = close_hook(workflow(spec_with()))
    body = run_hook(hook, {"customer_id": "c1", "outcome": "paid", "reason": "paid"})
    assert "reason" not in body
    body = run_hook(hook, {"customer_id": "c1", "outcome": "no_promise", "reason": "refused"})
    assert body["reason"] == "refused"


def test_paid_close_reason_outside_the_enum_is_reported():
    o = copy.deepcopy(OPENAPI)
    sch = o["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]
    sch["schema"]["properties"]["reason"]["enum"] = [
        "unclear", "callback", "no_callback", "wrong_number", "deceased", "failed", "declined",
        "restructuring", "human", "support", "refused", "hardship", "dispute", "service_error",
    ]  # fmt: skip
    s = spec_with(paid_as_no_promise)
    out = check(s, parse_openapi(o), BRIEF, o)
    assert any(p.startswith("neg.close_reasons.paid: 'paid'") for p in out), out


# ---------------------------------------------------------------- 2. a transfer without a reason
def no_transfer_reason(s):
    s["neg"]["transfer_reasons"] = {"restructuring": "", "human": "", "support": ""}


def code_of(wf, node):
    fn = wf["nodes"][node]["pre_actions"][0]["function"]
    return fn["parameters"]["properties"]["code"]["default"]


def test_transfer_without_a_reason_is_a_valid_spec():
    assert check(spec_with(no_transfer_reason), parse_openapi(OPENAPI), BRIEF, OPENAPI) == []
    # a missing key is still reported (the repair round fills it, '' allowed)
    s = spec_with(lambda s: s["neg"]["transfer_reasons"].pop("human"))
    out = check(s, parse_openapi(OPENAPI), BRIEF, OPENAPI)
    assert any(p.startswith("neg.transfer_reasons needs") for p in out)


def test_transfer_without_a_reason_sends_none():
    wf = workflow(spec_with(no_transfer_reason))
    assert "vars['final_reason'] = ''" in code_of(wf, "pre_transfer")
    body = run_hook(close_hook(wf), {"customer_id": "c1", "outcome": "transferred", "reason": ""})
    assert body == {"customer_id": "c1", "outcome": "transferred"}


# ---------------------------------------------------------------- 3a. variants check() accepted
def ok200(o, tool):
    return o["paths"]["/" + tool]["post"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]


def api_with(change):
    o = copy.deepcopy(OPENAPI)
    change(o)
    return o


def verify_tool(wf):
    return next(
        t for t in wf["nodes"]["identity"]["tools"] if t["function"]["name"] == "verify_identity"
    )


def run_post(src, data, v):
    result = {"status": "success", "data": dict(data)}
    exec(compile(src, "post", "exec"), {"vars": v, "result": result})
    return result["data"]


def test_verify_without_ok_passes_and_reads_wrong_answers_as_wrong():
    o = api_with(lambda o: ok200(o, "verify_identity")["properties"].pop("ok"))
    assert check(spec_with(), parse_openapi(o), BRIEF, o) == []
    post = verify_tool(workflow(spec_with(), o))["post_process_code"]
    v = {"id_last_field": "date_of_birth"}
    d = run_post(post, {"verified": False, "wrong_answers_left": 2}, v)
    assert d["what_to_do_next"].startswith("That answer is not correct")
    d = run_post(post, {"verified": True, "wrong_answers_left": 3}, v)
    assert d["what_to_do_next"].startswith("Correct. Now ask")
    # nothing judged (an error answer): unreadable, ask again
    d = run_post(post, {"error": "unreadable date", "wrong_answers_left": 3}, v)
    assert d["what_to_do_next"].startswith("The answer could not be read")


def test_verify_with_ok_unchanged():
    post = verify_tool(workflow(spec_with()))["post_process_code"]
    v = {"id_last_field": "date_of_birth"}
    d = run_post(post, {"ok": False, "error": "x", "wrong_answers_left": 3}, v)
    assert d["what_to_do_next"].startswith("The answer could not be read")
    d = run_post(post, {"ok": True, "verified": False, "wrong_answers_left": 2}, v)
    assert d["what_to_do_next"].startswith("That answer is not correct")


def test_verify_without_all_verified_counts_the_answers():
    from fb.validate import validate

    o = api_with(lambda o: ok200(o, "verify_identity")["properties"].pop("all_verified"))
    assert check(spec_with(), parse_openapi(o), BRIEF, o) == []
    wf, policy = build(to_generator(spec_with()), parse_openapi(o))
    assert validate(wf, parse_openapi(o), policy).ok
    post = verify_tool(wf)["post_process_code"]
    v = {"id_last_field": "date_of_birth"}
    d = run_post(post, {"ok": True, "verified": True, "wrong_answers_left": 3}, v)
    assert d["what_to_do_next"].startswith("Correct. Now ask") and "id_all_ok" not in v
    v["id_last_field"] = "phone_last4"
    d = run_post(post, {"ok": True, "verified": True, "wrong_answers_left": 3}, v)
    assert d["what_to_do_next"].startswith("Both answers are correct")
    assert v["id_all_ok"] == "true"
    route = wf["nodes"]["identity"]["transitions"][0]
    assert route["target_node"] == "account"
    assert {"variable": "id_all_ok", "operator": "equals", "operand": "true"} in route["condition"][
        "conditions"
    ]
    # the kit API (it has all_verified) keeps the plain route
    kit = workflow(spec_with())["nodes"]["identity"]["transitions"][0]["condition"]
    assert all(c["variable"] != "id_all_ok" for c in kit["conditions"])
    # neither flag: still a problem
    o2 = api_with(lambda o: [ok200(o, "verify_identity")["properties"].pop(k) for k in
                             ("all_verified", "verified")])  # fmt: skip
    assert any("all_verified" in p for p in check(spec_with(), parse_openapi(o2), BRIEF, o2))


def test_band_without_persuasion_attempts_is_valid():
    s = spec_with(lambda s: s["bands"][0].update(attempts=0))
    assert check(s, parse_openapi(OPENAPI), BRIEF, OPENAPI) == []


def test_note_reason_the_tool_has_no_value_for_is_not_required():
    def no_hardship(o):
        sch = o["paths"]["/add_note"]["post"]["requestBody"]["content"]["application/json"]
        sch["schema"]["properties"]["reason"]["enum"] = ["refused", "dispute", "service_error"]

    o = api_with(no_hardship)
    s = spec_with(lambda s: s["neg"]["reasons"].update(hardship="", paid=""))
    assert check(s, parse_openapi(o), BRIEF, o) == []
    # an empty value the tool does have a value for is still reported
    s = spec_with(lambda s: s["neg"]["reasons"].update(dispute=""))
    assert any("neg.reasons.dispute" in p for p in check(s, parse_openapi(o), BRIEF, o))
    neg = to_generator(spec_with(lambda s: s["neg"]["reasons"].update(hardship="")))["cfg"]["neg"]
    assert "hardship" not in neg["reasons"]


# ---------------------------------------------------------------- 3b. problems left: publish
def load_build():
    import importlib.util

    spec = importlib.util.spec_from_file_location("builder_main", ROOT / "builder" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_build(monkeypatch, tmp_path, s):
    import time

    from fb.trace import Trace

    b = load_build()
    saved = []
    monkeypatch.setattr(b, "ROOT", tmp_path)
    monkeypatch.setattr(b, "extract", lambda *a, **k: copy.deepcopy(s))
    monkeypatch.setattr(b.freya, "validate_workflow", lambda *a, **k: {})

    def save(slot, wf, **k):
        saved.append(wf)
        return {"workflow_id": "w1", "agent_id": "a1"}

    monkeypatch.setattr(b.freya, "save_workflow", save)
    monkeypatch.setattr(b.freya, "api", lambda *a, **k: {"response": "Hello."})
    monkeypatch.setattr(b.freya, "export_workflow", lambda *a, **k: None)
    log = tmp_path / "t.log"
    rc = b.run(
        Trace(log, echo=False),
        KIT / "briefs" / "brief-a.md",
        KIT / "tools" / "openapi.json",
        "x",
        time.monotonic() + 600,
    )
    return rc, log.read_text(), saved


def test_problems_left_after_the_repair_still_publish(monkeypatch, tmp_path):
    s = spec_with(lambda s: s["product_fields"].update(past_due="nonexistent"))
    rc, log, saved = run_build(monkeypatch, tmp_path, s)
    assert rc == 0 and len(saved) == 1
    assert "spec error: product_fields.past_due" in log
    assert "status=ok_spec_problems" in log.splitlines()[-1]


def test_a_spec_without_questions_publishes_nothing(monkeypatch, tmp_path):
    s = spec_with(lambda s: s["identity"].update(questions=[]))
    rc, log, saved = run_build(monkeypatch, tmp_path, s)
    assert rc == 1 and saved == []
    assert "spec invalid (identity.questions is empty)" in log.splitlines()[-1]


def test_a_clean_spec_still_ends_ok(monkeypatch, tmp_path):
    rc, log, _ = run_build(monkeypatch, tmp_path, spec_with())
    assert rc == 0 and "status=ok " in log.splitlines()[-1] + " "


# ---------------------------------------------------------------- 4. plan shorter than attempts
def three_attempts(s):
    s["bands"][1]["attempts"] = 3


def test_plan_shorter_than_the_attempts_is_reported_and_capped():
    out = check(spec_with(three_attempts), parse_openapi(OPENAPI), BRIEF, OPENAPI)
    assert any(p.startswith("attempt_plan has 2 step(s) but a band has 3") for p in out), out
    bands = to_generator(spec_with(three_attempts))["cfg"]["bands"]
    assert [b["attempts"] for b in bands] == [1, 2]


def test_a_full_plan_keeps_the_attempts():
    def plan3(s):
        three_attempts(s)
        s["attempt_plan"].append({"amount": "full", "date": "deadline"})

    assert check(spec_with(plan3), parse_openapi(OPENAPI), BRIEF, OPENAPI) == []
    assert [b["attempts"] for b in to_generator(spec_with(plan3))["cfg"]["bands"]] == [1, 3]


def test_the_engine_never_repeats_a_refused_offer_with_a_short_plan():
    from fb.harness.engine_sim import simulate
    from test_engine import CASES

    case = next(c for c in CASES.values() if c["id"] == "PUB-10")
    cfg = to_generator(spec_with(three_attempts))["cfg"]
    r = simulate(case, [{"kind": "no"}] * 5, cfg)
    offers = [s for s in r.says if "Could you pay" in s]
    assert len(offers) == 2 and len(set(offers)) == 2, r.says


# ---------------------------------------------------------------- 5. allOf / $ref responses
def promise_by_allof(o):
    o.setdefault("components", {}).setdefault("schemas", {})["Err"] = {
        "type": "object",
        "properties": {"error": {"type": "string"}},
    }
    sch = o["paths"]["/create_promise"]["post"]["responses"]["200"]["content"]
    sch["application/json"]["schema"] = {
        "allOf": [
            {"$ref": "#/components/schemas/Err"},
            {"type": "object", "properties": {"promise_id": {"type": "string"}}},
        ]
    }


def test_allof_response_without_ok_maps_the_success_field():
    from fb.generate import has_flag, success_field

    o = api_with(promise_by_allof)
    t = parse_openapi(o)["create_promise"]
    assert set(t.response_props) == {"error", "promise_id"}
    assert not has_flag(t, "ok")
    assert success_field(t, "ok") == ("promise_id", "present")
    wf = workflow(spec_with(), o)
    # first: the success field (an error-text mapping may follow: "already recorded")
    assert wf["nodes"]["record"]["response_mapping"][0] == {
        "path": "data.promise_id",
        "variable": "rec_ok",
    }
    assert check(spec_with(), parse_openapi(o), BRIEF, o) == []


def test_allof_response_with_ok_keeps_the_flag():
    def with_ok(o):
        promise_by_allof(o)
        o["components"]["schemas"]["Err"]["properties"]["ok"] = {"type": "boolean"}

    o = api_with(with_ok)
    wf = workflow(spec_with(), o)
    assert wf["nodes"]["record"]["response_mapping"][0] == {"path": "data.ok", "variable": "rec_ok"}


def test_verify_all_verified_behind_a_ref_is_found():
    def by_ref(o):
        o.setdefault("components", {}).setdefault("schemas", {})["V"] = ok200(o, "verify_identity")
        sch = o["paths"]["/verify_identity"]["post"]["responses"]["200"]["content"]
        sch["application/json"]["schema"] = {"allOf": [{"$ref": "#/components/schemas/V"}]}

    wf = workflow(spec_with(), api_with(by_ref))
    cond = wf["nodes"]["identity"]["transitions"][0]["condition"]
    assert all(c["variable"] != "id_all_ok" for c in cond["conditions"])


# ---------------------------------------------------------------- 6. reason_priority as codes
def priority_codes(s):
    s["neg"]["reasons"].update(dispute="DSP", hardship="HRD", refused="REF")
    s["neg"]["close_reasons"] = {"service_error": "TECH"}
    s["neg"].update(no_promise_reason="priority", reason_priority=["DSP", "TECH", "HRD", "REF"])


def test_priority_written_as_bank_codes_is_mapped_to_semantic_keys():
    neg = to_generator(spec_with(priority_codes))["cfg"]["neg"]
    assert neg["reason_priority"] == ["dispute", "service_error", "hardship", "refused"]
    out = structure_of(spec_with(priority_codes))
    assert not [p for p in out if "reason_priority" in p], out


def test_priority_with_unknown_entries_is_reported():
    s = spec_with(priority_codes)
    s["neg"]["reason_priority"] = ["disputed", "dispute"]
    out = structure_of(s)
    assert any(p.startswith("neg.reason_priority: ['disputed']") for p in out), out
    s["neg"]["reason_priority"] = []
    assert any("names no reason" in p for p in structure_of(s))


def test_engine_closes_with_the_priority_reason():
    from fb.nodecode.embed import namespace

    ns = namespace()
    neg = to_generator(spec_with(priority_codes))["cfg"]["neg"]
    st = {
        "p": [
            {"id": "p1", "n": 1, "status": "refused"},
            {"id": "p2", "n": 2, "status": "dispute"},
        ],
        "transfer": "",
        "recorded": [],
        "final_lines": [],
    }
    ns["_finish"](st, neg)
    assert st["final_reason"] == "DSP"


# ---------------------------------------------------------------- 7. product types of the enum
def mortgage_type(o):
    items = ok200(o, "get_account")["properties"]["products"]["items"]["properties"]
    items["type"]["enum"].append("mortgage")


def test_product_type_missing_from_floor_field_and_names_is_reported():
    o = api_with(mortgage_type)
    out = check(spec_with(), parse_openapi(o), BRIEF, o)
    assert any(
        p.startswith("floor_field has no entry for product type(s) ['mortgage']") for p in out
    )
    assert any(
        p.startswith("product_names has no entry for product type(s) ['mortgage']") for p in out
    )

    def covered(s):
        s["floor_field"]["mortgage"] = ""  # the brief gives it no floor
        s["product_names"]["mortgage"] = "mortgage"

    assert check(spec_with(covered), parse_openapi(o), BRIEF, o) == []


def structure_of(s):
    from fb.spec_extract import structure

    return structure(s)


# ---------------------------------------------------------------- 8. lines the brief is silent on
BRIEF_A_WORDS = ("text message", "mobile app", "one business day")


def test_every_engine_line_is_set():
    import re

    from fb.spec_extract import ENGINE_LINES

    src = (ROOT / "builder" / "fb" / "nodecode" / "negotiation_lib.py").read_text("utf-8")
    keys = set(re.findall(r'_line\(\s*neg,\s*"(\w+)"', src))
    keys |= {"transfer_" + w for w in ("restructuring", "human", "support")}
    body = (ROOT / "builder" / "fb" / "nodecode" / "bodies.py").read_text("utf-8")
    keys |= set(re.findall(r"get\('lines'\) or dict\(\)\)\.get\('(\w+)'\)", body))
    assert keys - {"transfer_"} <= set(ENGINE_LINES), keys - set(ENGINE_LINES)
    lines = to_generator(spec_with())["cfg"]["neg"]["lines"]
    assert set(ENGINE_LINES) <= set(lines) and all(lines.values())


def silent(s):
    for k in ("recorded", "paid", "cannot_change", "rest_overdue"):
        s["lines"][k] = ""


def test_a_silent_brief_gets_no_brief_a_wording():
    neg = to_generator(spec_with(silent))["cfg"]["neg"]
    said = " ".join(neg["lines"].values()).lower()
    assert not [w for w in BRIEF_A_WORDS if w in said]


def test_paid_and_recorded_say_neutral_words_in_the_engine():
    from fb.harness.engine_sim import simulate
    from test_engine import CASES

    cfg = to_generator(spec_with(silent))["cfg"]
    for case, replies in (
        ("PUB-08", [{"kind": "paid"}]),
        ("PUB-01", [{"kind": "yes"}, {"kind": "yes"}, {"kind": "unclear"}]),
    ):
        r = simulate(CASES[case], replies, cfg)
        said = " ".join(r.says).lower()
        assert not [w for w in BRIEF_A_WORDS if w in said], r.says


def test_the_brief_line_wins():
    neg = to_generator(spec_with())["cfg"]["neg"]
    assert neg["lines"]["recorded"] == "You will receive a text message confirming this promise."
    assert neg["lines"]["paid"].endswith("Payments can take one business day to appear.")


# ---------------------------------------------------------------- A. a 500 on a write
def test_close_call_is_repeated_after_a_failure():
    wf = workflow(spec_with())
    close = next(t for t in wf["tools"] if t["function"]["name"] == "close_call")
    assert close["config"]["maxRetries"] == 2
    for node in ("record", "cb_book", "transfer"):
        assert wf["nodes"][node]["pre_actions"][0]["config"]["maxRetries"] == 0


def test_a_failed_identity_check_is_tried_again_without_asking():
    """B PUB-12, 11 Oct: the bank answered an HTML error page and the model asked for the date
    of birth again ('the date of birth is not asked again' failed)."""
    wf = workflow(spec_with())
    tools = [t for n in wf["nodes"].values() for t in n.get("tools") or []]
    verify = [t for t in tools if t["function"]["name"] == "verify_identity"]
    assert verify and all(t["config"]["maxRetries"] == 1 for t in verify)
    task = wf["nodes"]["identity"]["task_message"]
    assert "never ask the customer again because of an error" in task


def after_cb(wf, v):
    exec(compile(code_of(wf, "after_cb"), "after_cb", "exec"), {"vars": v})
    return v["cb_next"]


def test_a_failed_callback_booking_is_tried_once_more():
    from fb.validate import validate

    wf, policy = build(to_generator(spec_with()), parse_openapi(OPENAPI))
    assert validate(wf, parse_openapi(OPENAPI), policy).ok
    v = {"cb_ok": "false", "cb_book_failure_count": "0"}
    assert after_cb(wf, v) == "retry"
    v.update(cb_ok="true")
    assert after_cb(wf, v) == "done"
    # a 500 (failure counter up, nothing mapped), twice: no callback
    v = {"cb_book_failure_count": "1"}
    assert after_cb(wf, v) == "retry"
    v.update(cb_book_failure_count="2")
    assert after_cb(wf, v) == "failed"
    assert after_cb(wf, {"cb_ok": "True"}) == "done"


def test_callback_without_ok_field_uses_the_error_field():
    def no_ok(o):
        ok200(o, "schedule_callback")["properties"].pop("ok")

    o = api_with(no_ok)
    wf = workflow(spec_with(), o)
    assert wf["nodes"]["cb_book"]["response_mapping"][0] == {
        "path": "data.error",
        "variable": "cb_ok",
    }
    assert after_cb(wf, {"cb_ok": ""}) == "done"
    assert after_cb(wf, {"cb_ok": "slot taken"}) == "retry"


# ---------------------------------------------------------------- B. callbacks on business days
def weekend_cb(s):
    s["callback"]["business_days_only"] = False


def test_business_days_only_false_against_the_brief_is_flagged():
    from fb.spec_extract import grounding

    out = grounding(spec_with(weekend_cb), BRIEF)
    assert any(p.startswith("callback.business_days_only is false") for p in out), out
    weekend = "6. A callback can be today or the next business day, weekends included."
    assert not any("business_days_only" in p for p in grounding(spec_with(weekend_cb), weekend))


def test_business_days_only_defaults_to_true():
    def unset(s):
        s["callback"].pop("business_days_only")

    assert to_generator(spec_with(unset))["cfg"]["callback"]["business_days_only"] is True
    s = spec_with(lambda s: s["callback"].update(business_days_only="false"))
    assert to_generator(s)["cfg"]["callback"]["business_days_only"] is True
    from fb.spec_extract import structure

    assert "callback.business_days_only must be true or false" in structure(s)


def test_extract_overrides_a_weekend_reading_of_a_business_day_rule(monkeypatch):
    import fb.spec_extract as se
    from fb.trace import Trace

    reply = json.dumps(spec_with(weekend_cb))
    monkeypatch.setattr(se, "chat", lambda *a, **k: reply)
    tools = parse_openapi(OPENAPI)
    s = se.extract(BRIEF, OPENAPI, tools, Trace(None, echo=False))
    assert s["callback"]["business_days_only"] is True
