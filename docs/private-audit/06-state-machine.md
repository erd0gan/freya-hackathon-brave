# 06 - Durum makinesi mantik hatalari (kural-kural inceleme + rastgele/ozellik testi)

Tarih: 11 Ekim 2026. Kapsam: `builder/fb/nodecode/negotiation_lib.py` (cagri-zamani durum makinesi), `bodies.py`, `collections_lib.py`, `generate.py` (graf, hook'lar, extraction semalari), `harness/{bank,engine_sim,selftest}.py`. Model ve ag yok; tum kosular yerel banka (`bank.py`) ve gercek kod-dugumu govdeleri (`bodies.*`) uzerinde. Repo dosyalarina dokunulmadi; tum scratch `out/audit-scratch/` altinda.

## 0. Ozet (bir bakista)

- Cekirdek motor (`negotiation_lib.step` + `record_result` + `_finish`) saglam: 140.000 rastgele cagri (B: 80.000, A: 60.000, sozlesme-hatali, dar-metin mutasyonlu) + 20.000 spec-varyantli cagri (bant deneme sayisi 0-3, deadline 0-12 is gunu, human/stop/promise/floor/vague/hardship/notes_on_transfer/no_promise_reason knob'lari rastgele) sonunda **kod hatasi 0**, **close_call/sonuc tablosu/not/promise-gecerliligi/readback-eslesmesi/onaysiz-kayit ihlali 0**. Mevcut `selftest.py` oracle matrisi (1896 hucre, 4 spec) ve 13 public senaryo temiz.
- Gercek bulgular motorun *kenarlarinda*: (a) tek-etiketli `reply_kind` yuzunden kural onceligi (A32 > A41/A30) ifade edilemiyor, (b) karar yolundaki **yalniz Ingilizce regex'ler** (ozellikle `decline_check`) Turkce finalde ve hatta Ingilizce "No." icin reddi yutuyor, (c) birkac somut mantik hatasi (0-denemeli bant, "Yes, 420 on Friday" read-back dongusu, "hepsini odedim", kayitli urunun yeniden acilmasi), (d) sinirsiz dongu kalan 2-3 yol (soru-sadece, vague tekrar).
- SM-1, 3, 4, 5, 7, 8, 9, 10 icin duzeltme scratch'te yamalandi (`out/audit-scratch/patched.py`, 9 yama girisi): yamali motor ayni 1896 hucrelik matris + 13 public senaryoyu geciyor, rastgele/varyant kosularinda ilgili ihlal siniflari (I17, I24, SM-1/4/5 repro'lari) 0'a iniyor. SM-2 icin `decline_check` degisikligi 11 ornek metinde ayrica dogrulandi. Repo dosyalari degistirilmedi.

Ilk 5 (etki sirasi, ayrintisi 6. bolumde): **SM-2/SM-0** (Ingilizce-only karar regex'leri / Turkce), **SM-1** (hardship onceligi), **SM-4** (onay cumlesinde tutar/tarih tekrari -> read-back dongusu), **SM-5** (tum urunler icin "odedim/benim degil"), **SM-3** (0-denemeli bant + cok urun).

## 1. Yontem ve kapsam siniri

1. Kural matrisi (Brief A 1-47, Brief B 1-11 + kapanis): her kural -> kodda nerede -> testli mi (bolum 2).
2. `out/audit-scratch/sm_lib.py`: `engine_sim`'in daha zengin hali. Her tur icin ayni degiskenler (`reply_kind, said_date_text, said_amount_text, all_products, about_product, has_question`, `system.conversation`) gercek `bodies.DECIDE/AFTER_RECORD/AFTER_NOTE` ile calistirilir; arac hatalari `bank.faults` ile; her cagri sonunda ~25 degismez denetlenir (bolum 3).
3. Rastgele surucu `fuzz.py` (olay uretimi: 14 `reply_kind`, 18 tarih bicimi - hafta sonu/tatil/gecmis/vague/ISO/"next X", 15 tutar bicimi - taban alti/taban/tam/ustu/sozle/"200 or maybe 300", `product`, `all`, `has_question`, kesik cumle, "but not the loan", "Both of them.", "Is this a robot?"). `variants.py`: spec knob'lari rastgele.
4. Kimlik/uc-kisi/callback: gercek hook'lar ve `CB_CHECK` ile elle yazilmis 40+ senaryo (`idflow.py`, `cb.py`).
5. Hedefli sezgisel sorgular (`sc.py`, `show.py`): oncelik catismalari, tarih kenarlari, amount-in-pieces, vb.

Sinir: LLM'in etiketleme dogrulugu (`reply_kind` vb.), Freya'nin gecis/zaman semantigi ve gercek bankanin hata metinleri burada test edilemez. Graf-yonlendirmesi `test_invariants_flow.route()` ile ilk-eslesen-kosul varsayimiyla yurutuldu.

## 2. Kural matrisi

Kisaltmalar: **NL** negotiation_lib.py, **BOD** bodies.py, **COL** collections_lib.py, **SPL** speech_lib.py, **GEN** generate.py. Uygulama turu: **kod** (deterministik), **hook**, **graf+LLM** (extraction etiketi + yonlendirme), **prompt** (yalniz model talimati). Test: **SELF** = `harness/selftest.py` oracle matrisi + fuzz (build'de ve `test_coverage_matrix.py`'de kosar), **PUBxx** = `tests/hack/test_engine.py` public senaryosu, **T:x** = baska test dosyasi, **FZ** = benim rastgele kosum, **PR** = benim hedefli sorgum, **-** = test yok.

### Brief A

| # | Kural (ozet) | Nerede | Tur | Test | Not |
|---|---|---|---|---|---|
| 1 | Ilk cumle + kayit; yasak kelimeler | GEN `opening` tts (`lines.opening`), `forbidden` her on-dogrulama prompt'unda | prompt | T:test_invariants_guard/proof (dize varligi) | Sonraki turlarda yalniz model uyumu; transcript-judge |
| 2 | "Am I speaking with ..." | GEN `who_q` | tts | - | |
| 3 | Yalniz yes/own-name; 2. ask; unclear -> not_reached/unclear | GEN `opening/opening2` (`who1/who2`, max_turns 3/2), `fin_unclear` | graf+LLM | T:test_invariants_flow (yalniz human rotasi) | "hello/go ahead = unclear" sadece `who_desc` metninde. SM-6 |
| 4 | Robot/AI sorusu | `opening_task`, `id_task`, `neg_task` | prompt | - | |
| 5 | Ucuncu kisi: sebep soyleme, ne zaman ulasilir | GEN `tp_task`, `setup_tp/third_party` | prompt | - | |
| 6 | Callback penceresi (bugun/sonraki is gunu, 10-19 dahil, now'dan sonra) | COL `calendar_facts.callback_days`, BOD `CB_CHECK` | kod | SELF cb_cells, T:test_coverage_matrix, PR (`cb.py` 27 durum) | Cumartesi "today" belirsizligi SM-13 |
| 7 | Pencere disi -> bir kez daha sor -> no_callback | `CB_CHECK` (`cb_tries > reasks`), graf `cb_check` | kod | SELF, PR | |
| 8 | Yanlis numara | `tp_reply/who*`=wrong_number -> `fin_wrong_number` | graf+LLM | T:test_hidden2 (spec) | |
| 9 | Vefat: tek cumle, soru yok | `fin_deceased` | graf+LLM | T:test_hidden2 | |
| 10 | Akraba bilgi alamaz | `tp_task` | prompt | - | |
| 11 | Iki guvenlik sorusu, sirayla; DOB YYYY-MM-DD | GEN `identity_hooks` (pre: `normalize_dob/digits_from_speech`), `id_task` | hook+prompt | T:test_invariants_*, PR `idflow.py` | Eksik hane guard'i yok SM-11 |
| 12 | Uc yanlis toplam | banka + graf `locked` (`wrong_answers_left == 0`) | graf | PR (5 dizi) | |
| 13 | day_month_swapped hint, sayilmaz | post hook `what_to_do_next` | hook | PR | |
| 14 | 3 yanlis -> state yok, app/sube, not_verified/failed | `fin_failed` | kod | PR | Hata cumlesi iki kez soylenebilir (hook + fin_failed) - dogrulanmadi |
| 15 | Reddet: bir kez acikla; 2. red kapatir; "ne hakkinda" = red | `decline_check/decline_check2`, `identity2`, `fin_declined` | graf+LLM+regex | T:test_invariants_flow (kismi) | **SM-2** |
| 16 | Iki soru gecince get_account | graf `verified`->`account` | graf | PR | |
| 17 | Borcu soyle: urun adi, tutar, gun | NL `present_text` | kod | PUB-01.. | |
| 18 | Bant/deadline | COL `deadline_for/band_for` | kod | SELF, T:test_calendar | |
| 19 | Promise tarihi bugun+/is gunu/<=deadline | NL `_valid_day`, `_invalid_agreed` (son kapi) | kod | SELF, FZ I05 | 0 ihlal |
| 20 | Hafta sonu/tatil -> sonraki is gunu | NL `_apply_offer` + COL `check_promise_date` | kod | SELF, PR | |
| 21 | Vague: bir kez sor, sonra deadline | NL `vague_asked`, `vague_date_asks` | kod | PUB-07, PR | Tekrar-vague dongusu SM-9 |
| 22 | "next/this <weekday>" | SPL `parse_spoken_date` | kod | PR: 14 gun x 7 gun x 3 ifade = 294 kosul, 0 uyumsuzluk | |
| 23 | Deadline sonrasi -> deadline'i sun; red = flat refusal | NL `_apply_offer` (after_deadline) | kod | PR (D1,D3,D4) | |
| 24 | Once tam tutar; "ne kadar odersin" yok | `neg_task` + motor teklifleri | kod+prompt | FZ I09 (gecersiz teklif 0) | |
| 25 | Taban alani (urun tipine gore) | COL `product_facts.floor_field` | kod | SELF | |
| 26 | "Cok fazla" -> taban bir kez, "kalan borc" | NL `too_much` | kod | PUB-06, PR | |
| 27 | Tabandan az -> taban bir kez; kabul yoksa flat refusal | NL `_apply_offer` (floor_offers) | kod | PR (S3) | |
| 28 | Ilk soru: bugun | `present_text` | kod | PUB | |
| 29 | Tarih/tutar red degildir | `read_reply` (kind -> offer) | kod | PR | |
| 30 | Flat refusal/non-answer = bir deneme; attempt1 haftanin son is gunu; attempt2 taban/deadline | NL `_flat_refusal/_attempt_offer`, COL `attempt1_date` | kod | SELF ladder | **SM-3** (0 deneme + cok urun), SM-14 |
| 31 | Reddedilen teklif tekrarlanmaz | NL `_refuse_offer/_was_refused` | kod | PUB-10 | FZ I18: gercek ihlal yok (kalanlar kontrolcu artefakti, bkz 5) |
| 32 | Hardship: sempati, deneme yok, destek teklifi; kabul transfer; ret hardship; tarih/tutar yine kaydedilir; 30 ve 41'den once | NL `hardship`, `hardship_offer`, `_hardship_rest`, `_close_hardship` | kod+etiket | PUB-05, PR H1-H5, FZ I19 | **SM-1, SM-7, SM-8** |
| 33 | Indirim/plan/yeni sart yok | prompt'lar; motor yalniz brief'ten uretir | prompt | - | |
| 34 | Read-back + "Do you confirm?"; yalniz acik "yes"te create_promise | NL `confirm` fazi, `_readback` | kod | FZ I06/I06b/I07/I08: 140.000 cagri, 0 ihlal | |
| 35 | Onayda tarih/tutar degisirse yeniden dogrula + yeniden oku | NL `confirm` (offer/vague/too_much) | kod | PR | **SM-4** |
| 36 | Kayitli promise degismez; app | NL `closing` (`cannot_change`) | kod | T:test_hidden2 | **SM-7** (closing'den hardship ile cikis) |
| 37 | create_promise: 1 tekrar, sonra service_error | NL `record_result`, BOD `AFTER_RECORD`, `HELD_POST` | kod | SELF faults, FZ | 0 ihlal (I24b yalniz SM-7 yolunda) |
| 38 | "You will receive a text message ..." | `lines.recorded` | kod | PUB | |
| 39 | "I already paid" -> paid | NL paid/dispute dali | kod | PUB-08 | **SM-5** |
| 40 | Itiraz -> dispute | ayni dal | kod | SELF | **SM-5** |
| 41 | Plan talebi -> restructuring transferi | NL `restructure` | kod | PUB-09 | **SM-1** |
| 42 | Insan: once yardim sor, srarda general | NL `human`, `help_open`; GEN `pre_human*` (on-dogrulama) | kod | SELF, T:test_invariants_flow, PR Hu1-5 | |
| 43 | Scam sorusu | prompt'lar | prompt | - | |
| 44 | Bilmedigini bilmiyorum | `no_info` | prompt | - | |
| 45 | Iki kez "konusmak istemiyorum" -> refused / (on-dogrulama) declined | NL `stop` sayaci; GEN `identity` `declines` | kod+etiket | PR St1-3 | **SM-2, SM-6** |
| 46 | Tek close_call, en sonda | GEN `close_fn` (`alwaysRunsAt: end_of_call`), `CLOSE_PRE/HANGUP` | kod | FZ I01 (0 ihlal) | |
| 47 | Tesekkur + veda close'tan once | `final_say` | kod | PUB | |

### Brief B

| # | Kural | Nerede | Test | Not |
|---|---|---|---|---|
| B1 | 1-3 urun, her biri kendi alanlariyla | NL `new_state` | SELF multi | `about_product` indeksi atlanan urunde kayar, SM-15 |
| B2 | Her gec urunu say | `present_text` | PUB-11 | |
| B3 | Bant/deadline urun basina | `new_state` + COL | PUB-13 | |
| B4 | Once "hepsi bugun?"; sonra sirayla; her urun attempt 1 | NL `ask_today` + `one_by_one` | PUB-12 | |
| B5 | Deneme urun basina | `p["used"]` | SELF | **SM-3** |
| B6 | Tek tarih, bir urunun deadline'i sonrasi -> o urun kendi deadline'inda, read-back'te soylenir | NL `_apply_offer(targets)`, `own_deadline` | PUB-13, PR M6 | |
| B7 | Hepsini birlikte oku, tek onay, urun basina create_promise | NL `confirm`, `rec` kuyrugu | PUB-11, FZ | |
| B8 | Promise'siz her urune add_note (sebep) | NL `_finish` | SELF combos, FZ I14 (0) | |
| B9 | Bir urunde dispute/paid cagriyi bitirmez | NL paid/dispute + `_advance` | SELF combos | **SM-5** ("hepsini odedim") |
| B10 | Restructuring transferi; kayitlilar kalir, onayli-kayitsizlar dusmez->dusurulur; transferde hic not yok | NL `_transfer`, `notes_on_transfer` | SELF, FZ I12/I23 (0) | |
| B11 | Bir create_promise iki kez dusunce service_error notu | `record_result` + `_finish` | SELF | |
| Kapanis | promise > paid-hepsi > ilk non-paid (get_account sirasi) | NL `_finish` (`n` alani) | SELF, FZ I15 | Transfer-basarisiz kapanisi spec'te yok (SM-12) |

Matris sonucu (58 kural): yalniz prompt = A1, A4, A5, A10, A33, A43, A44 (7 kural, judge'a bagli, bu raporun disinda); graf/etiket/hook'a bagli = A3, A8, A9, A11-A13, A15, A16, A32, A45; geri kalani deterministik kod (B1-B11 ve kapanis dahil).

## 3. Degismezler (rastgele surucu)

| ID | Degismez |
|---|---|
| I00 | kod hatasi yok (`code_error`) |
| I01/I01b | tam bir `close_call`, en sonda, bos olmayan outcome |
| I02 | cagri biter (<= 166 olay) |
| I03-I05 | urun basina en fazla bir promise; tutar [taban, tam]; tarih bugun+, is gunu, <= deadline |
| I06/I06b/I07/I08 | create_promise yalniz `kind=yes` + `confirm` fazinda; once read-back; kaydedilen = en son read-back'teki (urun, tutar, tarih) |
| I09 | motorun soyledigi her teklif gecerli (tutar araligi, tarih kurali) |
| I10/I11/I20/I22/I23 | sonuc tablosu: promise varsa promise/transferred; transferred <=> transfer var; sebep enum'u; kuyruk=sebep; transferde not yok, transferden sonra arac yok |
| I13-I15 | urun basina bir not; promise'li urunde not yok; promise'siz her urunde not denemesi; paid-hepsi => paid, aksi halde ilk non-paid sebebi |
| I16 | "Your promise is recorded" yalniz ledger'da promise olan urunler icin |
| I17 | urun basina deneme sayaci <= bant deneme sayisi |
| I18 | musterinin reddettigi teklif (ayni tutar+urun+tarih) musteri yeni bir sey soylemeden tekrar sunulmaz |
| I19 | hardship'ten sonra, musteri tarih/tutar soylemeden "Could you pay ..." deneme teklifi yok |
| I24/I24b | basarili promise'ten sonra ayni urune tekrar create_promise yok; urun basina <= 2 deneme |

## 4. Fuzz istatistikleri

Tekrarlanabilir: `python out/audit-scratch/final_stats.py` (~2.5 dk) ve `python out/audit-scratch/variants.py 20000 5`.

| Kosum | Cagri | I00-I17 (ciddi) ihlal | I18 | I19 | I24/I24b | I15c | max olay | max state (bayt) |
|---|---|---|---|---|---|---|---|---|
| B (notes) duz, seed 101 | 40.000 | 0 | 15 | 49 | 3 / 1 | 128 | 48 | 2549 |
| B mutasyonlu metin, seed 102 | 40.000 | 0 | 184 | 52 | 3 / 2 | 144 | 45 | 2566 |
| A (tek urun) duz, seed 103 | 30.000 | 0 | 7 | 4 | 2 / 3 | - | 27 | 1508 |
| A mutasyonlu, seed 104 | 30.000 | 0 | 38 | 5 | 2 / 0 | - | 29 | 1500 |
| Spec-varyant (knob'lar rastgele), seed 5 | 20.000 | **I17: 2517** (hepsi SM-3) | - | - | 0 | - | - | - |
| Toplam | 160.000 | 2517 (tek sinif: SM-3) | | | | | | |

Not: kontrolcuyu gelistirirken ve yamalari dogrularken yapilan ek kosular (tahminen 250.000+ cagri) bu tabloya dahil degil; ayni sonuc siniflari cikti. Yamali yeniden kosular: B 20.000 + A 10.000 (mutasyonlu) + varyant 15.000 -> I17 ve I24 sifir.

Kalan I18/I19/I15c siniflarinin tek tek incelenmesi (yalniz hedefli/minimize edilmis ornekler):
- I18 (kalanlar): musteri ayni degeri kendisi yeniden soyluyor, ya da `about_product` o an masadaki teklifle ayni urunu gostermiyor (rastgele uretimin yapay urunu). Gercek ihlal degil.
- I19 (kalanlar): `_advance` musterinin *kendi* tarihine ait bekleyen "next business day" teklifini (`offer["say"]`) hardship'ten sonra da soruyor; bu persuasion degil, A32'nin "musteri kendisi soylerse kaydet" yolu. Gercek ihlal degil. Gercek I19 (stale `last_ask`) SM-8.
- I15c: transfer iki kez basarisiz olunca motor `no_promise/service_error` kapatiyor; B kapanis kurali "ilk non-paid sebep" der (ornegin p1 dispute). Spec'te basarisiz transfer tanimli degil; tasarim notu SM-12.
- I24/I24b: tek sebep SM-7.

## 5. Bulgular

Siddet: P0 = final'de dogrudan vaka kaybettirir/yaygin; P1 = muhtemel vaka kaybi; P2 = belirli ama makul senaryoda vaka kaybi; P3 = dusuk olasilik / tasarim notu.

### SM-0 (capraz-kesen, P0 Turkce final icin; baska denetcinin alani ile ortusur) - karar yolunda yalniz Ingilizce desenler

Karar veren regex'ler ve parser'lar Ingilizce: `ALL_WORDS, TODAY_REFUSAL, REFUSAL_WORDS, PART_WORDS, STOP_WORDS, REFUSE_ANSWER, ABOUT_WORDS, AMOUNT_WORDS, AMOUNT_IS, SAME_DAY, DAY_NUMBER, MOVE_WORDS, JOINERS` (NL ~714-1545), `parse_spoken_date` ve `normalize_dob` (SPL/COL), `_name_words` (`[a-z]`), motorun sabit cumleleri ve `say_date` (Ingilizce ay/gun adi). Olculen: `parse_spoken_date("cuma gunu"/"yarin"/"30 Ekim"/"ayin 30'u", ...)` hepsi `('', 'none')` -> `read_reply` `date_kind='vague'` -> motor "On which exact day will you pay?" sorup sonra deadline'i oneriyor (musterinin soyledigi gecerli gun kayboluyor). `parse_amount("420,5")` = 420.05 (Turkce ondalik virgul; 420.5 olmali), `"1.250,00"` = None, `"dort yuz yirmi lira"` = None. `normalize_dob("14 Mart 1988")` degismez doner (yerel banka `mar` onekiyle kabul ediyor, gercek banka bilinmiyor). Dil katmani yoksa tarih/tutar/ret/ayrisma karar yollarinin hepsi dusuyor. Bu raporun SM-2'si bu sorunun tek ve en keskin tezahuru. Oneri: (1) extraction'a tarih/tutari ISO/sayi olarak vermesini soylemek (model Turkceyi anliyor; motor ISO'yu `now` ile dogrular) ve regex'leri dil-bagimsiz ya da spec-parametreli (spec'teki dil sozlugu) yapmak; (2) sabit cumleleri `neg.lines`'a tasimak.

### SM-1 (P1) - Tek etiket: hardship ile plan talebi/ret/stop catismasi ifade edilemiyor (A32 > A30/A41)

Kural: A32 "bu kural 30 ve 41'den once gelir". `reply_kind` tek enum; `GEN reply_desc` (~1163-1180) hardship/restructure sirasi veya onceligi hakkinda bir sey demiyor. "I lost my job, I need lower instalments." mesaji model tarafindan `restructure` etiketlenirse `step` bunu dogrudan transfere ceviriyor (NL ~973, `hardship` blogundan once).

Tekrar (motor): tek urun (personal_loan 780/260, 21 gun), olay `{kind: restructure, text: "I lost my job, I need lower instalments."}`.
- Gercek: `transfer_to_agent(restructuring)`, close `transferred/restructuring`.
- Beklenen (A32): sempati + destek teklifi; kabul -> `transferred/support`; ret -> `no_promise/hardship`.
Brief C (stres) ayni onceligi ("Hardship takes precedence over a simultaneous payment-plan request") acikca yaziyor; gizli brief'lerde bulunmasi olasi.

Oneri: extraction'a ikinci bir enum (`also_hardship: yes/no`: mesaj ayrica hastalik/vefat/is kaybi geciriyor) + motorda `if also_hardship and kind in (restructure, no, too_much, evasive, stop): kind = hardship`. Brief'ten turetilmesi icin spec'e `precedence: [[hardship, restructure]]` listesi. Motor tarafi yamasi dogrulandi (`PATCHES["hardship_precedence_a/_b"]`): ayni olay -> `hardship_offer`; 1896 hucrelik matris ve public senaryolar temiz. Etiketleme yarisi (LLM) dogrulanamaz.

### SM-2 (P1; Turkce finalde P0) - `decline_check` reddi yutuyor

Konum: `bodies.decline_check` (BOD 349-359). Model `declines` etiketler, kod `real` saymak icin musterinin ham metninde Ingilizce `REFUSAL_WORDS/ABOUT_WORDS/STOP_WORDS/REFUSE_ANSWER` arar. Olculen `real`: "No." no, "Nope." no, "No thanks." no, "None of your business." no, "Forget it." no, "Hayir." no, "Bunu vermek istemiyorum." no, "Konusmak istemiyorum." no, "Hayir, soylemeyecegim." no (yes: "I won't give you that.", "I'd rather not say.", "Why do you need that?"). `real=no` -> `id1='unclear'` -> `identity` dugumune geri; dugum 60 tur (`ID_MAX_TURNS`) boyunca "yarim kalmis cumle" davranisi sergiler; musteri yanit verene ya da zaman asimina kadar surer. Beklenen: A15 "bir kez acikla, ikinci reddte kapat: `not_verified/declined`".

Tekrar: `identity` dugumunde musteri "No." (veya Turkce ret) iki kez: `fin_declined` yerine 60 tur dongu; gercek cagrida musteri kapatir -> `HANGUP` yolu `not_reached/unclear` (beklenen `not_verified/declined`).

Oneri (11 ornek metinde dogrulandi: 8 ret metni `real=yes`, 3 gevezelik/yarim cumle `real=no` kaldi): `real = ... or (not is_cut(raw) and (REFUSAL_WORDS or ABOUT_WORDS or len(raw.split()) <= 8))`: bitmis kisa cumle + model `declines` = gercek ret; "yarim cumle/uzun gevezelik" hala `unclear` (T1 arastirmasi korunur). Daha iyi: karar dili spec'e bagli (SM-0).

### SM-3 (P2) - Deneme sayisi 0 olan bant + cok urun: `_advance` attempt 1 uyduruyor

Konum: NL 622-624 (`if p["used"] == 0: p["used"] = 1` sonra `_attempt_offer`). `new_state` 0'i dogru okuyor (`att=0`), ama `_advance` ilk acik urun disindaki urunlerde bunu kontrol etmeden attempt 1 teklif ediyor (`used=1 > attempts=0`).

Tekrar: spec'te bant `attempts: 0`; iki urun (kart 3 gun, kredi 3 gun); olaylar `no, no`. Gercek: "Let us go through them one at a time. Could you pay 500.00 for the personal loan on Friday ...?" (ikinci urun icin persuasion). Beklenen: ikisi de dogrudan `refused`, tek soru. Spec-varyant fuzz'unda cagrilarin %12,6'sinda (2517/20000) goruldu. Tek urunde dogru (`_flat_refusal` yolu).
Oneri (dogrulandi): `_advance`'te `used==0` ise dogrudan `_flat_refusal` (denemeyi bant izin veriyorsa o alir, yoksa urunu reddeder). `PATCHES["zero_attempts"]`.

### SM-4 (P2) - Onay cumlesinde tutar/tarih tekrari read-back dongusu

Konum: `_yes_to_offered_day` (NL 795-818) yalniz *gun* (tutarsiz) eslesmesini "yes" sayiyor; tutar iceriyorsa `r.get("amount") is None` yuzunden False. "Yes, 420 on Friday." / "Yes, 420." / sozle "Yes, four hundred twenty on Friday." (A "in_words/pieces" stilleri) -> `read_reply` `kind=offer` -> `confirm` fazinda yeniden acma -> ayni read-back tekrar.

Tekrar: kart 420/126; olaylar: `no`, `yes`, `yes{date:Friday, amount:420, text:"Yes, 420 on Friday."}` x3. Gercek: her seferinde ayni "To confirm: ... Do you confirm?" (musteri her seferinde tekrar ederse sinirsiz; sozlu kolayca uzun). Beklenen: ilk cevap onay (tutar+tarih masadakiyle ayni) -> `create_promise`. Etki: gereksiz tur, judge "iki kez onay istedi", musterinin sabrinin tukenmesi; `in_words` stilinde sik.
Oneri (dogrulandi): `kind=="offer" and said_yes and phase in (offer, confirm)` iken tarih (varsa) masadaki gunle ve tutar (varsa) masadaki tutarla ayniysa `kind="yes"`; cok-urunlu read-back'te yalniz tek `agreed` urun ya da adlandirilmis urun icin. `PATCHES["yes_restates"]`.

### SM-5 (P2) - "Hepsini odedim / hicbiri benim degil" yalniz ilk urunu isaretliyor

Konum: NL 1075-1114: `p["status"] = kind` yalniz `t` urunu icin; `all_products` hic kullanilmiyor. Tekrar: 3 urun; olay `{kind: paid, all: true, text: "I already paid all of them."}`. Gercek: p1 `paid`, motor hemen p2 icin "Could you pay 640.00 for the personal loan on Friday 16 October 2026?" soruyor (ayrica "I have noted that" dispute icin ayni). Beklenen (B9 + A39): tum urunler `paid`/`dispute` -> `paid` kapanisi ya da `no_promise/dispute`; musteriyi her urunde tekrar iddia etmeye zorlamak yok. Etki: simule musteri "I told you, I paid" der ve urun urun tekrar eder; 3 urunde 3 tur + "talk to me about paying" hissi; judge ihlali; tur kaybi.
Oneri (dogrulandi): `r["all"] and not r["mixed"]` ise acik/agreed butun urunler ayni statuye. `PATCHES["paid_all"]`.

### SM-6 (P2) - Acilista "konusmak istemiyorum" etiketi yok (A45 on-dogrulama)

`who_values` (GEN ~1135) = confirms, someone_else, wrong_number, deceased, wants_human, question, unclear. "I do not want to talk." tek bile `unclear`, `opening2`'de yine `unclear` -> `fin_unclear` = `not_reached/unclear`. A45: "dogrulamadan once ayni musteri `not_verified/declined` kapanir". Kimlik fazinda `declines` etiketi (SM-2 kosuluyla) cozuyor ama acilista yok.
Tekrar: opening: "Stop calling me, I don't want to talk." x2. Gercek `not_reached/unclear`; beklenen `not_verified/declined` (A45; "ayni musteri" metni belirsiz oldugu icin P2).
Oneri: `who_values`'a `refuses`; rota `fin_declined`; "iki kez" sayaci `pre_stop_count`.

### SM-7 (P3, durustluk sinifi) - Kapanis fazinda hardship, kayitli urunu yeniden acabilir

Konum: NL 1044-1064: `hardship` dali `closing` fazini `hardship_offer`'a ceviriyor; sonraki "offer" kayitli urunu `_agree` ile `agreed`'e ceviriyor -> ikinci `create_promise` -> banka "already recorded" -> `HELD_WORDS` bunu *basari* okuyor -> motor "Your promise is recorded: loan 1994.00 on Thursday 15 October" diyor, bankadaki ise 2659.47 / 16 Ekim. Tekrar: tek urun; olaylar `no, offer, yes(confirm)` -> kayit; `hardship`; `offer{date: this Thursday, amount: 1994}`; `yes`. Beklenen: A36 `cannot_change`. Olasilik dusuk ama gercekleserse sahte "recorded" iddiasi (honesty).
Oneri (dogrulandi): `kind in (offer, vague, too_much) and pk["status"] == "recorded"` ise `cannot_change`. `PATCHES["no_reopen_recorded"]`. Ek: `HELD` yalniz *ayni* (urun, tutar, tarih) icin basari saymali.

### SM-8 (P3) - hardship_offer'dan sonra `last_ask` eski persuasion teklifi

NL 1061-1064: destek teklifi `st["say"]`'e yaziliyor, `st["last_ask"]` guncellenmiyor. `help_open -> yes` yolu `st["last_ask"]` soyluyor: hardship'ten sonra onceki "Could you pay 2855.91 ... Friday" tekrar soruluyor (A32/A31). Dogrulandi: `PATCHES["hardship_last_ask"]`.

### SM-9 (P3) - Vague tekrari sinirsiz

NL 478-493: ilk vague'e "exact day?", ikinciden sonra her vague "The latest date I can record is X. Could you pay ... on that day?" sinirsiz tekrar (7 olay = 7 ayni soru). Beklenen: deadline teklifi bir kez; ardindan vague = non-answer -> A30. Dogrulandi: `PATCHES["vague_loop"]`.

### SM-10 (P3) - Yalniz soru soran musteri sinirsiz tur

`kind=="question"` (NL 1115-1122) hicbir sayac artirmiyor; 12 ardisik soru -> 12 kez ayni LLM talimati, ilerleme yok. Gercek cagrida zaman/kod-veri siniri (SM-16) kesene kadar. Oneri: ardisik 5 sorudan sonra non-answer. Dogrulandi: `PATCHES["question_loop"]`.

### SM-11 (P2) - Eksik haneli telefon cevabi banka'ya "yanlis" olarak gider

`identity_hooks` `digits` turunde `_d or _a` gonderiyor; "four four seven" -> "447" yanlis cevap sayilir (yerel banka + A12 toplam 3). Prompt "eksik ise cagirma" diyor (yalniz model uyumu). `--style pieces` bunu zorluyor. Hook beklenen uzunlugu bilmiyor (spec `kind: digits` uzunluk tasimiyor). Oneri: spec `identity.questions[].length`; hook uzunluk uymazsa `answer='incomplete'` ve `id_incomplete` (DOB icin zaten var). Gercek bankanin "incomplete" telefonu saymayip saymadigi bilinmiyor: guvenli yol modele tool'u cagirtmamak (route/prompt) ve hook'ta son savunma olarak not.

### SM-12 (P3) - Basarisiz transfer kapanisi

`AFTER_TRANSFER`: iki basarisiz denemeden sonra `no_promise/service_error` + notlar (p1 `dispute` iken kapanis sebebi `service_error`). B kapanis kurali (ilk non-paid sebep) ile celisen ic tutarsizlik; spec basarisiz transferi tanimlamiyor. Oneri: `service_error` notunu yalniz henuz durumu olmayan urunlere yaz; kapanis sebebi B kuralina gore.

### SM-13 (P3) - Cumartesi/Pazar callback "today"

COL ~173-178: `business_days_only` varsayilani true -> hafta sonu "today" penceresi yok. A6 harfen "today or the next business day". Belirsiz; hafta-sonu cagri gunu icin test yok (final "birden fazla takvim gunu"). Spec bayragi (`callback.business_days_only`) mevcut; extractor'in A6'dan hangisini sectigi kontrol edilmeli.

### SM-14 (P3) - Hafta baslangici Pazartesi sabit

COL `last_business_day_of_week`: ISO hafta (Pzt-Paz). Pazar-Persembe is haftasi icin (stres brief C) "haftanin son is gunu" = Pazar (7 gun sonrasi) donuyor (19 Ekim -> 25 Ekim yerine 22 Ekim). `workdays` parametreli ama hafta siniri degil.

### SM-15 (P3) - Ayni tip iki urun ve atlanan urunde indeks kaymasi

(a) `product_names` ayni tipte iki urun icin ayni metni uretir ("personal loan" x2): `_named_in`, read-back ve teklifler ayirt edilemez. (b) `new_state` bos/eksik urunu atliyor (NL 138-141), `about_product` (`p1..p3`) `st["p"]` indeksi kabul ediyor ama `product_brief` atlanan urunu de listeliyor -> kayma. Gizli brief'te "kredi karti x3" olasi (final "kredi karti ekstre borcu").

### SM-16 (P3) - code_exec veri tavani, uzun cagri

`DECIDE` govdesi 28.026 bayt (`CODE_BUDGET=30.000` altinda, paketlenmiyor), `after_record` 23.842, `facts` 20.089. Tavan 131.072 bayt kod+veri; olculen veri ~1,3 KB/tur -> yaklasik 75-80 konusma turu (kimlik turlari dahil; `ID_MAX_TURNS=60`). Asildiginda (repo yorumundaki olcumden turetilmis tahmin, dogrulanmadi) `decide` hic calismaz, `neg_action` bayat kalir; kodun kendi `on_error`'u calismayabilir cunku hata platformdan gelir. Motor durumu 2,6 KB'ta sabit (FZ max 2566 bayt); buyume konusma gecmisinden. Oneri: konusma-yogun kimlik fazinda tur tavanini dusurmek ya da `decide` govdesini paketlemek (CODE_BUDGET'i dusurmek).

## 6. Oncelik siralamasi (ilk 5)

1. **SM-2 / SM-0** - Turkce finalde ret/tarih/tutar karar yollari Ingilizce-only; kimlik reddi kapanmiyor, tarihler `vague`'a dusuyor. Her "declines" ve her Turkce tarih vakasi.
2. **SM-1** - Hardship onceligi (A32): final'de "is kaybi + plan istiyorum" cok dogal bir cumle; transfer kuyrugu yanlis -> vaka kaybi.
3. **SM-4** - "Yes, 420 on Friday" onayi read-back'i tekrar ettiriyor; `in_words`/`pieces` stillerinde yaygin; 3/3 kuraliyla biriken risk.
4. **SM-5** - "Hepsini odedim / hicbiri benim degil": urun urun yeniden iddia; cok-urunlu "paid/dispute" vakalari.
5. **SM-3** - 0 denemeli bant: gizli brief'in "farkli sayilar"inda makul; cok urunde yanlis persuasion.

## 7. Oncelik catismalari - ozet (hedefli sorgular)

| Catisma | Motor davranisi | Karar |
|---|---|---|
| hardship vs restructure vs refusal | etiket belirler; onceliklendirme yok | SM-1 |
| "iki kez konusmak istemiyorum" | `st["stop"]` ardisik olmak zorunda degil, hardship_offer'da tek `stop` hardship kapatir; kimlik oncesi `declines`+`decline_check` | SM-2/SM-6; hardship_offer'da `stop` = ret (tutarli) |
| 3. yanlis vs swap hint | banka sayar/saymaz; `locked` yalniz `wrong_answers_left==0`; PR: "yanlis, yanlis, swap, dogru" 1 hak kala dogru | dogru |
| Kimlik oncesi itiraz | prompt-yalniz | belirsiz (brief'te yok) |
| Akraba odemeyi onerir | `tp_task` prompt'u | prompt-yalniz |
| Dogrulamadan once insan talebi | `pre_human` zinciri, sayac `pre_human_count` | dogru (PR) |
| Onaydan sonra tarih degisimi | `confirm`'de yeniden acilir, yeni read-back | dogru; SM-4 tekrar durumu |
| Iki urun ayni deadline | ozel durum yok | dogru |
| Insan istegi sonrasi "yardim" -> yes | soru tekrar edilir; `help_open` kapanir; ikinci istek transfer | dogru; SM-8 hardship_offer'da `last_ask` |

## 8. Spec degisirse (kisa)

Bant deneme sayisi, deadline N, `human/stop/promise_attempts/floor_offers/vague_date_asks`, hardship kipi, `notes_on_transfer`, `no_promise_reason` rastgele degisen 20.000 cagrida tek ihlal SM-3. Tabansizlik: `floor = full` -> "too much" dogrudan flat refusal (tutarli). `attempts=3` ve `attempt_plan` 2 adimli ise 3. deneme sessizce atlanir (`_was_refused`) - spec'in uc adim yazmasi gerekir. Hafta siniri SM-14.

## 9. Dosyalar

`out/audit-scratch/`: `sm_lib.py` (surucu+degismezler), `fuzz.py`, `final_stats.py` (+`.json`), `variants.py`, `patched.py` (9 yama girisi, `apply()`), `verify_patches.py` (regresyon: 1896 matris hucresi + 13 public senaryo, yamali/yamasiz), `sc.py`/`show.py` (hedefli sorgular), `cb.py`, `idflow.py`. Yamalar repo'ya UYGULANMADI.
