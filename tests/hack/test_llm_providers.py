"""A Claude model goes to Anthropic's API first; a failed attempt goes to OpenRouter. Without an
Anthropic key (or for a model that is not Claude) everything goes to OpenRouter."""

import io
import json
import urllib.error

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
