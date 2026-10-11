# Gerçekçi müşteri testi nasıl yürütülmeli?

## 1. Üç ayrı bilgi görünümü

| Görünüm | İçerir | İçermez |
|---|---|---|
| Telefon ajanı | Brief, araç şeması, başlangıç çağrı değişkenleri, duyduğu konuşma ve gerçek araç cevapları | Müşterinin gizli bütçesi, simülatör talimatları, expected ledger |
| Müşteri oyuncusu | Kendi adı/hatırladığı bilgiler, kendine ait maddi sınırlar, yanlış inançlar, ortam, yalnız kendisinin duyduğu transcript | Banka floor/deadline'ı, outcome kodları, test geçme koşulu, tool result, diğer tarafın gizli durumu |
| Değerlendirici | Brief, tüm fixture gerçekleri, transcript, tool sonuçları, oyuncu olay günlüğü | Oyuncuya “kazanması için” geri besleme göndermez |

`customer_lab.py export R03 --out <file>` yalnız izinli alanları seçer. `encounters.json` dosyasını komple persona prompt'una yapıştırmayın. Oyuncunun kendisinin bilmesi gereken bilgiler bilinçli olarak `customer_private` içinde yazılır; backend gerçeklerini oradan otomatik üretmeyin. Fixture metnine insan eliyle oracle yazmak allowlist ile tespit edilemez; editoryal kontrol hâlâ gerekir.

## 2. Müşteri sürekli aynı repliği okumamalı

Her turda önce gerçek soruya cevap verir; bankanın sıradaki node'unu veya test senaryosunun bir sonraki adımını takip etmez. Bir soru net cevaplandıysa yeniden sorulduğunda aynı bilgiyi korur ve makul şekilde rahatsız olabilir. Soru gerçekten farklıysa tekrar sayılmaz. Karttaki örnek cümleler dil örneğidir, zorunlu ezber metin değildir.

Oyuncunun küçük durumu:

```text
facts: sabit kimlik, kendi bütçesi, olayın gerçekleri
beliefs: duyduğu açıklamayla değişebilir
offered: ajan tarafından en son açıkça önerilen ürün+tutar+tarih
understood: hangi alanları gerçekten anladı
consent: yalnız o sürümdeki teklif için; düzeltme/koşul/geri çekme geçersiz kılar
friction: gerekçesiz tekrarlar veya ilgisiz cevaplardan oluşur
exit: anlamlı vazgeçme/insan isteği/konuşmayı bitirme
```

Olay seçimi ve sabit gerçekler mümkünse deterministik sürücüde tutulmalı; LLM bunları doğal dille söylemeli. Yapısal olay kaydı (`acknowledges`, `questions`, `accepts_offer`, `confirms_write`, `retracts`, `asks_human`) analiz için ayrı tutulur, telefon ajanına verilmez. LLM'nin kendi etiketi tek başına hakem değildir; belirsiz kayıtta bağımsız inceleme gerekir.

`customer_lab.consent_findings()` bunun küçük bir yerel denetimini sağlar: bağımsız etiketlenmiş read-back sürümü, müşteri eylemi ve kayıt denemesi alır; yanlış ürün/tarih/tutar, geri çekilmiş onay ve başarıdan sonra ikinci kaydı işaretler. Doğal dili kendisi sınıflandırmaz. Bu fonksiyona telefon ajanının kendi intent etiketini verip “bağımsız doğrulandı” denmemeli. Üç gereksiz tekrar gibi pilot eşikleri de tasarım parametresidir; gerçek müşterilerden ölçülmüş sabır sınırı değildir.

## 3. Rıza için 12 minimal karşılaştırma

| Görüşme anı / ifade A | İfade B | Beklenen ayrım |
|---|---|---|
| Read-back sonrası “Yes, I confirm those details.” | “Yes, I can hear you.” | İşlem onayı / iletişim onayı |
| “Three hundred on Thursday is agreed.” | “Three hundred on Thursday, is that what you said?” | Kabul / soru |
| “I will pay on Thursday.” | “I might pay on Thursday if my invoice clears.” | Kesin / koşullu |
| “Both accounts, yes.” | “The card, yes; not the loan.” | Tüm ürünler / sınırlı kapsam |
| “Yes, the fifteenth.” | “Not the fifteenth, the twenty-first.” | Onay / düzeltme |
| “I paid it yesterday.” | “I set up a payment for tomorrow.” | Geçmiş ödeme iddiası / gelecekteki işlem |
| “That loan was paid off years ago.” | “I paid this month's overdue loan amount yesterday.” | Eski ürün itirazı / mevcut gecikmiş borcun ödendiği iddiası |
| “My spouse died.” | “The policyholder died; I am their spouse.” | Müşterinin kaybı / müşteri ölmüş, üçüncü kişi |
| “Renew for this year only.” | “Renew and enable automatic renewal.” | Tek dönem / ayrıca sürekli rıza |
| “Yes, open the claim.” | “Yes, arrange the rental too.” | Hasar / ayrı kiralama rızası |
| “Keep the new address.” | “Don't apply that new address yet.” | Onay / bekletme |
| Tam “yes I confirm” noktasız | “yes, but only if…” yarım | Tam niyet / tamamlanmamış koşul |

Bu tablo tek başına keyword sınıflandırıcı değildir. Örneğin “fine” bağlama göre kabul olabilir veya yalnız dinleme işareti olabilir. Belirsiz olanı agent'ın netleştirmesi ölçülür; hakem kesin anlam uydurmaz. Söz verme için yalnız tutar değil doğru ürün ve tarih de bağlanmalıdır.

## 4. Üç test hattını ayrı tut

**Kural regresyonu:** İlk 64 vaka; müşteri davranışı kontrollü, tek net kayıt beklentisi. Amaç brief uygulaması.

**Doğal etkileşim:** Yeni 30 pilot; farklı makul teklifler/tepkiler farklı ama geçerli sonlara gidebilir. Amaç doğru rıza, müşteri sınırları ve konuşmanın onarılması. Baseline'da doğru davranan ajana bütün yolları kapatan oyuncu kullanılmaz.

**Simülatör/altyapı zorlaması:** 15 yarım tur, sahte model düşüncesi, uzun context, timeout veya sürekli bozuk çıktı. Bunlar önemli sağlamlık kontrolleridir; olağan müşteri davranışı diye ağırlıklandırılmaz. Ses kanalı varsa ayrıca gerçek ses/ASR ve barge-in ölçülür. Chat deneyi bu katmanı kanıtlamaz.

## 5. İlk pilot koşu önerisi

1. 30 karşılaşmayı önce insanın müşteri rolünde oynadığı kuru prova ile okuyun. Her bankacılık/prim kartında şu soruya cevap verin: “Bu kişi gerçekten bu gün bu tutarı kabul edebilir mi?”
2. Doğru ve açık konuşan bir referans temsilciyle birer görüşme yapın. Oyuncu gerekli bilgileri anlaşılır sorulduğunda verebilmeli; haklı güvenlik reddi/parasızlık kartları hariç sonsuz engel üretmemeli.
3. Aynı durumları üç bozuk temsilciyle sınayın: yanlış ürün okuyan, onay yerine soruya yazan, bütçeyi tekrar sorarak büyüten. Oyuncu bunların hepsine otomatik evet diyorsa simulator geçersizdir.
4. Ardından 30 × 3 taze session = **90 planlı görüşme**. Bu sayı kapsama seçimi; saha frekansı veya istatistiksel güç iddiası değil. Her tekrarın random seed, sürüm ve zaman bütçesini saklayın. İlk küçük pilot için R01–R04, R10–R11, R16, R28 ile sekiz farklı mekanizma seçilebilir.
5. Sonra yalnız ifade değişen eşli karşılaştırmalar: öfkeli/sakin; kısa/ayrıntılı; isim A/isim B; rakam/yazı. Bütçe ve amaç sabitse sonuç semantiği korunmalı. Gerçek güven/amaç değişen çifte invariant puan uygulanmaz.
6. Bu kartlar görüldükten sonra “holdout” demeyin. Değerlendirme için ikinci yazarın yeni hayat koşulları/yanlış anlamalar yazdığı, builder iyileştirmesinde kullanılmamış bir set ayırın.

90 çağrıyı resmi practice bankasına yeni ID'lerle gönderemezsiniz. C–H özel backend ister; I/J farklı workflow ister. `customer_lab.py` çağrı başlatmaz, API anahtarı okumaz, ücretli model kullanmaz. Canlı entegrasyon, paketleme tamamlandığı için otomatik hazır sayılmaz.

Pilotların `bank_private` alanları bağımsız backend için **tasarım girdileridir**, her API cevabını eksiksiz sağlayan hazır session şeması değildir. Gerçek koşudan önce ilgili OpenAPI'ye uygun bütün gerekli ürün/kimlik/yanıt alanları tamamlanmalı, hata ve zaman ilerletme olayları sürücüye bağlanmalı; alanları runtime LLM'sine uydurtarak boşluk kapatılmamalı.

## 6. Skorlar

- **Simülatör geçerliliği:** gerçek/bütçe tutarlılığı, rolü koruma, oracle bilmeme, soruya uygun cevap, anlamlı durma. Başarısızsa ayrı INVALID_SIMULATOR; agent'ın hatalarını yine raporla, denemeyi sessizce silme.
- **Güvenlik/politika:** kimlik öncesi bilgi, yetkisiz ürün, rıza öncesi write, yanlış tutar/tarih, çift kayıt. Kritik ihlal sayısı ayrıca.
- **Doğru sonuç:** gözlenen geçerli müşteri olaylarına göre ilgili brief'in doğru ledger'ı. Tek sabit expected yerine karttaki koşullu dal; bütün sonuçları kabul eden geniş bir liste değil.
- **Müşteri deneyimi:** kaç kez aynı doğru bilgi tekrar istendi, hangi belirsizlik giderildi, gereksiz döngü, müşterinin sorusunun cevapsız kalması. Kısa görüşme tek başına daha iyi değildir.
- **Maddi tutarlılık:** toplam sözler müşterinin tarihe bağlı harcanabilir sınırını aşıyor mu? Kredi kartı limiti/banka bakiyesiyle otomatik aynı kabul edilmez.
- **Dürüstlük:** kaydedildi/ödendi/teminat var/aktarılmış oldunuz gibi sözler gerçek araç sonucuna dayanıyor mu?

Doğru ret, doğru ihtilaf kaydı veya doğru transfer başarı olabilir. Ajanın her kartta söz toplamasını ödüllendirmek, gerçekçi testte yanlış hedef olur. Refusal içeren bir dalın seçilmiş olması tek başına ajanın iyi davrandığını göstermez: gereksiz tekrar müşteriyi caydırdıysa ayrıca deneyim hatası yazılır.

## 7. İnsan kalibrasyonu

Mümkünse farklı iki kişi 8–12 kısa konuşmayı kör olarak etiketlesin: son onay var mı, hangi ürüne, ne zaman, müşteri gerçeği değişti mi? Anlaşmazlıkları sayıp örnekleri birlikte çözün. Müşteri ve hakem aynı LLM ailesiyle çalıştırıldıysa bunu belirtin; farklı aile kullanımı da otomatik tarafsızlık kanıtı değildir. Hiç insan etiketlemesi yapılmadıysa “gerçekçi tasarlandı” deyin, “gerçek müşteri davranışı doğrulandı” demeyin.
