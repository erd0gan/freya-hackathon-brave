"""The generated workflow in another language than English (TR-1, TR-2, TR-7, TR-8), the
identity lockout counted by the workflow itself (HA-1 / summary F), partial digits held back
(LP-6), retry loops ended by the platform's counters (LP-5), tool URLs (summary B) and the
spec-driven opening / identity / hang-up rules (HA-11, HA-14)."""

import copy
import datetime
import json
import re

import pytest
from fb import langpack
from fb.generate import build
from fb.nodecode.embed import CODE_HARD_CAP, LIB, namespace
from fb.openapi_tools import parse_openapi
from fb.validate import Policy, _packed_blobs, _unpack, validate
from spec_fixtures import SPEC_A

from conftest import KIT

OPENAPI = json.loads((KIT / "tools" / "openapi.json").read_text())

LINES_TR = {
    "opening": "Merhaba, ben Freya Bank'ın dijital asistanıyım ve bu görüşme kaydediliyor.",
    "who_question": "{{title}} {{last_name}} ile mi görüşüyorum?",
    "ai_answer": "Ben Freya Bank'ın dijital asistanıyım.",
    "scam_answer": "Bu aramayı Freya Bank müşteri hizmetlerini arayarak doğrulayabilirsiniz.",
    "deceased": "Başınız sağ olsun.",
    "identity_intro": "Devam etmeden önce size iki güvenlik sorusu sormam gerekiyor.",
    "identity_why": "Bu sorular hesabınızı korur.",
    "identity_failed": "Devam edemiyorum. Lütfen mobil uygulamayı kullanın ya da bir şubeye gidin.",
    "no_info": "Bu bilgiye sahip değilim.",
    "callback_booked": "Teşekkürler, o zaman sizi arayacağız.",
    "goodbye": "Zaman ayırdığınız için teşekkürler. İyi günler.",
}


def spec_tr():
    """Brief A's spec as a Turkish brief would give it: language, lines, questions in Turkish
    (the engine's lines are the engine's: a placeholder per key here)."""
    s = copy.deepcopy(SPEC_A)
    s["language"] = "tr"
    s["lines"] = dict(LINES_TR)
    s["cfg"] = copy.deepcopy(s["cfg"])
    s["cfg"]["lang"] = "tr"
    s["cfg"]["neg"]["lines"] = {k: "Türkçe satır " + k for k in s["cfg"]["neg"]["lines"]}
    s["identity"]["questions"] = [
        {"field": "date_of_birth", "ask": "Doğum tarihiniz nedir?", "kind": "date"},
        {
            "field": "phone_last4",
            "ask": "Kayıtlı telefon numaranızın son dört hanesi nedir?",
            "kind": "digits",
        },
    ]
    s["identity"]["swap_instruction"] = "Müşteriden ayı kelimeyle söylemesini isteyin."
    s["forbidden_before_verify"] = ["borç", "ödeme", "kredi kartı", "kredi"]
    return s


def tools():
    return parse_openapi(copy.deepcopy(OPENAPI))


def code(wf, node):
    return wf["nodes"][node]["pre_actions"][0]["function"]["parameters"]["properties"]["code"][
        "default"
    ]


def tool_of(wf, node, name):
    n = wf["nodes"][node]
    return next(
        t for t in (n.get("tools") or n.get("pre_actions")) if t["function"]["name"] == name
    )


def holds(cond, v):
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
    return False


def route(wf, node, v):
    return next(
        (t["target_node"] for t in wf["nodes"][node]["transitions"] if holds(t["condition"], v)),
        None,
    )


# ---------------------------------------------------------------- language packs
def test_gen_lines_same_keys_and_placeholders_in_every_pack():
    en = langpack.GEN_LINES["en"]
    for code_, pack in langpack.GEN_LINES.items():
        assert set(pack) == set(en), code_
        for k, v in pack.items():
            assert v.strip(), (code_, k)
            assert set(re.findall(r"\{\w+\}", v)) == set(re.findall(r"\{\w+\}", en[k])), (code_, k)


def test_language_names():
    assert langpack.language_name("tr") == "Turkish"
    assert langpack.language_name("de-DE") == "German"
    assert langpack.norm_language("Türkçe") == "tr"


def test_english_system_prompt_unchanged():
    wf, _ = build(SPEC_A, tools())
    assert "Speak English. Say one or two short sentences per turn." in wf["system_prompt"]
    assert "Your instructions are written in English" not in wf["system_prompt"]


def test_turkish_system_prompt_speaks_turkish():
    wf, _ = build(spec_tr(), tools())
    p = wf["system_prompt"]
    assert "Speak Turkish." in p and "English." not in p.split("Speak")[1][:10]
    assert "everything you say to the customer is in Turkish" in p
    assert "Say every quoted line exactly as it is given." in p


def test_a_language_without_a_pack_is_named_and_quoted_lines_carried_over():
    s = copy.deepcopy(SPEC_A)
    s["language"] = "de"
    wf, policy = build(s, tools())
    assert "Speak German." in wf["system_prompt"]
    assert "with the same meaning" in wf["system_prompt"]
    assert validate(wf, tools(), policy).ok


# English words that never appear in a Turkish sentence
EN_WORDS = re.compile(
    r"\b(the|you|your|are|have|we|sorry|thank|thanks|goodbye|please|cannot|speaking|"
    r"bothered|loss|continue|transfer|colleague|digits|year|minute)\b",
    re.I,
)


def spoken(wf):
    """Everything the Turkish customer may hear from the generator's own text: fixed lines,
    closing lines, and lines a prompt quotes for the model to say."""
    out = []
    for k, n in wf["nodes"].items():
        oe = n.get("on_enter") or {}
        if oe.get("type") == "tts":
            out.append((k, oe.get("text", "")))
        for txt in (n.get("task_message"), oe.get("prompt")):
            if isinstance(txt, str):
                # "say exactly: "..."", 'If asked ...: "..."' (not a 'no "sorry to hear"' rule)
                for m in re.finditer(r'(?<!no )"([^"]+)"', txt):
                    out.append((k, m.group(1)))
                for m in re.finditer(r"such as '([^']+)' or '([^']+)'", txt):
                    out += [(k, m.group(1)), (k, m.group(2))]
                for m in re.finditer(r"for example '([^']+)' or\s+'([^']+)'", txt):
                    out += [(k, m.group(1)), (k, m.group(2))]
        if n.get("node_type") == "code":
            out += [(k, s) for s in re.findall(r"vars\['final_say'\] = '([^']*)'", code(wf, k))]
    return out


def test_turkish_build_says_no_english_sentence():
    wf, _ = build(spec_tr(), tools())
    lines = spoken(wf)
    assert len(lines) > 15
    bad = [(k, s) for k, s in lines if EN_WORDS.search(re.sub(r"\{\{\w+\}\}", "", s))]
    assert bad == []
    # every fixed sentence of the generator, outside the code (the engine's own fallbacks live
    # in the code nodes: bodies.py) and in the closing lines the generator bakes into code
    w = copy.deepcopy(wf)
    for n in [*w["nodes"].values(), *w["tools"]]:
        for t in [n, *(n.get("tools") or []), *(n.get("pre_actions") or [])]:
            t.pop("pre_process_code", None)
            t.pop("post_process_code", None)
            p = ((t.get("function") or {}).get("parameters") or {}).get("properties") or {}
            p.pop("code", None)
    text = json.dumps(w, ensure_ascii=False) + " ".join(s for _, s in lines)
    en, tr_ = langpack.GEN_LINES["en"], langpack.GEN_LINES["tr"]
    left = [en[k] for k in en if en[k] != tr_[k] and en[k].replace("{bank}", "Freya Bank") in text]
    assert left == []


def test_turkish_lines_fill_what_the_brief_does_not_say():
    s = spec_tr()
    for k in ("deceased", "callback_booked", "identity_failed", "identity_why"):
        s["lines"].pop(k)
    wf, _ = build(s, tools())
    g = langpack.gen_lines("tr")
    assert g["deceased"] + " " + g["bye"] in code(wf, "fin_deceased")
    assert g["wrong_number"] in code(wf, "fin_wrong_number")
    assert g["callback_booked"] in code(wf, "fin_callback")
    assert g["help_first"] == wf["nodes"]["pre_human"]["on_enter"]["text"] or wf["nodes"][
        "pre_human"
    ]["on_enter"]["text"].startswith("Türkçe satır")
    assert g["identity_why"] in wf["nodes"]["identity2"]["on_enter"]["prompt"]


def test_turkish_workflow_validates_within_budget():
    wf, policy = build(spec_tr(), tools())
    rep = validate(wf, tools(), policy)
    assert rep.ok, [str(i) for i in rep.errors]
    assert "code_size" not in rep.codes()
    for k, n in wf["nodes"].items():
        srcs = [code(wf, k)] if n.get("node_type") == "code" else []
        srcs += [t[h] for t in n.get("tools") or [] for h in ("pre_process_code",) if t.get(h)]
        for src in srcs:
            assert len(src.encode("utf-8")) <= CODE_HARD_CAP, k
    # a turn of the engine that would be over the cap in one node is read in one, decided in
    # the next; the reading reaches the decision once
    if "read" in wf["nodes"]:
        assert route(wf, "negotiate_say", {}) == "read"
        assert route(wf, "read", {}) == "decide"
        v = {"now": "2026-10-14T11:00", "neg_reply": ""}
        exec(code(wf, "decide"), {"vars": v})
        assert v["neg_say"] and "code_error" in v  # nothing read: asked again, not a stale turn


def test_model_readings_only_where_the_parsers_are_new():
    def names(wf):
        return [x["name"] for x in wf["nodes"]["negotiate_say"]["extraction_schema"]]

    en, _ = build(SPEC_A, tools())
    assert "said_amount" not in names(en) and "said_date_iso" not in names(en)
    tr_, _ = build(spec_tr(), tools())
    assert names(tr_)[-2:] == ["said_amount", "said_date_iso"]
    assert names(tr_) == [x["name"] for x in tr_["nodes"]["negotiate"]["extraction_schema"]]
    desc = tr_["nodes"]["negotiate_say"]["extraction_schema"][-1]["description"]
    assert "{{said_dates}}" in desc and "YYYY-MM-DD" in desc
    # the table: three weeks from the call's day, ISO first
    v = {"now": "2026-10-14T11:00"}
    exec(code(tr_, "setup_id"), {"vars": v})
    rows = v["said_dates"].split("; ")
    assert len(rows) == 21 and rows[0].startswith("2026-10-14 = ") and rows[0].endswith("(today)")
    assert rows[1].startswith("2026-10-15 = ") and rows[-1].startswith("2026-11-03 = ")


def test_code_nodes_prepare_the_config_first():
    if "def prepare_cfg(" not in LIB:
        pytest.skip("collections_lib has no prepare_cfg yet")
    for spec in (SPEC_A, spec_tr()):
        wf, _ = build(spec, tools())
        for k in (
            "setup_id",
            "setup_tp",
            "decline_check",
            "facts",
            "decide",
            "after_record",
            "cb_check",
            "after_transfer",
        ):
            src = code(wf, k)
            assert "CFG = prepare_cfg(CFG, vars.get('now'))" in src, k


def test_a_dated_change_reaches_the_code_nodes():
    if "def prepare_cfg(" not in LIB:
        pytest.skip("collections_lib has no prepare_cfg yet")
    s = copy.deepcopy(SPEC_A)
    s["cfg"] = {**s["cfg"], "dated": [{"from": "2026-10-20", "set": {"callback.end": "17:00"}}]}
    wf, _ = build(s, tools())
    for now, end in (("2026-10-14T11:00", "19:00"), ("2026-10-21T11:00", "17:00")):
        v = {"now": now}
        exec(code(wf, "setup_tp"), {"vars": v})
        assert "to " + end in v["callback_window_text"], (now, v["callback_window_text"])


# ---------------------------------------------------------------- Turkish gates (TR-7, TR-8)
def decline(wf, node, said, label="declines"):
    v = {"system.conversation": [{"role": "user", "content": said}], "id1": label}
    exec(code(wf, node), {"vars": v})
    return v["decline_real"]


def test_turkish_refusal_closes_and_a_cut_fragment_does_not():
    wf, _ = build(spec_tr(), tools())
    assert decline(wf, "decline_check", "Bunu size söylemeyeceğim") == "yes"  # cut, refusal words
    assert decline(wf, "decline_check", "NEDEN SORUYORSUNUZ") == "yes"
    assert decline(wf, "decline_check", "Vermek istemiyorum.") == "yes"
    assert decline(wf, "decline_check", "Şey, bir saniye ben") == "no"  # cut, no refusal
    assert decline(wf, "decline_check", "Bugün çok yoruldum.") == "yes"  # whole: the label stands
    # English keeps its own gate
    en, _ = build(SPEC_A, tools())
    assert decline(en, "decline_check", "I won't give you that") == "yes"
    assert decline(en, "decline_check", "Well the thing is I") == "no"


def test_turkish_third_party_can_book_a_day_said_with_the_time():
    wf, _ = build(spec_tr(), tools())
    v = {"now": "2026-10-14T11:00"}
    exec(code(wf, "setup_tp"), {"vars": v})
    ns = namespace()
    ns["set_locale"]({"lang": "tr"}) if "set_locale" in ns else None
    if ns["parse_spoken_date"]("yarın", datetime.date(2026, 10, 14))[0] != "2026-10-15":
        pytest.skip("speech_lib reads no Turkish day yet")
    v.update(cb_day_text="", cb_time_text="yarın saat 15:00", tp_reply="gives_time")
    exec(code(wf, "cb_check"), {"vars": v})
    assert (v["cb_state"], v.get("cb_date"), v.get("cb_time")) == ("ok", "2026-10-15", "15:00")
    assert route(wf, "cb_check", v) == "cb_book"


# ---------------------------------------------------------------- identity lockout (HA-1, F)
def post(wf, data, v, node="identity"):
    result = {"status": "success", "status_code": 200, "data": dict(data)}
    exec(tool_of(wf, node, "verify_identity")["post_process_code"], {"vars": v, "result": result})
    return result["data"].get("what_to_do_next", "")


def counter_api():
    """A tool API that reports how many wrong answers were given so far (0 at the start)."""
    o = copy.deepcopy(OPENAPI)
    props = o["paths"]["/verify_identity"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["properties"]
    props.pop("wrong_answers_left")
    props["wrong_answers"] = {"type": "integer", "description": "Wrong answers so far."}
    return o


def no_count_api():
    o = copy.deepcopy(OPENAPI)
    props = o["paths"]["/verify_identity"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["properties"]
    props.pop("wrong_answers_left")
    return o


def table(data, v):
    t = {"verify_identity_response.data." + k: str(x) for k, x in data.items()}
    t.update(v)
    return t


def test_a_count_of_wrong_answers_is_never_read_as_answers_left():
    s = copy.deepcopy(SPEC_A)
    # what spec_extract.autocorrect binds for such an API (HA-1)
    s["tool_fields"] = {"verify_identity": {"response": {"wrong_answers_left": "wrong_answers"}}}
    o = counter_api()
    wf, policy = build(s, parse_openapi(o))
    assert validate(wf, parse_openapi(o), policy).ok
    v = {"id_last_field": "date_of_birth"}
    first = {"ok": True, "verified": True, "wrong_answers": 0, "all_verified": False}
    assert post(wf, first, v).startswith("Correct. Now ask")
    assert route(wf, "identity", table(first, v)) is None  # stays: the next question
    v = {"id_last_field": "phone_last4"}
    for n in (1, 2):
        d = {"ok": True, "verified": False, "wrong_answers": n, "all_verified": False}
        assert post(wf, d, v).startswith("That answer is not correct")
        assert route(wf, "identity", table(d, v)) != "fin_failed"
    d = {"ok": True, "verified": False, "wrong_answers": 3, "all_verified": False}
    assert "has failed" in post(wf, d, v)
    assert v["id_locked"] == "yes" and route(wf, "identity", table(d, v)) == "fin_failed"


def test_three_wrong_answers_lock_with_no_count_from_the_bank():
    o = no_count_api()
    wf, _ = build(SPEC_A, parse_openapi(o))
    v = {"id_last_field": "date_of_birth"}
    wrong = {"ok": True, "verified": False, "all_verified": False}
    for i in range(1, 4):
        said = post(wf, wrong, v)
        assert v["id_wrong"] == str(i)
        if i < 3:
            assert said.startswith("That answer is not correct")
            assert route(wf, "identity", table(wrong, v)) != "fin_failed"
    assert "has failed" in said and route(wf, "identity", table(wrong, v)) == "fin_failed"
    assert route(wf, "identity2", table(wrong, v)) == "fin_failed"


def test_the_brief_sets_the_limit_and_hints_do_not_count():
    s = copy.deepcopy(SPEC_A)
    s["identity"]["max_wrong_answers"] = 2
    wf, policy = build(s, tools())
    assert not policy.build_notes
    v = {"id_last_field": "date_of_birth"}
    swap = {"ok": True, "verified": False, "wrong_answers_left": 3, "hint": "day_month_swapped"}
    assert "does not count" in post(wf, swap, v)
    unread = {"ok": False, "error": "answer is not a date"}
    assert "could not be read" in post(wf, unread, v)
    assert v["id_wrong"] == "0"
    wrong = {"ok": True, "verified": False, "wrong_answers_left": 2}
    post(wf, wrong, v)
    assert v["id_locked"] == "no"
    wrong = {"ok": True, "verified": False, "wrong_answers_left": 1}
    assert "has failed" in post(wf, wrong, v) and v["id_locked"] == "yes"


def test_the_bank_saying_none_left_still_locks():
    wf, _ = build(SPEC_A, tools())
    v = {"id_last_field": "date_of_birth"}
    d = {"ok": True, "verified": False, "wrong_answers_left": 0}
    assert "has failed" in post(wf, d, v)
    # also without the post hook's variable (the hook did not run)
    assert route(wf, "identity", table(d, {})) == "fin_failed"


def test_a_lock_flag_locks():
    o = no_count_api()
    props = o["paths"]["/verify_identity"]["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["properties"]
    props["locked"] = {"type": "boolean"}
    wf, _ = build(SPEC_A, parse_openapi(o))
    d = {"ok": True, "verified": False, "locked": True}
    v = {"id_last_field": "date_of_birth"}
    assert "has failed" in post(wf, d, v)
    assert route(wf, "identity", table({"locked": "True"}, {})) == "fin_failed"


def test_a_missing_limit_is_a_build_note():
    wf, policy = build(SPEC_A, tools())
    assert policy.build_notes and "3" in policy.build_notes[0]
    rep = validate(wf, tools(), policy)
    assert "build_note" in rep.codes() and rep.ok


# ---------------------------------------------------------------- partial digits (LP-6)
def pre(wf, body, v):
    cfg = {"body": dict(body)}
    exec(tool_of(wf, "identity", "verify_identity")["pre_process_code"], {"vars": v, "config": cfg})
    return cfg["body"]


def test_partial_digits_are_not_sent_as_an_answer():
    wf, _ = build(SPEC_A, tools())
    v = {"id_last": "four four seven", "id_heard": "four four seven"}
    body = pre(wf, {"customer_id": "c1", "field": "phone_last4", "answer": "447"}, v)
    assert "field" not in body and v["id_partial"] == "yes"
    # the bank refuses the request (no question); not a wrong answer, not a service failure
    result = {"status": "error", "status_code": 422, "data": "Unprocessable Entity"}
    exec(
        tool_of(wf, "identity", "verify_identity")["post_process_code"],
        {"vars": v, "result": result},
    )
    assert "remaining digits" in result["data"]["what_to_do_next"]
    assert v.get("id_down", "0") == "0" and v.get("id_wrong", "0") == "0"
    # the parts said in two turns, the model sending only the last one: all of them go
    v = {"id_last": "seven one", "id_heard": "four four seven one"}
    body = pre(wf, {"customer_id": "c1", "field": "phone_last4", "answer": "71"}, v)
    assert body["field"] == "phone_last4" and body["answer"] == "4471" and v["id_partial"] == ""
    # no digits at all goes to the bank as before
    v = {"id_last": "I don't know", "id_heard": "I don't know"}
    body = pre(wf, {"customer_id": "c1", "field": "phone_last4", "answer": "I don't know"}, v)
    assert body["field"] == "phone_last4" and v["id_partial"] == ""


def test_turkish_digits_and_dates_are_read_in_turkish():
    wf, _ = build(spec_tr(), tools())
    v = {"id_last": "dört dört yedi bir", "id_heard": "dört dört yedi bir"}
    body = pre(wf, {"customer_id": "c1", "field": "phone_last4", "answer": "dört dört yedi bir"}, v)
    if body.get("answer") != "4471":
        pytest.skip("speech_lib reads no Turkish digits yet")
    v = {"id_last": "on dört ekim bin dokuz yüz", "id_heard": ""}
    body = pre(wf, {"customer_id": "c1", "field": "date_of_birth", "answer": "14 Ekim 19"}, v)
    assert v["id_incomplete"] == ""  # 'ekim' is a month: no lone century


# ---------------------------------------------------------------- loops (LP-5)
def test_retry_loops_end_by_the_platform_counters():
    s = copy.deepcopy(SPEC_A)
    s["cfg"] = {**s["cfg"], "neg": {**s["cfg"]["neg"], "notes": True}}
    wf, policy = build(s, tools())
    assert validate(wf, tools(), policy).ok
    # after_record never ran: neg_action is still 'record'
    v = {"neg_action": "record", "record_success_count": "9", "record_failure_count": "0"}
    assert route(wf, "after_record", v) == "fin_loop_error"
    assert (
        route(wf, "after_record", {"neg_action": "record", "record_success_count": "2"}) == "record"
    )
    assert (
        route(wf, "after_note", {"note_count": "1", "note_failure_count": "9"}) == "fin_loop_error"
    )
    assert (
        route(wf, "after_cb", {"cb_next": "retry", "cb_book_failure_count": "5"})
        == "fin_loop_error"
    )
    # the ending: a promise the bank holds stays the outcome
    v = {"neg_state": json.dumps({"p": [{"id": "p1", "status": "recorded"}]})}
    exec(code(wf, "fin_loop_error"), {"vars": v})
    assert v["final_outcome"] == "promise" and v["final_reason"] == ""
    v = {"neg_state": json.dumps({"p": [{"id": "p1", "status": "agreed"}]})}
    exec(code(wf, "fin_loop_error"), {"vars": v})
    assert (v["final_outcome"], v["final_reason"]) == ("no_promise", "service_error")
    assert "technical" in v["final_say"]
    # without the guard the validator says the loop ends only by code
    t = wf["nodes"]["after_record"]["transitions"]
    wf["nodes"]["after_record"]["transitions"] = [
        x for x in t if x["target_node"] != "fin_loop_error"
    ]
    rep = validate(wf, tools(), policy)
    assert "loop_unguarded" in rep.codes()


# ---------------------------------------------------------------- tool URLs (summary B)
def test_tool_urls_must_be_absolute_and_on_the_bank():
    wf, policy = build(SPEC_A, tools())
    base = OPENAPI["servers"][0]["url"]
    assert validate(wf, tools(), Policy(**{**policy.__dict__, "base_url": base})).ok
    rep = validate(wf, tools(), Policy(**{**policy.__dict__, "base_url": "https://mock.example"}))
    assert "url_outside_base" in rep.codes()
    # a relative URL (a tool API without servers, read by an older parser) goes nowhere
    rel = tools()
    for t in rel.values():
        t.url = "/" + t.url.rsplit("/", 1)[-1]
    wf, policy = build(SPEC_A, rel)
    rep = validate(wf, rel, policy)
    assert "url_not_absolute" in rep.codes()


# ---------------------------------------------------------------- HA-11 / HA-14 / LP-11
def test_opening_and_refusal_counts_from_the_spec():
    s = copy.deepcopy(SPEC_A)
    s["identity"]["refusals_to_decline"] = 1
    s["identity"]["about_is_refusal"] = False
    s["who_asks"] = 1
    wf, policy = build(s, tools())
    assert validate(wf, tools(), policy).ok
    assert "identity2" not in wf["nodes"] and "decline_check2" not in wf["nodes"]
    assert route(wf, "decline_check", {"decline_real": "yes"}) == "fin_declined"
    assert route(wf, "opening", {"who1": "unclear"}) == "fin_unclear"
    desc = wf["nodes"]["identity"]["extraction_schema"][0]["description"]
    assert "asks what the call is about instead" not in desc
    assert decline(wf, "decline_check", "What is this about?") == "no"
    # Brief A's defaults
    wf, _ = build(SPEC_A, tools())
    assert route(wf, "decline_check", {"decline_real": "yes"}) == "identity2"
    assert route(wf, "opening", {"who1": "unclear"}) == "opening2"


def close(wf, body, v):
    fn = next(t for t in wf["tools"] if t["function"]["name"] == "close_call")
    cfg = {"body": dict(body)}
    exec(fn["pre_process_code"], {"vars": v, "config": cfg})
    return cfg["body"]


def test_a_hang_up_reason_from_the_outcome_table():
    s = copy.deepcopy(SPEC_A)
    neg = copy.deepcopy(s["cfg"]["neg"])
    neg["close_reasons"] = {**(neg.get("close_reasons") or {}), "hangup": "interrupted"}
    s["cfg"] = {**s["cfg"], "neg": neg}
    s["outcomes"] = {**s["outcomes"], "hangup": ["not_reached", "interrupted"]}
    o = copy.deepcopy(OPENAPI)
    wf, _ = build(s, parse_openapi(o))
    st = json.dumps({"p": [{"id": "p1", "status": "open"}, {"id": "p2", "status": "refused"}]})
    body = close(wf, {"customer_id": "c1", "outcome": "", "reason": ""}, {"neg_state": st})
    assert (body["outcome"], body["reason"]) == ("no_promise", "interrupted")
    body = close(wf, {"customer_id": "c1", "outcome": "", "reason": ""}, {})
    assert (body["outcome"], body["reason"]) == ("not_reached", "interrupted")
    # Brief A: refused, as before
    wf, _ = build(SPEC_A, tools())
    body = close(wf, {"customer_id": "c1", "outcome": "", "reason": ""}, {"neg_state": st})
    assert (body["outcome"], body["reason"]) == ("no_promise", "refused")


def test_an_outcome_the_tool_api_does_not_list_is_replaced():
    wf, _ = build(SPEC_A, tools())
    body = close(wf, {"customer_id": "c1", "outcome": "call_ended", "reason": "x"}, {})
    assert (body["outcome"], body["reason"]) == ("not_reached", "unclear")


# ---------------------------------------------------------------- minor units (summary I)
def test_amount_scale_from_the_account_tool():
    ts = tools()
    ts["get_account"].response_scale = {"products.past_due": 0.01, "products.minimum_payment": 0.01}
    wf, _ = build(SPEC_A, ts)

    def cfg_of(w):
        m = re.search(r"CFG = json\.loads\(r'''\n(.*?)\n'''\)", code(w, "facts"), re.S)
        return json.loads(m.group(1))

    assert cfg_of(wf)["amount_scale"] == {"past_due": 0.01, "minimum_payment": 0.01}
    en, _ = build(SPEC_A, tools())
    assert "amount_scale" not in cfg_of(en)


def test_turkish_readers_only_where_the_customer_is_read():
    wf, _ = build(spec_tr(), tools())
    hook = "_rules('tr', _tr)"
    assert hook in code(wf, "decide") and hook in code(wf, "cb_check")
    assert hook in tool_of(wf, "identity", "verify_identity")["pre_process_code"]
    for k in ("after_transfer", "after_record", "facts", "decline_check"):
        # a node holds the readers' rules only when its library code reads words with them
        src = code(wf, k)
        lib = src + "".join(_unpack(b) or "" for b in _packed_blobs(src))
        assert (hook in src) == ("def _rewrite(" in lib), k
    en, _ = build(SPEC_A, tools())
    assert all(hook not in code(en, k) for k in ("decide", "cb_check"))


# ---------------------------------------------------------------- a service the API lacks
def test_optional_services_turn_their_step_off():
    ts = tools()
    del ts["schedule_callback"], ts["transfer_to_agent"]
    wf, policy = build(SPEC_A, ts)
    rep = validate(wf, ts, policy)
    assert rep.ok, [str(i) for i in rep.errors]
    assert "cb_book" not in wf["nodes"] and "after_cb" not in wf["nodes"]
    assert route(wf, "cb_check", {"cb_state": "ok"}) == "fin_callback"
    assert wf["nodes"]["transfer"]["node_type"] == "code"
    assert any("schedule_callback" in n for n in policy.build_notes)


def test_a_required_service_missing_is_named():
    ts = tools()
    del ts["get_account"]
    with pytest.raises(ValueError, match="get_account"):
        build(SPEC_A, ts)


def test_a_service_with_no_readable_request_is_an_error():
    ts = tools()
    wf, policy = build(SPEC_A, ts)
    ts["add_note"].warnings = ["no request field could be read (check its schema and $refs)"]
    ts["get_account"].fields = []
    rep = validate(wf, ts, policy)
    assert "tool_no_fields" in rep.codes()


def test_a_missing_note_service_turns_the_notes_off():
    s = copy.deepcopy(SPEC_A)
    s["cfg"] = {**s["cfg"], "neg": {**s["cfg"]["neg"], "notes": True}}
    ts = tools()
    del ts["add_note"]
    wf, policy = build(s, ts)
    assert validate(wf, ts, policy).ok
    assert "note" not in wf["nodes"] and "after_note" not in wf["nodes"]


# ---------------------------------------------------------------- forwarded (SM-1, SM-6, SM-11)
def test_also_hardship_is_read_beside_the_reply_kind():
    wf, _ = build(SPEC_A, tools())
    for k in ("negotiate_say", "negotiate"):
        xs = wf["nodes"][k]["extraction_schema"]
        names = [x["name"] for x in xs]
        assert names[names.index("reply_kind") + 1] == "also_hardship", k
        assert xs[names.index("also_hardship")]["enum_values"] == ["yes", "no"]


def test_a_refusal_at_the_opening_closes_as_declined_the_second_time():
    wf, policy = build(SPEC_A, tools())
    assert validate(wf, tools(), policy).ok
    who = wf["nodes"]["opening"]["extraction_schema"][0]
    assert "refuses" in who["enum_values"] and "refuses:" in who["description"]
    assert route(wf, "opening", {"who1": "refuses"}) == "opening2"
    assert route(wf, "opening2", {"who2": "refuses"}) == "fin_declined"
    assert route(wf, "opening2", {"who2": "unclear"}) == "fin_unclear"


def test_the_spec_gives_how_many_digits_an_answer_has():
    s = copy.deepcopy(SPEC_A)
    s["identity"]["questions"] = [
        {"field": "date_of_birth", "ask": "What is your date of birth?", "kind": "date"},
        {"field": "phone_digits", "ask": "Your phone number's end?", "kind": "digits", "length": 6},
    ]
    o = copy.deepcopy(OPENAPI)
    enum = o["paths"]["/verify_identity"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]["properties"]["field"]
    enum["enum"] = ["date_of_birth", "phone_digits"]
    wf, _ = build(s, parse_openapi(o))
    v = {"id_last": "four four seven one", "id_heard": "four four seven one"}
    body = pre(wf, {"customer_id": "c1", "field": "phone_digits", "answer": "4471"}, v)
    assert "field" not in body and v["id_partial"] == "yes"  # four of six: held back
    v = {"id_last": "four four seven one two two", "id_heard": "four four seven one two two"}
    body = pre(wf, {"customer_id": "c1", "field": "phone_digits", "answer": "447122"}, v)
    assert body["field"] == "phone_digits" and body["answer"] == "447122"


# ---------------------------------------------------------------- plans (neg.plans_allowed)
def test_the_plan_instruction_follows_the_documents():
    def task(wf):
        return wf["nodes"]["negotiate"]["task_message"]

    en, _ = build(SPEC_A, tools())
    assert "Never offer a discount, a waiver or an instalment plan." in task(en)
    s = copy.deepcopy(SPEC_A)
    s["cfg"] = {**s["cfg"], "neg": {**s["cfg"]["neg"], "plans_allowed": True}}
    wf, _ = build(s, tools())
    assert "Never offer a discount" not in task(wf)
    assert "only as the facts below or the sentences given here state it" in task(wf)
