"""Shared models for every file detector and reader.

Each format reports measurements, evidence, and explicit options with their
tradeoffs. The server never chooses an option: deterministic evidence may
veto a route, while preference remains with the caller.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class Severity(StrEnum):
    """Urgency of a finding; serialized values remain backward compatible."""

    INFO = "bilgi"
    WARNING = "dikkat"
    CRITICAL = "kritik"

    BILGI = INFO
    DIKKAT = WARNING
    KRITIK = CRITICAL


class Evidence(BaseModel):
    """Bir olcum sonucu. Yorum degil, sayi veya gozlem."""

    olcum: str = Field(description="Neyin olculdugu")
    deger: str = Field(description="Olculen deger")


class Option(BaseModel):
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


class Finding(BaseModel):
    """Dosyada tespit edilen tek bir konu."""

    baslik: str
    onem: Severity
    aciklama: str = Field(description="Sorunun ne oldugu, tek cumle")
    kanitlar: list[Evidence] = Field(default_factory=list)
    secenekler: list[Option] = Field(default_factory=list)


class Report(BaseModel):
    """Bir dosyanin tam incelemesi."""

    yol: str
    format: str = Field(description="csv, txt, json, xlsx, pdf, bilinmiyor")
    format_guveni: str = Field(description="kesin / yuksek / dusuk")
    boyut_bayt: int
    yapi: dict[str, str] = Field(
        default_factory=dict,
        description="Formata ozel yapisal ozet: satir/sutun/sayfa sayisi vb.",
    )
    bulgular: list[Finding] = Field(default_factory=list)
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


def summary_line(rapor: Report) -> str:
    """Insan icin tek satirlik ozet."""
    kritik = sum(1 for b in rapor.bulgular if b.onem is Severity.KRITIK)
    dikkat = sum(1 for b in rapor.bulgular if b.onem is Severity.DIKKAT)
    if kritik:
        return f"{rapor.format}: {kritik} kritik, {dikkat} dikkat gerektiren bulgu"
    if dikkat:
        return f"{rapor.format}: {dikkat} karar bekleyen bulgu"
    return f"{rapor.format}: sorun tespit edilmedi"


# Compatibility names are removed after repository importers move to the
# English surface. Keeping them here makes this first rename behavior-neutral.
Onem = Severity
Kanit = Evidence
Secenek = Option
Bulgu = Finding
Rapor = Report
ozet_satiri = summary_line
