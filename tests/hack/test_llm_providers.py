"""A Claude model goes to Anthropic's API first; a failed attempt goes to OpenRouter. Without an
Anthropic key (or for a model that is not Claude) everything goes to OpenRouter."""

import io
import json
import urllib.error

import pytest
from fb import llm
from fb.trace import Trace, redact


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


OR_OK = {"choices": [{"message": {"content": "from openrouter"}}], "usage": {"prompt_tokens": 10}}
AN_OK = {
    "type": "message",
    "content": [{"type": "text", "text": "from anthropic"}],
    "usage": {"input_tokens": 1000, "output_tokens": 100},
}


def fake(seq, seen):
    def urlopen(req, timeout=0):
        seen.append(req)
        what = seq.pop(0)
        if isinstance(what, int):
            raise urllib.error.HTTPError(req.full_url, what, "x", {}, io.BytesIO(b"busy"))
        return Resp(json.dumps(what).encode())

    return urlopen


def keys(monkeypatch, openrouter=True, anthropic=True):
    for k in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "BUILDER_PROVIDER"):
        monkeypatch.delenv(k, raising=False)
    if openrouter:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-key-123456")
    if anthropic:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-key-123456")
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)


MSGS = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]


def test_openrouter_only_works_as_before(monkeypatch, tmp_path):
    keys(monkeypatch, anthropic=False)
    seen = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake([OR_OK], seen))
    assert llm.chat(Trace(tmp_path / "t.log", echo=False), "p", MSGS) == "from openrouter"
    assert seen[0].full_url.endswith("/chat/completions")


def test_anthropic_request_shape_and_trace(monkeypatch, tmp_path):
    keys(monkeypatch, openrouter=False)
    seen = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake([AN_OK], seen))
    tr = Trace(tmp_path / "t.log", echo=False)
    assert llm.chat(tr, "p", MSGS) == "from anthropic"
    req = seen[0]
    body = json.loads(req.data)
    assert req.full_url == "https://api.anthropic.com/v1/messages"
    assert req.get_header("X-api-key") == "sk-ant-test-key-123456"
    assert req.get_header("Anthropic-version") == "2023-06-01"
    assert body["model"] == "claude-opus-5-5" and body["system"] == "be brief"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    log = (tmp_path / "t.log").read_text()
    assert "anthropic:claude-opus-5-5" in log and "cost=$0.0060" in log


def test_a_claude_model_goes_to_anthropic_first(monkeypatch, tmp_path):
    keys(monkeypatch)
    seen = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake([AN_OK], seen))
    assert llm.chat(Trace(tmp_path / "t.log", echo=False), "p", MSGS) == "from anthropic"
    assert len(seen) == 1 and "anthropic.com" in seen[0].full_url


def test_a_failed_anthropic_attempt_goes_to_openrouter(monkeypatch, tmp_path):
    keys(monkeypatch)
    seen = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake([503, OR_OK], seen))
    assert llm.chat(Trace(tmp_path / "t.log", echo=False), "p", MSGS) == "from openrouter"
    assert "anthropic.com" in seen[0].full_url and "openrouter" in seen[1].full_url


def test_a_model_that_is_not_claude_goes_to_openrouter(monkeypatch, tmp_path):
    keys(monkeypatch)
    seen = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake([OR_OK], seen))
    tr = Trace(tmp_path / "t.log", echo=False)
    assert llm.chat(tr, "p", MSGS, model="google/gemini-3-pro") == "from openrouter"
    assert llm.configured("google/gemini-3-pro") == ["openrouter"]


def test_an_error_body_goes_to_the_other_provider(monkeypatch, tmp_path):
    keys(monkeypatch)
    seen = []
    err = {"type": "error", "error": {"type": "overloaded_error"}}
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake([err, OR_OK], seen))
    assert llm.chat(Trace(tmp_path / "t.log", echo=False), "p", MSGS) == "from openrouter"


def test_anthropic_requests_carry_no_temperature(monkeypatch, tmp_path):
    keys(monkeypatch, openrouter=False)
    seen = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake([AN_OK], seen))
    llm.chat(Trace(tmp_path / "t.log", echo=False), "p", MSGS, temperature=0.0)
    assert "temperature" not in json.loads(seen[0].data)


def test_an_openrouter_model_without_sampling_settings_is_asked_again(monkeypatch, tmp_path):
    keys(monkeypatch, anthropic=False)
    seen = []

    def urlopen(req, timeout=0):
        seen.append(json.loads(req.data))
        if len(seen) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 400, "x", {}, io.BytesIO(b'{"error":"temperature is deprecated"}')
            )
        return Resp(json.dumps(OR_OK).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    assert llm.chat(Trace(tmp_path / "t.log", echo=False), "p", MSGS) == "from openrouter"
    assert "temperature" in seen[0] and "temperature" not in seen[1]


def test_builder_provider_can_put_openrouter_first(monkeypatch):
    keys(monkeypatch)
    assert llm.configured() == ["anthropic", "openrouter"]
    monkeypatch.setenv("BUILDER_PROVIDER", "openrouter")
    assert llm.configured() == ["openrouter", "anthropic"]


def test_no_key_is_an_error(monkeypatch, tmp_path):
    keys(monkeypatch, openrouter=False, anthropic=False)
    try:
        llm.chat(Trace(tmp_path / "t.log", echo=False), "p", MSGS)
        raise AssertionError("no error")
    except llm.LLMError as e:
        assert "ANTHROPIC_API_KEY" in str(e)


def test_anthropic_key_is_redacted(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "abcdefgh12345678")
    assert "abcdefgh12345678" not in redact("key abcdefgh12345678 and sk-ant-api03-xyzxyzxyz")
    assert "sk-ant-api03" not in redact("sk-ant-api03-xyzxyzxyz")


def test_model_names():
    assert llm.anthropic_model("anthropic/claude-opus-5.5") == "claude-opus-5-5"
    assert llm.anthropic_model("claude-sonnet-5-5") == "claude-sonnet-5-5"


# ---------------------------------------------------------------- review 01 IC-3, 04 SX-5
BLOCKS = [
    {"type": "text", "text": "read this"},
    {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                    "data": "JVBERi0="}, "title": "duyuru.pdf"},
    {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBO"}},
]  # fmt: skip


def test_content_blocks_go_to_both_providers(monkeypatch, tmp_path):
    keys(monkeypatch)
    seen = []
    msgs = [
        {"role": "system", "content": [{"type": "text", "text": "be brief"}]},
        {"role": "user", "content": BLOCKS},
    ]
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake([503, OR_OK], seen))
    assert llm.chat(Trace(tmp_path / "t.log", echo=False), "p", msgs) == "from openrouter"
    an, op = json.loads(seen[0].data), json.loads(seen[1].data)
    assert an["system"] == "be brief" and an["messages"][0]["content"] == BLOCKS
    parts = op["messages"][1]["content"]
    assert parts[0] == {"type": "text", "text": "read this"}
    assert parts[1]["type"] == "file" and parts[1]["file"]["filename"] == "duyuru.pdf"
    assert parts[1]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert parts[2]["image_url"]["url"] == "data:image/png;base64,iVBO"
    assert op["messages"][0]["content"] == [{"type": "text", "text": "be brief"}]


def test_openrouter_content_parts_are_read_as_text(monkeypatch, tmp_path):
    keys(monkeypatch, anthropic=False)
    parts = {"choices": [{"message": {"content": [{"type": "text", "text": "a"},
                                                  {"type": "text", "text": "b"}]}}]}  # fmt: skip
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake([parts], []))
    assert llm.chat(Trace(tmp_path / "t.log", echo=False), "p", MSGS) == "a\nb"


def test_a_dropped_connection_is_tried_again(monkeypatch, tmp_path):
    import http.client

    keys(monkeypatch, anthropic=False)
    seen = []

    def urlopen(req, timeout=0):
        seen.append(req)
        if len(seen) == 1:
            raise http.client.IncompleteRead(b'{"choi')
        if len(seen) == 2:
            raise ConnectionResetError("reset by peer")
        return Resp(json.dumps(OR_OK).encode())

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    tr = Trace(tmp_path / "t.log", echo=False)
    assert llm.chat(tr, "p", MSGS) == "from openrouter" and len(seen) == 3
    log = (tmp_path / "t.log").read_text()
    assert "IncompleteRead" in log and "ConnectionResetError" in log


def test_a_reply_cut_at_max_tokens_is_asked_again_with_more_room(monkeypatch, tmp_path):
    keys(monkeypatch, openrouter=False)
    cut = {
        **AN_OK,
        "content": [{"type": "text", "text": '{"a": [1, 2'}],
        "stop_reason": "max_tokens",
    }
    seen = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake([cut, AN_OK], seen))
    assert llm.chat(Trace(tmp_path / "t.log", echo=False), "p", MSGS) == "from anthropic"
    assert [json.loads(r.data)["max_tokens"] for r in seen] == [16000, 32000]
    # nothing better comes: the cut reply is the answer (the caller's parser decides)
    seen = []
    monkeypatch.setattr(llm.urllib.request, "urlopen", fake([cut, cut, cut, cut], seen))
    assert llm.chat(Trace(tmp_path / "t.log", echo=False), "p", MSGS) == '{"a": [1, 2'


def test_back_off_waits_only_after_every_provider_failed(monkeypatch):
    waits = []
    monkeypatch.setattr(llm.time, "sleep", waits.append)
    for attempt in range(4):
        llm._pause(attempt, 2, None)
    assert waits == [3.0, 6.0]
    waits.clear()
    for attempt in range(3):
        llm._pause(attempt, 1, None)
    assert waits == [3.0, 6.0, 12.0]


@pytest.mark.parametrize(
    "reply,want",
    [
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Here: {{title}} is a variable.\n{"a": {"b": 2}}\nNote {x}.', {"a": {"b": 2}}),
        ('{"a": [1, 2,], // why\n "b": "x\ny", /* c */ "u": "https://h/x"}',
         {"a": [1, 2], "b": "x\ny", "u": "https://h/x"}),
        ('{"s": "a, }"}', {"s": "a, }"}),
        ('{"small": 1} and then {"big": {"x": 1, "y": 2}}', {"big": {"x": 1, "y": 2}}),
    ],
)  # fmt: skip
def test_parse_json_reads_imperfect_replies(reply, want):
    assert llm.parse_json(reply) == want


def test_parse_json_without_json_raises_value_error():
    with pytest.raises(ValueError):
        llm.parse_json("no json here")
