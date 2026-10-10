# Hackathon'a yeniden hizalama (10 Ekim 2026, 15:10 UTC)

Kaynaklar:
- Hackathon sayfası (freyavoice.ai/hackathon, curl ile okundu).
- Kit v3: README, briefs, `openapi.json`, `public-test-set.json`, `freya.py`, `docs/api.md`, `workflow-format.md`, `tools-and-code.md`, `score.py`, `run_cases.py`, örnek builder ve skill.
- Takım rehberi `README-v2.md`.
- Staging uygulaması araştırma raporu.

## 1. Gerçek görev, bizim varsayımımızla yan yana

| Konu | Bizim varsaydığımız (6–9 Ekim) | Gerçek |
|---|---|---|
| Alan | Herhangi bir görev (banka, sigorta, kredi, TR) | Tek alan: borç tahsilatı. 7 sabit tool adı. Alanlar ve enum'lar brief'e göre değişebilir. |
| Freya çıktısı | Tek system prompt + agent config (Agents ürünü) | **Workflow JSON**: node'lar, transition'lar, code node'lar, pre/post hook'lar, lifecycle tool. `freya.export_workflow` ile teslim edilir. |
| Ajan modeli | Seçilebilir | **Sabit ve zayıf**: `experimental-5`. Kurallar prompt'ta değil; code, routing ve hook'larda yaşamalı. |
| Güvenlik | Yerel MutationGate (Python, bizim runtime'ımız) | Puanlanan runtime Freya. Koruma workflow'un içinde olmalı: deterministik routing, hook'lar, code. |
| Test | Kendi mock'umuz, paralel yüzlerce simülasyon | Workspace başına **aynı anda tek simülasyon**, 10 vaka 3–11 dk. Practice bankası **sadece PUB-01..13**'ü açar. Gizli brief'lerin practice vakası **yok**. |
| Seçim | GEPA-lite, halving, istatistik | Brief başına build **≤ 30 dk**, testler dahil. Optimizer döngüsü buna sığmaz. |
| Puan | Ortalama başarı | 2 gizli brief × 30 müşteri. Vaka 3 tekrarın **3'ünü de** geçerse sayılır. Eşitlikte önce söz tutarı, sonra build süresi. |
| Giriş noktası | `builder run -i bundle` | `python3 builder/build.py {brief_path} {tool_api_path}` |
| Teslim | `agent_spec.json`, manifest | Workflow export + run trace (başlangıç/bitiş, her model çağrısı, Freya id'leri, testler) + commit hash. İlk 10'un builder'ı yeniden koşturulur. |

Sonuç: altyapımızın çoğu yanlış hedefe kurulmuş. Kavramlar doğru, çıktı katmanı ve optimizasyon döngüsü yanlış.
- Doğru olan kavramlar: kanıtlı spec çıkarımı, deterministik koruma, oracle'a karşı test, kayıt / trace.
- Yanlış olanlar: prompt-blok adayları, yerel gate, mock simülatör, GEPA.

## 2. Repodan ne kalır, ne gider

| Parça | Karar | Neden |
|---|---|---|
| `llm/` (OpenRouter client, maliyet defteri, cache, bütçe) | **Kalır** | Spec çıkarımı ve denetim çağrıları için gerekli. Defter trace'e model çağrılarını yazar. |
| `ingest/policy_compiler` (kural id'leri, kanıt alıntısı) | **Kısmen** | Ham brief'te (mail + policy + değiştiren memo) kural kaynağı ve geçersiz kılma takibi gerekiyor. |
| `schema/` (`DomainSpec`, `PolicyRule`) | **Yeniden** | Yerine tahsilata özgü bir `CollectionsSpec` gelir: kimlik, bandlar, takvim, floor, outcome tablosu, sabit cümleler. |
| `testsynth/` + judge | **Kısmen** | Gizli brief'te practice vakası yok. Spec'ten beklenen ledger'lı senaryo üretmek faydalı. |
| Eski generic `adapters/mock` | **Dondur** | Yanlış mock. Yerine resmi semantiğe yakın yeni bir harness gelir (§3.3). |
| `adapters/freya/compile.py` | **Gider** | Agents config üretiyor. Workflow üretmiyor. |
| `adapters/freya/client.py` | **Gider** | Kitin `freya.py`'si resmi client. Onu kullan. |
| `runtime/mutation_gate`, `optimize/`, `select/`, `synth/candidate_generator` | **Dondur** | Puanlanan yolda yer almıyorlar. Silmeye gerek yok, build yolundan çıkar. |
| `manifest`, deney kaydı alışkanlığı | **Kalır** | Trace şartı bunu zaten istiyor. |

## 3. Hedef mimari: `builder/build.py`

### 3.0 Tasarım ilkeleri

- **Hedef, vaka düzeyinde 3/3.** Tek koşu ortalaması hedef değil. 2/3 geçen bir davranış final metriğinde sıfırdır. Self-test raporları ve onarım kararları vaka başına `k/3` üzerinden verilir.
- **Kurallar topolojide ve code'da yaşar.** Prompt yalnızca konuşma tonu ve birebir cümleler içindir.
- **Build deterministiktir.** Aynı spec ile aynı workflow üretilir. LLM yalnızca spec çıkarımında ve prompt metninde kullanılır.
- **Builder bağımlılıksızdır.** `builder/` stdlib ile çalışır ve OpenRouter'a urllib ile gider. Hakemlerin yeniden koşusunda kurulum sürprizi olmaz.

```
0. trace başlat (UTC zaman, girdiler, git commit)
1. INGEST    tek .md brief (temiz spec ya da ham materyal) + openapi.json
2. SPEC      LLM → CollectionsSpec (JSON Schema ile doğrulanır)
             · her kural: değer, kaynak, kaynak sırası, yürürlük tarihi, overrides[], kanıt alıntısı, güven
             · deterministik kontroller: kanıt alıntısı brief'te birebir var mı, zorunlu alanlar dolu mu,
               enum'lar openapi'de var mı, tatiller geçerli tarih mi
             · 2. model denetimi YALNIZCA şu durumlarda: ham materyal / çelişki / düşük güven / kontrol hatası
3. TOOLS     openapi → tool tanımları (url servers'tan, alanlar, enum'lar, zorunlular). Saf kod.
4. GENERATE  spec + tools → workflow JSON. Deterministik şablon.
             · init code node: now'dan takvim (iş günü, tatil, deneme 1 tarihi, band→deadline, callback penceresi)
             · ürün başına floor / deadline / sözlü ad (get_account sonrası code)
             · verify_identity pre-hook: tarih normalizasyonu. post-hook: durum stringi + what_to_do_next.
             · create_promise post-hook: ok/fail + "bir kez tekrar dene" talimatı
             · outcome/reason'ı code değişkenleri tutar. close_call bunları bake eder.
             · prompt metinleri: spec'teki birebir cümleler + kısa görev talimatları
5. VALIDATE  deterministik yapısal doğrulayıcı (§3.2) + dryRun
6. DEPLOY    freya.save_workflow(slot) → yayınlanmış
7. SMOKE     /chat/message: açılış cümlesi, kimlikten önce borç sızmıyor mu
8. SELF-TEST (zaman kalırsa) spec'ten üretilmiş senaryolar chat ile; brief A/B ise run_cases
9. EXPORT    submission/<brief>.workflow.json
10. trace bitir
```

### 3.1 CollectionsSpec: öncelik modeli merkezde

Her kural şu biçimde tutulur:

```
Rule {id, value, source, source_order, effective_date, overrides[], evidence_quote, confidence}
```

Ham brief'in asıl zorluğu, parser'ın bir kuralı bulamaması değil. Doğru görünen iki kuraldan hangisinin yürürlükte olduğunu seçmek. Yürürlükteki değer şöyle hesaplanır:
- Önce açık `overrides` bağı kullanılır.
- Bu yoksa en yeni `effective_date`, o da yoksa en yüksek `source_order` kazanır.
- Kazanan ve yenilenler trace'e yazılır.

### 3.2 Yapısal doğrulayıcı (LLM'siz, export'tan önce zorunlu)

- **Bütünlük:**
  - Null yok.
  - `tools` / `pre_actions` doğru node tipinde.
  - Passthrough node'lar `always` ile bitiyor.
  - Code'da `{{` / `}}` yok.
- **Graf:**
  - Tüm node'lar erişilebilir.
  - Boşa çıkan transition yok.
  - Her yoldan bir `end_call`'a ulaşılıyor.
  - Her conversation node'da `max_turn_transition` var.
  - Sonsuz retry döngüsü yok; sayaç sınırları spec limitlerini aşamaz.
- **Politika:**
  - Kimlik doğrulanmadan önceki node'larda bilgi açan tool yok (`get_account`, `create_promise`, `add_note`).
  - `create_promise` yalnızca onay node'unda.
  - `close_call` her iş sonu yolunda (lifecycle tool olarak tek sefer).
- **Kapsam:**
  - Yalnızca openapi'deki 7 tool, yalnızca openapi `servers` URL'si; dış servis yok.
  - Zorunlu argümanlar korunuyor.
  - Enum değerleri yalnızca openapi'den ya da spec'ten geliyor.
  - `agent_config` / model alanı gönderilmiyor.

### 3.3 Yerel harness (resmi semantiğe yakın)

| Katman | Gerçeğe yakınlık | Öncelik |
|---|---|---|
| Banka: 7 tool, kimlik durumu, ledger (promises, notes, callbacks, transfers), `fail_first_n` arıza enjeksiyonu | Birebir (openapi + `score.py`) | P0 |
| Skorlayıcı: `score.py` mantığı + vaka başına `k/3` raporu | Birebir | P0 |
| Workflow yorumlayıcı: code node'lar ve hook'lar gerçek Python olarak, deterministik transition'lar, `response_mapping`, sayaçlar | Birebir (deterministik kısım) | P1 |
| Diyalog / extraction / routing: `experimental-5` yerine bir LLM | Yaklaşık | P1 |
| Persona simülatörü: `run_cases.py`'nin PERSONA şablonu ve 5 stili | Yakın | P1 |

**Sınır:** Freya'daki ajan bizim harness'imize bağlanamaz. Container dışarıdan erişilebilir değil ve tool URL'leri openapi'deki sunucuya gidiyor. Bu yüzden harness, workflow'u **kendi yorumlayıcısında** koşar. Code ve routing hataları birebir yakalanır. Ajan modelinin davranışı ise yaklaşık kalır; gerçek doğrulama yine Freya'da PUB vakalarıyla yapılır.

### 3.4 Metamorfik test seti (gizli brief provası)

Brief A/B ve openapi programatik olarak mutasyona uğratılır. Builder **kod değişmeden** geçerli bir workflow üretmeli ve spec doğru değerleri yakalamalı:

- Kimlik: DOB + son 4 hane → postcode + doğum yılı; bireysel → şirket (`company_number`, `founded_year`).
- Outcome adı değişimi (`not_reached` → `no_contact`, `promise` → `commitment`), openapi enum'ları birlikte.
- Başka tatil listesi, başka bandlar (sınırlar, N. iş günü), deneme sayısı 2 → 3.
- Başka callback penceresi.
- A'nın ham materyal hali: mail + policy alıntısı + bir değeri değiştiren memo + kod listesi.

Her mutasyonun beklenen spec farkı bilindiği için, spec çıkarımı otomatik puanlanır.

### 3.5 Gizli brief kirlenmesine karşı koruma (`submission_guard.py`)

Gizli faz boyunca her commit'ten önce çalışır:
- Gizli brief'in hash'ini trace'e yazar.
- Kaynak ağaçta brief'ten uzun literal parçalar arar (≥ 20 karakterlik n-gram).
- Yeni eklenen tarih, outcome ve isim literallerini brief ile karşılaştırır.
- Freeze commit'ine göre diff özeti üretir.

`HOLIDAYS = ["2026-11-..."]` gibi diskalifiye edecek bir "fix" commit'lenmeden yakalanır.

### Neden tam agentic değil? Örnek builder (Claude Agent SDK) Brief A'da bir koşuda 10/10 yaptı. Ama:
- Gizli brief'te practice vakası yok.
- 30 dakikaya 1–2 simülasyon sığıyor.
- LLM'in her koşuda farklı workflow yazması 3/3 tutarlılığı bozuyor.
- Build süresi eşitlik bozucu.

Şablon ise bugün ve yarın public vakalarla olgunlaştırılır. Agentic onarım, şablonun karşılamadığı bir kural ailesi için yedek olarak kalır.

## 4. Gizli brief'lerde neler değişebilir (kod ve site ipuçları)

- **Kimlik soruları:** `run_cases.py` `postcode`, `birth_year`, `company_number` ve `founded_year` alanlarını tanıyor. Şirket müşterisi gelebilir; `title` Mr/Ms olmayabilir.
- **Outcome adları:** `score.py` `not_reached` ile birlikte `no_contact` ve `unreached` adlarını da tanıyor. Outcome tablosu brief'ten ve openapi enum'larından okunmalı.
- **Spec'ten okunacaklar:** başka tatiller, başka bandlar, başka deneme sayıları, başka callback penceresi.
- **Tool alanları ve enum'lar:** openapi'den türetilir, asla sabit kodlanmaz.
- **Ham materyal brief:** mail + policy alıntısı + onu değiştiren memo + kod listesi. Geçersiz kılma çözümü şart.
- **Müşteri davranışı:** müşteriler parça parça, düzelterek, laf kalabalığıyla, soru sorarak ya da yazıyla konuşur. Tutar ve tarih ayrıştırması sağlam olmalı.
- **Takvim:** birden çok takvim günü test edilecek. Cuma, tatil komşuluğu ve ay sonu `now` değerleriyle test et.

## 5. Önce doğrulanacak platform bilinmezleri (ucuz deneyler)

1. Lifecycle `close_call`, code node'un yazdığı `{{final_outcome}}` değişkenini bake ediyor mu?
2. Passthrough code node simülasyonda `initial_node` olarak çalışıyor mu? (Chat route atlıyor olabilir.)
3. `experimental-5` gecikmesi: tur başına süre, node ve tool sayısının etkisi.
4. Extraction `date` tipi gerçek saati baz alıyor, `now`'u değil. Tarihleri code'da `now` ile yorumla.
5. Gizli brief'in openapi `servers`'ı aynı mock mu? Builder URL'yi her durumda openapi'den okur.

## 5.1 Kapılar: compiler'dan önce kapanmalı (Cmt 15–19)

1. Kitin minimal builder'ı / base-agent gerçek bir Freya workflow'u oluşturuyor ve yayınlıyor.
2. Export edilen workflow yeniden import edilebiliyor (dry-run + create).
3. Chat üzerinden bir tool çağrısı transkriptte görülüyor.
4. Bir code node değişkeni bir transition'a ve lifecycle `close_call`'a taşınıyor.
5. Elle yapılmış bir baseline en az bir public vakayı uçtan uca geçiyor.

## 5.2 Takip edilen KPI'lar (özellik sayısı değil)

| KPI | Ölçüm |
|---|---|
| Public 3/3 | `run_cases.py --reps 3`, 13 vakada 3/3 geçen sayısı |
| Metamorfik geçiş | Mutasyonlarda çökmeden üretim + spec doğruluğu + harness skoru |
| Build süresi | Trace'teki başlangıç → bitiş |
| Tekrarlanabilirlik | Aynı brief'le iki build arasında workflow farkı (prompt metinleri hariç) |

## 6. Ön koşullar (kullanıcıda)

- Ortam değişkenleri, ortam ayarlarından eklenir; sohbete yapıştırılmaz:
  - `FREYA_API_KEY`: workspace → API keys.
  - `FREYA_BANK_TEAM_KEY`: ≥16 karakter, takımın uydurduğu sabit bir değer.
- Hakemlerin commit'i klonlayabilmesi için repo erişilebilir olmalı (public ya da erişim verilmiş).
- Builder modeli: OpenRouter (mevcut). Claude Agent SDK için `ANTHROPIC_API_KEY` yok; gerekmiyor.

## 7. Takvim (UTC)

| Zaman | İş | Kapı |
|---|---|---|
| Cmt 15–19 | Anahtarlar. Base-agent baseline. Platform deneyleri (§5). `builder/` iskeleti, trace, takvim, yapısal doğrulayıcı, banka harness'i. | §5.1'deki 5 kapı |
| Cmt 19–Paz 03 | Şablon generator (Brief A), code node'lar ve hook'lar. PUB A vakalarında iterasyon. | Brief A ≥ 8/10 |
| Paz 03–09 | Brief B (çok ürün). Spec çıkarımı + denetim. Şablonun spec'e bağlanması. | A+B 13 vakada yüksek skor, spec'ten üretim |
| Paz 09–14 | Metamorfik set (§3.4). Stiller ve farklı `now`. `--reps 3`. Süre ölçümü. `submission_guard`. | Mutasyonlarda çökmeden üretim, ≤ 30 dk |
| Paz 14–16 | Dondur, temiz ortam testi, commit. | Teslim hazır |
| Paz 17–20 | Gizli brief'ler: koş, sadece bug fix, export, trace, form. | Teslim |
