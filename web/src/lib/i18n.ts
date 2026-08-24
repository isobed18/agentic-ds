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
  // run and stage status
  "pending": "bekliyor",
  "queued": "sırada",
  "running": "çalışıyor",
  "resuming": "devam ediyor",
  "retrying": "yeniden deniyor",
  "needs approval": "onay bekliyor",
  "failed": "başarısız",
  "complete": "tamamlandı",
  "interrupted": "yarıda kaldı",
  "needs you": "sizi bekliyor",

  // gate verdicts and reasons
  "Continued": "Devam etti",
  "Sent back": "Geri gönderildi",
  "Asked you": "Size soruldu",
  "Stopped": "Durduruldu",
  "You asked to review this step": "Bu adımı gözden geçirmek istediniz",
  "A feature may leak the answer": "Bir öznitelik cevabı sızdırıyor olabilir",
  "Leakage still present after rework": "Düzeltmeden sonra da sızıntı sürüyor",
  "Agent challenged the leakage finding": "Agent sızıntı bulgusuna itiraz etti",
  "No attempts left": "Deneme hakkı kalmadı",
  "The same failure repeated": "Aynı hata tekrarlandı",
  "Personal data would leave the machine": "Kişisel veri makineden çıkacaktı",
  "A step would modify the source data": "Bir adım kaynak veriyi değiştirecekti",
  "The model did not beat the baseline": "Model referans modeli geçemedi",
  "The improvement is within noise": "İyileşme gürültü sınırları içinde",
  "Fold-to-fold scores vary widely": "Katlamalar arası skorlar çok değişken",
  "The proposals disagreed": "Öneriler birbirini tutmadı",
  "Not enough data to support this": "Bunu destekleyecek kadar veri yok",
  "The split left a fold unusable": "Bölme bir katlamayı kullanılamaz bıraktı",
  "A required check did not pass": "Zorunlu bir kontrol geçmedi",
  "This step is high risk": "Bu adım yüksek riskli",
  "Nothing to flag": "İşaretlenecek bir şey yok",

  // pipeline stages
  "intake": "veri alma",
  "schema discovery": "şema keşfi",
  "integration": "birleştirme",
  "problem discovery": "problem tanımı",
  "validation strategy": "doğrulama stratejisi",
  "eda": "keşifsel analiz",
  "leakage audit": "sızıntı denetimi",
  "feature pipeline": "öznitelik üretimi",
  "splitting": "bölme",
  "training": "eğitim",
  "evaluation": "değerlendirme",
  "report": "raporlama",

  // overview
  "Overview": "Genel bakış",
  "Local, self-hosted agentic data science. Nothing leaves this machine.":
    "Yerel, kendi sunucunuzda çalışan agentic veri bilimi. Hiçbir şey bu makineden çıkmıyor.",
  "Open the workflow": "Akışı aç",
  "This is what the data says. Nothing has been decided yet.":
    "Verinin söylediği bu. Henüz hiçbir şeye karar verilmedi.",
  "Configure the run.": "Koşuyu yapılandırın.",
  "Read what you need, then continue. You can come back.":
    "İhtiyacınız kadarını okuyun, sonra devam edin. Geri dönebilirsiniz.",
  "Continue": "Devam et",
  "Back": "Geri",
  "Ask": "Sor",
  "Tables": "Tablo",
  "Personal": "Kişisel",
  "identifier": "tanımlayıcı",
  "categorical": "kategorik",
  "numeric continuous": "sürekli sayısal",
  "numeric discrete": "ayrık sayısal",
  "datetime": "tarih",
  "boolean": "mantıksal",
  "text": "metin",
  "email": "e-posta",
  "constant": "sabit",
  "empty": "boş",
  "column renamed": "sütun yeniden adlandırıldı",
  "duplicate column name": "yinelenen sütun adı",
  "blank column name": "boş sütun adı",
  "empty columns dropped": "boş sütunlar atıldı",
  "empty rows dropped": "boş satırlar atıldı",
  "profiled on sample": "örneklem üzerinde profillendi",
  // Pipeline stages. Keyed on the English display name rather than the stage
  // id, because the id is a code and the rest of the catalogue is keyed on
  // source strings; `titleize` is what maps one to the other.
  "Intake": "Veri Alma",
  "Schema Discovery": "Şema Keşfi",
  "Integration": "Birleştirme",
  "Problem Discovery": "Problem Keşfi",
  "Validation Strategy": "Doğrulama Stratejisi",
  "Leakage Audit": "Sızıntı Denetimi",
  "Feature Pipeline": "Öznitelik Hattı",
  "Splitting": "Veri Bölme",
  "Training": "Eğitim",
  "Evaluation": "Değerlendirme",
  "Report": "Rapor",
  "Source Comprehension": "Kaynak Kavrama",
  "deterministic": "kurallı",
  "agent": "ajan",
  "Fit": "Sığdır",
  "Fit to view": "Ekrana sığdır",
  "Drag to resize": "Boyutlandırmak için sürükle",
  "Drag to move · pinch or ctrl+scroll to zoom": "Sürükleyerek gez · yakınlaştırmak için kıstır veya ctrl+kaydır",
  "Choose the data. Intake and schema discovery run straight away; you configure the rest on the pipeline.": "Veriyi seçin. Veri alma ve şema keşfi hemen çalışır; gerisini boruhattı üzerinde ayarlarsınız.",
  "Or upload files — CSV, Parquet, Excel. Several files become one dataset.": "Ya da dosya yükleyin — CSV, Parquet, Excel. Birkaç dosya tek veri seti olur.",
  "on the server, or upload them below.": "sunucuya koyun ya da aşağıdan yükleyin.",
  "Uploading…": "Yükleniyor…",
  "issues": "sorun",
  "Reading the data and its schema…": "Veri ve şeması okunuyor…",
  "Applied when the run continues. Nothing below is decided yet.": "Koşu devam ettiğinde uygulanır. Aşağıdakilerin hiçbiri henüz kararlaştırılmadı.",
  "The gate stops the run only where its own signals demand it.": "Kapı, koşuyu yalnızca kendi sinyalleri gerektirdiği yerde durdurur.",
  "Every stage stops for your approval.": "Her aşama onayınız için durur.",
  "Leave empty to let the agent discover the problem from the data.": "Ajanın problemi veriden keşfetmesi için boş bırakın.",
  "Let the agent decide": "Ajan karar versin",
  "Agent panel": "Ajan paneli",
  "Running a stage several times independently is what produces the agreement signal the gate uses.": "Bir aşamayı birkaç kez bağımsız çalıştırmak, kapının kullandığı uzlaşma sinyalini üretir.",
  "Let an agent write its own analysis on top of the fixed profile": "Sabit profilin üstüne bir ajan kendi analizini yazsın",
  "Run": "Çalıştır",
  "Starting…": "Başlatılıyor…",
  "Available once the first two stages finish.": "İlk iki aşama bitince kullanılabilir.",
  "Continues this run; it does not start a second one.": "Bu koşuya devam eder; ikinci bir koşu başlatmaz.",
  "reading the data": "veri okunuyor",
  "ready to run": "çalıştırmaya hazır",
  "suggested": "önerilen",
  "Load and deterministically profile every source table.": "Her kaynak tabloyu yükler ve kurallı biçimde profiller.",
  "Planner agent interprets relationships into an integration plan; a separate advisory pass proposes source comprehension from measured evidence.": "Planlayıcı ajan ilişkileri bir birleştirme planına dönüştürür; ayrı bir danışma turu ölçülen kanıttan kaynak kavrayışı önerir.",
  "Execute the proposed plan and verify analytical-base-table grain.": "Önerilen planı uygular ve analitik temel tablonun granülerliğini doğrular.",
  "An active scout chooses measurements or read-only code, then the planner proposes problems with host-measured feasibility support.": "Etkin bir kâşif ölçüm ya da salt-okunur kod seçer; ardından planlayıcı, uygulanabilirliği ana süreçte ölçülmüş problemler önerir.",
  "An active scout trials candidate splits; the exact final strategy is re-executed and fingerprint-bound to executor-owned diagnostics.": "Etkin bir kâşif aday bölmeleri dener; seçilen strateji yeniden çalıştırılıp yürütücünün tuttuğu tanılara parmak iziyle bağlanır.",
  "Profile the analytical base table, then run a separate advisory pass over the measured descriptors.": "Analitik temel tabloyu profiller, sonra ölçülen betimleyiciler üzerinde ayrı bir danışma turu çalıştırır.",
  "Run the mandatory leakage floor, then let an agent request a registered falsifiable challenge without clearing the gate itself.": "Zorunlu sızıntı taban denetimini çalıştırır; ardından ajan, kapıyı kendisi geçirmeden kayıtlı ve yanlışlanabilir bir sınama isteyebilir.",
  "Persist the mandatory fold-local feature floor, then optionally run one isolated, host-scored authored feature experiment.": "Zorunlu kat-yerel öznitelik tabanını kalıcılaştırır; isteğe bağlı olarak yalıtılmış, ana süreçte puanlanan tek bir öznitelik denemesi çalıştırır.",
  "Build the selected split and measure retained support.": "Seçilen bölmeyi kurar ve elde kalan desteği ölçer.",
  "Fit the deterministic candidate menu, then optionally run one isolated, host-scored agent-authored development experiment.": "Kurallı aday menüsünü eğitir; isteğe bağlı olarak yalıtılmış, ana süreçte puanlanan tek bir ajan denemesi çalıştırır.",
  "Assemble holdout, baseline, leakage, split, and gate evidence.": "Ayrık küme, taban çizgisi, sızıntı, bölme ve kapı kanıtlarını bir araya getirir.",
  "Render the complete auditable Markdown report.": "Denetlenebilir Markdown raporunun tamamını üretir.",
  "Rows": "Satır",
  "Columns": "Sütun",
  "rows": "satır",
  "columns": "sütun",
  "Schema": "Şema",
  "Every edge is an overlap measured between real column values, not a match on column names. An amber edge loses rows when the tables are joined.": "Her bağ, sütun adlarına değil gerçek sütun değerleri arasında ölçülen örtüşmeye dayanır. Kehribar renkli bağ, tablolar birleştirildiğinde satır kaybeder.",
  "The plan declared no base grain.": "Plan temel granülerliği bildirmedi.",
  "The plan did not pass the relationship and fan-out validators.": "Plan, ilişki ve fan-out doğrulayıcılarından geçemedi.",
  "The plan failed a trial execution against the real tables.": "Plan, gerçek tablolar üzerinde denenince başarısız oldu.",
  "artifacts": "artefakt",
  "Zoom in": "Yakınlaştır",
  "Zoom out": "Uzaklaştır",
  "Reset zoom": "Yakınlaştırmayı sıfırla",
  "Start pipeline": "Boruhattını başlat",
  "Staged run": "Hazırlanan koşu",
  "Prepare run": "Koşuyu hazırla",
  "Intake complete": "Veri alma tamamlandı",
  "Ready to start": "Başlatmaya hazır",
  "Discard": "Vazgeç",
  "Run configuration": "Koşu ayarları",
  "Needs attention": "Dikkat gerekiyor",
  "Schema overview": "Şema görünümü",
  "stages": "aşama",
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
  "Deterministic": "Kurallı",
  "Agent": "Ajan",
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
