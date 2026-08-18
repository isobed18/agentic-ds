# Koşu Defteri

Aynı veriye yapılan koşuların karşılaştırmalı kaydı. Yeni koşular eklendikçe
büyütülecek; her koşu için tutulacak alanlar en sonda.

Veri seti: `sample` — 4 tablo, 3 format (noktalı virgüllü CSV, Excel, Parquet),
anahtarlar farklı isimlerde (`provider_ref` ↔ `physician_id`).

---

## Koşu 1 — `ui-affa867a6388` (12 Ağustos, tek atışlık agentlar)

| | |
|---|---|
| Temel tablo | `physicians__physician_master` |
| Grain | `physician_id` — 800 satır |
| Join | 3 join + 2 agregasyon |
| Hedef | `annual_comp` |
| Görev | regresyon · rmse |
| Bölme | temporal · `hire_date` |
| **Sonuç** | **tamamlandı** (12/12 aşama) |

Kurulan plan:

```
base: physicians__physician_master  grain=[physician_id]
  JOIN physicians__compensation      [physician_id] = [physician_id]
  JOIN transactions_by_physician     [physician_id] = [physician_id]
  JOIN ledger_by_provider            [physician_id] = [provider_ref]
  AGG  transactions                  group_by=[physician_id]
  AGG  ledger_2019_2024              group_by=[provider_ref]
```

Sızıntı denetimi bir kez insana çıktı, insan karar verdi, ikinci denemede geçti.

---

## Koşu 2 — `ui-4db92b17456f` (17 Ağustos, araştırmacı agentlar)

| | |
|---|---|
| Temel tablo | `ledger_2019_2024` |
| Grain | `entry_id` — 20.000 satır |
| Join | 1 join, agregasyon yok |
| Hedef | `account_code` |
| Görev | çok sınıflı · accuracy |
| **Sonuç** | **değerlendirmede çöktü** |

Kurulan plan:

```
base: ledger_2019_2024  grain=[entry_id]
  JOIN physicians__physician_master  [provider_ref] = [physician_id]
```

---

## Hangisi daha mantıklı

**Koşu 1**, ve sebebi zevk meselesi değil: **verinin ne kadarını kullandığı**.

Koşu 1 dört tablonun **dördünü de** kullandı. Doktor başına tek satır kurdu,
maaş bilgisini join etti, işlem geçmişini ve muhasebe kayıtlarını doktor
seviyesine *toplayarak* ekledi. Tahmin edilen şey doktora ait bir büyüklük,
satır da doktoru temsil ediyor — granülerlik doğru.

Koşu 2 dört tablonun **ikisini attı**. Maaş ve işlem tabloları plana hiç
girmedi. Kalan plan muhasebe kaydına doktorun demografik bilgilerini ekliyor ve
kaydın hesap kodunu tahmin ediyor. Anlamsız bir problem değil — muhasebe
otomasyonunda gerçek bir görevdir — ama veri setinin yarısı kullanılmadan
kuruldu.

**Önemli nüans:** Koşu 2'nin planı deterministik denemeden temiz geçti. 20.000
satır girdi, 20.000 satır çıktı, grain korundu, uyarı yok. Plan **teknik olarak
doğru**. Sorun doğruluğunda değil kapsamında, ve sistem "bu join yanlış"
diyemez çünkü join yanlış değil. Deterministik doğrulamanın yakalayamayacağı bir
kalite farkı.

**Neden farklı çıktı:** Koşu 2 başlatılırken arayüz sessizce ilk tablonun ilk
hedef adayını gönderdi ve API bunu `confirmed_by="human"` diye kaydetti. Yani
uydurulmuş bir varsayılan, kullanıcının beyan ettiği niyet olarak yazıldı.
Bu düzeltildi: agent modunda artık sadece veri gönderiliyor, beyan edilmemiş
problem `auto` olarak kaydediliyor ve koşu `problem_discovery`'de durup insana
soruyor.

---

## Agent ne yaptı — denetim kayıtlarından

| Agent | Deneme | Kabul | Kanıt üreten tool | Süre |
|---|---|---|---|---|
| `schema_investigator` | 5 | ✅ evet | `trial_integration_plan` | 398 s |
| `problem_investigator` | 8 | ❌ **hayır** | **hiçbiri** | 390 s |
| `validation_investigator` | 1 | ✅ evet | skill: `choosing_a_split` | — |

### Şema araştırmacısı: çalıştı

Beş denemede kabul edildi. İki doğrulama hatası aldı —
`tool_error:join_overlap` ve `unsupported_join` — yani önce çalışmayan bir ölçüm
istedi, sonra desteklenmeyen bir join önerdi ve reddedildi. Sonunda planını
`trial_integration_plan` ile gerçekten çalıştırıp kanıtladı.

**Bu, tasarımın çalıştığı durum:** agent yanlış öneriyor, deterministik katman
reddediyor, agent düzeltiyor.

### Problem araştırmacısı: tamamen başarısız

Sekiz deneme, sıfır kanıt, 390 saniye:

```
invalid_action
finished_without_tool_evidence
tool_error:python
finished_without_tool_evidence
finished_without_tool_evidence
finished_without_tool_evidence
tool_error:value_counts
tool_error:value_counts
turn_budget_exhausted
```

Dört kez *ölçüm yapmadan bitirmeye* çalıştı ve dört kez reddedildi. Çağırdığı
tool'lar hata verdi. Sonunda tur bütçesi doldu.

**Ama boruhattı durmadı, ve bu tesadüf değil.** Araştırmacı çöktüğü halde
problem tanımı yine üretildi, çünkü **zorunlu deterministik taban** her koşuda
çalışıyor. Tek atışlık `problem_discovery` adayları üretti, Python destek
ölçümlerini iliştirdi: 20.000 satır, 7 sınıf, azınlık sınıf %14, 6 kullanılabilir
öznitelik, engelleyici sebep yok.

Araştırmacı katmanı bir *iyileştirme*dir, bağımlılık değil. Tasarımın en çok
tartışılan kararı buydu ve ilk gerçek testinde amaçlandığı gibi davrandı.

---

## Araştırmacı olmanın bedeli

| Aşama | Tek atış | Araştırmacı | Kat |
|---|---|---|---|
| `schema_discovery` | 105 s | 590 s | **5,6×** |
| `problem_discovery` | 83 s | 492 s | **5,9×** |
| `validation_strategy` | 77 s | 528 s | **6,9×** |
| `eda` | 0,2 s | 46 s | **230×** |
| `intake` | 0,3 s | 0,2 s | — |
| `integration` | 0,1 s | 0,1 s | — |

Toplam agent süresi **265 s → 1.656 s**. Yerel 27B model, RTX 3090.
Deterministik aşamalar hiç etkilenmedi; fark tamamen tur sayısından geliyor.

Bu bir hata değil, bütçenin sonucu: şema keşfine 12 tur ve 12 tool çağrısı hakkı
tanınmış ve her tur bir model üretimi. Ama **390 saniyenin hiçbir kanıt
üretmeden harcanabildiği** problem araştırmacısı örneği, bütçenin başarısızlık
durumunda çok pahalı olduğunu gösteriyor.

---

## Bulunan hata: eğitim seçilen metriği yok sayıyordu

Koşu 2 on bir aşamayı geçip değerlendirmede çöktü:

```
ValueError: TrainingReport primary_metric does not match ProblemDefinition.
```

Kök sebep `training/runner.py` içindeydi:

```python
metric_order = _METRICS_BY_TASK[task_type]
primary_metric = metric_order[0]     # problemin seçtiği metrik yok sayılıyor
```

Problem `accuracy` demişti; eğitim çok sınıflı görevin ilk metriği olan `f1`'i
üretti. Değerlendirme ikisinin eşitliğini şart koşuyor ve koşu düştü.

**Etkisi tek koşuyla sınırlı değildi:** regresyonda `rmse`, ikili
sınıflandırmada `roc_auc`, çok sınıflıda `f1` dışında bir metrik seçen her koşu
aynı yerde çökerdi.

**Koşu 1 neden yakalamadı:** regresyon seçmişti ve metriği `rmse`'ydi — zaten
regresyonun listesindeki ilk metrik. Tesadüfen eşleşti.

**Neden test paketi kaçırdı:** testler hep varsayılan metrikle koşuyor. "Her
zaman ilkini al" davranışı, ilk seçenek istendiğinde doğru cevabı verir. Bu,
projenin daha önce üç kez yaşadığı kalıbın aynısı — kontrol akışının zaten
bildiği bir şeyi verinin tesadüfi bir özelliğinden türetmek.

**Düzeltildi:** `train_candidates` artık `primary_metric` alıyor, görev için
geçerli olmayan bir metrik verilirse açıkça hata veriyor. Görev tipinin bütün
metrikleri zaten hesaplandığı için maliyeti yok.

---

## Sonraki koşular için kaydedilecekler

1. **Kurulan proje** — temel tablo, grain, join sayısı, hedef, görev, metrik,
   bölme stratejisi
2. **Verinin ne kadarı kullanıldı** — plana giren tablo sayısı / toplam. Bu tek
   sayı, iki planı karşılaştırmanın en hızlı yolu.
3. **Agent denetim kayıtları** — deneme sayısı, kabul edildi mi, hangi tool
   kanıt üretti, hangi doğrulamalar patladı
4. **Aşama süreleri** — özellikle araştırmacı aşamalar, ve harcanan sürenin
   kanıt üretip üretmediği
5. **Çökme varsa kök sebep** — hangi dosya, hangi satır, neden daha önce
   görülmedi

Dördüncü madde Koşu 2'nin en önemli dersi: **süre ile kanıt ayrı ölçülmeli.**
390 saniye harcayıp sıfır kanıt üreten bir agent, hızlı ama işe yaramaz bir
agenttan farklı bir sorundur ve farklı bir çözüm gerektirir.
