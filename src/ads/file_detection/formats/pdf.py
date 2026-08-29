"""Measure whether a PDF has usable text or requires OCR.

Extracted text is passed through the same structural-shape detector as other
text files. Image-only PDFs take the OCR path instead of silently producing an
empty document result.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from ..models import Evidence, Finding, Option, Report, Severity
from ..text_shape import TextShape, detect_shape

# Bir sayfada bunun altinda karakter cikarsa "metin katmani yok" sayilir.
SAYFA_BASI_AZAMI_BOS = 20


def inspect(yol: Path) -> Report:
    rapor = Report(yol=str(yol), format="pdf", format_guveni="yuksek",
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
        return _scanned_report(rapor, okuyucu, sayfa_sayisi, ortalama)

    s = detect_shape(metin)
    rapor.bulgular.append(Finding(
        baslik=f"Metin katmani bulundu — sekil: {s.sekil.value}",
        onem=Severity.BILGI,
        aciklama=f"{sayfa_sayisi} sayfa, {len(metin)} karakter cikarildi.",
        kanitlar=[Evidence(olcum=ad, deger=deger) for ad, deger in s.kanitlar],
    ))
    if s.sekil is TextShape.BELIRSIZ:
        rapor.bulgular.append(_ambiguous_shape_finding())
    return rapor


def _scanned_report(rapor: Report, okuyucu, sayfa_sayisi: int,
                    ortalama: float) -> Report:
    """Metin katmani yok: taranmis. Gomulu goruntuyu cikarip OCR dene.

    Sayfanin kendisini goruntuye cevirmek harici bir sistem bagimliligi
    (poppler vb.) ister; bunun yerine SAYFAYA GOMULU goruntuler cikarilir.
    Taranmis bir belgede sayfa zaten tek bir buyuk goruntudur, dolayisiyla
    bu ek bagimlilik olmadan calisir.
    """
    rapor.bulgular.append(Finding(
        baslik="Metin katmani yok — taranmis/goruntu tabanli",
        onem=Severity.BILGI,
        aciklama=(f"{sayfa_sayisi} sayfadan ortalama {ortalama:.0f} karakter "
                  "cikti; metin secilemiyor. OCR deneniyor."),
        kanitlar=[
            Evidence(olcum="sayfa sayisi", deger=str(sayfa_sayisi)),
            Evidence(olcum="sayfa basi ortalama karakter", deger=f"{ortalama:.0f}"),
        ],
    ))

    goruntuler = embedded_images(okuyucu)
    if not goruntuler:
        rapor.okunabilir = False
        rapor.bulgular.append(Finding(
            baslik="Sayfada gomulu goruntu de bulunamadi",
            onem=Severity.KRITIK,
            aciklama="Ne metin katmani ne cikarilabilir goruntu var; "
                     "bu dosya bu haliyle okunamiyor.",
            secenekler=[
                Option(kod="A", eylem="Human feedback ile isaretle",
                        kazanc="Yanlis bos sonuc yerine dogru siniflandirma.",
                        bedel="Otomatik akis durur.",
                        onerilen=True, parametre={"mod": "human_feedback"}),
            ],
        ))
        return rapor

    import tempfile

    from . import image as _image

    with tempfile.TemporaryDirectory() as gecici:
        gyol = Path(gecici) / "sayfa1.png"
        gyol.write_bytes(goruntuler[0])
        sonuc = _image.read(gyol)

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
    rapor.bulgular.append(Finding(
        baslik=("OCR ile tablo yapisi cikarildi" if tablo_mu
                else "OCR ile metin cikarildi"),
        onem=Severity.BILGI,
        aciklama=f"{len(sonuc.hucreler)} hucre, {len(sonuc.satirlar)} satir.",
        kanitlar=[Evidence(olcum="ilk satir",
                        deger=" | ".join(sonuc.satirlar[0][:6]))]
        if sonuc.satirlar else [],
    ))

    if not sonuc.turkce_dogrulanmis:
        rapor.bulgular.append(_image.turkish_unverified_finding())
    return rapor


def embedded_images(okuyucu) -> list[bytes]:
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


def _ambiguous_shape_finding() -> Finding:
    return Finding(
        baslik="Cikarilan metnin sekli belirsiz",
        onem=Severity.DIKKAT,
        aciklama="Sinyaller celisiyor veya zayif; otomatik karar verilmedi.",
        secenekler=[
            Option(kod="A", eylem="Human feedback iste",
                    kazanc="Yanlis akisa yonlendirme riski sifirlanir.",
                    bedel="Otomatik akis durur.",
                    onerilen=True, parametre={"mod": "human_feedback"}),
        ],
    )


incele = inspect
gomulu_goruntuler = embedded_images
