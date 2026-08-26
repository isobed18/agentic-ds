"""
KESIF — PDF cozumleyici

PDF iki turde gelir: metin katmani olan (rapor, fatura, makale) ve
sadece goruntu olan (taranmis belge, imzali form). Bu modul HANGISI
oldugunu OLCER — goruntu tabanliysa OCR gerektigini soyler, uydurmaz.

Metin katmani bulunursa, cikarilan metin sekil.py'ye verilir: ayni
"tablo mu belge mi" sorusu PDF icin de gecerlidir, tekerlek yeniden
icat edilmez.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..model import Bulgu, Kanit, Onem, Rapor, Secenek
from ..sekil import Sekil, sekil_tespit

# Bir sayfada bunun altinda karakter cikarsa "metin katmani yok" sayilir.
SAYFA_BASI_AZAMI_BOS = 20


def incele(yol: Path) -> Rapor:
    rapor = Rapor(yol=str(yol), format="pdf", format_guveni="yuksek",
                  boyut_bayt=yol.stat().st_size)

    try:
        okuyucu = PdfReader(str(yol))
        sayfa_sayisi = len(okuyucu.pages)
    except (PdfReadError, OSError, ValueError) as hata:
        rapor.okunabilir = False
        rapor.not_ = f"PDF acilamadi: {type(hata).__name__}: {hata}"
        return rapor

    if sayfa_sayisi == 0:
        rapor.okunabilir = False
        rapor.not_ = "PDF'de sayfa yok."
        return rapor

    parcalar: list[str] = []
    for sayfa in okuyucu.pages:
        try:
            parcalar.append(sayfa.extract_text() or "")
        except Exception:  # noqa: BLE001 - bozuk tek sayfa, digerlerine devam
            parcalar.append("")
    metin = "\n".join(parcalar)

    rapor.yapi = {
        "sayfa_sayisi": str(sayfa_sayisi),
        "cikarilan_karakter": str(len(metin)),
    }

    ortalama = len(metin) / sayfa_sayisi
    if ortalama < SAYFA_BASI_AZAMI_BOS:
        return _taranmis_rapor(rapor, okuyucu, sayfa_sayisi, ortalama)

    s = sekil_tespit(metin)
    rapor.bulgular.append(Bulgu(
        baslik=f"Metin katmani bulundu — sekil: {s.sekil.value}",
        onem=Onem.BILGI,
        aciklama=f"{sayfa_sayisi} sayfa, {len(metin)} karakter cikarildi.",
        kanitlar=[Kanit(olcum=ad, deger=deger) for ad, deger in s.kanitlar],
    ))
    if s.sekil is Sekil.BELIRSIZ:
        rapor.bulgular.append(_sekil_belirsiz_bulgusu())
    return rapor


def _taranmis_rapor(rapor: Rapor, okuyucu, sayfa_sayisi: int,
                    ortalama: float) -> Rapor:
    """Metin katmani yok: taranmis. Gomulu goruntuyu cikarip OCR dene.

    Sayfanin kendisini goruntuye cevirmek harici bir sistem bagimliligi
    (poppler vb.) ister; bunun yerine SAYFAYA GOMULU goruntuler cikarilir.
    Taranmis bir belgede sayfa zaten tek bir buyuk goruntudur, dolayisiyla
    bu ek bagimlilik olmadan calisir.
    """
    rapor.bulgular.append(Bulgu(
        baslik="Metin katmani yok — taranmis/goruntu tabanli",
        onem=Onem.BILGI,
        aciklama=(f"{sayfa_sayisi} sayfadan ortalama {ortalama:.0f} karakter "
                  "cikti; metin secilemiyor. OCR deneniyor."),
        kanitlar=[
            Kanit(olcum="sayfa sayisi", deger=str(sayfa_sayisi)),
            Kanit(olcum="sayfa basi ortalama karakter", deger=f"{ortalama:.0f}"),
        ],
    ))

    goruntuler = gomulu_goruntuler(okuyucu)
    if not goruntuler:
        rapor.okunabilir = False
        rapor.bulgular.append(Bulgu(
            baslik="Sayfada gomulu goruntu de bulunamadi",
            onem=Onem.KRITIK,
            aciklama="Ne metin katmani ne cikarilabilir goruntu var; "
                     "bu dosya bu haliyle okunamiyor.",
            secenekler=[
                Secenek(kod="A", eylem="Human feedback ile isaretle",
                        kazanc="Yanlis bos sonuc yerine dogru siniflandirma.",
                        bedel="Otomatik akis durur.",
                        onerilen=True, parametre={"mod": "human_feedback"}),
            ],
        ))
        return rapor

    import tempfile

    from . import goruntu as _goruntu

    with tempfile.TemporaryDirectory() as gecici:
        gyol = Path(gecici) / "sayfa1.png"
        gyol.write_bytes(goruntuler[0])
        sonuc = _goruntu.oku(gyol)

    if sonuc.hata:
        rapor.okunabilir = False
        rapor.not_ = f"OCR basarisiz: {sonuc.hata}"
        return rapor

    tablo_mu = sonuc.sutun_sayisi >= 2 and len(sonuc.satirlar) >= 2
    rapor.yapi.update({
        "gomulu_goruntu": str(len(goruntuler)),
        "ocr_hucre": str(len(sonuc.hucreler)),
        "ocr_satir": str(len(sonuc.satirlar)),
        "ocr_sutun": str(sonuc.sutun_sayisi),
    })
    rapor.bulgular.append(Bulgu(
        baslik=("OCR ile tablo yapisi cikarildi" if tablo_mu
                else "OCR ile metin cikarildi"),
        onem=Onem.BILGI,
        aciklama=f"{len(sonuc.hucreler)} hucre, {len(sonuc.satirlar)} satir.",
        kanitlar=[Kanit(olcum="ilk satir",
                        deger=" | ".join(sonuc.satirlar[0][:6]))]
        if sonuc.satirlar else [],
    ))

    if not sonuc.turkce_dogrulanmis:
        rapor.bulgular.append(_goruntu.turkce_dogrulanamadi_bulgusu())
    return rapor


def gomulu_goruntuler(okuyucu) -> list[bytes]:
    cikan: list[bytes] = []
    for sayfa in okuyucu.pages:
        try:
            for im in sayfa.images:
                cikan.append(im.data)
        except Exception:  # noqa: BLE001 - bozuk tek sayfa, digerlerine devam
            continue
        if cikan:
            break
    return cikan


def _sekil_belirsiz_bulgusu() -> Bulgu:
    return Bulgu(
        baslik="Cikarilan metnin sekli belirsiz",
        onem=Onem.DIKKAT,
        aciklama="Sinyaller celisiyor veya zayif; otomatik karar verilmedi.",
        secenekler=[
            Secenek(kod="A", eylem="Human feedback iste",
                    kazanc="Yanlis akisa yonlendirme riski sifirlanir.",
                    bedel="Otomatik akis durur.",
                    onerilen=True, parametre={"mod": "human_feedback"}),
        ],
    )
