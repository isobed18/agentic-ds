"""
KESIF — okuyucu

secenekler() ile sunulan seceneklerden biri secildikten SONRA calisir.
Yani: sunucu karar vermez, cagiran karar verir, burasi uygular.

Ciktisi her zaman kucuk bir onizlemedir; ham veri toptan doner degil.
Bu, hem baglami sismekten korur hem de hassas veriyi disarida tutar.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .kanit import ayrac_tespit, kodlama_tespit


def _cozumle(secimler: dict[str, str]) -> dict[str, Any]:
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


def oku_tablo(yol: Path, secimler: dict[str, str], satir: int = 5) -> dict[str, Any]:
    """Secilen ayarlarla tabloyu oku ve kucuk bir onizleme dondur."""
    ham = yol.read_bytes()
    arg = _cozumle(secimler)

    arg.setdefault("encoding", kodlama_tespit(ham[:128_000]).secilen)
    if "sep" not in arg:
        metin = ham[:128_000].decode(arg["encoding"], errors="replace")
        arg["sep"] = ayrac_tespit(metin).ayrac

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
