"""
KESIF — sekil tespiti

Magika formati soyler ("bu bir txt"). Ama akis karari formata degil
SEKLE baglidir. Olctugumuz vaka:

    i_log.txt      -> txt   log kaydi
    j_rapor.txt    -> txt   serbest metin
    k_sabit.txt    -> txt   sabit genislikli TABLO

Ucu de "txt". Ucu de ayri akisa gitmeli. Bu modul o ayrimi yapar.

YONTEM: her sekil icin bagimsiz sinyaller olculur, hicbiri tek basina
karar vermez. Sinyaller celisirse ya da hicbiri esigi gecmezse
KARAR VERILMEZ; artik olarak isaretlenir ve yargiya birakilir.

Sabit genislik tespiti klasik gozleme dayanir: bir tabloda sutunlar
arasi bosluk, kelimeler arasi bosluktan belirgin sekilde buyuktur ve
bu bosluklar satirlar boyunca AYNI konumda tekrar eder.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from enum import Enum


class Sekil(str, Enum):
    TABLO = "tablo"                  # ayracli, sutunlu
    SABIT_GENISLIK = "sabit_genislik"  # hizalanmis sutunlar, ayrac yok
    LOG = "log"                      # zaman damgali, tekrarli satirlar
    ANAHTAR_DEGER = "anahtar_deger"  # key: value satirlari
    SERBEST_METIN = "serbest_metin"  # cumleler, paragraflar
    BELIRSIZ = "belirsiz"            # kanit yetmedi -> yargiya


@dataclass
class SekilSonuc:
    sekil: Sekil
    guven: str                                  # yuksek / orta / dusuk
    kanitlar: list[tuple[str, str]] = field(default_factory=list)
    adaylar: list[tuple[Sekil, float]] = field(default_factory=list)
    yargi_gerekir: bool = False


# --- kaliplar --------------------------------------------------------------
ZAMAN_DAMGASI = re.compile(
    r"^\s*[\[\(]?\s*"
    r"(\d{4}[-/]\d{2}[-/]\d{2}|\d{2}[-/]\d{2}[-/]\d{4})"
    r"([ T]\d{1,2}:\d{2}(:\d{2})?)?"
)
LOG_SEVIYE = re.compile(
    r"\b(INFO|WARN|WARNING|ERROR|DEBUG|TRACE|FATAL|CRITICAL|"
    r"BILGI|UYARI|HATA)\b"
)
# Anahtar HARFLE baslamali. Aksi halde "10:22:01" gibi zaman damgalari
# anahtar:deger sanilir; olctugumuz ilk yanlis pozitif buydu.
ANAHTAR_DEGER_KALIBI = re.compile(
    r"^\s*[A-Za-z_çğıöşüÇĞİÖŞÜ][\w .\-çğıöşüÇĞİÖŞÜ]{0,40}\s*[:=]\s*\S"
)
CUMLE_SONU = re.compile(r"[.!?]['\"\)]?\s*$")


def _satirlar(metin: str, azami: int = 400) -> list[str]:
    return [s for s in metin.splitlines() if s.strip()][:azami]


# ---------------------------------------------------------------------------
# SINYALLER — her biri 0.0 ile 1.0 arasi bir oran doner
# ---------------------------------------------------------------------------

def _ayrac_sinyali(satirlar: list[str]) -> tuple[float, str]:
    """Satirlar arasinda TUTARLI ayrac var mi."""
    if len(satirlar) < 2:
        return 0.0, "satir yetersiz"
    en_iyi, en_iyi_ad = 0.0, ""
    for aday in (";", ",", "\t", "|"):
        adetler = [s.count(aday) for s in satirlar]
        if adetler[0] == 0:
            continue
        ayni = sum(1 for a in adetler if a == adetler[0])
        oran = ayni / len(adetler)
        if oran > en_iyi:
            en_iyi, en_iyi_ad = oran, f"{aday!r} her satirda {adetler[0]} kez"
    return en_iyi, en_iyi_ad or "tutarli ayrac yok"


def _sabit_genislik_sinyali(satirlar: list[str]) -> tuple[float, str]:
    """Sutun araligi tespiti.

    Klasik yontem: iki veya daha fazla ardisik boslugun bittigi konumlar
    sutun baslangicidir. Bu konumlar satirlar boyunca tekrar ediyorsa
    metin sabit genisliklidir.
    """
    ornek = [s for s in satirlar if len(s) > 12][:60]
    if len(ornek) < 3:
        return 0.0, "satir yetersiz"

    # her satirda "iki+ bosluk sonrasi karakter" konumlari
    konumlar: list[set[int]] = []
    for s in ornek:
        konumlar.append({m.start() for m in re.finditer(r"(?<=\s{2})\S", s)})

    if not any(konumlar):
        return 0.0, "coklu bosluk yok"

    # kac satirda ayni konum tekrar ediyor
    tum = set().union(*konumlar)
    tekrar = {k: sum(1 for ks in konumlar if k in ks) for k in tum}
    guclu = [k for k, adet in tekrar.items() if adet >= len(ornek) * 0.8]

    if len(guclu) < 1:
        return 0.0, "hizali sutun sinir yok"

    oran = min(1.0, len(guclu) / 3)
    return oran, (f"{len(guclu)} hizali sutun siniri, "
                  f"satirlarin >=%80'inde ayni konumda")


def _log_sinyali(satirlar: list[str]) -> tuple[float, str]:
    """Zaman damgasiyla baslayan satir orani ve seviye etiketleri."""
    if not satirlar:
        return 0.0, "satir yok"
    damga = sum(1 for s in satirlar if ZAMAN_DAMGASI.match(s))
    seviye = sum(1 for s in satirlar if LOG_SEVIYE.search(s))
    n = len(satirlar)
    oran = max(damga / n, min(1.0, (damga / n) * 0.6 + (seviye / n) * 0.6))
    return oran, f"zaman damgali {damga}/{n}, seviye etiketli {seviye}/{n}"


def _anahtar_deger_sinyali(satirlar: list[str]) -> tuple[float, str]:
    """Zaman damgasiyla baslayan satirlar sayilmaz: onlar log satiridir."""
    if not satirlar:
        return 0.0, "satir yok"
    aday = [s for s in satirlar if not ZAMAN_DAMGASI.match(s)]
    if not aday:
        return 0.0, "tum satirlar zaman damgali, log kalibi"
    eslesen = sum(1 for s in aday if ANAHTAR_DEGER_KALIBI.match(s))
    return eslesen / len(satirlar), (
        f"anahtar:deger kalibi {eslesen}/{len(satirlar)}"
        + ("" if len(aday) == len(satirlar)
           else f" ({len(satirlar)-len(aday)} zaman damgali satir elendi)")
    )


def _serbest_metin_sinyali(satirlar: list[str]) -> tuple[float, str]:
    """Cumle bitisi, satir uzunlugu degiskenligi, kelime yogunlugu."""
    if len(satirlar) < 2:
        return 0.0, "satir yetersiz"

    cumle = sum(1 for s in satirlar if CUMLE_SONU.search(s)) / len(satirlar)
    kelime_ort = statistics.fmean(len(s.split()) for s in satirlar)
    uzunluklar = [len(s) for s in satirlar]
    degisken = (statistics.pstdev(uzunluklar) / statistics.fmean(uzunluklar)
                if statistics.fmean(uzunluklar) else 0)

    # serbest metin: cumleyle biter, satir basina cok kelime, uzunluk degisken
    puan = (min(1.0, cumle * 1.2) * 0.45
            + min(1.0, kelime_ort / 8) * 0.35
            + min(1.0, degisken * 2) * 0.20)
    return puan, (f"cumleyle biten %{cumle*100:.0f}, "
                  f"satir basi {kelime_ort:.1f} kelime, "
                  f"uzunluk degiskenligi {degisken:.2f}")


# ---------------------------------------------------------------------------
# KARAR
# ---------------------------------------------------------------------------

ESIK_KARAR = 0.60     # bu esigin altinda hicbir sekil secilmez
ESIK_FARK = 0.15      # birinci ile ikinci arasinda bu kadar fark olmali


def sekil_tespit(metin: str) -> SekilSonuc:
    """Metnin seklini olc. Kanit yetmezse karar VERME."""
    satirlar = _satirlar(metin)
    if not satirlar:
        return SekilSonuc(Sekil.BELIRSIZ, "dusuk",
                          [("icerik", "bos veya sadece bosluk")],
                          yargi_gerekir=True)

    olcumler = {
        Sekil.TABLO: _ayrac_sinyali(satirlar),
        Sekil.SABIT_GENISLIK: _sabit_genislik_sinyali(satirlar),
        Sekil.LOG: _log_sinyali(satirlar),
        Sekil.ANAHTAR_DEGER: _anahtar_deger_sinyali(satirlar),
        Sekil.SERBEST_METIN: _serbest_metin_sinyali(satirlar),
    }

    kanitlar = [(s.value, f"{p:.2f} — {aciklama}")
                for s, (p, aciklama) in olcumler.items()]
    siralı = sorted(((s, p) for s, (p, _) in olcumler.items()),
                    key=lambda x: x[1], reverse=True)
    birinci, ikinci = siralı[0], siralı[1]

    # Log ve tablo ayni anda yuksekse: log satirlari da ayrac icerebilir.
    # Zaman damgasi daha ayirt edici oldugu icin log oncelenir.
    if (olcumler[Sekil.LOG][0] >= 0.7
            and birinci[0] is Sekil.TABLO):
        birinci, ikinci = (Sekil.LOG, olcumler[Sekil.LOG][0]), birinci

    if birinci[1] < ESIK_KARAR:
        return SekilSonuc(
            Sekil.BELIRSIZ, "dusuk", kanitlar, siralı[:3],
            yargi_gerekir=True,
        )

    if birinci[1] - ikinci[1] < ESIK_FARK:
        return SekilSonuc(
            Sekil.BELIRSIZ, "dusuk",
            kanitlar + [("celiski",
                         f"{birinci[0].value} ({birinci[1]:.2f}) ve "
                         f"{ikinci[0].value} ({ikinci[1]:.2f}) yakin")],
            siralı[:3], yargi_gerekir=True,
        )

    guven = "yuksek" if birinci[1] >= 0.8 else "orta"
    return SekilSonuc(birinci[0], guven, kanitlar, siralı[:3])
