"""Deterministic content-based file routing.

The router applies format detection, structural-shape measurement, and
encoding/type evidence in that order. It never invents a route when those
measurements cannot decide; unresolved files are marked for adjudication.

Short files need special care. Magika measured a three-row, one-column CSV as
``ignorefile`` with 0.953 confidence, so its label is not accepted alone for
short inputs and must agree with the package's structural measurement.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from magika import Magika

from .evidence import detect_encoding
from .text_shape import TextShape, detect_shape

_magika = Magika()

# Olcumle belirlenen esikler.
KISA_DOSYA_SATIR = 8        # bunun altinda Magika tek basina yeterli degil
KISA_DOSYA_BAYT = 512
MAGIKA_GUVEN_ESIGI = 0.90

METIN_TURLERI = {"csv", "tsv", "txt", "json", "jsonl", "markdown", "xml",
                 "html", "yaml", "toml", "ini", "ignorefile", "empty", "unknown"}
BELGE_TURLERI = {"pdf", "docx", "doc", "pptx", "odt", "rtf", "epub"}
KAPSAYICI_TURLERI = {"zip", "tar", "gzip", "7z", "rar"}
# XLSX teknik olarak zip'tir ama KAPSAYICI DEGILDIR: sayfalardan olusan
# bir tablo dosyasidir. Zip diye isaretlemek icerigini kaybettirirdi.
CALISMA_KITABI_TURLERI = {"xlsx", "xlsm"}
# Sutunlu ikili tablo. Parquet ne kapsayici ne belgedir: dosyanin
# basinda ve sonunda 'PAR1' imzasini tasiyan bir tablo dosyasidir.
# Cozumleyicisi tanimli olmadigi icin human feedback'e dusuyordu, oysa
# `ads.intake` onu zaten sorunsuz okuyor.
SUTUNLU_TABLO_TURLERI = {"parquet"}
PARQUET_IMZA = b"PAR1"

# Goruntu: metin SECILEMEZ, OCR gerekir. Taranmis tablo ve ekran
# goruntusu bu yoldan gecer.
GORUNTU_TURLERI = {"png", "jpeg", "jpg", "gif", "bmp", "tiff", "webp"}

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


class Flow(StrEnum):
    TABLE = "tablo"
    DOCUMENT = "belge"
    TREE = "agac"
    CONTAINER = "kapsayici"
    UNPROCESSABLE = "islenemez"
    ADJUDICATION = "yargi"

    # Temporary aliases keep the first rename PR behavior-neutral.
    TABLO = TABLE
    BELGE = DOCUMENT
    AGAC = TREE
    KAPSAYICI = CONTAINER
    ISLENEMEZ = UNPROCESSABLE
    YARGI = ADJUDICATION


@dataclass
class Decision:
    yol: str
    boyut: int
    format: str
    format_guveni: float
    sekil: str
    akis: Flow
    deterministik: bool = True
    yargi_sebebi: str = ""
    notlar: list[str] = field(default_factory=list)
    kanitlar: list[tuple[str, str]] = field(default_factory=list)


def _is_short(ham: bytes, boyut: int) -> bool:
    # Uzunluk GERCEK dosya boyutundan, satir sayisi okunan onekten gelir:
    # onek sinirina takilmis buyuk bir dosya "kisa" sayilmamali.
    return boyut < KISA_DOSYA_BAYT or ham.count(b"\n") < KISA_DOSYA_SATIR


# Dosyanin tamamini okumuyoruz. Kodlama tespiti zaten en fazla bu kadarini
# kullaniyor, sekil olcumu de ilk birkac yuz satirda karara variyor.
# `source_profile()` bunu koşum oncesi HER dosya icin cagiriyor; orada
# 121 MB'lik bir veri setini bellege almak olcum degil israf olurdu.
OKUMA_SINIRI = 128_000


def _read_prefix(yol: Path) -> tuple[bytes, int]:
    """Dosyanin basindan sinirli bir onek ve GERCEK boyutunu dondur.

    Onek satir sinirinda kesilir: ortadan bolunmus bir cok baytli karakter
    kodlama tespitini yaniltabilirdi (utf-8 gecerliyken gecersiz gorunur).
    """
    boyut = yol.stat().st_size
    with yol.open("rb") as f:
        ham = f.read(OKUMA_SINIRI)
    if boyut > OKUMA_SINIRI:
        son_satir = ham.rfind(b"\n")
        if son_satir > 0:
            ham = ham[:son_satir + 1]
    return ham, boyut


def _validate_structure(metin: str) -> tuple[str, str] | None:
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


def route(yol: Path) -> Decision:
    ham, boyut = _read_prefix(yol)
    m = _magika.identify_path(yol)
    format_ = m.output.label
    guven = float(m.score)

    k = Decision(yol=str(yol), boyut=boyut, format=format_,
              format_guveni=guven, sekil="-", akis=Flow.YARGI)

    if boyut == 0:
        k.akis = Flow.ISLENEMEZ
        k.notlar.append("dosya bos")
        return k

    # --- ikili / kapsayici / belge: sekil olcumu uygulanmaz -------------
    # Magika'nin etiketine guvenmeden once guvenini de kontrol ederiz:
    # dusuk guvenli bir "zip" ya da "pdf" etiketi de bir tahmindir, olgu
    # degil. Dusukse human feedback istenir, sessizce kabul edilmez.
    if format_ in KAPSAYICI_TURLERI:
        # Once ACARAK dogrula: zip ailesi ayristirilabilir, yani bu bir
        # olgu sorusudur, tahmin degil. Kural 4 ("ayristirilabilen sey
        # olculur") burada da gecerli; acilan bir arsiv icin dusuk model
        # guvenine bakip human feedback istemek gereksiz eskalasyondur.
        if zipfile.is_zipfile(yol):
            k.akis = Flow.KAPSAYICI
            k.format_guveni = guven = 1.0
            k.kanitlar.append(("yapisal dogrulama", "zip olarak acildi"))
            k.notlar.append("icerik acilip her bilesen yeniden yonlendirilmeli")
            return k
        if guven < MAGIKA_GUVEN_ESIGI:
            k.deterministik = False
            k.akis = Flow.YARGI
            k.yargi_sebebi = (f"format guveni dusuk ({guven:.2f}) ve arsiv "
                              "olarak acilamadi; human feedback gerekir")
            return k
        k.akis = Flow.KAPSAYICI
        k.notlar.append("icerik acilip her bilesen yeniden yonlendirilmeli")
        return k

    # --- calisma kitabi: sayfalari ac, tablo mu olc ---------------------
    if format_ in CALISMA_KITABI_TURLERI:
        return _route_workbook(k)

    # --- sutunlu ikili tablo: imzayi dogrula ---------------------------
    if format_ in SUTUNLU_TABLO_TURLERI:
        return _route_columnar_table(k)

    # --- goruntu: OCR ile oku, ama Turkce sinirini gizleme --------------
    if format_ in GORUNTU_TURLERI:
        return _route_image(k, guven)

    if format_ in BELGE_TURLERI:
        if guven < MAGIKA_GUVEN_ESIGI:
            k.deterministik = False
            k.akis = Flow.YARGI
            k.yargi_sebebi = (f"format guveni dusuk ({guven:.2f}); "
                              "belge siniflandirmasi human feedback gerektirir")
            return k
        # PDF'i "belge" diye gecmeden once METIN KATMANI VAR MI olc.
        # Taranmis bir PDF'te metin yoktur; belge akisina yollamak metin
        # cikaricinin bos donmesi, yani SESSIZ VERI KAYBI demektir.
        if format_ == "pdf":
            return _route_pdf(k)
        k.akis = Flow.BELGE
        k.notlar.append("belge cozumleyiciye gider")
        return k

    # Taninmayan format: "islenemez" de bir karardir, sessizce verilmez.
    # Insan "hayir, yine de belge olarak isle" diyebilir; sistem bu secimi
    # kendi basina yapip dosyayi sessizce eleyemez.
    if format_ not in METIN_TURLERI:
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = f"'{format_}' icin cozumleyici tanimli degil, human feedback gerekir"
        return k

    # Ikili icerik supheli: yazdirilamayan bayt orani yuksek. Ama bu bir
    # ESIK (%10), yani kesin olgu degil bir sezgi. Ayrica UTF-16 gibi cok
    # baytli kodlamalarda gecerli metin de sik sik null bayt icerir; bu
    # sezgi boyle bir dosyayi yanlislikla "ikili" damgalayabilir. Bu
    # yuzden otomatik ISLENEMEZ yerine human feedback istenir.
    ornek = ham[:8192]
    if b"\x00" in ornek or sum(1 for b in ornek if b < 9 or 13 < b < 32) > len(ornek) * 0.10:
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = ("yazdirilamayan bayt orani yuksek; gercekten ikili mi "
                          "yoksa yanlis kodlanmis metin mi (orn. UTF-16) "
                          "human feedback ayirt etsin")
        k.kanitlar.append(("ikili tespiti",
                           f"null bayt {'var' if b'\\x00' in ornek else 'yok'}, "
                           f"kontrol karakteri orani yuksek"))
        return k

    # --- metin: kodlamayi coz, sonra sekli olc -------------------------
    kod = detect_encoding(ham)
    k.kanitlar.extend(kod.kanitlar)
    metin = ham.decode(kod.secilen, errors="replace")

    if kod.karar_verilemedi:
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = "kodlama belirsiz, dil kaniti yetersiz"
        return k

    # --- yapisal dogrulama: tahmini kanita cevirir ---------------------
    # HTML cogu zaman gecerli XML'dir ama XML degildir: belgedir. Daha
    # ozel bir etiket varken genel ayristirma onu geride birakmamali.
    DAHA_OZEL = {"html": {"xml"}, "markdown": {"xml"}, "jsonl": set()}
    dogrulanan = _validate_structure(metin)
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

    s = detect_shape(metin)
    k.sekil = s.sekil.value
    k.kanitlar.extend(s.kanitlar)

    # --- format akisi zaten belirliyorsa sekil olcumu belirleyici degil ---
    belirleyici = FORMAT_AKIS_BELIRLER.get(format_)
    if belirleyici and guven >= MAGIKA_GUVEN_ESIGI:
        k.akis = Flow(belirleyici)
        k.notlar.append(
            f"akis formattan belirlendi ({format_}, guven {guven:.3f}); "
            "sekil olcumu belirleyici degil"
        )
        if k.akis is Flow.BELGE:
            k.notlar.append("serbest metin ajan baglamina girecek, "
                            "veri olarak etiketlenmeli")
        return k

    # --- Magika kisa dosyada guvenilmez: kendi olcumumuzle dogrula -----
    kisa = _is_short(ham, boyut)
    if kisa and guven < 1.0:
        k.notlar.append(
            f"kisa dosya ({ham.count(chr(10).encode()[0])} satir); "
            f"Magika karari ({format_}, {guven:.3f}) tek basina kabul edilmedi"
        )
        if s.yargi_gerekir:
            k.deterministik = False
            k.akis = Flow.YARGI
            k.yargi_sebebi = "kisa dosya, sekil de belirsiz"
            return k
        k.notlar.append(f"sekil olcumu karari verdi: {s.sekil.value}")

    elif guven < MAGIKA_GUVEN_ESIGI and s.yargi_gerekir:
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = (f"format guveni dusuk ({guven:.2f}) ve "
                          "sekil de belirsiz")
        return k

    if s.yargi_gerekir:
        # Buraya s.yargi_gerekir=True ile ulasildiysa guven zaten >= ESIGI
        # demektir: dusuk guvenli her durum yukaridaki kisa-dosya/elif
        # dallarinda daha once yakalanip human feedback'e yonlendirilir. Yani format
        # bir akisa isaret ediyorsa (belirleyici), bu isaret guvenli demektir;
        # hicbir sey bilmemekten iyidir.
        if belirleyici:
            k.akis = Flow(belirleyici)
            k.notlar.append(
                f"sekil belirsiz; formata ({format_}, {guven:.2f}) gore "
                "yonlendirildi, dogrulama onerilir"
            )
            return k
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = "sekil belirsiz, sinyaller celisiyor veya zayif"
        return k

    # --- sekil -> akis ------------------------------------------------
    k.akis = {
        TextShape.TABLO: Flow.TABLO,
        TextShape.SABIT_GENISLIK: Flow.TABLO,
        TextShape.LOG: Flow.BELGE,
        TextShape.ANAHTAR_DEGER: Flow.AGAC,
        TextShape.SERBEST_METIN: Flow.BELGE,
    }[s.sekil]

    if belirleyici:
        k.akis = Flow(belirleyici)
        k.notlar.append(f"format ({format_}) sekil olcumunun onune gecti")

    if s.sekil is TextShape.SABIT_GENISLIK:
        k.notlar.append("sabit genislikli tablo, sutun sinirlari cikarilmali")
    if s.sekil is TextShape.LOG:
        k.notlar.append("log kaydi, satir bazli ayristirma uygun")
    if k.akis is Flow.BELGE:
        k.notlar.append("serbest metin ajan baglamina girecek, "
                        "veri olarak etiketlenmeli")

    return k


def _route_pdf(k: Decision) -> Decision:
    """PDF'te metin katmani var mi OLC, yoksa taranmis gibi davran.

    Onceki surum butun PDF'leri "belge" sayiyordu. Taranmis bir fatura
    da PDF'tir ama icinde metin YOKTUR; belge akisina yollanirsa metin
    cikarici bos doner ve bu sessizce olur. Olcum ucuz: sayfa basina
    dusen karakter sayisi.
    """
    from .formats import pdf as _pdf

    try:
        from pypdf import PdfReader

        okuyucu = PdfReader(k.yol)
        sayfa_sayisi = len(okuyucu.pages)
        if sayfa_sayisi == 0:
            k.deterministik = False
            k.akis = Flow.YARGI
            k.yargi_sebebi = "PDF'te sayfa yok"
            return k
        parcalar = []
        for s in okuyucu.pages:
            try:
                parcalar.append(s.extract_text() or "")
            except Exception:  # noqa: BLE001 - bozuk tek sayfa, digerlerine devam
                parcalar.append("")
        karakter = len("\n".join(parcalar))
    except Exception as e:  # noqa: BLE001 - sebebi cagirana gosterilecek
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = f"PDF okunamadi: {type(e).__name__}: {e}"
        return k

    ortalama = karakter / sayfa_sayisi
    k.kanitlar.append((
        "PDF metin katmani",
        f"{sayfa_sayisi} sayfa, sayfa basina {ortalama:.0f} karakter",
    ))

    if ortalama >= _pdf.SAYFA_BASI_AZAMI_BOS:
        k.akis = Flow.BELGE
        k.sekil = "serbest_metin"
        k.notlar.append("metin katmani var, belge cozumleyiciye gider")
        return k

    # Metin yok: bu pratikte bir goruntudur. Sayfaya gomulu goruntuyu
    # cikarip OCR yolundan gecir ki Turkce siniri ayni sekilde isaretlensin.
    k.notlar.append("metin katmani yok (taranmis); OCR yoluna alindi")

    goruntuler = _pdf.embedded_images(okuyucu)
    if not goruntuler:
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = ("PDF'te ne metin katmani ne cikarilabilir goruntu var; "
                          "bu haliyle okunamiyor, human feedback gerekir")
        return k

    import tempfile

    with tempfile.TemporaryDirectory() as gecici:
        gyol = Path(gecici) / "sayfa1.png"
        gyol.write_bytes(goruntuler[0])
        return _route_image(k, 1.0, ocr_yolu=gyol)


def _route_workbook(k: Decision) -> Decision:
    """XLSX/XLSM: sayfalari acip yapiyi olc, akisi sayfalardan uret.

    Onceki surum XLSX'i zip diye "kapsayici" sayiyordu; teknik olarak
    dogru ama isleyis olarak yanlisti — icerigi hic acilmiyordu. Sayfalar
    ACILABILDIGI icin bu bir olgu sorusudur, tahmin degil.
    """
    from .formats import tabular

    sonuc = tabular.inspect_structure(Path(k.yol))
    if sonuc.hata:
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = f"calisma kitabi acilamadi: {sonuc.hata}"
        return k

    tablolar = sonuc.tablo_sayfalari
    k.kanitlar.append((
        "calisma kitabi",
        f"{len(sonuc.sayfalar)} sayfa acildi, {len(tablolar)} tanesi tablo",
    ))

    if not tablolar:
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = ("sayfalar acildi ama hicbirinde basliklanmis tablo "
                          "bulunamadi; hangi aralik okunacak human feedback ister")
        return k

    k.sekil = "tablo"
    k.akis = Flow.TABLO
    if len(tablolar) > 1:
        k.notlar.append(
            f"{len(tablolar)} tablo sayfasi var; hangisiyle calisilacagi "
            "TERCIH sorusudur, secenekler sunulur"
        )
    k.notlar.append("sayfalar: " + ", ".join(
        f"{s.ad}({s.satir}x{s.sutun})" for s in tablolar[:5]))
    return k


def _route_columnar_table(k: Decision) -> Decision:
    """Parquet: PAR1 imzasini dogrula, tablo akisina ver.

    Magika'nin etiketi bir IDDIA'dir; dosyanin gercekten parquet oldugunu
    soyleyen sey bas ve sondaki imzadir. Imza okunabildigi icin bu bir
    olgu sorusudur -- zip ailesinde `is_zipfile` ile yapilanin aynisi --
    ve model guvenine bakilmadan karar verilir. Imza yoksa etikete
    guvenip tablo demeyiz; karar human feedback'e kalir.
    """
    yol = Path(k.yol)
    if k.boyut < 2 * len(PARQUET_IMZA):
        bas = son = b""
    else:
        with yol.open("rb") as dosya:
            bas = dosya.read(len(PARQUET_IMZA))
            dosya.seek(-len(PARQUET_IMZA), 2)
            son = dosya.read(len(PARQUET_IMZA))

    if bas != PARQUET_IMZA or son != PARQUET_IMZA:
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = ("format 'parquet' etiketlendi ama PAR1 imzasi "
                          "dogrulanamadi; human feedback gerekir")
        return k

    k.format_guveni = 1.0
    k.sekil = "tablo"
    k.akis = Flow.TABLO
    k.kanitlar.append(("parquet imzasi", "bas ve sonda PAR1 dogrulandi"))
    k.notlar.append("sutunlu ikili tablo; semayi cozumleyici okur")
    return k


def _route_image(k: Decision, guven: float,
                    ocr_yolu: Path | None = None) -> Decision:
    """Goruntuyu OCR ile oku; tablo yapisini kur, Turkce sinirini isaretle.

    Kritik ayrim: SAYILAR ve YAPI olculur, TURKCE METIN olculemez.
    Model sozlugunde Turkce harf yoksa metin sessizce bozulur ve bu
    guven skoruyla yakalanamaz (olcum: 'Igdir' -> 'ngoa', guven 0.828).
    O yuzden yapiyi rapor edip metin dogrulamasi icin human feedback
    isteriz; uydurulmus metni akisa sokmayiz.

    `ocr_yolu`: OCR'a verilecek gercek goruntu. Taranmis PDF'te dosyanin
    kendisi okunamaz (RapidOCR PDF ayristirmaz); cagiran once sayfaya
    gomulu goruntuyu cikarip onun yolunu verir.
    """
    from .formats import image as _image

    try:
        s = _image.read(ocr_yolu or Path(k.yol))
    except ImportError as e:
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = f"OCR bileseni kurulu degil ({e}); human feedback gerekir"
        return k

    if s.hata:
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = f"goruntuden metin cikarilamadi: {s.hata}"
        return k

    tablo_mu = s.sutun_sayisi >= 2 and len(s.satirlar) >= 2
    k.sekil = "tablo" if tablo_mu else "serbest_metin"
    k.kanitlar.append(("OCR", f"{len(s.hucreler)} hucre, {len(s.satirlar)} satir, "
                              f"{s.sutun_sayisi} sutun"))

    if not s.turkce_dogrulanmis:
        k.deterministik = False
        k.akis = Flow.YARGI
        k.yargi_sebebi = (
            "OCR yapiyi ve sayilari okudu ancak tanima modelinin sozlugunde "
            "Turkce harfler yok; metin sessizce bozulmus olabilir, "
            "human feedback ile dogrulanmali"
        )
        k.kanitlar.append(("Turkce sozluk olcumu",
                           _image.model_supports_turkish()[1]))
        return k

    k.akis = Flow.TABLO if tablo_mu else Flow.BELGE
    k.notlar.append("OCR ile okundu, Turkce sozluk dogrulandi")
    return k


def _route_safely(yol: Path) -> Decision:
    """yonlendir() sarmalayicisi: okuma hatasi butun taramayi cokertmez.

    Bir izin hatasi, kopuk sembolik baglanti ya da tarama sirasinda
    silinen dosya, TEK dosyayi degil TUM klasoru cokertmemeli. Boyle bir
    hata da bir "karar verilemedi" durumudur; human feedback istenir.
    """
    try:
        return route(yol)
    except OSError as hata:
        try:
            boyut = yol.stat().st_size
        except OSError:
            boyut = -1
        return Decision(
            yol=str(yol), boyut=boyut, format="bilinmiyor", format_guveni=0.0,
            sekil="-", akis=Flow.YARGI, deterministik=False,
            yargi_sebebi=f"dosya okunamadi: {type(hata).__name__}: {hata}",
        )


def inventory(kok: Path, desen: str = "*") -> dict:
    """Bir klasordeki butun dosyalari ucuz gecisten gecirip ozet cikar.

    Pahali islem yapilmaz. Amac, kullaniciya SECENEK sunabilmek icin
    once neyin geldigini bilmek.
    """
    kararlar = [_route_safely(p) for p in sorted(kok.rglob(desen)) if p.is_file()]
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


def build_options(env: dict) -> dict:
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


Akis = Flow
Karar = Decision
yonlendir = route
envanter = inventory
secenek_uret = build_options
