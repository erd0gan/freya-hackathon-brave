# 03 — Gizli final için "Brief A/B'ye gömülü varsayımlar" denetimi

Tarih: 11 Ekim 2026. Kapsam: `builder/fb/generate.py`, `builder/fb/nodecode/{negotiation_lib,collections_lib,speech_lib,bodies}.py`, `builder/fb/validate.py`, `builder/fb/spec_extract.py` (`to_generator`, `check`, `structure`, `autocorrect`), `builder/fb/openapi_tools.py`, `builder/build.py`.

Yöntem: kodu okudum; sonra `fb.generate.build` / `to_generator` / `NS = namespace()` ile elle düzenlenmiş spec ve sözleşmelerle **çevrimdışı** çalıştırdım. Freya, OpenRouter ve ağ çağrısı yok; repo dosyalarına dokunulmadı. Tüm tekrarlar `out/audit-scratch/ha/` altında (`h.py` ortak yardımcı, `p1.py` ... `p20.py`); `cd out/audit-scratch/ha && python pN.py` ile çalışır. Kaynak sözleşmeler: `kit/tools/openapi.json`, `docs/stress-scenarios/contracts/openapi-{c,e,f}.json`.

Önemli bağlam: resmi sayfa (snapshot) "final = tek vaka, Freya Bank kredi kartı ekstre borcu hattı, **Türkçe**" diyor; pratik çağrılar İngilizce. Kit README: "diğer sayılar ve limitler, diğer kimlik soruları, başka sonuç tablosu, başka takvim; araç adları aynı kalır, **alanlar ve izinli değerler değişebilir**; en fazla üç ürün".

## Genel sonuç

Spec'e bağlanmış kısım iyi: kimlik soru sayısı/sırası/türü, band sayısı, iş günü/tatil, deadline birimi, attempt1 kuralı, callback penceresi, kuyruk/outcome/reason adları (`conform`), `notes` açık/kapalı, `human_requests_to_transfer`, `promise_attempts`, `floor_offers`, cümleler (`lines`) gerçekten spec'ten geliyor ve ben bunları değiştirerek çalıştırdım. Sorun üç yerde toplanıyor:

1. **Spec'in ifade edemediği şeyler** (kimlik kilidi anlamı, `type` alanı olmayan tek ürünlü API, `days_late` olmayan API, yüzde/yok/0 taban, kuruş birimli yanıt, tarihe bağlı deadline, ürün bazlı attempt planı, dördüncü kuyruk). Bu durumda üretici sessizce Brief A davranışına ya da boş/yanlış değere düşüyor.
2. **Deterministik düzeltici (`autocorrect`) yanlış anlamı kendisi bağlıyor** (`wrong_answers` sayacı → `wrong_answers_left`).
3. **İngilizce her yerde sabit** (sistem prompt'u, motor cümleleri, tarih/tutar/DOB ayrıştırıcıları, ret/durdurma regex'leri). Final Türkçe ise bu tek başına en büyük risk.

## Tablo: varsayım | kodda yeri | spec'ten mi? | karar

| Varsayım | Kodda yeri | Spec'ten mi? | Karar |
|---|---|---|---|
| Kimlik = tam iki soru (DOB + son4) | `generate.py:1320-1343` (`questions` listesi), `identity_hooks` | Evet (N soru, sıra, `kind`) | OK. 5 soru + `postcode/year/digits/date` kind'ları denendi (`p5.py`) |
| Yanlış cevap limiti 3 | `generate.py:1361-1363` (`locked`: `wrong_answers_left == "0"`), hook `generate.py:653` | **Hayır**: limit spec'te yok, API alanına bağlı | **HA-1 (P0/P1)** |
| `verify_identity` yanıt alanları (`ok/verified/all_verified/wrong_answers_left/hint`) | `generate.py:594-596`, `spec_extract.py:438-442` | Kısmen (`tool_fields.*.response` adı), anlamı sabit | HA-1 |
| swap-hint | `generate.py:507-508`, hook | Evet; boşsa sabit `day_month_swapped` | P3, zararsız (`p20.py` B) |
| Band sayısı / sınırlar | `collections_lib.py:124-140`, `spec_extract.py:984-1000` | Evet ama **yalnız `days_late` ile**, int zorunlu | OK sayı için; HA-5 diğer ölçüt için |
| Band `days_late` alanı zorunlu | `collections_lib.py:128` (`int(float(days_late))` hata → `None`) | Hayır | **HA-5 (P1)** |
| Deadline = N. iş günü / takvim günü | `collections_lib.py:88-98` | Evet (`deadline_unit`) | OK; tarihe/ay sonuna bağlı deadline yok (HA-9) |
| Attempt sayısı / band | `negotiation_lib.py:129-132`, `_flat_refusal` | Evet | OK; `0` çok ürünlü çağrıda yok sayılıyor (HA-8) |
| Attempt planı (tutar x tarih sözlüğü) | `negotiation_lib.py:343-355`, `spec_extract.py:1103-1109` | Evet ama tek global plan, 3x2 kelime | P2 (HA-9) |
| "Haftanın son iş günü" attempt1 | `collections_lib.py:101-121` | Kural seçimi spec'te; hafta Pazartesi başlar sabit | P3 (HA-15) |
| Haftasonu = Cmt/Paz | `collections_lib.py:40-42`, `spec_extract.py:1424` | `workdays`; **yoksa** sessizce Pzt-Cum | OK / varsayılan HA-13 |
| Tatil listesi | `collections_lib.py:42` | Evet; yoksa `[]` | OK |
| Callback penceresi 10-19, bugün/sonraki iş günü | `collections_lib.py:167-187`, `bodies.py:256-332`, `spec_extract.py:1427-1437` | Evet (`start/end/include_today/business_days_ahead`); yoksa 10:00-19:00 | OK; boşsa HA-13 |
| Çıplak saat çözümü ("eight" = 08:00, 1-7 = öğleden sonra) | `speech_lib.py:601` (`day_start/day_end` **kullanılmıyor**), `:656` | **Hayır** | P3 (HA-15) |
| Taban = `minimum_payment` / `instalment_amount` | `collections_lib.py:281`, `negotiation_lib.py:125` | Tip başına alan adı; yüzde/sabit/yok yok | **HA-6 (P1)** |
| Ürün `type` alanı var | `collections_lib.py:280-287` | Hayır | **HA-4 (P1)** |
| Tutarlar ondalık majör birim | `collections_lib.py:256-260,298`, istek tarafı `openapi_tools.py:295-345` | İstek: açıklamadan algılanır; **yanıt: hayır** | **HA-7 (P1)** |
| Ürün adı "credit card ending 4821" | `product_names` şablonu | Evet; `format` hatasında sessizce `type`'a düşer | OK / HA-4 |
| Aynı anda sunulan olgular: ad, `past due`, `days late` | `negotiation_lib.py:202-225` | **Hayır** (taban, son ödeme tarihi, toplam yok) | HA-10 (P2) |
| İlk soru = "tam borcu bugün ödeyebilir misiniz" | `negotiation_lib.py:1271-1288` | Metin spec'te, **kural sabit** (evet = tam tutar, bugün) | HA-10 |
| `restructure` her zaman transfer | `negotiation_lib.py:973-975` | **Hayır** | HA-10 (P2) |
| 3 anlamsal kuyruk, `hardship_queue` ⊂ {restructuring,human,support} | `spec_extract.py:965-972,1045-1052` | Eşleme spec'te; küme sabit | HA-10 |
| 7 ön-müzakere bitişi | `generate.py:1733-1778`, `spec_extract.py:1062-1075` | Değerler spec'te; küme sabit | P3 |
| 14 `reply_kind` | `generate.py:1175-1195` | Hayır | P3 (yeni niyet = `unclear`) |
| Ret: "2 ret / ne hakkında = ret", 2 kez kim konuşuyor | `generate.py:1148-1156,1332-1336`, `1287-1312` | **Hayır** (A kural 3, 15 gömülü) | HA-11 (P2) |
| "indirim/taksit/bağlayıcı vaat yok", "nasıl ödersiniz diye sorma" | `generate.py:1537-1547`, `negotiation_lib.py:1005-1037,1466-1470` | **Hayır** | HA-10 |
| Kapanışta `add_note` | `neg.notes` bool | Evet (global) | OK; ürün/neden bazlı yok |
| Hang-up sonucu | `generate.py:766-806,1077-1098` | `no_promise/refused` sabit, `interrupted` yok | HA-14 (P3) |
| Mandatory cümleler (açılış, scam, taziye, text-message, "paid") | `lines.*` → `neg.lines` | Evet (`ENGINE_LINES` nötr) | OK |
| Sistem dili / motor cümleleri | `generate.py:1230`, `spec_extract.py:252`, `negotiation_lib.py` (~60 cümle) | **Hayır** | **HA-2 (P0)** |
| Araç adı çözümü | `openapi_tools.py:260`, `generate.py:948,999,1008,1043,1054,1067` | Hayır (`operationId` ya da yol son parçası = kit adı) | **HA-3 (P1)** |
| Araç taban URL'si | `openapi_tools.py:248-249,282`; CLI `build.py:438-443` | Sözleşmenin `servers[0]`'ı; CLI `<brief> <openapi>` | HA-3 |
| `schedule_callback` = `date` + `time` ayrı | `generate.py:1045` | Hayır | HA-12 (P2) |
| En fazla 3 ürün | `generate.py:1221-1227` (`p1..p3`) | Sabit enum | P3 |
| Spec eksikse varsayılanlar | `negotiation_lib.py:23-48`, `spec_extract.py:1370-1437` | A değerleri sessiz | HA-13 (P3, `check` raporluyor) |

## Bulgular

### HA-1 (P0 sayaç biçimli sözleşmede, P1 diğerlerinde): Kimlik kilidi API'nin `wrong_answers_left` alanına bağlı; sayaç alanı otomatik olarak "kalan" diye bağlanıyor

Kanıt:

* Limit spec'te yok. `generate.py:1361-1363`: `locked = eq("verify_identity_response.data.<wrong_answers_left>", "0")`; hook `generate.py:653`: `elif _left == '0': "The identity check has failed..."`.
* `spec_extract.autocorrect` (`:836-848`, `_guess` difflib 0.75), spec'te rolün adı yoksa en yakın alanı bağlıyor. C/F/E sözleşmeleri `wrong_answers` (şimdiye kadar yanlış sayısı, 0 başlar) döndürüyor, "kalan" değil.

Tekrar (`p13.py`, `p10.py`):

```
autocorrect: tool_fields.verify_identity.response.wrong_answers_left: 'wrong_answers_left' -> 'wrong_answers'
correct 1st answer (wrong_answers=0)        -> The identity check has failed. Do not ask anything else. I could not verify your identity.
wrong 1st (wrong_answers=1)                 -> That answer is not correct. Ask the same question again.
wrong 2nd = LIMIT 2 (wrong_answers=2)       -> That answer is not correct. Ask the same question again.
identity transitions: ('fin_failed', variable verify_identity_response.data.wrong_answers == "0")
```

Yani sözleşme sayaç döndürürse: **ilk doğru cevap** hem modele "kimlik başarısız" dedirtir hem `fin_failed`'e yönlendirir (her meşru müşteri `not_verified/failed`); limit dolunca da hiçbir şey olmaz. `check()` geçiyor (alan mevcut).
API `locked: true`, `attempts_used`, `status: "LOCKED"` veya hiç alan vermezse (`p6.py`): `check` yalnızca "wrong_answers_left alanı yok" der (derlemeyi durdurmaz), hook "Ask the same question again" der, 3 yanlıştan sonra bile döngü sürer; 60 tur (`ID_MAX_TURNS`) sonra `fin_declined` (`not_verified/declined`, `failed` değil).

Tetikleyen değişiklik: "3 hatalı cevap" limitinin brief'te olup yanıtta sayaç/bool/hiçbir şeyin olması. Kit ("fields can change") bunu açıkça mümkün kılıyor; sentetik C/E/F sözleşmeleri tam bu biçimde.

Genel düzeltme: spec'e `identity.wrong_limit` (brief'ten) ekle; hook'ta `vars['id_wrong']` say (`ok` doğru, `verified` yanlış, swap-hint/`incomplete` değil); kilit = sayaç >= limit **veya** (anlamı "kalan" olduğu açıksa) alan == 0. Yanıt-rol sözlüğüne anlam ekle: `wrong_answers_left | wrong_answers_used | locked`; `autocorrect` `wrong_answers*` alanını asla "left" rolüne bağlamasın.

### HA-2 (P0 final Türkçe konuşulacaksa; diğer denetçilerle örtüşebilir): Dil İngilizceye sabit

Kanıt:

* `generate.py:1230` `Speak English.`; `spec_extract.py:252` "short, plain English" (spec `lines` bile İngilizce istenir).
* Motor cümleleri sabit ve TTS'e "speak" modunda aynen gidiyor: "Could you pay X for the Y on D?", "The latest date I can record is...", "To confirm: ...", "I have noted...", "Your promise is recorded: ...", "Sorry, I did not catch that." vb. `ENGINE_LINES`'ın 24 anahtarı spec'ten gelir, ama `negotiation_lib.py`'deki ~30 cümle (`"I understand."`, `"On which exact day can you pay?"`, `"The date has to be today or later..."`, `"I cannot record X. The minimum is Y."`) ve `say_date` (`collections_lib.py:36-37`, İngilizce gün/ay adları) sabit.
* Ayrıştırıcılar yalnız İngilizce (`p11.py`):

```
parse_spoken_date('16 Ekim')            -> ('', 'none')     'cuma günü','yarın','30 Ekim 2026' -> ('', 'none')
read_reply {kind: offer, date: '16 Ekim cuma'} -> kind 'vague'  (her Türkçe tarih "belirsiz": önce 1 kez sorar, sonra deadline önerir)
parse_amount('bin iki yüz elli lira')   -> None             ('iki yüz' -> None)
normalize_dob('14 Mart 1988')           -> '14 mart 1988'   (ISO'ya çevrilmiyor; banka reddeder = yanlış cevap sayılır)
digits_from_speech('dört dört yedi bir')-> ''               (kimlik cevabı ham kelime gider)
```

* Ret/durdurma regex'leri (`STOP_WORDS`, `REFUSE_ANSWER`, `ABOUT_WORDS`, `REFUSAL_WORDS`, `bodies.decline_check`): Türkçe ret gerçek kabul edilmiyor (`p17.py`):

```
'Bu bilgiyi vermek istemiyorum.' -> decline_real 'no' -> id1 'unclear'   (kimlik sorusu sonsuz döner)
'Seninle konuşmak istemiyorum.'  -> 'no'
'I won't give you that.'         -> 'yes'
```

Tetikleyen: final müşterinin Türkçe konuşması. Düzeltme: dil brief'ten (`spec.language`); motor cümleleri tablosu spec'ten (tüm anahtarlar, sayı/tarih biçimleri için şablon); tarih/tutar/DOB/rakam normalizasyonunu modele (ISO / ondalık üretsin, kod yalnız doğrulasın); regex yerine model etiketi + kısa dil-bağımsız kontrol.

### HA-3 (P1): Araç adı ve taban URL çözümü sabit; CLI final biçimiyle uyuşmuyor

Kanıt:

* `openapi_tools.py:260`: `name = op.get("operationId") or path.strip("/").split("/")[-1]`; `generate.py` `tools["verify_identity"]`, `["get_account"]`, `["create_promise"]`, `["schedule_callback"]`, `["transfer_to_agent"]`, `["close_call"]` doğrudan indeks. `p15.py`/inline: `operationId: verifyIdentity` ya da yol `/api/v2/tools/verify-identity` → `KeyError: 'verify_identity'`, derleme çöker. (`add_note` `.get`; `schedule_callback` yoksa yine `KeyError`.)
* URL = `servers[0].url + path` (`openapi_tools.py:248-282`). Final sözleşmesinin `servers` değeri yer tutucu olabilir; mock URL'si hiçbir yerden gelmiyor.
* `build.py:438-443` `argv = <brief_path> <tool_api_path>`; sayfadaki final komutu `build.py FreyaBank-Ekstre-Borc/ https://mock...` (klasör + URL). `Path(dir).read_text()`/`sha256(dir)` (try dışında, `main` içinde) çöker. Bu kod okumasıdır, çalıştırmadım (`traces/` yazar).

Düzeltme: `norm()` ile ad eşleme (operationId, yol, summary, `x-*`); URL için `--base-url`/ikinci argüman; klasör girdisi (BRIEF.md + sözleşme dosyası bulma). 3 saatlik final penceresinde "crash fix" sayılır ama bugün hazır olmalı.

### HA-4 (P1): Ürün `type` alanı olmayan (tek ürünlü) API'de ad boş, taban yok

`collections_lib.py:280-287`: `ptype = pf(p,'type')`, `floor_field.get(ptype)`, `product_names.get(ptype) or ptype.replace("_"," ")`. Tek ürünlü kredi kartı hattı `type` döndürmeyebilir.

Tekrar (`p18.py`, `type` yok, `minimum_payment: 125`):

```
name: ''   floor: ''
I am calling from the collections team about a late payment. Your  has 1250.00 past due and is 5 days late. Can you make this payment today?
(too_much) -> Could you pay 1250.00 for the  on Friday 16 October 2026?
```

`check()` yalnızca "product_fields.type alanı yok" der, onarılamaz, yine derlenir. Düzeltme: `floor_field` / `product_names` için `"*"` (varsayılan) anahtarı; tek giriş varsa onu kullan; `type` yokken `display_name`/`product_names["*"]`.

### HA-5 (P1): Band yalnız `days_late` ile; alan yoksa ürün düşer, çağrı `service_error` ile kapanır

`collections_lib.py:124-140`: `int(float(days_late))` hata verince bantlar bakılmadan `None`; `new_state` (`negotiation_lib.py:138`) deadline'sız ürünü atıyor; ürün kalmazsa `no_promise/service_error` ve hiç müzakere yok.

Tekrar (`p1.py`): tek evrensel band (`0..9999`) + üründe `days_late` yok (ya da `None`):

```
check(): []     (sorun raporlanmıyor)
p1_deadline '' p1_attempts ''  ->  state p: []  action finish  no_promise service_error
```

Ayrıca `structure()` (`:984-1000`) ve `blocking()` (`:940-957`) min/max'i **int ve >= 0** ister; açık uçlu band ("46+"), tutara/ekstre yaşına göre band, tek kuralı olan brief ifade edilemez; hiç int band yoksa **hiçbir şey yayınlanmaz** (`build.py` `spec invalid ... nothing published`).

Tetikleyen: final brief'in gün bandı olmaması ya da API'nin gecikme günü alanı taşımaması (ekstre borcu için "son ödeme tarihi" + gün hesabı olabilir). Düzeltme: tek band varsa `days_late` olmadan da kullan; `None` sınırlara izin ver; alan eksikse son çare olarak ilk band; hiç band yoksa varsayılan kural bloğu.

### HA-6 (P1): Taban semantiği tek biçim: "alan değeri"; yok/0/yüzde ifade edilemiyor

`negotiation_lib.py:125`: `floor = to_float(pf[_floor] or 0) or full`. `floor_field` yalnız yanıt alanı adı.

Tekrar (`p2.py`, `p14.py`):

```
floor yok (floor_field "")  : offer 500 -> "I cannot record 500.00. The minimum is 1250.00. Could you pay 1250.00 ...? The rest stays overdue."
floor 0 (minimum_payment: 0): offer 50  -> "I cannot record 50.00. The minimum is 300.00..."
floor None                  : floor = full
```

(Taban = tam tutar olduğunda "The rest stays overdue." anlamsız.) Brief "taban yok, her tutar olur", "asgari = borcun %X'i" ya da "asgari 0 olabilir" derse motor kısmi ödemeyi reddeder. Düzeltme: `floor: {field | pct | const | none}`; `0` geçerli taban; `none` = herhangi bir pozitif tutar; `floor == full` iken taban cümlesini kullanma.

### HA-7 (P1): Yanıt tarafında kuruş (minor unit) ölçeği yok

İstek tarafında `wire_format` "cents" algılıyor (`openapi_tools.py:295,326,341`), yanıt tarafında yok: `money()` `"%.2f" % to_float(x)`.

Tekrar (`p4.py`, E sözleşmesi biçimi: `arrears_minor: 42050`, `floor_minor: 8410`):

```
Your credit card ending 4821 has 42050.00 past due ...   (420.50 olmalı)
(offer 200) -> I cannot record 200.00. The minimum is 8410.00. ...
```

Müşteriye 100 kat büyük tutar söylenir, makul teklifler reddedilir. Düzeltme: `product_fields` için ölçek (açıklamadan "integer cents" algıla), okurken böl, yazarken çarp (şimdiki istek kancasıyla tutarlı).

### HA-8 (P2): `attempts: 0` band, çok ürünlü çağrıda ikinci üründe yok sayılıyor

`negotiation_lib.py:622-624` (`_advance`): `if p["used"] == 0: p["used"] = 1` sonra `_attempt_offer`; band 0 izin veriyor olsa da teklif yapılır.

Tekrar (`p8.py`, p1 early 1 attempt, p2 late 0 attempt): "no" sonrası "Let us go through them one at a time. Could you pay 600.00 for the personal loan..." ardından 2. "no" -> "Could you pay 900.00 for the car loan on Friday 16 October 2026?" (0 attempt'lı ürüne teklif). Tek üründe doğru (`finish`). Düzeltme: `_advance`'te `attempts == 0` ise ürünü `refused` işaretle.

### HA-9 (P2): Deadline ve attempt planı sözlüğü dar

* Deadline yalnız "bugünden N iş günü" ya da "N takvim günü" (`collections_lib.py:88-98`). "Son ödeme tarihi + N gün", "ay sonu", "bir sonraki ekstre kesimi", API'deki bir tarih alanı yok. (Kredi kartı ekstre borcu için doğal.)
* Attempt planı tek ve tüm bandlar için ortak (`negotiation_lib.py:343-355`); sözlük: tutar {current, full, floor} x tarih {attempt1, deadline}. Bandlara göre farklı plan, "tutarın %50'si", "attempt1 = deadline'dan N gün önce" yok.
* `attempt1_date` tek global (`collections_lib.py:101`).

### HA-10 (P2): Politika motorunda sabit davranışlar

* `present_text` (`negotiation_lib.py:202-225`) yalnız ad + `past due` + `days late`. Brief taban, son ödeme tarihi, ekstre toplamını söylemeyi isterse eklenemez.
* İlk soru: "evet" daima tam tutar + bugün (`negotiation_lib.py:1271-1288`).
* `restructure` daima transfer (`:973-975`); kuyruk kümesi `restructuring/human/support` (+ `hardship_queue` aynı kümede, `structure()` başka değeri hata sayar; `p20.py` N). Dispute için ayrı kuyruk, "kart kayıp" niyeti vs. yok.
* "İndirim, taksit, bağlayıcı vaat yok" ve "nasıl ödersiniz diye sorma" modele sabit yazılı (`generate.py:1537-1547`, `negotiation_lib.py:1005-1037,1466-1470`). Taksit/erteleme sunmaya izin veren bir brief (Türk kartlarında yaygın) bunu yapamaz.
* `_finish` önceliği: transfer her zaman söz'ü yener (`negotiation_lib.py:659-668`).

### HA-11 (P2): Açılış/kimlik protokolü Brief A kural 3, 15'e gömülü

`generate.py:1148-1156` (`id_desc`: "ne hakkında" = ret), `:1332-1336` (bir kez açıkla, ikincide kapat), `:1287-1312` (kimlik teyidi tam iki kez, `opening` + `opening2`), `:1129-1147` (`who_desc`: "go ahead" teyit değil). Sayılar spec'te yok; farklı bir brief ("ne hakkında" sorusu ret değil; üç kez sor) bunları değiştiremez. Hepsi İngilizce metin/regex (HA-2 ile birleşik).

### HA-12 (P2): Tek `datetime` alanlı `schedule_callback` (ve benzeri istek şekilleri) bağlanmıyor

`generate.py:1045` yalnız `date` + `time` rolleri. `callback_at: date-time` tek alanı (`p15.py`) bağlanmaz; gövde `"callback_at": "{{callback_at}}"` boş kalır, `check()` "role(s) ['date','time'] found no field" der (sert hata, onarılamaz), arama yine derlenir ve randevu çağrısı başarısız olur. `wire_format` içinde `datetime` dönüşümü var ama rol yok.

### HA-13 (P3): Spec eksik bırakırsa sessizce Brief A değerleri

`p12.py` (spec'ten `callback`, `attempt1`, `calendar`, `attempt_plan` ve politika düğmeleri çıkarıldı): `callback 10:00-19:00, include_today true, business_days_ahead 1`, `attempt1 last_business_day_of_week`, `workdays [0..4]`, `holidays []`, `human_requests_to_transfer 2`, `stop 2`, `promise_attempts 2`, `hardship offer_support`, `no_promise_reason first_product`, `notes_on_transfer False`, `vague_date_asks 1`, `floor_offers 1`, `callback_reasks 1`, `help_first` metni. `check()` politika eksiklerini raporluyor ve onarım turu istiyor, ama onarım de eksik bırakırsa `build.py` "building anyway"; `blocking()` yalnız soru/band yoksa durur. `attempt_plan` yoksa `steps = ... or 2` (`spec_extract.py:1404`) band attempt'lerini 2'ye kırpar.

### HA-14 (P3): Hang-up kapanışı sabit

`generate.py:766-806`, `1077-1098`: müşteri ilk soruda bile kapatsa `no_promise/refused` (`p20.py` A). Brief "kesinti" reason'ı isterse (C/F briefleri: `INTERRUPTED`) kullanılamaz.

### HA-15 (P3): Takvim ve saat ayrıntıları

* Hafta Pazartesi başlar sabit (`collections_lib.py:115,189`). Pazar-Perşembe çalışma haftasında (`p3.py`, `workdays [6,0,1,2,3]`, Çarşamba 14 Ekim): `attempt1_date = 2026-10-18` (Pazar, bir sonraki çalışma haftasının ilk günü); doğrusu Perşembe 15 Ekim. Pazar çalışılan günse, Pazar günü attempt1 bir sonraki Pazar'a gider.
* `parse_spoken_time(text, day_start=10, day_end=19)`: parametreler kullanılmıyor (`speech_lib.py:601`), kural `1 <= hour <= 7 => öğleden sonra` (`:656`). Pencere 13:00-21:00 ise "eight" 08:00 çıkar (`p7.py`).
* `normalize_dob`: 2 haneli yıl 30-99 = 19xx, 00-08 = 20xx, aradaki "incomplete".

## Düzgün çalışan (spec'e gerçekten bağlı) olarak doğruladıklarım

3 band + 3 attempt'li plan (`p16.py`: offer 1250 -> 125 -> 125@deadline -> `no_promise/refused`); `deadline_unit: calendar`; Pazar-Perşembe iş haftasında iş günü/hafta sonu etiketleri ve callback günleri; `business_days_ahead`/`include_today`; 5 sorulu kimlik (`postcode`, `year`, `digits`, `date`, `text`: `p5.py` içinde "nineteen eighty eight" -> `1988`, "oh oh four two" -> `0042`); yeniden adlandırılmış çağrı değişkenleri ve başlıksız hitap (validator yalnız uyarı verir); `D/F/E` önek kodları (`conform`); `notes` kapalı/açık. Ürün sayısı spec'ten değil ama 3'e kadar sorunsuz.

## Sıralı ilk 5

1. **HA-1**: kimlik kilidi/yanlış cevap sayacı. Sayaç biçimli sözleşmede meşru her müşteriyi `failed` yapar, `autocorrect` bunu kendisi bağlar; diğer biçimlerde limit hiç uygulanmaz. Sentetik C/E/F tam bu biçimde.
2. **HA-2**: dil İngilizceye sabit (sistem prompt'u, ~60 motor cümlesi, tarih/tutar/DOB/rakam ayrıştırıcıları, ret regex'leri). Final Türkçe ise Türkçe tarih = "belirsiz", Türkçe ret = tanınmaz.
3. **HA-3**: araç adı `KeyError`, URL `servers[0]`, CLI `<klasör> <mock_url>` ile uyumsuz; bugün çözülmezse build hiç ürün üretmez.
4. **HA-4 + HA-5 + HA-6** (tek ürünlü kredi kartı API'si): `type` yoksa ad boş/taban yok; `days_late` yoksa her çağrı `service_error`; taban 0/yok = tam tutar.
5. **HA-7**: yanıtta kuruş birimi (100 kat yanlış tutar söylenir), ve **HA-9/HA-10** (tarih alanına bağlı deadline, taksit/erteleme yasağı, sabit ilk soru) ifade edilemeyen kurallar.
