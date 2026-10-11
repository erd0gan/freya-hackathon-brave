"""Turkish speech: what a Turkish customer says about a day, a time, an amount, a date of birth
or digits reads like its English counterpart (audit 02: TR-3, TR-4, TR-5, TR-9). General
language only (month, weekday and number words, suffixes, idioms), no case values."""

import datetime

import pytest
from fb import langpack
from fb.nodecode.embed import namespace, snippet

NS = namespace()
WED = datetime.date(2026, 10, 14)  # a Wednesday


@pytest.fixture(autouse=True)
def english_after():
    yield
    NS["set_locale"](dict())  # every other test reads English


def date(text, today=WED):
    return NS["parse_spoken_date"](text, today, lang="tr")


# ---- TR-3: days
@pytest.mark.parametrize(
    "text,want",
    [
        ("on beş Ekim", ("2026-10-15", "date")),
        ("15 Ekim", ("2026-10-15", "date")),
        ("15 Ekim 2026", ("2026-10-15", "date")),
        ("on beş ekimde", ("2026-10-15", "date")),
        ("yirmi üç ekim", ("2026-10-23", "date")),
        ("23 Ekim Cuma", ("2026-10-23", "date")),
        ("22 Ekim Cuma", ("", "unclear")),  # the 22nd is a Thursday: asked again
        ("27 Ekim", ("2026-10-27", "date")),
        ("Ekimin 20'si", ("2026-10-20", "date")),
        ("aralık ayının 5'i", ("2026-12-05", "date")),
        ("ayın on beşi", ("2026-10-15", "date")),
        ("ayın 15'i", ("2026-10-15", "date")),
        ("ayın on beşinde", ("2026-10-15", "date")),
        ("15'inde", ("2026-10-15", "date")),
        ("ayın 27'si", ("2026-10-27", "date")),
        ("ayın yirmi yedisi", ("2026-10-27", "date")),
        ("ayın yirmi biri", ("2026-10-21", "date")),
        ("ayın beşinci günü", ("2026-11-05", "date")),  # the 5th has passed: next month's
        ("gelecek ayın 5'i", ("2026-11-05", "date")),
        ("bir kasım", ("2026-11-01", "date")),
        ("kasımın biri", ("2026-11-01", "date")),
        ("salı ya da çarşamba", ("", "unclear")),  # as 'Tuesday or Wednesday'
        ("bu hafta içinde", ("", "vague")),
        # weekdays, with suffixes; 'next' is the following calendar week (as in English)
        ("cuma", ("2026-10-16", "date")),
        ("cuma günü", ("2026-10-16", "date")),
        ("cumaya", ("2026-10-16", "date")),
        ("cumartesi", ("2026-10-17", "date")),
        ("pazartesi", ("2026-10-19", "date")),
        ("PAZARTESİ", ("2026-10-19", "date")),
        ("salı günü olur", ("2026-10-20", "date")),
        ("perşembe", ("2026-10-15", "date")),
        ("bu cuma", ("2026-10-16", "date")),
        ("önümüzdeki cuma", ("2026-10-23", "date")),
        ("gelecek cuma", ("2026-10-23", "date")),
        ("haftaya salı", ("2026-10-20", "date")),
        ("haftaya cuma", ("2026-10-23", "date")),
        ("bugün", ("2026-10-14", "date")),
        ("yarın", ("2026-10-15", "date")),
        ("yarın sabah", ("2026-10-15", "date")),
        ("bu akşam", ("2026-10-14", "date")),
        ("öbür gün", ("2026-10-16", "date")),
        ("yarından sonra", ("2026-10-16", "date")),
        ("iki gün sonra", ("2026-10-16", "date")),
        ("15 gün sonra", ("2026-10-29", "date")),
        ("bir hafta sonra", ("2026-10-21", "date")),
        ("iki hafta sonra", ("2026-10-28", "date")),
        # a period, not a day: asked once, like the English ones
        ("ay sonu", ("", "vague")),
        ("ayın sonunda", ("", "vague")),
        ("maaş yatınca", ("", "vague")),
        ("maaşı alınca", ("", "vague")),
        ("maaş günü", ("", "vague")),
        ("haftaya", ("", "vague")),
        ("gelecek hafta", ("", "vague")),
        ("önümüzdeki hafta", ("", "vague")),
        ("hafta sonu", ("", "vague")),
        ("bir ay sonra", ("", "vague")),
        ("cumadan sonra", ("", "vague")),
        # a refused or corrected day: the day left
        ("yarın değil cuma", ("2026-10-16", "date")),
        ("pazartesi değil salı", ("2026-10-20", "date")),
        ("cuma, yarın değil", ("2026-10-16", "date")),
        ("bugün ödeyemem ama cuma öderim", ("2026-10-16", "date")),
        ("yirmi bir, yok yirmi Ekim", ("2026-10-20", "date")),
        ("yarın değil", ("", "none")),
        # numbers as written
        ("23.10.2026", ("2026-10-23", "date")),
        ("23/10", ("2026-10-23", "date")),
        ("2026-10-23", ("2026-10-23", "date")),
    ],
)
def test_turkish_days(text, want):
    assert date(text) == want


def test_turkish_next_weekday_on_that_weekday():
    fri = datetime.date(2026, 10, 16)
    assert date("bu cuma", fri) == ("2026-10-16", "date")
    assert date("haftaya cuma", fri) == ("2026-10-23", "date")


def test_the_call_language_is_the_default():
    NS["set_locale"](dict(lang="tr"))
    assert NS["parse_spoken_date"]("haftaya salı", WED) == ("2026-10-20", "date")
    assert NS["parse_amount"]("1.250") == 1250
    NS["set_locale"](dict(lang="en"))
    assert NS["parse_spoken_date"]("next Tuesday", WED) == ("2026-10-20", "date")
    assert NS["parse_amount"]("1.250") == 1.25  # English: a decimal point


# ---- TR-3: times (the callback check reads them)
@pytest.mark.parametrize(
    "text,want",
    [
        ("saat on bir civarı", "11:00"),
        ("saat 11", "11:00"),
        ("on bir buçuk", "11:30"),
        ("saat on birde", "11:00"),
        ("öğleden sonra iki", "14:00"),
        ("öğleden sonra birde", "13:00"),
        ("sabah dokuzda", "09:00"),
        ("akşam beşte", "17:00"),
        ("yarın saat on", "10:00"),
        ("saat 3'te", "15:00"),
        ("saat iki buçukta", "14:30"),
        ("ikiyi çeyrek geçe", "14:15"),
        ("üçü on geçe", "15:10"),
        ("üçe çeyrek var", "14:45"),
        ("öğlen", "12:00"),
        ("14:30", "14:30"),
        ("saat 10.30", "10:30"),
        # a day of the month is no time
        ("20 Ekim", ""),
        ("ayın 20'si", ""),
    ],
)
def test_turkish_times(text, want):
    assert NS["parse_spoken_time"](text, lang="tr") == want


# ---- TR-4: amounts
@pytest.mark.parametrize(
    "text,want",
    [
        ("1.250", 1250),
        ("1.250 TL", 1250),
        ("2.500 lira", 2500),
        ("12.500", 12500),
        ("1.250,50 TL", 1250.50),
        ("2.500,75", 2500.75),
        ("1250,5", 1250.50),
        ("1250,50 TL", 1250.50),
        ("₺1.250", 1250),
        ("1,250.50", 1250.50),  # written the English way: still read
        ("1.5", 1.5),
        ("1250", 1250),
        ("420 lira", 420),
        ("420 TL", 420),
        ("650 TL'yi", 650),
        ("450 lira öderim", 450),
        ("500 civarı", 500),
        ("dört yüz yirmi lira", 420),
        ("bin iki yüz elli", 1250),
        ("on iki yüz elli", 1250),
        ("yüz elli lira kırk kuruş", 150.40),
        ("1250 lira 50 kuruş", 1250.50),
        ("iki bin", 2000),
        ("ikibin", 2000),
        ("yüzelli", 150),
        ("ikibinbeşyüz", 2500),
        ("bin lira", 1000),
        ("bin beş yüz", 1500),
        ("iki yüz elli bin", 250000),
        ("4 yüz 50", 450),
        ("altı yüz otuz yedi buçuk", 637.5),
        ("iki bin buçuk", 2500),
        ("dört yüz, yok beş yüz", 500),
        ("500 değil 400", 400),
        ("ayın 20'sinde 500 lira", 500),
        ("cuma günü 300 TL", 300),
        ("bu yüzden 300 lira", 300),  # 'bu yüzden' (that is why) holds no hundred
        ("dört elli", 4.50),  # as English 'four fifty'
        ("yirmi bir lira", 21),
    ],
)
def test_turkish_amounts(text, want):
    assert NS["parse_amount"](text, lang="tr") == pytest.approx(want)


@pytest.mark.parametrize("text", ["yarısı", "%50", "yüzde elli", "bir kısmını", "biraz"])
def test_a_share_is_no_amount_but_amount_like(text):
    assert NS["parse_amount"](text, lang="tr") is None
    assert NS["amount_like"](text, lang="tr")


@pytest.mark.parametrize("text", ["ikisini de cuma öderim", "onu cuma öderim", "evet", "cuma"])
def test_no_amount_words(text):
    assert NS["parse_amount"](text, lang="tr") is None
    assert not NS["amount_like"](text, lang="tr")


def test_amount_like_english_covers_amount_words():
    for t in ("half of it", "50%", "the minimum", "four hundred", "20 percent", "the full lot"):
        assert NS["amount_like"](t, lang="en"), t
    for t in ("yes", "on Friday", "I can pay it"):
        assert not NS["amount_like"](t, lang="en"), t
    assert not NS["amount_like"]("yarısı", lang="en")  # Turkish words only in a Turkish call


def test_amount_with_price_reading_lo_hi():
    # 'üç elli' said like a price, as English 'three fifty': 350 when the floor is 200
    assert NS["parse_amount"]("üç elli", 200, 1250, lang="tr") == 350


# ---- TR-5: digits and dates of birth
@pytest.mark.parametrize(
    "text,want",
    [
        ("sıfır sıfır yedi üç", "0073"),
        ("sıfır sıfır yetmiş üç", "0073"),
        ("0073", "0073"),
        ("0 0 7 3", "0073"),
        ("yedi üç", "73"),
        ("yetmiş üç", "73"),
        ("dört dört yedi bir", "4471"),
        ("kırk dört yetmiş bir", "4471"),
        ("çift dört yedi bir", "4471"),
        ("4 dört 7 bir", "4471"),
        ("doksan dokuz, sıfır dört", "9904"),
        ("on dokuz seksen sekiz", "1988"),
        ("bin dokuz yüz seksen sekiz", "1988"),
    ],
)
def test_turkish_digits(text, want):
    assert NS["digits_from_speech"](text, lang="tr") == want


@pytest.mark.parametrize(
    "text,want",
    [
        ("14 Mart 1988", "1988-03-14"),
        ("14 MART 1988", "1988-03-14"),
        ("14 Mart 88", "1988-03-14"),
        ("on dört mart bin dokuz yüz seksen sekiz", "1988-03-14"),
        ("on dördü mart bin dokuz yüz seksen sekiz", "1988-03-14"),
        ("14 Mart seksen sekiz", "1988-03-14"),
        ("mart 14 1988", "1988-03-14"),
        ("1988 mart 14", "1988-03-14"),
        ("14 mart 1988 doğumluyum", "1988-03-14"),
        ("14.03.1988", "1988-03-14"),
        ("14 mart, pardon 15 mart 1988", "1988-03-15"),
        ("bir mart iki bin", "2000-03-01"),
        # a year not whole yet: unreadable for the bank, not a wrong answer
        ("14 mart bin dokuz yüz", "incomplete"),
        ("14 Mart", "incomplete"),
        ("merhaba", "merhaba"),
    ],
)
def test_turkish_dates_of_birth(text, want):
    assert NS["normalize_dob"](text, "DMY", lang="tr") == want


def test_dob_digits_for_the_pieces_check():
    d = NS["dob_digits"]
    assert d("bin dokuz yüz", lang="tr") == "19"  # the year's first half only
    assert d("iki bin", lang="tr") == "2000"
    assert d("seksen sekiz", lang="tr") == "88"
    assert d("nineteen", lang="en") == "19"
    assert d("nineteen eighty-eight", lang="en") == "1988"


@pytest.mark.parametrize("i,month", list(enumerate(langpack.FORMAT["tr"]["months"])))
def test_every_turkish_month(i, month):
    assert NS["normalize_dob"]("14 " + month + " 1988", lang="tr") == f"1988-{i + 1:02d}-14"
    assert NS["parse_spoken_date"]("3 " + month, WED, lang="tr")[0][5:] == f"{i + 1:02d}-03"


# ---- TR-9: case and Unicode
def test_fold():
    fold = NS["fold"]
    assert fold("KRED\u0130 KARTI") == "kredi karti"
    assert fold("I\u015eIK \u0131l\u0131k") == "isik ilik"
    assert fold("\u00c7ar\u015famba A\u011fustos \u00dc\u00e7") == "carsamba agustos uc"
    nfd = "c\u0327ars\u0327amba gu\u0308nu\u0308 I\u0307"  # decomposed: c + cedilla, ...
    assert fold(nfd) == "carsamba gunu i"


def test_decomposed_and_capital_turkish_still_read():
    # today is a Wednesday: 'carsamba' is today, as English 'Wednesday'
    assert date("c\u0327ars\u0327amba gu\u0308nu\u0308") == ("2026-10-14", "date")
    assert date("\u00c7AR\u015eAMBA") == ("2026-10-14", "date")
    assert date("YARIN") == ("2026-10-15", "date")
    assert date("\u0130K\u0130 G\u00dcN SONRA") == ("2026-10-16", "date")


# ---- the language rules reach a code node only in a call of that language
BODY = (
    "vars['d'] = parse_spoken_date(vars['t'], datetime.date(2026, 10, 14))[0]\n"
    "vars['a'] = parse_amount(vars['m'])\n"
)


def test_turkish_rules_in_a_turkish_snippet_only():
    tr = snippet(BODY, dict(lang="tr"))
    en = snippet(BODY, dict(lang="en"))
    assert ("_rules('tr', _tr)" in tr and "def _tr(" in tr) or "b64decode" in tr
    assert "def _tr(" not in en and "_TRK_" not in en
    v = dict(t="haftaya salı", m="1.250,50 TL", now="2026-10-14T11:00")
    exec(tr, dict(vars=v))
    assert (v["d"], v["a"]) == ("2026-10-20", 1250.5)
    v = dict(t="next Tuesday", m="1,250.50", now="2026-10-14T11:00")
    exec(en, dict(vars=v))
    assert (v["d"], v["a"]) == ("2026-10-20", 1250.5)


# ---- FORMAT: what the engine says in a Turkish call
def test_turkish_money_and_date_format():
    f = langpack.fmt("tr")
    d = datetime.date(2026, 10, 23)
    assert NS["say_date"](d, f) == "23 Ekim 2026 Cuma"
    assert NS["money"](1250, f) == "1.250 lira"
    assert NS["money"]("1250.5", f) == "1.250,50 lira"
    assert NS["money"](200, f) == "200 lira"
    assert NS["money"](1250000, f) == "1.250.000 lira"
    # the brief's own currency word wins
    assert NS["money"](1250, dict(f, currency=dict(symbol="TL"))) == "1.250 TL"
    assert NS["money"](1250, dict(f, currency=dict(word="Türk lirası"))) == "1.250 Türk lirası"
    # from LOCALE when no format is passed; the machine form with fmt={}
    NS["set_locale"](dict(lang="tr", fmt=f))
    assert NS["say_date"](d) == "23 Ekim 2026 Cuma"
    assert NS["money"](1250) == "1.250 lira"
    assert NS["money"](1250, dict()) == "1250.00"
    # what money says reads back
    assert NS["to_float"]("1.250 lira") == 1250
    assert NS["to_float"]("1.250,50 lira") == 1250.5


def test_english_format_unchanged():
    d = datetime.date(2026, 10, 23)
    assert NS["say_date"](d) == "Friday 23 October 2026"
    assert NS["money"](1250) == "1250.00"
    assert langpack.fmt("en") == {}
    en = dict(langpack.fmt("en"), currency=dict(code="GBP", symbol="£"))  # SPEC merges it in
    assert NS["say_date"](d, en) == "Friday 23 October 2026"
    assert NS["money"]("1,250.5", en) == "1250.50"


def test_product_amounts_stay_data_in_a_turkish_call():
    NS["set_locale"](dict(lang="tr", fmt=langpack.fmt("tr")))
    cfg = dict(product_names=dict(card="kart"), floor_field=dict(card="minimum_payment"))
    p = dict(type="card", product_id="p1", past_due="1250.5", minimum_payment=200, days_late=10)
    pf = NS["product_facts"]([p], "2026-10-14T11:00", cfg)
    assert (pf["p1_past_due"], pf["p1_floor"]) == ("1250.50", "200.00")
