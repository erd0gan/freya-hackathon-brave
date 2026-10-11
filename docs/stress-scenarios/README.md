# BRAVE — Zorlayıcı banka ve sigorta senaryoları

11 Ekim 2026. Tamamı bu çalışma için üretilmiş sentetik test malzemesidir; resmi gizli brief veya gerçek kurum politikası değildir.

**8 yeni brief, 64 ayrıntılı vaka ve 16 konuşma/dönüşüm varyantı.** Amaç yalnızca A/B'yi farklı isimlerle tekrar etmek değil; builder'ın yeni politikayı okuyabilmesini, eski kuralı taşımamasını ve konuşma boyunca doğru durumu korumasını sınamak.

**Gerçekçilik genişlemesi:** Ayrıca [30 durumsal müşteri profili](realism/CUSTOMERS.md), [30 dallanan pilot görüşme](realism/ENCOUNTERS.md) ve [mevcut testlerin gerçekçilik incelemesi](realism/REVIEW.md) eklendi. Her profil bir pilotta somutlaştırıldı. Bunlar sabit bütçe, müşteri bilgi sınırı, güven, anlaşılma ve işlem rızasını ayrı tutar. Toplam 94 tasarım artık ayrı sentetik test servisine yüklenebilir; tamamının çalıştırıldığı iddia edilmiyor. [Çalıştırma ve Freya bağlantısı](RUNNING.md).

## Dosyalar

- [Senaryo kataloğu: 64 vaka](SCENARIOS.md): başlangıç durumu, İngilizce müşteri replikleri, beklenen kayıt ve yasak davranışlar.
- [Makine tarafından okunabilir aynı vakalar](scenarios.json): özel değerlendirme düzeneği için veri; mevcut runner'a doğrudan takılan bir test paketi değildir.
- [Konuşma varyantları ve değerlendirme planı](EVALUATION.md).
- [Çalışan backend, model müşteri ve koşu sürücüsü](RUNNING.md).
- [İlk çalıştırmalar, bulunan altyapı hataları ve ham kanıtlar](VALIDATION.md).
- [R01 para birimi takibi: kaynak, düzeltme ve tek yeniden koşu](R01-FOLLOWUP.md) (baseline ajan).
- [BRAVE workflow'u Freya'da: R01, D-04, R30 canlı koşuları ve bulgular](BRAVE-FREYA-RUNS.md).
- [Müşteri test laboratuvarı: kullanım, kontroller ve kanıt](realism/README.md).
- `briefs/`: her biri tek başına okunabilen İngilizce görev dosyaları. Müşteri vakaları builder girdisine dahil edilmemeli.
- `contracts/`: C–H için yedi resmi araç adını koruyan sentetik OpenAPI dosyaları. `.invalid` adresleri bilerek çalışmaz; gerçek API değil, sözleşme ve derleme test girdisidir.
- `build_pack.py`: bu paketin belge/veri üreticisi; BRAVE builder'ı veya test runner'ı değildir. Yalnızca bu klasördeki üretilmiş dosyaları yeniden yazar.

| Brief | Alan / biçim | Asıl zorluk | Kapsam |
|---|---|---|---|
| [C — Meridian](briefs/brief-c.md) | Banka / temiz şartname | Pazar–Perşembe iş haftası, yeni tatiller, sıfırla başlayan kimlik | Final biçimine yakın sentetik stres |
| [D — Alder](briefs/brief-d.md) | Banka / e-posta + politika + memo + kod listesi | Yürürlük tarihi, eski ve yeni değerler, belge önceliği | Final biçimine yakın sentetik stres |
| [E — Vector](briefs/brief-e.md) | Banka / API ağırlıklı şartname | Aynı araç adları, yeni alanlar, integer cents, iç içe yanıt | Final biçimine yakın sentetik stres |
| [F — Lattice](briefs/brief-f.md) | Banka / karar tablosu | Üç ürün, global neden önceliği, anında transfer, transfer notları | Final biçimine yakın sentetik stres |
| [G — Cedar](briefs/brief-g.md) | Sigorta / gecikmiş prim | Üç poliçe, teminat iddiası yasağı, poliçe bazlı tutar ve tarih | Tahsilata komşu alan aktarımı |
| [H — Beacon](briefs/brief-h.md) | Sigorta / ham operasyon dosyası | Prim borcu–yenileme ayrımı, gelecekteki memo, kanal sözü | Tahsilata komşu alan aktarımı |
| [I — Harbor](briefs/brief-i.md) | Sigorta / hasar ilk bildirimi | Olay tarihi, mükerrer hasar, yaralanma, belirsiz yazma sonucu | Mevcut tahsilat motoru dışı genişleme |
| [J — Summit](briefs/brief-j.md) | Sigorta / zeyil ve yenileme | Teklif süresi, iki bağımsız rıza, sürüm çakışması | Mevcut tahsilat motoru dışı genişleme |

## Kaynaklardan çıkan tasarım kararları

1. [Kit v3](../../kit/README.md), [Brief A](../../kit/briefs/brief-a.md), [Brief B](../../kit/briefs/brief-b.md): finalde kurallar, kimlik soruları, takvimler, alanlar ve enum'lar değişebilir; bir vaka üç tekrarın tamamında geçmelidir.
2. [Resmi sayfa](https://www.freyavoice.ai/hackathon), 11 Ekim 2026'da okundu: yarışma tahsilat odaklıdır, konuşmalar İngilizcedir ve practice bankası yalnızca PUB-01…PUB-13'ü açar. Sigorta hasar/yenileme akışları resmi final tahmini değildir.
3. [BRAVE açıklaması](../brave-algorithm.html): spec çıkarımı, varsayılanlar, semantik alan eşlemesi ve deterministik motor ayrı ayrı sınanmalı. Özellikle eksik bir kuralın Brief A'nın değerleriyle sessizce doldurulması yakalanmalı.
4. [Konuşkan müşteri araştırması](../research-talkative/README.md): yarım cümleler, eski extraction değerleri, bir tarihin yanlış ürüne uygulanması, “that Tuesday” referansı, uzun konuşma/code-data limiti için özel vakalar eklendi. Buradaki bulgular geçmiş araştırma kayıtlarıdır; bugün hepsinin hâlâ açık hata olduğu iddia edilmiyor.
5. [Yeniden hizalama](../HACKATHON_REALIGN.md), [yarışma araştırması](../COMPETITION_RESEARCH_2026-10-10.md) ve mevcut README: aktif yol `builder/build.py`; eski `src/builder` hakkındaki [GAP_ANALYSIS](../GAP_ANALYSIS.md) bulguları güncel motor arızası gibi sunulmadı.
6. [Mevcut sigorta örneği](../../examples/bundles/insurance_claims/policy.md): I, bunun yetkilendirme/yaralanma/hasar ilkelerini yeni zor durumlarla genişletir; mevcut collections builder'ının bunu desteklediği varsayılmaz.

## Önce hangi vakalar?

İlk tur: **C-01, C-03, D-01, D-06, E-01, E-02, E-07, F-01, F-03, F-06, G-04, H-01**. Bunlar takvim, memo, alan eşlemesi, hata sonrası dürüstlük, ürün kapsamı ve transfer önceliğini hızlıca açığa çıkarır.

Sonra C–H'nin tamamı; en son I/J ile motorun destek sınırı. Bu sıra, yeni alan geliştirmesinin yarışmaya doğrudan hazırlığın önüne geçmesini önler.

## Kullanım ve doğrulama durumu

İlk teslim senaryo üretimiydi. Sonrasında bağımsız HTTP backend, müşteri modeli, yerel ajan baseline'ı, kayıt denetimi ve Freya chat adapter'ı eklendi. İlk model görüşmeleri ve altyapı testleri yapıldı; Freya deployment'ı veya bütün paketin başarı oranı ölçülmedi. Kaynak katalogdaki tasarım durumları canlı test sonucu değildir. [Kullanım ve sınırlar](RUNNING.md).

`kit/tools/run_cases.py --cases` sunucuda yalnızca vaka ID'siyle session açar; fixture'daki müşteri/ürünleri sunucuya yüklemez. C-01 gibi ID'ler resmi practice bankasında çalışmaz. Bu nedenle yeni `builder/tools/stress_lab.py` ayrı bankayı kullanır ve kimliği önceden otomatik doğrulamaz. C–J backend sözleşmeleri export edilebilir; I/J'nin Freya'da çalışması için ayrıca uygun workflow gerekir.

Önerilen ölçüm: bağımsız fixture + araç kayıtlarıyla sonuç, transcript ile politika ve dürüstlük. Yerel motor testi, doğal dil anlama veya Freya üzerinde çalışma kanıtı sayılmamalı. Hiçbir sonlu senaryo seti gerçek testte sıfır hata garantisi vermez; bu paket kör noktaları görünür kılmak için tasarlandı.
