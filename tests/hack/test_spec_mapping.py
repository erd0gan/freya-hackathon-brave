"""The spec against the tool API: response fields, close reasons, band gaps, required request
fields and success flags (review findings on check()). Hidden tool APIs rename fields and enum
values; each mutation below is one such rename the spec did not follow. The unchanged kit and
Northwind APIs with their real extractions, and the hand-written SPEC_A / SPEC_B, must give no
problem: a false positive stops the build after the repair round."""

import copy
import json
from pathlib import Path

import pytest
from fb.openapi_tools import parse_openapi
from fb.spec_extract import bindings, check, close_reasons, responses, structure
from spec_fixtures import SPEC_A, SPEC_B

from conftest import KIT, ROOT

HERE = Path(__file__).parent
OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text())
BRIEF = (
    (KIT / "briefs" / "brief-a.md").read_text() + "\n" + (KIT / "briefs" / "brief-b.md").read_text()
)
SPEC = json.loads((HERE / "fixtures" / "spec_ab_extracted.json").read_text())
NW = ROOT / "tests" / "metamorphic" / "northwind"


def body(o, tool):
    return o["paths"]["/" + tool]["post"]["requestBody"]["content"]["application/json"]["schema"]


def ok200(o, tool):
    return o["paths"]["/" + tool]["post"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]


def problems(spec_change=None, api_change=None):
    s, o = copy.deepcopy(SPEC), copy.deepcopy(OPENAPI)
    if spec_change:
        spec_change(s)
    if api_change:
        api_change(o)
    return check(s, parse_openapi(o), BRIEF, o)


def as_extracted(gen):
    """SPEC_A / SPEC_B are generator input; the parts check() reads, in extractor form."""
    cfg = gen["cfg"]
    neg = cfg["neg"]
    return {
        "outcomes": gen["outcomes"],
        "identity": gen["identity"],
        "neg": neg,
        "bands": [{**b, "deadline_days": b["deadline_business_days"]} for b in cfg["bands"]],
        "product_fields": cfg.get("product_fields") or {},
        "floor_field": cfg["floor_field"],
        "product_names": cfg["product_names"],
        "tool_fields": gen.get("tool_fields") or {},
    }


# ---------------------------------------------------------------- no false positives
def test_kit_api_and_real_extraction_pass():
    assert problems() == []
    assert bindings(SPEC, parse_openapi(OPENAPI)) == []


def test_northwind_api_and_its_extraction_pass():
    o = json.loads((NW / "openapi.json").read_text(encoding="utf-8"))
    s = json.loads((HERE / "fixtures" / "spec_northwind_extracted.json").read_text("utf-8"))
    brief = (NW / "brief.md").read_text(encoding="utf-8")
    assert check(s, parse_openapi(o), brief, o) == []
    assert bindings(s, parse_openapi(o)) == []


@pytest.mark.parametrize("gen", [SPEC_A, SPEC_B], ids=["SPEC_A", "SPEC_B"])
def test_hand_written_specs_pass(gen):
    s, tools = as_extracted(gen), parse_openapi(OPENAPI)
    assert responses(s, tools, OPENAPI) == []
    assert close_reasons(s, tools) == []
    assert bindings(s, tools) == []
    assert not [p for p in structure(s) if p.startswith("bands")]


def test_unknown_response_schema_is_not_flagged():
    def no_schema(o):
        for tool in ("verify_identity", "get_account", "create_promise", "schedule_callback"):
            ok200(o, tool).pop("properties")

    assert problems(lambda s: s["product_fields"].update(past_due="nonexistent"), no_schema) == []


def test_ref_in_the_response_is_followed():
    def by_ref(o):
        o["components"]["schemas"] = {
            "Product": ok200(o, "get_account")["properties"]["products"]["items"]
        }
        ok200(o, "get_account")["properties"]["products"]["items"] = {
            "$ref": "#/components/schemas/Product"
        }

    assert problems(api_change=by_ref) == []
    out = problems(lambda s: s["product_fields"].update(past_due="nonexistent"), by_ref)
    assert any("product_fields.past_due" in p for p in out)
    # Without the file a nested $ref is unknown: nothing flagged.
    s = copy.deepcopy(SPEC)
    s["product_fields"]["past_due"] = "nonexistent"
    o = copy.deepcopy(OPENAPI)
    by_ref(o)
    assert responses(s, parse_openapi(o)) == []


# ---------------------------------------------------------------- response fields
def test_product_field_missing_from_the_response():
    out = problems(lambda s: s["product_fields"].update(past_due="nonexistent"))
    assert len(out) == 1 and out[0].startswith("product_fields.past_due: 'nonexistent'")


def test_product_field_renamed_by_the_api():
    def rename(o):
        items = ok200(o, "get_account")["properties"]["products"]["items"]["properties"]
        items["amount_overdue"] = items.pop("past_due")

    assert any("product_fields.past_due: 'past_due'" in p for p in problems(api_change=rename))
    assert problems(lambda s: s["product_fields"].update(past_due="amount_overdue"), rename) == []


def test_products_list_floor_field_and_name_placeholders():
    assert any(
        "product_fields.list" in p
        for p in problems(lambda s: s["product_fields"].update(list="items"))
    )
    assert any(
        "floor_field.credit_card" in p
        for p in problems(lambda s: s["floor_field"].update(credit_card="min_pay"))
    )
    out = problems(lambda s: s["product_names"].update(credit_card="credit card ending {last4}"))
    assert any("product_names.credit_card: {last4}" in p for p in out)


def test_verify_response_role_missing():
    out = problems(
        lambda s: s.update(
            tool_fields={"verify_identity": {"response": {"all_verified": "nonexistent"}}}
        )
    )
    assert len(out) == 1
    assert out[0].startswith("tool_fields.verify_identity.response.all_verified: 'nonexistent'")


def test_hint_only_when_the_spec_expects_one():
    def no_hint(o):
        ok200(o, "verify_identity")["properties"].pop("hint")

    assert any("response.hint" in p for p in problems(api_change=no_hint))
    assert problems(lambda s: s["identity"].update(swap_hint=""), no_hint) == []
    out = problems(lambda s: s["identity"].update(swap_hint="swapped"))
    assert any("identity.swap_hint 'swapped'" in p for p in out)


@pytest.mark.parametrize("tool", ["create_promise", "schedule_callback", "verify_identity"])
def test_success_flag_missing(tool):
    def no_ok(o):
        ok200(o, tool)["properties"]["success"] = ok200(o, tool)["properties"].pop("ok")

    out = problems(api_change=no_ok)
    assert any(p.startswith(f"tool_fields.{tool}.response.ok: 'ok'") for p in out), out
    named = problems(lambda s: s.update(tool_fields={tool: {"response": {"ok": "success"}}}), no_ok)
    assert named == []


# ---------------------------------------------------------------- close reasons
def reason_enum(values):
    def f(o):
        body(o, "close_call")["properties"]["reason"]["enum"] = list(values)

    return f


CLOSE_CODES = [
    "unclear", "callback", "no_callback", "wrong_number", "deceased", "failed", "declined",
    "restructuring", "human", "support", "refused", "hardship", "dispute", "service_error",
]  # fmt: skip


def test_close_reasons_valid_against_an_enum():
    assert problems(api_change=reason_enum(CLOSE_CODES)) == []


def test_outcome_reason_not_in_the_enum():
    codes = [c for c in CLOSE_CODES if c != "deceased"]
    out = problems(api_change=reason_enum(codes))
    assert out == [f"outcomes.deceased: reason 'deceased' is not a close_call reason {codes}"]


def test_transfer_reason_not_in_the_enum():
    out = problems(
        lambda s: s["neg"]["transfer_reasons"].update(human="agent"), reason_enum(CLOSE_CODES)
    )
    assert len(out) == 1 and out[0].startswith("neg.transfer_reasons.human: 'agent'")


def test_no_promise_reason_not_in_the_enum():
    codes = [c for c in CLOSE_CODES if c != "dispute"]
    # notes on and 'dispute' is a valid add_note reason: one value cannot meet both enums
    assert problems(api_change=reason_enum(codes)) == []
    out = problems(lambda s: s["neg"].update(notes=False), reason_enum(codes))
    assert len(out) == 1 and out[0].startswith("neg.reasons.dispute: 'dispute'")


def test_no_reason_enum_no_reason_check():
    assert problems(lambda s: s["neg"]["transfer_reasons"].update(human="agent")) == []


# ---------------------------------------------------------------- bands
def test_band_gap():
    out = problems(lambda s: s["bands"][1].update(min_days_late=9))
    assert out == [
        "bands 'early' and 'late' leave days_late 8 without a band (the next band's "
        "min_days_late must be the previous max_days_late + 1)"
    ]
    out = problems(lambda s: s["bands"][1].update(min_days_late=12))
    assert "days_late 8 to 11" in out[0]


# ---------------------------------------------------------------- required request fields
def rename_field(tool, old, new):
    def f(o):
        b = body(o, tool)
        b["properties"][new] = b["properties"].pop(old)
        b["required"] = [new if x == old else x for x in b["required"]]

    return f


def test_renamed_promise_amount_must_be_named():
    api = rename_field("create_promise", "amount", "pledge_value")
    out = problems(api_change=api)
    assert len(out) == 1
    assert "create_promise: required field(s) ['pledge_value']" in out[0]
    assert "tool_fields.create_promise.amount" in out[0]
    named = problems(
        lambda s: s.update(tool_fields={"create_promise": {"amount": "pledge_value"}}), api
    )
    assert named == []


@pytest.mark.parametrize(
    "tool,old,new,role",
    [
        ("verify_identity", "answer", "response_text", "answer"),
        ("verify_identity", "field", "kind", "field"),
        ("schedule_callback", "time", "slot", "time"),
        ("transfer_to_agent", "queue", "destination", "queue"),
        ("add_note", "product_id", "item", "product_id"),
        ("close_call", "outcome", "disposition", "outcome"),
    ],
)
def test_renamed_required_fields(tool, old, new, role):
    out = problems(api_change=rename_field(tool, old, new))
    assert any(p.startswith(f"{tool}: required field(s) ['{new}']") for p in out), out
    assert any(f"tool_fields.{tool}.{role}" in p for p in out)


def test_candidate_names_still_bind():
    # generate.find_field also takes a field containing a candidate word: no problem
    assert problems(api_change=rename_field("create_promise", "amount", "amount_eur")) == []
    assert problems(api_change=rename_field("transfer_to_agent", "queue", "team")) == []


def test_required_field_without_a_role_is_only_a_warning():
    def text_required(o):
        body(o, "add_note")["required"].append("text")

    assert problems(api_change=text_required) == []
    o = copy.deepcopy(OPENAPI)
    text_required(o)
    soft = bindings(SPEC, parse_openapi(o))
    assert len(soft) == 1 and soft[0][1] is False and "['text']" in soft[0][0]
