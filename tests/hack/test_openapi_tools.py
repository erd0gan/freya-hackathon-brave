import json

from fb.openapi_tools import freya_tool, parse_openapi

from conftest import KIT


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
