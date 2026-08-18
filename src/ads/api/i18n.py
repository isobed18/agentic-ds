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
    "Candidate comparison and the diagnostics that say whether to trust it.":
        "Aday karşılaştırması ve sonuca güvenilip güvenilmeyeceğini söyleyen tanılar.",
    "The selected model compared with its baseline, including alerts and decision history.":
        "Seçilen modelin referans modelle karşılaştırması, uyarılar ve karar geçmişiyle birlikte.",
    "Additional analysis written by the local agent and executed against a ":
        "Yerel agent tarafından yazılmış ve şu veriye karşı çalıştırılmış ek analiz: ",
    "At this ratio accuracy is misleading — a model predicting the majority ":
        "Bu oranda doğruluk yanıltıcıdır — çoğunluğu tahmin eden bir model ",
    # --- units and connectors that appear inside composed sentences
    "rows": "satır",
    "columns": "sütun",
    "of {column} is missing": "{column} sütununun {pct}'i eksik",
    "no candidate target measured": "ölçülmüş hedef adayı yok",
}

_TR.update({
    'Exploratory analysis': 'Keşifsel analiz',
    'Agent-authored · exploratory evidence': 'Agent yazdı · keşifsel kanıt',
    'Agent-authored · host-scored · exploratory': 'Agent yazdı · sistem skorladı · keşifsel',
    'Agent-authored model experiment': "Agent'ın yazdığı model denemesi",
    'Authored experiment': 'Yazılmış deney',
    'Agent declared': 'Agent beyan etti',
    'Host measured': 'Sistem ölçtü',
    'Class balance': 'Sınıf dengesi',
    'Candidate comparison': 'Aday karşılaştırması',
    'Correlation heatmap': 'Korelasyon ısı haritası',
    'Cross-validation stability': 'Çapraz doğrulama kararlılığı',
    'Entity isolation': 'Varlık izolasyonu',
    'Feature relationships': 'Öznitelik ilişkileri',
    'Group column': 'Gruplama sütunu',
    'Holdout cutoff': 'Holdout kesim noktası',
    'Holdout fraction': 'Holdout oranı',
    'Holdout performance': 'Holdout başarımı',
    'Aggregates data from the holdout period': 'Holdout dönemindeki veriyi de topluyor',
    'Correlates with the target almost perfectly': 'Hedefle neredeyse birebir örtüşüyor',
    'Candidate comparison and the diagnostics that say whether to trust it.':
        'Aday karşılaştırması ve sonuca güvenilip güvenilmeyeceğini söyleyen tanılar.',
})

_TR.update({
    "Adjusted MI":
        "Düzeltilmiş MI",
    "Baseline":
        "Referans",
    "Blocking":
        "Engelleyici",
    "Column A":
        "Sütun A",
    "Column B":
        "Sütun B",
    "Correlation":
        "Korelasyon",
    "Distinct":
        "Farklı",
    "Evidence":
        "Kanıt",
    "Leakage audit":
        "Sızıntı denetimi",
    "Leakage score":
        "Sızıntı skoru",
    "Lift over baseline":
        "Referansa göre kazanç",
    "Lower fence":
        "Alt sınır",
    "Missing rows":
        "Eksik satır",
    "Missing share":
        "Eksik oranı",
    "Missing values":
        "Eksik değerler",
    "Model family":
        "Model ailesi",
    "Naive baseline":
        "Basit referans",
    "Observed":
        "Gözlenen",
    "Outlier share":
        "Aykırı değer oranı",
    "Outliers":
        "Aykırı değerler",
    "Protection":
        "Koruma",
    "Protection provided":
        "Sağlanan koruma",
    "Provided by strategy":
        "Stratejinin sağladığı",
    "Required by data":
        "Verinin gerektirdiği",
    "Selected":
        "Seçilen",
    "Sensitivity":
        "Hassasiyet",
    "Split protection":
        "Bölme koruması",
    "Split setup":
        "Bölme kurulumu",
    "Statistic":
        "İstatistik",
    "Std deviation":
        "Standart sapma",
    "Strategy":
        "Strateji",
    "Target distribution":
        "Hedef dağılımı",
    "Threshold":
        "Eşik",
    "Time column":
        "Zaman sütunu",
    "Training":
        "Eğitim",
    "Upper fence":
        "Üst sınır",
    "Usable rows":
        "Kullanılabilir satır",
    "Its missingness alone predicts the target":
        "Yalnızca eksikliği bile hedefi tahmin ediyor",
    "Separates the target classes perfectly":
        "Hedef sınıflarını kusursuz ayırıyor",
    "No missing values in any column.":
        "Hiçbir sütunda eksik değer yok.",
    "No missing values were measured in any column.":
        "Hiçbir sütunda eksik değer ölçülmedi.",
    "Missingness is present but every column is below the 10% threshold.":
        "Eksiklik var ama her sütun %10 eşiğinin altında.",
    "No feature pair reaches the 0.90 correlation threshold.":
        "Hiçbir öznitelik çifti 0.90 korelasyon eşiğine ulaşmıyor.",
    "No values fall outside the 1.5×IQR fences.":
        "Hiçbir değer 1.5×IQR sınırlarının dışında değil.",
    "Some values sit outside the fences, but no column exceeds 5%.":
        "Bazı değerler sınırların dışında ama hiçbir sütun %5'i aşmıyor.",
    "Share of each target class among observed rows.":
        "Gözlenen satırlar içinde her hedef sınıfının payı.",
    "Share of rows with no value, per column.":
        "Sütun başına değeri olmayan satır oranı.",
    "Pearson correlation between every pair of numeric columns.":
        "Her sayısal sütun çifti arasındaki Pearson korelasyonu.",
    "Quartiles and 1.5×IQR fences per numeric column, with the share beyond them.":
        "Sayısal sütun başına çeyreklikler ve 1.5×IQR sınırları, dışında kalan oranla birlikte.",
    "How the rows are divided, and how many survive the target filter.":
        "Satırların nasıl bölündüğü ve hedef filtresinden kaçının geçtiği.",
    "This experiment cannot replace the deterministic winner or affect a gate.":
        "Bu deney deterministik kazananın yerine geçemez ve bir kapıyı etkileyemez.",
})

_TR.update({
    "Additional analysis written by the local agent and executed against a read-only data copy. "
    "It cannot affect gates.":
        "Yerel agent tarafından yazılmış ve salt-okunur bir veri kopyasına karşı çalıştırılmış ek "
        "analiz. Kapıları etkileyemez.",
    "This result is exploratory. Review its proposed interpretation and verification question "
    "before relying on it.":
        "Bu sonuç keşifseldir. Güvenmeden önce önerilen yorumunu ve doğrulama sorusunu gözden "
        "geçirin.",
    "Each fold keeps the observed class proportions, so per-fold scores are comparable to each "
    "other.":
        "Her katlama gözlenen sınıf oranlarını korur, dolayısıyla katlama skorları birbiriyle "
        "karşılaştırılabilir.",
    "No repeated entities and no multi-period time span were measured, so no structural "
    "protection is strictly required.":
        "Tekrarlayan varlık ve çok dönemli zaman aralığı ölçülmedi, dolayısıyla yapısal bir "
        "koruma zorunlu değil.",
    "Every metric measured on the held-out split, scored once after the model was selected.":
        "Ayrılmış bölmede ölçülen tüm metrikler; model seçildikten sonra bir kez skorlandı.",
    "The same entity cannot appear in both training and evaluation. Without it a model can "
    "memorise an individual and be scored on that same individual.":
        "Aynı varlık hem eğitimde hem değerlendirmede bulunamaz. Bu koruma olmadan model bir "
        "bireyi ezberleyip aynı birey üzerinden skorlanabilir.",
    "Training data never comes from later than evaluation data. Without it the model has seen "
    "the future and the score is unachievable in production.":
        "Eğitim verisi hiçbir zaman değerlendirme verisinden daha yeni olamaz. Bu koruma olmadan "
        "model geleceği görmüştür ve skor üretimde tekrarlanamaz.",
    "Strategies are not ranked. Each prevents a different leak, so the question is whether the "
    "chosen one covers what the data actually needs.":
        "Stratejiler bir sıralama değildir. Her biri farklı bir sızıntıyı engeller; asıl soru "
        "seçilenin verinin gerçekten ihtiyaç duyduğunu kapsayıp kapsamadığıdır.",
    "The selected model against a naive baseline on the same holdout. A model that cannot beat "
    "the baseline has learned nothing useful.":
        "Seçilen modelin aynı holdout üzerinde basit bir referansla karşılaştırması. Referansı "
        "geçemeyen bir model işe yarar hiçbir şey öğrenmemiştir.",
    "Four independent leakage families are measured against every feature. A high score is not "
    "proof of leakage, but it is a reason to look.":
        "Her özniteliğe karşı dört bağımsız sızıntı ailesi ölçülür. Yüksek skor sızıntının kanıtı "
        "değildir ama bakmak için bir sebeptir.",
    "Measured association between each feature and the target. These are aggregate statistics — "
    "no individual rows are plotted.":
        "Her öznitelik ile hedef arasında ölçülen ilişki. Bunlar toplu istatistiklerdir — hiçbir "
        "satır tek tek çizilmez.",
    "Code ran against copied development data. The host scored its predictions against labels "
    "withheld from the execution environment.":
        "Kod, kopyalanmış geliştirme verisi üzerinde çalıştı. Tahminlerini, çalışma ortamından "
        "saklanan etiketlere karşı sistem skorladı.",
})

_TR.update({
    "{n} feature pair(s) are correlated at or above ":
        "{n} öznitelik çifti şu eşikte ya da üzerinde korele: ",
    "{n} strong pair(s)":
        "{n} güçlü çift",
    "no strong correlation":
        "güçlü korelasyon yok",
    "{n} column(s) with outliers":
        "{n} sütunda aykırı değer",
    "{n} near-perfect predictor(s)":
        "{n} neredeyse kusursuz yordayıcı",
    "{n} feature(s) measured":
        "{n} öznitelik ölçüldü",
    "{r}:1 majority to minority":
        "{r}:1 çoğunluk/azınlık",
    "one row per {k}":
        "{k} başına bir satır",
})

_TR.update({
    "{n} feature pair(s) are correlated at or above {threshold}. Treat these as leakage "
    "candidates until the audit confirms otherwise, and expect unstable coefficients if both "
    "are kept.":
        "{n} öznitelik çifti {threshold} eşiğinde ya da üzerinde korele. Denetim aksini "
        "doğrulayana kadar bunları sızıntı adayı sayın; ikisi de tutulursa katsayıların kararsız "
        "olmasını bekleyin.",
})

_CATALOGUE: dict[str, dict[str, str]] = {"tr": _TR, "en": {}}

__all__ = ["DEFAULT", "SUPPORTED", "current", "normalise", "t", "using"]
