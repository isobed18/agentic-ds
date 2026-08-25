"""KESIF yonlendirici: hicbir dal sessizce ISLENEMEZ demez ya da cokmez.

Kural: sistem emin degilse insana sorar (deterministik=False, akis=YARGI).
Yalnizca gercekten olgu olan seyler (bos dosya, yuksek guvenli format +
sekil) otomatik karar verir. Magika'nin ciktisi burada sahtelenir; testler
gercek modelin o an ne dedigine degil, kodun o cikti karsisinda ne
yaptigina bakar.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ads.kesif import yonlendirici as y
from ads.kesif.yonlendirici import Akis, envanter, yonlendir


def _sahte_magika(monkeypatch: pytest.MonkeyPatch, etiket: str, guven: float) -> None:
    sonuc = SimpleNamespace(output=SimpleNamespace(label=etiket), score=guven)
    monkeypatch.setattr(y._magika, "identify_path", lambda yol: sonuc)


def test_taninmayan_format_insana_sorulur(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dosya = tmp_path / "gizemli.xyz"
    dosya.write_bytes(b"herhangi bir icerik")
    _sahte_magika(monkeypatch, "cozumleyicisi_olmayan_format", 0.99)

    k = yonlendir(dosya)

    assert k.deterministik is False
    assert k.akis is Akis.YARGI
    assert "cozumleyici tanimli degil" in k.yargi_sebebi


def test_ikili_supheli_icerik_insana_sorulur(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dosya = tmp_path / "supheli.txt"
    # yuksek oranda kontrol karakteri: ikili mi, UTF-16 metin mi belirsiz
    dosya.write_bytes(bytes([1, 2, 3, 4, 5, 6, 7]) * 50)
    _sahte_magika(monkeypatch, "txt", 0.99)

    k = yonlendir(dosya)

    assert k.deterministik is False
    assert k.akis is Akis.YARGI
    assert "ikili" in k.yargi_sebebi.lower() or "UTF-16" in k.yargi_sebebi


def test_acilamayan_dusuk_guvenli_kapsayici_human_feedbacke_dusuyor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zip gibi gorunen ama acilamayan bir dosya: etiket tahmindir, sorulur."""
    dosya = tmp_path / "belirsiz.zip"
    dosya.write_bytes(b"PK\x03\x04" + b"\x00" * 20)  # gecerli bir arsiv degil
    _sahte_magika(monkeypatch, "zip", 0.5)

    k = yonlendir(dosya)

    assert k.deterministik is False
    assert k.akis is Akis.YARGI
    assert "acilamadi" in k.yargi_sebebi


def test_acilabilen_arsiv_dusuk_guvende_bile_kapsayici_oluyor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ayristirilabilen sey olculur: acilan arsiv icin model guveni onemsiz."""
    import zipfile

    dosya = tmp_path / "gercek.zip"
    with zipfile.ZipFile(dosya, "w") as z:
        z.writestr("a.txt", "icerik")
    _sahte_magika(monkeypatch, "zip", 0.3)

    k = yonlendir(dosya)

    assert k.deterministik is True
    assert k.akis is Akis.KAPSAYICI
    assert any("zip olarak acildi" in d for _, d in k.kanitlar)


def test_yuksek_guvenli_kapsayici_otomatik_kalir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dosya = tmp_path / "guvenli.zip"
    dosya.write_bytes(b"PK\x03\x04" + b"\x00" * 20)
    _sahte_magika(monkeypatch, "zip", 0.97)

    k = yonlendir(dosya)

    assert k.deterministik is True
    assert k.akis is Akis.KAPSAYICI


def test_dusuk_guvenli_belge_insana_sorulur(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dosya = tmp_path / "belirsiz.pdf"
    dosya.write_bytes(b"%PDF-1.4 sahte icerik")
    _sahte_magika(monkeypatch, "pdf", 0.4)

    k = yonlendir(dosya)

    assert k.deterministik is False
    assert k.akis is Akis.YARGI
    assert "belge" in k.yargi_sebebi


def test_metin_katmanli_belge_otomatik_kalir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gercek metin iceren bir PDF belge akisinda kalir."""
    # 'tests.' onekli import yalnizca depo koku sys.path'te oldugunda
    # calisir; CI duz `pytest` kostugu icin orada cokerdi. tests/ bir
    # paket olmadigindan pytest bu dizini zaten sys.path'e ekler.
    from test_kesif_pdf import _basit_pdf

    dosya = tmp_path / "guvenli.pdf"
    dosya.write_bytes(_basit_pdf([
        "Bu belgede gercek bir metin katmani vardir.",
        "Ikinci satir da metin icerir ve secilebilir.",
    ]))
    _sahte_magika(monkeypatch, "pdf", 0.95)

    k = yonlendir(dosya)

    assert k.deterministik is True
    assert k.akis is Akis.BELGE
    assert any("PDF metin katmani" in a for a, _ in k.kanitlar)


def test_acilamayan_pdf_belge_sayilmiyor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Yuksek model guveni bile olsa, ACILAMAYAN bir PDF sessizce gecmemeli.

    Onceki surum butun yuksek guvenli PDF'leri 'belge' sayiyordu; icerik
    hic acilmadigi icin bozuk bir dosya da bos sonucla akisa giriyordu.
    """
    dosya = tmp_path / "bozuk.pdf"
    dosya.write_bytes(b"%PDF-1.4 bu gecerli bir PDF degil")
    _sahte_magika(monkeypatch, "pdf", 0.99)

    k = yonlendir(dosya)

    assert k.deterministik is False
    assert k.akis is Akis.YARGI


def test_bos_dosya_hala_otomatik_islenemez(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tek istisna: 0 bayt olgu, tahmin degil. Insana sorulmaz."""
    dosya = tmp_path / "bos.txt"
    dosya.write_bytes(b"")
    _sahte_magika(monkeypatch, "empty", 1.0)

    k = yonlendir(dosya)

    assert k.deterministik is True
    assert k.akis is Akis.ISLENEMEZ


def test_envanter_tek_bozuk_dosyada_cokmez(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    iyi1 = tmp_path / "iyi1.csv"
    iyi1.write_text("a,b\n1,2\n", encoding="utf-8")
    bozuk = tmp_path / "bozuk.csv"
    bozuk.write_text("a,b\n1,2\n", encoding="utf-8")
    iyi2 = tmp_path / "iyi2.csv"
    iyi2.write_text("a,b\n1,2\n", encoding="utf-8")

    gercek_read_bytes = Path.read_bytes

    def _kirik_okuma(self: Path, *a, **kw):
        if self.name == "bozuk.csv":
            raise PermissionError("izin reddedildi (simule)")
        return gercek_read_bytes(self, *a, **kw)

    monkeypatch.setattr(Path, "read_bytes", _kirik_okuma)

    env = envanter(tmp_path)

    assert env["dosya_sayisi"] == 3
    bozuk_karar = next(k for k in env["kararlar"] if k.yol.endswith("bozuk.csv"))
    assert bozuk_karar.deterministik is False
    assert bozuk_karar.akis is Akis.YARGI
    assert "okunamadi" in bozuk_karar.yargi_sebebi
    assert "PermissionError" in bozuk_karar.yargi_sebebi

    diger_kararlar = [k for k in env["kararlar"] if not k.yol.endswith("bozuk.csv")]
    assert len(diger_kararlar) == 2
    assert all(k.deterministik for k in diger_kararlar)
