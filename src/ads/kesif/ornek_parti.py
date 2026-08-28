"""
KESIF — olcum icin karma ornek parti

Eskalasyon oranini uretmek icin kullanilan karma parti (26 dosya),
once /tmp altinda geciciydi ve kayboldu; olculen sayi tekrarlanabilir
degildi. Bu modul o partiyi DETERMINISTIK olarak yeniden uretir.

Ikili dosyalar (zip, xlsx, pdf, parquet, bin) depoya konmaz, calisma aninda
uretilir: depo sismez, sayi her yerde ayni cikar.

Kullanim:
    from ads.kesif.ornek_parti import yaz
    yaz(Path("/tmp/karma"))
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

# --- metin dosyalari -------------------------------------------------------

_METIN: dict[str, str] = {
    "satis_2024.csv": (
        "tarih,urun,adet,tutar\n"
        "2024-01-05,klavye,3,450.00\n"
        "2024-01-06,fare,7,210.50\n"
        "2024-01-07,monitor,2,3400.00\n"
        "2024-01-08,kablo,12,180.75\n"
        "2024-01-09,docking,1,1250.00\n"
        "2024-01-10,mikrofon,4,890.25\n"
        "2024-01-11,kamera,2,1600.00\n"
        "2024-01-12,kulaklik,5,745.50\n"
        "2024-01-13,adaptor,9,320.00\n"
        "2024-01-14,canta,3,540.00\n"
    ),
    "tutarlar.csv": (
        "kalem;tutar;oran\n"
        "kira;12.500,00;%15\n"
        "elektrik;3.240,50;%4\n"
        "su;890,25;%1\n"
        "internet;1.100,00;%2\n"
        "personel;84.000,00;%62\n"
        "bakim;5.600,75;%7\n"
        "sigorta;4.200,00;%5\n"
        "diger;3.100,00;%4\n"
        "toplam;114.631,50;%100\n"
    ),
    "sehirler_tr.csv": (
        "sehir,plaka,bolge\n"
        "Istanbul,34,Marmara\n"
        "Ankara,06,Ic Anadolu\n"
        "Izmir,35,Ege\n"
        "Konya,42,Ic Anadolu\n"
        "Sanliurfa,63,Guneydogu\n"
        "Gaziantep,27,Guneydogu\n"
        "Diyarbakir,21,Guneydogu\n"
        "Trabzon,61,Karadeniz\n"
        "Antalya,07,Akdeniz\n"
    ),
    "mini.csv": "a,b,c\n1,2,3\n4,5,6\n7,8,9\n",
    # Magika'nin yuksek guvenle yanildigi vaka: kisa, tek sutunlu.
    "tek_sutun.csv": "sehir\nKonya\nAnkara\nIzmir\n",
    "bos.csv": "",
    "kayit.tsv": (
        "id\tad\tpuan\n"
        "1\tahmet\t85\n"
        "2\tmehmet\t92\n"
        "3\tayse\t78\n"
        "4\tfatma\t95\n"
        "5\tali\t67\n"
        "6\tzeynep\t88\n"
        "7\tmustafa\t74\n"
        "8\telif\t91\n"
    ),
    # Sabit genislikli tablo: Magika 'txt' der, sekil tespiti TABLO bulur.
    "hizali_rapor.txt": (
        "URUN          ADET     TUTAR\n"
        "klavye           3    450.00\n"
        "fare             7    210.50\n"
        "monitor          2   3400.00\n"
        "kablo           12    180.75\n"
        "docking          1   1250.00\n"
        "mikrofon         4    890.25\n"
        "kamera           2   1600.00\n"
        "kulaklik         5    745.50\n"
    ),
    "aylik_rapor.txt": (
        "Ocak ayinda toplam satis hacmi bir onceki aya gore yuzde on iki artti. "
        "Bu artisin baslica sebebi kurumsal musteri segmentindeki yenilenen "
        "sozlesmelerdir. Ikinci ceyrekte benzer bir egilim beklenmektedir.\n\n"
        "Lojistik tarafinda ise teslimat suresi ortalama iki gun kisaldi. "
        "Depo otomasyonu yatiriminin geri donusu ilk verilere gore olumlu "
        "gorunuyor. Onumuzdeki donemde ikinci depo icin fizibilite calismasi "
        "baslatilacaktir.\n"
    ),
    "notlar.txt": (
        "Toplanti notlari asagida yer almaktadir. Katilimcilar butce kalemlerini "
        "tek tek gozden gecirdi ve iki maddede revizyon talep etti. "
        "Revizyonlar gelecek hafta sunulacaktir.\n"
        "Ikinci gundem maddesi olan tedarikci degerlendirmesi ertelendi. "
        "Sebep olarak veri eksikligi gosterildi.\n"
    ),
    "tek_kelime.txt": "merhaba\n",
    "config.txt": (
        "host: localhost\n"
        "port: 5432\n"
        "user: admin\n"
        "timeout: 30\n"
        "retries: 3\n"
        "debug: false\n"
        "region: eu-central\n"
        "pool_size: 16\n"
    ),
    "ayar.conf": (
        "max_connections = 100\n"
        "shared_buffers = 256MB\n"
        "work_mem = 4MB\n"
        "maintenance_work_mem = 64MB\n"
        "effective_cache_size = 1GB\n"
        "wal_level = replica\n"
        "checkpoint_timeout = 5min\n"
        "random_page_cost = 1.1\n"
    ),
    "sunucu.log": (
        "2026-08-20 10:22:01 INFO  sunucu basladi port=8080\n"
        "2026-08-20 10:22:03 INFO  veritabani baglantisi kuruldu\n"
        "2026-08-20 10:23:14 WARN  yavas sorgu 1240ms\n"
        "2026-08-20 10:24:55 INFO  istek islendi id=4471\n"
        "2026-08-20 10:25:02 ERROR baglanti koptu retry=1\n"
        "2026-08-20 10:25:04 INFO  yeniden baglandi\n"
        "2026-08-20 10:26:31 INFO  istek islendi id=4472\n"
        "2026-08-20 10:27:00 INFO  saglik kontrolu tamam\n"
    ),
    "hata.log": (
        "2026-08-19 03:11:07 ERROR disk doluluk %94\n"
        "2026-08-19 03:12:00 ERROR yazma basarisiz path=/var/data\n"
        "2026-08-19 03:12:45 WARN  temizlik gorevi baslatildi\n"
        "2026-08-19 03:18:22 INFO  120GB serbest birakildi\n"
        "2026-08-19 03:19:01 INFO  yazma yeniden denendi basarili\n"
        "2026-08-19 04:02:13 WARN  bellek kullanimi %81\n"
        "2026-08-19 04:30:00 INFO  gunluk rotasyonu tamam\n"
    ),
    "veri.json": (
        '{"musteriler": [{"id": 1, "ad": "Ahmet", "sehir": "Konya"},'
        ' {"id": 2, "ad": "Ayse", "sehir": "Ankara"}],'
        ' "toplam": 2, "guncelleme": "2026-08-20"}'
    ),
    "kayitlar.jsonl": (
        '{"id": 1, "olay": "giris", "ts": "2026-08-20T10:00:00"}\n'
        '{"id": 2, "olay": "tiklama", "ts": "2026-08-20T10:00:12"}\n'
        '{"id": 3, "olay": "cikis", "ts": "2026-08-20T10:04:31"}\n'
        '{"id": 4, "olay": "giris", "ts": "2026-08-20T11:20:05"}\n'
    ),
    "yapilandirma.xml": (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<yapilandirma>\n"
        "  <sunucu host=\"localhost\" port=\"8080\"/>\n"
        "  <veritabani ad=\"ads\" surucu=\"postgres\"/>\n"
        "  <gunluk seviye=\"info\" dosya=\"/var/log/ads.log\"/>\n"
        "</yapilandirma>\n"
    ),
    "sayfa.html": (
        "<!doctype html>\n<html lang=\"tr\"><head><meta charset=\"utf-8\">\n"
        "<title>Rapor</title></head>\n<body>\n"
        "<h1>Aylik Rapor</h1>\n"
        "<p>Ocak ayi satis hacmi yuzde on iki artti.</p>\n"
        "<p>Lojistikte teslimat suresi kisaldi.</p>\n"
        "</body></html>\n"
    ),
    "belge.md": (
        "# Proje Notlari\n\n"
        "## Amac\n\n"
        "Dagitik veriden model uretmek.\n\n"
        "## Durum\n\n"
        "- Veri alimi tamam\n"
        "- Sema kesfi devam ediyor\n"
        "- Egitim bekliyor\n"
    ),
}


def _pdf_bayt(satirlar: list[str]) -> bytes:
    """Gecerli, kucuk bir PDF uretir (xref tablosu dogru hesaplanir)."""
    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    icerik = ["BT", "/F1 12 Tf", "72 720 Td"]
    for i, satir in enumerate(satirlar):
        icerik.append(f"({esc(satir)}) Tj" if i == 0
                      else f"0 -14 Td ({esc(satir)}) Tj")
    icerik.append("ET")
    govde = "\n".join(icerik).encode("latin-1")

    nesneler = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(govde) + govde + b"\nendstream",
    ]

    out = bytearray(b"%PDF-1.4\n")
    konumlar = [0]
    for i, nesne in enumerate(nesneler, start=1):
        konumlar.append(len(out))
        out += f"{i} 0 obj\n".encode() + nesne + b"\nendobj\n"
    xref = len(out)
    n = len(nesneler) + 1
    out += f"xref\n0 {n}\n".encode() + b"0000000000 65535 f \n"
    for k in konumlar[1:]:
        out += f"{k:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {n} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(out)


def _zip_bayt() -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("icerik/okuma.txt", "arsiv icindeki metin dosyasi\n")
        z.writestr("icerik/veri.csv", "a,b\n1,2\n3,4\n")
    return tampon.getvalue()


def _xlsx_bayt() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "satis"
    ws.append(["urun", "adet", "tutar"])
    for satir in (("klavye", 3, 450.0), ("fare", 7, 210.5), ("monitor", 2, 3400.0)):
        ws.append(list(satir))
    tampon = io.BytesIO()
    wb.save(tampon)
    return tampon.getvalue()


def _parquet_bayt() -> bytes:
    """Gercek bir parquet: imzasi da semasi da uydurma degil.

    Elle 'PAR1...PAR1' yazmak testi gecirirdi ama Magika'nin gercekten
    'parquet' etiketi urettigini olcmezdi; parti bu yuzden kutuphaneyle
    uretiliyor. pyarrow cekirdek bagimlilik, ekstra gerektirmez.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    tablo = pa.table({
        "urun": ["klavye", "fare", "monitor", "kablo"],
        "adet": [3, 7, 2, 12],
        "tutar": [450.0, 210.5, 3400.0, 180.75],
    })
    tampon = io.BytesIO()
    pq.write_table(tablo, tampon)
    return tampon.getvalue()


def yaz(kok: Path) -> Path:
    """Karma partiyi `kok` altina yazar ve dizini dondurur."""
    kok.mkdir(parents=True, exist_ok=True)

    for ad, icerik in _METIN.items():
        (kok / ad).write_text(icerik, encoding="utf-8")

    # Turkce Windows kodlamasi: cp1254 kaniti bu dosyada olculur.
    (kok / "tr_cp1254.csv").write_bytes(
        "sehir,nufus\nİstanbul,15840900\nŞanlıurfa,2143020\nÇorum,527000\n"
        .encode("cp1254")
    )

    (kok / "rapor.pdf").write_bytes(_pdf_bayt([
        "Aylik Faaliyet Raporu",
        "Ocak 2026 doneminde satis hacmi artti.",
        "Lojistik tarafinda teslimat suresi kisaldi.",
        "Ikinci depo icin fizibilite baslatilacak.",
    ]))
    (kok / "arsiv.zip").write_bytes(_zip_bayt())
    (kok / "tablo.xlsx").write_bytes(_xlsx_bayt())
    (kok / "olcumler.parquet").write_bytes(_parquet_bayt())
    (kok / "ikili.bin").write_bytes(bytes(range(256)) * 8)

    return kok
