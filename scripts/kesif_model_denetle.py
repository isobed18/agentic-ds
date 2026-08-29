"""
KESIF — OCR tanima modeli denetleyicisi

"Bu model Turkce biliyor mu?" sorusu denemeyle degil SOZLUKLE cevaplanir.
Bir ONNX tanima modelinin karakter sozlugu okunur ve Turkce harflerin
hepsi var mi diye bakilir. Eksik harf varsa model o harfi hicbir kosulda
uretemez; yerine ASCII benzerini koyar ve bunu sessizce yapar.

Boylece aday bir model, hatta indirmeden once degil ama TAKMADAN once,
objektif olarak elenebilir.

Kullanim:
    # kullanimdaki modeli denetle
    .venv/bin/python scripts/kesif_model_denetle.py

    # aday bir modeli denetle
    .venv/bin/python scripts/kesif_model_denetle.py /yol/aday_rec.onnx

Turkce destekli bir model bulundugunda:
    export KESIF_OCR_REC_MODEL=/yol/turkce_rec.onnx
Kod degismez; sistem otomatik olarak "dogrulanmis" moda gecer.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJE / "src"))

from ads.file_detection.formats.image import (  # noqa: E402
    MODEL_ORTAM_DEGISKENI,
    TURKCE_HARFLER,
    model_dictionary,
    recognition_model_path,
    turkish_coverage,
)


def denetle(yol: str | None) -> int:
    hedef = yol or recognition_model_path()
    print(f"model : {hedef}")
    if not Path(hedef).exists():
        print("HATA  : dosya bulunamadi")
        return 2

    try:
        sozluk = model_dictionary(hedef)
    except Exception as e:  # noqa: BLE001 - kullaniciya sebebi gosterilecek
        print(f"HATA  : sozluk okunamadi — {type(e).__name__}: {e}")
        return 2

    tokenlar = sozluk.split("\n") if "\n" in sozluk else list(sozluk)
    print(f"sozluk: {len(tokenlar)} token")

    tam, eksik, gerekce = turkish_coverage(hedef)
    print()
    print("TURKCE HARF KAPSAMI")
    for h in TURKCE_HARFLER:
        print(f"   {h}  {'VAR' if h not in eksik else 'YOK'}")
    print()
    print(f"SONUC : {gerekce}")

    if tam:
        print()
        print("Bu model Turkce metni dogru uretebilir. Kullanmak icin:")
        print(f"   export {MODEL_ORTAM_DEGISKENI}={hedef}")
        return 0

    print()
    print("Bu model Turkce metni SESSIZCE bozar; yerine ASCII benzerini koyar.")
    print("Sistem bunu olctugu icin metni human feedback'e cikaracak;")
    print("sayilar ve tablo yapisi bu sinirdan etkilenmez.")
    return 1


if __name__ == "__main__":
    raise SystemExit(denetle(sys.argv[1] if len(sys.argv) > 1 else None))
