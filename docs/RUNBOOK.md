# Runbook: hazırlık (7–9 Ekim) ve yarışma günü (10–11 Ekim)

## 0. Ortam

```bash
uv sync && make check                 # ağ ve anahtar olmadan da geçmeli
cp .env.example .env                  # OPENROUTER_API_KEY, (varsa) FREYA_API_KEY
uv run python -m builder info         # etkin config + şimdiye kadarki harcama
```

Cloud ortamında yapılması gerekenler: `openrouter.ai` adresini Allowed domains'e ekleyin ve `OPENROUTER_API_KEY`'i secret olarak tanımlayın. Freya'ya erişim için `docs.freyavoice.ai` ve API host'u da allowlist'e eklenmeli.

## 1. Hazırlık sırası ($50 bütçe)

Model profili: hazırlıkta varsayılan `BUILDER_PROFILE=test` kullanılır (builder/optimizer/analyzer/agent = `anthropic/claude-haiku-5.5`, judge/user_sim = farklı aileler). Yarışma günü `BUILDER_PROFILE=competition` ile builder/optimizer `anthropic/claude-sonnet-5.5`'e geçer. Tek bir rolü `BUILDER_MODEL_<ROL>` ile ayrıca ezebilirsiniz.

1. **İlk ucuz gerçek koşu (~$1–3):**
   `builder run -i examples/bundles/retail_bank.json -o out/bank --scenarios 8 --candidates 3 --generations 1 --trials 1`
   `out/bank/report.md` dosyasını okuyun ve aşağıdakileri kontrol edin:
   - B0 diğer adaylardan kötü olmalı.
   - Reddedilen senaryo oranı makul olmalı.
   - Trace'ler (`runs/traces.jsonl`) anlamlı olmalı.
2. **Görülmemiş görev provası (§67, 9 Ekim):** `loan_deferral.json`, `insurance_claims` ve `tr_card_limit.yaml` bundle'larını elle müdahale olmadan tek komutla çalıştırın. Bu domainler için mock'u LLM simüle ediyor.
3. **Ablation (Q1):**
   `scripts/ablation.py --bundle examples/bundles/retail_bank.json --scenarios out/bank/scenarios.jsonl --trials 2`
4. **Freya hesabı açılınca:** `scripts/smoke_freya.py` çalıştırın, sonuçları `docs/RULES_MAPPING.md` B tablosuna işleyin ve `ENDPOINTS` / `FIELD_STATUS` tablolarını düzeltin. İlk simülasyonun ham yanıtı her trace'te `platform_result.raw` altında saklanır; tool çağrılarının hangi alanda göründüğünü buradan okuyup `messages_to_events` ayrıştırıcısını ve test fixture'ını buna göre düzeltin. Persona LLM çeşitliliği için `FREYA_PERSONA_LLMS` ayarlayın.
5. **Temiz ortam testi:** repoyu yeni bir dizine clone'layın ve `uv sync && make check && builder run ... --adapter none` çalıştırın.

## 2. Yarışma günü

**İlk 60–120 dakika:** sadece `docs/RULES_MAPPING.md` A tablosu doldurulur, kod yazılmaz (§44).

Sonra §45 karar ağacına göre hareket edin:

| Kural | Yapılacak |
|---|---|
| Sadece prompt/config üretilebiliyor | Varsayılan yol, değişiklik yok |
| Workflow serbest | C4'ün Freya workflow derleyicisini yazın (`synth/` + `adapters/freya/compile.py`) |
| Simülasyon bütçesi çok küçük | `--scenarios 8 --candidates 3 --generations 0 --trials 1`, veya `--mode fast` |
| Simülasyon bütçesi büyük | `--scenarios 30 --generations 3 --trials 3` (crossover pipeline'a bağlı değil; açmak kod değişikliği ister) |
| Büyük doküman seti | Retrieval-first aday üst sıraya çıkar; platform KB zorunluysa `doc_shaper` kullanın |
| Ses puanlanıyor | `channel=voice` olduğundan emin olun; `voice_policy` + `confidenceReask` |
| Kendi simülatörü yok | `--adapter mock` (LLM tool backend) ile self-test. Teslim için `freya_config_overlay.json` platform varsayılanına birleştirilir (`merge_overlay`); mümkünse `--adapter freya` ile doğrulanmış tam config üretin |

**Öncelik sırası:** geçerli teslim > sağlam baseline (A) > otomatik testler > optimizer > mimari arama.

## 3. 11 Ekim

- Bilinen iyi bir çıktıyı erkenden dondurun: `out/` + `manifest.json` + commit hash.
- İyileştirmeleri yeni bir out dizininde yapın ve dondurulmuş validation ile karşılaştırın.
- Son 3 saatte mimari değişiklik yapmayın.
- Teslimden önce temiz ortam testini tekrarlayın.

## 4. Sorun giderme

| Belirti | Neden / çözüm |
|---|---|
| `[warn] OPENROUTER_API_KEY is not set` | Anahtar yok, FAST modda A export edilir |
| `BudgetExceeded` | `MAX_COST_USD` aşıldı; `.builder/cost_ledger.jsonl` dosyasına bakın |
| `only N valid scenarios generated` | `report.md` içindeki "scenario rejected" satırları; genelde referans eylemler başarısız. Üretici promptunu ya da seed state'i kontrol edin |
| Bütün adaylar 0 | Önce trace'e bakın: user simülatörü erken mi `###STOP###` diyor, tool hatası mı var |
| Audit reddi | `report.md` → "audit X: [...]" satırı; genelde pratik literal'i, `fallback` otomatik devreye girer |
