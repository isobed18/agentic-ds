"""
KESIF — MCP istemci kaniti

Bu betik sunucuyu AYRI BIR SUREC olarak baslatir ve ona gercek MCP
protokolu uzerinden baglanir. Yani "fonksiyon cagirdim calisti" degil,
"protokol konustuk" kaniti.

Calistir:
    KESIF_KOK=$PWD .venv/bin/python scripts/kesif_istemci.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

ROOT = Path(__file__).resolve().parent.parent


def basli(n: str) -> None:
    print(f"\n{'=' * 72}\n{n}\n{'=' * 72}")


def icerik(sonuc) -> object:
    """Tool sonucundan yapisal ciktiyi cikar."""
    if getattr(sonuc, "structured_content", None):
        return sonuc.structured_content
    parcalar = []
    for p in sonuc.content:
        if getattr(p, "text", None):
            try:
                parcalar.append(json.loads(p.text))
            except json.JSONDecodeError:
                parcalar.append(p.text)
    return parcalar[0] if len(parcalar) == 1 else parcalar


async def main() -> None:
    ortam = dict(os.environ, KESIF_KOK=str(ROOT))
    parametre = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ads.file_detection.server"],
        env=ortam,
        cwd=str(ROOT),
    )

    async with stdio_client(parametre) as (oku_akis, yaz_akis):
        async with ClientSession(oku_akis, yaz_akis) as oturum:
            bilgi = await oturum.initialize()

            basli("1. BAGLANTI")
            print(f"  sunucu   : {bilgi.server_info.name} v{bilgi.server_info.version}")
            print(f"  protokol : {bilgi.protocol_version}")

            basli("2. SUNUCU HANGI TOOL'LARI SUNUYOR")
            araclar = await oturum.list_tools()
            for t in araclar.tools:
                ilk = (t.description or "").split(".")[0]
                print(f"  - {t.name:20} {ilk}")

            basli("3. secenekler('tests/fixtures/kesif/tr_sayi.csv')")
            s = await oturum.call_tool(
                "secenekler", {"yol": "tests/fixtures/kesif/tr_sayi.csv"})
            veri = icerik(s)
            print(f"  ozet          : {veri['ozet']}")
            print(f"  karar sayisi  : {veri['karar_sayisi']}")
            for karar in veri["kararlar"]:
                print(f"\n  [{karar['onem'].upper()}] {karar['baslik']}")
                print(f"     {karar['neden']}")
                for k in karar["kanit"]:
                    print(f"       kanit . {k}")
                for sec in karar["secenekler"]:
                    im = "  <== onerilen" if sec["onerilen"] else ""
                    print(f"       {sec['kod']}) {sec['eylem']}{im}")
                    print(f"          + {sec['kazanc']}")
                    print(f"          - {sec['bedel']}")

            # --- secim: her karar icin onerilen secenegin parametreleri ---
            secimler: dict[str, str] = {}
            for karar in veri["kararlar"]:
                for sec in karar["secenekler"]:
                    if sec["onerilen"]:
                        secimler.update(sec["parametre"])

            basli("4. SECIM YAPILMADAN oku()  —  bugunku davranis")
            once = icerik(await oturum.call_tool(
                "oku", {"yol": "tests/fixtures/kesif/tr_sayi.csv", "secimler": {}}))
            print(f"  tipler          : {once['tipler']}")
            print(f"  sayisal sutunlar: {once['sayisal_sutunlar'] or 'YOK'}")
            print(f"  istatistik      : {once['sayisal_ozet'] or 'hesaplanamaz'}")

            basli(f"5. SECIM UYGULANARAK oku()  —  secimler={secimler}")
            sonra = icerik(await oturum.call_tool(
                "oku", {"yol": "tests/fixtures/kesif/tr_sayi.csv", "secimler": secimler}))
            print(f"  tipler          : {sonra['tipler']}")
            print(f"  sayisal sutunlar: {sonra['sayisal_sutunlar']}")
            for kolon, ozet in sonra["sayisal_ozet"].items():
                print(f"  {kolon:16}: {ozet}")

            basli("6. KODLAMA — ayni dosya, iki secenek")
            for enc in ("cp1252", "cp1254"):
                r = icerik(await oturum.call_tool(
                    "oku", {"yol": "tests/fixtures/kesif/tr_cp1254.csv",
                            "secimler": {"encoding": enc}}))
                sehirler = [d["sehir"] for d in r["onizleme"]]
                print(f"  {enc}: {sehirler}")

            basli("7. GUVENLIK — kok dizin disina cikma denemesi")
            kotu = await oturum.call_tool(
                "oku", {"yol": "../../../etc/passwd", "secimler": {}})
            print(f"  isError : {kotu.is_error}")
            metin = kotu.content[0].text if kotu.content else ""
            print(f"  cevap   : {metin.splitlines()[-1][:80]}")

            print(f"\n{'=' * 72}")
            print("Butun cagrilar gercek MCP protokolu uzerinden yapildi.")
            print("=" * 72)


if __name__ == "__main__":
    asyncio.run(main())
