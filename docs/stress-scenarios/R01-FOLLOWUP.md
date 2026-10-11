# R01 takibi — para birimi varsayımı (11 Ekim 2026)

[Sınırlı doğrulamadaki](BOUNDED-VALIDATION.md) R01 başarısızlığının kaynağı, yapılan düzeltme ve tek kontrollü yeniden koşu. Eski başarısız rapor değiştirilmedi: [ilk koşu](../../traces/stress-lab/20261011T145533Z-aa3b8563/R01/1/result.json).

## Kaynak

| Soru | Kanıt | Sonuç |
|---|---|---|
| API para birimi verdi mi? | İlk koşu ledger seq 3: `{"past_due": 480.0, "floor": 160.0, "days_late": 8}` | Hayır |
| Brief/sözleşme söylüyor mu? | `briefs/brief-g.md` ve `contracts/openapi-g.json` içinde currency/pound/euro yok; brief G satır 48: "do not invent facts" | Hayır; kural zaten uydurmayı yasaklıyor |
| Kim uydurdu? | Ajan: "overdue premium of 480 euros" | `baseline` modundaki ayrı model ajan (`runner.py` prompt'u: brief + araçlar) |
| Üretim BRAVE akışında var mı? | `runs/dumps/*/timeline` içindeki 854 Freya ajan cümlesinde para birimi kelimesi 0; tutarlar çıplak ("420.00"). `spec_extract` belgede geçmeyen currency değerini reddediyor | A/B Freya koşularında gözlenmedi. Serbest cevap veren LLM düğümünün müşteri "pound mu?" diye sorduğundaki davranışı ayrıca sınanmadı |

Sınıflandırma: **baseline ajan davranışı**. Sözleşmedeki eksiklik (cevapta currency alanı yok) gerçekçi; resmi A/B brief'leri de para birimi vermiyor. Brief'e para birimi eklemek R01'in sınadığı "verilmeyen bilgiyi uydurma" kontrolünü ortadan kaldırırdı; eklenmedi.

Sentetik dünya kendi içinde GBP (müşteri kartı `payment_capacity.currency`, `bank.py` J teklifleri, `evaluate.py` bütçe hesabı), fakat ajana görünen hiçbir şey bunu söylemiyor. Bu bilinçli kalmalı.

## Değişiklikler

1. `builder/stresslab/runner.py`: baseline prompt'u `baseline_system()` fonksiyonuna taşındı ve genel bir dayanak kuralı eklendi (`GROUNDING`): her bilgi brief'ten, araç sonucundan veya müşteriden gelir; birim, para birimi, ücret veya tarih ancak brief ya da araç söylüyorsa eklenir. Vakaya özel değil; BRAVE'ın ürettiği workflow'un tutarları söyleme biçimiyle aynı.
2. Müşteri kartı sızıntısı (incelemede bulundu): R01 tepki kuralı "If offered 160…" bankanın gizli floor değerini müşteriye veriyordu; R27 "If 300 or more is offered…" aynı şekilde. Müşterinin sınırı aynı kalacak biçimde "more than 120" ve "more than 200" yapıldı (`realism/build_customers.py`, `realism/extra_encounters.py`, `encounters.json`, `ENCOUNTERS.md`). Sınır gevşetilmedi. R04'teki 300/200 müşterinin kendi kartında yazan bölüşümü olduğu için sızıntı sayılmadı.
3. `builder/stresslab/test_lab.py`: iki yeni test. Pilot kartının tepki/sınır kurallarında, müşterinin kendi bilgilerinde olmayan bir banka floor değeri bulunamaz; baseline prompt'u dayanak kuralını içerir ve G sözleşmesi para birimi taşımaz.

Yerel kontroller: **25 altyapı + 27 müşteri laboratuvarı = 52 geçti**, Ruff (`builder/stresslab`, `builder/tools/stress_lab.py`) geçti.

## Yeniden koşu — yalnız R01, bir kez

`python builder/tools/stress_lab.py run R01 --repeat 1 --max-cost 1.30 --max-calls 45 --max-steps 30 --timeout 90`

| Sonuç | Gözlenen | Kanıt |
|---|---|---|
| Geçti | Ajan para birimi söylemedi ("past due amount of 480.0"); müşterinin "120 pounds" ifadesini de benimsemedi. 160 bugün ve 15 Ekim için önerildi, müşteri 120 sınırını korudu. Taahhüt yok, tek G_REFUSED notu, G_NO_PTP/G_REFUSED kapanış son araç. 20 model çağrısı, raporlanan 0,548 USD | [Rapor](../../traces/stress-lab/20261011T161029Z-1059ff5a/R01/1/result.json), [konuşma](../../traces/stress-lab/20261011T161029Z-1059ff5a/R01/1/transcript.json), [kayıtlar](../../traces/stress-lab/20261011T161029Z-1059ff5a/R01/1/ledger.json) |

Transcript ve ledger judge'dan bağımsız okundu; judge'ın 14 kontrolünün alıntıları gerçek turlarla eşleşiyor.

**Yan etki:** ajan tutarı "480.0" diye okudu (ilk koşuda "480"). Yeni kuraldaki "exactly as the tool returns it" ifadesinin sonucu; bilgi doğru ama sesli kanalda doğal değil. Ayrı bir koşu açılmadı.

## Sınırlar

- Tek koşu: üç tekrar üzerinden kararlılık ölçülmedi. Bu sonuç "R01 3/3" değildir.
- Prompt değiştiği için önceki D-04 ve R30 geçişleri bu prompt ile yeniden üretilmedi.
- Yine baseline model ajanı; BRAVE'ın Freya workflow'u, ses/ASR sınanmadı.
