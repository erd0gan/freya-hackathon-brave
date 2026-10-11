# 08 - Tool-API sözleşme çeşitliliği + mock etkileşimi denetimi

Kapsam: `builder/fb/openapi_tools.py` (parse), `generate.py` (api_request / hook üretimi), `validate.py` (scope/gate),
`spec_extract.py` (check/autocorrect), `nodecode/*` (hook ve facts kodu). Yöntem: `kit/tools/openapi.json`'dan ve
`docs/stress-scenarios/contracts/openapi-c..h.json`'dan türetilmiş mutant sözleşmeler; hepsi OFFLINE
`parse_openapi -> check -> autocorrect -> to_generator -> build -> validate` hattından geçirildi, üretilen hook'lar
`exec` ile çalıştırıldı (ağ yok, Freya/OpenRouter yok, repo dosyası değişmedi). Repro betikleri: `out/audit-scratch/`
(`ta.py` ortak harness, `especs.py` E sözleşmesi için elle uyarlanmış spec, `e1..e14.py`; `python e5.py` gibi çalışır).
Mevcut testler: `pytest tests/hack/test_openapi_tools.py tests/hack/test_fuzz_openapi.py` -> 77 passed
(not: `tests/conftest.py` `builder.llm` import ettiği için varsayılan pytest çağrısı toplama aşamasında düşüyor;
`--confcutdir=tests/hack -c /dev/null` ile koşuldu).

Bağlam (kaynak: organizatör sayfası + kit): finalde telefon ajanı YALNIZCA bizim Cloudflare Workers mock'umuzu çağırır
(`mock_url`), başka servise her çağrı konuşmayı düşürür; "service contract" bir belgedir ve serbest biçimli olabilir; gerçek
banka API'si brief'i zorlamaz. Kit: "yedi araç adı sabit, alanlar ve izinli değerler değişebilir". Girdi biçimi (klasör, docx,
YAML, CLI argümanları) 01-input-contract.md'nin (IC-1/2/3/4) konusu; burada tekrar edilmedi, yalnız tool-API tarafına
etkisi ve yeni kanıt eklendi.

Önemli not: Final mock'u BİZİM yazdığımız için "banka cevabı şöyle olur" varsayımlarının bir kısmını biz seçebiliriz
(duplicate metni, auth yok, base path). Aşağıdaki bulguların "tetikleyici" satırı bunu ayırır: (M) mock'umuzu uyumlu yazarak
etkisiz kılınabilir, (C) sözleşmenin kendi şekli yüzünden mock'tan bağımsız bozulur (builder düzeltmesi şart).

---

## Sıralama (ilk 5)

1. **TA-1 (P0, C)** Araç URL'si yalnız `servers[0].url`'den; `mock_url` hiçbir yerde yok; `servers` yoksa göreli URL ile workflow yayınlanıyor, `validate` susuyor, ve `validate` mock host'unu "foreign_host" diye reddedecek biçimde kurulu.
2. **TA-2 (P0, C)** `wrong_answers` (sayaç) alanlı sözleşmede `autocorrect` bunu `wrong_answers_left`'e (kalan hak) eşliyor: ilk doğru cevap `fin_failed` oluyor, 3. yanlış hiç kilitlemiyor. Yazarın kendi C, D, E, F, G, H sözleşmelerinin hepsi bu şekilde.
3. **TA-4 (P1, C)** Yanıtta minor unit (cents) tutar desteklenmiyor: `42050` -> "42050.00 past due", floor/offer 100x kayık; istek tarafındaki cents dönüşümü yalnız açıklama metnindeki kelimeye bağlı.
4. **TA-3 (P1, C)** Yanıtta "kalan hak" alanı yoksa kilitlenme (3 yanlış) hiç algılanmıyor; ajan "tekrar sor" döngüsüne giriyor (ID_MAX_TURNS=60'a kadar).
5. **TA-5 + TA-6 (P1, C)** Araç çözümü tam ada bağlı (`operationId`/path son parçası) -> `KeyError` ve hiçbir şey yayınlanmaz; çözülemeyen şema (Swagger 2 `in: body`, harici/`~1`/`%20` `$ref`) sessizce 0 alan veriyor ve `validate` aynı bozuk parse'a karşı kontrol ettiği için 0 hata veriyor.

---

## Bulgular

### TA-1 (P0, C) Base URL: yalnız `servers[0]`, mock_url enjekte edilemiyor, göreli/placeholder URL sessizce yayınlanıyor

Kanıt:
- `openapi_tools.py:248-249` `base = servers[0].url.rstrip("/")`; `:283` `url = base + path`; `:454` `freya_tool` `"url": {"default": t.url}`.
  `grep -rn "mock_url\|mock url" builder tests` -> sonuç yok; `build.py:442` ikinci argüman `api_path`.
- `e1.py`: (a) `servers` silindi -> 7 araç URL'si `/get_account`, `/create_promise`... (göreli). `check` = [], `validate` = 0 hata (13 uyarı).
  (b) `servers=[{"url":"/v1"}]` -> `/v1/get_account`, yine 0 hata. (c) `servers[0].url="https://{env}.bank.example/{ver}"` (OpenAPI server
  variables) -> workflow'a harfi harfine `https://{env}.bank.example/{ver}/get_account` gömülüyor, 0 hata. (d) `servers` çoklu ve ilki
  sandbox -> sandbox seçiliyor; operation/path düzeyi `servers` yok sayılıyor (`e13.py`).
- `e5.py` (E sözleşmesi, `https://e-fixture.invalid/v1`): üretilen workflow'daki 7 URL `https://e-fixture.invalid/v1/<tool>`;
  yazarın kendi notu "`.invalid` host placeholder". Hiçbir kontrol uyarmıyor.
- `e1b.py`: workflow'daki kit host'u elle mock host'uyla değiştirip `validate(wf, kit_tools, pol)` -> **9 `foreign_host` hatası**
  (`validate.py:735-739`). Yani doğru davranışı (mock'a yönlendirme) sonradan yapmak validator'ı ve `check_export`'u
  (`build.py` export check) kırıyor; yalnız sözleşmenin `servers`'ını elle mock yapınca 0 hata.
Tetikleyici: final sözleşmesi OpenAPI değilse / `servers` yoksa / organizatörün kendi host'u / `.invalid` placeholder ise (hepsi
makul), ya da sözleşme `/v1` gibi bir path öneki içeriyorsa. Hangisinde olursa olsun tüm çağrılar başka servise gider -> "conversation fails".
Genel düzeltme: `parse_openapi(spec, base_url=None)`; CLI'dan gelen `mock_url` HER ZAMAN `servers`'ı ezer; path öneki
sözleşmeden (örn. `/v1`) alınır, host mock'tan. `validate`/`check_export` aynı `base_url` ile çalışsın (foreign_host = "mock_url dışı host").
`parse_openapi` sonunda her `ToolDef.url` için `urlparse(url).scheme in (http, https) and netloc` zorunlu, değilse build
`status=error` ile dursun (sessiz göreli URL yerine). Trace'e "tool base = ..." satırı. (IC-2 ile aynı kök; burada ek kanıt:
validator'ın mock'u cezalandırması ve server variables.)

### TA-2 (P0, C) `wrong_answers` (sayaç) -> "kalan hak" gibi okunuyor; `autocorrect` bunu kendisi yapıyor

Kanıt:
- `generate.py:594,619,1361-1363`: "locked" = `verify_identity_response.data.<wrong_answers_left> == 0`; post hook `_left == '0'` ->
  "The identity check has failed". Rol adı `wrong_answers_left` = KALAN hak anlamında.
- `spec_extract.py:814-841` (`autocorrect`, response rolleri): `_guess('wrong_answers_left', leaves)` yanıttaki `wrong_answers` alanını seçiyor.
- Yazarın C, D, E, F, G, H sözleşmelerinin altısında da `verify_identity` yanıtı `wrong_answers` (integer sayaç), `wrong_answers_left`/`all_verified`/`hint` yok.
- `e12.py` (F sözleşmesi, kit adlı spec): autocorrect notu `tool_fields.verify_identity.response.wrong_answers_left: 'wrong_answers_left' -> 'wrong_answers'`; sonra
  `{"ok":true,"verified":true,"wrong_answers":0}` (1. cevap doğru) -> hook "The identity check has failed. Do not ask anything else." ve route `fin_failed`;
  `{"verified":false,"wrong_answers":3}` (kilit) -> "That answer is not correct. Ask the same question again." route None (kilitlenme hiç görülmüyor).
  `e6.py` (E, `payload.wrong_answers`) aynı sonuç.
Etki: böyle bir sözleşmede her müşteri 1. doğru cevapta kapatılır (`not_verified/failed`), hiçbir müşteri geçemez.
Mevcut test: `test_fuzz_openapi.py` mutasyonları (01-33) bu şekli içermiyor.
Genel düzeltme: spec'e `identity.wrong_answers_semantics: "left"|"count"` (+ `identity.max_wrong`, brief'ten: "3 wrong answers TOTAL") ekle;
`count` ise koşul `>= max_wrong`, yoksa `== 0`. `autocorrect` bu eşlemeyi anlam bilmeden yapmasın (ad `wrong_answers` ise `count` işaretle).
Alan hiç yoksa TA-3.

### TA-3 (P1, C) Kalan-hak alanı yoksa kilitlenme algılanmıyor -> kimlik döngüsü

Kanıt: `e7.py` (kit sözleşmesinden `wrong_answers_left` çıkarıldı). Yanıtlar: yanlış cevap -> "ask the same question again";
3. yanlıştan sonra `{"ok":false,"error":"identity check locked: too many wrong answers"}` (HTTP 200/403/423) -> "The answer could not be read. Ask for it again."
(`_unread` yolu), route None. `fin_failed` geçişi yalnız `wrong_answers_left == 0`'a bağlı (`generate.py:1361`). Hook kendi yanlış sayacını tutmuyor.
Etki: kilit sonrası ajan sonsuza kadar tekrar sorar; `ID_MAX_TURNS=60` dolunca kapanır (muhtemelen `declined`; yanlış gerekçe).
Tetikleyici: sözleşme kalan-hak/sayaç alanı vermezse, yalnız `ok:false` + metin verirse. Kit'in kendi mock'u kilitte `wrong_answers_left:0` veriyor (harness taklidi),
final mock'umuzda biz de verebiliriz (M) ama sözleşmeden okunan şekil buysa builder kendi saymalı.
Genel düzeltme: post hook `id_wrong` sayar (ok ve verified==false ve not hint ve not _down), `identity.max_wrong` (brief) aşılınca `fin_failed`;
`error` metninde `lock|too many|exceeded|blocked|kilit` -> kilit.

### TA-4 (P1, C) Yanıt tarafında minor unit (cents) tutar desteği yok; istek tarafı yalnız açıklama kelimesine bağlı

Kanıt:
- `grep -n -i "cents\|minor\|scale" builder/fb/nodecode/*.py builder/fb/generate.py` -> yanıt tarafı için hiçbir ölçek yok; `collections_lib.py:256-260` `money()` = `"%.2f" % float(x)`.
- `e5.py` (E sözleşmesi: `arrears_minor`/`floor_minor`, "Integer cents; 42050 means 420.50"): `product_facts` -> `p1_past_due: 42050.00`, `p1_floor: 12600.00`,
  `product_brief: "credit card: 42050.00 past due"`. Ajan 42 bin sterlin söyler; floor/full 100x; müşteri "420" derse "below floor". İstek tarafı ise
  `amount_minor`'e `fmt_value('cents')` uyguluyor (`e5`: `"amount_minor": 42050` her şey doğru olsa bile 100x kayık state'ten 4_205_000 üretir).
- İstek dönüşümü `wire_format` (`openapi_tools.py:325-326`) yalnız `pattern + description` içinde `cents|pence|minor units` arıyor, ALAN ADINA bakmıyor:
  `e3.py` 1: `amount_cents` integer, açıklamasız -> gövde `420.5` (float) gönderiliyor; 1b: açıklama "Amount in cents" -> 42050. "smallest currency unit", "x100",
  "2 implied decimals", "kuruş" -> `''` (dönüşüm yok). `amount` string-decimal (`pattern ^\d+\.\d{2}$`) -> yine float 420.5 (`e3` 2).
Etki: E-tipi sözleşmede (yazarın kendi "API-ağır" varyantı) tutarlar baştan sona yanlış; hiçbir uyarı yok.
Genel düzeltme: `ToolField.unit`/`scale` (açıklama + ad + `multipleOf` + `x-` ipuçlarından; belirsizse spec/LLM sorusu) ve iki yönde kullan:
`product_fields.money_scale` -> `find_products` içinde /100; istekte `ceil/round(x*scale)`; tip `string` ise `"%.2f"`; ad regex'i `(cents|minor|pence|kurus|kurus)` genişlet.

### TA-5 (P1, C) Araç çözümü tam ada bağlı: `operationId`/path son parçası farklıysa `KeyError`, hiçbir şey yayınlanmaz

Kanıt: `openapi_tools.py:260` `name = op.get("operationId") or path.strip("/").split("/")[-1]`; `generate.py:948,999,1008,1043,1054,1067` `tools["verify_identity"]` vb.
`e2.py`: operationId camelCase (`verifyIdentity`...) -> `KeyError: 'verify_identity'`; `bank_<tool>` öneki -> aynı; path `/identity/verify`, `/accounts/lookup`... operationId'siz -> aynı;
`paths={}` -> aynı. build.py bu hatayı `generation failed... nothing published` yapıyor (IC-4 aynı kökü işaretlemişti; burada yeni olan: yalnız DEĞİŞKEN ad biçimi bile yetiyor).
Kit: "yedi araç adı aynı kalır" ama bu garanti `operationId` yazımını kapsamıyor; üretici araçlar (Swagger codegen, Postman) camelCase üretir.
Genel düzeltme (PoC `e14.py` camelCase ve `bank_` önekini 0 hatayla çözüyor): ad = normalize (camel->snake, önek/ek at) -> `STEMS` eşlemesi (verif/identity, account, promise, note, callback, transfer, close),
önce ad+path, sonra `summary`; tek aday yoksa net hata. `ToolDef.name` rol adı olsun, orijinal ad `.op_id` alanında.

### TA-6 (P1, C) Çözülemeyen şema sessizce "0 alan"; `validate` aynı parse'a karşı kontrol ettiği için bunu yakalamıyor

Kanıt:
- `e2b.py` Swagger 2.0 (`parameters: [{in: body, schema}]`, `host`/`basePath`): 7 araçta `fields = []`, `url = /create_promise` (host yok), `response_props = []`;
  `check` = [], `validate` = 0 hata; üretilen workflow'da her `api_call` gövdesi boş (`customer_id` bile yok). `parse_openapi` yalnız `requestBody` ve `in: query|path` okuyor (`:261-270`).
- `e13.py`: harici dosya `$ref: schemas/promise.json#/X`, JSON-pointer kaçışı (`~1`) ve yüzde-kodlu (`%20`) `$ref` -> `_resolve` (`:53-59`) `lstrip("#/")` ile çözemiyor, `_safe` None döndürüyor -> 0 alan, sessiz.
  Döngüsel `$ref` de sessiz 0 alan. Yalnız `application/x-www-form-urlencoded` gövde: alanlar okunuyor ama `Content-Type: application/json` sabit (`openapi_tools.py:459`).
- `validate.py:25,727-760` araç şemasını `parse_openapi(aynı dosya)`'dan alıyor: alan listesi boşsa "required_field" kontrolü de boş -> dairesel doğrulama.
Etki: yayınlanan agent hiçbir çağrıda müşteri kimliği/tutar gönderemez; trace "ok". (IC-4 aynı davranışı `requestBody` silinince göstermişti; burada tetikleyiciler somutlaştı.)
Genel düzeltme: `parse_openapi` sonunda sağlık kapısı: yedi araçtan her birinde >= 1 body/query/path alanı ve bir müşteri-kimliği rolü; `customer_id` rolü bulunamadıysa build hata. `_resolve`: `unquote`, `~1/~0` çözümü, `$ref` çözülemezse uyarı listesi (`ToolDef.warnings`) -> trace.
Swagger 2: `body` parametresi + `host/basePath/schemes` -> 3.x'e normalize eden 20 satırlık adım. Gövde türü `form` ise Content-Type'ı ona göre.

### TA-7 (P2, C) İstek başına üretilmesi gereken zorunlu alanlar (idempotency_key, timestamp) yalnız sabit olabiliyor

Kanıt: `e10.py`: `create_promise.idempotency_key` (required string) + `close_call.closed_at` (required date-time). Bayat spec: `check` sert uyarı verir ("set tool_fields.<tool>.extras"), build yine de yayınlar ve gövdeye `"idempotency_key": ""`, `"closed_at": ""` koyar. Spec `extras` ile çözülürse değer SABİT: `"idempotency_key": "call-1"` -> her ürün için aynı anahtar
(mock "aynı anahtar = ilk cevap" derse 2. ürünün promise'ı ilkinin cevabını alır, kayıt yazılmaz); `closed_at` donmuş bir saat. `grep -n -i "uuid\|idempot\|request_key\|timestamp" builder/fb/generate.py openapi_tools.py` -> yok.
Mock'umuzu bu alanları istemeyecek şekilde yazmak (M) mümkün; ama sözleşme bunu istiyorsa builder üretemez.
Genel düzeltme: `extras` değeri `{"$gen": "key"|"now"|"uuid"}` olabilsin; `request_hook` içinde `key = sha1(customer_id|tool|product_id|amount|date)` (deterministik: retry aynı anahtarı kullanır = gerçek idempotency), `now` = `vars['now']` (call variable, duvar saati değil).

### TA-8 (P2, C) `floor_field` autocorrect edilmiyor; floor sessizce "full"e düşüyor; parasal alan biçimleri

Kanıt: `e4.py` (`minimum_payment->min_due`, `instalment_amount->instalment`, bayat spec): `autocorrect` notları [] (`spec_extract.py:768-880` `floor_field`'e dokunmuyor); `check` raporluyor ama build `ok_spec_problems` ile yayınlıyor;
`product_facts` -> `p1_floor = ''`, `new_state` -> `floor == full == 420.0` (`negotiation_lib.py:121-146`): 126 teklif eden müşteri "below floor" olur, floor teklifi (full ödeyemeyen) hiç sunulmaz.
Uyarlanmış spec ile sorun yok -> ancak LLM extraction doğru adlandırırsa. `e12.py` F sözleşmesi (`floor`) ve `e3.py` camelCase (`pastDue`, `minimumPayment`) aynı `floor_field` kalıntısı.
`e9.py` (facts): `past_due: "420,50"` -> `money()` `'420,50'` döndürür, `to_float` düşer, `new_state` ürünü atar ("nothing can be offered"); `past_due: {"amount":420.5,"currency":"GBP"}` aynı; ürün listesi `{ "p1": {...} }` (dict) veya `{items:[...]}` -> `products_state=unknown` -> `FACTS_ERROR` (service_error kapanış); boş liste aynı (`bodies.py` FACTS_ERROR). Hepsi sessiz.
Genel düzeltme: `autocorrect`'e `floor_field` (ürün tipi başına: sayısal alan, adı `min|floor|instal|minimum` içeren, tek aday); `load_products` dict-of-dicts ve tek anahtarlı zarf; `to_float` ondalık virgülü (tek virgül + 1-2 hane); nesne tutar (`amount|value`); boş liste için ayrı sonuç (`products_state=empty`).

### TA-9 (P2, C/M) "Zaten kaydedildi" tanıma metne bağlı; belirsiz hatada (timeout) yeniden deneme çift kayıt riski

Kanıt: `bodies.py:15-44` `HELD_WORDS` ("already record/exist/creat..."), `NOT_HELD_WORDS`; `generate.py:726-757` `HELD_POST`; `negotiation_lib.py:1385-1403` `record_result`: her başarısızlıkta (kesin ret de, kayıp yanıt da)
`promise_attempts` kadar yeniden dener (`create_promise` `timeoutMs=25000, maxRetries=0`, `generate.py:49,1016`).
`e8.py`: `held_expr` -> `"a promise is already recorded for this product"` True, `"promise already exists"` True; **`"duplicate_promise"`, `"e_duplicate"`, `"promise exists for this product"`, `"e_already_recorded"` (alt çizgi), `"idempotent replay: already applied"`, `"conflict"`, Türkçe `"zaten kaydedilmiş"` -> False** (yalnız HTTP 409 + boş hata metni True).
Stress-lab bankası `DUPLICATE_PROMISE` döndürüyor (`builder/stresslab/bank.py:225`) -> bu şekil gerçek bir "aynı kayıt zaten var" cevabı olmasına rağmen başarısızlık okunur: ilk çağrı commit etmiş + yanıt kaybolmuşsa müşteriye "kaydedemedim" denir, ledger'da söz var, outcome `no_promise/service_error` (dürüstlük + kayıt tutarsızlığı).
Brief E ("lost response ise replay etme, GENERAL'a aktar") bu ayrımı istiyor; generator timeout/5xx (belirsiz) ile `ok:false` (kesin no-commit) arasında ayrım yapmıyor.
Mock'umuz (M): duplicate'e `already recorded` benzeri metin + `ok:false` verir veya aynı body'ye aynı başarılı cevabı döner (idempotent), ve KAYDI İKİNCİ KEZ YAZMAZ. Builder tarafı: normalize (`_`,`-` -> boşluk), listeye `duplicate|exists|idempot|replay|zaten|daha önce` ekle, ve spec'ten `retry_on_uncertain: false`.

### TA-10 (P2, C) Her outcome için zorunlu `reason` olan sözleşmede `promise`/`paid` reason'ı sessizce düşüyor; test bunu "clean" sayıyor

Kanıt: `e15.py` (close_call `required: [..., reason]`): `check` = [], `validate` = 0 hata; hook gövdeleri `promise -> {customer_id, outcome}`, `paid -> {customer_id, outcome}` (reason yok), yalnız `no_promise`/`transferred` reason alıyor
(`generate.py:766-806` `CLOSE_PRE`: `b.get(F_OUT) in NO_REASON` -> `b.pop(F_REASON)`; `NO_REASON` brief tablosundan, sözleşmenin `required`'ından değil, `generate.py:1070-1074`).
`test_fuzz_openapi.py` mutasyon 07 ("close_call reason required, create_promise amount optional") `stale="clean"`: yani "uyarı yok" beklenen sonuç olarak kilitlenmiş.
Etki: mock `reason`'ı zorunlu tutarsa (örn. `enum` içinde `completed`/`none`) her `promise`/`paid` kapanışı reddedilir (`close_call` 4xx -> kayıt yok, outcome boş).
Genel düzeltme: `NO_REASON`'dan çıkarma, `ToolField.required` ise reason'ı `enum` içinden "none|completed|n/a|-" gibi tek uyan değerle (yoksa `extras`) doldur; yoksa `check` sert problem.

### TA-11 (P2, C/M) Auth/header/session parametreleri ve `security` yok sayılıyor

Kanıt: `openapi_tools.py:267-270` yalnız `in: query|path`; `:456-460` header = yalnız `Content-Type`. `e2.py` E denemesi: `securitySchemes.apiKey(header X-Team-Key)` + `security` + her op'ta zorunlu `X-Session-Id` header parametresi -> workflow'da `X-Team-Key`/`X-Session-Id` geçmiyor, `check` = [], `validate` = 0 hata.
Kit gerçeği: yedi araç anahtar istemiyor (`customer_id` sır); `x-team-key` yalnız scorer uçlarında. Mock'umuz anahtar istemezse zarar yok (M). IC-2 aynı noktayı işaretledi; burada kanıt + test boşluğu.
Genel düzeltme: zorunlu header parametresi + `security` -> `freya_tool.headers` leaf'i `default` (call variable ya da `extras`), build'de "headers needed: ..." trace satırı; bulunamazsa sert uyarı.

### TA-12 (P2, C) Alan rolü bağlama: alt-dize geri düşüşü yanlış alana bağlıyor; iç içe/birleşik alanlar sessiz

Kanıt: `generate.py:179-193` `find_field` (tam ad, sonra "içerir"):
- `e11.py`: `create_promise.date` kaldırılıp zorunlu `days_from_today` (integer) eklendi -> rol `date` "day" alt-dizesiyle `days_from_today`'e bağlandı, gövde `"days_from_today": "2026-10-16"`; `check` = [] (susuyor). `date` integer `YYYYMMDD` ise ISO string gönderiliyor.
- `e3.py` 4: iç içe gövde `promise: {product_id, amount, date}` -> `ToolField('promise','object')`, gövde `{"customer_id":"c1","promise":""}`; `check` sert uyarı veriyor ama build yine yayınlıyor (uyarı gerçek, davranış "yayınla").
- `e11.py` 1: callback `date`+`time` yerine tek `scheduled_at` (date-time): rol bulunamadı, `"scheduled_at": ""`; birleştirme (date+time) desteği yok.
- `freya_tool` yaprak tipi: `object/array` -> `"string"` (`openapi_tools.py:420-422`).
Genel düzeltme: alt-dize geri düşüşünde ad/tip uyumu (`date` rolü için tip string/format date, `time` için string/time) ve tip uyuşmazlığı hard problem; `compose` rolü (`scheduled_at = date + "T" + time`); nesne alanı için gövde yeniden kurma hook'u (`pre_code` ile `body = {promise: {...}}`).

### TA-13 (P3) Küçük boşluklar (hepsi sessiz)

- OpenAPI 3.1 `const` okunmuyor (`openapi_tools.py:232-244`, `fixed_value` yalnız `default`/tek-değerli `enum`): `e2.py` D `currency: {const: "GBP"}` required -> `"currency": ""`, `check` uyarır (tek problemli), yine de yayınlanır. Tip dizileri (`["number","null"]`) ve `nullable` doğru okunuyor.
- Sayısal enum (`outcome: 1|2|3`) `str(e)` ile; yaprak tipi `integer` ama değişken string.
- `product_id` integer tipli (`e3.py` 3): yaprak `{"type":"integer","default":"{{rec_product_id}}"}`, motor sayıya çevirir mi bilinmiyor (belgelenmemiş), ledger string/int farkı score'da `str` ile tutuluyor.
- UTF-8 BOM'lu JSON: `build.py:493` `read_text(encoding="utf-8")` -> `JSONDecodeError: Unexpected UTF-8 BOM` (`e2.py` B2); `utf-8-sig` ile 1 satır.
- Aynı path'te `post` + `put`: sonuncusu kazanıyor (`e13.py`).
- `validate.py:167-169` `gated_tools = {get_account, create_promise, add_note}` sabit; kit ve C-H sözleşmelerinin açıklamalarıyla (hepsi: verify, get_account, create_promise, add_note "identity must pass") tutarlı; sözleşmeden türetilmiyor, brief başka bir araç (örn. `schedule_callback`) kapılarsa yeni kural eklenmez.
- Boş/okunamayan ürün listesi ile gerçek "geciken ürün yok" ayrımı yok (`e9`/FACTS_ERROR: ikisi de `no_promise/service_error`).

### TA-14 (P2, test) Test boşlukları

`test_fuzz_openapi.py` 33 mutasyon x (uyarlanmış, bayat) = 66 test + 11 tekil bulgu testi; `test_openapi_tools.py` 2 test. Güçlü yanlar: alan/enum yeniden adlandırma, `$ref/allOf/oneOf` gövde,
query/path, charset/2xx/`default` yanıt, tarih/saat biçimleri, bayat spec'in "sessiz olmaması". Kapsanmayanlar (bu raporun bulguları): yanıt tarafı minor unit; `wrong_answers` sayaç anlamı (autocorrect'in ürettiği);
kilit alanı yokluğu; `operationId`/path adı; Swagger 2 / harici-`~1`-`%20` `$ref`; `servers` yok/göreli/değişkenli/`.invalid` ve mock_url; header/security; nesne-tipli gövde alanı; istek başına üretilen zorunlu alan;
`floor_field` yeniden adlandırma; zorunlu `reason` (mutasyon 07 "clean" ile yanlış güvence veriyor); duplicate yanıt metinleri; sözleşme düzeyi sağlık kapısı. Ayrıca: hiçbir test mutant sözleşmeyi uçtan uca motor simülasyonuna
(`fb/harness/engine_sim.py` + `bank.py`) vermiyor; harness bankası yalnız kit şeklini konuşuyor (`harness/bank.py` `_verify_identity`, `{"ok","verified","wrong_answers_left","all_verified"}`), bu yüzden alan-adı değişiklikleri yalnız statik/hook-exec seviyesinde sınanıyor.
`tests/conftest.py` `builder.llm` modülünü import ediyor (repo'da yok) -> `tests/hack` testleri varsayılan `pytest` çağrısında toplanmıyor (CI'da yeşil görünmeyebilir).
Öneri: `tests/hack/test_contract_variants.py` içinde C-H sözleşmelerini + yukarıdaki mutantları parametrik koş; "ok:true ama 0 alan", "wrong_answers=0 ilk doğru cevap" gibi davranış testleri (TA-2/3/4 için hook-exec).

---

## Tablo: varyasyon | ele alınıyor mu? | kanıt

| Varyasyon | Ele alınıyor mu? | Kanıt |
|---|---|---|
| Alan yeniden adlandırma (customer_id, amount, date, field/answer, outcome/reason, queue/team, product_id) | Evet (spec `tool_fields` + autocorrect) | fuzz 01-04, 12, 21, 22; `e5` (E: session_ref, asset_ref, amount_minor, due_on, challenge/response, disposition/detail) -> doğru gövde |
| Zorunlu/opsiyonel değişimi, ekstra zorunlu enum/default/tek-değerli enum | Evet | fuzz 07, 16-18, 28-29; `e10` constant `extras` |
| Ekstra zorunlu serbest alan (channel, idempotency_key, timestamp) | Kısmen (yalnız sabit `extras`; üretilen değer yok) | TA-7, `e10` |
| Enum değerleri farklı (prefix, büyük harf, yeni/eksik) | Evet (`conform`/`match_enum`) | fuzz 05, 06, 13, 32; E/F `E_`/`F_` önekleri `e5`/`e12` |
| Outcome için reason zorunlu | Hayır (sessiz düşer, test "clean") | TA-10 |
| Yanıt zarfı `{data:{...}}`, `payload.*`, iç içe verify/products | Evet | fuzz 08, 09; `e5` `data.payload.assets` |
| Ürün alanları (past_due/days_late/product_id/type) yeniden adlandırma | Evet | fuzz 12, 19, 20 |
| `floor_field` (minimum_payment/instalment_amount) yeniden adlandırma | Hayır (autocorrect yok, floor=full) | TA-8, `e4` |
| `ok` yok / `success` / `status=APPLIED + payload.accepted` | Evet | fuzz 10, 11; `success_field` |
| `wrong_answers` sayaç vs `wrong_answers_left` | Hayır (TERS okunuyor) | TA-2, `e12`, `e6` |
| Kalan-hak/kilit alanı hiç yok | Hayır (döngü) | TA-3, `e7` |
| Tutar yanıtta minor unit (cents) | Hayır | TA-4, `e5` (42050.00) |
| Tutar istekte integer cents, açıklamada "cents" var | Evet | `e3` 1b -> 42050 |
| Tutar istekte cents, açıklama yok / "smallest unit" / "x100" | Hayır | TA-4, `e3` 1 |
| Tutar string decimal | Hayır (float gider) | TA-4/13, `e3` 2 |
| Tarih `DD/MM/YYYY`, `date-time`, `HH:MM:SS` | Evet | fuzz 31; `e11` |
| Tarih integer YYYYMMDD / göreli gün / tek `scheduled_at` | Hayır | TA-12, `e11` |
| camelCase/snake alan adları | Kısmen (stale spec: ürün/yanıt autocorrect OK; floor_field kalıyor) | `e3` 6 |
| Araç adı = operationId farklı (camelCase, önek), path-türevli ad | Hayır (KeyError, hiçbir şey yayınlanmaz) | TA-5, `e2` A/A2/A3 |
| `servers` yok / göreli / değişkenli / `.invalid` / çoklu | Hayır (sessiz yanlış URL) | TA-1, `e1`, `e5`, `e13` |
| mock_url argümanı | Hayır (kodda yok) | TA-1, IC-2 |
| validator mock host'unu kabul eder mi | Hayır (`foreign_host`) | TA-1, `e1b` |
| Auth header / `security` / `X-Session-Id` | Hayır (sessiz) | TA-11, `e2` E |
| `customer_id` path/query parametresi | Evet (request_hook URL'ye taşır) | fuzz 27, 33 |
| allOf / oneOf / anyOf / `$ref` (yerel) / charset / 2xx / `default` | Evet | fuzz 14, 23-26 |
| 3.1 tip dizisi / `nullable` | Evet | `e2` D (amount) |
| 3.1 `const` | Hayır | TA-13, `e2` D |
| harici `$ref` / `~1` / `%20` / döngü | Hayır (0 alan, sessiz) | TA-6, `e13` |
| Swagger 2.0 | Hayır (0 alan, `validate` 0 hata) | TA-6, `e2b` |
| YAML / md-docx tablo / Postman / düz metin sözleşme | Hayır (`json.loads` çöker) | IC-4, `e2` B |
| UTF-8 BOM | Hayır | TA-13, `e2` B2 |
| İç içe istek gövdesi (`promise:{...}`) | Hayır (`check` sert uyarı, yine yayınlanır, `""`) | TA-12, `e3` 4 |
| 5xx / HTML 502 / non-JSON (verify) | Evet (id_down, 1 yeniden deneme, `fin_id_error`) | `e7` (500/503/502 -> route `fin_id_error`) |
| 5xx / ok:false (create_promise, callback, transfer) | Evet (HELD_POST, 1 yeniden deneme, dürüst mesaj) | `generate.py:726-757`, `record_result` |
| duplicate/"already recorded" metni farklı | Kısmen (yalnız İngilizce "already ..." + 409) | TA-9, `e8` |
| Belirsiz commit (timeout) vs kesin no-commit ayrımı | Hayır | TA-9 |
| `get_account` ok:false / boş / dict-of-dicts ürün | Kısmen (hepsi service_error kapanış) | TA-8/13, `e9` |
| Gated araçlar | Sabit küme (kit/C-H ile tutarlı) | TA-13, `validate.py:167` |

---

## Önerilen genel çözümler (özet, önem sırasıyla)

1. `parse_openapi(spec, base_url=mock_url)`; absolute-URL kapısı; validator ve export check aynı base ile (TA-1).
2. Kimlik: `wrong_answers` anlam bayrağı + `max_wrong` + kendi sayacı + kilit metni (TA-2, TA-3). 8-10 satırlık hook değişikliği, spec şemasına iki alan.
3. Para ölçeği: `money_scale` hem yanıt (`find_products`) hem istek (`fmt_value`); ad/açıklama tabanlı tespit (TA-4).
4. Araç çözümü: normalize + STEMS (PoC `e14.py`), `ToolDef.op_id`; ayrıştırma sağlık kapısı ve `warnings` (TA-5, TA-6).
5. `extras` için `$gen` değerleri (key/now), `floor_field` ve `NO_REASON` sözleşmeye duyarlı (TA-7, TA-8, TA-10).
6. Mutant sözleşme testleri + (mümkünse) harness bankasını sözleşme şekline parametrize etmek (TA-14).
