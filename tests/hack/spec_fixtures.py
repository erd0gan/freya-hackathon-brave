"""Hand-written specs for the public Brief A and Brief B: what the spec extractor must produce.
Below them, a synthetic Turkish case (written for the tests, not any real bank's or the
final's values) in the shape the case reader gives extract(): one policy text with a
'## <file> (<role>)' header per document, plus example conversations apart; tr_spec() is the
spec a correct reading gives (the public Brief A/B extraction with this case's values, Turkish
lines and the new keys). The documents keep their one-line rules (long lines), as case
documents have them. Test data only. The builder never imports this file."""

import copy
import json
from pathlib import Path

from test_engine import CFG, CFG_A

LINES = {
    "opening": "Hello, this is the digital assistant of Freya Bank, and we are recording this call.",
    "who_question": "Am I speaking with {{title}} {{last_name}}?",
    "ai_answer": "I am Freya Bank's digital assistant.",
    "scam_answer": "You can verify this call by phoning Freya Bank's customer service number.",
    "deceased": "I am sorry for your loss.",
    "identity_intro": "Before we continue, I need to ask you two security questions.",
    "identity_why": "These questions protect your account.",
    "identity_failed": "I cannot continue. Please use the mobile app or visit a branch.",
    "no_info": "I do not have that information.",
    "callback_booked": "Thank you, we will call back then.",
    "goodbye": "Thank you for your time. Goodbye.",
}

SPEC_A = {
    "cfg": CFG_A,
    "bank_name": "Freya Bank",
    "lines": LINES,
    "forbidden_before_verify": ["collections", "debt", "payment", "credit card", "loan"],
    "identity": {
        "questions": [
            {"field": "date_of_birth", "ask": "What is your date of birth?", "kind": "date"},
            {
                "field": "phone_last4",
                "ask": "What are the last four digits of the phone number we have on file?",
                "kind": "digits",
            },
        ],
        "swap_hint": "day_month_swapped",
        "swap_instruction": "Ask the customer to say the month of their date of birth as a word.",
    },
    "outcomes": {
        "unclear": ["not_reached", "unclear"],
        "callback": ["not_reached", "callback"],
        "no_callback": ["not_reached", "no_callback"],
        "wrong_number": ["not_reached", "wrong_number"],
        "deceased": ["not_reached", "deceased"],
        "failed": ["not_verified", "failed"],
        "declined": ["not_verified", "declined"],
    },
    "callback_reasks": 1,
}

SPEC_B = copy.deepcopy(SPEC_A)
SPEC_B["cfg"] = CFG


# ---------------------------------------------------------------- a Turkish case
HERE = Path(__file__).parent
SPEC_EN = json.loads((HERE / "fixtures" / "spec_ab_extracted.json").read_text())

TR_DOCS = """## analiz.md (policy)

# Kart borcu arama politikası (test metni)

1. Arama şu cümleyle başlar: "Merhaba, Örnek Bank dijital asistanıyım, bu görüşme kayıt altına alınmaktadır." Kimlik doğrulanmadan borç, tutar veya ürün anılmaz.
2. Kimlik: önce doğum tarihi, sonra telefon numarasının son dört hanesi sorulur. Toplam üç yanlış cevap kimlik kontrolünü bitirir.
3. Geri arama: bugün ya da bir sonraki iş günü, saat 09.00 ile 18.00 arasında (ikisi dahil).
4. İş günleri pazartesiden cumaya kadardır. Resmi tatiller: 29 Ekim 2026, 1 Ocak 2027 ve 23 Nisan 2027.

| Gecikme günü | Bant | Son tarih | İkna denemesi |
|---|---|---|---|
| 1-7 | erken | bugünden sonraki 12. iş günü | 1 |
| 8-45 | geç | bugünden sonraki 7. iş günü | 2 |

5. Asgari ödeme tutarı ekstre borcunun %20'sidir. Tutarlar TL olarak söylenir (Türk lirası ve kuruş).
6. Kapanış kodları: `promise`, `paid`, `transferred`, `no_promise`, `not_verified`, `not_reached`. Neden kodları: `refused`, `hardship`, `dispute`, `service_error`, `unclear`, `callback`, `no_callback`, `wrong_number`, `deceased`, `failed`, `declined`, `support`, `restructuring`, `human`.

## duyuru-2026-41.docx (announcement)

Duyuru 2026/41, yürürlük tarihi 19 Ekim 2026. Bu tarihten itibaren geri arama bitişi 17.30 olur ve geç bantta son tarih beşinci iş günüdür. Kurban Bayramı nedeniyle 16-19 Mayıs 2027 tatildir.

## taslak-duyuru.md (announcement)

TASLAK, ONAYLANMADI: geri arama bitişi 20.00 ve geç bantta son tarih on beş iş günü önerilmektedir.

## sss.md (questions and answers)

S: Müşteri "borcum yok" derse? C: İtiraz kaydı açılır (`dispute`), ikna yapılmaz.

## test-musteriler.xlsx (test customers)

| müşteri | doğum tarihi | gecikme | borç |
|---|---|---|---|
| Ayşe Yılmaz | 14 Mart 1988 | 9 | 1.250,00 TL |
| Mehmet Kaya | 02.11.1975 | 33 | 3.400,50 TL |
"""

TR_EXAMPLES = """Asistan: Merhaba, Örnek Bank dijital asistanıyım, bu görüşme kayıt altına alınmaktadır.
Müşteri: Evet, benim.
Asistan: Borcunuzu en geç 11 Kasım 2026'ya kadar, dört deneme içinde ödeyebilir misiniz?
"""

TR_LINES = {
    "opening": "Merhaba, Örnek Bank dijital asistanıyım, bu görüşme kayıt altına alınmaktadır.",
    "who_question": "{{title}} {{last_name}} ile mi görüşüyorum?",
    "ai_answer": "Ben Örnek Bank'ın dijital asistanıyım.",
    "scam_answer": "Bu aramayı Örnek Bank müşteri hizmetlerini arayarak doğrulayabilirsiniz.",
    "deceased": "Başınız sağ olsun.",
    "identity_intro": "Devam etmeden önce iki güvenlik sorusu sormam gerekiyor.",
    "identity_why": "Bu sorular hesabınızı korumak içindir.",
    "identity_failed": "Kimliğinizi doğrulayamadım. Lütfen şubemize başvurun.",
    "present_intro": "Gecikmiş bir ödeme hakkında arıyorum.",
    "today_question": "Bu ödemeyi bugün yapabilir misiniz?",
    "multi_today_question": "Hepsini bugün ödeyebilir misiniz?",
    "confirm_question": "Onaylıyor musunuz?",
    "recorded": "Teşekkür ederim.",
    "paid": "Bilgi verdiğiniz için teşekkür ederim.",
    "sympathy": "Bunu duyduğuma çok üzüldüm.",
    "support_offer": "Sizi yardımcı olabilecek bir arkadaşıma bağlamamı ister misiniz?",
    "help_first": "Sizi aktarmadan önce ben yardımcı olabilir miyim?",
    "callback_booked": "Teşekkürler, geri arama kaydedildi.",
    "goodbye": "Zaman ayırdığınız için teşekkür ederim. İyi günler.",
    "no_info": "Maalesef bu bilgiye sahip değilim.",
    "rest_overdue": "Kalan tutar gecikmede kalır.",
}

HOLIDAYS = ["2026-10-29", "2027-01-01", "2027-04-23"]
EID = ["2027-05-16", "2027-05-17", "2027-05-18", "2027-05-19"]


def tr_spec():
    s = copy.deepcopy(SPEC_EN)
    s["bank_name"] = "Örnek Bank"
    s["language"] = "tr"
    s["notes"] = ["Duyuru 2026/41 is dated: its values are in dated_changes"]
    s["lines"] = dict(TR_LINES)
    s["forbidden_before_verify"] = ["borç", "tutar", "ödeme", "kart"]
    s["identity"]["questions"] = [
        {"field": "date_of_birth", "ask": "Doğum tarihinizi söyler misiniz?", "kind": "date"},
        {
            "field": "phone_last4",
            "ask": "Telefon numaranızın son dört hanesi nedir?",
            "kind": "digits",
        },
    ]
    s["identity"]["swap_instruction"] = "Doğum tarihinizi ayı yazıyla söyler misiniz?"
    s["identity"]["max_wrong_answers"] = 3
    s["callback"].update(start="09:00", end="18:00")
    s["calendar"] = {"holidays": list(HOLIDAYS), "workdays": [0, 1, 2, 3, 4], "week_start": None}
    s["bands"][0].update(min_days_late=1, max_days_late=7, deadline_days=12, attempts=1)
    s["bands"][1].update(min_days_late=8, max_days_late=45, deadline_days=7, attempts=2)
    s["product_names"] = {
        "credit_card": "{card_last4} ile biten kredi kartı",
        "personal_loan": "ihtiyaç kredisi",
        "car_loan": "taşıt kredisi",
    }
    s["currency"] = {"code": "TRY", "symbol": "TL", "word": "lira", "minor_word": "kuruş"}
    s["floor_rule"] = {"percent_of": "past_due", "percent": 20}
    s["dated_changes"] = [
        {
            "effective_from": "2026-10-19",
            "path": "callback.end",
            "value": "17:30",
            "source": "Bu tarihten itibaren geri arama bitişi 17.30 olur",
        },
        {
            "effective_from": "2026-10-19",
            "path": "bands.1.deadline_days",
            "value": 5,
            "source": "geç bantta son tarih beşinci iş günüdür",
        },
        {
            "effective_from": "2026-10-19",
            "path": "calendar.holidays",
            "value": HOLIDAYS + EID,
            "source": "Kurban Bayramı nedeniyle 16-19 Mayıs 2027 tatildir",
        },
    ]
    return s


# The same kind of case written without Turkish letters (as plain-ASCII exports often are),
# with its own numbering and a code list of its own words.
ASCII_DOCS = """# Kredi karti ekstre borcu hatti (test metni)

## Belge 1 - Analiz dokumani

1. Arama, "Bu gorusme kayit altina alinmaktadir." cumlesiyle baslar. Kimlik dogrulanmadan borc, tutar veya urun anilmaz.
2. Kimlik: once dogum tarihi, sonra telefon numarasinin son dort hanesi. Iki yanlis cevap toplam hak.
3. Gecikme 1-10 gun: son odeme sozu tarihi en gec on is gunu sonradir, bir ikna denemesi. Gecikme 11-90 gun: yedi is gunu, uc ikna denemesi.
4. Geri arama: bugun veya sonraki is gunu, 09.00 - 18.00 arasi (ikisi dahil).
5. Resmi tatiller: 29 Ekim 2026, 1 Ocak 2027, 23 Nisan 2027.

## Belge 2 - Duyuru (yururluk: 19 Ekim 2026)

Yururluk tarihinden itibaren geri arama bitisi 17.30 olur; 11-90 gun bandinda son tarih bes is gunudur ve iki deneme yapilir.

## Belge 3 - Taslak duyuru (ONAYLANMADI, gecersiz)

Onerilen geri arama bitisi 20.00; onerilen son tarih on iki is gunu.

## Kod listesi

| Sonuc | Kod | Neden kodlari |
|---|---|---|
| soz alindi | SOZ | - |
| aktarildi | AKTARIM | INSAN, YAPILANDIRMA, DESTEK |
| soz yok | SOZ_YOK | RET, ZORLUK, ITIRAZ, SISTEM |
"""


def ascii_spec():
    """The policy part of a correct reading of ASCII_DOCS (what grounding() reads)."""
    return {
        "language": "tr",
        "identity": {"questions": [{"field": "dob"}], "max_wrong_answers": 2},
        "callback": {"start": "09:00", "end": "18:00"},
        "calendar": {"holidays": ["2026-10-29", "2027-01-01", "2027-04-23"]},
        "bands": [
            {
                "name": "a",
                "min_days_late": 1,
                "max_days_late": 10,
                "deadline_days": 10,
                "attempts": 1,
            },
            {
                "name": "b",
                "min_days_late": 11,
                "max_days_late": 90,
                "deadline_days": 7,
                "attempts": 3,
            },
        ],
        "neg": {
            "outcomes": {"promise": "SOZ", "transferred": "AKTARIM", "no_promise": "SOZ_YOK"},
            "reasons": {
                "refused": "RET",
                "hardship": "ZORLUK",
                "dispute": "ITIRAZ",
                "service_error": "SISTEM",
            },
            "transfer_reasons": {
                "human": "INSAN",
                "restructuring": "YAPILANDIRMA",
                "support": "DESTEK",
            },
        },
        "dated_changes": [
            {
                "effective_from": "2026-10-19",
                "path": "callback.end",
                "value": "17:30",
                "source": "geri arama bitisi 17.30 olur",
            },
            {
                "effective_from": "2026-10-19",
                "path": "bands.1.deadline_days",
                "value": 5,
                "source": "11-90 gun bandinda son tarih bes is gunudur",
            },
            {
                "effective_from": "2026-10-19",
                "path": "bands.1.attempts",
                "value": 2,
                "source": "iki deneme yapilir",
            },
        ],
    }
