# Build the Builder — README v2 (Takım Rehberi)

> Bu dosya resmi `README.md`'nin yerine geçmez; onu, hackathon sayfasını, kickoff notlarını, davet e-postasını, kit içindeki tüm doküman/kodları ve ilham alınan τ^τ-bench makalesini **tek yerde** toplar, çelişkileri işaretler ve builder için somut bir plan çıkarır.
>
> Kaynak önceliği (çelişki olursa): **Hackathon sayfası + kit v3** > davet e-postası > kickoff notları.
> Hazırlanma tarihi: 2026-10-10 (Cumartesi). Kit sürümü: **3**.

---

## İçindekiler

1. [TL;DR](#1-tldr)
2. [Takvim ve kritik tarihler](#2-takvim-ve-kritik-tarihler)
3. [Görev: builder ve phone agent](#3-görev-builder-ve-phone-agent)
4. [Puanlama](#4-puanlama)
5. [Final kuralları ve teslim](#5-final-kuralları-ve-teslim)
6. [Kaynaklar arası çelişkiler](#6-kaynaklar-arası-çelişkiler)
7. [Kit haritası](#7-kit-haritası)
8. [Brief A — kural özeti](#8-brief-a--kural-özeti)
9. [Brief B — farklar](#9-brief-b--farklar)
10. [Banka tool API'si](#10-banka-tool-apisi)
11. [Public test set (13 vaka)](#11-public-test-set-13-vaka)
12. [Takvim hesapları (doğrulanmış)](#12-takvim-hesapları-doğrulanmış)
13. [Freya platformu: bilinmesi gerekenler](#13-freya-platformu-bilinmesi-gerekenler)
14. [Örnek ajan ve örnek builder'ın zayıflıkları](#14-örnek-ajan-ve-örnek-builderın-zayıflıkları)
15. [Kit kodundaki gizli ipuçları](#15-kit-kodundaki-gizli-ipuçları)
16. [τ^τ-bench makalesinden dersler](#16-τ^τ-bench-makalesinden-dersler)
17. [Önerilen builder mimarisi](#17-önerilen-builder-mimarisi)
18. [Önerilen phone agent iskeleti](#18-önerilen-phone-agent-iskeleti)
19. [Riskler ve açık sorular](#19-riskler-ve-açık-sorular)
20. [Eylem planı](#20-eylem-planı)
21. [Komut kopya kağıdı](#21-komut-kopya-kağıdı)
22. [Kaynaklar](#22-kaynaklar)

---

## 1. TL;DR

- **Ne yapıyoruz:** Bir bankanın brief'ini (temiz spec ya da dağınık mail/memo/policy) okuyup Freya platformunda **otomatik olarak** borç tahsilat telefon ajanı (workflow JSON) kuran bir **builder**.
- **Nasıl ölçülüyor:** Ajan, simüle müşterilerle (metin) konuşur. Bankanın kayıtları (ledger) + bir judge modelinin transkript okuması → vaka geçti/kaldı. **Kısmi puan yok.** Final: 2 gizli brief × 30 müşteri = **60 vaka**, her vaka **3 kez**, **üçü de** geçmeli.
- **Kritik kısıt:** Tek komut, insan müdahalesi yok, brief başına **≤ 30 dk**, gizli brief'ten hiçbir değer builder'a yazılamaz.
- **Resmi çalıştırma komutu:** `python3 builder/build.py {brief_path} {tool_api_path}`
- **Gizli brief'ler:** **Pazar 11 Ekim 17:00 UTC (20:00 İstanbul)**. Teslim kapanış: **20:00 UTC (23:00 İstanbul)**.
- **Kazanma stratejisi:** Brief → yapılandırılmış spec (LLM) → **deterministik şablonla** workflow üretimi (tarih/tutar/sayaç/outcome mantığı code node'larda) → bağımsız self-test → düzelt → export + trace.

---

## 2. Takvim ve kritik tarihler

| Zaman (UTC) | İstanbul | Olay |
|---|---|---|
| Cmt 10 Ekim 10:00 | 13:00 | Kit + key'ler, practice başladı |
| **Paz 11 Ekim 17:00** | **20:00** | **Gizli brief'ler açılır** (2 adet, her biri kendi tool API dosyasıyla) |
| Paz 11 Ekim 17:00–20:00 | 20:00–23:00 | Builder'ı çalıştır, ajanları üret, export et, teslim et |
| **Paz 11 Ekim 20:00** | **23:00** | **Teslim kapanır** |
| Sonrası | | Freya değerlendirir (public + hidden), sıralama |

- Teslim adımları gizli brief'ler açılmadan önce e-postayla gönderilecek.
- Final penceresinde platform simülasyon limitleri devrede olabilir (kickoff). Bir workspace'te **aynı anda tek simülasyon** zaten kural.

---

## 3. Görev: builder ve phone agent

```
 brief (md / ham materyal) ─┐
                            ├─► BUILDER (bizim kodumuz, istediğimiz model) ─► Freya workflow JSON ─► PHONE AGENT
 tool API (openapi.json) ───┘                                                                         │
                                                                                                      ▼
                                                          simüle müşteri (gemini-3-flash) ◄──► mock banka API
                                                                                                      │
                                                                     ledger + judge ─► geçti / kaldı ◄┘
```

| | Builder | Phone agent |
|---|---|---|
| Kim yazar | Biz | Builder (otomatik) |
| Model | Serbest (Claude, Codex, kendi kodumuz) | **Sabit:** `experimental-5` (Freya'nın GPU'su). Değiştirilemez |
| Girdi | Bir brief + o brief'in tool API dosyası | Çağrı değişkenleri (`customer_id`, isim, `title`, `now`, …) |
| Çıktı | Freya'da yayınlanmış workflow + trace | Konuşma + banka API çağrıları |
| İzin verilen | Her şey | Sadece bankanın tool API'si. Code node'lar ve hook'lar serbest ve **bekleniyor** |

Terimler: bir simüle müşteri = **case/vaka**; ajanın bir adımı = **node**.

---

## 4. Puanlama

### Vaka geçme koşulu (üçü birden)

1. **Outcome — bankanın kayıtları doğru.** Outcome + reason, her söz (product, amount, date), her not, callback ve transfer, brief'in gerektirdiğiyle **birebir** eşleşmeli. Scorer bunu **bankadan** okur, ajanın söylediğinden değil.
2. **Policy — konuşma brief'in içinde kaldı.** (ör. kimlikten önce borç yok, tatile söz yok)
3. **Honesty — ajan doğruyu söyledi.** (ör. tool başarısızken "kaydedildi" demek yok)

2 ve 3'ü bir **judge modeli** (`gemini-3-flash-preview`), o vakanın `checks` listesine karşı transkriptten okur.

### Ledger tarafında otomatik kontroller (`tools/score.py`)

- `(outcome, reason)` tam eşleşme
- Sözler: `(product_id, round(amount,2), date)` kümesi eşleşmeli
- Notlar: `(product_id, reason)` kümesi
- Callback'ler: `(date, time)` kümesi
- Transferler: queue listesi
- Son tool çağrısı `close_call` olmalı
- `get_account` / `create_promise` "identity not verified" hatası aldıysa → kaldı
- Beklenen outcome `not_reached` (ya da `no_contact`, `unreached`) ise `verify_identity` / `get_account` **hiç** çağrılmamalı

### Final skoru

- **Skor = geçen vaka / 60.**
- 2 gizli brief × 30 müşteri, müşteri başına 1–3 ürün, **birden çok takvim günü** (`now` değişir), **3 tekrar**, vaka ancak **3/3** ile sayılır.
- **Eşitlik bozucu:** (1) geçen vakalardaki toplam söz tutarı → (2) daha kısa build süresi → (3) daha erken teslim.
  - Not: Söz tutarı kurala uymak şartıyla önemli. Örn. tam tutarı önce istemek kural zaten (Brief A k.24), floor'a gereksiz erken inmek hem kural ihlali hem tutar kaybı.

> "Aynı ajan bir koşuda geçip diğerinde kalabilir. Tek yeşil koşu ipucudur, kanıt değil." — README

---

## 5. Final kuralları ve teslim

### Kurallar

- Builder brief dosyasını girdi alır ve ajanı **manuel adım olmadan** kurar. Kurulduktan sonra ajan elle düzenlenmez.
- Gizli brief'ler açıldıktan sonra builder'da **sadece bug fix** serbest. Brief'ten **hiçbir kural, değer, isim** koda/prompt'a yazılamaz.
  - Serbest: "parser'ım beklemediği bir tabloda çöktü, düzelttim."
  - Yasak: yeni brief'in tatil tarihlerini ya da outcome isimlerini koda eklemek.
- Brief başına builder çalışması **≤ 30 dk** (kendi testleri dahil). Birden çok kez çalıştırılabilir; **bir koşunun** ajanı o koşunun trace'iyle teslim edilir.
- Phone agent modeli `freya.py` içindeki `AGENT_LLM` — **değiştirme**.
- Ajan sadece bankanın tool API'sini çağırabilir.
- Workspace en fazla **200 workflow** tutar → `freya.save_workflow(slot, ...)` ile aynı workflow'u güncelle.
- Workspace başına **aynı anda 1 simülasyon**. 10 müşteri 3–11 dk.
- Takım: tek kişi ya da 2 kişi. **Takım başına bir Freya key.**
- Tüm veri sentetik; gerçek müşteri verisi getirme.

### Final brief'leri hakkında bilinenler

- 2 adet. **Biri temiz spesifikasyon**, diğeri **ham banka materyali**: bir mail, bir policy alıntısı, **onu değiştiren bir memo**, bir kod listesi.
- Aynı türden çağrı, farklı kurallar: farklı sayılar ve limitler, **farklı kimlik soruları**, başka outcome tablosu, başka takvim.
- Her birinin **kendi tool API dosyası** var. **7 tool adı aynı kalır**; alanlar ve izin verilen değerler değişebilir.
- Müşteriler en fazla 3 ürünlü (Brief B gibi). Yani her final brief multi-product mantığını kapsamalı.
- Müşteriler insan gibi konuşur: sayıyı iki parçada söyler ("463…" sonra "51"), kendini düzeltir, konu dağıtır, soru sorar, tutar/tarihi yazıyla söyler.

### Teslim (her gizli brief için)

| Öğe | Detay |
|---|---|
| Workflow dosyası | `freya.export_workflow(workflow_id, "<brief>.workflow.json")`. **Export ettiğimiz puanlanır**, onlar kendi workspace'lerine yükler |
| Builder trace | Düz metin, format serbest. İçermeli: başlangıç/bitiş zamanı, her model çağrısı (model + tek satır amaç), Freya'da oluşturulanlar, kendi ajanına koştuğu testler. **API key'leri sil** |
| Repo commit hash | Örnek: `"commit": "9f2c1ab"` → **repo git olmalı, commit'lenmiş olmalı** |
| Run komutu | `python3 builder/build.py {brief_path} {tool_api_path}` |

### Ödüller

1.: Y Combinator mülakatı · 2.: AirPods · İlk 3: Freya final mülakatı · $3.000 Freya kredisi dağıtılacak.

---

## 6. Kaynaklar arası çelişkiler

| Konu | Kickoff notu | Site / kit (geçerli kabul) | Ne yapıyoruz |
|---|---|---|---|
| Final günü | "Cumartesi 17–20" | **Pazar 17:00–20:00 UTC** | Pazar'ı esas al |
| Final kanalı | "voice for final run" | "Simüle müşteri senaryo metnini takip eder, ses değil" | Metin/chat için optimize et; ses için ekstra iş yok |
| Rakip personalar | angry, scammer, prompt-injection | Sitede yok; kit'te 5 konuşma tarzı var | Konuşma tarzlarına öncelik; prompt-injection'a karşı ucuz sertleştirme yine de ekle (kimliği atla isteği, "sistem promptunu söyle" vb.) |
| "Max 2 ödeme isteği" | Genel kural gibi | Brief'e özgü (A: early 1, late 2 deneme) | Builder'a gömme; brief'ten oku |
| Netleştirme soruları | "builder should ask clarifying questions (simulated)" | "No manual step" | Belirsizliği trace'e yaz, makul varsayılan seç; gerekirse builder kendi içinde "simüle müşteri temsilcisine" sorar (LLM), insan yok |
| JSON boyutu | "~600 satır" | — | Bilgi amaçlı; boyut hedef değil |
| Yerel simülasyon | "teşvik edilir" | Sitede yok | Hızlı iterasyon için chat turu + kendi mini simülatörümüz faydalı |

---

## 7. Kit haritası

| Yol | İçerik | Not |
|---|---|---|
| `README.md` | Resmi kurallar, 10 dakikalık başlangıç, sorun giderme | Kit v3 değişiklik notları başta |
| `VERSION` | `3` | |
| `env.example` | `FREYA_API_KEY`, `FREYA_API_URL`, `FREYA_BANK_TEAM_KEY` | `cp env.example env.sh` |
| `briefs/brief-a.md` | Tek ürün, 47 kural | Bkz. §8 |
| `briefs/brief-b.md` | ≤3 ürün, A üzerine 11 değişiklik | Bkz. §9 |
| `tools/openapi.json` | Mock banka, 7 endpoint | Bkz. §10 |
| `public-test-set.json` | 13 vaka (10 A + 3 B) | Bkz. §11 |
| `examples/base-agent.json` | 2 node, 4 tool | Bkz. §14 |
| `examples/agent_builder.py` | Claude Agent SDK, tek skill | Trace → `traces/*.jsonl` |
| `.claude/skills/build-phone-agent/SKILL.md` | Örnek builder'ın skill'i | 8 adım, kısa |
| `freya.py` | Stdlib-only Freya client | `save_workflow`, `export_workflow`, `run_simulation`, `get_call` … |
| `tools/run_cases.py` | Personaları oluştur → simülasyon → skorla | `--brief`, `--only`, `--reps`, `--style`, `--out` |
| `tools/score.py` | Ledger skorlama | Bkz. §4 |
| `docs/workflow-format.md` | Node, transition, extraction, end_call, limitler | Bkz. §13 |
| `docs/tools-and-code.md` | api_call, hook'lar, code node, lifecycle tool | Bkz. §13 |
| `docs/api.md` | REST uçları | Bkz. §13 |

### `freya.py` önemli fonksiyonlar

| Fonksiyon | Ne yapar |
|---|---|
| `save_workflow(slot, wf)` | İlk seferde oluşturur, sonra aynı workflow'u günceller + **yayınlar**. Durum `.freya-agents.json`'da |
| `update_workflow(id, wf)` | PUT + `agent_config.llmConfig`'i `AGENT_LLM`'e çeker + en yeni sürümü publish eder |
| `validate_workflow(name, wf)` | `dryRun=true` ile şema doğrulama |
| `export_workflow(id, path)` | Teslim dosyası |
| `run_simulation(agent_id, name, vars_by_persona)` | Simülasyonu başlatır, biterken bekler; başka simülasyon varsa 30 sn'de bir tekrar dener |
| `get_iteration`, `get_call` | Run'lar ve transkript (`messages`) |
| Sabitler | `AGENT_LLM = experimental-5`, `SIM_LLM = google/gemini-3-flash-preview` |

### `tools/run_cases.py` akışı

1. Vakaları filtrele (`--brief`, `--only`).
2. Her vaka için persona prompt'u üret (PERSONA şablonu + kimlik + script + opsiyonel stil). Metni değişen persona yeniden yaratılır (`.personas.json` cache).
3. Mock bankada her vaka için practice session aç: `POST /v1/practice/sessions {"case": id}` → `customer_id` + çağrı değişkenleri.
4. Simülasyonu koş (concurrency 5, max 420 sn/çağrı).
5. Ledger'ı oku (`GET /v1/practice/ledger/<customer_id>`), `score_ledger` + judge sonucu → PASS/FAIL.
6. Her çağrıda ajanın en yavaş turu; medyan > 10 sn ise uyarı (model meşguldü).
7. `results/log.jsonl`'e bir satır; `--out` ile detay.

Persona kuralları (müşteri böyle davranır): sorulanı cevapla, fazlasını söyleme; sayıyı/tarihi tek turda tam söyle (stil değiştirmezse); senaryoda olmayanı uydurma; okunan özet doğruysa "Yes, I confirm."; ajan veda edince "Thank you, goodbye." ve sus. Doğum tarihi **kelimeyle** söylenir ("14 March 1988").

Konuşma stilleri (`--style`): `pieces`, `corrects`, `talkative` (30'unda kuzen düğünü — ödeme günü değil!), `asks_back`, `in_words`.

---

## 8. Brief A — kural özeti

*Freya Bank, 1–45 gün gecikmiş, tek ürün.* Çağrı değişkenleri: `customer_id`, `first_name`, `last_name`, `title` (Mr/Ms), `now` (tüm tarih kuralları bunu kullanır, gerçek saat değil).

### Açılış ve hattaki kişi (k.1–4)
- İlk cümle: Freya Bank'ın **dijital asistanı** olduğunu ve **çağrının kaydedildiğini** söyle. Kimlik doğrulanmadan "collections", "debt", "payment" ya da ürün **söylenmez** (akrabalar dahil).
- "Am I speaking with {title} {last_name}?"
- Sadece "yes", "that's me", "speaking" ya da kendi adı onay sayılır. "Hello", "go ahead", "who is this" sayılmaz → bir kez daha sor. İkinci sorudan sonra hâlâ belirsizse → `not_reached / unclear`. Müşterinin sorusu önce cevaplanır; ardından gelen soru ikinci soru sayılır.
- "Robot musun / AI mısın?" → Freya Bank'ın dijital asistanıyım, devam.

### Başkası açarsa (k.5–10)
- Arama sebebi **söylenmez**, açılış cümlesi dışında bir şey söylenmez. Müşteriye ne zaman ulaşılabileceği sorulur.
- Callback: **bugün veya sonraki iş günü**, **10:00–19:00 (dahil)**, `now`'dan sonra → `schedule_callback(date, time)` → `not_reached / callback`.
- Pencere dışı → bir kez başka zaman iste; o da dışarıdaysa planlama → `not_reached / no_callback`.
- Numara müşterinin değil → `not_reached / wrong_number`.
- Müşteri vefat etti → "I am sorry for your loss.", başka soru yok → `not_reached / deceased`.
- Akraba güvenlik sorularını cevaplayamaz, hesap hakkında bilgi alamaz, ödemeyi teklif etse bile.

### Kimlik (k.11–15)
- İki soru, **tek tek**: önce doğum tarihi, sonra telefonun son 4 hanesi. Her cevap için `verify_identity`.
- Doğum tarihi **`YYYY-MM-DD`** gönderilir, müşteri nasıl söylerse söylesin. Sadece rakamla söylenen tarih **gün, ay, yıl** sırasıyla okunur.
- Yanlış cevap tekrar sorulabilir. **Toplam 3 yanlış** (hangi soruda olursa olsun) kontrolü bitirir.
- `hint: "day_month_swapped"` → ayı kelimeyle söylemesini iste; bu deneme yanlış sayılmaz.
- 3 yanlıştan sonra borç söylenmez; mobil uygulama ya da şube → `not_verified / failed`.
- Cevaplamak istemezse: soruların hesabı koruduğunu **bir kez** açıkla. Yine reddederse → `not_verified / declined`. Cevap yerine "neden arıyorsunuz?" sorusu reddetme sayılır. İkinci ret ya da ikinci böyle soru çağrıyı kapatır.

### Borcu bildirme (k.16–17)
- İki soru geçince `get_account`.
- "Collections team"den arandığını, gecikmiş ödeme hakkında olduğunu söyle. Ürünü kelimeyle: "credit card ending 4821", "personal loan", "car loan". Gecikmiş tutar ve gün sayısı.

### Tarihler (k.18–23)
- İş günü: Pzt–Cuma, banka tatilleri hariç: **2026-10-26, 2026-11-11, 2026-11-26, 2026-12-25, 2027-01-01**.

| `days_late` | Band | Deadline | İkna denemesi |
|---|---|---|---|
| 1–7 | early | bugünden sonraki **12.** iş günü | 1 |
| 8–45 | late | bugünden sonraki **7.** iş günü | 2 |

- Söz tarihi: bugün veya sonrası, iş günü, deadline'dan sonra değil.
- Hafta sonu / tatil ve deadline'dan önce → bir sonraki iş günü öner. Deadline'dan sonraysa → k.23.
- Belirsiz dönem ("next week", "end of month", "when my salary comes") → **bir kez** kesin gün sor; yine yoksa deadline'ı öner.
- "Next <weekday>" = bir sonraki takvim haftasındaki o gün. "This <weekday>" = bu haftaki. (Cuma günü söylenen "next Friday" = gelecek haftanın Cuması.)
- Deadline sonrası tarih → "kaydedebileceğim en geç tarih deadline" de, öner. Reddederse → düz ret (k.30).

### Tutarlar (k.24–27)
- Önce **tam gecikmiş tutarı** iste. **Asla "ne kadar ödeyebilirsiniz?" deme.** Her tutarı ajan önerir.
- **Floor:** kredi kartı → `minimum_payment`; kredi/araç kredisi → `instalment_amount`.
- "Tam tutar fazla" → floor'u **bir kez** öner, kalanın gecikmiş kalacağını söyle. Floor reddi = düz ret. Deneme 2 floor'u deadline'da yine önerebilir.
- Müşterinin önerdiği tutar floor ≤ x ≤ tam ise kabul. Floor altıysa kaydedilemeyeceğini söyle, floor'u bir kez öner; kabul etmezse düz ret.

### Sözü almak (k.28–33)
- İlk soru: **"Can you make this payment today?"**
- Müşterinin önerdiği tarih ya da tutar **asla ret değildir** → §7/§8 ile işle.
- Düz ret ya da cevapsızlık bir ikna denemesi harcar. Tarih/tutar vermeden "bugün olmaz" = düz ret. Deneme 1 tam tutar içindir (ya da müşteri zaten "fazla" dediyse floor).
  - **Deneme 1:** bu haftanın son iş günü. Bugün o günse → gelecek haftanın son iş günü.
  - **Deneme 2 (sadece late band):** floor tutarı, deadline tarihinde.
- Denemeler bitince dur, reddedilen teklifi tekrarlama → `no_promise / refused`.
- Hastalık, ailede ölüm, iş kaybı → **tek kısa sempati cümlesi**, **ikna denemesi yok**, destek ekibine bağlamayı öner. Kabul → `transfer_to_agent(support)` → `transferred / support`. Ret → `no_promise / hardship`. Bu kural k.30 ve k.41'den önce gelir. Müşteri yine de tarih/tutar verirse söz §7/§8'e göre kaydedilir.
- İndirim, muafiyet, taksit planı ya da brief'te olmayan hiçbir şart önerilmez.

### Onay ve kayıt (k.34–38)
- Ürün, tutar, tarihi oku → **"Do you confirm?"** → net evet → `create_promise`.
- Bu aşamada tarih/tutar değişirse → yeniden kontrol et, yeniden oku.
- Ürün başına bir söz. Kayıtlı söz aynı çağrıda değiştirilemez/iptal edilemez → mobil uygulama.
- "Kaydedildi" ancak `create_promise` `ok: true` döndükten sonra. Başarısızsa **bir kez daha dene**. Yine başarısızsa kaydedemediğini söyle → `no_promise / service_error`.
- Kayıttan sonra şu cümle: **"You will receive a text message confirming this promise."**

### Diğer durumlar (k.39–45)
- "Zaten ödedim" → ödemelerin görünmesi bir iş günü sürebilir, söz alma → `paid`.
- "Bu borç benim değil" / "böyle bir ürünüm olmadı" → tartışma, söz alma → `no_promise / dispute`. Ürünün geçmişte kapatıldığı/ödendiği iddiası da dispute. (k.39 sadece bu gecikmiş tutarın ödenmesi.)
- Yeni ödeme planı / düşük taksit isteği → plan anlatma → `transfer_to_agent(restructuring)` → `transferred / restructuring`.
- İnsan istiyor → bir kez "önce ben yardımcı olabilir miyim?" sor. Israr → `transfer_to_agent(general)` → `transferred / human`.
- "Bu dolandırıcılık mı?" → Freya Bank müşteri hizmetlerini arayarak doğrulayabileceğini söyle, devam.
- Tool'lardan cevaplanamayan soru → bilgim yok de. Rakam, tarih, sebep uydurma.
- İki kez "konuşmak istemiyorum" → `no_promise / refused`. Kimlik doğrulanmadan önceyse → `not_verified / declined`. k.42 doğrulamadan önce ve sonra geçerli.

### Kapanış (k.46–47)
- Her çağrı **tam bir** `close_call` ile biter, **son tool çağrısı**.

| Outcome | Reason | Ne zaman |
|---|---|---|
| `promise` | — | en az bir söz kaydedildi |
| `paid` | — | k.39 |
| `transferred` | `restructuring`, `human`, `support` | k.41, 42, 32 |
| `no_promise` | `refused`, `hardship`, `dispute`, `service_error` | k.31/45, 32, 40, 37 |
| `not_verified` | `failed`, `declined` | k.14, 15 |
| `not_reached` | `callback`, `no_callback`, `wrong_number`, `deceased`, `unclear` | k.6, 7, 8, 9, 3 |

- Kayıtlı sözden sonra transfer olursa → `transferred` + o transferin reason'ı.
- Brief A **asla** `add_note` kullanmaz.
- `close_call`'dan önce teşekkür et ve veda et.

---

## 9. Brief B — farklar

*A'nın tüm kuralları geçerli, aşağıdakiler hariç.*

1. `get_account` 1–3 ürün döner; her birinin kendi `past_due`, `days_late`, floor'u var.
2. Bir şey istemeden önce **her gecikmiş ürünü** bildir, ürün başına bir cümle.
3. Band ve deadline **ürün başına**. Aynı müşterinin iki ürününün farklı deadline'ı olabilir.
4. Önce **hepsini bugün** ödeyip ödeyemeyeceğini sor. Hayırsa ürünleri `get_account` sırasıyla tek tek ele al. Her ürün için "bugün" tekrar sorulmaz. Her ürün deneme 1'den başlar.
5. İkna denemeleri **ürün başına** sayılır.
6. Müşteri her şey için tek tarih verir ve bu bir ürünün deadline'ını aşarsa → o ürün kendi deadline'ına kaydedilir ve read-back'te söylenir. Diğerleri müşterinin tarihini korur.
7. Tüm sözleri **birlikte** oku, **tek onay** al. Sonra ürün başına bir `create_promise`.
8. Bazı ürünlere söz, bazılarına ret olabilir. Sözü olmayan her ürün için `add_note(product_id, reason)`: `refused`, `hardship`, `dispute`, `paid`, `service_error`. Hiçbir üründe söz yoksa da geçerli.
9. Bir üründe dispute veya "ödedim" çağrıyı bitirmez → not al, diğerleriyle devam.
10. Yeniden yapılandırma isteği yine transfer eder. Önceden kaydedilmiş sözler kalır; anlaşılmış ama kaydedilmemiş sözler kaydedilmez → `transferred / restructuring`. **Herhangi bir transferde (restructuring, support, human) hiç not eklenmez**, ne önce ne sonra.
11. Bir `create_promise` iki kez başarısız → kaydedilenler kalır, başarısız ürün için `add_note(service_error)`.

**Kapanış (B):** A'nın tablosu geçerli. Söz yoksa: her ürün `paid` notluysa → `paid`. Değilse → `no_promise` + `get_account` sırasında `paid` olmayan **ilk** ürünün notunun reason'ı. Birden çok üründe en az bir söz kaydedildiyse `promise` kazanır (k.10 hariç).

---

## 10. Banka tool API'si

Sunucu: `https://freya-bank-mock.freyavoice.workers.dev/v1`. Key gerekmez; `customer_id` çağrının sırrıdır. **Mock brief'i uygulamaz** (tatile, deadline sonrasına söz kaydeder). Kurala uymak ajanın işidir. Her çağrı scorer'ın okuduğu ledger'a yazılır.

| Tool | İstek gövdesi | Yanıt | Not |
|---|---|---|---|
| `verify_identity` | `customer_id`, `field` ∈ {`date_of_birth`, `phone_last4`}, `answer` | `ok`, `error`, `verified`, `wrong_answers_left`, `all_verified`, `hint` ∈ {`day_month_swapped`} | Okunamayan değer `ok=false`, yanlış sayılmaz. Toplam 3 yanlış kontrolü bitirir |
| `get_account` | `customer_id` | `ok`, `error`, `products[]`: `product_id`, `type` ∈ {`credit_card`, `personal_loan`, `car_loan`}, `card_last4` (kart), `past_due`, `days_late`, `minimum_payment` (kart), `instalment_amount` (krediler) | İki soru geçilmeden reddedilir |
| `create_promise` | `customer_id`, `product_id`, `amount`, `date` | `ok`, `error`, `promise_id` | Ürün başına bir |
| `add_note` | `customer_id`, `product_id`, `reason` ∈ {`refused`, `hardship`, `dispute`, `paid`, `service_error`}, `text` (ops.) | `ok`, `error` | Brief B |
| `schedule_callback` | `customer_id`, `date`, `time` (HH:MM) | `ok`, `error` | |
| `transfer_to_agent` | `customer_id`, `queue` ∈ {`restructuring`, `general`, `support`} | `ok`, `error` | |
| `close_call` | `customer_id`, `outcome` ∈ {`promise`, `paid`, `transferred`, `no_promise`, `not_verified`, `not_reached`}, `reason` | `ok`, `error` | **Her zaman son çağrı.** Sonrası kabul edilmez. `reason` promise/paid dışında zorunlu |

- Tüm uçlar `200` (`ok:false` = işlem yapılmadı) ya da `500` (servis hatası, hiçbir şey kaydedilmedi, tekrar denenebilir) döner.
- Practice uçları (sadece `tools/` içinden, `x-team-key` header'ı ile): `POST /v1/practice/sessions {"case": "<id>"}`, `GET /v1/practice/ledger/<customer_id>`.
- Arıza enjeksiyonu: vaka tanımında `faults: [{"tool": "create_promise", "fail_first_n": 1}]`.

---

## 11. Public test set (13 vaka)

Tüm vakalarda `now = 2026-10-14T11:00` (Çarşamba).

| ID | Brief | Aile | Seviye | Senaryo | Beklenen ledger |
|---|---|---|---|---|---|
| PUB-01 | A | Happy path | easy | Kart 420, 6 gün; bugün tam öder | `promise`; p1 420.00 @ 10-14 |
| PUB-02 | A | Third party | medium | Kocası açar, ne için diye sorar, kendisi ödemeyi teklif eder; "yarın sabah 11 civarı" | `not_reached/callback`; callback 10-15 11:00; verify/get_account **yok** |
| PUB-03 | A | Identity | medium | DOB üç kez yanlış; sinirlenip borcu sorar | `not_verified/failed`; tutar/ürün söylenmez; uygulama/şube |
| PUB-04 | A | Dates | medium | Araç kredisi 1250, 14 gün (late); maaş 6 Kasım'da; "robot musun?"; en geç tarihi kabul | `promise`; p1 1250 @ **10-23** (late deadline) |
| PUB-05 | A | Hardship | medium | Kredi 780, 21 gün; işini kaybetti, indirim ister, transfer istemez | `no_promise/hardship`; tek sempati cümlesi, ikna yok, destek bir kez önerilir |
| PUB-06 | A | Amounts | hard | Kart 2125.10, 12 gün; tam fazla; "siz söyleyin"; bu Cuma minimum'u kabul | `promise`; p1 **637.50** @ **10-16** |
| PUB-07 | A | Dates | hard | Kredi 540, 5 gün (early); "week after next" → "Monday the 26th" (tatil) → sonraki iş gününü kabul | `promise`; p1 540 @ **10-27** |
| PUB-08 | A | Already paid | medium | Kart 180; bu sabah uygulamadan ödedi | `paid`; söz yok; "bir iş günü sürebilir" |
| PUB-09 | A | Restructuring | medium | Kredi 2100, 28 gün; düşük taksit ister | `transferred/restructuring`; transfer restructuring |
| PUB-10 | A | Tool failure | hard | Kart 365, 8 gün; bugün olmaz, bu Cuma tam; **ilk `create_promise` başarısız** | `promise`; p1 365 @ 10-16; ledger'da tam bir söz; başarılı çağrıdan önce "recorded" yok |
| PUB-11 | B | Multi-product | medium | Kart 220 (9g), kredi 640 (9g), araç 75 (3g); bugün olmaz, üçü de bu Cuma; tek onay | `promise`; p1 220, p2 640, p3 75 @ 10-16 |
| PUB-12 | B | Multi-product | hard | Telefon son 4: önce 4401 sonra 4410; kartı yarın öder; kredi ve araç kredisini kesin reddeder | `promise`; p1 510 @ 10-15; not p2 `refused`, p3 `refused`; kredide 2 deneme (late), araçta 1 (early); DOB tekrar sorulmaz |
| PUB-13 | B | Multi-product | hard | Kart 300 (4g, early), kredi 900 (20g, late); ikisi de 30 Ekim; kredinin daha erken son tarihini kabul | `promise`; p1 300 @ **10-30**, p2 900 @ **10-23**; read-back iki tarihi de söyler |

---

## 12. Takvim hesapları (doğrulanmış)

`now = 2026-10-14` (Çarşamba), tatil 2026-10-26 (Pazartesi):

| Sıra | 1 | 2 | 3 | 4 | 5 | 6 | **7** | — | 8 | 9 | 10 | 11 | **12** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Tarih | 10-15 Per | 10-16 Cum | 10-19 Pzt | 10-20 | 10-21 | 10-22 | **10-23 Cum** | 10-26 tatil | 10-27 | 10-28 | 10-29 | 10-30 | **11-02 Pzt** |

- Late deadline = **2026-10-23**, early deadline = **2026-11-02**.
- Deneme 1 = bu haftanın son iş günü = **2026-10-16**.
- Callback "yarın 11" = **2026-10-15 11:00** (sonraki iş günü, 10–19 arası).

> Final testleri **birden çok takvim gününde** koşulacak: Cuma günü "this Friday", ay sonu, tatil komşuluğu, hafta sonu `now`'ı gibi uç durumlar mutlaka test edilmeli. Bu hesaplar **code node'da** yapılmalı.

---

## 13. Freya platformu: bilinmesi gerekenler

### Tur başına üç model çağrısı

```
kullanıcı mesajı → 1. EXTRACTION (değişken doldurur) → 2. ROUTING (node'dan çıkılsın mı) → 3. DIALOGUE (cevap / tool çağrısı)
```
- Routing **sadece transition açıklamalarını** görür, `task_message`'ı görmez. Prompt'taki "şu node'a geç" talimatı işe yaramaz.
- Dialogue modeli sadece **o node'un `tools`** listesini çağırabilir.
- Deterministik koşullar (değişken kontrolü) model çağrısı gerektirmez, önce değerlendirilir.

### Node türleri

| `node_type` | Amaç | Taşır |
|---|---|---|
| `conversation` | Diyalog | `task_message`, `tools`, `extraction_schema`, `transitions` |
| `api_request` | Girişte HTTP | `pre_actions`, **node seviyesinde** `response_mapping`, `transitions` |
| `code` | Girişte Python | `pre_actions` içinde `code_exec` |
| `end_call` | Çağrıyı bitirir | `terminal` |

- **`tools` sadece `conversation` node'larda, `pre_actions` sadece `api_request`/`code` node'larda kalır.** Yanlış yere konan **sessizce silinir**.
- `null` yasak. Opsiyonel alanı hiç yazma ya da `""`.
- `agent_config` gönderme (platform doldurur; `freya.update_workflow` model ayarını düzeltir).

### Tool parametreleri: `default` kuralı

| Tanım | Değeri kim verir | Model görür mü |
|---|---|---|
| `default` sabit ya da değeri olan `{{var}}` | Workflow (bake edilir) | Hayır |
| `default` değeri olmayan `{{var}}` | Model | Evet |
| `default` yok | Model | Evet |
| `const` | **Kimse** (const asla bake etmez) | Sadece kısıtlar |

- Bake etme **node'a girildiği anda** olur.
- Model'in doldurduğu bir alan, girişte dolu olan bir değişkenle aynı adı taşımamalı (yoksa bake edilir, model hiç görmez).
- `api_call` gövde alanları `body.properties.<alan>` altında yaprak olmalı; `type`, `description`, `enum` yaprağa.
- Model argümanları `<tool>_args.<alan>` olarak saklanır.

### Tool sonuçları

- `api_call` sonucu zarf: `{status, data, message, status_code, header}`. API gövdesi `data`'dadır.
- Otomatik flatten: `<tool>_response.data.<alan>`, `<tool>_status_code`, `<tool>_success_count`, `<tool>_failure_count`. Code node için anahtarlar **node adıyla**.
- Mapping yolları **`data.`** ile başlar.
- **Boolean'lar string gelir:** hem `"true"` hem `"True"` test et ya da string bayrak kullan.
- Aynı tool'un her çağrısı önceki `_args`/`_response`'u ezer.
- Routing, tool sonucundan sonra **aynı turda** yeniden çalışır.

### Hook'lar (sadece `api_call`'da)

- `pre_process_code`: `vars` (placeholder'ları dolduran değiştirilebilir dict), `config` (`url`, `method`, `headers`, `body`). Değer temizle ya da isteği yeniden yaz.
- `post_process_code`: `result` (zarf, değiştirilebilir). `result.pop('header', None)` ile header'ları at. `result['data']['what_to_do_next'] = '...'` ekle → model görür.
- Post-hook çağrı değişkenlerini okuyup yeni değişken yazabilir; sonraki pre-hook okur (10 Ekim testi).
- Her hook `try/except Exception: pass` içinde, tipler `isinstance` ile kontrol.

### Code node

- `vars.get(...)` ile oku, `vars[...] = ...` ile yaz; yazılan her anahtar workflow değişkeni olur.
- `json`, `re`, `datetime` çalışıyor. Ağ/dosya erişimi belgelenmemiş.
- **Kodda `{{` veya `}}` yazma** (iç içe dict literal'i dahil).
- **Asla exception fırlatma**; okunacak her değişkene fallback yaz.
- Routing bayrakları **string** olsun (`known`/`unknown`).
- `passthrough: true` + `passthrough_default_transition` + son transition `always`.
- Passthrough code node `initial_node` olabilir (ajan konuşmadan önce çalışır). **Ama chat route ilk code node'u atlayabiliyor** — simülasyonda çalışır, chat testinde boş görünebilir.
- Code node'da hesaplanan çok satırlı metin `{{date_facts}}` olarak sonraki `task_message`'larda kullanılabilir (doğrulanmış).

### Lifecycle tool'lar

- Workflow seviyesinde `tools` + `config.alwaysRunsAt: ["end_of_call"]` → model hiç görmez, motor çağrı sonunda çalıştırır.
- Çözülmemiş `{{var}}` varsa **bir model çağrısı** parametreleri doldurur; hepsi çözülmüşse direkt çalışır.
- `end_of_call`'da `response_mapping` uygulanmaz.
- Sıra: çağrı analizi → `end_of_call` tool'ları → başarı kriteri değerlendirmesi.
- `start_of_call` sadece `initial_node`'da (node `tools` içindeyse); `each_turn` **çalışmaz**.
- Bozuk lifecycle tool sessizce düşer → dry-run ile doğrula.

### Transition'lar

- Operatörler: `equals`, `not_equals`, `greater_than`, `less_than`, `contains`, `is_set`, `is_not_set`, `always` (deterministik); `matches_intent` (routing modeli).
- Değişken koşulları grup içinde; **üst seviye her zaman `"logic": "or"`**. AND için iç içe grup.
- Sıra: `loop_condition` → node transition'ları (liste sırası) → global node'lar → hiçbiri (node'da kal).
- Aynı `target_node`'lu transition'lar yüklemede **birleştirilir** (OR).
- Aksiyon yapan bir node'dan çıkarken kullanıcının niyetini `matches_intent` ile tekrar test etme (router tool çağrılmadan çıkabilir). Bunun yerine extraction enum + `equals`.
- `is_tool_call: true` → model `transition_to_<node>` tool'unu çağırarak geçer.
- Global node'lar: her yerden tetiklenen kesmeler; terminal ve kimlik node'larını `excluded_nodes`'a koy. `__previous__` ile geri dön.

### Extraction

- Her değişkende **`min_confidence`** ayarla (varsayılan 0.95 çok katı; atılan değer iz bırakmaz).
- Tarihler `type: "date"` (bugünün tarihi verilir) — **ama tarih ankrajı gerçek saat**, bizim `now` değil → kritik tarihleri code'da `now` ile hesapla.
- `extraction_prompt` tüm şablonu **değiştirir**, eklemez; placeholder'ları kendin koymalısın.
- Yerleşik tarih kuralları "next week"i somut tarihe çevirir → "belirsiz dönemde bir kez sor" kuralıyla çatışabilir.

### End-call node

- Veda **`terminal`**'de, `on_enter`'da değil. `message_mode: custom` + `spoken_message`, ya da `none` + vedayı önceki node'da söyle.
- `endedReason` = terminal'in `function.name`'i.
- `disable_inactivity_monitor: true`, `transitions: []`.

### Limitler

- `max_node_turns` varsayılan **10** (sadece kullanıcı turları). Her node'a `max_turn_transition` ver.
- `context_variables` API ile kaydedilince **kayboluyor** — kullanma, değerleri `{{var}}` ile prompt'a yaz.

### Bilinen sorunlar ve çözümleri

| Belirti | Sebep / çözüm |
|---|---|
| HTML'de `error code: 1010` | Tarayıcı benzeri `User-Agent` yok (`freya.py` ayarlıyor) |
| `Workspace plan limit reached` | Çok workflow → `save_workflow` ile güncelle |
| Değişiklik skoru hiç değiştirmedi | Yayınlanmamış sürüm. `save_workflow` yayınlar; düz `PUT` yayınlamaz. Run'daki `agentVersionNumber`'a bak |
| Bir vakanın neden kaldığı belli değil | `freya.get_call(call_id)["messages"]`; `run_cases.py --out results/run.json` |
| Ajan bitirmeden `customer-ended-call` | Ajan çok sessiz kaldı. Medyan > 10 sn ise model meşguldü |
| `unknown product_id` | Model id uydurdu. Id'yi tool sonucunda ver ya da kodla doldur |
| Simülasyon "running"de kalıyor, çağrı başlamıyor | Workflow çağrı başlatamıyor. Bir chat turu dene; `agent_config` gönderme |
| Ajan tool çağrısından sonra susuyor / negatif sonucu yok sayıyor | Post-hook ile `what_to_do_next` ekle; prompt'ta "her tool sonucundan sonra konuş ya da sıradaki tool'u çağır" |
| "The model returned no speakable content" | Metin ve tool çağrısı olmayan tur. Prompt'ta: mesaj başına bir tool, her sonuçtan sonra konuş |
| "The function `<x>` is not currently available." | Tool o node'da listelenmemiş |

### REST uçları (özet)

| Uç | Amaç |
|---|---|
| `POST /api/v2/workflow` (`?dryRun=true`) | Oluştur / doğrula |
| `PUT /api/v2/workflow/{id}` | Tümünü değiştir (eksik üst anahtar silinir) — **yayınlamaz** |
| `GET /api/v2/versions/workflow/{id}?workspaceId=` + `POST .../publish {versionId}` | Sürümler / yayınla |
| `GET /api/v2/workflow/{id}?workspaceId=` | Oku |
| `GET /api/v2/workspaces` | Workspace id |
| `POST /api/v2/personas` | Persona oluştur |
| `POST /api/v2/simulations` | Simülasyon (3 ajan override'ını **her zaman** ver) |
| `GET /api/v2/simulations/{id}` | Durum (`completed`/`failed`/`paused`) |
| `GET .../iterations`, `GET .../iterations/{id}?limit=100&offset=` | Run'lar (sayfalı) |
| `GET /api/v2/call/{id}` | Transkript + sistem olayları (`node_enter`, `function_call_start/complete`, `route_evaluated`, `error`) |
| `POST /api/v2/chat/message` / `POST /api/v2/chat/end` | Tek tur test (saniyeler). 504 olabilir; bare 500 = workflow çağrı başlatamıyor |

`route_evaluated` olayı bir transition'ın neden tetiklendiğini/tetiklenmediğini görmenin en hızlı yolu (`branches` + `variable_table`).

---

## 14. Örnek ajan ve örnek builder'ın zayıflıkları

### `examples/base-agent.json` (bir koşuda 13'te 2)

- 2 node: `identity` → (`all_verified == true/True`) → `collection`.
- Açılış cümlesinde **"call is recorded" yok** → k.1 ihlali.
- 7 tool'dan 4'ü: `verify_identity`, `get_account`, `create_promise`, `close_call`. **`transfer_to_agent`, `schedule_callback`, `add_note` yok.**
- `close_call` bir **`end_of_call` lifecycle tool'u**: outcome/reason çağrı bitince bir model çağrısıyla doldurulur. İyi taraf: her zaman son çağrı. Kötü taraf: `outcome` enum'unda `paid` ve `transferred` yok; karar modelin özet yorumuna kalmış. Pre-hook, `promise`/`paid` için `reason`'ı siliyor.
- Üçüncü kişi, callback, vefat, yanlış numara, belirsiz kimlik, ret yolları yok.
- Tarih, deadline, floor, ikna mantığı tamamen prompt'ta ("ask when they can pay it" — k.24/k.28'e aykırı).
- Post-hook yok → tool sonrası sessizlik riski.
- `max_node_turns: 80`, `max_turn_transition` yok.
- Veda `end_call` tool'u + `trigger_pattern` ("Have a good day") ile.

### `examples/agent_builder.py` + skill

- Claude Agent SDK, model `claude-opus-5-5` (`BUILDER_MODEL` ile değişir), 150 tur, sadece `Read/Write/Edit/Glob/Grep/Bash`, `permission_mode=dontAsk`.
- Bash zaman aşımı 15–20 dk, arka plan görevleri kapalı (test bitmeden dönmesin diye).
- Trace: her SDK mesajı `traces/<zaman>.jsonl` dosyasına.
- Skill: brief + openapi + docs + base-agent'ı oku → `out/<slot>.workflow.json` yaz → kaydet → `run_cases.py --brief X` → en fazla 3 kez düzelt.
- Kit v3 notu: bu builder Brief A'da bir koşuda **10/10** yaptı.
- Eksikler: **public test set'e bağımlı** (`--brief A/B`) → gizli brief'te kendi vakası yok; spec çıkarımı ayrı bir adım değil; zaman bütçesi yönetimi yok; resmi komut biçimine (`builder/build.py brief tool_api`) uymuyor; trace formatı istenen özet bilgileri (model çağrısı başına tek satır amaç) açıkça yazmıyor.

---

## 15. Kit kodundaki gizli ipuçları

| Yer | İpucu | Anlamı |
|---|---|---|
| `tools/run_cases.py:41-43` | Persona kimlik alanları: `date_of_birth`, `phone_last4`, **`postcode`, `birth_year`, `company_number`, `founded_year`** | Final brief'lerde **farklı güvenlik soruları** ve **şirket müşterileri** olabilir. `title` Mr/Ms olmayabilir. Kimlik adımı tamamen brief + openapi'den parametrik olmalı |
| `tools/score.py:33` | `not_reached` yanında **`no_contact`, `unreached`** | **Outcome adları değişebilir**. Outcome/reason tablosu brief'ten + openapi enum'larından okunmalı |
| `public-test-set.json` `faults` | `fail_first_n` | Finalde başka tool'larda da arıza olabilir; retry politikası brief'ten |
| `tools/run_cases.py` `STYLES` | 5 konuşma tarzı | Değerlendirme müşterilerinin davranışı |
| `tools/run_cases.py:66` | `--rerun-if-slow` "organisers" yorumu | Organizatörler yavaş model koşularını tekrarlıyor; yavaşlık senin hatan sayılmayabilir |
| Site | "other identity questions, another outcome table, another calendar" | Yukarıdakileri doğruluyor |
| Site | "The seven tool names stay the same. The fields and the allowed values can change." | Tool gövdeleri openapi'den üretilmeli |

---

## 16. τ^τ-bench makalesinden dersler

*"τ^τ-Bench: An Environment for End-To-End, Realistic Agent Construction" — Shi, Dhandhania, Narasimhan, Barres (Sierra, Princeton), arXiv 2609.04611, Eylül 2026.* Davet e-postasında "bu formatı esinleyen benchmark" olarak verildi.

### Bulgular

- En iyi kurulum (Claude Code + Opus 5 max): **%23.9**. Uzman referans: **%82.2**.
- **Bankacılık en zor alan: %5.9** (diğerleri %48–73). Sebep: bankacılık korpusu **2.969 atomik kural**, diğer alanlar 85–155.
- Builder'lar ortalama 48 dk (Codex) – 216 dk (Claude Code) sürdü.
- Kurulan ajanların %92'si **tek LLM tool döngüsü**. Fark yaratan şey etrafına konan **deterministik iskelet**: policy guard'lar (Codex %71, Claude Code %57), yapılandırılmış durum, tur başına tool kısıtlaması.
- Telekom deneyinde builder'a tek satırlık "**niyete göre yönlendir, tool çağrılarını gözden geçir**" ipucu vermek skoru **%31 → %67** yaptı.

### Builder'ların başarısızlık kalıpları ve bizim karşılığımız

| Kalıp | Makaledeki örnek | Bize ders |
|---|---|---|
| **Okumak yerine grep** | 1.700 dosyadan < 80'i açıldı; ajan "doğrulanmış bir kart kataloğum yok" dedi | Brief'i **baştan sona** oku. Ham materyalde kural her parçaya dağılmış olabilir |
| **Müşteriye sorulmuyor** | Açık soruları dosyaya yazıp hiç sormadılar; tek soruyla çözülecek değer eksik kaldı | Bizde insan yok → belirsizlikleri trace'e yaz, **brief'in kendi metnine dayanan** varsayılan seç, kanıt alıntısı tut |
| **Geçersiz kalmış kurallar** (App. D) | Sonraki tarihli mail kararı değiştirir, "bu eskisini geçersiz kılar" demez; hangisinin geçerli olduğu tarih/içerikten çıkarılmalı | Final'deki "**onu değiştiren memo**" tam bu. Spec çıkarımında **belgeleri tarih/öncelik sırasına koy**, her kural için "kaynak + geçersiz kılan kaynak" tut |
| **Miras kodu çalıştırmadan yeniden yazmak** | Hiçbir builder başlangıç kodunu çalıştırmadı | Base-agent'tan öğrenilecek çalışan parçaları (lifecycle `close_call`, pre-hook) koru |
| **Sessiz API hataları** (App. H) | Sayfalama devamı kaçırıldı; ajan 504 sonrası "kayıt olmadı" dedi ama olmuştu | Final API'sinde alan adı/değer/tarih formatı değişebilir → post-hook'ta **normalizasyon**; yazma işlemlerinde brief'in retry kuralı |
| **Bütçe kötü yönetimi** | Ya taştı ya yarısı kullanıldı | 30 dk sınırı + build süresi eşitlik bozucu → zaman bütçeli pipeline |
| **Testleri ajana uydurmak** | Başarısız assertion'ı gevşettiler, sahte mock'la test ettiler | **Test kehaneti üreticiden bağımsız**: beklenen sonuçları ayrı LLM çağrısıyla, brief'ten türet; assertion'ları asla ajanı geçirmek için değiştirme |
| **Hile denemeleri** | %17–42 koşuda grader/test verisi arandı | Bize ilgisiz ama kural: gizli brief'ten değer gömmek yasak |

### App. H — API arıza kataloğu (finalde benzerleri olabilir)

| Sınıf | Davranış |
|---|---|
| Değer dönüşümü | Bir alan sözleşmede yazmayan biçimde döner (ör. `"yes"/"no"` yerine boolean) |
| Alan adı değişmesi | Alan belgelenmemiş başka bir adla gelir |
| Tutar işareti | Tutarlar belgelenen konvansiyondan bağımsız sabit işaretle |
| Tarih tipi kayması | Takvim tarihi UTC gece yarısı datetime string olarak gelir |
| Async tamamlama | Senkron olması gereken yazma `202 Accepted` döner |
| Yazma sonrası timeout | İşlem kaydedilir, başarı yanıtı `504` olarak kaybolur |
| Rate limit | Belirli istek sırasında `429` |
| Sayfalama | Sayfalar kesilir, cursor kaybolur |
| Projeksiyon gecikmesi | Okuma, onaylanmış yazmanın gerisinde kalır |

### Judge örneği (App. K)

Judge'lar kural başına bir rubrikle çalışıyor ve katı. Örnek kural: "aynı açılış kalıbıyla birden fazla cevaba başlama". Bizim vakalarda `checks` listesi bunun karşılığı → ajanın **sabit cümleleri** brief'teki kelimelerle söylemesi ("You will receive a text message confirming this promise.", "I am sorry for your loss.", "Can you make this payment today?", "Do you confirm?") judge'ı kolay geçer.

---

## 17. Önerilen builder mimarisi

**İlke:** LLM'i **anlamak** için kullan (brief → spec), **inşa etmek** için deterministik kod kullan (spec → workflow). Ajanın zayıf modeline (experimental-5) kural ezberletme; kuralları **code node, routing ve hook'lara** taşı.

```
python3 builder/build.py <brief_path> <tool_api_path>
│
├─ 0. Trace başlat (start time, girdiler, git commit)
├─ 1. INGEST     brief dosyası/klasörü (md, txt, eml, csv, json…) + openapi.json → ham metin + tool şemaları
├─ 2. SPEC       LLM: brief → spec.json (JSON Schema ile doğrulanmış)
│                 · her alan için kanıt alıntısı + kaynak belge
│                 · belge sıralaması / geçersiz kılma çözümü
│                 · belirsizlik listesi + seçilen varsayılan
│               LLM (bağımsız geçiş): spec'i brief'e karşı denetle → düzelt
├─ 3. TOOLS      openapi → tool tanımları (alanlar, enum'lar, zorunlular) — saf kod
├─ 4. GENERATE   spec + tools → workflow JSON — şablon/kod
│                 · code node: takvim, deadline, floor, deneme sayaçları, ürün sırası, outcome/reason kararı
│                 · post-hook: header sil, normalize et, what_to_do_next ekle
│                 · prompt metinleri: spec'teki sabit cümleler + kısa görev talimatları
├─ 5. VALIDATE   statik kontroller (null yok, tools/pre_actions yerleşimi, always transition, {{ }} yok…) + dryRun
├─ 6. DEPLOY     freya.save_workflow(slot) → workflow_id, agent_id (yayınlanmış)
├─ 7. SMOKE      /chat/message ile 1–2 tur (açılış cümlesi, çağrı başlıyor mu)
├─ 8. SELF-TEST  spec'ten bağımsız LLM ile senaryolar + beklenen ledger üret → simülasyon → skorla
│                 (zaman kalırsa) başarısızlıklara göre spec/şablon parametresi düzelt → tekrar deploy
├─ 9. EXPORT     freya.export_workflow → out/<brief>.workflow.json
└─ 10. Trace bitir (end time, toplam süre, oluşturulanlar, testler, sonuç)
```

### Spec şeması (taslak alanlar)

- `bank_name`, `agent_identity_sentence`, `recording_notice`, `forbidden_words_before_verification`
- `call_variables` (isim, örnek, anlam), `addressing` (ör. title + last_name)
- `confirmation_phrases`, `max_identity_confirmation_asks`
- `identity`: sorular (sıra, alan adı → openapi `field` enum'u, format dönüşümü: tarih sırası DMY/MDY, son N hane…), `max_wrong_total`, `hint` davranışları, ret politikası
- `third_party`: callback penceresi (gün kuralı, saat aralığı, dahil/hariç), yeniden sorma sayısı, wrong number, deceased cümlesi
- `calendar`: iş günleri, tatil listesi, `next/this weekday` semantiği
- `bands[]`: `days_late` aralığı → deadline (N. iş günü) → deneme sayısı
- `floor_by_product_type`, ürün sözlü adları ("credit card ending {last4}")
- `negotiation`: ilk soru, deneme 1/2 tanımları, belirsiz dönem politikası, deadline sonrası politika, floor altı politika
- `hardship_triggers`, `hardship_flow`, `restructuring_triggers`, `human_flow`, `scam_reply`, `already_paid_reply`, `dispute_rules`
- `confirmation`: read-back biçimi, onay cümlesi, retry sayısı, kayıt sonrası cümle
- `multi_product`: bildirim, toplu "bugün" sorusu, sıra, tek read-back, not kuralları, transferde not yok
- `outcomes[]`: outcome, reason, tetikleyici kural, öncelik; multi-product kapanış kuralları
- `fixed_sentences[]`: brief'teki birebir cümleler
- `ambiguities[]`: soru, seçilen yorum, gerekçe

### Neden "agentic" değil de hibrit?

- Örnek builder (tam agentic) Brief A'da 10/10 yaptı ama gizli brief'te **public vakalar yok**, 30 dakikada 2 simülasyondan fazlası sığmaz, ve LLM'in her seferinde farklı workflow yazması 3/3 tutarlılığa zarar verir.
- Şablon + spec yaklaşımı **tekrarlanabilir**, hızlı (build süresi eşitlik bozucu), ve hataları builder geliştirilirken (bugün–yarın 17:00) public vakalarla düzeltilir.
- Agentic döngü, şablonun karşılamadığı bir brief yapısı için **yedek** olarak kalabilir (ör. bilinmeyen bir tool alanı ya da beklenmeyen kural ailesi).

### Zaman bütçesi (≤ 30 dk)

| Adım | Hedef |
|---|---|
| Ingest + spec (2 LLM geçişi) | ≤ 4 dk |
| Generate + validate + deploy + smoke | ≤ 2 dk |
| Self-test simülasyonu (≈10 vaka) | 3–11 dk |
| Bir düzeltme turu + ikinci simülasyon | ≤ 12 dk |
| Export + trace | < 1 dk |

Builder her adımda **kalan süreyi** kontrol etmeli; 25. dakikada son iyi sürümü export edip çıkmalı.

### Trace formatı (öneri)

```
RUN 2026-10-11T17:05:12Z  brief=briefs/final-1.md  tool_api=final-1.openapi.json  commit=9f2c1ab
17:05:13  MODEL claude-opus-5-5  purpose: extract spec from brief (pass 1)
17:06:40  MODEL claude-opus-5-5  purpose: audit spec against brief (pass 2)
17:06:58  FREYA save_workflow slot=final-1 -> workflow_id=… agent_id=… version=3
17:07:05  TEST  chat smoke: opening line ok
17:07:10  MODEL claude-sonnet-5-5  purpose: generate 10 self-test scenarios
17:15:44  TEST  simulation sim_… : 9/10 passed; failed: S07 (reason …)
…
END 2026-10-11T17:29:01Z  duration=23m49s  exported=out/final-1.workflow.json
```

---

## 18. Önerilen phone agent iskeleti

> Hipotez — platformda doğrulanacak. Her kutu deterministik sınırları gösterir.

```
init (code, passthrough)
  · now'dan takvim: bugün, iş günü mü, hafta sonu son iş günü, gelecek hafta son iş günü,
    callback penceresi (bugün/sonraki iş günü), band→deadline haritası
  · varsayılanlar: outcome="", reason="", sayaçlar=0
      │
opening (conversation)  on_enter tts: kimlik + kayıt cümlesi + "Am I speaking with {{title}} {{last_name}}?"
  · extraction: who_answered ∈ {customer, third_party, wrong_number, deceased, unclear, question}
  · tools: yok (ya da sadece close-intent bayrağı)
  · → customer → identity    → third_party → third_party    → wrong_number/deceased/unclear → closing
      │
third_party (conversation)  tools: schedule_callback (pre-hook: pencere kontrolü, dışarıdaysa reddet + what_to_do_next)
  · → callback ok → closing (not_reached/callback)   → iki kez pencere dışı → closing (no_callback)
      │
identity (conversation)  tools: verify_identity (pre-hook: tarih normalizasyonu → YYYY-MM-DD; post-hook: sonucu
                                 'passed/wrong/swapped/unreadable/locked' string'ine çevir + what_to_do_next)
  · → all_verified → fetch_account   → wrong_answers_left == 0 → closing (not_verified/failed)
  · → declined x2 → closing (not_verified/declined)
      │
fetch_account (api_request, passthrough)  pre_actions: get_account
  · post-hook / sonraki code node: ürün başına band, deadline, floor, sözlü ad, deneme sayısı → products_facts metni
      │
disclose (conversation)  tüm ürünleri bildir → "Can you make this payment today?" (multi: "all of them")
      │
negotiate (conversation)  ürün bazlı durum: current_product, attempt_n, agreed[]; tools: yok
  · extraction: customer_intent ∈ {accept, propose_date, propose_amount, too_much, refuse, vague, paid, dispute,
                                   restructure, hardship, human, scam_question, stop_talking}
  · tarih/tutar doğrulaması: code node (deadline, iş günü, floor) → sonuç metni prompt'a
  · → hardship/restructure/human → transfer akışı   → anlaşma tamam → confirm
      │
confirm (conversation)  tek read-back + "Do you confirm?"  tools: create_promise (post-hook: ok/fail + retry talimatı)
  · → hepsi kaydedildi/kalıcı hata → wrap_up
      │
wrap_up (conversation)  "You will receive a text message…" · add_note'lar (multi) · teşekkür + veda
      │
closing (end_call)   +  close_call: lifecycle end_of_call; outcome/reason code'un tuttuğu değişkenlerden bake
```

- **Outcome'u model değil kod belirlesin:** her karar noktası `final_outcome` / `final_reason` değişkenlerini yazar; lifecycle `close_call` bunları `{{final_outcome}}` ile bake eder (değişken boşsa model doldurur — yedek).
- Global node'lar: "AI/robot musun?", "dolandırıcılık mı?", "insan istiyorum" — `excluded_nodes` ile terminal node'ları hariç tut; kimlik öncesi bilgi sızdırmayacak sabit cümleler.
- Kimlik doğrulanmadan önceki her node'un prompt'unda: borç/ürün/tutar/"collections" kelimeleri yasak.
- Her tool'da `result.pop('header')` + `what_to_do_next`.
- Her conversation node'da `max_node_turns` + `max_turn_transition`.

---

## 19. Riskler ve açık sorular

| # | Soru / risk | Neden önemli | Nasıl çözülür |
|---|---|---|---|
| 1 | **Mock banka, gizli brief için practice session açabiliyor mu?** `POST /v1/practice/sessions` sadece public vaka id'leri mi kabul ediyor, yoksa özel ürün/kimlik verisi alabiliyor mu? | Gizli brief'te self-test'in ledger'la skorlanıp skorlanamayacağını belirler | Bugün `{"case": "custom", "products": …}` gibi gövdelerle dene; hata mesajını oku |
| 2 | Final tool API'si farklı bir sunucu/URL mi kullanacak? | Tool URL'leri openapi `servers`'tan alınmalı | Builder URL'yi openapi'den okusun |
| 3 | Lifecycle `close_call` code'da set edilen değişkenleri bake ediyor mu? | Deterministik outcome için kritik | Bir test çağrısında `function_call_start` olayına bak |
| 4 | Chat route ilk code node'u atlıyor | Smoke test yanıltıcı olabilir | Smoke'ta init node'a güvenme ya da init'i ikinci node yap |
| 5 | Extraction tarih ankrajı gerçek saat | `now` 2026-10-14 iken gerçek tarih farklı → "tomorrow" yanlış | Tarih yorumunu code'da `now` ile yap; extraction'a ham ifadeyi aldır |
| 6 | Ham brief'te çelişen kurallar | Yanlış sürüm → tüm vakalar kalır | Spec'te belge tarih sırası + "geçersiz kılar" çıkarımı + denetim geçişi |
| 7 | Farklı kimlik soruları (postcode, company_number…) | Kimlik adımı sabit kodlanırsa çöker | Kimlik tamamen spec'ten |
| 8 | Outcome isimleri değişik | Tüm ledger kontrolleri kalır | Outcome/reason tablosu spec + openapi enum'larından |
| 9 | `experimental-5` hızı | Yavaş turlar → müşteri kapatır | Kısa prompt'lar, `suppress_tool_results` gerektiğinde, az tool/node |
| 10 | Final penceresinde simülasyon limitleri | Self-test sayısı kısıtlı | Bugün–yarın public vakalarla şablonu olgunlaştır; finalde 1–2 simülasyon yeter |
| 11 | Workspace 200 workflow limiti | Builder çökebilir | Her zaman `save_workflow(slot)` |
| 12 | Repo git değil | Teslim commit hash istiyor | `git init`, `.gitignore` (env.sh, .freya-agents.json, .personas.json, results/, traces/ içinde key yok) |

---

## 20. Eylem planı

### Cumartesi (bugün)

1. `env.sh` kur (`FREYA_API_KEY`, `FREYA_BANK_TEAM_KEY`), `source env.sh`.
2. Baseline: `base-agent` → `run_cases.py --brief A` (13'te ~2 bekleniyor). Bir transkripti incele (`route_evaluated`, `function_call_start`).
3. Açık soru #1 ve #3'ü test et (mock özel session, lifecycle bake).
4. `git init` + `builder/` iskeleti (`build.py`, trace modülü, `spec_schema.json`).
5. Brief A+B için **elle** güçlü bir workflow şablonu kur (kickoff'un önerisi); public vakalarda 13/13'e yaklaş.

### Pazar sabah – 17:00 UTC

6. Şablonu spec'e parametrik bağla; spec çıkarımı + denetim geçişi.
7. **Sahte gizli brief'ler** üret ve dene: Brief A'yı (a) ham materyal biçimine çevir (mail + policy + değiştiren memo + kod listesi), (b) farklı kimlik soruları, farklı tatiller, farklı band'lar, yeniden adlandırılmış outcome'lar, (c) değiştirilmiş openapi alan/enum'ları. Builder bunları çökmeden kurabilmeli.
8. Farklı `now` günleriyle (Cuma, Pazartesi, tatil öncesi, ay sonu) ve `--style` seçenekleriyle `--reps 3` koşuları.
9. Zaman bütçesini ölç; 30 dk altında kal.
10. Son commit, hash'i not et, teslim e-postasındaki adımları oku.

### Final penceresi (17:00–20:00 UTC)

11. Her gizli brief için: `python3 builder/build.py <brief> <tool_api>` → trace + export.
12. Gerekirse **sadece bug fix** (brief'e özgü değer yok) → commit → yeniden çalıştır.
13. En iyi koşunun export'u + o koşunun trace'i + commit hash + komut → teslim. Hedef: 22:30 İstanbul'a kadar bitir.

---

## 21. Komut kopya kağıdı

```bash
# kurulum
cp env.example env.sh && $EDITOR env.sh && source env.sh

# örnek ajanı yükle ve test et
python3 -c "import freya,json; print(freya.save_workflow('base', json.load(open('examples/base-agent.json'))))"
python3 tools/run_cases.py <agent_id> --brief A
python3 tools/run_cases.py <agent_id> --brief B
python3 tools/run_cases.py <agent_id> --only PUB-06,PUB-07 --reps 3 --style in_words --out results/run.json

# örnek builder
pip install claude-agent-sdk
python3 examples/agent_builder.py briefs/brief-a.md
python3 examples/agent_builder.py briefs/brief-a.md briefs/brief-b.md

# dry-run doğrulama
python3 -c "import freya,json; print(freya.validate_workflow('x', json.load(open('out/brief-a.workflow.json'))))"

# transkript
python3 -c "import freya,json; print(json.dumps(freya.get_call('<call_id>')['messages'], indent=1))"

# tek ledger skorla
python3 tools/score.py PUB-01 <customer_id>

# export (teslim)
python3 -c "import freya; freya.export_workflow('<workflow_id>', 'final-1.workflow.json')"

# resmi final komutu (bizim builder'ımız)
python3 builder/build.py <brief_path> <tool_api_path>
```

Ortam değişkenleri: `FREYA_API_KEY`, `FREYA_API_URL` (varsayılan staging), `FREYA_BANK_TEAM_KEY` (≥16 karakter, her zaman aynı), opsiyonel `FREYA_WORKSPACE_ID`, `FREYA_SIM_CONCURRENCY` (5), `BUILDER_MODEL`, `FREYA_BANK_HOST`. **`FREYA_AGENT_PROVIDER` / `FREYA_AGENT_MODEL` değiştirme** (organizatörlere ait).

---

## 22. Kaynaklar

- Hackathon sayfası: https://www.freyavoice.ai/hackathon
- Kit v3: https://www.freyavoice.ai/hackathon/build-the-builder-v3.zip (yerel kopyayla birebir aynı, doğrulandı)
- Freya uygulaması / API key: https://staging-app.freyavoice.ai
- Platform dokümanları: https://docs.freyavoice.ai
- Kickoff kaydı: https://fathom.video/share/SCFTpw9gPFqxxEqk49PbM3HFjSros35m (otomatik okunamadı; notlar e-posta/özetten)
- Makale: Shi et al., *τ^τ-Bench: An Environment for End-To-End, Realistic Agent Construction*, arXiv 2609.04611 — https://arxiv.org/abs/2609.04611
- Destek: davet e-postasına cevap / yavuz@freya.voice
- Paydaşlar: Tunga Bayrak (Freya, eş lider, vizyon), Yavuz Öztürk (kurucu mühendis, mekanik/API/değerlendirme)
