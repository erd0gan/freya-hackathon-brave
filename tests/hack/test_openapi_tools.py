import copy
import json

import pytest
from fb.openapi_tools import (
    KIT_TOOLS,
    ToolApiError,
    ToolDef,
    ToolField,
    freya_tool,
    parse_openapi,
    request_hook,
    says_minor_units,
    wire_format,
)

from conftest import KIT

KIT_OA = json.loads((KIT / "tools" / "openapi.json").read_text())


def test_kit_openapi_parses_seven_tools():
    tools = parse_openapi(json.loads((KIT / "tools" / "openapi.json").read_text()))
    assert set(tools) == {
        "verify_identity",
        "get_account",
        "create_promise",
        "add_note",
        "schedule_callback",
        "transfer_to_agent",
        "close_call",
    }
    v = tools["verify_identity"]
    assert v.url == "https://freya-bank-mock.freyavoice.workers.dev/v1/verify_identity"
    assert v.field("field").enum == ["date_of_birth", "phone_last4"]
    assert "products" in tools["get_account"].response["properties"]


def test_freya_tool_bakes_and_exposes_fields():
    tools = parse_openapi(json.loads((KIT / "tools" / "openapi.json").read_text()))
    t = freya_tool(
        tools["transfer_to_agent"], "CALL it when ...", bake={"customer_id": "customer_id"}
    )
    body = t["function"]["parameters"]["properties"]["body"]
    assert body["properties"]["customer_id"] == {"type": "string", "default": "{{customer_id}}"}
    assert body["properties"]["queue"]["enum"] == ["restructuring", "general", "support"]
    assert body["required"] == ["queue"]  # baked fields are not model-required
    assert t["config"]["maxRetries"] == 0


def test_the_kit_file_parses_as_before():
    tools = parse_openapi(KIT_OA)
    assert all(t.api_name == t.name and not t.warnings for t in tools.values())
    assert all(not t.response_scale and not t.headers for t in tools.values())
    assert all(not f.part_of for t in tools.values() for f in t.fields)
    fn = freya_tool(tools["close_call"], "x")
    headers = fn["function"]["parameters"]["properties"]["headers"]["properties"]
    assert list(headers) == ["Content-Type"]


# ---------------------------------------------------------------- kit names (summary K, TA-5)
def renamed(names):
    """The kit's tool API with every operation moved to path names[tool] and, when the name
    has no slash, that as its operationId."""
    oa = copy.deepcopy(KIT_OA)
    paths = {}
    for p, item in oa["paths"].items():
        new = names[p.strip("/")]
        if "/" in new:
            paths[new] = item
        else:
            item["post"]["operationId"] = new
            paths["/" + new] = item
    oa["paths"] = paths
    return oa


def _camel(t, first_lower=True):
    words = t.split("_")
    return "".join(w if (i == 0 and first_lower) else w.capitalize() for i, w in enumerate(words))


@pytest.mark.parametrize(
    "names",
    [
        {t: _camel(t) for t in KIT_TOOLS},
        {t: "bank_" + t for t in KIT_TOOLS},
        {t: "api" + _camel(t, False) + "Tool" for t in KIT_TOOLS},
        {
            "verify_identity": "/identity/verify",
            "get_account": "/accounts/lookup",
            "create_promise": "/promises",
            "add_note": "/notes",
            "schedule_callback": "/callbacks",
            "transfer_to_agent": "/handoff",
            "close_call": "/calls/close",
        },
        {
            "verify_identity": "/kimlik/dogrula",
            "get_account": "/hesap/borc-sorgula",
            "create_promise": "/odeme-sozu",
            "add_note": "/not-ekle",
            "schedule_callback": "/geri-arama",
            "transfer_to_agent": "/temsilciye-aktar",
            "close_call": "/gorusme/kapat",
        },
    ],
    ids=["camelCase", "bank_prefix", "api_Tool", "paths", "turkish_paths"],
)
def test_operations_are_keyed_by_the_kit_name_whatever_they_are_called(names):
    tools = parse_openapi(renamed(names))
    assert set(tools) == set(KIT_TOOLS)
    for kit, t in tools.items():
        assert t.name == kit
        new = names[kit]
        assert t.api_name == (new.strip("/").split("/")[-1] if "/" in new else new)
        assert t.url.endswith(new if "/" in new else "/" + new)
    assert tools["create_promise"].field("amount") is not None


def test_turkish_summaries_name_the_role_when_the_names_do_not():
    oa = renamed({t: f"/op{i}" for i, t in enumerate(KIT_TOOLS)})
    summaries = {
        "/op0": "Müşteri kimlik doğrulama",
        "/op1": "Hesap ve borç bilgisi",
        "/op2": "Ödeme sözü kaydı",
        "/op3": "Not ekle",
        "/op4": "Geri arama randevusu",
        "/op5": "Müşteri temsilcisine aktar",
        "/op6": "Görüşmeyi kapat",
    }
    for p, s in summaries.items():
        oa["paths"][p]["post"]["summary"] = s
    tools = parse_openapi(oa)
    assert {k: t.api_name for k, t in tools.items()} == {
        t: f"op{i}" for i, t in enumerate(KIT_TOOLS)
    }


def test_an_explicit_x_kit_role_wins_and_unknown_tools_keep_their_name():
    oa = copy.deepcopy(KIT_OA)
    item = oa["paths"].pop("/close_call")
    item["post"]["operationId"] = "finalize"
    item["post"]["x-kit-role"] = "close_call"
    oa["paths"]["/finalize"] = item
    oa["paths"]["/health"] = {"get": {"operationId": "health", "responses": {}}}
    tools = parse_openapi(oa)
    assert tools["close_call"].api_name == "finalize"
    assert "health" in tools and tools["health"].api_name == "health"


# ---------------------------------------------------------------- mock_url (summary B, TA-1)
def test_mock_url_replaces_scheme_and_host_and_keeps_the_path_prefix():
    tools = parse_openapi(KIT_OA, base_url="https://my-mock.example.workers.dev")
    assert tools["verify_identity"].url == "https://my-mock.example.workers.dev/v1/verify_identity"
    tools = parse_openapi(KIT_OA, base_url="https://my-mock.example.workers.dev/")
    assert tools["get_account"].url == "https://my-mock.example.workers.dev/v1/get_account"
    # a mock URL with a path of its own: that path, not the contract's /v1
    tools = parse_openapi(KIT_OA, base_url="http://127.0.0.1:8787/bank")
    assert tools["close_call"].url == "http://127.0.0.1:8787/bank/close_call"


def test_no_server_and_no_mock_url_stops_relative_urls():
    oa = copy.deepcopy(KIT_OA)
    oa.pop("servers")
    with pytest.raises(ToolApiError, match="relative"):
        parse_openapi(oa)
    oa["servers"] = [{"url": "/v1"}]
    with pytest.raises(ToolApiError):
        parse_openapi(oa)
    tools = parse_openapi(oa, base_url="https://m.example")
    assert tools["add_note"].url == "https://m.example/v1/add_note"
    with pytest.raises(ToolApiError, match="not an http"):
        parse_openapi(oa, base_url="ftp://m.example")


def test_server_variables_and_path_level_servers():
    oa = copy.deepcopy(KIT_OA)
    oa["servers"] = [
        {
            "url": "https://{env}.bank.example/{ver}",
            "variables": {"env": {"default": "sandbox"}, "ver": {"default": "v2"}},
        }
    ]
    oa["paths"]["/add_note"]["servers"] = [{"url": "https://notes.bank.example/n"}]
    tools = parse_openapi(oa)
    assert tools["verify_identity"].url == "https://sandbox.bank.example/v2/verify_identity"
    assert tools["add_note"].url == "https://notes.bank.example/n/add_note"
    tools = parse_openapi(oa, base_url="https://mock.example")
    assert tools["add_note"].url == "https://mock.example/n/add_note"


# ---------------------------------------------------------------- schema reading (TA-6, TA-13)
def test_swagger2_body_parameters_and_host_are_read():
    ok = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    sw = {
        "swagger": "2.0",
        "host": "bank.example",
        "basePath": "/api",
        "schemes": ["https"],
        "paths": {
            "/create_promise": {
                "post": {
                    "parameters": [
                        {
                            "in": "body",
                            "name": "b",
                            "required": True,
                            "schema": {"$ref": "#/definitions/P"},
                        },
                        {"in": "query", "name": "trace", "type": "string"},
                    ],
                    "responses": {"200": {"schema": ok}},
                }
            }
        },
        "definitions": {
            "P": {
                "type": "object",
                "required": ["amount"],
                "properties": {"amount": {"type": "number"}, "date": {"type": "string"}},
            }
        },
    }
    t = parse_openapi(sw)["create_promise"]
    assert t.url == "https://bank.example/api/create_promise"
    assert [(f.name, f.required, f.location) for f in t.fields] == [
        ("amount", True, "body"),
        ("date", False, "body"),
        ("trace", False, "query"),
    ]
    assert "ok" in t.response_props


def test_refs_with_escapes_resolve_and_broken_refs_are_warned():
    oa = copy.deepcopy(KIT_OA)
    media = oa["paths"]["/add_note"]["post"]["requestBody"]["content"]["application/json"]
    oa.setdefault("components", {}).setdefault("schemas", {})["Note/Body x"] = media["schema"]
    media["schema"] = {"$ref": "#/components/schemas/Note~1Body%20x"}
    close = oa["paths"]["/close_call"]["post"]["requestBody"]["content"]["application/json"]
    close["schema"] = {"$ref": "schemas/close.json#/Close"}
    tools = parse_openapi(oa)
    assert tools["add_note"].field("reason") is not None
    assert any("no request field" in w for w in tools["close_call"].warnings)
    warns = [w for t in tools.values() for w in t.warnings]
    assert any("schemas/close.json#/Close" in w for w in warns)


def test_const_and_header_parameters_with_their_values():
    oa = copy.deepcopy(KIT_OA)
    op = oa["paths"]["/create_promise"]["post"]
    props = op["requestBody"]["content"]["application/json"]["schema"]["properties"]
    props["currency"] = {"const": "XYZ"}
    op["parameters"] = [
        {"in": "header", "name": "X-Channel", "schema": {"type": "string", "enum": ["voice"]}},
        {"in": "header", "name": "X-Session", "required": True, "schema": {"type": "string"}},
    ]
    t = parse_openapi(oa)["create_promise"]
    assert t.field("currency").enum == ["XYZ"]
    assert t.headers == {"X-Channel": "voice"}
    assert any("X-Session" in w for w in t.warnings)
    fn = freya_tool(t, "x")
    hdr = fn["function"]["parameters"]["properties"]["headers"]["properties"]
    assert hdr["X-Channel"] == {"type": "string", "default": "voice"}


# ---------------------------------------------------------------- minor units (summary I)
def test_response_amounts_in_minor_units_are_detected():
    oa = copy.deepcopy(KIT_OA)
    resp = oa["paths"]["/get_account"]["post"]["responses"]["200"]["content"]["application/json"]
    item = {
        "type": "object",
        "properties": {
            "past_due_cents": {"type": "integer"},
            "bakiye": {"type": "integer", "description": "Bakiye, kuruş cinsinden"},
            "asgari": {"type": "integer", "description": "Asgari tutar (kuruş)"},
            "limit": {"type": "integer", "description": "42050 means 420.50"},
            "fee": {"type": "number", "description": "TL, kuruş after the dot (12.50)"},
            "days_late": {"type": "integer"},
        },
    }
    resp["schema"] = {
        "type": "object",
        "properties": {
            "data": {
                "type": "object",
                "properties": {"items": {"type": "array", "items": item}},
            }
        },
    }
    t = parse_openapi(oa)["get_account"]
    assert t.response_scale == {
        "data.items.past_due_cents": 0.01,
        "data.items.bakiye": 0.01,
        "data.items.asgari": 0.01,
        "data.items.limit": 0.01,
    }


def test_request_amounts_minor_units_and_decimal_strings():
    def f(name="amount", **k):
        return ToolField(name, **k)

    assert wire_format(f(type="integer", description="Amount in pence."), "amount") == "cents"
    assert wire_format(f(type="integer", description="Tutar, kuruş"), "amount") == "cents"
    assert wire_format(f("tutar_kurus", type="integer"), "amount") == "cents"
    assert wire_format(f("amount_cents", type="integer"), "amount") == "cents"
    assert wire_format(f(type="number", description="TL, kuruş hassasiyetinde"), "amount") == ""
    assert wire_format(f(type="number"), "amount") == ""
    assert wire_format(f(type="string", pattern=r"^\d+\.\d{2}$"), "amount") == "dec2"
    assert says_minor_units("x", "integer", "Integer, smallest currency unit")


def run_hook(src, body, url="https://m.example/x"):
    config = {"body": body, "url": url}
    exec(compile(src, "hook", "exec"), {"config": config})
    return config


def test_decimal_string_amounts_are_sent_with_two_decimals():
    t = ToolDef(
        "create_promise", "https://m.example/p", "POST", "", [ToolField("amount", "string")]
    )
    src = request_hook(t, {"amount": "dec2"})
    assert run_hook(src, {"amount": 420.5})["body"] == {"amount": "420.50"}
    assert "}}" not in src


# ---------------------------------------------------------------- one date-time field (HA-12)
def callback_oa(field_name="callback_at", extra=None):
    oa = copy.deepcopy(KIT_OA)
    media = oa["paths"]["/schedule_callback"]["post"]["requestBody"]["content"]
    s = media["application/json"]["schema"]
    s["properties"] = {
        "customer_id": {"type": "string"},
        field_name: {"type": "string", "format": "date-time"},
        **(extra or {}),
    }
    s["required"] = ["customer_id", field_name]
    return oa


def test_a_single_datetime_callback_field_gets_date_and_time_parts():
    t = parse_openapi(callback_oa())["schedule_callback"]
    assert [(f.name, f.required, f.part_of) for f in t.fields] == [
        ("customer_id", True, ""),
        ("callback_at", False, ""),
        ("date", True, "callback_at"),
        ("time", False, "callback_at"),
    ]
    src = request_hook(t, {})
    assert "}}" not in src
    out = run_hook(src, {"customer_id": "c1", "date": "2026-10-16", "time": "9:30"})
    assert out["body"] == {"customer_id": "c1", "callback_at": "2026-10-16T09:30:00"}
    out = run_hook(src, {"customer_id": "c1", "date": "2026-10-16"})
    assert out["body"]["callback_at"] == "2026-10-16T00:00:00"
    # the spec bound the field itself as the date: still joined with the time part
    src = request_hook(t, {"callback_at": "datetime"})
    out = run_hook(src, {"callback_at": "2026-10-16", "time": "14:00"})
    assert out["body"] == {"callback_at": "2026-10-16T14:00:00"}
    # the workflow binds the parts; the joined field is not a leaf the step must fill
    fn = freya_tool(
        t,
        "x",
        bake={"customer_id": "customer_id", "date": "cb_date", "time": "cb_time"},
        model_filled=False,
    )
    props = fn["function"]["parameters"]["properties"]["body"]["properties"]
    assert set(props) == {"customer_id", "date", "time"}


def test_datetime_fields_a_role_binds_as_they_are_are_not_split():
    # 'date' as date-time with a 'time' beside it, or alone: the date role binds it as it is
    t = parse_openapi(callback_oa("date", {"time": {"type": "string"}}))["schedule_callback"]
    assert [f.name for f in t.fields] == ["customer_id", "date", "time"]
    t = parse_openapi(callback_oa("date"))["schedule_callback"]
    assert [f.name for f in t.fields] == ["customer_id", "date"]
    # a verify answer that is a date-time is never split (not a promise or callback)
    oa = copy.deepcopy(KIT_OA)
    media = oa["paths"]["/verify_identity"]["post"]["requestBody"]["content"]["application/json"]
    media["schema"]["properties"]["answer"]["format"] = "date-time"
    assert not any(f.part_of for f in parse_openapi(oa)["verify_identity"].fields)
