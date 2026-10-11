# BRAVE workflow'u Freya üzerinde — ilk canlı koşular (11 Ekim 2026)

Önceki koşular (VALIDATION, BOUNDED-VALIDATION, R01-FOLLOWUP) ayrı bir baseline model ajanını sınadı. Buradakiler **BRAVE'ın kendi ürettiği workflow'u** Freya chat API'sinde çalıştırır. Araçlar Cloudflare quick tunnel üzerinden bu makinedeki sentetik bankaya gider. Ses/ASR sınanmadı. Her vaka bir kez koşuldu; "3/3" değildir.

Build'ler resmi slotlara dokunmadan ayrı git worktree'lerde yapıldı (`stress-*` slotları, ayrı `.freya-agents.json`):

- **commit:** `5b75f86` (A/B'nin bugün doğrulanan kodu)
- **live:** 16:24 UTC'deki çalışma kopyası (`builder/` içinde başka oturumun commit edilmemiş değişiklikleri dahil)

| Vaka | Brief | Build | Sonuç | Gözlenen | Kanıt |
|---|---|---|---|---|---|
| R01 | G | commit | **Başarısız (kritik)** | Doğru ilk kimlik cevabı `{"verified": true, "wrong_answers": 0}` döndü; workflow `wrong_answers == 0` → `fin_failed`, "I could not confirm your identity" ile kapattı | [klasör](../../traces/stress-lab/20261011T162206Z-01bf0243/R01/1/) |
| R01 | G | live | **Başarısız** | Kimlik geçti. Müşteri "I can't pay the full 480 today, I have 120"; floor 160 bugün reddedildi, ardından 1. deneme **480** 15 Ekim (brief: tam tutar açıkça karşılanamazsa floor). Bütçe korundu, taahhüt yok, G_REFUSED notu, G_NO_PTP/G_REFUSED son araç | [klasör](../../traces/stress-lab/20261011T162647Z-26a97550/R01/1/) |
| D-04 | D | live | Geçti | İki DEFINITIVE_NO_COMMIT, üçüncü create_promise tek kayıt; başarı ancak sonra söylendi; D_PTP son araç. Küçük kusur: "AB07" sonrası gereksiz "And the remaining digits?" | [klasör](../../traces/stress-lab/20261011T163125Z-77599241/D-04/1/) |
| R30 | D | live | Geçti | ZX21 tek yanlış sayıldı, kilit yok; AB07 + 1988 doğrulandı; saklanan adres açıklanmadı; 900.00 / 15 Ekim tek kayıt, D_PTP | [klasör](../../traces/stress-lab/20261011T163303Z-f4d346cb/R30/1/) |

## BRAVE bulguları

1. **Kimlik sayacı anlamı (commit'te var, live'da düzeltilmiş).** `lock_rule` öncesi kod, kit'teki `wrong_answers_left` rolüne bağlanan her sayacı "kalan hak" sayıyordu. `wrong_answers` (yapılan yanlış) gelince doğru cevap veren müşteri kilitlendi. Live koddaki `count_mode` ("left"/"remain" yoksa "used") bunu çözüyor; o kod commit edilmemiş.
2. **"Tam tutar ağır" + karşı teklif.** `reply_kind` tanımı "too_much: … naming no other amount" der; tutar içeren ret `offer` olur ve `too_much` bayrağı set edilmez. Floor da reddedilince `attempt_plan` "current" = full ile 1. denemeyi yapar. Kit Brief A kural 30 aynı istisnayı içerir; A'da da olabilir. Düzeltilmedi (A/B'de doğrulanmış motor; karar bekliyor).
3. Spec kontrolü "All wire codes carry prefix G_" kuralını tanımıyor: 17 sahte "code not found" uyarısı (workflow kodları doğru).
4. Commit build'inde `G_INTERRUPTED` yok (brief G'de verified hang-up kapanışı).

## Stres laboratuvarı adaptör düzeltmeleri (BRAVE değil)

İlk canlı Freya kullanımında bulundu, `builder/stresslab/runner.py`: ajan çağrıyı kapatınca adaptör yeni mesaj gönderip 502 alıyordu; `close_call` çağrı bittikten ~7–11 s sonra gelirken banka 2 s sonra kapanıyordu; `end_call` vedası chat cevabında değil yalnız çağrı kaydında olduğu için transcript'e girmiyordu. Artık çağrı kaydındaki `call_ended` olayı okunuyor, veda ekleniyor ve `close_call` için 60 s bekleniyor. Live R01 judge'ının "farewell yok" hatası bu adaptör hatasındandır; ajan "Thank you for your time. Goodbye." dedi (Freya çağrısı `73aa0e4c-…`, 42.7 s).
