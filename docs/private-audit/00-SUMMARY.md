# Private benchmark denetimi: özet (11 Ekim 2026)

Sekiz paralel denetçi, BRAVE'in (`builder/build.py`, `builder/fb/*`) gizli final benchmark'ında karşılaşabileceği hataları araştırdı. Hepsi salt-okunur çalıştı: ağ yok, Freya/OpenRouter çağrısı yok, depo dosyası değişmedi. Ayrıntılar `01..08-*.md` dosyalarında, kanıt betikleri `out/audit-scratch/` altında (gitignored).

Kaynak: kit v3 ve resmi sayfa. Sayfanın ham metni: [`hackathon-page-2026-10-11.txt`](hackathon-page-2026-10-11.txt).

## 0. Temel bulgu: görev tanımı sayfada kit'ten farklı

| | Kit v3 README | Resmi sayfa (11 Ekim'de okundu) | Builder bugün |
|---|---|---|---|
| Final | Pazar günü iki yeni brief | **Tek vaka**: kredi kartı ekstre borcu, **Türkçe** | A/B İngilizce |
| Girdi | brief + tool API dosyası | **Klasör**: md, Word, PDF, Excel (analiz, akış çizimi, duyurular, servis sözleşmesi, test müşterileri, SSS, 6 örnek konuşma) | tek `.md` + `openapi.json` |
| Komut | | `build.py {case_folder} {mock_url}` | `build.py <brief> <openapi>` |
| Banka | Kit'in sunucusu | **Bizim yazdığımız Cloudflare Workers mock'u**; başka servise çağrı konuşmayı düşürür | URL `servers[0]`'dan, `mock_url` yok |
| Puanlama | 3 koşunun 3'ü | gizli simüle müşteriler, ledger + politika + dürüstlük | aynı |

Kit'in bir yerinde "iki final brief" yazıyor, sayfa tek vaka diyor. Hangisinin güncel olduğunu organizatörler söyleyebilir. Her iki durumda da aşağıdaki bulguların çoğu geçerli: farklı takvim, farklı sayılar, farklı sözleşme alanları, ham belge girdisi.

## 1. Birden fazla denetçinin bağımsız doğruladığı bulgular (en güvenilir)

| # | Bulgu | Denetçiler | Etki |
|---|---|---|---|
| A | **Klasör / çok biçimli girdi yok.** `build.py` klasörde `PermissionError` ile biter, docx/xlsx/pdf okuyucu yok | 01, 04, 03 | **P0.** Final'de builder hiç çalışmaz |
| B | **`mock_url` yok, araç URL'si `servers[0]`'dan.** `servers` yoksa göreli URL (`/get_account`) ve `validate` 0 hata der | 01, 08, 03 | **P0.** Araçlar yanlış sunucuya gider, konuşmalar düşer |
| C | **Dil İngilizceye sabit.** "Speak English" (`generate.py:1230`), çıkarım istemi "plain English", motorun ~40-60 cümlesi koda gömülü | 02, 03, 04, 07, 06 | **P0.** Türkçe çağrıda ajan İngilizce konuşur |
| D | **Türkçe ayrıştırma çöker.** Tarih ("yarın", "haftaya salı", "on beş Ekim") `none`/vague olur, deadline kaydedilir. `1.250` → 1.25, `1.250,50` → None, yazıyla tutar → None. DOB/son-4 ham gider. Red regex'leri hiçbir Türkçe ifadeyi yakalamaz (60 tur döngü) | 02, 05, 06, 07, 03 | **P0.** Doğru tarih/tutar kaydedilmez, kimlik kilitlenemez |
| E | **Trace dosyası encoding'siz açılıyor** (`trace.py:38,45`). cp1254'te tek `₺`, `→` ya da `≤` `UnicodeEncodeError` ile build'i öldürür | 01, 02, 04 | **P1, Windows'ta fiilen P0.** İki satırlık düzeltme |
| F | **Kimlik kilitlenmesi API alanına bağlı.** Wrong-answer limiti spec'te yok. Sayaç alanı (`wrong_answers`) "kalan hak" sanılır: doğru ilk cevap `fin_failed`'e gider. Alan yoksa 3 yanlış cevap kilitlemez, 60 tur sonra "declined" | 03, 08 | **P0** (sayaç tarzı sözleşme), aksi **P1** |
| G | **Duyuru / yürürlük tarihi modeli yok.** Çıkarıcıya tarih verilmez, spec'te `effective_from` yok. Test penceresinin ortasında yürürlüğe giren değer bir tarafta yanlış olur | 01, 04, 05 | **P1** |
| H | **`grounding()` anlamsız.** Sayı brief'te herhangi bir yerde geçiyorsa geçer: eski politika, taslak, uydurma değer hepsi "bulundu" (A+B'de 1-30 arası her tam sayı). Türkçe'de ise doğru spec'e yanlış ret + gereksiz onarım turu | 04, 01 | **P0/P1.** Güvenlik ağı yok |
| I | **Yanıttaki minor-unit tutarlar ölçeklenmez** (42050 → "42050.00", floor 100 kat yüksek). İstek tarafı yalnızca açıklamada "cents" geçerse çevirir | 08, 03 | **P1** |
| J | **Brief A varsayılanlarına sessiz düşüş.** Eksik anahtar → callback 10-19, Pzt-Cum, `human_requests_to_transfer`=2; eksik `days_late` → ürün düşer, her çağrı `no_promise/service_error`; `check()` sessiz | 03, 04 | **P1.** Yanlış ajan hata vermeden yayınlanır |
| K | **Araç adı eşleştirme katı.** `verifyIdentity` ya da `bank_` öneki `KeyError` verir, hiçbir şey yayınlanmaz. Altı araç adı `generate.py`'ye sabit | 01, 08, 03 | **P1** |
| L | **Haftanın başı Pazartesi sabit.** Cuma/Cumartesi tatilinde `attempt1` Pazar'a düşer (9729/9828 koşu) | 05, 03, 06 | **P2** (takvim farklıysa) |

## 2. Tek denetçinin bulduğu önemli bulgular

- **SM-1 (06, P1):** `reply_kind` tek etiket: "I lost my job, I need lower instalments" restructuring'e transfer eder, A32 (hardship önceliği) çiğnenir.
- **SM-4 (06, P2):** Okuma-onayda "Yes, 420 on Friday" yeni teklif sayılır, onay tekrarlanır (in_words/pieces).
- **SM-5 (06, P2):** "I already paid all of them" yalnızca ilk ürünü işaretler.
- **SM-3 (06, P2):** 0 denemelik bandda 2./3. ürüne yine teklif (spec varyantı çağrılarının %12.6'sı).
- **SM-7 (06, P3, dürüstlük):** Kayıttan sonra hardship cümlesi kaydedilmiş ürünü yeniden açar, ajan yanlış tarihle "kaydedildi" der.
- **LP-3 (07, P1):** Beş serbest LLM yüzeyi temperature 0.7'de koşudan koşuya değişir (çift sempati cümlesi, "bad connection" iddiası). 3/3 şartı için varyans kaynağı.
- **LP-2 (07, P1):** Tüm kayıtlı hang-up'lar açılış/kimlik turunda. Yavaş koşuda kimlik turu medyan 15.7 sn. Düzeltmeler **doğruluk kuralı gereği** (hafıza: accuracy-over-latency) önce correctness-neutral olduğu kanıtlanmalı.
- **LP-5 (07, P2):** `create_promise` retry döngüsü yalnızca kod sayacına güveniyor. Code node çalışmazsa sınırsız tekrar, "bir kez kaydedildi" bozulur.
- **LP-6 / SM-11 (07, 06, P2):** Eksik telefon rakamları ("447") bankaya yanlış cevap olarak gider ve 3 hakkı yer.
- **SX-5 (04, P1):** JSON ayrıştırma hatası, kesik çıktı, yanlış tipli spec (67 şekil) → ajansız bitiş, yeniden deneme yok.
- **SX-9 (04, P2):** Observer birleşimi filtresiz; spec'in kendi açılış cümlesindeki kelimeler yasaklı listeye girebilir.
- **LP-7 (07, P2):** Smoke test yalnızca canlılık. Boş yanıtla geçer.
- **CAL-4/5/6 (05, P3):** Noel bloğunda `attempt1_date` boş; `now` tek haneli saatte callback reddi; ISO olmayan `now` → `service_error`.

## 3. Temiz çıkanlar (güvenle bırakılabilir)

- İngilizce A/B motoru: 140 bin fuzz çağrısı, ciddi invariant ihlali 0; 1896 hücrelik selftest ve 13 public senaryo geçiyor (06).
- Takvim matematiği: ~1.1 milyon kontrol, bağımsız referansa karşı mantık sapması 0; saat sızıntısı yok, sonsuz döngü yok, Python 3.10/3.12 aynı (05).
- Sonraki/bu hafta günü (294 kombinasyon), callback penceresi, kimlik hook'ları, hardship/insan/stop, deadline-hafta sonu-tatil (06).
- Alan ve enum yeniden adlandırma, `data`/`payload` sarmalama, `allOf/oneOf/$ref`, query/path parametreleri, 5xx ve JSON olmayan yanıtlar (08).

Sonuç: bugünkü 10/10 İngilizce kodun doğruluğundan geliyor, ama final koşulları (Türkçe, klasör girdisi, kendi mock'umuz, sözleşme farkları) o koda ulaşmadan önce builder'ı ya da ajanı kırıyor.

## 4. Önerilen düzeltme sırası

Hepsi **genel** olmalı: final kuralı builder'a final'den hiçbir değer/ad yazmayı yasaklıyor.

1. **Girdi katmanı (A, B, K):** klasör/zip girdisi; md/docx/xlsx stdlib ile, pdf için ToUnicode destekli okuyucu (01 prototipleri `out/audit-scratch/ic/` altında); belge rolleri (politika / duyuru / SSS / test müşterisi / örnek konuşma) ve test müşterilerinin politika metninden ayrılması; `argv[2]` http ile başlıyorsa `mock_url` ve tüm araç URL'lerini onunla değiştirme; araç adı eşleştirmesini gevşetme; servis sözleşmesinin OpenAPI dışı biçimleri (md/docx tablo) için model destekli çıkarım.
2. **Trace encoding (E):** `Trace` içinde `encoding="utf-8", errors="replace"`, final komutunda `PYTHONUTF8=1`.
3. **Dil paketi (C, D):** spec'e `language` alanı; motor cümleleri build-time yerelleştirme; Türkçe tarih/tutar/rakam/evet-hayır sözlüğü; model destekli yedek (`said_date_iso`, `said_amount`) ve sessiz tam tutara düşmeme.
4. **Kimlik kilitlenmesi (F):** limit spec'e, kod tarafında kendi sayacımız (API alanına bağlı olmayan).
5. **Sözleşme uyumu (I, J):** yanıt tutarı ölçekleme, eksik alan/`type` için açık `check()` hatası, Brief A varsayılanlarına sessiz düşüş yerine uyarı.
6. **Spec güvenilirliği (G, H):** `effective_from` + çalışma zamanı seçimi; grounding'i bağlamlı yapma (alan-rol-değer eşleşmesi), parse_json yeniden deneme + gözlemci spec'e düşüş.
7. **Durum makinesi yamaları (SM-1, 3, 4, 5, 7, 8, 9, 10):** 06'daki `patched.py` aday yamalar (scratch'te 1896 hücre + 13 senaryo geçiyor).
8. **Doğruluk-nötr gecikme (LP-2/LP-3):** yalnızca kanıtlandıktan sonra.

## 5. Bu denetimin sınırları

- Canlı model davranışı ölçülmedi: `reply_kind` etiketleme kalitesi, Türkçe brief'te gerçek çıkarım, Freya routing, gerçek simüle müşteri. Ağ gerektiren sondalar 07 §5'te listeli.
- Platformu yük testine sokmamak için hiçbir Freya/OpenRouter çağrısı yapılmadı.
- Kök `tests/conftest.py` bozuk (`builder.llm` yok): `pytest` varsayılan çağrıda toplama aşamasında düşüyor. `--confcutdir=tests/hack` gerekiyor (04 ve 08 bağımsız bildirdi).
- Stale `submission/*.workflow.json` dosyalarındaki code node'ları 59 KB, bugünkü üretim (decide ~29.6 KB) değil.
- Başka bir oturum depoya commit atıyor (ör. `8d50cbb`); denetçilerin hiçbiri depo dosyası değiştirmedi.
