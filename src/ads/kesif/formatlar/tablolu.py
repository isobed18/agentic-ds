"""
KESIF — XLSX / XLSM cozumleyici

XLSX teknik olarak bir zip arsividir; onceki surum bunu "kapsayici"
sayip icerigine hic bakmiyordu. Ama bir XLSX kapsayici DEGIL, birden
cok SAYFA iceren bir tablo dosyasidir. Bu modul sayfalari acar, her
sayfanin gercekten tablo olup olmadigini olcer ve akis kararini
sayfalarin kendisinden uretir.

TASARIM: hucre DEGERLERI degil, YAPI olculur. Sayfa sayisi, kullanilan
aralik, baslik satiri, bos oran. Deger okuma `oku()` cagrisinda ve
yalnizca kucuk bir onizleme olarak yapilir.

BIRDEN COK SAYFA BIR TERCIH SORUSUDUR: hangi sayfayla calisilacagi
olcumden cikmaz. Tek sayfa varsa karar nettir; birden fazlaysa secenek
sunulur.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from ..model import Bulgu, Kanit, Onem, Rapor, Secenek

# Bir sayfanin "tablo" sayilmasi icin en az bu kadar satir/sutun.
ASGARI_SATIR = 2
ASGARI_SUTUN = 2
# Basliklarin bu orandan fazlasi bossa baslik satiri guvenilmez.
BASLIK_DOLULUK_ESIGI = 0.5
# Baslik her zaman ilk satirda degildir: kurumsal disa aktarimlar tablonun
# ustune rapor basligi ve bos satir koyar. Yalnizca 1. satira bakmak boyle
# bir sayfayi "tablo degil" saydiriyordu. `ads.intake._infer_header_row`
# ayni pencereyi tariyor; sayi bilerek onunla ayni tutuldu.
BASLIK_TARAMA_SATIRI = 20


@dataclass
class Sayfa:
    ad: str
    satir: int
    sutun: int
    basliklar: list[str] = field(default_factory=list)
    tablo_mu: bool = False
    bos_mu: bool = False
    # Basligin bulundugu satir (1 tabanli). 1'den buyukse ustunde baslik
    # ya da bos satir vardir; kanit metninde bunu soylemek gerekiyor.
    baslik_satiri: int = 1


@dataclass
class KitapSonuc:
    sayfalar: list[Sayfa] = field(default_factory=list)
    hata: str = ""

    @property
    def tablo_sayfalari(self) -> list[Sayfa]:
        return [s for s in self.sayfalar if s.tablo_mu]


def _oku_kitap(yol: Path, sadece_yapi: bool = True):
    # read_only: buyuk dosyalari belleğe almadan gezmek icin.
    # data_only: formul metni degil son hesaplanan deger.
    return load_workbook(yol, read_only=sadece_yapi, data_only=True)


def _baslik_ara(ws, s: Sayfa, satir: int, sutun: int) -> None:
    """Ilk dolu baslik satirini bul; yoksa 1. satiri kanit olarak birak.

    Onceki surum basligi YALNIZCA 1. satirdan okuyordu. Ustunde rapor
    basligi olan bir sayfa boylece doluluk esigini gecemiyor ve "tablo
    degil" sayiliyordu -- oysa `ads.intake` ayni sayfayi
    `header_row_inferred` ile sorunsuz yukluyor. Butun sayfalari boyle
    olan bir kitap da yonlendiricide human feedback'e dusuyordu.
    """
    ilk_satir: list[str] = []
    tarama = min(BASLIK_TARAMA_SATIRI, satir)

    for i, ham in enumerate(
        ws.iter_rows(min_row=1, max_row=tarama, values_only=True), start=1
    ):
        hucreler = [("" if h is None else str(h)) for h in ham]
        if not hucreler:
            continue
        if not ilk_satir:
            ilk_satir = hucreler
        dolu = sum(1 for h in hucreler if h.strip())
        if dolu / len(hucreler) < BASLIK_DOLULUK_ESIGI:
            continue
        # Basligin altinda en az bir veri satiri kalmali; aksi halde bu
        # bir tablo degil, sayfanin son satiridir.
        if satir - i + 1 < ASGARI_SATIR or sutun < ASGARI_SUTUN:
            continue
        s.basliklar = hucreler
        s.baslik_satiri = i
        s.tablo_mu = True
        return

    # Hicbir satir esigi gecmedi: kanit yine de 1. satir olsun ki rapor
    # "ne gordugumuzu" gosterebilsin.
    s.basliklar = ilk_satir
    s.tablo_mu = False


def incele_yapi(yol: Path) -> KitapSonuc:
    """Sayfalari ac, her birinin yapisini olc. Hucre degeri okunmaz."""
    sonuc = KitapSonuc()
    # XLSX bir zip oldugu icin bozuk dosya `BadZipFile` firlatir; bu
    # openpyxl'in kendi hata ailesinde degildir ve yakalanmazsa TEK bozuk
    # dosya butun taramayi cokertir.
    try:
        kitap = _oku_kitap(yol)
    except (InvalidFileException, zipfile.BadZipFile,
            OSError, ValueError, KeyError) as e:
        sonuc.hata = f"{type(e).__name__}: {e}"
        return sonuc

    try:
        for ws in kitap.worksheets:
            satir = int(ws.max_row or 0)
            sutun = int(ws.max_column or 0)
            s = Sayfa(ad=str(ws.title), satir=satir, sutun=sutun)

            if satir == 0 or sutun == 0:
                s.bos_mu = True
                sonuc.sayfalar.append(s)
                continue

            _baslik_ara(ws, s, satir, sutun)
            sonuc.sayfalar.append(s)
    finally:
        kitap.close()

    return sonuc


def incele(yol: Path) -> Rapor:
    rapor = Rapor(yol=str(yol), format=yol.suffix.lstrip(".").lower() or "xlsx",
                  format_guveni="yuksek", boyut_bayt=yol.stat().st_size)

    sonuc = incele_yapi(yol)
    if sonuc.hata:
        rapor.okunabilir = False
        rapor.not_ = f"Calisma kitabi acilamadi: {sonuc.hata}"
        return rapor

    if not sonuc.sayfalar:
        rapor.okunabilir = False
        rapor.not_ = "Calisma kitabinda sayfa yok."
        return rapor

    tablolar = sonuc.tablo_sayfalari
    rapor.yapi = {
        "sayfa_sayisi": str(len(sonuc.sayfalar)),
        "tablo_sayfasi": str(len(tablolar)),
        "sayfalar": ", ".join(
            f"{s.ad}({s.satir}x{s.sutun})" for s in sonuc.sayfalar[:8]
        ),
    }

    rapor.bulgular.append(Bulgu(
        baslik=f"{len(sonuc.sayfalar)} sayfa acildi, {len(tablolar)} tanesi tablo",
        onem=Onem.BILGI,
        aciklama="XLSX bir kapsayici degil, sayfalardan olusan bir tablo dosyasi; "
                 "sayfalar acilip yapilari olculdu.",
        kanitlar=[
            Kanit(olcum=s.ad,
                  deger=(f"{s.satir} satir x {s.sutun} sutun"
                         + (f" | baslik: {', '.join(s.basliklar[:5])}"
                            if s.basliklar else "")
                         # Baslik 1. satirda degilse bunu soylemek gerekiyor:
                         # okuyan kisi ustteki satirlarin atlandigini gormeli.
                         + (f" | baslik {s.baslik_satiri}. satirda"
                            if s.tablo_mu and s.baslik_satiri > 1 else "")
                         + ("" if s.tablo_mu else "  (tablo degil)")))
            for s in sonuc.sayfalar[:8]
        ],
    ))

    bos = [s for s in sonuc.sayfalar if s.bos_mu]
    if bos:
        rapor.bulgular.append(Bulgu(
            baslik=f"{len(bos)} bos sayfa",
            onem=Onem.BILGI,
            aciklama="Bos sayfalar analize girmez.",
            kanitlar=[Kanit(olcum="bos sayfalar",
                            deger=", ".join(s.ad for s in bos[:6]))],
        ))

    if not tablolar:
        rapor.okunabilir = False
        rapor.bulgular.append(Bulgu(
            baslik="Hicbir sayfa tablo yapisinda degil",
            onem=Onem.KRITIK,
            aciklama="Sayfalar acildi ama hicbirinde basliklanmis bir tablo "
                     "bulunamadi; hangi sayfanin nasil okunacagi olcumle "
                     "cikmiyor.",
            secenekler=[
                Secenek(kod="A", eylem="Human feedback: sayfayi ve araligi bildir",
                        kazanc="Dogru aralik okunur, uydurma yapilmaz.",
                        bedel="Otomatik akis durur.",
                        onerilen=True, parametre={"mod": "human_feedback"}),
            ],
        ))
        return rapor

    # Birden fazla tablo sayfasi: hangisiyle calisilacagi TERCIH sorusudur.
    if len(tablolar) > 1:
        rapor.bulgular.append(_sayfa_secimi_bulgusu(tablolar))

    return rapor


def _sayfa_secimi_bulgusu(tablolar: list[Sayfa]) -> Bulgu:
    secenekler = [
        Secenek(
            kod=chr(ord("A") + i),
            eylem=f"'{s.ad}' sayfasiyla calis ({s.satir}x{s.sutun})",
            kazanc="Tek ve net bir tablo; sutun tipleri dogrudan cikarilir.",
            bedel="Diger sayfalar bu turda islenmez.",
            onerilen=(i == 0),
            parametre={"sayfa": s.ad},
        )
        for i, s in enumerate(tablolar[:5])
    ]
    return Bulgu(
        baslik=f"{len(tablolar)} tablo sayfasi var — hangisi kullanilacak",
        onem=Onem.DIKKAT,
        aciklama="Bu bir TERCIH sorusudur, olcumle cevaplanamaz: hangi "
                 "sayfayla ilgilenildigi hicbir olcumden cikmaz. En genis "
                 "sayfa onerilir ama secim cagiran tarafta kalir.",
        kanitlar=[Kanit(olcum=s.ad, deger=f"{s.satir} satir x {s.sutun} sutun")
                  for s in tablolar[:5]],
        secenekler=secenekler,
    )
