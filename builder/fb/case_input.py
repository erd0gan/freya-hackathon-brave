"""A case folder (or its zip) -> the texts the builder works from. Stdlib only.

The final case is a folder of a bank's documents in Markdown, Word, PDF and Excel: BRIEF.md
(read first), README.md (says what each document is), an analysis, a flow drawing,
announcements, a service contract, test customers, questions and answers and example
conversations. `load_case(path)` reads every file it can, gives each one a role and keeps the
roles apart:

  policy_text    BRIEF + README + analysis + flow + announcements + Q&A (+ documents of no
                 known role), each under a "## <file> (<role>)" header; announcements keep
                 their dates (and are ordered by the date in their file name when there is one)
  examples_text  the example conversations, marked as illustrations, not rules
  customers      test customer rows (dicts by column): for the trace only, never policy
                 (they are the mock's data; their numbers would ground any guess)
  contract_text  the service contract when it is a document, not an OpenAPI file
  openapi        an OpenAPI/Swagger document found in the folder (JSON or YAML), else None

Roles come from README.md's description of each file when it names the file, else from the
file name, else from the content. A file that cannot be read is listed with the reason
(`Case.unread()`), never fatal: one bad file must not stop the build.

Readers: md/txt, docx (paragraphs, tables as pipe rows, headers/footers), xlsx (shared and
inline strings, numbers, dates by cell style, every sheet), pdf (Flate streams, text operators,
ToUnicode CMaps, object streams; scanned PDFs have no text and are reported), csv, json, yaml
(JSON first, then a small YAML subset enough for an OpenAPI file), svg/drawio (text labels),
pptx/vsdx/odt (their XML text). Text encodings: UTF-8 (BOM or not), UTF-16 (BOM or the NUL
pattern), cp1254 (Turkish Windows) as the last resort.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

ROLES = (
    "policy",
    "readme",
    "analysis",
    "flow",
    "announcement",
    "contract",
    "test_customers",
    "faq",
    "example_conversation",
    "other",
)
# policy_text order: the brief first, then what explains or changes it
POLICY_ORDER = ("policy", "readme", "analysis", "flow", "announcement", "faq", "other")
EXAMPLES_TITLE = "Example conversations (illustrations, not rules)"
EXAMPLES_NOTE = (
    "They show the kind of call the bank tests with. The policy documents decide; a sentence, "
    "amount, date or name in an example is not a rule and not a value to copy."
)
# Files that are never documents: OS and editor litter, lock files.
_SKIP = re.compile(r"(^|/)(__MACOSX/|\.DS_Store$|Thumbs\.db$|desktop\.ini$|~\$|\._|\.~lock)", re.I)
_IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".heic"}


@dataclass
class Document:
    name: str  # path inside the case, '/' separated
    text: str = ""
    role: str = "other"
    reader: str = ""  # which reader produced the text ("md", "docx", "pdf", ...)
    size: int = 0  # bytes of the file
    error: str = ""  # why it could not be read; '' when it was
    role_from: str = ""  # "readme", "name", "content" or "default"
    rows: list[list[str]] = field(default_factory=list)  # table rows (xlsx, csv)
    data: Any = None  # parsed JSON/YAML
    dated: str = ""  # YYYY-MM-DD from the file name (announcements)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Case:
    root: str
    name: str
    documents: list[Document]
    policy_text: str = ""
    examples_text: str = ""
    customers: list[dict[str, str]] = field(default_factory=list)
    contract_text: str = ""
    openapi: dict[str, Any] | None = None
    openapi_source: str = ""

    def unread(self) -> list[Document]:
        return [d for d in self.documents if d.error]

    def by_role(self, role: str) -> list[Document]:
        return [d for d in self.documents if d.role == role and not d.error]

    def trace_lines(self) -> list[str]:
        """One line per document (role, size, reader) and per unread one, then the totals."""
        out = []
        for d in self.documents:
            if d.error:
                out.append(f"case: UNREAD {d.name} ({d.size} bytes): {d.error}")
                continue
            extra = f", dated {d.dated}" if d.dated else ""
            out.append(
                f"case: read {d.name} as {d.role} (role from {d.role_from}{extra}; "
                f"{d.size} bytes, reader {d.reader}, {len(d.text)} chars)"
            )
            for w in d.warnings:
                out.append(f"case: warning {d.name}: {w}")
        cols = sorted({k for row in self.customers for k in row})
        out.append(
            f"case: policy text {len(self.policy_text)} chars; examples {len(self.examples_text)} "
            f"chars; contract text {len(self.contract_text)} chars; OpenAPI "
            f"{'from ' + self.openapi_source if self.openapi else 'none'}; test customers "
            f"{len(self.customers)} rows kept out of the policy text (columns: {cols[:12]})"
        )
        return out


# ---------------------------------------------------------------- text and names
def decode_text(data: bytes) -> tuple[str, str]:
    """(text, encoding) for a text file: UTF-8 with or without BOM, UTF-16 by BOM or by its NUL
    pattern, else cp1254 (a Turkish Windows export), which reads any byte."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace"), "utf-16"
    head = data[:2000]
    if len(head) >= 4 and head.count(b"\x00") > len(head) // 4:
        le = head[1::2].count(b"\x00") > head[0::2].count(b"\x00")
        return data.decode("utf-16-le" if le else "utf-16-be", "replace"), "utf-16"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("cp1254", "replace"), "cp1254"


_FOLD = str.maketrans("ıİşŞğĞüÜöÖçÇâÂîÎûÛ", "iissgguuooccaaiiuu")


def fold(text: str) -> str:
    """Lower case, Turkish letters to ASCII ('Örnek Konuşma' -> 'ornek konusma'), other
    accents dropped; for matching names and keywords, never for text the model reads."""
    s = str(text).translate(_FOLD).lower()
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def _words(name: str) -> str:
    """A file name as words: 'ornek-konusma_1.md' -> 'ornek konusma 1 md', camelCase split."""
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name))
    return " ".join(re.split(r"[^a-z0-9]+", fold(s))).strip()


def slot_name(path: str | Path) -> str:
    """The build slot (trace, submission and Freya workflow names) for an input: the folder's
    name, the zip's or the brief's name without its extension; never empty ('.' resolves to the
    folder it names; dots inside a folder name are kept)."""
    p = Path(str(path).rstrip("/\\") or ".")
    try:
        if p.name in ("", ".", ".."):
            p = p.resolve()
    except OSError:
        pass
    name = p.name
    if p.is_file() or p.suffix.lower() in (".zip", ".md", ".txt", ".docx", ".pdf"):
        name = p.stem if p.suffix else p.name
    name = re.sub(r"[^\w.-]+", "-", name).strip("-.")
    return name or "final"


# ---------------------------------------------------------------- docx and other OOXML
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"


def _on(el: ET.Element | None) -> bool:
    """A Word toggle property (<w:strike/>, <w:strike w:val="0"/>)."""
    if el is None:
        return False
    return str(el.get(_W + "val", "true")).lower() not in ("0", "false", "off", "none")


def _run_text(el: ET.Element, out: list[str]) -> None:
    """The text of one run: w:t, tabs, breaks; not a text box's copy for old readers
    (mc:Fallback), deleted text or field codes."""
    for n in el:
        if n.tag in (_MC + "Fallback", _W + "delText", _W + "instrText"):
            continue
        if n.tag == _W + "t":
            out.append(n.text or "")
        elif n.tag == _W + "tab":
            out.append("\t")
        elif n.tag in (_W + "br", _W + "cr"):
            out.append("\n")
        elif n.tag == _W + "noBreakHyphen":
            out.append("-")
        _run_text(n, out)


def _docx_runs(p: ET.Element) -> str:
    out: list[str] = []

    def walk(el: ET.Element, struck: bool) -> None:
        for ch in el:
            tag = ch.tag
            if tag == _MC + "Fallback" or tag == _W + "delText" or tag == _W + "instrText":
                continue  # the Choice holds the same text; deleted text is gone
            if tag == _W + "del":
                continue
            if tag == _W + "r":
                rpr = ch.find(_W + "rPr")
                s = struck or (
                    rpr is not None
                    and (_on(rpr.find(_W + "strike")) or _on(rpr.find(_W + "dstrike")))
                )
                txt: list[str] = []
                _run_text(ch, txt)
                t = "".join(txt)
                # struck-through text is a removed rule in a policy draft: kept, but marked
                out.append(f"~~{t}~~" if s and t.strip() else t)
                continue
            walk(ch, struck)

    walk(p, False)
    return "".join(out)


def _docx_para(p: ET.Element) -> str:
    text = _docx_runs(p)
    ppr = p.find(_W + "pPr")
    if ppr is not None and text.strip():
        st = ppr.find(_W + "pStyle")
        sid = fold(str(st.get(_W + "val", ""))) if st is not None else ""
        m = re.search(r"(heading|balk|baslik|title)\s*(\d?)", sid)
        if m:
            return "#" * min(int(m.group(2) or 1), 6) + " " + text.strip()
        if ppr.find(_W + "numPr") is not None or sid.startswith(("list", "liste")):
            return "- " + text.strip()
    return text


def _docx_cell(tc: ET.Element) -> str:
    parts = []
    for p in tc.iter(_W + "p"):
        t = _docx_runs(p).strip()
        if t:
            parts.append(t)
    return " ".join(parts).replace("|", "/")


def _docx_body(root: ET.Element) -> list[str]:
    out: list[str] = []

    def walk(el: ET.Element) -> None:
        for ch in el:
            if ch.tag == _W + "p":
                out.append(_docx_para(ch))
            elif ch.tag == _W + "tbl":
                for tr in ch.findall(_W + "tr"):
                    out.append(
                        "| " + " | ".join(_docx_cell(tc) for tc in tr.findall(_W + "tc")) + " |"
                    )
                out.append("")
            elif ch.tag in (_MC + "Fallback",):
                continue
            else:
                walk(ch)

    walk(root)
    return out


def read_docx(data: bytes) -> str:
    z = zipfile.ZipFile(io.BytesIO(data))
    names = z.namelist()
    parts = [n for n in names if re.fullmatch(r"word/header\d*\.xml", n)]
    body = _docx_body(ET.fromstring(z.read("word/document.xml")))
    out: list[str] = []
    heads = [t for n in sorted(parts) for t in _docx_body(ET.fromstring(z.read(n))) if t.strip()]
    if heads:
        out += ["[header] " + " / ".join(dict.fromkeys(heads))]
    out += body
    foots = [
        t
        for n in sorted(x for x in names if re.fullmatch(r"word/(footer|footnotes)\d*\.xml", x))
        for t in _docx_body(ET.fromstring(z.read(n)))
        if t.strip()
    ]
    if foots:
        out += ["[footer] " + " / ".join(dict.fromkeys(foots))]
    return _tidy("\n".join(out))


def _xml_texts(data: bytes, tags: tuple[str, ...]) -> list[str]:
    root = ET.fromstring(data)
    out = []
    for el in root.iter():
        local = el.tag.rsplit("}", 1)[-1]
        if local in tags and (el.text or "").strip():
            out.append(el.text.strip())
    return out


def read_ooxml_text(data: bytes, kind: str) -> str:
    """pptx (slides), vsdx (Visio pages), odt/ods/odp (content.xml): the text of every text
    element, page by page. A flow drawing saved by Visio or PowerPoint reads this way."""
    z = zipfile.ZipFile(io.BytesIO(data))
    if kind == "pptx":
        parts = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
        tags: tuple[str, ...] = ("t",)
    elif kind == "vsdx":
        parts = [n for n in z.namelist() if re.fullmatch(r"visio/pages/page\d+\.xml", n)]
        tags = ("Text",)
    else:
        parts = ["content.xml"] if "content.xml" in z.namelist() else []
        tags = ("p", "h", "span")
    parts.sort(key=lambda n: int((re.findall(r"\d+", n) or ["0"])[-1]))
    out = []
    for n in parts:
        out.append(f"[{n.rsplit('/', 1)[-1]}]")
        out += _xml_texts(z.read(n), tags)
    return _tidy("\n".join(out))


def read_svg(data: bytes) -> str:
    text, _ = decode_text(data)
    root = ET.fromstring(text.encode("utf-8"))
    out = []
    for el in root.iter():
        local = el.tag.rsplit("}", 1)[-1]
        if local in ("text", "tspan", "title", "desc"):
            t = "".join(el.itertext()).strip() if local == "text" else (el.text or "").strip()
            if t and (not out or out[-1] != t):
                out.append(t)
    return _tidy("\n".join(out))


def _strip_tags(s: str) -> str:
    s = re.sub(r"<br\s*/?>|</div>|</p>", "\n", s, flags=re.I)
    return re.sub(r"<[^>]+>", "", s).replace("&nbsp;", " ")


def read_drawio(data: bytes) -> str:
    """draw.io / diagrams.net: every cell's label (value attribute), pages compressed as
    base64 raw deflate of a URL-encoded XML or written plain."""
    text, _ = decode_text(data)
    root = ET.fromstring(text.encode("utf-8"))
    out: list[str] = []

    def labels(model: ET.Element) -> None:
        for cell in model.iter():
            v = cell.get("value") or cell.get("label")
            if v and v.strip():
                out.append(" ".join(_strip_tags(v).split()))

    for diagram in root.iter("diagram"):
        out.append(f"[page {diagram.get('name') or ''}]".replace(" ]", "]"))
        inner = list(diagram)
        if inner:
            labels(diagram)
        elif (diagram.text or "").strip():
            raw = zlib.decompress(base64.b64decode(diagram.text.strip()), -15)
            labels(ET.fromstring(urllib.parse.unquote(raw.decode("utf-8"))))
    if not out:
        labels(root)
    return _tidy("\n".join(out))


# ---------------------------------------------------------------- xlsx
_M = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
# built-in number formats that are dates or times (ECMA-376 18.8.30, plus the locale ids
# 27-36 and 50-58 East Asian versions use for dates)
_DATE_IDS = set(range(14, 23)) | set(range(27, 37)) | {45, 46, 47} | set(range(50, 59))


def _is_date_format(code: str) -> bool:
    s = re.sub(r'"[^"]*"|\\.|\[[^\]]*\]', "", code or "").lower()
    return bool(re.search(r"[dmyhs]", s)) and "general" not in s and "0" not in s.replace("00", "")


def _num(v: str) -> str:
    """A cell's number as written: 17 not 17.0, 1250.5 not 1250.4999999999998."""
    try:
        x = float(v)
    except ValueError:
        return v
    if x.is_integer() and abs(x) < 1e15:
        return str(int(x))
    return repr(round(x, 10))


def _serial(v: str, date1904: bool, kind: str) -> str:
    try:
        x = float(v)
    except ValueError:
        return v
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    t = base + timedelta(days=x)
    t = t.replace(microsecond=0) + timedelta(seconds=round(t.microsecond / 1e6))
    if kind == "time" or (x < 1 and kind != "date"):
        return t.strftime("%H:%M") if t.second == 0 else t.strftime("%H:%M:%S")
    if kind == "datetime" and (t.hour or t.minute or t.second):
        return t.strftime("%Y-%m-%d %H:%M")
    return t.strftime("%Y-%m-%d")


def _col(ref: str) -> int:
    n = 0
    for c in re.match(r"[A-Z]*", ref or "").group(0):  # type: ignore[union-attr]
        n = n * 26 + ord(c) - 64
    return n - 1


def read_xlsx(data: bytes) -> tuple[str, list[list[str]]]:
    """(text, rows of the first sheet that has any). Every sheet becomes '### Sheet: <name>'
    and its rows 'a | b | c'; date-styled numbers become ISO dates."""
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(_M + "si"):
            # rich text runs, but not the phonetic guide (rPh)
            parts = [t.text or "" for t in si.iter(_M + "t")]
            rph = [t.text or "" for r in si.iter(_M + "rPh") for t in r.iter(_M + "t")]
            shared.append("".join(parts[: len(parts) - len(rph)] if rph else parts))
    styles: list[str] = []  # cellXfs index -> "date" | "time" | "datetime" | ""
    if "xl/styles.xml" in names:
        st = ET.fromstring(z.read("xl/styles.xml"))
        custom = {
            int(nf.get("numFmtId", "0")): nf.get("formatCode", "") for nf in st.iter(_M + "numFmt")
        }
        xfs = st.find(_M + "cellXfs")
        for xf in xfs.findall(_M + "xf") if xfs is not None else []:
            fid = int(xf.get("numFmtId", "0"))
            code = custom.get(fid, "")
            if fid in _DATE_IDS or (code and _is_date_format(code)):
                low = re.sub(r'"[^"]*"|\[[^\]]*\]', "", code.lower())
                has_d = (
                    bool(re.search(r"[dy]", low))
                    or fid in (14, 15, 16, 17, 22)
                    or (fid in _DATE_IDS and fid not in (18, 19, 20, 21, 45, 46, 47))
                )
                has_t = bool(re.search(r"h|s", low)) or fid in (18, 19, 20, 21, 22, 45, 46, 47)
                styles.append("datetime" if has_d and has_t else ("time" if has_t else "date"))
            else:
                styles.append("")
    wb = ET.fromstring(z.read("xl/workbook.xml")) if "xl/workbook.xml" in names else None
    date1904 = False
    sheets: list[tuple[str, str, bool]] = []  # (name, part, hidden)
    if wb is not None:
        pr = wb.find(_M + "workbookPr")
        date1904 = pr is not None and str(pr.get("date1904", "")).lower() in ("1", "true")
        rels = {}
        if "xl/_rels/workbook.xml.rels" in names:
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels")).iter(
                _PKG + "Relationship"
            ):
                tgt = r.get("Target", "").lstrip("/")
                rels[r.get("Id")] = tgt if tgt.startswith("xl/") else "xl/" + tgt
        for i, s in enumerate(wb.iter(_M + "sheet"), 1):
            part = rels.get(s.get(_R + "id")) or f"xl/worksheets/sheet{i}.xml"
            sheets.append(
                (s.get("name") or f"Sheet{i}", part, s.get("state") in ("hidden", "veryHidden"))
            )
    if not sheets:
        parts = sorted(n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
        sheets = [(PurePosixPath(n).stem, n, False) for n in parts]
    out: list[str] = []
    first_rows: list[list[str]] = []
    for sname, part, hidden in sheets:
        if part not in names:
            continue
        out.append(f"### Sheet: {sname}" + (" (hidden)" if hidden else ""))
        rows: list[list[str]] = []
        for row in ET.fromstring(z.read(part)).iter(_M + "row"):
            cells: dict[int, str] = {}
            for i, c in enumerate(row.findall(_M + "c")):
                t = c.get("t", "n")
                v = c.find(_M + "v")
                raw = v.text if v is not None and v.text is not None else ""
                if t == "s" and raw:
                    val = shared[int(raw)] if int(raw) < len(shared) else ""
                elif t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(_M + "t"))
                elif t == "b":
                    val = "TRUE" if raw == "1" else "FALSE"
                elif t in ("str", "e"):
                    val = raw
                elif raw:
                    s = int(c.get("s", "0") or 0)
                    kind = styles[s] if s < len(styles) else ""
                    val = _serial(raw, date1904, kind) if kind else _num(raw)
                else:
                    val = ""
                cells[_col(c.get("r", "")) if c.get("r") else i] = val.strip()
            if any(cells.values()):
                width = max(cells) + 1
                rows.append([cells.get(i, "") for i in range(width)])
        for r in rows:
            out.append(" | ".join(r))
        out.append("")
        if rows and not first_rows:
            first_rows = rows
    return _tidy("\n".join(out)), first_rows


# ---------------------------------------------------------------- pdf
class _PDF:
    """Text of a PDF without a library: objects (also inside object streams), Flate /
    ASCIIHex / ASCII85 streams, the page tree, fonts with their ToUnicode CMaps and widths,
    and the text operators (Tj TJ ' " Td TD Tm T* Tf Do). Spaces come from TJ gaps and from
    moves wider than the text drawn; lines from vertical moves."""

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.objs: dict[int, bytes] = {}
        self.warnings: list[str] = []
        for m in re.finditer(rb"(\d+)\s+(\d+)\s+obj\b(.*?)\bendobj", data, re.S):
            self.objs[int(m.group(1))] = m.group(3)
        for body in list(self.objs.values()):
            if re.search(rb"/Type\s*/ObjStm", body[:400]):
                self._objstm(body)
        self.fonts: dict[int, dict[str, Any]] = {}

    # -- objects
    def _objstm(self, body: bytes) -> None:
        s = self.stream(body)
        n = re.search(rb"/N\s+(\d+)", body)
        first = re.search(rb"/First\s+(\d+)", body)
        if not s or not n or not first:
            return
        head = s[: int(first.group(1))].split()
        offs = [
            (int(head[i]), int(head[i + 1]))
            for i in range(0, min(len(head), 2 * int(n.group(1))) - 1, 2)
        ]
        for k, (num, off) in enumerate(offs):
            end = offs[k + 1][1] if k + 1 < len(offs) else len(s) - int(first.group(1))
            self.objs.setdefault(num, s[int(first.group(1)) + off : int(first.group(1)) + end])

    def obj(self, ref: Any) -> bytes:
        if isinstance(ref, int):
            return self.objs.get(ref, b"")
        m = re.fullmatch(rb"\s*(\d+)\s+\d+\s+R\s*", ref or b"")
        return self.objs.get(int(m.group(1)), b"") if m else (ref or b"")

    def stream(self, body: bytes) -> bytes | None:
        m = re.search(rb"stream\r?\n", body)
        if not m:
            return None
        dict_part = body[: m.start()]
        raw = body[m.end() :]
        ln = re.search(rb"/Length\s+(\d+)(?!\s+\d+\s+R)", dict_part)
        if ln and int(ln.group(1)) <= len(raw):
            raw = raw[: int(ln.group(1))]
        else:
            e = raw.rfind(b"endstream")
            raw = raw[:e] if e >= 0 else raw
            raw = raw.rstrip(b"\r\n")
        filters = re.findall(
            rb"/(FlateDecode|Fl|ASCIIHexDecode|AHx|ASCII85Decode|A85|DCTDecode|JPXDecode|CCITTFaxDecode|LZWDecode)",
            dict_part,
        )
        for f in filters:
            if f in (b"FlateDecode", b"Fl"):
                try:
                    raw = zlib.decompress(raw)
                except zlib.error:
                    d = zlib.decompressobj()
                    try:
                        raw = d.decompress(raw)
                    except zlib.error:
                        return None
            elif f in (b"ASCIIHexDecode", b"AHx"):
                h = re.sub(rb"[^0-9A-Fa-f]", b"", raw.split(b">")[0])
                raw = bytes.fromhex((h + b"0" * (len(h) % 2)).decode())
            elif f in (b"ASCII85Decode", b"A85"):
                try:
                    raw = base64.a85decode(raw.strip().removesuffix(b"~>"), adobe=False)
                except ValueError:
                    return None
            else:
                return None  # an image or a filter stdlib cannot undo: no text in it
        return raw

    def _dict_entry(self, body: bytes, key: bytes) -> bytes:
        """The raw value of /key in a dictionary: a reference, a name, a number or an inline
        <<...>> dictionary / [...] array (balanced)."""
        m = re.search(rb"/" + key + rb"(?![A-Za-z0-9])\s*", body)
        if not m:
            return b""
        i = m.end()
        if body[i : i + 2] == b"<<":
            depth, j = 0, i
            while j < len(body):
                if body[j : j + 2] == b"<<":
                    depth, j = depth + 1, j + 2
                    continue
                if body[j : j + 2] == b">>":
                    depth, j = depth - 1, j + 2
                    if depth == 0:
                        return body[i:j]
                    continue
                j += 1
            return body[i:]
        if body[i : i + 1] == b"[":  # balanced: /W [0 [506 0 226] 44 [630]]
            depth = 0
            for j in range(i, len(body)):
                c = body[j : j + 1]
                if c == b"[":
                    depth += 1
                elif c == b"]":
                    depth -= 1
                    if depth == 0:
                        return body[i : j + 1]
            return body[i:]
        r = re.match(rb"\d+\s+\d+\s+R|/[^\s/<>\[\]()]+|[-\d.]+", body[i:])
        return r.group(0) if r else b""

    def _resolved(self, body: bytes, key: bytes) -> bytes:
        v = self._dict_entry(body, key)
        return self.obj(v) if re.fullmatch(rb"\d+\s+\d+\s+R", v) else v

    # -- fonts
    def font(self, ref: bytes) -> dict[str, Any]:
        m = re.fullmatch(rb"(\d+)\s+\d+\s+R", ref.strip())
        key = int(m.group(1)) if m else id(ref)
        if key in self.fonts:
            return self.fonts[key]
        body = self.obj(ref) if m else ref
        info: dict[str, Any] = {
            "two": False,
            "map": {},
            "widths": {},
            "dw": 1000.0,
            "enc": "cp1252",
        }
        if re.search(rb"/Subtype\s*/Type0", body):
            info["two"] = True
            info["dw"] = 1000.0
            desc = self._dict_entry(body, b"DescendantFonts")
            dref = re.search(rb"(\d+)\s+\d+\s+R", desc)
            dfont = self.obj(dref.group(0)) if dref else desc
            dw = re.search(rb"/DW\s+([\d.]+)", dfont)
            if dw:
                info["dw"] = float(dw.group(1))
            w = self._resolved(dfont, b"W")
            info["widths"] = self._cid_widths(w)
        else:
            fc = re.search(rb"/FirstChar\s+(\d+)", body)
            ws = self._resolved(body, b"Widths")
            nums = re.findall(rb"[-\d.]+", ws)
            if fc and nums:
                info["widths"] = {int(fc.group(1)) + i: float(x) for i, x in enumerate(nums)}
            info["dw"] = 500.0
            enc = self._dict_entry(body, b"Encoding")
            if b"MacRoman" in enc:
                info["enc"] = "mac_roman"
            diffs = self._resolved(body, b"Encoding")
            info["diff"] = self._differences(diffs) if b"Differences" in diffs else {}
        tu = self._dict_entry(body, b"ToUnicode")
        if re.fullmatch(rb"\d+\s+\d+\s+R", tu):
            cm = self.stream(self.obj(tu))
            if cm:
                info["map"], two = self._cmap(cm)
                if two is not None:
                    info["two"] = two
        self.fonts[key] = info
        return info

    @staticmethod
    def _cid_widths(w: bytes) -> dict[int, float]:
        out: dict[int, float] = {}
        toks = re.findall(rb"\[|\]|[-\d.]+", w.strip()[1:-1] if w.strip().startswith(b"[") else w)
        i = 0
        while i < len(toks):
            if toks[i] in (b"[", b"]"):
                i += 1
                continue
            c = int(float(toks[i]))
            if i + 1 < len(toks) and toks[i + 1] == b"[":
                j = i + 2
                k = c
                while j < len(toks) and toks[j] != b"]":
                    out[k] = float(toks[j])
                    k, j = k + 1, j + 1
                i = j + 1
            elif i + 2 < len(toks):
                c2, wv = int(float(toks[i + 1])), float(toks[i + 2])
                for k in range(c, min(c2, c + 65535) + 1):
                    out[k] = wv
                i += 3
            else:
                break
        return out

    @staticmethod
    def _differences(enc: bytes) -> dict[int, str]:
        names = {
            "space": " ", "scedilla": "ş", "Scedilla": "Ş", "gbreve": "ğ", "Gbreve": "Ğ",
            "dotlessi": "ı", "Idotaccent": "İ", "udieresis": "ü", "Udieresis": "Ü",
            "odieresis": "ö", "Odieresis": "Ö", "ccedilla": "ç", "Ccedilla": "Ç",
        }  # fmt: skip
        out: dict[int, str] = {}
        body = enc[enc.find(b"Differences") :]
        body = body[body.find(b"[") + 1 : body.find(b"]")]
        code = 0
        for tok in re.findall(rb"\d+|/[^\s/\[\]]+", body):
            if tok[:1].isdigit():
                code = int(tok)
                continue
            n = tok[1:].decode("latin-1")
            if n in names:
                out[code] = names[n]
            elif re.fullmatch(r"uni[0-9A-Fa-f]{4}", n):
                out[code] = chr(int(n[3:], 16))
            elif len(n) == 1:
                out[code] = n
            code += 1
        return out

    @staticmethod
    def _utf16(h: bytes) -> str:
        try:
            return bytes.fromhex(h.decode()).decode("utf-16-be", "replace")
        except ValueError:
            return ""

    def _cmap(self, cm: bytes) -> tuple[dict[int, str], bool | None]:
        mp: dict[int, str] = {}
        two: bool | None = None
        spaces = re.findall(
            rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>",
            b"".join(re.findall(rb"begincodespacerange(.*?)endcodespacerange", cm, re.S)),
        )
        if spaces:
            two = all(len(a) >= 4 for a, _ in spaces)
        for blk in re.findall(rb"beginbfchar(.*?)endbfchar", cm, re.S):
            for a, c in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", blk):
                mp[int(a, 16)] = self._utf16(c)
        for blk in re.findall(rb"beginbfrange(.*?)endbfrange", cm, re.S):
            for a, z, rest in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", blk
            ):
                lo, hi = int(a, 16), int(z, 16)
                if rest.startswith(b"["):
                    for i, c in enumerate(re.findall(rb"<([0-9A-Fa-f]+)>", rest)):
                        mp[lo + i] = self._utf16(c)
                    continue
                dst = rest.strip(b"<>")
                start = self._utf16(dst)
                for i in range(0, min(hi - lo, 65535) + 1):
                    if start:
                        mp[lo + i] = start[:-1] + chr(ord(start[-1]) + i)
        return mp, two

    def decode(self, f: dict[str, Any], raw: bytes) -> tuple[str, float]:
        """(text, advance in thousandths of the font size) of a string's bytes."""
        out, adv = [], 0.0
        if f["two"]:
            codes = [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw) - 1, 2)]
        else:
            codes = list(raw)
        for c in codes:
            ch = f["map"].get(c)
            if ch is None:
                if f["two"]:
                    ch = "�"
                else:
                    ch = (f.get("diff") or {}).get(c) or bytes([c]).decode(f["enc"], "replace")
            out.append(ch)
            adv += f["widths"].get(c, f["dw"])
        return "".join(out), adv

    # -- content
    @staticmethod
    def _pdf_string(s: bytes, i: int) -> tuple[bytes, int]:
        """A (literal) string starting at s[i] == '(' -> (bytes, index after it)."""
        out = bytearray()
        depth, i = 1, i + 1
        esc = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f"}
        while i < len(s):
            c = s[i : i + 1]
            if c == b"\\":
                n = s[i + 1 : i + 2]
                if n in esc:
                    out += esc[n]
                    i += 2
                elif n.isdigit():
                    m = re.match(rb"[0-7]{1,3}", s[i + 1 : i + 4])
                    out.append(int(m.group(0), 8) & 255)  # type: ignore[union-attr]
                    i += 1 + len(m.group(0))  # type: ignore[union-attr]
                elif n in (b"\r", b"\n"):
                    i += 2 + (s[i + 1 : i + 3] == b"\r\n")
                else:
                    out += n
                    i += 2
                continue
            if c == b"(":
                depth += 1
            elif c == b")":
                depth -= 1
                if depth == 0:
                    return bytes(out), i + 1
            out += c
            i += 1
        return bytes(out), i

    def _tokens(self, s: bytes):
        i, n = 0, len(s)
        while i < n:
            c = s[i : i + 1]
            if c in b" \t\r\n\f\x00":
                i += 1
            elif c == b"%":
                j = s.find(b"\n", i)
                i = n if j < 0 else j
            elif c == b"(":
                v, i = self._pdf_string(s, i)
                yield ("str", v)
            elif s[i : i + 2] == b"<<":
                yield ("op", b"<<")
                i += 2
            elif s[i : i + 2] == b">>":
                yield ("op", b">>")
                i += 2
            elif c == b"<":
                j = s.find(b">", i)
                j = n if j < 0 else j
                h = re.sub(rb"[^0-9A-Fa-f]", b"", s[i + 1 : j])
                yield ("str", bytes.fromhex((h + b"0" * (len(h) % 2)).decode()))
                i = j + 1
            elif c in b"[]":
                yield (c.decode(), None)
                i += 1
            elif c == b"/":
                m = re.match(rb"/[^\s/<>\[\]()%{}]*", s[i:])
                yield ("name", m.group(0)[1:])  # type: ignore[union-attr]
                i += len(m.group(0))  # type: ignore[union-attr]
            else:
                m = re.match(rb"[^\s/<>\[\]()%{}]+", s[i:])
                if not m:
                    i += 1
                    continue
                w = m.group(0)
                i += len(w)
                if re.fullmatch(rb"[-+]?(\d+\.?\d*|\.\d+)", w):
                    yield ("num", float(w))
                elif w == b"BI":  # an inline image: skip to its end
                    j = s.find(b"EI", i)
                    i = n if j < 0 else j + 2
                else:
                    yield ("op", w)

    def run(self, content: bytes, resources: bytes, out: list[str], depth: int = 0) -> None:
        fonts_dict = self._resolved(resources, b"Font")
        xobjs = self._resolved(resources, b"XObject")
        font: dict[str, Any] = {"two": False, "map": {}, "widths": {}, "dw": 500.0, "enc": "cp1252"}
        size = 12.0
        line_x = line_y = 0.0  # start of the current text line (text space)
        drawn = 0.0  # advance drawn since the line start, text space units
        stack: list[Any] = []
        arr: list[Any] | None = None

        def put(text: str) -> None:
            out.append(text)

        def newline() -> None:
            if out and not out[-1].endswith("\n"):
                out.append("\n")

        def space() -> None:
            if out and not out[-1].endswith((" ", "\n")):
                out.append(" ")

        def move(tx: float, ty: float, absolute: bool = False) -> None:
            nonlocal line_x, line_y, drawn
            nx, ny = (tx, ty) if absolute else (line_x + tx, line_y + ty)
            if abs(ny - line_y) > max(size * 0.3, 0.5):
                newline()
            elif nx - (line_x + drawn) > size * 0.15:
                space()
            line_x, line_y, drawn = nx, ny, 0.0

        def show(raw: bytes) -> None:
            nonlocal drawn
            t, adv = self.decode(font, raw)
            put(t)
            drawn += adv / 1000.0 * size

        for kind, v in self._tokens(content):
            if kind == "[":
                arr = []
                continue
            if kind == "]":
                stack.append(arr if arr is not None else [])
                arr = None
                continue
            if arr is not None:
                arr.append(v if kind != "op" else None)
                continue
            if kind != "op":
                stack.append(v)
                continue
            op = v
            try:
                if op == b"Tf" and len(stack) >= 2:
                    name, size = stack[-2], float(stack[-1]) or size
                    ref = self._dict_entry(fonts_dict, name) if isinstance(name, bytes) else b""
                    if ref:
                        font = self.font(ref)
                elif op == b"BT":
                    line_x = line_y = drawn = 0.0
                elif op == b"ET":
                    pass
                elif op in (b"Td", b"TD") and len(stack) >= 2:
                    move(float(stack[-2]), float(stack[-1]))
                elif op == b"Tm" and len(stack) >= 6:
                    move(float(stack[-2]), float(stack[-1]), absolute=True)
                elif op == b"T*":
                    newline()
                    drawn = 0.0
                elif op == b"Tj" and stack and isinstance(stack[-1], bytes):
                    show(stack[-1])
                elif op in (b"'", b'"') and stack and isinstance(stack[-1], bytes):
                    newline()
                    drawn = 0.0
                    show(stack[-1])
                elif op == b"TJ" and stack and isinstance(stack[-1], list):
                    for part in stack[-1]:
                        if isinstance(part, bytes):
                            show(part)
                        elif isinstance(part, float):
                            if part < -180:
                                space()
                            drawn -= part / 1000.0 * size
                elif op == b"Do" and stack and isinstance(stack[-1], bytes) and depth < 5:
                    ref = self._dict_entry(xobjs, stack[-1])
                    body = self.obj(ref)
                    if re.search(rb"/Subtype\s*/Form", body[:600]):
                        s = self.stream(body)
                        res = self._resolved(body, b"Resources") or resources
                        if s:
                            newline()
                            self.run(s, res, out, depth + 1)
                            newline()
            except (ValueError, TypeError, IndexError):
                pass
            stack = []

    def pages(self) -> list[bytes]:
        root = re.search(rb"/Root\s+(\d+\s+\d+\s+R)", self.data)
        cat = self.obj(root.group(1)) if root else b""
        if not cat:
            cat = next((o for o in self.objs.values() if re.search(rb"/Type\s*/Catalog", o)), b"")
        out: list[bytes] = []
        seen: set[bytes] = set()

        def walk(ref: bytes, inherited: bytes, depth: int) -> None:
            if depth > 30 or ref in seen:
                return
            seen.add(ref)
            node = self.obj(ref)
            res = self._dict_entry(node, b"Resources") or inherited
            if re.search(rb"/Type\s*/Pages", node):
                kids = self._dict_entry(node, b"Kids")
                for k in re.findall(rb"\d+\s+\d+\s+R", kids):
                    walk(k, res, depth + 1)
            elif re.search(rb"/Type\s*/Page\b", node):
                out.append(node + b"\n/__res " + res if res else node)

        pages_ref = self._dict_entry(cat, b"Pages")
        if pages_ref:
            walk(pages_ref, b"", 0)
        if not out:  # no readable page tree: every page object in file order
            out = [o for o in self.objs.values() if re.search(rb"/Type\s*/Page\b", o)]
        return out

    def text(self) -> str:
        if re.search(rb"/Encrypt\s", self.data[-4000:]) or re.search(
            rb"trailer[^%]*/Encrypt", self.data
        ):
            raise ValueError("the PDF is encrypted")
        out: list[str] = []
        for i, page in enumerate(self.pages()):
            res_raw = page.split(b"\n/__res ", 1)
            page_body = res_raw[0]
            res = res_raw[1] if len(res_raw) > 1 else self._dict_entry(page_body, b"Resources")
            if re.fullmatch(rb"\s*\d+\s+\d+\s+R\s*", res or b""):
                res = self.obj(res)
            contents = self._dict_entry(page_body, b"Contents")
            refs = re.findall(rb"\d+\s+\d+\s+R", contents)
            streams = []
            for r in refs:
                body = self.obj(r)
                if body.lstrip().startswith(b"["):  # a reference to an array of streams
                    streams += [
                        self.stream(self.obj(x)) for x in re.findall(rb"\d+\s+\d+\s+R", body)
                    ]
                else:
                    streams.append(self.stream(body))
            content = b"\n".join(s for s in streams if s)
            if i:
                out.append(f"\n[page {i + 1}]\n")
            self.run(content, res or b"", out)
        return "".join(out)


def read_pdf(data: bytes) -> tuple[str, list[str]]:
    """(text, warnings). Raises ValueError when the PDF has no readable text (a scan, an
    encrypted file, fonts without a Unicode map)."""
    if not data.startswith(b"%PDF") and b"%PDF" not in data[:1024]:
        raise ValueError("not a PDF file")
    pdf = _PDF(data)
    text = _tidy(pdf.text())
    letters = sum(ch.isalpha() for ch in text)
    bad = text.count("�")
    if letters < 20:
        raise ValueError(
            "no text layer could be read (a scanned or image-only PDF, or fonts without a "
            "Unicode map); convert it to text or Word and run again"
        )
    warns = list(pdf.warnings)
    if bad > letters * 0.05:
        warns.append(f"{bad} characters could not be mapped to Unicode (shown as �)")
    return text, warns


# ---------------------------------------------------------------- yaml (a small subset)
class YAMLError(ValueError):
    pass


def _yaml_scalar(s: str) -> Any:
    s = s.strip()
    if not s:
        return None
    if s[0] in "\"'":
        q = s[0]
        end = s.rfind(q)
        if end <= 0:
            raise YAMLError(f"unterminated string: {s[:40]}")
        body = s[1:end]
        if q == "'":
            return body.replace("''", "'")
        try:
            return json.loads('"' + body + '"')
        except ValueError:
            return body
    if s[0] in "[{":
        return _yaml_flow(s)
    low = s.lower()
    if low in ("null", "~"):
        return None
    if low in ("true", "yes", "on"):
        return True if low == "true" else s
    if low in ("false", "no", "off"):
        return False if low == "false" else s
    if re.fullmatch(r"[-+]?\d+", s):
        return int(s)
    if re.fullmatch(r"[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?", s):
        return float(s)
    return s


def _yaml_flow(s: str) -> Any:
    """A flow collection ([a, b], {a: 1}) of plain or quoted scalars, nested."""
    pos = 0

    def ws() -> None:
        nonlocal pos
        while pos < len(s) and s[pos] in " \t\n":
            pos += 1

    def item() -> Any:
        nonlocal pos
        ws()
        if s[pos] == "[":
            pos += 1
            out = []
            ws()
            if s[pos] == "]":
                pos += 1
                return out
            while True:
                out.append(item())
                ws()
                if s[pos] == ",":
                    pos += 1
                    continue
                if s[pos] == "]":
                    pos += 1
                    return out
                raise YAMLError("bad flow sequence")
        if s[pos] == "{":
            pos += 1
            d: dict[str, Any] = {}
            ws()
            if s[pos] == "}":
                pos += 1
                return d
            while True:
                k = scalar(":")
                pos += 1
                d[str(k)] = item()
                ws()
                if s[pos] == ",":
                    pos += 1
                    continue
                if s[pos] == "}":
                    pos += 1
                    return d
                raise YAMLError("bad flow mapping")
        return scalar(",]}")

    def scalar(stops: str) -> Any:
        nonlocal pos
        ws()
        if s[pos] in "\"'":
            q = s[pos]
            j = pos + 1
            while j < len(s):
                if s[j] == q and not (q == '"' and s[j - 1] == "\\"):
                    if q == "'" and s[j + 1 : j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            v = _yaml_scalar(s[pos : j + 1])
            pos = j + 1
            return v
        j = pos
        while j < len(s) and s[j] not in stops:
            j += 1
        v = _yaml_scalar(s[pos:j])
        pos = j
        return v

    try:
        return item()
    except IndexError:
        raise YAMLError(f"unterminated flow collection: {s[:60]}") from None


def _strip_comment(line: str) -> str:
    out, q = [], ""
    for i, c in enumerate(line):
        if q:
            if c == q:
                q = ""
        elif c in "\"'" and (i == 0 or line[i - 1] in " \t:[{,-"):
            q = c
        elif c == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(c)
    return "".join(out).rstrip()


def load_yaml(text: str) -> Any:
    """Block mappings and sequences, flow collections, quoted and plain scalars, block scalars
    (| and >), comments. Enough for an OpenAPI file; anchors, tags and multi-documents are
    not supported (YAMLError)."""
    raw = text.replace("\t", "    ").splitlines()
    lines: list[tuple[int, str, int]] = []  # (indent, content, raw index)
    for i, ln in enumerate(raw):
        if ln.strip() in ("---", "...") and not lines:
            continue
        s = _strip_comment(ln)
        if s.strip():
            lines.append((len(s) - len(s.lstrip(" ")), s.strip(), i))
    if any(re.match(r"^(&|\*|!)", c.split(": ", 1)[-1]) for _, c, _ in lines):
        raise YAMLError("anchors, aliases and tags are not supported")
    pos = 0

    def block_scalar(indicator: str, parent: int, at: int) -> tuple[str, int]:
        # raw lines after `at` indented deeper than the parent
        body, j = [], at + 1
        ind = None
        while j < len(raw):
            ln = raw[j].replace("\t", "    ")
            if ln.strip() and len(ln) - len(ln.lstrip(" ")) <= parent:
                break
            if ln.strip() and ind is None:
                ind = len(ln) - len(ln.lstrip(" "))
            body.append(ln[ind:] if ind is not None and ln.strip() else "")
            j += 1
        while body and not body[-1]:
            body.pop()
        txt = (
            "\n".join(body)
            if indicator.startswith("|")
            else re.sub(r"(?<!\n)\n(?!\n)", " ", "\n".join(body))
        )
        if not indicator.endswith("-"):
            txt += "\n"
        return txt, j

    def skip_to(raw_index: int) -> None:
        nonlocal pos
        while pos < len(lines) and lines[pos][2] < raw_index:
            pos += 1

    def value(rest: str, indent: int, at: int) -> Any:
        nonlocal pos
        if rest in ("|", "|-", "|+", ">", ">-", ">+"):
            txt, nxt = block_scalar(rest, indent, at)
            skip_to(nxt)
            return txt
        if rest:
            return _yaml_scalar(rest)
        if pos < len(lines) and (
            lines[pos][0] > indent or (lines[pos][0] == indent and lines[pos][1].startswith("- "))
        ):
            return block(lines[pos][0])
        return None

    def block(indent: int) -> Any:
        nonlocal pos
        if pos >= len(lines):
            return None
        if lines[pos][1] == "-" or lines[pos][1].startswith("- "):
            seq = []
            while (
                pos < len(lines)
                and lines[pos][0] == indent
                and (lines[pos][1] == "-" or lines[pos][1].startswith("- "))
            ):
                _, content, at = lines[pos]
                rest = content[1:].strip()
                if not rest:
                    pos += 1
                    seq.append(value("", indent, at))
                elif re.match(r"^(\"[^\"]*\"|'[^']*'|[^\s\"'\[{][^:]*?):(\s|$)", rest):
                    # "- key: value" opens a mapping indented past the dash
                    lines[pos] = (indent + 2, rest, at)
                    seq.append(block(indent + 2))
                else:
                    pos += 1
                    seq.append(value(rest, indent, at))
            return seq
        out: dict[str, Any] = {}
        while pos < len(lines) and lines[pos][0] == indent:
            _, content, at = lines[pos]
            m = re.match(
                r"^(\"(?:[^\"\\]|\\.)*\"|'(?:[^']|'')*'|[^\s\"'][^:]*?|\?)\s*:(?:\s+(.*))?$",
                content,
            )
            if not m:
                raise YAMLError(f"line {at + 1}: expected 'key: value', got {content[:60]!r}")
            k = m.group(1)
            key = _yaml_scalar(k) if k[0] in "\"'" else k
            pos += 1
            out[str(key)] = value((m.group(2) or "").strip(), indent, at)
        if pos < len(lines) and lines[pos][0] > indent:
            raise YAMLError(f"line {lines[pos][2] + 1}: unexpected indentation")
        return out

    if not lines:
        return None
    result = block(lines[0][0])
    if pos < len(lines):
        raise YAMLError(f"line {lines[pos][2] + 1}: could not be read")
    return result


def load_json_or_yaml(text: str) -> Any:
    """JSON first (the usual OpenAPI file), then the YAML subset."""
    t = text.lstrip("﻿")
    try:
        return json.loads(t)
    except ValueError:
        return load_yaml(t)


def is_openapi(doc: Any) -> bool:
    return (
        isinstance(doc, dict)
        and isinstance(doc.get("paths"), dict)
        and bool(doc.get("openapi") or doc.get("swagger"))
    )


def openapi_in_text(text: str) -> dict[str, Any] | None:
    """An OpenAPI document written inside a text document (a ```json or ```yaml block, or the
    whole text), else None."""
    for m in re.finditer(r"```[ \t]*(json|ya?ml|)[^\n]*\n(.*?)```", text, re.S | re.I):
        try:
            doc = load_json_or_yaml(m.group(2))
        except ValueError:
            continue
        if is_openapi(doc):
            return doc
    if re.match(r"\s*(\{|openapi\s*:|swagger\s*:)", text):
        try:
            doc = load_json_or_yaml(text)
        except ValueError:
            return None
        if is_openapi(doc):
            return doc
    return None


# ---------------------------------------------------------------- csv and tables
def read_csv(text: str) -> list[list[str]]:
    sample = text[:4000]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
        if sample.count(";") > sample.count(","):
            dialect = type("semi", (csv.excel,), {"delimiter": ";"})
    return [
        [c.strip() for c in row]
        for row in csv.reader(io.StringIO(text), dialect)
        if any(c.strip() for c in row)
    ]


def table_rows(text: str) -> list[list[str]]:
    """Rows of the first pipe table in a text (Markdown, or docx/xlsx text as read here)."""
    rows: list[list[str]] = []
    for ln in text.splitlines():
        s = ln.strip()
        if "|" not in s or s.startswith("#"):
            if rows:
                break
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
            continue
        rows.append(cells)
    return rows if len(rows) >= 2 else []


def rows_to_dicts(rows: list[list[str]]) -> list[dict[str, str]]:
    if len(rows) < 2:
        return []
    head = [h or f"col{i + 1}" for i, h in enumerate(rows[0])]
    return [
        {head[i] if i < len(head) else f"col{i + 1}": v for i, v in enumerate(r) if v}
        for r in rows[1:]
        if any(r)
    ]


def _tidy(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# ---------------------------------------------------------------- roles
# Checked in this order; patterns run on folded text (lower case, Turkish letters as ASCII).
_ROLE_PATTERNS: list[tuple[str, list[str]]] = [
    (
        "example_conversation",
        [
            r"\b(ornek|example|sample|demo)\w* "
            r"(konusma|gorusme|diyalog|cagri|conversation|call|dialog|transcript)",
            r"\b(konusma|gorusme|diyalog|conversation|dialog|transcript)\w* (ornek|example|sample)",
            r"\btranscripts?\b",
            r"\b(konusma|gorusme|conversation|dialog)\w* \d+\b",
        ],
    ),
    (
        "test_customers",
        [
            r"\btest\w* (musteri|customer|kisi|persona|veri|data|account|hesap)",
            r"\b(musteri|customer)\w* (listesi|tablosu|verisi|verileri|list|table|data)\b",
            r"\b(sample|ornek) (musteri|customer)",
        ],
    ),
    (
        "contract",
        [
            r"\b(servis|hizmet|api|web servis|entegrasyon)\w* "
            r"(sozlesme|tanim|dokuman|belge|spesifikasyon)",
            r"\bservice (contract|definition|specification)\b",
            r"\b(openapi|swagger|endpoints?|uc nokta|api contract|tool api)\b",
        ],
    ),
    (
        "faq",
        [
            r"\b(sss|faq|faqs)\b",
            r"\bq ?(&|and|ve) ?a\b",
            r"\bsoru\w* (ve|-|&) ?cevap",
            r"\bsoru-cevap\b",
            r"\bsik(ca)? sorulan",
            r"\bquestions? (and|&) answers?\b",
        ],
    ),
    (
        "announcement",
        [
            r"\bduyuru",
            r"\bannouncement",
            r"\bmemo\b",
            r"\bgenelge",
            r"\bsirkuler",
            r"\bcircular",
            r"\bbildiri\w*\b",
            r"\bnotice\b",
        ],
    ),
    ("flow", [r"\bakis", r"\bflow", r"\b(diyagram|diagram|flowchart)", r"\b(cizim|drawing)\b"]),
    ("analysis", [r"\banaliz", r"\banalysis\b", r"\bgereksinim", r"\brequirements?\b"]),
    (
        "policy",
        [
            r"\bbrief\b",
            r"\bpolitika",
            r"\bpolicy\b",
            r"\bprosedur",
            r"\bprocedure",
            r"\byonerge",
            r"\btalimat",
            r"\bkurallar",
        ],
    ),
]
# a "contract" (sozlesme) with nothing technical in it is a customer agreement, i.e. policy
_WEAK_CONTRACT = re.compile(r"\bsozlesme|\bcontract\b")
_API_MARKERS = re.compile(
    r"\b(post|get|put|patch|delete)\s+/|\bendpoint|\bjson\b|\bistek\b|\byanit\b|\brequest\b|"
    r"\bresponse\b|\bparametre|\bparameter|\balan(lar)?\b|\bfield",
    re.I,
)


def role_from_text(text: str) -> str:
    """The role a description or a file name points to, '' when none does."""
    s = fold(text)
    for role, pats in _ROLE_PATTERNS:
        if any(re.search(p, s) for p in pats):
            return role
    if _WEAK_CONTRACT.search(s):
        return "contract?"
    return ""


def role_from_content(doc: Document) -> str:
    text = doc.text
    if doc.data is not None and is_openapi(doc.data):
        return "contract"
    turns = len(
        re.findall(
            r"(?im)^\W{0,3}(m[uü][sş]teri|ajan|temsilci|asistan|agent|customer|bot|arayan|caller|"
            r"operat[oö]r|banka|bank)\W{0,3}\s*:",
            text,
        )
    )
    if turns >= 4:
        return "example_conversation"
    qa = len(
        re.findall(
            r"(?im)^\W{0,3}(s|soru|q|question|c|cevap|a|answer|yan[iı]t)\s*\d*\s*[:.)-]", text
        )
    )
    if qa >= 4:
        return "faq"
    rows = doc.rows or table_rows(text)
    if rows and len(rows) >= 2:
        head = fold(" ".join(rows[0]))
        if re.search(
            r"\b(musteri|customer|ad|adi|soyad|name|tckn|kimlik|dogum|birth|telefon|phone)\b", head
        ):
            return "test_customers"
    if len(re.findall(r"(?im)\b(POST|GET|PUT|PATCH|DELETE)\s+/[\w{}/.-]*", text)) >= 2:
        return "contract"
    return ""


def readme_descriptions(readme: str, names: list[str]) -> dict[str, str]:
    """name -> what README.md says about it: the lines that name the file (its full name, its
    name without the extension, or a folder it is in), and the line after a line that is
    only the name (a heading or a list item followed by its description)."""
    lines = readme.splitlines()
    folded = [fold(ln) for ln in lines]
    out: dict[str, str] = {}
    for name in names:
        p = PurePosixPath(name)
        keys = {fold(p.name), fold(p.stem)} | {fold(x) for x in p.parts[:-1]}
        keys = {k for k in keys if len(k) >= 3}
        hits: list[str] = []
        for i, ln in enumerate(folded):
            if not any(re.search(r"(?<![\w.-])" + re.escape(k) + r"(?![\w-])", ln) for k in keys):
                continue
            hits.append(lines[i])
            rest = " " + re.sub(r"[\W_]+", " ", ln) + " "
            for k in keys:
                rest = rest.replace(" " + re.sub(r"[\W_]+", " ", k).strip() + " ", " ")
            rest = re.sub(r"\b(md|docx|xlsx|pdf|txt|csv|json|ya?ml)\b", " ", rest)
            if not rest.split():  # the line is only the name: the next one describes it
                nxt = next(
                    (lines[j] for j in range(i + 1, min(i + 4, len(lines))) if lines[j].strip()), ""
                )
                hits.append(nxt)
        if hits:
            out[name] = " ".join(hits)
    return out


def _date_in_name(name: str) -> str:
    s = PurePosixPath(name).stem
    m = re.search(r"(20\d\d)[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])", s)
    if m:
        y, mo, d = m.groups()
    else:
        m = re.search(r"(0[1-9]|[12]\d|3[01])[-_.](0[1-9]|1[0-2])[-_.](20\d\d)", s)
        if not m:
            return ""
        d, mo, y = m.groups()
    try:
        return date(int(y), int(mo), int(d)).isoformat()
    except ValueError:
        return ""


def assign_roles(docs: list[Document]) -> None:
    """README.md's description first, then the file name, then the content; BRIEF.md is
    always the policy and README.md the readme."""
    readme = next(
        (
            d
            for d in docs
            if fold(PurePosixPath(d.name).name) in ("readme.md", "readme.txt", "readme")
            and not d.error
        ),
        None,
    )
    desc = (
        readme_descriptions(readme.text, [d.name for d in docs if d is not readme])
        if readme
        else {}
    )
    for d in docs:
        base = fold(PurePosixPath(d.name).stem)
        if d is readme:
            d.role, d.role_from = "readme", "name"
            continue
        if base == "brief":
            d.role, d.role_from = "policy", "name"
            continue
        role, src = "", ""
        if d.name in desc:
            role, src = role_from_text(desc[d.name]), "readme"
        if not role or role == "contract?":
            by_name = role_from_text(_words(d.name))
            if by_name and (not role or by_name != "contract?"):
                role, src = by_name, "name"
        if not d.error and (not role or role == "contract?"):
            by_content = role_from_content(d)
            if by_content:
                role, src = by_content, "content"
        if role == "contract?":
            # "sozlesme" alone: a service contract only when it reads like one
            role = (
                "contract"
                if _API_MARKERS.search(d.text[:20000]) or d.data is not None
                else "policy"
            )
        if not role and d.data is not None and is_openapi(d.data):
            role, src = "contract", "content"
        d.role, d.role_from = (role, src) if role else ("other", "default")
        if d.role == "announcement":
            d.dated = _date_in_name(d.name)


# ---------------------------------------------------------------- reading files
def read_document(name: str, data: bytes) -> Document:
    """One file -> a Document with its text (or the reason it could not be read)."""
    doc = Document(name=name, size=len(data))
    ext = PurePosixPath(name).suffix.lower()
    try:
        if ext in (".md", ".markdown", ".txt", ".text", ".rst", ""):
            doc.text, enc = decode_text(data)
            doc.text, doc.reader = _tidy(doc.text.lstrip("﻿")), f"text ({enc})"
            if ext in (".md", ".markdown", ".txt"):
                found = openapi_in_text(doc.text)
                if found:
                    doc.data = found
        elif ext == ".docx" or ext == ".docm":
            doc.text, doc.reader = read_docx(data), "docx"
        elif ext in (".xlsx", ".xlsm"):
            doc.text, doc.rows = read_xlsx(data)
            doc.reader = "xlsx"
        elif ext == ".pdf":
            doc.text, doc.warnings = read_pdf(data)
            doc.reader = "pdf"
        elif ext in (".csv", ".tsv"):
            text, enc = decode_text(data)
            doc.rows = read_csv(text.lstrip("﻿"))
            doc.text = "\n".join(" | ".join(r) for r in doc.rows)
            doc.reader = f"csv ({enc})"
        elif ext in (".json", ".yaml", ".yml"):
            text, enc = decode_text(data)
            text = text.lstrip("﻿")
            doc.reader = f"{'json' if ext == '.json' else 'yaml'} ({enc})"
            try:
                doc.data = json.loads(text) if ext == ".json" else load_json_or_yaml(text)
            except ValueError as e:
                doc.warnings.append(
                    f"could not be parsed ({type(e).__name__}: {str(e)[:160]}); read as text"
                )
            doc.text = _tidy(text)
            if (
                isinstance(doc.data, list)
                and doc.data
                and all(isinstance(x, dict) for x in doc.data)
            ):
                keys = list(dict.fromkeys(k for x in doc.data for k in x))
                doc.rows = [keys] + [[str(x.get(k, "")) for k in keys] for x in doc.data]
        elif ext == ".pptx" or ext == ".vsdx" or ext in (".odt", ".ods", ".odp"):
            doc.text, doc.reader = read_ooxml_text(data, ext[1:]), ext[1:]
        elif ext == ".svg":
            doc.text, doc.reader = read_svg(data), "svg"
        elif ext in (".drawio", ".dio"):
            doc.text, doc.reader = read_drawio(data), "drawio"
        elif ext in (".html", ".htm"):
            text, enc = decode_text(data)
            doc.text, doc.reader = (
                _tidy(_strip_tags(re.sub(r"(?is)<(script|style).*?</\1>", "", text))),
                f"html ({enc})",
            )
        elif ext in _IMAGES:
            doc.error = (
                "an image: no text reader (a flow drawing as a picture needs a vision model)"
            )
        elif ext in (
            ".doc",
            ".xls",
            ".ppt",
            ".xlsb",
            ".rtf",
            ".pages",
            ".numbers",
            ".zip",
            ".rar",
            ".7z",
        ):
            doc.error = f"{ext} files cannot be read without a library; save it as docx/xlsx/pdf/md"
        else:
            text, enc = decode_text(data)
            if text.count("�") > len(text) * 0.01 or "\x00" in text:
                doc.error = f"unknown file type {ext!r} and not text"
            else:
                doc.text, doc.reader = _tidy(text), f"text ({enc})"
    except Exception as e:  # one unreadable file never stops the build
        doc.error = f"{type(e).__name__}: {str(e)[:200]}"
        doc.text = ""
    if not doc.error and not doc.text.strip() and doc.data is None:
        doc.error = "no text in it"
    return doc


def _zip_name(info: zipfile.ZipInfo) -> str:
    """A member's name as written: zip tools without the UTF-8 flag store UTF-8 anyway, or
    the OEM code page (Turkish Windows: cp857) that zipfile reads as cp437."""
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except UnicodeEncodeError:
        return name
    for enc in ("utf-8", "cp857"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return name


def _files(path: Path) -> tuple[str, list[tuple[str, bytes]]]:
    """(case name, [(relative name, bytes)]) for a folder, a zip or one file."""
    if path.is_dir():
        files = []
        for p in sorted(path.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(path).as_posix()
            if _SKIP.search(rel) or any(part.startswith(".") for part in PurePosixPath(rel).parts):
                continue
            files.append((rel, p.read_bytes()))
        return slot_name(path), files
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            members = [(_zip_name(i), i) for i in z.infolist() if not i.is_dir()]
            members = [(n, i) for n, i in members if not _SKIP.search(n)]
            tops = {
                PurePosixPath(n).parts[0] for n, _ in members if len(PurePosixPath(n).parts) > 1
            }
            single = len(tops) == 1 and all(len(PurePosixPath(n).parts) > 1 for n, _ in members)
            files = []
            for n, i in sorted(members, key=lambda x: x[0]):
                rel = "/".join(PurePosixPath(n).parts[1:]) if single else n
                files.append((rel, z.read(i)))
            name = next(iter(tops)) if single else path.stem
            return re.sub(r"[^\w.-]+", "-", name).strip("-.") or "final", files
    return slot_name(path), [(path.name, path.read_bytes())]


def _section(d: Document) -> str:
    extra = f", dated {d.dated}" if d.dated else ""
    return f"## {d.name} ({d.role}{extra})\n\n{d.text.strip()}\n"


def load_case(path: str | Path) -> Case:
    """Read a case folder, a zip of one or a single document. Never raises for a file that
    cannot be read (it is listed in Case.unread()); raises FileNotFoundError when the path
    does not exist and ValueError when it holds no readable document."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{path}: no such file or folder")
    name, files = _files(p)
    docs = [read_document(n, b) for n, b in files]
    assign_roles(docs)
    case = Case(root=str(p), name=name, documents=docs)
    ok = [d for d in docs if not d.error]
    if not ok:
        raise ValueError(f"{path}: no readable document ({len(docs)} files)")
    # an OpenAPI document: a JSON/YAML file first, then one written inside a contract document
    for d in sorted(
        ok,
        key=lambda d: (
            PurePosixPath(d.name).suffix.lower() not in (".json", ".yaml", ".yml"),
            d.name,
        ),
    ):
        if is_openapi(d.data):
            case.openapi, case.openapi_source = d.data, d.name
            d.role = "contract"
            break
    order = {r: i for i, r in enumerate(POLICY_ORDER)}
    pol = [d for d in ok if d.role in order]
    # BRIEF.md first ("read BRIEF.md first"), then README, then the rest by role
    pol.sort(
        key=lambda d: (
            fold(PurePosixPath(d.name).stem) != "brief",
            order[d.role],
            d.dated or "9999",
            d.name,
        )
    )
    case.policy_text = "\n".join(_section(d) for d in pol).strip()
    ex = [d for d in ok if d.role == "example_conversation"]
    if ex:
        body = "\n".join(_section(d) for d in ex).strip()
        case.examples_text = f"# {EXAMPLES_TITLE}\n\n{EXAMPLES_NOTE}\n\n{body}"
    contracts = [d for d in ok if d.role == "contract" and d.name != case.openapi_source]
    case.contract_text = "\n".join(_section(d) for d in contracts).strip()
    for d in ok:
        if d.role == "test_customers":
            case.customers += rows_to_dicts(d.rows or table_rows(d.text))
    return case
