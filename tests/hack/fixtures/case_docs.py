"""Synthetic case documents for the input tests, made with the stdlib: docx, xlsx and pdf bytes
and a whole case folder. Generic content (a made-up "Lumen Bank"), never a real case's values.
"""

# ruff: noqa: E501  (XML and PDF bodies written out as data)

from __future__ import annotations

import io
import json
import zipfile
import zlib
from pathlib import Path

W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
MC_NS = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'


def _p(text: str, style: str = "", num: bool = False) -> str:
    ppr = ""
    if style or num:
        ppr = "<w:pPr>"
        ppr += f'<w:pStyle w:val="{style}"/>' if style else ""
        ppr += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if num else ""
        ppr += "</w:pPr>"
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def docx(paragraphs: list[str] | None = None, table: list[list[str]] | None = None) -> bytes:
    """A Word file: a heading, the paragraphs, a tracked change, struck text, a list item, a
    text box written twice (Choice + Fallback), a table and a header part."""
    body = _p("Duyuru 2026-03-01", "Heading1")
    for t in paragraphs or ["Asgari ödeme oranı %25 olarak güncellenmiştir."]:
        body += _p(t)
    body += (
        '<w:p><w:r><w:t xml:space="preserve">Eski oran </w:t></w:r>'
        '<w:del w:id="1" w:author="a"><w:r><w:delText>%10</w:delText></w:r></w:del>'
        '<w:ins w:id="2" w:author="a"><w:r><w:t>%25</w:t></w:r></w:ins></w:p>'
    )
    body += (
        "<w:p><w:r><w:rPr><w:strike/></w:rPr><w:t>Kaldırılan kural</w:t></w:r>"
        '<w:r><w:t xml:space="preserve"> geçerli kural</w:t></w:r></w:p>'
    )
    body += _p("Liste maddesi", num=True)
    body += (
        '<w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:t>Kutu metni</w:t>'
        "</mc:Choice><mc:Fallback><w:t>Kutu metni</w:t></mc:Fallback></mc:AlternateContent>"
        "</w:r></w:p>"
    )
    rows = table or [["Gün", "Limit"], ["1-30", "₺5.000"]]
    body += (
        "<w:tbl>"
        + "".join("<w:tr>" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in r) + "</w:tr>" for r in rows)
        + "</w:tbl>"
    )
    body += "<w:p><w:r><w:t>Satır</w:t><w:br/><w:t>sonu</w:t><w:tab/><w:t>sekme</w:t></w:r></w:p>"
    doc = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W_NS} {MC_NS}>'
        f"<w:body>{body}</w:body></w:document>"
    )
    header = f'<?xml version="1.0" encoding="UTF-8"?><w:hdr {W_NS}>{_p("Lumen Bank - iç kullanım")}</w:hdr>'
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        z.writestr("word/document.xml", doc)
        z.writestr("word/header1.xml", header)
    return out.getvalue()


def xlsx(customers: list[list[object]] | None = None) -> bytes:
    """An Excel file: a customer sheet (shared strings with a rich-text entry, an inline
    string, numbers, a date in a built-in date style, a date in a custom style, a time, a
    boolean) and a second sheet, wired through workbook rels."""
    shared = ["Müşteri", "Doğum tarihi", "Borç", "Son ödeme", "Saat", "Aktif"]
    sst = (
        '<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{s}</t></si>" for s in shared)
        + "<si><r><t>Ayşe </t></r><r><rPr><b/></rPr><t>Demir</t></r></si>"
        + "</sst>"
    )
    styles = (
        '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="dd\\.mm\\.yyyy"/></numFmts>'
        '<cellXfs count="5"><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/>'
        '<xf numFmtId="20"/><xf numFmtId="4"/></cellXfs></styleSheet>'
    )
    # 1985-04-12 = serial 31149; 2026-10-20 = 46315; 0.5 = 12:00
    sheet1 = (
        '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c>'
        '<c r="D1" t="s"><v>3</v></c><c r="E1" t="s"><v>4</v></c><c r="F1" t="s"><v>5</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>6</v></c><c r="B2" s="1"><v>31149</v></c><c r="C2" s="4"><v>1250.5</v></c>'
        '<c r="D2" s="2"><v>46315</v></c><c r="E2" s="3"><v>0.5</v></c><c r="F2" t="b"><v>1</v></c></row>'
        '<row r="3"><c r="A3" t="inlineStr"><is><t>Can Yıldız</t></is></c><c r="C3"><v>17</v></c>'
        '<c r="F3" t="b"><v>0</v></c></row>'
        "</sheetData></worksheet>"
    )
    sheet2 = (
        '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="B1" t="inlineStr"><is><t>Not</t></is></c></row>'
        "</sheetData></worksheet>"
    )
    wb = (
        '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
        '<sheet name="Müşteriler" sheetId="1" r:id="rId7"/><sheet name="Notlar" sheetId="2" r:id="rId8"/>'
        "</sheets></workbook>"
    )
    rels = (
        '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId7" Type="worksheet" Target="worksheets/sheetB.xml"/>'
        '<Relationship Id="rId8" Type="worksheet" Target="/xl/worksheets/sheetA.xml"/>'
        "</Relationships>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", sst)
        z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/sheetB.xml", sheet1)
        z.writestr("xl/worksheets/sheetA.xml", sheet2)
    return out.getvalue()


def _pdf(objects: dict[int, bytes], root: int) -> bytes:
    out = bytearray(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")
    offs = {}
    for n in sorted(objects):
        offs[n] = len(out)
        out += f"{n} 0 obj\n".encode() + objects[n] + b"\nendobj\n"
    xref = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n0000000000 65535 f \n".encode()
    for n in range(1, size):
        out += f"{offs.get(n, 0):010d} 00000 n \n".encode()
    out += f"trailer\n<</Size {size} /Root {root} 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _stream(data: bytes, extra: str = "", flate: bool = True) -> bytes:
    if flate:
        data = zlib.compress(data)
        extra += " /Filter /FlateDecode"
    return f"<</Length {len(data)}{extra}>>\nstream\n".encode() + data + b"\nendstream"


def pdf_text(lines: list[str]) -> bytes:
    """A PDF that writes `lines` with a Type0 font (2-byte glyph ids, a ToUnicode CMap with
    bfchar and bfrange entries, widths) the way browsers and Word do: words split over
    several Tj with Td moves, kerning inside TJ arrays, words apart by TJ gaps; plus a line in
    a simple WinAnsi font with a literal string. The font dictionaries sit in an object
    stream (PDF 1.5)."""
    chars = sorted({c for ln in lines for c in ln} | set("Hello (world)"))
    gid = {c: 3 + i for i, c in enumerate(chars)}
    # bfrange for the first three glyphs (consecutive Unicode only when they are), bfchar rest
    bfchar = "".join(f"<{gid[c]:04X}> <{ord(c):04X}>\n" for c in chars)
    cmap = (
        "/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
        "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n"
        f"{len(chars)} beginbfchar\n{bfchar}endbfchar\n"
        "1 beginbfrange\n<0001> <0002> <0041>\nendbfrange\n"
        "endcmap\nend\nend\n"
    ).encode()
    width = 500  # every glyph half an em wide
    size = 10.0

    def hexs(s: str) -> str:
        return "<" + "".join(f"{gid[c]:04X}" for c in s) + ">"

    content = ["BT", "/F1 10 Tf", "1 0 0 1 50 750 Tm"]
    y = 750.0
    for i, ln in enumerate(lines):
        if i:
            y -= 14
            content.append(f"1 0 0 1 50 {y:g} Tm")
        words = ln.split(" ")
        # first word split mid-way over two Tj with a Td of exactly its drawn width
        first = words[0]
        cut = max(1, len(first) // 2)
        content.append(f"{hexs(first[:cut])} Tj")
        content.append(f"{cut * width / 1000 * size:g} 0 Td {hexs(first[cut:])} Tj")
        rest = words[1:]
        if rest:
            parts = []
            for w in rest:
                # a small kerning number inside a word, a wide gap before it
                parts.append("-250")
                parts.append(hexs(w[:1]) + " -20 " + hexs(w[1:]) if len(w) > 1 else hexs(w))
            content.append("[" + " ".join(parts) + "] TJ")
    content += ["ET", "BT", "/F2 10 Tf", "50 600 Td", "(Hello \\(world\\)) Tj", "ET"]
    page_content = "\n".join(content).encode()
    objstm_objs = {
        5: b"<</Type /Font /Subtype /Type0 /BaseFont /Test /Encoding /Identity-H "
        b"/DescendantFonts [6 0 R] /ToUnicode 7 0 R>>",
        6: f"<</Type /Font /Subtype /CIDFontType2 /BaseFont /Test /DW 1000 /W [3 [{' '.join([str(width)] * len(chars))}]]>>".encode(),
        8: b"<</Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding>>",
    }
    head = b" ".join(f"{n} {0}".encode() for n in objstm_objs)  # offsets fixed below
    bodies, offs, pos = [], [], 0
    for n, body in objstm_objs.items():
        offs.append(f"{n} {pos}")
        bodies.append(body)
        pos += len(body) + 1
    head = " ".join(offs).encode() + b" "
    stm = head + b"\n".join(bodies)
    first = len(head)
    objects = {
        1: b"<</Type /Catalog /Pages 2 0 R>>",
        2: b"<</Type /Pages /Kids [3 0 R] /Count 1>>",
        3: b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources <</Font <</F1 5 0 R /F2 8 0 R>>>> /Contents 4 0 R>>",
        4: _stream(page_content),
        7: _stream(cmap),
        9: _stream(stm, f" /Type /ObjStm /N {len(objstm_objs)} /First {first}"),
    }
    return _pdf(objects, 1)


def pdf_image_only() -> bytes:
    """A scanned page: one image, no text."""
    img = _stream(
        b"\xff\xd8\xff" + b"\x00" * 64,
        " /Type /XObject /Subtype /Image /Width 8 /Height 8 /Filter /DCTDecode",
        flate=False,
    )
    content = _stream(b"q 600 0 0 800 0 0 cm /Im1 Do Q")
    objects = {
        1: b"<</Type /Catalog /Pages 2 0 R>>",
        2: b"<</Type /Pages /Kids [3 0 R] /Count 1>>",
        3: b"<</Type /Page /Parent 2 0 R /Resources <</XObject <</Im1 5 0 R>>>> /Contents 4 0 R>>",
        4: content,
        5: img,
    }
    return _pdf(objects, 1)


CONTRACT_MD = """# Servis Sözleşmesi

Taban adres: https://bank.example/api/v1

## kimlikDogrula
POST /kimlik/dogrula
| Alan | Tip | Zorunlu | Açıklama |
|---|---|---|---|
| musteri_no | string | evet | Müşteri numarası |
| soru | string | evet | dogum_tarihi veya tel_son4 |
| cevap | string | evet | Müşterinin cevabı |
Yanıt: basarili (boolean), dogrulandi (boolean), kalan_hak (integer, kalan deneme hakkı)
"""

README_MD = """# Belgeler

| Dosya | Ne |
|---|---|
| BRIEF.md | Önce bunu okuyun |
| analiz.docx | İş analizi |
| duyurular/ | Tarihli duyurular |
| sozlesme.md | Servis sözleşmesi (bankanın servisleri) |
| test-verisi.xlsx | Test müşterileri (mock için) |
| sss.md | Sıkça sorulan sorular |
| konusmalar/ | 2 örnek konuşma |
| akis.png | Akış çizimi |
"""


def case_folder(root: Path, *, openapi: dict | None = None) -> Path:
    """A whole synthetic case folder; `openapi` adds an openapi.json to it."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "BRIEF.md").write_text(
        "# Lumen Bank kart borcu hattı\n\nAjan kimliği doğrulamadan borçtan söz etmez.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(README_MD, encoding="utf-8")
    (root / "analiz.docx").write_bytes(docx(["Kimlik iki soruyla doğrulanır."]))
    (root / "duyurular").mkdir(exist_ok=True)
    (root / "duyurular" / "duyuru-2026-05-02.md").write_text(
        "Geri arama saatleri 10:00-18:00 olarak değişti.\n", encoding="utf-8"
    )
    (root / "duyurular" / "duyuru-2026-01-15.md").write_text(
        "Asgari ödeme oranı değişti.\n", encoding="utf-8-sig"
    )
    (root / "sozlesme.md").write_text(CONTRACT_MD, encoding="utf-8")
    (root / "test-verisi.xlsx").write_bytes(xlsx())
    (root / "sss.md").write_bytes("S: Taksit olur mu?\nC: Hayır.\n".encode("cp1254"))
    (root / "konusmalar").mkdir(exist_ok=True)
    for i in (1, 2):
        (root / "konusmalar" / f"konusma-{i}.md").write_text(
            f"Ajan: Merhaba, Lumen Bank'tan arıyorum.\nMüşteri: Evet benim.\nAjan: Teşekkürler {i}.\n"
            "Müşteri: Tamam.\n",
            encoding="utf-8",
        )
    (root / "akis.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    (root / "~$analiz.docx").write_bytes(b"lock")
    if openapi is not None:
        (root / "openapi.json").write_text(json.dumps(openapi), encoding="utf-8")
    return root
