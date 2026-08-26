"""Language for everything the user reads.

The rule this is built to: **the backend emits codes and measured numbers, and
the words are chosen at the edge.** Most of the system already works that way —
intake issues carry stable codes, gates carry reason codes — and the frontend
maps those to whatever language is selected.

`ads.api.panels` is the exception. It composes prose around measured values, so
the sentence cannot be assembled from a code after the fact. Those strings are
translated here instead, keyed by their English source text so an untranslated
string degrades to English rather than to a missing-key placeholder.

The active language is per-request, not global: two people can be reading the
same server in different languages, so it lives in a ContextVar rather than a
module-level setting.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

SUPPORTED = ("tr", "en")
DEFAULT = "tr"

_active: ContextVar[str] = ContextVar("ads_language", default=DEFAULT)


def normalise(value: str | None) -> str:
    """Accept `tr`, `tr-TR`, `TR`, an Accept-Language header, or nothing."""
    if not value:
        return DEFAULT
    for part in value.split(","):
        code = part.split(";")[0].strip().lower()[:2]
        if code in SUPPORTED:
            return code
    return DEFAULT


def current() -> str:
    return _active.get()


@contextmanager
def using(language: str | None):
    token = _active.set(normalise(language))
    try:
        yield
    finally:
        _active.reset(token)


def t(text: str, /, **params: Any) -> str:
    """Translate `text` into the active language and interpolate `params`.

    The key is the English sentence. That keeps the call sites readable and
    means a string nobody has translated yet still renders correctly, just in
    the wrong language — which is a far better failure than a blank panel.
    """
    catalogue = _CATALOGUE.get(current(), {})
    rendered = catalogue.get(text, text)
    return rendered.format(**params) if params else rendered


#: English source -> Turkish. Only prose the user reads; codes stay codes.
_TR: dict[str, str] = {
    # --- panel titles and captions
    "Exploratory analysis": "Keşifsel analiz",
    "Agent-authored · exploratory evidence": "Agent yazdı · keşifsel kanıt",
    "Agent-authored · host-scored · exploratory": "Agent yazdı · sistem skorladı · keşifsel",
    "Agent-authored model experiment": "Agent'ın yazdığı model denemesi",
    "Authored experiment": "Yazılmış deney",
    "Agent declared": "Agent beyan etti",
    "Missing values": "Eksik değerler",
    "Target distribution": "Hedef dağılımı",
    "Correlations": "Korelasyonlar",
    "Leakage audit": "Sızıntı denetimi",
    "Evaluation result": "Değerlendirme sonucu",
    "Model comparison": "Model karşılaştırması",
    "Candidate problems": "Aday problemler",
    "Schema relationships": "Şema ilişkileri",
    "Split strategy": "Bölme stratejisi",
    "Feature pipeline": "Öznitelik hattı",
    # --- recurring sentences
    "Aggregates data from the holdout period": "Holdout dönemindeki veriyi de topluyor",
    "Candidate comparison and the diagnostics that say whether to trust it.": (
        "Aday karşılaştırması ve sonuca güvenilip güvenilmeyeceğini söyleyen tanılar."
    ),
    "The selected model compared with its baseline, including alerts and decision history.": (
        "Seçilen modelin referans modelle karşılaştırması, uyarılar ve karar geçmişiyle birlikte."
    ),
    "Additional analysis written by the local agent and executed against a ": (
        "Yerel agent tarafından yazılmış ve şu veriye karşı çalıştırılmış ek analiz: "
    ),
    "At this ratio accuracy is misleading — a model predicting the majority ": (
        "Bu oranda doğruluk yanıltıcıdır — çoğunluğu tahmin eden bir model "
    ),
    # --- units and connectors that appear inside composed sentences
    "rows": "satır",
    "columns": "sütun",
    "of {column} is missing": "{column} sütununun {pct}'i eksik",
    "no candidate target measured": "ölçülmüş hedef adayı yok",
}

_TR.update(
    {
        "Exploratory analysis": "Keşifsel analiz",
        "Agent-authored · exploratory evidence": "Agent yazdı · keşifsel kanıt",
        "Agent-authored · host-scored · exploratory": "Agent yazdı · sistem skorladı · keşifsel",
        "Agent-authored model experiment": "Agent'ın yazdığı model denemesi",
        "Authored experiment": "Yazılmış deney",
        "Agent declared": "Agent beyan etti",
        "Host measured": "Sistem ölçtü",
        "Class balance": "Sınıf dengesi",
        "Candidate comparison": "Aday karşılaştırması",
        "Correlation heatmap": "Korelasyon ısı haritası",
        "Cross-validation stability": "Çapraz doğrulama kararlılığı",
        "Entity isolation": "Varlık izolasyonu",
        "Feature relationships": "Öznitelik ilişkileri",
        "Group column": "Gruplama sütunu",
        "Holdout cutoff": "Holdout kesim noktası",
        "Holdout fraction": "Holdout oranı",
        "Holdout performance": "Holdout başarımı",
        "Aggregates data from the holdout period": "Holdout dönemindeki veriyi de topluyor",
        "Correlates with the target almost perfectly": "Hedefle neredeyse birebir örtüşüyor",
        "Candidate comparison and the diagnostics that say whether to trust it.": (
            "Aday karşılaştırması ve sonuca güvenilip güvenilmeyeceğini söyleyen tanılar."
        ),
    }
)

_TR.update(
    {
        "Adjusted MI": "Düzeltilmiş MI",
        "Baseline": "Referans",
        "Blocking": "Engelleyici",
        "Column A": "Sütun A",
        "Column B": "Sütun B",
        "Correlation": "Korelasyon",
        "Distinct": "Farklı",
        "Evidence": "Kanıt",
        "Leakage audit": "Sızıntı denetimi",
        "Leakage score": "Sızıntı skoru",
        "Lift over baseline": "Referansa göre kazanç",
        "Lower fence": "Alt sınır",
        "Missing rows": "Eksik satır",
        "Missing share": "Eksik oranı",
        "Missing values": "Eksik değerler",
        "Model family": "Model ailesi",
        "Naive baseline": "Basit referans",
        "Observed": "Gözlenen",
        "Outlier share": "Aykırı değer oranı",
        "Outliers": "Aykırı değerler",
        "Protection": "Koruma",
        "Protection provided": "Sağlanan koruma",
        "Provided by strategy": "Stratejinin sağladığı",
        "Required by data": "Verinin gerektirdiği",
        "Selected": "Seçilen",
        "Sensitivity": "Hassasiyet",
        "Split protection": "Bölme koruması",
        "Split setup": "Bölme kurulumu",
        "Statistic": "İstatistik",
        "Std deviation": "Standart sapma",
        "Strategy": "Strateji",
        "Target distribution": "Hedef dağılımı",
        "Threshold": "Eşik",
        "Time column": "Zaman sütunu",
        "Training": "Eğitim",
        "Upper fence": "Üst sınır",
        "Usable rows": "Kullanılabilir satır",
        "Its missingness alone predicts the target": (
            "Yalnızca eksikliği bile hedefi tahmin ediyor"
        ),
        "Separates the target classes perfectly": (
            "Hedef sınıflarını kusursuz ayırıyor"
        ),
        "No missing values in any column.": "Hiçbir sütunda eksik değer yok.",
        "No missing values were measured in any column.": "Hiçbir sütunda eksik değer ölçülmedi.",
        "Missingness is present but every column is below the 10% threshold.": (
            "Eksiklik var ama her sütun %10 eşiğinin altında."
        ),
        "No feature pair reaches the 0.90 correlation threshold.": (
            "Hiçbir öznitelik çifti 0.90 korelasyon eşiğine ulaşmıyor."
        ),
        "No values fall outside the 1.5×IQR fences.": (
            "Hiçbir değer 1.5×IQR sınırlarının dışında değil."
        ),
        "Some values sit outside the fences, but no column exceeds 5%.": (
            "Bazı değerler sınırların dışında ama hiçbir sütun %5'i aşmıyor."
        ),
        "Share of each target class among observed rows.": (
            "Gözlenen satırlar içinde her hedef sınıfının payı."
        ),
        "Share of rows with no value, per column.": (
            "Sütun başına değeri olmayan satır oranı."
        ),
        "Pearson correlation between every pair of numeric columns.": (
            "Her sayısal sütun çifti arasındaki Pearson korelasyonu."
        ),
        "Quartiles and 1.5×IQR fences per numeric column, with the share beyond them.": (
            "Sayısal sütun başına çeyreklikler ve 1.5×IQR sınırları, dışında kalan oranla birlikte."
        ),
        "How the rows are divided, and how many survive the target filter.": (
            "Satırların nasıl bölündüğü ve hedef filtresinden kaçının geçtiği."
        ),
    }
)

_TR.update(
    {
        "This experiment cannot replace the deterministic winner or affect a gate.": (
            "Bu deney deterministik kazananın yerine geçemez ve bir kapıyı etkileyemez."
        ),
        "An isolated development experiment scored by the host on withheld labels.": (
            "Host'un, saklanan etiketler üzerinde puanladığı yalıtılmış bir "
            "geliştirme deneyi."
        ),
    }
)

_TR.update(
    {
        (
            "Additional analysis written by the local agent and executed against a "
            "read-only data copy. It cannot affect gates."
        ): (
            "Yerel agent tarafından yazılmış ve salt-okunur bir veri kopyasına karşı "
            "çalıştırılmış ek analiz. Kapıları etkileyemez."
        ),
        (
            "This result is exploratory. Review its proposed interpretation and verification "
            "question before relying on it."
        ): (
            "Bu sonuç keşifseldir. Güvenmeden önce önerilen yorumunu ve doğrulama sorusunu "
            "gözden geçirin."
        ),
        (
            "Each fold keeps the observed class proportions, so per-fold scores are "
            "comparable to each other."
        ): (
            "Her katlama gözlenen sınıf oranlarını korur, dolayısıyla katlama skorları "
            "birbiriyle karşılaştırılabilir."
        ),
        (
            "No repeated entities and no multi-period time span were measured, so no "
            "structural protection is strictly required."
        ): (
            "Tekrarlayan varlık ve çok dönemli zaman aralığı ölçülmedi, dolayısıyla "
            "yapısal bir koruma zorunlu değil."
        ),
        (
            "Every metric measured on the held-out split, scored once after the model was selected."
        ): (
            "Ayrılmış bölmede ölçülen tüm metrikler; model seçildikten sonra bir kez skorlandı."
        ),
        (
            "The same entity cannot appear in both training and evaluation. Without it a model "
            "can memorise an individual and be scored on that same individual."
        ): (
            "Aynı varlık hem eğitimde hem değerlendirmede bulunamaz. Bu koruma olmadan model bir "
            "bireyi ezberleyip aynı birey üzerinden skorlanabilir."
        ),
        (
            "Training data never comes from later than evaluation data. Without it the model "
            "has seen the future and the score is unachievable in production."
        ): (
            "Eğitim verisi hiçbir zaman değerlendirme verisinden daha yeni olamaz. Bu koruma "
            "olmadan model geleceği görmüştür ve skor üretimde tekrarlanamaz."
        ),
        (
            "Strategies are not ranked. Each prevents a different leak, so the question is "
            "whether the chosen one covers what the data actually needs."
        ): (
            "Stratejiler bir sıralama değildir. Her biri farklı bir sızıntıyı engeller; asıl "
            "soru seçilenin verinin gerçekten ihtiyaç duyduğunu kapsayıp kapsamadığıdır."
        ),
        (
            "The selected model against a naive baseline on the same holdout. A model that "
            "cannot beat the baseline has learned nothing useful."
        ): (
            "Seçilen modelin aynı holdout üzerinde basit bir referansla karşılaştırması. "
            "Referansı geçemeyen bir model işe yarar hiçbir şey öğrenmemiştir."
        ),
        (
            "Four independent leakage families are measured against every feature. A high "
            "score is not proof of leakage, but it is a reason to look."
        ): (
            "Her özniteliğe karşı dört bağımsız sızıntı ailesi ölçülür. Yüksek skor "
            "sızıntının kanıtı değildir ama bakmak için bir sebeptir."
        ),
        (
            "Measured association between each feature and the target. These are aggregate "
            "statistics — no individual rows are plotted."
        ): (
            "Her öznitelik ile hedef arasında ölçülen ilişki. Bunlar toplu istatistiklerdir — "
            "hiçbir satır tek tek çizilmez."
        ),
        (
            "Code ran against copied development data. The host scored its predictions against "
            "labels withheld from the execution environment."
        ): (
            "Kod, kopyalanmış geliştirme verisi üzerinde çalıştı. Tahminlerini, çalışma "
            "ortamından saklanan etiketlere karşı sistem skorladı."
        ),
    }
)

_TR.update(
    {
        "{n} feature pair(s) are correlated at or above ": (
            "{n} öznitelik çifti şu eşikte ya da üzerinde korele: "
        ),
        "{n} strong pair(s)": "{n} güçlü çift",
        "no strong correlation": "güçlü korelasyon yok",
        "{n} column(s) with outliers": "{n} sütunda aykırı değer",
        "{n} near-perfect predictor(s)": "{n} neredeyse kusursuz yordayıcı",
        "{n} feature(s) measured": "{n} öznitelik ölçüldü",
        "{r}:1 majority to minority": "{r}:1 çoğunluk/azınlık",
        "one row per {k}": "{k} başına bir satır",
    }
)

_TR.update(
    {
        # --- report headers and sections
        "Evaluation report": "Değerlendirme raporu",
        "This evaluation is not clear to ship.": "Bu değerlendirme yayına hazır değil.",
        "The selected model did not demonstrate lift over the baseline.": (
            "Seçilen model referansa göre bir kazanç göstermedi."
        ),
        "Blocking leakage remains unresolved for {columns}.": (
            "{columns} için engelleyici sızıntı çözülmedi."
        ),
        "Separator provenance remains unconfirmed for {columns}; the model is not leakage-clean.": (
            "{columns} için ayrıştırıcı kökeni doğrulanmadı; model sızıntıdan arındırılmış değil."
        ),
        "Decisions and escalations": "Kararlar ve yükseltmeler",
        "No gate decision history was attached to this evaluation artifact.": (
            "Bu değerlendirme çıktısına kapı karar geçmişi eklenmedi."
        ),
        "human-approved": "insan onaylı",
        "autonomous": "otonom",
        "human approval not recorded": "insan onayı kaydedilmedi",
        "Human-approved": "İnsan onaylı",
        "Autonomous": "Otonom",
        "Approval not recorded": "Onay kaydedilmedi",
        "none": "yok",
        "yes": "evet",
        "no": "hayır",
        "the defined outcome": "tanımlanan sonuç",
        (
            "The persisted model is the same fitted pipeline that produced the holdout "
            "measurement. It was not silently refit on the holdout rows."
        ): (
            "Kayıtlı model, holdout ölçümünü üreten eğitilmiş hattın aynısıdır. "
            "Holdout satırları üzerinde sessizce yeniden eğitilmemiştir."
        ),
        (
            "The persisted model was refit after holdout evaluation. The holdout "
            "metric does not directly measure that refitted artifact."
        ): (
            "Kayıtlı model, holdout değerlendirmesinden sonra yeniden eğitilmiştir. "
            "Holdout metriği bu yeniden eğitilen modeli doğrudan ölçmez."
        ),
        "No persisted model blob is referenced by this evaluation artifact.": (
            "Bu değerlendirme çıktısı tarafından referans verilen kayıtlı bir model "
            "ikili dosyası yok."
        ),
        "Problem and model": "Problem ve model",
        (
            "The model predicts {target} for a {task} problem. **{winner}** was "
            "selected from {count} candidates using {metric}."
        ): (
            "Model bir {task} problemi için {target} sütununu tahmin eder. "
            "**{winner}**, {count} aday arasından {metric} kullanılarak seçildi."
        ),
        "Performance against the baseline": "Referans modele göre başarım",
        (
            "On the untouched holdout, the winner scored **{winner}** versus **{baseline}** "
            "for the naive baseline: an improvement of **{delta}** "
            "({direction} is better for {metric})."
        ): (
            "Dokunulmamış holdout üzerinde kazanan model **{winner}**, basit referans ise "
            "**{baseline}** skorunu aldı: **{delta}** kadar bir iyileşme "
            "({metric} için {direction} daha iyidir)."
        ),
        (
            "On the untouched holdout, the winner and naive baseline both scored **{winner}**. "
            "The model showed **no improvement over the baseline**."
        ): (
            "Dokunulmamış holdout üzerinde kazanan model ve basit referansın ikisi de "
            "**{winner}** skorunu aldı. Model **referansa göre hiçbir gelişme göstermedi**."
        ),
        (
            "On the untouched holdout, the selected model scored **{winner}** versus "
            "**{baseline}** for the naive baseline. It was **worse than the baseline by "
            "{delta}** on the oriented {metric} scale."
        ): (
            "Dokunulmamış holdout üzerinde seçilen model **{winner}**, basit referans ise "
            "**{baseline}** skorunu aldı. Yönlendirilmiş {metric} ölçeğinde **referanstan "
            "{delta} kadar daha kötüydü**."
        ),
        "lower": "daha düşük",
        "higher": "daha yüksek",
        (
            "Training received {input_rows:,} rows, dropped {dropped_rows:,} rows with no target, "
            "and evaluated {train_rows:,} labeled rows."
        ): (
            "Eğitim için {input_rows:,} satır alındı, hedefi olmayan {dropped_rows:,} satır "
            "elendi ve etiketli {train_rows:,} satır değerlendirildi."
        ),
        "Candidate": "Aday",
        "CV mean": "ÇD ortalaması",
        "CV std": "ÇD standart sapması",
        "Holdout": "Holdout",
        "Winner holdout metrics": "Kazanan modelin holdout metrikleri",
        "Validation strategy": "Doğrulama stratejisi",
        (
            "The run used **{strategy}** validation with {folds} inner folds and a "
            "{holdout:.0%} outer holdout."
        ): (
            "Koşuda {folds} iç katlamalı ve %{holdout:.0%} dış holdout'lu **{strategy}** "
            "doğrulaması kullanıldı."
        ),
        "Leakage controls": "Sızıntı kontrolleri",
        "The following audited leakage findings were cleared by exclusion:": (
            "Aşağıdaki denetlenen sızıntı bulguları hariç tutularak temizlendi:"
        ),
        "No audited leakage finding was recorded as cleared by exclusion.": (
            "Hariç tutularak temizlendiği kaydedilen bir denetimli sızıntı bulgusu yok."
        ),
        "Non-blocking leakage-audit warnings remain:": (
            "Engelleyici olmayan sızıntı denetimi uyarıları mevcut:"
        ),
        "score": "skor",
        "Decision provenance": "Karar kaynağı",
        "Other gate outcomes": "Diğer kapı sonuçları",
        "Stage": "Aşama",
        "Attempt": "Deneme",
        "Verdict": "Karar",
        "Reason code": "Neden kodu",
        "Rules fired": "Çalışan kurallar",
        "Authority": "Yetki",
        # --- Stage human views and descriptions
        "Your decision is needed": "Kararınız gerekiyor",
        "In progress": "Devam ediyor",
        "No action needed": "İşlem gerekmiyor",
        "Load and deterministically profile every source table.": (
            "Tüm kaynak tabloları yükleyin ve kurallı olarak profillemesini çıkarın."
        ),
        (
            "Planner agent interprets relationships into an integration plan; a separate "
            "advisory pass proposes source comprehension from measured evidence."
        ): (
            "Planlayıcı ajan ilişkileri birleştirme planına dönüştürür; ayrı bir danışma adımı "
            "ölçülen kanıtlardan kaynak kavrayışı önerir."
        ),
        "Execute the proposed plan and verify analytical-base-table grain.": (
            "Önerilen planı çalıştırın ve analitik temel tablo tanecik yapısını doğrulayın."
        ),
        (
            "An active scout chooses measurements or read-only code, then the planner proposes "
            "problems with host-measured feasibility support."
        ): (
            "Aktif bir öncü ölçümleri veya salt-okunur kodu seçer, ardından planlayıcı sistem "
            "tarafından ölçülen uygulanabilirlik desteğiyle problemleri önerir."
        ),
        (
            "An active scout trials candidate splits; the exact final strategy is re-executed and "
            "fingerprint-bound to executor-owned diagnostics."
        ): (
            "Aktif bir öncü aday bölmeleri dener; nihai kesin strateji yeniden çalıştırılır ve "
            "yürütücüye ait tanılara parmak iziyle bağlanır."
        ),
        (
            "Deterministic split execution, fitting, and leakage-auditing on "
            "candidate features."
        ): (
            "Aday öznitelikler üzerinde kurallı bölme çalıştırma, uydurma ve sızıntı denetimi."
        ),
        "Autonomous training of baseline and candidate estimators across folds.": (
            "Katlamalar boyunca referans ve aday tahminleyicilerin otonom eğitimi."
        ),
        "Holdout evaluation, metric verification, and human gate decision packaging.": (
            "Holdout değerlendirmesi, metrik doğrulaması ve insan kapısı karar paketlemesi."
        ),
        "Compile full auditable report and export artifacts.": (
            "Tam denetlenebilir raporu derleyin ve çıktıları dışa aktarın."
        ),
        "Deterministic data intake and profiling of source tables.": (
            "Kaynak tabloların kurallı veri alımı ve profillemesi."
        ),
        "Local planning agent": "Yerel planlayıcı ajan",
        "Python executor": "Python yürütücüsü",
        "Agent-planned": "Ajan planlı",
        "Manual plan": "Manuel plan",
        "Planner agents discover schema, problem, and validation.": (
            "Planlayıcı ajanlar şema, problem ve doğrulamayı keşfeder."
        ),
        "Use the problem and validation choices configured here.": (
            "Burada yapılandırılan problem ve doğrulama seçeneklerini kullanın."
        ),
        "Load/profile source tables and persist approved run inputs.": (
            "Kaynak tabloları yükleyin/profillendirin ve onaylanan koşu girdilerini kaydedin."
        ),
        "Execute the approved integration plan and verify ABT grain.": (
            "Onaylanan birleştirme planını çalıştırın ve analitik temel tablo "
            "tanecik yapısını doğrulayın."
        ),
        "Profile the analytical base table for the confirmed problem.": (
            "Onaylanan problem için analitik temel tablonun profilini çıkarın."
        ),
        (
            "Profile the analytical base table, then run a separate advisory "
            "pass over the measured descriptors."
        ): (
            "Analitik temel tablonun profilini çıkarın, ardından ölçülen betimleyiciler "
            "üzerinde ayrı bir danışma adımı çalıştırın."
        ),
        "Detect leakage and apply mechanical feature-drop corrections.": (
            "Sızıntıyı tespit edin ve mekanik öznitelik çıkarma düzeltmelerini uygulayın."
        ),
        (
            "Run the mandatory leakage floor, then let an agent request a registered "
            "falsifiable challenge without clearing the gate itself."
        ): (
            "Zorunlu sızıntı taban denetimini çalıştırın, ardından ajanın kapıyı kendisi "
            "geçirmeden kayıtlı ve yanlışlanabilir bir sınama istemesine izin verin."
        ),
        "Declare executor-owned feature routing for fold-local fitting.": (
            "Katlama-yerel uydurma için yürütücüye ait öznitelik yönlendirmesini bildirin."
        ),
        (
            "Persist the mandatory fold-local feature floor, then optionally run "
            "one isolated, host-scored authored feature experiment."
        ): (
            "Zorunlu katlama-yerel öznitelik tabanını kalıcılaştırın, ardından isteğe "
            "bağlı olarak sistem tarafından skorlanan tek bir yalıtılmış öznitelik "
            "deneyini çalıştırın."
        ),
        "Build the selected split and measure retained support.": (
            "Seçilen bölmeyi oluşturun ve elde tutulan desteği ölçün."
        ),
        (
            "Fit the deterministic candidate menu, then optionally run one isolated, "
            "host-scored agent-authored development experiment."
        ): (
            "Kurallı aday menüsünü eğitin, ardından isteğe bağlı olarak sistem tarafından "
            "skorlanan tek bir yalıtılmış ajan geliştirme deneyini çalıştırın."
        ),
        "Assemble holdout, baseline, leakage, split, and gate evidence.": (
            "Holdout, referans, sızıntı, bölme ve kapı kanıtlarını bir araya getirin."
        ),
        "Render the complete auditable Markdown report.": (
            "Eksiksiz denetlenebilir Markdown raporunu oluşturun."
        ),
        # --- Artifact Presentation
        "A durable result produced by this stage.": (
            "Bu aşama tarafından üretilen kalıcı sonuç."
        ),
        "Profiled table: {name}": "Profili çıkarılan tablo: {name}",
        "dataset": "veri seti",
        "Schema, size, and quality statistics; no source rows are shown.": (
            "Şema, boyut ve kalite istatistikleri; hiçbir kaynak satır gösterilmez."
        ),
        "Data integration plan": "Veri birleştirme planı",
        "How source tables are aggregated and joined into one analytical table.": (
            "Kaynak tabloların tek bir analitik tabloda nasıl toplulaştırılıp birleştirildiği."
        ),
        "Integration plan trial": "Birleştirme planı denemesi",
        (
            "Measured result of executing the proposed joins and aggregations on a copy."
        ): (
            "Önerilen birleştirmelerin ve toplulaştırmaların bir kopya üzerinde "
            "çalıştırılmasının ölçülen sonucu."
        ),
        "Candidate analysis problems": "Aday analiz problemleri",
        "Problems proposed by the planner and checked against measured support.": (
            "Planlayıcı tarafından önerilen ve ölçülen destekle kontrol edilen problemler."
        ),
        "Selected problem": "Seçilen problem",
        "The target, task, and success metric used downstream.": (
            "Sonraki aşamalarda kullanılan hedef, görev ve başarı metriği."
        ),
        "Validation plan": "Doğrulama planı",
        "How training and holdout data are separated to keep evaluation honest.": (
            "Değerlendirmenin dürüst kalması için eğitim ve holdout verisinin nasıl ayrıldığı."
        ),
        (
            "Features checked for information that would make model results "
            "unrealistically good."
        ): (
            "Model sonuçlarını gerçek dışı derecede iyi gösterecek bilgiler için "
            "taranan öznitelikler."
        ),
        "Features checked": "Taranan öznitelikler",
        "Findings": "Bulgular",
        "Clean": "Temiz",
        (
            "Candidate models compared by cross-validation and untouched "
            "holdout performance."
        ): (
            "Çapraz doğrulama ve dokunulmamış holdout başarımına göre "
            "karşılaştırılan aday modeller."
        ),
        "Winner": "Kazanan",
        "Holdout score": "Holdout skoru",
        "Training rows": "Eğitim satırları",
        "Score": "Skor",
        "Evaluation rows": "Değerlendirme satırları",
        "Baseline improvement": "Referansa göre iyileşme",
        "Alerts": "Uyarılar",
        "Final auditable report": "Nihai denetlenebilir rapor",
        (
            "The human-readable handoff tying conclusions to the exact evaluation evidence."
        ): (
            "Sonuçları kesin değerlendirme kanıtlarına bağlayan insan tarafından "
            "okunabilir teslim belgesi."
        ),
        "Report length": "Rapor uzunluğu",
        "Agent execution audit": "Ajan çalıştırma denetimi",
        "Contract validation, panel agreement, and deterministic tool provenance.": (
            "Sözleşme doğrulaması, panel uzlaşması ve kurallı araç kaynak izi."
        ),
        "Agent": "Ajan",
        "Panel": "Panel",
        "Valid": "Geçerli",
        "Agreement": "Uzlaşma",
        "Pydantic": "Pydantic",
        "Tools": "Araçlar",
        "Skills": "Yetenekler",
        "Exploratory data findings": "Keşifsel veri bulguları",
        "Measured distributions, missingness, and relationships relevant to the problem.": (
            "Problemle ilgili ölçülen dağılımlar, eksiklikler ve ilişkiler."
        ),
        "Agent-authored exploratory analysis": "Ajan yazımı keşifsel analiz",
        "Validated exploratory output produced by locally executed code.": (
            "Yerel olarak çalıştırılan kodun ürettiği doğrulanmış keşifsel çıktı."
        ),
        "Evidence class": "Kanıt sınıfı",
        "Chart": "Grafik",
        "Tool calls": "Araç çağrıları",
        "Base table": "Temel tablo",
        "Joins": "Birleştirmeler",
        "Aggregations": "Toplulaştırmalar",
        "Base rows": "Temel satırlar",
        "Result rows": "Sonuç satırları",
        "Grain preserved": "Tanecik yapısı korundu",
        "Result columns": "Sonuç sütunları",
        "Candidates": "Adaylar",
        "Viable": "Uygulanabilir",
        "Task": "Görev",
        "Target": "Hedef",
        "Metric": "Metrik",
        "Group": "Grup",
        "Time": "Zaman",
        "Rows": "Satır",
        "Columns": "Sütun",
        "Candidate keys": "Aday anahtarlar",
        # --- Story Suggestions & Quality Checks
        (
            "Use {base_table} at one row per {base_grain}; apply {n_aggs} "
            "aggregation(s) before {n_joins} join(s)."
        ): (
            "{base_table} tablosunu her {base_grain} için bir satır olarak kullanın; "
            "{n_joins} birleştirmeden önce {n_aggs} toplulaştırma uygulayın."
        ),
        "Interpretation was not produced.": "Yorum üretilemedi.",
        (
            "The proposed plan was executed by the deterministic integration engine "
            "before it was accepted."
        ): (
            "Önerilen plan kabul edilmeden önce kurallı birleştirme motoru tarafından çalıştırıldı."
        ),
        "The planner ranked these analysis problems.": (
            "Planlayıcı bu analiz problemlerini sıraladı."
        ),
        (
            "Proceed with “{title}” as a {task_type} problem, predicting "
            "{target_column} and measuring {primary_metric}."
        ): (
            "“{title}” problemini bir {task_type} problemi olarak yürütün, "
            "{target_column} sütununu tahmin edin ve {primary_metric} ile ölçün."
        ),
        "Use {strategy} validation with {n_folds} folds.": (
            "{n_folds} katlamalı {strategy} doğrulaması kullanın."
        ),
        "Use {strategy} validation with {n_folds} folds, grouped by {group_column}.": (
            "{group_column} sütununa göre gruplanmış, {n_folds} katlamalı {strategy} "
            "doğrulaması kullanın."
        ),
        "Use {strategy} validation with {n_folds} folds, ordered by {time_column}.": (
            "{time_column} sütununa göre sıralanmış, {n_folds} katlamalı {strategy} "
            "doğrulaması kullanın."
        ),
        (
            "Use {strategy} validation with {n_folds} folds, grouped by {group_column}, "
            "ordered by {time_column}."
        ): (
            "{group_column} sütununa göre gruplanmış ve {time_column} sütununa göre sıralanmış, "
            "{n_folds} katlamalı {strategy} doğrulaması kullanın."
        ),
        "Do not train yet; correct or exclude the blocking features.": (
            "Henüz eğitime geçmeyin; engelleyici öznitelikleri düzeltin veya hariç tutun."
        ),
        "No blocking leakage was detected; training may proceed.": (
            "Engelleyici bir sızıntı tespit edilmedi; eğitim aşamasına geçilebilir."
        ),
        (
            "Start by understanding {target}: inspect its distribution, then review missing "
            "fields and the strongest measured relationships before accepting any "
            "modelling direction."
        ): (
            "Öncelikle {target} hedefini anlamakla başlayın: dağılımını inceleyin, ardından "
            "herhangi bir modelleme yönünü kabul etmeden önce eksik alanları ve ölçülen "
            "en güçlü ilişkileri gözden geçirin."
        ),
        "Review coverage, missingness, correlations, and outliers before modelling.": (
            "Modellemeden önce kapsama oranını, eksiklikleri, korelasyonları ve "
            "aykırı değerleri gözden geçirin."
        ),
        "Target distribution measured": "Hedef dağılımı ölçüldü",
        "Every feature covered": "Tüm öznitelikler kapsandı",
        "Missingness measured": "Eksiklik ölçüldü",
        (
            "{count} column(s) have at least 10% missing values; "
            "confirm how they should be handled."
        ): (
            "{count} sütunda en az %10 eksik değer var; nasıl ele alınacağını onaylayın."
        ),
        (
            "{count} feature(s) have |correlation| at or above 0.90; "
            "treat these as leakage candidates until audited."
        ): (
            "{count} öznitelikte |korelasyon| 0.90 veya üzerinde; denetlenene kadar "
            "bunları sızıntı adayı sayın."
        ),
        "Treat this as a proposed extension to the mandatory EDA, not as gate evidence.": (
            "Bunu zorunlu keşifsel analize önerilen bir uzantı olarak değerlendirin, "
            "kapı kanıtı olarak değil."
        ),
        "Select {winner} from {count} compared candidates using {metric}.": (
            "Karşılaştırılan {count} aday arasından {metric} kullanarak {winner} modelini seçin."
        ),
        (
            "Review this as a proposed development experiment; it did not use the final "
            "holdout and cannot change the selected model."
        ): (
            "Bunu önerilen bir geliştirme denemesi olarak inceleyin; nihai holdout verisini "
            "kullanmamıştır ve seçilen modeli değiştiremez."
        ),
        (
            "The selected {winner} scored {score} on holdout; "
            "baseline improvement was {delta}."
        ): (
            "Seçilen {winner} holdout üzerinde {score} skorunu aldı; "
            "referansa göre iyileşme {delta} oldu."
        ),
        "The final report is ready for review and export.": (
            "Nihai rapor inceleme ve dışa aktarım için hazır."
        ),
        (
            "Review panel agreement and validation evidence before trusting "
            "this agent-authored contract."
        ): (
            "Ajan tarafından yazılan bu sözleşmeye güvenmeden önce panel uzlaşmasını "
            "ve doğrulama kanıtlarını gözden geçirin."
        ),
        "Pydantic output contract": "Pydantic çıktı sözleşmesi",
        "Row access matches the agent role": "Satır erişimi ajan rolüyle eşleşiyor",
        "Local investigator may read a read-only copy; output remains typed.": (
            "Yerel araştırmacı salt-okunur bir kopyayı okuyabilir; çıktılar tipli kalır."
        ),
        "Planner context contains schema and aggregate measurements only.": (
            "Planlayıcı bağlamı yalnızca şema ve toplu ölçümleri içerir."
        ),
        "Evidence tools were allowlisted": "Kanıt araçları izin listesindeydi",
        # --- Panels Insights, Captions, Headers
        (
            "{pct} of {column} is missing ({missing_rows:,} rows), so those rows cannot "
            "be trained on or scored against."
        ): (
            "{column} sütununun {pct}'i eksik ({missing_rows:,} satır), bu yüzden o satırlar "
            "üzerinde eğitim yapılamaz veya skorlanamaz."
        ),
        "The most frequent class is {value} at {pct}.": (
            "En sık görülen sınıf {pct} oranıyla {value}."
        ),
        "Count": "Sayı",
        "How the target {column} is distributed across observed rows.": (
            "Hedef {column} sütununun gözlenen satırlara nasıl dağıldığı."
        ),
        "Class": "Sınıf",
        "Share": "Pay",
        "Total": "Toplam",
        "Values range {min_val} to {max_val} with a standard deviation of {spread}.": (
            "Değerler {min_val} ile {max_val} arasında değişiyor, standart sapma {spread}."
        ),
        "median {val}": "medyan {val}",
        "How the numeric target {column} is distributed across observed rows.": (
            "Sayısal hedef {column} sütununun gözlenen satırlara nasıl dağıldığı."
        ),
        "Minimum": "Minimum",
        "25th percentile": "25. yüzdelik",
        "Median": "Medyan",
        "75th percentile": "75. yüzdelik",
        "Maximum": "Maksimum",
        "Mean": "Ortalama",
        "{count} column(s) are at or above {threshold} missing: {columns}.": (
            "{count} sütun %{threshold} veya üzerinde eksik: {columns}."
        ),
        (
            "Decide per column whether to impute, drop the column, or drop the rows — "
            "each choice changes what the model can be used for."
        ): (
            "Sütun bazında doldurma mı yapılacağına, sütunun mu atılacağına yoksa satırların mı "
            "silineceğine karar verin — her seçenek modelin kullanım amacını değiştirir."
        ),
        "{count} column(s) over {threshold}": "{count} sütun %{threshold} üzerinde",
        "all columns under 10%": "tüm sütunlar %10 altında",
        "no missing values": "eksik değer yok",
        "Column": "Sütun",
        (
            "{count} column(s) have more than {threshold} of values outside "
            "the 1.5×IQR fences: {columns}."
        ): (
            "{count} sütunun değerlerinin %{threshold}'inden fazlası 1.5×IQR sınırlarının "
            "dışında: {columns}."
        ),
        (
            "Far values are not automatically errors. Confirm whether they are real "
            "before clipping — removing genuine extremes biases the model toward the middle."
        ): (
            "Uzak değerler otomatik olarak hata sayılmaz. Kırpmadan önce gerçek olup "
            "olmadıklarını doğrulayın — gerçek uç değerleri çıkarmak modeli merkeze doğru "
            "yanlı hale getirir."
        ),
        (
            "Ranked by absolute Pearson correlation with the target. Adjusted mutual "
            "information is shown alongside because it also catches non-linear "
            "association that correlation misses entirely."
        ): (
            "Hedefle mutlak Pearson korelasyonuna göre sıralanmıştır. Korelasyonun tamamen "
            "kaçırdığı doğrusal olmayan ilişkileri de yakaladığı için düzeltilmiş karşılıklı "
            "bilgi (AMI) de yanında gösterilir."
        ),
        (
            "{count} feature(s) correlate with {target} at or above {threshold:.2f}. "
            "A feature that predicts the target almost perfectly is usually leakage "
            "rather than a finding."
        ): (
            "{count} öznitelik {target} ile {threshold:.2f} veya üzerinde korele. "
            "Hedefi neredeyse kusursuz tahmin eden bir öznitelik bir bulgu değil, "
            "genellikle sızıntıdır."
        ),
        "|correlation| with {target}": "{target} ile |korelasyon|",
        "the target": "hedef",
        "Feature": "Öznitelik",
        "The majority class {majority} outnumbers {minority} by {ratio:.1f}:1.": (
            "Çoğunluk sınıfı {majority}, {minority} sınıfından {ratio:.1f}:1 oranında fazla."
        ),
        (
            "At this ratio accuracy is misleading — a model predicting the majority "
            "class every time would score {rate}. Judge it on the minority class."
        ): (
            "Bu oranda doğruluk yanıltıcıdır — her seferinde çoğunluk sınıfını tahmin "
            "eden bir model {rate} skor alırdı. Modeli azınlık sınıfı üzerinden değerlendirin."
        ),
        "no column or combination was measured unique, so one row's identity is unclear": (
            "hiçbir sütun veya kombinasyon benzersiz ölçülmedi, bir satırın kimliği belirsiz"
        ),
        "{n_rows:,} rows, {grain}. Columns: {composition}.": (
            "{n_rows:,} satır, {grain}. Sütunlar: {composition}."
        ),
        "Measured grain: {grain}.": "Ölçülen tanecik yapısı: {grain}.",
        "Measured grain: {grain}. {count} candidate key(s) in total: {keys}.": (
            "Ölçülen tanecik yapısı: {grain}. Toplamda {count} aday anahtar: {keys}."
        ),
        (
            "{count} column(s) classified as sensitive: {columns}. "
            "Values from these are never shown or sent to a model."
        ): (
            "{count} sütun hassas olarak sınıflandırıldı: {columns}. "
            "Bunlardaki değerler asla gösterilmez veya bir modele gönderilmez."
        ),
        "{count} quality issue(s) recorded during load: {details}.": (
            "Yükleme sırasında {count} kalite sorunu kaydedildi: {details}."
        ),
        (
            "{count} informational note(s) from loading — renames, inferred "
            "headers, detected delimiters. Recorded for provenance, not problems."
        ): (
            "Yüklemeden {count} bilgilendirme notu — yeniden adlandırmalar, çıkarılan "
            "başlıklar, algılanan ayırıcılar. Sorun olarak değil, kaynak takibi için kaydedildi."
        ),
        "Highest missingness is {column} at {rate}.": (
            "En yüksek eksiklik %{rate} ile {column} sütununda."
        ),
        "{n_rows:,} rows · {n_cols} cols · {n_issues} issue(s)": (
            "{n_rows:,} satır · {n_cols} sütun · {n_issues} sorun"
        ),
        "{n_rows:,} rows · {n_cols} cols": "{n_rows:,} satır · {n_cols} sütun",
        "Type": "Tür",
        (
            "{strategy} does not provide {gaps}, which the measured data requires. "
            "Scores from this split would be optimistic in a way no later stage can detect."
        ): (
            "{strategy}, ölçülen verinin gerektirdiği {gaps} korumasını sağlamıyor. "
            "Bu bölmeden elde edilen skorlar, sonraki aşamaların tespit edemeyeceği "
            "şekilde iyimser olacaktır."
        ),
        "{strategy} provides every protection the measured data requires.": (
            "{strategy}, ölçülen verinin gerektirdiği her korumayı sağlıyor."
        ),
        "{count} required protection(s) missing": "{count} zorunlu koruma eksik",
        "{strategy} covers what the data requires": (
            "{strategy} verinin gerektirdiğini karşılıyor"
        ),
        (
            "Strategies are not ranked. Each prevents a different leak, so the "
            "question is whether the chosen one covers what the data measured."
        ): (
            "Stratejiler sıralı değildir. Her biri farklı bir sızıntıyı engeller; asıl "
            "soru seçilenin verinin ölçtüğü ihtiyacı karşılayıp karşılamadığıdır."
        ),
        "{folds} folds, {holdout} holdout": "{folds} katlama, %{holdout} holdout",
        (
            "{usable:,} of {total:,} rows are usable; the rest have no target value "
            "and cannot be trained on or scored against."
        ): (
            "{total:,} satırdan {usable:,} tanesi kullanılabilir; geri kalanında hedef değer "
            "yoktur ve üzerinde eğitim veya skorlama yapılamaz."
        ),
        "Time column {column} spans {start} to {end} ({days:,} days).": (
            "{column} zaman sütunu {start} ile {end} arasını kapsıyor ({days:,} gün)."
        ),
        "Folds": "Katlamalar",
        "Development {metric}": "Geliştirme {metric}",
        (
            "Measured {metric} on {count} inner-development rows; "
            "the final holdout was not used."
        ): (
            "İç geliştirme {count} satırı üzerinde {metric} ölçüldü; nihai holdout kullanılmadı."
        ),
        "Result": "Sonuç",
        "{label} selected": "{label} seçildi",
        "Every candidate's holdout {metric}. {direction}.": (
            "Her adayın holdout {metric} skoru. {direction}."
        ),
        "Lower is better": "Düşük olan daha iyi",
        "Higher is better": "Yüksek olan daha iyi",
        "{label} was selected on holdout {metric} {score}.": (
            "{label}, holdout {metric} {score} skoruyla seçildi."
        ),
        (
            "Holdout is scored once, after selection, so it is not the number the "
            "candidates were chosen by — the cross-validation mean is."
        ): (
            "Holdout seçimden sonra yalnızca bir kez skorlanır, dolayısıyla adayların "
            "seçildiği sayı bu değildir — belirleyici olan çapraz doğrulama ortalamasıdır."
        ),
        "Model": "Model",
        "CV mean ({metric})": "ÇD ortalaması ({metric})",
        "CV spread": "ÇD dağılımı",
        "Holdout ({metric})": "Holdout ({metric})",
        "Role": "Rol",
        "selected": "seçilen",
        "baseline": "referans",
        "candidate": "aday",
        "Fold": "Katlama",
        "Fold {n}": "Katlama {n}",
        "spread {cov} of mean": "ortalamanın %{cov}'i kadar sapma",
        (
            "{name} scored on each validation fold. Consistency across folds is what makes "
            "a single holdout number believable."
        ): (
            "Her doğrulama katlamasında skorlanan {name}. Katlamalar arası tutarlılık, "
            "tek bir holdout sayısını inandırıcı kılan şeydir."
        ),
        "Fold scores range {min_val} to {max_val} around a mean of {mean_val}.": (
            "Katlama skorları {mean_val} ortalaması etrafında {min_val} ile "
            "{max_val} arasında değişiyor."
        ),
        (
            "That spread is over a quarter of the mean, so the reported score is "
            "unstable and the difference between candidates may be noise."
        ): (
            "Bu sapma ortalamanın dörtte birinden fazladır, bu nedenle bildirilen skor "
            "kararsızdır ve adaylar arasındaki fark gürültüden ibaret olabilir."
        ),
        (
            "The spread is small relative to the mean, so the score is "
            "stable across resampling."
        ): (
            "Sapma ortalamaya göre küçüktür, bu nedenle skor yeniden örneklemelerde kararlıdır."
        ),
        "beats baseline": "referansı geçiyor",
        "does not beat baseline": "referansı geçemiyor",
        (
            "The selected model against a naive baseline on the same holdout. A model "
            "that cannot beat the baseline has learned nothing worth deploying."
        ): (
            "Aynı holdout üzerinde basit bir referansa karşı seçilen model. Referansı "
            "geçemeyen bir model yayına almaya değer hiçbir şey öğrenmemiştir."
        ),
        "Selected {winner_score} versus baseline {baseline_score} on holdout {metric}.": (
            "Holdout {metric} metriğinde seçilen {winner_score} - referans {baseline_score}."
        ),
        (
            "The selected model scored {winner_score} against a baseline of {baseline_score}. "
            "It has not demonstrated value over guessing."
        ): (
            "Seçilen model, {baseline_score} referansına karşı {winner_score} aldı. "
            "Rastgele tahminden öte bir değer gösteremedi."
        ),
        "{count} metric(s) measured": "{count} metrik ölçüldü",
        "Primary metric {name} scored {score}.": "Birincil metrik {name}, {score} skorunu aldı.",
        "Baseline improvement was {delta}.": "Referansa göre iyileşme {delta} oldu.",
        "{count} alert(s) remain unresolved on this run.": (
            "Bu koşuda {count} uyarı henüz çözülmedi."
        ),
        (
            "{count} feature(s) block training. Each would make holdout "
            "results look better than anything achievable in production."
        ): (
            "{count} öznitelik eğitimi engelliyor. Her biri holdout sonuçlarını "
            "üretimde ulaşılabilecek olandan daha iyi gösterir."
        ),
        "{count} feature(s) were flagged but none block training.": (
            "{count} öznitelik işaretlendi ancak hiçbiri eğitimi engellemiyor."
        ),
        "All {checked} feature(s) passed every leakage family checked: {families}.": (
            "Taranan tüm {checked} öznitelik kontrol edilen her sızıntı "
            "ailesinden geçti: {families}."
        ),
        "{count} blocking of {checked} checked": (
            "Taranan {checked} öznitelikten {count} tanesi engelleyici"
        ),
        "{checked} feature(s) clean": "{checked} öznitelik temiz",
        (
            "Four independent leakage families are measured against every feature. "
            "A high score is not proof of leakage, but it is a reason not to trust "
            "the result until someone confirms the feature is available at "
            "prediction time."
        ): (
            "Her özniteliğe karşı dört bağımsız sızıntı ailesi ölçülür. Yüksek bir skor "
            "sızıntının kesin kanıtı değildir, ancak biri özniteliğin tahmin anında mevcut "
            "olduğunu doğrulayana kadar sonuca güvenmemek için bir nedendir."
        ),
        "Family": "Aile",
    }
)

_CATALOGUE: dict[str, dict[str, str]] = {"tr": _TR, "en": {}}

__all__ = ["DEFAULT", "SUPPORTED", "current", "normalise", "t", "using"]
