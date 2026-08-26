"""
KESIF — CSV / TSV / TXT cozumleyici

Olctugu seyler:
  1. kodlama          (kanit.kodlama_tespit)
  2. ayrac            (kanit.ayrac_tespit)
  3. sutun bazinda sayi bicimi  (kanit.sayi_bicimi_tespit)
  4. tarih gorunumlu sutunlar

Her olcum, karar gerektiriyorsa bir Bulgu'ya ve seceneklere donusur.
Hicbiri otomatik uygulanmaz.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from ..kanit import ayrac_tespit, kodlama_tespit, sayi_bicimi_tespit
from ..model import Bulgu, Kanit, Onem, Rapor, Secenek

ORNEK_BAYT = 128_000
TARIH_KALIBI = re.compile(
    r"^\s*\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}([ T]\d{1,2}:\d{2})?\s*$"
)


def incele(yol: Path) -> Rapor:
    ham = yol.read_bytes()
    rapor = Rapor(
        yol=str(yol),
        format="txt",
        format_guveni="dusuk",
        boyut_bayt=len(ham),
    )

    if not ham:
        rapor.okunabilir = False
        rapor.not_ = "Dosya bos."
        return rapor

    # --- 1) KODLAMA -------------------------------------------------------
    kod = kodlama_tespit(ham[:ORNEK_BAYT])
    metin = ham.decode(kod.secilen, errors="replace")

    if kod.belirsiz:
        rapor.bulgular.append(_kodlama_bulgusu(kod))
    else:
        rapor.bulgular.append(Bulgu(
            baslik="Kodlama belirlendi",
            onem=Onem.BILGI,
            aciklama=f"Dosya {kod.secilen} olarak okunuyor.",
            kanitlar=[Kanit(olcum=o, deger=d) for o, d in kod.kanitlar],
        ))

    # --- 2) AYRAC ---------------------------------------------------------
    ayr = ayrac_tespit(metin[:ORNEK_BAYT])
    tablo_mu = ayr.guven == "yuksek"

    if not tablo_mu:
        return _duz_metin_raporu(rapor, metin, kod, ayr)

    rapor.format = "tsv" if ayr.ayrac == "\t" else "csv"
    rapor.format_guveni = "yuksek"

    satirlar = list(csv.reader(io.StringIO(metin), delimiter=ayr.ayrac))
    satirlar = [s for s in satirlar if any(h.strip() for h in s)]
    if not satirlar:
        rapor.okunabilir = False
        rapor.not_ = "Ayrac bulundu ama satir okunamadi."
        return rapor

    basliklar = satirlar[0]
    veri = satirlar[1:]
    rapor.yapi = {
        "satir_sayisi": str(len(veri)),
        "sutun_sayisi": str(len(basliklar)),
        "ayrac": repr(ayr.ayrac),
        "sutunlar": ", ".join(basliklar[:12]),
    }
    rapor.bulgular.append(Bulgu(
        baslik="Ayrac belirlendi",
        onem=Onem.BILGI,
        aciklama=f"Ayrac {ayr.ayrac!r}; her satirda tutarli.",
        kanitlar=[Kanit(olcum="aday sayimlari",
                        deger=", ".join(f"{k!r}={v}" for k, v in ayr.sayimlar.items()))],
    ))

    # --- 3) SUTUN BAZINDA SAYI BICIMI ------------------------------------
    for i, ad in enumerate(basliklar):
        degerler = [s[i] for s in veri if i < len(s)]
        if not degerler:
            continue

        bic = sayi_bicimi_tespit(degerler)
        if bic.ondalik and bic.guven != "yok":
            bulgu = _sayi_bulgusu(ad, bic)
            if bulgu:
                rapor.bulgular.append(bulgu)

        tarih_adet = sum(1 for d in degerler if TARIH_KALIBI.match(d or ""))
        if tarih_adet and tarih_adet >= len(degerler) * 0.8:
            rapor.bulgular.append(_tarih_bulgusu(ad, tarih_adet, len(degerler), degerler))

    return rapor


# ---------------------------------------------------------------------------
# BULGU URETICILERI
# ---------------------------------------------------------------------------

def _kodlama_bulgusu(kod) -> Bulgu:
    """Kodlama ikilemi. Kanitlar celisiyorsa ONERI 'sor' olur.

    Bu, tasarimin en onemli yeri: sistemin vaadi "sessizce tahmin etme".
    Kanit yetmiyorsa bir kodlama secip yuksek guvenle sunmak, tam da
    onlemeye calistigimiz hatayi yapmak olurdu.
    """
    kanitlar = [Kanit(olcum=o, deger=d) for o, d in kod.kanitlar]
    emin_degil = kod.karar_verilemedi or kod.guven == "dusuk"

    secenekler = [
        Secenek(
            kod="A",
            eylem=f"{kod.secilen} ile oku",
            kazanc="Kanitlarin isaret ettigi okuma uygulanir.",
            bedel="Dosya baska bir Windows kodlamasindaysa harfler "
                  "sessizce degisir.",
            onerilen=not emin_degil,
            parametre={"encoding": kod.secilen},
        ),
        Secenek(
            kod="B",
            eylem="cp1254 ile oku (Turkce Windows)"
                  if kod.secilen != "cp1254" else "cp1252 ile oku (Bati Avrupa)",
            kazanc="Diger dil ailesi icin dogru okuma.",
            bedel="Yanlis secilirse harfler sessizce degisir; hata verilmez.",
            parametre={"encoding": "cp1254" if kod.secilen != "cp1254"
                       else "cp1252"},
        ),
        Secenek(
            kod="C",
            eylem="Kullaniciya sor",
            kazanc="Kesin sonuc; sessiz bozulma riski sifir.",
            bedel="Otomatik akis durur, insan beklenir.",
            onerilen=emin_degil,
            parametre={"encoding": "SOR"},
        ),
    ]

    aciklama = (
        "Birden fazla kodlama dosyayi HATASIZ cozuyor; sadece harfler "
        "farkli cikiyor. Otomatik secim sessiz veri bozulmasi demektir."
    )
    if emin_degil:
        aciklama += (" Bu dosyada hangi dil oldugunu gosteren kanit "
                     "bulunamadi; karar insana birakiliyor.")

    return Bulgu(
        baslik="Kodlama belirsiz" + (" — karar verilemedi" if emin_degil else ""),
        onem=Onem.KRITIK,
        aciklama=aciklama,
        kanitlar=kanitlar,
        secenekler=secenekler,
    )


def _sayi_bulgusu(sutun: str, bic) -> Bulgu | None:
    if bic.guven == "dusuk":
        return Bulgu(
            baslik=f"'{sutun}' sutununda karisik sayi bicimi",
            onem=Onem.KRITIK,
            aciklama="Ayni sutunda hem Turkce hem Ingilizce bicimli sayi var. "
                     "Tek bir ayarla dogru okunamaz.",
            kanitlar=[
                Kanit(olcum="TR bicimli deger", deger=str(bic.tr_kanit)),
                Kanit(olcum="EN bicimli deger", deger=str(bic.en_kanit)),
                Kanit(olcum="ornekler", deger=", ".join(bic.ornekler)),
            ],
            secenekler=[
                Secenek(kod="A", eylem="Sutunu metin olarak birak",
                        kazanc="Veri kaybi olmaz, gercek hal korunur.",
                        bedel="Sayisal analiz yapilamaz.",
                        onerilen=True, parametre={"dtype": "str"}),
                Secenek(kod="B", eylem="Baskin bicimle oku",
                        kazanc="Cogunluk dogru cevrilir.",
                        bedel="Azinliktaki degerler bos (NaN) olur.",
                        parametre={"decimal": bic.ondalik or ",",
                                   "thousands": bic.binlik or "."}),
                Secenek(kod="C", eylem="Satir bazinda ayristir ve raporla",
                        kazanc="Hangi satirin hangi bicimde oldugu gorulur.",
                        bedel="Ek islem maliyeti.",
                        parametre={"mod": "satir_bazinda"}),
            ],
        )

    if bic.ondalik == ",":
        return Bulgu(
            baslik=f"'{sutun}' sutunu Turkce sayi biciminde",
            onem=Onem.KRITIK,
            aciklama="Ondalik ayraci virgul. Varsayilan okumada bu sutun "
                     "METNE doner ve butun sayisal analiz duser.",
            kanitlar=[
                Kanit(olcum="kesin TR bicimli deger", deger=str(bic.tr_kanit)),
                Kanit(olcum="kesin EN bicimli deger", deger=str(bic.en_kanit)),
                Kanit(olcum="belirsiz deger (or. 1.234)", deger=str(bic.belirsiz_adet)),
                Kanit(olcum="ornekler", deger=", ".join(bic.ornekler)),
            ],
            secenekler=[
                Secenek(kod="A", eylem="decimal=',' thousands='.' ile oku",
                        kazanc="Sutun sayiya doner; ortalama, dagilim, "
                               "korelasyon hesaplanabilir.",
                        bedel="Ayni ayar tum dosyaya uygulanir; dosyada "
                              "Ingilizce bicimli baska sutun varsa bozulur.",
                        onerilen=True,
                        parametre={"decimal": ",", "thousands": "."}),
                Secenek(kod="B", eylem="Metin olarak birak",
                        kazanc="Hicbir donusum riski yok.",
                        bedel="Sutun sayisal analize giremez.",
                        parametre={"dtype": "str"}),
                Secenek(kod="C", eylem="Sadece bu sutunu donustur",
                        kazanc="Diger sutunlar etkilenmez.",
                        bedel="Sutun bazinda ayar gerekir, akis karmasiklasir.",
                        parametre={"sutun": sutun, "decimal": ","}),
            ],
        )
    return None


def _tarih_bulgusu(sutun: str, adet: int, toplam: int, degerler: list[str]) -> Bulgu:
    ornek = next((d for d in degerler if TARIH_KALIBI.match(d or "")), "")
    return Bulgu(
        baslik=f"'{sutun}' sutunu tarih gorunumlu",
        onem=Onem.DIKKAT,
        aciklama="Degerler tarih kalibina uyuyor ama tip cikariminda "
                 "metin/kategorik olarak gecer.",
        kanitlar=[
            Kanit(olcum="tarih kalibina uyan", deger=f"{adet}/{toplam}"),
            Kanit(olcum="ornek", deger=ornek),
        ],
        secenekler=[
            Secenek(kod="A", eylem="Tarihe cevir (gun-ay-yil)",
                    kazanc="Zaman bazli analiz ve sizinti kontrolu mumkun olur.",
                    bedel="Gun/ay sirasi yanlissa tarihler sessizce kayar.",
                    onerilen=True,
                    parametre={"parse_dates": sutun, "dayfirst": "true"}),
            Secenek(kod="B", eylem="Tarihe cevir (ay-gun-yil)",
                    kazanc="ABD bicimli dosyalar icin dogru.",
                    bedel="TR bicimli dosyada ilk 12 gun yanlis eslesir.",
                    parametre={"parse_dates": sutun, "dayfirst": "false"}),
            Secenek(kod="C", eylem="Metin birak",
                    kazanc="Yorum yapilmaz, ham hal korunur.",
                    bedel="Tarih ozellikleri uretilemez.",
                    parametre={"dtype": "str"}),
        ],
    )


def _duz_metin_raporu(rapor: Rapor, metin: str, kod, ayr) -> Rapor:
    """Tutarli ayrac yok: bu bir tablo degil, serbest metin."""
    satirlar = metin.splitlines()
    rapor.format = "txt"
    rapor.format_guveni = "yuksek"
    rapor.yapi = {
        "satir_sayisi": str(len(satirlar)),
        "karakter_sayisi": str(len(metin)),
        "bos_satir": str(sum(1 for s in satirlar if not s.strip())),
    }
    rapor.bulgular.append(Bulgu(
        baslik="Tablo degil, serbest metin",
        onem=Onem.DIKKAT,
        aciklama="Satirlar arasinda tutarli bir ayrac yok. Dosya tablo "
                 "olarak degil, metin olarak ele alinmali.",
        kanitlar=[
            Kanit(olcum="ayrac adaylari",
                  deger=", ".join(f"{k!r}={v}" for k, v in ayr.sayimlar.items())),
            Kanit(olcum="tutarlilik", deger="hicbir aday her satirda esit degil"),
        ],
        secenekler=[
            Secenek(kod="A", eylem="Metin belgesi olarak isle",
                    kazanc="Icerik bozulmadan aktarilir.",
                    bedel="Sutun/tip cikarimi yapilamaz.",
                    onerilen=True, parametre={"mod": "metin"}),
            Secenek(kod="B", eylem="Sabit genislikli tablo olarak dene",
                    kazanc="Hizalanmis raporlar tabloya cevrilebilir.",
                    bedel="Hizalama bozuksa sutunlar kayar.",
                    parametre={"mod": "sabit_genislik"}),
            Secenek(kod="C", eylem="Ayraci elle bildir",
                    kazanc="Kesin sonuc.",
                    bedel="Insan mudahalesi gerekir.",
                    parametre={"ayrac": "SOR"}),
        ],
    ))
    rapor.bulgular.append(_serbest_metin_guvenlik_bulgusu())
    return rapor


def _serbest_metin_guvenlik_bulgusu() -> Bulgu:
    """Serbest metin ajanin baglamina girer: talimat enjeksiyonu yuzeyi."""
    return Bulgu(
        baslik="Serbest metin ajan baglamina girecek",
        onem=Onem.DIKKAT,
        aciklama="Tablo disi icerik modele metin olarak ulasir. Belgeye "
                 "gomulu bir talimat, model tarafindan talimat sanilabilir.",
        kanitlar=[Kanit(olcum="icerik turu", deger="yapilandirilmamis metin")],
        secenekler=[
            Secenek(kod="A", eylem="Icerigi VERI olarak etiketle",
                    kazanc="Model icerigi talimat degil veri olarak gorur.",
                    bedel="Etiketleme cagiran tarafta uygulanmali.",
                    onerilen=True, parametre={"etiket": "veri"}),
            Secenek(kod="B", eylem="Sadece ozet/istatistik dondur",
                    kazanc="Ham metin hic baglama girmez.",
                    bedel="Icerik analizi yapilamaz.",
                    parametre={"mod": "ozet"}),
        ],
    )
