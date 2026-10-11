"""The generated workflow: identity field binding (F08), a person asked for before verification
(F09), the opening fallback (F02), renamed call variables."""

import copy
import json

import pytest
from fb.generate import build, digit_count
from fb.openapi_tools import parse_openapi
from fb.validate import validate
from spec_fixtures import SPEC_A

from conftest import KIT

OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text())


def tools_with_identity_renamed():
    o = copy.deepcopy(OPENAPI)
    b = o["paths"]["/verify_identity"]["post"]["requestBody"]["content"]["application/json"]
    props = b["schema"]["properties"]
    props["question_code"] = props.pop("field")
    props["response"] = props.pop("answer")
    b["schema"]["required"] = ["customer_id", "question_code", "response"]
    return parse_openapi(o)


def run_hook(src, body, v=None):
    cfg = {"body": body}
    exec(compile(src, "hook", "exec"), {"vars": v or {}, "config": cfg})
    return cfg["body"]


def identity_tool(wf):
    return next(
        t for t in wf["nodes"]["identity"]["tools"] if t["function"]["name"] == "verify_identity"
    )


def test_identity_hook_uses_bound_field_names():
    spec = copy.deepcopy(SPEC_A)
    spec["tool_fields"] = {"verify_identity": {"field": "question_code", "answer": "response"}}
    wf, _ = build(spec, tools_with_identity_renamed())
    t = identity_tool(wf)
    body = run_hook(
        t["pre_process_code"],
        {"customer_id": "c1", "question_code": "date_of_birth", "response": "14 March 1988"},
    )
    assert body == {"customer_id": "c1", "question_code": "date_of_birth", "response": "1988-03-14"}
    leaf = t["function"]["parameters"]["properties"]["body"]["properties"]
    assert set(leaf) == {"customer_id", "question_code", "response"}


def test_identity_hook_default_names():
    wf, _ = build(SPEC_A, parse_openapi(OPENAPI))
    body = run_hook(
        identity_tool(wf)["pre_process_code"],
        {"customer_id": "c1", "field": "phone_last4", "answer": "four four seven one"},
    )
    assert body["answer"] == "4471"


def test_person_before_verification_has_its_own_path():
    wf, policy = build(SPEC_A, parse_openapi(OPENAPI))
    assert validate(wf, parse_openapi(OPENAPI), policy).ok
    nodes = wf["nodes"]
    targets = [t["target_node"] for t in nodes["identity"]["transitions"]]
    assert (
        targets.index("pre_transfer") < targets.index("pre_human") < targets.index("decline_check")
    )
    assert nodes["pre_human"]["on_enter"]["text"].startswith("Before I transfer you")
    assert nodes["transfer_pre"]["transitions"][-1]["target_node"] == "after_transfer_pre"
    assert nodes["after_transfer_pre"]["transitions"][-1]["target_node"] == "goodbye"
    code = nodes["pre_transfer"]["pre_actions"][0]["function"]["parameters"]["properties"]
    src = code["code"]["default"]
    assert "'general'" in src and "'transferred'" in src and "'human'" in src


def test_person_transfer_at_once_when_the_brief_says_so():
    spec = copy.deepcopy(SPEC_A)
    spec["cfg"] = {**spec["cfg"], "neg": {**spec["cfg"]["neg"], "human_requests_to_transfer": 1}}
    wf, _ = build(spec, parse_openapi(OPENAPI))
    first = wf["nodes"]["identity"]["transitions"][2]
    assert first["target_node"] == "pre_transfer"
    assert first["condition"]["conditions"][0]["conditions"][1]["operand"] == -1


def test_opening_line_is_also_in_the_task():
    wf, _ = build(SPEC_A, parse_openapi(OPENAPI))
    line = wf["nodes"]["opening"]["on_enter"]["text"]
    assert line in wf["nodes"]["opening"]["task_message"].replace("'", "'")
    assert "recording this call" in line


def test_full_name_call_variable():
    spec = copy.deepcopy(SPEC_A)
    spec["call_variables"] = {"customer_id": "customer_id", "full_name": "full_name", "now": "now"}
    spec["lines"] = {**spec["lines"], "who_question": "Is this {{full_name}}?"}
    wf, _ = build(spec, parse_openapi(OPENAPI))
    text = json.dumps(wf)
    assert "{{title}}" not in text and "{{last_name}}" not in text
    assert "{{full_name}}" in wf["nodes"]["third_party"]["task_message"]


def test_failed_callback_booking_is_not_reported_as_booked():
    wf, _ = build(SPEC_A, parse_openapi(OPENAPI))
    node = wf["nodes"]["cb_book"]
    # the outcome of the booking is decided by after_cb (one more try, then no callback)
    assert [t["target_node"] for t in node["transitions"]] == ["after_cb"]
    assert node["response_mapping"][0] == {"path": "data.ok", "variable": "cb_ok"}
    after = [t["target_node"] for t in wf["nodes"]["after_cb"]["transitions"]]
    # first the guard on the platform's call counters (LP-5), then the code's decision
    assert after == ["fin_loop_error", "cb_book", "fin_callback", "fin_no_callback"]


def test_digit_pieces_in_the_heard_order():
    src = identity_tool(build(SPEC_A, parse_openapi(OPENAPI))[0])["pre_process_code"]
    body = {"customer_id": "c1", "field": "phone_last4", "answer": "0499"}
    heard = {"id_heard": "ninety-nine zero-four", "id_last": "zero-four"}
    assert run_hook(src, dict(body), heard)["answer"] == "9904"


def test_a_new_full_answer_is_not_reordered():
    # PUB-12: 4401 was wrong, now the customer says 4410 in one go; a stale heard value
    # with the same digits must not undo it
    src = identity_tool(build(SPEC_A, parse_openapi(OPENAPI))[0])["pre_process_code"]
    body = {"customer_id": "c1", "field": "phone_last4", "answer": "4410"}
    heard = {"id_heard": "four four zero one", "id_last": "four four one zero"}
    assert run_hook(src, dict(body), heard)["answer"] == "4410"


@pytest.mark.parametrize(
    "sent,last,want",
    [
        ("666604", "The last four digits are 6604.", "6604"),  # B corrects PUB-13, 11 Oct
        ("6606604", "6604", "6604"),
        ("9904", "zero-four", "9904"),  # pieces still joined
        ("44010", "four four zero", "44010"),  # last message not whole: left as it is
    ],
)
def test_a_whole_answer_said_again_is_not_joined_to_the_pieces(sent, last, want):
    src = identity_tool(build(SPEC_A, parse_openapi(OPENAPI))[0])["pre_process_code"]
    body = {"customer_id": "c1", "field": "phone_last4", "answer": sent}
    v = {"id_heard": "66 " + last, "id_last": last}
    assert run_hook(src, dict(body), v)["answer"] == want


def test_digit_count_of_a_question():
    assert digit_count({"field": "phone_last4"}) == 4
    assert digit_count({"field": "pin", "ask": "What are the last six digits of it?"}) == 6
    assert digit_count({"field": "code", "ask": "What is your code?"}) == 0


def test_lone_century_is_not_sent_as_a_year():
    src = identity_tool(build(SPEC_A, parse_openapi(OPENAPI))[0])["pre_process_code"]
    body = {"customer_id": "c1", "field": "date_of_birth", "answer": "2019-12-05"}
    v = {"id_heard": "5 December 19", "id_last": "19"}
    assert run_hook(src, dict(body), v)["answer"] == "incomplete"
    assert v["id_incomplete"] == "yes"
    v = {"id_heard": "5 December 19 75", "id_last": "75"}
    body["answer"] = "1975-12-05"
    assert run_hook(src, dict(body), v)["answer"] == "1975-12-05"
    v = {"id_last": "nineteen seventy-five"}
    assert run_hook(src, dict(body), v)["answer"] == "1975-12-05"


def test_post_hook_asks_for_the_rest_of_the_year():
    post = identity_tool(build(SPEC_A, parse_openapi(OPENAPI))[0])["post_process_code"]
    result = {"data": {"ok": False, "error": "answer is not a date"}}
    exec(compile(post, "hook", "exec"), {"vars": {"id_incomplete": "yes"}, "result": result})
    assert "rest of the year" in result["data"]["what_to_do_next"]


def test_writes_wait_longer_than_reads():
    wf, _ = build(SPEC_A, parse_openapi(OPENAPI))
    for node in ("record", "cb_book", "transfer", "transfer_pre"):
        assert wf["nodes"][node]["pre_actions"][0]["config"]["timeoutMs"] == 25000
        assert wf["nodes"][node]["pre_actions"][0]["config"]["maxRetries"] == 0


# talkative (B PUB-11): 15 cut small-talk turns at the phone question hit the 16-turn cap and
# the call was closed as declined although the customer never refused
def test_identity_turn_cap_leaves_room_for_cut_turns():
    wf, _ = build(copy.deepcopy(SPEC_A), parse_openapi(OPENAPI))
    for name in ("identity", "identity2"):
        assert wf["nodes"][name]["max_node_turns"] >= 40


def close_hook(wf):
    fn = next(t for t in wf["tools"] if t["function"]["name"] == "close_call")
    return fn["pre_process_code"]


# talkative: the customer hung up, no finish step ran, close_call went out with outcome ""
def test_hangup_after_a_recorded_promise_closes_as_promise():
    wf, _ = build(copy.deepcopy(SPEC_A), parse_openapi(OPENAPI))
    st = json.dumps({"p": [{"id": "p1", "status": "recorded"}]})
    body = run_hook(
        close_hook(wf), {"customer_id": "c1", "outcome": "", "reason": ""}, {"neg_state": st}
    )
    assert body == {"customer_id": "c1", "outcome": "promise"}


def test_hangup_during_negotiation_closes_as_refused():
    wf, _ = build(copy.deepcopy(SPEC_A), parse_openapi(OPENAPI))
    st = json.dumps({"p": [{"id": "p1", "status": "open"}]})
    body = run_hook(
        close_hook(wf), {"customer_id": "c1", "outcome": "", "reason": ""}, {"neg_state": st}
    )
    assert (body["outcome"], body["reason"]) == ("no_promise", "refused")


def test_hangup_before_the_account_closes_as_not_reached():
    wf, _ = build(copy.deepcopy(SPEC_A), parse_openapi(OPENAPI))
    body = run_hook(close_hook(wf), {"customer_id": "c1", "outcome": "", "reason": ""}, {})
    assert (body["outcome"], body["reason"]) == ("not_reached", "unclear")


def test_a_set_outcome_is_kept():
    wf, _ = build(copy.deepcopy(SPEC_A), parse_openapi(OPENAPI))
    body = run_hook(close_hook(wf), {"customer_id": "c1", "outcome": "paid", "reason": ""}, {})
    assert body == {"customer_id": "c1", "outcome": "paid"}


# talkative: an 80-turn call went over code_exec's 131072-byte limit (code + vars) with the
# plain 63 KB lib in every engine node; the packed lib leaves room for the conversation
def test_engine_nodes_are_small():
    from fb.nodecode.embed import CODE_HARD_CAP

    wf, _ = build(copy.deepcopy(SPEC_A), parse_openapi(OPENAPI))
    for name in ("facts", "decide", "after_record", "setup_id", "cb_check"):
        code = wf["nodes"][name]["pre_actions"][0]["function"]["parameters"]["properties"]["code"]
        assert len(code["default"].encode()) <= CODE_HARD_CAP, name


def text_identity_hook():
    spec = copy.deepcopy(SPEC_A)
    spec["identity"]["questions"][1]["kind"] = "text"
    return identity_tool(build(spec, parse_openapi(OPENAPI))[0])["pre_process_code"]


# stress D-01 on Freya, 11 Oct: 'A-B-zero-seven' went to the bank as said, twice, and the
# customer was locked out
@pytest.mark.parametrize(
    "said,sent",
    [
        ("A-B-zero-seven", "AB07"),
        ("A-B-zero-seven, that's A as in apple, B as in bravo, zero, seven", "AB07"),
        ("a b 0 7", "AB07"),
        ("AB07", "AB07"),
        ("SW1A 1AA", "SW1A 1AA"),
        ("Mary Ann", "Mary Ann"),
        ("A B A B", "ABAB"),
        ("zero zero seven three", "0073"),  # C-01
        ("zero zero seven three, that's 0-0-7-3", "0073"),
        ("Zero zero seven three.", "0073"),  # C-03
        ("My current postcode is ZX21, though I moved recently.", "ZX21"),  # R30
        ("AB07 is all I can give you there, sorry.", "AB07"),  # R11
        ("It is SW1A 1AA I think", "It is SW1A 1AA I think"),
        ("My name is Mary Ann Smith", "My name is Mary Ann Smith"),
    ],
)
def test_a_spelled_code_is_sent_as_the_code(said, sent):
    body = {"customer_id": "c1", "field": "phone_last4", "answer": said}
    assert run_hook(text_identity_hook(), body)["answer"] == sent


def test_a_text_answer_is_never_asked_for_more():
    wf, _ = build(copy.deepcopy(SPEC_A), parse_openapi(OPENAPI))
    assert "is whole as the customer says it" in wf["nodes"]["identity"]["task_message"]


def _ab_workflow(change=None):
    from fb.spec_extract import to_generator

    path = KIT.parent / "tests" / "hack" / "fixtures" / "spec_ab_extracted.json"
    raw = json.loads(path.read_text())
    if change:
        change(raw)
    return build(to_generator(raw), parse_openapi(OPENAPI))[0]


def _reply_kind(wf):
    schema = wf["nodes"]["negotiate"]["extraction_schema"]
    return next(v for v in schema if v["name"] == "reply_kind")["description"]


# stress G-05 / R28 on Freya, 11 Oct: two optional spec fields; empty, the A/B workflow is the
# same byte for byte
def test_restructure_also_and_answers_empty_change_nothing():
    base = json.dumps(_ab_workflow(), sort_keys=True)

    def empty(s):
        s.setdefault("neg", {})["restructure_also"] = []
        s["answers"] = []

    assert json.dumps(_ab_workflow(empty), sort_keys=True) == base


def test_restructure_also_reaches_the_reply_kind_and_not_the_engine():
    def also(s):
        s.setdefault("neg", {})["restructure_also"] = ["cancellation or renewal requests"]

    wf = _ab_workflow(also)
    assert "or makes one of these requests: cancellation or renewal requests" in _reply_kind(wf)
    assert "restructure_also" not in json.dumps(wf["nodes"]["decide"])


def test_answers_reach_the_negotiation_model():
    def answers(s):
        s["answers"] = [{"when": "whether the policy is renewed",
                         "say": "A promise is not a renewal.", "source": "x"}]

    task = _ab_workflow(answers)["nodes"]["negotiate"]["task_message"]
    want = 'If the customer asks whether the policy is renewed: "A promise is not a renewal."'
    assert want in task
