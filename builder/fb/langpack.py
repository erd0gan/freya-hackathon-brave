"""Language packs, read at build time only.

The spec says which language the call is in (`spec["language"]`, a short code such as "en" or
"tr"). Everything the agent says that the brief does not word itself comes from here, and the
date and money formats the code nodes speak come from here too (they reach the code nodes as
CFG["lang"] and CFG["fmt"]). Parsing what the customer says stays in the code-node libraries
(speech_lib, collections_lib): it needs code, not tables.

A language without a pack falls back to English lines (see `lines_for`) and the system prompt
still tells the model which language to speak. Nothing here is a bank's policy: these are the
neutral sentences used where the brief gives no words.

Sections, each owned by one part of the builder:
  LANGUAGE_NAMES  code -> the language's English name, for "Speak <name>." in prompts
  FORMAT          date and money formats the code nodes speak (collections_lib.say_date/money)
  ENGINE_LINES    every neg.lines key negotiation_lib / bodies read, per language
  GEN_LINES       fixed sentences of the generated workflow (generate.py), per language
"""

from __future__ import annotations

from typing import Any

DEFAULT_LANGUAGE = "en"

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "tr": "Turkish",
    # no pack (English lines, "Speak <name>."): the name still has to be a word, not "de"
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "it": "Italian",
    "nl": "Dutch",
    "pt": "Portuguese",
    "pl": "Polish",
    "ru": "Russian",
    "uk": "Ukrainian",
    "ar": "Arabic",
    "el": "Greek",
    "ro": "Romanian",
    "sv": "Swedish",
    "da": "Danish",
    "no": "Norwegian",
    "fi": "Finnish",
    "cs": "Czech",
    "hu": "Hungarian",
    "bg": "Bulgarian",
    "az": "Azerbaijani",
    "fa": "Persian",
    "he": "Hebrew",
    "hi": "Hindi",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
}

# ---- FORMAT (owned by the speech / calendar side: collections_lib reads CFG["fmt"])
# Keys collections_lib.say_date / money read; a key a language lacks is English. English is {}:
# "Friday 23 October 2026" and "1250.00" exactly as before.
#   weekdays, months   names, Monday first / January first
#   date               pattern with {weekday} {day} {month} {year}
#   thousands, decimal the marks in a spoken amount
#   minor_if_any       no ",00" on a whole amount
#   say_currency       the currency word follows the amount: fmt["currency"] (the spec's
#                      {"word", "symbol", "code"}: word, else symbol, else code), else
#                      currency_word
# Turkish money is spoken as "1.250 lira" / "1.250,50 lira": the written Turkish marks (TTS
# reads "1.250" as bin iki yüz elli; "1250.00" would be read as 1,25 thousand or with "nokta"),
# no ",00" (read out as "virgül sıfır sıfır"), and "lira", which TTS speaks as a word, where an
# abbreviation like "TL" can come out letter by letter. A brief's own currency word wins.
FORMAT: dict[str, dict[str, Any]] = {
    "en": {},
    "tr": {
        "weekdays": ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"],
        "months": [
            "Ocak",
            "Şubat",
            "Mart",
            "Nisan",
            "Mayıs",
            "Haziran",
            "Temmuz",
            "Ağustos",
            "Eylül",
            "Ekim",
            "Kasım",
            "Aralık",
        ],
        "date": "{day} {month} {year} {weekday}",
        "thousands": ".",
        "decimal": ",",
        "minor_if_any": True,
        "say_currency": True,
        "currency_word": "lira",
    },
}

# ---- ENGINE_LINES (owned by the engine side: negotiation_lib._line / bodies.py keys)
# Every sentence the call-time engine speaks. The spec's neg.lines takes the brief's words for a
# key, else these. Placeholders ({amount}, {date}, {product}, ...) are filled by
# negotiation_lib._line with money()/say_date() already in the call's language; every language
# has the same keys and the same placeholders per key (tests/hack/test_engine_tr.py).
# English: the first block are the neutral sentences (they promise nothing a brief did not say);
# the rest are byte for byte the engine's own defaults, so an English call is unchanged.
ENGINE_LINES: dict[str, dict[str, str]] = {
    "en": {
        "no_account": "I am sorry, I cannot see your account details right now.",
        "present_intro": "I am calling about a late payment.",
        "today_question": "Can you make this payment today?",
        "multi_today_question": "Can you pay all of them today?",
        "confirm_question": "Do you confirm?",
        "anything_else": "Is there anything else I can help you with?",
        "support_reask": "Would you like that?",
        "rest_overdue": "The rest stays overdue.",
        "vague_ask": "On which exact day will you pay?",
        "offer_day_question": "Can you do that, or is another day better for you?",
        "help_first": "Before I transfer you, can I help you with this myself?",
        "support_offer": "Would you like me to connect you with a colleague who can help?",
        "sympathy": "I am sorry to hear that.",
        "paid": "Thank you for letting me know.",
        "dispute_noted": "I have noted that.",
        "which_change": "Which promise would you like to change?",
        "one_by_one": "Let us go through them one at a time.",
        "recorded": "Thank you.",
        "cannot_change": "A recorded promise cannot be changed in this call.",
        "transfer_human": "I am connecting you with a colleague now.",
        "transfer_restructuring": "I am connecting you with a colleague now.",
        "transfer_support": "I am connecting you with a colleague now.",
        "transfer_failed": "I am sorry, I could not connect you.",
        "goodbye": "Thank you for your time. Goodbye.",
        # the engine's built-in sentences
        "present_product": "Your {product} has {amount} past due and is {days} days late.",
        "present_product_nodays": "Your {product} has {amount} past due.",
        "product_default": "account",
        "sorry_1": "Sorry, I did not catch that.",
        "sorry_2": "Sorry, I missed the end of that.",
        "sorry_3": "Take your time.",
        "cut_sorry": "Sorry, I think you were cut off.",
        "go_on": "Please go on.",
        "say_again": "Sorry, could you say that again?",
        "understand": "I understand.",
        "offer_question": "Can you do that?",
        "attempt_offer": "Could you pay {amount} for {product} on {date}?",
        "pay_on": "Could you pay {amount} on {date}?",
        "floor_for": "The minimum for {product} is {amount}.",
        "below_floor": "I cannot record {amount}. The minimum is {floor}.",
        "latest_date": "The latest date I can record is {date}.",
        "not_business_day": "{date} is not a business day.",
        "deadline_offer": "The latest date I can record is {date}. Could you pay {amount} on "
        "that day?",
        "deadline_offer_product": "The latest date I can record for {product} is {date}. Could "
        "you pay {amount} on that day?",
        "next_business_day": "{date} is not a business day. Could you pay {amount} on the next "
        "business day, {next_date}?",
        "next_business_day_product": "{date} is not a business day. Could you pay {amount} for "
        "{product} on the next business day, {next_date}?",
        "next_business_day_holiday": "{date} is a holiday. Could you pay {amount} on the next "
        "business day, {next_date}?",
        "day_ask_amount": "On which day can you pay {amount}?",
        "exact_day_ask": "On which exact day can you pay?",
        "date_past": "The date has to be today or later. On which day can you pay?",
        "amount_ask": "Sorry, I did not catch the amount. Which amount do you mean?",
        "allocation_ask": "How would you like to split {amount} between {products}?",
        "readback": "To confirm: {items}.",
        "readback_item": "{product}, {amount} on {date}",
        "readback_item_deadline": "{product}, {amount} on {date}, which is the latest date I can "
        "record for it",
        "readback_item_moved": "{product}, {amount} on {date}, the next business day",
        "ack": "I have noted {items}.",
        "ack_item": "{product} for {amount} on {date}",
        "promise_recorded": "Your promise is recorded: {items}.",
        "record_failed": "I could not record the promise for {products}.",
        "service_error": "I am sorry, we have a technical problem.",
    },
    # Turkish: polite (siz) and plain. Names, dates and amounts go before postpositions ("için",
    # "tarihinde"), so no case suffix has to be added to a value.
    "tr": {
        "no_account": "Özür dilerim, şu anda hesap bilgilerinizi göremiyorum.",
        "present_intro": "Gecikmiş bir ödemeniz hakkında arıyorum.",
        "today_question": "Bu ödemeyi bugün yapabilir misiniz?",
        "multi_today_question": "Bunların hepsini bugün ödeyebilir misiniz?",
        "confirm_question": "Onaylıyor musunuz?",
        "anything_else": "Size yardımcı olabileceğim başka bir konu var mı?",
        "support_reask": "Bunu ister misiniz?",
        "rest_overdue": "Kalan tutar gecikmiş olarak kalır.",
        "vague_ask": "Tam olarak hangi gün ödeyeceksiniz?",
        "offer_day_question": "Bunu yapabilir misiniz, yoksa sizin için başka bir gün mü daha "
        "uygun?",
        "help_first": "Sizi aktarmadan önce, bu konuda size ben yardımcı olabilir miyim?",
        "support_offer": "Size yardımcı olabilecek bir arkadaşıma bağlamamı ister misiniz?",
        "sympathy": "Bunu duyduğuma üzüldüm.",
        "paid": "Bilgi verdiğiniz için teşekkür ederim.",
        "dispute_noted": "Bunu not aldım.",
        "which_change": "Hangi ödeme sözünü değiştirmek istersiniz?",
        "one_by_one": "Bunları tek tek ele alalım.",
        "recorded": "Teşekkür ederim.",
        "cannot_change": "Kaydedilmiş bir ödeme sözü bu görüşmede değiştirilemez.",
        "transfer_human": "Sizi şimdi bir arkadaşıma bağlıyorum.",
        "transfer_restructuring": "Sizi şimdi bir arkadaşıma bağlıyorum.",
        "transfer_support": "Sizi şimdi bir arkadaşıma bağlıyorum.",
        "transfer_failed": "Özür dilerim, sizi bağlayamadım.",
        "goodbye": "Zaman ayırdığınız için teşekkür ederim. İyi günler.",
        "present_product": "{product} için {amount} tutarında gecikmiş ödemeniz bulunuyor ve "
        "ödemeniz {days} gün gecikmiş durumda.",
        "present_product_nodays": "{product} için {amount} tutarında gecikmiş ödemeniz bulunuyor.",
        "product_default": "hesabınız",
        "sorry_1": "Özür dilerim, tam anlayamadım.",
        "sorry_2": "Özür dilerim, sonunu kaçırdım.",
        "sorry_3": "Acele etmeyin.",
        "cut_sorry": "Özür dilerim, sanırım sözünüz kesildi.",
        "go_on": "Lütfen devam edin.",
        "say_again": "Özür dilerim, tekrar eder misiniz?",
        "understand": "Anlıyorum.",
        "offer_question": "Bunu yapabilir misiniz?",
        "attempt_offer": "{product} için {date} tarihinde {amount} ödeyebilir misiniz?",
        "pay_on": "{date} tarihinde {amount} ödeyebilir misiniz?",
        "floor_for": "{product} için asgari tutar {amount}.",
        "below_floor": "{amount} tutarını kaydedemiyorum. Asgari tutar {floor}.",
        "latest_date": "Kaydedebileceğim en geç tarih {date}.",
        "not_business_day": "{date} bir iş günü değil.",
        "deadline_offer": "Kaydedebileceğim en geç tarih {date}. O gün {amount} ödeyebilir "
        "misiniz?",
        "deadline_offer_product": "{product} için kaydedebileceğim en geç tarih {date}. O gün "
        "{amount} ödeyebilir misiniz?",
        "next_business_day": "{date} bir iş günü değil. Bir sonraki iş günü olan {next_date} "
        "tarihinde {amount} ödeyebilir misiniz?",
        "next_business_day_product": "{date} bir iş günü değil. {product} için bir sonraki iş "
        "günü olan {next_date} tarihinde {amount} ödeyebilir misiniz?",
        "next_business_day_holiday": "{date} resmi tatil. Bir sonraki iş günü olan {next_date} "
        "tarihinde {amount} ödeyebilir misiniz?",
        "day_ask_amount": "{amount} tutarını hangi gün ödeyebilirsiniz?",
        "exact_day_ask": "Tam olarak hangi gün ödeyebilirsiniz?",
        "date_past": "Tarih bugün ya da daha sonrası olmalı. Hangi gün ödeyebilirsiniz?",
        "amount_ask": "Özür dilerim, tutarı anlayamadım. Hangi tutarı kastediyorsunuz?",
        "allocation_ask": "{amount} tutarını {products} arasında nasıl bölmek istersiniz?",
        "readback": "Teyit ediyorum: {items}.",
        "readback_item": "{product} için {date} tarihinde {amount}",
        "readback_item_deadline": "{product} için {date} tarihinde {amount}; bu, kaydedebileceğim "
        "en geç tarih",
        "readback_item_moved": "{product} için bir sonraki iş günü olan {date} tarihinde {amount}",
        "ack": "Not aldım: {items}.",
        "ack_item": "{product} için {date} tarihinde {amount}",
        "promise_recorded": "Ödeme sözünüz kaydedildi: {items}.",
        "record_failed": "{products} için ödeme sözünü kaydedemedim.",
        "service_error": "Özür dilerim, teknik bir sorun yaşıyoruz.",
    },
}

# ---- GEN_LINES (owned by the generator side: fixed sentences in generate.py)
# generate.py takes the brief's line first (spec lines / neg.lines) and these only where the
# brief gives none. Placeholders: {bank} (the bank's name), {who} (how the customer is
# addressed, "{{title}} {{last_name}}"). Keys starting with ex_ are examples inside model
# instructions and extraction descriptions: what a customer of that language would say.
# English is the exact text the generator used before the packs existed.
GEN_LINES: dict[str, dict[str, str]] = {
    "en": {
        # said to the customer (tts, closing lines, quoted lines in prompts)
        "opening": "Hello, this is the digital assistant of {bank}.",
        "who_question": "Am I speaking with {who}?",
        "ai_answer": "I am {bank}'s digital assistant.",
        "no_info": "I do not have that information.",
        "goodbye": "Thank you for your time. Goodbye.",
        "bye": "Goodbye.",
        "wrong_number": "Sorry to have bothered you. Goodbye.",
        "deceased": "I am sorry for your loss.",
        "identity_intro": "Before we continue, I need to ask you some security questions.",
        "identity_why": "I need to confirm your identity before I can continue.",
        "identity_failed": "I cannot continue the call now.",
        "callback_booked": "Thank you, we will call back then.",
        "help_first": "Before I transfer you, can I help you with this myself?",
        "transfer_human": "I am connecting you with a colleague now.",
        "service_error": "I am sorry, we have a technical problem.",
        "cut_off": "Sorry, I think you were cut off.",
        "take_time": "Take your time.",
        "ask_year": "And the year?",
        "ask_rest_digits": "And the remaining digits?",
        # examples in instructions and extraction descriptions
        "ex_date_words": "twenty one July nineteen ninety",
        "ex_confirms": "yes, that's me, speaking",
        "ex_who_unclear": "hello, go ahead, who is this",
        "ex_refuse": "I won't give you that",
        "ex_not_customer": "'this is his wife', 'he is not here'",
        "ex_answer_and_ask": "No, I don't want that. Can I get a discount?",
        "ex_vague": "next week, end of the month, when the salary comes",
        "ex_evasive": "'I don't know', 'we will see', 'maybe later'",
        "ex_pay_date": "'this Friday', 'the 30th of October', 'tomorrow'",
        "ex_pay_amount": "'637.50', 'four hundred and twenty'",
        "ex_amount_digits": "'four hundred and twenty' -> '420', '1,250.50' -> '1250.50'",
        "ex_pieces": "'ninety-nine' and then 'zero-four' -> 'ninety-nine zero-four'",
        "ex_cb_day": "'tomorrow', 'Friday'",
        "ex_cb_time": "'around eleven', '3 pm'",
    },
    "tr": {
        "opening": "Merhaba, {bank} adına arayan dijital asistanım.",
        "who_question": "{who} ile mi görüşüyorum?",
        "ai_answer": "Ben {bank} adına arayan dijital asistanım.",
        "no_info": "Bu konuda bilgim yok.",
        "goodbye": "Zaman ayırdığınız için teşekkür ederim. İyi günler.",
        "bye": "İyi günler.",
        "wrong_number": "Rahatsız ettiğim için özür dilerim. İyi günler.",
        "deceased": "Başınız sağ olsun.",
        "identity_intro": "Devam etmeden önce size birkaç güvenlik sorusu sormam gerekiyor.",
        "identity_why": "Devam edebilmem için kimliğinizi doğrulamam gerekiyor.",
        "identity_failed": "Görüşmeye şu anda devam edemiyorum.",
        "callback_booked": "Teşekkür ederim, sizi o zaman tekrar arayacağız.",
        "help_first": "Sizi aktarmadan önce bu konuda ben yardımcı olabilir miyim?",
        "transfer_human": "Sizi şimdi bir müşteri temsilcisine aktarıyorum.",
        "service_error": "Özür dilerim, teknik bir sorun yaşıyoruz.",
        "cut_off": "Kusura bakmayın, sanırım sesiniz kesildi.",
        "take_time": "Acele etmeyin.",
        "ask_year": "Yılı da söyler misiniz?",
        "ask_rest_digits": "Kalan rakamları da söyler misiniz?",
        "ex_date_words": "yirmi bir Temmuz bin dokuz yüz doksan",
        "ex_confirms": "evet, benim, evet o benim",
        "ex_who_unclear": "alo, buyurun, kim arıyor",
        "ex_refuse": "Bunu size söylemeyeceğim",
        "ex_not_customer": "'ben eşiyim', 'kendisi evde değil'",
        "ex_answer_and_ask": "Hayır, bunu istemiyorum. İndirim yapabilir misiniz?",
        "ex_vague": "haftaya, ay sonunda, maaşım yatınca",
        "ex_evasive": "'bilmiyorum', 'bakarız', 'belki sonra'",
        "ex_pay_date": "'bu cuma', 'ayın otuzunda', 'yarın'",
        "ex_pay_amount": "'637,50', 'dört yüz yirmi'",
        "ex_amount_digits": "'dört yüz yirmi' -> '420', '1.250,50' -> '1250.50'",
        "ex_pieces": "'doksan dokuz' ve sonra 'sıfır dört' -> 'doksan dokuz sıfır dört'",
        "ex_cb_day": "'yarın', 'cuma'",
        "ex_cb_time": "'on bir gibi', 'öğleden sonra üç'",
    },
}


def norm_language(x: Any) -> str:
    """'tr', 'TR', 'tr-TR', 'Turkish', 'Türkçe' -> 'tr'; anything unreadable -> 'en'."""
    s = str(x or "").strip().lower().replace("_", "-")
    if not s:
        return DEFAULT_LANGUAGE
    for code, name in LANGUAGE_NAMES.items():
        if s == code or s.startswith(code + "-") or s == name.lower():
            return code
    if s.startswith(("türk", "turk")):
        return "tr"
    if s.startswith("engl"):
        return "en"
    if len(s) >= 2 and s[:2].isalpha() and (len(s) == 2 or s[2] == "-"):
        return s[:2]
    return DEFAULT_LANGUAGE


def language_name(code: str) -> str:
    code = norm_language(code)
    return LANGUAGE_NAMES.get(code, code)


def _pack(table: dict[str, dict[str, Any]], code: str) -> dict[str, Any]:
    code = norm_language(code)
    out = dict(table.get(DEFAULT_LANGUAGE) or {})
    if code != DEFAULT_LANGUAGE:
        out.update({k: v for k, v in (table.get(code) or {}).items() if v not in (None, "")})
    return out


def engine_lines(code: str) -> dict[str, str]:
    return _pack(ENGINE_LINES, code)


def gen_lines(code: str) -> dict[str, str]:
    return _pack(GEN_LINES, code)


def fmt(code: str) -> dict[str, Any]:
    return _pack(FORMAT, code)


def has_pack(code: str) -> bool:
    return norm_language(code) in ENGINE_LINES and bool(ENGINE_LINES[norm_language(code)])
