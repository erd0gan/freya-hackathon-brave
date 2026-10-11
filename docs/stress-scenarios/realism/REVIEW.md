# Müşteri gerçekçiliği incelemesi ve yeni test tasarımı

11 Ekim 2026. Bu çalışma sentetik test tasarımıdır. Gerçek çağrı kaydı veya müşteri örneklemi kullanılmadı; aşağıdaki profiller nüfusu temsil ettikleri iddiası taşımaz.

## Önceki pakette ne eksikti?

| Bulgu | Kanıt / kapsam | Düzeltme |
|---|---|---|
| Müşteri çok kolay kabul ediyor | 64 vakada “önerilen günü kabul eder” biçiminde çok sayıda sabit yol var; gerekli kural regresyonları ama davranış testi olarak sınırlı | Müşteri likiditesi, takvimi ve rıza sınırı önceden sabitlenir; teklif uygun değilse ajan başarılı konuşsa da reddedebilir |
| Tarz ile kişilik aynı şey gibi ele alınmış | `pieces`, `talkative` ve `in_words` ifade biçimleridir; güven, amaç, bütçe ve bilgi durumunu modellemez | 30 durumsal müşteri profili; her birinde motivasyon, bilgi sınırı, işbirliği ve sürtüşme tetikleyicileri |
| Otomatik “Yes, I confirm” | Resmi runner `PERSONA` içindeki eşleşen read-back'e sabit onay kuralı; bizim ilk pakette de benzer kolaylaştırma | Anlama, teklif kabulü ve son işlem rızası ayrı tutulur; soru/koşul/geri çekme ayrıca işlenir |
| Her tarz değişiminde aynı sonucun beklenmesi fazla güçlü | Önceki `EVALUATION.md` tüm varyantlar için aynı oracle diyordu | Yalnız anlamı koruyan paraphrase için aynı sonuç; gerçek bütçe/güven/zaman değişirse dallanan oracle gerekir |
| Müşteri banka kurallarını biliyormuş gibi davranabilir | Final deadline, floor ve outcome kodu oyuncuya verilirse sınav cevabı müşteri prompt'una sızar | Banka durumu, müşteri bilgisi ve değerlendirici beklenen sonucu ayrı veri alanları; müşteri export'u izinli alanları seçer |
| Bütün retler başarısızlık gibi görülebilir | Tahsilat odaklı test kolayca “mutlaka söz al” hedefine kayabilir | Uygun para yoksa, rıza yoksa veya kimlik reddedilmişse doğru records/no_promise veya güvenli kapanış başarı olabilir |
| Yapay konuşma kesilmeleri gerçek konuşmayla karışabilir | Arşivde 74 farklı sentetik çağrı, 1.020 müşteri turu, noktalama sezgisiyle işaretli 588 kesik tur; 5 müşteri kapanışı, 2 süre aşımı | Bunlar ses hatası veya gerçek müşteri sıklığı kanıtı değil. Uzun bozuk çıktı ayrı simulator robustness hattında |
| Yerel reviewer son onayı semantik doğrulamıyor | `builder/fb/harness/review.py` R4: tutarı içeren bir ajan satırından sonra herhangi bir müşteri turu var mı? Açık rıza, doğru ürün/tarih anlamını çözmüyor | Olumlu/olumsuz rıza kalibrasyon çiftleri ve insan/ayrı hakem incelemesi. R4'ün boş bulgusu tam politika geçişi değildir |
| Aynı sentetik kişi ve aynı güvenlik cevapları çok tekrar ediyor | İlk pakette ortak Alex Rowan ve brief başına kimlik | Yeni pilot karşılaşmalarda farklı kurgu kişiler, ayrı bilgi/bütçe ve durum. Yaş/isim/aksan üzerinden davranış atanmaz |

Arşiv sayıları mevcut `docs/research-talkative/calls.jsonl` dosyasından yeniden sayıldı; eski rapordaki daha küçük altküme oranıyla birleştirilmedi. Bu veri aynı modellerin sentetik konuşmalarıdır; gerçek saha frekansı çıkarılamaz.

## Gerçek dünyadan hangi örüntüler alındı?

FCA, iletişim ihtiyacını sağlık, yaşam olayları, finansal dayanıklılık ve yetkinlik gibi birden çok eksende ele alıyor. Bundan yaptığımız tasarım çıkarımı: “zor müşteri” tek sınıf olmamalı; geçici mahremiyet ihtiyacı ile ödeme güçlüğü veya sayısal açıklama ihtiyacı ayrı olmalı. Bunlar sentetik kurum brief'ine ek mevzuat yüklemek için kullanılmadı. [FCA, vulnerable customer guidance](https://www.fca.org.uk/publications/finalised-guidance/guidance-firms-fair-treatment-vulnerable-customers).

FCA'nın hizmet incelemesi, iletişim kanalı ve destek ihtiyacının müşteriler arasında değiştiğini vurguluyor. Bu nedenle “uygulamadan halledin” açıklamasını anlayabilen ama uygulama kullanamayan kişiyi, kimlik vermek istemeyen kişiden ayırıyoruz. [FCA, customer support findings](https://www.fca.org.uk/publications/good-and-poor-practice/delivering-vulnerable-customers).

Ombudsman'ın yenileme örneklerinde fiyat değişikliği, yanlış veya eksik bilgi ve müşterinin ne satın aldığını anlaması önemli temalar. Bu pakette “fiyatı duydum” ile “bu fiyatla yenilemeyi onayladım” ve “gelecek yıllar için de izin verdim” ayrı davranışlar oldu. [Financial Ombudsman, pricing and renewals](https://www.financial-ombudsman.org.uk/businesses/resolving-complaint/complaints-deal/insurance/insurance-pricing-and-renewals).

Hasar süreçlerinde gecikme ve farklı taraflar arasında iletişim kopması da şikâyet temaları. Buradan, aynı olayı tekrar anlatmaktan yorulmuş ama aslında yeni hasar açmak istemeyen müşteri profili türetildi. Kaynaktaki konut sigortası örüntüsü araç hasarı senaryosuna **tasarım analojisi** olarak taşındı; dağılım veya sıklık varsayılmadı. [Financial Ombudsman, claim communication](https://www.financial-ombudsman.org.uk/who-we-are/data-insight/news/buildings-insurance-complaints-hit-10-year-high).

## Yeni malzeme

- [30 müşteri kartı](CUSTOMERS.md), [JSON karşılığı](profiles.json).
- [30 dallanan pilot görüşme](ENCOUNTERS.md), [ayrılmış fixture verisi](encounters.json). Her profil bir görüşmede somutlaştırıldı. Bunlar ilk 64 vakaya ek tasarımlardır; onun beklenen kayıtlarını değiştirmez.
- [Uygulama ve ölçüm protokolü](PROTOCOL.md).
- `customer_lab.py`: oyuncuya yalnız müşteri bilgilerini çıkarır, ödeme kapasitesini kontrol eder, yerel reviewer'ın rıza sınırını ölçer. Diyalog simülatörü veya canlı runner değildir.
- `test_customer_lab.py`: bilgi sızıntısı, toplu bütçe, kimlik rolü ve komut hattı sözleşmesinin yerel kontrolleri.

## Bir müşteriyi nasıl tanımlıyoruz?

**Amaç + maddi sınır + bilgi + inanç + ortam + etkileşim durumu.**

Örneğin kişi 480 borcu olduğunu yeni öğrenebilir, 120 parası vardır, 20 Ekim'de gelir bekliyordur ve “söz vermek” ile “kartımdan şimdi çekmek” arasındaki farkı bilmiyordur. Ajan açıklayınca kavrayışı değişir; parası 160 olmaz. Daha anlaşılır konuşmak bir yanlış anlamayı çözebilir, imkânsız bir ödemeyi mümkün kılamaz.

Kişi kızgın olup ödemeyi kabul edebilir; sakin olup hiçbir şeye onay vermeyebilir. Tek kelimelik yanıttan duygu, niyet ve rıza birlikte çıkarılmamalı. İsim, cinsiyet veya yaş değişince bütçe/işbirliği değişmiyorsa beklenen davranış da değişmemeli.

## Sınırlar

Kartlar sentetik ve insan gözüyle kalibre edilmesi gereken başlangıç tasarımlarıdır. Hiçbir müşteriyi hastalık/yaş/meslek nedeniyle otomatik saldırgan, yetersiz veya güvenilmez yapmıyoruz. Ses engeli testi metinle gerçek akustik doğrulama sayılmaz. İstismar, klinik kriz veya hukuki temsil gibi konular için burada bulunmayan operasyon politikasını uydurmak yerine eksik gereksinim raporlanır.
