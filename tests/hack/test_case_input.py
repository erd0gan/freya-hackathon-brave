"""The final's input is a folder (or zip) of a bank's documents in Markdown, Word, PDF and
Excel, with README.md saying what each one is (hackathon page, 11 Oct). fb.case_input reads
them with the stdlib, gives each a role and keeps policy, examples, test customers and the
service contract apart (audit 01: IC-1, IC-3, IC-7, IC-9, IC-10)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fb.case_input import (
    EXAMPLES_TITLE,
    _zip_name,
    decode_text,
    load_case,
    load_json_or_yaml,
    load_yaml,
    read_csv,
    read_document,
    read_docx,
    read_pdf,
    read_xlsx,
    role_from_text,
    slot_name,
)
from fixtures.case_docs import case_folder, docx, pdf_image_only, pdf_text, xlsx


# ---------------------------------------------------------------- encodings (IC-9)
def test_text_encodings_bom_utf16_and_cp1254():
    s = "Ödeme sözü: ₺1.250,50 ğış"
    assert decode_text(b"\xef\xbb\xbf" + s.encode()) == (s, "utf-8-sig")
    assert decode_text(s.encode("utf-16"))[0] == s
    assert decode_text(s.encode("utf-16-le"))[0] == s  # no BOM: the NUL pattern
    assert decode_text(s.encode("utf-16-be"))[0] == s
    t = "Müşteri ödemesi ğüşıöç"
    assert decode_text(t.encode("cp1254")) == (t, "cp1254")


def test_a_bom_json_file_and_a_semicolon_cp1254_csv_are_read():
    d = read_document(
        "api.json", b"\xef\xbb\xbf" + json.dumps({"openapi": "3.0.0", "paths": {}}).encode()
    )
    assert not d.error and d.data == {"openapi": "3.0.0", "paths": {}}
    rows = read_csv("Ad;Borç;Şehir\nAyşe;1250,50;İzmir\n")
    assert rows == [["Ad", "Borç", "Şehir"], ["Ayşe", "1250,50", "İzmir"]]
    d = read_document("musteriler.csv", "Ad;Borç\nAyşe;10\n".encode("cp1254"))
    assert d.rows == [["Ad", "Borç"], ["Ayşe", "10"]] and d.reader == "csv (cp1254)"


# ---------------------------------------------------------------- readers (IC-3)
def test_docx_paragraphs_tables_changes_and_header():
    text = read_docx(docx(["Kimlik iki soruyla doğrulanır."]))
    assert "[header] Lumen Bank - iç kullanım" in text
    assert "# Duyuru 2026-03-01" in text  # a heading style
    assert "Eski oran %25" in text and "%10" not in text  # tracked deletion gone
    assert "~~Kaldırılan kural~~ geçerli kural" in text  # struck text kept, marked
    assert "- Liste maddesi" in text
    assert text.count("Kutu metni") == 1  # mc:Choice only, not the Fallback copy
    assert "| Gün | Limit |" in text and "| 1-30 | ₺5.000 |" in text
    assert "Satır\nsonu\tsekme" in text


def test_xlsx_shared_inline_numbers_dates_times_booleans_and_sheets():
    text, rows = read_xlsx(xlsx())
    assert rows[0] == ["Müşteri", "Doğum tarihi", "Borç", "Son ödeme", "Saat", "Aktif"]
    # rich text joined, built-in date style (14), custom dd.mm.yyyy style, a time, a boolean
    assert rows[1] == ["Ayşe Demir", "1985-04-12", "1250.5", "2026-10-20", "12:00", "TRUE"]
    assert rows[2] == ["Can Yıldız", "", "17", "", "", "FALSE"]  # an inline string, gaps kept
    assert "### Sheet: Müşteriler" in text and "### Sheet: Notlar" in text
    assert text.index("Müşteriler") < text.index("Notlar")  # workbook order, not file order


def test_pdf_tounicode_turkish_words_lines_and_object_streams():
    text, warns = read_pdf(
        pdf_text(["Hizmet Sözleşmesi ğüşıöç İstanbul", "Asgari ödeme %40 ₺5.000"])
    )
    lines = text.splitlines()
    # a word drawn in two pieces stays one word; TJ gaps make the spaces; Tm a new line
    assert lines[0] == "Hizmet Sözleşmesi ğüşıöç İstanbul"
    assert lines[1] == "Asgari ödeme %40 ₺5.000"
    assert "Hello (world)" in text  # a simple WinAnsi font, an escaped literal string
    assert warns == []


def test_a_scanned_pdf_is_reported_not_fatal():
    with pytest.raises(ValueError, match="no text layer"):
        read_pdf(pdf_image_only())
    d = read_document("akis.pdf", pdf_image_only())
    assert d.error and "no text layer" in d.error and d.text == ""
    d = read_document("bozuk.pdf", b"%PDF-1.4\n garbage")
    assert d.error  # never an exception
    d = read_document("bozuk.docx", b"PK\x03\x04 not a zip")
    assert d.error.startswith("BadZipFile")


def test_yaml_subset_reads_an_openapi_file():
    y = (
        "openapi: 3.0.3  # c\nservers:\n  - url: https://x.example/v1\npaths:\n"
        "  /kimlik/dogrula:\n    post:\n      operationId: kimlikDogrula\n"
        "      requestBody:\n        content:\n          application/json:\n            schema:\n"
        "              type: object\n"
        "              required: [musteri_no]\n"
        "              properties:\n"
        '                musteri_no: {type: string, description: "No: 1"}\n'
        "                soru:\n                  type: string\n                  enum:\n"
        "                    - dogum_tarihi\n                    - 'tel_son4'\n"
        "                aciklama:\n"
        "                  type: string\n"
        "                  description: |\n"
        "                    bir\n                    iki\n"
        "      responses:\n        '200':\n          description: ok\n"
    )
    doc = load_yaml(y)
    schema = doc["paths"]["/kimlik/dogrula"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]
    assert doc["servers"] == [{"url": "https://x.example/v1"}]
    assert schema["required"] == ["musteri_no"]
    assert schema["properties"]["musteri_no"] == {"type": "string", "description": "No: 1"}
    assert schema["properties"]["soru"]["enum"] == ["dogum_tarihi", "tel_son4"]
    assert schema["properties"]["aciklama"]["description"] == "bir\niki\n"
    assert "200" in doc["paths"]["/kimlik/dogrula"]["post"]["responses"]
    assert load_json_or_yaml('{"a": 1}') == {"a": 1}


# ---------------------------------------------------------------- roles and the case (IC-1, IC-7)
def test_load_case_roles_from_readme_and_the_parts_kept_apart(tmp_path):
    case = load_case(case_folder(tmp_path / "Lumen-Kart.v2"))
    roles = {d.name: d.role for d in case.documents}
    assert roles == {
        "BRIEF.md": "policy",
        "README.md": "readme",
        "analiz.docx": "analysis",
        "duyurular/duyuru-2026-01-15.md": "announcement",
        "duyurular/duyuru-2026-05-02.md": "announcement",
        "sozlesme.md": "contract",
        "test-verisi.xlsx": "test_customers",
        "sss.md": "faq",
        "konusmalar/konusma-1.md": "example_conversation",
        "konusmalar/konusma-2.md": "example_conversation",
        "akis.png": "flow",  # the README says what it is, even though it cannot be read
    }
    assert "~$analiz.docx" not in roles  # Word's lock file is not a document
    # policy: BRIEF first, then README, analysis, announcements oldest first with their date, Q&A
    pol = case.policy_text
    assert pol.startswith("## BRIEF.md (policy)")
    heads = [ln for ln in pol.splitlines() if ln.startswith("## ")]
    assert heads == [
        "## BRIEF.md (policy)",
        "## README.md (readme)",
        "## analiz.docx (analysis)",
        "## duyurular/duyuru-2026-01-15.md (announcement, dated 2026-01-15)",
        "## duyurular/duyuru-2026-05-02.md (announcement, dated 2026-05-02)",
        "## sss.md (faq)",
    ]
    assert "S: Taksit olur mu?" in pol  # cp1254 file read
    # examples apart, marked; customers and the contract out of the policy text
    assert case.examples_text.startswith("# " + EXAMPLES_TITLE)
    assert "Teşekkürler 1" in case.examples_text and "Teşekkürler 1" not in pol
    assert case.customers[0]["Müşteri"] == "Ayşe Demir" and "Ayşe Demir" not in pol
    assert "1985-04-12" not in pol and "1985-04-12" not in case.examples_text
    assert "kimlikDogrula" in case.contract_text and "kimlikDogrula" not in pol
    assert case.openapi is None
    # the image is listed as unread, with the reason
    assert [d.name for d in case.unread()] == ["akis.png"]
    lines = case.trace_lines()
    assert any(ln.startswith("case: UNREAD akis.png") for ln in lines)
    assert any(
        "analiz.docx as analysis (role from readme" in ln and "reader docx" in ln for ln in lines
    )
    assert case.name == "Lumen-Kart.v2"


def test_an_openapi_file_in_the_folder_is_the_tool_api(tmp_path):
    oa = {"openapi": "3.0.0", "servers": [{"url": "https://b.example/v1"}], "paths": {}}
    case = load_case(case_folder(tmp_path / "c", openapi=oa))
    assert case.openapi == oa and case.openapi_source == "openapi.json"
    # an OpenAPI block inside a Markdown contract counts too
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "BRIEF.md").write_text("# Brief\n", encoding="utf-8")
    (tmp_path / "d" / "service-contract.md").write_text(
        "# API\n\n```yaml\nopenapi: 3.0.0\npaths:\n  /x:\n    post:\n      summary: x\n```\n",
        encoding="utf-8",
    )
    case = load_case(tmp_path / "d")
    assert case.openapi["paths"]["/x"]["post"]["summary"] == "x"
    assert case.openapi_source == "service-contract.md"


def test_roles_without_a_readme_from_names_then_content(tmp_path):
    d = tmp_path / "case"
    d.mkdir()
    (d / "BRIEF.md").write_text("# Brief\n", encoding="utf-8")
    (d / "Ornek Konusma 3.md").write_text("x\n", encoding="utf-8")
    (d / "Duyuru_2026-09-30.docx").write_bytes(docx())
    (d / "notes.md").write_text(
        "Agent: Hello\nCustomer: Hi\nAgent: Can you\nCustomer: Yes\n", encoding="utf-8"
    )
    (d / "kayit.md").write_text("S: a?\nC: b.\nS: c?\nC: d.\n", encoding="utf-8")
    (d / "liste.csv").write_text("customer_id,name\nc1,A\n", encoding="utf-8")
    (d / "api.md").write_text("POST /verify\nPOST /promise\n", encoding="utf-8")
    (d / "sozlesme.md").write_text("Kart üyelik sözleşmesi: faiz oranları...\n", encoding="utf-8")
    roles = {x.name: (x.role, x.role_from) for x in load_case(d).documents}
    assert roles["Ornek Konusma 3.md"] == ("example_conversation", "name")
    assert roles["Duyuru_2026-09-30.docx"] == ("announcement", "name")
    assert roles["notes.md"] == ("example_conversation", "content")
    assert roles["kayit.md"] == ("faq", "content")
    assert roles["liste.csv"] == ("test_customers", "content")
    assert roles["api.md"] == ("contract", "content")
    # "sözleşme" with nothing technical in it is a customer agreement: policy, not the API
    assert roles["sozlesme.md"][0] == "policy"
    assert role_from_text("Servis sözleşmesi") == "contract"
    assert role_from_text("Örnek görüşmeler") == "example_conversation"
    assert role_from_text("Soru ve cevaplar") == "faq"


def test_a_zip_with_one_top_folder_and_os_litter(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Lumen-Kart/BRIEF.md", "# Brief\nKurallar.\n")
        z.writestr("Lumen-Kart/test-musteriler.xlsx", xlsx())
        z.writestr("__MACOSX/Lumen-Kart/._BRIEF.md", b"\x00\x05\x16\x07")
        z.writestr("Lumen-Kart/.DS_Store", b"\x00")
    p = tmp_path / "final.zip"
    p.write_bytes(buf.getvalue())
    case = load_case(p)
    names = [d.name for d in case.documents]
    assert sorted(names) == ["BRIEF.md", "test-musteriler.xlsx"]
    assert case.name == "Lumen-Kart"
    assert case.customers and "Kurallar." in case.policy_text
    # a Turkish name stored in the OEM code page without the UTF-8 flag (zipfile reads cp437)
    info = zipfile.ZipInfo("x")
    info.filename, info.flag_bits = "Duyuru-ş.md".encode("cp857").decode("cp437"), 0
    assert _zip_name(info) == "Duyuru-ş.md"


def test_a_folder_with_nothing_readable_raises(tmp_path):
    (tmp_path / "e").mkdir()
    (tmp_path / "e" / "x.png").write_bytes(b"\x89PNG")
    with pytest.raises(ValueError, match="no readable document"):
        load_case(tmp_path / "e")
    with pytest.raises(FileNotFoundError):
        load_case(tmp_path / "missing")


# ---------------------------------------------------------------- slot (IC-10)
def test_slot_names(tmp_path, monkeypatch):
    d = tmp_path / "FreyaLike.Case.v2"
    d.mkdir()
    assert slot_name(d) == "FreyaLike.Case.v2"  # a folder keeps its dots
    assert slot_name(str(d) + "/") == "FreyaLike.Case.v2"
    monkeypatch.chdir(d)
    assert slot_name(".") == "FreyaLike.Case.v2"
    assert slot_name("./") == "FreyaLike.Case.v2"
    assert slot_name("kit/briefs/brief-a.md") == "brief-a"
    assert slot_name("x/final case.zip") == "final-case"
