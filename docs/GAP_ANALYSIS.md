# Eksiklik analizi — memo, PLAN ve sistemin gerçek durumu (9 Ekim 2026)

**Kapsam:** `freya_build_the_builder_research.md` (memo), `docs/PLAN.md`, `docs/CORRECTNESS_REVIEW.md`,
`docs/RUNBOOK.md`, `docs/RULES_MAPPING.md`, `README.md`, bütün `src/` (≈9,4k satır), `tests/` (≈2,4k satır),
`scripts/`, `examples/` ve `vendor/freya/openapi.json`.

**Kanıt düzeyi etiketleri** (her bulguda belirtildi):

| Etiket | Anlamı |
|---|---|
| **[ÇALIŞTIRDIM]** | Kodu/komutu bu ortamda çalıştırıp sonucu ölçtüm |
| **[SPEC]** | `vendor/freya/openapi.json` (v2.0.0) ile karşılaştırdım |
| **[KOD]** | Kodu okuyarak çıkardım (çalıştırmadım) |
| **[TAHMİN]** | Sayısal tahmin; ölçülmedi |

---

## 0. Özet

**Genel hüküm.** İskelet gerçekten sağlam: 125 test geçiyor, `ruff` ve `mypy` temiz, bütçe sınırı, fallback
zinciri, dondurulmuş validation, holdout, maliyet defteri ve regresyon testleri yerinde. Memonun *mimari* tezi
(derleyici + test üretici + optimizer) kodda büyük ölçüde karşılık buluyor.

Ama sistem şu an **"çalışıyor"dan çok "çalışıyor gibi görünüyor"** noktasında. Dört yapısal sorun var:

1. **Değerlendirilen ajan ≠ teslim edilen ajan.** Yerel seçim, bizim koduyla çalışan `ReferenceAgent` (+ deterministik
   MutationGate + 4 ek araç) üzerinde yapılıyor. Freya'ya giden şey yalnızca *prompt + tool tanımları*. Seçimin
   kanıtı, teslim edilen şeye aktarılmıyor (§A1).
2. **Freya yolu hiç canlı koşmadı ve testleri döngüsel.** Adaptör, spec'te olmayan mesaj şekillerini tahmin ediyor;
   testteki sahte Freya o tahmini üretiyor (§A2). Ayrıca spec'in sunduğu iki kritik özellik (persona LLM override,
   unit-test assertion) kullanılmıyor (§A2, §A3).
3. **Kanıtlanmış bir kazanç yok.** Optimizer canlı koşuda tek bir mutasyon kabul etmedi; ablation çalışmadı;
   kabul kuralı mevcut haliyle *hiçbir şey değiştirmeyen* bir mutasyonu %38–43 olasılıkla kabul ediyor (§A8).
4. **İki somut performans/doğruluk hatası:** LLM istemcisi bütün çağrıları kilitle seri hale getiriyor (§A4) ve
   dışa aktarılan Freya config'i spec'e göre geçersiz (§A5).

### En kritik 10 bulgu (öncelik sırasıyla)

| # | Bulgu | Etki | Efor | Kanıt |
|---|---|---|---|---|
| A4 | `LLMClient.complete()` HTTP çağrısını da kilit altında yapıyor → `concurrency=4` fiilen 1 | Her şey ~4× yavaş; wall-clock bütçesi erken biter | **30 dk** | [ÇALIŞTIRDIM] 8 çağrı×0,5 sn → 4,01 sn |
| A5 | `freya_agent_config.json` içinde `"model": null`, `complianceConfig` yok; iki farklı config dosyası; `maximumDuration=300` gömülü | Teslim dosyası API'ye gönderilemeyebilir | 1 saat | [ÇALIŞTIRDIM] + [SPEC] |
| A6 | Hiçbir derleyici `REQUIRES_BEFORE` kenarı üretmiyor → gate'in önkoşul kontrolü ölü, `depth()` hep 1 | Kimlik doğrulama önkoşulu zorlanmıyor | 1–2 saat | [ÇALIŞTIRDIM] 4 bundle'da 0 kenar |
| A8 | Boş mutasyon %38–43 kabul ediliyor; varsayılan 16 senaryoda 9/5/2 bölünme | Optimizer gürültüye uyuyor; holdout anlamsız | 1 saat | [ÇALIŞTIRDIM] Monte Carlo |
| A7 | 6 "mimari" aday birbirine %93–99 benzer; C1≡C6; sıralama etkisiz | Arama gerçekte arama değil | 2–4 saat | [ÇALIŞTIRDIM] |
| A1 | Gate/`think`/`search_knowledge`/`request_action_confirmation` Freya'da yok ama prompt'larda atıf var | Teslim edilen ajan ölçülenden farklı; var olmayan araçları çağırabilir | 2 saat | [KOD] |
| A2 | Freya mesaj şekli tahmin; testler döngüsel; `assertionResults`, `successCriteriaResult` okunmuyor | Tool-call tabanlı değerlendirme Freya'da hepsini düşürebilir | 2–3 saat | [SPEC] + [KOD] |
| A3 | Her senaryo×deneme için ayrı simülasyon, eşzamanlılık 1, `personaLlmOverride` yok; chat API + yerel simülatör alternatifi yok | Canlı koşu çok yavaş; simülatör çeşitliliği iddiası boş | 4–6 saat | [SPEC] + [KOD] |
| B2 | Ablation (Q1–Q7), Spearman, "çekirdek hazır" tanımının 4. maddesi doğrulanmadı | Sistemin B0'dan iyi olduğuna dair sayı yok | yarım gün + ~$10 | [KOD]/docs |
| B3 | PLAN'da ✅ olan 7 bileşen pipeline'a bağlı değil (adversarial, OPRO, doc_shaper, toggle_fewshot, …) | Plan–gerçek farkı | karar | [ÇALIŞTIRDIM] ölü-kod taraması |

---

## 1. Yöntem

Ne yaptım (hepsi tekrarlanabilir; komutlar §8'de):

* Memonun 4170 satırının tamamı, PLAN'ın 409 satırı ve diğer dokümanlar okundu.
* `src/` içindeki her modül okundu; `uv sync`, `pytest` (125 geçti), `ruff check`, `ruff format --check`, `mypy` çalıştırıldı.
* `vendor/freya/openapi.json` programatik olarak çözümlenip adaptör/compile kodundaki her alan karşılaştırıldı.
* Örnek bundle'larda IR'ı, adayları ve FAST-mod çıktısını gerçekten ürettim; prompt benzerliklerini, kenar sayılarını ölçtüm.
* LLM istemcisinin eşzamanlılığını sahte bir yavaş sağlayıcıyla ölçtüm; kabul kuralının yanlış-kabul oranını Monte Carlo ile hesapladım.
* `docs.freyavoice.ai` rehber sayfalarını (agents, integrations, knowledgebase, best-practices, iterating-agents) çektim.

**Yapamadığım:** Gerçek bir `FREYA_API_KEY`/`OPENROUTER_API_KEY` olmadığı için canlı LLM veya Freya koşusu yapmadım.
Dolayısıyla "canlı davranış" iddiaları (ör. gerçek ajan başarım oranları) bu rapordan **çıkarılamaz**.

---

## 2. Güçlü yönler (korunmalı)

* **Disiplin:** değişmez ilkeler (PLAN §1) kodda gerçekten uygulanıyor: bütçe her döngüde kontrol ediliyor,
  `A` fallback'i her zaman üretiliyor, audit zinciri `A`'ya düşüyor, pipeline her aşamada istisna yakalayıp export ediyor.
* **Veri sızıntısı koruması:** train/validation/holdout ayrımı; regresyon senaryoları yalnızca TRAIN'den türetiliyor
  (`failure/regression.py:35-36`); holdout sadece şampiyon + fallback için bir kez koşuyor.
* **Oracle kalitesi:** referans eylemler *çalıştırılarak* beklenen durum türetiliyor; ikinci bir JUDGE çağrısıyla denetleniyor;
  LLM-backend'li domainlerde kararlılık kontrolü var.
* **MutationGate'in yerel sürümü** (onay tek kullanımlık, argümana bağlı, yeni bilgi içeren "evet" reddediliyor, kimlik
  korunumu) canlı koşulardan çıkan düzeltmelerle (PLAN §9.2) olgunlaşmış.
* **Spec uyumu çabası:** adaptör vendor edilmiş OpenAPI'ye göre yazılmış; yol, zarf (`success/data.result`) ve çoğu alan adı doğru.
* **İstatistik altyapısı** (kümeli SE, eşleştirilmiş fark, Beta alt sınırı, pass^k, güç hesabı) doğru ve testli.
* Gizli anahtar sızıntısı yok; `.env`, `.builder/` ignore'da; `uv.lock` sabit.

---

## 3. A — Kritik bulgular

### A1. Değerlendirilen ajan ≠ teslim edilen ajan  `[KOD]`

`ReferenceAgent` (`runtime/reference_agent.py`) yerel simülasyonda şunları ekliyor; **hiçbiri Freya config'ine derlenmiyor**
(`adapters/freya/compile.py:84-113` yalnızca bundle'daki araçları yazıyor):

| Yerel ajan | Freya'da karşılığı |
|---|---|
| Deterministik MutationGate (`check_mutation`) | **Yok.** `preprocess_guard_code` yazılmış ama `factory.py:41-46` `preprocess_language` vermiyor → hiç devreye girmiyor |
| `request_action_confirmation` aracı | Yok (yalnızca metin protokolü: `tool_prompt.py`) |
| `think` aracı (C2) | **Yok**, ama C2 prompt'u "`think` aracını kullan" diyor (`prompt_blocks.py:270-276`) |
| `search_knowledge` aracı | Yok, ama retrieval prompt'u ona atıf yapıyor (`prompt_blocks.py:152-158`) |
| `end_conversation` aracı | Yok; completion prompt'u "konuşmayı bitir" diyor (Freya'nın yerleşik sonlandırma mekanizması olabilir — VERIFY) |
| `context_cleaner`, `max_steps` | Yok |
| `blocked_by_safeguard` geri bildirim döngüsü | Yok; `mutation_guard` bloğu bu hata mesajını anlatıyor (`prompt_blocks.py:172`) |

Sonuç: README bunu kabul ediyor ("local deterministic gate is not installed on Freya") ama PLAN §9 ve rapor, seçimi gate'li
ajan üzerinden yapıyor. Freya tarafında sadece **prompt metni ve tool açıklamaları** aktarılıyor; bu metinde var olmayan
araçlara atıf, ajanı halüsinasyonlu araç çağrısına itebilir.

**Öneri:**
1. Dışa aktarma için ayrı bir `render_for_platform(spec, platform)` ekle: var olmayan araçlara/hata mesajlarına atıf içeren cümleleri çıkar (veya platforma özel metinle değiştir).
2. Freya adaptörü seçimde kullanılacaksa (§A3) *aynı* prompt/araç yüzeyi değerlendirilmeli; yerel `ReferenceAgent` yalnızca ön eleme için kullanılmalı.
3. `factory.py`'de `preprocess_language` açılabilir yapılsın ve smoke testte `pre_process_code` reddi doğrulansın (RULES_MAPPING #17).
4. Rapora "yerel skor ≠ Freya skoru" uyarısı ve her ikisinin karşılaştırması eklensin.

### A2. Freya trace ayrıştırması spec'e dayanmıyor; testler döngüsel; platform-yerel sonuçlar okunmuyor  `[SPEC][KOD]`

* Spec'teki `GET /call/{callId}` → `messages[]` öğesi yalnızca şunları tanımlıyor: `role`, `message`, `duration`,
  `secondsFromStart`, `timestamp`, `messageId`, `was_blocked/wasBlocked`, `blocked_content/blockedContent`.
  **`toolCalls`/`tool_calls` alanı spec'te hiç yok** (spec metninde 0 geçiş).
* `adapters/freya/adapter.py:195-248` (`messages_to_events`) ise `toolCalls`, `tool_calls`, `tool_call_result`, `function_call` gibi
  *tahmin edilmiş* şekilleri ayrıştırıyor. `tests/unit/test_freya_compile.py` içindeki `fake_freya()` tam da bu tahmini üretiyor
  → test, tahminin kendisini doğruluyor (**döngüsel**).
* Tool-call görünmüyorsa: `evaluate_tool_calls` (beklenen çağrı), `evaluate_policy` ve yazma tespiti boş trace üzerinde çalışır →
  yazma bekleyen her senaryo **başarısız** (ve `false_refusal` olarak etiketlenir). Seçim ve optimizer bütün Freya koşusunda yanlış sinyal alır.
* Spec'te zaten var olan ve **kullanılmayan** sonuçlar: çağrı nesnesinde `successCriteriaResult`, `assertionResults`,
  `assertionsSummary`, `problems`, `analysis`, `cost`, `duration`. Adaptör yalnızca `assertionsSummary`'yi okuyor ve onu hiçbir şey
  doldurmuyor (simülasyona `assertions` verilmiyor).
* `compile_assertion` (unit-test `tool_call` assertion'ı) **hiçbir yerden çağrılmıyor** [ÇALIŞTIRDIM: ölü-kod taraması];
  `create_unit_test` endpoint'i tanımlı ama sarmalayıcısı yok. PLAN §0'daki "iki rejim için de evaluator" sözünün Freya-native
  tool-call yarısı bağlanmamış.
* `was_blocked/blocked_content` (guardrail engelleri) hiç izlenmiyor; guardrail'in yanlış-pozitif yaptığı yer görünmez.

**Öneri (sıra önemli):**
1. Canlı smoke'ta **ham** `GET /call/{id}` JSON'unu her trace'e `platform_result["raw"]` olarak kaydet (şimdi yok). İlk koşudan sonra ayrıştırıcıyı gerçek şekle göre yaz.
2. Tool-call doğrulamasını mesaj ayrıştırmasından bağımsız yap: senaryo başına unit test oluştur (`POST /unit-tests`, `tool_call` assertion), `POST /simulations` gövdesinde `assertions: [ids]` ver, sonucu `assertionResults`/`assertionsSummary`'den oku.
3. `successCriteriaResult`'u judge çıktısı olarak kullan (platform_verdict ile birlikte), kendi judge'ınla çapraz kontrol et.
4. Test fixture'ını gerçek bir kayıtlı yanıttan üret (smoke çıktısı), tahminden değil.

### A3. Freya simülasyon yolu: yavaş, tek akışlı ve simülatör çeşitliliği yok  `[SPEC][KOD]`

* `run_simulation` **bir senaryo × bir deneme** için tam bir simülasyon kuruyor (`adapter.py:177-186`): persona oluştur → simülasyon yarat/başlat →
  5 sn aralıkla poll → iterasyon → persona-runs → call. `capabilities().max_concurrency=1` (`adapter.py:69`), yani **sıralı**.
  Her (aday, senaryo, deneme) bir simülasyon; varsayılan `--max-simulations 200` → **200'e kadar ardışık simülasyon**.
  [TAHMİN] Her biri (kurulum + 5 sn poll aralığı + sohbet süresi) en az birkaç on saniye → saatler.
  Varsayılan duvar saati 3600 sn (`cli.py:55`), %35'i final için ayrılıyor.
* Spec `POST /simulations` içinde **`personaLlmOverride`, `assistantLlmOverride`, `analysisLlmOverride`, `routingLlmOverride`,
  `extractionLlmOverride`, `maxRetries`, `customerVariablesMap`** alanlarını sunuyor. Adaptör bunların hiçbirini kullanmıyor; README
  "persona LLM API'si doğrulanmadı" diyor ama alan spec'te **var**. Memo §59 ("en az iki persona LLM'i") bu yüzden yalnızca
  *prompt'a bir cümle eklenerek* taklit ediliyor (`adapter.py:117-124`) — bu simülatör çeşitliliği değil.
* Spec'e göre simülasyon `settings.maxRetries` varsayılanı **3**, `maxCallDurationSeconds` varsayılanı **240**. Retry'ın ne yeniden denediği
  belirsiz; başarısız bir koşuyu sessizce yeniden çalıştırıyorsa ölçümü bozar. Açıkça `0` verilmeli ve smoke'ta davranış doğrulanmalı.
* `DONE_STATUSES = {"completed", "paused"}` (`client.py:49`): `paused` "bitti" sayılıyor; duraklatılmış bir simülasyonun sonuçları eksik olabilir.
* **Kaçırılan alternatif:** `POST /chat/message` (yalnızca `message`, `agentId`, `callId`, `versionId`, `fromMessageId`, `customerVariables`) yanıtı `response, callId, messageId, endedByAgent`.
  Bu yol ile *kendi* `UserSimulator`'ımızı (istediğimiz model, istediğimiz çeşitlilik, kendi eşzamanlılığımız) Freya ajanına bağlayıp
  tool çağrılarını sonradan `GET /call/{callId}` ile okuyabiliriz. Ayrıca `fromMessageId` ile **fork** mümkün — adaptör şu an
  `supports_fork=False` ve `fork_probe` Freya'da hiç çalışmıyor. `chat_message`/`chat_end` sarmalayıcıları sadece smoke script'te kullanılıyor.

**Öneri:** Smoke sonrası karar ver: (a) `FreyaChatAdapter` (yerel kullanıcı simülatörü + Freya ajanı + yerel evaluator; hızlı, kontrollü, fork'lu) mi, (b) Freya simülasyonları mı (resmi değerlendiriciye daha yakın).
İkisini yan yana tutmak ideal; en azından `personaLlmOverride`'ı ve toplu `selectedPersonaIds` + `runsPerPersona` kullanımını ekle (tek simülasyonda birden çok persona).

### A4. LLM istemcisi bütün çağrıları seri hale getiriyor  `[ÇALIŞTIRDIM][KOD]`

`llm/client.py:48-51`:

```python
def complete(self, request):
    with self._budget_lock:          # RLock — HTTP çağrısı da bu kilidin içinde
        return self._complete(request)   # ... self.provider.complete(model, request) satır 85
```

Deney (`scratchpad/lock_test.py`): 0,5 sn gecikmeli sahte sağlayıcı, 4 thread, 8 çağrı → **4,01 sn** (ideal paralel ≈1,0 sn).
Yani `Simulator`'ın `ThreadPoolExecutor(concurrency=4)` (eval/simulator.py:41,79) mock/tau yolunda **hiçbir hız kazandırmıyor**;
judge, kullanıcı simülatörü ve LLM tool-backend çağrılarının hepsi sıraya giriyor.

[TAHMİN] Bir simülasyon ≈ 8 tur × (kullanıcı + ajan + backend) ≈ 30 çağrı × 2–4 sn ≈ 1–2 dk. 200 simülasyon → 3–6 saat seri; varsayılan 3600 sn
bütçe 30–60 simülasyonda biter.

**Düzeltme (≈30 dk):** kilidi yalnızca "bütçe kontrolü + ön rezervasyon" ve "kayıt/ücretlendirme" adımlarında tut; `provider.complete` kilidin dışında olsun.
Yarış korunumu için çağrı öncesi tahmini maliyeti rezerve et, sonrasında gerçeğe düzelt. `tests/unit/test_llm.py`'ye yavaş sağlayıcılı bir paralellik testi ekle.

### A5. Dışa aktarılan Freya config'i geçersiz ve çelişkili  `[ÇALIŞTIRDIM][SPEC]`

FAST modda üretilen `freya_agent_config.json` (insurance bundle):

```
keys: llmConfig, guardrailsConfig, sttConfig, ttsConfig
llmConfig.provider = "openai"   llmConfig.model = null
```

* Spec: `llmConfig.model` zorunlu string (minLength 1); `config` altında `llmConfig, ttsConfig, sttConfig, complianceConfig` zorunlu. `complianceConfig` yok;
  metin kanallı görevlerde `ttsConfig/sttConfig` de eksik olacak [KOD]. Neden: `pipeline._export` (`pipeline.py:274`) `compile_agent`'ı **base_config olmadan** çağırıyor.
* İki ayrı config üretiliyor: `out/freya_agent_config.json` (base yok, geçersiz) ve `out/platform/freya_agent_config.json` (adaptör; varsayılan config + `maximumDuration=300`).
  Hangisinin "teslim" olduğu belirsiz.
* `adapter.compile` her zaman `callTimeoutSettings.maximumDuration = max_call_seconds (300)` yazıyor (`compile.py:191-194`, `adapter.py:44`). Spec'te varsayılan **600**.
  Builder'ın *simülasyon süresi* ayarı, teslim edilen ajanın *gerçek çağrı süresini* 300 sn'ye kırpabilir.
* `default_guardrails` her domaine `no_full_pan` regex'i (`\b(?:\d[ -]?){13,19}\b`, `regenerate`) ekliyor (`compile.py:116-128`). Bu **kart/banka'ya özgü** (PLAN ilke 9 ihlali) ve 13+ haneli
  poliçe/talep/takip numaralarını söylemeyi engelleyip `regenerate` döngüsüne sokabilir; `was_blocked` izlenmediği için fark edilmez.
* `model` boşken `provider="openai"` varsayımı sabit.

**Öneri:** tek bir export yolu; `base_config` yoksa hata ver (ya da `GET /agents/defaults`'tan al); `model` yoksa `--agent-model` iste; `maximumDuration`'ı yalnızca simülasyon ayarına taşı veya submission'da spec varsayılanını koru;
`no_full_pan`'ı yalnızca görev/policy "kart numarası"ndan söz ediyorsa ekle (ya da kaldır); çıktıyı `jsonschema` ile spec'e karşı doğrula (CI'da `vendor/freya/openapi.json`'dan).

### A6. Önkoşul (`REQUIRES_BEFORE`) kenarı hiçbir yerde üretilmiyor  `[ÇALIŞTIRDIM]`

Dört örnek bundle'da (`retail_bank`, `insurance_claims`, `loan_deferral`, `tr_card_limit`):

```
retail_bank     edges: {'reads-produced-data-for': 7}  depth: 1  tools with prerequisites: []
insurance       edges: {'reads-produced-data-for': 4}  depth: 1  tools with prerequisites: []
loan_deferral   edges: {}                              depth: 1  tools with prerequisites: []
tr_card_limit   edges: {}                              depth: 1  tools with prerequisites: []
```

Doğrulama aracı (`verify_identity`, `verify_policyholder`, `verify_borrower`) var; yine de hiçbir yazma aracı ona bağlı değil.
Neden: düzeltme turunda (`CORRECTNESS_REVIEW`) LLM'in `must_run_after` çıktısı *tavsiye niteliğinde* `FEEDS` kenarına indirildi (`tool_compiler.py:352-361`), ve kimse onu `REQUIRES_BEFORE`'a terfi ettirmiyor.
Sonuçlar:
* `check_mutation` önkoşul kontrolü (`mutation_gate.py:355-371`) **her zaman geçiyor** — "önce kimlik doğrula" deterministik olarak zorlanmıyor.
* `ToolGraph.depth()` hep 1 → mimari seçicideki `tool_depth >= 3 → PLANNER_EXECUTOR` dalı hiç tetiklenmiyor (`architecture_selector.py:47`).
* `tool_use_policy` bloğundaki "only after X" ifadesi hiç yazılmıyor (`prompt_blocks.py:111-125`).

**Öneri:** deterministik terfi kuralı: policy bölümü `_AUTH` seviyesinde (kimlik doğrulama) ve `affected_tools` içinde yazma aracı varsa, ve ilgili okuma aracı adında `verif|auth|kimlik|dogrula` geçiyorsa `REQUIRES_BEFORE` üret. LLM önerisi ancak policy metninden birebir alıntı kanıtı varsa terfi etsin (kanıtı kenarda sakla).
Birim test: 4 bundle'da en az bir `REQUIRES_BEFORE` kenarı ve `depth ≥ 2`.

### A7. Aday çeşitliliği kozmetik (memo ilke 7 ihlali)  `[ÇALIŞTIRDIM]`

4 bundle üzerinde `generate_candidates(n=6)`:

* Satır-düzeyi benzerlik (difflib): **C2↔C3 %98,8–99,0, C1↔C5 %98,5–98,7, C3↔C6 %98,1–98,4, C4↔C6 %93,3–94,5.**
* **C1 ve C6'nın bütün bölüm metinleri birebir aynı**; fark yalnızca C1'in 3 few-shot'ı olması. "Retrieval-first" aday, retrieval modunu da değiştirmiyor (`_retrieval_mode(d)` hepsinde aynı).
* **Sıra değişikliği etkisiz:** `AgentSpec.render_system_prompt` bölümleri her zaman kanonik `PROMPT_BLOCKS` sırasıyla yazıyor (`schema/agent.py:97-103`). `POLICY_FIRST` ve `RETRIEVAL_FIRST` için `candidate_generator.py:127-148` içindeki yeniden sıralama **no-op**.
* C3 ("planner/executor") ve C4 ("finite-state") yalnızca tek bir ek paragraf (`planning`/`workflow`); çalışma zamanında yapısal fark yok. C2 yalnızca yerel `think` aracı (Freya'da yok, §A1).
* Tüm adaylar aynı model/sıcaklık (B0 hariç), aynı gate modu, aynı tool açıklamaları.

Memo §3.15: "Bad population: A: 'You are a helpful agent…' B: 'You are an expert…'". Mevcut popülasyon buna çok yakın. Başarı farkı SE ≈ 0,1–0,2 iken bu adayları ayırt etmek mümkün değil.

**Öneri:** gerçekten ayrışan eksenler ekle: (i) gate açık/kapalı + prompt-only, (ii) kısa/uzun prompt (A vs C*), (iii) few-shot açık/kapalı (`toggle_fewshot` zaten yazılı, kullanılmıyor), (iv) sıcaklık 0 / 0,3, (v) `tool_description_overrides` (format kuralları), (vi) Freya'ya özgü yüzeyler: `postHistoryPrompt` (spec'te var, her tur sonrası hatırlatma — gate'in prompt karşılığı), `tools[].config.maxRetries=0` yazma araçlarında, `post_process_code` ile sonuç normalizasyonu.
`render_system_prompt`'un sırayı korumasını sağla veya sıralamayı kasıtlı bir mutasyon ekseni yap.

### A8. İstatistiksel güç ve kabul kuralı  `[ÇALIŞTIRDIM]`

Varsayılan bölünme (`--scenarios 16`, validation 0,34, holdout 0,1): **train 9 / validation 5 / holdout 2.** `--scenarios 24` → 14/8/2; `30` → 17/10/3.

* **Boş mutasyon kabulü:** `AcceptancePolicy` (`min_net_wins=1`, `max_sign_p=0.5`, `acceptance.py:12-15`). Ebeveynle *aynı* davranışlı bir çocuğu, train n=9..30, 1 deneme/senaryo ile kabul etme olasılığı: **%38 (n=9), %40 (n=12), %43 (n=20/30).**
  (Monte Carlo, 20k tekrar, senaryo zorlukları Beta(1,2;0,8)'den). Yani basitlik önceliği fiilen çalışmıyor.
* **Güç (`required_n`, p=0,7):** Δ=0,10'u ayırt etmek için eşleştirilmemiş **330**, eşleştirilmiş (ρ=0,5) **165** senaryo; Δ=0,20 için **83 / 42**. Elimizdeki 5–10 validation senaryosu yalnızca çok büyük farkları görür.
* **Beta alt sınırı** 5 senaryoda %100 başarı için bile 0,61; 10 senaryoda 0,76. Holdout (2–3 senaryo) genelleme tahmini olarak anlamsız.
* GEPA döngüsü `trials=1` koşuyor (`gepa_lite.py:81`) → `user_variant = trial % 2 = 0`: **optimizasyon boyunca tek bir kullanıcı simülatörü**; ikinci simülatör yalnızca finalde. Memo §59'un uyarısı (tek simülatöre aşırı uyum) tam burada.
* PLAN Faz 8 kabul kriteri ("dondurulmuş validation'da istatistiksel olarak anlamlı iyileşme") bu bütçelerle **ulaşılamaz**; PLAN §9 zaten "kabul edilmiş mutasyon yok" diyor ama nedenini veri azlığına bağlıyor — asıl neden kuralın eşiği + güç.

**Öneri:** `min_net_wins = max(2, ⌈0,25·n⌉)`, çocuk ve ebeveyn için ≥2 deneme/senaryo, yalnızca eşleştirilmiş fark %80 güven aralığı sıfırı dışlıyorsa kabul (veya iki ardışık turda doğrulama); Monte Carlo'yu test olarak ekle (boş mutasyon kabulü <%10).
Varsayılan senaryo sayısını yükselt (≥40), `required_n` çıktısını rapora yaz, "bu karşılaştırmanın gücü şu" uyarısı ver. Holdout'u ≥10 senaryoya çıkar ya da raporda "tanısal değil" diye işaretle.

---

## 4. B — Önemli bulgular

### B1. MutationGate memo §12'nin yarısı  `[KOD]`

`MutationCheck` sekiz alan taşıyor, `check_mutation` yalnızca dördünü gerçekten set ediyor (`arguments_grounded`, `confirmation_satisfied`, `duplicate_risk_clear`, `preconditions_satisfied`).
`goal_alignment`, `policy_allows`, `state_is_fresh`, `target_unambiguous` **hep True**; memonun "hedef belirsizse asla onaylama" ve "eylem hedefe uygun mu" kuralları kodda yok (yalnızca prompt).
Memo "deterministik **ve** LLM doğrulayıcı (tercihen ikisi)" diyor; LLM doğrulayıcı yok (`GuardMode.PROMPT_AND_DETERMINISTIC` bir etiketten ibaret).
Grounding istisnaları: tam sayı < 100, boolean, tarih, boşluklu metin grounding'den muaf (`needs_grounding`, `mutation_gate.py:115-134`) → "500" yerine "50" yazılan küçük tutarlar deterministik olarak yakalanmıyor (yalnızca onay koruyor).
`RuntimeState` ve `CompletionSpec` şemaları tanımlı ama hiçbir yerde kullanılmıyor; `AgentSpec.completion_verifier` yalnızca audit'te bir bayrak, `planning_mode`, `memory_mode`, `max_steps`, `tool_policy` alanlarının çalışma zamanı etkisi yok.

**Öneri:** `target_unambiguous` için: aynı `*_id` argümanının son okuma sonucunda birden fazla aday dönmüşse blokla. `state_is_fresh` için `state_version` (son yazmadan sonra aynı kaydın okunmuş olması). Kullanılmayan alanları ya bağla ya sil (aldatıcı).

### B2. Kanıt eksikliği  `[KOD]/docs`

* PLAN §9: "Optimizer … canlı koşularda henüz kabul edilmiş mutasyon yok", Q1 (MutationGate faydası) ve TR deneyi koşulmadı, Spearman kalibrasyonu yok (τ² koşusu ertelendi).
* "Çekirdek hazır" tanımının 4. maddesi ("seçilen aday dondurulmuş validation'da B0'dan kötü değil") için kayıtlı bir koşu/sayı repoda yok (`experiments/` dizini yok; `out/` ignore'da).
* Harcama ≈ $2 / $50. 125 test sahte LLM ile; gerçek-model davranışı yalnızca canlı koşulardan çıkan düzeltmelerle dolaylı olarak kanıtlı.
* Golden risk testi 24 araç/12 yazma, **geliştirmede kullanılan aynı bundle'lardan** → "%100 yazma recall" kendi kendini doğruluyor. Bağımsız bir test seti yok.

**Öneri:** 9 Ekim gecesi tek bir kayıtlı kıyas koşusu: B0 vs A vs C1 vs (varsa) çocuk, ≥40 senaryo, ≥2 deneme, `competition` profili; sonuç tablosu + güven aralığı `docs/` veya `experiments/`'a girsin. Sistem B0'dan anlamlı iyi değilse bunu bilmek yarın için kritik.

### B3. PLAN'da ✅ olan ama pipeline'a bağlı olmayan bileşenler  `[ÇALIŞTIRDIM]`

| Bileşen | Durum |
|---|---|
| `testsynth/adversarial.py::adversarial_scenarios` | Hiçbir yerden çağrılmıyor (test dahil sıfır referans). PLAN §9 "Faz 5 ✅ canlı: …adversarial" diyor. |
| `optimize/opro.py` | Hiç çağrılmıyor, testi yok |
| `knowledge/doc_shaper.py::shaped_documents` | Yalnızca testte; ingest/export çıkışı yok (Freya KB API'si de yok, §B4) |
| `optimize/mutations.py::toggle_fewshot` | Yalnızca testte; optimizer döngüsü few-shot'u aramıyor (memo §27/§3.17) |
| `testsynth/oracle_validate.py::filter_consistent` | Kullanılmıyor (`cross_check` doğrudan çağrılıyor) |
| `GepaLite(enable_crossover=False)` | Pipeline açmıyor; RUNBOOK "crossover açık" diyor ama CLI bayrağı yok |
| `eval/stats.py::required_n` | Pipeline kullanmıyor (§A8) |
| `adapters/tau.py` | Tamamen testsiz, çalıştırılmadı (karar gereği) |
| `Oracle.policy`, `ScoreVector.reliability`, `RuntimeState`, `CompletionSpec`, `InputBundle.examples` | Tanımlı, kullanılmıyor |

**Öneri:** PLAN §9'daki durum sütununu "kod var / pipeline'a bağlı" olarak ikiye ayır; bağlı olmayanları ya bağla (adversarial: final audit öncesi son 1–2 jenerasyon için kırmızı takım senaryosu) ya da "kullanılmıyor" diye işaretle. Jüri kodu okuyacak; ölü kod "yaptık" izlenimi verir.

### B4. Bilgi katmanı  `[KOD][SPEC]`

* Yalnızca BM25 (`knowledge/retriever.py`). `retrieval_modes=["bm25"]` sabit (`knowledge_compiler.py:66`); ama `_retrieval_mode` büyük korpusta `RetrievalMode.HYBRID` etiketi veriyor (`candidate_generator.py:67-72`). Etiket, uygulamayla uyuşmuyor. Dense/hybrid/rerank/sorgu yeniden yazımı/kanıt paketleme (memo §14.2-14.3) yok; `POLICY_GRAPH` modu tanımlı ama BM25'e düşüyor.
* Freya OpenAPI'sinde **knowledge base endpoint'i yok** (spec'te "knowledge" yalnızca bir hata kategorisi). Rehber (`knowledgebase.md`): KB çalışma alanı düzeyinde, UI'dan yükleniyor, ajana "KB'yi ara" talimatı veriliyor; ajan başına ayar yok. Dolayısıyla memo §55.7'nin "doküman şekillendirme" fikrinin otomatik bir çıkışı yok.
* Prompt'un "40 000 karakterden büyükse `search_knowledge` kullan" kuralı (`INLINE_DOC_CHAR_LIMIT`) Freya'da geçersiz bir araç adı; oysa Freya system prompt'u 200 000 karaktere kadar kabul ediyor. 40k–200k arası korpus için **inline** daha güvenli olabilir (maliyet/gecikme artar).
* `search_knowledge` yalnızca yerel `ReferenceAgent`'ta var.

**Öneri:** Freya hedefi için retrieval politikasını platform varyantına ayır: `kb_mode ∈ {inline, platform_kb}` ve görev kuralı gelince karar ver (RULES_MAPPING #6, #7). BM25'e "HYBRID" demeyi bırak.

### B5. Kanal/dil sezgileri  `[ÇALIŞTIRDIM]`

`looks_like_voice` (`ingest/lang.py:9`) `phone|call|voice|…` sözcüklerine bakıyor. Test: "Customers may update their *phone* number…" ve "…the customer may *call* back" gibi **metin** görevleri `voice=True` dönüyor.
Etkisi: `voice_policy` bloğu (1-2 cümle, rakam rakam okuma), VOICE senaryo kategorisi, `sttConfig/ttsConfig` yazılması, mimari seçici. Kanal çoğunlukla belirtilmemişse (kural günü), yanlış kanal prompt'u bozabilir.
**Öneri:** `meta.channel` varsa ona uy; yoksa sezgiyi muhafazakâr yap (örn. "by phone", "voice agent", "inbound call" ifadeleri) ve karar `RULES_MAPPING #8` ile kesinleşsin.

### B6. Optimizer yüzeyi dar ve bir risk taşıyor  `[KOD]`

* Yalnızca prompt blokları mutasyona uğruyor (`apply_block`). Sıcaklık, model, `tool_description_overrides`, gate modu, few-shot, `postHistoryPrompt`, tool `config` mutasyonu yok. `tool_descriptions` bileşeni (WRONG_ARGUMENT/WRONG_TOOL için asıl hedef) `tool_use_policy` bloğuna yönlendiriliyor (`gepa_lite.py:125`, `mutations.py:118`) — oysa argüman formatı sorunu en iyi tool açıklamasında çözülür.
* `domain_rules` bloğu (policy'nin derlenmiş metni) mutasyon hedefi (`POLICY_VIOLATION → domain_rules`, `schema/failure.py:48`). LLM bu bloğu yeniden yazarken kural kimliklerini/ön koşullarını düşürebilir; `final_audit` **kural kapsamı** (her kural id'si prompt'ta var mı) kontrol etmiyor, yalnızca literal lint'i var (`final_audit.py:35-36`).
* Çocuk üretimi `children_per_parent=2`, bileşen başına bir operatör; kurtarılan "ders" yalnızca mutasyon prompt'una giriyor, **ilk aday üretiminde** kullanılmıyor (memo §3.8 A: "aday üretimi ilgili dersleri alır").
* `OperatorStats` her koşuda sıfırlanıyor (PromptBreeder-lite görevler arası öğrenmiyor).

**Öneri:** audit'e "bütün policy kural id'leri `domain_rules` içinde mevcut" ve "mutasyon sonrası kural sayısı azalmadı" kontrolü; `domain_rules`'ı yalnızca *ekleme* yapan mutasyonla sınırla (veya sadece `rule_priority`/`over_caution` bloklarını değiştir). Tool açıklaması mutasyonu için `tool_description_overrides` yolunu bağla.

### B7. Seçim ölçütleri memodan eksik  `[KOD]`

`selection_key` (`select/robustness.py:25-27`): alt sınır → ortalama → FC → FR → boyut. Memo §17.2'nin saydığı **pass^k, en kötü durum, hata-küme sıklığı** raporlanıyor ama seçime girmiyor. `ScoreVector.reliability` hiç doldurulmuyor.
Memo §57.3: varsayılan arama süresi kısa; kısalık doğruluk özelliği. Gecikme/tur uzunluğu seçimde yok; `TIMEOUT` yalnızca başarısızlık olarak sayılıyor.
Memo §17.1/§61: FC/FR **toplamı** optimize edilecekti; kodda yalnızca sıralı bağ-kırıcı olarak kullanılıyor (ağırlıklı toplam yok). Resmi puanlama bilinince değişecek (RULES_MAPPING #11) ama şimdi bile pass^2 birincil tie-breaker olmalı.

### B8. Persona/simülatör çeşitliliği  `[KOD]`

İkinci kullanıcı modeli (`user_sim_alt`) yalnızca final doğrulamada (trial tek sayılı olunca) devreye giriyor (`mock/adapter.py:23,79`). Judge ve user_sim aynı model ailesi (`openai/gpt-6-luna-pro`), yani hakem simülatörün çıktısını değerlendirirken kendi ailesini görüyor (kendini kayırma riski; config yorumu bunu ajan için önlemiş, judge↔user_sim için değil).
`trial` ile `user_variant` aynı değişkene bağlı → pass^k, *çalıştırma gürültüsü* ile *simülatör farkını* ayırt edilemez biçimde karıştırıyor.

---

## 5. C — Orta/düşük bulgular

| # | Bulgu | Not |
|---|---|---|
| C1 | `lexicon_risk`: `send_money` gibi bileşik fiiller tokenlara bölündüğü için `IRREVERSIBLE_VERBS`'taki `send_money` hiç eşleşmiyor (ilk token `send` → `EXTERNAL_SIDE_EFFECT`). Hâlâ "yazma" sayılıyor ama otomatik onay gereksinimi çıkmıyor. **[ÇALIŞTIRDIM]** `send_money → EXTERNAL_SIDE_EFFECT` | `tool_compiler.py:75-108,127-153` |
| C2 | Sözlük dışı okuma adları yazma sayılıyor: **[ÇALIŞTIRDIM]** `quote_premium → REVERSIBLE_WRITE`, `inspect_claim → REVERSIBLE_WRITE`, ve açıklamasında "transfer" geçen `preview_fee ("Shows the fee for a transfer") → IRREVERSIBLE_WRITE` (otomatik onay gerektirir). `merge_semantics` **yalnızca riskli yönde** birleştirdiği için (`riskier(...)`, `tool_compiler.py:335`) LLM geçişi bu yanlış pozitifleri düzeltemez. Sonuç: gereksiz onay/gate → false refusal (memo §61). Öneri: açıklama regex'i ancak ad-fiili bilinmiyorsa devreye girsin; LLM "READ_ONLY" derse ve sözlük yalnızca *zayıf* (açıklama kaynaklı) sinyal verdiyse aşağı terfiye izin ver. | memo §61 |
| C3 | Few-shot'lar uydurma araç adları kullanıyor (`cancel_subscription`, `submit_payment`, `get_payment_status`, `book_slot`); görevde olmayan araçları çağırmaya itebilir. Alan-nötr ama "araç-nötr" değil. | `synth/fewshot.py` |
| C4 | Dual-control (memo §3.3): `GUIDE_USER` davranışı yalnızca prompt; kullanıcı simülatörü kullanıcı-tarafı eylemleri yapamıyor ve evaluator bunu ölçmüyor. | UNKNOWN: kural günü bilinecek |
| C5 | Ses (memo §3.5/§63) ve Türkçe (§64): prompt blokları + stt/tts config var; **hiç koşulmadı**. TR bundle'ı (`tr_card_limit.yaml`) için sonuç yok. | PLAN Faz 10 ertelenmiş |
| C6 | Doğrudan testi olmayan modüller: `gepa_lite` (yalnızca e2e), `final_audit`, `cli`, `factory`, `llm_env` (görülmemiş görevlerin tool backend'i!), `task_compiler`, `knowledge_compiler`, `policy_eval`, `simulator`, `oracle_validate`, `tau`, `opro`, `adversarial`. | Ölü-kod/test taraması |
| C7 | `mypy --strict` yalnızca `schema/`, `optimize/`, `llm/` (34 dosya). `runtime/`, `eval/`, `adapters/`, `ingest/` tipsiz denetim dışında. | `pyproject.toml` |
| C8 | Dokümantasyon: `docs/ARCHITECTURE.md`, `docs/RESEARCH.md`, `experiments/`, `evals/`, `submission/` yok (PLAN §3). `RUNBOOK.md` Türkçe, PLAN "teslim dokümanları İngilizce" diyor. `CORRECTNESS_REVIEW.md` "94 passed" diyor, gerçek 125. PLAN'daki `python -m builder.run` yolu eski (gerçek: `python -m builder run`). | Jüri okuyacak |
| C9 | `PlatformAdapter` protokolü memo/PLAN'daki yüzeyin (`fork_chat`, `get_call_details`, `upsert_personas`, `upsert_unit_tests`, `run_simulation(..., mode)`) yalnızca bir kısmını taşıyor; Freya'ya özgü yetenekler (branch/commit/merge, unit test, chat) soyutlamada yok. | `adapters/base.py` |
| C10 | `FreyaClient`: `create_branch` ve `chat_fork`, `list_workflows` endpoint'leri tanımlı, sarmalayıcı/kullanım yok. Versions/branches ("hazır aday deposu", memo §55.5) kullanılmıyor; aday başına yeni bir **agent** yaratılıyor (`deploy_agent`). | 6 aday + çocuklar = çok sayıda agent; kota/yönetim riski |
| C11 | Hata durumları: `FreyaClient.call` 4xx'te hemen düşer (doğru); ama `wait_simulation` zaman aşımında tüm `_search` iptal oluyor (`pipeline.build` `except Exception → fallback`) — kısmi sonuçlar kullanılmıyor. | dayanıklılık |

---

## 6. Memo → uygulama kapsama matrisi

(✅ uygulandı ve pipeline'a bağlı · 🟡 kısmi/kozmetik · ⚪ yazıldı ama bağlı değil · ❌ yok)

| Memo | Konu | Durum | Not |
|---|---|---|---|
| 3.1 | Sonuç/durum tabanlı değerlendirme | ✅ mock · ❌ Freya | Freya'da durum yok; judge + platform verdict (A2) |
| 3.1 | pass^k | 🟡 | Hesaplanıyor, seçimde yok (B7) |
| 3.2 | Tek trajektoriye bağlanmama | ✅ | Durum + subset tool-call |
| 3.3 | Çift kontrol (dual control) | 🟡 | Yalnızca prompt (C4) |
| 3.4 | Bilgi katmanı (hibrit, rerank, politika grafı) | 🟡 | Yalnızca BM25 (B4) |
| 3.5/63 | Ses | 🟡 | Prompt + stt/tts; koşulmadı (C5) |
| 3.6 | `think` aracı | 🟡 | Yalnızca yerel; Freya'da yok (A1) |
| 3.7/12 | MutationGate | 🟡 | 4/8 kontrol; Freya'da yok; LLM doğrulayıcı yok (A1, A6, B1) |
| 3.8/20 | Ders hafızası | 🟡 | Mutasyon prompt'unda; aday üretiminde yok (B6) |
| 3.9 | GEPA-lite | ✅ | Yüzey dar, kabul kuralı zayıf (A8, B6) |
| 3.10 | TextGrad kredi ataması | 🟡 | Statik harita + LLM rafine |
| 3.11 | OPRO | ⚪ | Bağlı değil (B3) |
| 3.12 | PromptBreeder | 🟡 | Operatör istatistiği var, kalıcı değil |
| 3.14-3.16 | ADAS/AgentSquare/AFlow (mimari arama) | 🟡 | Çeşitlilik kozmetik (A7); workflow derleyici yok (UNKNOWN #3) |
| 3.17/27 | Few-shot araması | ⚪ | `toggle_fewshot` bağlı değil |
| 5 | Typed IR | ✅ | Bazı alanlar ölü (B1, B3) |
| 6 | Tool graf kenarları | 🟡 | Yalnızca `FEEDS`; `REQUIRES_BEFORE/REVERSES/VERIFIES/MUTUALLY_EXCLUSIVE` yok (A6) |
| 7 | Policy derleme | ✅ | Kaynaksız kural reddi, öncelik, çelişki |
| 10-13 | Runtime state / protokol / bağlam temizliği | 🟡 | Prompt + yerel; `state_version` yok (B1) |
| 15 | Test kategorileri | ✅ | 13+3+1; kota round-robin |
| 16 | Oracle | 🟡 | `Oracle.policy` kullanılmıyor |
| 17 | Skor vektörü / seçim | 🟡 | B7 |
| 18-19 | Optimizer / bütçe / halving | ✅ | Beta alt sınırı sıralaması iyi |
| 22 | Adapter sınırı | 🟡 | C9 |
| 23 | Mock platform | ✅ | 1 domain + LLM backend; paralellik hatası (A4) |
| 24-26 | τ² ve ablation | ⚪ | Kod var, koşu yok |
| 28 | Hata analizi / regresyon | ✅ | Deterministik lokalizasyon + LLM rafine |
| 29-30 | Adversarial | ⚪ | Bağlı değil (B3) |
| 31 | Completion verifier | 🟡 | Yalnızca prompt |
| 34 | Aşırı uyum önleme | ✅ | Split, lint, regresyon train-only; audit'te kural kapsamı yok (B6) |
| 35-36 | Tekrarlanabilirlik / trace | ✅ | Manifest, JSONL trace; Freya ham yanıt yok (A2) |
| 43 | Final audit | ✅ | Kural kapsamı + şema doğrulaması eksik (A5, B6) |
| 55.4 | Chat API / fork | ❌ | Kullanılmıyor (A3) |
| 55.5 | Versions/branches | ❌ | Kullanılmıyor (C10) |
| 55.7 | KB | ❌ | API yok (B4) |
| 56 | Memo bileşeni → Freya primitifi | 🟡 | `pre_process_code` ölü, `post_process_code`/`postHistoryPrompt`/tool `config` yok |
| 57 | İki puanlama rejimi | 🟡 | Judge ✅; tool-call assertion ❌ (A2) |
| 58 | IntellAgent-lite | ✅ | Pearson/Spearman kalibrasyonu yok (B2) |
| 59 | Simülatör sağlamlığı | 🟡 | Finalde 2 model; override yok (A3, B8) |
| 60 | İstatistik | ✅ kütüphane · 🟡 uygulama | Kabul kuralı ve güç (A8) |
| 61 | Aşırı temkin | ✅ | 3 kategori, FC/FR ayrı |
| 64 | Türkçe | 🟡 | Dil-bilinçli üretim var; TR deneyi yok |

---

## 7. PLAN'daki "✅" iddiaları ile doğrulanan durum

| PLAN §9 | PLAN diyor | Doğrulanan |
|---|---|---|
| Faz 2 | "✅ mock / ⚠️ Freya VERIFY" | Doğru; ayrıca spec ile 5 ek uyumsuzluk (A2, A3, A5) |
| Faz 3 | "Golden risk testi: write recall %100" | Test geçiyor ama etiketler geliştirme bundle'larıyla aynı; `REQUIRES_BEFORE` üretimi yok (A6) |
| Faz 4 | "B0, A, C1–C6; blok kütüphanesi; seçici" | Adaylar var; çeşitlilik %93–99 benzer, sıralama etkisiz (A7) |
| Faz 5 | "✅ canlı … adversarial" | `adversarial_scenarios` çağrılmıyor (B3) |
| Faz 6 | "✅ canlı" | Mock'ta evet; Freya trace parse tahmin (A2) |
| Faz 8 | "✅ kod / ⏳ kanıt" | Doğru; kabul kuralı boş mutasyonu %38–43 kabul ediyor (A8) |
| Faz 9 | "✅ canlı; temiz ortam testi geçti" | Export geçersiz config (A5); yeniden doğrulanmalı |
| Faz 10 | "✅ kod / ⏳ koşu" | Doğru (τ² koşulmadı) |
| §8 tanım | "çekirdek hazır" 1-5 | (1) ✅ doğrulandım; (2) FAST modda ✅; (3) Freya yolu doğrulanmadı; (4) kanıt yok; (5) bütçe aşımı 0 beklenir ama paralellik hatası süre limitini bozar |

---

## 8. Öncelikli aksiyon planı

### Bugün (9 Ekim) — anahtar gerekmeyenler

| Sıra | İş | Dosya | Efor |
|---|---|---|---|
| 1 | LLM kilidini HTTP çağrısının dışına al + paralellik testi | `llm/client.py`, `tests/unit/test_llm.py` | 30 dk |
| 2 | Tek, geçerli export yolu: base config zorunlu, `model` zorunlu, `maximumDuration` submission'a gömülmesin, `no_full_pan` koşullu; config'i spec'e karşı doğrula | `pipeline.py`, `adapters/freya/compile.py`, yeni test | 1–1,5 saat |
| 3 | Platform prompt varyantı: `think`, `search_knowledge`, `blocked_by_safeguard`, `end_conversation` atıflarını çıkar | `synth/prompt_blocks.py`, `schema/agent.py` | 1–2 saat |
| 4 | `REQUIRES_BEFORE` üretimi (kimlik doğrulama → yazma) + test | `ingest/tool_compiler.py` | 1–2 saat |
| 5 | Kabul kuralını sıkılaştır + Monte Carlo testi (boş mutasyon <%10); varsayılan senaryo ≥40; rapora güç bilgisi | `optimize/acceptance.py`, `cli.py`, `pipeline.render_report` | 1 saat |
| 6 | Freya trace'e ham JSON ekle (`platform_result["raw"]`); `personaLlmOverride`, `maxRetries=0`, `assertions` alanlarını (bayrak arkasında) gönder | `adapters/freya/adapter.py` | 1,5 saat |
| 7 | Audit: policy kural kapsamı + export şema doğrulaması | `select/final_audit.py` | 45 dk |
| 8 | Kayıtlı kıyas koşusu: B0 vs A vs C1(+çocuk), ≥40 senaryo, ≥2 deneme, `competition` | `experiments/` | ~2 saat + ~$5–10 |

### 10 Ekim — ilk 60–120 dakika (kod yazma yok; RULES_MAPPING)

Memo §44 kuralı korunmalı. Bu raporun açtığı **yeni sorular** RULES_MAPPING A tablosuna eklenmeli:

* Tool çağrıları `GET /call/{id}.messages` içinde görünüyor mu? Hangi alan? (A2) — `scripts/smoke_freya.py` bunu çözer.
* `handler` değerleri ve araçların gerçek backend'i (HTTP/mock) kim tarafından sağlanıyor? (RULES_MAPPING #18)
* `personaLlmOverride` ve `analysisLlmOverride` çalışıyor mu, hangi model değerleri geçerli? (A3)
* `pre_process_code` bir çağrıyı reddedebilir mi, hangi dil? (A1, #17)
* Simülasyon `maxRetries` neyi yeniden deniyor? (A3)
* Teslim nesnesi nedir: ajan, `config` JSON'u mu, kod mu? Hangi `provider/model` serbest? (#2, #4)
* KB nasıl yükleniyor (yalnız UI mi)? (B4, #6)
* Kanal ses mi, metin mi? (B5, #8)

Sonra: smoke → ham yanıtlarla adaptörü ve fixture'ları düzelt → karar: `FreyaChatAdapter` mi, simülasyon mu (A3).

### 10–11 Ekim — çeşitlilik ve kanıt

* A7 (gerçek eksenler), A8 (güç), B6 (tool açıklaması mutasyonu, few-shot araması), B7 (pass^k/gecikme seçimde).
* `adversarial_scenarios`'ı son jenerasyona bağla; `kayıtlı` ablation Q1/Q3.
* Gerekirse Freya'da `postHistoryPrompt` ve tool `config.maxRetries=0` gibi **deterministik** yüzeyleri kullan (memo §56 "en deterministik primitifi seç").

### Kesme önerisi

Zaman azsa **yeni mimari/özellik ekleme**; A4, A5, A6, A8, A1 (hepsi < yarım gün) ve B2 (kanıt koşusu) yapılmalı. Crossover, OPRO, τ² ve TR/ses deneyleri en sona.

---

## 9. Yeniden üretmek için

```bash
uv sync
uv run pytest                      # 125 passed
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy                        # yalnızca schema/optimize/llm (34 dosya)

# A5: FAST modda dışa aktarılan config
env -u OPENROUTER_API_KEY uv run python -m builder run -i examples/bundles/insurance_claims -o /tmp/out --adapter none
python3 -c "import json;c=json.load(open('/tmp/out/freya_agent_config.json'));print(list(c), c['llmConfig']['model'])"

uv run python scripts/gap_checks/llm_lock_concurrency.py   # A4: 8 çağrı, 4 thread, 0,5 sn gecikme → ≈4 sn ise seri
uv run python scripts/gap_checks/tool_graph_edges.py       # A6: 4 bundle'da kenar türleri, depth, önkoşullar
uv run python scripts/gap_checks/candidate_similarity.py   # A7: aday prompt'ları arası difflib benzerliği
uv run python scripts/gap_checks/acceptance_power.py       # A8: boş mutasyon kabul olasılığı + güç + bölünme boyutları
```

Spec karşılaştırması (A2, A3, A5) `vendor/freya/openapi.json`'un `json` ile çözülüp `$ref`'lerin açılmasıyla yapıldı
(`/agents` POST gövdesi, `/simulations` POST gövdesi, `/call/{callId}` yanıtı, `/unit-tests` POST gövdesi, `/chat/message`).
Bu betikler yalnızca teşhis içindir; `make check` kapsamında değildir.

---

## 10. Çözüm durumu (9 Ekim akşamı)

Bu rapor ve ardından gelen ikinci bir review birlikte değerlendirildi. İkinci review'ın dört nüansı benimsendi:
- **A5 yeniden tanımlandı:** sorun "bütün export geçersiz" değil, "iki export yolu ayrışmış ve standalone dosya eksik".
- **`REQUIRES_BEFORE`:** LLM önerisi doğrudan terfi ettirilmiyor, yalnızca kanıtlı kenar üretiliyor.
- **Önce kabul kuralı:** senaryo sayısını ≥40'a çıkarmadan önce kabul kuralı düzeltildi.
- **Dürüst durum etiketleri:** ölü kod pipeline'a zorla bağlanmadı; PLAN'da durumu "kod / bağlı / canlı" olarak ayrıldı.

| # | Bulgu | Durum | Ne yapıldı | Kanıt |
|---|---|---|---|---|
| A4 | LLM kilidi | **Düzeltildi** | Ağ çağrısı kilit dışında yapılıyor. Kilit altında yalnızca cache, bütçe kontrolü ve ücretlendirme kalıyor. Uçuştaki çağrılar modelin ortalama maliyet ve token'ını rezerve ediyor, böylece paralel çağrılar bütçe sınırını birlikte aşamıyor. | `llm_lock_concurrency.py`: 4,01 sn → **1,01 sn**. Test: paralellik ve rezerv. |
| A5 | İki export yolu | **Düzeltildi** | Tek yol `submission_config`. Freya varken: platform varsayılanı + builder alanları, spec'e göre doğrulanmış `freya_agent_config.json`. Yokken: açıkça `freya_config_overlay.json`. `model: null` artık yazılmıyor. Simülasyona özgü `maximumDuration` teslime girmiyor. `no_full_pan` yalnızca görev kart numarasından söz ediyorsa ekleniyor. | `test_single_submission_path_*`, `test_card_number_guardrail_*` |
| A1 | Teslim edilen prompt'ta yerel araçlar | **Düzeltildi (prompt kısmı)** | `platform_system_prompt`, `think`, `search_knowledge` ve `blocked_by_safeguard` atıflarını platform karşılıklarıyla değiştiriyor. Bu atıflardan biri kalırsa audit reddediyor. `system_prompt.md` teslim edilen prompt oluyor. **Açık:** ölçülen ajan yerel gate'li ajan. Freya açılınca seçim Freya üzerinde yapılmalı. | `test_shipped_prompt_has_no_local_only_tools_*` |
| A6 | `REQUIRES_BEFORE` yok | **Düzeltildi (kanıtlı)** | Kenarlar artık `source` ve `evidence` taşıyor. Zorlanan kenar yalnızca şu koşulda üretiliyor: bir policy paragrafı doğrulama aracını yazmalardan **önce** şart koşuyor ve ya yazma aracını ya da "herhangi bir değişiklik" ifadesini anıyor. LLM önkoşulu `FEEDS` olarak tavsiye düzeyinde kalıyor. Varlık eşleştirmesi `policy_number` gibi anahtarlara ve doğrulama sonucunun döndürdüğü ID'lere genişletildi. | Banka 4, sigorta 3 kenar, derinlik 2. Kredi ve TR'de policy yalnızca "bilgi vermeden önce" dediği için kenar yok (doğru). |
| A8 | Boş mutasyon %38–43 kabul | **Düzeltildi** | Kabul koşulları: net kazanç ≥ max(2, %20·n), tek yönlü işaret testi p ≤ 0,1 ve ortalama fark ≥ 0,05. Ebeveyn ve çocuk senaryo başına 2 denemeyle, dönüşümlü kullanıcı simülatörüyle karşılaştırılıyor (B8'i de kapatıyor). Rapor tespit edilebilir en küçük farkı yazıyor; küçük holdout'lar "tanısal" olarak işaretleniyor. | Monte Carlo testi: boş kabul **%3–6**; gerçek +0,3 farkı yakalama gücü n=20'de %85 |
| A2 | Freya trace tahmini | **Kısmen** | Ham `run` ve `call` her trace'te saklanıyor. Platformun kendi sonuçları okunuyor: `successCriteriaResult`, `assertionResults`, `problems`, guardrail engelleri. Tool çağrısı görünmüyorsa tool, policy ve yazma tabanlı kontroller **bilinmiyor** sayılıyor, başarısız değil; kararı platformun sonucu veriyor. Fixture'ın tahmin olduğu belgelendi. **Açık:** gerçek mesaj şekli, unit-test assertion'larının oluşturulması (canlı smoke gerekli). | `test_spec_shaped_call_uses_native_results_and_keeps_raw` |
| A3 | Simülasyon ayarları | **Kısmen** | `maxRetries=0`. `FREYA_PERSONA_LLMS` ile varyant başına `personaLlmOverride`. **Açık:** toplu simülasyon (`runsPerPersona`, çoklu persona), `FreyaChatAdapter`. Karar smoke sonrasında verilecek. | |
| A7 | Kozmetik çeşitlilik | **Kısmen** | Bölüm sırası artık render ediliyor (policy-first gerçekten önce kuralları yazıyor). Doküman yokken retrieval-first üretilmiyor (C1 kopyasıydı). Birebir aynı aday yok (test). **Açık:** C2↔C3 hâlâ %99 benzer. PLAN'da dürüst tanım: "prompt-blok optimizer'ı + birkaç gerçek eksen". | `test_candidates_are_structurally_distinct` |
| B5 | Ses sezgisi | **Düzeltildi** | Tek kelime değil kanal ifadesi aranıyor ("voice agent", "inbound calls", "telefonda"). | test |
| B6 | Kural kapsamı | **Düzeltildi** | Audit, `domain_rules` bloğundan düşen kural ID'si varsa adayı reddediyor. | test |
| C1/C2 | `lexicon_risk` | **Düzeltildi** | Bileşik fiiller tanınıyor (`send_money` → IRREVERSIBLE). `quote`, `preview` ve `inspect` okuma fiili. Deterministik sonuç kanıt düzeyi taşıyor. LLM yalnızca zayıf kanıtı (açıklama veya varsayılan) READ_ONLY'ye indirebiliyor; `transfer_funds` yazma olarak kalıyor. | test |
| C3 | Few-shot uydurma araç adları | **Düzeltildi** | Araçlar köşeli parantezli yer tutucularla anılıyor, yanında "yalnızca kendi araçlarını çağır" notu var. | test |
| B3 | ✅ ama bağlı değil | **Belgelendi** | PLAN §9 artık "kod / bağlı / canlı doğrulandı" ayrımını yapıyor. Adversarial, OPRO, crossover, `toggle_fewshot` ve `doc_shaper` bilerek bağlanmadı. | PLAN §9 |
| B1, B2, B4, B7, C4–C11 | | **Açık** | B2 (kayıtlı B0 / A / C kıyas koşusu) bir sonraki adım. Diğerleri Freya'ya bağlı ya da öncelik dışı. | |

Ayrıca bu turda canlı Türkçe koşudan gelen iki hata düzeltildi:
- Güvenlik kapısı, daha önceki turda söylenmiş bir tutarı (`100.000 TL`) kanıt saymıyordu. Geçmiş bir kayıttaki aynı adlı alan da müşterinin istediği tutarı geçersiz kılıyordu.
- Senaryo üretici liste içindeki kayda kimliğiyle erişen durum yamalarında (`musteriler.C003.durum`) hata veriyordu.

Test sayısı 141'e çıktı; `ruff` ve `mypy` temiz.
