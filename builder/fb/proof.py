"""Proof map: each rule of the brief -> the spec keys and workflow nodes that carry it.

Deterministic, no model. The brief is split into rule units (numbered lines, bold policy items
such as **4.2, table rows, other paragraphs that state a rule). Each unit gets kinds from a
keyword table; a kind names the spec keys and the nodes that implement it. The literals a unit
states (whole numbers, ISO dates, HH:MM times, codes, quoted sentences the agent must say,
quoted words it must not say before the identity check) are looked up in the spec and in the
workflow's strings (code and packed code included).

Status per unit:
    values_ok       literals found, the rule's kinds are implemented
    values_missing  a literal or a mandatory quoted sentence is in neither spec nor workflow
    covered         no literals; a kind with code / spec support is implemented
    prompt_only     only kinds the agent model handles from its prompt (AI, scam, no info...)
    unmapped        no kind matched, or nothing in the spec / workflow implements it

It never raises on a strange brief or workflow shape: `proof_map` returns what it could read.
"""

from __future__ import annotations

import ast
import json
import re
from collections import Counter
from typing import Any

from fb.spec_extract import _Docs, date_mentions, detect_language, fold, time_mentions
from fb.validate import _packed_blobs, _unpack

# kind -> (regex on the unit's lower-case text, spec keys, node ids, how it is enforced)
# enforce: "code" (code nodes / tools / graph), "line" (a spoken line of the spec),
# "prompt" (only the agent model's instructions).
KINDS: list[tuple[str, str, tuple[str, ...], tuple[str, ...], str]] = [
    (
        "opening",
        r"first sentence|start every call|\bopening\b|call is (being )?(recorded|monitored)",
        ("lines.opening",),
        ("opening",),
        "line",
    ),
    (
        "who",
        r"am i speaking|speaking with|is this \{|counts as confirmation|confirms?\b.{0,20}"
        r"(name|yes)|still unclear|asking twice|ask once more|clear yes or",
        ("lines.who_question", "outcomes.unclear"),
        ("opening", "opening2", "fin_unclear"),
        "code",
    ),
    ("ai", r"\brobot\b|\ban ai\b|\bai\b|automated assistant", ("lines.ai_answer",), (), "prompt"),
    ("scam", r"\bscam\b", ("lines.scam_answer",), (), "prompt"),
    (
        "third_party",
        r"someone else|somebody else|\brelatives?\b|family members?|third part|answers? the "
        r"phone|other person",
        ("forbidden_before_verify",),
        ("setup_tp", "third_party"),
        "code",
    ),
    (
        "callback",
        r"call\s*-?\s*back|be reached",
        ("callback", "outcomes.callback", "outcomes.no_callback"),
        ("third_party", "cb_check", "cb_book", "after_cb", "fin_callback", "fin_no_callback"),
        "code",
    ),
    (
        "wrong_number",
        r"wrong number|does not belong|not belong to",
        ("outcomes.wrong_number",),
        ("fin_wrong_number",),
        "code",
    ),
    (
        "deceased",
        r"deceased|has died|customer.{0,25}\bdied\b|for a death|passed away",
        ("outcomes.deceased", "lines.deceased"),
        ("fin_deceased",),
        "code",
    ),
    (
        "forbidden",
        r"do not say|say nothing|nothing about|before identity is verified|until security|"
        r"may not be told|don't say why",
        ("forbidden_before_verify",),
        ("opening", "opening2", "third_party"),
        "code",
    ),
    (
        "identity",
        r"security question|\bidentity\b|date of birth|postcode|year of birth|last four "
        r"digits|verify_identity|\bsecurity\b",
        ("identity.questions", "tool_fields.verify_identity"),
        ("identity", "identity2"),
        "code",
    ),
    (
        "identity_fail",
        r"wrong answers?|check fails|identity check|not_verified|no_id",
        ("outcomes.failed", "lines.identity_failed"),
        ("fin_failed",),
        "code",
    ),
    (
        "identity_decline",
        r"does not want to answer|refuses|declin|second refusal|protects? their account",
        ("outcomes.declined", "lines.identity_why"),
        ("decline_check", "decline_check2", "fin_declined"),
        "code",
    ),
    (
        "swap",
        r"day_month_swapped|other way round|month as a word|swapp?",
        ("identity.swap_hint", "identity.swap_instruction"),
        ("identity", "identity2"),
        "code",
    ),
    (
        "date_order",
        r"day, then month|month, then day|given only as numbers|yyyy-mm-dd",
        ("identity.date_order",),
        ("identity", "identity2"),
        "code",
    ),
    (
        "account",
        r"get_account|state the debt|past-due amount|past_due|days late|days_late|how much is in"
        r"|name each account|late product|name the product|calling from the collections|"
        r"calling about",
        ("product_fields", "product_names", "lines.present_intro"),
        ("account", "facts"),
        "code",
    ),
    (
        "calendar",
        r"business days are|holiday|monday to friday",
        ("calendar.holidays", "calendar.workdays"),
        ("facts", "decide"),
        "code",
    ),
    (
        "deadline",
        r"deadline|latest promise date|business day after today|\bbands?\b|\bstages?\b|"
        r"days_late|days in arrears",
        ("bands",),
        ("facts", "decide"),
        "code",
    ),
    (
        "date_rules",
        r"weekend|next <weekday>|this <weekday>|next friday|today or later|a vague|exact day|"
        r"promise date must",
        ("neg.vague_date_asks", "calendar"),
        ("decide",),
        "code",
    ),
    (
        "floor",
        r"\bfloor\b|\bminimum\b|instalment_amount|minimum_payment|min_due|too much|below the",
        ("floor_field", "neg.floor_offers"),
        ("decide",),
        "code",
    ),
    (
        "attempts",
        r"persuasion attempts?|\battempt \d|\battempts\b|flat refusal|non-answer|offer the "
        r"customer already refused|never a refusal",
        ("attempt_plan", "attempt1", "bands"),
        ("decide",),
        "code",
    ),
    (
        "amounts",
        r"full past-due amount|how much can you pay|proposes every amount|from the floor up "
        r"to|full amount|full arrears",
        ("attempt_plan", "floor_field"),
        ("decide",),
        "code",
    ),
    (
        "today_question",
        r"first ask|pay(ing)? (all of them )?today|clear (the arrears|all of these) today|"
        r"about today",
        ("lines.today_question", "lines.multi_today_question"),
        ("negotiate_say", "negotiate", "decide"),
        "code",
    ),
    (
        "hardship",
        r"illness|job loss|redundancy|bereavement|hardship|death in the family",
        ("neg.hardship", "neg.hardship_queue", "neg.reasons.hardship", "lines.sympathy"),
        ("decide", "transfer"),
        "code",
    ),
    (
        "restructuring",
        r"payment plan|restructur|lower instalments|plans team",
        ("neg.queues.restructuring", "neg.transfer_reasons.restructuring"),
        ("decide", "transfer"),
        "code",
    ),
    (
        "human",
        r"\bhuman\b|for a person|a person\b|service team|real person",
        ("neg.queues.human", "neg.human_requests_to_transfer"),
        ("pre_human", "transfer", "transfer_pre"),
        "code",
    ),
    (
        "transfer",
        r"transfer_to_agent|\btransfer|\bqueue\b",
        ("neg.queues", "neg.transfer_reasons", "neg.notes_on_transfer"),
        ("transfer", "after_transfer"),
        "code",
    ),
    (
        "confirm",
        r"read .{0,40}back|read-back|do you confirm|is that correct|confirmation|clear yes",
        ("lines.confirm_question",),
        ("negotiate", "decide"),
        "code",
    ),
    (
        "record",
        r"create_promise|\brecord(ed|s)?\b|promise per product|try once more|fails? again|"
        r"failed calls|service fails|fails twice",
        ("neg.promise_attempts", "neg.record_failure", "tool_fields.create_promise"),
        ("record", "after_record"),
        "code",
    ),
    (
        "change_promise",
        r"cannot be changed|change it in|changes the date|cancelled",
        ("lines.cannot_change",),
        ("decide",),
        "code",
    ),
    (
        "paid",
        r"already paid|\bpaid\b",
        ("neg.outcomes.paid", "neg.reasons.paid", "lines.paid"),
        ("decide",),
        "code",
    ),
    (
        "dispute",
        r"dispute|not mine|never had",
        ("neg.reasons.dispute",),
        ("decide",),
        "code",
    ),
    (
        "note",
        r"add_note|\bnot(e|es|ed)\b(?! of)",
        ("neg.notes", "neg.reasons", "tool_fields.add_note"),
        ("note",),
        "code",
    ),
    (
        "stop",
        r"(do|does) not want to talk|stop (the )?call",
        ("neg.stop_requests_to_close",),
        ("decide",),
        "code",
    ),
    (
        "order",
        r"one by one|in the order|per product|order `?get_account|each product",
        ("neg.product_order",),
        ("decide",),
        "code",
    ),
    (
        "no_info",
        r"cannot answer|do not have that information|invent",
        ("lines.no_info",),
        (),
        "prompt",
    ),
    ("no_discount", r"discount|waiver|any term", (), (), "prompt"),
    (
        "closing",
        r"close_call|close-out|close as|closes? (the call|without)|\boutcome|code list|"
        r"\bclosed?\b|goodbye|end the call",
        (
            "outcomes",
            "neg.outcomes",
            "neg.reasons",
            "neg.close_reasons",
            "neg.reason_priority",
            "neg.no_promise_reason",
        ),
        ("wrap", "goodbye"),
        "code",
    ),
    (
        "call_vars",
        r"call variables?|customer_id|`now`|full_name|first_name|last_name",
        ("call_variables",),
        (),
        "code",
    ),
]
# The same kinds in Turkish, as folded stems (fold(): no accents, 'ı' and 'İ' are 'i'); a
# stem matches its inflected forms ('anilmaz', 'aktarilir'). Language knowledge only.
KINDS_TR: dict[str, str] = {
    "opening": r"ilk cumle|arama .{0,40}(basla|acil)|kayit altina|kaydedil",
    "who": r"ile mi gorus|siz misiniz|kendisi mi|teyit|iki kez sor",
    "ai": r"robot|yapay zeka|bot|otomatik asistan",
    "scam": r"dolandiric|sahte arama",
    "third_party": r"yakin|baska biri|ucuncu (kisi|sahis)|aile",
    "callback": r"geri ara|tekrar ara|ulasilabil",
    "wrong_number": r"yanlis numara|numara .{0,20}ait degil",
    "deceased": r"vefat|hayatini kaybet|oldu",
    "forbidden": r"anilmaz|soylenmez|bahsedilmez|soyleme|bahsetme|dogrulanmadan|"
    r"kimlik .{0,30}once",
    "identity": r"kimlik|dogum tarihi|tc kimlik|son dort hane|guvenlik soru",
    "identity_fail": r"yanlis cevap|hatali cevap|dogrulama basarisiz|kilitlen",
    "identity_decline": r"cevap vermek istemez|reddeder|reddet|vermek istemez",
    "swap": r"gun ve ay|ay ve gun|yer degistir",
    "date_order": r"gun, ay|gun ay yil|once gun",
    "account": r"borcu (soyle|belirt|bildir)|ekstre borcu|gecikme gun|gecikmis (tutar|borc)",
    "calendar": r"tatil|is gunleri|resmi tatil|bayram|calisma gun",
    "deadline": r"son tarih|son odeme|en gec|bant|vade|gecikme",
    "date_rules": r"hafta ?sonu|belirsiz|kesin (bir )?gun|tarih .{0,30}olmali",
    "floor": r"asgari|en az|taban|minimum",
    "attempts": r"ikna|deneme",
    "amounts": r"tam tutar|toplam borc|ne kadar odeyebilir",
    "today_question": r"bugun (odeye|odeme|odenebilir)",
    "hardship": r"hastalik|is kaybi|issiz|zor durum|maddi (sikinti|zorluk)|vefat",
    "restructuring": r"yapilandirma|taksit|odeme plani",
    "human": r"temsilci|musteri hizmetleri|insan|canli destek",
    "transfer": r"aktar|yonlendir|devret",
    "confirm": r"teyit|onayla|dogru mu|tekrar oku|geri oku",
    "record": r"kaydet|kayit|soz al|odeme sozu",
    "change_promise": r"degistirilemez|degistirile|iptal",
    "paid": r"odedim|odendi|zaten ode",
    "dispute": r"itiraz|benim degil|borcum yok",
    "note": r"not (ekle|dus|al)",
    "stop": r"konusmak istem|aramayin|gorusmeyi (sonlandir|bitir)",
    "order": r"tek tek|sirayla|her (urun|kart) icin",
    "no_info": r"bilgi(m|si) yok|cevaplayama|uydurma",
    "no_discount": r"indirim|muafiyet|faiz silme",
    "closing": r"kapanis|kapat|gorusmeyi bitir|sonuc kod|kod listesi|iyi gunler|hosca kal",
    "call_vars": r"arama degisken",
}
KIND_RE = [(k, re.compile(rx, re.I), keys, nodes, how) for k, rx, keys, nodes, how in KINDS]
KIND_RE_TR = {k: re.compile(rx) for k, rx in KINDS_TR.items()}

# A quoted text is something the agent must SAY when the words just before it ask for that.
SAY_CTX = re.compile(
    r"(\b(say|says|ask|asks|question|sentence|exactly|reply|answer with|in words|words)\b"
    r"[^\"\n]{0,30}|:\s*)$",
    re.I,
)
NEG_CTX = re.compile(r"\b(not|n't|no)\s+(say|use|mention)\b[^\"\n]{0,30}$", re.I)
NEVER_CTX = re.compile(r"\b(never|not|n't|no)\s+(say|ask|offer)\b[^\"\n]{0,30}$", re.I)
CUST_CTX = re.compile(
    r"\b(customer|person|they|caller|someone)\s+(says?|asks?|said|gives|names|offers|means)\b"
    r"[^\"\n]{0,30}$|\bonly\s*$|\(\s*$",
    re.I,
)
LIST_CTX = re.compile(r"^[\s,]*(,|or|and|ve|veya|ya da)?\s*$", re.I)
QUOTE = re.compile(r"[\"“«]([^\"“”«»\n]{1,200})[\"”»]")
# Turkish puts the verb after the quote: '"..." cümlesiyle başlar', '"..." kelimesi kullanılmaz'
# (searched in the folded text right after the quote)
SAY_AFTER_TR = re.compile(
    r"^\W{0,3}(diye|der\b|deyin|denir|demeli|cumle|soru(su|yu|suyla)?\b|ifade|seklinde|sozleriyle)"
)
FORBID_AFTER_TR = re.compile(
    r"^\W{0,3}(kelime|sozcu|ifade)\w*\s+(kullanilmaz|soylenmez|anilmaz|kullanma|soyleme)"
)
BACKTICK = re.compile(r"`([^`\n]{1,80})`")
CODE_TOKEN = re.compile(r"^(?:[a-z][a-z0-9_]*|[A-Z][A-Z0-9_]*)$")
UPPER = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b|\b[A-Z]{3,}\b")
ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})(?!T|\d)")
TIME = re.compile(r"\b(\d{1,2}):(\d{2})\b")
INT = re.compile(r"(?<![\w.:/-])(\d{1,4})(?:st|nd|rd|th)?(?![\w.:/-]|\.\d)")
# rule references, attempt / stage / step names, versions and decimals are not values
REFS = re.compile(
    r"\b(rules?|sections?|attempts?|steps?|brief [a-z] rule|v|madde|kural|bölüm|bolum|belge|"
    r"duyuru|deneme)\s*\d+(?:\.\d+)?"
    r"(?:\s*(?:,|and|or|to)\s*\d+)*|\b\d+\.\d+\b|\bv\d+\b",
    re.I,
)
HEADER_LINE = re.compile(r"^\s*\*\*(from|to|date|subject|cc):\*\*", re.I)


# ---------------------------------------------------------------- splitting the brief
def _section_lines(text: str) -> list[tuple[int, str]]:
    return list(enumerate(text.splitlines(), start=1))


def rule_units(brief: str) -> list[dict[str, Any]]:
    """[{id, line, text, table}] for every rule-like unit of the brief."""
    units: list[dict[str, Any]] = []
    lines = _section_lines(brief)
    cur: dict[str, Any] | None = None
    table_header = False
    seen_nums: Counter[str] = Counter()

    def flush() -> None:
        nonlocal cur
        if cur and cur["text"].strip():
            units.append(cur)
        cur = None

    for ln, raw in lines:
        s = raw.strip()
        if not s or s.startswith("#") or re.fullmatch(r"-{3,}|\*{3,}", s):
            flush()
            table_header = False
            continue
        if HEADER_LINE.match(s):
            flush()
            continue
        if s.startswith("|"):
            flush()
            if re.fullmatch(r"\|[\s:|-]+\|?", s):
                continue
            if not table_header:
                table_header = True  # the first row of a table names its columns
                continue
            units.append({"id": f"table:{ln}", "line": ln, "text": s, "table": True})
            continue
        table_header = False
        m = re.match(r"^\s*(\d+)[.)]\s+", raw)
        b = re.match(r"^\s*\*\*(\d+(?:\.\d+)+)\b", raw)
        # Turkish numbering: 'Madde 3:', 'Kural 4.2 -', '**Madde 5**'
        t = re.match(r"^\s*\**\s*(?:madde|kural|md\.)\s*(\d+(?:\.\d+)*)\**\s*[:.)-]", fold(raw))
        if m or b or t:
            flush()
            num = (m or b or t).group(1)  # type: ignore[union-attr]
            seen_nums[num] += 1
            cur = {"id": num, "line": ln, "text": s, "table": False}
            continue
        if cur is not None and (raw.startswith((" ", "\t")) or not cur["id"].startswith("para")):
            # a sub-bullet or a continuation line of a rule
            cur["text"] += " " + s
            continue
        if cur is None:
            cur = {"id": f"para:{ln}", "line": ln, "text": s, "table": False}
        else:
            cur["text"] += " " + s
    flush()
    dup = {n for n, c in seen_nums.items() if c > 1}
    for u in units:
        if u["id"] in dup:
            u["id"] = f"{u['id']}@L{u['line']}"
    return units


# ---------------------------------------------------------------- what the spec / workflow hold
def _walk(x: Any, out: list[Any]) -> None:
    if isinstance(x, dict):
        for v in x.values():
            _walk(v, out)
    elif isinstance(x, list):
        for v in x:
            _walk(v, out)
    else:
        out.append(x)


def _code_strings(src: str, depth: int = 0) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return out
    for x in ast.walk(tree):
        if isinstance(x, ast.Constant) and isinstance(x.value, str):
            out.append(x.value)
    if depth < 3:
        for blob in _packed_blobs(src):
            inner = _unpack(blob)
            if inner:
                out += _code_strings(inner, depth + 1)
    return out


def _norm(text: str) -> str:
    # folded (Turkish-aware lower case, no accents): 'KAYIT ALTINA' meets 'kayıt altına'
    t = fold(text).replace("\\", "")
    t = re.sub(r"\{\{?\s*[\w.]+\s*\}?\}", "#", t)
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"[^\w#' ]+", " ", t)
    return " ".join(t.split())


class _Hay:
    def __init__(self, spec: Any, gen: Any, wf: Any) -> None:
        leaves: list[Any] = []
        # the spec's notes quote the brief: a value only there is not one the agent uses
        _walk({k: v for k, v in spec.items() if k != "notes"}, leaves)
        _walk({k: v for k, v in gen.items() if k != "notes"}, leaves)
        self.spec_ints = {x for x in leaves if isinstance(x, int) and not isinstance(x, bool)} | {
            int(x) for x in leaves if isinstance(x, float) and x == int(x)
        }
        self.spec_strs = [str(x) for x in leaves if isinstance(x, str)]
        wleaves: list[Any] = []
        _walk(wf, wleaves)
        wstrs = [str(x) for x in wleaves if isinstance(x, str)]
        for s in list(wstrs):
            if "\n" in s and ("try:" in s or "import " in s or "def " in s):
                wstrs += _code_strings(s)
        self.wf_strs = wstrs
        self.spec_text = "\n".join(self.spec_strs)
        self.wf_text = "\n".join(wstrs)
        self.spec_norm = _norm(self.spec_text)
        self.wf_norm = _norm(self.wf_text)
        self.spec_low = self.spec_text.lower()
        self.wf_low = self.wf_text.lower()
        self.forbidden = {fold(x).strip() for x in _get(spec, "forbidden_before_verify")}
        self.forbidden |= {fold(x).strip() for x in _get(gen, "forbidden_before_verify")}
        self.nodes = (
            wf.get("nodes") if isinstance(wf, dict) and isinstance(wf.get("nodes"), dict) else {}
        )

    def has_code(self, code: str) -> bool:
        """The code as a whole word, same case (SERVICE is not 'customer service')."""
        pat = r"(?<![A-Za-z0-9_])" + re.escape(code) + r"(?![A-Za-z0-9_])"
        return bool(re.search(pat, self.spec_text) or re.search(pat, self.wf_text))

    def nodes_with(self, phrase: str) -> list[str]:
        n = _norm(phrase)
        if not n:
            return []
        out = []
        for k, node in self.nodes.items():
            leaves: list[Any] = []
            _walk(node, leaves)
            blob = " ".join(str(x) for x in leaves if isinstance(x, str))
            if n in _norm(blob):
                out.append(k)
        return out


def _get(d: Any, path: str) -> Any:
    cur = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, dict) and isinstance(cur.get("cfg"), dict) and part in cur["cfg"]:
            cur = cur["cfg"][part]
        elif (
            isinstance(cur, dict)
            and isinstance(cur.get("cfg"), dict)
            and isinstance(cur["cfg"].get("neg"), dict)
            and part == "neg"
        ):
            cur = cur["cfg"]["neg"]
        else:
            return []
    return cur if cur is not None else []


def _present(spec: Any, gen: Any, key: str) -> bool:
    for src in (spec, gen):
        v = _get(src, key)
        if v not in ([], {}, "", None):
            return True
    return False


# ---------------------------------------------------------------- literals of a unit
def _overridden(brief: str) -> set[str]:
    """Values a later text replaces: 'not 17:30', 'not 3'."""
    return set(re.findall(r"\bnot\s+(\d{1,2}:\d{2}|\d{1,4}|\d{4}-\d{2}-\d{2})\b", brief))


def _quotes(text: str, lang: str = "en") -> list[tuple[str, str]]:
    """(role, quoted text): role say | forbid | customer | value. In Turkish the verb that
    says what a quote is often follows it ('"..." cümlesiyle başlar')."""
    out: list[tuple[str, str]] = []
    prev_end = 0
    prev_role = ""
    sent_start = 0
    for m in QUOTE.finditer(text):
        q = m.group(1).strip()
        between = text[prev_end : m.start()]
        # context: from the start of the sentence (or the previous quote) to this quote
        cut = max(text.rfind(". ", 0, m.start()), text.rfind("? ", 0, m.start()), sent_start)
        ctx = text[max(cut, prev_end if prev_role else cut) : m.start()]
        if prev_role and LIST_CTX.match(between):
            role = prev_role
        elif NEG_CTX.search(ctx):
            role = "forbid"
        elif NEVER_CTX.search(ctx):
            role = "never"  # a phrase the agent must not use; not a pre-identity word
        elif CUST_CTX.search(ctx):
            role = "customer"
        elif SAY_CTX.search(ctx):
            role = "say"
        else:
            role = "customer"
        if lang == "tr" and role == "customer" and not CUST_CTX.search(ctx):
            after = fold(text[m.end() : m.end() + 40])
            if FORBID_AFTER_TR.search(after):
                role = "forbid"
            elif SAY_AFTER_TR.search(after):
                role = "say"
        if role == "say" and " " not in q and re.fullmatch(r"[A-Za-z_][\w-]*", q):
            role = "value"
        if re.search(r"<\w+>", q):
            role = "customer"
        out.append((role, q))
        prev_end, prev_role = m.end(), role
    return out


def literals(unit: dict[str, Any], overridden: set[str], lang: str = "en") -> dict[str, list[Any]]:
    text = unit["text"]
    quotes = _quotes(text, lang)
    rest = QUOTE.sub(" ", text)
    ticks = [t for t in BACKTICK.findall(rest)]
    rest_nt = BACKTICK.sub(" ", rest)
    dates = [d for d in ISO.findall(rest) if d not in overridden]
    times = []
    for hh, mm in TIME.findall(rest):
        t = f"{int(hh):02d}:{mm}"
        if f"{hh}:{mm}" not in overridden and t not in overridden:
            times.append(t)
    plain = ISO.sub(" ", TIME.sub(" ", rest_nt))
    plain = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", " ", plain)
    if lang == "tr":
        # Turkish dates and times ('29 Ekim 2026', '29.10.2026', '09.00') as ISO / HH:MM; their
        # digits are not counts (read before REFS drops '09.00' as a decimal)
        plain = fold(plain)
        cut = list(plain)
        for d, _, _, a, e in date_mentions(plain):
            if d is not None and d.isoformat() not in overridden:
                dates.append(d.isoformat())
            cut[a:e] = " " * (e - a)
        for hh, mm, a, e in time_mentions(plain):
            t = f"{hh:02d}:{mm:02d}"
            if t not in overridden:
                times.append(t)
            cut[a:e] = " " * (e - a)
        plain = "".join(cut)
    plain = REFS.sub(" ", plain)
    ints = []
    for x in INT.findall(plain):
        if x not in overridden and len(x) <= 3:
            ints.append(int(x))
    codes = [t for t in ticks if CODE_TOKEN.match(t.strip())]
    # a code list in plain text: UPPER_SNAKE anywhere; plain UPPER words in a table or a
    # code-like spot ('NO_ID / REFUSED', '(SERVICE)')
    code_list = bool(re.search(r"\bcodes?\b", rest_nt, re.I))
    for c in UPPER.findall(rest_nt):
        if (
            "_" in c
            or unit.get("table")
            or code_list
            or re.search(
                r"(/\s*|\(\s*|\b(before|after|then)\s+)"
                + c
                + r"\b|\b"
                + c
                + r"(\s*/|\s*\)|\s+(before|after|then)\b)",
                rest_nt,
            )
        ):
            codes.append(c)
    return {
        "ints": sorted(set(ints)),
        "dates": sorted(set(dates)),
        "times": sorted(set(times)),
        "codes": sorted(set(codes)),
        "say": [q for r, q in quotes if r == "say"],
        "forbid": [q for r, q in quotes if r == "forbid"],
        "values": [q for r, q in quotes if r == "value"],
    }


# ---------------------------------------------------------------- the map
def proof_map(
    brief_text: str, spec: dict[str, Any], gen_cfg: dict[str, Any] | None, wf: dict[str, Any]
) -> list[dict[str, Any]]:
    """[{rule_id, kind, spec_keys, nodes, status, missing, line, text}] per rule unit."""
    spec = spec if isinstance(spec, dict) else {}
    gen = gen_cfg if isinstance(gen_cfg, dict) else {}
    wf = wf.get("workflow", wf) if isinstance(wf, dict) and "nodes" not in wf else wf
    wf = wf if isinstance(wf, dict) else {}
    try:
        hay = _Hay(spec, gen, wf)
        lang = str(spec.get("language") or "") or detect_language(str(brief_text or ""))[0]
        lang = "tr" if fold(lang).startswith(("tr", "turk")) else "en"
        # test customers, example conversations and drafts are not rules: no unit of theirs
        line_kinds = _Docs(str(brief_text or "")).kinds
        units = [
            u
            for u in rule_units(str(brief_text or ""))
            if not 0 < u["line"] <= len(line_kinds) or line_kinds[u["line"] - 1] == "rule"
        ]
        over = _overridden(str(brief_text or ""))
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for u in units:
        try:
            row = _unit(u, hay, over, spec, gen, lang)
        except Exception as e:  # one unreadable unit never hides the others
            row = {
                "rule_id": u.get("id"),
                "kind": "-",
                "spec_keys": [],
                "nodes": [],
                "status": "unmapped",
                "missing": [f"proof map error: {type(e).__name__}"],
                "missing_quotes": [],
                "line": u.get("line"),
                "text": u.get("text", ""),
            }
        if row is not None:
            out.append(row)
    return out


def _unit(
    u: dict[str, Any],
    hay: _Hay,
    over: set[str],
    spec: dict[str, Any],
    gen: dict[str, Any],
    lang: str = "en",
) -> dict[str, Any] | None:
    text = u["text"]
    folded = fold(text) if lang == "tr" else ""
    kinds = [
        (k, keys, nodes, how)
        for k, rx, keys, nodes, how in KIND_RE
        if rx.search(text) or (folded and k in KIND_RE_TR and KIND_RE_TR[k].search(folded))
    ]
    lit = literals(u, over, lang)
    has_lit = any(lit[k] for k in lit)
    prose = u["id"].startswith("para:") or u.get("table")
    if not kinds and not has_lit and prose:
        return None  # prose: an introduction, a greeting, a signature
    missing: list[str] = []
    for n in lit["ints"]:
        if n not in hay.spec_ints:
            missing.append(str(n))
    for d in lit["dates"]:
        if d not in hay.spec_text and d not in hay.wf_text:
            missing.append(d)
    for t in lit["times"]:
        if t not in hay.spec_text and t not in hay.wf_text:
            missing.append(t)
    for c in lit["codes"]:
        if not hay.has_code(c):
            missing.append(c)
    for v in lit["values"]:
        if not hay.has_code(v):
            missing.append(v)
    missing_quotes: list[str] = []
    for q in lit["say"]:
        if _norm(q) and _norm(q) not in hay.wf_norm:
            missing_quotes.append(q)
    pre_id = re.search(
        r"before identity|until security|before .{0,20}verified", text, re.I
    ) or re.search(r"dogrulanmadan|dogrulamadan once|kimlik .{0,30}once", fold(text))
    for w in lit["forbid"] if pre_id else []:
        if fold(w).strip() not in hay.forbidden:
            missing.append(f"forbidden word {w!r} not in forbidden_before_verify")
    spec_keys: list[str] = []
    nodes: list[str] = []
    implemented = False
    prompt_only = bool(kinds)
    for _k, keys, knodes, how in kinds:
        pk = [x for x in keys if _present(spec, gen, x)]
        pn = [x for x in knodes if x in hay.nodes]
        spec_keys += [x for x in pk if x not in spec_keys]
        nodes += [x for x in pn if x not in nodes]
        if how != "prompt":
            prompt_only = False
            if pk or pn:
                implemented = True
    for q in lit["say"]:
        nodes += [x for x in hay.nodes_with(q) if x not in nodes]
    if missing or missing_quotes:
        status = "values_missing"
    elif not kinds:
        status = "values_ok" if has_lit else "unmapped"
    elif prompt_only:
        status = "prompt_only"
    elif not implemented and not has_lit:
        status = "unmapped"
    else:
        status = "values_ok" if has_lit else "covered"
    return {
        "rule_id": u["id"],
        "kind": "+".join(k for k, *_ in kinds) or "-",
        "spec_keys": spec_keys,
        "nodes": nodes,
        "status": status,
        "missing": missing,
        "missing_quotes": missing_quotes,
        "line": u["line"],
        "text": text,
    }


def summary(units: list[dict[str, Any]]) -> dict[str, int]:
    c = Counter(u["status"] for u in units)
    out = {
        k: c.get(k, 0)
        for k in ("covered", "values_ok", "values_missing", "prompt_only", "unmapped")
    }
    out["units"] = len(units)
    out["missing_quotes"] = sum(len(u.get("missing_quotes") or []) for u in units)
    return out


def trace_lines(units: list[dict[str, Any]]) -> list[str]:
    """One line per unit that is not plainly covered, then a summary line."""
    lines = []
    for u in units:
        if u["status"] not in ("prompt_only", "unmapped", "values_missing"):
            continue
        bits = [f"proof: {u['rule_id']} {u['status']} kind={u['kind']}"]
        if u.get("missing_quotes"):
            bits.append("missing sentences=" + json.dumps(u["missing_quotes"])[:300])
        if u.get("missing"):
            bits.append("missing=" + json.dumps(u["missing"])[:200])
        bits.append("| " + " ".join(u["text"].split())[:100])
        lines.append(" ".join(bits))
    s = summary(units)
    lines.append(
        f"proof map: {s['units']} rule units: covered {s['covered']}, values_ok "
        f"{s['values_ok']}, values_missing {s['values_missing']}, prompt_only "
        f"{s['prompt_only']}, unmapped {s['unmapped']}; mandatory sentences missing from the "
        f"workflow: {s['missing_quotes']}"
    )
    return lines
