# Uygulama Planı — Freya "Build the Builder" Agent Builder

> **10 Ekim:** Yarışmanın ana planı artık [`HACKATHON_REALIGN.md`](HACKATHON_REALIGN.md). Bu belge, genel amaçlı builder araştırmasının kaydı olarak kalıyor. Puanlanan yol workflow derleyicisi.

**Kaynak:** `freya_build_the_builder_research.md` (bundan sonra "memo"; `§N` = memo bölümü)
**Hazırlanma:** 6 Ekim 2026
**Hedef:** 10 Ekim sabahı kurallar açıklanmadan önce çekirdeğin uçtan uca çalışması. O andan sonra sadece adapter, config ve bütçe değişecek (§44, §45).

> **Plan dili:** Plan Türkçe yazıldı. Kod, docstring, README ve teslim dokümanları İngilizce olacak, çünkü teslimi jüri okuyacak.

---

## 0. Memonun çelişkilerine verilen kararlar

Memonun ilk yarısı (§1–54) ile ekleri (§55–70) bazı yerlerde çelişiyor. Kural şu: **ek, ana metni geçersiz kılar.** Tek istisna aşağıda ayrıca belirtilen mock kararı.

| Konu | Ana metin | Ek | **Karar** |
|---|---|---|---|
| Freya API | Özel, bilinmiyor (§1.2, §22) | Herkese açık OpenAPI var (§55) | Gerçek `FreyaAdapter` yazılacak. Spec bu ortamdan indirilemiyor, `vendor/freya/openapi.json` olarak repoya konulmalı (bkz. Sorular). |
| Mock platform | Kur (§23) | Düşük öncelik (§66) | **Küçük, deterministik bir mock kurulacak.** Sebepleri: (a) bu container Freya'ya erişemiyor, (b) birim testleri ve CI ağ gerektirmemeli, (c) DB-state oracle'ını (§16, §58 "oracle validation") doğrulamanın tek yolu bu. Bütçesi kısıtlı tutulacak: 1 domain, en fazla 8 tool. |
| Güvenlik önceliği | Leksikografik güvenlik (§17.1) | Aşırı temkin de puan kaybettirir (§61) | ScoreVector'da `false_compliance` ve `false_refusal` ayrı tutulacak. Seçimde ikisinin toplamı optimize edilecek, ağırlıklar resmi skora göre ayarlanacak. |
| FLARE | Deney olarak (§3.17) | Doğrulanamadı (§66) | Plandan çıkarıldı. Few-shot arama (§27) bağımsız bir eksen olarak duruyor, FLARE'e dayanmıyor. |
| Sıralama | §54 sırası | §70 P0/P1/P2 | §70 esas alınacak. §54'teki teknik bağımlılık sırası fazların içinde korunuyor. |
| Değerlendirme | DB-state oracle (§3.1) | Freya-native LLM judge + assertion (§57) | **İkisi için de evaluator yazılacak.** `ScoreVector` her iki rejimi de taşıyabilecek. |

### 0.1 Kullanıcı kararları (6 Ekim)

| Soru | Cevap | Plana etkisi |
|---|---|---|
| Freya erişimi | Henüz hesap yok | Mock-first geliştirme. `FreyaAdapter` memo §55–56'daki alanlara göre yazılır, her alan `VERIFY` olarak işaretlenir. Testler kayıtlı (elle yazılmış) HTTP fixture'larıyla yapılır. Hesap açılınca ilk iş Faz 2'deki smoke script çalıştırılır. |
| LLM sağlayıcısı | **OpenRouter** | Tek sağlayıcı: OpenAI-uyumlu `https://openrouter.ai/api/v1`, `OPENROUTER_API_KEY`. Rol başına farklı model seçilebilir (örneğin `user_sim` için 2 farklı aile, §59). `openrouter.ai` ortam allowlist'ine eklenmeli. `LLMClient` protokolü sağlayıcıdan bağımsız kalır. |
| τ²-bench | Kodu yazılacak, şimdilik çalıştırılmayacak | `adapters/tau.py` + ablation scriptleri yazılır ve birim testleri cassette ile yapılır. Gerçek koşu yapılmaz. |
| Takım / bütçe | Solo, ~$50 | **Varsayılan hazırlık bütçesi `max_cost_usd=50`** (global sayaç, `storage/` içinde kalıcı). Ucuz modeller `user_sim`, `judge` ve `analyzer` rollerinde, güçlü model sadece `builder` ve `optimizer` rollerinde. Agresif cache, varsayılan olarak cassette tabanlı testler. Ablation (Faz 10) küçük örneklemle. Paralel iş yok, fazlar sıralı ilerler. |

Durum etiketleri (§70) planda ve kodda kullanılacak: `CONFIRMED`, `STRONG HYPOTHESIS`, `UNKNOWN`, `VERIFY`. Freya'ya bağlı her davranış config içinde bir `VERIFY` bayrağıyla işaretlenecek ve smoke testi geçince `CONFIRMED` yapılacak.

---

## 1. Değişmez ilkeler (her PR bunlara göre gözden geçirilecek)

1. Deterministik yapılabilen bir şey LLM'e bırakılmaz (§70, §56).
2. Runtime agent sade kalır. Deney, yansıtma ve arama builder'da yapılır (§4 İlke 8, §70).
3. Optimize edilen şey görülmemiş görevlerdeki güvenilirliktir, pratik setini ezberlemek değil (§34, §70).
4. Her pahalı döngünün bir bütçesi ve durma koşulu vardır (§4 İlke 10, §19).
5. Her zaman geçerli bir fallback agent vardır. Optimizer son çalışan agent'ı asla bozamaz (§43, §4 İlke 9).
6. Doğruluk transkriptin iyi görünmesiyle değil, sonuç durumuyla ölçülür (§3.1, §4 İlke 1).
7. Yazma işlemleri okumalardan daha fazla akıl yürütme hak eder (§3.7, §4 İlke 2).
8. Çeşitlilik mimari düzeyde olur, kozmetik düzeyde değil (§3.15, §4 İlke 7).
9. Domaine özgü sabit değer yazılmaz: banka tool adları, ID'ler, pratik cevapları yok (§46, §34.4).
10. Her çalıştırma izlenebilir ve tekrarlanabilir olur: trace + manifest (§35, §36).

**Yapılmayacaklar (§46):** fine-tuning/RL, UI, büyük bir multi-agent runtime, sadece pratik skoruna göre optimizasyon, DB assertion'ın kanıtlayabileceği bir şey için LLM judge, sabit domain tool adları, non-idempotent yazmalarda kör retry, tool sonucu doğrulanmadan başarı iddiası.

---

## 2. Teknik temel

| Konu | Seçim | Gerekçe |
|---|---|---|
| Dil | Python ≥3.12 (container'da 3.13) | §24 τ²-bench uyumu |
| Paket yönetimi | `uv` + `uv.lock` | §35 tekrarlanabilirlik, §37 |
| Şemalar | `pydantic` v2 | §5 typed IR, JSON şema üretimi |
| HTTP | `httpx` (sync + async) | Freya client, eşzamanlı simülasyon |
| CLI | `typer` | `python -m builder run -i … -o …` (§35) |
| LLM | Kendi `LLMClient` protokolümüz + OpenRouter (OpenAI-uyumlu) adapteri, `httpx` ile ve SDK'sız | §32 rol bazlı model yönlendirme. Maliyet, OpenRouter yanıtındaki `usage` alanından okunur. |
| Retrieval | Saf Python BM25 + opsiyonel embedding sağlayıcısı | §14. Bağımlılık az, ağ olmadan test edilebilir. |
| Test | `pytest`, `pytest-asyncio`; LLM için kayıt/oynatma (cassette) fixture'ları | CI'da ağ ve anahtar gerekmez |
| Lint/format/type | `ruff`, `ruff format`, `mypy --strict` (sadece `schema/` ve `optimize/`) | |
| Depolama | Dosya tabanlı JSONL + SQLite (deney indeksi), içerik hash'li cache | §36, §37 `storage/` |

**LLM rolleri (§32, §33):**

| Rol | Varsayılan sıcaklık | Not |
|---|---|---|
| `builder` (compile, aday üretimi) | 0.7 | Çeşitlilik |
| `optimizer` (yansıtma/mutasyon) | 0.5 | |
| `analyzer` (hata teşhisi) | 0.0 | |
| `judge` | 0.0 | Sadece semantik kriterler için |
| `user_sim` (mock simülatör) | 0.8 | En az 2 farklı model (§59) |
| `agent` (runtime) | 0.0–0.3 | Platform varsayılanı 0.7, ablation ile test edilecek (§56) |
| `embedding` | — | Opsiyonel |

Her LLM çağrısı: içerik hash'li disk cache, token/maliyet sayacı, `OptimizationBudget` ile kesme, trace'e yazma.

---

## 3. Repo yerleşimi (§37 temel alındı, §55–58 eklemeleriyle)

```text
agent-builder/
├── README.md                    # İngilizce, teslim odaklı
├── pyproject.toml / uv.lock / .env.example / Makefile
├── docs/
│   ├── PLAN.md                  # bu dosya
│   ├── RESEARCH.md              # memo (taşınacak, çelişkiler düzeltilecek)
│   ├── ARCHITECTURE.md
│   ├── RUNBOOK.md               # 10 Ekim prosedürü + §44 tablosu
│   └── RULES_MAPPING.md         # 10 Ekim'de doldurulacak (§44, §68)
├── vendor/freya/openapi.json    # sağlanacak (VERIFY)
├── src/builder/
│   ├── cli.py, config.py, manifest.py
│   ├── llm/        client.py, anthropic.py, openai_compat.py, cache.py, roles.py
│   ├── schema/     task.py policy.py tool.py knowledge.py agent.py
│   │               scenario.py eval.py failure.py budget.py state.py status.py
│   ├── ingest/     bundle.py task_compiler.py policy_compiler.py
│   │               tool_compiler.py knowledge_compiler.py
│   ├── knowledge/  chunk.py bm25.py dense.py hybrid.py rerank.py policy_graph.py doc_shaper.py
│   ├── synth/      architecture_selector.py candidate_generator.py
│   │               prompt_compiler.py prompt_blocks/ fewshot.py
│   ├── runtime/    state.py mutation_gate.py completion.py context_cleaner.py reference_agent.py
│   ├── testsynth/  policy_graph_walk.py scenario_generator.py categories.py
│   │               adversarial.py persona_compiler.py oracle_generator.py oracle_validate.py split.py
│   ├── eval/       simulator.py state_eval.py toolcall_eval.py policy_eval.py
│   │               communication_eval.py judge.py metrics.py stats.py
│   ├── failure/    taxonomy.py analyzer.py first_deviation.py lessons.py regression.py
│   ├── optimize/   budget.py tournament.py halving.py gepa_lite.py opro.py mutations.py
│   │               pareto.py crossover.py acceptance.py overfit_lint.py fork_probe.py
│   ├── select/     robustness.py final_audit.py
│   ├── adapters/   base.py mock/ (domain, tools, db, user_sim) freya/ (client, compile, deploy, simulate) tau.py
│   └── storage/    experiments.py traces.py cache.py lessons_store.py
├── tests/          unit/ integration/ cassettes/
├── evals/          local/ regression/ holdout/
├── examples/       bundles/ (bankacılık-mock, sigorta-hasar, kredi-erteleme, TR-dil)
├── experiments/    ablation sonuçları (B0–B10)
└── submission/
```

---

## 4. Fazlar

Her fazın girdisi, çıktısı, kabul kriteri ve memo referansı var. **Çekirdek = Faz 0–9** ve 9 Ekim akşamına kadar bitmesi gerekiyor. Faz 10 kalibrasyon, Faz 11 yarışma günü.

### Faz 0 — Altyapı ve iskelet (6 Ekim)
- `pyproject.toml`, `uv.lock`, `Makefile` (`make check` = ruff + mypy + pytest), `.env.example`.
- `config.py`: bütün ayarlar tek bir `BuilderConfig` içinde (pydantic-settings). Ortam değişkenleri: `FREYA_API_KEY`, `FREYA_BASE_URL`, `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, rol başına model ID'leri (`BUILDER_MODEL_<ROL>`).
- `llm/`: `LLMClient` protokolü, OpenRouter sağlayıcısı + test için `FakeLLM`, structured output (pydantic şemasıyla doğrulama + hata durumunda 1 tamir denemesi), disk cache, token/maliyet muhasebesi, test için cassette.
- `storage/traces.py`: §36'daki trace formatı (run_id, candidate_id, scenario_id, seed, mesajlar, retrieval, tool çağrıları, state diff, policy/mutation kontrolleri, score vector, failure report, gecikme, token). JSONL yazılır, okuyucuyla birlikte gelir.
- `manifest.py`: §35'teki manifest (commit, Python, lock hash, model ID'leri, prompt versiyonları, seed, indeks versiyonu, doküman hash'leri, tool şema hash'i, optimizer config, seçilen aday).
- **Kabul:** `uv sync && make check` temiz geçer. `python -m builder --help` çalışır.

### Faz 1 — Typed IR / şemalar (6 Ekim) — §5, §10, §12, §16, §17, §18.2, §19, §70
- `TaskSpec` (§5.1). τ² eklemeleri (§3.3): `actor_capabilities {agent_tools, user_actions, external_system_actions}`, `coordination_requirements {user_must_confirm, user_must_supply, user_must_perform}`, `channel: text|voice`, `language`.
- `PolicyRule` (§5.2) + `PolicySpec` (kurallar, öncelik sırası §7.4, çelişkiler §7.2, rule→tool haritası §7.3).
- `ToolSpec` (§5.3) + `risk_class`, `requires_confirmation`, `idempotent`, `retryable`, `failure_modes`, `related_tools`, `inferred_purpose`, `preconditions`, `postconditions`, `arg_format_rules` (§57.2) + `ToolGraph` kenarları (§6: requires-before, reads-produced-data-for, mutually-exclusive-with, reverses, verifies).
- `KnowledgeSpec` (§5.4) + `DocumentRecord` (§14.5: özet, tür, varlıklar, kural ID'leri, tool referansları, çapraz bağlantılar).
- `AgentSpec` (§5.5) + `architecture` enum (A–F, §8), bölüm bazlı prompt, `few_shot_examples`, `voice_policy`, `language_policy`, `platform_overrides` (Freya config parçaları).
- `ScenarioSpec` (§5.6) + `category` (§15.1–15.13 + §61 aşırı-temkin kategorileri), `persona {prompt, style, language}`, `success_criteria` / `evaluation_prompt` (§57), `expected_tool_calls` (subset/exact, negated), `complexity`, `split`.
- `Oracle` (§16): `state`, `unchanged`, `required_communication`, `forbidden`, `policy`.
- `ScoreVector` (§17): task_success, policy, state, communication, reliability, tool_cost, token_cost, latency, **false_compliance, false_refusal**.
- `FailureReport` (§5.7) + `FailureType` enum (§18.2'deki 19 tip + `FALSE_REFUSAL`, `UNNECESSARY_ESCALATION`, `TIMEOUT`) + `failure → component` haritası (§18.3).
- `RuntimeState` (§10), `MutationCheck` (§12), `CompletionSpec` (§31), `OptimizationBudget` (§19), `Status` etiketleri (§70).
- **Kabul:** Bütün şemalar JSON şemaya dönüşür. Her biri için en az 1 round-trip testi. Örnek bundle'lar şemaya göre doğrulanır.

### Faz 2 — Platform adapter sınırı (6–7 Ekim) — §22, §23, §55, §56
- `adapters/base.py`: `PlatformAdapter` protokolü. Arayüz §22'dekinin genişletilmiş hali:
  `validate_agent`, `deploy_agent` (versiyon/branch), `run_simulation(agent, scenarios, runs, mode)`, `chat_turn`, `fork_chat(anchor, target)` (§55.4), `get_call_details`, `upsert_personas`, `upsert_unit_tests`, `export_submission`, `capabilities()` (adapterin desteklediği özellikler; örneğin `supports_fork`, `supports_workflows`, `supports_preprocess_reject`).
- **`adapters/mock/`** (sınırlı kapsam): küçük bir bankacılık domaini (müşteri, kart, adres, iade, randevu; §23), bir DB, risk sınıfları bilinen 6–8 tool, LLM ile çalışan kullanıcı simülatörü, referans eylemleri çalıştırarak beklenen durumu türeten oracle (τ yöntemi, §3.2, §58). Runtime için `runtime/reference_agent.py` kullanılır. Bu agent, `AgentSpec`'i bizim kodumuzla çalıştırır ve MutationGate'i gerçekten uygular.
- **`adapters/freya/`**:
  - `client.py`: OpenAPI'den üretilmiş veya elle yazılmış typed client. Endpointler: create/get agent, default config (B0, §55.1), versions/branches/commits/merge (§55.5), personas + generate preview (§55.3), simulations + results + iteration history, unit tests, chat send/fork/close + call details (§55.4), workflows (salt okunur + templates, §55.2), knowledge base upload/search (§55.7). Retry, rate limit ve idempotency anahtarı içerir.
  - `compile.py`: `AgentSpec` → Freya `config` JSON. §56 tablosu birebir uygulanacak:
    - prompt bölümleri → `llmConfig.systemPrompt` (≤200K karakter kontrolü)
    - tool açıklamaları ve format kuralları → `tools[].function.description/parameters`
    - MutationGate → `pre_process_code` (VERIFY), yoksa prompt checklist
    - post-write → `post_process_code` / `response_mapping` ile `status` alanı normalize edilir
    - yasak iddialar → `guardrailsConfig` regex/classifier + `regenerate`
    - ASR → `sttConfig.confidenceReask`
    - sesli sayılar/ID'ler → `ttsConfig.speechNormalization` / `textSubstitutions`
    - süre → `callTimeoutSettings`
    - `temperature`, `maxTokens`, `firstMessage`, `farewellMessage`, `retryExhaustionMessage`
  - `simulate.py`: senaryoları persona + unit test'e çevirir, simülasyonu başlatır, bitene kadar sorgular, sonuçları ve transkriptleri trace'e dönüştürür.
- **Smoke scriptleri (§67, 6 Ekim):** `scripts/smoke_freya.py`
  1. default config ile agent oluştur → 3 chat turu → call details: tool çağrıları transkriptte görünüyor mu? (VERIFY)
  2. `successCriteria` içeren 2 persona → simülasyon (`chat`, `runsPerPersona=2`) → sonuçları ve iterasyon detayını oku.
  3. `pre_process_code` bir çağrıyı reddedebiliyor mu? (VERIFY, §68 S4)
  4. fork API (VERIFY).
  Sonuçlar `docs/RULES_MAPPING.md` içine `CONFIRMED`/`VERIFY` olarak işlenecek.
- **Kabul:** Mock adapter ile uçtan uca bir simülasyon ağ olmadan çalışır. Freya adapter'ın `compile` fonksiyonu snapshot testleriyle doğrulanır. Smoke script, anahtar sağlandığında çalışır.

### Faz 3 — Ingest / compiler'lar (7 Ekim) — §6, §7, §14, §21 için özellik çıkarımı
- `ingest/bundle.py`: girdi formatından bağımsız loader. JSON, YAML, markdown ve doküman klasörlerini `InputBundle` haline getirir. Hackathon formatı belli olunca sadece buraya yeni bir parser eklenir.
- `tool_compiler.py` (§6):
  1. Deterministik ön çıkarım: fiil sözlüğü (get/list/search → READ_ONLY; create/issue/transfer/close/cancel → IRREVERSIBLE_WRITE; update/set → REVERSIBLE_WRITE; send/notify/email → EXTERNAL_SIDE_EFFECT).
  2. LLM ile semantik analiz: amaç, ön/son koşullar, hata modları, onay gerekip gerekmediği.
  3. **Uzlaştırma:** LLM ile sözlük çelişirse, daha riskli sınıf seçilir.
  4. Tool grafiği kenarları ve argüman format kuralları (ISO tarih, sayısal tutar, ID olduğu gibi; §57.2).
- `policy_compiler.py` (§7): atomik kural çıkarımı (§7.1), çelişki taraması (§7.2), rule→tool eşlemesi (§7.3), öncelik (§7.4, competition rules'tan derlenir), kaynak referansı zorunlu. Kaynağı olmayan kural reddedilir.
- `task_compiler.py`: TaskSpec, intent'ler, gerekli bilgiler, sonlandırma ve yükseltme koşulları, belirsizlikler, aktör yetenekleri, kanal ve dil tespiti (§64).
- `knowledge_compiler.py` (§14.5, §55.7): doküman ön işleme. Retriever Freya KB ise `doc_shaper.py` dokümanları böler, başlık ekler ve kural özetlerini ayrı doküman olarak üretir.
- **Kabul:** 3 örnek bundle (bankacılık-mock, sigorta-hasar, kredi-erteleme) için IR üretilir. Tool risk sınıfları elle etiketlenmiş bir altın sete karşı ölçülür (hedef ≥%95, yazma tool'larında %100 recall). Kaynaksız kural sayısı 0.

### Faz 4 — Agent sentezi (7 Ekim) — §8, §9, §11, §12, §21, §27, §31, §49, §57, §61, §63, §64
- `prompt_blocks/` (her biri ayrı ayrı optimize edilebilir, §3.9):
  `role`, `global_policy`, `rule_priority`, `action_protocol` (§11: ASK_USER … COMPLETE), `operational_state` (§10), `tool_use_policy`, `knowledge_retrieval_policy`, `mutation_guard` (§12 hard-fail kuralları), `post_write_verify`, `completion_verifier` (§31), `outcome_statement` (§57.1: sonucu açıkça söyle), `over_caution_guard` (§61: geçerli isteği reddetme, onaylanmışsa tekrar sorma, gereksiz transfer yapma), `coordination` (§3.3: DO_IT_MYSELF / ASK / GUIDE / WAIT / ESCALATE), `voice_policy` (§3.5, §63), `language_policy` (§64), `few_shot`.
- §49'daki runtime değişmezleri `global_policy` içine sabit olarak gömülür.
- `architecture_selector.py` (§21): kural tabanlı. Tool sayısı, transaksiyonel olup olmama, prosedürel policy, doküman sayısı, tool bağımlılık derinliği, açık uçluluk, ses, bütçe değerlendirilir.
- `candidate_generator.py` (§49): C1 guarded ReAct, C2 C1+think/checklist (§3.6), C3 planner/executor+verifier (receding horizon, §8C), C4 finite-state (workflow izni varsa Freya workflow'una, yoksa prompt state machine'e derlenir), C5 policy-first, C6 retrieval-first + completion verifier. Ayrıca **B0 = Freya default config** ve **A = one-shot compiler** (fallback, §8A).
- `fewshot.py` (§27): davranışsal değişmez örnek ailesi (eksik bilgi, belirsiz varlık, yazma onayı, tool hatası, kullanıcı düzeltmesi, policy reddi, onay geri çekme). Domain bilgisi içermez.
- `runtime/` (mock/reference agent için gerçek kod; Freya için prompt + platform özellikleri): `state.py` (state_version, §13), `mutation_gate.py` (deterministik kontroller + opsiyonel LLM doğrulayıcı), `completion.py`, `context_cleaner.py` (§13 blok politikası).
- **Kabul:** Her aday 3 örnek bundle için geçerli `AgentSpec` ve geçerli Freya config üretir. Prompt uzunluk bütçesi uygulanır. MutationGate birim testleri §12'deki 5 hard-fail kuralını kapsar.

### Faz 5 — Test sentezi (7–8 Ekim) — §15, §29, §30, §58, §59, §61, §64
- `policy_graph_walk.py` (IntellAgent-lite, §58): policy düğümleri karmaşıklık ağırlığı taşır, kenarlar birlikte görülme olasılığıdır. Rastgele yürüyüşle karmaşıklık kovaları (2–11) dengeli örneklenir.
- `scenario_generator.py`: her örnekten bir olay üretilir (kullanıcı isteği, başlangıç durumu, hedef policy'ler). §15'in 13 kategorisinin hepsi ve §61'in 3 kategorisi kapsanır. Kategori kotası budget'tan türetilir.
- `adversarial.py` (§29, §30): agent'ı kırmaya çalışan ayrı bir üretici. Gerçekçilik şartı var, parser hatalarını sömürmesi yasak.
- `persona_compiler.py` (§59): stiller kısa, geveze, kafası karışık, sabırsız ve anadili İngilizce olmayan. Persona LLM'i en az 2 modelden seçilir. Freya `prompt` + `systemPrompt` + `successCriteria` + `evaluationPrompt` alanlarına çıkış yapılır (§57.4).
- `oracle_generator.py` + `oracle_validate.py` (§16, §58):
  - Mock'ta beklenen durum referans eylemler **çalıştırılarak** türetilir.
  - `successCriteria` ikinci bir modelle çapraz kontrol edilir, iki model anlaşamazsa senaryo atılır.
  - Tool çağrısı assertion'ları üretilir (subset/exact/negated, §55.3).
- `split.py` (§34): train / validation (dondurulmuş, optimizer bu trace'leri görmez) / challenge-holdout. Bir persona'nın bütün koşuları aynı split'e düşer, sızıntı olmaz.
- Dil farkındalığı (§64): senaryolar görevin dilinde üretilir. Türkçe persona seti de hazırlanır.
- **Kabul:** Mock domaininde 30 senaryo üretilir. Her kategori temsil edilir. Oracle'ların %100'ü yürütülerek doğrulanır. Elle 10 senaryo örneklenip gerçekçilik kontrolü yapılır.

### Faz 6 — Değerlendirme ve istatistik (8 Ekim) — §16, §17, §57, §60
- `simulator.py`: adapter'a bağlı paralel koşucu. Eşzamanlılık limiti, timeout ve maliyet kesici içerir.
- Evaluator'lar, öncelik sırası §4 İlke 1'deki gibi: `state_eval` > `toolcall_eval` (subset/exact/negated) > `policy_eval` > `communication_eval` > `judge` (Freya'nın `evaluationPrompt` stilini taklit eder, §57.4).
- `metrics.py`: `ScoreVector`, pass^k (§3.1), en kötü durum, hata kümesi frekansı, `endedByAgent` / erken sonlandırma / timeout oranı (§57.3, §59).
- `stats.py` (§60): standart hata, kümelenmiş SE (persona = küme), eşleştirilmiş fark (aynı senaryo + seed), Beta posterior / alt güven sınırı seçimi (§17.3), güç analizi yardımcısı.
- **Kabul:** Bilinen bir iyi ve bir kötü agent üzerinde evaluator'lar doğru ayrım yapar. Stats fonksiyonları analitik örneklerle test edilir (n=30, p=0.7 → SE≈0.084).

### Faz 7 — Hata analizi ve ders hafızası (8 Ekim) — §18.2, §18.3, §20, §28, §3.8
- `first_deviation.py`: ilk belirleyici sapmayı bulur. Önce yazma adımlarına bakar (SABER, §3.7), sonra okumalara.
- `analyzer.py`: taksonomi etiketi, ilgili kural/tool, sorumlu bileşen (§18.3 haritası + LLM, TextGrad tarzı kredi ataması §3.10), minimal mutasyon önerisi.
- `regression.py` (§28): her teşhis edilen hatadan bir regresyon senaryosu üretilir. Bu senaryo train setine girer.
- `lessons.py` + `lessons_store.py` (§20): sadece doğrulanmış hatalardan ders çıkarılır. Tekrarlar birleştirilir, çelişen dersler emekli edilir, provenance ve confidence tutulur. Görev özelliği, risk sınıfı, policy kategorisi ve hata sınıfına göre aranır. **Varsayılan: builder tarafı hafıza.** Runtime hafızası ancak kurallar izin verirse eklenir (§3.8 B).
- **Kabul:** Mock'ta kasıtlı enjekte edilen 6 hata tipinden en az 5'i doğru sınıflanır.

### Faz 8 — Optimizer (8–9 Ekim) — §3.9, §3.11, §3.12, §18.4–18.6, §19, §34.4–5, §55.4, §60
- `budget.py`: duvar saati, LLM çağrısı, simülasyon, aday, token ve maliyet limitleri. Hepsi kesilebilir, `can_continue()` sunar.
- `halving.py` (§19): örneğin 8×3 → 4×5 → 2×tam validation → mutasyon → 2 challenger + şampiyon → güvenilirlik denemeleri.
- `gepa_lite.py` (§3.9): aday havuzu, trace + hata metni, hedef bileşen seçimi (§18.3), yansıtıcı mutasyon, validation skoru, Pareto tutma. Mutasyon operatörleri §18.4 + §3.12 listesinden gelir. Operatör başarı istatistiği tutulur (PromptBreeder-lite, P2).
- `opro.py` (§3.11): yedek optimizer. En iyi adaylar + hata kümeleri + skorlar girdi olarak verilir.
- `acceptance.py` (§60): bir mutasyon ancak eşleştirilmiş karşılaştırmayı önceden belirlenmiş bir marjla kazanırsa kabul edilir. Aksi halde **sadelik önceliği** gereği ebeveyn kalır.
- `pareto.py` (§18.5), `crossover.py` (§18.6, P2): bileşen bazında birleştirme, ardından yeniden validation.
- `fork_probe.py` (§55.4, P1): başarısız trace ilk sapmadan önce fork edilir, mutant aday o noktadan yeniden oynatılır.
- `overfit_lint.py` (§34.4–5): prompt değişikliklerinde pratik setindeki literal değerler (ID, tutar, isim, görev ifadesi) tespit edilir ve reddedilir.
- **Kabul:** Mock'ta B0'dan başlayan bir optimizasyon, **dondurulmuş validation** setinde istatistiksel olarak anlamlı bir iyileşme gösterir, ya da gösteremezse ebeveyni korur. Bütçe aşımı sıfır.

### Faz 9 — Seçim, denetim, teslim (9 Ekim) — §17.2–3, §35, §43
- `robustness.py`: alt güven sınırı, pass^k, birden fazla persona-LLM'de kazanma şartı (§59), false_compliance + false_refusal.
- `final_audit.py` (§43): config geçerli mi, tool'lar mevcut mu, placeholder kalmış mı, prompt limiti aşılıyor mu, pratik literal'i var mı, mutation guard ve completion verifier açık mı. Herhangi biri başarısızsa önceki şampiyona dönülür.
- `cli.py`: `python -m builder run -i challenge.json -o submission/ [--budget …] [--mode fast|full]`. `fast` modu bütçe çok küçükse one-shot + MutationGate çalıştırır (§21, §47).
- Export: Freya config JSON + manifest + seçim raporu + trace özetleri.
- `RUNBOOK.md`: temiz ortamda kurulumdan teslime kadar adımlar, ve bu adımlar gerçekten temiz bir container'da test edilir.
- **Kabul:** Görülmemiş bir görevde (örneğin sigorta-hasar, §67 9 Ekim) elle müdahale olmadan tek komutla teslim üretilir. Kurulum temiz ortamda doğrulanır.

### Faz 10 — Kalibrasyon ve ablation (8–9 Ekim; $50 bütçe içinde, küçük örneklemle) — §24–26, §58, §59, §63–65
- τ²-bench adapter (`adapters/tau.py`): τ-Bench Verified (§65) airline/retail ile geliştirme, `banking_knowledge` holdout olarak (§24). **Kod yazılır, şimdilik koşulmaz** (kullanıcı kararı). Bağımlılık opsiyonel bir extra olarak tanımlanır (`uv sync --extra tau`).
- Ablation merdiveni B0–B10 (§25). Öncelikli sorular Q1–Q7 (§26).
- Üretici kalibrasyonu: sentetik skor sıralaması ile oracle sıralaması arasındaki Spearman korelasyonu ≥0.6 olmalı (§58). Değilse önce üretici düzeltilir.
- Simülatör sağlamlığı: 2 persona-LLM (§59).
- Ses smoke testi (§63) ve Türkçe deneyi (İngilizce prompt + "kullanıcının dilinde cevap ver" ile tamamen yerelleştirilmiş prompt karşılaştırması, §64).

### Faz 11 — Yarışma günü oyun planı (10–11 Ekim) — §44, §45, §50, §68
- İlk 60–120 dakika: `RULES_MAPPING.md` tablosu doldurulur (§44 tablosu + §68'deki 9 soru + §50'deki 30 soru). Kod yazılmaz.
- §45 karar ağacına göre sadece adapter, config ve bütçe değişir.
- Öncelik (§44): geçerli teslim > sağlam baseline > otomatik testler > optimizer > mimari arama.
- 11 Ekim: bilinen iyi bir aday erkenden dondurulur, iyileştirmeler ayrı branch/soyda yapılır, son saatlerde mimari değişiklik yasak, tekrarlanabilirlik temiz ortamda yeniden test edilir.

---

## 5. Takvim

| Gün | Fazlar | Gün sonu kapısı |
|---|---|---|
| **6 Ekim (bugün)** | 0, 1, 2 (mock + Freya client iskeleti + smoke) | Şemalar + mock ile uçtan uca 1 simülasyon |
| **7 Ekim** | 2 (Freya compile/simulate), 3, 4, 5'in başı | 3 bundle → IR → 6 aday → geçerli config |
| **8 Ekim** | 5, 6, 7, 8 (halving + GEPA-lite + acceptance), 10 (τ² başlangıcı) | Mock'ta kapalı döngü: üret → simüle et → teşhis et → mutasyon → seç |
| **9 Ekim** | 8 (fork, lint), 9, 10 (ablation, ses, TR), dry-run | Görülmemiş görevde tek komutla teslim, temiz ortamda kurulum |
| **10–11 Ekim** | 11 | §44 / §45 |

**Kesme sırası** (zaman yetmezse en son yapılacaktan başlayarak çıkarılır): crossover → PromptBreeder-lite → fork probe → τ² adapter → ses/TR deneyleri → C3/C4 adayları. **Asla kesilmeyecekler:** fallback, MutationGate, deterministik evaluator, dondurulmuş validation, final audit, manifest.

---

## 6. İzlenebilirlik: memo bölümü → faz

| § | Konu | Faz |
|---|---|---|
| 0, 2, 48, 53 | Genel tez ve formalizasyon | Bütün mimari |
| 1, 50, 66, 68 | Bilinenler, sorular, düzeltmeler | 0 (kararlar), 2 (smoke), 11 |
| 3.1–3.2 | Sonuç durumu, pass^k, tek trajektoriye bağlanmama | 6 |
| 3.3 | Çift kontrol ve koordinasyon | 1, 4 |
| 3.4, 14 | Bilgi katmanı | 3, 4 (retrieval politikası) |
| 3.5, 63 | Ses | 4, 10 |
| 3.6 | think / checklist | 4 (C2) |
| 3.7, 12 | MutationGate | 4, 2 (`pre_process_code`) |
| 3.8, 20 | Ders hafızası | 7 |
| 3.9–3.13 | GEPA / TextGrad / OPRO / PromptBreeder / Reflexion | 7, 8 |
| 3.14–3.16, 8, 21 | Mimari arama ve seçici | 4, 8 |
| 3.17, 27 | Few-shot | 4, 8 |
| 4 | İlkeler | Bölüm 1 |
| 5, 70 | Typed IR ve durum etiketleri | 1 |
| 6, 7 | Tool ve policy derleme | 3 |
| 9 | Hibrit mimari | Bütün mimari |
| 10, 11, 13, 31 | Runtime state, eylem protokolü, context temizleme, completion | 4 |
| 15, 29, 30, 58, 61 | Test sentezi | 5 |
| 16, 17, 57, 60 | Oracle, skor, istatistik | 6 |
| 18, 19 | Optimizer ve bütçe | 8 |
| 22, 23, 55, 56 | Adapter ve Freya primitifleri | 2 |
| 24–26, 65 | τ², ablation | 10 |
| 28 | Hata analizi | 7 |
| 32, 33 | Model yönlendirme, sıcaklık | 0 |
| 34 | Aşırı uyum önleme | 5, 8 |
| 35, 36 | Tekrarlanabilirlik, gözlemlenebilirlik | 0, 9 |
| 37 | Repo yerleşimi | Bölüm 3 |
| 38–41 | P0–P3 | Fazlar + kesme sırası |
| 42 | Uçtan uca sözde kod | 8 (`builder.run`) |
| 43 | Final denetim | 9 |
| 44, 45, 67 | Takvim ve karar ağacı | Bölüm 5, Faz 11 |
| 46 | Yapılmayacaklar | Bölüm 1 |
| 47 | Risk kaydı | Bölüm 7 |
| 49 | İlk versiyon | Faz 4 adayları + değişmezler |
| 59 | Simülatör sağlamlığı | 5, 9, 10 |
| 62 | Endüstri emsalleri | Tez doğrulaması |
| 64 | Türkçe | 3, 5, 10 |
| 69, 51, 52 | Kaynaklar | `docs/RESEARCH.md` |

---

## 7. Risk kaydı (§47 + bu ortamdan gelen yeni riskler)

| Risk | Etki | Önlem |
|---|---|---|
| Freya API bu container'dan erişilemiyor | Yüksek | Ağ allowlist'i veya spec'i repoya koymak. Mock ile geliştirmeye devam; cassette ile adapter testleri. |
| Freya hesabı yok (kesin) | Yüksek | Adapter §55 alanlarına göre yazılır, her alan `VERIFY` olarak işaretli. Hesap açılınca smoke script, sonuçlar `RULES_MAPPING.md`'ye işlenir. Gerçek API farklı çıkarsa sadece `adapters/freya/compile.py` ve `client.py` değişir. |
| GitHub engelli (τ²-bench) | Düşük | Kod yazılır, koşu yapılmaz (kullanıcı kararı). |
| OpenRouter anahtarı / allowlist yok | Yüksek | `FakeLLM` + cassette ile deterministik testler. Gerçek koşular anahtar ve `openrouter.ai` allowlist'i sağlanınca. |
| $50 bütçe | Orta | Global maliyet sayacı ve sert kesme. Ucuz modeller simülasyon rollerinde. Validation n'i §60 güç analiziyle minimumda tutulur. |
| §47'deki bütün riskler | — | Önlemleri ilgili fazlara gömüldü (adapter sınırı, fast mode, mock, ses bayrağı, retrieval, holdout, şema doğrulama, fallback, state versioning, prompt bütçesi, kalibrasyon). |

---

## 8. Tanım: "çekirdek hazır"

1. `uv sync && make check` temiz geçer.
2. `python -m builder run -i examples/bundles/insurance_claims -o out/ --adapter mock` görülmemiş bir görev için manifestli bir teslim üretir.
3. Aynı komut `--adapter freya` ile Freya üzerinde agent oluşturur, simüle eder ve seçer (anahtar sağlandığında).
4. Seçilen aday dondurulmuş validation setinde B0'dan kötü değilse kabul edilir. Kötüyse fallback seçilir ve bu durum raporlanır.
5. Bütçe aşımı 0, kaynaksız kural 0, final audit ihlali 0.

---

## 9. Durum (9 Ekim akşamı)

Durum üç ayrı soruya bölündü: **kod var mı**, **pipeline'a bağlı mı** (varsayılan `builder run` yolunda çalışıyor mu), **canlı doğrulandı mı** (gerçek LLM koşusunda gözlendi mi). "✅" tek başına artık kullanılmıyor (GAP_ANALYSIS B3).

| Bileşen | Kod | Bağlı | Canlı doğrulandı | Not |
|---|---|---|---|---|
| Altyapı, CLI, cache, $ ledger, manifest, model profilleri | ✅ | ✅ | ✅ | LLM çağrıları artık paralel (A4) |
| Şemalar / IR | ✅ | ✅ | ✅ | `RuntimeState`, `CompletionSpec`, `Oracle.policy`, `ScoreVector.reliability` tanımlı ama kullanılmıyor |
| Mock adapter + LLM-simüle backend | ✅ | ✅ | ✅ | 4 domain (banka deterministik; sigorta, kredi, TR simüle) |
| Freya adapter | ✅ | ✅ (`--adapter freya`) | ❌ | Anahtar yok. Ham yanıt kaydı, platformun kendi sonuçları, `maxRetries=0`, persona LLM override eklendi. Tool çağrısı parse'ı tahmin. |
| Freya export | ✅ | ✅ | ❌ | Tek yol (`submission_config`): Freya varken doğrulanmış tam config, yokken açıkça "overlay" |
| Tool derleyici + risk | ✅ | ✅ | ✅ | Kanıtlı `REQUIRES_BEFORE` (policy alıntısı); LLM önkoşulu tavsiye |
| Policy / task derleyici | ✅ | ✅ | ✅ | |
| Aday üretimi (B0, A, C1–C6) | ✅ | ✅ | ✅ | Çeşitlilik gerçekte prompt-blok düzeyinde (C2↔C3 %99). Dürüst tanım: **prompt-blok optimizer'ı + birkaç gerçek eksen** (gate açık/kapalı, kısa/uzun prompt, few-shot, blok sırası) |
| MutationGate (yerel) | ✅ | ✅ | ✅ | 8 kontrolün 4'ü deterministik; Freya'da yok (yalnızca prompt) |
| Senaryo üretimi + oracle | ✅ | ✅ | ✅ | IntellAgent-lite, çalıştırılarak türetilen oracle, JUDGE denetimi, kararlılık replay'i |
| `adversarial_scenarios` | ✅ | ❌ | ❌ | Bilerek bağlanmadı (kanıt yok) |
| Değerlendirme + istatistik | ✅ | ✅ | ✅ | Rapor artık tespit edilebilir en küçük farkı yazıyor |
| Hata analizi + ders hafızası | ✅ | ✅ | ✅ | Dersler aday üretiminde kullanılmıyor, yalnızca mutasyonda |
| GEPA-lite + halving | ✅ | ✅ | ⚠️ | Yeni kabul kuralı (boş mutasyon %3–6); canlı koşuda henüz kabul edilmiş mutasyon yok |
| Crossover | ✅ | ❌ (`enable_crossover=False`, CLI bayrağı yok) | ❌ | |
| OPRO | ✅ | ❌ | ❌ | |
| `toggle_fewshot` (few-shot araması) | ✅ | ❌ | ❌ | |
| `doc_shaper` | ✅ | ❌ | ❌ | Freya'da KB API'si yok |
| Fork probe | ✅ | ⚠️ (Freya'da `supports_fork=False`) | ❌ | |
| Seçim / final audit / holdout | ✅ | ✅ | ✅ | Audit'e kural kapsamı ve yerel-araç atfı kontrolü eklendi |
| τ² adapter | ✅ | ❌ | ❌ | Karar gereği koşulmadı, testsiz |
| Dense/hybrid retrieval | ❌ | — | — | Yalnızca BM25 |

**Modeller (OpenRouter, `test` profili):** builder / optimizer / analyzer / agent = `anthropic/claude-haiku-5.5`; judge / user_sim = `openai/gpt-6-luna-pro`; user_sim_alt = `google/gemini-2.5-flash`. `competition` profili builder ve optimizer'ı `anthropic/claude-sonnet-5.5`'e çıkarır (9 Ekim'de küçük bir koşuyla doğrulandı).

**Temel karar (GAP_ANALYSIS + ikinci review):** hedef daha akıllı bir builder değil, **builder'ın kazandırdığını kanıtlamak**. Asgari kanıt: aynı görülmemiş senaryolar, aynı tohum/persona, aynı runtime ve aynı evaluator üzerinde B0 / A / C (optimize edilmiş) karşılaştırması; başarı, false compliance, false refusal ve gecikme sütunlarıyla. Yerel skor yalnızca ön elemedir; Freya açılınca seçim Freya üzerinde yapılır.

**Açık işler (Freya hariç), öncelik sırasıyla:**
1. Kayıtlı kıyas koşusu: B0 vs A vs C, ≥2 deneme, sonuç `experiments/` altına (GAP B2).
2. Optimizer'ın yeni kabul kuralıyla dondurulmuş validation'da kazanç gösterdiği bir koşu (Faz 8 kabul kriteri).
3. Ablation Q1 (MutationGate faydası).
4. Gerçek aday çeşitliliği ya da adayların bu şekilde dürüstçe tanımlanması (A7).
5. Embedding tabanlı retrieval; Spearman kalibrasyonu (τ² gerektiriyor, ertelendi).

**Freya'ya bağlı açık işler:**
- Smoke testi; sonra parser'ı ve test fixture'ını kayıtlı gerçek yanıta göre yeniden yazmak.
- `handler` değerleri.
- `pre_process_code` bir çağrıyı reddedebiliyor mu?
- Unit-test assertion'larının simülasyona bağlanması.
- Persona LLM değerleri.
- C4 için workflow derleyicisi (workflow izni çıkarsa).

### 9.1 Kod inceleme turu (6 Ekim gece)

Kendi kodumun gözden geçirilmesinde bulunan ve düzeltilen hatalar:

| Alan | Hata | Etki | Düzeltme |
|---|---|---|---|
| MutationGate | Boolean, küçük sayı, ISO tarih ve serbest metin argümanları "grounded değil" diye engelleniyordu | `open_claim(injuries=false)` gibi çağrılar hiç geçmiyordu; sistematik sahte ret | Grounding sadece ID benzeri değerler, e-posta ve tutarlar için; ayraç duyarsız eşleşme |
| MutationGate | "That's not right" ve "ok, but…?" onay sayılıyordu | Onaysız yazma | Olumsuzluk listesi genişletildi; soru içeren cevap onay değil |
| State diff | `session` kayıtları beklenmeyen değişiklik sayılıyordu | LLM-backend'li domainlerde sahte false-compliance | Kök `session` hariç tutuldu |
| Evaluator | Hakem parse hatası bütün aramayı çökertiyordu | Fallback'e düşme | Hakem hatası yutuluyor, deterministik kontroller geçerli kalıyor |
| Evaluator | Başarılı salt-okuma senaryoları false-refusal sayılıyordu | Seçim metriği bozuk | Sadece yazma beklentisi ve başarısızlık durumunda |
| Trace | Agent metni tool çağrılarından önce yazılıyordu | Hakem sonucu çağrıdan önce görüp yanlış ceza verebilirdi | Olaylar gerçek sırada |
| Optimizer | Erken elenen adaylar az senaryoyla ortalamada öne geçebiliyordu | Yanlış finalist | Beta alt sınırıyla sıralama |
| Lessons | Aynı kanıt güveni tekrar tekrar artırıyordu; geri bildirim hiç çağrılmıyordu | Ders hafızası şişiyordu, emeklilik çalışmıyordu | Düzeltildi ve bağlandı |
| Pipeline | Beklenmeyen istisnada hiçbir şey export edilmiyordu | Teslim kaybı (§43 ihlali) | Her aşamada fallback ve export garanti |
| Generator | Geçersiz referans eylemli senaryo direkt atılıyordu | Bütçe israfı | Hata geri beslemeli 1 onarım denemesi |
| OpenRouter | `response_format` ve ortada system mesajı bazı sağlayıcılarda 400 verir; boş assistant mesajı reddedilir | Gerçek koşuda çökme riski | Sağlayıcıya göre `json_mode`; şema talimatı user mesajı; boş mesaj filtresi |
| Cache | Paralel yazımda tmp dosyası çakışması | Nadiren bozuk cache | Thread'e özel tmp |

### 9.2 Canlı koşu bulguları (9 Ekim, sahte banka + sigorta)

Toplam harcama bu bölümün yazıldığı anda yaklaşık $2. Her bulgu için regresyon testi eklendi.

| Alan | Bulgu | Düzeltme |
|---|---|---|
| Onay | "Do you confirm freezing…?" → "Yes" doğal onayı reddediliyordu; agent pes edip insana aktarıyordu (false refusal) | Soruda bu işlem geçiyorsa doğal onay tek kullanımlık geçerli; sadece onay eksikse kesin onay sorusu otomatik üretiliyor |
| Onay | Sorudaki ifadeyi tekrar eden ("Yes, I confirm freezing the card…") veya aynı mesajda başka bir şey soran cevaplar reddediliyordu | Cümle bazlı değerlendirme: evet cümlesi sıkı kontrol, diğer cümleler ancak eylemi düzeltir/olumsuzlarsa veya aynı biçimde yeni bir değer getirirse reddeder |
| Onay | Müşteri az önce onayladığı işlem için ikinci kez soruluyordu | `request_action_confirmation` taze doğal onay varsa `already_confirmed` döndürüyor |
| Grounding | Kullanıcının alakasız bir mesajı bütün `_id` faktlarını siliyordu; başka bir müşterinin ID'si doğrulanmış kimliği siliyordu | Sadece aynı biçimde farklı bir değer gelirse geçersiz; doğrulanmış kimlik korunuyor |
| Agent | Seçenek argümanını (neden: lost/stolen) müşteri söylemeden seçiyordu | Global kural: seçenek müşteri söylemediyse sorulur |
| Simülatör | Agent'ın yanlış detayını ve hedef dışı teklifleri kabul ediyordu | Çelişen detayı düzeltir, hedef dışı teklifi reddeder |
| Üretici | `forbidden_tool_calls` içine "freeze_card with card_id K-2" yazılınca senaryo atılıyordu (8'de 3) | Argümanlı yasaklar `negated` expected call olarak isteniyor; açıklamalı girdiler atılıyor |
| Üretici | Hikâyedeki ön koşul (önceki sipariş) state'te yoktu | Prompt + oracle denetimi ön koşulu kontrol ediyor |
| Metrik | Kısmi ret senaryolarında doğru yazmalar false compliance sayılıyordu | Sadece hiç yazma beklenmiyorsa |
| Audit | Tool dokümanındaki örnek ID (`e.g. C-1001`) "pratik literal" sayılıp en iyi aday eleniyordu | Whitelist tool argüman açıklamalarını da kapsıyor |
| Halving | Hepsi berabere bir turda liste sırası B0'ı kayırıyordu | Liderle berabere olanlar elenmiyor |
| Simüle backend | LLM backend konuşmayı göremediği halde "müşteri onaylamadı" diye reddediyordu; sigorta görevinde bütün adaylar 0 | Sadece veri seviyesinde ret |
| Simüle backend | Backend'in ürettiği ID/tarih ve serbest metin birebir bekleniyordu | Agent'ın verdiği değerler birebir; üretilen değerler ve serbest metin sadece var olmalı; değişen kayıttaki ekstra alanlar tolere ediliyor |

