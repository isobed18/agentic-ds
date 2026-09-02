import { t } from "../lib/i18n";
/**
 * The two remaining top-level destinations: Home and Settings.
 *
 * #111 makes projects the only top-level concept, so the four global catalogues
 * (Datasets, Experiments, Models, Reports) are gone -- their outputs now live
 * inside the project that produced them (see ProjectContents). Home is a
 * minimal orientation surface; project browsing lives at /projects. Settings
 * shows the enforced guarantees.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Disclosure, Spinner } from "../components/ui";
import { api, type Hardening } from "../lib/api";

function useAsync<T>(load: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    load()
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading };
}

function Page({ title, subtitle, children }: { title: string; subtitle?: string; children: React.ReactNode }) {
  return (
    <div className="h-full overflow-y-auto px-6 py-5">
      <header className="mb-4">
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        {subtitle && <p className="mt-0.5 text-sm text-ink-mute">{subtitle}</p>}
      </header>
      {children}
    </div>
  );
}

/**
 * Hardening values are heterogeneous — booleans, numbers, lists of strings, and
 * lists of mount objects. String() on the last of those yields "[object
 * Object]", so structured values are rendered as their key=value pairs.
 */
function renderValue(v: unknown): React.ReactNode {
  if (typeof v === "boolean") return v ? "enabled" : "disabled";
  if (Array.isArray(v)) {
    return (
      <ul className="space-y-0.5">
        {v.map((item, i) => <li key={i}>{renderValue(item)}</li>)}
      </ul>
    );
  }
  if (v && typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, val]) => `${k}=${String(val)}`)
      .join("  ");
  }
  return String(v);
}

export function Settings() {
  const { data, error, loading } = useAsync<Hardening>(() => api.hardening());
  return (
    <Page title={t("Settings")} subtitle={t("Enforced guarantees. These are properties of the system, not preferences.")}>
      {loading && <Spinner label={t("Loading…")} />}
      {error && <p className="text-sm text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
      <div className="space-y-3">
        {data && Object.entries(data).map(([group, values]) => (
          <Disclosure key={group} title={group.replace(/_/g, " ")} defaultOpen>
            <dl className="grid gap-2 sm:grid-cols-2">
              {Object.entries(values).map(([k, v]) => (
                <div key={k} className="rounded-lg border border-line bg-surface-sunken px-3 py-2">
                  <dt className="text-3xs uppercase tracking-wide text-ink-faint">{k.replace(/_/g, " ")}</dt>
                  <dd className="text-sm text-ink">{renderValue(v)}</dd>
                </div>
              ))}
            </dl>
          </Disclosure>
        ))}
      </div>
    </Page>
  );
}

export function Home() {
  return (
    <div className="relative h-full overflow-y-auto bg-gradient-to-br from-brand-50/80 via-surface to-violet-50/70 px-6 py-10 sm:px-10">
      <div className="pointer-events-none absolute right-[8%] top-[12%] h-44 w-44 rounded-full bg-brand-200/25 blur-3xl" />
      <div className="pointer-events-none absolute bottom-[10%] left-[8%] h-52 w-52 rounded-full bg-violet-200/25 blur-3xl" />
      <main className="relative mx-auto flex min-h-full max-w-4xl items-center justify-center">
        <section className="w-full rounded-3xl border border-white/70 bg-surface/85 px-7 py-10 shadow-pop backdrop-blur sm:px-12 sm:py-14">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-700">Agentic DS</p>
          <h1 className="mt-3 max-w-2xl text-3xl font-semibold tracking-tight text-ink sm:text-4xl">
            {t("Welcome to Agentic DS")}
          </h1>
          <p className="mt-4 max-w-2xl text-lg text-ink sm:text-xl">
            {t("Your data-science work starts inside a project.")}
          </p>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-ink-mute sm:text-base">
            {t("Keep data, automations, models, and reports together. Open Projects to create or continue your work.")}
          </p>
          <Link to="/projects" className="btn-primary mt-8 inline-flex items-center gap-2 !px-5 !py-3 text-sm">
            {t("Open Projects")} <span aria-hidden="true">→</span>
          </Link>
          <div className="mt-10 grid gap-3 border-t border-line-soft pt-6 sm:grid-cols-3">
            {["Local by design", "Measured before modeled", "Decisions stay reviewable"].map((label) => (
              <div key={label} className="flex items-center gap-2 text-xs font-medium text-ink-mute">
                <span className="h-2 w-2 rounded-full bg-brand-400" aria-hidden="true" />
                {t(label)}
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
