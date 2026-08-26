"""
KESIF — cekirdek veri modeli

Bu dosyadaki yapilar butun format cozumleyicilerin ortak dilidir.
PDF de CSV de XLSX de ayni sekilde konusur: olctugunu soyler,
kanitini gosterir, secenekleri bedelleriyle sunar.

TEMEL KURAL: sunucu karar VERMEZ.
Olcer, kaniti gosterir, secenekleri siralar. Secim cagiran tarafta kalir.
Bu, pipeline'in kendi ilkesiyle ayni: deterministik katmanin vetosu vardir,
ajan olcum uretmez.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Onem(StrEnum):
    """Bir bulgunun ne kadar acil oldugu."""

    BILGI = "bilgi"      # farkinda ol, bir sey yapman gerekmiyor
    DIKKAT = "dikkat"    # karar vermen gerek, veri etkilenebilir
    KRITIK = "kritik"    # karar vermezsen veri KESIN yanlis okunur


class Kanit(BaseModel):
    """Bir olcum sonucu. Yorum degil, sayi veya gozlem."""

    olcum: str = Field(description="Neyin olculdugu")
    deger: str = Field(description="Olculen deger")


class Secenek(BaseModel):
    """Bir bulguya karsi yapilabilecek is. Bedeli birlikte yazilir."""

    kod: str = Field(description="Secenek harfi: A, B, C...")
    eylem: str = Field(description="Ne yapilacak")
    kazanc: str = Field(description="Bu secilirse ne duzelir")
    bedel: str = Field(description="Bu secilirse ne riske girer")
    onerilen: bool = Field(
        default=False,
        description="Kanitlara gore one cikan secenek. Emir degil, isaret.",
    )
    # Cagiran taraf oku() cagirirken bu parametreleri gonderir.
    parametre: dict[str, str] = Field(
        default_factory=dict,
        description="Bu secenek uygulanirsa okuyucuya gecilecek ayarlar",
    )


class Bulgu(BaseModel):
    """Dosyada tespit edilen tek bir konu."""

    baslik: str
    onem: Onem
    aciklama: str = Field(description="Sorunun ne oldugu, tek cumle")
    kanitlar: list[Kanit] = Field(default_factory=list)
    secenekler: list[Secenek] = Field(default_factory=list)


class Rapor(BaseModel):
    """Bir dosyanin tam incelemesi."""

    yol: str
    format: str = Field(description="csv, txt, json, xlsx, pdf, bilinmiyor")
    format_guveni: str = Field(description="kesin / yuksek / dusuk")
    boyut_bayt: int
    yapi: dict[str, str] = Field(
        default_factory=dict,
        description="Formata ozel yapisal ozet: satir/sutun/sayfa sayisi vb.",
    )
    bulgular: list[Bulgu] = Field(default_factory=list)
    okunabilir: bool = Field(
        default=True,
        description="False ise bu dosya bu haliyle pipeline'a giremez",
    )
    not_: str = Field(
        default="",
        alias="not",
        description="Okunamiyorsa veya kisitli okunuyorsa sebebi",
    )

    model_config = {"populate_by_name": True}


def ozet_satiri(rapor: Rapor) -> str:
    """Insan icin tek satirlik ozet."""
    kritik = sum(1 for b in rapor.bulgular if b.onem is Onem.KRITIK)
    dikkat = sum(1 for b in rapor.bulgular if b.onem is Onem.DIKKAT)
    if kritik:
        return f"{rapor.format}: {kritik} kritik, {dikkat} dikkat gerektiren bulgu"
    if dikkat:
        return f"{rapor.format}: {dikkat} karar bekleyen bulgu"
    return f"{rapor.format}: sorun tespit edilmedi"
