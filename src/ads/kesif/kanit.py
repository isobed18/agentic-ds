"""
KESIF — kanit katmani

Burada hicbir sey tahmin edilmez, olculur. Her fonksiyon
"su degeri buldum" degil, "su kaniti gordum, aday su" doner.

EN ONEMLI FIKIR — kodlama ayrimi:
cp1252 ve cp1254 tam olarak 6 baytta ayrilir:
    0xD0  Ð / Ğ      0xDD  Y-aksanli / I-noktali
    0xDE  Th / S     0xF0  eth / g-yumusak
    0xFD  y-aksanli / i-noktasiz      0xFE  th / s-cedilla

Ama 6 Turkce harf IKISINDE DE AYNI konumdadir:
    0xC7 C   0xD6 O   0xDC U   0xE7 c   0xF6 o   0xFC u

Yani: UZLASAN harfler, AYRILAN harflerin nasil okunacagini soyler.
Dosyada hem "c/o/u" hem de ayrilan baytlar varsa, metin Turkcedir
ve dogru okuma cp1254'tur. Bu, dil tahmini degil; bayt kaniti.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Iki kodlamada da ayni olan Turkce harflerin baytlari.
UZLASAN_TR = {0xC7, 0xD6, 0xDC, 0xE7, 0xF6, 0xFC}

# cp1252 ve cp1254'un ayrildigi, Turkce'ye ozgu baytlar.
AYRILAN_TR = {0xD0, 0xDD, 0xDE, 0xF0, 0xFD, 0xFE}

# cp1254'te tanimsiz, cp1252'de tanimli baytlar: gorulurse cp1254 elenir.
CP1254_DISI = {0x8E, 0x9E}

# --- KARSI KANIT --------------------------------------------------------
# Turkce'de KULLANILMAYAN, Bati Avrupa dillerinde yaygin harfler.
# Hepsi cp1252 ve cp1254'te AYNI bayttadir; yani gorulmeleri kodlamadan
# bagimsiz bir dil sinyalidir. Varliklari "bu metin Turkce degil" der.
#
# NEDEN VAR: ilk kuralimiz (uzlasan + ayrilan -> Turkce) Izlandaca'da
# YANLIS POZITIF veriyordu. Izlandaca hem o (0xF6) hem d (0xF0) hem
# th (0xFE) kullanir; kural dosyayi Turkce sanip d -> g, th -> s
# donusturuyor ve tam da engellemeye calistigimiz sessiz bozulmayi
# kendimiz uretiyorduk. Bu kume o acigi kapatir.
BATI_OZEL = {
    0xC0, 0xC1, 0xC3, 0xC4, 0xC5, 0xC6, 0xC8, 0xC9, 0xCA, 0xCB, 0xCC,
    0xCD, 0xCF, 0xD1, 0xD2, 0xD3, 0xD4, 0xD5, 0xD8, 0xD9, 0xDA, 0xDF,
    0xE0, 0xE1, 0xE3, 0xE4, 0xE5, 0xE6, 0xE8, 0xE9, 0xEA, 0xEB, 0xEC,
    0xED, 0xEF, 0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF8, 0xF9, 0xFA, 0xFF,
}

# Izlandaca yazim kurali: th (0xFE) pratikte SADECE kelime basinda gecer;
# kelime ici ve sonunda d (0xF0) kullanilir. Turkce s ise her konumda
# gecer (sey / baska / is). Yani 0xFE'nin konumu iki dili ayirir.
_KELIME_ICI_FE = re.compile(r"(?<=\w)\xfe", re.UNICODE)


@dataclass
class KodlamaSonuc:
    secilen: str
    guven: str                       # kesin / yuksek / dusuk
    adaylar: list[str] = field(default_factory=list)
    kanitlar: list[tuple[str, str]] = field(default_factory=list)
    belirsiz: bool = False           # birden fazla aday hatasiz coz(uyor)
    karar_verilemedi: bool = False   # kanitlar celisiyor -> insana sor


def kodlama_tespit(ham: bytes) -> KodlamaSonuc:
    """Bayt dizisine bakarak kodlamayi kanitla belirle."""
    kanitlar: list[tuple[str, str]] = []

    # 1) BOM varsa tartisma yok.
    if ham.startswith(b"\xef\xbb\xbf"):
        return KodlamaSonuc("utf-8-sig", "kesin", ["utf-8-sig"],
                            [("BOM", "UTF-8 BOM bulundu")])

    # 2) UTF-8 kendi kendini dogrular: gecerse baska ihtimal pratikte yok.
    try:
        ham.decode("utf-8")
    except UnicodeDecodeError as hata:
        kanitlar.append(("utf-8 denemesi", f"basarisiz, bayt {hata.start}"))
    else:
        cok_baytli = sum(1 for b in ham if b >= 0x80)
        return KodlamaSonuc(
            "utf-8", "kesin", ["utf-8"],
            kanitlar + [("utf-8 denemesi", "basarili"),
                        ("ASCII disi bayt", str(cok_baytli))],
        )

    # 3) Tek baytli kodlama. Hangisi oldugunu bayt imzasindan cikar.
    baytlar = set(ham)
    uzlasan = sorted(baytlar & UZLASAN_TR)
    ayrilan = sorted(baytlar & AYRILAN_TR)
    cp1252_ozel = sorted(baytlar & CP1254_DISI)

    kanitlar.append((
        "iki kodlamada ayni olan TR harfleri",
        f"{len(uzlasan)} farkli bayt" + (
            f" ({', '.join(hex(b) for b in uzlasan)})" if uzlasan else ""),
    ))
    kanitlar.append((
        "cp1252/cp1254 ayrimindaki baytlar",
        f"{len(ayrilan)} farkli bayt" + (
            f" ({', '.join(hex(b) for b in ayrilan)})" if ayrilan else ""),
    ))

    if cp1252_ozel:
        kanitlar.append(("cp1254'te tanimsiz bayt",
                         ", ".join(hex(b) for b in cp1252_ozel)))
        return KodlamaSonuc("cp1252", "yuksek", ["cp1252", "latin-1"], kanitlar)

    # Ayrisan bayt yoksa ortada bir ikilem de yok.
    if not ayrilan:
        return KodlamaSonuc("cp1252", "yuksek", ["cp1252", "latin-1"], kanitlar)

    # --- IKI YONLU KANIT TOPLAMA ---------------------------------------
    # Tek yonlu kural (sadece "Turkce mi") Izlandaca'da yanlis pozitif
    # veriyordu. Artik hem lehte hem aleyhte kanit toplanip karsilastiriliyor.
    cp1252_metin = ham.decode("cp1252", errors="replace")

    # (a) Turkce'de olmayan harfler: en guclu karsi kanit.
    bati = sorted(baytlar & BATI_OZEL)

    # (b) Diken (thorn) konum testi. Izlandaca'da th SADECE kelime basinda
    #     gecer; Turkce s her konumda gecer (sey / baska / is).
    #     Buyuk ve kucuk hali birlikte sayilir.
    diken_toplam = cp1252_metin.count("\xfe") + cp1252_metin.count("\xde")
    diken_kelime_ici = len(_KELIME_ICI_FE.findall(cp1252_metin))
    diken_kalibi = diken_toplam >= 2 and diken_kelime_ici == 0

    yabanci_puan = (2 if bati else 0) + (1 if diken_kalibi else 0)
    turkce_puan = len(uzlasan)

    if bati:
        kanitlar.append((
            "Turkce'de kullanilmayan harfler",
            f"{len(bati)} farkli bayt ("
            + ", ".join(f"{hex(b)}={bytes([b]).decode('cp1252')}"
                        for b in bati[:6]) + ")",
        ))
    if diken_kalibi:
        kanitlar.append((
            "diken (0xDE/0xFE) konum testi",
            f"{diken_toplam} kez gecti, hicbiri kelime ici degil "
            "-> Izlandaca th kalibi; Turkce s kelime icinde de gecerdi",
        ))

    # --- KARAR ---------------------------------------------------------
    # Guclu karsi kanit: metin Turkce degil.
    if yabanci_puan >= 2:
        kanitlar.append(("degerlendirme",
                         "metin Turkce degil; ayrisan baytlar cp1252 okunmali"))
        return KodlamaSonuc("cp1252", "yuksek",
                            ["cp1252", "latin-1"], kanitlar)

    # Turkce lehine kanit var, aleyhine hicbir sey yok.
    if turkce_puan and yabanci_puan == 0:
        ornek = _ilk_tr_kelime(ham.decode("cp1254", errors="replace"))
        if ornek:
            kanitlar.append(("cp1254 okumasi ornek", ornek))
        kanitlar.append(("karsi kanit", "Turkce disi harf bulunmadi"))
        return KodlamaSonuc(
            "cp1254", "yuksek", ["cp1254", "cp1252", "latin-1"], kanitlar,
            belirsiz=True,
        )

    # Geri kalan her sey: kanitlar celisiyor ya da hicbiri yeterli degil.
    # Burada bir kodlama secip "yuksek guven" demek, tam olarak
    # engellemeye calistigimiz sessiz bozulmayi uretmek olurdu.
    if turkce_puan and yabanci_puan:
        kanitlar.append((
            "degerlendirme",
            f"kanitlar celisiyor (Turkce lehine {turkce_puan}, "
            f"aleyhine {yabanci_puan}); karar insana birakiliyor",
        ))
    else:
        kanitlar.append((
            "degerlendirme",
            "ayrisan baytlar var ama hangi dil oldugunu gosteren "
            "yeterli kanit yok; karar insana birakiliyor",
        ))
    return KodlamaSonuc(
        "cp1252", "dusuk", ["cp1252", "cp1254", "latin-1"], kanitlar,
        belirsiz=True, karar_verilemedi=True,
    )


def _ilk_tr_kelime(metin: str) -> str:
    """Turkce harf iceren ilk kelimeyi ornek olarak dondur."""
    for kelime in re.findall(r"\S+", metin):
        if any(h in kelime for h in "ğĞıİşŞçÇöÖüÜ"):
            return kelime[:40]
    return ""


# ---------------------------------------------------------------------------
# SAYI BICIMI
# ---------------------------------------------------------------------------

# Kesin TR: binlik nokta + ondalik virgul  ->  1.234,56
_TR_KESIN = re.compile(r"^-?\d{1,3}(\.\d{3})+,\d+$")
# Kesin EN: binlik virgul + ondalik nokta  ->  1,234.56
_EN_KESIN = re.compile(r"^-?\d{1,3}(,\d{3})+\.\d+$")
# Virgul ondalik: virgulden sonra 3 HANE DEGILSE binlik olamaz -> 987,25
_TR_ONDALIK = re.compile(r"^-?\d+,\d{1,2}$|^-?\d+,\d{4,}$")
# Nokta ondalik: ayni mantik  -> 987.25
_EN_ONDALIK = re.compile(r"^-?\d+\.\d{1,2}$|^-?\d+\.\d{4,}$")
# Coklu ayrac: 1.234.567 -> noktalar binlik   /  1,234,567 -> virguller binlik
_TR_COKLU = re.compile(r"^-?\d{1,3}(\.\d{3}){2,}$")
_EN_COKLU = re.compile(r"^-?\d{1,3}(,\d{3}){2,}$")
# Belirsiz: 1.234 veya 1,234 -> tek ayrac, tam 3 hane
_BELIRSIZ = re.compile(r"^-?\d{1,3}[.,]\d{3}$")


@dataclass
class SayiBicimSonuc:
    ondalik: str | None              # "," veya "." veya None (sayi degil)
    binlik: str | None
    guven: str
    tr_kanit: int = 0
    en_kanit: int = 0
    belirsiz_adet: int = 0
    ornekler: list[str] = field(default_factory=list)


def sayi_bicimi_tespit(degerler: list[str]) -> SayiBicimSonuc:
    """Bir sutundaki metin degerlerin hangi sayi biciminde oldugunu olc.

    Sadece KESIN kanitlari sayar. Belirsiz degerler ayri sayilir,
    karar icin kullanilmaz.
    """
    tr = en = belirsiz = 0
    tr_ornek: list[str] = []
    en_ornek: list[str] = []

    for ham in degerler:
        d = (ham or "").strip()
        if not d:
            continue
        if _BELIRSIZ.match(d):
            belirsiz += 1
            continue
        if _TR_KESIN.match(d) or _TR_ONDALIK.match(d) or _TR_COKLU.match(d):
            tr += 1
            if len(tr_ornek) < 3:
                tr_ornek.append(d)
        elif _EN_KESIN.match(d) or _EN_ONDALIK.match(d) or _EN_COKLU.match(d):
            en += 1
            if len(en_ornek) < 3:
                en_ornek.append(d)

    if tr == 0 and en == 0:
        return SayiBicimSonuc(None, None, "yok",
                              belirsiz_adet=belirsiz)

    if tr and not en:
        return SayiBicimSonuc(",", ".", "yuksek", tr, en, belirsiz, tr_ornek)
    if en and not tr:
        return SayiBicimSonuc(".", ",", "yuksek", tr, en, belirsiz, en_ornek)

    # Ikisi de var: dosya karisik. Bu bir uyari durumu.
    baskin = "," if tr > en else "."
    return SayiBicimSonuc(
        baskin, "." if baskin == "," else ",", "dusuk",
        tr, en, belirsiz, (tr_ornek + en_ornek)[:4],
    )


# ---------------------------------------------------------------------------
# AYRAC
# ---------------------------------------------------------------------------

@dataclass
class AyracSonuc:
    ayrac: str
    guven: str
    sayimlar: dict[str, int] = field(default_factory=dict)


def ayrac_tespit(ornek: str) -> AyracSonuc:
    """Satirlar arasinda TUTARLI olan ayraci sec.

    Tek satirda en cok gecen karakter degil; her satirda ayni sayida
    gecen karakter dogru ayractir. Bu, metin icindeki virgulleri eler.
    """
    satirlar = [s for s in ornek.splitlines() if s.strip()][:20]
    if len(satirlar) < 2:
        return AyracSonuc(",", "dusuk", {})

    sayimlar: dict[str, int] = {}
    tutarli: dict[str, int] = {}
    for aday in (";", ",", "\t", "|"):
        adetler = [s.count(aday) for s in satirlar]
        sayimlar[aday] = sum(adetler)
        if adetler[0] > 0 and len(set(adetler)) == 1:
            tutarli[aday] = adetler[0]

    if tutarli:
        secilen = max(tutarli, key=lambda k: tutarli[k])
        return AyracSonuc(secilen, "yuksek", sayimlar)

    if any(sayimlar.values()):
        secilen = max(sayimlar, key=lambda k: sayimlar[k])
        return AyracSonuc(secilen, "dusuk", sayimlar)

    return AyracSonuc(",", "dusuk", sayimlar)
