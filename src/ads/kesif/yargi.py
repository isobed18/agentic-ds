"""
KESIF — yargi katmani

BU MODUL MCP SUNUCUSUNUN ICINDE DEGIL, DISINDADIR. Bilerek.

Sunucu deterministik kalir: olcer, kaniti verir, cozemezse "karar
verilemedi" der. Karari bu katman verir ve verdigi karar OLCUM DEGIL
YORUM olarak kaydedilir. Boylece:

  - deterministik katmanin vetosu korunur
  - hangi kararin olcumden, hangisinin yargidan geldigi ayirt edilir
  - eskalasyon orani olculebilir kalir

MALIYET DISIPLINI: bu katman yalnizca deterministik katmanin
cozemedigi dosyalar icin cagrilir. Olcumde 25 dosyanin 3'u buraya
dustu, yani %12.

KARAR: BULUT LLM YOK. Bu katmanin gorevi "hangi akis" sorusunu
cevaplamak. README ilke #6: "Olgu sorusu olculur, tercih sorusu
sorulur." Deterministik katmanin cozemedigi bir dosyanin akisi cogu
zaman gercekten bir TERCIH sorusudur (LLM'in tahmin etmesi degil,
kullanicinin "buna odaklan" demesi dogru cevaptir). Bu yuzden bir
modele tahmin ettirmek yerine dogrudan human feedback isteniyor —
once hesaplanmis kapali secenek listesiyle, ana pipeline'daki gates
katmaninin ayni deseni (build_human_prompt): acik uctu soru degil,
sonuclari onceden yazilmis kapali secim.

Bunun bir sonucu: kesif katmaninda artik bulut API bagimliligi yok.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .yonlendirici import Karar

ORNEK_AZAMI_KARAKTER = 600

GECERLI_AKISLAR = ["tablo", "belge", "agac", "kapsayici", "islenemez"]


@dataclass(frozen=True)
class AkisSecenegi:
    akis: str
    aciklama: str


SECENEKLER: list[AkisSecenegi] = [
    AkisSecenegi("tablo", "satir ve sutunu olan, tiplenebilir veri"),
    AkisSecenegi("belge", "serbest metin, log, yapilandirilmamis icerik"),
    AkisSecenegi("agac", "ic ice kayit yapisi (json, xml, anahtar-deger)"),
    AkisSecenegi("kapsayici", "icinde baska dosyalar var, acilmali"),
    AkisSecenegi("islenemez", "bu haliyle hicbir akisa giremez"),
]

# (soru metni, secenekler) -> (secilen akis, kisa gerekce)
Cevaplayici = Callable[[str, list[AkisSecenegi]], tuple[str, str]]


@dataclass
class InsanSonucu:
    yol: str
    akis: str
    gerekce: str = ""
    kaynak: str = "human_feedback"  # olcum degil, LLM yorumu da degil
    hata: str = ""


def onizleme_hazirla(karar: Karar) -> str:
    """Human feedback ekraninda GOSTERILEBILIR bir onizleme uret.

    Ham baytlari dokmek ikili dosyada (goruntu, PDF) ekrana coplu bir
    blok basiyordu; bu soruyu kolaylastirmak yerine ZORLASTIRIYOR. Iyi
    feedback ancak cevaplanabilir bir soruyla alinir.

    Sirayla: (1) OCR tablosu varsa o, (2) okunabilir metinse metin,
    (3) hicbiri degilse durust bir "gosterilemez" notu.
    """
    yol = Path(karar.yol)
    try:
        ham = yol.read_bytes()[:4096]
    except OSError as e:
        return f"(okunamadi: {e})"

    # 1) Goruntu / taranmis belge: cikarilan TABLOYU goster, baytlari degil.
    if any(ad == "OCR" for ad, _ in karar.kanitlar):
        satirlar = _ocr_onizleme(yol)
        if satirlar:
            return "OCR ile okunan icerik:\n" + "\n".join(
                "  " + " | ".join(r) for r in satirlar)
        return "(goruntu icerigi cikarilamadi)"

    # 2) Metin gibi gorunuyorsa metni goster.
    ornek = ham[:2048]
    yazdirilamayan = sum(1 for b in ornek if b < 9 or 13 < b < 32)
    if b"\x00" not in ornek and yazdirilamayan <= len(ornek) * 0.05:
        return ham.decode("utf-8", errors="replace")[:ORNEK_AZAMI_KARAKTER]

    # 3) Ikili icerik: cop basmak yerine ne oldugunu soyle.
    return (f"(ikili icerik, metin olarak gosterilemez — "
            f"ilk baytlar: {ham[:12].hex(' ')})")


def _ocr_onizleme(yol: Path, azami_satir: int = 6) -> list[list[str]]:
    try:
        from .formatlar import goruntu as _goruntu

        s = _goruntu.oku(yol)
        return s.satirlar[:azami_satir] if not s.hata else []
    except Exception:  # noqa: BLE001 - onizleme kritik degil, soru yine sorulur
        return []


def _soru_metni_hazirla(karar: Karar) -> str:
    yol = Path(karar.yol)
    onizleme = onizleme_hazirla(karar)

    kanit = "\n".join(f"  {ad}: {deger}" for ad, deger in karar.kanitlar)
    notlar = "\n".join(f"  {n}" for n in karar.notlar) or "  (yok)"

    return f"""DOSYA: {yol.name}
BOYUT: {karar.boyut} bayt
FORMAT TAHMINI: {karar.format} (guven {karar.format_guveni:.3f})
SEKIL OLCUMU: {karar.sekil}
YARGIYA CIKMA SEBEBI: {karar.yargi_sebebi}

OLCULEN KANITLAR:
{kanit or "  (yok)"}

NOTLAR:
{notlar}

ONIZLEME:
{onizleme}"""


def _cli_sor(soru: str, secenekler: list[AkisSecenegi]) -> tuple[str, str]:
    print(soru)
    print()
    for i, s in enumerate(secenekler, start=1):
        print(f"  {i}) {s.akis:12} {s.aciklama}")
    while True:
        secim = input("Secim (numara): ").strip()
        if secim.isdigit() and 1 <= int(secim) <= len(secenekler):
            akis = secenekler[int(secim) - 1].akis
            break
        print("Gecersiz secim, tekrar dene.")
    gerekce = input("Kisa gerekce (opsiyonel, Enter ile gec): ").strip()
    return akis, gerekce


def sor(karar: Karar, cevaplayici: Cevaplayici | None = None) -> InsanSonucu:
    """Deterministik katmanin cozemedigi tek dosya icin human feedback iste.

    `cevaplayici` verilmezse terminalden interaktif sorar (varsayilan:
    `_cli_sor`). Testlerde veya bir UI'dan cagrildiginda, soru metnini
    ve secenek listesini alip (akis, gerekce) donduren herhangi bir
    fonksiyon gecirilebilir — stdin'e bagli kalinmaz.
    """
    if karar.deterministik:
        raise ValueError("bu dosya deterministik cozuldu, human feedback istenmez")

    cevaplayici = cevaplayici or _cli_sor
    soru = _soru_metni_hazirla(karar)

    try:
        akis, gerekce = cevaplayici(soru, SECENEKLER)
    except (EOFError, KeyboardInterrupt):
        return InsanSonucu(
            yol=karar.yol, akis="", hata="human feedback alinamadi (iptal/EOF)"
        )

    if akis not in GECERLI_AKISLAR:
        return InsanSonucu(
            yol=karar.yol, akis="", gerekce=gerekce, hata=f"gecersiz akis: {akis!r}"
        )

    return InsanSonucu(yol=karar.yol, akis=akis, gerekce=gerekce)


# ---------------------------------------------------------------------------
# OTOMATIK MOD — gozetimsiz kosumlar icin deterministik yedek
# ---------------------------------------------------------------------------
#
# Human feedback dogru varsayilandir ama basinda kimsenin olmadigi bir
# toplu kosumda dosyanin suresiz beklemesi de bir arizadir. Otomatik mod
# bunu cozer ve UC KURALA uyar:
#
#   1. ACIK TERCIH: kendiliginden devreye girmez, cagiran secer.
#   2. DETERMINISTIK: bir modele sorulmaz; olculmus kanitlardan sabit bir
#      oncelik zinciriyle secilir. Ayni girdi her zaman ayni cikti.
#   3. GIZLENMEZ: sonuc `kaynak="otomatik_varsayilan"` olarak kaydedilir,
#      hangi kuralin tetiklendigi yazilir. Olcumden de human feedback'ten
#      de ayirt edilebilir kalir.
#
# Yani "tahmin yok" ilkesi bozulmaz: tahmin yapiliyorsa oldugu gibi
# etiketlenir ve sonradan denetlenebilir.

SEKIL_AKIS: dict[str, str] = {
    "tablo": "tablo",
    "sabit_genislik": "tablo",
    "log": "belge",
    "serbest_metin": "belge",
    "anahtar_deger": "agac",
}

# Sekil olculemediginde formatin kendisi ne soyluyor.
FORMAT_AKIS: dict[str, str] = {
    "csv": "tablo", "tsv": "tablo", "ignorefile": "tablo",
    "json": "agac", "jsonl": "agac", "xml": "agac",
    "yaml": "agac", "toml": "agac", "ini": "agac",
    "markdown": "belge", "html": "belge", "txt": "belge",
    "pdf": "belge", "docx": "belge",
    "zip": "kapsayici", "tar": "kapsayici", "xlsx": "tablo",
}


def en_makul_akis(karar: Karar) -> tuple[str, str]:
    """Olculmus kanitlardan en makul akisi SABIT bir zincirle sec.

    Doner: (akis, hangi kuralin tetiklendigi). Model cagrilmaz.
    """
    if karar.sekil in SEKIL_AKIS:
        return SEKIL_AKIS[karar.sekil], f"sekil olcumu '{karar.sekil}'"

    if karar.format in FORMAT_AKIS:
        return FORMAT_AKIS[karar.format], f"format '{karar.format}'"

    # Hicbir sey bilinmiyorsa: veriyi ATMA. Metin olarak islenebilen her
    # sey belge akisina girebilir; "islenemez" demek veri kaybidir ve
    # geri alinamaz. Yalnizca gercekten okunamayan icerik islenemez olur.
    if "ikili" in karar.yargi_sebebi or "yazdirilamayan" in karar.yargi_sebebi:
        return "islenemez", "ikili icerik kaniti"
    return "belge", "varsayilan: veri atilmaz, belge akisina alinir"


def otomatik_coz(karar: Karar) -> InsanSonucu:
    """Insan yokken deterministik yedek. Sonuc acikca etiketlenir."""
    if karar.deterministik:
        raise ValueError("bu dosya deterministik cozuldu, yedek gerekmez")

    akis, kural = en_makul_akis(karar)
    return InsanSonucu(
        yol=karar.yol,
        akis=akis,
        gerekce=f"otomatik secim ({kural}); insan onayi alinmadi",
        kaynak="otomatik_varsayilan",
    )


@dataclass
class ArtikRapor:
    toplam: int
    deterministik: int
    human_feedbacke_cikan: int
    eskalasyon_orani: float
    yanitlar: list[InsanSonucu] = field(default_factory=list)

    @property
    def otomatik_cozulen(self) -> int:
        return sum(1 for y in self.yanitlar if y.kaynak == "otomatik_varsayilan")


def artigi_coz(envanter_sonucu: dict, cevaplayici: Cevaplayici | None = None,
               otomatik: bool = False) -> ArtikRapor:
    """Envanterdeki SADECE human feedback gerektiren dosyalari coz.

    `otomatik=True` verilirse human feedback beklenmez; deterministik yedek
    kullanilir ve her sonuc `kaynak="otomatik_varsayilan"` olarak
    etiketlenir. Gozetimsiz toplu kosumlar icindir.
    """
    kararlar = envanter_sonucu["kararlar"]
    artik = [k for k in kararlar if not k.deterministik]

    r = ArtikRapor(
        toplam=len(kararlar),
        deterministik=len(kararlar) - len(artik),
        human_feedbacke_cikan=len(artik),
        eskalasyon_orani=round(len(artik) / len(kararlar), 3) if kararlar else 0.0,
    )
    for k in artik:
        r.yanitlar.append(otomatik_coz(k) if otomatik else sor(k, cevaplayici))
    return r
