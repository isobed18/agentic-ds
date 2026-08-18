/**
 * The stage-status vocabulary, stated once.
 *
 * These six strings are produced by `ControlPlane._stage_status` in
 * src/ads/api/service.py and nowhere else. Keeping the mapping in one module
 * is deliberate: the first version of this UI spelled the same concept
 * "completed" in the progress bar, "retrying" in the rail and "auto_proceed"
 * in the tone map, so a finished run rendered as 0/11 complete.
 */
export type StageStatus =
  | "pending"
  | "running"
  | "retry"
  | "blocked"
  | "failed"
  | "succeeded";

/** A stage that finished and passed its gate. */
export const isSucceeded = (s?: string | null) => s === "succeeded";

/** A stage the run is currently working through — the reason to keep polling. */
export const isActive = (s?: string | null) => s === "running" || s === "retry";

/** A stage that cannot proceed without a person. */
export const isAttention = (s?: string | null) => s === "blocked" || s === "failed";

/**
 * Run-level states that mean more progress is still coming.
 *
 * `interrupted` is deliberately excluded. The backend reports it for a run
 * whose worker process exited, and polling one forever is exactly the bug that
 * state was introduced to end.
 */
export const isRunActive = (s?: string | null) =>
  s === "queued" || s === "running" || s === "resuming";

/** Human-facing label. The backend vocabulary is precise but not prose. */
export const statusLabel = (s?: string | null) =>
  ({
    pending: "bekliyor",
    queued: "sırada",
    running: "çalışıyor",
    resuming: "devam ediyor",
    retry: "yeniden deniyor",
    blocked: "onay bekliyor",
    failed: "başarısız",
    succeeded: "tamamlandı",
    completed: "tamamlandı",
    interrupted: "yarıda kaldı",
    awaiting_human: "sizi bekliyor",
  })[s ?? ""] ?? (s ?? "").replace(/_/g, " ");

/**
 * Gate verdicts and reason codes are internal vocabulary. They stay in the API
 * and the audit record — the run's own history should read as sentences, not as
 * enum values a reader has to decode.
 */
const VERDICT_LABELS: Record<string, string> = {
  auto_proceed: "Devam etti",
  retry: "Geri gönderildi",
  escalate: "Size soruldu",
  abort: "Durduruldu",
};

export const verdictLabel = (v: string): string =>
  VERDICT_LABELS[v] ?? titleCase(v);

const REASON_LABELS: Record<string, string> = {
  profile_checkpoint: "Bu adımı gözden geçirmek istediniz",
  leakage_detected: "Bir öznitelik cevabı sızdırıyor olabilir",
  leakage_unresolved: "Düzeltmeden sonra da sızıntı sürüyor",
  leakage_challenge_needs_confirmation: "Agent sızıntı bulgusuna itiraz etti",
  retry_budget_exhausted: "Deneme hakkı kalmadı",
  repeated_identical_failure: "Aynı hata tekrarlandı",
  pii_egress_requested: "Kişisel veri makineden çıkacaktı",
  destructive_operation: "Bir adım kaynak veriyi değiştirecekti",
  model_below_baseline: "Model referans modeli geçemedi",
  lift_within_noise: "İyileşme gürültü sınırları içinde",
  high_cv_variance: "Katlamalar arası skorlar çok değişken",
  candidate_disagreement: "Öneriler birbirini tutmadı",
  statistical_support_low: "Bunu destekleyecek kadar veri yok",
  degenerate_split: "Bölme bir katlamayı kullanılamaz bıraktı",
  unmet_mandatory_criteria: "Zorunlu bir kontrol geçmedi",
  risk_class_gate: "Bu adım yüksek riskli",
  clean: "İşaretlenecek bir şey yok",
};

export const reasonLabel = (code: string): string =>
  REASON_LABELS[code] ?? titleCase(code);

function titleCase(s: string): string {
  const words = s.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
