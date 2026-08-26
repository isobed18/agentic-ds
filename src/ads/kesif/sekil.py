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
from enum import StrEnum


class Sekil(StrEnum):
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
    """Sutun araligi tespiti: satirlar boyunca tekrar eden BOSLUK SUTUNLARI.

    Ilk surum yalnizca sol kenari ariyordu ("iki+ bosluk sonrasi ilk
    karakter"). Bu, SAGA HIZALI sayi sutunlarinda calismiyordu: baslik
    sola, sayilar saga yaslandigi icin sol kenar satirdan satira kayiyor
    ve hizalama goremiyorduk. Olcumde `hizali_rapor.txt` tam bu yuzden
    kaciyordu.

    Dogru degismez, sutunun kenari degil ARADAKI BOSLUK: gercek bir sabit
    genislikli tabloda belirli karakter konumlari neredeyse her satirda
    bosluktur. Hizalama yonunden bagimsizdir.
    """
    ornek = [s for s in satirlar if len(s) > 12][:60]
    if len(ornek) < 3:
        return 0.0, "satir yetersiz"

    genislik = min(len(s) for s in ornek)
    if genislik < 8:
        return 0.0, "satirlar cok kisa"

    # Her karakter konumu icin: kac satirda bosluk?
    bosluk_orani = [
        sum(1 for s in ornek if s[i] == " ") / len(ornek)
        for i in range(genislik)
    ]

    # Neredeyse her satirda bosluk olan konumlar = ayirici sutunlar.
    ayirici = [i for i, o in enumerate(bosluk_orani) if o >= 0.9]
    if not ayirici:
        return 0.0, "her satirda bosluk olan konum yok"

    # Bitisik konumlari tek bir "bosluk blogu" say; 1 karakterlik tek
    # bosluklar kelime arasi olabilir, en az 2 genisligindekiler sutun
    # ayiricisidir.
    bloklar: list[list[int]] = []
    for i in ayirici:
        if bloklar and i == bloklar[-1][-1] + 1:
            bloklar[-1].append(i)
        else:
            bloklar.append([i])
    gercek = [b for b in bloklar if len(b) >= 2]

    # Satir basindaki girinti bir sutun ayiricisi degildir.
    gercek = [b for b in gercek if b[0] != 0]

    if not gercek:
        return 0.0, "hizali sutun ayiricisi yok"

    oran = min(1.0, len(gercek) / 2)
    return oran, (f"{len(gercek)} sutun ayiricisi, "
                  f"satirlarin >=%90'inda ayni konumda bosluk")


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

    # Log ile tablo ayni anda yuksek olabilir, iki ayri sebeple:
    #   (a) log satirlari da ayrac (virgul, bosluk) icerebilir
    #   (b) ILK SUTUNU TARIH olan bir CSV, zaman damgasiyla baslayan
    #       satirlar uretir ve log gibi gorunur
    # Ayirt eden kanit SEVIYE ETIKETI (INFO/WARN/ERROR): gercek log'da
    # vardir, tarih sutunlu tabloda yoktur. Olcumde `satis_2024.csv`
    # tam (b) yuzunden human feedback'e dusuyordu.
    seviye_var = bool(LOG_SEVIYE.search("\n".join(satirlar)))
    if (olcumler[Sekil.LOG][0] >= 0.7
            and birinci[0] is Sekil.TABLO
            and seviye_var):
        birinci, ikinci = (Sekil.LOG, olcumler[Sekil.LOG][0]), birinci
    elif (birinci[0] is Sekil.TABLO
            and olcumler[Sekil.TABLO][0] >= 0.95
            and not seviye_var):
        # Tutarli ayrac kesin: tarih sutunu log kanitti sayilmaz.
        ikinci = max(
            ((s, p) for s, p in siralı if s not in (Sekil.TABLO, Sekil.LOG)),
            key=lambda x: x[1], default=(Sekil.BELIRSIZ, 0.0),
        )

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
