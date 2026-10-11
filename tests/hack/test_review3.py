"""Review of 11 Oct (8b1a9a6): a refusal read as 'already held', an object read as a success,
and an empty model reply taken as an answer."""

import copy
import io
import json

from fb import generate, llm
from fb.generate import build
from fb.openapi_tools import parse_openapi
from fb.trace import Trace
from spec_fixtures import SPEC_A

from conftest import KIT

OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text())


def held_hook(field="ok", mode="flag", err="error"):
    return generate.held_post(field, mode, err)


def run(code, result):
    exec(code, {"result": result})
    return result


def test_a_rejection_is_never_read_as_held():
    r = run(
        held_hook(),
        {
            "status_code": 400,
            "data": {"ok": False, "error": "Your request has already been rejected."},
        },
    )
    assert r["data"]["ok"] is False
    for text in (
        "promise already declined",
        "already cancelled",
        "already failed",
        "this call is already closed",
    ):
        r = run(held_hook(), {"status_code": 400, "data": {"ok": False, "error": text}})
        assert r["data"]["ok"] is False, text


def test_a_promise_the_bank_holds_is_read_as_held():
    for text in (
        "A promise is already recorded for this product",
        "callback already scheduled",
        "promise already exists",
    ):
        r = run(held_hook(), {"status_code": 400, "data": {"ok": False, "error": text}})
        assert r["data"]["ok"] is True, text


def test_held_sets_a_nested_success_field_where_it_is_read():
    r = run(
        held_hook("result.success", "flag", "result.error"),
        {
            "status_code": 409,
            "data": {"result": {"success": False, "error": "promise already recorded"}},
        },
    )
    assert r["data"]["result"]["success"] is True


def test_a_nested_success_flag_is_read_not_the_object():
    t = parse_openapi(OPENAPI)["create_promise"]
    t = copy.deepcopy(t)
    t.response_props = {
        "result": {
            "type": "object",
            "properties": {"success": {"type": "boolean"}, "promise_id": {"type": "string"}},
        }
    }
    field, mode = generate.success_field(t, "ok")
    assert (field, mode) == ("result.success", "flag")
    from fb.nodecode.embed import namespace

    call_ok = namespace()["call_ok"]
    assert call_ok("false", mode) is False and call_ok("true", mode) is True


def test_without_a_flag_an_id_is_used_never_a_status_word():
    t = copy.deepcopy(parse_openapi(OPENAPI)["create_promise"])
    t.response_props = {
        "result": {
            "type": "object",
            "properties": {"status": {"type": "string"}, "promise_id": {"type": "string"}},
        }
    }
    assert generate.success_field(t, "ok") == ("result.promise_id", "present")


def test_kit_workflow_success_fields_are_unchanged():
    wf, _ = build(copy.deepcopy(SPEC_A), parse_openapi(OPENAPI))
    maps = wf["nodes"]["record"]["pre_actions"][0].get("response_mapping") or wf["nodes"][
        "record"
    ].get("response_mapping")
    assert maps[0] == {"path": "data.ok", "variable": "rec_ok"}


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_an_empty_reply_goes_to_the_other_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key-123456")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-123456")
    monkeypatch.delenv("BUILDER_PROVIDER", raising=False)
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    seen = []
    replies = [{}, {"choices": [{"message": {"content": '{"a": 1}'}}], "usage": {}}]

    def urlopen(req, timeout=0):
        seen.append(req.full_url)
        return Resp(json.dumps(replies.pop(0)).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    out = llm.chat(Trace(tmp_path / "t.log", echo=False), "p", [{"role": "user", "content": "x"}])
    assert out == '{"a": 1}' and "anthropic.com" in seen[0] and "openrouter" in seen[1]
    assert "empty reply" in (tmp_path / "t.log").read_text()
