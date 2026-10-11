# Müşteri gerçekçiliği laboratuvarı

Bu klasör müşteri davranışını ve değerlendirme kalitesini geliştirir. Buradaki kartlar artık [çalışan test sürücüsü](../RUNNING.md) tarafından kullanılabilir. `customer_lab.py` kendi başına canlı Freya entegrasyonu değildir.

## Okuma sırası

1. [İnceleme: önceki testlerdeki açıklar](REVIEW.md).
2. [30 ayrıntılı müşteri profili](CUSTOMERS.md).
3. [30 dallanan görüşme pilotu](ENCOUNTERS.md).
4. [Koşu, rıza kalibrasyonu ve değerlendirme protokolü](PROTOCOL.md).

Ana değişiklik: müşterinin parası ve bildikleri sabit, anlaması ve işbirliği ise konuşmanın gidişine bağlı. Müşteri banka limitlerini bilmez; önerilen günü sırf agent istediği için kabul etmez. Kibar bir dinleme işareti işlem onayına dönüştürülmez. Ödemeye uygun bir seçenek yoksa doğru ret de başarılı hizmet sonucu olabilir.

## Yerelde çalıştırma

Repo kökünden, Python standart kütüphanesi yeterli:

```powershell
python docs/stress-scenarios/realism/customer_lab.py validate
python -m unittest discover -s docs/stress-scenarios/realism -p test_customer_lab.py -v
python docs/stress-scenarios/realism/customer_lab.py export R03 --out out/customer-r03.json
python docs/stress-scenarios/realism/customer_lab.py probe --out docs/stress-scenarios/realism/review-evidence.json
```

`export` oyuncuya yalnız kendi bilgilerini verir; `bank_private`, `evaluator_private`, brief, beklenen sonuç ve bilinmeyen metadata aktarılmaz. Bu **yapısal** korumadır; izinli doğal dil alanına bir yazarın yanlışlıkla gizli kural koymasını kendiliğinden algılamaz. Üçüncü kişi kartına müşteri güvenlik cevapları konması reddedilir.

`capacity_assessment()` tüm başarılı/bekleyen taahhüt kümesini birlikte almalıdır; her ürün için ayrı çağırıp bütçeyi yeniden başlatmayın. `within_capacity` yalnız bütçe içinde demektir; brief'e uygun, rızalı veya kaydedilmiş demek değildir. Para Decimal ile, toplamlar tarihe göre birikimli karşılaştırılır.

`consent_findings()` bağımsız etiketlenmiş olaylardan ürün+tutar+tarih+sürüm bağını denetler. Bilinmeyen/eksik anotasyon başarı sayılmaz. Anotasyon üretimi ve doğal dil anlama bu araçta uygulanmamıştır; insan veya ayrı değerlendirici gerekir.

## Çalıştırılan kontrol ve bulunan sınır

- 30 profil ve 30 karşılaşmanın bağlantıları, müşteri görünümü ve bütçe fixture'ları doğrulandı. Her profil en az bir karşılaşmada kullanılıyor.
- **27 yerel kontrol**: bilgi ayrımı, üçüncü kişi rolü, baştaki sıfırlar/değişmez fixture, toplam ve tarihe bağlı bütçe; ayrıca rıza, düzeltme, geri çekme, yanlış ürün/tutar/tarih ve tekrar yazma durumları.
- Mevcut R4 reviewer'ına 1 olumlu ve 5 olumsuz yapay transcript verildi. Olumsuz beşinde de semantik rıza ihlali işaretlenmedi. Bu, reviewer'ın belgelenmiş sıra sezgisinin sınırını gösterir; canlı ajan hatası ölçümü değildir. [Ham kanıt](review-evidence.json).
- Yeni anotasyon tabanlı kontrol aynı beş olumsuz durumu işaretler ve olumlu kontrolü kabul eder; **etiketler elle verilmiştir**, metni anlama başarısı ölçülmemiştir.
- Eski arşivde 74 farklı sentetik çağrı, 1.020 müşteri turu ve sezgisel olarak işaretli 588 kesik tur sayıldı. Gerçek müşteri oranı veya gerçek ses kesilmesi diye yorumlanmaz.

## Veri üretimi

`build_customers.py` ve `extra_encounters.py`, `profiles.json`, `encounters.json`, `CUSTOMERS.md` ve `ENCOUNTERS.md` dosyalarının kaynağıdır. İçeriği değiştirmek için ilgili üreticiyi düzenleyin, sonra:

```powershell
python docs/stress-scenarios/realism/build_customers.py
python docs/stress-scenarios/realism/customer_lab.py validate
```

İlk 64 vakanın `build_pack.py` üreticisi bu klasöre dokunmaz. Aktif builder, resmi kit ve canlı ajan değiştirilmedi. **Canlı görüşme sayısı bu çalışma için sıfırdır.** Sonraki doğrulama, müşteri kartlarının insanla kalibrasyonu ve tamamlanmış özel backend üzerinde 30 × 3 koşudur.
