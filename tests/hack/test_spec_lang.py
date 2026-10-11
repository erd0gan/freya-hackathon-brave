"""The spec side of a case in another language (review 04 SX-*, summary G, H, J; 02 TR-1,
TR-10; 03 HA-13): language detection, grounding that reads Turkish and English documents
(and flags values said only in test customers, example conversations, drafts or unrelated
places), dated changes, the new spec keys and their generator form, wrong shapes, the retry
and fallback of the extraction call, the build-time translation of neutral lines, the
observer's filtered merge and the proof map on Turkish documents. No network: chat is a fake."""

from __future__ import annotations

import copy
import json
import re
import threading
import time

import pytest
from fb import langpack
from fb import spec_extract as se
from fb.generate import build
from fb.llm import parse_json
from fb.openapi_tools import parse_openapi
from fb.proof import _quotes, proof_map, rule_units, summary
from fb.spec_extract import (
    ExtractionObserver,
    SpecError,
    check,
    date_mentions,
    defaults_used,
    detect_language,
    fold,
    forbidden_conflicts,
    grounding,
    missing_parts,
    number_mentions,
    observe,
    percent_mentions,
    shape,
    structure,
    time_mentions,
    to_generator,
    translate_neutral_lines,
    warnings,
)
from fb.trace import Trace
from spec_fixtures import (
    ASCII_DOCS,
    EID,
    HOLIDAYS,
    SPEC_EN,
    TR_DOCS,
    TR_EXAMPLES,
    ascii_spec,
    tr_spec,
)

from conftest import KIT

OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text())
TOOLS = parse_openapi(OPENAPI)
BRIEF_EN = (
    (KIT / "briefs" / "brief-a.md").read_text() + "\n" + (KIT / "briefs" / "brief-b.md").read_text()
)


def quiet():
    return Trace(None, echo=False)


# ---------------------------------------------------------------- language
def test_fold_is_turkish_aware():
    assert fold("İŞ GÜNÜ") == "is gunu" == fold("iş günü") == fold("is gunu")
    assert fold("Iğdır’da") == "igdir'da"
    assert fold("KREDİ KARTI") == "kredi karti"


def test_detect_language():
    assert detect_language(TR_DOCS) == ("tr", True)
    assert detect_language(ASCII_DOCS) == ("tr", True)  # no Turkish letters: read from words
    assert detect_language(BRIEF_EN) == ("en", True)
    assert detect_language("Merhaba")[1] is False  # too short to be sure


def test_the_detector_wins_over_the_model_when_sure():
    s = {"language": "English"}
    se._set_language(s, *detect_language(TR_DOCS), None)
    assert s["language"] == "tr"
    s = {"language": "de"}
    se._set_language(s, "en", False, None)  # not sure: the model's reading stays
    assert s["language"] == "de"
    s = {}
    se._set_language(s, *detect_language(BRIEF_EN), None)
    assert s["language"] == "en"


# ---------------------------------------------------------------- reading numbers, dates, times
def test_turkish_and_english_number_forms():
    t = fold(
        "Gecikme 1-10 gün: son tarih on iş günü, yedi iş günü, üç deneme, on ikinci gün; "
        "madde 23; 1.250,00 TL; the 12th business day; twenty-five days; 3. Kural"
    )
    got = {(t[a:b], v) for vals, a, b in number_mentions(t, "tr") for v in vals}
    for want in [
        ("1", 1),
        ("10", 10),
        ("on", 10),
        ("yedi", 7),
        ("uc", 3),
        ("on ikinci", 12),
        ("1.250,00", 1250),
        ("12", 12),
        ("twenty-five", 25),
    ]:
        assert want in got, want
    assert not any(v == 23 for _, v in got)  # a reference, not a value
    # 'on' is ten only in Turkish
    assert not [v for vals, _, _ in number_mentions("pay on the deadline", "en") for v in vals]


def test_turkish_dates_times_and_percentages():
    t = fold("29 Ekim 2026, 1 Ocak 2027; 16-19 Mayıs 2027; 23.04.2027; March 24-26, 2027")
    days = {d.isoformat() for d, *_ in date_mentions(t) if d}
    assert {"2026-10-29", "2027-01-01", "2027-04-23", "2027-03-25", *EID} <= days
    t = fold("Geri arama 09.00 - 18.00 arası; akşam 7'ye kadar; from 10:00 to 7 pm; 14.10.2026")
    times = {(h, m) for h, m, *_ in time_mentions(t)}
    assert {(9, 0), (18, 0), (19, 0), (10, 0)} <= times
    assert (14, 10) not in times  # a date, not a time
    t = fold("Asgari ödeme borcun %20'si; yüzde yirmi beş; 30 percent")
    assert {v for v, *_ in percent_mentions(t, "tr")} == {20.0, 25.0, 30.0}


# ---------------------------------------------------------------- grounding (summary H, TR-10)
def test_a_correct_turkish_spec_passes_every_check():
    s = tr_spec()
    assert check(s, TOOLS, TR_DOCS, OPENAPI) == []
    assert grounding(s, TR_DOCS, TR_EXAMPLES) == []
    assert warnings(s, TR_DOCS) == []


def test_a_correct_reading_of_ascii_turkish_passes():
    assert grounding(ascii_spec(), ASCII_DOCS) == []


def test_the_english_extraction_still_grounds():
    assert grounding(SPEC_EN, BRIEF_EN) == []


PLANTED = {
    "callback end from a draft": (lambda s: s["callback"].update(end="20:00"), "draft"),
    "deadline from a draft": (lambda s: s["bands"][1].update(deadline_days=15), "draft"),
    "a test customer's birth date as a holiday": (
        lambda s: s["calendar"]["holidays"].append("1988-03-14"),
        "test customers",
    ),
    "a test customer's days late as a band edge": (
        lambda s: s["bands"][0].update(max_days_late=9),
        "test customers",
    ),
    "the identity limit as attempts": (
        lambda s: s["bands"][1].update(attempts=3),
        "unrelated place",
    ),
    "an invented wrong-answer limit": (
        lambda s: s["identity"].update(max_wrong_answers=5),
        "",
    ),
    "an invented percentage": (lambda s: s["floor_rule"].update(percent=25), "not found"),
    "an invented code": (lambda s: s["neg"]["reasons"].update(refused="RET"), "not found"),
    "an invented currency": (lambda s: s["currency"].update(symbol="EUR"), "not found"),
    "a misquoted announcement": (
        lambda s: s["dated_changes"][0].update(source="geri arama bitişi 17.00 olur"),
        "quotation",
    ),
    "a wrong dated value": (lambda s: s["dated_changes"][1].update(value=6), "not found"),
    "a wrong effective date": (
        lambda s: s["dated_changes"][0].update(effective_from="2026-11-01"),
        "not found",
    ),
    "an invented callback start": (lambda s: s["callback"].update(start="08:30"), "not found"),
}


@pytest.mark.parametrize("name", sorted(PLANTED))
def test_planted_wrong_values_are_flagged(name):
    change, why = PLANTED[name]
    s = tr_spec()
    change(s)
    out = grounding(s, TR_DOCS, TR_EXAMPLES)
    assert out and all(why in p for p in out), out


def test_a_value_only_in_the_example_conversations_is_flagged():
    s = tr_spec()
    s["calendar"]["holidays"].append("2026-11-11")  # only in TR_EXAMPLES
    out = grounding(s, TR_DOCS, TR_EXAMPLES)
    assert out == [
        "holiday 2026-11-11 appears only in the test customers / example conversations, which "
        "illustrate and are never rules"
    ]
    assert grounding(s, TR_DOCS) == ["holiday 2026-11-11 is not found in the documents"]


ENGLISH_MEMO = """## policy.md (policy)

1. Callbacks: today or the next business day, 09:00 to 18:00.
2. Stage 1 (1 to 14 days late): deadline 8 business days after today, 1 attempt.

## memo-m7.md (announcement)

Approved memo, effective 2026-10-19: callbacks end at 17:00.

## memo-m8.md (announcement)

Status: DRAFT, NOT APPROVED. Proposed callback end: 20:00; proposed deadline: 12 business days.

## faq.md (questions and answers)

Q: Can a customer get 3 attempts? A: No, see rule 2. Call us on 0800 1200.
"""


def english_spec(**cb):
    return {
        "callback": {"start": "09:00", "end": "18:00", **cb},
        "bands": [
            {
                "name": "1",
                "min_days_late": 1,
                "max_days_late": 14,
                "deadline_days": 8,
                "attempts": 1,
            }
        ],
        "dated_changes": [
            {
                "effective_from": "2026-10-19",
                "path": "callback.end",
                "value": "17:00",
                "source": "callbacks end at 17:00",
            }
        ],
    }


def test_english_drafts_and_unrelated_numbers_are_flagged():
    assert grounding(english_spec(), ENGLISH_MEMO) == []
    assert "draft" in grounding(english_spec(end="20:00"), ENGLISH_MEMO)[0]
    s = english_spec()
    s["bands"][0]["deadline_days"] = 12
    assert "draft" in grounding(s, ENGLISH_MEMO)[0]
    s["bands"][0].update(deadline_days=8, attempts=3)  # 3 attempts is a question, refused
    assert grounding(s, ENGLISH_MEMO) == []  # ...said beside 'attempts': the model's call
    s["bands"][0].update(attempts=1, max_days_late=800)  # a phone number
    assert "unrelated place" in grounding(s, ENGLISH_MEMO)[0]


def test_business_days_rule_reads_turkish_and_any_day_wording():
    assert se._callback_business_days("Geri arama yalnızca iş günlerinde yapılır.")
    assert not se._callback_business_days(
        "Geri arama haftanın her günü yapılabilir, iş günü dahil."
    )
    # SX-11: a sentence that allows any day is not a business-day rule
    assert not se._callback_business_days(
        "Callbacks may be booked on any day, 7 days a week, while promise dates must be "
        "business days."
    )
    assert se._callback_business_days("A callback is for today or the next business day.")


# ---------------------------------------------------------------- shapes (SX-5, SX-14)
VALUES = {"None": None, "str": "x", "int": 5, "list": [], "dict": {}, "strs": ["a", "b"]}


def readers(s):
    spec = copy.deepcopy(s)
    check(spec, TOOLS, TR_DOCS, OPENAPI)
    grounding(spec, TR_DOCS)
    structure(spec)
    warnings(spec, TR_DOCS)
    se.blocking(spec)
    to_generator(spec)
    se.policy_fields(spec)
    se.autocorrect(spec, TOOLS, OPENAPI)
    to_generator(spec)


def mutations():
    base = tr_spec()
    base["tool_fields"] = {"create_promise": {"amount": "amount", "response": {"ok": "ok"}}}
    for k in [*base, "unknown"]:
        for vn, v in VALUES.items():
            yield f"{k}={vn}", (lambda s, k=k, v=v: s.__setitem__(k, v)), base
    for top in (
        "neg",
        "callback",
        "calendar",
        "identity",
        "tool_fields",
        "product_fields",
        "lines",
        "currency",
        "floor_rule",
    ):
        for k in list(base[top]):
            for vn, v in VALUES.items():
                yield f"{top}.{k}={vn}", (lambda s, t=top, k=k, v=v: s[t].__setitem__(k, v)), base
    for path in (
        "bands",
        "attempt_plan",
        "dated_changes",
        "identity.questions",
        "calendar.holidays",
        "outcomes.unclear",
        "forbidden_before_verify",
    ):
        for vn, v in (("str", "x"), ("None", None), ("int", 3), ("list", []), ("dict", {})):

            def f(s, path=path, v=v):
                *head, last = path.split(".")
                cur = s
                for h in head:
                    cur = cur[h]
                cur[last] = [v]

            yield f"{path}[0]={vn}", f, base


def test_no_wrong_shape_raises():
    crashed = []
    for name, change, base in mutations():
        s = copy.deepcopy(base)
        change(s)
        try:
            readers(s)
        except Exception as e:
            crashed.append(f"{name}: {type(e).__name__}: {e}")
    assert crashed == []


def test_wrong_shapes_are_problems_for_the_repair_round():
    s = tr_spec()
    s["callback"] = "09:00-18:00"
    s["identity"]["questions"] = ["date_of_birth", "phone_last4"]
    s["bands"].append("late")
    out = check(s, TOOLS, TR_DOCS, OPENAPI)
    assert "callback must be an object, not str" in out
    assert any(p.startswith("identity.questions: 'date_of_birth' must be an object") for p in out)
    assert any("every entry of bands must be an object" in p for p in out)
    assert shape(tr_spec()) == []
    # the questions are kept as fields: a workflow can still be built
    assert se.blocking(s) == []


def test_text_reasks_and_compact_holiday_dates_never_break_the_build():
    s = tr_spec()
    s["callback"]["reasks"] = "once"
    s["calendar"]["holidays"].append("20261225")  # Python 3.12 reads it; the nodes never match
    assert "callback.reasks must be a whole number >= 0" in structure(s)
    assert "holiday '20261225' is not YYYY-MM-DD" in check(s, TOOLS, TR_DOCS, OPENAPI)
    gen = to_generator(s)
    assert gen["callback_reasks"] == 1 and gen["cfg"]["holidays"] == HOLIDAYS


# ---------------------------------------------------------------- missing keys (J, HA-13)
def test_missing_parts_are_problems_not_brief_a_defaults():
    s = tr_spec()
    del s["calendar"]
    s["forbidden_before_verify"] = []
    s["product_fields"]["days_late"] = ""
    got = missing_parts(s)
    assert any(p.startswith("calendar is missing") for p in got)
    assert any(p.startswith("forbidden_before_verify is empty") for p in got)
    assert any(p.startswith("product_fields.days_late is empty") for p in got)
    assert set(got) <= set(check(s, TOOLS, TR_DOCS, OPENAPI))
    assert missing_parts(tr_spec()) == []


def test_every_default_is_reported():
    s = tr_spec()
    del s["callback"]
    for k in ("human_requests_to_transfer", "promise_attempts"):
        del s["neg"][k]
    del s["calendar"]["workdays"]
    used = defaults_used(s)
    for want in (
        "callback.start = 10:00",
        "callback.end = 19:00",
        "neg.human_requests_to_transfer = 2",
        "calendar.workdays = Monday to Friday",
    ):
        assert want in used, used
    assert [w for w in warnings(s, TR_DOCS) if w.startswith("default (the documents are silent)")]
    assert defaults_used(tr_spec()) == []


def test_a_single_product_api_without_type_or_days_late_is_reported():
    s = tr_spec()
    s["bands"] = s["bands"][:1]
    s["product_fields"].update(type="", days_late="")
    w = warnings(s, TR_DOCS)
    assert any(x.startswith("product_fields.type is empty") for x in w)
    assert any(x.startswith("product_fields.days_late is empty: with one band") for x in w)
    s["identity"].pop("max_wrong_answers")
    assert any("max_wrong_answers is missing" in x for x in warnings(s, TR_DOCS))


# ---------------------------------------------------------------- the generator input (contract 2)
def test_to_generator_carries_the_language_and_the_new_keys():
    gen = to_generator(tr_spec())
    cfg = gen["cfg"]
    assert gen["language"] == cfg["lang"] == "tr"
    assert cfg["fmt"]["currency"] == {
        "code": "TRY",
        "symbol": "TL",
        "word": "lira",
        "minor_word": "kuruş",
    }
    assert {k: v for k, v in cfg["fmt"].items() if k != "currency"} == langpack.fmt("tr")
    assert "week_start" not in cfg  # null: derived from the workdays
    assert cfg["floor_rule"] == {"percent_of": "past_due", "percent": 20}
    assert gen["identity"]["max_wrong_answers"] == 3
    assert gen["gen_lines"] == langpack.gen_lines("tr")
    assert cfg["dated"] == [
        {
            "from": "2026-10-19",
            "set": {
                "callback.end": "17:30",
                "bands.1.deadline_days": 5,
                "bands.1.deadline_business_days": 5,
                "holidays": HOLIDAYS + EID,
            },
        }
    ]
    lines = cfg["neg"]["lines"]
    pack = langpack.engine_lines("tr")
    assert set(pack) | set(se.ENGINE_LINES) <= set(lines)
    assert lines["goodbye"] == tr_spec()["lines"]["goodbye"]  # the brief's words win
    for k in set(pack) - set(tr_spec()["lines"]):
        assert lines[k] == pack[k]  # else the Turkish neutral line, never English


def test_to_generator_in_english_is_as_before():
    gen = to_generator(SPEC_EN)
    cfg = gen["cfg"]
    assert gen["language"] == cfg["lang"] == "en" and cfg["fmt"] == langpack.fmt("en")
    assert not {"dated", "week_start", "floor_rule"} & set(cfg)
    assert cfg["holidays"] == SPEC_EN["calendar"]["holidays"]
    assert gen["identity"]["max_wrong_answers"] is None
    s = copy.deepcopy(SPEC_EN)
    s["calendar"]["week_start"] = 6
    assert to_generator(s)["cfg"]["week_start"] == 6


DATED = {
    "bands.late.attempts": (8, {"bands.1.attempts": 2}),  # capped at the plan's two steps
    "bands[0].deadline_days": (
        9,
        {"bands.0.deadline_days": 9, "bands.0.deadline_business_days": 9},
    ),
    "lines.recorded": ("Sözünüz kaydedildi.", {"neg.lines.recorded": "Sözünüz kaydedildi."}),
    "neg.floor_offers": (0, {"neg.floor_offers": 0}),
    "floor_rule.percent": (25, {"floor_rule.percent": 25}),
    "currency.symbol": ("₺", {"fmt.currency.symbol": "₺"}),
    "attempt_plan": (
        [{"amount": "full", "date": "deadline"}],
        {"neg.attempt_plan": [{"amount": "full", "date": "deadline"}]},
    ),
}


@pytest.mark.parametrize("path", sorted(DATED))
def test_dated_changes_map_to_cfg_paths(path):
    value, want = DATED[path]
    s = tr_spec()
    s["dated_changes"] = [
        {"effective_from": "2027-01-04", "path": path, "value": value, "source": "x"}
    ]
    assert structure(s) == []
    assert to_generator(s)["cfg"]["dated"] == [{"from": "2027-01-04", "set": want}]


@pytest.mark.parametrize(
    "path,value,why",
    [
        ("lines.opening", "Merhaba.", "said by the agent model"),
        ("identity.questions", [], "cannot be changed by date"),
        ("bands.middle.attempts", 1, "no band 'middle'"),
        ("callback.end", "5 pm", "wrong type"),
        ("calendar.holidays", ["29 Ekim"], "YYYY-MM-DD"),
    ],
)
def test_a_dated_change_the_workflow_cannot_apply_is_reported_and_dropped(path, value, why):
    s = tr_spec()
    s["dated_changes"].append(
        {"effective_from": "2027-01-04", "path": path, "value": value, "source": "x"}
    )
    out = check(s, TOOLS, TR_DOCS, OPENAPI)
    assert any(p.startswith("dated_changes[3]: path") and why in p for p in out), out
    assert [d["from"] for d in to_generator(s)["cfg"]["dated"]] == ["2026-10-19"]


def test_floor_rule_and_new_keys_are_validated():
    s = tr_spec()
    s["floor_rule"] = {"percent_of": "statement_debt", "percent": 120}
    s["calendar"]["week_start"] = 7
    s["identity"]["max_wrong_answers"] = 0
    out = check(s, TOOLS, TR_DOCS, OPENAPI)
    assert "floor_rule.percent must be a number above 0 and at most 100" in out
    assert any(p.startswith("floor_rule.percent_of: 'statement_debt' is not a field") for p in out)
    assert "calendar.week_start must be 0..6 (Monday = 0) or null" in out
    assert "identity.max_wrong_answers must be a whole number >= 1 or null" in out
    s = tr_spec()
    s["floor_rule"] = {"field": "minimum_payment"}
    assert check(s, TOOLS, TR_DOCS, OPENAPI) == []
    s["floor_rule"] = {"_": "description only"}
    assert structure(s) == [] and "floor_rule" not in to_generator(s)["cfg"]


# ---------------------------------------------------------------- forbidden words (SX-9)
def test_forbidden_words_the_spec_itself_says_are_problems():
    s = tr_spec()
    s["forbidden_before_verify"].append("görüşme")  # the opening says 'görüşme'
    s["identity"]["questions"][1]["ask"] = "Kartınızın son dört hanesi nedir?"  # 'kart'
    out = forbidden_conflicts(s)
    assert len(out) == 2
    assert "lines.opening" in out[1] or "lines.opening" in out[0]
    assert any("identity.questions[1].ask" in p for p in out)
    assert forbidden_conflicts(tr_spec()) == []


class Done:
    def __init__(self, spec):
        self.spec, self.error = spec, ""

    def wait(self, timeout):
        return "done"


def test_the_observer_merge_skips_words_the_main_spec_says(tmp_path):
    spec = tr_spec()
    other = tr_spec()
    other["forbidden_before_verify"] += ["Örnek", "görüşme", "gecikme"]
    t = Trace(tmp_path / "t.log", echo=False)
    info = observe(t, Done(other), spec, TR_DOCS, OPENAPI, 1)
    assert info["forbidden_added"] == ["gecikme"]
    assert info["forbidden_refused"] == ["Örnek", "görüşme"]
    assert spec["forbidden_before_verify"][-1] == "gecikme"
    assert "not merged (said by the spec's own lines" in (tmp_path / "t.log").read_text("utf-8")


# ---------------------------------------------------------------- extract(): retry, fallback
class FakeChat:
    """Answers in order; a callable answer gets the messages."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, list]] = []

    def __call__(self, trace, purpose, messages, **k):
        self.calls.append((purpose, copy.deepcopy(messages)))
        trace.model("m/test", purpose, 0.0, 10)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a(messages) if callable(a) else a


def run_extract(monkeypatch, fake, docs=TR_DOCS, **k):
    monkeypatch.setattr(se, "chat", fake)
    log = []
    t = quiet()
    t.note = log.append  # type: ignore[method-assign]
    spec = se.extract(docs, OPENAPI, TOOLS, t, **k)
    return spec, log


GOOD = json.dumps(tr_spec(), ensure_ascii=False)


def test_the_prompt_asks_for_the_call_language_and_dated_changes(monkeypatch):
    fake = FakeChat(GOOD)
    run_extract(monkeypatch, fake, examples=TR_EXAMPLES)
    prompt = fake.calls[0][1][0]["content"]
    assert "plain English" not in prompt
    assert "written in the call's language" in prompt and "never translate" in prompt
    assert "it is not today and you\nmust not guess it" in prompt
    assert '"dated_changes"' in prompt and '"max_wrong_answers"' in prompt
    assert "EXAMPLE CONVERSATIONS (illustrations only" in prompt and TR_EXAMPLES in prompt
    fake = FakeChat(GOOD)
    run_extract(monkeypatch, fake)
    assert "EXAMPLE CONVERSATIONS" not in fake.calls[0][1][0]["content"]


def test_a_clean_turkish_extraction_needs_no_repair(monkeypatch):
    fake = FakeChat(GOOD)
    spec, log = run_extract(monkeypatch, fake, examples=TR_EXAMPLES)
    assert [p for p, _ in fake.calls] == ["extract the spec from the brief"]
    assert spec["language"] == "tr" and not [x for x in log if x.startswith("spec check")]


def test_an_unusable_reply_is_asked_again_once(monkeypatch):
    for bad in ("Sorry, I cannot do that.", GOOD[: len(GOOD) // 2], '{"bands": {"x": 1}}'):
        fake = FakeChat(bad, GOOD)
        spec, log = run_extract(monkeypatch, fake)
        assert spec["bank_name"] == "Örnek Bank"
        assert [p for p, _ in fake.calls] == [
            "extract the spec from the brief",
            "extract the spec from the brief (asked again)",
        ]
        again = fake.calls[1][1][-1]["content"]
        assert "Your previous answer could not be used" in again
        assert any("asking again" in x for x in log)


def test_main_parts_of_the_wrong_type_are_asked_again(monkeypatch):
    broken = tr_spec()
    broken["identity"] = "date of birth, then the last four digits"
    fake = FakeChat(json.dumps(broken), GOOD)
    spec, _ = run_extract(monkeypatch, fake)
    assert len(fake.calls) == 2 and isinstance(spec["identity"], dict)


def test_no_usable_reply_falls_back_to_the_cross_check(monkeypatch):
    fake = FakeChat("not json")
    other = tr_spec()
    other["bank_name"] = "from the observer"
    spec, log = run_extract(monkeypatch, fake, fallback=lambda: copy.deepcopy(other))
    assert spec["bank_name"] == "from the observer"
    assert any("building the cross-check extraction's spec instead" in x for x in log)
    with pytest.raises(SpecError):
        run_extract(monkeypatch, FakeChat("not json"))
    with pytest.raises(SpecError, match="no usable spec either"):
        run_extract(monkeypatch, FakeChat("not json"), fallback=lambda: {"bands": "x"})


def test_a_failed_model_call_falls_back_to_the_running_observer(monkeypatch):
    gate = threading.Event()

    def observer_run():
        gate.wait(5)
        return tr_spec()

    obs = ExtractionObserver(observer_run)
    fake = FakeChat(se.LLMError("model call failed after 4 attempts"))
    threading.Timer(0.05, gate.set).start()
    spec, _ = run_extract(monkeypatch, fake, fallback=obs, deadline=time.monotonic() + 900)
    assert spec == {**tr_spec(), "language": "tr"}


def test_a_repair_that_breaks_more_is_not_taken(monkeypatch):
    first = tr_spec()
    first["bands"][1]["deadline_days"] = 15  # a draft's value: the repair round is asked
    worse = tr_spec()
    worse["neg"]["outcomes"] = {"promise": "x"}
    fake = FakeChat(json.dumps(first), json.dumps(worse))
    spec, log = run_extract(monkeypatch, fake)
    assert spec["bands"][1]["deadline_days"] == 15 and spec["neg"]["outcomes"]["promise"]
    assert any(x.startswith("spec repair: kept the first reading") for x in log)
    fake = FakeChat(json.dumps(first), "no json", "still none")
    spec, log = run_extract(monkeypatch, fake)
    assert spec["bands"][1]["deadline_days"] == 15
    assert any(x.startswith("spec repair failed") for x in log)
    fake = FakeChat(json.dumps(first), GOOD)
    spec, _ = run_extract(monkeypatch, fake)
    assert spec["bands"][1]["deadline_days"] == 7
    assert "appears only in a draft" in fake.calls[1][1][-1]["content"]


def test_the_language_is_set_from_the_documents(monkeypatch):
    said_en = tr_spec()
    said_en["language"] = "en"
    spec, log = run_extract(monkeypatch, FakeChat(json.dumps(said_en)))
    assert spec["language"] == "tr"
    assert any("spec override: language 'en' -> 'tr'" in x for x in log)


# ---------------------------------------------------------------- neutral lines without a pack
GERMAN = (
    "Die Bank ruft Kunden an, die mit einer Zahlung im Rückstand sind. Der Agent prüft die "
    "Identität, nennt den Betrag und fragt nach einem Zahlungsversprechen. "
) * 3


def translations(messages):
    todo = parse_json(messages[-1]["content"])
    out = {g: {k: "DE " + v for k, v in lines.items()} for g, lines in todo.items()}
    if out["engine"]:
        k = next(k for k, v in todo["engine"].items() if "{" in v)
        out["engine"][k] = "DE ohne Platzhalter"  # a placeholder lost: English kept
    return json.dumps(out)


def test_a_language_without_a_pack_gets_translated_neutral_lines(monkeypatch):
    s = copy.deepcopy(SPEC_EN)
    s["language"] = "de"
    fake = FakeChat(translations)
    monkeypatch.setattr(se, "chat", fake)
    warns = translate_neutral_lines(s, quiet())
    assert fake.calls[0][0] == f"translate the neutral lines into {langpack.language_name('de')}"
    assert len(warns) == 1 and "kept in English" in warns[0]
    lost = re.search(r"engine\.(\w+)", warns[0]).group(1)
    tx = s["neutral_lines"]["engine"]
    assert lost not in tx and tx and all(v.startswith("DE ") for v in tx.values())
    gen = to_generator(s)
    lines = gen["cfg"]["neg"]["lines"]
    # English: the engine says its own default when the key is left out of CFG
    assert lines.get(lost, se.ENGINE_LINES[lost]) == se.ENGINE_LINES[lost]
    silent = [k for k in tx if k not in s["lines"]]
    assert silent and all(lines[k].startswith("DE ") for k in silent)
    assert all(v.startswith("DE ") for v in gen["gen_lines"].values() if v)


def test_a_failed_translation_keeps_english_with_a_warning(monkeypatch):
    s = copy.deepcopy(SPEC_EN)
    s["language"] = "de"
    monkeypatch.setattr(se, "chat", FakeChat(se.LLMError("down")))
    warns = translate_neutral_lines(s, quiet())
    assert warns and warns[0].startswith(
        f"neutral lines stay English for {langpack.language_name('de')}"
    )
    assert "neutral_lines" not in s
    s["language"] = "tr"  # the pack is complete: no call at all
    monkeypatch.setattr(se, "chat", FakeChat(AssertionError("no call expected")))
    assert translate_neutral_lines(s, quiet()) == []


def test_extract_translates_for_a_language_without_a_pack(monkeypatch):
    s = copy.deepcopy(SPEC_EN)
    s["language"] = "de"
    fake = FakeChat(
        lambda m: (
            translations(m) if "Translate these sentences" in m[-1]["content"] else json.dumps(s)
        )
    )
    spec, _ = run_extract(monkeypatch, fake, docs=GERMAN)
    assert spec["language"] == "de"  # the detector is not sure of a German text
    assert fake.calls[-1][0].startswith("translate the neutral lines into")
    assert spec["neutral_lines"]["engine"]


# ---------------------------------------------------------------- proof map on Turkish documents
def test_turkish_numbering_and_quote_roles():
    ids = [u["id"] for u in rule_units("Madde 1: Arama başlar.\nMadde 2.1 - Kimlik.\n3) Bant.\n")]
    assert ids == ["1", "2.1", "3"]
    roles = dict(
        (q, r)
        for r, q in _quotes(
            'Arama "Bu görüşme kayıt altına alınmaktadır." cümlesiyle başlar; "borç" kelimesi '
            "kullanılmaz.",
            "tr",
        )
    )
    assert roles["Bu görüşme kayıt altına alınmaktadır."] == "say"
    assert roles["borç"] == "forbid"


def test_proof_map_reads_turkish_documents():
    s = tr_spec()
    gen = to_generator(s)
    wf, _ = build(gen, TOOLS)
    sm = summary(proof_map(TR_DOCS, s, gen, wf))
    # the test customers' table is not a rule; every rule unit maps
    assert sm["units"] == 10 and sm["values_missing"] == 0 and sm["unmapped"] == 0
    s["callback"]["end"] = "18:30"
    gen = to_generator(s)
    wf, _ = build(gen, TOOLS)
    missing = [u for u in proof_map(TR_DOCS, s, gen, wf) if u["status"] == "values_missing"]
    assert [(u["rule_id"], u["missing"]) for u in missing] == [("3", ["18:00"])]


def test_examples_appended_under_a_heading_are_illustrations_too():
    # the case reader's fallback when extract() had no examples keyword
    docs = TR_DOCS + "\n\n# Example conversations (illustrations, not rules)\n\n" + TR_EXAMPLES
    s = tr_spec()
    assert grounding(s, docs) == []
    s["calendar"]["holidays"].append("2026-11-11")
    assert "test customers / example conversations" in grounding(s, docs)[0]
    # a rule heading that merely says 'conversation' stays a rule
    assert se._Docs("## 2. Opening the conversation\n\nSay hello.").kinds[-1] == "rule"


def test_the_tool_summary_names_the_contract_tool():
    o = copy.deepcopy(OPENAPI)
    tools = parse_openapi(o)
    tools["close_call"].api_name = "kapat"
    text = se.tool_summary(o, tools)
    assert "close_call (contract: kapat)  (POST" in text
    assert "get_account  (POST" in text


def test_unmodelled_rules_reach_the_agent_and_must_be_quotes():
    s = tr_spec()
    s["unmodelled_rules"] = ["Tutarlar TL olarak söylenir"]
    assert grounding(s, TR_DOCS) == []
    assert to_generator(s)["extra_rules"] == ["Tutarlar TL olarak söylenir"]
    s["unmodelled_rules"].append("Taksit teklif edilebilir")  # not in the documents
    assert grounding(s, TR_DOCS) == [
        "unmodelled_rules[1] is not a quotation of a rule in force (quote the documents' own "
        "words, exactly; the agent is told to follow it)"
    ]
    assert to_generator(SPEC_EN)["extra_rules"] == []


def test_a_turkish_callback_rule_on_business_days_overrides_weekend_callbacks():
    docs = "Geri arama saatleri 09.00 - 18.00 arasıdır; bugün veya bir sonraki iş günü."
    s = {"callback": {"start": "09:00", "end": "18:00", "business_days_only": False}}
    assert any(p.startswith("callback.business_days_only is false") for p in grounding(s, docs))


def test_lines_the_engine_says_anyway_stay_out_of_cfg():
    # every byte of CFG is code-node budget: an English line equal to the engine's own words
    # is left out; the neutral lines (whose engine default is one bank's policy) never are
    lines = to_generator(SPEC_EN)["cfg"]["neg"]["lines"]
    en = langpack.ENGINE_LINES["en"]
    assert set(se._LOCAL_ENGINE_LINES) <= set(lines)
    assert not [k for k, v in lines.items() if k not in se._LOCAL_ENGINE_LINES and v == en.get(k)]
    tr = to_generator(tr_spec())["cfg"]["neg"]["lines"]
    assert {k for k, v in langpack.ENGINE_LINES["tr"].items() if v != en.get(k)} <= set(tr)


def test_new_neg_knobs_and_due_date_are_checked():
    s = tr_spec()
    s["neg"].update(restructure="no", plans_allowed="yes")
    s["neg"]["queues"]["restructuring"] = ""
    out = structure(s)
    assert out == ["neg.plans_allowed must be true or false"]
    s["neg"]["restructure"] = "maybe"
    assert "neg.restructure must be transfer or no" in structure(s)
    s = tr_spec()
    s["product_fields"].update(days_late="", due_date="due_on")
    assert not [p for p in missing_parts(s) if "days_late" in p]
    assert any(
        p.startswith("product_fields.due_date: 'due_on' is not a field")
        for p in check(s, TOOLS, TR_DOCS, OPENAPI)
    )


def test_identity_numbers_are_no_persuasion_values_and_back():
    # 'son dört hanesi' counts digits; 'iki yanlış cevap toplam hak' is the identity limit, no
    # attempt count; 'üç ikna denemesi' / 'iki deneme yapılır' are attempts, no identity limit
    s = ascii_spec()
    s["bands"][1]["attempts"] = 4
    assert grounding(s, ASCII_DOCS) == ["band 'b' attempts=4 is not found in the documents"]
    s = ascii_spec()
    s["identity"]["max_wrong_answers"] = 3
    assert grounding(s, ASCII_DOCS) == [
        "identity.max_wrong_answers=3 is in the documents only in an unrelated place (never "
        "where they speak of wrong answers)"
    ]
    assert not [v for vals, _, _ in number_mentions(fold("the last 4 digits, 4 haneli"), "en")
                for v in vals]  # fmt: skip
    # a limit said under an identity heading only ('three tries') still grounds
    docs = "## Identity check\n\nThe customer has three tries in total.\n"
    assert grounding({"identity": {"max_wrong_answers": 3}}, docs) == []


def test_an_announcement_heading_date_is_not_its_effective_date():
    docs = (
        "## notice.docx (announcement)\n\n# Notice 2026-10-01\n"
        "The minimum payment is 40% of the statement balance from 15 October 2026.\n"
    )
    ch = {
        "effective_from": "2026-10-15",
        "path": "floor_rule.percent",
        "value": 40,
        "source": "The minimum payment is 40% of the statement balance",
    }
    s = {"floor_rule": {"percent_of": "balance", "percent": 40}, "dated_changes": [ch]}
    assert grounding(s, docs) == []
    ch["effective_from"] = "2026-10-01"
    assert grounding(s, docs) == [
        "dated_changes[0].effective_from 2026-10-01 is in the documents only in an unrelated "
        "place (never where they speak of an effective date)"
    ]
    # a heading that itself says the date is effective is read as such
    docs = "## Duyuru (yürürlük: 15 Ekim 2026)\n\nAsgari ödeme oranı %40 olur.\n"
    ch.update(effective_from="2026-10-15", source="Asgari ödeme oranı %40 olur")
    assert grounding(s, docs) == []


AUDIT = KIT.parent / "out" / "audit-scratch"


@pytest.mark.skipif(not (AUDIT / "brief-tr-ekstre.md").exists(), reason="audit scratch absent")
def test_the_audit_turkish_brief_grounds_a_correct_reading_and_flags_planted_values():
    # the audit's own Turkish raw material (review 04 SX-4): a correct post-announcement
    # reading gets no false rejection, each planted wrong value one problem
    docs = (AUDIT / "brief-tr-ekstre.md").read_text(encoding="utf-8")
    s = ascii_spec()
    s["calendar"]["holidays"] += ["2027-05-15", "2027-05-16", "2027-05-17", "2027-05-18",
                                  "2027-05-19"]  # fmt: skip
    s["neg"]["outcomes"]["paid"] = "ODENDI"
    s["unmodelled_rules"] = ["Deneme 2: asgari odeme tutari, ikinci is gunu."]
    assert grounding(s, docs) == []
    for change in (
        lambda x: x["callback"].update(end="20:00"),  # the draft
        lambda x: x["dated_changes"][1].update(value=12),  # the draft
        lambda x: x["bands"][1].update(attempts=4),
        lambda x: x["identity"].update(max_wrong_answers=3),
        lambda x: x["dated_changes"][0].update(effective_from="2026-09-01"),
        lambda x: x["calendar"]["holidays"].append("2026-11-10"),
        lambda x: x["neg"]["outcomes"].update(promise="SOZ_VAR"),
    ):
        x = copy.deepcopy(s)
        change(x)
        assert len(grounding(x, docs)) == 1


@pytest.mark.skipif(
    not (AUDIT / "ic" / "FreyaBank-Ekstre-Borc").is_dir(), reason="audit scratch absent"
)
def test_the_audit_case_folder_grounds_its_announced_percentage():
    from fb.case_input import load_case

    case = load_case(AUDIT / "ic" / "FreyaBank-Ekstre-Borc")
    ch = {
        "effective_from": "2026-10-15",
        "path": "floor_rule.percent",
        "value": 40,
        "source": "Asgari ödeme oranı %40 olarak güncellenmiştir",
    }
    s = {
        "language": "tr",
        "currency": {"symbol": "₺"},
        "floor_rule": {"percent_of": "statement_balance", "percent": 40},
        "dated_changes": [ch],
    }
    assert grounding(s, case.policy_text, case.examples_text) == []
    for change in (
        lambda x: x["floor_rule"].update(percent=20),  # struck out in the announcement
        lambda x: x["dated_changes"][0].update(effective_from="2026-10-01"),  # its heading
        lambda x: x["currency"].update(symbol="TL"),
    ):
        x = copy.deepcopy(s)
        change(x)
        assert len(grounding(x, case.policy_text)) == 1


def test_brief_a_callback_rule_reads_as_business_days_only():
    # SM-13: 'today or the next business day' (Brief A rule 6): a weekend call offers the
    # next business day; Brief B says nothing of callbacks and changes nothing
    a = (KIT / "briefs" / "brief-a.md").read_text()
    assert se._callback_business_days(a)
    assert not se._callback_business_days((KIT / "briefs" / "brief-b.md").read_text())
    s = copy.deepcopy(SPEC_EN)
    s["callback"].pop("business_days_only", None)
    assert to_generator(s)["cfg"]["callback"]["business_days_only"] is True
    s["callback"]["business_days_only"] = False
    assert any(p.startswith("callback.business_days_only is false") for p in grounding(s, a))


def test_deadline_units_from_a_product_date_and_plan_rule_dates():
    # the calendar words collections_lib reads: an end-of-month deadline counted from a
    # product's own date, plan steps on rule dates
    s = tr_spec()
    s["bands"][0].update(deadline_unit="end_of_month", deadline_from_field="due_date")
    s["attempt1"] = {"rule": "last_business_day_of_month"}
    s["attempt_plan"] = [
        {"amount": "full", "date": "next_business_day"},
        {"amount": "floor", "date": "nth_business_day:2"},
        {"amount": "floor", "date": "last_business_day_of_month"},
    ]
    assert structure(s) == []
    assert not [p for p in grounding(s, TR_DOCS, TR_EXAMPLES) if "deadline_days" in p]
    cfg = to_generator(s)["cfg"]
    assert cfg["bands"][0]["deadline_unit"] == "end_of_month"
    assert cfg["bands"][0]["deadline_from_field"] == "due_date"
    assert "deadline_from_field" not in cfg["bands"][1]
    assert cfg["neg"]["attempt_plan"][1]["date"] == "nth_business_day:2"
    # the kit's get_account products have no due_date field
    assert any(
        "deadline_from_field 'due_date' is not a field" in p for p in check(s, TOOLS, TR_DOCS)
    )
    s["bands"][0]["deadline_unit"] = "weeks"
    s["attempt_plan"][1]["date"] = "nth_business_day"
    s["attempt1"] = {"rule": "first_monday"}
    out = structure(s)
    assert any("deadline_unit must be one of" in p for p in out)
    assert any(p.startswith("attempt_plan[1] must use") for p in out)
    assert any(p.startswith("attempt1.rule must be one of") for p in out)
    # by date too
    s = tr_spec()
    ch = {"effective_from": "2026-10-19", "path": "bands.0.deadline_unit", "value": "end_of_month"}
    assert se._cfg_change(ch, s) == ({"bands.0.deadline_unit": "end_of_month"}, "")
    ch = {"effective_from": "2026-10-19", "path": "attempt_plan",
          "value": [{"amount": "floor", "date": "nth_business_day:3"}]}  # fmt: skip
    assert se._cfg_change(ch, s)[1] == ""
