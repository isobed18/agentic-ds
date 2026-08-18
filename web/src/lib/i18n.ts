/**
 * Language for the interface.
 *
 * Keys are the English sentence, so a string nobody has translated yet renders
 * in English rather than as a missing-key placeholder. That is a visible gap
 * someone can fix, not a broken screen.
 *
 * The choice is persisted and sent to the API, which composes some prose around
 * measured values and cannot be translated after the fact.
 */
export const LANGUAGES = [
  { code: "tr", label: "Türkçe" },
  { code: "en", label: "English" },
] as const;

export type Language = (typeof LANGUAGES)[number]["code"];

const KEY = "ads.language";

export function currentLanguage(): Language {
  const stored = localStorage.getItem(KEY);
  return stored === "en" || stored === "tr" ? stored : "tr";
}

export function setLanguage(code: Language): void {
  localStorage.setItem(KEY, code);
  // A full reload is the honest option: server-composed panel prose is fixed at
  // fetch time, so re-rendering alone would leave half the screen in the old
  // language and look like a bug rather than a pending refresh.
  window.location.reload();
}

const TR: Record<string, string> = {
  // navigation and chrome
  "Home": "Ana sayfa",
  "Your data": "Veriniz",
  "Workflows": "Akışlar",
  "Datasets": "Veri setleri",
  "Experiments": "Denemeler",
  "Models": "Modeller",
  "Reports": "Raporlar",
  "Settings": "Ayarlar",
  "Collapse": "Daralt",
  "Collapse sidebar": "Kenar çubuğunu daralt",
  "Expand sidebar": "Kenar çubuğunu genişlet",
  "Account": "Hesap",
  "Preferences": "Tercihler",
  "Sign out": "Çıkış yap",
  "Language": "Dil",
  "Notifications": "Bildirimler",
  "Nothing to report.": "Bildirilecek bir şey yok.",
  "Waiting for you": "Sizi bekliyor",
  "Run finished": "Koşu tamamlandı",
  "Run stopped with an error": "Koşu hatayla durdu",

  // data screen
  "Drop CSV, Excel or Parquet files here": "CSV, Excel veya Parquet dosyalarını buraya bırakın",
  "Or click to choose. Drop several at once to keep them as one dataset.":
    "Ya da tıklayıp seçin. Birden fazlasını birlikte bırakırsanız tek veri seti olarak kalır.",
  "Uploading and profiling…": "Yükleniyor ve profilleniyor…",
  "Databases are not supported yet — export the tables you need as files for now.":
    "Veritabanı desteği henüz yok — ihtiyacınız olan tabloları şimdilik dosya olarak dışa aktarın.",
  "Drop files in or pick a dataset. Everything below is measured the moment the data lands — no model has seen it yet.":
    "Dosya bırakın ya da bir veri seti seçin. Aşağıdaki her şey veri iner inmez ölçüldü — henüz hiçbir model görmedi.",
  "No data yet": "Henüz veri yok",
  "Drop a file above to begin.": "Başlamak için yukarıya bir dosya bırakın.",
  "Reading the data…": "Veri okunuyor…",
  "Relationships": "İlişkiler",
  "measured": "ölçüldü",
  "No column overlaps strong enough to suggest a join between these tables.":
    "Bu tablolar arasında birleştirmeyi düşündürecek kadar güçlü bir sütun örtüşmesi yok.",
  "of rows match": "satır eşleşiyor",
  "unmatched": "eşleşmedi",
  "Unmatched rows would be dropped from the joined table. Worth knowing what they are before the pipeline decides for you.":
    "Eşleşmeyen satırlar birleştirilmiş tablodan düşecek. Boruhattı sizin adınıza karar vermeden önce bunların ne olduğunu bilmeye değer.",
  "Ready to run": "Çalıştırmaya hazır",
  "Continue to the pipeline": "Boruhattına geç",
  "Ask about this data": "Bu veri hakkında sorun",
  "Column": "Sütun",
  "Kind": "Tür",
  "Missing": "Eksik",
  "Distinct": "Farklı",
  "unique": "benzersiz",
  "sensitive": "hassas",
  "notes": "not",
  "key": "anahtar",

  // launch dialog
  "New run": "Yeni koşu",
  "Choose the data to work on.": "Üzerinde çalışılacak veriyi seçin.",
  "Change data": "Veriyi değiştir",
  "Cancel": "Vazgeç",
  "Start run": "Koşuyu başlat",
  "Loading datasets…": "Veri setleri yükleniyor…",
  "Profiling…": "Profilleniyor…",
  "Profiled locally. No rows leave this machine.":
    "Yerelde profillendi. Hiçbir satır bu makineden çıkmıyor.",
  "table": "tablo",
  "tables": "tablo",
  "candidate key": "aday anahtar",
  "candidate keys": "aday anahtar",
  "Exploratory analysis": "Keşifsel analiz",
  "The fixed profile always runs. This only controls whether an agent adds to it.":
    "Sabit profil her zaman çalışır. Bu yalnızca agent'ın üstüne ekleme yapıp yapmayacağını belirler.",
  "Deterministic only": "Yalnızca deterministik",
  "Fixed profile of every column. Seconds, no model.":
    "Her sütunun sabit profili. Saniyeler sürer, model kullanmaz.",
  "Add agent analysis": "Agent analizi ekle",
  "Agent writes and runs one extra analysis. Adds about a minute.":
    "Agent bir ek analiz yazıp çalıştırır. Yaklaşık bir dakika ekler.",
  "I already know what to predict — set it myself":
    "Ne tahmin edeceğimi biliyorum — kendim seçeyim",
  "Execution mode": "Çalışma modu",
  "Base table": "Temel tablo",
  "Base grain": "Temel granülerlik",
  "Task type": "Görev tipi",
  "Primary metric": "Birincil metrik",
  "Target column": "Hedef sütun",
  "Split strategy": "Bölme stratejisi",
  "Folds": "Katlama",
  "Holdout fraction": "Holdout oranı",
  "Instructions to the planner": "Planlayıcıya talimat",

  // workflow / stage
  "no run selected": "koşu seçilmedi",
  "No runs yet.": "Henüz koşu yok.",
  "Delete": "Sil",
  "Delete run permanently": "Koşuyu kalıcı olarak sil",
  "stages complete": "aşama tamamlandı",
  "Select a stage": "Bir aşama seçin",
  "Pick a node in the pipeline above to see its workspace.":
    "Çalışma alanını görmek için yukarıdaki boruhattından bir düğüm seçin.",
  "Deterministic": "Deterministik",
  "Agent": "Agent",
  "attempts": "deneme",
  "Loading stage…": "Aşama yükleniyor…",
  "Approval required": "Onay gerekiyor",
  "Answer sent": "Cevap gönderildi",
  "Recorded. The run is resuming — this card clears on the next refresh.":
    "Kaydedildi. Koşu devam ediyor — bu kart bir sonraki yenilemede kapanacak.",
  "Attempts": "Denemeler",
  "Decision history": "Karar geçmişi",
  "Artifacts": "Çıktılar",
  "attempt": "deneme",
  "This stage has not run yet": "Bu aşama henüz çalışmadı",
  "No output recorded": "Kaydedilmiş çıktı yok",
  "Start a run to populate this workspace.":
    "Bu çalışma alanını doldurmak için bir koşu başlatın.",

  // branching
  "Start {n} branches": "{n} dal başlat",
  "From here the agents read this same profile, propose what is worth predicting and how to validate it, and stop for you before committing.":
    "Buradan sonra agentlar aynı profili okuyup neyin tahmin edilmeye değer olduğunu ve nasıl doğrulanacağını önerir, ve karara bağlamadan önce size sorar.",
  "Each one you pick starts its own run on the same data with its own agents. This run keeps the problem it already chose.":
    "Seçtiğiniz her biri, aynı veri üzerinde kendi agentlarıyla kendi koşusunu başlatır. Bu koşu zaten seçtiği problemle devam eder.",
  "Supervision": "Denetim",
  "Hard rules still stop the run in either mode; this only controls the clean stages.":
    "Katı kurallar iki modda da koşuyu durdurur; bu yalnızca sorunsuz aşamaları etkiler.",
  "Automatic": "Otomatik",
  "Runs through. Stops only where the gate finds a reason.":
    "Baştan sona çalışır. Yalnızca kapı bir sebep bulursa durur.",
  "Step by step": "Adım adım",
  "Stops after every stage so you can review and correct it.":
    "Her aşamadan sonra durur; inceleyip düzeltebilirsiniz.",
  "Instruct": "Talimat",
  "Tell this stage's agent what you want": "Bu aşamanın agent'ına ne istediğinizi söyleyin",
  "e.g. prefer a linear model and report calibration":
    "örn. doğrusal bir model tercih et ve kalibrasyonu da raporla",
  "Send to the agent": "Agent'a gönder",
  "Applies the next time this stage runs.": "Bu aşama bir sonraki çalıştığında uygulanır.",
  "Personal data": "Kişisel veri",
  "Check what was marked personal": "Kişisel işaretlenenleri gözden geçirin",
  "Columns marked personal are kept out of the model. The classifier is a heuristic and gets both directions wrong — correct it here before the run builds on it.":
    "Kişisel işaretlenen sütunlar modele girmez. Sınıflandırıcı bir sezgiseldir ve iki yönde de yanılır — koşu bunun üstüne inşa etmeden önce buradan düzeltin.",
  "changed": "değişti",
  "personal": "kişisel",
  "usable": "kullanılabilir",
  "Apply {n} changes": "{n} değişikliği uygula",
  "No changes": "Değişiklik yok",
  "Applied. Continue when ready.": "Uygulandı. Hazır olduğunuzda devam edin.",
  "What the agent noticed": "Agent ne fark etti",
  "Agent proposal": "Agent önerisi",
  "Branch": "Dal",
  "Try another problem alongside this one": "Bunun yanında başka bir problem de dene",
  "Start branches": "Dalları başlat",
  "original": "asıl",
  "branch": "dal",
};

const CATALOGUE: Record<Language, Record<string, string>> = { tr: TR, en: {} };

let active: Language = "tr";
try {
  active = currentLanguage();
} catch {
  // localStorage can be unavailable; English keys still render.
}

/** Translate. The key is the English text, so an unknown key is not a failure. */
export function t(text: string, params?: Record<string, string | number>): string {
  const rendered = CATALOGUE[active][text] ?? text;
  if (!params) return rendered;
  return Object.entries(params).reduce(
    (acc, [k, v]) => acc.replaceAll(`{${k}}`, String(v)),
    rendered,
  );
}

export const activeLanguage = (): Language => active;
