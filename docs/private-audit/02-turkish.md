# 02 – Türkçe dayanıklılık denetimi (TURKISH-LANGUAGE ROBUSTNESS)

Denetçi: 02-turkish. Tarih: 2026-10-11. Kapsam: build zamanı (spec_extract.py, generate.py, trace.py, build.py) ve çağrı zamanı (speech_lib.py, negotiation_lib.py, collections_lib.py, bodies.py). Hiçbir mevcut dosya değiştirilmedi, ağ çağrısı yapılmadı, commit yok. Tüm tablolardaki "gerçek çıktı" değerleri `out/audit-scratch/tr/t1.py ... t10.py` betikleriyle bu makinede (Python 3.12, locale cp1254) gerçek koddan alındı: `fb.nodecode.embed.namespace()` ile üç kütüphane code node'daki gibi yüklendi, `bodies.CB_CHECK` ve `generate.identity_hooks` kodları `exec` ile çalıştırıldı. Çalıştırma: `PYTHONUTF8=1 python out/audit-scratch/tr/tN.py` (t5.py bilerek PYTHONUTF8 olmadan çalışır).

## 0. Yönetici özeti

Final tek vaka ve Türkçe. Bu deponun çağrı zamanı kodunun neredeyse tamamı İngilizce. Sonuç: public 13 vakada 10/10 veren motor, Türkçe konuşan bir müşteriyle şu hâliyle üç ayrı yerden kırılıyor:

1. Ajan İngilizce konuşuyor (sistem prompt'u "Speak English", spec çıkarım prompt'u "plain English", motorun ürettiği ~60 cümle parçası koda gömülü İngilizce).
2. Müşterinin söylediği tarih ve tutarı okuyan saf-Python ayrıştırıcılar Türkçe bilmiyor. Her Türkçe tarih sessizce "vague" oluyor, Türkçe yazıyla söylenen tutar sessizce "tam tutar" oluyor, `1.250` (Türkçe binlik) `1.25` okunuyor.
3. Kimlik doğrulamada Türkçe kelimeli doğum tarihi ve son-4-hane cevapları normalleştirilmeden olduğu gibi bankaya gidiyor; "pieces" stilinin güvenlik ağı (eksik yıl koruması) Türkçede devre dışı.

Ek olarak Windows cp1254 ortamında `Trace.line` dosyaya yazarken `₺`, `→`, `≤` gibi karakterlerde UnicodeEncodeError ile build'i öldürebilir (TR-6). Bu en ucuz düzeltilen ama sonucu en ağır risk.

LLM'e devredilen yollar (yes/no/offer/hardship/dispute/human/deceased/wrong number sınıflandırması, robot/dolandırıcı sorusu, kişi eşleştirme) kodda İngilizce anahtar kelimeye bağlı değil ve Türkçe için en az risk taşıyor. Ama bu yolların çıktısını *denetleyen veya filtreleyen* İngilizce regex'ler var (decline_check, STOP_WORDS, REFUSAL_WORDS, ALL_WORDS, CB_DAY_WORDS) ve Türkçede sessizce hiçbir şey yakalamıyor.

## 1. Bulgular

Şiddet: P0 = Türkçe finalde vakaların büyük kısmını bozar; P1 = belirli müşteri stilinde bozar veya build'i öldürebilir; P2 = güvenlik ağını zayıflatır; P3 = küçük.

### TR-1 (P0) Konuşma dilini kimse belirlemiyor; sabit olarak İngilizce

- Kanıt: `builder/fb/generate.py:1230` sistem prompt'u `"You are the digital assistant of {bank}, on a phone call. Speak English. ..."`. Hiçbir dil alanı spec'te, SCHEMA'da veya `to_generator`'da yok (`grep -i "language|locale|turk"` builder/ altında 0 sonuç).
- Kanıt: `builder/fb/spec_extract.py:252` çıkarım prompt'u: `Sentences in "lines" are spoken to the customer: short, plain English. Where the brief gives exact words, use them exactly.` Türkçe bir brief'te modele "İngilizce yaz" deniyor; yalnızca brief'in tırnak içinde verdiği cümleler Türkçe kalır. Sonuç karışık dil: bazı satırlar Türkçe, çoğu İngilizce. `ENGINE_LINES` (spec_extract.py:1334) tarafsız yedekleri de İngilizce.
- Etki: `negotiate_say` düğümü `on_enter: tts "{{neg_say}}"` ile motor cümlesini kelimesi kelimesine söyler; `negotiate`, `identity`, `third_party`, `pre_human` düğümlerinde model "Speak English" talimatıyla İngilizce yanıt verir. Türkçe konuşan simüle müşteri İngilizce yanıt alır. Yargıç ve müşteri simülatörü bunu ne yapar bilinmiyor, ama "Türkçe final" ile açık çelişki; en iyi hâlinde "dil tutarsızlığı" ile kalma, en kötüsünde müşteri tepkisi bozulur ve akış ölür.
- Sabit İngilizce kaçaklar (spec `lines` Türkçe olsa bile değişmez): `generate.py:1739` "Sorry to have bothered you. Goodbye." (yanlış numara yolu), `1744` `(L.get("deceased")) + " Goodbye."`, `1768` "I am sorry, we have a technical problem.", `939` goodbye varsayılanı, `938` no_info, `936` ai_answer ("I am {bank}'s digital assistant."), `1434` help_first, `1514` transfer_human; `bodies.py:113,124,128,162,202,236` ("Goodbye.", "Sorry, could you say that again?", "I am sorry, I could not connect you."); kimlik hook'larındaki `what_to_do_next` talimatları (`generate.py:637-665,703-706`) İngilizce talimat (model'e talimat olarak sorun değil ama dil belirten bir şey yok).
- Genel düzeltme: spec'e `language` alanı (brief'ten LLM ile, ör. "tr") ekle; `sys_prompt` ve spec prompt'u bu dile göre "Speak {language}" desin; sabit cümleleri koddan çıkar (TR-2).

### TR-2 (P0) Motorun ürettiği cümleler İngilizce, koda gömülü; tarih ve para da İngilizce biçimde

- Kanıt `negotiation_lib.py` (satır numaraları): 203-225 `present_text` ("Your … has … past due and is … days late."), 239 SORRY listesi, 244 CUT_SORRY, 268 "Can you do that?", 366 "Could you pay … for … on …?", 454-470 "The latest date I can record is…", "I cannot record …. The minimum is …", 482 "On which day can you pay …?", 492, 512-531, 567 "To confirm: …", 579 "I have noted …", 907 "Please go on.", 1073 "I understand.", 1182, 1268 "Sorry, I did not catch the amount. Which amount do you mean?", 1416-1427 "Your promise is recorded: …", "I could not record the promise for …". `spec.lines` yalnızca ~25 anahtarı (ENGINE_LINES) değiştirebiliyor; yukarıdaki yaklaşık 40 parça hiçbir anahtarla değiştirilemez.
- Kanıt `collections_lib.py:8-22,36-37`: `WEEKDAYS`/`MONTHS` İngilizce; `say_date` "Friday 23 October 2026" üretir. `collections_lib.py:256` `money()` çıplak "1250.00" döndürür: para birimi (TL/lira) hiçbir cümlede söylenmiyor, ondalık noktalı (Türkçede nokta binlik ayracı: "1250.00" okuması belirsiz).
- `_the(name)` (negotiation_lib.py:228) Türkçe ürün adının başına İngilizce "the" koyar.
- Gerçek çıktı (t4.py, `product_names` Türkçe şablonla): `Your 4471 ile biten kredi kartı has 1250.00 past due and is 10 days late.` ve `Could you pay 200.00 for the kredi kartı on Friday 16 October 2026?`. t2.py: `To confirm: the credit card ending 4471, 1250.00 on Friday 23 October 2026. Do you confirm?`
- Etki: P0 (TR-1 ile aynı sonuç, ayrı kök neden: spec'i Türkçeleştirsek bile motor İngilizce konuşur).
- Düzeltme (genel): her cümle parçasını `neg.lines` altında yer tutuculu şablon yap (`{amount} {currency} {product} {date}`); builder, brief dilinde bu şablonları LLM ile build sırasında üretsin; yer tutucuların korunduğunu ve `format` ile örnek değerlerle render edildiğini deterministik doğrulasın. `say_date` ve `money` dil paketinden (hafta günü/ay adı tablosu, binlik/ondalık, para birimi) beslensin; tablo spec'ten veya LLM'den build'de gelebilir.

### TR-3 (P0) `parse_spoken_date` Türkçe bilmiyor: her Türkçe tarih "vague" olup son tarihe düşüyor

- Kanıt `speech_lib.py:8-22,300-452`: ay/gün/sayı/sıra sözlükleri yalnızca İngilizce; `_month_of`, `_WDAYS`, `tomorrow/today`, "next/this", "week" tamamen İngilizce. `negotiation_lib.py:1561-1563` (`read_reply`): ISO boşsa ve metin boş değilse `dk = "vague"` yapılır, `kind` "offer" ise "vague"a döner.
- Gerçek çıktı (t1.py, bugün Çarşamba 2026-10-14): çalışanlar yalnızca `23.10.2026`, `23/10`, `23.10`, `2026-10-23`. Şunların hepsi `('', 'none')`: "on beş Ekim", "15 Ekim", "ayın on beşi", "ayın 15'i", "önümüzdeki cuma", "haftaya salı", "bu cuma", "cuma", "cuma günü", "yarın", "bugün", "ay sonu", "ayın sonunda", "yirmi üç ekim", "23 Ekim Cuma", "27 Ekim", "iki hafta sonra", "yarından sonra", "öbür gün", "yarın değil cuma", "pazartesi değil salı" (olumsuzlama bile okunmaz).
- Motor düzeyinde (t2.py): müşteri "Haftaya salı öderim." (reply_kind=offer) → `kind=vague` → ajan "On which exact day will you pay?" der; müşteri tekrar aynı şeyi söyler → ajan "The latest date I can record is Friday 23 October 2026. Could you pay 1250.00 on that day?" ile son tarihi dayatır. Müşteri "bu cuma olur" dediğinde de aynı: `T11` 'bu cuma' → vague → tekrar sorma. Kabul edilirse kayıtlı söz tarihi müşterinin söylediği tarih değil son tarih olur: sonuç (promise date) yanlış → vaka kaybı. Reddedilirse "vague_date_asks" sayacı bitince döngü sona erer ama yine yanlış tarih.
- Etkilenen kullanım: hidden müşterilerin "in_words" ve "talkative/corrects" stilleri ve gün adıyla/ay adıyla tarih veren her müşteri. Sadece "bugün/evet" yolu (tam tutar, bugün) ve sayısal `dd.mm.yyyy` korunur.
- `parse_spoken_time` (speech_lib.py:601): "saat 11" ve "14:30" çalışır; "saat on bir civarı", "öğleden sonra iki", "on bir buçuk", "sabah dokuzda", "akşam beşte", "yarın saat on" → `''` (t1.py). Üçüncü taraf geri arama akışı da etkilenir (TR-8).
- Düzeltme: (a) dil paketi: Türkçe ay/gün/sayı kelimeleri, göreli ifadeler (bugün, yarın, öbür gün, haftaya, önümüzdeki, gelecek, bu, ay sonu, "ayın N'i/N'inde", "N gün/hafta sonra"), ASCII katlama (TR-9); (b) dilden bağımsız yedek: çıkarım şemasına `said_date_iso` ekle (modele prompt'ta `date_table` verilir; `calendar_facts` zaten üretiyor ama hiçbir prompt'ta kullanılmıyor: `grep date_table` yalnızca collections_lib.py), motor ISO'yu kendi `check_promise_date` ile doğrular ve `parse_spoken_date` sonucu boşsa onu kullanır.

### TR-4 (P0) `parse_amount`: Türkçe sayılar sessizce yok sayılıyor, `1.250` = 1,25

Gerçek çıktı (t1.py, `parse_amount`):

| girdi | gerçek | beklenen |
|---|---|---|
| `1.250` | 1.25 | 1250 |
| `1.250 TL` | 1.25 | 1250 |
| `2.500 lira` | 2.5 | 2500 |
| `12.500` | 12.5 | 12500 |
| `1.250,50 TL` | None | 1250.50 |
| `2.500,75` | None | 2500.75 |
| `1250,5` | 1250.05 | 1250.50 |
| `dört yüz yirmi lira` / `bin iki yüz elli` / `yüz elli lira kırk kuruş` / `iki bin` / `yüzelli` / `bin lira` | None | 420 / 1250 / 150.40 / 2000 / 150 / 1000 |
| `yarısı`, `%50` | None | (belirsiz, tekrar sorulmalı) |
| `420 lira`, `420 TL`, `1250,50 TL`, `650 TL'yi`, `1250` | doğru | doğru |

- Kök neden 1: satır 534 `\d\.\d{3},\d` yalnızca "1.250,50"yi reddeder, "1.250" ise ondalık sayı sanılır (satır 537 virgül kuralı yalnız `(\d),(\d{2})`). Türkçede nokta binliktir.
- Kök neden 2: sayı kelimeleri (`_UNITS/_TENS`) İngilizce; `_CUR/_SUB` içinde TL/lira/kuruş yok; `AMOUNT_WORDS` (negotiation_lib.py:1529) de İngilizce olduğundan `bad` bayrağı Türkçe kelimeli tutarda **yanmıyor** (t3.py: "dört yüz", "yarısı", "yüzde elli" hepsi False). Yani motor "tutar okunamadı, tekrar sor" yoluna bile girmiyor.
- Motor etkisi (t2.py): T4 müşteri "Dört yüz yirmi lira öderim." (`offer`) → `amount=None, bad=False` → ajan `On which day can you pay 1250.00?` (müşterinin söylediği tutar yok sayılıp tam tutara dönülüyor). T8 pieces "bin iki yüz" + "elli" → okuma-onayında "1250.00". T5 `1.250 TL` → "I cannot record 1.25. The minimum is 200.00." (tam tutarı kabul eden müşteri reddedilmiş sayılıyor). T6 `1.250,50 TL` → `amount_bad` → "Which amount do you mean?" döngüsü; 3. turda `evasive`.
- `negotiation_lib.py:1448/1575` `said_amount` yedek yolu ölü: `generate.py` extraction şemasında `said_amount` değişkeni tanımlı değil (yalnızca `said_amount_text`, satır ~1196). LLM sayısal değeri vermiyor.
- Düzeltme: dil paketli `parse_amount` (Türkçe: "." tam 3 haneyle biten grup = binlik, "," = ondalık, `TL/₺/lira/kuruş`, sayı kelimeleri + "yüz/bin/milyon", "buçuk"); `AMOUNT_WORDS` ve `_UNITS` aynı paketten; ayrıca `said_amount` (rakamla) çıkarım değişkeni ekle ve lib sonucu `None` iken yalnızca lib/LLM tutarlıysa kullan. Sessiz "tam tutar" yedeğini kaldır: tutar metni dolu ama okunamıyorsa `bad=True` olsun.

### TR-5 (P0/P1) Kimlik cevapları: Türkçe kelimeli doğum tarihi ve son-4 normalleştirilmeden bankaya gidiyor; "pieces" güvenlik ağı Türkçede devre dışı

- Kanıt `collections_lib.py:371-449` (`_WORD_MONTHS` İngilizce, `_dob_words` İngilizce sayı kelimeleri), `speech_lib.py:663-704` (`digits_from_speech` İngilizce), `generate.py:512-572` (pre hook).
- Gerçek çıktı (t9.py, pre hook çalıştırıldı, "sent" = tool'a giden `answer`):
  - `14.03.1988` → `1988-03-14` (iyi); `14 Mart 1988` → `14 mart 1988` (küçük harfe çevrilmiş ham metin); `on dört mart bin dokuz yüz seksen sekiz` → aynen ham; `14 Mart bin dokuz yüz` → ham; `14 Şubat/Nisan/Haziran/Temmuz/Ağustos/Eylül/Ekim/Kasım/Aralık/Ocak 1988` hepsi ham (t1.py); yalnızca `14 Mayıs 1988` doğru çıkıyor (tesadüf: `mayıs` içinden `may` parçası yakalanıyor).
  - `sıfır sıfır yedi üç` → ham Türkçe metin (beklenen `0073`); `yetmiş üç` → ham; `0073` → iyi; İngilizce karşılığı `zero zero seven three` → `0073`.
  - Eksik yıl koruması (`_ld in ('19','20')`, `incomplete`) Türkçede hiç tetiklenmez: "14 mart bin dokuz yüz" ham gönderilir. İngilizce `14 March nineteen` → `incomplete`.
- Etki: tool'un `answer` alanı için `field_notes` "customer's own words, complete" dediği için LLM'in kendi dönüşümüne güveniliyor. LLM `0073` yazarsa sorun yok; ham Türkçe yazarsa banka (bizim mock'umuz dahil, sözleşmeye göre ISO/rakam bekliyorsa) yanlış cevap sayar → `wrong_answers_left` düşer → `fin_failed` (kimlik başarısız). Final müşterilerinin "pieces" ve "in_words" stilleri tam bu yolu vurur. Ağır risk: kimlik sonrası tüm akış kaybolur.
- Düzeltme: TR-3/TR-4 ile aynı dil paketi `normalize_dob`/`digits_from_speech`'e uygulanmalı; `_dayish` regex'i (generate.py:543) dil paketinden ay adlarıyla beslenmeli (Türkçede "mart" içindeki `mar` zaten yanlışlıkla "dayish" sayılıyor). Mock tarafı: Türkçe aylarla gelen cevabı da çözecek şekilde yazılması bir sigorta olur.

### TR-6 (P1, felaket sonuçlu) `Trace.line` Windows cp1254'te çöküyor; `₺`, `→`, `≤`, `✓` hataya yol açar

- Kanıt `builder/fb/trace.py:38,45`: `path.write_text("")` ve `self.path.open("a")` kodlama belirtmeden açılıyor; bu makinede `locale.getpreferredencoding() = cp1254`, `PYTHONUTF8` yok. t5.py çıktısı: `→` (U+2192), `≤`, `✓`, `₺` (U+20BA; cp1254 bunu bilmiyor) için `UnicodeEncodeError: 'charmap' codec can't encode character`. Türkçe harfler (çğıöşüÇĞİÖŞÜ), `’ “ ” … –` sorunsuz.
- Hangi satırlar tehlikeli: `spec_extract.py:1700` `_short()` `json.dumps(..., ensure_ascii=False)` (observer "uncertainty:" satırları), `trace.note("spec decision: " + str(n)[:300])` (modelin notları: Türkçe brief'te "₺" ve "→" çok olası), `spec_summary` satırları. `build.py:501` ve `freya.export_workflow` `json.dumps` varsayılanıyla ASCII-kaçışlı yazdığı için güvenli; `build.py:158/492` `encoding="utf-8"` ile okuyor.
- Etki: istisna `run()` içinde kalırsa `main` `finish(..., status="error: UnicodeEncodeError")` ile bitirir; workflow hiç yayınlanmaz, aynı koşu yeniden denemede aynı sonucu verir. Final koşusu aynı Windows makinesinde yapılacaksa gerçek risk. Stderr yazımı güvenli (backslashreplace).
- Düzeltme: `Trace.__init__` ve `line`'da `encoding="utf-8"`; ayrıca çalıştırma komutuna `PYTHONUTF8=1` (veya `PYTHONIOENCODING=utf-8`). Anında, kod değişikliği gerektirmeyen önlem: final komutunu `PYTHONUTF8=1 python builder/build.py ...` olarak çalıştırmak.

### TR-7 (P1) İngilizce regex'lerin LLM etiketini denetleyen/filtreleyen yolları Türkçede sessizce hiçbir şey yakalamıyor

Gerçek çıktı (t3.py), tüm Türkçe girdiler `False`:

| regex (negotiation_lib.py) | örnek Türkçe girdi | sonuç |
|---|---|---|
| `STOP_WORDS` (1511) | "Seninle konuşmak istemiyorum.", "Beni arama.", "Kapatıyorum", "İlgilenmiyorum" | False |
| `REFUSE_ANSWER` (1517) | "Bunu söylemeyeceğim.", "Vermeyeceğim.", "Cevap vermeyeceğim" | False |
| `ABOUT_WORDS` (1523) | "Bu ne hakkında?", "Neden arıyorsunuz?", "Siz kimsiniz?" | False |
| `REFUSAL_WORDS` (1500) | "Bugün ödeyemem", "Ödemiyorum", "Hayır" | False |
| `ALL_WORDS` (1488) | "Hepsini cuma öderim", "İkisini de" | False |
| `TODAY_REFUSAL` (1493) | "Bugün ödeyemem" | False |
| `AMOUNT_IS` (1536) | "asgari tutar", "tamamını" | False |
| `SAME_DAY` (1544) | "o gün", "aynı gün" | False |
| `DAY_NUMBER`/`MOVE_WORDS` (715/723) | "ayın on beşi"/"önümüzdeki cuma" | False |

Sonuçlar:
- **decline_check** (`bodies.py:349-359`): LLM `declines` dese bile `real` yalnızca bu regex'lerle onaylanır. Türkçe "Bunu söylemeyeceğim." / "Cevap vermeyeceğim, seninle konuşmak istemiyorum." / "Hayır, vermem." → `decline_real = no` → etiket `unclear`'a düşürülür (t3.py son 4 satır). Kimlik sorusunu reddeden Türkçe müşteri hiçbir zaman `declined` ile kapanmaz; ajan soruyu tekrar sorar, `ID_MAX_TURNS=60` (generate.py:54) veya çağrı süre sınırına kadar döngü. Yalnızca "metin yoksa" (`raw` boş) durumunda onaylanır.
- **mixed / product** (read_reply 1591-1594): "Kartı ödeyeceğim ama krediyi ödemem" → `mixed=False`; çok ürünlü vakada "evet" tüm ürünlere uygulanır (marjinal: final "up to three products", ama konu kredi kartı ekstre borcu; tek ürün olabilir).
- `all_products` hâlâ LLM'den gelir; `ALL_WORDS` yalnızca düzeltme yolu.
- Kesik tur mantığı (`is_cut`, 1548) dilden bağımsız ama "Evet" noktasız → `is_cut=True`; "Hayır" noktasız ve tarih/tutar yoksa `no`→`unclear` (cut_kind) olur; ardışık 3. turda kabul edilir. İngilizce "No" ile aynı davranış, Türkçeye özel değil; yalnız not.
- Düzeltme: `decline_check`'i İngilizce regex yerine dilden bağımsız kanıta bağla (LLM etiketi + `not is_cut(raw)`), veya çıkarım şemasına `refusal_in_message`/`wants_to_end` gibi LLM bayrakları ekle; regex'ler dil paketine taşınsın.

### TR-8 (P1) Üçüncü taraf geri arama (third_party) Türkçede randevu alamıyor

- Kanıt `bodies.py:256-332` `CB_CHECK`: `CB_DAY_WORDS` İngilizce; `parse_spoken_date`/`parse_spoken_time` Türkçe bilmiyor. Gerçek çıktı (t7.py, now=Çarşamba 11:00):
  - ("yarın", "saat 15:00") → `cb_state=partial`, `cb_say="Ask for the missing day."` (gün söylendi ama okunamadı: ajan günü tekrar ister).
  - ("yarın","öğleden sonra üç"), ("yarın","saat on bir"), ("bugün","saat on beş") → `cb_state=none`, `cb_say=''`.
  - ("perşembe","saat 3") → partial (gün okunamıyor).
  - ("", "saat 15:00") → `ok`, 2026-10-14 15:00 (gün yoksa ilk uygun gün; yalnız bu durumda çalışır).
  - İngilizce ("tomorrow","15:00") → ok, 2026-10-15.
- Etki: üçüncü kişi "yarın öğleden sonra arayın" derse tur sayacı (`CB_MAX_TURNS=6`) dolana kadar tekrar sorulur, sonunda `fin_no_callback`. Callback bekleyen vakalar ("someone_else") kaybedilir.

### TR-9 (P2) Büyük/küçük harf, Unicode normalizasyonu ve `\w`/`\b` kuralları Türkçeye uygun değil

Gerçek çıktı (t4.py):
- `'İ'.lower()` = `'i̇'` (2 kod noktası), `'I'.lower()` = `'i'` (Türkçede `ı` olmalı), `'ı'.upper()` = `'I'`, `'KREDİ KARTI'.lower()` = `'kredi̇ karti'`. Kod boyunca `.lower()` ile eşleşme yapılıyor (`read_reply`, `_named_in` 739, `parse_*`, `spec_extract.grounding` 1220, `_grounded` 1685, smoke test `build.py:~266`, `proof._norm`).
- NFD: `"yarın cuma günü öderim şimdi"` NFD'de 32 karakter ve NFC'ye eşit değil; hiçbir yerde `unicodedata.normalize` yok. `\b` sınırı birleştirici işaretlerde (Mn) kırılır.
- `[a-z]` sınıfları Türkçe harfleri atar: `_name_words` (negotiation_lib.py:732) `ihtiyaç` → `ihtiya`; `norm()` (generate.py:227) `re.sub(r"[^a-z0-9]","")`: `doğum_tarihi` → `doumtarihi` ≠ `dogum_tarihi` (t8/üstteki deneme).
- Ek (agglutination): `\b` ile kelime eşleştirme çekim eklerinde başarısız: `_named_in` (t4.py) "kartı öderim", "kartım", "krediyi" → `[]`; "Kredi kartını", "KREDİ KARTI" → `[0]` (kısmen tesadüfen çalışıyor). Zorunlu yasaklı kelime listesi ve smoke testi (`build.py:~268`, `\b borç \b`) "borcunuz" (ç→c yumuşaması) ve "borçları"yı yakalamaz.
- Düzeltme: tek bir `fold()` yardımcı: NFKC, `İ→i`, `I→ı` Türkçe-farkında küçültme, sonra ASCII'ye katlama (`ı→i ş→s ğ→g ü→u ö→o ç→c`) hem müşteri metnine hem sözlüklere; eşleştirmede kök/önek (`startswith`) kullan, `\b` yerine.

### TR-10 (P2) `spec_extract.grounding` Türkçe brief'te işe yaramıyor (ve fazladan LLM turu harcıyor)

Gerçek çıktı (t6.py, Türkçe brief parçası):
- `_date_in` (spec_extract.py:1188) yalnızca İngilizce ay adı öneki arıyor: "29 Ekim 2026", "23 Nisan", "30 Ağustos", "1 Ocak 2027", "10 Kasım" → **False** (rakamsal `29.10.2026`, `29/10/2026` → True; "1 Mayıs" → True tesadüfen `may`). Yani Türkçe brief'te yazıyla yazılmış her tatil "brief'te bulunamadı" sayılıyor; tam tersine uydurulmuş bir tatil de aynı mesajı alır: kontrol bilgi taşımıyor.
- `_number_in` (1180): "on beş iş günü", "yedi iş günü", "otuz", "iki", "üç" → False (yalnızca rakam). Band sayıları kelimeyle yazılmışsa hepsi "not found".
- `_callback_business_days` (1204) İngilizce ("callback", "business day"); Türkçede "geri arama ... iş günlerinde" → False; `business_days_only` düzeltmesi hiç devreye girmez.
- Saat kontrolü (1233-1239): "saat 10.00", "10.00 - 19.00", "10'dan 19'a kadar", "akşam 7'ye kadar" → False; yalnız `10:00` biçimi.
- Etki: `extract()` her seferinde "repair" çağrısını tetikler (30 dk bütçeden zaman/maliyet), spec'e karşı güvence sağlamaz. Final sırasında kritik kural ("tatilde söz kabul etme") için tek deterministik güvenlik ağı zayıflıyor. Bloklamaz (yalnızca uyarı).
- `quotes_found` (spec_extract.py:1461) yalnızca ASCII `"` tırnaklı cümleleri sayar; Türkçe “ ” veya « » kullanılırsa brief'in zorunlu cümleleri izlenmez (yalnız trace).
- Düzeltme: kanıt tabanlı doğrulama: LLM her değer için brief'ten birebir alıntı (`evidence`) versin; doğrulama `fold(alıntı) in fold(brief)` olsun (dil bağımsız). Ay adları için Türkçe tablo yine eklenebilir.

### TR-11 (P2) Türkçe stringlerin code node kaynağına gömülmesi ve smoke testin İngilizce olması

- `embed.config_literal/config_block` `ensure_ascii=False` ile CFG'yi code node kaynak koduna gömer; `bodies.finish_pre` `{say!r}` ile Türkçe harfleri kaynak koduna yazar. Freya code_exec sandbox'ı UTF-8 kaynağı kabul ediyor mu bu depoda ölçülmemiş (pratik vakalarda yalnız `£`/`—` benzeri; bkz. test_northwind). Bayt sınırı (`CODE_BUDGET`) UTF-8 bayt sayısıyla ölçülür; Türkçe harf 2 bayt.
- `build.py` smoke testi tek İngilizce "Hello?" turu ve `title: "Ms"`; çıkış kodu düğümlerini veya Türkçe akışı hiç çalıştırmaz. `fb.harness.selftest` / `engine_sim` yanıtları da İngilizce (`reply_vars`: "this Friday"). Yani Türkçe için build-time hiçbir öz-test yok; TR-3/4/5/8 build sırasında görünmez.
- Düzeltme: spec'in diline göre Türkçe örnek yanıtlarla (`yarın`, `cuma`, `dört yüz yirmi lira`, `1.250,50`) çalışan bir selftest ve bir chat smoke turu ("Evet benim" gibi) ekle; sandbox UTF-8 kabulünü ilk koşuda `chat/message` ile doğrula.

### TR-12 (P3) Küçük bulgular

- `parse_now` (collections_lib.py:25) yalnızca ISO okur: `14.10.2026 11:00`, `14/10/2026 11:00`, `Çarşamba, 14 Ekim 2026 11:00` → `None` (calendar_state `unknown`). Pratikte `now` ISO ("2026-10-14T11:00"); `...Z` ve `+03:00` ek kısmı atılıp duvar saati olarak kullanılıyor (UTC/İstanbul farkı 21:00 UTC sonrası gün kaydırır).
- `to_float` (collections_lib.py:247) Türkçe biçimli API değerini reddeder (`"1.250,00"`, `"₺1.250,00"`, `"1250,5"` → ValueError); `new_state` bu ürünü sessizce atar ("no_account" bitişi). Sözleşme sayı döndürüyorsa sorun yok.
- `HELD_WORDS`/`NOT_HELD_WORDS`/ID_DOWN hata sözcükleri (bodies.py:15-44, generate.py:692) İngilizce; mock Türkçe hata mesajı döndürürse "zaten kayıtlı" tekrarı "başarılı" sayılmaz, servis hatası ayrımı yapılmaz. Mock İngilizce `error` döndürürse sorun yok.
- `validate.IDENT` (`^[A-Za-z_]...`) ve `norm()` Türkçe harfli değişken/alan adlarını eler; API alanları ASCII ise sorun yok. Diakritikli enum değerleri (`ödeme_sözü`) yalnız tam eşleşmede güvenli; `match_enum` bulanık eşleşmede `ödeme_sozu` ↔ `ödeme_sözü` gibi durumlarda `norm` yüzünden yanlış sonuç verebilir.
- Hitap: `who_name = "{{title}} {{last_name}}"` (generate.py:919) yalnızca son-ad önünde unvan kurar. Türkçe "Ahmet Bey"/"Yılmaz Hanım" sonek hitapları ve "-i hâli" ("Ahmet Bey'i arıyorum") şablonla üretilemez; cümle spec `who_question`'dan gelir (LLM'in brief'ten yazdığı), yani brief'in biçimine bağlı. Bay/Bayan değişken değeri olarak gelirse çalışır.
- `lines` içindeki `opening` uyarısı `"recorded" in opening.lower()` İngilizce (spec_extract.py:309).
- `forbidden_before_verify` küçük harfleme (`policy_fields` 1654) "İ" → `i̇` (birleştirici nokta) yapar; prompt'ta görsel olarak aynı ama smoke testteki `said.lower()` ile eşleşme "icra" vs "İcra" için tutarsız.

## 2. Kod yolu haritası: İngilizce-only mi, LLM'e mi devredilmiş?

| Girdi sınıfı | Karar veren | Türkçe sonucu |
|---|---|---|
| evet/tamam/olur/doğru/onaylıyorum/peki vs hayır/istemiyorum/yok (reply_kind) | LLM (extraction `reply_desc`) | Kodda anahtar kelime yok; LLM'e bağlı (test edilemedi). Noktasız "Evet"/"Hayır" `is_cut` (dilden bağımsız). |
| Robot/AI sorusu, "dolandırıcı mısınız" | LLM (`has_question`, `neg_task` ai/scam satırı) | Yanıt metni spec `lines`'tan; spec İngilizce yazarsa İngilizce (TR-1). |
| Kapat/görüşmek istemiyorum (stop) | LLM etiketi + `STOP_WORDS` yalnız kesik turda | Kesik Türkçe tur `stop` yerine `unclear` olur (TR-7). |
| Kimlik reddi | LLM `declines` + `decline_check` regex | Türkçe red `unclear`'a düşürülür (TR-7). |
| vefat / yanlış numara / "ödedim" / temsilci / hardship ("işsiz kaldım", "hastayım") | LLM (`who_desc`, `reply_desc`, `tp_reply`) | Etiket LLM'e bağlı; sonrasında söylenen cümleler İngilizce (TR-2). |
| tarih ifadesi | `speech_lib.parse_spoken_date` (İngilizce-only) | Rakamsal dışı hepsi vague (TR-3). |
| tutar ifadesi | `speech_lib.parse_amount` (İngilizce-only) | TR-4. |
| DOB / son-4 | `normalize_dob` / `digits_from_speech` (İngilizce-only) + LLM'in kendi dönüşümü | TR-5. |
| saat | `parse_spoken_time` (İngilizce-only) | TR-3/TR-8. |
| ürün adı eşleştirme | `_named_in` ASCII regex | Ek/ I-İ sorunları (TR-9). |
| isim doğrulama "yes this is <name>" | LLM (`who1/who2`), kodda isim eşleştirme yok | Türkçe ek/hitap LLM'e bağlı; test edilemedi. |
| Bay/Bayan vs Mr/Ms | call variable + spec `who_question` | Şablon yalnızca `{{title}} {{last_name}}` (TR-12). |

## 3. Kapsam tablosu (test edilen girdiler)

Sembol: OK = beklenen; SESSİZ-HATA = yanlış sonuç, hata yok; ÇÖKME = istisna; YOK = hiçbir şey okunmadı.

| Girdi (örnek) | Fonksiyon | Gerçek çıktı | Sonuç |
|---|---|---|---|
| `23.10.2026`, `23/10`, `23.10`, `2026-10-23` | parse_spoken_date | `2026-10-23, date` | OK |
| `on beş Ekim`, `15 Ekim`, `15 Ekim 2026`, `27 Ekim`, `yirmi üç ekim` | parse_spoken_date | `('', 'none')` | YOK → vague |
| `ayın on beşi`, `ayın 15'i`, `ayın on beşinde`, `15'inde`, `Ekimin 20'si`, `ayın 27'si`, `ayın yirmi yedisi` | parse_spoken_date | `('', 'none')` | YOK |
| `önümüzdeki cuma`, `haftaya salı`, `bu cuma`, `cuma`, `cuma günü`, `cumaya`, `cumartesi`, `pazartesi`, `perşembe`, `salı günü olur` | parse_spoken_date | `('', 'none')` | YOK |
| `yarın`, `bugün`, `öbür gün`, `yarından sonra`, `ay sonu`, `ayın sonunda`, `haftaya`, `maaş günü`, `iki hafta sonra`, `15 gün sonra`, `bir hafta sonra` | parse_spoken_date | `('', 'none')` | YOK |
| `yarın değil cuma`, `pazartesi değil salı`, `yirmi bir, yok yirmi Ekim` (düzeltme/olumsuzlama) | parse_spoken_date | `('', 'none')` | YOK |
| `1.250,50 TL` / `2.500,75` | parse_amount | None | YOK → "Which amount?" döngüsü |
| `1.250`, `1.250 TL`, `2.500 lira`, `12.500` | parse_amount | 1.25 / 1.25 / 2.5 / 12.5 | SESSİZ-HATA |
| `1250,5` | parse_amount | 1250.05 | SESSİZ-HATA |
| `1250`, `420 lira`, `420 TL`, `1250,50 TL`, `650 TL'yi`, `450 lira öderim`, `500 civarı`, `1,250.50` | parse_amount | doğru | OK |
| `dört yüz yirmi lira`, `bin iki yüz elli`, `yüz elli lira kırk kuruş`, `1250 lira 50 kuruş`, `iki bin`, `yüzelli`, `bin lira`, `bin beş yüz`, `iki yüz elli bin`, `dört yüz, yok beş yüz`, `altı yüz otuz yedi buçuk`, `4 yüz 50`, `dört elli`, `yarısı`, `%50` | parse_amount | None | YOK (bad=False → tam tutar) |
| `sıfır sıfır yedi üç`, `sıfır sıfır yetmiş üç`, `yedi üç`, `dört dört yedi bir`, `kırk dört yetmiş bir`, `çift dört yedi bir`, `doksan dokuz, sıfır dört` | digits_from_speech | `''` | YOK → ham metin tool'a |
| `0073`, `0 0 7 3`, `4471`, `1988` | digits_from_speech | doğru | OK |
| `14.03.1988`, `14/03/1988`, `14.3.1988`, `03/14/1988`, `14 Mar 1988`, `14 Mayıs 1988` | normalize_dob | `1988-03-14` / `1988-05-14` | OK |
| `14 Mart 1988`, `14 MART 1988`, `14 Mart 88`, `14 Ocak..Aralık 1988` (Mayıs hariç) | normalize_dob | `14 mart 1988` (ham, küçük harf) | SESSİZ-HATA |
| `on dört mart bin dokuz yüz seksen sekiz`, `on dörd mart ...`, `1988 mart 14`, `mart 14 1988`, `14 mart 1988 doğumluyum` | normalize_dob | ham | SESSİZ-HATA |
| `03.04.1988` DMY / MDY | normalize_dob | `1988-04-03` / `1988-03-04` | OK (sıra spec'ten) |
| `saat 11`, `14:30` | parse_spoken_time | `11:00`, `14:30` | OK |
| `saat on bir civarı`, `öğleden sonra iki`, `on bir buçuk`, `ikiyi çeyrek geçe`, `sabah dokuzda`, `akşam beşte`, `yarın saat on` | parse_spoken_time | `''` | YOK |
| DOB/last4 pre hook (t9.py) | identity_hooks pre | ham Türkçe gönderilir, `incomplete` tetiklenmez | SESSİZ-HATA |
| STOP/REFUSE/ABOUT/REFUSAL/ALL/TODAY_REFUSAL/AMOUNT_IS/SAME_DAY/DAY_NUMBER/MOVE (34 Türkçe girdi) | regex'ler | hepsi False | YOK |
| decline_check (4 Türkçe red) | bodies.decline_check mantığı | `decline_real = no` → `unclear` | YANLIŞ (red yutulur) |
| Motor T1-T11 (reply_kind LLM etiketi + Türkçe metin) | read_reply+step | tarih/tutar yok sayılır, İngilizce cümleler | bkz TR-2/3/4 |
| Geri arama günü/saat Türkçe (10 çift) | CB_CHECK | yalnız günsüz `saat 15:00` ok | bkz TR-8 |
| `parse_now`: ISO 5 biçim | parse_now | ok | OK |
| `parse_now`: `14.10.2026 11:00`, `14/10/2026`, `14 Ekim 2026 Çarşamba 11:00` | parse_now | None | YOK |
| `to_float`: `1.250,00`, `₺1.250,00`, `1250,5` | to_float | ValueError | ÇÖKME (yakalanır) |
| Fuzz: 20 000 karışık Türkçe/NFD/Unicode-rakam dizisi × 6 ayrıştırıcı + read_reply | hepsi | 0 istisna | OK (never raises) |
| Unicode rakamlar `٣٠٠ lira`, `３００ lira` | parse_amount | 300.0 | OK |
| `İ/I/ı` küçültme, NFD | str.lower, unicodedata | bkz TR-9 | KUSURLU |
| `_named_in`: "Kredi kartını", "KREDİ KARTI", "ihtiyaç kredisini" | _named_in | `[0]`, `[0]`, `[1]` | OK (kısmen tesadüf) |
| `_named_in`: "kartı", "kartım", "krediyi" | _named_in | `[]` | YOK |
| spec grounding: Türkçe tatil/band/saat (t6.py) | grounding | 8 yanlış "not found" | bkz TR-10 |
| Trace cp1254: `→ ≤ ✓ ₺` | Trace.line | UnicodeEncodeError | ÇÖKME |
| Trace cp1254: Türkçe harfler, `’ “ ” … –` | Trace.line | OK | OK |

LLM'e devredilen etiketler (evet/hayır/robot/hardship/vefat/temsilci...) offline test edilemedi (ağ yok); kod yolu İngilizce anahtar kelimeye bağlı değil.

## 4. Public vakalarda neden görünmedi

Public vakalar İngilizce; `tests/hack/test_speech.py` ve `engine_sim` yanıtları İngilizce; Türkçe testi yok (`grep -i turk tests/` boş). `docs/GAP_ANALYSIS.md` C5 maddesi ("Türkçe hiç koşulmadı") eski bir mimari için; mevcut `builder/fb` hattında Türkçe hiç ele alınmamış.

## 5. Önerilen genel düzeltme planı (final değerleri koda yazmadan)

1. `language` + `currency` alanları (brief'ten LLM ile çıkar, doğrulama: brief'te geçiyor mu). `sys_prompt`, spec prompt'u ve tüm `Speak English` ifadeleri bu dilden beslenir.
2. Motor cümlelerini şablon yap (`neg.lines`), build'de LLM ile hedef dilde üret, deterministik yer-tutucu denetimi; `say_date`/`money` yerelleştirme tablosundan.
3. `speech_lib`/`collections_lib` için dil paketi (lexicon + sayı/tarih/tutar/saat kuralları, `fold()`); önce Türkçe, aynı arayüzle diğer diller.
4. Dilden bağımsız yedekler: çıkarım şemasına `said_date_iso` (prompt'a `date_table`) ve `said_amount` (rakam); motor lib sonucu boşsa kullanır, `check_promise_date` ile doğrular. Tutar metni dolu ama okunamazsa `bad=True` (asla sessiz tam tutar).
5. İngilizce regex kapıları (decline_check, STOP, REFUSAL, ALL, CB_DAY_WORDS) LLM bayraklarıyla veya dil paketiyle değiştir.
6. Build-time Türkçe selftest + Türkçe chat smoke (dil paketinin ve UTF-8 code node'un çalıştığını ilk koşuda gösterir).
7. `Trace` UTF-8; final komutuna `PYTHONUTF8=1`.
8. `grounding`'i alıntı-kanıt (`fold(quote) in fold(brief)`) ile dil bağımsız yap.

## 6. Öncelik sırası (ilk 5)

1. **TR-1 + TR-2 (P0)**: Ajan İngilizce konuşuyor; motor cümleleri sabit İngilizce, para birimsiz. Türkçe finalin temel koşulu sağlanmıyor. Önce `Speak {language}` ve cümle şablonları.
2. **TR-3 (P0)**: Türkçe tarih ifadelerinin tamamı vague; söz tarihi müşterinin dediği değil son tarih oluyor. Tarih içeren her hidden müşteri kaybedilir.
3. **TR-4 (P0)**: Türkçe tutar: kelimeli tutar sessizce tam tutar, `1.250` = 1,25, `1.250,50` döngü. `said_amount` ölü yol.
4. **TR-6 (P1, felaket)**: cp1254 + `₺`/`→` ile `Trace.line` çökmesi tüm build'i öldürebilir; çözüm tek satır (`encoding="utf-8"` veya `PYTHONUTF8=1`). Aynı gün yapılmalı.
5. **TR-5 (P0/P1)**: Kimlik cevapları (Türkçe kelimeli DOB/son-4) ham gidiyor, eksik-yıl koruması çalışmıyor, kimlik kilidi riski ("pieces/in_words" stilleri). TR-7 (decline_check'in Türkçe reddi yutması) hemen arkasında.
