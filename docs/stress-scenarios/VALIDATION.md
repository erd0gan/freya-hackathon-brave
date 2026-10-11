# İlk altyapı doğrulaması — 11 Ekim 2026

Sonraki sınırlı doğrulamada üç görüşme tek tek çalıştırıldı: D-04 ve R30 geçti; R01 para birimi uydurma hatasını yakaladı ve başarısız kaldı. Tüm paket başlatılmadı. [Sınırlı koşunun sonuçları ve kanıtları](BOUNDED-VALIDATION.md).

23 yeni altyapı/HTTP testi ve mevcut 27 müşteri laboratuvarı kontrolü geçti: toplam **50 yerel kontrol**. Yeni kodun Ruff kontrolü de geçti. Bunlar 94 doğal dil görüşmesinin veya Freya ajanının başarı oranı değildir.

Model müşteri ve model ajanla beş görüşme koşusu yapıldı. Son başarılı F-08 ve R04 koşuları ayrı baseline model ajanını kullanır; BRAVE'ın Freya workflow'u değildir. Bütün koşular, başarısızlıklar ve onarım öncesi sonuçlar korunmuştur.

| Koşu | Sonuç | Kanıt |
|---|---|---|
| F-08 ilk | Kayıt geçti, konuşma değerlendirilemedi: judge API'ye yalnız system mesajı verilmişti | [Rapor](../../traces/stress-lab/20261011T142845Z-42b26e04/F-08/1/result.json) |
| F-08 düzeltme sonrası | Kayıt ve konuşma geçti; güvenlikten önce insan isteği, F_GENERAL transfer, F_XFER/F_HUMAN kapanış | [Rapor](../../traces/stress-lab/20261011T142954Z-64ef8b6a/F-08/1/result.json) |
| R04 ilk | Yarım kaldı: müşteri modeli ayrılan çıktı bütçesinde konuşma üretemedi; ürün metadata'sı da tamamlandı | [Rapor](../../traces/stress-lab/20261011T142954Z-64ef8b6a/R04/1/result.json) |
| R04 ikinci | Değerlendirilemedi: card/car eşleştirmesi yanlış ürün tarifine yol açtı; müşteri itiraz etti. Judge JSON'u kesildi | [Rapor](../../traces/stress-lab/20261011T143237Z-a6e0b53c/R04/1/result.json) |
| R04 düzeltme sonrası | Konuşma, gözlenen dal ve bütçe geçti: kart 300, bireysel kredi 200, araç kredisi ret notu, F_PTP | [Rapor](../../traces/stress-lab/20261011T143550Z-755a8325/R04/1/result.json), [kayıtlar](../../traces/stress-lab/20261011T143550Z-755a8325/R04/1/ledger.json), [konuşma](../../traces/stress-lab/20261011T143550Z-755a8325/R04/1/transcript.json) |

Beş koşunun toplam raporlanan model maliyeti **2,164284 USD**. Provider'ın maliyetini vermediği başarısız istekler bu toplamda eksik olabilir. Varsayılan agent/customer/judge modeli mevcut builder ayarından `anthropic/claude-opus-5.5` oldu. Üç tekrar üzerinden kararlılık henüz ölçülmedi.

R04'ün `record_status=requires_branch_review` alanı deterministik denetimin tek başına pilot dalını seçmediğini gösterir. Son raporda judge dal 0'ı konuşma kanıtıyla seçip gereken kayıtları kontrol etti, `overall_status=pass` üretti. Sabit beklenen kayıtlı ana vakalar ile dallanan pilotların değerlendirmeleri bu nedenle farklıdır.

Gerçek HTTP testinde I-04'ün yanıtı soket kapatılarak kaybettirildi; sonrasında aynı anahtar için SUCCEEDED ve tek claim bulundu. D-04'te iki kesin hata sonrasında tek kayıt, E-02'de HTTP 200/REJECTED, E-07'de kısmi başarı, J-02'de fiyat değişimi ve J-03'te teklif süresine tam eşit saat ayrıca kontrol edildi. Aynı vaka oturumlarının birbirine kayıt veya kimlik durumu taşımadığı doğrulandı. İncelemesiz sonucu başarı sayma ve pilotu dal kanıtı olmadan geçirme girişimleri kontrol testlerinde reddedildi.

Freya URL adapter'ı gerçek `submission/brief-a.workflow.json` yapısının kopyasında doğrulandı; bu bir politika aktarımı veya gerçek Freya görüşmesi değildi. C–J sözleşmeleri export edilebilir ve 94 fixture derlenebilir. Bütün 94 görüşme çalıştırılmadı; I/J için collections generator dışında uygun workflow gerekir; Freya J-03 saat ilerletme henüz desteklenmiyor. Ses/ASR değerlendirmesi yapılmadı. [Çalıştırma rehberi](RUNNING.md).
