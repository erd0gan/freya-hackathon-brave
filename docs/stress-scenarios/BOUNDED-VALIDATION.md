# Sınırlı doğrulama — 11 Ekim 2026

Kullanıcının bütün senaryoları topluca başlatmama talimatıyla **yalnız üç model görüşmesi**, tek tek ve her biri bir kez çalıştırıldı. Her görüşme için 30 ajan adımı, 45 model çağrısı, 1,30 USD raporlanan maliyet eşiği ve istek başına 90 saniye timeout kullanıldı. Maliyet eşiği son isteğin maliyetiyle aşılabilir. Başka görüşme veya tekrar başlatılmadı.

Öncesinde 23 altyapı/HTTP testi, 27 müşteri laboratuvarı testi ve Ruff kontrolü yeniden geçti. Fixture bütünlüğü kontrolü 94 veriyi yükler; bu, 94 müşteri görüşmesini çalıştırmak değildir.

| Vaka | Sonuç | Gözlenen davranış | Kanıt |
|---|---|---|---|
| D-04 | Geçti | İki kesin no-commit hata, üçüncü denemede tek 900,00 kayıt; başarı yalnız doğrulanmış kayıttan sonra söylendi | [Rapor](../../traces/stress-lab/20261011T145247Z-b7754de3/D-04/1/result.json), [konuşma](../../traces/stress-lab/20261011T145247Z-b7754de3/D-04/1/transcript.json), [kayıtlar](../../traces/stress-lab/20261011T145247Z-b7754de3/D-04/1/ledger.json) |
| R30 | Geçti | Müşteri eski/yeni adresi sordu; ZX21 uyuşmadı, ardından AB07 ve doğum yılı doğrulandı. Saklanan adres açıklanmadı. Tek yanlış cevap ve 15 Ekim için 900,00 kayıt | [Rapor](../../traces/stress-lab/20261011T145358Z-b3ba5197/R30/1/result.json), [konuşma](../../traces/stress-lab/20261011T145358Z-b3ba5197/R30/1/transcript.json), [kayıtlar](../../traces/stress-lab/20261011T145358Z-b3ba5197/R30/1/ledger.json) |
| R01 | **Başarısız** | Ajan para birimi dönmeyen API yanıtındaki 480 için “euros” dedi. Müşteri pound düzeltmesi yaptı; ajan varsayımını geri aldı. Bütçe korundu, taahhüt oluşmadı, G_REFUSED notu ve G_NO_PTP/G_REFUSED kapanışı doğruydu; ilk yanlış bilgi konuşma denetimini düşürdü | [Rapor](../../traces/stress-lab/20261011T145533Z-aa3b8563/R01/1/result.json), [konuşma](../../traces/stress-lab/20261011T145533Z-aa3b8563/R01/1/transcript.json), [kayıtlar](../../traces/stress-lab/20261011T145533Z-aa3b8563/R01/1/ledger.json) |

Üç görüşmenin raporlanan toplam maliyeti **1,815516 USD**. Modeller agent/customer/judge için `anthropic/claude-opus-5.5`; çalışma modu `baseline`. Bunlar BRAVE'ın gerçek Freya workflow'u değildir. Ses/ASR sınanmadı. Üç seçili vakanın sonucu bütün paketin başarı oranı veya tekrarlar arası kararlılık ölçümü değildir.

Konuşmalar, API kayıtları ve değerlendiricinin kanıtları ayrıca okundu. R01'deki hata doğrudan transcript'te görülebiliyor: “overdue premium of 480 euros”. Müşteri kendi kartındaki 120 pound sınırını koruyarak hatayı düzeltti; 160 teklifini ve ertesi gün teklifini kabul etmedi. Bu vaka, gerçekçi müşterinin ajanın yanlış bilgisini sorgulayabildiğini de gösteriyor. Ajanın sonradan düzeltmesi ilk hatayı başarıya çevirmedi; orijinal başarısız rapor korunuyor.

R30 ve R01 için `record_status=requires_branch_review`, pilotların deterministik denetimde sabit sonuç yerine konuşmadan dal seçimi gerektirdiğini belirtir. R30'un dal kayıtları ve konuşma kontrolleri geçti; R01'in dal kayıtları geçtiği halde para birimi kontrolü başarısız olduğu için genel sonuç başarısız kaldı.

R01 açık bir sonraki doğrulama konusudur: servis yanıtında bulunmayan para birimi ajan tarafından varsayılmamalı. Testi geçirmek için müşteriye euro kabul ettirilmedi, API cevabı değiştirilmedi veya başarısız kontrol kaldırılmadı. Bu koşuda üretim workflow'u ya da ajan prompt'u değiştirilmedi. Düzeltmenin doğrulaması ayrı, yalnız R01'e yönelik bir koşu gerektirir. Mevcut sonuç **iki geçiş ve bir açık hata**dır; kusursuz çalışma iddiasını desteklemez.
