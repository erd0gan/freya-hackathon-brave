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

## Platform kapıları: sonuçlar (10 Ekim 2026, 16:30 UTC, `builder/tools/gate_probe.py`)

Test: küçük bir probe workflow, PUB-10 practice müşterisi (ilk `create_promise` 500 döner), `/chat/message` ile 5 tur. Kanıt: call event'leri (`node_enter`, `route_evaluated`, `function_call_start`) ve bankanın ledger'ı.

| # | Soru | Sonuç |
|---|---|---|
| G6 | Tam node-code kütüphanesini (52.780 karakter) taşıyan code node kabul ediliyor ve çalışıyor mu? | **Evet.** Dry-run geçti, kod platformda kesilmeden saklandı, `code_error` yok, takvim değerleri doğru (`attempt1_date = 2026-10-16`). Workflow JSON'u 64,5 KB. |
| G0 | Chat route, `initial_node` olan passthrough code node'u çalıştırıyor mu? | **Hayır.** Code node baştayken hiçbir değişkeni yazılmadı: `create_promise` gövdesi boş gitti, `close_call`'ı model doldurdu (`outcome: "success"` → bank `unknown outcome`). Aynı code node ilk konuşma node'undan sonra konunca çalıştı. **Karar:** initial node her zaman açılış konuşma node'u (tts); setup code node ondan sonra. Simülasyonda nasıl davrandığına bakmaya gerek kalmıyor. |
| G2 | Bir sonraki turda değer çıkmazsa extraction değişkeni eski değerini koruyor mu? | **Evet.** `color = red`, şemasında `color` da olan sonraki node'da iki tur boyunca korundu. |
| G1 | `max_node_turns = N` iken N. turda eşleşen transition mı, cap mi kazanıyor? | **Transition.** `max_node_turns: 2` olan node'da 2. turda `animal == cat` transition'ı çalıştı, `max_turn_transition` çalışmadı. |
| G4 | Code değişkeni `api_request` gövdesine bake ediliyor mu? Sayı tipi? | **Evet, düz `{{rec_amount}}` ile.** Gövde: `{"amount": 365.0, "date": "2026-10-16", "product_id": "p1", ...}`. `amount` JSON number olarak gitti; `:json` gerekmedi. |
| G5 | 500 cevabında `<node>_failure_count` artıyor mu? Node'a yeniden girince `pre_actions` tekrar çalışıyor mu? | **İkisi de evet.** `record_failure_count = 1`, transition aynı turda çalıştı; code node üzerinden `record`'a dönünce çağrı tekrarlandı. 500'den sonra `record_status_code` güvenilir değil (sonraki başarılı çağrı 200 yazar); sayaca bakılmalı. |
| G3 | Code'un yazdığı `final_outcome`, lifecycle `close_call`'a bake ediliyor mu? | **Evet.** `close_call` gövdesi `{"outcome": "promise", "customer_id": ...}`; boş `reason` pre-hook ile düştü. `end_call` → `call_ended` → `close_call` sırası doğrulandı. |

Ek gözlemler:
- Chat'te ilk kullanıcı mesajı, açılış satırından önce gelir (`route ... Suppressed: Missing prior assistant message`). Bu tur `max_node_turns`'e sayılabilir; açılış node'unun cap'i buna göre seçilmeli.
- `end_call` node'una giden turda `/chat/message` 504 döndü (13 sn): çağrı sonu + lifecycle tool aynı istekte çalışıyor. Çağrı yine de kapandı ve `close_call` gitti.
- Konuşma turları 1,4–2,9 sn sürdü (küçük prompt'larla).

## Platform varsayımları: Freya'da doğrulanacak (gate 4 ve sonrası)

1. `max_node_turns` = N iken N. kullanıcı turunda transition'lar cap'ten önce mi çalışıyor?
2. Extraction değişkeni, sonraki turda değer çıkmazsa eski değerini koruyor mu? Plan: her node kendi değişken adını kullanır.
3. Code node'un yazdığı `final_outcome`, lifecycle `close_call`'a bake ediliyor mu?
4. Code değişkeni, api_request body default'una (`{{pending_amount}}`) bake ediliyor mu? Sayı tipi `{{x:json}}` ile mi gitmeli?
5. Bir api_request node'unun 500 cevabında `<node>_failure_count` artıyor mu? Bu, `create_promise` tekrar mantığı için gerekli.
