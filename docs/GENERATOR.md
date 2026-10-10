# Workflow generator tasarımı (10 Ekim 2026, 16:00 UTC)

Ana plan `HACKATHON_REALIGN.md`. Bu belge, §3'teki 4. adımın (GENERATE) iç tasarımını anlatır.

## İlke: kayda giden her değer koddan gelir

Ledger'a yazılan her alan code node'lardan ve baked parametrelerden gelir. Bunlar: promise tutarı, tarihi ve ürünü, callback tarihi ve saati, not nedeni, transfer kuyruğu, outcome ve reason. Model bu değerleri tool argümanı olarak doldurmaz.

Modelin işi üç şeydir:
1. Konuşmak.
2. Extraction ile müşterinin ne dediğini sınıflamak ve söylediği tarih ve tutarı yakalamak.
3. `verify_identity` çağırmak. Bu tek istisna; cevabı müşteri verir. Pre-hook tarihi normalize eder.

Sebep: `experimental-5` zayıf bir model. Aynı vaka üç tekrarın üçünde de geçmeli.

## Graf (Brief A şekli; B, ürün döngüsü ekler)

```
setup(code: takvim, CFG)
  └─ opening(conv: "Am I speaking with {{title}} {{last_name}}?")
       ├─ confirmed ──────────────► identity
       ├─ other_person ───────────► third_party ─► cb_check(code) ─► cb_book(api) ─► fin
       ├─ wrong_number / deceased ► fin
       └─ 2 tur netleşmedi ───────► fin(unclear)
identity(conv, tool: verify_identity + pre/post hook)
  ├─ all_verified ─► account(api: get_account) ─► facts(code: product_facts) ─► present
  ├─ wrong_answers_left == 0 ─► fin(failed)
  └─ 1. ret ─► identity_again ─ 2. ret ─► fin(declined)
present / negotiate(conv) ⇄ decide(code: müzakere motoru)
  ├─ offer hazır ─► confirm(conv: read-back) ─ evet ─► record(api: create_promise, baked)
  │                                                   └─ ok / 2× fail ─► decide
  ├─ hardship ─► support teklifi ─► transfer(api) / fin
  ├─ restructuring / human ─► transfer(api) ─► fin
  └─ paid / dispute / refused ─► (B: note(api)) ─► decide ─► fin
fin_<durum>(code: final_outcome / final_reason) ─► end(end_call, sabit veda)
lifecycle close_call: outcome={{final_outcome}}, reason={{final_reason}}
  pre-hook: boş ya da gereksiz reason'ı düşürür
```

## Müzakere motoru (`collections_lib`, saf Python)

- **Girdi:**
  - ürün durumu: tutar, floor, band, deadline, kullanılan deneme sayısı, verilmiş teklifler;
  - müşterinin son cevabı: extraction ile yakalanan `reply` enum'u, `said_amount`, `said_date`;
  - spec: deneme stratejileri, belirsiz tarih kuralı, floor kuralı.
- **Çıktı:**
  - `next_say`: modelin söyleyeceği tek talimat;
  - `pending_amount` / `pending_date`;
  - `stage` (`ask_today` | `offer` | `confirm` | `done_promise` | `done_refused` | …);
  - `close_intent`.
- **Test:** Yerel olarak public vakaların senaryolarına karşı test edilir. LLM gerekmez; senaryo, `reply` dizisi olarak yazılır.

## Spec'ten gelenler (hiçbiri koda gömülmez)

- kimlik soruları ve sırası, openapi'deki `field` enum'u
- bandlar, deadline iş günü sayısı, deneme sayıları, floor alanları, tatiller
- callback penceresi
- deneme stratejileri (A: deneme 1 = bu haftanın son iş günü ve tam tutar; deneme 2 = deadline ve floor)
- durum tablosu. Her durum için tetikleyici açıklaması, faz (kimlikten önce / sonra / her zaman), aksiyon (transfer kuyruğu / not nedeni / callback / yok), outcome ve reason.
- birebir cümleler (açılış, kayıt cümlesi, taziye, scam cevabı)
- çok ürün kuralları (B): sıra, not kuralı, kapanış önceliği

## Platform varsayımları: Freya'da doğrulanacak (gate 4 ve sonrası)

1. `max_node_turns` = N iken N. kullanıcı turunda transition'lar cap'ten önce mi çalışıyor?
2. Extraction değişkeni, sonraki turda değer çıkmazsa eski değerini koruyor mu? Plan: her node kendi değişken adını kullanır.
3. Code node'un yazdığı `final_outcome`, lifecycle `close_call`'a bake ediliyor mu?
4. Code değişkeni, api_request body default'una (`{{pending_amount}}`) bake ediliyor mu? Sayı tipi `{{x:json}}` ile mi gitmeli?
5. Bir api_request node'unun 500 cevabında `<node>_failure_count` artıyor mu? Bu, `create_promise` tekrar mantığı için gerekli.
