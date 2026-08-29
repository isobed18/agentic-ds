"""Extract image text and reconstruct table geometry with OCR.

Capability is measured from the recognition model's dictionary. The bundled
model cannot emit every Turkish character and has produced incorrect text with
0.828 confidence, so confidence thresholds cannot make that text trustworthy.
Unverified text is escalated while measured numbers and table geometry remain
available. A model with complete character coverage becomes trusted without a
code change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..models import Evidence, Finding, Option, Report, Severity

# Turkce'ye ozgu, cp1252/latin-1 ile karisan harfler. Modelin sozlugunde
# bunlar yoksa Turkce metin sessizce bozulur.
TURKCE_HARFLER = "ğĞıİşŞçÇöÖüÜ"

# Bir satir sayilmak icin iki kutunun dikey merkezi bu kadar yakin olmali
# (kutu yuksekliginin orani olarak).
SATIR_TOLERANSI = 0.6

# Bunun altinda guvenle okunan hucreler ayrica isaretlenir.
DUSUK_GUVEN = 0.80

_SAYI = re.compile(r"^[-+]?[%$€₺]?\s*\d[\d.,\s]*%?$")


@dataclass
class Cell:
    metin: str
    guven: float
    x: float
    y: float           # kutunun ust kenari
    yukseklik: float = 0.0

    @property
    def merkez(self) -> float:
        return self.y + self.yukseklik / 2


@dataclass
class OcrResult:
    hucreler: list[Cell] = field(default_factory=list)
    satirlar: list[list[str]] = field(default_factory=list)
    sutun_sayisi: int = 0
    turkce_dogrulanmis: bool = False
    hata: str = ""

    @property
    def metin(self) -> str:
        return "\n".join(" ".join(s) for s in self.satirlar)


# Tanima modeli DEGISTIRILEBILIR. Turkce destekli bir model edinildiginde
# tek yapilacak sey bu degiskeni gostermek; kod degismez, cunku yetenek
# esikten degil SOZLUKTEN olculur ve model degisince olcum de degisir.
MODEL_ORTAM_DEGISKENI = "KESIF_OCR_REC_MODEL"


def _default_model_path() -> str:
    import os

    import rapidocr_onnxruntime

    return os.path.join(
        os.path.dirname(rapidocr_onnxruntime.__file__),
        "models", "ch_PP-OCRv4_rec_infer.onnx",
    )


def recognition_model_path() -> str:
    """Kullanilacak tanima modeli: ortam degiskeni varsa o, yoksa gomulu."""
    import os

    return os.environ.get(MODEL_ORTAM_DEGISKENI) or _default_model_path()


@lru_cache(maxsize=1)
def _engine():
    """OCR motorunu bir kez yukle. Modeller pakete gomulu, ag erisimi yok."""
    import os

    from rapidocr_onnxruntime import RapidOCR

    ozel = os.environ.get(MODEL_ORTAM_DEGISKENI)
    if ozel:
        return RapidOCR(rec_model_path=ozel)
    return RapidOCR()


def model_dictionary(yol: str | None = None) -> str:
    """Bir ONNX tanima modelinin karakter sozlugunu dondur."""
    import onnxruntime as ort

    oturum = ort.InferenceSession(yol or recognition_model_path(),
                                  providers=["CPUExecutionProvider"])
    return oturum.get_modelmeta().custom_metadata_map.get("character", "")


def turkish_coverage(yol: str | None = None) -> tuple[bool, list[str], str]:
    """Bir modeli TURKCE ACISINDAN degerlendir: (tam mi, eksikler, gerekce).

    Aday bir modeli takmadan once olcmek icin de kullanilir; boylece
    "bu model Turkce biliyor mu" sorusu denemeyle degil sozlukle
    cevaplanir.
    """
    try:
        sozluk = model_dictionary(yol)
    except Exception as e:  # noqa: BLE001 - olculemezse guvenme
        return False, list(TURKCE_HARFLER), (
            f"sozluk okunamadi ({type(e).__name__}), Turkce dogrulanamadi")

    if not sozluk:
        return False, list(TURKCE_HARFLER), "model sozlugu bos, Turkce dogrulanamadi"

    eksik = [h for h in TURKCE_HARFLER if h not in set(sozluk)]
    if eksik:
        return False, eksik, ("model sozlugunde eksik Turkce harfler: "
                              + " ".join(eksik))
    return True, [], "model sozlugu butun Turkce harfleri iceriyor"


@lru_cache(maxsize=1)
def model_supports_turkish() -> tuple[bool, str]:
    """Kullanimdaki modelin SOZLUGUNU oku: Turkce harfler var mi?

    Bu bir olcumdur, tahmin degil: model dosyasinin metadata'sindaki
    karakter listesi okunur. Eksik harf varsa model o harfi hicbir
    kosulda uretemez.
    """
    tam, _, gerekce = turkish_coverage()
    return tam, gerekce


def is_number(metin: str) -> bool:
    """Hucre sayi bicimine uyuyor mu. Turkce sinirindan ETKILENMEZ."""
    return bool(_SAYI.match(metin))


def _group_into_rows(hucreler: list[Cell]) -> list[list[Cell]]:
    """Kutu konumlarindan satirlari kur: dikey merkezi yakin olanlar ayni satir.

    Esik KUTU YUKSEKLIGINDEN turetilir, ardisik y farklarindan degil.
    Ilk surum farklarin medyanini kullaniyordu; o medyan satir ICI 1-2
    piksellik kaymalarla domine olup esigi cok kucultuyor ve tek bir
    satiri ikiye boluyordu (olcumde 'Istanbul' satiri boyle bolundu).
    Bir hucrenin kendi yuksekligi ise satir araligini dogrudan verir.
    """
    if not hucreler:
        return []
    sirali = sorted(hucreler, key=lambda h: h.merkez)

    yukseklikler = sorted(h.yukseklik for h in sirali if h.yukseklik > 0)
    tipik = (yukseklikler[len(yukseklikler) // 2] if yukseklikler else 10.0)
    esik = max(tipik * SATIR_TOLERANSI, 1.0)

    satirlar: list[list[Cell]] = [[sirali[0]]]
    # Karsilastirma satirin ORTALAMA merkezine gore yapilir; boylece kucuk
    # kaymalar satir boyunca birikmez.
    merkezler = [sirali[0].merkez]
    for h in sirali[1:]:
        if abs(h.merkez - merkezler[-1]) <= esik:
            satirlar[-1].append(h)
            merkezler[-1] = sum(c.merkez for c in satirlar[-1]) / len(satirlar[-1])
        else:
            satirlar.append([h])
            merkezler.append(h.merkez)
    return [sorted(s, key=lambda h: h.x) for s in satirlar]


def read(yol: Path) -> OcrResult:
    """Goruntuden metni cikar ve tablo yapisini kur."""
    s = OcrResult()
    try:
        ham, _ = _engine()(str(yol))
    except ImportError:
        # Opsiyonel bilesen: eksikligi bir cokme degil, eyleme donuk bir mesaj.
        s.hata = ("OCR bileseni kurulu degil; kurmak icin: "
                  'pip install -e ".[kesif-ocr]"')
        return s
    except Exception as e:  # noqa: BLE001 - sebebi cagirana gosterilecek
        s.hata = f"{type(e).__name__}: {e}"
        return s

    if not ham:
        s.hata = "goruntude metin bulunamadi"
        return s

    for kutu, metin, guven in ham:
        ust = min(p[1] for p in kutu)
        alt = max(p[1] for p in kutu)
        s.hucreler.append(Cell(
            metin=str(metin).strip(),
            guven=float(guven),
            x=min(p[0] for p in kutu),
            y=ust,
            yukseklik=alt - ust,
        ))

    gruplar = _group_into_rows(s.hucreler)
    s.satirlar = [[h.metin for h in g] for g in gruplar]
    if s.satirlar:
        uzunluklar = [len(r) for r in s.satirlar]
        s.sutun_sayisi = max(set(uzunluklar), key=uzunluklar.count)

    s.turkce_dogrulanmis = model_supports_turkish()[0]
    return s


def inspect(yol: Path) -> Report:
    rapor = Report(yol=str(yol), format=yol.suffix.lstrip(".").lower() or "goruntu",
                  format_guveni="yuksek", boyut_bayt=yol.stat().st_size)

    sonuc = read(yol)
    if sonuc.hata:
        rapor.okunabilir = False
        rapor.not_ = f"OCR basarisiz: {sonuc.hata}"
        return rapor

    tablo_mu = sonuc.sutun_sayisi >= 2 and len(sonuc.satirlar) >= 2
    rapor.yapi = {
        "okunan_hucre": str(len(sonuc.hucreler)),
        "satir_sayisi": str(len(sonuc.satirlar)),
        "sutun_sayisi": str(sonuc.sutun_sayisi),
        "yapi": "tablo" if tablo_mu else "serbest metin",
    }

    rapor.bulgular.append(Finding(
        baslik=("Goruntuden tablo yapisi cikarildi" if tablo_mu
                else "Goruntuden metin cikarildi"),
        onem=Severity.INFO,
        aciklama=(f"{len(sonuc.hucreler)} hucre, {len(sonuc.satirlar)} satir"
                  + (f", {sonuc.sutun_sayisi} sutun." if tablo_mu else ".")),
        kanitlar=[
            Evidence(olcum="ilk satir", deger=" | ".join(sonuc.satirlar[0][:6])),
        ],
    ))

    sayisal = [h for h in sonuc.hucreler if is_number(h.metin)]
    if sayisal:
        rapor.bulgular.append(Finding(
            baslik="Sayisal hucreler taninmis",
            onem=Severity.INFO,
            aciklama=f"{len(sayisal)} hucre sayi bicimine uyuyor.",
            kanitlar=[
                Evidence(olcum="ortalama guven",
                      deger=f"{sum(h.guven for h in sayisal)/len(sayisal):.3f}"),
                Evidence(olcum="ornekler",
                      deger=", ".join(h.metin for h in sayisal[:4])),
            ],
        ))

    if not sonuc.turkce_dogrulanmis:
        rapor.bulgular.append(turkish_unverified_finding())

    dusuk = [h for h in sonuc.hucreler if h.guven < DUSUK_GUVEN]
    if dusuk:
        rapor.bulgular.append(Finding(
            baslik=f"{len(dusuk)} hucre dusuk guvenle okundu",
            onem=Severity.WARNING,
            aciklama="Bu hucreler ayrica gozden gecirilmeli.",
            kanitlar=[Evidence(olcum=h.metin, deger=f"{h.guven:.3f}") for h in dusuk[:5]],
        ))

    return rapor


def turkish_unverified_finding() -> Finding:
    _, gerekce = model_supports_turkish()
    return Finding(
        baslik="Turkce metin dogrulanamadi — model sozlugu yetersiz",
        onem=Severity.CRITICAL,
        aciklama=(
            "Tanima modelinin sozlugunde Turkce'ye ozgu harfler yok. Model "
            "bu harfleri uretemez; yerine ASCII benzerini koyar ve bunu "
            "SESSIZCE yapar. Olcumde 'Igdir' kelimesi 0.828 guvenle 'ngoa' "
            "okundu: yanlis cevap yuksek guvenle geldi, guven esigiyle "
            "elenemez. Sayilar ve tablo yapisi bu sinirdan etkilenmez."
        ),
        kanitlar=[Evidence(olcum="sozluk olcumu", deger=gerekce)],
        secenekler=[
            Option(
                kod="A", eylem="Metni human feedback ile dogrula",
                kazanc="Sessiz bozulma riski sifirlanir; sayilar zaten guvenilir.",
                bedel="Otomatik akis durur, insan beklenir.",
                onerilen=True, parametre={"mod": "human_feedback"},
            ),
            Option(
                kod="B", eylem="Sadece sayisal hucreleri kullan",
                kazanc="Turkce metne dokunmadan sayisal analiz yapilabilir.",
                bedel="Metin sutunlari disarida kalir.",
                parametre={"mod": "sadece_sayi"},
            ),
            Option(
                kod="C", eylem="Turkce destekli tanima modeli tak",
                kazanc="Kalici cozum; ayni kod otomatik dogrulanmis moda gecer.",
                bedel="Model dosyasinin kuruma teslim edilmesi gerekir.",
                parametre={"mod": "model_degistir"},
            ),
        ],
    )
