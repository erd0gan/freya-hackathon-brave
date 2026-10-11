# 04 - Spec extraction ve grounding denetimi (BRAVE, 11 Ekim 2026)

Kapsam: `builder/fb/spec_extract.py` (extract, check, structure, grounding, repair, override, observer), `builder/fb/llm.py` (model cagrisi, parse), `builder/build.py` (spec'in build'e akisi), `builder/fb/proof.py`. Ag kullanilmadi, repo dosyasi degistirilmedi, commit yok. Tum kanitlar `out/audit-scratch/` altindaki betiklerle offline yeniden uretilir (komutlar her bulgunun altinda). Betikler `python out/audit-scratch/<ad>.py` ile calisir (Windows, Python 3.12, cp1254 konsol).

Ground truth olarak okunanlar: `kit/README.md` (Final bolumu), `docs/private-audit/hackathon-page-2026-10-11.txt`, `kit/tools/openapi.json`, `docs/stress-scenarios/briefs/brief-d.md` (e-posta + politika + memo + kod listesi), `contracts/openapi-d.json`, SCENARIOS D-01..D-08.

Offline calistirilan mevcut testler: `pytest tests/hack/test_spec_checks.py test_spec_mapping.py test_llm_providers.py test_northwind.py --confcutdir=tests/hack` -> 59 gecti (not: kokteki `tests/conftest.py` eski `builder.llm` icin bozuk, `--confcutdir=tests/hack` olmadan toplama hatasi veriyor).

## 0. Ozet

Public A/B uzerinde spec cikarimi saglam gorunuyor, ama bu kanitin degeri dusuk: A+B briefinde her `1..30` tamsayisi "grounded" sayiliyor (SX-3), 9 kayitli A ve 7 kayitli AB cikarimi policy alanlarinda neredeyse ozdes (yalniz `forbidden_before_verify` oynuyor), ve repair turu kayitli hicbir kosuda hic tetiklenmemis. Yani grounding/repair/validator hattinin ozel finaldeki davranisi **hic olculmemis**. Ozel finalin ozellikleri (Turkce, klasor halinde md/docx/pdf/xlsx, duyuru/memo ile degisen kurallar, kod listesi, ornek konusmalar) bu hattin zayif yerlerine tam denk geliyor:

1. Girdi: builder tek bir UTF-8 metin dosyasi + `openapi.json` bekliyor; final bir klasor (SX-1).
2. Dil: extraction promptu ve motor satirlari Ingilizce; final Turkce (SX-2).
3. Grounding "bu deger brief'te bir yerde geciyor mu" diye bakiyor; eski/taslak/gecersiz deger de brief'te gectigi icin dogru deger ile ayirt edilemiyor (SX-3). Turkce yazilmis dogru degerleri ise yanlis reddediyor (SX-4).
4. Spec semasi sabit (Brief A seklinde); sema'ya sigmayan kural sessizce `notes`'ta kaliyor ve hicbir yere gitmiyor (SX-7).
5. Bircok hata yolu sessizce yanlis ama yayinlanmis bir agent uretiyor (`ok_spec_problems`), bircok baska yol ise hic agent uretmeden coker (SX-5, SX-6).

## 1. Bulgular

Siddet: P0 = final skoru dogrudan sifirlar/cok buyuk kayip; P1 = buyuk olasilikla bir veya birkac vaka sinifini bozar veya build'i durdurur; P2 = kosullu; P3 = kucuk/not.

---

### SX-1 (P0, kapsam disi ama belirleyici) Girdi tek dosya + openapi.json; final bir klasor

- Kanit: `builder/build.py:442` `brief_path, api_path = Path(argv[1]), Path(argv[2])`; `build.py:492` `brief_path.read_text(encoding="utf-8")`; `build.py:493` `json.loads(api_path.read_text())`. Kodda `docx`, `xlsx`, `pdf`, `is_dir`, `iterdir`, `zipfile` icin sifir eslesme (`grep -rn` builder/ ve scripts/).
- Sayfa: `python3 builder/build.py FreyaBank-Ekstre-Borc/ https://your-mock...workers.dev`; zip icinde analysis, flow drawing, announcements, service contract, test customers, Q&A, 6 ornek konusma; "Read BRIEF.md first; README.md says what each document is". Ikinci arguman burada mock URL'si, bizde `api_path`.
- Etki: klasor verilirse `read_text` PermissionError/IsADirectoryError ile cokup `finish(status="error: ...")`; hic workflow yayinlanmaz. Klasor elle tek `.md`'ye cevrilse bile spec_extract'e girecek metinde dosya sinirlari, tarihler ve belge turleri korunmaz (SX-12).
- Tetikleyici ozellik: "folder of docs (md/docx/pdf/xlsx)", "service contract", mock URL argumani.
- Genel cozum: (a) klasor/zip girdisi: dosyalari `[DOC n: name, type, mtime]` basliklariyla birlestiren, docx/xlsx/pdf'i metne ceviren (stdlib `zipfile`+XML ile docx/xlsx, pdf icin model/vision yolu) bir ingestion katmani; (b) tool API'nin openapi.json olmadigi durumda (docx/md "service contract") model ile openapi benzeri tool tanimina cevirme veya en azindan 7 tool + alan adlarinin cikarilmasi; (c) ikinci arguman mock URL ise `servers[0].url`'u buna yazma. Bu madde diger denetciler tarafindan da dusulmus olabilir; burada sadece spec_extract'in girdisi olarak onemi isaretlenir.

---

### SX-2 (P0) Extraction promptu ve motor satirlari Ingilizce; final Turkce

- Kanit: `spec_extract.py:252` ("short, plain English"), `LINE_KEYS` (34-71) tamamen Ingilizce ornekler (`'Am I speaking with {{title}} {{last_name}}?'`), `ENGINE_LINES` (1334-1359) brief bos birakirsa Ingilizce nötr cumle: "Thank you for your time. Goodbye.", "Is there anything else I can help you with?"; `generate.py:1230` "Speak English."; `build.py:262` smoke testi Ingilizce kelimeler (`debt`, `collections`, `past due`, `overdue`) ile sizinti ariyor.
- Etki: model Turkce brief'ten bile Ingilizce satir uretmeye yonlendiriliyor; "brief tam sozcukleri veriyorsa aynen kullan" kurali ile celisiyor (hangisi kazanir belirsiz). Brief'in vermedigi her satir (goodbye, no_info, sympathy, support_offer, help_first, rest_overdue ... ENGINE_LINES'in 24 anahtari) Turkce cagrida Ingilizce soylenir. `LINE_KEYS.opening`'in "'recorded' kelimesinden kacin" tavsiyesi Ingilizce'ye ozgu; Turkce kayit uyarisi ("kayit altina alinmaktadir") icin etkisiz.
- Tetikleyici: "the final case is in Turkish", musteriler Turkce konusur.
- Genel cozum: prompta "call language = language of the brief's own scripted lines; write every line in it" kurali, `ENGINE_LINES`'in dil-bagimli versiyonu icin modelden bir `language` alani + tum motor satirlarinin o dilde uretilmesi (tek ek cagri), smoke kelime listesinin brief dilinden turetilmesi.

---

### SX-3 (P0) Grounding supersession hatalarini yakalayamaz; eski/taslak/uydurma degerleri kabul eder

Mekanizma (`spec_extract.py:1180-1254`): `_number_in(n, text)` regex'i `(?<![\d.])n(?!\d)`: brief'in herhangi bir yerinde `n` rakami (bitisik rakam/nokta yok) gecmesi yeterli; saat icin `\b0?(hh|h12)(:mm|am|pm|h)`; kod icin kelime siniri ile geciyor mu; tatil icin `_date_in`. Hicbiri **degerin hangi kural/kaynaktan geldigine** bakmaz.

Kanit 1, kendi buyuklugunu gosteren sayim (`out/audit-scratch/` icinde `python - <<` ile; ayni mantik `gaps_tr.py`):
- Brief A (47 numarali kural): `_number_in(n)` 1..30'un hepsi icin True (yalniz 0 yok). A+B ayni. Yani `deadline_days`, `attempts`, `min/max_days_late` icin grounding A/B'de **hicbir sey denetlemiyor** (basliklar `## 2.`, `1.` gibi satir numaralari her kucuk sayiyi "bulunmus" yapiyor). Kayitli kosularda repair'in hic tetiklenmemesi bununla uyumlu.
- Brief D: 1..30'dan 0, 9, 11, 13, 21-24, 26-29 bulunamiyor; kalan her sayi gecerli.

Kanit 2, Brief D icin elle yazilmis aday spec'ler (`python out/audit-scratch/gaps_d.py`), `check()+grounding()+blocking()` sonucu:

| aday | check() | grounding() (kod uyarilari haric) | blocking |
|---|---|---|---|
| D-CORRECT (memo degerleri: 17:00, 15-60 bandi 4 gun/2 deneme, +2 tatil) | 0 | 0 | [] |
| D-STALE (eski politika: 18:30, 6 gun, 3 deneme, yalniz 12-25) | 0 | 0 | [] |
| D-DRAFT (onaysiz M-18: 20:00, 12 gun) | 0 | 0 | [] |
| D-INVENTED (17:30, 5 gun; brief'te hic yok) | 0 | yalniz `17:30` | [] |

Dogru, eski ve taslak spec'ler birbirinden ayirt edilemiyor; uydurma `5` bile gecti. Tum kosullarda build "ok_spec_problems"a bile dusmez (check() 0), sessizce yanlis agent cikar. Eski deger hem metinde geciyor hem `grounding` onu "brief'te var" sayiyor; tek savunma modelin kendisi.

Ek: prompt modele **tarih vermiyor**. `PROMPT` (241-271) "call's date is the variable `now`, so apply what is in force for calls being made now" diyor ama build gunu/test gunleri bilgisi yok; model "yururluk tarihi 19 Ekim 2026" gibi bir duyuruyu hangi tarihe gore degerlendirecegini bilmiyor. README: "several calendar days" ve spec statik (efektif tarihli deger yok, bkz. SX-7), yani yururluk tarihi test penceresinin ortasina dusen bir duyuru icin bazi gunler yanlis kalir. D'de memo 12 Ekim'de yururlukte, tum fixture'lar sonrasi oldugu icin sorun gorunmez; finalde "duyurular" bu kalibi test edebilir.

- Tetikleyici: "a memo that changes it", "announcements", "draft", "effective date", e-posta vs politika onceligi.
- Genel cozum: (1) modelden her policy degeri icin `{value, source_doc, verbatim_quote, status: in_force|superseded|draft|future, effective_from}` istemek; grounding'i bu **alintinin brief'te birebir (normalize edilmis) gecmesi** + alintinin yanindaki 200 karakterde `taslak/DRAFT/onaylanmadi/iptal/superseded` gibi isaretleyici olmamasi + her degerin `superseded` listesindeki degerlerle ayni olmamasi uzerinden yapmak; (2) ayni cagrida modele "builder'in referans tarihi" ve "test gunleri bilinmiyor" bilgisini vermek, yururluk tarihi test penceresinde kalan kurallar icin acik `ambiguity` alani doldurtup trace'e `uncertainty: effective-date` yazdirmak.

---

### SX-4 (P1) Grounding Ingilizce'ye bagli: Turkce/prefixli/kelimeyle yazilmis dogru degerleri yanlis reddeder; repair turu gurultulu ve test edilmemis

Kanit (`python out/audit-scratch/gaps_tr.py`, `gaps_tr2.py`, `gaps_d.py`; `out/audit-scratch/brief-tr-ekstre.md` Turkce kurgusal hammadde):
- `_MONTHS` ve `_date_in` yalniz Ingilizce ay adlari: "29 Ekim 2026", "1 Ocak 2027", "23 Nisan 2027" -> `not found`; "16-19 Mayis" araligindan acilan 16/17/18 -> `not found` (19 ve 15 yalniz "may" on ekinin tesadufuyle bulunur). Ayni durum Ingilizce'de "24-26 March" araliklari icin de gecerli: araligin ortasindaki gun hep reddedilir (tatil araliklari sik).
- `_NUMWORDS` yalniz Ingilizce 0..20: Turkce "on is gunu", "bes", "uc deneme" ve Ingilizce "thirty", "forty-five", "twenty-five" -> `not found`.
- Saat regex'i `09.00` / `saat 9'da` bicimini bilmiyor: `callback start 09:00 is not found`.
- Dogru post-duyuru spec'i (brief-tr-ekstre) 8 yanlis red aldi (6 tatil + 2 saat); kod listesi duz belirtec olduklari icin (SOZ, RET, ITIRAZ ...) gecti.
- Brief D tarzi prefixli kod: dogru D-CORRECT spec'te 17 kod ("D_XFER", "D_NO_CONTACT", "D_UNCLEAR"...) `not found in the brief`, cunku brief "wire suffix" + "prefix `D_`" diyor ve tam kod metinde yok. Mevcut `grounding()` `close/note code` kontrolunu tam eslesme ile yapiyor.
- Etki mekanizmasi: `extract()` (1274) check+grounding problemlerini birlestirip tek repair turu yapiyor (1277-1292); repair mesaji "'not found' may still be right... keep it only if the brief really says it" (1283-1286) diyor ama 17 yalanci uyari modeli dogru degerleri degistirmeye iterek (ozellikle tatil listesinden gun atma, kodu "bulunan" yazima cevirme) regresyon riski tasiyor. Repair sonrasi `check()` yeniden calismaz; sonuc dogrudan `spec`e yazilir (1290), fark/regresyon kontrolu yok. Kayitli hicbir run'da repair turu yok (`grep "repair the spec" runs/*/*/*.log traces/*.log` -> 0), yani bu yol canlida hic denenmemis.
- Ayrica `text = brief.lower()`: Turkce `İ.lower()` = `i̇` (birlesik nokta U+0307) ve `I.lower()` = `i` (dogrusu `ı`); Turkce buyuk harfli basliklarda eslesme bozulabilir.
- Genel cozum: grounding'i dil-bagimsiz yapmak: modelden alinti (SX-3) + alintinin brief'te normalize (casefold, NFKC, bosluk/noktalama) alt-dize eslesmesi; tarih/sayi/saat dogrulamasini ikinci bir (kucuk, ucuz) model cagrisina veya Turkce/Ingilizce ay-sayi tablosuna dayandirmak; kod grounding'inde `prefix+suffix` bilesimi ve tablo hucreleri icin kismi eslesme (kodun kuyrugu brief'te kelime olarak geciyorsa kabul); repair turunu yalniz sert `check()` hatalari icin kosmak ve repair sonrasi diff + yeniden `check()` yapip regresyonda ilk spec'i secmek.

---

### SX-5 (P1) Cokme yollari: tek bozuk cevap, tip hatasi veya baglanti kopmasi tum build'i oldurur

Hepsi icin build.py:469 `except Exception` -> `finish(status="error: ...")`, workflow yok.

1. **parse_json zayif** (`llm.py:208`; `spec_extract._parse` 1313, retry/fallback yok). Offline test (`python -c` parse_json uzerinde): JSON'dan once `{{title}}` iceren yorum, JSON'dan sonra `{x}` iceren not, yarim kalan cevap, sondaki virgul, `//` yorum, string icinde ham satir sonu -> hepsi `JSONDecodeError` -> `SpecError`. Ilk cevap bozuksa gozlemci spec'i (gecerli olabilir) kullanilmaz; repair cevabi bozuksa gecerli ilk spec de atilir (1290).
2. **check() tip savunmasi yok.** `out/audit-scratch/fuzz_types.py`: spec'in bir bolumu yanlis tipte ise (ornek `attempt1: "first business day"` string, `bands` string/None elemanli liste, `callback: "09:00-18:00"`, `calendar.holidays: 5`, `identity.questions: ["postcode","birth_year"]`) **67 kombinasyon** `check()`, `grounding()`, `autocorrect()` veya `to_generator()` icinde AttributeError/TypeError atar. `extract()` `check()`'i dogrudan cagirdigi (1274) icin repair'e bile ulasmadan build olur. Model sema disi bir kurali "kendince" bir string'e sikistirdiginda (SX-7) bu olasidir.
3. **Hata olarak raporlanip bloklanmayan ama sonra to_generator'da cokenler**: `callback.reasks: "once"` -> `structure()` uyarir (non-blocking, "building anyway"), sonra `to_generator` `int("once")` ValueError (`spec_extract.py:1454`), `generation failed`, hicbir sey yayinlanmaz.
4. **Baglanti hatasi retry edilmez** (`llm.py:170-181`): `except (URLError, TimeoutError, JSONDecodeError)` retry eder, `except Exception: ... raise` retry etmez. Yerel sahte sunucu testi (`python out/audit-scratch/llm_fault.py`): cevap govdesi ortasinda baglanti koparsa `IncompleteRead` ile **tek denemede** olur (50-120 sn suren non-stream cagrida makul bir olay); iki saglayici anahtari olsa bile ikinciye gecmez.
5. **Provider kesintisi penceresi cok kisa**: HTTP 503 icin 3 deneme, uyku 3+6+9 sn ~18 sn; bu surede toparlanmazsa `LLMError`. Ana cagri ve observer ayni anda baslar (iki kat istek -> 429 ihtimali artar), ikisi de dusebilir.
6. **Kesilmis cikti fark edilmez**: `max_tokens=16000` (`llm.py:144`); Anthropic `stop_reason: max_tokens`/OpenRouter `finish_reason: length` okunmuyor; kesik JSON -> madde 1. Turkce metin ve uzun `notes` token sayisini artirir (kayitli A+B kosusu 13.7k toplam token; Turkce ve klasor girdisinde 2-3 kat olabilir).
- Genel cozum: `parse_json` yerine `json.JSONDecoder().raw_decode` + `strict=False` + sondaki virgul/yorum temizligi; parse/tip hatasinda tek "return only valid JSON" yeniden denemesi, ardindan observer spec'ine dusme; `check()` etrafinda tip dogrulayici (`coerce_spec`) veya try/except ile "tip hatasi -> repair mesaji"; `chat()`'te bütün `OSError`/`http.client.HTTPException` icin retry ve diger saglayiciya gecis, backoff'u 20 sn yerine dakikalara cikarma, `stop_reason` kontrolu ve kesilmede yeniden deneme.

---

### SX-6 (P1) Windows cp1254'te `Trace.line` model notundaki ok/matematik isaretiyle cokertiyor

- Kanit: `builder/fb/trace.py:45` `self.path.open("a")` (kodlama verilmemis). Bu makinede `locale.getpreferredencoding()` = `cp1254`, `PYTHONUTF8` yok. `trace.note("spec decision: old 6 -> new 4")` ASCII'de sorun yok; ama `->` yerine `→` (U+2192), `≥`, akilli tirnak gibi cp1254 disi bir karakter `UnicodeEncodeError` firlatir (offline denendi: Turkce harfler gecti, `→` coktu; `finish()` de ayni `trace.line`'i kullandigi icin ikinci kez cokebilir).
- Yol: `extract()` sonunda `for n in spec.get("notes"): trace.note("spec decision: " + n[:300])` (1308-1309) ve `trace.note(f"spec check: ... {problems}")` (1276). Model notlari ("old -> new" aciklamalari tam supersession durumlarinda yazilir) `→`/`≥`/`–` icerebilir. Kayitli A/B/Northwind spec'lerinde cp1254 disi karakter yok (taradim), ama Turkce brief'te modelin bunlari alintilama ihtimali artar. Son commit 55df102 ayni sinifi (spec yazimi) duzeltmis; trace yazimi atlanmis. `spec.json` yazimi (`build.py:501`) `json.dumps` varsayilan ASCII oldugu icin guvenli.
- Etki: extraction bittikten sonra, hicbir sey yayinlanmadan build olur.
- Genel cozum: `open("a", encoding="utf-8", errors="replace")`, `PYTHONUTF8=1` ve `sys.stdout/stderr.reconfigure(errors="replace")` baslangicta; trace/yazilan tum dosyalar icin `encoding="utf-8"`.

---

### SX-7 (P1) Sema'ya sigmayan kural sessizce dusuyor; `notes` yalniz trace'e gidiyor

Kanit: `notes` yalniz `trace.note("spec decision: ...")` (spec_extract.py:1308), `generate`/`to_generator` onu kullanmaz. SCHEMA'ya (73-213) sigmayan ve prompt'ta "never invent" denen kurallar atilir. Somut bosluklar:

- `attempt_plan[].date` yalniz `attempt1 | deadline` (136, `negotiation_lib.py:353`); `attempt1.rule` yalniz `last_business_day_of_week | nth_business_day`. Brief D'nin eski politikasi "attempt 2: floor on the second business day after today" ifade edilemez; model onu `deadline`'a veya `attempt1`'e kaydirir ve gonul rahatligiyla notlara yazar. Brief D memo sonrasi planda yok ama benzer kural finalde olabilir (3 deneme, farkli tarihler).
- Bandlar yalniz `days_late` uzerinden (135-136 ve 125-134); tutar kademesi, urun tipine gore farkli son tarih, hesap alanindan gelen son tarih (ekstre/son odeme tarihi) ifade edilemez. Finalde "ekstre borcu" odakli bir politika bu eksenlerden birini kullanabilir.
- Yururluk tarihli deger yok (SX-3 notu): statik spec.
- Yarim gun tatil/arife ve gune gore degisen callback penceresi yok (`calendar.holidays` yalniz tam gun, `callback.start/end` tek pencere).
- `forbidden_before_verify` kelime listesi; "yakin/ucuncu kisi" kurallari, "teklif edilmeyecek indirim/taksit", "ozur cumlesi" gibi davranissal kurallar yalniz agent prompt'una dolayli gider.
- Bunlarin hicbiri `check()`/`proof` tarafindan bir uyari uretmez: `proof.py` yalniz trace satiri yazar ve `KINDS` regex'leri Ingilizce (`proof.py:43-...`), Turkce kural satirlari "unmapped" olur; geri besleme dongusu yok (`build.py:377-392`, yalniz `trace.note`).
- Genel cozum: spec'e `unmodelled_rules: [{quote, why_not_expressible}]` alani; build bunlari bir `extra_rules` metni olarak ilgili agent node prompt'una enjekte etsin ve trace'te "UNMODELLED" diye listelesin. Sema genisletme: `attempt_plan[].date: attempt1|deadline|nth_business_day(n)`, `effective_from` alanli deger listeleri.

---

### SX-8 (P1) Sessiz Brief-A varsayilanlari ve cok dar `blocking()`

- `blocking()` (spec_extract.py:940-957) yalniz iki sey bloklar: bos `identity.questions` ve gecerli band yok. Geri kalan her sey `ok_spec_problems` ile yayinlanir (`build.py:513-523`, 640).
- Eksik anahtarlar `to_generator` icinde Brief A degerleriyle doldurulur: `neg.setdefault(k, v)` (spec_extract.py:1370) `POLICY_DEFAULTS` ile (human_requests_to_transfer=2, stop_requests_to_close=2, promise_attempts=2, hardship=offer_support, reason_priority=[dispute,hardship,service_error,refused] ...); `callback` bos/eksikse `"10:00"`/`"19:00"`, `business_days_ahead=1` (1427-1437); `workdays` Mon-Fri; `date_order` DMY; `holidays` []. `negotiation_lib.py:19-20` yorumu "`missing_policy` lets the builder refuse a config" diyor ama `missing_policy` hicbir yerden cagrilmiyor (dead code; `quotes_found` de oyle).
- Kanit: `python out/audit-scratch/silent_defaults.py`: 7 neg anahtari + `callback` + `calendar` + `forbidden_before_verify` + `lines.opening` silinmis spec -> `check()` 9 sorun, `blocking()` [], `cfg.callback` = Brief A penceresi 10:00-19:00, `holidays=[]`, `forbidden=[]`. Ozellikle **`calendar`, `forbidden_before_verify`, `holidays` bos olmasi hic hata uretmez** (check bunlari denetlemez): "kimlik dogrulanmadan borcu anma" kurali ve tatil kurali sessizce kalkar.
- Sessizce yanlis ama gecerli kalan sema degerleri (structure/check yalniz tip/enum/mevcutluk bakar): `neg.outcomes`/`queues` anlam olarak yer degisse (promise<->paid), `floor_field`/`product_fields.past_due` baska bir var olan alana baglansa ("toplam borc" vs "ekstre borcu"), `deadline_unit` yanlis, `attempt_plan` mantik hatasi. `autocorrect` (768-912) ve `generate.match_enum` (235-266) bulanik esleme yapar (ortak sozcuk, difflib 0.75); dogru olabilir ama kayitli gerekce yok.
- `check()` hatalarinin hangisi ne yapar (ozet):
  - Yayinlanir ve **sessizce yanlis agent**: `neg.<k> missing` (Brief A degeri), `callback.start/end` hatali (10-19), `attempt_plan` bos veya kisa (motor 2 adim; `attempts` plana kirpilir, 1403-1416), `bands` bosluk/cakisma ("band yok -> o gunler icin deadline ve deneme yok", 1000-1005), `holidays` ISO olmayan (asagi SX-14), `lines.*` bos (ENGINE_LINES Ingilizce notr), `outcomes.*` enum disi (conform bulaniklasamazsa literal kalir).
  - Yayinlanir ve **calisma zamaninda tool 4xx/yanlis kayit**: enum disi `close_call`/`add_note`/`transfer` degerleri (yalniz bankanin enforce ettigi yerde; final'de mock bizim, kayit yine yanlis olabilir).
  - **Build olur**: SX-5.
- Genel cozum: `blocking()`'e (a) `forbidden_before_verify` bos, (b) `calendar` yok/holiday bos ama brief'te tatil sozcugu geciyor, (c) herhangi bir `neg.*`/`callback.*` eksikligi -> repair'e zorunlu soru, ikinci basarisizlikta Brief-A degeri yerine "unknown" isaretli trace + agent'a "bilinmeyen kural icin musteriyi insana aktar" davranisi; `missing_policy`'yi gercekten cagirmak.

---

### SX-9 (P2) Observer birlesimi forbidden listeyi sisirir ve kendi cumleleriyle celisir; anlasmazlikta ana spec secilir

- Kanit: kayitli 9 A spec'inin `forbidden_before_verify` listesi 5 farkli sekle sahip (6 ile 13 kelime arasi; bir kosuda `amount`, `card`, `promise`; AB'de `pay`) (`python out/audit-scratch/variance.py`). Birlesim `observe()` (spec_extract.py:1742-1754) gozlemcinin her kelimesini **filtresiz** ekler (gozlemci check'ini gecmese de, `observer.spec` olarak ne dondurduyse).
- Kanit 2 (`python out/audit-scratch/observer_union.py`, gercek `observe()` kodu, sahte observer): ana liste `['debt','payment','credit card']` + gozlemci `['credit','card','account','call','recorded','payment','statement','alder']` -> birlesim spec'in kendi opening/identity_why/identity ask cumlelerinde gecen 6 kelime (`credit, card, account, call, recorded, alder`) iceriyor; `check()` hala `[]`. Generate bu listeyi acilis ve kimlik node prompt'larina "never say '...'" olarak yazar (`generate.py:916, 1247, 1333, 1439, 1652`) -> ayni prompt'ta hem "say exactly: ..." hem "never say 'alder'". Smoke testi acilis cumlesinde yasakli kelime bulursa `_smoke_failed` (build.py:262-272), engellemez.
- Turkce bagi: kredi karti ekstre hatti icin urun adi (kart, ekstre, borc) forbidden'a girecek; kimlik sorusu "kartinizin son dort hanesi" gibi olabilir; birlesim `kart`i bos cumleye karsi ekleyebilir.
- Ikinci sorun: anlasmazlik loglaniyor ama yalniz `forbidden`'da etkili: `main=n other=y` (ana spec grounding'e takildi, gozlemci gecti) olsa da ana spec kalir (`observe` 1731-1741). Gercek oy birligi/uc kisilik oylama yok; gozlemci yalniz teshis.
- Genel cozum: birlesimi `forbidden` icin yalniz iki spec'in **kesisimi** veya "kelime brief'in yasaklama cumlesinde aynen geciyor mu" filtresi ile yapmak; `check()`'e "forbidden_before_verify hicbir pre-verification satiriyla/kimlik sorusuyla/acilisla/bank_name ile kesismemeli" kurali; gozlemci ve ana spec farkli ve biri grounded/gecerli ise gecerli olani sec veya 3. cagri ile hakem.

---

### SX-10 (P2) Nondeterminizm: Anthropic yolunda sicaklik 1.0, oylama yok

- `llm.py:83`: Anthropic istegine temperature konmaz (400 hatasi nedeniyle). `.env` her iki anahtari da iceriyor (`ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY`), saglayici sirasi once Anthropic (`llm.py:55-63`) -> varsayilan yol T=1 civari. Kayitli sonuc: ayni brief iki kosuda `forbidden` listesi degisiyor (yukarida), `business_days_only` bir AB kosusunda `false` cikmis ve override yakalamis (`variance.py`: 6 true/1 false), `product_order` null/`get_account` karisik, `tool_fields.*.response` var/yok.
- Policy alanlari 9 kosuda ozdes: iyi isaret, ama A/B numarali ve temiz; belirsiz/tahrif edilmis hammaddede ayni kararlilik beklenemez. Ayni spec'in iki kosusu farkli agent verir ve "3 tekrarda gecme" kriteri build degil agent tekrarini olcer (cunku submit edilen tek build); yine de yanlis/dogru spec secimi tek zar.
- Self-consistency yok: gozlemci ikinci bir ornek ama merge yalniz forbidden'da (SX-9). Sert alanlarda (bands, callback, holidays, kodlar) iki okuma farkli ise `uncertainty:` satiri yazilir, ana spec kalir.
- Genel cozum: kritik policy alanlari icin 3 okuma + coklu oy; oy birligi yoksa o alan icin hedefli 2. soru (alinti ile); `chat()`'te Anthropic icin `temperature` kabul eden modeller icin gonderme, yoksa `top_p` vb.; spec'i diske yazip (hash) ayni brief hash'i icin yeniden kullanma secenegi.

---

### SX-11 (P2) `business_days_only` override'i tek yonlu ve yalanci pozitif uretebilir

- `_callback_business_days` (1204-1214): cumlede `callback` + `business day` var ve `weekend|saturday|sunday|holiday` yok ise `true`a zorlar (1293-1302 ve grounding 1238).
- Kanit: "Callbacks may be booked on any day, 7 days a week, while promise dates must be business days." -> True (yanlis). "Geri arama (callback) her gun yapilabilir; odeme sozu tarihleri business day olmalidir." -> True. Tek yonlu: dogru `true`yu degistirmez, ama dogru `false`i (7 gun geri arama) ezer.
- Etki: Turkce bankalarda cumartesi arama olasiligi; yalanci pozitifte cumartesi geri arama reddedilir.
- Cozum: override yerine modelden alinti iste, alinti `weekend|7 days|every day` iceriyorsa dokunma; veya yalniz uyar.

---

### SX-12 (P2) Kaynak onceligi ve belge turleri ayrimi yok; ornek konusmalar/test musterileri/Soru-Cevap normatif sayilabilir

- `PROMPT` (241-247): "later or explicitly overriding wins" ve "Read ALL of it". Klasorden birlestirilen girdide "later" dosya sirasi mi, tarih mi belli degil; ornek konusmalar (6 adet) ve "test customers" icindeki sayilar/tarihler/akis (eski politika surumuyle yazilmis olabilir) politika gibi okunabilir; Soru-Cevap belgesi analiz belgesiyle celisebilir.
- Prompt'ta belge turune gore oncelik (analiz < duyuru < Q&A?) tanimi yok, "orneklerden kural cikarma, orneklerle celisen kurali dogrula" yok. Brief D'nin aciklik cumlesi ("Approved memo effective at call now > policy > explanatory email. Drafts are never effective") bir brief'e ozgu; baska brief bunu soylemez.
- Genel cozum: ingestion'da her belgeye tur + tarih + yol basligi (SX-1) ver; prompt'a genel oncelik sirasi (onayli ve yururlukte olan duyuru/memo > ana politika > S&C aciklamasi > e-posta > taslak > ornekler) ekle; ornek konusma/test musteri bolumlerini "yalniz dogrulama icin, deger kaynagi degil" etiketli ver.

---

### SX-13 (P3) Arac yanit semasi 1500 karakterde kesiliyor

- `tool_summary` (spec_extract.py:292): `"    response: " + json.dumps(shown)[:1500]`. A'nin `get_account` yaniti 563, D 603, E 886 karakter; yaklasik 80 karakter/alan ile ~18 alan icin sinir. Ekstre/statement alanlari (asgari odeme, son odeme tarihi, kesim tarihi, kart sonu, faiz, ...) ile zengin bir yanit kesilirse modelin gormedigi alan `product_fields`/`floor_field` icin uydurulur; `responses()` yanit ozelliklerini sema'dan tam okudugu icin yanlis alani yakalar ama dogruyu bilemez.
- Cozum: kesme yerine alan listesi (isim + tip + aciklama) kompakt sekilde, her zaman tam.

### SX-14 (P3) Tatil formati: ISO olmayan ama `fromisoformat`i gecen degerler sessizce etkisiz

- `check()` (359-363) `date.fromisoformat` ile dogrular; Python 3.12'de `20261029` ve `2026-W44-4` gecer, ama motor `d.isoformat() not in set(cfg["holidays"])` (`collections_lib.py:42,206`) ile **string** karsilastirir -> bu tatiller hic eslesmez. `python` denemesi: `check()` sorun vermedi, `cfg.holidays=['20261029']`. `29.10.2026` ve parantezli aciklama ise uyari verir ama non-blocking; `cfg`te ayni bozuk string kalir.
- Cozum: `to_generator`'da tatilleri `date.fromisoformat(...).isoformat()` ile normalize et, ayrilamayanlari at ve trace'e yaz.

### SX-15 (P3) proof/observer yalniz teshis; Turkce'de kor

- `proof.py` `KINDS` regex'leri Ingilizce; Turkce brief'te nerdeyse tum birimler `unmapped`; zaten dongude kullanilmiyor (SX-7). Sonuc: ozel finalde "spec dogru mu?" sorusuna cevap veren hicbir bagimsiz sinyal yok.

---

## 2. Sessizce yanlis agent yollari (listesi)

Her biri `check()=0` veya `ok_spec_problems` ile yayinlanir:

1. Eski/taslak/uydurma sayisal veya saat degeri kabul (SX-3): `deadline_days`, `attempts`, `callback.end`, holiday listesi.
2. Memo/duyuru yururluk tarihi test gunlerinin ortasinda; statik spec tek tarih secer (SX-3, SX-7).
3. Sema'ya sigmayan kural (farkli deneme tarihi, tutar kademesi, son odeme tarihi ekseni, yarim gun) notlarda kalir (SX-7).
4. Eksik policy anahtari -> Brief A degeri; eksik callback -> 10:00-19:00; eksik calendar -> tatil yok; eksik forbidden -> kimlik oncesi kisit yok (SX-8).
5. Bos/hatali `lines.*` -> Ingilizce notr satir veya bos opening (SX-2, SX-8).
6. Ingilizce satirlar Turkce cagrida (SX-2).
7. Over-broad/celiskili `forbidden_before_verify`: agent zorunlu acilis/kimlik cumlesini soylemez veya "alder"/"kart" gibi kelime nedeniyle bozulur (SX-9).
8. Yanlis ama gecerli alan eslemesi (`past_due`, `floor_field`, outcome<->enum) (SX-8).
9. `business_days_only` yalanci pozitif override (SX-11).
10. ISO olmayan tatil bicimleri etkisiz (SX-14).
11. Band bosluklari/cakismasi: o gun araliginda deadline ve deneme yok (SX-8).
12. Ornek konusma/test musterisi degerinin policy olarak alinmasi (SX-12).
13. Repair turu dogru degeri bozar (grounding yalanci uyari, regresyon kontrolu yok) (SX-4).
14. Brief'in kendisinde zaten celiskili/belirsiz kural: `notes`'a yazilir, agent'a hicbir iz gitmez; "most literal reading" tek zar (SX-10).

## 3. Calistirilan offline deneyler ve ciktilar

Tumu `out/audit-scratch/` altinda:

- `variance.py`: 9 A + 7 AB spec'in `policy_fields` karsilastirmasi. Sadece `forbidden_before_verify` (5 farkli liste), `business_days_only`, `product_order`, `tool_fields.*.response` degisiyor.
- `gaps_d.py`: Brief D icin 4 aday spec; tablo SX-3'te. `fuzz_types.py`: 67 cokme kombinasyonu. `silent_defaults.py`: SX-8. `observer_union.py`: SX-9. `llm_fault.py`: SX-5 (IncompleteRead retry yok, 503 pencere ~18 sn, kesik JSON). `gaps_tr.py`, `gaps_tr2.py`, `brief-tr-ekstre.md`: Turkce grounding yalanci redleri (SX-4).
- Mevcut testler: 59 gecti (yukaridaki komut). Kayitli spec fixture'lari (`tests/hack/fixtures/spec_ab_extracted.json`, `spec_northwind_extracted.json`) yalniz Ingilizce ve temiz; Turkce/klasor/duyuru yok.
- Offline calistirilamayan: gercek modelin Turkce/duyurulu hammaddedeki davranisi (model/ag gerekli).

## 4. Ilk 5 (oncelik sirasi)

1. **SX-3 (P0)** grounding'in supersession'a kor olmasi ve tarih/kaynak provenance'inin olmamasi: yanlis agent'in en olasi sessiz yolu; final "announcements" ile bunu dogrudan test edebilir.
2. **SX-1 + SX-2 (P0)** girdi (klasor/docx/pdf/xlsx + service contract) ve dil (Ingilizce satirlar): ikisi de olursa spec dogru olsa bile agent calismaz veya yanlis dilde konusur.
3. **SX-5 + SX-6 (P1)** cokme yollari: tek bozuk JSON, tip hatasi, `IncompleteRead`, cp1254 trace hatasi build'i hic agent olmadan bitirir; hepsi ucuz duzeltme.
4. **SX-7 + SX-8 (P1)** sema disi kurallarin sessiz dusmesi ve Brief-A varsayilanlari; ozellikle callback penceresi/tatil/forbidden bos kalmasi.
5. **SX-4 + SX-9 (P1/P2)** Turkce grounding yalanci redleri ile gurultulu/regresif repair ve observer birlesiminin forbidden listeyi celiskili hale getirmesi.

## 5. Hizli duzeltme onerileri (kod degistirilmedi)

- `parse_json`: `raw_decode`, `strict=False`, yeniden deneme; `check()` tip korumasi; `Trace.line` UTF-8; `chat()` icin `OSError`/`HTTPException` retry; `stop_reason` kontrolu (SX-5, SX-6).
- `to_generator`: tatil normalizasyonu, eksik `neg`/`callback`/`calendar` icin acik "UNKNOWN" trace + build durdurma veya repair (SX-8, SX-14).
- `check()`: `forbidden_before_verify` vs spec'in kendi opening/identity/who_question/bank_name kesisimi (SX-9).
- Prompt: Turkce/dil kurali, tarih bagi, belge turu onceligi, ornek konusmalar yalniz dogrulama (SX-2, SX-3, SX-12).
- Grounding: alinti tabanli, dilden bagimsiz; repair sonrasi diff + yeniden check (SX-3, SX-4).
