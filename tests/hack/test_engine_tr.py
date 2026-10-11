"""The engine in another language (the private final is one Turkish case): the language packs'
keys and placeholders, every sentence the engine speaks coming from them, the Turkish word lists
on the decision paths, and Turkish calls walked through the code-node bodies end to end (no
English sentence leaks, the promises the kit's scorer expects)."""

import ast
import copy
import re

import pytest
from fb import langpack
from fb.harness.bank import Bank
from fb.harness.scoring import score_ledger
from fb.harness.selftest import Engine
from fb.nodecode import bodies
from fb.nodecode.embed import namespace
from test_engine import CASES, CFG, CFG_A

from conftest import ROOT

NODECODE = ROOT / "builder" / "fb" / "nodecode"
EN, TR = langpack.ENGINE_LINES["en"], langpack.ENGINE_LINES["tr"]
# keys the engine builds at run time (negotiation_lib._sorry, _item, _transfer)
DYNAMIC = {"sorry_1", "sorry_2", "sorry_3", "readback_item", "readback_item_deadline", "ack_item"}
DYNAMIC |= {"readback_item_moved"}
DYNAMIC |= {"transfer_" + w for w in ("human", "restructuring", "support")}
# the 24 keys whose English line is the brief-neutral one, not the engine's own default
NEUTRAL = {
    "no_account", "present_intro", "today_question", "multi_today_question", "confirm_question",
    "anything_else", "support_reask", "rest_overdue", "vague_ask", "offer_day_question",
    "help_first", "support_offer", "sympathy", "paid", "dispute_noted", "which_change",
    "one_by_one", "recorded", "cannot_change", "transfer_human", "transfer_restructuring",
    "transfer_support", "transfer_failed", "goodbye",
}  # fmt: skip


def placeholders(s):
    return set(re.findall(r"\{(\w+)\}", s))


def line_calls():
    """(key, English default) of every _line call with a literal key in the engine's code."""
    out = []
    trees = [ast.parse((NODECODE / "negotiation_lib.py").read_text("utf-8"))]
    # the code-node bodies are strings: each one as the node runs it
    texts = [v for v in vars(bodies).values() if isinstance(v, str)] + [bodies.decline_check("x")]
    trees += [ast.parse(t) for t in texts if "_line(" in t]
    for c in (c for t in trees for c in ast.walk(t)):
        if (
            isinstance(c, ast.Call)
            and getattr(c.func, "id", "") == "_line"
            and len(c.args) >= 2
            and isinstance(c.args[1], ast.Constant)
        ):
            d = c.args[2] if len(c.args) > 2 else None
            out.append((c.args[1].value, d.value if isinstance(d, ast.Constant) else None))
    return out


# ---------------------------------------------------------------- the packs
def test_both_packs_have_the_same_keys_and_placeholders():
    assert set(EN) == set(TR)
    for k in EN:
        assert placeholders(EN[k]) == placeholders(TR[k]), k
        assert TR[k].strip() and EN[k].strip(), k


def test_every_line_the_engine_speaks_is_in_the_english_pack():
    keys = {k for k, _ in line_calls()} | DYNAMIC
    assert keys <= set(EN), keys - set(EN)
    # and nothing in the pack is dead
    assert set(EN) <= keys, set(EN) - keys


def test_english_pack_is_the_engines_own_words():
    # an English call says exactly what it said before the packs (the neutral 24 aside, which
    # the spec always sets)
    for key, default in line_calls():
        if default is not None and key not in NEUTRAL:
            assert EN[key] == default, key


def test_turkish_pack_has_no_english_sentence():
    for k, s in TR.items():
        assert not ENGLISH.search(s), (k, s)


def test_pack_lines_fill_their_placeholders():
    ns = namespace()
    ns["set_locale"]({"lang": "tr"})
    try:
        neg = {"lines": TR}
        s = ns["_line"](neg, "attempt_offer", "", amount="200 TL", product="kredi kartı", date="X")
        assert s == "Kredi kartı için X tarihinde 200 TL ödeyebilir misiniz?"
        s = ns["_line"](neg, "readback_item", "", amount="1", product="ihtiyaç kredisi", date="D")
        assert s.startswith("ihtiyaç kredisi")  # mid-sentence: no capital
        s = ns["_line"](neg, "readback_item_deadline", "", amount="1", product="ihtiyaç", date="D")
        assert s.startswith("ihtiyaç")
        assert ns["_line"]({"lines": {"x": "{a} {{b}"}}, "x", "", a=1) == "1 {{b}"
    finally:
        ns["set_locale"]({})


# ---------------------------------------------------------------- Turkish calls end to end
ENGLISH = re.compile(
    r"\b(the|you|your|pay|could|confirm|sorry|minimum|latest|record|recorded|promise|today|"
    r"goodbye|thank|please|which|day|cannot|connect|noted)\b",
    re.I,
)
EN_DATES = re.compile(r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|October|November)\b")
TR_NAMES = {
    "credit_card": "{card_last4} ile biten kredi kartı",
    "personal_loan": "ihtiyaç kredisi",
    "car_loan": "taşıt kredisi",
}


def tr_cfg(base, lang="tr"):
    c = copy.deepcopy(base)
    c["lang"] = lang
    c["fmt"] = langpack.fmt(lang)
    c["product_names"] = dict(TR_NAMES)
    c["neg"]["lines"] = dict(langpack.engine_lines(lang))
    return c


TR_CFG, TR_CFG_A = tr_cfg(CFG), tr_cfg(CFG_A)


def call(case, replies, cfg, engine=None):
    """One call through the bodies with every extraction variable the engine reads.
    Returns (spoken sentences, ledger, code errors)."""
    case = copy.deepcopy(case)
    eng, bank, errs, says = engine or Engine(), Bank(), [], []
    v = dict(bank.open_session(case))
    cid = v["customer_id"]
    eng.run(bodies.SETUP, cfg, v, errs)
    for f, a in case["identity"].items():
        bank.call(cid, "verify_identity", {"customer_id": cid, "field": f, "answer": a})
    _, acc = bank.call(cid, "get_account", {"customer_id": cid})
    v["products"] = acc.get("products")
    eng.run(bodies.FACTS, cfg, v, errs)

    def said():
        s = str(v.get("neg_say") or "")
        if v.get("neg_mode") == "llm":  # an instruction to the model: its quoted sentence
            s = s.split('exactly: "', 1)[-1].rstrip('"')
        says.append(s)

    said()
    for r in replies:
        if v.get("neg_action") in ("finish", "transfer"):
            break
        v.update(
            reply_kind=r.get("kind", ""),
            said_date_text=r.get("date", ""),
            said_amount_text=r.get("amount", ""),
            all_products="yes" if r.get("all") else "no",
            about_product=r.get("product", "none"),
            has_question="yes" if r.get("kind") == "question" else "no",
            said_amount=r.get("said_amount", ""),
            said_date_iso=r.get("said_date_iso", ""),
            also_hardship=r.get("also_hardship", "no"),
        )
        v["system.conversation"] = [{"role": "user", "content": r.get("text", "")}]
        eng.run(bodies.DECIDE, cfg, v, errs)
        while v.get("neg_action") == "record":
            st, out = bank.call(
                cid,
                "create_promise",
                {
                    "customer_id": cid,
                    "product_id": v["rec_product_id"],
                    "amount": v["rec_amount"],
                    "date": v["rec_date"],
                },
            )
            v["rec_ok"] = "true" if st == 200 and out.get("ok") else "false"
            v["rec_err"] = "" if st == 200 and out.get("ok") else str(out.get("error") or "")
            eng.run(bodies.AFTER_RECORD, cfg, v, errs)
        if v.get("neg_action") == "say":
            said()
    if v.get("neg_action") == "transfer":
        for _ in range(3):
            q = {"customer_id": cid, "queue": v["transfer_queue"]}
            st, out = bank.call(cid, "transfer_to_agent", q)
            v["transfer_ok"] = "true" if st == 200 and out.get("ok") else "false"
            eng.run(bodies.AFTER_TRANSFER, cfg, v, errs)
            if v.get("transfer_next") != "retry":
                break
    if v.get("neg_action") in ("finish", "transfer"):
        while int(v.get("note_count") or 0) > 0:
            n = {"customer_id": cid, "product_id": v["note_product_id"], "reason": v["note_reason"]}
            bank.call(cid, "add_note", n)
            v["note_ok"] = "true"
            eng.run(bodies.AFTER_NOTE, cfg, v, errs)
        says.append(str(v.get("final_say") or ""))
    close = {"customer_id": cid, "outcome": v.get("final_outcome") or ""}
    if v.get("final_reason"):
        close["reason"] = v["final_reason"]
    bank.call(cid, "close_call", close)
    return says, bank.ledger(cid), errs


def promises(led):
    return [(p["product_id"], p["amount"], p["date"]) for p in led.get("promises") or []]


Y = {"kind": "yes", "text": "Evet."}
N = {"kind": "no", "text": "Hayır, ödeyemem."}
BYE = {"kind": "unclear", "text": "Hayır, bu kadar."}
# the public scenarios of test_engine.py, said in Turkish
TR_SCENARIOS = {
    "PUB-01": [Y, Y, BYE],  # full today
    "PUB-04": [  # a date in Turkish after the deadline, a question
        {"kind": "offer", "date": "6 Kasım", "text": "6 Kasım'da ödeyebilirim."},
        {"kind": "question", "text": "Bu görüşme kaydediliyor mu?"},
        Y,
        Y,
        BYE,
    ],
    "PUB-05": [  # hardship
        {"kind": "hardship", "text": "İşimi kaybettim, çok zor durumdayım."},
        {"kind": "question", "text": "Bu ne demek?"},
        N,
    ],
    "PUB-06": [  # too much -> floor, then a Turkish day
        {"kind": "too_much", "text": "Bu benim için çok fazla."},
        {"kind": "unclear", "text": "Şey"},
        {"kind": "offer", "date": "bu cuma", "text": "Bu cuma ödeyebilirim."},
        Y,
        BYE,
    ],
    "PUB-07": [  # vague, then a holiday
        {"kind": "vague", "date": "öbür hafta", "text": "Öbür hafta bir ara."},
        {"kind": "offer", "date": "26 Ekim Pazartesi", "text": "26 Ekim Pazartesi olur."},
        Y,
        Y,
        BYE,
    ],
    "PUB-08": [{"kind": "paid", "text": "Ben bunu zaten ödedim."}],
    "PUB-09": [{"kind": "restructure", "text": "Taksitlerimi yeniden yapılandırmak istiyorum."}],
    "PUB-10": [N, Y, Y, BYE],  # refusal, attempt accepted
    "PUB-11": [  # several products, one day for all
        N,
        {"kind": "offer", "date": "bu cuma", "all": True, "text": "Hepsini bu cuma öderim."},
        Y,
        BYE,
    ],
    "PUB-12": [
        {"kind": "offer", "date": "yarın", "product": "p1", "text": "Kartı yarın öderim."},
        N,
        N,
        N,
        Y,
        BYE,
    ],
    "PUB-13": [  # one day after one product's deadline
        N,
        {"kind": "offer", "date": "30 Ekim", "all": True, "text": "İkisini de 30 Ekim'de öderim."},
        Y,
        BYE,
    ],
}


def assert_turkish(says):
    for s in says:
        assert not ENGLISH.search(s), s
        if (langpack.fmt("tr") or {}).get("weekdays"):
            assert not EN_DATES.search(s), s


@pytest.mark.parametrize("name", sorted(TR_SCENARIOS))
def test_turkish_public_scenarios_pass_and_speak_turkish(name):
    case = CASES[name]
    cfg = TR_CFG if case["brief"] == "B" else TR_CFG_A
    says, led, errs = call(case, TR_SCENARIOS[name] + [BYE] * 3, cfg)
    assert not errs, errs
    sc = score_ledger(case, led)
    assert sc["pass"], (sc["failed"], says)
    assert_turkish(says)


def test_turkish_call_through_the_generated_snippet():
    # the real snippet: Turkish sentences in CFG, the libraries cut to what the node uses
    reps = TR_SCENARIOS["PUB-06"] + [BYE] * 3
    says, led, errs = call(CASES["PUB-06"], reps, TR_CFG_A, Engine(real=True))
    assert not errs, errs
    assert score_ledger(CASES["PUB-06"], led)["pass"], says
    assert_turkish(says)


def test_turkish_refusals_end_the_call_as_refused():
    no = [{"kind": "no", "text": t} for t in ("Hayır.", "Ödemeyeceğim.", "Ödeyemem, hayır.")]
    says, led, errs = call(CASES["PUB-01"], no + [BYE] * 3, TR_CFG_A)
    assert not errs
    assert (led["outcome"], led["reason"]) == ("no_promise", "refused"), says
    assert_turkish(says)


def test_turkish_stop_cut_without_a_full_stop_still_counts():
    stop = {"kind": "stop", "text": "Artık sizinle konuşmak istemiyorum"}
    says, led, _ = call(CASES["PUB-01"], [stop, stop], TR_CFG_A)
    assert (led["outcome"], led["reason"]) == ("no_promise", "refused"), says
    assert says[1].startswith("Anlıyorum.")


def test_turkish_dispute_and_human_and_paid_all():
    dispute = {"kind": "dispute", "text": "Bu borç bana ait değil."}
    _, led, _ = call(CASES["PUB-01"], [dispute], TR_CFG_A)
    assert (led["outcome"], led["reason"]) == ("no_promise", "dispute")
    human = {"kind": "human", "text": "Bir müşteri temsilcisiyle görüşmek istiyorum."}
    says, led, _ = call(CASES["PUB-01"], [human, human], TR_CFG_A)
    assert (led["outcome"], led["reason"]) == ("transferred", "human")
    assert says[1] == TR["help_first"]
    paid = {"kind": "paid", "all": True, "text": "Hepsini zaten ödedim."}
    says, led, _ = call(CASES["PUB-11"], [paid], TR_CFG)
    assert led["outcome"] == "paid", says
    assert_turkish(says)


def test_turkish_amount_words_and_floor():
    # 'dört yüz' read by speech_lib in a Turkish call; below the floor -> the floor once
    low = {"kind": "offer", "amount": "elli lira", "date": "bu cuma", "text": "Bu cuma elli lira."}
    says, led, _ = call(CASES["PUB-01"], [low, Y, Y, BYE], TR_CFG_A)
    assert promises(led) == [("p1", 126.0, "2026-10-16")], says
    assert "Asgari tutar" in says[1], says
    assert_turkish(says)


def test_turkish_minimum_and_all_of_it_name_the_floor_and_the_full_amount():
    mn = {"kind": "offer", "amount": "asgari tutarı", "date": "bu cuma", "text": "Asgari tutarı."}
    _, led, _ = call(CASES["PUB-01"], [mn, Y, BYE], TR_CFG_A)
    assert promises(led) == [("p1", 126.0, "2026-10-16")]
    full = {"kind": "offer", "amount": "tamamını", "date": "bu cuma", "text": "Tamamını öderim."}
    _, led, _ = call(CASES["PUB-01"], [full, Y, BYE], TR_CFG_A)
    assert promises(led) == [("p1", 420.0, "2026-10-16")]


def test_turkish_mixed_yes_and_no_names_the_product():
    # "the card on Friday, but not the loan": only the card gets the day
    said = "Kartı bu cuma öderim ama krediyi ödemeyeceğim."
    mixed = {"kind": "offer", "date": "bu cuma", "text": said}
    _, led, _ = call(CASES["PUB-13"], [N, mixed, N, N, Y, BYE], TR_CFG)
    assert [p[0] for p in promises(led)] == ["p1"], led


# ---------------------------------------------------------------- word lists, unit level
@pytest.fixture
def tr_ns():
    ns = namespace()
    ns["set_locale"]({"lang": "tr"})
    yield ns
    ns["set_locale"]({})


@pytest.mark.parametrize(
    "text,real",
    [
        ("Hayır.", True),
        ("Bunu söylemeyeceğim", True),
        ("Vermek istemiyorum", True),
        ("Sizinle konuşmak istemiyorum", True),
        ("Neden arıyorsunuz?", True),
        ("Siz kimsiniz?", True),
        ("Bu bilgiyi vermem.", True),
        ("Bugün işte çok yoğundum çünkü", False),  # cut off, no refusal
        ("Bugün işte çok yoğun bir gün geçirdim, trafik de berbattı, eve yeni geldim.", False),
    ],
)
def test_turkish_identity_refusal_is_real(tr_ns, text, real):
    assert tr_ns["decline_real"](text) is real


def test_turkish_word_lists(tr_ns):
    rr = tr_ns["read_reply"]
    import datetime

    today = datetime.date(2026, 10, 14)
    conv = lambda t: [{"role": "user", "content": t}]  # noqa: E731
    r = rr(
        {
            "reply_kind": "offer",
            "said_date_text": "cuma",
            "system.conversation": conv("İkisini de cuma öderim."),
        },
        today,
    )
    assert r["all"] and r["date"] == "2026-10-16"
    r = rr({"reply_kind": "stop", "system.conversation": conv("Beni bir daha aramayın")}, today)
    assert r["kind"] == "stop"
    r = rr({"reply_kind": "no", "system.conversation": conv("Hayır ödemeyeceğim")}, today)
    assert r["kind"] == "unclear" and r["cut_kind"] == "no"  # cut off: counted later
    r = rr(
        {
            "reply_kind": "offer",
            "said_date_text": "o gün",
            "system.conversation": conv("O gün olur."),
        },
        today,
    )
    assert r["date_kind"] != "vague"
    assert tr_ns["_named_in"](
        {"p": [{"name": "4821 ile biten kredi kartı"}, {"name": "taşıt kredisi"}]},
        "Kartımı öderim ama taşıtı değil",
    ) == [0, 1]


def test_model_amount_and_date_are_read_only_when_ours_read_nothing(tr_ns):
    import datetime

    rr, today = tr_ns["read_reply"], datetime.date(2026, 10, 14)
    v = {"reply_kind": "offer", "said_amount_text": "o kadar işte", "said_amount": "250"}
    assert rr(v, today)["amount"] == 250.0
    v = {"reply_kind": "offer", "said_amount_text": "dört yüz", "said_amount": "999"}
    assert rr(v, today)["amount"] == 400.0  # our own reading wins
    v = {"reply_kind": "offer", "said_amount_text": "yarısını"}
    r = rr(v, today)
    assert r["amount"] is None and r["amount_bad"]  # asked again, never the full amount
    v = {
        "reply_kind": "offer",
        "said_date_text": "annemin doğum gününde",
        "said_date_iso": "2026-10-30",
    }
    assert rr(v, today)["date"] == "2026-10-30"
    v = {
        "reply_kind": "offer",
        "said_date_text": "annemin doğum gününde",
        "said_date_iso": "2025-10-30",
    }
    r = rr(v, today)
    assert r["date"] == "" and r["kind"] == "vague"  # a day in the past is a slip
    v = {"reply_kind": "offer", "said_date_text": "öbür hafta", "said_date_iso": "2026-10-21"}
    assert rr(v, today)["date"] == ""  # a period our parser knows as vague stays vague


def test_turkish_callback_day_said_with_the_time():
    from fb.harness.selftest import run_cb

    # 'yarın saat 15:00' all in the time words (third party): the day is in there too
    got, errs = run_cb(TR_CFG_A, "2026-10-14T11:00", "", "yarın saat 15:00")
    assert not errs and got == ("ok", "2026-10-15", "15:00"), got
    got, _ = run_cb(TR_CFG_A, "2026-10-14T11:00", "", "saat 15:00")
    assert got == ("ok", "2026-10-14", "15:00"), got


def test_unknown_language_trusts_the_models_label():
    ns = namespace()
    ns["set_locale"]({"lang": "de"})
    try:
        assert ns["decline_real"]("Das sage ich Ihnen nicht, das geht Sie gar nichts an, wirklich.")
        assert not ns["decline_real"]("Ich war heute so beschäftigt weil")
        r = ns["read_reply"]({"reply_kind": "offer", "said_amount_text": "die Hälfte"}, None)
        assert r["amount_bad"]
    finally:
        ns["set_locale"]({})


def test_a_plan_step_dated_by_another_rule_word_is_offered_on_that_day():
    """A plan step dated "next_business_day" (or any collections_lib.rule_date word) was
    offered on attempt 1's day; the product's deadline still caps it."""
    ns = namespace()
    cfg = copy.deepcopy(CFG_A)
    cfg["neg"] = dict(cfg.get("neg") or {})
    cfg["neg"]["attempt_plan"] = [{"amount": "current", "date": "next_business_day"}]
    cal = ns["engine_cal"]("2026-10-14T11:00", cfg)  # a Wednesday
    assert cal["rule_dates"] == {"next_business_day": "2026-10-15"}
    p = {"used": 1, "floor": 50.0, "full": 400.0, "deadline": "2026-10-30", "too_much": False}
    st = {"p": [dict(p, name="card")]}
    ns["_attempt_offer"](st, 0, cfg["neg"], cal)
    assert st["p"][0]["offer"]["date"] == "2026-10-15"
    st["p"][0].update(deadline="2026-10-14", offer=None)
    ns["_attempt_offer"](st, 0, cfg["neg"], cal)
    assert st["p"][0]["offer"]["date"] == "2026-10-14"
    # the default plan has no other word: nothing extra in the calendar facts
    assert "rule_dates" not in ns["engine_cal"]("2026-10-14T11:00", CFG_A)
