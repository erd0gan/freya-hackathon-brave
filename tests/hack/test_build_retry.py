"""build.py talks to Freya through bounded_api: a gateway error is tried again, a refusal is not
(11 Oct: a dry run got 504 and the build ended with nothing published)."""

import io
import json
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "builder"))
import build


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake(seq, calls):
    def urlopen(req, timeout=0):
        calls.append(timeout)
        code = seq.pop(0)
        if code == 200:
            return Resp(json.dumps({"data": {"result": {"valid": True}}}).encode())
        if code == "timeout":
            raise TimeoutError("timed out")
        raise urllib.error.HTTPError(req.full_url, code, "x", {}, io.BytesIO(b"error code"))

    return urlopen


def test_gateway_errors_are_tried_again(monkeypatch):
    calls = []
    monkeypatch.setattr(build.urllib.request, "urlopen", fake([504, "timeout", 200], calls))
    monkeypatch.setattr(build.time, "sleep", lambda s: None)
    api = build.bounded_api(time.monotonic() + 600)
    assert api("POST", "/api/v2/workflow", {"x": 1}, dryRun="true") == {"valid": True}
    assert len(calls) == 3


def test_a_refusal_is_not_tried_again(monkeypatch):
    calls = []
    monkeypatch.setattr(build.urllib.request, "urlopen", fake([422, 200], calls))
    monkeypatch.setattr(build.time, "sleep", lambda s: None)
    api = build.bounded_api(time.monotonic() + 600)
    try:
        api("POST", "/api/v2/workflow", {"x": 1})
        raise AssertionError("no error")
    except RuntimeError as e:
        assert "-> 422:" in str(e)
    assert len(calls) == 1


def test_retries_stop_at_the_budget(monkeypatch):
    calls = []
    monkeypatch.setattr(build.urllib.request, "urlopen", fake([504] * 50, calls))
    monkeypatch.setattr(build.time, "sleep", lambda s: None)
    api = build.bounded_api(time.monotonic() + 12)
    try:
        api("GET", "/x")
        raise AssertionError("no error")
    except RuntimeError as e:
        assert "no time left" in str(e)
    assert len(calls) == 1
