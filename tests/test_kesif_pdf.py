"""KESIF PDF cozumleyicisi: metin katmani var/yok ayrimi, human feedback'e dusme.

Test PDF'leri disaridan hazir dosya almiyor; `_basit_pdf()` elle gecerli,
kucuk bir PDF uretir (xref tablosu dogru hesaplanmis). Boylece reportlab
gibi ek bir bagimlilik gerekmeden gercek pypdf ayristirmasi test edilir.
"""

from __future__ import annotations

from pathlib import Path

from ads.kesif.formatlar import pdf as pdf_cozumleyici
from ads.kesif.model import Onem
from ads.kesif.okuyucu import oku_pdf


def _basit_pdf(satirlar: list[str]) -> bytes:
    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    content_lines = ["BT", "/F1 12 Tf", "72 720 Td"]
    for i, satir in enumerate(satirlar):
        if i == 0:
            content_lines.append(f"({esc(satir)}) Tj")
        else:
            content_lines.append(f"0 -14 Td ({esc(satir)}) Tj")
    content_lines.append("ET")
    content = "\n".join(content_lines).encode("latin-1")

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_offset = len(out)
    n = len(objects) + 1
    out += f"xref\n0 {n}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets[1:]:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode()
    return bytes(out)


def test_metin_katmanli_pdf_sekil_tespit_eder(tmp_path: Path) -> None:
    dosya = tmp_path / "rapor.pdf"
    dosya.write_bytes(_basit_pdf([
        "ad: Ahmet", "sehir: Konya", "tarih: 2026-08-24",
        "not: bu bir anahtar deger belgesi", "durum: tamam",
    ]))

    rapor = pdf_cozumleyici.incele(dosya)

    assert rapor.format == "pdf"
    assert rapor.okunabilir is True
    assert rapor.yapi["sayfa_sayisi"] == "1"
    assert int(rapor.yapi["cikarilan_karakter"]) > 0
    basliklar = [b.baslik for b in rapor.bulgular]
    assert any("Metin katmani bulundu" in b for b in basliklar)


def test_ne_metin_ne_goruntu_iceren_pdf_human_feedbacke_dusuyor(tmp_path: Path) -> None:
    """Okunacak hicbir sey yoksa bile otomatik ISLENEMEZ denmez, sorulur.

    Bu PDF'de ne metin katmani ne de gomulu goruntu var; OCR'a verilecek
    bir sey de yok. Dogru davranis sessizce elemek degil, sebebini yazip
    human feedback istemek.
    """
    dosya = tmp_path / "bos_sayfa.pdf"
    dosya.write_bytes(_basit_pdf([]))

    rapor = pdf_cozumleyici.incele(dosya)

    assert rapor.okunabilir is False
    # Metin katmaninin olmadigi BILGI olarak, okunamazlik KRITIK olarak raporlanir.
    basliklar = " ".join(b.baslik.lower() for b in rapor.bulgular)
    assert "metin katmani yok" in basliklar

    kritikler = [b for b in rapor.bulgular if b.onem is Onem.KRITIK]
    assert len(kritikler) == 1
    assert "gomulu goruntu" in kritikler[0].baslik.lower()
    secenek = kritikler[0].secenekler[0]
    assert secenek.parametre == {"mod": "human_feedback"}
    assert secenek.onerilen is True


def test_bozuk_pdf_hata_ile_doner_cokmez(tmp_path: Path) -> None:
    dosya = tmp_path / "bozuk.pdf"
    dosya.write_bytes(b"bu bir PDF degil, duz metin")

    rapor = pdf_cozumleyici.incele(dosya)

    assert rapor.okunabilir is False
    assert "acilamadi" in rapor.not_


def test_oku_pdf_onizleme_dondurur(tmp_path: Path) -> None:
    dosya = tmp_path / "onizle.pdf"
    dosya.write_bytes(_basit_pdf(["birinci satir", "ikinci satir"]))

    sonuc = oku_pdf(dosya, {})

    assert sonuc["basarili"] is True
    assert sonuc["sayfa_sayisi"] == 1
    assert "birinci satir" in sonuc["onizleme"]


def test_oku_pdf_bozuk_dosyada_hata_dondurur(tmp_path: Path) -> None:
    dosya = tmp_path / "bozuk.pdf"
    dosya.write_bytes(b"gecersiz")

    sonuc = oku_pdf(dosya, {})

    assert sonuc["basarili"] is False
    assert "hata" in sonuc
