"""Hidden-brief variants the public kit does not show (second round). Each test is one variant
a hidden brief or tool API can take and what the builder must do with it."""

import copy
import json
from pathlib import Path

from fb.generate import build
from fb.openapi_tools import parse_openapi
from fb.spec_extract import check, to_generator

from conftest import KIT

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
