import copy
import json

import pytest
from fb.openapi_tools import freya_tool, parse_openapi
from fb.validate import Policy, validate

from conftest import KIT

TOOLS = parse_openapi(json.loads((KIT / "tools" / "openapi.json").read_text()))
ALWAYS = {"operator": "always"}
VERIFIED = {
    "logic": "or",
    "conditions": [{"variable": "identity_state", "operator": "equals", "operand": "verified"}],
}


def code_node(name, nxt, src="try:\n    vars['x'] = 'y'\nexcept Exception:\n    pass\n"):
    return {
        "name": name,
        "node_type": "code",
        "passthrough": True,
        "passthrough_default_transition": nxt,
        "on_enter": {"type": "silent"},
        "pre_actions": [
            {
                "type": "function",
                "function": {
                    "name": "code_exec",
                    "handler": "__function__:code_exec",
                    "parameters": {
                        "type": "object",
                        "properties": {"code": {"type": "string", "default": src}},
                    },
                },
            }
        ],
        "transitions": [{"target_node": nxt, "description": "always", "condition": ALWAYS}],
    }


def end_node(name):
    return {
        "name": name,
        "node_type": "end_call",
        "on_enter": {"type": "silent"},
        "transitions": [],
        "terminal": {
            "type": "function",
            "function": {
                "name": name,
                "handler": "__function__:end_call",
                "parameters": {
                    "type": "object",
                    "properties": {"message_mode": {"type": "string", "default": "none"}},
                },
            },
        },
    }


def good():
    t = TOOLS
    return {
        "initial_node": "setup",
        "system_prompt": "You call for the bank.",
        "nodes": {
            "setup": code_node("setup", "identity"),
            "identity": {
                "name": "identity",
                "node_type": "conversation",
                "task_message": "Ask the security questions. CALL verify_identity for each answer.",
                "tools": [
                    freya_tool(
                        t["verify_identity"],
                        "CALL it for every answer.",
                        bake={"customer_id": "customer_id"},
                    )
                ],
                "max_node_turns": 12,
                "max_turn_transition": "end_unverified",
                "transitions": [
                    {"target_node": "account", "description": "verified", "condition": VERIFIED},
                    {
                        "target_node": "end_unverified",
                        "description": "failed",
                        "condition": {
                            "logic": "or",
                            "conditions": [
                                {
                                    "variable": "identity_state",
                                    "operator": "equals",
                                    "operand": "failed",
                                }
                            ],
                        },
                    },
                ],
            },
            "account": {
                "name": "account",
                "node_type": "api_request",
                "passthrough": True,
                "passthrough_default_transition": "talk",
                "on_enter": {"type": "silent"},
                "pre_actions": [
                    freya_tool(t["get_account"], "Get.", bake={"customer_id": "customer_id"})
                ],
                "response_mapping": [{"path": "data.products", "variable": "products"}],
                "transitions": [
                    {"target_node": "talk", "description": "always", "condition": ALWAYS}
                ],
            },
            "talk": {
                "name": "talk",
                "node_type": "conversation",
                "task_message": "Agree a payment. CALL create_promise after a clear yes.",
                "tools": [
                    freya_tool(
                        t["create_promise"],
                        "CALL after the read-back.",
                        bake={"customer_id": "customer_id"},
                        model_names={"amount": "promise_amount", "date": "promise_date"},
                    )
                ],
                "max_node_turns": 30,
                "max_turn_transition": "end_done",
                "transitions": [
                    {
                        "target_node": "end_done",
                        "description": "done",
                        "condition": {"operator": "matches_intent", "description": "goodbye"},
                    }
                ],
            },
            "end_unverified": end_node("end_unverified"),
            "end_done": end_node("end_done"),
        },
        "tools": [
            {
                **freya_tool(
                    t["close_call"],
                    "Record the outcome.",
                    bake={"customer_id": "customer_id", "outcome": "final_outcome"},
                    extra_config={"alwaysRunsAt": ["end_of_call"]},
                )
            }
        ],
    }


def test_good_workflow_is_clean():
    r = validate(good(), TOOLS)
    assert r.ok, [str(i) for i in r.errors]


def test_kit_base_agent_passes_in_export_mode():
    wf = json.loads((KIT / "examples" / "base-agent.json").read_text())
    r = validate(wf, TOOLS, Policy(exported=True, require_turn_caps=False))
    assert r.ok, [str(i) for i in r.errors]
    strict = validate(wf, TOOLS)
    assert {"null", "turn_cap"} <= strict.codes()


def mutate(fn):
    wf = good()
    fn(wf)
    return validate(wf, TOOLS)


N = "nodes"


@pytest.mark.parametrize(
    "code,fn",
    [
        ("null", lambda w: w[N]["talk"].__setitem__("role_message", None)),
        ("agent_config", lambda w: w.__setitem__("agent_config", {"x": 1})),
        ("initial_node", lambda w: w.__setitem__("initial_node", "nope")),
        ("tools_placement", lambda w: w[N]["account"].__setitem__("tools", w[N]["talk"]["tools"])),
        (
            "pre_actions_placement",
            lambda w: w[N]["talk"].__setitem__("pre_actions", w[N]["account"]["pre_actions"]),
        ),
        (
            "passthrough_always",
            lambda w: w[N]["account"]["transitions"][0].__setitem__("condition", VERIFIED),
        ),
        (
            "code_braces",
            lambda w: w[N]["setup"]["pre_actions"][0]["function"]["parameters"]["properties"][
                "code"
            ].__setitem__(
                "default", "try:\n    x = {'a': {'b': 1}}\nexcept Exception:\n    pass\n"
            ),
        ),
        (
            "code_syntax",
            lambda w: w[N]["setup"]["pre_actions"][0]["function"]["parameters"]["properties"][
                "code"
            ].__setitem__("default", "try:\n    x = (\nexcept Exception:\n    pass\n"),
        ),
        ("bad_target", lambda w: w[N]["talk"]["transitions"][0].__setitem__("target_node", "gone")),
        (
            "top_logic",
            lambda w: w[N]["identity"]["transitions"][0]["condition"].__setitem__("logic", "and"),
        ),
        ("turn_cap", lambda w: w[N]["talk"].pop("max_turn_transition")),
        (
            "dead_transition",
            lambda w: w[N]["identity"]["transitions"].insert(
                0, {"target_node": "account", "description": "x", "condition": ALWAYS}
            ),
        ),
        ("unreachable", lambda w: w[N].__setitem__("orphan", end_node("orphan"))),
        (
            "no_path_to_end",
            lambda w: (
                w[N]["talk"].__setitem__("transitions", []),
                w[N]["talk"].__setitem__("max_turn_transition", "talk"),
            ),
        ),
        (
            "gated_before_verify",
            lambda w: w[N]["identity"]["tools"].append(w[N]["talk"]["tools"][0]),
        ),
        (
            "gated_before_verify",
            lambda w: w[N]["identity"]["transitions"].insert(
                0,
                {
                    "target_node": "account",
                    "description": "x",
                    "condition": {
                        "operator": "matches_intent",
                        "description": "they want the balance",
                    },
                },
            ),
        ),
        ("close_lifecycle", lambda w: w.__setitem__("tools", [])),
        (
            "unknown_tool",
            lambda w: w[N]["talk"]["tools"][0]["function"].__setitem__("name", "send_sms"),
        ),
        (
            "foreign_host",
            lambda w: w[N]["talk"]["tools"][0]["function"]["parameters"]["properties"][
                "url"
            ].__setitem__("default", "https://evil.example.com/v1/create_promise"),
        ),
        (
            "required_field",
            lambda w: w[N]["talk"]["tools"][0]["function"]["parameters"]["properties"]["body"][
                "properties"
            ].pop("amount"),
        ),
        (
            "const",
            lambda w: w[N]["talk"]["tools"][0]["function"]["parameters"]["properties"][
                "url"
            ].__setitem__("const", "x"),
        ),
        (
            "enum_scope",
            lambda w: w["tools"][0]["function"]["parameters"]["properties"]["body"]["properties"][
                "outcome"
            ].__setitem__("default", "commitment"),
        ),
        (
            "param_outside_body",
            lambda w: w[N]["talk"]["tools"][0]["function"]["parameters"]["properties"].__setitem__(
                "amount", {"type": "number"}
            ),
        ),
        (
            "mapping_in_pre_action",
            lambda w: w[N]["account"]["pre_actions"][0].__setitem__(
                "response_mapping", [{"path": "data.products", "variable": "p"}]
            ),
        ),
        (
            "passthrough_cycle",
            lambda w: (
                w[N].__setitem__("loop", code_node("loop", "setup")),
                w[N]["setup"]["transitions"].insert(
                    0, {"target_node": "loop", "description": "x", "condition": VERIFIED}
                ),
            ),
        ),
        (
            "lifecycle_point",
            lambda w: w["tools"][0]["config"].__setitem__("alwaysRunsAt", ["each_turn"]),
        ),
        (
            "hook_handler",
            lambda w: w[N]["setup"]["pre_actions"][0].__setitem__(
                "post_process_code", "try:\n    pass\nexcept Exception:\n    pass\n"
            ),
        ),
    ],
)
def test_mutation_is_caught(code, fn):
    r = mutate(fn)
    assert code in {i.code for i in r.errors}, [str(i) for i in r.issues]


def test_bool_spelling_warns():
    wf = good()
    wf[N]["identity"]["transitions"][0]["condition"] = {
        "logic": "or",
        "conditions": [
            {
                "variable": "verify_identity_response.data.all_verified",
                "operator": "equals",
                "operand": "true",
            }
        ],
    }
    r = validate(wf, TOOLS)
    assert "bool_spelling" in r.codes()


def test_validator_never_mutates_input():
    wf = good()
    before = copy.deepcopy(wf)
    validate(wf, TOOLS)
    assert wf == before
