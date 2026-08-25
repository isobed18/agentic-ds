# Keşif

Veri hattının girişinde duran tanıma ve yönlendirme katmanı.
Türü bilinmeyen bir dosyayı alır, ne olduğunu ölçer, hangi akışa
gitmesi gerektiğine karar verir. Karar veremediğinde **uydurmaz**;
artık olarak işaretler ve sebebini yazar.

## Dört katman

| # | Katman | Ne yapar | Maliyet | Tür |
|---|--------|----------|---------|-----|
| 1 | Magika | Format tespiti | ~4 ms | deterministik |
| 2 | `sekil.py` | Yapısal şekil | mikrosaniye | deterministik |
| 3 | `kanit.py` | Kodlama, ayraç, sayı biçimi | mikrosaniye | deterministik |
| 4 | `yargi.py` | Sadece artık | human feedback hızı | **sunucunun dışında, human feedback ister** |

Dördüncü katman bilerek MCP sunucusunun dışındadır. Sunucu
deterministik kalır; karar veremediği dosyaları işaretleyip dışarıya
bırakır. Böylece deterministik katmanın vetosu korunur ve eskalasyon
oranı ölçülebilir olur.

Dördüncü katman bir LLM'e tahmin ettirmez, **doğrudan human feedback ister** —
önceden hesaplanmış kapalı seçenek listesiyle (tablo / belge / ağaç /
kapsayıcı / işlenemez), ana pipeline'ın `gates` katmanındaki
`build_human_prompt` deseninin aynısı. Gerekçe kural 6: bu çoğu zaman
bir *tercih* sorusu, bir *olgu* sorusu değil — modelin tahmin etmesi
değil, kullanıcının "buna odaklan" demesi doğru cevap. Bunun yan
etkisi: keşif katmanında artık bulut LLM bağımlılığı yok.

## Ölçülen sonuç

25 dosyalık karma parti (CSV, TSV, TXT, JSON, JSONL, XML, HTML,
Markdown, YAML, conf, log, hizalı rapor, PDF, XLSX, ZIP, ikili, boş):

```
25/25 doğru yönlendirme
deterministik çözülen : 22/25
human feedback'e çıkan:  3/25
eskalasyon oranı      : %12.0
```

Parti `ads.kesif.ornek_parti` tarafından **deterministik üretiliyor**;
ikili dosyalar depoda tutulmaz, çalışma anında oluşturulur. Sayı
`tests/test_kesif_olcum.py` ile regresyon testine bağlı — bir katman
bozulursa test kırılır.

Human feedback'e çıkan 3 dosya gerçekten belirsiz: tek kelimelik bir
metin, Magika'nın 0.953 güvenle yanıldığı 4 satırlık CSV, ve ikili mi
yanlış kodlanmış metin mi ayırt edilemeyen bir dosya.

## Görüntü ve taranmış belge (OCR)

Metni **seçilemeyen** bir tablo — internetten alınmış ekran görüntüsü ya
da taranmış PDF — OCR ile okunur. Tablo yapısı, kutu konumlarından
yeniden kurulur.

**Ölçülen Türkçe sınırı.** Gömülü tanıma modelinin sözlüğü 6623 karakter
ve içinde `ğ Ğ ı İ ş Ş ç Ç ö Ö` **yok**. Yani model Türkçe harfleri
üretemez; yerine ASCII benzerini koyar ve bunu sessizce yapar:

```
Şehir    -> 'Sehir'
Değişim  -> 'Degisim'
Iğdır    -> 'ngoa'     güven 0.828   <- YÜKSEK güvenle uydurdu
```

Son satır kritik: yanlış cevap düşük güvenle değil **yüksek güvenle**
geliyor, güven eşiğiyle elenemez — Magika'nın 0.953 hatasıyla aynı sınıf.

Bu yüzden sözlük her çalıştırmada ölçülür (`turkce_sozlukte_var_mi`) ve
Türkçe doğrulanamıyorsa **metin** human feedback'e çıkarılır. **Sayılar ve
tablo yapısı** bu sınırdan etkilenmez; `15.840.900` 1.000 güvenle okundu.
Türkçe destekli bir model takılırsa aynı kod otomatik doğrulanmış moda
geçer — eşik değil, sözlük ölçülür.

**Model değiştirilebilir.** Türkçe destekli bir model edinildiğinde tek
yapılacak `KESIF_OCR_REC_MODEL` değişkenini göstermek; kod değişmez,
çünkü yetenek eşikten değil sözlükten ölçülür. Aday bir modeli takmadan
önce denetlemek için:

```bash
.venv/bin/python scripts/kesif_model_denetle.py /yol/aday_rec.onnx
```

Ölçtüğüm iki aday: gömülü Çince+İngilizce model 10 harfi kaçırıyor,
PaddleOCR "latin" modeli 5 harfi (`ğ Ğ İ ş Ş`) kaçırıyor. İkisi de
yetersiz — ama artık bu bir **deneme sonucu değil, ölçüm**.

Kurulum ayrı tutuldu, **kurulu boyut ~245 MB** (opencv 125, onnxruntime 82):
`pip install -e ".[kesif-ocr]"`. Modeller pakete gömülü gelir, çalışma
anında **indirme yapmaz** (air-gap doğrulandı: soketler kapalıyken
0 bağlantı denemesi).

## XLSX

XLSX teknik olarak bir zip'tir ama **kapsayıcı değildir** — sayfalardan
oluşan bir tablo dosyasıdır. Sayfalar açılır, her birinin gerçekten tablo
olup olmadığı ölçülür (satır/sütun sayısı, başlık doluluğu) ve akış
sayfalardan üretilir. Birden fazla tablo sayfası varsa **hangisiyle
çalışılacağı bir tercih sorusudur**; seçenek olarak sunulur.

## Otomatik mod

Human feedback doğru varsayılandır, ama başında kimsenin olmadığı bir
toplu koşumda dosyanın süresiz beklemesi de bir arızadır. `otomatik=True`
verildiğinde sistem beklemez; **sabit bir kural zinciriyle** en makul
akışı seçer:

1. Şekil ölçümü varsa ona uyulur (`tablo` → tablo, `log` → belge, …)
2. Yoksa formata bakılır (`csv` → tablo, `json` → ağaç, …)
3. İkili içerik kanıtı varsa `işlenemez`
4. Hiçbiri yoksa `belge` — **veri atılmaz**, çünkü veri kaybı geri alınamaz

Üç kural da geçerli: **açık tercih** (kendiliğinden devreye girmez),
**deterministik** (model çağrılmaz, aynı girdi aynı çıktı), **gizlenmez**
(`kaynak="otomatik_varsayilan"`, hangi kuralın tetiklendiği yazılı).
Yani "tahmin yok" ilkesi bozulmaz: tahmin yapılıyorsa olduğu gibi
etiketlenir ve sonradan denetlenebilir.

## Çalıştırma

```bash
# tarayicidan canli gosterim (mentöre demo icin)
.venv/bin/python scripts/kesif_poc.py     # -> http://127.0.0.1:8600

# OCR modelinin Türkçe kapsamını denetle
.venv/bin/python scripts/kesif_model_denetle.py

# uçtan uca gösterim (MCP protokolü üzerinden, parti kendi üretilir)
.venv/bin/python scripts/kesif_demo.py

# Türkçe kodlama ve sayı biçimi kanıtı
PYTHONPATH=src .venv/bin/python scripts/kesif_tr_kanit.py

# sunucuyu tek başına
KESIF_KOK=$PWD .venv/bin/python -m ads.kesif.sunucu
```

## Tasarım kuralları

1. **Ölç, tahmin etme.** Her sonuç kanıtıyla birlikte döner.
2. **Kanıt yetmezse karar verme.** Artık olarak işaretle, sebebini yaz.
3. **Uzantıya güvenme.** Karar içerikten üretilir.
4. **Ayrıştırılabilen şey ölçülür.** `json.loads` ayrıştırıyorsa o JSON'dur.
5. **Ucuz geçiş herkese, pahalı geçiş seçilene.**
6. **Olgu sorusu ölçülür, tercih sorusu sorulur.**
7. **Yargı ölçümün yerine geçmez.** Kaynağı ayrı kaydedilir.

## Dosyalar

```
src/ads/kesif/
  model.py         Bulgu / Kanıt / Seçenek yapıları
  kanit.py         kodlama, ayraç, sayı biçimi ölçümü
  sekil.py         tablo / sabit genişlik / log / anahtar-değer / serbest metin
  yonlendirici.py  dört katmanı birleştirir, envanter ve seçenek üretir
  okuyucu.py       seçim uygulandıktan sonra okuma
  yargi.py         human feedback katmanı (sunucunun dışında, bulut LLM yok)
  sunucu.py        MCP sunucusu, 7 tool
  ornek_parti.py   ölçüm için deterministik karma parti üreticisi
  formatlar/
    metin.py       csv / tsv / txt çözümleyici
    pdf.py         metin katmanı var mı; yoksa gömülü görüntüyü OCR'a verir
    goruntu.py     OCR, tablo yapısı kurma, ölçülen Türkçe sınırı
    tablolu.py     xlsx/xlsm sayfaları — kapsayıcı değil, tablo dosyası
```
