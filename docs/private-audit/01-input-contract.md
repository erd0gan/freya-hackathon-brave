# 01 - Girdi sözleşmesi (input contract) denetimi

Kapsam: final girdisi (belge KLASÖRÜ + mock URL) ile `builder/build.py` arasındaki boşluk.
Yöntem: kod okuma + `out/audit-scratch/ic/` altında gerçek fixture'lar (docx/xlsx/pdf/md), ağ çağrısı yok,
repo dosyalarına dokunulmadı. build.py denemeleri `out/audit-scratch/ic/repo/` altındaki bir KOPYADA koşuldu.
Fixture ve prototip okuyucular: `out/audit-scratch/ic/{mkfix.py,readers.py,pdfcmap.py,FreyaBank-Ekstre-Borc/}`.

Ground truth (organizatör sayfası): final komutu `python3 builder/build.py FreyaBank-Ekstre-Borc/ https://your-mock.workers.dev`;
klasör = analysis, flow drawing, announcements, service contract, test customers, Q&A, 6 örnek konuşma (Türkçe) + BRIEF.md + README.md;
"Fixing a crash on a file type your builder did not expect is allowed" (yani dosya tipi çökmesi düzeltmesi serbest, ama
final içeriği koda yazılamaz). Araçlar bizim mock'a (mock_url) gider.

Özet: builder bugün yalnızca "tek .md + tek openapi.json" girdisini tanıyor. Final girdisinin NEREDEYSE HER parçası
(klasör, mock URL, docx/xlsx/pdf, sözleşmenin belge olması) ilk satırlarda çöküyor. Bu şekilde final koşusu
`status=error`, sıfır workflow ile biter.

---

## IC-1 (P0) Klasör argümanı: build.py ilk okumada çöker

Kanıt: `builder/build.py:442-445` (`brief_path = Path(argv[1])`, `slot = brief_path.stem`),
`build.py:492` (`brief_path.read_text(encoding="utf-8")`). Klasör okuma yolu hiç yok; `grep -rn "zipfile|docx|xlsx|pdf|iterdir|glob|is_dir" builder/` (stresslab hariç) = 0 sonuç.

Repro (kopya repoda, anahtarlar silinmiş, ağ yok):
```
python builder/build.py ../FreyaBank-Ekstre-Borc/ https://x.workers.dev
RUN  start ... brief=..\FreyaBank-Ekstre-Borc tool_api=https:\x.workers.dev brief_sha256=unreadable(PermissionError) tool_api_sha256=unreadable(OSError)
END  ... model_calls=0 status=error: PermissionError: [Errno 13] Permission denied: '..\\FreyaBank-Ekstre-Borc'
```
(POSIX'te `IsADirectoryError`.) Sıfır model çağrısı, sıfır workflow.

Neden önemli: finalin tek komutu tam olarak bu. Çıktı yok = skor 0.

Öneri (genel): `ingest(path)` katmanı: dosya, klasör (recursive, sıralı), `.zip` (zipfile; `__MACOSX/`, tek üst klasör, cp437/utf-8 ad
sorunları) kabul etsin. Önce README.md (belge haritası) + BRIEF.md, sonra kalanlar. Her belge `=== FILE: yol (tip, boyut) ===` başlığıyla
birleşsin (IC-7). Tek bozuk dosya tüm koşuyu öldürmesin (dosya başına try/except, trace'e "okundu/atlandı + neden").

## IC-2 (P0) İkinci argüman mock URL'i artık openapi yolu değil; mock URL hiçbir yerde kullanılmıyor, araç URL'leri yanlış

Kanıt: `build.py:442` (`api_path = Path(argv[2])`), `build.py:493` (`json.loads(api_path.read_text())`),
`openapi_tools.py:248-249,283` (`base = servers[0].url`, `url = base + path`), `generate.py` `freya_tool(... "url": {"default": t.url})`
(`openapi_tools.py:454`). `grep -n "mock\|FREYA_BANK_TEAM_KEY\|servers" builder/fb/generate.py` -> yok; builder hiçbir yerde
mock_url almıyor.

Repro 1: `Path("https://x.workers.dev")` Windows'ta `https:\x.workers.dev`, POSIX'te `https:/x.workers.dev` olur (çift slash kaybolur) ->
`read_text` OSError -> `status=error` (IC-1 ile aynı çıktı satırı).
Repro 2 (workflow tarafı): `kit/tools/openapi.json` ile üretilen workflow'daki 7 araç URL'i
`https://freya-bank-mock.freyavoice.workers.dev/v1/<tool>` (organizatörün mock'u). `servers` anahtarını silince
`parse_openapi` -> `url='/get_account'` (göreli), üretilen workflow'da `"url": "/verify_identity"`; çağrı anında hiçbir host yok.

Neden önemli: sayfa: "its tools call your mock, so a wrong or unreachable mock fails those conversations". Sözleşmedeki
`servers` ya yok, ya organizatörün kendi adresi, ya da (md/docx sözleşmede) hiç yok. Mock URL CLI'dan gelip `base`'i HER ZAMAN ezmeli.

Öneri: `argv[2]` http(s):// ile başlıyorsa mock_url; aksi halde (geriye uyum) openapi yolu. `parse_openapi(spec, base_override=mock_url)`;
`base.rstrip("/")` + sözleşmedeki path öneki (örn. `/v1`) kuralı net olsun (sözleşme `/v1/x` diyorsa mock_url + `/v1`?). Bunu
bir sözleşme-türevli "base path" olarak sözleşmeden al, sabit yazma. Trace'e "tool base = ..." yaz. Auth: sözleşme bir header/anahtar
istiyorsa (`security`, "X-API-Key") `freya_tool.headers`'a default olarak eklenmeli; şu an yalnızca `Content-Type` var
(`openapi_tools.py:456-460`, `grep -i "security|authorization|api.key" builder/fb` = 0). Kendi mock'umuz olduğu için en kolay yol:
mock anahtar istemesin, ama builder yine de sözleşmedeki `securitySchemes`/header'ı taşımalı (genel kod).

## IC-3 (P0) docx / xlsx / pdf / görsel okuyucu yok (flow drawing dahil)

Kanıt: builder'da sıfır belge ayrıştırıcı (yukarıdaki grep). `kit/README.md` "raw bank material (a mail, a policy extract, a memo...)"
diyor; sayfa final klasöründe Markdown, Word, PDF, Excel olacağını söylüyor.

Fixture'larla denenen stdlib fizibilitesi (`readers.py`, `pdfcmap.py`):
- DOCX (`zipfile` + `ElementTree`, ~25 satır): çalışıyor. Paragraflar, tablolar (satır başı `|`), `w:tab/w:br`, izlenen değişiklik
  (`w:del/w:delText` atılırsa "Eski oran %20 -> %40" doğru çıkıyor: "%40"). Çıktı: `Asgari ödeme oranı %40 ...`, `Gün | Limit`,
  `1-30 | ₺5.000`. Eksik kalanlar (gerçek Word dosyalarında olur): üstü çizili (`w:strike`/`w:dstrike`) kaldırılmış kurallar,
  `mc:AlternateContent` text box'ta Choice+Fallback ÇİFT metin, `word/comments.xml`, dipnot, üst/alt bilgi, numbering.xml etiketleri
  ("1." kaybolur), iç içe tablo, gömülü resim.
- XLSX (`zipfile` + `ElementTree`, ~25 satır): çalışıyor (sharedStrings, inlineStr, çok sayfa, formül önbellek değeri). Tuzaklar:
  tarih hücresi ham seri sayı döner (`46310` yazdı; gerçek tarih için `styles.xml` numFmt'e bakıp `1899-12-30 + n`), gizli sayfa,
  birleşik hücre, zengin metin `<r><t>`, `.xls` (ikili) ve `.xlsb` desteklenemez.
- PDF: ham regex ile `Tj` okumak ÇÖPTÜR. Edge ile üretilen (Word/Skia benzeri Identity-H) Türkçe PDF'te naif okuma glif kimliği
  verdi: `['002C015D01CC0175011E', '019A0003005E0182', ...]`. ToUnicode CMap çözen ~60 satırlık prototip düzgün Türkçe verdi
  (`'Hizme','t Sö','zleşmesi' ... 'ağüşıöç ₺5.000.'`, kerning parçalı, satır sırası/tablo düzeni garantisiz). ObjStm
  (sıkıştırılmış nesne akışı) ve taranmış (görüntü) PDF'ler stdlib ile olmaz. Makinede `pdftotext` var ama final koşucusunda olması garanti değil.
- Flow drawing: büyük olasılıkla PNG/JPG, `.drawio`/`.vsdx`/`.svg`/PDF. stdlib ile OCR yok. `.vsdx` ve `.drawio` (ZIP/XML) metin çıkarılabilir;
  PNG/JPG sadece çok-modlu (vision) model ile okunur.

Neden önemli: akış çizimi ve duyurular çoğunlukla bu formatlarda; hiçbiri okunmazsa ajan sadece BRIEF.md ile kalır, kuralların bir kısmı hiç görülmez.
"Dosya tipi çökmesi" düzeltmesi serbest ama BAŞTAN hazır olmalı: final 2 saatlik pencerede yazmak risk.

Öneri (genel, belgeden bağımsız): 3 kademe, hepsi isteğe bağlı bağımlılık + stdlib yedek:
1. Yerel metin: md/txt/csv/json (BOM+cp1254 bak IC-9), docx/xlsx/pptx/odt (zipfile+xml; strike ve altKonu notu), pdf (varsa `pdftotext -layout`,
   yoksa ToUnicode-farkında stdlib yedeği).
2. Model-yerel: PDF ve görüntüler için Anthropic Messages `document`/`image` base64 blokları (stdlib urllib ile gönderilebilir).
   Bugünkü `llm.py:74-79` mesajları düz `str` varsayıyor (`system` birleştirmesi `"\n\n".join(m["content"])`); içerik-blok listesi
   desteklenmeli, OpenRouter yedeği blok desteklemiyorsa metin yoluna düşmeli.
3. Okunamayan her dosya trace'e "UNREAD: ad, neden" satırı olarak yazılmalı (gözden kaçmasın).

## IC-4 (P0/P1) "Service contract" bir belge; araçlar nereden gelecek? Yedi araç adı sabit, eksik şema sessizce bozuk workflow üretir

Kanıt:
- `build.py:493` yalnızca JSON OpenAPI okur. YAML (OpenAPI'nin yaygın biçimi), md tablo, docx tablo, Postman koleksiyonu -> JSONDecodeError -> `status=error`.
  (Repo'nun pyproject'unda `pyyaml` var ama "stdlib only" iddiası ve builder importları yaml kullanmıyor.)
- `generate.py:948,999,1008,1043,1054,1067`: `tools["verify_identity"|"get_account"|"create_promise"|"schedule_callback"|"transfer_to_agent"|"close_call"]`.
  Repro (spec = brief-ab çıktısı, tools'dan biri düşürülerek):
  `drop schedule_callback -> KeyError 'schedule_callback'`, `transfer_to_agent -> KeyError`, `get_account -> KeyError`; `add_note -> build ok`.
  build.py bu KeyError'u `generation failed ... nothing published` olarak yakalar. Sayfa "service contract"ın pratikteki yedi araçla aynı kaldığını
  söylemiyor; kit README bunu yalnızca "final briefs" (pratik formatı) için söyledi. Türkçe "ekstre borcu hattı" farklı bir servis seti kullanabilir.
- Gövde/yanıt şeması eksikse (örn. sözleşme düz metin tablo) hiçbir hata yok: tüm operasyonlardan `requestBody` silinince `parse_openapi` 0 alan,
  `build()` "ok", `validate()` = `0 errors, 13 warnings`, üretilen workflow hiçbir araç gövdesinde `customer_id` taşımıyor.
  Yani ajan yayınlanır, hiçbir çağrıda kimlik/tutar gönderemez ve bunu hiçbir kontrol yakalamaz.

Neden önemli: finalde "sözleşme" bir belge ve biz mock'u ondan yazıyoruz; builder aynı sözleşmeyi tutarlı okumalı yoksa agent mock'a yanlış istek atar.

Öneri: (a) sözleşme dosyası JSON/YAML-OpenAPI ise direkt (YAML için küçük stdlib alt-küme ayrıştırıcı ya da modelden JSON'a çevirtme);
(b) değilse bir model adımı "sözleşme -> OpenAPI 3 JSON" (operationId, path, method, body/query alanları, enum, yanıt alanları, base path) üretsin, sonra `parse_openapi` ile
doğrulansın; (c) yedi RÖL (verify, account, promise, note, callback, transfer, close) adlarla değil sözleşmeden anlama göre eşlensin (model + doğrulama), rol eksikse
"o özelliksiz" yol (örn. callback yok = callback seçeneği sunulmaz) yerine çökme olmasın; (d) `build()` sonrası "kimlik alanı/zorunlu gövde alanları araç gövdesinde var mı"
kapısı (validator hatası, uyarı değil).

## IC-5 (P1, Windows'ta P0) Trace dosyası locale kodlamasıyla yazılıyor: `₺` ve benzeri karakterde build ÇÖKER

Kanıt: `builder/fb/trace.py:45` `with self.path.open("a") as f:` (encoding yok), `:38` `path.write_text("")`. Bu makinede
`locale.getpreferredencoding() = cp1254`, `sys.flags.utf8_mode = 0`.
Repro (kopya repoda): `Trace(...).note("spec decision: asgari ödeme ₺500")` ->
`UnicodeEncodeError: 'charmap' codec can't encode character '₺'` (`trace.py:46`). Ayrıca Türkçe harfli satır dosyada cp1254 baytı olarak
yazılıyor; UTF-8 okuyunca bozuk (`t�m g�n`). cp1252/cp1250 locale'de `ş ğ ı İ` bile çöker. `→ ≥ ✓` gibi karakterler cp1254'te yok.
Notlar `extract()` içinde brief'ten alıntı yapıyor (`spec decision: ...[:300]`, `spec warning ...`), yani Türkçe/₺ içeren brief'te
çökme olasılığı yüksek; çökme `run()` içinden `main()`'e çıkar -> `status=error`, workflow yok.
Stderr yansıması güvenli (`backslashreplace`), sadece dosya yolu kırık. Diğer `write_text` çağrıları `json.dumps` (ASCII) olduğu için güvenli;
`print` ile stdout'a Türkçe/₺ basmak (bizim araçlarda) cp1254'te ayrıca çöker (kanıtlandı).

Öneri: `open("a", encoding="utf-8", errors="replace")`, `write_text("", encoding="utf-8")`, `main()` başında
`sys.stdout/stderr.reconfigure(encoding="utf-8", errors="replace")`; final komutuna `PYTHONUTF8=1` ve RUNBOOK'a ekle. Aynı ilke `kit/freya.py` `open(path,"w")` için geçerli
(json ASCII olduğundan zararsız).

## IC-6 (P1) Duyurular / geçerlilik tarihleri: kaynak sırası yok, derleme tarihi yok, değerler sabit; test "birkaç takvim günü"nde

Kanıt: `spec_extract.py:241-247` PROMPT: "a memo that changes a policy replaces the policy's value from its effective date; the call's date is the variable
`now`, so apply what is in force for calls being made now". Ancak: (1) modele hiçbir "bugün/derleme tarihi" verilmiyor (`grep -n "now" spec_extract.py` yalnızca prompt metni);
(2) belgeler arasında kaynak/tarih başlığı yok (build.py tek `brief` string); (3) spec şeması tek değer tutuyor, `effective_from` yok
(`grep -i "effective\|valid_from" spec_extract.py generate.py nodecode/` = yalnızca prompt metni); sayısal limit/oran/gün sayısı koda `cfg` olarak sabit gömülüyor.
Sayfa: ajana çağrının tarihi `now` değişkeni olarak verilir, "Every date rule uses it"; test "on several calendar days". Pratik koşuda hep 14 Ekim 2026 idi
(`public-test-set`), finalde değil.

Neden önemli: duyuru "20 Ekim'den itibaren X" derse, derleme anındaki model tahmini ("şu an hangi tarih") yanlış tarafı seçer; testin yarısı o tarihten önce, yarısı sonra olabilir.
Aynı kural iki günde farklı sonuç verecek ve tek statik değer biri geçip diğerini bozar. Modelin "şimdi" bilgisi eğitimden gelir, gerçek tarih 11 Ekim 2026 (kit `VERSION` = 10 Ekim 2026).

Öneri (genel): spec'e "zamana bağlı kurallar" kavramı: `{key, value, effective_from, effective_to?}` listesi; kod düğümü `now` ile seçsin. En azından
prompt'a derleme tarihi ver, belgeleri dosya adı/başlıktaki tarihe göre sırala ve her çelişkiyi `notes`'ta (trace'e "spec decision" olarak zaten düşüyor) kaynak+tarih ile kaydettir;
birden çok yürürlük tarihi varsa "tarihe bağlı alternatif" üret. Kural birbirine çelişiyorsa ve tarih belirsizse ilkeyi (önce duyuru, sonra politika; yeni tarih eskiyi ezer) tek yerde sabit tut.

## IC-7 (P1) Politika dışı belgeler (test müşterileri, örnek konuşmalar, Q&A) politika ile aynı çuvalda; grounding anlamsızlaşır; kaynak sınıflaması yok

Kanıt: `build.py:492` tek `brief` metni -> `PROMPT {brief}` (`spec_extract.py:1264-1272`) ve `observer` aynı metni kullanır. Rol ayrımı yok.
Sonuçlar: (a) Test müşterilerinin kimlik cevapları (doğum yılı, TCKN son 4 vb.) ve örnek konuşmalardaki ajan cümleleri "kural" gibi okunabilir
(model "Where the brief gives exact words, use them exactly" talimatıyla örnek konuşmadaki ajan cümlelerini `lines`'a kopyalayabilir; iyi niyetle ama örnek bir "kötü örnek" ya da tek müşteriye özgü olabilir).
(b) `grounding()` ve `_grounded()` (`spec_extract.py:1217-1256,1660-1700`) tatil tarihi/gün sayısı/kod değerini "brief'te var mı" diye arar; test müşterisi tablosundaki
rastgele tarihler ve sayılar bu denetimi boşa çıkarır (hayalet tatil de "grounded" görünür). (c) Müşteri verisi (isim, kimlik) spec/lines'a sızarsa `submission/*.spec.json` ve workflow'da kalır
(PII yok, sentetik, ama "final değerleri koda" kuralı açısından da hassas: sızan örnek isim/tarih builder çıktısında olmamalı).
(d) Q&A belgesi çoğu zaman politikayı netleştirir/değiştirir: yok sayılmamalı, ama hangi belge hangi türde, bilinmeli.

Öneri: belge başına rol (`policy`, `announcement`, `contract`, `flow`, `qa`, `examples`, `test_customers`) -- önce README.md'nin belge haritasından
(modelle çıkar), yoksa dosya adı/uzantıdan. Prompt'ta bölümler: POLİTİKA kaynakları (dilimi sırasıyla), DUYURULAR (tarihli), SÖZLEŞME, ÖRNEKLER "yalnızca üslup ve
senaryo çıkarımı, kural değil; değerleri kopyalama" etiketiyle, TEST MÜŞTERİLERİ builder'a hiç verilmesin (mock yazmak için; builder'ın ihtiyacı yok) ya da yalnızca şema/sütun
adı düzeyinde. `grounding` yalnızca politika+duyuru+sözleşme metni üzerinde çalışsın.

## IC-8 (P2) Grounding ve geçersiz kılma sezgileri yalnızca İngilizce: Türkçe belgede her tatil "bulunamadı"

Kanıt/Repro (`spec_extract.py:1129-1256`): Türkçe brief `29 Ekim 2026 Cumhuriyet Bayramı, 1 Ocak 2027 ...`, `deadline` `otuz gün`, `09:00 - 18:00`:
```
GROUNDING: holiday 2026-10-29 is not found in the brief
GROUNDING: holiday 2027-01-01 is not found in the brief
GROUNDING: band 'x' deadline_days=7 ... attempts=3 is not found in the brief
callback_business_days('geri arama: iş günü içinde') = False
_number_in(30,'otuz gün') = False ; _number_in(3,'üçüncü') = False
"İSTANBUL".lower() == 'i̇stanbul' (U+0307 birleşik nokta)  -> "istanbul" in ... = False
```
`_MONTHS` İngilizce, `_NUMWORDS/_ORDWORDS` İngilizce, `_callback_business_days` "business day" arıyor (Türkçe "iş günü" yok -> `business_days_only` zorlaması ASLA tetiklenmez), saat için `am/pm/o'clock`.
Etki: her koşuda `grounding()` doğru tatillere "hata" der -> gereksiz onarım turu (`extract()` ikinci model çağrısı, "keep it only if the brief really says it")
ve modelin DOĞRU tatilleri atma riski; Türkçe `iş günü` kuralından gelen "hafta sonu geri aramayı kapat" güvencesi çalışmaz. (Gerçek dil sorunu için IC-13.)

Öneri: aylar/sayı sözcükleri için dil tablosu (tr+en), `casefold()`/Türkçe-farkında küçük harf (`İ`->`i`, `I`->`ı` normalleştir), `iş günü|hafta sonu|cumartesi|pazar|tatil`.
Bunlar "belgeden değer" değil, dil bilgisi: kural ihlali olmaz.

## IC-9 (P2) Kodlama ve dosya dayanıklılığı

Kanıt: `build.py:492-493` `read_text(encoding="utf-8")`. Repro: `utf-8` ile okunan BOM'lu JSON -> `JSONDecodeError Unexpected UTF-8 BOM`;
Türkçe Excel'in "CSV" dışa aktarımı (cp1254) -> `UnicodeDecodeError 'utf-8' ... byte 0xfc`; BOM'lu .md başa `﻿` taşır (zararsız ama kirli).
Word kilit dosyaları `~$x.docx` ve `__MACOSX/._x` zipfile'da `BadZipFile`. Önerilen: `utf-8-sig` -> `utf-16` (BOM varsa) -> `cp1254` -> `latin-1` sırası,
bilinmeyen/bozuk dosya atlanır ve trace'e yazılır.

## IC-10 (P2) Slot adı, çıktı yolları, trace

Kanıt: `build.py:443` `slot = brief_path.stem`. Repro: `Path("FreyaBank-Ekstre-Borc/").stem` = `FreyaBank-Ekstre-Borc` (iyi);
`Path(".").stem` ve `Path("./").stem` = `''` -> `traces/.log`, `submission/.spec.json`, Freya workflow adı `" 1011-..."`, `.freya-agents.json` anahtarı `''`;
`Path("FreyaBank.Ekstre.v2").stem` = `FreyaBank.Ekstre` (nokta sonrası kesilir); `.zip` argümanı `stem` verir ama zaten okunamaz (IC-1).
Sayfa örneği: `workflow: submission/final.workflow.json`, `trace: traces/final.log` ("example", değiştirilebilir) ve `... | tee traces/final.log` — builder ayrıca
`traces/<slot>.log` yazıyor; ikisi de aynı satırları içerir, sorun değil, ama gönderim dosyası adı slot'tan türüyor (`submission/<klasör>.workflow.json`).
Öneri: `slot = re.sub(r"[^\w.-]+","-", name.rstrip("/\\").split(...)[-1])`, boşsa `"final"`; `Path.resolve()` ile `.` çöz; `.stem` yerine `.name` (zip ise uzantı at).
`keep_previous_run` aynı slot'u koruyor (iyi: yeni koşu eski izleri `runs/<slot>/<zaman>/` altına taşıyor). Not: bu auditing sırasında `submission/brief-a.*`/`brief-ab.*` dosyaları
başka bir süreç tarafından `runs/` altına taşınmış görünüyor (yan etki beklenen davranışla uyumlu, ama bu klasörleri elle sayarken dikkat).

## IC-11 (P2) Boyut / token / süre

Kanıt: `extract()` tek istem (sınırsız `brief`), `llm.chat(max_tokens=16000, timeout=300)`; `observer` AYNI istemi paralel ikinci kez çalıştırır (maliyet x2).
Ölçüm: brief-a+b (12.5k karakter) -> istem ~28.4k karakter (~8k token); son gerçek koşu 52 sn / $0.28 (`traces/brief-ab.log`), yani bugün zaman sorun değil.
Final klasöründe 6 örnek konuşma + test müşteri tablosu + analiz + Q&A gelirse yüz binlerce karakter olabilir. Koruma yok: (1) modelin bağlam sınırını aşan girdi için kırpma/kısaltma yok;
(2) tek belge okunamazsa tüm istek yok; (3) çıktı 16000 token'ı aşan bir spec'te JSON kesilir (`_parse` -> SpecError -> build durur).
Öneri: belge başına karakter bütçesi (tablolar satır sınırı + "ilk N satır, toplam M" notu), rol bazlı öncelik (IC-7), ham metni belge sırasıyla koyup
bağlam %60'ı aşarsa en düşük öncelikli rolü (örnekler, test müşterileri) önce kırp; `max_tokens` gerektiğinde yükselt / yanıt "stop_reason=max_tokens" ise yeniden iste.
30 dk bütçe (`BUDGET_S`) bu girdi için yeterli ama belge dönüştürme (PDF/görsel model çağrısı) süresi bütçeye dahil edilmeli.

## IC-12 (P3) Küçük boşluklar

- Mock erişilebilirlik/sözleşme ön kontrolü yok: builder mock_url'e hiç istek atmıyor. Sayfa: "keep it running until the results are out". Önerilen: koşu başında
  mock'a bir kimlik-doğrulama probe'u (sahte müşteri) + yanıt alanlarının sözleşmeyle uyumu kontrolü, trace'e satır; hata durdurmasın (uyarı).
- `proof.py` / `quotes_found` brief'in markdown yapısını (numaralı kural) varsayar; birleştirilmiş çok-belge metninde gürültülü ama `try/except` ile korunuyor (`build.py:380-387`) - risk düşük.
- `blocking(spec)` (`spec_extract.py:940-957`) bant (min/max days late) ve kimlik sorusu yoksa "nothing published". Ekstre-borç hattı "gün gecikme bandı" mantığına oturmayabilir
  -> sıfır workflow. Bu bir alan-genelleme sorunu (başka denetçinin kapsamı), burada yalnızca "girdi sözleşmesi bunu tetikleyebilir" notu.
- `smoke()` örnek müşteri adı İngilizce, yasak sözcükler İngilizce (`build.py:261`).

## IC-13 (P0, çapraz-referans) Türkçe: spec istemi "plain English", varsayılan satırlar ve konuşma ayrıştırıcıları İngilizce

Kanıt: `spec_extract.py:252` ("Sentences in 'lines' are spoken to the customer: short, plain English"), `spec_extract.py:1330-1360` varsayılan satırlar ("I am connecting you with a colleague now."),
`generate.py`/`nodecode/*`/`spec_extract.py` içinde `grep -i "turkish|türk|language|locale"` = 0; `speech_lib.py` ay/gün adları ve `first thing Monday`, `no to Monday` gibi İngilizce kalıplar.
Final Türkçe; müşteri tutar/tarihi sözle ("on beş bin", "ayın yirmisi", "cuma") söyleyecek (kit README `--style in_words`). Dil denetiminde ayrıntılı ele alınmalı; bu rapor yalnızca
girdi tarafında "talimatı değiştir + dil tablosu" gerektiğini işaretler. Türkçe belge verildiğinde builder'ın kendi 'lines' çıktısı İngilizce kalır.

---

## Sıralı en önemli 5

1. **IC-1 + IC-2 (P0)**: Klasör + mock URL argümanı. Bugün `build.py <klasör> <url>` birinci saniyede `status=error` ile biter (PermissionError/IsADirectoryError, `https:\x...` yolu). Mock URL hiçbir workflow URL'sine yazılmıyor; araç
   URL'leri `servers[0]` (organizatör mock'u ya da göreli `/verify_identity`). Düzeltme küçük ve genel: `argv[2]` http ise mock, klasör/zip ingest, `parse_openapi(base_override)`.
2. **IC-3 (P0)**: docx/xlsx/pdf/görsel okuyucusu sıfır. docx+xlsx stdlib ile ~50 satır (kanıtlandı, prototip `readers.py`), PDF için ToUnicode-farkında okuyucu ya da model-yerel `document` bloğu,
   flow drawing için model-yerel `image` bloğu. Okunamayanı trace'e yaz. Finalden ÖNCE hazır olmalı.
3. **IC-4 (P0/P1)**: Servis sözleşmesi bir belge, yedi araç adı koda sabit (`KeyError` -> hiçbir şey yayınlanmaz), şema eksikse sessizce `customer_id`'siz araç (validator 0 hata). Sözleşme->OpenAPI normalleştirici + rol eşleme + "gövde alanları var mı" kapısı.
4. **IC-5 (P1; Windows'ta fiilen P0)**: `trace.py` locale kodlamasıyla yazar; bu makinede (cp1254) tek bir `₺` build'i `UnicodeEncodeError` ile öldürür. Tek satırlık düzeltme (`encoding="utf-8", errors="replace"`) + `PYTHONUTF8=1`.
5. **IC-6 + IC-7 (P1)**: Duyurular tarihli kural değişimi ve politika-dışı belgeler. Spec şeması zamana bağlı kural taşımıyor, modele derleme tarihi/kaynak başlığı verilmiyor, test müşteri/örnek konuşma verisi aynı çuvalda
   ve `grounding` denetimini boşaltıyor. Belge rolleri + `effective_from` + `now` ile kod düğümünde seçim.

Çapraz not: IC-13 (Türkçe) bu denetimin dışında ama girdi düzeltmeleri yapılsa bile ajanın dili İngilizce kalıyor; dil denetçisinin bulgusuyla birlikte düşünülmeli.
