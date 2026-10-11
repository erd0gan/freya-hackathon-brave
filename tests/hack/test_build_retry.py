"""build.py talks to Freya through bounded_api: a gateway error is tried again, a refusal is not
(11 Oct: a dry run got 504 and the build ended with nothing published)."""

import io
import json
import re
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


def test_build_runs_the_engine_selftest_and_logs_it(monkeypatch, tmp_path):
    from test_invariants_guard import run_build

    rc, log, _ = run_build(monkeypatch, tmp_path)
    line = next(x for x in log.splitlines() if "engine selftest:" in x)
    assert rc == 0 and "engine selftest: passed" in line, line
    manifest = json.loads((tmp_path / "submission" / "x.manifest.json").read_text())
    assert manifest["selftest"]["state"] == "passed" and manifest["selftest"]["cases"] > 100


def test_a_crashing_selftest_is_only_a_note(monkeypatch, tmp_path):
    from test_invariants_guard import run_build

    def patch(b):
        def boom(*a, **k):
            raise ValueError("x")

        monkeypatch.setattr(b, "run_selftest", boom)

    rc, log, _ = run_build(monkeypatch, tmp_path, before_run=patch)
    assert rc == 0 and "engine selftest failed to run: ValueError" in log


# ---------------------------------------------------------------- the final's command (contract §7)
def test_command_forms():
    p = build.parse_args
    assert p(["b", "case/", "https://m.workers.dev"]) == (
        Path("case/"),
        None,
        "https://m.workers.dev",
    )
    assert p(["b", "brief.md", "openapi.json"]) == (Path("brief.md"), Path("openapi.json"), None)
    assert p(["b", "case.zip"]) == (Path("case.zip"), None, None)
    assert p(["b"]) is None and p(["b", "1", "2", "3"]) is None


def test_smoke_speaks_the_calls_language_and_an_empty_reply_fails(monkeypatch):
    from fb.trace import Trace

    sent = []

    def says(reply):
        def api(method, path, body=None, **k):
            if path.endswith("/message"):
                sent.append(body)
            return reply

        return api

    t = Trace(None, echo=False)
    monkeypatch.setattr(
        build.freya, "api", says({"response": "Merhaba Deniz Hanım, doğum tarihiniz?"})
    )
    assert build.smoke(t, "wf", {}, [], "Lumen Bank", "tr") == "passed"
    assert sent[-1]["message"] == "Alo, evet benim."
    assert sent[-1]["customerVariables"]["first_name"] == "Deniz"
    # a debt word before the identity check, whatever its suffix (borç -> borcunuz)
    monkeypatch.setattr(build.freya, "api", says({"response": "Borcunuz hakkında arıyorum."}))
    assert build.smoke(t, "wf", {}, [], "", "tr") == "failed"
    monkeypatch.setattr(build.freya, "api", says({"response": "Kredi kartınızla ilgili arıyorum."}))
    assert build.smoke(t, "wf", {}, ["kredi kartı"], "", "tr") == "failed"
    # LP-7: nothing said, or an error answered, is a failure in any language
    monkeypatch.setattr(build.freya, "api", says({"response": ""}))
    assert build.smoke(t, "wf", {}) == "failed"
    assert sent[-1]["message"] == "Hello?"
    monkeypatch.setattr(build.freya, "api", says({"response": "Hi", "error": "node crashed"}))
    assert build.smoke(t, "wf", {}) == "failed"


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_the_mock_serves_its_openapi_in_one_short_request(monkeypatch):
    from fb.trace import Trace

    seen = []
    oa = {"openapi": "3.0.0", "paths": {"/x": {}}}

    def urlopen(req, timeout=0):
        seen.append((req.full_url, timeout))
        return _Resp(json.dumps(oa).encode())

    monkeypatch.setattr(build.urllib.request, "urlopen", urlopen)
    t = Trace(None, echo=False)
    assert build.fetch_mock_openapi(t, "https://m.example/") == oa
    assert seen == [("https://m.example/openapi.json", build.MOCK_OPENAPI_TIMEOUT_S)]

    def down(req, timeout=0):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(build.urllib.request, "urlopen", down)
    assert build.fetch_mock_openapi(t, "https://m.example") is None


def _folder(tmp_path, contract_text=None, openapi=None):
    from fixtures.case_docs import case_folder

    d = case_folder(tmp_path / "Lumen-Kart", openapi=openapi)
    if contract_text:
        (d / "sozlesme.md").write_text(contract_text, encoding="utf-8")
    return d


def test_a_folder_with_a_document_contract_and_a_mock_url(monkeypatch, tmp_path):
    """No OpenAPI file, the mock serves none: the contract document becomes OpenAPI (one
    model call, here a fake), and every tool URL goes to the mock."""
    from fb import llm
    from fb.trace import Trace
    from test_contract import CONTRACT, GOOD, FakeLLM

    fake = FakeLLM(GOOD)
    monkeypatch.setattr(llm, "chat", fake)
    monkeypatch.setattr(build, "fetch_mock_openapi", lambda trace, url: None)
    log = tmp_path / "t.log"
    inp = build.read_inputs(
        Trace(log, echo=False), _folder(tmp_path, CONTRACT), None, "https://mock.example", None
    )
    assert len(fake.calls) == 1 and inp.api_source.startswith("the service contract")
    tools = build.parse_openapi(inp.openapi, base_url="https://mock.example")
    assert set(tools) == set(build.KIT_TOOLS)
    assert all(t.url.startswith("https://mock.example/api/v1/") for t in tools.values())
    assert inp.brief.startswith("## BRIEF.md (policy)")
    assert "Ayşe Demir" not in inp.brief and "Teşekkürler 1" not in inp.brief
    assert "Teşekkürler 1" in inp.examples
    text = log.read_text(encoding="utf-8")
    assert "case: read analiz.docx as analysis" in text and "case: UNREAD akis.png" in text
    assert "contract: POST /kimlik/dogrula -> verify_identity" in text


def test_main_on_a_folder_without_keys_stops_at_the_first_model_call(monkeypatch, tmp_path):
    """The final's command offline: the folder is read and the tool API looked for (mock
    refused, then the contract), and the build ends with a clear status at the model call."""
    b = __import__("test_invariants_guard").load_build()
    monkeypatch.setattr(b, "ROOT", tmp_path)
    for k in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)

    def refused(req, timeout=0):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(b.urllib.request, "urlopen", refused)
    case = _folder(tmp_path / "in")
    assert b.main(["build.py", str(case), "http://127.0.0.1:9"]) == 1
    log = (tmp_path / "traces" / "Lumen-Kart.log").read_text(encoding="utf-8")
    assert "mock_url=http://127.0.0.1:9" in log.splitlines()[0]
    assert "case: read BRIEF.md as policy" in log
    assert "tool API: GET http://127.0.0.1:9/openapi.json -> URLError" in log
    assert "the service contract" in log
    end = log.splitlines()[-1]
    assert "status=error: LLMError: no model provider key" in end and "model_calls=0" in end


def test_run_on_a_folder_with_an_openapi_file_and_a_mock_url(monkeypatch, tmp_path):
    """The whole build offline: OpenAPI found in the folder (no servers in it), every tool on
    the mock; the extractor gets the policy text with the examples apart, no customers."""
    import copy

    from fb.trace import Trace
    from test_invariants_guard import SPEC_AB, load_build

    from conftest import KIT

    b = load_build()
    oa = json.loads((KIT / "tools" / "openapi.json").read_text())
    oa.pop("servers")
    case = _folder(tmp_path / "in", openapi=oa)
    got = {}

    def extract(brief, openapi, tools, trace, deadline=None):
        got["brief"] = brief
        return copy.deepcopy(SPEC_AB)

    sent = []
    monkeypatch.setattr(b, "ROOT", tmp_path)
    monkeypatch.setattr(b, "extract", extract)
    monkeypatch.setattr(b, "extract_observer", None)
    monkeypatch.setattr(b.freya, "validate_workflow", lambda *a, **k: {})
    monkeypatch.setattr(
        b.freya,
        "save_workflow",
        lambda slot, wf, **k: sent.append(wf) or {"workflow_id": "w", "agent_id": "a"},
    )
    monkeypatch.setattr(
        b.freya,
        "export_workflow",
        lambda wid, path: Path(path).write_text(json.dumps({"workflow": sent[-1]})),
    )
    monkeypatch.setattr(b.freya, "api", lambda *a, **k: {"response": "Hello, this is the bank."})
    log = tmp_path / "t.log"
    rc = b.run(
        Trace(log, echo=False),
        case,
        None,
        "Lumen-Kart",
        time.monotonic() + 600,
        mock_url="https://mock.example",
    )
    text = log.read_text(encoding="utf-8")
    assert rc == 0, text.splitlines()[-1]
    urls = re.findall(r'"url": \{"type": "string", "default": "([^"]+)"', json.dumps(sent[-1]))
    # no servers in the file: no path prefix, the mock's host and the operation paths
    assert urls and all(re.fullmatch(r"https://mock\.example/[a-z_]+", u) for u in urls)
    assert "tool API: from openapi.json in the case; every tool URL on the mock" in text
    assert "# Example conversations (illustrations, not rules)" in got["brief"]
    assert got["brief"].index("## BRIEF.md") < got["brief"].index("# Example conversations")
    assert "Ayşe Demir" not in got["brief"]


def test_examples_go_as_a_keyword_when_the_extractor_takes_one(monkeypatch):
    got = {}

    def extract(brief, openapi, tools, trace, deadline=None, examples=""):
        got.update(brief=brief, examples=examples)
        return {}

    monkeypatch.setattr(build, "extract", extract)
    build.extract_spec("POLICY", {}, {}, None, examples="# Example conversations\nEX")
    assert got == {"brief": "POLICY", "examples": "# Example conversations\nEX"}


def test_no_server_and_no_mock_url_stops_the_build(monkeypatch, tmp_path):
    from fb.trace import Trace

    from conftest import KIT

    oa = json.loads((KIT / "tools" / "openapi.json").read_text())
    oa.pop("servers")
    api = tmp_path / "openapi.json"
    api.write_text(json.dumps(oa))
    b = __import__("test_invariants_guard").load_build()
    monkeypatch.setattr(b, "ROOT", tmp_path)
    log = tmp_path / "t.log"
    rc = b.run(
        Trace(log, echo=False), KIT / "briefs" / "brief-a.md", api, "x", time.monotonic() + 60
    )
    end = log.read_text(encoding="utf-8").splitlines()[-1]
    assert rc == 1 and "no usable tool API" in end and "relative" in end
