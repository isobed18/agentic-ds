"""
KESIF — yonlendirici

Dort katmani sirayla calistirir ve her dosya icin bir AKIS karari uretir.
Karar veremediginde uydurmaz; artik olarak isaretler ve sebebini yazar.

    1. Magika          format          ~4 ms      deterministik
    2. Sekil tespiti   yapisal sekil   mikrosaniye deterministik
    3. Kanit katmani   kodlama/tip     mikrosaniye deterministik
    4. YARGI           sadece artik    dis katman  (bu modul CAGIRMAZ)

Dorduncu katman bilerek burada YOK. Bu modul deterministik kalir; yargi
gerektiren dosyalari isaretleyip disariya birakir. Boylece deterministik
katmanin vetosu korunur ve eskalasyon orani olculebilir hale gelir.

MAGIKA DUZELTMESI: olcumle su tespit edildi — kisa dosyalarda Magika
yuksek guvenle yanlis cevap verebiliyor (tek sutunlu 3 satirlik CSV ->
'ignorefile', guven 0.953). Bu yuzden kisa dosyalarda Magika'nin karari
tek basina kabul edilmez, kendi sekil olcumumuzle dogrulanir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from magika import Magika

from .kanit import kodlama_tespit
from .sekil import Sekil, sekil_tespit

_magika = Magika()

# Olcumle belirlenen esikler.
KISA_DOSYA_SATIR = 8        # bunun altinda Magika tek basina yeterli degil
KISA_DOSYA_BAYT = 512
MAGIKA_GUVEN_ESIGI = 0.90

METIN_TURLERI = {"csv", "tsv", "txt", "json", "jsonl", "markdown", "xml",
                 "html", "yaml", "toml", "ini", "ignorefile", "empty", "unknown"}
BELGE_TURLERI = {"pdf", "docx", "doc", "pptx", "odt", "rtf", "epub"}
KAPSAYICI_TURLERI = {"zip", "tar", "gzip", "7z", "rar", "xlsx", "xlsm"}

# Bazi formatlarda akis zaten formattan bellidir; sekil olcumune gerek yok.
# Bir markdown dosyasinin "sekli" belirsiz cikabilir ama markdown olmasi
# tek basina belge akisina gitmesi icin yeterlidir.
FORMAT_AKIS_BELIRLER: dict[str, str] = {
    "json": "agac", "jsonl": "agac", "xml": "agac",
    "yaml": "agac", "toml": "agac", "ini": "agac",
    "markdown": "belge", "html": "belge",
}
# Bu formatlar genel amaclidir: icerik ne oldugunu sekil olcumu soyler.
SEKIL_GEREKTIREN = {"txt", "csv", "tsv", "ignorefile", "unknown", "dat"}


class Akis(str, Enum):
    TABLO = "tablo"
    BELGE = "belge"
    AGAC = "agac"
    KAPSAYICI = "kapsayici"
    ISLENEMEZ = "islenemez"
    YARGI = "yargi"          # deterministik katman karar veremedi


@dataclass
class Karar:
    yol: str
    boyut: int
    format: str
    format_guveni: float
    sekil: str
    akis: Akis
    deterministik: bool = True
    yargi_sebebi: str = ""
    notlar: list[str] = field(default_factory=list)
    kanitlar: list[tuple[str, str]] = field(default_factory=list)


def _kisa_mi(ham: bytes) -> bool:
    return (len(ham) < KISA_DOSYA_BAYT
            or ham.count(b"\n") < KISA_DOSYA_SATIR)


def _yapisal_dogrula(metin: str) -> tuple[str, str] | None:
    """Yapisi kesin dogrulanabilen formatlari AYRISTIRARAK teyit et.

    Bu adim tahmini kanita cevirir: bir metin json.loads ile ayrisiyorsa
    o metin JSON'dur, model ne derse desin. Yargiya cikmasi gereken bir
    belirsizlik degildir. Ucuz, deterministik, kesin.
    """
    import json as _json
    import tomllib
    import xml.etree.ElementTree as _et

    kirp = metin.strip()
    if not kirp:
        return None

    if kirp[0] in "{[":
        try:
            _json.loads(kirp)
            return "json", "json.loads ile ayristi"
        except ValueError:
            pass

    # JSON Lines: her satir ayri bir JSON belgesi
    satirlar = [s for s in kirp.splitlines() if s.strip()]
    if len(satirlar) >= 2 and all(s.lstrip()[:1] in "{[" for s in satirlar):
        try:
            for s in satirlar:
                _json.loads(s)
            return "jsonl", f"{len(satirlar)} satirin tamami JSON olarak ayristi"
        except ValueError:
            pass

    if kirp[0] == "<":
        try:
            _et.fromstring(kirp)
            return "xml", "XML olarak ayristi"
        except _et.ParseError:
            pass

    try:
        veri = tomllib.loads(kirp)
        if veri:
            return "toml", "TOML olarak ayristi"
    except (tomllib.TOMLDecodeError, ValueError):
        pass

    return None


def yonlendir(yol: Path) -> Karar:
    ham = yol.read_bytes()
    m = _magika.identify_path(yol)
    format_ = m.output.label
    guven = float(m.score)

    k = Karar(yol=str(yol), boyut=len(ham), format=format_,
              format_guveni=guven, sekil="-", akis=Akis.YARGI)

    if not ham:
        k.akis = Akis.ISLENEMEZ
        k.notlar.append("dosya bos")
        return k

    # --- ikili / kapsayici / belge: sekil olcumu uygulanmaz -------------
    if format_ in KAPSAYICI_TURLERI:
        k.akis = Akis.KAPSAYICI
        k.notlar.append("icerik acilip her bilesen yeniden yonlendirilmeli")
        return k

    if format_ in BELGE_TURLERI:
        k.akis = Akis.BELGE
        k.notlar.append("belge cozumleyiciye gider")
        return k

    if format_ not in METIN_TURLERI:
        k.akis = Akis.ISLENEMEZ
        k.notlar.append(f"'{format_}' icin cozumleyici tanimli degil")
        return k

    # Ikili icerik: metin olarak islenemez. Yargi gerektirmez, olculebilir.
    ornek = ham[:8192]
    if b"\x00" in ornek or sum(1 for b in ornek if b < 9 or 13 < b < 32) > len(ornek) * 0.10:
        k.akis = Akis.ISLENEMEZ
        k.notlar.append("ikili icerik: yazdirilamayan bayt orani yuksek, "
                        "metin cozumleyiciye giremez")
        k.kanitlar.append(("ikili tespiti",
                           f"null bayt {'var' if b'\\x00' in ornek else 'yok'}, "
                           f"kontrol karakteri orani yuksek"))
        return k

    # --- metin: kodlamayi coz, sonra sekli olc -------------------------
    kod = kodlama_tespit(ham[:128_000])
    k.kanitlar.extend(kod.kanitlar)
    metin = ham.decode(kod.secilen, errors="replace")

    if kod.karar_verilemedi:
        k.deterministik = False
        k.akis = Akis.YARGI
        k.yargi_sebebi = "kodlama belirsiz, dil kaniti yetersiz"
        return k

    # --- yapisal dogrulama: tahmini kanita cevirir ---------------------
    # HTML cogu zaman gecerli XML'dir ama XML degildir: belgedir. Daha
    # ozel bir etiket varken genel ayristirma onu geride birakmamali.
    DAHA_OZEL = {"html": {"xml"}, "markdown": {"xml"}, "jsonl": set()}
    dogrulanan = _yapisal_dogrula(metin)
    if dogrulanan and dogrulanan[0] in DAHA_OZEL.get(format_, set()):
        k.kanitlar.append((
            "yapisal dogrulama",
            f"{dogrulanan[0]} olarak da ayrisiyor, ancak '{format_}' "
            "daha ozel bir siniflandirma; korundu",
        ))
        dogrulanan = None

    if dogrulanan:
        gercek, kanit = dogrulanan
        k.kanitlar.append(("yapisal dogrulama", f"{gercek}: {kanit}"))
        if gercek != format_:
            k.notlar.append(
                f"Magika '{format_}' ({guven:.3f}) dedi, ayristirma "
                f"'{gercek}' dogruladi; ayristirma esas alindi"
            )
        k.format = format_ = gercek
        k.format_guveni = guven = 1.0

    s = sekil_tespit(metin)
    k.sekil = s.sekil.value
    k.kanitlar.extend(s.kanitlar)

    # --- format akisi zaten belirliyorsa sekil olcumu belirleyici degil ---
    belirleyici = FORMAT_AKIS_BELIRLER.get(format_)
    if belirleyici and guven >= MAGIKA_GUVEN_ESIGI:
        k.akis = Akis(belirleyici)
        k.notlar.append(
            f"akis formattan belirlendi ({format_}, guven {guven:.3f}); "
            "sekil olcumu belirleyici degil"
        )
        if k.akis is Akis.BELGE:
            k.notlar.append("serbest metin ajan baglamina girecek, "
                            "veri olarak etiketlenmeli")
        return k

    # --- Magika kisa dosyada guvenilmez: kendi olcumumuzle dogrula -----
    kisa = _kisa_mi(ham)
    if kisa and guven < 1.0:
        k.notlar.append(
            f"kisa dosya ({ham.count(chr(10).encode()[0])} satir); "
            f"Magika karari ({format_}, {guven:.3f}) tek basina kabul edilmedi"
        )
        if s.yargi_gerekir:
            k.deterministik = False
            k.akis = Akis.YARGI
            k.yargi_sebebi = "kisa dosya, sekil de belirsiz"
            return k
        k.notlar.append(f"sekil olcumu karari verdi: {s.sekil.value}")

    elif guven < MAGIKA_GUVEN_ESIGI and s.yargi_gerekir:
        k.deterministik = False
        k.akis = Akis.YARGI
        k.yargi_sebebi = (f"format guveni dusuk ({guven:.2f}) ve "
                          "sekil de belirsiz")
        return k

    if s.yargi_gerekir:
        # Sekil belirsiz ama format bir akisa isaret ediyorsa, dusuk guvenle
        # de olsa formati kullan; hicbir sey bilmemekten iyidir.
        if belirleyici:
            k.akis = Akis(belirleyici)
            k.notlar.append(
                f"sekil belirsiz; formata ({format_}, {guven:.2f}) gore "
                "yonlendirildi, dogrulama onerilir"
            )
            return k
        k.deterministik = False
        k.akis = Akis.YARGI
        k.yargi_sebebi = "sekil belirsiz, sinyaller celisiyor veya zayif"
        return k

    # --- sekil -> akis ------------------------------------------------
    k.akis = {
        Sekil.TABLO: Akis.TABLO,
        Sekil.SABIT_GENISLIK: Akis.TABLO,
        Sekil.LOG: Akis.BELGE,
        Sekil.ANAHTAR_DEGER: Akis.AGAC,
        Sekil.SERBEST_METIN: Akis.BELGE,
    }[s.sekil]

    if belirleyici:
        k.akis = Akis(belirleyici)
        k.notlar.append(f"format ({format_}) sekil olcumunun onune gecti")

    if s.sekil is Sekil.SABIT_GENISLIK:
        k.notlar.append("sabit genislikli tablo, sutun sinirlari cikarilmali")
    if s.sekil is Sekil.LOG:
        k.notlar.append("log kaydi, satir bazli ayristirma uygun")
    if k.akis is Akis.BELGE:
        k.notlar.append("serbest metin ajan baglamina girecek, "
                        "veri olarak etiketlenmeli")

    return k


def envanter(kok: Path, desen: str = "*") -> dict:
    """Bir klasordeki butun dosyalari ucuz gecisten gecirip ozet cikar.

    Pahali islem yapilmaz. Amac, kullaniciya SECENEK sunabilmek icin
    once neyin geldigini bilmek.
    """
    kararlar = [yonlendir(p) for p in sorted(kok.rglob(desen)) if p.is_file()]
    if not kararlar:
        return {"dosya_sayisi": 0, "kararlar": []}

    akis_dagilimi: dict[str, int] = {}
    for k in kararlar:
        akis_dagilimi[k.akis.value] = akis_dagilimi.get(k.akis.value, 0) + 1

    det = sum(1 for k in kararlar if k.deterministik)
    return {
        "dosya_sayisi": len(kararlar),
        "akis_dagilimi": akis_dagilimi,
        "deterministik": det,
        "yargi_gerektiren": len(kararlar) - det,
        "eskalasyon_orani": round((len(kararlar) - det) / len(kararlar), 3),
        "yargi_sebepleri": sorted({k.yargi_sebebi for k in kararlar
                                   if k.yargi_sebebi}),
        "kararlar": kararlar,
    }


def secenek_uret(env: dict) -> dict:
    """Envanterden kullaniciya sunulacak somut secenekler uret.

    ILKE: soru bos sorulmaz. Once ucuz gecis herkese uygulanir, sonra
    "sunlari buldum, nereden baslayalim" diye sorulur. Boylece kullanici
    ne oldugunu BILEREK secer.

    Not: bu bir TERCIH sorusudur, olgu sorusu degil. Hangi dosyalarla
    ilgilenildigi hicbir olcumden cikmaz; sorulmasi gerekir.
    """
    d = env.get("akis_dagilimi", {})
    kararlar = env.get("kararlar", [])
    if not kararlar:
        return {"ozet": "dosya bulunamadi", "secenekler": []}

    tablo = d.get("tablo", 0)
    agac = d.get("agac", 0)
    belge = d.get("belge", 0)
    kapsayici = d.get("kapsayici", 0)
    yargi = d.get("yargi", 0)
    islenemez = d.get("islenemez", 0)

    parcalar = []
    for ad, n in (("tablo", tablo), ("ağaç", agac), ("belge", belge),
                  ("kapsayıcı", kapsayici)):
        if n:
            parcalar.append(f"{n} {ad}")
    ozet = ", ".join(parcalar) or "sınıflandırılabilir dosya yok"
    if yargi:
        ozet += f"; {yargi} dosya karar bekliyor"
    if islenemez:
        ozet += f"; {islenemez} dosya işlenemez"

    secenekler = []
    if tablo:
        secenekler.append({
            "kod": "A",
            "eylem": f"Sadece tablo akışı ({tablo} dosya)",
            "kazanc": "En hızlı sonuç; tiplenmiş sütunlar doğrudan analize girer.",
            "bedel": "Belge ve ağaç içeriği bu turda işlenmez.",
            "kapsam": ["tablo"],
        })
    if tablo or agac:
        secenekler.append({
            "kod": "B",
            "eylem": f"Yapılandırılmış veri: tablo ve ağaç ({tablo + agac} dosya)",
            "kazanc": "Şemalı verinin tamamı tek turda işlenir.",
            "bedel": "Serbest metin dışarıda kalır.",
            "kapsam": ["tablo", "agac"],
        })
    if belge:
        secenekler.append({
            "kod": "C",
            "eylem": f"Tümü, belge akışı dahil ({tablo + agac + belge} dosya)",
            "kazanc": "Hiçbir içerik dışarıda kalmaz.",
            "bedel": ("Belge çözümlemesi daha yavaş; serbest metin ajan "
                      "bağlamına gireceği için veri olarak etiketlenmeli."),
            "kapsam": ["tablo", "agac", "belge"],
        })

    ek = []
    if kapsayici:
        ek.append(f"{kapsayici} kapsayıcı dosya var; açılırsa "
                  "içindekiler yeniden yönlendirilecek.")
    if yargi:
        ek.append(f"{yargi} dosya deterministik olarak çözülemedi; "
                  "yargı katmanına çıkarılabilir.")

    return {
        "ozet": ozet,
        "dosya_sayisi": env["dosya_sayisi"],
        "eskalasyon_orani": env["eskalasyon_orani"],
        "secenekler": secenekler,
        "ek_notlar": ek,
        "soru_turu": "tercih",
        "aciklama": ("Bu bir tercih sorusudur, ölçümle cevaplanamaz. "
                     "Format ve şekil zaten ölçüldü; sorulan şey hangi "
                     "içerikle ilgilenildiği."),
    }
