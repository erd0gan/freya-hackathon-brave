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
