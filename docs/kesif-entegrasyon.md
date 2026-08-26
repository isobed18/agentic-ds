# Keşif katmanı — entegrasyon notu

**Kime:** pipeline sahibine ve bu dalı inceleyecek olana
**Dal:** `Emre` · **Kapsam:** `src/ads/kesif/` + testler + `pyproject.toml`

Bu not, dalı soğuk okuyan birinin (ya da bir ajanın) kaçırabileceği şeyi
yazmak için var: **entegrasyonun önündeki engeller bu dalda değil,
pipeline tarafındaki üç varsayımda.** Kesif kodunu incelemek "sorun yok"
sonucunu verir ama asıl soruyu cevaplamaz.

---

## 1. Bu katman ne yapıyor

Pipeline'ın girişinde, türü **bilinmeyen** bir dosyanın ne olduğunu
içerikten ölçüp doğru akışa yönlendirir. Dört katman:

| # | Katman | Ne ölçer | LLM |
|---|--------|----------|-----|
| 1 | Magika | format (içerikten, uzantıdan değil) | yok |
| 2 | `sekil.py` | yapısal şekil: tablo / log / serbest metin / anahtar-değer | yok |
| 3 | `kanit.py` | kodlama, ayraç, sayı ve tarih biçimi | yok |
| 4 | `yargi.py` | çözülemeyeni human feedback'e çıkarır | yok |

Çıkış akışları: `tablo`, `belge`, `agac`, `kapsayici`, `islenemez`,
`yargi` (karar verilemedi).

**Ölçüm:** 25 dosyalık karma partide 25/25 doğru; %88 otomatik çözülüyor,
%12 human feedback'e çıkıyor. Sayı `tests/test_kesif_olcum.py` ile
regresyon testine bağlı — bir katman bozulursa test kırılır.

**Bulut bağımlılığı yok.** Soketler kapatılarak doğrulandı: 0 bağlantı
denemesi.

---

## 2. Birleştirme durumu

`origin/staged-intake-screen` ile gerçek birleştirme provası yapıldı
(`git merge-tree`, çalışma ağacına dokunmadan):

```
CATISMA YOK
```

| | |
|---|---|
| `staged-intake-screen` dokunduğu | 145 dosya |
| `Emre` dokunduğu | 34 dosya |
| **Ortak** | **1** — `pyproject.toml` |

`pyproject.toml` çakışması önemsiz: iki taraf farklı bölgelere ekstra
ekliyor (`kesif`/`kesif-ocr` vs `sandbox`), otomatik birleşiyor.

Kesif kendi paketi olduğu için (`src/ads/kesif/`) başka hiçbir dosyada
temas yok.

---

## 3. Entegrasyonun önündeki üç engel

Hepsi pipeline tarafında. Kesif'i kullanabilmek için bunların
değişmesi gerekiyor ve **bu dosyalar bu dalda değiştirilmedi** —
sahibinin kararı olduğu için.

### 3.1 Yükleme kapısı dosyayı reddediyor

`src/ads/api/service.py`

```python
_UPLOAD_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls",
                    ".parquet", ".pq"}
...
raise ValueError("supported uploads are CSV/TSV, Excel, or Parquet")
```

PDF veya görüntü yüklemek **hata veriyor**. Kesif'in var olma sebebi tam
olarak bu kapıyı kaldırmak: dosyanın ne olduğuna uzantıya bakarak değil
içeriğe bakarak karar vermek.

### 3.2 Klasör tarayıcı desteklemediğini sessizce atlıyor

`src/ads/intake/loaders.py`

```python
if path.is_file() and path.suffix.lower() in supported:
```

Klasördeki bir PDF **görünmez** oluyor — hata bile vermiyor. Bu sessiz
veri kaybı; mimarinin önlemek için kurulduğu şeyin ta kendisi. Kesif bu
dosyaları görünür kılıp akışa ya da human feedback'e yönlendirir.

### 3.3 Sıralama — en önemlisi

`web/src/components/IntakeStage.tsx` başındaki yorum:

> *"by the time this is on screen intake and schema discovery have really
> executed"*

Hazırlık ekranı, intake **çalıştıktan sonra** duruyor. Kesif ise
intake'ten **önce** çalışmak zorunda: hangi dosyanın intake'e
girebileceğine o karar veriyor.

Yani kesif o ekranın *içine* konamaz. Daha erken bir noktaya girmesi
gerekiyor.

---

## 4. Önerilen yer: `source_profile()`

`src/ads/api/service.py` içindeki `source_profile()` kesif'in doğal
komşusu. Kendi docstring'i şunu diyor:

> *"Measured before any run exists. This is what makes the pre-run screen
> honest: the relationships shown are the same ones schema discovery will
> reason over, not a picture drawn from column names."*

Koşum başlamadan önce dosyalara ucuz bir geçiş zaten yapılıyor. Kesif o
geçişi genişletiyor: *"tabloları profille"* → *"bu dosyalar da ne"*.

Felsefe aynı: **ekranda ölçülmüş veri göster, diyalogda tahmin etme.**

Ayrıca `source_profile()` şu an şunu fırlatıyor:

```python
raise ValueError("source contains no supported data files")
```

Kesif devredeyse bu hata yerine *"şu dosyalar geldi, şunlar şu akışa
gidiyor, şu ikisi için karar gerekiyor"* cevabı üretilebilir.

---

## 5. Karar bekleyen konular

Bunlar inceleme bulgusu değil, **pipeline sahibinin vermesi gereken
kararlar**. Bu dalda bilerek çözülmedi.

### 5.1 Kesif nereye girecek? ⭐

- **A —** `source_profile()` içine (önerilen; yukarıdaki gerekçe)
- **B —** `upload()` anında, dosya başına
- **C —** intake'ten önce ayrı bir pipeline aşaması

### 5.2 Human feedback nereye bağlanacak? ⭐

Pipeline'da **zaten** bir insan-onayı mekanizması var:
`src/ads/gates/evaluator.py` → `build_human_prompt`. Kesif de kapalı
seçenekli sorular üretiyor ve deseni aynı ("açık uçlu soru sorma,
sonuçları yazılı seçenek sun").

**Kesif ikinci bir paralel mekanizma kurmamalı.** Ürettiği seçenekleri
mevcut `gates` mekanizmasına devretmesi gerekiyor. Bu bağlantıyı kurmak
`gates` tarafını bildiğinden emin olan birinin işi.

### 5.3 Seçilmeyen dosyaların akıbeti

Kullanıcı "sadece tablo akışı" derse diğer dosyalar ne olacak?
Öneri: **park edilsin ve sayısı görünür kalsın** — sessizce kaybolmasın.
Tanımlı değil.

---

## 6. Kullanım — ek yük olmadan

Pipeline modülü **doğrudan import edebilir**; MCP devreye girmez:

```python
from ads.kesif.yonlendirici import envanter

env = envanter(kaynak_dizini)          # klasör başına TEK çağrı
env["deterministik"]                    # otomatik çözülen sayısı
env["yargi_gerektiren"]                 # human feedback bekleyen
for k in env["kararlar"]:
    k.akis          # tablo / belge / agac / kapsayici / islenemez / yargi
    k.deterministik # False ise karar gerekiyor, k.yargi_sebebi yazılı
    k.kanitlar      # her ölçümün dayanağı
```

MCP yalnızca **dış ajanlar** için. Ölçüldü: çağrı başına **1,10 ms**
(ortanca). Dosya başına çağrılırsa 10.000 dosyada 11 sn'ye çıkar; toplu
çağrıda (`envanter`) ihmal edilebilir. İkisi aynı anda mümkün, seçim
gerekmiyor.

Gözetimsiz koşum için:

```python
from ads.kesif.yargi import artigi_coz
rapor = artigi_coz(env, otomatik=True)   # beklemez, sabit kuralla seçer
```

Seçim bir modele değil sabit kural zincirine dayanır ve
`kaynak="otomatik_varsayilan"` olarak etiketlenir — ölçümden de human
feedback'ten de ayırt edilebilir kalır.

---

## 7. Kurulum

```bash
pip install -e ".[kesif]"          # yalnızca magika — pipeline için gereken bu
pip install -e ".[kesif-mcp]"      # MCP sunucusu, OPSİYONEL
pip install -e ".[kesif-ocr]"      # OCR, ~245 MB, OPSİYONEL
```

**MCP çekirdek kuruluma dahil değil.** Veri alma yolunda protokol katmanı
yok: pipeline `envanter()`'ı doğrudan import eder (§6). MCP yalnızca keşif'i
*dış* bir ajana açmak istendiğinde gerekiyor. CI de `kesif`'i kuruyor,
`kesif-mcp`'yi kurmuyor — yani "MCP olmadan çalışır" iddiası her koşumda
sınanıyor.

OCR ayrı tutuldu: yalnızca görüntü/taranmış belge işleyenler ihtiyaç
duyar. Kurulu değilse sistem çökmüyor, dosya human feedback'e çıkıyor ve
mesajda ne kurulacağı yazıyor.

**OCR bileşeni için mentörden öneri alındı; o kısım revize edilecek.**
Mevcut sınır ölçüldü ve gizlenmiyor: tanıma modelinin sözlüğünde Türkçe
harfler yok, dolayısıyla Türkçe metin doğrulanamıyor — sayılar ve tablo
yapısı etkilenmiyor.

---

## 8. Test durumu

```bash
pytest -q          # düz pytest, CI'ın koştuğu biçim
```

Kesif testleri: 7 dosya, hepsi düz `pytest` ile de geçiyor.

**Not:** Depoda `test_intake`, `test_orchestration_critic`,
`test_prefect_ui`, `test_validation_strategy` düz `pytest` ile
toplanamıyor (`ModuleNotFoundError: No module named 'tests'`). Bu
**önceki commit'ten** gelen bir sorun, bu dalda değil.
`staged-intake-screen` dalı bunu `pythonpath = ["src", "."]` ile
çözüyor. Kesif testleri o düzeltmeye bağımlı bırakılmadı, kendi
tarafında çözüldü.

---

## 9. İncelerken bakılması istenen yerler

Kod temiz mi sorusundan çok, şu üç şeye bakılması daha faydalı:

1. **`source_profile()` doğru yer mi?** (§4) — yanlışsa alternatifi ne
2. **`gates` bağlantısı nasıl kurulmalı?** (§5.2) — kesif kendi
   mekanizmasını kurmasın diye
3. **`_UPLOAD_SUFFIXES` ve `load_directory` kimin tarafından
   genişletilecek?** (§3.1, §3.2) — bu dosyalar bu dalda bilerek
   değiştirilmedi

Daha ayrıntılı teknik döküm: `docs/kesif-rapor.html`
Katmanların kendi açıklaması: `src/ads/kesif/README.md`
