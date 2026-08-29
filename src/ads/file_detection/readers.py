"""Apply a caller's selected read options and return bounded previews.

Readers run only after a choice is made. They never return a source wholesale,
which keeps raw rows out of agent context and limits sensitive-data exposure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .evidence import detect_delimiter, detect_encoding


def _resolve_options(secimler: dict[str, str]) -> dict[str, Any]:
    """Secenek parametrelerini pandas argumanlarina cevir."""
    arg: dict[str, Any] = {}
    if "encoding" in secimler and secimler["encoding"] != "SOR":
        arg["encoding"] = secimler["encoding"]
    if "decimal" in secimler:
        arg["decimal"] = secimler["decimal"]
    if "thousands" in secimler:
        arg["thousands"] = secimler["thousands"]
    if "ayrac" in secimler and secimler["ayrac"] != "SOR":
        arg["sep"] = secimler["ayrac"]
    if secimler.get("dtype") == "str":
        arg["dtype"] = str
    if "parse_dates" in secimler:
        arg["parse_dates"] = [secimler["parse_dates"]]
        arg["dayfirst"] = secimler.get("dayfirst", "true").lower() == "true"
    return arg


def read_table(yol: Path, secimler: dict[str, str], satir: int = 5) -> dict[str, Any]:
    """Secilen ayarlarla tabloyu oku ve kucuk bir onizleme dondur."""
    ham = yol.read_bytes()
    arg = _resolve_options(secimler)

    arg.setdefault("encoding", detect_encoding(ham[:128_000]).secilen)
    if "sep" not in arg:
        metin = ham[:128_000].decode(arg["encoding"], errors="replace")
        arg["sep"] = detect_delimiter(metin).ayrac

    try:
        df = pd.read_csv(yol, **arg)
    except Exception as hata:  # noqa: BLE001 - kullaniciya sebebi gosterilecek
        return {
            "basarili": False,
            "hata": f"{type(hata).__name__}: {hata}",
            "kullanilan_ayarlar": {k: str(v) for k, v in arg.items()},
        }

    sayisal = [k for k, t in df.dtypes.items() if str(t).startswith(("int", "float"))]
    metinsel = [k for k, t in df.dtypes.items() if str(t) in ("object", "string")]

    ozet: dict[str, str] = {}
    for kolon in sayisal:
        seri = df[kolon].dropna()
        if len(seri):
            ozet[kolon] = (f"ort={seri.mean():.2f} min={seri.min():.2f} "
                           f"max={seri.max():.2f} n={len(seri)}")

    return {
        "basarili": True,
        "kullanilan_ayarlar": {k: str(v) for k, v in arg.items()},
        "satir_sayisi": int(len(df)),
        "sutun_sayisi": int(len(df.columns)),
        "tipler": {str(k): str(v) for k, v in df.dtypes.items()},
        "sayisal_sutunlar": [str(k) for k in sayisal],
        "metinsel_sutunlar": [str(k) for k in metinsel],
        "sayisal_ozet": ozet,
        "onizleme": df.head(satir).astype(str).to_dict(orient="records"),
    }


# Onizlemede gosterilecek azami karakter. Ham metin toptan donmez.
PDF_ONIZLEME_AZAMI_KARAKTER = 2000


def read_pdf(yol: Path, secimler: dict[str, str], sayfa: int = 3) -> dict[str, Any]:
    """PDF'den metni cikarir ve ilk birkac sayfanin kucuk bir onizlemesini dondurur."""
    try:
        okuyucu = PdfReader(str(yol))
    except (PdfReadError, OSError, ValueError) as hata:
        return {"basarili": False, "hata": f"{type(hata).__name__}: {hata}"}

    parcalar: list[str] = []
    for s in okuyucu.pages:
        try:
            parcalar.append(s.extract_text() or "")
        except Exception:  # noqa: BLE001 - bozuk tek sayfa, digerlerine devam
            parcalar.append("")
    metin = "\n".join(parcalar)

    onizleme_sayfa = int(secimler.get("onizleme_sayfa", sayfa))
    onizleme = "\n".join(parcalar[:onizleme_sayfa])[:PDF_ONIZLEME_AZAMI_KARAKTER]

    return {
        "basarili": True,
        "sayfa_sayisi": len(okuyucu.pages),
        "toplam_karakter": len(metin),
        "onizleme": onizleme,
    }


def read_image(yol: Path, secimler: dict[str, str], satir: int = 8) -> dict[str, Any]:
    """Goruntudeki tabloyu OCR ile okur ve kucuk bir onizleme dondurur.

    `turkce_dogrulanmis` alani bilerek ciktinin bir parcasi: cagiran taraf
    metnin dogrulanip dogrulanmadigini bilmeden kullanmasin.
    """
    from .formats import image as _image

    s = _image.read(yol)
    if s.hata:
        return {"basarili": False, "hata": s.hata}

    sadece_sayi = secimler.get("mod") == "sadece_sayi"
    satirlar = s.satirlar[:satir]
    if sadece_sayi:
        satirlar = [[h for h in r if _image.is_number(h)] for r in satirlar]

    return {
        "basarili": True,
        "satir_sayisi": len(s.satirlar),
        "sutun_sayisi": s.sutun_sayisi,
        "turkce_dogrulanmis": s.turkce_dogrulanmis,
        "turkce_gerekce": _image.model_supports_turkish()[1],
        "onizleme": satirlar,
    }


def read_workbook(yol: Path, secimler: dict[str, str],
                       satir: int = 5) -> dict[str, Any]:
    """Secilen sayfayi okur ve kucuk bir onizleme dondurur.

    Sayfa secilmemisse ve tek tablo sayfasi varsa o kullanilir; birden
    fazlaysa hangisinin secilecegi bir TERCIH sorusudur ve secenekler
    `secenekler()` ile sunulmustur.
    """
    from .formats import tabular

    yapi = tabular.inspect_structure(yol)
    if yapi.hata:
        return {"basarili": False, "hata": yapi.hata}

    tablolar = yapi.tablo_sayfalari
    if not tablolar:
        return {"basarili": False, "hata": "tablo yapisinda sayfa yok"}

    istenen = secimler.get("sayfa")
    if istenen:
        secili = next((s for s in tablolar if s.ad == istenen), None)
        if secili is None:
            return {"basarili": False,
                    "hata": f"'{istenen}' adli tablo sayfasi yok",
                    "mevcut_sayfalar": [s.ad for s in tablolar]}
    else:
        secili = tablolar[0]

    try:
        df = pd.read_excel(yol, sheet_name=secili.ad)
    except Exception as hata:  # noqa: BLE001 - sebebi cagirana gosterilecek
        return {"basarili": False, "hata": f"{type(hata).__name__}: {hata}"}

    sayisal = [k for k, t in df.dtypes.items() if str(t).startswith(("int", "float"))]
    return {
        "basarili": True,
        "sayfa": secili.ad,
        "secim_yapildi": bool(istenen),
        "tum_tablo_sayfalari": [s.ad for s in tablolar],
        "satir_sayisi": int(len(df)),
        "sutun_sayisi": int(len(df.columns)),
        "tipler": {str(k): str(v) for k, v in df.dtypes.items()},
        "sayisal_sutunlar": [str(k) for k in sayisal],
        "onizleme": df.head(satir).astype(str).to_dict(orient="records"),
    }
