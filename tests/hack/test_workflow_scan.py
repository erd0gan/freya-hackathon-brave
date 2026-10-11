"""Workflow bug hunt of 11 Oct: callback check, code errors after a record, person requests
before the identity check. Reproduced on the generated workflow's own code."""

import copy
import json

from fb.generate import build
from fb.openapi_tools import parse_openapi
from spec_fixtures import SPEC_A

from conftest import KIT

TOOLS = parse_openapi(json.loads((KIT / "tools" / "openapi.json").read_text()))
WF, _ = build(copy.deepcopy(SPEC_A), TOOLS)


def code(node):
    return WF["nodes"][node]["pre_actions"][0]["function"]["parameters"]["properties"]["code"][
        "default"
    ]


def cb(now, *turns):
    v = {"now": now}
    exec(code("setup_tp"), {"vars": v})
    for day, time in turns:
        v.update(cb_day_text=day, cb_time_text=time)
        exec(code("cb_check"), {"vars": v})
    return v["cb_state"], v.get("cb_date"), v.get("cb_time"), v


def test_day_said_inside_the_time_words():
    state, day, time, _ = cb("2026-10-14T09:30", ("", "tomorrow morning around eleven"))
    assert (state, day, time) == ("ok", "2026-10-15", "11:00")


def test_a_day_outside_the_callback_days_asks_for_another_day():
    state, _, _, v = cb("2026-10-14T11:00", ("Monday", ""))
    assert state == "outside" and "day is not possible" in v["cb_say"]


def test_a_person_who_never_says_when_ends_the_loop():
    *_, v = cb("2026-10-14T11:00", *[("", "")] * 8)
    assert int(v["cb_tries"]) > 100  # routes to fin_no_callback like a repeated 'outside'


def test_vague_day_counts_as_a_reask():
    state, _, _, v = cb("2026-10-14T11:00", ("next week", ""))
    assert state == "outside" and v["cb_tries"] == "1"


def test_code_error_after_a_record_reads_success_by_the_tools_field():
    v = {"neg_state": "null", "neg_action": "record", "rec_ok": "pr_1", "now": "2026-10-14"}
    from fb.nodecode import bodies
    from fb.nodecode.embed import snippet

    w = dict(v)
    exec(
        snippet(bodies.AFTER_RECORD, {"rec_mode": "present"}, on_error=bodies.NEG_ERROR),
        {"vars": w},
    )
    assert w["neg_action"] == "finish" and w["final_outcome"] == "promise"


def test_person_requests_before_the_identity_check_count():
    p = dict(json.loads((KIT / "public-test-set.json").read_text())[0]["products"][0])
    p["product_id"] = "p1"
    v = {"products": [p], "now": "2026-10-14T11:00", "pre_human_count": "1"}
    exec(code("facts"), {"vars": v})
    v.update(reply_kind="human", system_conversation=[])
    exec(code("decide"), {"vars": v})
    assert v["neg_action"] == "transfer", v.get("neg_say")


def close(state):
    cfg = {"body": {"customer_id": "c1", "outcome": "", "reason": ""}}
    v = {"neg_state": json.dumps(state)}
    exec(WF["tools"][0]["pre_process_code"], {"vars": v, "config": cfg})
    return cfg["body"].get("outcome"), cfg["body"].get("reason")


# a hang-up mid-call closes with the reason the products' states give (rules 37, 32, 40, 39)
def test_hang_up_outcome_follows_the_state():
    p = {"id": "p1", "n": 0}
    assert close({"p": [dict(p, status="failed")], "phase": "closing"}) == (
        "no_promise",
        "service_error",
    )
    assert close({"p": [dict(p, status="open")], "phase": "hardship_offer"}) == (
        "no_promise",
        "hardship",
    )
    assert close({"p": [dict(p, status="dispute")]}) == ("no_promise", "dispute")
    assert close({"p": [dict(p, status="paid")]}) == ("paid", None)
    assert close({"p": [dict(p, status="open")], "phase": "offer"}) == ("no_promise", "refused")
    assert close({"p": [dict(p, status="recorded")], "recorded": ["p1"]}) == ("promise", None)


VT = next(t for t in WF["nodes"]["identity"]["tools"] if t["function"]["name"] == "verify_identity")


def pre(answer, field="date_of_birth", v=None):
    cfg = {"body": {"customer_id": "c1", "field": field, "answer": answer}}
    v = {} if v is None else v
    exec(VT["pre_process_code"], {"vars": v, "config": cfg})
    return cfg["body"]["answer"], v


def test_a_day_number_is_not_a_lone_century():
    sent, _ = pre("1988-03-20", v={"id_last": "the 20th of March 1988"})
    assert sent == "1988-03-20"
    sent, _ = pre("1988-03-20", v={"id_last": "nineteen"})
    assert sent == "incomplete"  # the year in two parts: '19' ... '88'


def test_heard_words_are_used_once():
    _, v = pre("4410", "phone_last4", {"id_heard": "four four one zero", "id_last": "x"})
    assert v["id_heard"] == "" and v["id_last"] == ""
    sent, _ = pre("4410", "phone_last4", dict(v))
    assert sent == "4410"


# A15: after a refusal and a detour to 'a person', a second refusal still closes the call
def test_back_from_a_person_goes_to_where_the_request_came_from():
    src = WF["nodes"]["pre_human_back"]["pre_actions"][0]["function"]["parameters"]
    v = {"id2": "wants_human"}
    exec(src["properties"]["code"]["default"], {"vars": v})
    assert v["pre_human_from"] == "identity2"
    assert WF["nodes"]["pre_human_back"]["transitions"][0]["target_node"] == "identity2"
