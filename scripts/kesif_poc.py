"""
KESIF — canli gosterim (PoC)

Tek komutla acilan, tarayicidan kullanilan bir gosterim. Amaci
mimariyi ANLATMAK degil GOSTERMEK: bir dosya birak, sistemin onu
nasil olctugunu, hangi akisa yolladigini, emin olamadiginda nasil
human feedback istedigini canli gor.

    .venv/bin/python scripts/kesif_poc.py
    -> http://127.0.0.1:8600

Ornek dosyalar acilista otomatik uretilir (ads.file_detection.sample_batch +
Turkce bir tablo ekran goruntusu), yani gosterim harici bir dosyaya
bagimli degildir.

SALT OKUNUR: hicbir uc nokta dosya degistirmez. Yuklenen dosyalar
gecici bir dizine yazilir ve surec bitince silinir.
"""

from __future__ import annotations

import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

PROJE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJE / "src"))

import uvicorn  # noqa: E402
from fastapi import FastAPI, UploadFile  # noqa: E402
from fastapi.responses import HTMLResponse, JSONResponse  # noqa: E402

from ads.file_detection.adjudication import (  # noqa: E402
    FLOW_OPTIONS,
    prepare_preview,
    resolve_automatically,
)
from ads.file_detection.router import route  # noqa: E402
from ads.file_detection.sample_batch import write_sample_batch  # noqa: E402
from ads.file_detection.server import INSPECTORS  # noqa: E402

ROOT = Path(tempfile.mkdtemp(prefix="kesif_poc_"))
ORNEK = ROOT / "ornekler"
YUKLEME = ROOT / "yukleme"
YUKLEME.mkdir(parents=True, exist_ok=True)

# Insan kararlari burada tutulur: kaynak ayrimi gorunur kalsin diye
# olcumden AYRI saklanir.
KARARLAR: dict[str, dict] = {}

app = FastAPI(title="Kesif PoC")


def _turkce_tablo_goruntusu(hedef: Path) -> Path | None:
    """Gosterimin yildizi: metni SECILEMEYEN bir Turkce tablo."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None

    adaylar = (
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    yol = next((a for a in adaylar if Path(a).exists()), None)
    if yol is None:
        return None

    satirlar = [
        ["Şehir", "Nüfus", "Bölge", "Değişim"],
        ["İstanbul", "15.840.900", "Marmara", "%1,2"],
        ["Şanlıurfa", "2.143.020", "Güneydoğu", "%2,8"],
        ["Çorum", "527.000", "Karadeniz", "-%0,4"],
        ["Muğla", "1.048.185", "Ege", "%1,7"],
    ]
    f = ImageFont.truetype(yol, 22)
    img = Image.new("RGB", (760, 60 + len(satirlar) * 44), "white")
    d = ImageDraw.Draw(img)
    y = 30
    for i, satir in enumerate(satirlar):
        for x, hucre in zip((40, 260, 440, 630), satir, strict=True):
            d.text((x, y), hucre, fill="black", font=f)
        if i == 0:
            d.line([(30, y + 34), (730, y + 34)], fill="black", width=2)
        y += 44
    img.save(hedef)
    return hedef


write_sample_batch(ORNEK)
_turkce_tablo_goruntusu(ORNEK / "tablo_ekran_goruntusu.png")

# Gosterimde one cikacak dosyalar: her biri ayri bir dersi anlatiyor.
ONE_CIKAN = {
    "tablo_ekran_goruntusu.png": "Metni secilemeyen tablo — OCR + Türkçe sınırı",
    "tek_sutun.csv": "Magika %95.3 güvenle yanılıyor",
    "hizali_rapor.txt": "Ayraçsız hizalı tablo — format 'txt' der, şekil 'tablo' bulur",
    "satis_2024.csv": "Tarih sütunlu CSV — log sanılmıyor",
    "sunucu.log": "Gerçek log — belge akışına",
    "arsiv.zip": "Açılarak doğrulanır, tahmin edilmez",
    "tablo.xlsx": "XLSX kapsayıcı değil — sayfaları açılıp ölçülüyor",
}


def _karar_bekleyenler(yol: Path) -> list[dict]:
    """Dosyaya OZEL, kazanc/bedelli secenekler.

    Jenerik akis listesinden farkli: bu secenekler dosyanin kendisinden
    uretiliyor ("bu sutun Turkce sayi biciminde", "OCR Turkce'yi
    dogrulayamadi") ve her birinin ne kazandirip ne kaybettirdigi yazili.
    Dosya deterministik cozulmus olsa bile OKUMA PARAMETRESI icin karar
    bekliyor olabilir; o yuzden akis kararindan bagimsiz bakilir.
    """
    cozumleyici = INSPECTORS.get(yol.suffix.lower())
    if cozumleyici is None:
        return []
    try:
        rapor = cozumleyici(yol)
    except Exception:  # noqa: BLE001 - gosterim kritik degil
        return []

    return [
        {
            "baslik": b.baslik,
            "onem": b.onem.value,
            "neden": b.aciklama,
            "kanit": [f"{k.olcum}: {k.deger}" for k in b.kanitlar],
            "secenekler": [
                {"kod": s.kod, "eylem": s.eylem, "kazanc": s.kazanc,
                 "bedel": s.bedel, "onerilen": s.onerilen}
                for s in b.secenekler
            ],
        }
        for b in rapor.bulgular if b.secenekler
    ]


def _karar_sozlugu(k) -> dict:
    yol = Path(k.yol)
    return {
        "ad": yol.name,
        "boyut": k.boyut,
        "format": k.format,
        "format_guveni": round(k.format_guveni, 3),
        "sekil": k.sekil,
        "akis": k.akis.value,
        "deterministik": k.deterministik,
        "yargi_sebebi": k.yargi_sebebi,
        "kanitlar": [{"ad": a, "deger": d} for a, d in k.kanitlar],
        "notlar": k.notlar,
        # Dosyaya ozel kararlar: her biri kendi secenekleriyle.
        "kararlar": _karar_bekleyenler(yol),
        # Akis kararsizsa jenerik akis listesi de sunulur.
        "secenekler": (
            [asdict(s) for s in FLOW_OPTIONS] if not k.deterministik else []
        ),
        # Insana GOSTERILEBILIR onizleme: ham bayt degil, okunan icerik.
        "onizleme": ("" if k.deterministik else prepare_preview(k)),
    }


@app.get("/ornekler")
def ornekler() -> JSONResponse:
    dosyalar = sorted(p.name for p in ORNEK.iterdir() if p.is_file())
    return JSONResponse({
        "one_cikan": [
            {"ad": ad, "aciklama": ONE_CIKAN[ad]}
            for ad in ONE_CIKAN if (ORNEK / ad).exists()
        ],
        "hepsi": dosyalar,
    })


@app.post("/incele/ornek/{ad}")
def incele_ornek(ad: str) -> JSONResponse:
    hedef = (ORNEK / ad).resolve()
    if ORNEK.resolve() not in hedef.parents or not hedef.is_file():
        return JSONResponse({"hata": "bulunamadi"}, status_code=404)
    return JSONResponse(_karar_sozlugu(route(hedef)))


@app.post("/incele")
async def inspect(dosya: UploadFile) -> JSONResponse:
    ad = Path(dosya.filename or "dosya").name
    hedef = YUKLEME / ad
    hedef.write_bytes(await dosya.read())
    return JSONResponse(_karar_sozlugu(route(hedef)))


@app.post("/karar/{ad}/{akis}")
def karar(ad: str, akis: str) -> JSONResponse:
    gecerli = {s.akis for s in FLOW_OPTIONS}
    if akis not in gecerli:
        return JSONResponse({"hata": f"gecersiz akis: {akis}"}, status_code=400)
    KARARLAR[ad] = {"akis": akis, "kaynak": "human_feedback"}
    return JSONResponse({"ad": ad, "akis": akis, "kaynak": "human_feedback"})


@app.post("/otomatik/{ad}")
def otomatik(ad: str) -> JSONResponse:
    """Insan yokken deterministik yedek. Sonuc acikca etiketlenir."""
    hedef = (ORNEK / ad).resolve()
    if not hedef.is_file() or ORNEK.resolve() not in hedef.parents:
        hedef = (YUKLEME / ad).resolve()
        if not hedef.is_file() or YUKLEME.resolve() not in hedef.parents:
            return JSONResponse({"hata": "bulunamadi"}, status_code=404)

    k = route(hedef)
    if k.deterministik:
        return JSONResponse({"hata": "bu dosya zaten deterministik cozuldu"},
                            status_code=400)
    s = resolve_automatically(k)
    KARARLAR[ad] = {"akis": s.akis, "kaynak": s.kaynak}
    return JSONResponse({"ad": ad, "akis": s.akis,
                         "kaynak": s.kaynak, "gerekce": s.gerekce})


@app.get("/", response_class=HTMLResponse)
def kok() -> str:
    return SAYFA


SAYFA = """<!doctype html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Keşif — Canlı Gösterim</title>
<style>
  :root{--paper:#EFF2F1;--surface:#F8FAF9;--sunk:#E6EBEA;--ink:#101819;
    --muted:#57696B;--rule:#C8D3D2;--hair:#DDE4E3;--accent:#0D6B70;
    --risk:#A8481F;--ok:#4A7241;--warm:#8A6A2F;}
  @media(prefers-color-scheme:dark){:root{--paper:#0A1113;--surface:#121B1C;
    --sunk:#0E1618;--ink:#E0EAE8;--muted:#8EA3A3;--rule:#233130;--hair:#1A2526;
    --accent:#4FC0C2;--risk:#E08B5A;--ok:#84B276;--warm:#D6B06A;}}
  *{box-sizing:border-box;} body{margin:0;background:var(--paper);color:var(--ink);
    font:16px/1.6 "Iowan Old Style",Palatino,Georgia,serif;}
  .wrap{max-width:60rem;margin:0 auto;padding:2.5rem 1.25rem 5rem;}
  .mono,h2,.lbl,button,code,pre,th{font-family:ui-monospace,"SF Mono",Menlo,monospace;}
  h1{font-size:1.6rem;margin:.3rem 0 .4rem;font-family:ui-monospace,Menlo,monospace;}
  .sub{color:var(--muted);margin:0 0 1.8rem;}
  h2{font-size:.72rem;letter-spacing:.15em;text-transform:uppercase;
    color:var(--accent);border-bottom:1px solid var(--rule);padding-bottom:.5rem;
    margin:2rem 0 1rem;}
  .kartlar{display:grid;grid-template-columns:repeat(auto-fill,minmax(15rem,1fr));gap:.7rem;}
  .kart{background:var(--surface);border:1px solid var(--rule);border-left:3px solid var(--accent);
    padding:.7rem .9rem;cursor:pointer;text-align:left;color:inherit;font:inherit;}
  .kart:hover{background:var(--sunk);} .kart:focus-visible{outline:2px solid var(--accent);}
  .kart .ad{font-family:ui-monospace,Menlo,monospace;font-size:.82rem;font-weight:700;}
  .kart .ac{font-size:.78rem;color:var(--muted);margin-top:.2rem;}
  #birak{border:2px dashed var(--rule);padding:1.6rem;text-align:center;color:var(--muted);
    margin:1rem 0;background:var(--surface);}
  #birak.uzerinde{border-color:var(--accent);color:var(--accent);}
  .satir{display:flex;gap:.6rem;flex-wrap:wrap;margin:.5rem 0 1rem;}
  .rozet{font-family:ui-monospace,Menlo,monospace;font-size:.72rem;padding:.25rem .6rem;
    border:1px solid var(--rule);background:var(--surface);}
  .rozet b{color:var(--ink);} .ok{border-left:3px solid var(--ok);}
  .hf{border-left:3px solid var(--warm);}
  table{border-collapse:collapse;width:100%;font-size:.86rem;margin:.5rem 0;}
  td,th{text-align:left;padding:.4rem .8rem .4rem 0;border-bottom:1px solid var(--hair);
    vertical-align:top;}
  th{font-size:.66rem;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);}
  .kutu{background:var(--surface);border:1px solid var(--rule);padding:1rem 1.2rem;margin:1rem 0;}
  .kutu.uyari{border-left:4px solid var(--warm);}
  .kutu.ok{border-left:4px solid var(--ok);}
  .lbl{font-size:.64rem;letter-spacing:.14em;text-transform:uppercase;
    color:var(--warm);font-weight:700;display:block;margin-bottom:.5rem;}
  .kutu.ok .lbl{color:var(--ok);}
  button.sec{display:block;width:100%;text-align:left;margin:.35rem 0;padding:.55rem .8rem;
    background:var(--surface);border:1px solid var(--rule);color:inherit;
    font-family:ui-monospace,Menlo,monospace;font-size:.8rem;cursor:pointer;}
  button.sec:hover{border-color:var(--accent);background:var(--sunk);}
  button.sec b{color:var(--accent);}
  pre{background:var(--sunk);border:1px solid var(--hair);padding:.7rem .9rem;
    overflow-x:auto;font-size:.78rem;margin:.4rem 0;}
  .karar{background:var(--surface);border:1px solid var(--rule);
    border-left:4px solid var(--warm);padding:.9rem 1.1rem;margin:.9rem 0;}
  .karar.kritik{border-left-color:var(--risk);}
  .karar h3{font-size:.98rem;margin:0 0 .3rem;font-family:ui-monospace,Menlo,monospace;}
  .karar .neden{font-size:.88rem;color:var(--muted);margin:0 0 .5rem;}
  .karar .kanit{font-family:ui-monospace,Menlo,monospace;font-size:.74rem;
    color:var(--muted);margin:.1rem 0;}
  .sec2{display:block;width:100%;text-align:left;margin:.4rem 0;padding:.6rem .85rem;
    background:var(--paper);border:1px solid var(--rule);color:inherit;cursor:pointer;
    font-family:inherit;font-size:.9rem;}
  .sec2:hover{border-color:var(--accent);}
  .sec2 .ey{font-family:ui-monospace,Menlo,monospace;font-weight:700;
    display:block;margin-bottom:.25rem;}
  .sec2 .kz{color:var(--ok);font-size:.82rem;display:block;}
  .sec2 .bd{color:var(--risk);font-size:.82rem;display:block;}
  .sec2 .on{font-family:ui-monospace,Menlo,monospace;font-size:.64rem;
    letter-spacing:.1em;color:var(--accent);margin-left:.5rem;}
  .gizli{display:none;}
</style></head><body><div class="wrap">

<h1>Keşif — canlı gösterim</h1>
<p class="sub">Bir dosya seç ya da sürükle. Sistem ne ölçtüğünü, hangi akışa
yolladığını ve <b>emin olamadığında neden sorduğunu</b> gösterir.</p>

<h2>Örnek dosyalar</h2>
<div class="kartlar" id="kartlar"></div>

<div id="birak">veya buraya bir dosya sürükle</div>

<div id="sonuc" class="gizli"></div>

<script>
const $ = s => document.querySelector(s);

fetch("/ornekler").then(r=>r.json()).then(d=>{
  $("#kartlar").innerHTML = d.one_cikan.map(o=>
    `<button class="kart" data-ad="${o.ad}">
       <span class="ad">${o.ad}</span>
       <span class="ac">${o.aciklama}</span></button>`).join("");
  document.querySelectorAll(".kart").forEach(b=>
    b.onclick = ()=> gonder(`/incele/ornek/${encodeURIComponent(b.dataset.ad)}`));
});

const birak = $("#birak");
["dragover","dragenter"].forEach(e=>birak.addEventListener(e,ev=>{
  ev.preventDefault(); birak.classList.add("uzerinde");}));
["dragleave","drop"].forEach(e=>birak.addEventListener(e,ev=>{
  ev.preventDefault(); birak.classList.remove("uzerinde");}));
birak.addEventListener("drop",ev=>{
  const f = ev.dataTransfer.files[0]; if(!f) return;
  const fd = new FormData(); fd.append("dosya", f);
  gonder("/incele", {method:"POST", body:fd});
});

function gonder(url, opt){
  $("#sonuc").classList.remove("gizli");
  $("#sonuc").innerHTML = "<p class='sub'>ölçülüyor…</p>";
  fetch(url, opt || {method:"POST"}).then(r=>r.json()).then(ciz);
}

function esc(s){ return String(s).replace(/[&<>]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c])); }

function ciz(k){
  if(k.hata){ $("#sonuc").innerHTML = `<p>${esc(k.hata)}</p>`; return; }
  const hf = !k.deterministik;
  let h = `<h2>Sonuç · ${esc(k.ad)}</h2>
    <div class="satir">
      <span class="rozet">format <b>${esc(k.format)}</b> (${k.format_guveni})</span>
      <span class="rozet">şekil <b>${esc(k.sekil)}</b></span>
      <span class="rozet ${hf?"hf":"ok"}">akış <b>${esc(k.akis)}</b></span>
      <span class="rozet ${hf?"hf":"ok"}">${hf
          ?"human feedback gerekli":"deterministik çözüldü"}</span>
    </div>`;

  if(k.kanitlar.length){
    h += `<table><thead><tr><th>ölçüm</th><th>değer</th></tr></thead><tbody>` +
      k.kanitlar.map(x=>`<tr><td>${esc(x.ad)}</td><td>${esc(x.deger)}</td></tr>`).join("") +
      `</tbody></table>`;
  }
  if(k.notlar.length){
    h += `<pre>${k.notlar.map(esc).join("\\n")}</pre>`;
  }
  if(k.onizleme){
    h += `<p class="lbl" style="margin:.9rem 0 .3rem">Sistemin okuduğu içerik</p>
          <pre>${esc(k.onizleme)}</pre>`;
  }

  if(k.kararlar && k.kararlar.length){
    h += `<h2>Karar bekleyen konular (${k.kararlar.length})</h2>`;
    k.kararlar.forEach((kr,ki)=>{
      h += `<div class="karar ${kr.onem==='kritik'?'kritik':''}">
        <h3>${esc(kr.baslik)}</h3>
        <p class="neden">${esc(kr.neden)}</p>` +
        kr.kanit.map(x=>`<div class="kanit">${esc(x)}</div>`).join("") +
        kr.secenekler.map((s,si)=>
          `<button class="sec2" data-k="${ki}" data-s="${si}">
             <span class="ey">${esc(s.kod)}) ${esc(s.eylem)}${s.onerilen
                 ?'<span class="on">ÖNERİLEN</span>':''}</span>
             <span class="kz">+ ${esc(s.kazanc)}</span>
             <span class="bd">− ${esc(s.bedel)}</span>
           </button>`).join("") +
      `</div>`;
    });
  }

  if(hf){
    h += `<div class="kutu uyari"><span class="lbl">Akış kararı verilemedi — tahmin edilmiyor</span>
      <p style="margin:.2rem 0 .8rem">${esc(k.yargi_sebebi)}</p>
      <p style="margin:.2rem 0 .6rem;font-size:.88rem;color:var(--muted)">
        Açık uçlu soru sorulmaz; sonuçları önceden yazılmış kapalı seçenekler sunulur:</p>` +
      k.secenekler.map(s=>
        `<button class="sec" data-akis="${s.akis}">`
        + `<b>${esc(s.akis)}</b> — ${esc(s.aciklama)}</button>`
      ).join("") +
      `<p style="margin:.9rem 0 .3rem;font-size:.82rem;color:var(--muted)">
         Gözetimsiz toplu koşumda başında kimse olmayabilir. O durumda
         sistem beklemez; sabit bir kural zinciriyle en makul akışı seçer
         ve <b>otomatik seçtiğini gizlemez</b>:</p>
       <button class="sec" id="oto"><b>otomatik mod</b> — insan yokken deterministik yedek</button>
      </div>`;
  }
  $("#sonuc").innerHTML = h;

  document.querySelectorAll("button.sec2").forEach(b=>b.onclick=()=>{
    const kr = k.kararlar[+b.dataset.k], sc = kr.secenekler[+b.dataset.s];
    b.parentElement.querySelectorAll(".sec2").forEach(x=>x.style.borderColor="");
    b.style.borderColor = "var(--accent)";
    b.insertAdjacentHTML("afterend",
      `<div class="kutu ok" style="margin:.5rem 0"><span class="lbl">Seçim kaydedildi</span>
       <pre>konu   : ${esc(kr.baslik)}
seçim  : ${esc(sc.kod)}) ${esc(sc.eylem)}
kaynak : human_feedback   (ölçüm değil, model yorumu değil)</pre></div>`);
    b.disabled = true;
  });

  document.querySelectorAll("button.sec").forEach(b=>b.onclick=()=>{
    fetch(`/karar/${encodeURIComponent(k.ad)}/${b.dataset.akis}`,{method:"POST"})
      .then(r=>r.json()).then(c=>{
        $("#sonuc").insertAdjacentHTML("beforeend",
          `<div class="kutu ok"><span class="lbl">Karar kaydedildi</span>
           <pre>dosya  : ${esc(c.ad)}
akış   : ${esc(c.akis)}
kaynak : ${esc(c.kaynak)}   (ölçüm değil, model yorumu değil)</pre></div>`);
      });
  });
  const oto = $("#oto");
  if(oto) oto.onclick = ()=>{
    fetch(`/otomatik/${encodeURIComponent(k.ad)}`,{method:"POST"})
      .then(r=>r.json()).then(c=>{
        if(c.hata){ alert(c.hata); return; }
        $("#sonuc").insertAdjacentHTML("beforeend",
          `<div class="kutu uyari"><span class="lbl">Otomatik seçildi — insan onayı alınmadı</span>
           <pre>dosya  : ${esc(c.ad)}
akış   : ${esc(c.akis)}
kaynak : ${esc(c.kaynak)}
gerekçe: ${esc(c.gerekce)}</pre></div>`);
      });
  };
  $("#sonuc").scrollIntoView({behavior:"smooth", block:"nearest"});
}
</script>
</div></body></html>
"""


if __name__ == "__main__":
    print(f"ornekler : {ORNEK}")
    print("adres    : http://127.0.0.1:8600")
    uvicorn.run(app, host="127.0.0.1", port=8600, log_level="warning")
