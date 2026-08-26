"""
TURKCE VERI UYUMLULUGU — KANIT

Bu betik agentic-ds deposundaki GERCEK loader kodunu, Turkce dosyalarla
calistirir. Hicbir seyi taklit etmiyor: ads.intake.loaders.load_csv cagriliyor.

Calistir:
    PYTHONPATH=~/projects/agentic-ds/src .venv/bin/python scripts/kesif_tr_kanit.py
"""

from pathlib import Path

import pandas as pd

from ads.intake.loaders import load_csv

KLASOR = Path(__file__).resolve().parent.parent / "tests/fixtures/kesif"
KLASOR.mkdir(exist_ok=True)


def baslik(n, ad):
    print(f"\n{'=' * 74}\n{n}. {ad}\n{'=' * 74}")


# ---------------------------------------------------------------------------
# TEST DOSYALARINI URET
# ---------------------------------------------------------------------------

# 1) Turkce Windows kodlamasi (cp1254). Eski Excel ciktilarinin standardi.
tr_metin = (
    "sehir,aciklama\n"
    "İstanbul,Şubat ayı raporu\n"
    "Iğdır,çalışma özeti\n"
    "Şanlıurfa,dağıtım listesi\n"
)
dosya_cp1254 = KLASOR / "tr_cp1254.csv"
dosya_cp1254.write_bytes(tr_metin.encode("cp1254"))

# 2) Turkce Excel sayi formati: ; ayrac, virgul ondalik, nokta binlik
tr_sayi = (
    "musteri;tutar;oran\n"
    "Ali;1.234,56;0,75\n"
    "Ayse;12.500,00;0,40\n"
    "Mehmet;987,25;0,92\n"
)
dosya_sayi = KLASOR / "tr_sayi.csv"
dosya_sayi.write_text(tr_sayi, encoding="utf-8")


# ---------------------------------------------------------------------------
# KANIT 1 — ENCODING
# ---------------------------------------------------------------------------

baslik(1, "ENCODING — cp1254 dosya nasil okunuyor")

print("Dosyaya yazilan gercek icerik (cp1254 baytlari, dogru cozulmus hali):")
print("   sehir     : İstanbul | Iğdır | Şanlıurfa")
print("   aciklama  : Şubat ayı raporu | çalışma özeti | dağıtım listesi")
print()

tablo = load_csv(dosya_cp1254)
df = tablo.frame

print("Loader'in okudugu hali:")
for _, satir in df.iterrows():
    print(f"   {satir['sehir']:<12} | {satir['aciklama']}")
print()

print("Loader'in kaydettigi notlar:")
if tablo.issues:
    for issue in tablo.issues:
        print(f"   [{issue.severity}] {issue.code}: {issue.detail}")
else:
    print("   (hicbir not yok)")
print()

bozuk = [s for s in df["sehir"] if any(k in s for k in "ÞþÝýÐð")]
if bozuk:
    print(f"SONUC: {len(bozuk)} deger bozuldu. Ornek: {bozuk[0]}")
    print("       Hata YOK. Uyari YOK. Sadece yanlis harf.")
else:
    print("SONUC: karakterler dogru okunmus.")


# ---------------------------------------------------------------------------
# KANIT 2 — SAYI FORMATI
# ---------------------------------------------------------------------------

baslik(2, "SAYI FORMATI — Turkce Excel ciktisi")

print("Dosyadaki gercek degerler:")
print("   tutar : 1.234,56  |  12.500,00  |  987,25")
print("   oran  : 0,75      |  0,40       |  0,92")
print()

tablo2 = load_csv(dosya_sayi)
df2 = tablo2.frame

print("Loader'in okudugu tipler:")
for kolon, tip in df2.dtypes.items():
    print(f"   {kolon:<10} -> {tip}")
print()

print("Loader'in kaydettigi notlar:")
for issue in tablo2.issues:
    print(f"   [{issue.severity}] {issue.code}: {issue.detail}")
print()

print("tutar sutunu sayiya cevrilebiliyor mu:")
cevrilen = pd.to_numeric(df2["tutar"], errors="coerce")
print(f"   {cevrilen.tolist()}")
print()

if cevrilen.isna().all():
    print("SONUC: sayi sutunlarinin TAMAMI metne dondu.")
    print("       Ortalama yok, histogram yok, korelasyon yok, aykiri deger yok.")
    print("       Ayrac dogru tespit edildi, dosya 'basariyla' okundu.")
    print("       Hata YOK.")
else:
    print("SONUC: sayilar dogru okunmus.")


# ---------------------------------------------------------------------------
# DOGRU OKUMA — karsilastirma
# ---------------------------------------------------------------------------

baslik(3, "AYNI DOSYA, DOGRU PARAMETRELERLE")

dogru = pd.read_csv(dosya_sayi, sep=";", decimal=",", thousands=".")
print("pd.read_csv(..., decimal=',', thousands='.')")
print()
for kolon, tip in dogru.dtypes.items():
    print(f"   {kolon:<10} -> {tip}")
print()
print(f"   tutar degerleri: {dogru['tutar'].tolist()}")
print()
print("Fark: iki parametre. Ama sabitlenemez —")
print("Ingilizce dosyada '1,234.56' bu ayarla yanlis okunur.")
print("Yani hangi formatta oldugu TESPIT edilmeli. Asil is orada.")

print(f"\n{'=' * 74}")
print("Test dosyalari: tests/fixtures/kesif/tr_cp1254.csv, tests/fixtures/kesif/tr_sayi.csv")
print("=" * 74)
