"""
KESIF — uctan uca gosterim

Sunucuyu ayri surec olarak baslatir, gercek MCP protokolu uzerinden
butun akisi kosar:

    1. envanter        ucuz gecis, HERKESE
    2. secenekler      kullaniciya sunulacak 3 tercih
    3. yonlendir       tek dosya detayi, kanitlariyla
    4. artik           deterministik cozulemeyen dosyalar

Ornek parti verilmezse `ads.kesif.ornek_parti` ile gecici bir dizine
uretilir; harici bir klasore bagimli degildir.

Calistir:
    .venv/bin/python scripts/kesif_demo.py
    KESIF_KOK=/baska/klasor .venv/bin/python scripts/kesif_demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

PROJE = Path(__file__).resolve().parent.parent

_verilen = os.environ.get("KESIF_KOK")
if _verilen:
    KOK = Path(_verilen).resolve()
else:
    sys.path.insert(0, str(PROJE / "src"))
    from ads.kesif.ornek_parti import yaz

    KOK = yaz(Path(tempfile.mkdtemp(prefix="kesif_")) / "karma").resolve()


def basli(n: str) -> None:
    print(f"\n{'=' * 74}\n{n}\n{'=' * 74}")


def icerik(sonuc):
    if getattr(sonuc, "structured_content", None):
        return sonuc.structured_content
    for p in sonuc.content:
        if getattr(p, "text", None):
            try:
                return json.loads(p.text)
            except json.JSONDecodeError:
                return p.text
    return None


async def main() -> None:
    par = StdioServerParameters(
        command=sys.executable, args=["-m", "ads.kesif.sunucu"],
        env=dict(os.environ, KESIF_KOK=str(KOK)), cwd=str(PROJE))

    async with stdio_client(par) as (o, y):
        async with ClientSession(o, y) as oturum:
            bilgi = await oturum.initialize()
            basli("BAGLANTI")
            print(f"  {bilgi.server_info.name} v{bilgi.server_info.version}"
                  f"  |  protokol {bilgi.protocol_version}")
            araclar = await oturum.list_tools()
            print(f"  {len(araclar.tools)} tool: "
                  + ", ".join(t.name for t in araclar.tools))
            print(f"  kok dizin: {KOK}")

            basli("1. ENVANTER  —  ucuz gecis, butun dosyalara")
            e = icerik(await oturum.call_tool("envanter", {"klasor": "."}))
            print(f"  {e['dosya_sayisi']} dosya tarandi")
            print(f"  deterministik : {e['deterministik']}")
            print(f"  yargi         : {e['yargi_gerektiren']}")
            print(f"  ESKALASYON    : %{e['eskalasyon_orani']*100:.1f}")
            print()
            print(f"  {'dosya':22} {'format':11} {'sekil':16} {'akis'}")
            print("  " + "-"*62)
            for d in e["dosyalar"]:
                im = "  <<<" if not d["deterministik"] else ""
                print(f"  {d['ad']:22} {d['format']:11} {d['sekil']:16} "
                      f"{d['akis']}{im}")

            basli("2. SECENEKLER  —  tercih sorusu, olcumle cevaplanamaz")
            s = icerik(await oturum.call_tool("secenekler_toplu", {"klasor": "."}))
            print(f"  {s['ozet']}\n")
            for o_ in s["secenekler"]:
                print(f"  {o_['kod']}) {o_['eylem']}")
                print(f"       + {o_['kazanc']}")
                print(f"       - {o_['bedel']}")
            for n in s["ek_notlar"]:
                print(f"\n  not: {n}")

            basli("3. TEK DOSYA  —  Magika'nin yanildigi vaka")
            k = icerik(await oturum.call_tool(
                "yonlendir", {"yol": "tek_sutun.csv"}))
            print(f"  dosya         : {Path(k['dosya']).name}")
            print(f"  Magika        : {k['format']} (guven {k['format_guveni']})")
            print(f"  sekil olcumu  : {k['sekil']}")
            print(f"  AKIS          : {k['akis']}")
            print(f"  deterministik : {k['deterministik']}")
            print(f"  yargi sebebi  : {k['yargi_sebebi']}")
            for n in k["notlar"]:
                print(f"  not           : {n}")

            basli("4. SEKIL OLCUMUNUN KURTARDIGI VAKA")
            k2 = icerik(await oturum.call_tool(
                "yonlendir", {"yol": "hizali_rapor.txt"}))
            print(f"  Magika        : {k2['format']} (guven {k2['format_guveni']})")
            print(f"  sekil olcumu  : {k2['sekil']}")
            print(f"  AKIS          : {k2['akis']}")
            print("  -> Magika'ya birakilsaydi belge akisina giderdi,")
            print("     tablo oldugu kaybolurdu.")

            print(f"\n{'=' * 74}")
            print("Butun cagrilar gercek MCP protokolu uzerinden yapildi.")
            print("Sunucu deterministik kaldi; yargi katmani disaridadir.")
            print("=" * 74)


if __name__ == "__main__":
    asyncio.run(main())
