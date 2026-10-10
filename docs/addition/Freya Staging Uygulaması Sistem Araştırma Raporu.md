# Freya Staging Uygulaması: Sistem Araştırma Raporu

Oct 10, 2026 · @Burak

## 1. Özet

`staging-app.freyavoice.ai`, Freya'nın sesli yapay zeka platformunun staging ortamıdır. Platform; sesli ajanlar (agents), düğüm tabanlı iş akışları (workflows), giden arama kampanyaları, simülasyon ve birim testleri, çağrı sonrası analiz (insights, feedbacks), izleme/alarm ve çok kanallı dağıtım (telefon numaraları, WhatsApp, mesajlaşma kanalları) sunan bir "çağrı yönetimi" paneli. Panelin başlığı "Automated Call Management, Powered by Freya".

İncelenen workspace ("Burak Erdoğan's Workspace") yaklaşık 3 saat önce oluşturulmuş ve neredeyse boş: tek bir workflow ("Freya Bank Collections Base"), sıfır çağrı, sıfır ajan, sıfır telefon numarası. Bu yüzden rapordaki ayrıntının büyük kısmı o tek workflow'un yapısından ve uygulamanın sunduğu yapılandırma yüzeyinden geliyor.

**Kapsam notu:** Yalnızca okuma yaptım. Hiçbir kayıt oluşturmadım, değiştirmedim, çağrı başlatmadım, API anahtarı oluşturmadım. Arayüz dili İngilizce, ses ve STT dili Türkçe ayarlı.

## 2. Teknik mimari (gözlemlenen ve çıkarılan)

| Katman | Bulgu | Kesinlik |
| --- | --- | --- |
| Ön yüz | Sayfa geçişlerinde `_rsc` parametreli istekler var; bu, Next.js App Router imzası | Çıkarım |
| API | Tüm veri `https://staging-app.freyavoice.ai/api/v2/...` altında: `workflow`, `agents`, `versions/workflow/{id}`, `assertions`, `insights`, `integrations`, `phone-numbers`, `models`, `audio-assets`, `unit-tests/checks-summary`, `agents/defaults` | Gözlem |
| Çok kiracılık | Her istek `workspaceId` ile kapsamlı; doğrudan çağrıda `Workspace ID is required but not found` (400) dönüyor | Gözlem |
| Workflow editörü | React Flow tuvali + Monaco tabanlı JSON editörü (`/monaco/...`) | Gözlem |
| Telemetri | `/ingest/...` (ürün analitiği), `/monitoring?...` (hata izleme tüneli), `/cdn-cgi/rum` (Cloudflare RUM) | Çıkarım |
| Mock backend | Workflow araçları `freya-bank-mock.freyavoice.workers.dev` (Cloudflare Workers) üzerindeki sahte banka API'sini çağırıyor | Gözlem |
| Bölge | Giriş ekranında ve üst çubukta `US` bölge seçici var | Gözlem |

Sürüm yönetimi git benzeri: workflow'lar sürümlenir (v2 Published), "Solutions" sayfasındaki *Branches* ise "bir ajan veya workflow için önerilen değişiklik; kaydedilir, simüle edilir, birim testten geçirilir ve pull request gibi birleştirilir" modeli.

## 3. Menü haritası

| Bölüm | Sayfa | Amaç | Durum |
| --- | --- | --- | --- |
| Analyze | Home | Karşılama, bölgeye göre çağrı dağılımı haritası, son çağrılar, hızlı eylemler | Boş |
| Analyze | Call Logs | Çağrı listesi; sekmeler: All, Live, Transferred, Successful, Failed; sütunlar: asistan, numara, durum, süre, maliyet, duygu, temalar | Boş |
| Analyze | Analytics | Calls / Customers / Insights sekmeleri, tarih, asistan, tür, numara filtreleri | Boş |
| Optimize | Feedbacks | Çağrılardan otomatik tespit edilen geri bildirimler; Grouped / Table / Kanban görünümü; önem, atanan kişi, durum, çözüm | Boş |
| Optimize | Solutions (Beta) | Branches: önerilen değişiklikler (Open / Merged / Discarded) | Boş |
| Optimize | Simulation (Beta) | Simulations, Personas, Assertions, Unit tests | Boş |
| Build | Workflows | Düğüm tabanlı konuşma akışları | 1 workflow |
| Build | Agents | Tek ajanlı yapılandırma | Boş |
| Build | Knowledge Base (Beta) | Website sync, doküman yükleme, snippet'ler, önizleme testi | Boş |
| Build | Integrations | Apps + Auth profiles | 15 uygulama, 0 profil |
| Build | Insights | Çağrılardan çıkarılan çalışma alanı seviyesinde alan tanımları | Boş |
| Deploy | Outbound Calls | Kampanyalar, hızlı giden arama, Dial protection | Boş |
| Deploy | Phone Numbers | Numaralar, yetenekler, aktif yapılandırma | Boş |
| Deploy | WhatsApp | WhatsApp Business bağlantısı | Bağlı değil |
| Deploy | Messaging channels | Mesajlaşma platformlarından Messaging Channel API ile gelen konuşmalar | Boş |
| Observe | Monitoring | Eşik aşımında otomatik açılan sorunlar | 0 açık |
| Observe | Alert Rules | 15 hazır kural | Açık, kanalsız |
| Observe | Notification Channels | E-posta, HTTP webhook, Slack | Boş |
| Observe | Event Log | Platform olayları | Boş |
| Developers | API Keys | Anahtar yönetimi, Playground, API Docs | Boş |
| Hesap menüsü | Account, Billing, Documentation, Workspace Settings, Sign Out |  |  |

## 4. Mevcut workflow: "Freya Bank Collections Base (2 nodes)"

Açıklama: *"freya-bank-mock üzerinde minimal iki düğümlü tahsilat ajanı: kimlik doğrulama, sonra ödeme sözü. Hackathon geliştirmeleri için taban."* Sürüm v2, Published. Prompt boyutu göstergesi 294 / 65,536 (modelin bağlam penceresi).

### 4.1 Akış

1. **identity** (giriş düğümü, kesintiye kapalı): açılış cümlesi "Hello, this is the digital assistant of Freya Bank. Am I speaking with {{title}} {{last\_name}}?". Görev: yalnızca kimlik doğrulama. Kişi kimliğini teyit edince doğum tarihini, sonra telefonun son 4 hanesini ister; her cevap için `verify_identity` çağırır. Banka neden aradığını bilmez, güvenlik kontrolünden sonra açıklar. Başarısızlıkta "Have a good day." ile kapatır.
2. **Geçiş koşulu:** `verify_identity_response.data.all_verified` değeri `true` veya `True` (OR) ise `collection` düğümüne geçilir.
3. **collection** (kesintiye açık): önce `get_account` çağırır, gecikmiş tutarı söyler, ödeme tarihini sorar, tutar ve tarihi geri okur, müşteri onaylayınca `create_promise` çağırır. Söz kaydedilince ya da müşteri ödemeyi reddedince "Have a good day." ile kapatır. Prompt'ta `{{now}}` ile güncel zaman verilir.

### 4.2 Araçlar

| Araç | Düğüm | İş | Uç nokta (mock) |
| --- | --- | --- | --- |
| `verify_identity` | identity | Tek bir güvenlik cevabını kontrol eder (`field`: date\_of\_birth veya phone\_last4; `answer`; `customer_id`) | POST /v1/verify\_identity |
| `get_account` | collection | Müşterinin geciken ürünlerini getirir | POST /v1/get\_account |
| `create_promise` | collection | `product_id`, `amount`, `date` ile ödeme sözü kaydeder | POST /v1/create\_promise |
| `end_call` | iki düğüm | Aramayı sonlandırır; normalde "Have a good day." ifadesi bunu kendiliğinden tetikler | dahili |
| `close_call` (global) | workflow | Aramanın nasıl bittiğini kaydeder; `outcome`: promise / no\_promise / not\_verified / not\_reached; `reason` isteğe bağlı. `alwaysRunsAt: end_of_call` ile her aramanın sonunda çalışır | POST /v1/close\_call |

API araçları 10 000 ile 12 000 ms zaman aşımı ve `maxRetries: 0` ile ayarlı. `close_call` için bir ön işleme betiği var: sonuç `promise` ise `reason` alanını siler.

### 4.3 Ajan yapılandırması

- **LLM:** "Freya YC LLM" (`experimental-5`), maxTokens 1000, sıcaklık 0.7, asistan konuşmayı başlatır, ilk mesajda kesinti kapalı.
- **Ses (TTS):** Freya, Türkçe, ses "Leyla" (kadın), Enhanced.
- **STT:** Freya STT, Türkçe; `confidenceReask` kapalı.
- **Çağrı ayarları:** zaman aşımı, azami süre, kapanış mesajı, sessizlik denetimi, dinamik bekleme, sesli mesaj algılama, DTMF girişi.
- **Çağrı sonrası analiz:** özel özet prompt'u, insight üretimi, assertion seçimi, analiz modeli, otomatik feedback tespiti.
- **Guardrails:** çıktı korumaları (küfür, gizli anahtar, kart numarası) ve girdi korumaları (prompt injection, jailbreak, konuşma dışı içerik).
- **PII redaction:** çağrı raporundan kişisel veriyi geri döndürülebilir bir kasaya (vault) çıkarır; yalnızca "Reveal PII" iznine sahip roller görebilir.

### 4.4 Değişkenler

Değişken paneli 32 kaydedilebilir alan listeliyor (14 girdi, 8 araç argümanı, 10 sayaç) ve 59 sistem değişkeni var. Her biri "insight olarak kaydet" ile çağrı listesinde filtrelenebilir bir sütuna dönüşüyor. Girdiler: `amount, answer, customer_id, date, field, first_name, last_name, now, outcome, product_id, reason, title` ile iki test alanı (`test_caller_date_of_birth`, `test_caller_phone_last4`). Sayaçlar düğüm turlarını ve araç başarı/başarısızlık sayılarını tutuyor.

### 4.5 Sürüm geçmişi (Enterprise Logs)

Workspace oluşturuldu, ardından "Freya Bank Collections Base" workflow'u oluşturuldu, "anket-demo" adlı bir ajan silindi, workflow güncellendi ve sürüm 2 yayınlandı. Hepsi aynı kullanıcı tarafından, son birkaç saat içinde.

## 5. Model ve ses kataloğu (`/api/v2/models`)

- **Sohbet modeli:** yalnızca "Freya YC LLM" (`experimental-5`), 65 536 token bağlam, önerilen, analizde kullanılabilir.
- **TTS:** Freya TTS (önerilen); Cartesia Sonic ailesi (Sonic 3.6 önerilen, yaklaşık 31 $/1M karakter, eski sürümler deprecated); ElevenLabs (Flash v2.5 yaklaşık 50 $/1M karakter, Multilingual v2 yaklaşık 100 $/1M karakter); OpenAI gpt-4o-mini-tts (yaklaşık 12 $/1M karakter). ElevenLabs modellerinde hız, kararlılık ve benzerlik ayarları var.
- **STT:** Freya STT ve Freya STT Continuous (anahtar terimler, boost terimleri, boost gücü, süre eşiği, anti-parrot yeniden deneme, akış modu); Deepgram modelleri (eski "base" ailesi deprecated, yaklaşık 0,0125 $/dk).

Yanıt çok uzun olduğu için liste kesildi; Deepgram ve varsa diğer sağlayıcıların tam listesini görmedim.

## 6. Entegrasyonlar

Atlas.so, GitHub, Gmail, Gorgias (iki kayıt), HubSpot, Microsoft, Microsoft (Client Credentials), NetSuite (OAuth), NetSuite, Mock API, Salesforce, Slack, Twilio, WhatsApp Business. Hiçbiri bağlı değil, auth profili yok. Entegrasyonlar workflow içinde kişi, satış verisi gibi bilgileri çeken araçlar olarak kullanılıyor.

## 7. Gözlenebilirlik

Alert Rules'ta 15 hazır kural açık, ama hiçbirine bildirim kanalı bağlı değil:

- **Araç ve hata:** araç hata oranı, çağrı hata oranı, transkripsiyon hataları, model hataları, ses hataları.
- **Gecikme:** tur gecikmesi, transkripsiyon, model ve ses gecikmesi.
- **Trafik:** çağrı hacmi düşüşü ve artışı (7 günlük taban gerekir, en az 5 çağrı).
- **Kalite:** bilgi tabanı boş dönüşleri, müşteri konuşması yok, erken kapatmalar, LLM soğuk prefill.

Tipik koşul: "son 3 çağrının hepsinde sorun varsa Critical", 15 dakikalık pencere. Bildirim kanalı türleri: e-posta, HTTP webhook, Slack.

## 8. Workspace ayarları

- **Genel:** ad, logo, varsayılan dil (en, es, tr, cs), veri saklama (süresiz, 30 gün, 90 gün, 180 gün, 1 yıl, 2 yıl; kısaltmak silmeyi geri alınamaz yapar), iki faktörlü doğrulama zorunluluğu, iki webhook (çağrı sonu, yeni IP girişi), üye yönetimi, workspace silme.
- **Roller:** Owner (115 izin), Admin (111, üye ve içerik yönetir, workspace'i silemez, rol düzenleyemez), Member (72, varsayılan). Özel rol oluşturulabiliyor.
- **Phone Book:** müşteri numaralarını isimle gruplar; test çağrılarından ayırt etmek için.
- **Do Not Call:** listedeki numaralar manuel çağrılarda ve kampanyalarda asla aranmaz.
- **Billing:** 100 $ bakiye, 5 eşzamanlı çağrı kanalı; ön ödemeli kredi (10 $ için 10 $, 40 $ için 50 $, yani %20 indirim); 30 günlük kullanım ve maliyet grafikleri.
- **Affiliates:** %10 komisyon, yönlendirilen workspace'lerin çağrılarından kredi.
- **Enterprise Logs:** giriş, güvenlik ve üyelik olayları, dışa aktarılabilir.

## 9. WhatsApp entegrasyonu

Kendi Meta uygulamanı bir kez bağlıyorsun, sonra istediğin kadar WhatsApp Business numarası ekliyorsun; müşteriler ajanla WhatsApp içinden arayabiliyor ve yazışabiliyor. Sekiz adımlı kurulum: Meta Business portfolio, Meta app, App ID/secret/WABA ID, işletme numarası, kalıcı sistem kullanıcısı token'ı, Freya'da hesabı bağlama (bilgiler şifreli saklanıyor), webhook (callback URL ve verify token), uygulamayı yayınlama. Kısıtlar: arama için günde en az 2 000 konuşma mesajlaşma limiti; işletme başlatmalı aramalar ABD, Kanada, Mısır, Vietnam ve Nijerya'da yok.

## 10. Kamuya açık API ve dokümantasyon

`docs.freyavoice.ai` bölümleri: başlangıç adımları, Fundamentals, Features (Knowledgebase, Analytics, Campaigns), Telephony, Embed SDK ve API referansı. API referansı çok geniş: Campaigns, Webhooks (call outcome, end of call report), Calls (başlatma, toplu silme, ZIP dışa/içe aktarma, web çağrısı kaydı, etiketler, kayıtlar), Agents, Workflows, Feedbacks, Branches, Simulations, Personas, Unit Tests, Insights, Chat, Versions, Analytics ve widget'lar, Phone Numbers, Phone Book, Suppression List, API Keys, Workspaces/Members/Roles/Invitations, Integrations, Reviews, Audio (kapalı beta), TTS kısaltmaları. Ayrıca iki OpenAPI şeması ve Model API (STT, TTS) var.

Dokümantasyonda **bulunmayan** konular: Messaging Channel API, Guardrails, PII redaction ve "Solutions" için ayrı sayfa.

## 11. Herkese açık ürün bağlamı

`freyavoice.ai` ana sayfasına göre ürünler: Voice Agents, Workflow Builder, Simulation, Outbound Campaigns, Noise Cancellation; API'ler: Text to Speech, Speech to Text, Voice Agents, Models. Freya TTS Adam V1 (İngilizce) Design Arena kör testinde 1419 Elo ile ikinci sırada. FreyaTTS-small, Türkçe için sıfırdan eğitilmiş, 48 kHz, Apache 2.0 lisanslı açık model. Panelde de "Freya TTS APIs are now available: en iyi Türkçe TTS" duyurusu var.

## 12. Gözlemler ve olası sorunlar

1. **Dil uyumsuzluğu:** Workflow prompt'ları "Speak English" diyor, ama ses (Leyla) ve STT Türkçe ayarlı. Türkçe konuşan müşteriyle ya da İngilizce konuşan ajanla beklenmedik davranış çıkabilir; bilinçli bir test kurgusu değilse hizalanmalı.
2. **Boş kalite katmanı:** Sıfır assertion, sıfır birim test, sıfır simülasyon; Checks paneli "No checks yet". Branch/merge modeli test olmadan değer katmıyor.
3. **Alarmlar sessiz:** 15 kural açık ama kanal yok; bir sorun çıkarsa kimseye ulaşmaz.
4. **Kimlik doğrulama başarısızlık yolu:** `close_call` sonucu modelin konuşmadan çıkarımına bağlı; `outcome` ve `reason` değerlerini LLM üretiyor. Gerçek kullanımda bu alanların denetlenmesi gerekir.
5. **Tekrarlı Gorgias ve NetSuite kayıtları:** Entegrasyon listesinde Gorgias iki kez, NetSuite OAuth ve normal olarak iki kez var; ikisinin farkı arayüzde açıklanmıyor.
6. **Veri saklama süresiz:** Varsayılan "Indefinite"; gerçek müşteri verisiyle çalışılacaksa süre ve PII redaction açıkça yapılandırılmalı.
7. **Düğümlerde sarı uyarı simgesi:** Hem `identity` hem `collection` düğümünde var; içeriğini açıp doğrulamadım.

## 13. Doğrulanmamış veya incelenmeyenler

- Hiç çağrı olmadığı için gerçek bir çağrının transkripti, maliyeti, duygu analizi ve tema çıkarımı görülemedi.
- Global Nodes, Global Prompt ve Global Tools panelleri, Agent Config sekmelerinin ayrıntı değerleri (zaman aşımı süreleri, guardrail seçenekleri) ve Account sayfası açılmadı.
- Yeni ajan, simülasyon, persona, assertion, kampanya, API anahtarı ve mesajlaşma kanalı oluşturma akışlarına girilmedi.
- Model katalog yanıtının sonu kesildi.
- Mimari katmanındaki "çıkarım" satırları ağ isteği imzalarına dayanıyor, kaynak koddan doğrulanmadı.

## Kaynaklar

- Staging uygulaması (oturum açılmış gezinti, 10 Ekim 2026)
- https://freyavoice.ai
- https://docs.freyavoice.ai
- https://docs.freyavoice.ai/llms.txt
