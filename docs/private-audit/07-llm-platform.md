# 07 - Çağrı anı LLM / platform dayanıklılığı denetimi

Kapsam: üretilen Freya workflow'unun (builder/fb/generate.py -> brief-a / brief-ab export'ları, `runs/brief-ab/20261011T141939Z/brief-ab.workflow.json`, 40 düğüm, `docs/baseline-2026-10-10/*.workflow.json`) çağrı sırasında zayıf sabit modelle ("Freya LLM" = experimental-5, `agent_config.llmConfig.temperature = 0.7`) ve platform kurallarıyla nasıl davrandığı. Ağ yok, Freya/OpenRouter çağrısı yok; tüm bulgular repo içindeki dosyalardan, 26+65 kayıtlı simülasyon timeline'ından, `docs/research-talkative/raw/*.json` ham olaylarından ve yerel (modelsiz) denemelerden. Scratch: `out/audit-scratch/lp07/` (`tr_probe.py`, `dob.py`, `lat.py`, `idsteps.py`, `payload.py`, `sumres.py`, `ab_outline.txt`). Mevcut repo dosyalarına dokunulmadı.

Okuma notu: bu rapordaki "düzeltme" önerileri, hafıza kuralına uygun olarak yalnızca doğruluğu azaltmayan (correctness-neutral) olanlardır; doğruluğu etkileyebilecek bir mimari değişiklik "deney, kanıt gerekir" diye ayrıca işaretlendi.

## 0. Özet ve sıralama

Güçlü yanlar (korunmalı): bütün yönlendirme deterministik (`routing` için sıfır model çağrısı; `matches_intent` hiç yok), karar/tarih/tutar/outcome/not kodda, motorun cümleleri `on_enter: tts` ile sabit okunuyor, enum değişkenler her turda geçerli bir değere sıfırlanıyor (T18), kod düğümleri yavaş çalışmada bile hızlı (yavaş koşuda `code_exec` max 3.55 s, medyan 0.29 s; `verify_identity` medyan 0.74 s), her `conv` düğümünün kendi turn cap'i ve cap hedefi var, hiçbir pasif düğümde `always` eksik değil, tool sonuçlarına `what_to_do_next` ekleniyor (kit'in önerdiği kalıp). Gerçek başarısızlıklar iki sınıfta: (1) platform yavaşlığı + 60 sn sessizlik hang-up'ı, (2) modelin serbest ürettiği cümleler.

Top-5 (öncelik sırasıyla):

| # | ID | Sev | Başlık |
|---|---|---|---|
| 1 | LP-1 | P0 | Dil İngilizce'ye sabit; final Türkçe. Sistem promptu "Speak English", motor cümleleri, tarih/tutar/rakam/doğum tarihi çözücüleri ve regex korumaları İngilizce |
| 2 | LP-2 | P1 | Kimlik turu en uzun model zinciri (iki extraction çağrısı + tool kararı); gerçek hang-up'ların hepsi opening/identity'de |
| 3 | LP-3 | P1 | Hâlâ serbest LLM üreten 5 yüzey (opening2, identity, identity2 girişi, third_party, negotiate) çalıştırmadan çalıştırmaya değişen/ politika dışı cümle üretiyor |
| 4 | LP-4 | P2 | Opening tek bir extraction'a bağlı, kod yedeği yok; yanlış/boş `who1` onaylayan müşteriyi 2 turda `not_reached/unclear` ile kapatır |
| 5 | LP-5 | P2 | Passthrough retry döngüleri (`record`/`note`/`transfer`/`cb_book`) yalnızca kodun çalışmasına güveniyor; kod düğümü çalışmazsa bayat `neg_action=record` ile sınırsız `create_promise` döngüsü |

Diğerleri: LP-6 P2 (kısmi rakamlar bankaya yanlış cevap olarak gidebilir), LP-7 P2 (smoke test yalnızca canlılık), LP-8 P2 (`reasoning_override` ve sıcaklık kolları denenmemiş), LP-9 P2 (14 sınıflı `reply_kind` örtüşmeleri), LP-10 P3 (`pre_human`: "No" belirsizliği), LP-11 P3 (`close_call` yaşam döngüsü: çözülmemiş değişken ve hang-up fallback'ının doğruluğu), LP-12 P3 (code_exec yükü, eski `submission/` dosyaları), LP-13 P3 (yumuşak süre koruması yok, `ID_MAX_TURNS=60` etkisiz).

---

## 1. Platform anlambilimi, bu workflow'la karşılaştırma

Kit belgelerinden (workflow-format.md, tools-and-code.md, api.md) çıkan ve bu denetimin dayandığı kurallar, ve workflow'un onlarla ilişkisi:

| Platform kuralı | Workflow'da durum |
|---|---|
| Her kullanıcı mesajından sonra 3 ayrı model çağrısı: extraction, routing, dialogue. Deterministik koşullarda routing çağrısı yok | Tüm geçişler `equals/greater_than/always`; routing çağrısı 0. Dialogue yalnız `opening2`, `identity*`, `third_party`, `negotiate` düğümlerinde gerçekten çalışıyor; `opening`, `negotiate_say`, `pre_human` `on_enter: tts` ve `always` ile dialogue'a hiç ulaşmadan ayrılıyor |
| Extraction: değişken türü gruplarına ayrılıyor (ham olaylarda `raw_response` ayrı JSON: `{"extracted": {"id1": ...}}` ve ayrı `{"extracted": {"id_heard":..., "id_last":...}}`) | `identity`: enum grubu + metin grubu = 2 çağrı; `negotiate_say`: 4 enum + 2 metin değişkeni = 2 çağrı |
| `min_confidence` düşükse değer atılır, iz bırakmaz | `0.5` (identity `id1` `0.6`); atılan değer `unset` ya da önceki tur değeri olarak kalır. `reply_kind` ve diğerleri her turdan sonra kodla `unclear/no/none` değerine sıfırlanıyor (negotiation_lib.py:1631 `REPLY_CLEAR`); `who1/who2/id1/id2` sıfırlanmıyor (bkz. LP-4) |
| Eşleşen geçiş yoksa node'da kal, aynı `task_message`ten cevap ver | `identity*` bilerek böyle; diğer her yerde `always` var |
| `max_node_turns` yalnız kullanıcı mesajı sayar, her geçişte sıfırlanır; `max_turn_transition` yoksa etkisiz | Hepsinde hedef var; ama bkz. §6: `negotiate_say/negotiate` cap'i `always -> decide` ile zaten ölü, `identity` 60 pratikte ulaşılmaz |
| Tool sonucu model tarafından konuşulmalı, boş cevap = "model returned no speakable content" | Sistem promptu + `what_to_do_next` var; `identity` dışında tool-çağıran düğüm yok |
| `code_exec` kod+veri toplamı 131072 byte | `decide` 29.6 KB (paketli), `cb_check` 27.5 KB, `after_record` 25.4 KB, `facts` 21.7 KB; veri ~1.3 KB/müşteri turu (bkz. LP-12) |
| `pre_actions`/`tools` karşılıklı strip | Doğru yerleştirilmiş (api_request/code düğümlerinde pre_actions, yalnız `identity*`ta `tools`) |
| Müşteri ~60 sn sessizlikte kapatır (simülasyon davranışı) | Bkz. LP-2: model adımları 10x yavaşlayınca kimlik turu bu sınırı geçiyor |
| Lifecycle tool `end_of_call`: çözülmemiş `{{var}}` için bir model çağrısı parametreleri doldurur | `close_call` `final_outcome/final_reason` bağlanıyor; `CLOSE_PRE` yalnız boş / `{`-ile başlayan değeri düzeltir (bkz. LP-11) |

---

## 2. Bulgular

### LP-1 (P0) Dil: çağrının her katmanı İngilizce'ye sabit; final Türkçe

Kanıt (hackathon sayfası, `docs/private-audit/hackathon-page-2026-10-11.txt`): "Practice calls are in English, the final case is in Turkish." Kit README: final müşteriler "pieces, corrects, talkative, asks_back, in_words" konuşur, tutarları ve tarihleri kelimeyle söyler.

Workflow'da dil sabit:

- `generate.py:1230` sistem promptu `... Speak English.` Bütün LLM üretimi (opening2, identity, third_party, negotiate cevapları) İngilizce.
- `negotiation_lib.py` içinde ~40 sabit cümle (satır 239 `SORRY`, 266-268 `Can you do that?`, 366/464/1182 `Could you pay ... on ...?`, 454/488/508 `The latest date I can record ...`, 531-534, 1268, 1346 `The minimum for ...`, 1416 `Your promise is recorded: ...`, `present_text` "Your X has N past due and is D days late.") spec `lines`ında olsun olmasın İngilizce; `say_date` "Wednesday 14 October 2026" biçiminde üretir.
- Hook'ların `what_to_do_next` metinleri (generate.py:600-712) İngilizce.
- Müşteri sözünü çözen her kod İngilizce. Yerel probe (`out/audit-scratch/lp07/tr_probe.py`, `dob.py`):

| Girdi | Sonuç |
|---|---|
| `parse_spoken_date("16 Ekim")`, `"on altı Ekim"`, `"cuma günü"`, `"yarın"`, `"bu cuma"`, `"gelecek hafta"` | hepsi `('', 'none')`; `read_reply` bunu `date_kind='vague'` yapar (sayı bile Türkçe ay ismiyle çözülmüyor) |
| `parse_amount("dört yüz yirmi")`, `"iki bin beş yüz"` | `None` (`420`, `637,50` rakamla çalışır) |
| `digits_from_speech("dört dört sıfır bir")`, `"dört yedi bir iki"` | `''` (boş) |
| `normalize_dob("14 Mart 1988")` | `'14 mart 1988'` (ISO değil) |
| `parse_spoken_time("öğleden sonra üç")`, `"yarın sabah on bir"` | boş |

- İngilizce regex korumaları Türkçe'de hiç eşleşmez: `STOP_WORDS`, `REFUSE_ANSWER`, `ABOUT_WORDS`, `REFUSAL_WORDS`, `ALL_WORDS`, `TODAY_REFUSAL`, `PART_WORDS` (negotiation_lib.py:1488-1530), `CB_DAY_WORDS` (bodies.py:257), `_MONTHS/_WDAYS` (speech_lib.py:8,22).
- Extraction açıklamalarının örnekleri İngilizce ('this Friday', 'the 30th of October', '637.50', 'four hundred and twenty'); alan `type: string` olduğu için platformun tarih çapası yok, çıkarma modeli "tam olduğu gibi kopyala" diyor, yani Türkçe ham metin kodda çözülemiyor.

Tetikleyen müşteri davranışı ve sonuç zinciri (Türkçe konuşan, kelimeyle söyleyen müşteri):

1. Doğum tarihi "on dört Mart bin dokuz yüz seksen sekiz" / "14 Mart 1988": hook ISO üretemez, bankaya okunamayan değer gider. Bank `ok:false` ve yanlış saymıyor (kit openapi: "unreadable ... not counted as a wrong answer"), ajan "The answer could not be read. Ask for it again." talimatını alır: ayda sonsuz yeniden soru; kimlik asla geçmez. Model kendi başına ISO'ya çevirirse (tool açıklaması "date as YYYY-MM-DD" ve bizim "in their own words" çelişkili) geçer; kararsız.
2. Telefon son 4 hane kelimeyle ("dört dört sıfır bir"): `_d` boş, `_b['answer'] = str(_a)` ham kelimeler bankaya gider; telefon alanı için "okunamayan = sayılmaz" güvencesi yok; 3 yanlış = `fin_failed` (`not_verified/failed`). Bu doğrudan `in_words` ve `pieces` stillerinde final başarısını öldürür.
3. Türkçe ret ("Vermek istemiyorum", "Bu ne için?"): `decline_check` (bodies.py:349-360) `real=False` döner, `id1` `unclear` yapılır; ajan sonsuz yeniden sorar. Beklenen sonuç `not_verified/declined` hiç gelmez; çağrı en geç `maxCallDurationSeconds`ta `exceeded-max-duration` ile biter, `close_call` fallback'ı `not_reached/unclear` (yanlış sonuç).
4. Pazarlık: Türkçe tarih/tutar çözülmez -> `dk='vague'` -> bir kez "On which exact day can you pay?" -> sonra `deadline` önerisi. Müşterinin söylediği tarih (örn. "cuma") yerine son günü kaydeder ya da müşteri reddeder: tarih içeren her vaka (PUB-04/07/13 türü) düşer. Üç ürün + "ikisini de ayın otuzunda" -> `ALL_WORDS` çalışmaz, `all_products` extraction'a bağlı.
5. Üçüncü kişi: `cb_check` Türkçe gün ("yarın sabah on bir") çözemez -> `outside` -> `fin_no_callback`; beklenen `callback`.

Dil karışımı: spec `lines` (açılış, kimlik açıklaması, kimlik hatası, başarılı kapanış, robot/scam cevabı) brief'in dilindedir (spec çıkarıcı brief cümlelerini kopyalıyor); motor cümleleri (teklif, onay okuması, yeniden sorma, "Is there anything else...") ve LLM serbest cümleleri İngilizce kalır. Aynı çağrıda üç dil kaynağı: spec (TR) + motor (EN) + LLM (EN). Judge modeli Türkçe kriter okuyorsa ("ajan Türkçe konuşur" gibi) ya da müşteri (LLM) ajanın diline uyup İngilizceye kayarsa belirsiz sonuç; ikisi de kontrol edilemez. `agent_config` içindeki `ttsConfig.language/sttConfig.language = "tr"` platform varsayılanı, workflow ile uyumsuz.

Düzeltme (builder'ın brief'ten dil okuması kuralına uygun; metin hardcode değil):
- `spec.language` (ISO kod) brief'ten çıkarılsın; `generate.py:1230` bu dili yazsın ("Speak {language}"). ENGINE_LINES'taki bütün motor cümleleri build-time'da (zaten 2 model çağrısı var) spec dilinde bir çeviri turuyla üretilsin ve `neg.lines` ile `cfg`ye girsin; `negotiation_lib` sabit İngilizce cümleleri `_line(neg, key, default)`ten okusun. Yeni anahtarlar: `sorry[]`, `cut_sorry`, `confirm_lead`, `offer_*`, `latest_date`, `not_business_day`... (şu an kodda gömülü olanlar).
- Çözücüler için `lang` paketi: `_MONTHS/_WDAYS/_UNITS/_TENS/_ORD` ve regex listeleri `CFG["lang"]` ile gelen sözlükten yüklensin (Türkçe ay/gün/sayı/sıra sözlüğü kütüphaneye statik eklenebilir; "hiçbir brief değeri" değil, dil verisi). `STOP/REFUSE/ABOUT/ALL/PART/TODAY_REFUSAL/CB_DAY_WORDS` aynı şekilde.
- Dil-bağımsız yedek (en ucuz doğruluk sigortası): extraction'a model tarafı normalize alanlar ekleyin: `said_date` (`type: date`, platformun tarih çapası "yarın/cuma" gibi göreli ifadeleri çözer) ve `said_amount` (`type: float`) ve `id_digits` (yalnız rakam). Kodda önce kendi çözücü, çözemezse model alanı kullanılsın (yalnız `kind in offer/yes` ve metin boş değil iken). "vague" ayrımı için `date_precision: exact|vague|none` enum'u. İngilizce yolda hiçbir şey değişmez (çözücü çözüyorsa model alanı okunmaz), yani İngilizce için nötr.
- Smoke test ve forbidden-kelime denetimi için bkz. LP-7.

### LP-2 (P1) Kimlik turu = en uzun model zinciri; kayıtlı hang-up'ların tamamı opening/identity'de

Ölçüm (`out/audit-scratch/lp07/idsteps.py`, `lat.py`, ham olaylar `docs/research-talkative/raw/style4-a-r{1,2,3}-*.json`):

| Kimlik turu (tool çağrılan) | r1 medyan | r2 medyan | r3 (yavaş koşu) medyan / max |
|---|---|---|---|
| mesaj -> enum extraction (`id1`) | 0.5 s | 0.7 s | 4.5 / 10.6 s |
| enum -> metin extraction (`id_heard`, `id_last`) | 0.7 s | 0.4 s | 4.1 / 15.4 s |
| metin extraction -> tool başlangıcı (dialogue #1) | 2.1 s | 1.9 s | 5.5 / 31.9 s |
| tool (HTTP + hook) | 0.9 s | 0.5 s | 0.7 / 2.5 s |
| toplam (mesaj -> ajan sesi) | 5.6 s (max 22) | 5.1 s (max 41) | 15.7 / 45.0 s |

Aynı koşuda `negotiate_say` turu medyan 3.8 s, kimlik turu 10.3 s (`lat.py`: identity p90 24.5 s, max 44.9 s). Tek bir örnekte (style4-a-r3-PUB-03): müşteri 8.98 s'de doğum tarihini söyledi, `id1` 17.7 s'de, `id_heard/id_last` 50.1 s'de geldi, müşteri ajan sesi gelmeden 69 s'de kapattı (`customer-ended-call`, çağrı sonunda `close_call not_reached/unclear`). style4-a-r3-PUB-01 aynı desende 65 s. README'deki 11 Ekim yavaş koşusunda "Yes, this is Owen Park" okuması 4-6 dk sürmüş (opening extraction, tek enum, bu platformdan kaynaklı ve bizim elimizde kontrol yok).

Neden kimlik turu uzun: (a) `id_heard` extraction'ı müşterinin bütün cümlesini model çıktısı olarak geri yazıyor (örn. "Work has been absolutely non-stop today, I barely had time for lunch. It is the twelfth of July nineteen ninety-one."): uzun çıktı = yavaş çağrı; yalnız tool hook'unda rakam sırasını doğrulamak için kullanılıyor (generate.py:555 `_h`). (b) ayrı bir metin grubu çağrısı daha. (c) model tool çağrısına karar veriyor.

Platform yavaşlığını biz değiştiremeyiz (README: yavaşken aynı agent hızlı koşuda geçiyor), ama kimlik turunda sıralı model adımı sayısı 3 (2 extraction çağrısı + dialogue), `negotiate_say` turunda 1-2. Doğruluğu azaltmayan seçenekler:

1. (Doğruluk nötr, önce prob gerekli) `id_heard`'i kaldır, kodda `vars['system.conversation']`'dan türet. Kod düğümlerinde bu liste var (L1, ölçüldü 11 Ekim, negotiation_lib.py:1474 `last_customer_text` zaten kullanıyor). Açık soru: `pre_process_code` hook'unun `vars`'ında da var mı? Probe: `builder/tools/probe_lastmsg.py` benzeri, bir `api_call` hook'unda `vars.get('system.conversation')` yaz ve sonucu tool sonucuna koy. Varsa: `id_heard` = son agent sorusundan sonraki user mesajlarının birleşimi (kod, model yok); `id_last` kalır. Faydası: metin extraction çağrısının çıktısı kısalır (yalnız `id_last`), uzun-kopya çıktısı ve onun yavaşlığı gider; `id_heard`'a sızan simülatör metni ("model) I am so sor...", AUDIT_FABLE 4.) de gider.
2. (Doğruluk nötr) `id1 == unclear` (küçük konuşma, kesik cümle) turlarında dialogue adımını atla: `id_reask` kod düğümü + `on_enter: tts` ("Sorry, I think you were cut off." + açık sorunun `ask` metni, spec'ten). Bu zaten negotiation için yapılmış (`_sorry`/`_short` rotasyonu); kimlikte hâlâ LLM yapıyor. Hem 1 model adımı hem de uydurma cümleler (LP-3) gider. Hangi sorunun açık olduğu `id_ok_fields` + `_order`'dan kodda biliniyor.
3. (Deney, doğruluk riski var; yalnız kanıtla) tek tur modelsiz yol: `verify_identity`'yi `api_request` düğümü yap, cevabı extraction `id_last` + kod ayrıştırıcıdan al, `what_to_do_next` yerine spec cümlelerini `tts` ile söyle. Kimlik turu 3 yerine 1 model çağrısı olur. README "pieces cevapları kaybederiz" dediği için varsayılan önerim değil: yalnız `pieces/corrects/in_words/talkative` x3 koşu A/B ile ve `--rerun-if-slow` ile; bu bölüm hafıza kuralına göre varsayılan değil.
4. (Deney, platformda desteklenip desteklenmediği bilinmiyor) `reasoning_override: "disabled"` (workflow-format.md "Node fields"): bkz. LP-8.

Not: yavaş çağrıların maliyeti 10 sn'lik adımları 60 sn'ye çıkarıyor; kod düğümleri ve API çağrıları bu yavaşlıktan etkilenmiyor (max 3.5 s), yani "karar kodda, model yalnız sınıflar" yönü doğru ve genişletilebilir.

### LP-3 (P1) Serbest LLM cümleleri: varyans ve politika/dürüstlük riski

Bugün hâlâ model üretimi olan yüzeyler: `opening2` (`on_enter: llm`), `identity` (tts girişinden sonra bütün konuşma), `identity2` (`on_enter: llm`), `third_party` (`on_enter: llm`, her tur), `negotiate` (müşteri soru sorunca, `has_question=yes`). Sıcaklık 0.7 (export `agent_config.llmConfig.temperature`), workflow'dan değiştirilemez (agent_config bırakılmalı). 3 koşuda hepsi geçmeli; bu yüzey varyans kaynağı.

Gözlenen (timeline'lardan):

- `opening2` boş sohbete yorum: style4-a-r3-PUB-06 10.7 s `I am sorry to hear that your day has been so hectic. Am I speaking with Ms Nair?`; sonra hardship durumunda judge "sympathy twice" (PUB-05, style4-a-r3). Düzeltme prompt cümlesiyle yapıldı (generate.py:1297-1311 "no sympathy, no 'sorry to hear'"); yani zayıf modelin uymasına güveniyor.
- `identity` serbest yeniden sorma: "I'm sorry, I think we have a bad connection." (style4-a-r1-PUB-07/-08, style4-a-r2-PUB-10, style4-a-r3-PUB-05), "I apologize, you're still breaking up.", "I'm sorry, I keep losing you." ve "And the remaining digits of the year?"; bağlantı sorunu iddiası gerçek dışı; "And the remaining digits?" müşteri küçük konuşma yaptığında bile soruluyor (model küçük konuşmayı kısmi cevap sanıyor).
- `negotiate` önekleri: "Yes, that is possible." / "Yes, you can." / "Thank you." / "This is about your personal loan." (style2-a-asks_back-PUB-07) / "I am sorry, I do not have that information." ardından "Monday 26 October 2026 is not a business day ..." (çelişkili iki cümle, judge tolere etti). Model müşterinin "X olabilir mi?" sorusuna kendi başına evet/hayır diyor, oysa karar kodda. Yanlış-olumlu öneki ("Yes, that is possible." + kodun reddi) henüz görülmedi ama kod yolu buna açık (`answer_first`, negotiation_lib.py:1455: her `has_question=yes` ve `action == say` turu LLM'e gider; onay okuması ve tutar/tarih içeren cümleler de model üzerinden geçer: gözlenenlerde tutar/tarih bozulmadı, gözlenen 69 `[negotiate]` satırında tutar/tarih bozulmadı, ama bu "exact" talimata sadakat sıcaklık 0.7'de garanti değil).
- Tarihçe: gen-a-1 PUB-10 ("said recorded before success", LLM), gen-a-2 PUB-04 (robot sorusuna "digital assistant" demedi), gen-a-2 PUB-05 (hardship'te ikna denemesi): LLM-sürümlü yüzeyler hep bu tür hatalar çıkardı ve motorun kodlanmasıyla gitti. Aynı mantığın kalan yüzeylere uzatılması doğal.

Düzeltme (hepsi model adımı azaltır, cümleler brief/spec'ten sabit olur; doğruluk nötr veya daha iyi):

1. `opening2`: `who1 != question` ise LLM değil `tts` ("Am I speaking with X?" spec `who_question`) ile yeniden sor; `who1 == question` ise yalnız o durumda LLM. İki düğüm: `opening2_say` (tts) ve `opening2` (LLM). Sempati/yorum riski ve bir model adımı gider.
2. `identity` küçük konuşma/kesik cümle: LP-2 madde 2 (kod + tts).
3. `negotiate` yüzeyini daralt: `has_question` yerine `question_topic` enum'u (`robot_ai`, `scam`, `discount_plan`, `balance`, `other`, `none`); `robot_ai/scam/discount_plan/other` için spec cümleleri (`ai_answer`, `scam_answer`, `no_info`) kodda birleştirilip `negotiate_say` tts ile söylensin; `balance` için `product_facts`'tan kod cümlesi. LLM yalnız son çare. Böylece "evet/hayır" öneki ve indirim/plan uydurma ihtimali ortadan kalkar.
4. `third_party`: yeniden sorma metni (`cb_say`) zaten kodla hazırlanıyor; `on_enter: llm` yerine `tts` ("Ask when {{title}} {{last_name}} can be reached ... " spec'te yoksa neutral satır) ve yalnız `tp_reply == question` iken LLM.

Bu liste uygulanınca çağrıdaki LLM üretimi yalnız gerçek soru cevaplarında ve kimlikte kısmi cevap sorularında kalır.

### LP-4 (P2) Opening: tek extraction, kod yedeği yok, iki şans

`opening` (generate.py:1283-1296) ve `opening2` (1297-1311): `who1/who2` extraction `min_confidence 0.5`. Hiçbir koşul eşleşmezse `always -> opening2 / fin_unclear`. Brief kural 3 gerçek "belirsiz" için iki ask'ı tanımlıyor; fakat extraction'ın hiç değer üretmemesi (düşük güven atılır, platform timeout/hata, geçersiz enum) "müşteri belirsiz" ile aynı yola düşer: "Yes, this is Owen Park" cevabı okunamazsa ajan aynı soruyu tekrar sorar, ikinci okunamayışta çağrıyı `not_reached/unclear` ile kapatır. README'deki 4-6 dk'lık yavaş extraction bu düğümde görüldü; platform extraction'ı düşürürse (kayıtlı örnek yok ama tasarım buna açık) onaylayan müşteri kapatılır.

Düzeltme (doğruluk nötr, yalnızca yedek):
- `who1/who2` değişkeni "hiç gelmedi" (`is_not_set`) ise `always` yerine bir `open_fallback` kod düğümüne git: `last_customer_text(vars)` üzerinde sıkı onay deseni (kural 3 sözlüğü: yes / that's me / speaking / müşterinin kendi adı; soru işareti ve başka-kişi sözleri yok). Eşleşirse `setup_id`, değilse mevcut yol. Brief sözlüğü brief'ten çıkarılır (`lines`/spec `who_confirm_words`), dil paketi LP-1 ile.
- Deney (A/B ile): `opening`da extraction'ı hiç kullanmayan modelsiz hızlı yol: `opening` düğümünde extraction yok, `always -> open_fast` (kod), net onaysa `setup_id`; değilse mevcut LLM yoluna (`opening2`). Net onay cevaplarında ilk tur sıfır model çağrısı (README'deki hang-up'ların bir bölümü bu turda). Yalnız temiz cevapları yakalar (talkative cümleler eski yoldan gider), yani doğruluk nötr, fakat precision'ı ölçmek şart.

### LP-5 (P2) Kod düğümü çalışmazsa bayat durumla sınırsız döngü

`validate.py:190-192,850`: `record/after_record`, `note/after_note`, `transfer/after_transfer`, `transfer_pre/after_transfer_pre`, `cb_book/after_cb` "kodla sayaçla sınırlı" diye `bounded_loops` içinde ve passthrough-cycle hatasından muaf. Sınır yalnızca kod düğümünün gerçekten çalışmasına bağlı. `on_error` (bodies.py:102 `NEG_ERROR`) yalnız snippet içindeki istisnayı yakalar; sandbox'ın kendi reddi ("Code and data for code_exec total 131136 bytes, over the 131072-byte limit", T11, exp-wait2 PUB-12: `decide` 134.8 s'den sonra her turda başarısız; motor bayat `neg_say`i 67 kez yeniden okudu) ya da platform 5xx'i durumunda değişkenler güncellenmez. `after_record` başarısız olursa `neg_action` hâlâ `record` (DECIDE bunu yazdı) ve geçiş listesi (`after()`, generate.py:1568) `record` hedefine tekrar gider: sonsuz `create_promise` (banka "already recorded" dese de `rec_ok` hiç işlenmez, kimse sonuç söylemez). Platformun kendi döngü sınırı belgede yok.

Düzeltme (yalnız kod yolu bozulduğunda devreye girer, normal çağrıda etkisiz):
- `after_record`, `decide`, `record` yolunun ilk geçişi olarak motor sayaçlarını kullan: `record_success_count > 8 or record_failure_count > 8` (ürün sayısı <= 3, ürün başına <= 2 deneme: yasal en çok 6) -> yeni `fin_service_error` kod düğümü (spec `outcomes.no_promise`/`service_error`, `lines.service_error`, `lines.goodbye`) -> `goodbye`. Aynı kalıp `note_*_count`, `transfer_*_count`, `cb_book_*_count` için.
- Yük sınırı kaynaklı bayat durumun asıl çaresi LP-12 (kod ve veri payını küçültmek); `negotiate_say` bayat `neg_say`i tekrar okur ama müşteriyi konuşur halde tutar (sessizlik değil), bu yüzden onun için ayrı koruma önerilmiyor.

### LP-6 (P2) Kısmi rakamlar bankaya yanlış cevap olarak gidebilir; koruma yalnız prompt

`identity_hooks` pre hook'u (generate.py:490-565): `kind in ('digits','year')` için `_d = digits_from_speech(_a)` ve `_b['answer'] = _d or str(_a)`. Uzunluk denetimi yok. Yerel deneme: `"four, four"` -> `'44'`, `"4 4 0"` -> `'440'`. Model "fewer digits than asked for ... do not call the tool yet" talimatını izlemezse (prompt, generate.py:1321-1343) 2-3 haneli cevap bankaya gider ve yanlış sayılır (DOB için `incomplete` göndererek bankanın saymaması sağlanıyor, `phone_last4` için böyle bir sentinel yok; openapi'de "unreadable ... not counted" yalnız `date_of_birth` açıklamasında). Üç yanlış = `fin_failed`. 280 `verify_identity` çağrısında bu görülmedi (hepsi 4 haneli); riski ölçen veri yok. `pieces` stilinde bir kopyada "19" + "75" yılın yarım parçasının 2019 olarak gittiği görüldü (style2-a-pieces-PUB-07, eski hook; `_ld in ('19','20')` korumasıyla kapatıldı, dob.py: `'5 December 19' -> incomplete`).

Düzeltme: spec `identity.questions[].length` (kural metni: "last four digits"). Kısmi rakamı bankaya göndermeden durdurmanın tek belgelenmiş yolu model kararı olduğundan, kapıyı extraction tarafına koy: `id_last` içindeki rakam sayısı `length`ten küçükse `id1`in değeri `answers` yerine yeni enum değeri `partial` olsun (kod: `identity` extraction'ından sonra değil, `id1` etiketi zaten modelin; bu yüzden ek olarak tool açıklamasına "CALL only when the answer has all digits" yazılır) ve `partial` iken modelsiz `tts` ("And the remaining digits?") söylensin (LP-2 madde 2 ile aynı düğüm). Hook'ta `len(_d) < length` ise cevabı değiştirmek `phone_last4` için güvenli değil (sentinel yanlış sayılır); bu yüzden hook'a bırakmayın. Hidden mock bizim olduğu için service contract izin veriyorsa "okunamayan cevap yanlış sayılmaz" kuralını bütün alanlara uygulamak da seçenektir.

### LP-7 (P2) Smoke test yalnızca canlılık testi; yanlış-OK ve yanlış-FAIL

`builder/build.py:228-268` tek tur `"Hello?"` gönderir, `response` boş olsa bile ("The chat route can skip the first entry line") "passed"; yalnız: (a) çağrı başladı, (b) `forbidden` kelime listesi `\b...\b` ile ilk cümlede yok.

- Yanlış-OK: bozuk bir kod düğümü, yanlış tool alan eşlemesi, çalışmayan mock URL'i, kimlik hook'u, `negotiate` zinciri hiç çalışmaz; smoke ikinci tura geçmez. Dil kontrolü yok (LP-1).
- Yanlış-OK Türkçe'de: `\b` + `.lower()`; "borç" kökü "borcunuz/borcunuzu" ile eşleşmez (ç->c ünsüz yumuşaması), `İ.lower()` iki karakter üretir; kök listesi eylemsel eklere karşı kör. Yani açılış cümlesindeki sızıntıyı kaçırır.
- Yanlış-FAIL: yok görülmedi; `bank` adı çıkarılıyor.
- Selftest (1024 vaka) modelsiz motoru sınıyor, LLM yüzeyi (extraction, prompt) sınanmıyor.

Düzeltme: (a) smoke ikinci tur: onay cümlesi (dil tespitinden sonra, brief'in kendi örneğinden: `lines.who_question`'a karşılık "yes"-eşdeğeri; dil bilinmiyorsa İngilizce/Türkçe iki deneme) ve cevapta kimlik sorusunun (spec `questions[0].ask`) bulunup bulunmadığı ile forbidden stem kontrolü; (b) forbidden denetimi Unicode-katlı ve kök-tabanlı (`startswith`/alt dize, `casefold`); (c) finalde zip'teki 6 örnek konuşmanın müşteri turlarını `POST /api/v2/chat/message` ile yeniden oynatıp (builder girdisinden, hardcode değil) ajan cevaplarında yasak kök ve dil kontrolü yapmak; hepsi `smoke=failed` etiketi, yayını durdurmaz (mevcut sözleşme korunur).

### LP-8 (P2) `reasoning_override` ve sıcaklık: kullanılmamış kollar

`reasoning_override` (workflow-format.md, "Only models that support such a switch react to it") hiçbir düğümde ayarlı değil (`grep` 0). experimental-5'in destekleyip desteklemediği belgede yok. Kayıtlı yavaşlık desenleri (tek kelimelik enum extraction'ın dakikalar sürmesi, 1 kelimelik `id1`in 8.7 s olması) düşünme tokenlarına benziyor. Deney: sınıflandırma/kısa konuşma düğümlerinde (`opening`, `opening2`, `identity*`, `third_party`, `negotiate`) `reasoning_override: "disabled"`; ölçüt: `run_cases --style talkative/pieces --rerun-if-slow` medyan ve 3x doğruluk. Etkisizse sıfır maliyet; doğruluğu düşürürse geri al. Sıcaklık 0.7 `agent_config`te; kit "agent_config'i gönderme" dediği için workflow'dan ayarlanamıyor (belgelenmemiş), bu yüzden serbest üretimi azaltmak (LP-3) tek kontrol.

### LP-9 (P2) `reply_kind`: 14 sınıf, örtüşme, sınıf hatalarının sonucu

`generate.py:1157-1178`: tek enum, ~1400 karakterlik açıklama. Örtüşenler: `no/too_much/evasive/vague/offer/hardship/restructure/dispute/paid`. Zayıf modelin hatasının politika maliyeti sınıfa göre: `hardship` (sempati + destek teklifi, ikna yok; kural 32 33'ten önce) bir kesik/küçük konuşma cümlesinden de çıkabilir (AUDIT_FABLE 2.6: "looking for work" -> hardship, conf 1.0); `dispute/paid` kapanışa gider; `stop` (2 kere = kapanış). Kod yalnız kesik mesajlarda (`cut_kind`) bu etiketleri `unclear`a düşürüyor, tam mesajlarda etikete güveniyor. Karşı doğrulama İngilizce regex'li (`TODAY_REFUSAL`, `STOP_WORDS`); Türkçe'de bu doğrulamalar da yok.

Düzeltme (doğruluk nötr): LP-1'deki dil paketiyle birlikte, `hardship`, `dispute`, `paid`, `restructure`, `human`, `stop` etiketleri için kodda ikinci görüş: ham mesajda (`last_customer_text`) o sınıfın anahtar kök listesinden en az biri yoksa etiket `unclear` (bir tur yeniden sorulur) ve `unclear_run` mantığı zaten sonsuzluğu kesiyor. Brief'e göre etiketlerin kök listesi spec'ten (`semantic_keywords`) çıkarılır, kodda sabit değil. Not: bu bir "model etiketi + kod kapısı" paterni; İngilizcede mevcut 10/10 sonuçları bozmamalı (öncesinde `engine_sim` selftest'e `reply` + metin çiftleri eklenerek sınanır).

`has_question` -> LLM yolu için bkz. LP-3 madde 3.

### LP-10 (P3) `pre_human`: "No" belirsizliği

`hum1` açıklaması (generate.py:1461-1463): "insists: still wants a person. continues: agrees to go on, or anything else." "Can I help you with this myself first?" sorusuna sade "No." / "Hayır." cevabı kural olarak "insists" (yardımı reddetti), açıklamada "anything else" -> `continues` ile çakışıyor. Karşılığı pazarlıkta kodlu (`step()`: `help_open` + `no -> human`), ama kimlikten önceki yolda yalnız extraction etiketine bağlı. Kural 42 hidden final'de test edilebilir (public sette Human vakası yok).

Düzeltme: açıklamayı `insists: wants a person, or says no / no thanks to your offer of help; continues: yes, agrees to go on.` yap; ayrıca ham mesaj kod kapısı gerekmiyor.

### LP-11 (P3) `close_call` yaşam döngüsü ve hang-up fallback

- `close_call` (`alwaysRunsAt: ["end_of_call"]`) gövdesi `{{final_outcome}}`/`{{final_reason}}` ile bağlı; bu değişkenleri `SETUP` (setup_id/setup_tp) `''` yapıyor. Opening'de hang-up olursa (README'de 4-6 dk'lık extraction gecikmesi tam bu düğümde) değişken tanımsız: kit belgesi "still unresolved parameter -> one model call fills the parameters". `CLOSE_PRE` yalnız boş veya `{` ile başlayan değeri düzeltir (generate.py:766-). Modelin uydurduğu bir `outcome`/`reason` doğrulanmıyor (`outcome` alanında enum yok). Düzeltme: close_call `outcome` ve `reason` için tool API'nin enum'unu şemaya ekle (kit "Constrain model-filled parameters"), ve `CLOSE_PRE` içinde değer enum dışıysa `HANGUP['unreached']`e çevir. Opening'de hang-up probu: platformda bir hang-up sınaması yapın (bu denetimde ağ yok).
- Fallback doğruluğu: kimlik sürerken hang-up (style4-a-r3-PUB-03) `unreached` = `not_reached/unclear` olarak kaydedildi; müşteri iki yanlış kimlik cevabı vermişti. `id_started=='yes'` ise kimlik tamamlanmadan bırakma `not_verified/declined`e daha yakın (brief kural 15/14'ten çıkarılacak, spec `outcomes.declined`); mevcut fallback bunu kullanmıyor.

### LP-12 (P3) code_exec yük payı

Ölçüm (`payload.py`, 3 ürünlü yerel simülasyon, `decide` 28.1 KB, çıktı kod+yaklaşık veri): 1 turda 32.8 KB, 60 turda 44.0 KB (yalnız `vars` ve konuşma; platformun akış değişkenleri/header'ları dahil değil). Platform ölçümü (README T11): 51. turda 66 KB kodla toplam 131136 byte, yani veri ~1.3 KB/müşteri turu; kod 30 KB iken sınır ~75 tur, 36 KB sert tavanda ~73 tur. Türkçe'de JSON kaçışı (`ensure_ascii`, her `çğıöşü` 6 byte) bu oranı artırabilir: Türkçe karakter oranı ~%8 ise yaklaşık +40 %, ~50-55 tura iner (ölçülmedi, probe: Türkçe metinle 100 KB `code_exec`). Gerçek çağrılar <= 30 tur; yalnız kilitlenmiş döngülerde ve LP-1'in tetiklediği döngülerde ulaşılır.

Düğüm boyutları (brief-ab): `decide` 29592 (paketli), `cb_check` 27470, `after_record` 25436, `facts` 21683, `setup_id` 12028, `setup_tp` 12005, `after_transfer` 8019, `decline_check*` 6354, `after_note` 5829, hook'lar `verify_identity` pre 10.8 KB + post 3.7 KB, `close_call` pre 2.2 KB. `CODE_BUDGET=30_000` (embed.py:29): yalnız 30 KB üstü paketleniyor; `cb_check` (27.5 KB) ve `after_record` (25.4 KB) paketsiz ve eşiğe yakın; daha uzun bir spec `cfg`si (ör. çok sayıda `lines`, tatil, bant) önce bu düğümleri 30 KB'ı aştırır, sonra paketlenir; paketleme sonrası ~22 KB. Büyüme kodla sınırlı (`cfg` şu an 2.2 KB), ciddi risk yok.

Öneri: (a) `cb_check`/`after_record`/`facts` de paketlensin (sıkıştırma maliyeti ~0.1 s) ya da `CODE_BUDGET` 20 KB'a insin: veri payı +10 KB; (b) hook'lar (`verify_identity` pre 10.8 KB) hâlâ açık. (c) eski `submission/northwind.workflow.json` ve `brief-ab-run.workflow.json` (10 Ekim, düğüm başına 59 KB kod, `Speak English`) yanlışlıkla teslim edilirse T11 yeniden doğar; güncel olanlar `runs/` içine taşınmış (`submission/` şu an yalnız eski dosyaları tutuyor); finalden önce `submission/`i yalnız son koşuyla bırakın.

### LP-13 (P3) Tur ve süre sınırları; cap'te sonuç

| Düğüm | `max_node_turns` -> hedef | Pratik etki |
|---|---|---|
| `opening` | 3 -> `opening2` | `always` geçişi zaten `opening2`ye gider; cap ölü |
| `opening2` | 2 -> `fin_unclear` | `always` ile aynı; kural 3'e uygun (`not_reached/unclear`) |
| `identity`, `identity2` | 60 -> `fin_declined` | 60 kimlik turu ~4-5 dk'dan fazla; `maxCallDurationSeconds=420` (kit) önce biter (`exceeded-max-duration`). Cap yalnız `ID_MAX_TURNS` yorumunun savunduğu "güvenlik ağı"; fiilen çağrı süresi sınırı çalışır |
| `pre_human` | 2 -> `pre_human_back` | Doğru |
| `third_party` | 8 -> `fin_no_callback` | `cb_check -> third_party` her turda giriş sayacını sıfırlar; cap'e ulaşılmaz; `CB_MAX_TURNS=6` (bodies.py) gerçek sınır, ardından `outside` ve `fin_no_callback`: doğru sonuç |
| `negotiate_say`, `negotiate` | 6 -> `decide` | `always -> decide` ile aynı; ölü. Pazarlıkta tur sınırı kodla: `blank_run>3` evasive/no, `unclear_run`, `stop>=2`, `hardship_repeats>2`; "Can you do that?" ~20 kez gibi uzun döngüler yalnızca süre sınırıyla durur |
| tüm çağrı | `maxCallDurationSeconds` (kit 420; final bilinmiyor) | `exceeded-max-duration` (T16: A PUB-06) kapanışta `close_call` fallback'ı `no_promise/refused` (mid-pazarlıkta) veya `not_reached/unclear`; kabul edilmiş ama kaydedilmemiş bir söz kaybolur |

Yumuşak süre koruması yok (`{{call.duration}}` hiç kullanılmıyor). Seçenek (deney, probe 5 gerekli): `decide`te kalan süre azaldığında (eşik `maxCallDurationSeconds`ten, finalde bilinmediği için çağrı değişkeni olarak gelirse) kabul edilmiş ürün varsa read-back'i tek cümleyle isteyip kapanışa gitmek. Gerçeklik değeri düşük (T16 düzeltildi), "deney" olarak bırakıldı.

---

## 3. Alt başlık soruları: kısa cevaplar

(a) Prompt kırılganlığı. Uzun prompt: `identity` task ~2.3 KB, 12+ koşullu talimat; birden çok alan aynı anda: `negotiate_say` 6 değişken; çakışan kategoriler: `reply_kind` (LP-9), `id1` (declines/question/unclear arasındaki "what is this about" kuralı örtüşüyor, `decline_check` koduyla kısmen kapatılıyor); yasak kelimeyi modele bırakma: açılış tts (güvenli), ama `identity/third_party/pre_human` promptlarında yasak kelime listesi modele veriliyor ("never say 'collections', 'debt', ... 'loan'"): listeyi vermek kelimeyi çağrıştırabilir; 700+ kayıtlı ajan satırında ihlal yok (grep: 0), yine de Türkçe'de çekimli biçimler ("borcunuz") listede olmayacak (LP-1/7). Tam cümleye güvenme: negotiate "exact" talimatı (LP-3); sayı/tarih uydurma: kodla verilen metin dışında model rakam söylememeli (sistem promptu), gözlemde tutar/tarih bozulmadı.

(b) Beklenmeyen extraction. Null/geçersiz enum -> `read_reply` `unclear` (sağlam). İki niyet: ret+soru (düzeltildi, 466c13d), "yes but only the card" (`_yes_products`), tarih+tutar+yes (`offer`), çoklu ürün tek cümlede (`about_product` "p1, p2" -> ilk ürün; `ALL_WORDS` yalnız İngilizce), kısmi doğum tarihi (`incomplete`), kısmi telefon (LP-6), kesik tutar (`_pieces`). Eksik: `who1/id1/hum1/tp_reply` için "değer gelmedi" yolu (LP-4); `reply_kind` için karşı doğrulama yalnız kesik mesajlarda (LP-9).

(c) Çıkmaz/sonsuz döngü/cap. Bkz. LP-5, LP-13. Hiçbir geçişsiz çıkmaz yok; `after()` her zaman `negotiate_say` always ile biter; passthrough döngüleri kod sayacına bağlı (LP-5).

(d) Gecikme: tur başına model adımı (platform "3 çağrı" modelinden, ham olay `raw_response` kanıtı ile):

| Tur | Extraction çağrıları | Dialogue çağrıları | Toplam sıralı model adımı |
|---|---|---|---|
| opening (net onay) | 1 (`who1`) | 0 (`tts` girişli sonraki düğüm) | 1 |
| opening (belirsiz) -> opening2 | 1 | 1 (`on_enter: llm`) | 2 |
| kimlik cevabı (tam) | 2 (enum + metin) | 1 (tool kararı) + tool sonrası konuşma (kimlik tamamlanınca yok, sonraki düğüm tts) | 3 (kimlik tamam: 2) |
| kimlik küçük konuşma / kesik | 2 | 1 | 3 |
| pazarlık turu | 2 (4 enum + 2 metin) | 0 (`negotiate_say` always -> decide -> tts) | 1-2 |
| pazarlık + soru | 2 | 1 (`negotiate`) | 2-3 |
| third_party | 2 | 1 (`third_party` LLM) | 3 |
| kapanış | 0 | 0 (`end_call` terminal sabit) | 0 |

Doğruluğu azaltmadan birleştirme/kaldırma adayları: LP-2 madde 1 ve 2, LP-3 madde 1, 3, 4, LP-4 deneyi. Yasak: kimlik turunu modelsiz yapma (LP-2 madde 3) kanıtsız.

(e) Yük: LP-12. (f) Değişken/durum boyutu: `neg_state` JSON ~0.3-1.5 KB, `product_brief` 132 B, `date_table` 1.6 KB; yalnız `system.conversation` (11.4 KB/60 tur) ve platformun akış anahtarları (header, `_response.*`) büyüyor; `ensure_ascii` Türkçe şişirmesi LP-12'de. Sınırı olan bir değişken bulunmadı (belgede yok).

(g) Dürüstlük yolu. Tool hatası sonrası: `create_promise` başarısız -> bir yeniden deneme -> `record_result` kodu "could not record" cümlesini yazar (model yok); `rec_err` "already recorded" tutulan yazma sayılır; kapanışta `final_say` kod; `goodbye` terminalde `{{final_say}}` (spec dili + İngilizce motor cümleleri, LP-1). Kimlikten önce söylenenler: açılış tts (spec; `forbidden` yok), `fin_*` kapanışları tts/kod; yasak kelime riski yalnız `identity/third_party/pre_human/opening2` LLM cümlelerinde (LP-3). Akraba durumu: `third_party` LLM; kodda `fin_deceased` "I am sorry for your loss." (spec); kimlik soruları sorulmaz (prompt). Ses: "bağlantı kötü" iddiası (LP-3) küçük dürüstlük riski. Hang-up: close_call fallback (LP-11).

(h) Dil: LP-1. Kısaca: Türkçe üreten = spec `lines` (yalnız brief Türkçe ise ve çıkarıcı kopyalarsa); motor cümleleri ve LLM İngilizce; sistem promptu "Speak English"; müşteri, judge ve tts/stt (`tr`) ile uyumsuz.

(i) Çalıştırmadan çalıştırmaya varyans kaynakları (üç koşunun üçü de geçmeli):
1. `temperature 0.7`, kontrol dışı. 2. Serbest LLM yüzeyleri (LP-3): yorum/sempati, "bağlantı kötü", `negotiate` önekleri, soru cevapları ("discount" gibi). 3. Extraction güveni: `min_confidence 0.5` (confidence token olasılığı) ve sınıf örtüşmeleri (LP-9). 4. Platform yavaşlığı -> hang-up (LP-2); kitin kendi uyarısı: "median > 10 s" ise koşu atılabilir (`--rerun-if-slow`); final değerlendirmesinde bu olmayabilir. 5. Persona simülatörü: ~%45-57 kesik mesaj (talkative), yapışkan; ajan metninden bağımsız. 6. Judge: tekrar sayımı ("asked again once"), "offered twice" gibi kelime-tabanlı sayımlar (T3, T7). Her biri 3x'te bağımsız olduğundan başarı olasılığı p^3 (p=0.9 -> 0.73).

(j) Smoke test: LP-7.

---

## 4. Başarısızlık sınıflandırması: bizim mi platform mu?

`kit/results/*.json` ve `docs/research-talkative` (otomatik özet: `out/audit-scratch/lp07/sumres.py`):

| Koşu / vaka | Neden | Sınıf |
|---|---|---|
| style4-a-r3 (medyan yavaş tur 25 s, max 60.6 s): PUB-01, PUB-03 | müşteri ~60-69 sn'de kapattı, identity extraction 17.7 -> 50.1 s | Platform (yavaşlık) + bizim kimlik zinciri uzunluğu (LP-2) |
| style4-a-r3 PUB-05 | `opening2` LLM "sorry to hear ..." + sonra hardship sempatisi = iki sempati | Bizim (LP-3), prompt-yalnız düzeltme (e2948ce) |
| style4-a-r3 PUB-06, style4-a-r2 PUB-06 | `exceeded-max-duration`: "Can you do that?" döngüsü + kesik cevaplar | Bizim motor (T16, düzeltildi: `offer_day_question`) + persona yapışkanlığı |
| style4-a-r3 PUB-07 | "that Tuesday" yanlış tarih | Bizim (T19, düzeltildi) |
| style3-b-talkative PUB-11, style2-b/a-talkative | kimlik cap'i 16 / `declines` yanlış okuma | Bizim (T1/T2, düzeltildi: 60, `decline_check`) |
| style2-b-talkative PUB-13, exp-wait2 PUB-12 | 12-67 aynı read-back, kesik cevap, `decide` yük sınırı, `close_call outcome ""` | Bizim (T4/T5/T11, düzeltildi) + persona |
| style2-b-talkative PUB-12 | "asked again twice", "3 attempts" | Bizim + judge sayımı (T3/T7) |
| gen-a-1 PUB-10, gen-a-2 PUB-04, PUB-05, gen-b-1 PUB-12 | LLM sürümlü eski yüzeylerde "recorded" hatası, robot sorusunda cevapsız, ikna denemesi, döngü | Bizim (LLM yüzeyi; motor kodlanınca gitti) |
| README 11 Ekim `f3d74fa` A 5/10 -> 3/5 geçti | her başarısızlık hang-up (60 sn sessizlik); "Yes, this is Owen Park" 4-6 dk | Platform; LP-2/LP-4 maruziyeti azaltır |
| 11 Ekim `ab8f89b` sonrası iki stil hatası | ret+soru -> `question`; yıl isteme | Bizim (466c13d, düzeltildi) |

Ders: kalan başarısızlıkların bizim tarafımızdaki kök nedeni hep "modelin serbest üretimi veya sınıflaması"; platform kaynaklı olanlar model adımı sayısına orantılı.

## 5. Doğrulama gerektiren (ağ yok, bu denetimde yapılamadı) probe listesi

1. `pre_process_code` hook'unda `vars['system.conversation']` var mı (LP-2).
2. Hang-up opening'de `close_call` `{{final_outcome}}` tanımsız: model ne dolduruyor (LP-11).
3. `reasoning_override: "disabled"` experimental-5'te gecikmeyi/sınıflamayı etkiliyor mu (LP-8).
4. Passthrough `conversation` düğümü (`on_enter: llm` + `passthrough`) desteklenip desteklenmediği (LP-3 madde 3, "cevabı LLM, sabit cümleyi tts" ayrımı için).
5. `call.duration` kod düğümünde okunabiliyor mu (LP-13).
6. Türkçe içerikle `code_exec` kod+veri baytı (LP-12).
7. Platformun passthrough döngü üst sınırı (LP-5).

## 6. Önerilen uygulama sırası

1. LP-1 (dil paketi, motor cümleleri build-time lokalizasyonu, model tarafı normalize yedek alanlar). Bu olmadan Türkçe final için diğer hiçbir şey anlamlı değil.
2. LP-3 madde 1, LP-2 madde 2 (opening2/identity küçük konuşma tts): ucuz, doğruluk nötr, varyansı ve bir model adımını azaltır.
3. LP-2 madde 1 + probe 1 (`id_heard` kaldırma).
4. LP-4 `open_fallback`, LP-5 sayaç korumaları, LP-6 `partial` kapısı, LP-7 smoke ikinci tur, LP-11 enum, LP-10 açıklama.
5. LP-8 deneyi, LP-13 yumuşak süre (kanıt gelirse).
