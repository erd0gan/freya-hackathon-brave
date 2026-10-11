"""A service contract that is a document, not an OpenAPI file: one model call writes the
OpenAPI, deterministic checks hold it to the contract's text, one repair round (audit 01
IC-4). The model is a fake here; no network."""

from __future__ import annotations

import copy
import json

from fb.contract import check_openapi, contract_to_openapi, describe
from fb.openapi_tools import KIT_TOOLS, parse_openapi
from fb.trace import Trace

CONTRACT = """# Servis Sözleşmesi (Lumen Bank)

Taban adres: https://bank.example/api/v1

| Servis | Metot | Yol | Alanlar |
|---|---|---|---|
| kimlikDogrula | POST | /kimlik/dogrula | musteri_no, soru (dogum_tarihi / tel_son4), cevap |
| hesapGetir | POST | /hesap/getir | musteri_no |
| sozKaydet | POST | /soz/kaydet | musteri_no, urun_no, tutar, tarih |
| notEkle | POST | /not/ekle | musteri_no, urun_no, neden (itiraz / zorluk) |
| geriArama | POST | /geri/arama | musteri_no, zaman |
| aktar | POST | /aktar | musteri_no, kuyruk (genel / yapilandirma) |
| kapat | POST | /kapat | musteri_no, sonuc (soz / sozsuz), neden |

Yanıtlar: basarili (boolean); kimlikDogrula ayrıca dogrulandi (boolean), kalan_hak
(integer, kalan deneme hakkı); hesapGetir urunler listesi: urun_no, borc (integer, kuruş
cinsinden), gecikme_gun.
"""


def op(path, opid, role, props, required=None, resp=None):
    schema = {"type": "object", "properties": props, "required": required or list(props)}
    return {
        path: {
            "post": {
                "operationId": opid,
                "x-kit-role": role,
                "requestBody": {"content": {"application/json": {"schema": schema}}},
                "responses": {
                    "200": {
                        "description": "ok",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": resp or {"basarili": {"type": "boolean"}},
                                }
                            }
                        },
                    }
                },
            }
        }
    }


S = {"type": "string"}
GOOD = {
    "openapi": "3.0.3",
    "servers": [{"url": "https://bank.example/api/v1"}],
    "paths": {
        **op(
            "/kimlik/dogrula",
            "kimlikDogrula",
            "verify_identity",
            {
                "musteri_no": S,
                "soru": {"type": "string", "enum": ["dogum_tarihi", "tel_son4"]},
                "cevap": S,
            },
            resp={
                "basarili": {"type": "boolean"},
                "dogrulandi": {"type": "boolean"},
                "kalan_hak": {"type": "integer", "description": "kalan deneme hakkı"},
            },
        ),
        **op(
            "/hesap/getir",
            "hesapGetir",
            "get_account",
            {"musteri_no": S},
            resp={
                "urunler": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "urun_no": S,
                            "borc": {"type": "integer", "description": "kuruş cinsinden"},
                            "gecikme_gun": {"type": "integer"},
                        },
                    },
                }
            },
        ),
        **op(
            "/soz/kaydet",
            "sozKaydet",
            "create_promise",
            {
                "musteri_no": S,
                "urun_no": S,
                "tutar": {"type": "number"},
                "tarih": {"type": "string", "format": "date"},
            },
        ),
        **op(
            "/not/ekle",
            "notEkle",
            "add_note",
            {
                "musteri_no": S,
                "urun_no": S,
                "neden": {"type": "string", "enum": ["itiraz", "zorluk"]},
            },
        ),
        **op(
            "/geri/arama",
            "geriArama",
            "schedule_callback",
            {"musteri_no": S, "zaman": {"type": "string", "format": "date-time"}},
        ),
        **op(
            "/aktar",
            "aktar",
            "transfer_to_agent",
            {"musteri_no": S, "kuyruk": {"type": "string", "enum": ["genel", "yapilandirma"]}},
        ),
        **op(
            "/kapat",
            "kapat",
            "close_call",
            {"musteri_no": S, "sonuc": {"type": "string", "enum": ["soz", "sozsuz"]}, "neden": S},
            required=["musteri_no", "sonuc"],
        ),
    },
}


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, trace, purpose, msgs, deadline=None):
        self.calls.append((purpose, copy.deepcopy(msgs)))
        trace.model("fake", purpose)
        r = self.replies.pop(0)
        return r if isinstance(r, str) else "```json\n" + json.dumps(r) + "\n```"


def parse(text):
    from fb.llm import parse_json

    return parse_json(text)


def test_a_good_reply_is_checked_and_used_with_one_call(tmp_path):
    fake = FakeLLM(GOOD)
    log = tmp_path / "t.log"
    doc, left = contract_to_openapi(CONTRACT, Trace(log, echo=False), chat_fn=fake, parse_fn=parse)
    assert left == [] and len(fake.calls) == 1
    assert "kimlikDogrula" in fake.calls[0][1][0]["content"]  # the contract is in the prompt
    tools = parse_openapi(doc, base_url="https://mock.example")
    assert set(tools) == set(KIT_TOOLS)
    assert tools["verify_identity"].api_name == "kimlikDogrula"
    assert tools["verify_identity"].url == "https://mock.example/api/v1/kimlik/dogrula"
    assert tools["get_account"].response_scale == {"urunler.borc": 0.01}
    # the one date-time callback field gets date/time parts the workflow binds
    assert [f.name for f in tools["schedule_callback"].fields][-2:] == ["date", "time"]
    text = log.read_text(encoding="utf-8")
    assert "purpose: turn the service contract into OpenAPI" in text
    assert "contract: OpenAPI written, 7 paths, 0 check problems" in text
    assert "POST /kimlik/dogrula -> verify_identity" in describe(doc)


def test_invented_names_go_back_once_and_the_repair_is_used(tmp_path):
    bad = copy.deepcopy(GOOD)
    media = bad["paths"]["/soz/kaydet"]["post"]["requestBody"]["content"]["application/json"]
    media["schema"]["properties"]["kanal"] = {"type": "string", "enum": ["telefon"]}
    bad["paths"]["/aktar"]["post"]["x-kit-role"] = None
    fake = FakeLLM(bad, GOOD)
    doc, left = contract_to_openapi(
        CONTRACT, Trace(tmp_path / "t.log", echo=False), chat_fn=fake, parse_fn=parse
    )
    assert left == [] and len(fake.calls) == 2
    repair = fake.calls[1][1][-1]["content"]
    assert "['kanal']" in repair and "['telefon']" in repair
    assert "transfer_to_agent" in repair  # the unmapped role
    assert doc == {**GOOD}


def test_problems_left_after_the_repair_are_returned_and_traced(tmp_path):
    bad = copy.deepcopy(GOOD)
    bad["paths"]["/uydurma"] = bad["paths"].pop("/aktar")
    fake = FakeLLM(bad, bad)
    log = tmp_path / "t.log"
    doc, left = contract_to_openapi(CONTRACT, Trace(log, echo=False), chat_fn=fake, parse_fn=parse)
    assert doc is not None and len(fake.calls) == 2
    assert any("/uydurma" in p for p in left)
    assert "contract check: path /uydurma: ['uydurma'] not in the contract" in log.read_text(
        encoding="utf-8"
    )


def test_a_reply_that_is_never_json_gives_none(tmp_path):
    fake = FakeLLM("I cannot do that.", "Still no JSON")
    doc, left = contract_to_openapi(
        CONTRACT, Trace(tmp_path / "t.log", echo=False), chat_fn=fake, parse_fn=parse
    )
    assert doc is None and left and "not JSON" in left[0]


def test_check_openapi_rules():
    assert check_openapi(GOOD, CONTRACT) == []
    assert check_openapi({"paths": {}}, CONTRACT) == [
        "the reply is not an OpenAPI document with paths"
    ]
    d = copy.deepcopy(GOOD)
    d["paths"]["/kapat"]["put"] = d["paths"]["/kapat"].pop("post")  # the contract says POST
    d["paths"]["/aktar"]["post"]["x-kit-role"] = "close_call"  # two operations, one role
    d["paths"]["/hesap/getir"]["post"]["requestBody"]["content"]["application/json"]["schema"][
        "required"
    ] = ["musteri_no", "tckn"]
    out = check_openapi(d, CONTRACT)
    assert any("PUT /kapat: the contract does not give PUT" in p for p in out)
    assert any("x-kit-role close_call is given to" in p for p in out)
    assert any("'tckn' is not a property" in p for p in out)
    assert any("roles with no operation: ['transfer_to_agent']" in p for p in out)
    # a role the contract has no service for, said so, is not a problem
    d = copy.deepcopy(GOOD)
    d["paths"].pop("/geri/arama")
    d["x-missing-roles"] = {"schedule_callback": "the contract has no callback service"}
    assert check_openapi(d, CONTRACT) == []
