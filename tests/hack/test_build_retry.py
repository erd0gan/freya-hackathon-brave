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


def test_export_comes_before_the_smoke_test_and_the_smoke_test_has_its_own_clock(
    monkeypatch, tmp_path
):
    """11 Oct: the chat route answered 504 for 14 minutes and the smoke test's retries ran
    with the whole budget; the export after it would have had no time left."""
    from test_invariants_guard import run_build

    order, clocks = [], []

    def patch(b):
        real, export = b.bounded_api, b.freya.export_workflow

        def bounded(hard):
            clocks.append(hard - time.monotonic())
            return real(hard)

        def export_first(wid, path):
            order.append("export")
            return export(wid, path)

        def smoke(*a, **k):
            order.append("smoke")
            return "failed"

        monkeypatch.setattr(b, "bounded_api", bounded)
        monkeypatch.setattr(b.freya, "api", real(time.monotonic() + 600))
        monkeypatch.setattr(b.freya, "export_workflow", export_first)
        monkeypatch.setattr(b, "smoke", smoke)

    rc, _, out = run_build(monkeypatch, tmp_path, before_run=patch)
    assert rc == 0 and order == ["export", "smoke"] and out.exists()
    assert clocks and max(clocks) <= build.SMOKE_MAX_S + 1
