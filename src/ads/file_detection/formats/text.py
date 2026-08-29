"""Inspect CSV, TSV, and text content without applying preferences.

The inspector measures encoding, delimiter consistency, per-column number
formats, and date-like columns. Measurements that require a choice become
findings with explicit options; none are applied automatically.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

from ..evidence import detect_delimiter, detect_encoding, detect_number_format
from ..models import Evidence, Finding, Option, Report, Severity

ORNEK_BAYT = 128_000
TARIH_KALIBI = re.compile(
    r"^\s*\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}([ T]\d{1,2}:\d{2})?\s*$"
)


def inspect(yol: Path) -> Report:
    ham = yol.read_bytes()
    rapor = Report(
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
    kod = detect_encoding(ham[:ORNEK_BAYT])
    metin = ham.decode(kod.secilen, errors="replace")

    if kod.belirsiz:
        rapor.bulgular.append(_encoding_finding(kod))
    else:
        rapor.bulgular.append(Finding(
            baslik="Kodlama belirlendi",
            onem=Severity.INFO,
            aciklama=f"Dosya {kod.secilen} olarak okunuyor.",
            kanitlar=[Evidence(olcum=o, deger=d) for o, d in kod.kanitlar],
        ))

    # --- 2) AYRAC ---------------------------------------------------------
    ayr = detect_delimiter(metin[:ORNEK_BAYT])
    tablo_mu = ayr.guven == "yuksek"

    if not tablo_mu:
        return _plain_text_report(rapor, metin, kod, ayr)

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
    rapor.bulgular.append(Finding(
        baslik="Ayrac belirlendi",
        onem=Severity.INFO,
        aciklama=f"Ayrac {ayr.ayrac!r}; her satirda tutarli.",
        kanitlar=[Evidence(olcum="aday sayimlari",
                        deger=", ".join(f"{k!r}={v}" for k, v in ayr.sayimlar.items()))],
    ))

    # --- 3) SUTUN BAZINDA SAYI BICIMI ------------------------------------
    for i, ad in enumerate(basliklar):
        degerler = [s[i] for s in veri if i < len(s)]
        if not degerler:
            continue

        bic = detect_number_format(degerler)
        if bic.ondalik and bic.guven != "yok":
            bulgu = _number_finding(ad, bic)
            if bulgu:
                rapor.bulgular.append(bulgu)

        tarih_adet = sum(1 for d in degerler if TARIH_KALIBI.match(d or ""))
        if tarih_adet and tarih_adet >= len(degerler) * 0.8:
            rapor.bulgular.append(_date_finding(ad, tarih_adet, len(degerler), degerler))

    return rapor


# ---------------------------------------------------------------------------
# BULGU URETICILERI
# ---------------------------------------------------------------------------

def _encoding_finding(kod) -> Finding:
    """Kodlama ikilemi. Kanitlar celisiyorsa ONERI 'sor' olur.

    Bu, tasarimin en onemli yeri: sistemin vaadi "sessizce tahmin etme".
    Kanit yetmiyorsa bir kodlama secip yuksek guvenle sunmak, tam da
    onlemeye calistigimiz hatayi yapmak olurdu.
    """
    kanitlar = [Evidence(olcum=o, deger=d) for o, d in kod.kanitlar]
    emin_degil = kod.karar_verilemedi or kod.guven == "dusuk"

    secenekler = [
        Option(
            kod="A",
            eylem=f"{kod.secilen} ile oku",
            kazanc="Kanitlarin isaret ettigi okuma uygulanir.",
            bedel="Dosya baska bir Windows kodlamasindaysa harfler "
                  "sessizce degisir.",
            onerilen=not emin_degil,
            parametre={"encoding": kod.secilen},
        ),
        Option(
            kod="B",
            eylem="cp1254 ile oku (Turkce Windows)"
                  if kod.secilen != "cp1254" else "cp1252 ile oku (Bati Avrupa)",
            kazanc="Diger dil ailesi icin dogru okuma.",
            bedel="Yanlis secilirse harfler sessizce degisir; hata verilmez.",
            parametre={"encoding": "cp1254" if kod.secilen != "cp1254"
                       else "cp1252"},
        ),
        Option(
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

    return Finding(
        baslik="Kodlama belirsiz" + (" — karar verilemedi" if emin_degil else ""),
        onem=Severity.CRITICAL,
        aciklama=aciklama,
        kanitlar=kanitlar,
        secenekler=secenekler,
    )


def _number_finding(sutun: str, bic) -> Finding | None:
    if bic.guven == "dusuk":
        return Finding(
            baslik=f"'{sutun}' sutununda karisik sayi bicimi",
            onem=Severity.CRITICAL,
            aciklama="Ayni sutunda hem Turkce hem Ingilizce bicimli sayi var. "
                     "Tek bir ayarla dogru okunamaz.",
            kanitlar=[
                Evidence(olcum="TR bicimli deger", deger=str(bic.tr_kanit)),
                Evidence(olcum="EN bicimli deger", deger=str(bic.en_kanit)),
                Evidence(olcum="ornekler", deger=", ".join(bic.ornekler)),
            ],
            secenekler=[
                Option(kod="A", eylem="Sutunu metin olarak birak",
                        kazanc="Veri kaybi olmaz, gercek hal korunur.",
                        bedel="Sayisal analiz yapilamaz.",
                        onerilen=True, parametre={"dtype": "str"}),
                Option(kod="B", eylem="Baskin bicimle oku",
                        kazanc="Cogunluk dogru cevrilir.",
                        bedel="Azinliktaki degerler bos (NaN) olur.",
                        parametre={"decimal": bic.ondalik or ",",
                                   "thousands": bic.binlik or "."}),
                Option(kod="C", eylem="Satir bazinda ayristir ve raporla",
                        kazanc="Hangi satirin hangi bicimde oldugu gorulur.",
                        bedel="Ek islem maliyeti.",
                        parametre={"mod": "satir_bazinda"}),
            ],
        )

    if bic.ondalik == ",":
        return Finding(
            baslik=f"'{sutun}' sutunu Turkce sayi biciminde",
            onem=Severity.CRITICAL,
            aciklama="Ondalik ayraci virgul. Varsayilan okumada bu sutun "
                     "METNE doner ve butun sayisal analiz duser.",
            kanitlar=[
                Evidence(olcum="kesin TR bicimli deger", deger=str(bic.tr_kanit)),
                Evidence(olcum="kesin EN bicimli deger", deger=str(bic.en_kanit)),
                Evidence(olcum="belirsiz deger (or. 1.234)", deger=str(bic.belirsiz_adet)),
                Evidence(olcum="ornekler", deger=", ".join(bic.ornekler)),
            ],
            secenekler=[
                Option(kod="A", eylem="decimal=',' thousands='.' ile oku",
                        kazanc="Sutun sayiya doner; ortalama, dagilim, "
                               "korelasyon hesaplanabilir.",
                        bedel="Ayni ayar tum dosyaya uygulanir; dosyada "
                              "Ingilizce bicimli baska sutun varsa bozulur.",
                        onerilen=True,
                        parametre={"decimal": ",", "thousands": "."}),
                Option(kod="B", eylem="Metin olarak birak",
                        kazanc="Hicbir donusum riski yok.",
                        bedel="Sutun sayisal analize giremez.",
                        parametre={"dtype": "str"}),
                Option(kod="C", eylem="Sadece bu sutunu donustur",
                        kazanc="Diger sutunlar etkilenmez.",
                        bedel="Sutun bazinda ayar gerekir, akis karmasiklasir.",
                        parametre={"sutun": sutun, "decimal": ","}),
            ],
        )
    return None


def _date_finding(sutun: str, adet: int, toplam: int, degerler: list[str]) -> Finding:
    ornek = next((d for d in degerler if TARIH_KALIBI.match(d or "")), "")
    return Finding(
        baslik=f"'{sutun}' sutunu tarih gorunumlu",
        onem=Severity.WARNING,
        aciklama="Degerler tarih kalibina uyuyor ama tip cikariminda "
                 "metin/kategorik olarak gecer.",
        kanitlar=[
            Evidence(olcum="tarih kalibina uyan", deger=f"{adet}/{toplam}"),
            Evidence(olcum="ornek", deger=ornek),
        ],
        secenekler=[
            Option(kod="A", eylem="Tarihe cevir (gun-ay-yil)",
                    kazanc="Zaman bazli analiz ve sizinti kontrolu mumkun olur.",
                    bedel="Gun/ay sirasi yanlissa tarihler sessizce kayar.",
                    onerilen=True,
                    parametre={"parse_dates": sutun, "dayfirst": "true"}),
            Option(kod="B", eylem="Tarihe cevir (ay-gun-yil)",
                    kazanc="ABD bicimli dosyalar icin dogru.",
                    bedel="TR bicimli dosyada ilk 12 gun yanlis eslesir.",
                    parametre={"parse_dates": sutun, "dayfirst": "false"}),
            Option(kod="C", eylem="Metin birak",
                    kazanc="Yorum yapilmaz, ham hal korunur.",
                    bedel="Tarih ozellikleri uretilemez.",
                    parametre={"dtype": "str"}),
        ],
    )


def _plain_text_report(rapor: Report, metin: str, kod, ayr) -> Report:
    """Tutarli ayrac yok: bu bir tablo degil, serbest metin."""
    satirlar = metin.splitlines()
    rapor.format = "txt"
    rapor.format_guveni = "yuksek"
    rapor.yapi = {
        "satir_sayisi": str(len(satirlar)),
        "karakter_sayisi": str(len(metin)),
        "bos_satir": str(sum(1 for s in satirlar if not s.strip())),
    }
    rapor.bulgular.append(Finding(
        baslik="Tablo degil, serbest metin",
        onem=Severity.WARNING,
        aciklama="Satirlar arasinda tutarli bir ayrac yok. Dosya tablo "
                 "olarak degil, metin olarak ele alinmali.",
        kanitlar=[
            Evidence(olcum="ayrac adaylari",
                  deger=", ".join(f"{k!r}={v}" for k, v in ayr.sayimlar.items())),
            Evidence(olcum="tutarlilik", deger="hicbir aday her satirda esit degil"),
        ],
        secenekler=[
            Option(kod="A", eylem="Metin belgesi olarak isle",
                    kazanc="Icerik bozulmadan aktarilir.",
                    bedel="Sutun/tip cikarimi yapilamaz.",
                    onerilen=True, parametre={"mod": "metin"}),
            Option(kod="B", eylem="Sabit genislikli tablo olarak dene",
                    kazanc="Hizalanmis raporlar tabloya cevrilebilir.",
                    bedel="Hizalama bozuksa sutunlar kayar.",
                    parametre={"mod": "sabit_genislik"}),
            Option(kod="C", eylem="Ayraci elle bildir",
                    kazanc="Kesin sonuc.",
                    bedel="Insan mudahalesi gerekir.",
                    parametre={"ayrac": "SOR"}),
        ],
    ))
    rapor.bulgular.append(_prose_safety_finding())
    return rapor


def _prose_safety_finding() -> Finding:
    """Serbest metin ajanin baglamina girer: talimat enjeksiyonu yuzeyi."""
    return Finding(
        baslik="Serbest metin ajan baglamina girecek",
        onem=Severity.WARNING,
        aciklama="Tablo disi icerik modele metin olarak ulasir. Belgeye "
                 "gomulu bir talimat, model tarafindan talimat sanilabilir.",
        kanitlar=[Evidence(olcum="icerik turu", deger="yapilandirilmamis metin")],
        secenekler=[
            Option(kod="A", eylem="Icerigi VERI olarak etiketle",
                    kazanc="Model icerigi talimat degil veri olarak gorur.",
                    bedel="Etiketleme cagiran tarafta uygulanmali.",
                    onerilen=True, parametre={"etiket": "veri"}),
            Option(kod="B", eylem="Sadece ozet/istatistik dondur",
                    kazanc="Ham metin hic baglama girmez.",
                    bedel="Icerik analizi yapilamaz.",
                    parametre={"mod": "ozet"}),
        ],
    )
