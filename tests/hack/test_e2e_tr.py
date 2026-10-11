"""The whole offline pipeline on a Turkish case, the way the private final runs it
(`python3 builder/build.py {case_folder} {mock_url}`): a folder of Turkish documents (BRIEF.md,
README.md, a Word analysis and announcement, an Excel sheet of test customers, a PDF of
questions and answers, a Markdown service contract with an OpenAPI block, example
conversations) -> case_input.load_case -> the tool API on the mock -> a hand-written correct
Turkish spec -> spec checks and grounding -> to_generator -> generate.build -> validate -> the
generated workflow walked node by node (its own code nodes, hooks, request bodies and
transitions) against the local bank for Turkish customers. Plus build.py's main on the folder
with no model key: it reads the folder and stops cleanly at the first model call.

Synthetic content only: a made-up "Lumen Bank" with values written for this test, never a real
case's or the final's. No network: the bank is fb.harness.bank, the model's half of a turn
(the extraction values and the tool arguments) is given by the script."""

# ruff: noqa: E501  (case documents written out as data)

from __future__ import annotations

import contextlib
import copy
import io
import json
import re
import urllib.error
import zipfile
from typing import Any

import pytest
from fb import langpack
from fb.case_input import load_case
from fb.generate import build
from fb.harness.bank import Bank, envelope, flatten
from fb.nodecode.embed import CODE_HARD_CAP
from fb.openapi_tools import parse_openapi
from fb.spec_extract import (
    blocking,
    check,
    detect_language,
    grounding,
    structure,
    to_generator,
)
from fb.validate import validate
from fixtures.case_docs import MC_NS, W_NS, _p, pdf_text, xlsx
from spec_fixtures import SPEC_EN

from conftest import KIT

MOCK = "https://lumen-mock.example.workers.dev"
KIT_TOOLS = (
    "verify_identity",
    "get_account",
    "create_promise",
    "add_note",
    "schedule_callback",
    "transfer_to_agent",
    "close_call",
)

# ---------------------------------------------------------------- the case documents
BRIEF = """# Lumen Bank kredi kartı ekstre borcu hattı (sentetik test vakası)

Bu klasör, Lumen Bank'ın gecikmiş kredi kartı ekstre borçları için müşteriyi arayan dijital asistanın kurallarını içerir. Önce bu dosyayı, sonra analiz belgesini okuyun. Duyurular, analizdeki değerleri yürürlük tarihlerinden itibaren değiştirir.

1. Görüşme Türkçe yapılır. Tarihler ve tutarlar Türkçe söylenir.
2. Asistan kimlik doğrulanmadan borçtan, tutardan, ödemeden veya karttan söz etmez.
"""

README = """# Belgeler

| Dosya | Ne |
|---|---|
| BRIEF.md | Önce bunu okuyun |
| analiz.docx | İş analizi: arama akışı ve kurallar |
| duyurular/ | Tarihli duyurular |
| sozlesme.md | Servis sözleşmesi (bankanın servisleri, OpenAPI) |
| test-musterileri.xlsx | Test müşterileri (mock için) |
| sss.pdf | Sıkça sorulan sorular |
| konusmalar/ | Örnek konuşmalar |
"""

ANALYSIS = [
    'Kural 1. Arama şu cümleyle başlar: "Merhaba, ben Lumen Bank\'ın dijital asistanıyım, bu görüşme kayıt altına alınmaktadır." Ardından müşterinin unvanı ve soyadıyla onunla mı görüşüldüğü sorulur. Kimlik doğrulanmadan borç, tutar veya ürün anılmaz.',
    "Kural 2. Kimlik: önce doğum tarihi, sonra kayıtlı telefon numarasının son dört hanesi sorulur. Toplam üç yanlış cevap kimlik kontrolünü bitirir ve görüşme kapatılır.",
    "Kural 3. Geri arama: bugün ya da bir sonraki iş günü, saat 09.30 ile 18.00 arasında (ikisi dahil).",
    "Kural 4. İş günleri pazartesiden cumaya kadardır. Resmi tatiller: 29 Ekim 2026 ve 1 Ocak 2027.",
    "Kural 5. Gecikme gününe göre bant, son tarih ve ikna denemesi aşağıdaki tablodadır.",
]
BAND_TABLE = [
    ["Gecikme günü", "Bant", "Son tarih", "İkna denemesi"],
    ["1-15", "erken", "bugünden sonraki 10. iş günü", "1"],
    ["16-60", "geç", "bugünden sonraki 5. iş günü", "2"],
]
ANALYSIS_TAIL = [
    "Kural 6. Önce borcun tamamının bugün ödenip ödenemeyeceği sorulur. İlk ikna denemesinde borcun tamamı haftanın son iş günü için önerilir; ikinci denemede asgari tutar son tarih için önerilir.",
    "Kural 7. Asgari ödeme tutarı dönem borcunun %30'udur. Tutarlar Türk lirası olarak söylenir (TL, TRY; lira ve kuruş).",
    "Kural 8. Müşteri zor durumdaysa (iş kaybı, hastalık, vefat) ikna yapılmaz, destek ekibine aktarım önerilir. Müşteri zaten ödediğini söylerse teşekkür edilir.",
    "Kural 9. Söz alınamayan her ürün için not düşülür; aktarımda not düşülmez.",
    "Kural 10. Kapanış kodları: promise, paid, transferred, no_promise, not_verified, not_reached. Neden kodları: refused, hardship, dispute, service_error, unclear, callback, no_callback, wrong_number, deceased, failed, declined, support, restructuring, human.",
]
ANNOUNCEMENT = [
    "Duyuru 2026/7, yürürlük tarihi 19 Ekim 2026.",
    "Bu tarihten itibaren geri arama bitişi 17.00 olur. Diğer kurallar değişmez.",
]
FAQ = [
    "S: Müşteri borcun kendisine ait olmadığını söylerse?",
    "C: İtiraz kaydı açılır (dispute), ikna yapılmaz.",
    "S: Müşteri bir temsilciyle görüşmek isterse?",
    "C: Önce yardım teklif edilir, ısrar ederse aktarılır.",
]
EXAMPLE = """Asistan: Merhaba, ben Lumen Bank'ın dijital asistanıyım, bu görüşme kayıt altına alınmaktadır.
Müşteri: Evet, benim.
Asistan: Devam etmeden önce iki güvenlik sorusu sormam gerekiyor.
Müşteri: Tamam, sorun.
"""

TR_OPS = {  # kit path -> (operationId, summary): a Turkish service contract's names
    "/verify_identity": ("kimlikDogrula", "Müşterinin bir güvenlik cevabını doğrular"),
    "/get_account": ("hesapGetir", "Gecikmiş ürünleri getirir"),
    "/create_promise": ("odemeSozuKaydet", "Bir ödeme sözü kaydeder"),
    "/add_note": ("notEkle", "Bir ürüne not ekler"),
    "/schedule_callback": ("geriAramaPlanla", "Geri arama planlar"),
    "/transfer_to_agent": ("temsilciyeAktar", "Görüşmeyi bir temsilciye aktarır"),
    "/close_call": ("aramayiKapat", "Görüşmenin sonucunu kaydeder"),
}


def tr_openapi() -> dict[str, Any]:
    """The kit's tool API shape (its field names, so the local bank can answer) under Turkish
    operation names and paths and a bank host that is not the mock."""
    oa = json.loads((KIT / "tools" / "openapi.json").read_text(encoding="utf-8"))
    oa["info"] = {"title": "Lumen Bank Servisleri", "version": "1.0"}
    oa["servers"] = [{"url": "https://api.lumen-bank.example/v1"}]
    paths = {}
    for path, item in oa["paths"].items():
        op_id, summary = TR_OPS[path]
        for op in item.values():
            if isinstance(op, dict):
                op["operationId"], op["summary"] = op_id, summary
        paths["/" + re.sub(r"(?<!^)(?=[A-Z])", "-", op_id).lower()] = item
    oa["paths"] = paths
    return oa


CONTRACT = (
    "# Servis Sözleşmesi\n\nLumen Bank çağrı servisleri. Aşağıdaki OpenAPI belgesi "
    "bağlayıcıdır; tüm istekler JSON gövdeyle POST edilir.\n\n```json\n{api}\n```\n"
)


def docx(paragraphs: list[str], table: list[list[str]] | None = None, tail=()) -> bytes:
    """A plain Word file: a heading, paragraphs, an optional table, more paragraphs, a header."""
    body = _p("Lumen Bank", "Heading1") + "".join(_p(t) for t in paragraphs)
    if table:
        body += (
            "<w:tbl>"
            + "".join(
                "<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in r) + "</w:tr>" for r in table
            )
            + "</w:tbl>"
        )
    body += "".join(_p(t) for t in tail)
    doc = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W_NS} {MC_NS}>'
        f"<w:body>{body}</w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        z.writestr("word/document.xml", doc)
    return out.getvalue()


def make_case(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "BRIEF.md").write_text(BRIEF, encoding="utf-8")
    (root / "README.md").write_text(README, encoding="utf-8")
    (root / "analiz.docx").write_bytes(docx(ANALYSIS, BAND_TABLE, ANALYSIS_TAIL))
    (root / "duyurular").mkdir(exist_ok=True)
    (root / "duyurular" / "duyuru-2026-10-19.docx").write_bytes(docx(ANNOUNCEMENT))
    api = json.dumps(tr_openapi(), ensure_ascii=False, indent=1)
    (root / "sozlesme.md").write_text(CONTRACT.format(api=api), encoding="utf-8")
    (root / "test-musterileri.xlsx").write_bytes(xlsx())
    (root / "sss.pdf").write_bytes(pdf_text(FAQ))
    (root / "konusmalar").mkdir(exist_ok=True)
    (root / "konusmalar" / "ornek-konusma-1.md").write_text(EXAMPLE, encoding="utf-8")
    return root


# ---------------------------------------------------------------- the correct spec
LINES = {
    "opening": "Merhaba, ben Lumen Bank'ın dijital asistanıyım, bu görüşme kayıt altına alınmaktadır.",
    "who_question": "{{title}} {{last_name}} ile mi görüşüyorum?",
    "ai_answer": "Ben Lumen Bank'ın dijital asistanıyım.",
    "scam_answer": "Bu aramayı Lumen Bank müşteri hizmetlerini arayarak doğrulayabilirsiniz.",
    "deceased": "Başınız sağ olsun.",
    "identity_intro": "Devam etmeden önce iki güvenlik sorusu sormam gerekiyor.",
    "identity_why": "Bu sorular hesabınızı korumak içindir.",
    "identity_failed": "Kimliğinizi doğrulayamadım. Lütfen şubemize başvurun.",
    "present_intro": "Gecikmiş bir ödeme hakkında arıyorum.",
    "today_question": "Bu ödemeyi bugün yapabilir misiniz?",
    "multi_today_question": "Hepsini bugün ödeyebilir misiniz?",
    "confirm_question": "Onaylıyor musunuz?",
    "recorded": "Teşekkür ederim, sözünüz kaydedildi.",
    "paid": "Bilgi verdiğiniz için teşekkür ederim.",
    "sympathy": "Bunu duyduğuma çok üzüldüm.",
    "support_offer": "Sizi destek ekibimize bağlamamı ister misiniz?",
    "help_first": "Sizi aktarmadan önce ben yardımcı olabilir miyim?",
    "callback_booked": "Teşekkürler, geri arama kaydedildi.",
    "goodbye": "Zaman ayırdığınız için teşekkür ederim. İyi günler.",
    "no_info": "Maalesef bu bilgiye sahip değilim.",
    "rest_overdue": "Kalan tutar gecikmede kalır.",
}


def lumen_spec() -> dict[str, Any]:
    """What a correct reading of the case gives (the public extraction's shape, this case's
    values)."""
    s = copy.deepcopy(SPEC_EN)
    s["bank_name"] = "Lumen Bank"
    s["language"] = "tr"
    s["notes"] = ["Duyuru 2026/7 is dated: its value is in dated_changes"]
    s["lines"] = dict(LINES)
    s["forbidden_before_verify"] = ["borç", "tutar", "ödeme", "kart"]
    s["identity"]["questions"] = [
        {"field": "date_of_birth", "ask": "Doğum tarihinizi söyler misiniz?", "kind": "date"},
        {
            "field": "phone_last4",
            "ask": "Kayıtlı telefon numaranızın son dört hanesi nedir?",
            "kind": "digits",
        },
    ]
    s["identity"]["swap_instruction"] = "Doğum tarihinizi ayı yazıyla söyler misiniz?"
    s["identity"]["max_wrong_answers"] = 3
    s["callback"].update(start="09:30", end="18:00")
    s["calendar"] = {
        "holidays": ["2026-10-29", "2027-01-01"],
        "workdays": [0, 1, 2, 3, 4],
        "week_start": None,
    }
    s["bands"][0].update(name="erken", min_days_late=1, max_days_late=15, deadline_days=10)
    s["bands"][0]["attempts"] = 1
    s["bands"][1].update(name="geç", min_days_late=16, max_days_late=60, deadline_days=5)
    s["bands"][1]["attempts"] = 2
    s["product_names"] = {
        "credit_card": "{card_last4} ile biten kredi kartı",
        "personal_loan": "ihtiyaç kredisi",
        "car_loan": "taşıt kredisi",
    }
    s["currency"] = {"code": "TRY", "symbol": "TL", "word": "lira", "minor_word": "kuruş"}
    s["floor_rule"] = {"percent_of": "past_due", "percent": 30}
    s["dated_changes"] = [
        {
            "effective_from": "2026-10-19",
            "path": "callback.end",
            "value": "17:00",
            "source": "Bu tarihten itibaren geri arama bitişi 17.00 olur",
        }
    ]
    return s


# ---------------------------------------------------------------- the pipeline, once
class Pipe:
    pass


@pytest.fixture(scope="module")
def pipe(tmp_path_factory):
    p = Pipe()
    p.folder = make_case(tmp_path_factory.mktemp("case") / "Lumen-Kart-Borcu")
    p.case = load_case(p.folder)
    p.tools = parse_openapi(copy.deepcopy(p.case.openapi), base_url=MOCK)
    p.spec = lumen_spec()
    p.gen = to_generator(p.spec)
    p.wf, p.policy = build(p.gen, p.tools)
    return p


# ---------------------------------------------------------------- 1. the folder
def test_the_folder_is_read_with_each_document_in_its_role(pipe):
    case = pipe.case
    roles = {d.name: d.role for d in case.documents}
    assert roles.pop("analiz.docx") in ("analysis", "flow")  # both policy text (see below)
    assert roles == {
        "BRIEF.md": "policy",
        "README.md": "readme",
        "duyurular/duyuru-2026-10-19.docx": "announcement",
        "sozlesme.md": "contract",
        "test-musterileri.xlsx": "test_customers",
        "sss.pdf": "faq",
        "konusmalar/ornek-konusma-1.md": "example_conversation",
    }, roles
    assert not case.unread(), [(d.name, d.error) for d in case.unread()]
    assert case.openapi_source == "sozlesme.md" and case.openapi["paths"]
    pol = case.policy_text
    assert pol.startswith("## BRIEF.md (policy)")
    # every document's rules reach the policy text: Word paragraphs and table, PDF, dated note
    for want in (
        "Toplam üç yanlış cevap",
        "09.30 ile 18.00",
        "16-60",
        "%30",
        "geri arama bitişi 17.00",
        "dated 2026-10-19",
        "İtiraz kaydı açılır",
    ):
        assert want in pol, want
    # customers and examples stay out of the policy; the contract's JSON too
    assert case.customers and "Ayşe Demir" not in pol and "Can Yıldız" not in pol
    assert "Tamam, sorun." in case.examples_text and "Tamam, sorun." not in pol
    assert "kimlikDogrula" not in pol
    assert detect_language(pol) == ("tr", True)


@pytest.mark.xfail(
    strict=True,
    reason="case_input._ROLE_PATTERNS tries 'flow' (\\bakis) before 'analysis': a README row "
    "'İş analizi: arama akışı ve kurallar' makes the analysis a flow document (label only: "
    "both go to the policy text)",
)
def test_a_readme_row_naming_an_analysis_of_the_call_flow_is_an_analysis(pipe):
    assert {d.name: d.role for d in pipe.case.documents}["analiz.docx"] == "analysis"


# ---------------------------------------------------------------- 2. tools on the mock
def test_tools_are_the_kit_roles_on_the_mock(pipe):
    assert set(pipe.tools) == set(KIT_TOOLS)
    for name, t in pipe.tools.items():
        assert t.url.startswith(MOCK + "/v1/"), (name, t.url)
        assert "lumen-bank.example" not in t.url
    assert pipe.tools["verify_identity"].api_name == "kimlikDogrula"
    assert pipe.tools["create_promise"].api_name == "odemeSozuKaydet"


# ---------------------------------------------------------------- 3. spec checks
def test_the_correct_turkish_spec_passes_every_check(pipe):
    pol, ex = pipe.case.policy_text, pipe.case.examples_text
    assert check(pipe.spec, pipe.tools, pol, pipe.case.openapi) == []
    assert structure(pipe.spec) == []
    assert blocking(pipe.spec) == []
    assert grounding(pipe.spec, pol, ex) == []


def test_to_generator_carries_the_language_currency_and_dated_change(pipe):
    gen = pipe.gen
    assert gen["language"] == "tr" and gen["cfg"]["lang"] == "tr"
    fmt = gen["cfg"]["fmt"]
    assert fmt["months"][9] == "Ekim" and fmt["decimal"] == ","
    assert gen["cfg"]["floor_rule"] == {"percent_of": "past_due", "percent": 30}
    assert gen["cfg"]["dated"] == [{"from": "2026-10-19", "set": {"callback.end": "17:00"}}]
    assert gen["identity"]["max_wrong_answers"] == 3
    assert set(langpack.engine_lines("tr")) <= set(gen["cfg"]["neg"]["lines"])


# ---------------------------------------------------------------- 4. the workflow
def code_of(n: dict[str, Any]) -> str:
    return n["pre_actions"][0]["function"]["parameters"]["properties"]["code"]["default"]


def requests_of(wf: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """(where, api_call function) of every request the workflow can send."""
    out = []
    for k, n in wf["nodes"].items():
        for a in [*(n.get("pre_actions") or []), *(n.get("tools") or [])]:
            if (a.get("function") or {}).get("handler") == "__function__:api_call":
                out.append((k, a))
    out += [("tools", t) for t in wf.get("tools") or []]
    return out


def test_the_workflow_validates_on_the_mock_within_the_code_cap(pipe):
    wf, tools = pipe.wf, pipe.tools
    rep = validate(wf, tools, pipe.policy)
    assert rep.ok and not rep.errors, [str(i) for i in rep.errors]
    reqs = requests_of(wf)
    assert {a["function"]["name"] for _, a in reqs} == set(KIT_TOOLS)
    for where, a in reqs:
        url = a["function"]["parameters"]["properties"]["url"]["default"]
        assert url.startswith(MOCK + "/"), (where, url)
        assert url == tools[a["function"]["name"]].url
    for k, n in wf["nodes"].items():
        srcs = [code_of(n)] if n.get("node_type") == "code" else []
        for a in [*(n.get("pre_actions") or []), *(n.get("tools") or [])]:
            srcs += [a[h] for h in ("pre_process_code", "post_process_code") if a.get(h)]
        for src in srcs:
            assert len(src.encode("utf-8")) <= CODE_HARD_CAP, (k, len(src.encode("utf-8")))
    for t in wf.get("tools") or []:
        for h in ("pre_process_code", "post_process_code"):
            if t.get(h):
                assert len(t[h].encode("utf-8")) <= CODE_HARD_CAP


# ---------------------------------------------------------------- 5. walking the workflow
def holds(cond: dict[str, Any], v: dict[str, Any]) -> bool:
    if cond.get("operator") == "always":
        return True
    if "conditions" in cond:
        got = [holds(c, v) for c in cond["conditions"]]
        return any(got) if cond.get("logic", "and") == "or" else all(got)
    x = v.get(cond["variable"])
    if cond["operator"] == "equals":
        return x is not None and str(x) == str(cond["operand"])
    if cond["operator"] == "greater_than":
        try:
            return float(x) > float(cond["operand"])
        except (TypeError, ValueError):
            return False
    raise AssertionError(f"operator {cond['operator']!r} not simulated")


def fill(text: Any, v: dict[str, Any]) -> str:
    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", lambda m: str(v.get(m.group(1), "")), str(text))


def quoted(text: str) -> list[str]:
    """The sentences an instruction tells the model to say word for word."""
    return re.findall(r'(?:exactly|Say|say):\s*"([^"]+)"', text)


class Walk:
    """The generated workflow run as the platform runs it, with the model's half given by the
    script: each customer turn names the step it answers, the customer's words, the extraction
    values and (in the identity step) the verify_identity arguments the model sends."""

    def __init__(self, pipe: Pipe, case: dict[str, Any]) -> None:
        self.wf, self.tools = pipe.wf, pipe.tools
        self.bank = Bank()
        self.v: dict[str, Any] = dict(self.bank.open_session(case))
        self.cid = self.v["customer_id"]
        self.v["system.conversation"] = []
        self.said: list[str] = []
        self.path: list[str] = []
        self.errors: list[str] = []
        self.sent: list[tuple[str, str, dict[str, Any]]] = []
        self.entered: set[str] = set()

    # the agent's voice
    def say(self, text: str) -> None:
        text = text.strip()
        if text:
            self.said.append(text)
            self.v["system.conversation"].append({"role": "assistant", "content": text})

    # requests
    def request(self, fn: dict[str, Any], hook_pre: str = "", hook_post: str = "", args=None):
        props = fn["parameters"]["properties"]
        name, url = fn["name"], props["url"]["default"]
        body = {}
        for k, s in (props.get("body") or {}).get("properties", {}).items():
            body[k] = (args or {}).get(k, fill(s.get("default", ""), self.v))
            if s.get("type") == "number" and isinstance(body[k], str):
                with contextlib.suppress(ValueError):
                    body[k] = float(body[k])
        if hook_pre:
            cfg = {"body": body}
            exec(hook_pre, {"vars": self.v, "config": cfg})
            body = cfg["body"]
        assert url == self.tools[name].url and url.startswith(MOCK + "/"), url
        assert not any("{{" in str(x) for x in body.values()), (name, body)
        self.sent.append((name, url, copy.deepcopy(body)))
        st, out = self.bank.call(self.cid, name, body)
        result = envelope(st, copy.deepcopy(out))
        if hook_post:
            exec(hook_post, {"vars": self.v, "result": result})
        return st, result

    def run_code(self, key: str, n: dict[str, Any]) -> None:
        self.v.pop("code_error", None)
        exec(code_of(n), {"vars": self.v})
        if self.v.get("code_error"):
            self.errors.append(f"{key}: {self.v['code_error']}")

    def route(self, n: dict[str, Any]) -> str | None:
        for t in n.get("transitions") or []:
            if holds(t["condition"], self.v):
                return t["target_node"]
        return None

    def enter(self, key: str, n: dict[str, Any]) -> None:
        oe = n.get("on_enter") or {}
        first = key not in self.entered
        self.entered.add(key)
        if n.get("on_enter_first_entry_only") and not first:
            return
        if oe.get("type") == "tts":
            self.say(fill(oe.get("text", ""), self.v))
        elif oe.get("type") == "llm":
            for s in quoted(fill(n.get("task_message", ""), self.v)):
                self.say(s)

    def turn(self, key: str, n: dict[str, Any], t: dict[str, Any]) -> None:
        assert t.get("node", key) == key, (t, key, self.path)
        self.v["system.conversation"].append({"role": "user", "content": t.get("text", "")})
        for x in n.get("extraction_schema") or []:
            enum = x.get("enum_values") or []
            self.v[x["name"]] = t.get("x", {}).get(x["name"], "no" if "no" in enum else "")
        if "call" in t:
            tool = next(a for a in n["tools"] if a["function"]["name"] == t["call"][0])
            _, result = self.request(
                tool["function"],
                tool.get("pre_process_code", ""),
                tool.get("post_process_code", ""),
                t["call"][1],
            )
            self.v.update(flatten(tool["function"]["name"] + "_response", result))
            todo = str((result.get("data") or {}).get("what_to_do_next") or "")
            for s in quoted(todo):
                self.say(s)

    def run(self, script: list[dict[str, Any]], limit: int = 120) -> Walk:
        script = list(script)
        key, turns = self.wf["initial_node"], 0
        for _ in range(limit):
            n = self.wf["nodes"][key]
            self.path.append(key)
            kind = n.get("node_type")
            if kind == "end_call":
                props = n["terminal"]["function"]["parameters"]["properties"]
                self.say(fill(props.get("spoken_message", {}).get("default", ""), self.v))
                for t in self.wf.get("tools") or []:
                    if "end_of_call" in (t.get("config") or {}).get("alwaysRunsAt", []):
                        self.request(t["function"], t.get("pre_process_code", ""))
                return self
            if kind == "code":
                self.run_code(key, n)
                nxt = self.route(n) or n.get("passthrough_default_transition")
            elif kind == "api_request":
                a = n["pre_actions"][0]
                st, result = self.request(
                    a["function"], a.get("pre_process_code", ""), a.get("post_process_code", "")
                )
                ok = "success" if 200 <= st < 300 else "failure"
                c = f"{key}_{ok}_count"
                self.v[c] = str(int(self.v.get(c) or 0) + 1)
                for m in n.get("response_mapping") or []:
                    x: Any = result
                    for part in m["path"].split("."):
                        x = x.get(part) if isinstance(x, dict) else None
                    self.v[m["variable"]] = (
                        ("true" if x else "false")
                        if isinstance(x, bool)
                        else ("" if x is None else x)
                    )
                nxt = self.route(n) or n.get("passthrough_default_transition")
            else:  # a conversation step: the agent speaks, the customer answers
                if key != (self.path[-2] if len(self.path) > 1 else None):
                    self.enter(key, n)
                    turns = 0
                assert script, f"the script ran out at {key}; said so far: {self.said[-3:]}"
                self.turn(key, n, script.pop(0))
                turns += 1
                nxt = self.route(n)
                if nxt is None and n.get("max_node_turns") and turns >= n["max_node_turns"]:
                    nxt = n.get("max_turn_transition")
                nxt = nxt or key
            assert nxt in self.wf["nodes"], (key, nxt)
            key = nxt
        raise AssertionError(f"no end after {limit} steps: {self.path[-12:]}")

    def ledger(self) -> dict[str, Any]:
        return self.bank.ledger(self.cid)


# English words that never appear in a Turkish sentence
ENGLISH = re.compile(
    r"\b(the|you|your|are|have|we|pay|could|confirm|sorry|minimum|latest|record|recorded|"
    r"promise|today|goodbye|thank|thanks|please|which|day|cannot|connect|noted|speaking|"
    r"continue|transfer|digits|year|amount|date|of|and|is|to|for|by|with)\b",  # not 'on': ten
    re.I,
)
EN_NAMES = re.compile(
    r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday|January|February|March|"
    r"April|June|July|August|September|October|November|December)\b"
)
ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
EN_MONEY = re.compile(r"\b\d{1,3}(,\d{3})+(\.\d+)?\b|\b\d+\.\d{2}\b")


def assert_turkish(said: list[str]) -> None:
    assert said, "nothing was said"
    for s in said:
        s2 = re.sub(r"\{\{\w+\}\}", "", s)
        assert not ENGLISH.search(s2), s
        assert not EN_NAMES.search(s2), s
        assert not ISO.search(s2), s
        assert not EN_MONEY.search(s2), s


NOW = "2026-10-14T11:00"  # a Wednesday
PAST_DUE = 2450.5
FLOOR = 735.15  # 30% of the statement debt (Kural 7); the account gives no minimum


def customer(cid: str, days_late: int = 9, now: str = NOW) -> dict[str, Any]:
    return {
        "id": cid,
        "variables": {"first_name": "Deniz", "last_name": "Aksoy", "title": "Sayın", "now": now},
        "identity": {"date_of_birth": "1988-03-14", "phone_last4": "4471"},
        "products": [
            {
                "type": "credit_card",
                "card_last4": "5521",
                "past_due": PAST_DUE,
                "days_late": days_late,
            }
        ],
    }


HELLO = {"node": "opening", "text": "Evet, benim.", "x": {"who1": "confirms"}}


def ident(field: str, said: str, answer: str) -> dict[str, Any]:
    return {
        "node": "identity",
        "text": said,
        "x": {"id1": "answers", "id_heard": said, "id_last": said},
        "call": ("verify_identity", {"field": field, "answer": answer}),
    }


VERIFIED = [
    HELLO,
    ident("date_of_birth", "On dört Mart bin dokuz yüz seksen sekiz.", "14 Mart 1988"),
    ident("phone_last4", "Dört dört yedi bir.", "dört dört yedi bir"),
]


def reply(kind: str, text: str, **x: str) -> dict[str, Any]:
    vals = {"reply_kind": kind, **x}
    return {"text": text, "x": vals}


Y = reply("yes", "Evet, onaylıyorum.")
N = reply("no", "Hayır, ödeyemem.")
BYE = reply("unclear", "Hayır, bu kadar, teşekkürler.")


def promises(led: dict[str, Any]) -> list[tuple[str, float, str]]:
    return [(p["product_id"], p["amount"], p["date"]) for p in led["promises"]]


def tl(x: float) -> str:
    """The amount as a Turkish speaker reads it: 2.450,50 (thousands '.', decimals ',')."""
    whole, cents = f"{x:.2f}".split(".")
    whole = f"{int(whole):,}".replace(",", ".")
    return whole if cents == "00" else f"{whole},{cents}"


def walk(pipe, script, case=None) -> Walk:
    w = Walk(pipe, case or customer("TR-E2E")).run(script)
    assert not w.errors, w.errors
    return w


def test_full_payment_today(pipe):
    w = walk(pipe, [*VERIFIED, Y, Y, BYE, BYE])
    led = w.ledger()
    assert promises(led) == [("p1", PAST_DUE, "2026-10-14")], w.said
    assert (led["outcome"], led["reason"]) == ("promise", None)
    calls = [c["tool"] for c in led["calls"]]
    assert calls.index("get_account") > max(
        i for i, c in enumerate(calls) if c == "verify_identity"
    )
    text = " ".join(w.said)
    assert tl(PAST_DUE) in text and "5521 ile biten kredi kartı" in text, w.said
    assert LINES["opening"] in text and "Sayın Aksoy ile mi görüşüyorum?" in text
    assert "14 Ekim 2026 Çarşamba" in text, w.said  # today's date in the read-back, in Turkish
    assert_turkish(w.said)


def test_the_floor_after_too_much(pipe):
    too_much = reply("too_much", "Bu benim için çok fazla.")
    friday = reply("offer", "Bu cuma ödeyebilirim.", said_date_text="bu cuma")
    w = walk(pipe, [*VERIFIED, too_much, friday, Y, BYE, BYE])
    led = w.ledger()
    assert promises(led) == [("p1", FLOOR, "2026-10-16")], w.said
    assert led["outcome"] == "promise"
    assert tl(FLOOR) in " ".join(w.said), w.said
    assert_turkish(w.said)


def test_a_turkish_date_next_week(pipe):
    offer = reply(
        "offer",
        "Haftaya cuma tamamını öderim.",
        said_date_text="haftaya cuma",
        said_amount_text="tamamını",
    )
    w = walk(pipe, [*VERIFIED, offer, Y, BYE, BYE])
    led = w.ledger()
    assert promises(led) == [("p1", PAST_DUE, "2026-10-23")], w.said
    text = " ".join(w.said)
    assert "23 Ekim" in text, w.said  # the read-back names the day in Turkish
    assert_turkish(w.said)


def test_two_refusals_end_as_refused(pipe):
    w = walk(pipe, [*VERIFIED, N, reply("no", "Hayır, ödemeyeceğim."), N, N, BYE, BYE])
    led = w.ledger()
    assert promises(led) == []
    assert (led["outcome"], led["reason"]) == ("no_promise", "refused"), w.said
    assert [(n["product_id"], n["reason"]) for n in led["notes"]] == [("p1", "refused")]
    assert_turkish(w.said)


def test_hardship(pipe):
    hard = reply("hardship", "İşimi kaybettim, çok zor durumdayım.", also_hardship="yes")
    w = walk(pipe, [*VERIFIED, hard, reply("no", "Hayır, istemiyorum."), BYE, BYE])
    led = w.ledger()
    assert promises(led) == [] and led["transfers"] == []
    assert (led["outcome"], led["reason"]) == ("no_promise", "hardship"), w.said
    assert LINES["sympathy"] in " ".join(w.said) and LINES["support_offer"] in " ".join(w.said)
    assert_turkish(w.said)


def test_already_paid(pipe):
    w = walk(pipe, [*VERIFIED, reply("paid", "Ben bunu zaten ödedim."), BYE, BYE])
    led = w.ledger()
    assert promises(led) == []
    assert led["outcome"] == "paid", w.said
    assert [(n["product_id"], n["reason"]) for n in led["notes"]] == [("p1", "paid")]
    assert_turkish(w.said)


def test_three_wrong_dates_of_birth_lock_the_check(pipe):
    wrong = ident("date_of_birth", "Bir Ocak bin dokuz yüz doksan.", "1 Ocak 1990")
    w = walk(pipe, [HELLO, wrong, wrong, wrong])
    led = w.ledger()
    assert (led["outcome"], led["reason"]) == ("not_verified", "failed"), w.path
    calls = [c["tool"] for c in led["calls"]]
    assert calls.count("verify_identity") == 3 and "get_account" not in calls
    assert w.v.get("id_locked") == "yes"
    assert LINES["identity_failed"] in " ".join(w.said), w.said
    assert_turkish(w.said)


@pytest.mark.parametrize(
    "day,time",
    [("yarın", "saat on birde"), ("", "yarın saat on birde")],
    ids=["day-and-time", "all-in-the-time-words"],
)
def test_a_third_party_books_a_callback(pipe, day, time):
    other = {
        "node": "opening",
        "text": "Ben eşiyim, kendisi şu an evde yok.",
        "x": {"who1": "someone_else"},
    }
    when = {
        "node": "third_party",
        "text": "Yarın saat on birde arayabilirsiniz.",
        "x": {"tp_reply": "gives_time", "cb_day_text": day, "cb_time_text": time},
    }
    w = walk(pipe, [other, when])
    led = w.ledger()
    assert led["callbacks"] == [{"date": "2026-10-15", "time": "11:00"}], (
        w.path,
        w.v.get("cb_state"),
    )
    assert (led["outcome"], led["reason"]) == ("not_reached", "callback")
    assert not [c for c in led["calls"] if c["tool"] in ("verify_identity", "get_account")]
    assert_turkish(w.said)


def test_the_announcement_moves_the_callback_end_from_its_date(pipe):
    # 17.30 is inside the window before 19 Ekim 2026 and outside it from that day on
    other = {"node": "opening", "text": "Ben kızıyım.", "x": {"who1": "someone_else"}}
    late = {
        "node": "third_party",
        "text": "Yarın akşam beş buçukta.",
        "x": {"tp_reply": "gives_time", "cb_day_text": "yarın", "cb_time_text": "17.30"},
    }
    w = walk(pipe, [other, late], customer("TR-CB1", now="2026-10-14T11:00"))
    assert w.ledger()["callbacks"] == [{"date": "2026-10-15", "time": "17:30"}], w.v.get("cb_state")
    no = {"node": "third_party", "text": "Bilmiyorum.", "x": {"tp_reply": "refuses"}}
    w = Walk(pipe, customer("TR-CB2", now="2026-10-20T11:00")).run([other, late, no])
    assert w.v.get("cb_state") == "outside" and w.ledger()["callbacks"] == []
    assert_turkish(w.said)


# ---------------------------------------------------------------- 6. build.py on the folder
def test_build_main_on_the_folder_without_keys_stops_at_the_first_model_call(monkeypatch, tmp_path):
    """The final's command offline: the folder is read, the OpenAPI block of the contract is
    used with every tool on the mock (no request to the mock), and the build ends with a clear
    status at the spec model's call."""
    b = __import__("test_invariants_guard").load_build()
    monkeypatch.setattr(b, "ROOT", tmp_path)
    for k in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    seen = []

    def refused(req, timeout=0):
        seen.append(getattr(req, "full_url", req))
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(b.urllib.request, "urlopen", refused)
    folder = make_case(tmp_path / "in" / "Lumen-Kart-Borcu")
    assert b.main(["build.py", str(folder), MOCK]) == 1
    log = (tmp_path / "traces" / "Lumen-Kart-Borcu.log").read_text(encoding="utf-8")
    assert f"mock_url={MOCK}" in log.splitlines()[0]
    for want in (
        "case: read BRIEF.md as policy",
        "case: read analiz.docx as ",
        "case: read duyurular/duyuru-2026-10-19.docx as announcement",
        "case: read sss.pdf as faq",
        "case: read test-musterileri.xlsx as test_customers",
        f"tool API: from sozlesme.md in the case; every tool URL on the mock {MOCK}",
        f"tool: verify_identity <- kimlikDogrula (POST {MOCK}/v1/kimlik-dogrula)",
    ):
        assert want in log, want
    assert "UNREAD" not in log
    end = log.splitlines()[-1]
    assert "status=error: LLMError: no model provider key" in end and "model_calls=0" in end, end
    assert seen == []  # the OpenAPI was in the folder: the mock was never asked
