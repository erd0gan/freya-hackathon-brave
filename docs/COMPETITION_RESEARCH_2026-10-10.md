# Build the Builder: yarışma yönü, mevcut durum ve eksiklerin araştırması

**İnceleme tarihi:** 10 Ekim 2026. İlk kanıt fotoğrafı 20:08 İstanbul / 17:08 UTC; yeniden değerlendirme yaklaşık **20:19 İstanbul / 17:19 UTC**.  
**İncelenen commit:** `e1b01f3d857b8ff3c5f3224a788645e2a36fb55e` ve çalışma alanındaki henüz commit edilmemiş değişiklikler.  
**Ana değerlendirme:** Elle yazılmış public spec'ten üretilen workflow'lar **A 10/10, B 3/3; bütün müşteriler 3/3** geçti. Bu, public baseline için 39/39 başarılı konuşma ve workflow yaklaşımının gerçek platformda çalıştığına dair güçlü kanıttır. LLM spec'iyle build/publish/export da çalışıyor; ancak bu çıktının simülasyon başarısı henüz ölçülmedi. Northwind ham materyal provası extraction ve compilation için olumlu kanıt sağlıyor. Ana öncelik artık mimariyi değiştirmek değil, **LLM spec'inden çıkan ajanı üç tekrarda ölçmek, konuşma stillerini ve farklı takvim günlerini sınamak**.

**Bu güncellemenin değiştirdiği karar:** Önceki 'publicte yalnızca tek tekrar var' ve '3/3 başarısı kanıtlanmadı' değerlendirmeleri elle yazılmış spec için kapanmıştır. Eski 8/10, 9/10 ve 2/3 kayıpları geçmiş sürümlerin sonuçlarıdır. Buna karşılık aşağıda yeniden sınanan ek uç durumlar ve spec denetimi açık kalıyor; public 13 vakada başarı, bunların düzeldiği anlamına gelmiyor. Kod hâlâ commit edilmemiştir; bu yeniden değerlendirmede commit yapılmadı.

## 1. Kapsam ve kanıt sınırı

Kit v3'ün README'si, A/B briefleri, yedi aracın OpenAPI dosyası, public vakalar, scorer, test runner, resmi örnek builder ve workflow belgeleri incelendi. Bunlar mevcut `builder/` uygulaması, önceki `src/builder/` yaklaşımı, araştırma notları, gerçek Freya sonuç dosyaları ve son export ile karşılaştırıldı.

Yarışmanın [resmi sayfası](https://freyavoice.ai/hackathon) güncel olarak indirildi; kit v3 ve final takvimi kontrol edildi. Web aracının sayfaya erişimi başarısız olunca standart HTTP indirmesi kullanıldı. Ayrıca ajan güvenilirliği ve değerlendirmesi hakkında dört birincil kaynak incelendi; bunların yarışmaya uygulanması bölüm 13'te açıkça yorum olarak ayrıldı.

Bu çalışma sırasında üretim koduna düzeltme yapılmadı, yeni Freya simülasyonu veya ücretli model çağrısı başlatılmadı. Canlı sonuçlar, çalışma alanında bulunan önceki koşulardan okunmuştur. Ek sınamalar yerel ve ağsızdır.

Çalışma alanı inceleme sırasında güncellendi: başlangıçta iskelet olan `builder/build.py`, daha sonra tam build yoluna bağlandı. Rapordaki değerlendirme bu sonraki hal içindir. Bu nedenle eski raporlardaki 'bağlı değil', 'anahtar yok' ve 'platform bilinmiyor' ifadeleri güncel durum kabul edilmedi.

Yeniden değerlendirmede kullanıcının yeni sonuçları `kit/results/gen-a-3.json`, `gen-b-3.json`, `traces/northwind.log`, Northwind brief/API/spec/export dosyaları ve güncel kodla karşılaştırıldı. Public sonuçların **39 ayrı denemesinde de pass** olduğu dosyalardan doğrulandı. Bu skorların elle yazılmış spec'e ait olduğu bilgisi kullanıcının koşu açıklamasından geliyor; sonuç JSON'ları spec kaynağını ayrıca kaydetmiyor. Yeni platform veya model çağrısı başlatılmadı.

Kanıt etiketleri:

- **Kayıt:** Var olan gerçek platform sonucu, export veya trace.
- **Yeniden üretildi:** Yerel kodla gözlenen davranış.
- **Kod bulgusu:** Kaynak kodda görülen, platformda ayrıca ölçülmemiş sınır.
- **Öneri:** Yarışma kısıtlarından türetilen mühendislik kararı.

Yerel sınama kodu ve ham sonuçlar: [audit.py](research-2026-10-10/audit.py), [audit.json](research-2026-10-10/audit.json). JSON içinde incelenen kaynakların ve sonuç dosyalarının SHA-256 değerleri bulunur. Script, bu çalışma alanındaki public spec ve kaydedilmiş platform dosyalarını okur; gizli brief veya servis çağrısı içermez.

İlk `audit.json` tarihsel kanıt olarak korundu. Güncel karşı örneklerin yeniden çalıştırıldığı kayıt [audit-reassessment.json](research-2026-10-10/audit-reassessment.json); üç tekrar dosyaları, Northwind ve açık mapping ile üretilen kimlik hook'unun ayrıca denetlendiği kayıtlar [reassess.py](research-2026-10-10/reassess.py) ve [reassess.json](research-2026-10-10/reassess.json).

## 2. Yarışmanın gerçekten istediği ürün

Kitin istediği şey, brief dosyasından insan müdahalesi olmadan Freya üzerinde çalışacak telefon ajanını oluşturan bir programdır. Ajan bir **workflow** olarak teslim edilir. Kodun kendi ortamındaki ajanına ait başarı, Freya workflow'unun başarısı yerine geçmez.

Kit v3'e göre her vaka üç ayrı boyutta doğru olmalıdır: banka ledger'ı, brief'e uygun konuşma ve dürüstlük. Kısmi puan yoktur. Finalde bir müşterinin üç denemesinin tamamı geçmelidir. Tool adları sabit kalacak; argümanlar, enum'lar, kimlik soruları, limitler, tatiller ve kapanış kuralları değişebilecektir. İki final girdisinden biri temiz spec, diğeri mail/policy/memo/kod listesi gibi ham materyal olacaktır. [Kaynak: kit README](../kit/README.md).

Resmi sayfada final girdilerinin 11 Ekim Pazar **17:00 UTC / 20:00 İstanbul** açılacağı, teslimin **20:00 UTC / 23:00 İstanbul** kapanacağı belirtiliyor. İlk on takımın builder'ı verilen commit'ten yeniden çalıştırılacak; trace ve çıktı birlikte incelenecek. Bu üç saat genel bir araştırma veya brief'e özel elle ayar zamanı olarak planlanmamalı. [Kaynak: resmi yarışma sayfası](https://freyavoice.ai/hackathon).

| Kısıt | Projeye etkisi |
|---|---|
| Brief başına en fazla 30 dakika, kendi testleri dahil | Build içinde sınırsız model onarımı veya simülasyon döngüsü kullanılamaz. |
| Phone agent modeli `freya.py` içindeki sabit model | Güçlü builder modeli kullanılabilir; runtime modelinin zayıf noktaları workflow ile sınırlandırılmalı. |
| Yalnızca bankanın tool API'si | Runtime'a harici LLM, retrieval veya yerel gate taşınamaz. Code node/hook kullanılabilir. |
| Workspace başına tek simülasyon | Aynı anda ayrı test run'ları başlatmak toplam değerlendirmeyi hızlandırmaz. Bir simülasyonda birden fazla çağrı ayrı konudur. |
| Yeni girdilerin API alanları değişebilir | Alan isimleri ve response rolleri compile aşamasında açıkça bağlanmalı. |
| Birden fazla `now` günü | Takvim ve yürürlük mantığı build makinesinin saatinden bağımsız olmalı. |
| Final açıldıktan sonra yeni brief değerleri builder'a yazılamaz | Genel eksikler şimdi bulunup giderilmeli; final girdileri yalnızca girdi olarak işlenmeli. |
| Eşitlikte geçen vakaların toplam söz tutarı, sonra build süresi | Önce vaka geçişi; güvenilirlik sağlandıktan sonra tam tutarı koruma ve build hızına odaklanma. |
| Export, kod ve run trace birlikte teslim | Yerel taslak dosya yerine gerçek `export_workflow` çıktısı; trace'in koşuyla eşleşmesi gerekir. |

Buradaki temel ürün tanımı: **tahsilat kurallarını okuyup çalışan bir konuşma durum makinesine derleyen builder**.

## 3. Ne yönde gidiyoruz?

Repoda iki yaklaşım var:

| Yol | Ana yaklaşım | Yarışmadaki konumu |
|---|---|---|
| `src/builder/` | Genel domain IR, aday ajanlar, yerel MutationGate, mock simülasyon, prompt optimizasyonu, aday seçimi | Ön hazırlık araştırması ve bazı yeniden kullanılabilir fikirler. Güncel yarışma teslim yolunu temsil etmiyor. |
| `builder/` | Brief → LLM ile collections spec → deterministik Freya workflow → doğrulama → yayınlama → smoke → export | Gerçek yarışma yoluna uyuyor; son kodda ana komuta bağlı. |

Eski yaklaşım teknik açıdan değerli, ama yarışma için yanlış çıktı ve süre varsayımlarından hareket etmiş. Özellikle yerel gate, Agents config export'u, çok adaylı GEPA/halving ve yüzlerce yerel simülasyon finalde puanlanan ürünü doğrudan güçlendirmiyor. Bunu bugünkü araştırma keşfi olarak sunmamak gerekir: `docs/HACKATHON_REALIGN.md` de yön değişimini zaten doğru biçimde tanımlamış.

Yeni yaklaşımın güçlü tarafları:

- Tarih, floor, deneme sayısı, product id ve kayıt sırası modelin serbest üretiminden çıkarılıyor.
- Müşterinin açık onayı sonrası kayıt ayrı bir aşamada yapılıyor.
- Çok ürün için ortak read-back ve ürün bazında kayıt/not mekanizması var.
- Workflow validator, kimlikten önce hesap araçlarına ulaşılmasını ve platformun sessizce attığı bazı hatalı alanları denetliyor.
- Resmi client ile save/publish/export kullanılıyor.
- Code node'larının önemli platform davranışlarına ait yerel kayıtlar ve probe sonuçları var.
- Yeni builder stdlib kullanıyor; hakem ortamında büyük bir bağımlılık yükü yok.
- Tutar, tarih ve sabit cümle içeren konuşma turları engine'in metnini birebir okuyor; LLM'in kendi teklifini uydurması sınırlandırılmış. Bu son yaklaşımın public üç-tekrar başarısı dosyalarda görülüyor.
- `tool_fields` ile açık request/response rol eşlemesi ve `full_name` dahil call-variable desteği eklenmiş; Northwind bu özelliklerin bir kısmını yeni adlarla kullanıyor.

Ancak 'deterministik runtime' ifadesinin kapsamı doğru anlaşılmalı. Hesap ve eylem mantığı deterministikleşiyor; müşterinin niyetini sınıflandırma, parçalı bilgiyi anlama ve soru yanıtı hâlâ Freya modeline bağlı. Aynı şekilde spec çıkarma da model çağrısı olduğu için builder'ın bütünü matematiksel olarak deterministik değildir.

## 4. Gerçek ölçümler ne söylüyor?

### Yerel testler

Aşağıdaki suite sayıları **ilk incelemenin kaydıdır**; bu belge güncellemesinde tüm pytest suite'i yeniden çalıştırılmadı. Güncel ek karşı örnekler ağsız olarak yeniden çalıştırıldı.

| Çalıştırma | Sonuç | Açıklama |
|---|---|---|
| `.venv/Scripts/python.exe -m pytest` | **281 geçti, 3 kaldı** | Üç hata eski `src/builder/pipeline.py` export'unda Windows `cp1254` kodlamasının `→` karakterini yazamaması. |
| `.venv/Scripts/python.exe -X utf8 -m pytest` | **284 geçti** | UTF-8 çalışma moduyla aynı suite geçti. |
| `.venv/Scripts/python.exe -X utf8 -m pytest tests/hack` | **141 geçti** | Yarışmaya yönelik yerel bölüm ayrı olarak geçti. |

Bu sonuçlar 'mevcut testlerde beklenen davranışlar çalışıyor' demektir. 'Bütün brief kuralları ve gizli konuşmalar karşılanıyor' demek değildir. Ek araştırma sınamalarında yeşil suite'e rağmen hatalar üretildi.

### Kaydedilmiş gerçek Freya koşuları

| Dosya | Ledger doğru | Ledger + konuşma geçti | Tekrar | Süre |
|---|---:|---:|---:|---:|
| `kit/results/base-A.json` | 2/10 | **0/10** | 1 | 320 sn |
| `kit/results/gen-a-1.json` | 10/10 | **9/10** | 1 | 156 sn |
| `kit/results/gen-a-2.json` | 10/10 | **8/10** | 1 | 125 sn |
| `kit/results/gen-b-1.json` | 2/3 | **2/3** | 1 | 456 sn |
| `kit/results/gen-b-2.json` | 3/3 | **3/3** | 1 | 83 sn |
| `kit/results/gen-a-3.json` | 10/10 (son tekrar) | **10/10, her vaka 3/3** | **3** | 387 sn |
| `kit/results/gen-b-3.json` | 3/3 (son tekrar) | **3/3, her vaka 3/3** | **3** | 357 sn |

Ledger sütunu `rows` içindeki konuşma dışı hatalardan çıkarılır; üç-tekrar dosyalarında `rows` son tekrardır. Buna ek olarak `all_rows` ve `final` kontrol edildi: A'da 3×10, B'de 3×3 denemenin tamamı geçti. Bu araştırmada bu koşular yeniden başlatılmadı. **Yeni skorlar elle yazılmış spec'e aittir; LLM extraction'ın doğruluğunu ölçmez.**

Önemli bulgular:

1. **Public baseline artık geçiyor.** A ve B'de bütün müşteriler üç denemenin tamamında ledger + konuşma kontrollerini geçmiş. İlk A dosyalarındaki judge kayıpları güncel baseline'ın başarısızlığı olarak sunulmamalı.
2. İlk A koşusunda PUB-10, açılıştaki 'call is recorded' ifadesini judge'ın söz kaydı gibi okuması nedeniyle kaybedilmiş. Kodun 'recording this call' tercihinin somut bir geçmişi var. Bu tercih mevcut brief'in anlamını koruyor; ama bütün yeni brieflerde `recorded` kelimesini yasaklamak genel bir kural kabul edilmemeli.
3. İkinci A koşusundaki PUB-04 robot sorusu ve PUB-05 hardship kayıpları, sonraki üç-tekrar koşusunda görülmemiş. Bu public senaryolar için düzeltme kanıtı var; `asks_back` veya çok niyetli mesajlar ayrıca ölçülmeli.
4. İlk B koşusundaki PUB-12 döngüsü, sonraki üç-tekrar koşusunda yok. Engine metninin birebir okunması ve identity self-transition'ın kaldırılması kullanıcının açıkladığı düzeltmelerdir; mevcut generator da bu yaklaşımı taşıyor.
5. G0–G6 platform kapıları `docs/GENERATOR.md` içindeki sonuç tablosunda doğrulanmış. Bunları yeniden 'bilinmiyor' iş listesine koymak gereksiz. Agent id tek başına sürümü belirtmediğinden sonraki yeni testleri version/hash ile bağlamak hâlâ yararlı.

Platform düzeltmelerinin kapsamı da net: chat'in ilk code node'u atlaması nedeniyle initial node artık konuşma node'u, setup onun ardından geliyor. Kimlik node'unun kendine dönen transition'ı kaldırılarak tool sonrası routing'in atlanması önlenmiş. `docs/GENERATOR.md` içindeki eski graf ve sonundaki 'doğrulanacak' listesi sonuç tablosundan önceki tasarım notlarıdır; güncel durum için sonuç tablosu ve çalışan kod esas alınmalı.

### LLM spec'iyle build/publish/export kaydı

`traces/brief-ab-run.log` içinde 17:04:57–17:05:44 UTC arasında public A+B girdisinden model ile spec çıkarıldığı, 28 node'lu workflow üretildiği, dry-run, publish, chat smoke ve export yapıldığı görülüyor. Bir model çağrısı yaklaşık 40 saniye; toplam build trace'i 46 saniye ve bildirilen model maliyeti yaklaşık $0,13.

`submission/brief-ab-run.workflow.json`, resmi export'un **wrapper** biçiminde: `workflow_id`, `agent_id`, `updated_at`, `workflow`. Node'lar içteki `workflow` altında. Model config'inde provider/model `experimental-5`. Dosya yaklaşık 496 KB; yerel üretilen JSON yaklaşık 460 KB. Bu boyutun sorun yarattığı gösterilmedi; sık kütüphane gömme nedeniyle büyüklük ve latency ölçümü izlenmeli.

Bu build'in smoke cevabı yalnızca **'Hello. Am I speaking with Ms Morgan?'** olmuş. Banka, digital assistant ve call recording bildirimi görünmüyor. Trace buna rağmen `status=ok` ile bitmiş. Bu, LLM spec'li ajanın chat smoke'unun yalnızca başlayabildiğini gösterir; **elle yazılmış spec'li public ajanların üç-tekrar sonucunu geçersiz kılmaz**. LLM spec'li ajanın opening davranışı ilk tam simülasyonda ayrıca incelenmeli.

### Northwind: ham materyalden gerçek build provası

`tests/metamorphic/northwind/brief.md` ve `openapi.json` ile `submission/northwind.spec.json`, `northwind.workflow.json` ve `traces/northwind.log` karşılaştırıldı. Trace 80 saniyelik build, bir model çağrısı, yaklaşık $0,16, platform dry-run, publish ve export gösteriyor. Public A+B build'iyle birlikte gözlenen aralık **46–80 saniye, yaklaşık $0,13–0,16**. Testli final build'in toplam süresi henüz aynı aralıkta ölçülmüş değil.

Northwind spec'inde memonun üç değişikliği doğru:

- Stage 2 attempts 3 yerine **2**; ara teklif kaldırılıp ikinci teklif floor/deadline olmuş.
- **2026-10-30** tatil listesine eklenmiş.
- Callback kapanışı 17:30 yerine **17:00** olmuş.

Yeni kimlik alanları postcode/birth_year, uppercase outcome/reason/queue kodları, `accounts/account_ref/arrears/days_in_arrears` ürün alanları ve `pay_date/code/team/result` request rolleri eşlenmiş. `full_name` ile hitap destekleniyor. Yerel yeniden üretimde validator sıfır hata verdi; export 28 node ve sabit model taşıyor.

**Kanıt sınırı:** Bu, ham materyalde extraction + compile + platform kabulü için gerçek olumlu kanıttır. Northwind konuşma/ledger davranışı simüle edilmedi. Ayrıca fixture'da `verify_identity` request alanları hâlâ `field/answer`, response alanları hâlâ `all_verified/wrong_answers_left`; bu fixture bütün kimlik alan-renaming kombinasyonlarını sınamıyor. Tarihsel yürürlük sınırının iki tarafında farklı `now` değerleriyle davranış da henüz sınanmadı.

## 5. Başarıyı neden tek koşuyla ölçemeyiz?

Final metriği, müşterinin üç kez de geçmesi. Tekrarların bağımsız ve bir vakadaki başarı ihtimalinin sabit `p` olduğunu varsayan basit örnekte üç geçiş olasılığı `p³` olur:

| Bir denemede başarı | Üç denemenin tamamında başarı |
|---:|---:|
| %80 | %51,2 |
| %90 | %72,9 |
| %95 | %85,7 |
| %98 | %94,1 |
| %99 | %97,0 |

Bu bir final tahmini değil; bağımsızlık varsayımını anlatan örnek. Vakaların güçlüğü farklıdır ve hatalar korelasyonlu olabilir. Gerçek rapor vaka bazında 0/3, 1/3, 2/3, 3/3 göstermeli. İki A dosyasını birleştirerek p³ veya güvenilirlik sonucu üretmek doğru olmaz; sürümleri sabit değil.

Bu koşul artık **elle yazılmış spec'li public baseline için ölçülmüş durumda: 13 müşterinin tamamı 3/3**. Kalan soru, aynı sonucu LLM'in çıkardığı spec ve değiştirilmiş konuşma/takvim dağılımında koruyabilmek.

Üç farklı ölçüm korunmalı:

- Ledger başarısı: kayıtlar doğru mu?
- Transcript başarısı: konuşma ve dürüstlük kontrolleri geçti mi?
- Final benzeri başarı: aynı sabit workflow'da ilgili müşterinin üç koşusu da geçti mi?

## 6. Yeniden üretilen ve öncelikli kod bulguları

Güncel durum: public senaryolardaki eski konuşma kayıpları kapanmıştır. **F01, F03–F07 ve F10 yerel olarak son kodda yeniden üretildi.** F02 smoke'un kapsam eksikliğidir; artık public baseline açılışının genel başarısızlığı olarak sınıflandırılmıyor. F08, Northwind'de sınanan mapping'ler için kısmen kapanmıştır; daha geniş kimlik request rename karşı örneği ayrıca devam ediyor. F12'de tek ham materyal fixture'ı başarılıdır; genel yürürlük/kanıt modeli hâlâ açık. Bu ayrım, yeni public skoru küçümsemeden geriye kalan riskleri tanımlar.

### F01 — Spec'in brief'e dayandığı denetlenmiyor; hatalı spec ile devam edilebiliyor

**P0 · Yeniden üretildi / kod bulgusu.** `fb/spec_extract.py:check` fonksiyonuna verilen `brief` mevcut denetimde kanıt karşılaştırması için kullanılmıyor. Aynı public spec'in yedi ayrı bozulmuş hali **sıfır problem** döndürdü:

| Spec bozulması | Denetim sonucu |
|---|---|
| Tatillerin brief'te olmayan `2030-01-01` ile değiştirilmesi | Kabul |
| Early deadline'ın 12 yerine 999 gün yapılması | Kabul |
| İki bandın aynı gecikme aralığını kapsaması | Kabul |
| Persuasion attempts = -1 | Kabul |
| `notes` boolean yerine `'false'` string'i | Kabul |
| `unclear` reason'ının uydurulmuş bir değer olması | Kabul |
| Callback başlangıcı `25:99`, bitişi `01:00` | Kabul |

Burada 'kabul', spec denetiminin bu hatayı görmediği anlamındadır; her bozuk spec'in platform tarafından da kabul edildiği iddia edilmiyor. Model onarımı sonrası sorunlar kaldığında `extract` yine spec döndürüyor; ana build komutu da problemleri yazıp üretime devam ediyor. Bazı eksik policy değerleri public default'larla tamamlanabiliyor.

**Düzeltme ölçütü:** Tip, aralık, enum ve temel tutarlılık hataları build'i durdurmalı. Her kritik değer kaynak alıntısına/span'ine bağlanmalı. Tatil/deadline/sayaç gibi puanlanan değerlerin brief'te gerçekten geçtiği ve ilgili kuraldan çıkarıldığı doğrulanmalı. Model cevabının JSON olması JSON Schema doğrulaması değildir; mevcut `SCHEMA` açıklama örneğidir.

### F02 — LLM spec'li smoke policy doğrulaması yerine sağlık kontrolü yapıyor

**P1 · Kayıt / kod bulgusu.** LLM spec'li public ve Northwind trace'lerinde chat smoke açılış bilgilerini tam göstermiyor. Güncel `smoke` fonksiyonu call-variable isimlerini alıyor; zorunlu açılış metnini doğrulamıyor. Eksik açılış veya hata build sonucunu başarısız yapmıyor. Bu sağlık kontrolü export üretildiğini kanıtlar; transcript policy başarısını kanıtlamaz. Public elle yazılmış spec'li ajanların 3/3 başarısı ayrı ve olumlu kanıttır.

**Düzeltme ölçütü:** Chat ve simülasyondaki ilk gerçekten söylenen assistant cümlesi ayrı doğrulanmalı. Model ilk-entry metnini söylemezse prompt fallback'i de aynı policy'yi karşılamalı. Smoke doğrulanamıyorsa trace 'policy doğrulandı' izlenimi vermemeli.

### F03 — Callback günü ile saati ayrı turlarda gelince yanlış gün seçiliyor

**P0 · Yeniden üretildi.** `now=2026-10-14T11:00`. Önce 'tomorrow', sonra 'three pm' verildi. İlk adım `partial` ve 'missing time' dedi; ikinci adım callback'i **14 Ekim 15:00** olarak kabul etti. Doğru birleştirme **15 Ekim 15:00** olmalı. `CB_CHECK` extraction alanlarını temizliyor, kısmi bilgiyi kalıcı pending alanında tutmuyor.

**Düzeltme ölçütü:** Pending gün ve saat ayrı saklanmalı; yeni bir düzeltme yalnızca ilgili alanı değiştirmeli. Bu, `pieces` stilinde özellikle hedeflenmesi gereken bir davranış; stilin gerçek platform koşusu henüz yapılmadı.

### F04 — Destek teklifinden sonra gelen soru reddetme sayılıyor

**P0 · Yeniden üretildi.** Çıkarılmış olay sırası `hardship → question → yes` olduğunda motor ikinci olayda kapanıyor; üçüncü onay işlenemiyor. `hardship_offer` aşaması yes/offer dışındaki olayları destek reddi gibi değerlendiriyor. 'Destek ekibi ne yapabilir?' gibi bir soru açık ret değildir.

**Düzeltme ölçütü:** Soru mevcut kararı açık tutmalı; yanıtlandıktan sonra destek teklifi korunmalı. `question`, `unclear`, `partial`, açık `no` birbirinden ayrılmalı. Yerel PUB-05 fixture'ında sorudan sonra no geldiği için yalnızca ledger'a bakmak bu erken kapanışı maskeliyor; güncel gerçek public koşudaki 3/3 başarı ayrıca korunuyor.

### F05 — Hardship ile birlikte verilen ödeme tarihi kaybolabiliyor

**P0 · Yeniden üretildi.** `kind=hardship`, `date=this Friday` girdisi ödeme önerisini değerlendirmeden destek teklifine gidiyor. Brief A kural 32, müşterinin kendisi tarih veya tutar söylemeye devam ederse ilgili ödeme kurallarıyla kayıt alınmasını istiyor.

**Düzeltme ölçütü:** Bir mesaj birden fazla olay taşıyabilmeli: hardship + gönüllü ödeme önerisi + soru. Tek bir enum seçimi bütün diğer anlamları silmemeli. Bu bulgu, Freya'nın her zaman hardship sınıfını seçeceği iddiası değil; bu sınıflandırma geldiğinde motorun kaybı gösterildi.

### F06 — Non-answer ikna sayacını tüketmiyor

**P0 · Yeniden üretildi.** `unclear` girdisi aynı başlangıç ödeme sorusunu tekrar ettiriyor ve `ask_today` fazını koruyor. Brief A kural 30'da non-answer bir persuasion attempt kullanır. Gerçekten eksik bilgi ile tam ama cevapsız müşteri mesajı aynı kategoride işlenmemeli.

**Düzeltme ölçütü:** Parçalı yanıt beklenirken sayaç tüketilmez; tam non-answer brief'in deneme mantığını uygular. Ürün ve çağrı düzeyinde toplam ilerleme/counter denetimi olmalı.

### F07 — Tarih düzeltmesinde eski ISO değer kazanıyor

**P1 · Yeniden üretildi.** `2026-10-16, sorry I mean tomorrow`, 14 Ekim'de **16 Ekim** döndürüyor; son düzeltmeye göre **15 Ekim** beklenir. Parser ISO aramasını düzeltme segmentini ayırmadan önce yapıyor. `May 5` ise çözümlenemiyor; `5 May` çözümleniyor.

**Düzeltme ölçütü:** Son düzeltme segmenti bütün tarih biçimlerinden önce seçilmeli. Ay-başta/gün-başta biçimler simetrik sınanmalı. May örneği mevcut Ekim practice için kritik değil, görülmemiş takvim/ifade desteğinin sınırını gösteriyor.

### F08 — Tool mapping desteği eklendi; kimlik request hook'u için sınır kaldı

**P0 kalan sınır · Kısmen kapandı / yeniden üretildi.** Güncel extraction `tool_fields` üretiyor; generator `role_field` ve `resp` ile açık request/response mapping kullanıyor. Northwind'in farklı ürün, promise, note, transfer ve close alanları doğru eşlenmiş. Eski 'API alanları eşlenmiyor' ifadesi artık fazla geniştir.

Ancak explicit mapping ile gerçek workflow üzerinden bir ek sınama yapıldı: OpenAPI kimlik request alanları `field→question_code`, `answer→response` olarak değiştirildi, spec'e doğru mapping yazıldı ve workflow üretildi. Tool body'si doğru yeni alanlarla oluştu; **üretilen pre-hook** yine sabit `field/answer` okuduğu için `response` ISO tarihe çevrilmedi ve ek `answer: ''` yazıldı. Kanıt `reassess.json` içindeki `explicit_identity_rename` alanıdır. Northwind fixture'ı bu iki kimlik request alanını değiştirmediğinden kendi başarılı sonucu bu sınırı kapatmıyor.

**Düzeltme ölçütü:** Compile aşamasında doğrulanmış `ToolBindings` üret: müşteri id, question discriminator, security answer, verified/remaining/hint response, product id, amount/date, queue, outcome/reason. Her zorunlu alanın belirli bir kaynağı olmalı. Alias veya enum değişimi tek başına API semantiğinin bağlandığı anlamına gelmez.

Olumlu ayrıntı: customer id, `now`, `full_name` ve product response alan mapping'leri var. Kimlik response route'ları da `resp` kullanıyor. Kalan geliştirme, özellikle kimlik **request pre-hook'una** aynı bağlamanın taşınması ve bütün response rollerinin doğrulanmasıdır.

### F09 — Kimlik öncesi insan talebi için açık akış yok

**P0 · Kod bulgusu.** A42 ve A45 insan talebinin doğrulamadan önce de geçerli olduğunu belirtir. Açılış/identity extraction enum'larında insan talebi ve ayrı help-first/transfer yolu yok; negotiation motorunda human yolu var. Workflow'un `global_nodes` alanı boş.

**Düzeltme ölçütü:** Human talebi pre/post verification aşamalarında aynı explicit policy sayacıyla işlenmeli. Bu yapılırken kimlik öncesi borç sızdırılmamalı. Bütün cevapları büyük bir global prompt'a bırakmak yerine ilgili stage event'leri tanımlanmalı.

### F10 — Kayıtlı söz değişikliği talebinde gereken açıklama verilmiyor

**P1 · Yeniden üretildi.** Tam tutar bugün onaylanıp kayıt edildikten sonra yeni tarih önerisi verildiğinde motor doğrudan veda ediyor. A36'nın 'mobil uygulamada değiştirebilirsiniz' açıklaması yok. Kaydın ikinci kez yazılmaması iyi; konuşmanın kalan zorunlu parçası eksik.

**Düzeltme ölçütü:** Kayıt sonrası değişiklik/iptal talepleri kayıt durumunu değiştirmeden açıklanmalı; yeni create_promise çağrısı çıkmamalı.

### F11 — Callback, transfer ve note sonuçlarına bakılmadan ilerleniyor

**P1 · Kod bulgusu.** `cb_book → fin_callback`, `transfer → wrap`, `note → after_note` yolları tool'ın `ok` sonucuna göre ayrılmıyor. Callback başarısız olsa bile 'booked' denebilir; transfer veya not başarısızlığında bankadaki sonuç iddia edilen sonuçla ayrışabilir.

**Düzeltme ölçütü:** Yazma başarısı doğrulanmadan başarı cümlesi ve terminal outcome verilmemeli. Brief'in belirttiği retry sınırı korunmalı; bütün yazmalara kör otomatik retry eklenmemeli. Publicte özellikle create_promise arızası verilmiş; finalde diğer arızaların mutlaka geleceği iddia edilmiyor.

### F12 — Northwind override extraction'ı geçti; genel yürürlük modeli açık

**P1 · Kısmen kapandı / kod bulgusu.** Northwind'in üç memo override'ı doğru çıkarılmış ve workflow'a derlenmiş. Bu yüzden 'ham materyal başarısı yok' değerlendirmesi kaldırıldı. Kalan sınır, spec'te değer başına `source`, `evidence_quote`, `effective_date`, `overrides` yapısının bulunmaması ve iki yürürlük dönemi arasında çağrı `now`'uyla seçimin sınanmamış olması. Modelin `notes` açıklamaları mevcut örnekte doğru; bütün çelişkiler için otomatik kanıt denetimi yerine geçmiyor.

**Düzeltme ölçütü:** Başarılı Northwind fixture'ı korunmalı; buna belge sırasının tarih sırasından farklı olduğu ve yürürlük öncesi/sonrası çağrıların değiştiği ikinci örnek eklenmeli. Yeni bir genel policy engine yazmadan önce finalin gerçekten gerektirdiği temporal davranışın destek sınırı belirlenmeli.

### F13 — 30 dakika sınırı kesin olarak uygulanmıyor

**P1 · Kod bulgusu.** `BUDGET_S` bugün smoke'u çalıştırıp çalıştırmamak için kullanılıyor. Extraction, retry, dry-run, save ve export'un tamamı deadline denetimine bağlı değil. Bir model isteğinin varsayılan 300 saniyesi × üç attempt, onarımda da tekrarlanırsa yalnızca model çağrıları 30 dakikaya yaklaşabilir; servis süreleri buna eklenir.

**Düzeltme ölçütü:** Monotonic deadline bütün çağrılara kalan süre olarak aktarılmalı, export için süre ayrılmalı ve aşımda trace terminal statüsü yazmalı. Gözlenen 46 saniyelik hızlı run iyi performans kanıtı; worst-case sınırı kanıtı değil.

### F14 — Trace koşu kimliği ve yeniden üretilebilirlik yetersiz

**P1 · Kod bulgusu.** Aynı slot yeni run'da trace/spec/export dosyasını üzerine yazıyor. Unhandled exception'da bitiş satırı garanti değil. Başarısız model attempt'leri başarılı çağrılar kadar düzenli model kimliği/amaç kaydı almıyor. Git commit, kirli çalışma alanındaki gerçek kodun tamamını temsil etmiyor.

**Düzeltme ölçütü:** Run-id klasörü, input/tool/spec/workflow/code hash'leri, giriş ve terminal sonuç, publish version id, aynı sürüme bağlı testler ve seçilen run eşlemesi. Hakem için minimal trace biçimi serbest; bu kayıtları saklamak hataları ve sürüm karışıklığını ayırmayı kolaylaştırır.

## 7. Brief kapsamı: hangi kurallar nerede risk taşıyor?

Bu tablo kapsam haritasıdır; yeşil test sayısı yerine rule → uygulama → kanıt ilişkisi kurulmalıdır.

| Kural grubu | Bugünkü karşılık | Durum / eksik kanıt |
|---|---|---|
| A1–4: açılış, kimin konuştuğu, AI sorusu | opening/opening2 + extraction | Güncel public vakalar 3/3. LLM-build smoke açılışı eksik; önceki PUB-04 kaybı tarihsel. asks_back kapsamı açık. |
| A5–10: üçüncü kişi, callback, ölüm, yanlış numara | third_party + CB_CHECK + finish node'ları | Publicte bulunan vakalar 3/3. İki turda callback gün/saat birleştirme hatası yerelde sürüyor; publicte olmayan istisnalar ayrıca ölçülmeli. |
| A11–15: iki soru, toplam yanlış, swap hint, decline | verify hook + identity/identity2 | Temel akış var; parçalı kimlik ve yeni request/response isimleri riskli. |
| A16–17: hesap okuma ve borcu söyleme | account → facts → negotiation | Validator kimlik geçişini denetliyor. Bütün gerçek konuşma sıraları yerel harness'te yok. |
| A18–23: band, iş günü, vague/late date | collections/speech + negotiation | Public A ledger ve final kontrolü 10/10, her vaka 3/3. ISO düzeltme ve farklı çağrı tarihleri açık. |
| A24–27: full, floor, kısmi tutar | negotiation amount logic | Public floor vakası yerelde geçiyor; toplam tutar veya parçalı tutar farklı conversation layer gerektiriyor. |
| A28–31: today, refusal, attempt bütçesi | step/_flat_refusal | Public tekrar döngüsü düzeltildi; B hepsi 3/3. Non-answer'ın tekrar ask_today üretmesi yerelde sürüyor. |
| A32: hardship, destek, gönüllü öneri | hardship_offer | Soru ile ret karışması ve hardship+offer kaybı yeniden üretildi. |
| A33: yetkisiz plan/indirim | prompt + teklif sınırları | Sabit prohibited içerik yeni brief'te izin verilen bir seçenekle çelişebilir; spec kaynaklı olmalı. |
| A34–35: read-back, açık yes, correction | confirm → record | Temel deterministik kontrol güçlü; karma mesajdaki onay+soru sınıflandırması ayrıca ölçülmeli. |
| A36: kayıtlı söz değişmez | closing | Çift yazmayı önleme var; app açıklaması eksik. |
| A37–38: retry, başarı, SMS cümlesi | record_result + tts | Public failed-first-call final ölçütünü 3/3 geçiyor. Başarı öncesi recorded belirsizliği giderilmiş; yeni spec ile aynı davranış ölçülmeli. |
| A39–40: paid vs dispute | semantic kind + state | Public paid çalışıyor; çok ürün bazında kapsam ve kayıt sonrası durum korunumu genişletilmeli. |
| A41–45: restructure/human/scam/no info/stop | negotiation events + prompt | Güncel public kapsamı 3/3. Önceki soru kaybı güncel baseline'a yazılmamalı; pre-auth human ve karma mesaj kapsamı ayrıca ölçülmeli. |
| A46–47: tek close ve veda | lifecycle close + goodbye | Bilinen normal sonlar var; müşteri erken kapatırsa boş final_outcome riski ayrıca ölçülmeli. |
| B1–5: bütün ürünleri sunma, sıra, ayrı attempts/deadline | product state list | Public üç B vakasının her biri üç tekrarın üçünde geçti. LLM-spec ve farklı tarihler henüz aynı ölçütle denenmedi. |
| B6–7: ortak tarih, deadline farkı, ortak confirmation | offer/readback/record loop | PUB-13 fixture'ı geçiyor; yeni spec varyantlarında otomatik kanıt yok. |
| B8–9: ürün bazında note, paid/dispute devamı | notes/statuses | Deterministik testler var; arızalı add_note sonucu kontrol edilmiyor. |
| B10: transferde not yok; henüz kayıt edilmemiş sözler kalmaz | _transfer/_finish | Policy knob ve tests var; pre-auth transfer hariç gerçek karmaların 3/3 kanıtı yok. |
| B11 ve closing: kısmi servis hatası, ilk non-paid reason | record_result/_finish | Policy tests ve golden ledger var; gerçek çok ürün arızası matrisi eksik. |

## 8. Test altyapısındaki kör noktalar

Gerçek platformun yeni public sonuçları, elle yazılmış spec için aşağıdaki kör noktaların **publicte bulunan konuşma yollarını** kapsayan güçlü kanıttır. Yerel harness'in sınırlılıkları bu başarıyı geçersiz kılmaz; farklı stiller, LLM-spec ve yeni API/brief için otomatik genelleme yapılmasını engeller.

`fb/harness/engine_sim.py` gerçek workflow yorumlayıcısı değildir. Code-node gövdelerini çalıştırır, kimliği başlangıçta doğru cevaplarla otomatik geçirir, müşterinin yanıtını `kind/date/amount/product` olarak hazır alır ve ledger'ı resmi scorer ile karşılaştırır.

Bu yaklaşım hesap/kayıt hatalarını hızlı yakalar. Ancak şu parçaları doğrulamaz:

- Gerçek opening, third_party ve identity graph'ının bütün yolu.
- Freya extraction modelinin anlamsal ve parçalı ifadeyi sınıflandırması.
- Platform routing'in stale extraction, turn cap ve re-entry davranışının tüm kombinasyonları.
- Tool response mapping'in güncel export'ta korunduğu ve gerçek değişken tablosuna yazıldığı.
- Transcript policy ve honesty kontrolleri.
- Spec çıkarıcısının görülmemiş belgeyi doğru okuması.

Public golden_calls/replay, beklenen ledger'dan ideal tool dizisi üretir. Banka ile scorer arasındaki uyumu kontrol eder; builder'ın o diziyi konuşma yoluyla gerçekten seçebildiğini tek başına kanıtlamaz. Mevcut policy knob mutation testleri de insanın CFG'yi değiştirmesine dayanır. Northwind ise gerçekten yeni brief → LLM extraction → değişmiş workflow zincirini sınadı; eksik olan yeni bankanın konuşma/ledger davranışıdır.

Ayrıca validator şu anda grafın end node'a **ulaşabildiğini** denetler. Her gerçek çalıştırmanın sonlanacağını kanıtlamaz; bazı bounded-loop node'ları ayrıca hariç tutulur. Doğrulanmış code_error değişkeni var, ama üretilen graf hata halinde bütün aşamalarda güvenli terminale yönlendirmiyor. Bunlar statik doğrulayıcıya 'policy tamamen kanıtlandı' rolü verilmemesi gerektiğini gösterir.

## 9. En değerli görülmemiş-brief provası

Amaç publicteki 13 vakayı ezberlemek değil, doğru değerlerin yeni girdiden taşındığını ölçmek. Girdiler ve beklenen farklar final açılmadan hazırlanmalı.

Northwind; farklı kimlik soruları, bazı request/response alanları, enum'lar, callback penceresi ve üç memo override'ı için extraction/compile provasını tamamladı. Aşağıdaki tablo sonraki kapsamı tarif ediyor; bu boyutların hepsinin hiç denenmediği anlamına gelmiyor. Özellikle identity request rename, parçalı konuşma ve yürürlük öncesi/sonrası davranış henüz başarılı bir uçtan uca örnekle kapanmadı.

| Prova | Beklenen kontrol |
|---|---|
| 12/7 iş günü yerine 9/5 | Source/spec/deadline sonucu birlikte değişir; eski sayılar cfg'de kalmaz. |
| Band sınırı 7→10, farklı attempts | 10 ve 11 gün geçişi ürün bazında doğru; bandlar çakışmaz. |
| Farklı tatiller ve ay/yıl geçişi | İlgili tarihler yeni kalenderden hesaplanır. |
| DOB/phone yerine postcode/birth-year veya company/founded-year | Soru sırası, kind ve API field değerleri girdiden gelir. |
| Request field ve response field rename | Hook, extraction ve route aynı semantik binding'i kullanır. |
| Outcome/reason/queue enum değişimi | Hiçbir eski literal tool gövdesinde veya finish state'te kalmaz. |
| Callback penceresi 10–19 yerine 09–17; day/time iki tur | Yeni sınırlar ve kısmi birleştirme birlikte çalışır. |
| Tek policy + değiştirici memo | Kazanan değer ve kanıtı bulunur; yürürlük başlangıcının iki yanında doğru seçim yapılır. |
| Yanlış tarih/tutar sonra aynı cümlede correction | Yalnızca son anlamlı düzeltilmiş değer read-back'e girer. |
| Aynı cümlede hardship + offer + AI sorusu | Sempati, gönüllü teklif ve soru yanıtı birlikte korunur. |

Bu provada iki ayrı bağımsızlık gerekir: beklenen kural/değer mutation fixture'ından gelir; spec'i üreten modelin kendisi bütün sonuçların hakemi yapılmaz. Kimlik, araç ve ledger kontrolleri deterministik; anlam/konuşma kontrolleri kısa ve kural bazlı rubric ile değerlendirilir.

Final için bütün varsayımsal API özelliklerini desteklemek gerekmiyor. En azından kitin açıkça değişebileceğini söylediği field/enum/question/calendar/rule boyutlarının çalışan bir örneği bulunmalı. Desteklenmeyen davranışın sessiz default'a dönüşmesi önlenmeli.

## 10. Doğal konuşma için hedef olay modeli

Bugün tek `reply_kind` kullanmak uygulamayı basitleştiriyor; ama bir müşteri mesajı birçok olayı birlikte taşıyabilir. Örnek: 'İşimi kaybettim ama cuma 100 ödeyebilirim; siz robot musunuz?' Böyle bir mesajda hardship veya offer'dan yalnızca birini seçmek bilgi kaybı yaratır.

Önerilen sınırlı geliştirme: mevcut motor korunup reply yapısı zenginleştirilsin. Şu roller birbirinden ayrı tutulmalı:

- Açık payment offer ve değiştirilen amount/date.
- Mevcut teklife acceptance/rejection.
- Question ve hangi sorunun sorulduğu.
- Hardship, dispute, paid, human/restructure/stop olayları.
- Partial bilgi ve beklenen devam.
- Product scope: bir ürün, ürünler altkümesi veya bütün ürünler.

Motor her mesajda brief'teki önceliğe göre olayları işler; araya giren soruyu yanıtlayınca açık karar aşamasına geri döner. Kısmi bilgi ayrıca pending state'te tutulur. Onay, onaylanan ürün/tutar/tarih kümesine bağlı kalır; müşteri düzeltirse yeniden read-back gerekir.

Bunun amacı mevcut motorun yakalanmış bilgi kayıplarını gidermek. PUB-04/PUB-05'in önceki kayıpları güncel public koşularda kapandı. F03–F06'nın yerel karşı örnekleri ise hedefli stil testleri için başlangıç noktasıdır; geniş bir motor değişikliğinden önce LLM-spec baseline'ı ölçülmeli ve çalışan 3/3 sürüm saklanmalıdır.

## 11. Öncelik ve çalışma sırası

İlk üç iş kullanıcının bildirdiği eksik testlerle aynı sırada. Yeni public ölçümden önce başarılı baseline'ın spec/workflow/code hash'leri ve publish version'ı saklanmalı.

| Sıra | İş | Bitti sayılma ölçütü |
|---:|---|---|
| 1 | `build.py` ile LLM-spec'ten üretilmiş A/B ajanını `--reps 3` ile simüle et | Her vaka aynı sabit yayın sürümünde 3/3; ledger ve transcript sonuçları saklı. Başarısızlıkta elle yazılan spec ile çıkarılan spec'in ilgili kural/alan/metin farkı bulunmuş. |
| 2 | pieces, corrects, talkative, asks_back ve in_words stillerini dene | Önce callback, kimlik, hardship, correction ve çok ürün vakaları; ardından kapsam genişletme. Stil/vaka başına k/3 ve başarısız transcript mevcut. |
| 3 | Farklı çağrı tarihleri için yerel sınır testleri | Cuma, tatil arifesi, ay/yıl sonu ve ürünlerin farklı deadline'ları bağımsız beklenen tarihle karşılaştırılmış. Practice bank'ın sabit tarihini değiştirerek resmi skor üretilmemiş. |
| 4 | Yeniden üretilen konuşma ve identity-binding karşı örneklerini düzelt | Split callback, hardship+question/offer, non-answer, ISO correction ve gerçek derlenmiş renamed identity hook doğru davranıyor; ilgili public regresyonlar korunuyor. |
| 5 | Spec tip/tutarlılık denetimi ve hatada durma | Yedi bozuk spec reddediliyor; hâlâ bozuk repair publish edilmiyor. Alan eşlemesi ve belirsiz policy değerleri sessiz default'a düşmüyor. |
| 6 | Northwind için yerel davranış provası ve ikinci temporal fixture | Yeni API'ye uygun yanıtlarla kimlik/ledger/konuşma sınanmış; memo yürürlük öncesi/sonrası seçimi ölçülmüş. Practice bank'ta olmayan Northwind vakaları varmış gibi raporlanmamış. |
| 7 | Deadline, trace ve clean-environment teslim provası | Her hata terminal trace bırakıyor; iki brief birer komutla export oluyor; tested/published/exported sürüm eşleşiyor. |
| 8 | Teslim paketini ve güncel belgeleri sabitle | Kod ve gerekli fixture'lar yeniden üretilebilir; final için seçilen trace/export paketlenmiş. Geliştirme test çıktılarıyla final teslim kanıtı ayrılmış. |

Yeni elle yazılmış-spec koşuları A için 387, B için 357 saniye sürdü; bunlar LLM-spec veya stil testlerinin süre tahmini değildir. 46–80 saniyelik build süresi de davranış simülasyonunun süresinden ayrı raporlanmalı.

Yarışma öncesi hız kazanmak için public testlerin tamamını her küçük değişiklikten sonra üç kez koşmak gerekmez. Önce ilgili vakaları birer kez koş, ardından sabit bir release adayı için üç tekrar ve seçilmiş konuşma stilleri ölç. İki farklı workflow sürümünün sonuçlarını aynı 3/3 hesabında toplama.

Final build içinde önerilen varsayımsal süre rezervi: spec ve gerekiyorsa sınırlı audit için 5–7 dk, generation/validation/publish için 1–2 dk, hedef smoke/self-check için kalan süreden kontrollü pay, export/trace için son 2–3 dk. Bunlar gözlenen süre dağılımı değildir; 30 dk sınırı için önerilen rezerv tasarımıdır. Gizli brief'te public bankanın aynı vakaları bulunacağını varsayarak official practice testini zorunlu kılma.

## 12. Şimdi kapsam dışında tutulabilecek işler

Yarışma süresinde en yüksek getiriyi sağlamlaştırma veriyor. Yeni çok adaylı optimizer, crossover/OPRO, genel RAG, Türkçe/ses altyapısı, yeni UI, farklı domain benchmark kurulumu ve büyük framework değişimi öncelikli değil. Eski modüllerin silinmesi de gerekmiyor; ana README ve runbook hangi komutun güncel yarışma yolunu çalıştırdığını açıkça göstermeli.

Özellikle `README.md`, `docs/RUNBOOK.md` ve `docs/RULES_MAPPING.md` önceki generic planı anlatıyor; `docs/HACKATHON_REALIGN.md` ve `docs/GENERATOR.md` güncel bağlamı daha iyi taşıyor. Güncel tek giriş komutunun ve gerçek durumun belgelerde görünmesi ekip içi yön karışıklığını azaltır.

## 13. Dış araştırma bize ne katıyor?

**Workflow tercihi.** Anthropic'in [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents) yazısı, önceden tanımlı kod yollarıyla çalışan workflow'ları dinamik karar veren ajanlardan ayırıyor; iyi tanımlanmış görevlerde öngörülebilirliği, ek karmaşıklığın maliyet ve latency'sini tartışıyor. Bu yarışmada sabit süreç ve küçük runtime modeli nedeniyle deterministik workflow'u tercih etmek bizim mühendislik çıkarımımızdır; yazı bu projede ölçülmüş üstünlük kanıtı değildir.

**Tutarlı başarı ve son durum.** [τ-bench](https://arxiv.org/abs/2406.12045), tool/policy/user etkileşimini ve hedef veritabanı durumunu birlikte değerlendirir; tekrarların hepsinde başarıyı ölçen pass^k yaklaşımını ele alır. Yarışmanın ledger ve üç-tekrar koşuluyla benzerlik açıktır. Buradan çıkardığımız uygulama kararı, ortalama tek-run puanı yerine müşteri bazında istikrarlı geçişi raporlamaktır. Makaledeki model skorları Freya modelinin beklenen skoru olarak kullanılamaz.

**Harness ile ürünün aynı olması.** Anthropic'in [Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents) yazısı outcome ile transcript ayrımını, deterministik/model/human grader'ların farklı rollerini ve agent+harness'in birlikte değerlendirilmesini anlatır. Bizim çıkarımımız: yerel golden replies harness'i hesapları doğrulamak için yararlı, Freya extraction+routing+speech değerlendirmesinin yerine geçmek için yetersiz. Aynı yazıdan yararlanan test tasarımında objective ledger kontrolleriyle kısa transcript rubric'i birlikte kullanılmalı.

**Builder'ın kendisini ölçmek.** [τ^τ-Bench](https://arxiv.org/abs/2609.04611) ajan kurulumunu başlı başına benchmark görevi olarak konumlandırır. Bu, yarışmada public agent'ın başarısı ile yeni brief'ten doğru agent üreten builder'ın başarısını ayırmak için yararlı bir çerçevedir. Buradan çıkardığımız somut öneri, metamorfik brief/API girdileriyle gerçek extraction→workflow zincirini ölçmektir. Bu çalışma ile yarışmanın aynı benchmark olduğu iddia edilmiyor.

Kaynaklar yeni bir framework kullanmayı gerektirmiyor. Mevcut koda en yararlı aktarım: doğru son durum, tutarlı tekrar, daha iyi olay temsili, bağımsız oracle ve gerçek platformla aynı test yüzeyi.

## 14. Teslime hazır olma eşiği

Bugünkü kanıtlar şu durum ayrımını destekliyor:

| Boyut | Değerlendirme |
|---|---|
| Yarışma yönü | Doğru yola dönülmüş. |
| Elle yazılmış spec ile public ajan | A 10/10, B 3/3; her vaka üç tekrarın üçünde final ölçütünü geçti. |
| Tek komut build/publish/export | Gerçek public A+B trace'iyle kanıtlı. |
| LLM-spec ile public ajan | Build/export/smoke var; final ölçütüyle davranış simülasyonu henüz yok. |
| Gerçek konuşma kuralları | Public baseline başarılı; beş stil denenmedi, karma/parçalı mesaj karşı örnekleri yerelde sürüyor. |
| Görülmemiş ham brief / değişmiş API | Northwind extraction ve compile başarılı; davranış ve identity request rename açık. |
| Memo önceliği / yürürlük çatışması | Üç Northwind override'ı doğru. Genel temporal seçim ve kaynak denetimi henüz kanıtlanmış değil. |
| Farklı tarihler | Public sabit tarihte başarılı; farklı çağrı günü sınır matrisi açık. |
| Tekrarlanabilir teslim | Temel trace/export var; run/version/hash ve deadline sertleştirmesi gerekli. |

Release adayı için aşağıdaki sonuçlar birlikte aranmalı:

1. Elle yazılmış spec için public A/B üç tekrar eşiği karşılandı; aynı eşik LLM-spec'ten üretilmiş sabit yayın sürümü için de karşılanmış.
2. Beş konuşma stilinden özellikle pieces, asks_back ve corrects ile yakalanmış riskler ve farklı çağrı tarihi sınırları sınanmış.
3. Şu yerel karşı örnekler düzelmiş: split callback, hardship+question, hardship+offer, non-answer, ISO correction, renamed identity hook.
4. Bilinen bozuk spec'ler publish'e ilerlemiyor.
5. Northwind'in başarılı extraction/compile kanıtı davranış testiyle tamamlanmış; kaynak önceliği için temporal sınır örneği mevcut.
6. Export içindeki workflow, yayınlanan ve test edilen sürümle eşleşiyor.
7. Gerekli kodlar, bağımlı kit dosyaları ve seçilen trace/export commit'te bulunuyor.
8. Deadline ve hata sonlandırma clean-environment provasından geçmiş.

Yeni sonuçlar, mevcut deterministik mimarinin public görevlerde istikrarlı çalıştığını gösteriyor. Karar artık mimariyi yeniden kurmak değil, aynı başarıyı LLM-spec zincirinde ölçmek ve finalin değiştirebileceği konuşma/brief/API/tarih boyutlarında sınamak. Bu araştırma güncellemesinde commit, deploy veya yeni ücretli simülasyon yapılmadı.

## 15. Dosya ve kanıt rehberi

| Amaç | Kaynak |
|---|---|
| Yarışmanın bağlayıcı yerel şartları | `kit/README.md`, `kit/briefs/brief-a.md`, `kit/briefs/brief-b.md` |
| Resmi tool ve scorer sözleşmesi | `kit/tools/openapi.json`, `kit/tools/score.py`, `kit/tools/run_cases.py` |
| Workflow runtime referansı | `kit/docs/workflow-format.md`, `kit/docs/tools-and-code.md`, `kit/docs/api.md` |
| Güncel builder girişi | `builder/build.py` |
| Extraction ve denetim | `builder/fb/spec_extract.py`, `builder/fb/llm.py` |
| Workflow ve hook üretimi | `builder/fb/generate.py` |
| Tarih, speech, state machine | `builder/fb/nodecode/collections_lib.py`, `speech_lib.py`, `negotiation_lib.py`, `bodies.py` |
| Statik doğrulama | `builder/fb/validate.py` |
| Yerel harness ve scorer | `builder/fb/harness/engine_sim.py`, `scoring.py`, `bank.py` |
| Yerel regression/policy fixture'ları | `tests/hack/` |
| Güncel public üç-tekrar sonuçları | `kit/results/gen-a-3.json`, `kit/results/gen-b-3.json`; önceki sonuçlar tarihsel karşılaştırma |
| Public LLM-spec builder koşusu | `traces/brief-ab-run.log`, `submission/brief-ab-run.spec.json`, `submission/brief-ab-run.workflow.json` |
| Northwind ham materyal ve API | `tests/metamorphic/northwind/brief.md`, `tests/metamorphic/northwind/openapi.json` |
| Northwind LLM-spec builder kanıtı | `traces/northwind.log`, `submission/northwind.spec.json`, `submission/northwind.workflow.json` |
| Eski yaklaşım ve yön değişimi | `freya_build_the_builder_research.md`, `docs/HACKATHON_REALIGN.md`, `docs/GENERATOR.md` |
| İlk araştırmanın yerel kanıtı | `docs/research-2026-10-10/audit.py`, `audit.json` |
| Güncellemenin yerel kanıtı | `docs/research-2026-10-10/audit-reassessment.json`, `reassess.py`, `reassess.json` |

Yerel problar `.venv/Scripts/python.exe -X utf8 docs/research-2026-10-10/audit.py docs/research-2026-10-10/audit-latest.json` ile yeniden çalıştırılabilir; ayrı hedef ilk kanıtı korur. Güncellemenin sonuç/Northwind/derlenmiş-hook kontrolleri `.venv/Scripts/python.exe -X utf8 docs/research-2026-10-10/reassess.py` ile çalışır ve `reassess.json` dosyasını yeniler. Bunlar ağ, publish veya ücretli model çağrısı yapmaz. Çalışma alanında public spec, kit/results ve `.builder` kayıtları bulunduğu varsayılır; bu kayıtların tamamı git takibinde değildir. Ham JSON, sonraki çalışma alanı değişiklikleriyle karıştırılmaması için tarih ve hash içerir.
