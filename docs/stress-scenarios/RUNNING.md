# Çalışan sentetik test altyapısı

Repo kökünden `python builder/tools/stress_lab.py --help`. Ek paket kurulumu gerekmiyor; Python standart kütüphanesi yeterli. Kod `builder/stresslab/` altında.

Bu servis 64 stres vakasını ve 30 müşteri pilotunu oturumlara dönüştürür. Her oturumun kimliği, doğrulama sayacı, ürünleri, işlem sonuçları ve kapanışı ayrıdır. Resmi practice bankasına müşteri yüklemez. Kaynak JSON'daki `designed_not_executed` tarihsel tasarım durumudur; gerçekten çalıştırılan görüşmelerin durumu kendi raporunda bulunur.

İlk doğrulama: **23 yeni + 27 mevcut kontrol geçti**; F-08 ve R04 yerel model görüşmelerinin son koşuları geçti. Onarım öncesi sorunlu koşular da tutuldu. [Sonuçlar ve kanıtlar](VALIDATION.md).

## Yerel model görüşmesi

```powershell
python builder/tools/stress_lab.py list
python builder/tools/stress_lab.py run F-08 R04 --max-cost 1.30
python builder/tools/stress_lab.py run R01 R14 R30 --repeat 3 --max-cost 1.30
```

`baseline` modunda ayrı müşteri ve ajan model çağrıları konuşur; ajan brief ve araç sözleşmesinden hareket ederek gerçek test bankasına yazar. Bu, BRAVE'ın ürettiği Freya workflow'unu çalıştırmaz. Ajan, müşteri ve değerlendirici model adları raporda belirtilir. Ortamdan `.env`/`env.sh` anahtarları okunur; model seçimi `--agent-model`, `--customer-model`, `--judge-model` veya `STRESS_*_MODEL` ile değiştirilebilir. Varsayılanlar mevcut builder'ın model seçimini kullanır.

Her komut `traces/stress-lab/<zaman-benzersiz-id>/<vaka>/<tekrar>/` oluşturur:

- `transcript.json`: müşterinin ve ajanın söylediği metin, ilgili araç sırası.
- `ledger.json`: bütün araç denemeleri, hatalar, başarılı kayıtlar, yanlış cevap sayısı ve kapanış.
- `result.json`: kayıt denetimi, toplam müşteri bütçesi, konuşma denetimi, müşteri geçerliliği ve sonuç.
- `models.log`: modeller, kullanım, raporlanan maliyet ve servis hataları.
- `models.judge-response.txt`: değerlendiricinin ham çıktısı; JSON bozuksa da korunur.
- Üst dizindeki `summary.json`: başarılı, başarısız, değerlendirilemeyen ve yarım kalan koşular birlikte.

`--max-cost` her vaka/tekrar için raporlanmış maliyet eşiğidir; son istek bu eşiği aşabilir. `--max-calls`, `--max-steps` ve `--timeout` ek sınırlar sağlar. `--no-judge` konuşmayı değerlendirmeden bırakır; yalnız kayıtların geçmesi genel başarı sayılmaz. Başarısız, yarım veya değerlendirilemeyen suite komutu çıkış kodu 1 döndürür. Tekrarlar yeni oturumlar açar, eski koşuların üzerine yazılmaz. Üç tekrarın tümü geçmeden üçte üç başarı raporlanmaz.

## Müşteri gerçekçiliği ve başarı ölçümü

30 pilotta müşteri yalnız kendi profilini, kişisel bilgilerini, bütçesini ve duyduğu konuşmayı görür. Bankanın kayıtları, gizli sınırlar, beklenen sonuç ve değerlendirici dalları müşteri modeline verilmez. Müşteri güvenlik sorusuna otomatik doğru yanıt vermez: eski/yeni adres, üçüncü kişi rolü, düzeltme ve itirazlar kendi kartının kurallarına göre ilerler. 64 ana vakanın oyuncusu kritik replik kartı kullanır; bunlar persona kalibrasyonu olarak sunulmaz.

Toplam bütçe tüm ürünler ve tarihler üzerinden Decimal ile hesaplanır. Örneğin R04'te 500 sterlin üç kez kullanılabilir bir limit değildir. Kayıt sınırını aşan taahhüt, yanlış sonuç, yanlış ürün, eksik not, fazla tekrar veya yanlış iş günü deterministik denetimde yakalanır. Ödeme yapmayı reddetmek, insan istemek veya görüşmeyi uygun biçimde bitirmek doğru hizmet sonucu olabilir.

Konuşma rızası ve gizlilik ayrı model denetimi ister. Pilotlarda gözlenen konuşma ile bir dal seçilmeli ve o dalın gerekli kayıtları kontrol edilmelidir; yalnız sonucun izin verilen bir enum olması yetmez. Eksik kontrol, dal kanıtı veya bozuk model çıktısı başarı sayılmaz. Model denetimi yanılabilir; alıntılanan kanıtlar insan incelemesine açıktır. Ses, ASR, aksan, gerçek kesinti ve eşzamanlı konuşma bu metin sürücüsünde sınanmaz.

Bazı pilotların tasarım kartında tam ürün metadata'sı yoktur. Derleyici sentetik varsayılanları açıkça tamamlar: banka ürünü için aksi belirtilmedikçe 900/300, prim için 480/160, dört gecikme günü; ürün kimlikleri korunur. `card-`, `loan-`, `car-` ürünleri sırasıyla kart, bireysel kredi, araç kredisidir. Saklanan kimlik açıkça verildiyse o kullanılır; aksi durumda karttaki kişisel kimlik bilgileri kullanılır. Üçüncü kişiye bankanın cevapları verilmez. I/J ortak poliçe setup'ları kodda açık alanlara dönüştürülür; vaka istisnaları ayrı uygulanır.

Tamamlanmış fixture'ları incelemek için:

```powershell
python builder/tools/stress_lab.py fixtures --output traces/stress-lab/compiled-fixtures.json
```

Bu çıktı **özel test verisidir**; tamamını müşteri prompt'una vermeyin.

## Gerçek HTTP servisi ve hata enjeksiyonu

```powershell
python builder/tools/stress_lab.py serve --port 8765
python builder/tools/stress_lab.py contract E --output traces/stress-lab/openapi-e.json
python builder/tools/stress_lab.py contract I --output traces/stress-lab/openapi-i.json
python builder/tools/stress_lab.py contract J --output traces/stress-lab/openapi-j.json
```

`serve` ekrana yalnız yerel kullanım için üretilen admin token'ını yazar. `POST /admin/sessions` gövdesi `{"case_id":"C-01"}` ile oturum açar; `GET /admin/ledger/<session_id>` kayıtları verir. Bu uçlar `Authorization: Bearer <admin-token>` ister. `POST /v1/<araç>` çağrıları müşterinin rastgele oturum kimliğini kullanır. `/health` canlılık ve servis instance kimliğini verir. Ledger ve fixture'lar araç yanıtlarında açığa çıkmaz. Oturumlar bellektedir; servisi kapatınca kaybolur, sürücünün rapor dosyaları kalır.

Uygulanan vaka hataları: D-04 iki kesin no-commit hata ardından başarı; D-05 üç kesin hata; E-02 HTTP 200 içinde REJECTED; E-07 yalnız ikinci ürünün iki kesin hatası; I-04 kayıt oluştuktan sonra gerçek bağlantı kesilmesi; I-05 slotun dolması ve yeni slot; I-08 bilinmeyen işlem durumu; J-02/R09 sürüm çakışması ve yeni fiyat; J-03 kontrollü saat ilerlemesi; J-08 başarı sonrası yanıt kaybı. I/J yazmaları request_key ile idempotenttir; aynı anahtar farklı işleme verilemez.

Backend yanlış iş kararlarını sessizce düzeltmez: örneğin alt tutardan küçük bir ödeme kaydı denetimde hata olarak görünür. Yetkilendirme, wire şeması, ürün kimliği ve idempotency ise servis tarafından korunur. Böylece hatalı agent kararları kaybolmaz.

## Freya'daki BRAVE workflow'unu bağlama

Freya buluttan araç çağırdığı için `127.0.0.1` adresine erişemez. Bu makinedeki **8765 portuna** yönlenen bir HTTPS tüneli veya aynı servisin erişilebilir deployment'ı gerekir. Yerel model başarısı Freya başarısı diye raporlanmaz. Bu oturumda Freya üzerinden sentetik görüşme çalıştırılmadı; adapter ve workflow URL değişimi yerelde doğrulandı.

Önce ilgili sentetik brief ve onun gerçek URL'li sözleşmesiyle workflow üretin. A/B workflow'unun yalnız URL'sini değiştirmek C/J politikalarını öğretmez. C–H tahsilat akışlarını zorlar; I/J için mevcut collections generator'ı yeterli değildir, ayrı hasar/zeyil workflow'u gerekir. Test için hazırlanmış workflow export'unun bir kopyasını ayrı slotta yayınlamak mümkündür:

```powershell
python builder/tools/stress_lab.py contract C --bank-url https://YOUR-TUNNEL --output traces/stress-lab/openapi-c.json
python builder/tools/stress_lab.py clone PATH-TO-TEST-WORKFLOW.json --bank-url https://YOUR-TUNNEL --output traces/stress-lab/test.workflow.json --publish
python builder/tools/stress_lab.py run C-01 --mode freya --workflow-id TEST-WORKFLOW-ID --public-url https://YOUR-TUNNEL --port 8765 --repeat 3
```

`--publish` resmi `.freya-agents.json` yerine `traces/stress-lab/test-workflows.json` kullanır ve `stress-lab-*` isimli ayrı slotu yayınlar. URL değiştirme desteklenmeyen veya gizli eski endpoint bırakıyorsa hata verir. Freya koşusu kendi bankasını başlatır; o sırada ayrı `serve` komutunu aynı portta çalıştırmayın. Sürücü HTTPS adresinin **bu koşunun bankasına** ulaştığını instance kimliğiyle kontrol eder. Freya call ID'si rapora eklenir. Freya modu J-03'ün görüşme ortası `now` değişimini henüz doğrulamadığı için bu vakayı hata olarak durdurur.

## Yerel doğrulama ve tekrar inceleme

```powershell
$env:PYTHONPATH='builder'
python -m unittest stresslab.test_lab -v
python -m unittest discover -s docs/stress-scenarios/realism -p test_customer_lab.py
python builder/tools/stress_lab.py review PATH-TO-SAVED-RUN
```

`review` ajanı yeniden konuşturmaz; mevcut transcript ve ledger'ı güncel denetimle tekrar inceler ve yeni tarihli `review-*.json` yazar. Eski sonuç değişmez; yarım kalmış orijinal koşu sonradan tam görüşme gibi gösterilemez.
