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
| 4 | `yargi.py` | Sadece artık | saniye | **sunucunun dışında** |

Dördüncü katman bilerek MCP sunucusunun dışındadır. Sunucu
deterministik kalır; yargı gerektiren dosyaları işaretleyip dışarıya
bırakır. Böylece deterministik katmanın vetosu korunur ve eskalasyon
oranı ölçülebilir olur.

## Ölçülen sonuç

24 dosyalık karma parti, 117 ms:

```
deterministik çözülen : 22/24
yargıya çıkan         :  2/24
eskalasyon oranı      : %8.3
```

## Çalıştırma

```bash
# uçtan uca gösterim (MCP protokolü üzerinden)
KESIF_KOK=/tmp/karma .venv/bin/python scripts/kesif_demo.py

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
  yargi.py         LLM yargı katmanı (sunucunun dışında)
  sunucu.py        MCP sunucusu, 7 tool
  formatlar/
    metin.py       csv / tsv / txt çözümleyici
```
