# 05 - Tarih / takvim / saat dogrulugu (differential test)

Kapsam: `builder/fb/nodecode/collections_lib.py` (takvim, is gunu, bant, deadline, callback penceresi),
`speech_lib.py` (soylenen tarih/saat), `bodies.py::CB_CHECK` (callback dogrulama), ve bunlarin
uretilen workflow icindeki gomulu halleri (`submission/brief-ab-run.workflow.json`, 15 code snippet).
Yontem: brief'in tarih kurallarindan (A 6-7, 18-23, 30; B 3) bagimsiz bir referans yazildi
(`out/audit-scratch/cal_ref.py`, ordinal aritmetigi, repo kodu paylasmiyor), repo fonksiyonlari binlerce
`now` uzerinde buna karsi calistirildi. Repo dosyalarina dokunulmadi, commit yok.

## Ozet

- Cekirdek takvim matematigi (is gunu, N. is gunu, deadline, attempt-1 tarihi, callback gunleri/saatleri,
  promise-date gecerliligi, "tomorrow / in two days / next|this <weekday> / the 20th / March 3") referansla
  **0 mantik sapmasi** verdi (asagidaki istatistikler). Takvim kodu sandbox'ta sadece `json`, `re`, `datetime`
  kullaniyor; Python 3.10 ve 3.12 ayni cikti veriyor; sonsuz dongu yok; saat/DST/yerel saat sorunu yok
  (yalnizca naif tarih, `datetime.now()`/`date.today()` sizintisi yok).
- Asil riskler kod hatasi degil **tasarim bosluklari**: (1) kural degisikligi memolari (yururluk tarihi)
  build aninda tek sefere cozuluyor, (2) soylenen tarih/saat ayristiricisi yalniz Ingilizce, (3) hafta her
  zaman Pazartesi baslar, (4) birkac uc durum.
- Mevcut testler (`test_calendar.py`, `test_call_days.py`) hepsi geciyor ama yalniz Cumartesi-Pazar hafta
  sonu, 5 gun, tek tatil seti kapsiyor (bkz. "Test bosluklari").

## Fuzz istatistikleri

Takvim: 4 hafta sonu kumesi {Sat,Sun}, {Fri,Sat}, {Sun}, {} x 6 tatil seti (Brief A, bos, hafta sonu tatilleri,
12 gunluk Noel blogu, "baska takvim" 11 tatil, ay sonu tatilleri) x 182 gun (1 Eki 2026 - 31 Mar 2027) x 9 saat
(00:00, 09:59, 10:00, 10:01, 11:00, 18:59, 19:00, 19:01, 23:59).

| Deney | Calisma | Mantik sapmasi |
|---|---|---|
| `calendar_facts` cagrisi (state, now_time, today_is_business_day, last_bd, attempt1 [Pzt-baslangic], callback_days, 28 satir date_table) | 39.312 cagri, 280k+ kontrol | 0 |
| deadline N=0..30 is gunu (her N ayri bant) + "is gunu ve >= today" | 135.408 + 135.408 | 0 |
| `check_promise_date` (today-3 .. deadline+12 her gun, 5 farkli N) | 194.372 | 0 |
| `parse_spoken_date` (today, tomorrow, in N days (rakam+kelime), in a week/two weeks, next/this/bare/on <weekday>, "the Nth" 1..31, Month N, N Month, N/M, olmayan tarihler) | 47.138 | 0 |
| CB_CHECK (gercek snippet govdesi): gun metni x saat metni (09:59, 10:00, 10:01, 14:30, 18:59, 19:00, 19:01, 20:00, 08:00; 24 s ve am/pm) x 8 `now` saati x 3 takvim | 290.720 | 0 |
| deadline_unit="calendar", callback business_days_ahead 0..3, business_days_only true/false | 58.240 | 0 |
| Uctan uca (`engine_sim.simulate`, mevcut Bank + sonuc defteri; musteri "tomorrow / in two days / next Friday / this Friday / Monday / the 20th / deadline+3" der, onaylar): kayitli tarih = beklenen (bagimsiz oracle) | 3.720 | 0 |
| Uctan uca: "bugun odeyebilirim" (hafta sonu/tatilde sonraki is gunu) ve "hayir -> attempt 1 -> evet" | 364 | 0 |
| Python 3.10.10 vs 3.12.2 ozet karsilastirmasi (910 facts + 15 ifade x 182 gun) | hash ayni | 0 |
| Toplam | ~1,1 M kontrol | 0 mantik sapmasi |

Ham fuzz sayaci 23.737 "sapma" gosterdi; hepsi asagidaki bulgulardan: 180 = CAL-4 (bos attempt-1),
9.729 = CAL-3 ({Fri,Sat} icin dogal hafta okumasi), 13.828 = CAL-7 (pencere metni "from HH:MM" = `now`).
Performans: `calendar_facts` 1,28 ms/cagri (31 bant dahil), 700 tatilli takvimde 6 ms; tum gunler tatil /
`workdays=[]` / N=1000: dongular sinirli (60/400), sonsuz dongu yok.
Betikler: `out/audit-scratch/cal_fuzz.py`, `cal_cb_fuzz.py`, `cal_fuzz2.py`, `cal_e2e.py`, `cal_e2e2.py`,
`cal_digest.py`, `cal_probe1-4.py`.

## Bulgular

### CAL-1 (P1, kosullu) - Yururluk tarihli kural degisiklikleri build aninda tek seferde cozuluyor; `now`'a gore calisma zamani secimi yok

- Kanit: `spec_extract.py:243-246` extractor'a "memo kurali yururluk tarihinden itibaren degistirir; cagri tarihi
  `now`, **simdi yapilan aramalar icin gecerli olani uygula**" diyor. Extractor'a gercek bugun verilmiyor
  (`strftime/now` yok), `CFG` semasinda `from/until/effective` alani yok (`grep effective|valid_from|versions`
  nodecode/generate/proof: sonuc yok). `build.py:240` smoke testi gercek saati kullaniyor.
- Neden onemli: `kit/README.md` "iki final brief'ten biri ham banka materyali: ... **degistiren bir memo**" ve
  "birkac takvim gununde, uc kez". Hackathon sayfasi/diger denetcinin Turkce sentetik brief'i (D-2026/41:
  "19 Ekim 2026'dan itibaren geri arama bitisi 17.30, 11-90 gun bandinda son tarih 5 is gunu, 2 deneme;
  yururluk oncesi eski kurallar") tam bu tuzak. Tek donmus spec ya hep eski ya hep yeni kurali uygular; test
  `now`'lari yururluk tarihinin iki yanina duserse bir yan kesin kalir. Extractor'in "memo wins" sezgisiyle
  hangi tarafa dustugu de belirsiz.
- Tekrar (kod okuma): `now=2026-10-14` ve `now=2026-10-21` icin ayni `CFG` -> ayni `deadline_*`,
  `callback.end`, `attempts`.
- Etki: yururluk tarihi test araligina duserse o gunlerin butun musterileri (deadline, callback sonu, deneme
  sayisi) yanlis. Yururluk tarihi hepsinden once/sonra ise sorun yok (o yuzden kosullu P1; patlama yaricapi en
  buyuk).
- Cozum: `CFG["versions"] = [{"from":"YYYY-MM-DD","set":{...}}]`; extractor eski degeri + degisiklikleri
  `from` ile cikarsin; `calendar_facts/product_facts/CB_CHECK/new_state` en basta `cfg = effective_cfg(CFG, now)`
  (derin birlestirme: bands, callback, holidays, attempts, attempt_plan) uygulasin. Tarih karsilastirmasi
  `today_iso >= from`. Test: ayni cagri 2 yanda 2 farkli deadline.

### CAL-2 (P1 kosullu, Turkce final ise P0) - Soylenen tarih/saat ayristiricisi yalniz Ingilizce

- Kanit: hackathon sayfasi (snapshot 2026-10-11) "final case is in Turkish" (satir 73, 116, 207). `speech_lib.py`
  ay/gun/sayi/ordinal/goreli ifade tablolari Ingilizce.
- Tekrar (today=2026-10-14): `parse_spoken_date('yarın')`, `'bugün'`, `'cuma'`, `'gelecek cuma'`, `'bu cuma'`,
  `'30 Ekim'`, `"ayın 20'si"`, `'iki gün sonra'`, `'haftaya pazartesi'` -> hepsi `('', 'none')`;
  `read_reply` bunu "vague"a ceviriyor (`negotiation_lib.py:1560-1563`): ajan bir kez "tam gun" sorup sonra
  deadline'i dayatir. Ek hata: `parse_spoken_time('20 Ekim') == '20:00'` (rakam saat sanilir),
  `parse_spoken_time("ayın 20'si") == '20:00'`.
- Etki: Turkce finalde tarih soyleyen her musteri (cogu senaryo) vague/deadline yoluna duser; "yarin" diyen
  callback ucuncu kisi "outside"a. Ingilizce finalde etkisiz.
- Not: `said_date_text` extractor aciklamasi "as said" diyor; Turkce kalir. Cozum: speech_lib'e dil tablosu
  (gun/ay/ordinal/goreli: yarin, ertesi gun, öbür gün, haftaya, gelecek/bu hafta <gun>, ayin N'i, N <ay>,
  `N gun sonra`, "hafta basi/sonu/ay sonu" = vague, saat: "saat uc", "ogleden sonra", "bucuk", "çeyrek"), dil
  brief'ten cikarilir; ya da extraction degiskeninde modelden "ISO tarih veya Ingilizce kalip" istenir.
  (Dil/NLU denetimiyle ortusuyor.)

### CAL-3 (P2) - Hafta her zaman Pazartesi baslar; hafta sonu {Fri,Sat} olan takvimde "this/next <weekday>" ve attempt-1 kayiyor

- Kod: `last_business_day_of_week` ve `date_table` etiketleri `d - d.weekday()` (Pzt), `parse_spoken_date`
  `monday = today - weekday()`.
- Tekrar (hafta sonu Fri+Sat, tatil Brief A): `now=2026-10-01T11:00` (Persembe, haftanin son is gunu).
  Dogal Pazar-Persembe haftasi: bugun son is gunu -> attempt-1 = **2026-10-08** (Persembe). Repo:
  **2026-10-04** (Pazar; Pzt-Paz haftasinin "son is gunu"). `{Fri,Sat}` icin 9.828 kosunun 9.729'unda sapma.
  Ayni hatadan `next Sunday` (Pzt-haftasi: 11 Eki, dogal hafta: 4 Eki) ve date_table "this/next" etiketleri.
  `{Sat,Sun}`, `{Sun}`, `{}` icin 0 sapma.
- Okuma farki: brief "hafta"yi tanimlamiyor; Pzt-Paz literal okumasi savunulabilir ama bir insanin yazacagi
  beklenen deger (hafta sonu Cuma-Cumartesi olan banka icin) Persembe olur. Finaller "baska takvim, belki baska
  hafta sonu" ise risk gercek.
- Cozum: `cfg.week_start` (varsayilan: `workdays` icinde "onceki gunu is gunu olmayan ilk gun", yoksa 0);
  `last_business_day_of_week`, `date_table`, `parse_spoken_date(text, today, week_start=0)`'da kullan.

### CAL-4 (P3) - Komsu hafta tamamen tatilse `attempt1_date` bos; ajan "on ?" diyor ve takiliyor

- Tekrar: tatiller 2026-12-24..2027-01-04 (12 gun), `now=2026-12-23T11:00`, Brief A calendar. Bugun haftanin
  son is gunu, gelecek haftada is gunu yok -> `last_business_day_of_week(today+7)=None` -> `attempt1_date=""`
  (180/39.312 kosu). Uctan uca: "Could you pay 1250.00 for the car loan on ?", "To confirm: ... on . Do you
  confirm?" ve promise kaydedilmedi.
- Etki: dusuk olasilik (en az 5 ardisik is gunu tatil, Bayram kopruleri / Noel-yilbasi bloklari) ama kalici
  hata (bos tarih okunur).
- Cozum: `attempt1_date`: hafta sonrasi bos ise ilk is gunu bulana dek (en fazla 8 hafta) ileri sar; en son
  `min(..., deadline)` zaten `_attempt_offer`'da var. Bos sonuc `_d("")` yerine deadline.

### CAL-5 (P3) - CB_CHECK `now16` ham string dilimi, `parse_now` ile tutarsiz

- Kod: `bodies.py:272` `now16 = str(vars.get('now'))[:16].replace(' ', 'T')`; "later than now" karsilastirmasi
  string. `parse_now` ise strip eder, tek haneli saati yutar.
- Tekrar: `now=' 2026-10-14T09:30'` (bas bosluk), gun "today", saat "2 pm" -> `outside` (beklenen `ok`;
  `now16='T2026-10-14T09:3'` her tarihten buyuk oldugu icin bugunun her saati reddedilir; bosluksuz `now` ile `ok`).
  `now='2026-10-14T9:30'` ile "today 2 pm" -> `outside` (beklenen `ok`; string karsilastirmasinda '1' < '9').
- Etki: yalniz bicimi bozuk `now`'da; ornek format `2026-10-14T11:00`, saniyeli/Z/ofsetli/bosluklu/yalniz-tarih
  bicimleri dogru (asagi bkz.).
- Cozum: `now16 = dt.strftime('%Y-%m-%dT%H:%M') if dt else ''`.

### CAL-6 (P3) - `now` ISO disinda ise butun cagri `service_error`

- `parse_now` formatlari: `2026-10-14T11:00` (+ `:00`, `.123456`, `Z`, `+03:00`, bosluk, yalniz tarih,
  `T11`, kenar bosluk) dogru; ofset yok sayilir (naif yerel saat, tutarli). Sapmalar: `'14/10/2026 11:00'`,
  `'2026/10/14 11:00'`, RFC-2822, epoch -> `calendar_state=unknown`; uctan uca: tum urunler deadline'siz,
  cagri `no_promise / service_error`. `'2026-10-14T9:05'` -> saat 00:00 (zararsiz: <10:00 zaten gecerli).
- Etki: yalniz brief ornegi disinda bir `now` formati gelirse, ama o zaman 100% basarisiz. Brief ornegi
  `2026-10-14T11:00`.
- Cozum: `parse_now`'a regex yedekleri (`DD.MM.YYYY`, `DD/MM/YYYY`, `YYYY/MM/DD`, epoch s/ms, "14 Oct 2026").

### CAL-7 (P3) - Callback pencere metni "from HH:MM" = `now`; ilk gecerli dakika `now+1`

- `callback_window_text` bugun icin `max(start, now_time)` yaziyor ("today ... from 18:59 to 19:00" `now=18:59`).
  Brief "later than now" -> 18:59 gecersiz, tek gecerli slot 19:00. CB_CHECK dogru reddediyor (290k kosu,
  sapma yok); yalniz modelin sozlu yonlendirmesi bir dakika yaniltici (13.828 metin sapmasi).
- Cozum: ilk slot = `now+1 dk` (en az 10:00).

### CAL-8 (P3, okuma farki) - Callback "bugun" yalniz is gunuyse

- Brief 6: "today or the next business day". Repo: `business_days_only=true` iken `now` hafta sonu/tatilse bugun
  yok (sadece sonraki is gunu). Literal okuma ("bugun" her gun serbest, 10:00-19:00, `now`'dan sonra)
  Cumartesi/Pazar `now` icin farkli. `spec_extract.py` yorumu gercek extraction'in bunu `false` verdigini
  ve Cumartesi callback'inin sunuldugunu not ediyor; override (bilincli) bu. Her iki yon da brief'e uyar;
  hafta sonu `now` ile test ihtimali dusuk. Bir sey yapilmasin; sadece bu varsayim bilinsin. Finalin "now" gunu
  Cumartesi/Pazar/tatil ise 50-50 risk.

### CAL-9 (P3) - Soylenen tarih ifadeleri: nadir bosluklar

today=2026-10-14 (Carsamba):
- `'a week from tomorrow'` -> **2026-10-15** (yarin; beklenen 2026-10-21): "tomorrow" kurali goreli-hafta
  regex'inden once cozuluyor.
- `'in three working days'`, `'in 3 business days'` -> `none` (read_reply'de vague olur: bir kez "hangi gun"
  sorar; dogru okuma 3. is gunu).
- `'20-10-2026'` -> none (vague); `'03/04'` DMY sabit (`date_order` yalniz DOB'de; MDY bankasi icin 3 Nisan).
- `'this Monday'` Pazar gunu -> gecmis Pazartesi (brief 22 literal okuma, dogru ama insan "yarin" der).
- `'the 31st'` 30 gunluk ayda -> unclear (olmayan gun; yeniden sorar; kabul edilebilir).
- Dogru calisanlar (kanit): the 20th gecmisse sonraki ay, Aralik->Ocak devri, `March 3` Ekim'de -> 2027-03-03,
  `Feb 29` gecersiz yil -> unclear, `next Friday` Cuma'da -> sonraki hafta Cuma, `this Friday`, `day after
  tomorrow`, `in a fortnight`, `Friday after next`, kelimeli "twenty-third / the third of December /
  October the thirtieth / two thousand twenty-six".

### CAL-10 (P3) - Tatil dizgesi tam eslesme; sinirli dongu sessiz kirpar

- `is_business_day`: `d.isoformat() not in set(holidays)`. Dogrulama (`spec_extract.py:359`) `date.fromisoformat`
  kullaniyor; Python 3.11+ `20261026` ve `2026-W44-1` biciminde kabul eder ama tatil hic eslesmez
  (date_table'da is gunu gorunur). Cozum: `cfg` olustururken `date.fromisoformat(h).isoformat()` ile normalle.
- `nth_business_day_after` en fazla 400 gun tarar: N~>280 is gunu (ya da tum takvim tatil) sessizce yanlis tarih
  doner (N=1000 -> 2027-11-18). Gercekci degil; sonsuz dongu yok.
- Bos tatil listesi, hafta sonuna dusen tatil, deadline N=0 (bugun / is gunu degilse sonraki is gunu) dogru.

### CAL-11 (P2, spekulatif) - Deneme tarih bicimi ifade gucu sinirli

- `attempt_plan[].date` yalniz `attempt1 | deadline`; `attempt1.rule` yalniz `last_business_day_of_week |
  nth_business_day(n)`. Diger bir brief "deneme 2: ikinci is gunu", "ay sonu son is gunu", "yarin" derse
  (diger denetcinin Turkce sentetik brief'i boyle: ertesi is gunu / ikinci is gunu / son tarih) deneme 2 tarihi
  ifade edilemez; dogrulayici reddeder veya deadline'a yuvarlanir. Brief A'da etkisiz.
- Cozum: `date` degerine `nth_business_day:<n>`, `last_business_day_of_week`, `last_business_day_of_month`.

## Dogrulananlar (bulgu degil)

- `now` her yerde `vars['now']` (gerekirse takma ad); `datetime.now/date.today/utcnow/time.*` hicbir snippette yok
  (regex taramasi, 15 snippet). `import` yalniz `datetime, json, re` (+ `zlib/base64` yalniz paketlenmis buyuk
  snippet; README'de 11 Ekim'de olculmus).
- Sozdizimi 3.6 gramerinde parse ediyor; walrus, match, `|` sozluk, `removeprefix` vs. yok;
  `fromisoformat` yalniz `YYYY-MM-DDTHH:MM` dilimiyle (3.7+).
- Tatil bos / hafta sonuna denk / N=0 / bant sinirlari (0,1,7,8,45,46,"7.0","08",None) dogru.
- Public beklenen degerler (PUB-04/07/10/13) referansla uyumlu.

## Test bosluklari (`tests/hack`)

`test_calendar.py` / `test_call_days.py`: yalniz {Sat,Sun}, tek tatil seti, 5 `now` gunu. Yok: {Fri,Sat}/{Sun}/{}
hafta sonu; bos tatil listesi; N=0; tatil blogu (CAL-4); `now` bicimleri (saniye/Z/ofset/bosluk/yalniz tarih);
CB sinirlari 09:59/10:00/19:00/19:01 x gun turleri; yil devri tarih ifadeleri; yururluk tarihli CFG (CAL-1);
bu kosulari kapsayan ozellik-tabanli (property) bir test ekleyin: `cal_ref.py`'yi `tests/hack/` altina tasimak
yeterli.

## Sirali ilk 5

1. **CAL-1** - Yururluk tarihli memo/kural degisikligi calisma zamani `now`'a gore uygulanmiyor (P1, kosullu;
   iki yan test gunu varsa kesin kayip, en buyuk patlama yaricapi).
2. **CAL-2** - Tarih/saat ayristiricisi yalniz Ingilizce; Turkce final ise tum tarihli senaryolar vague'a duser
   (P1 kosullu; "20 Ekim" saat 20:00 okunuyor).
3. **CAL-3** - Pzt-baslangicli hafta sabiti: hafta sonu Cuma-Cumartesi ise attempt-1 ve this/next yanlis (P2).
4. **CAL-11** - Deneme tarihi bicimleri yalniz `attempt1|deadline` (P2, spekulatif; "2. is gunu" ifade edilemez).
5. **CAL-4** - Bir hafta tamamen tatilse `attempt1_date` bos -> "on ?" ve promise yok (P3, ama gercek ve basit cozum).
   (Hemen ardindan CAL-5/CAL-6 `now` bicimi dayanikliligi: 2 satirlik duzeltmeler.)
