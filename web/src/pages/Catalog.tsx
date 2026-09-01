import { t } from "../lib/i18n";
/**
 * The two remaining top-level destinations: Home and Settings.
 *
 * #111 makes projects the only top-level concept, so the four global catalogues
 * (Datasets, Experiments, Models, Reports) are gone -- their outputs now live
 * inside the project that produced them (see ProjectContents). Home is the
 * project-first browsing surface; Settings shows the enforced guarantees.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Disclosure, Empty, Metric, Spinner } from "../components/ui";
import { api, type Hardening, type HomeOutput, type HomeOverview, type HomeProject, type ProjectState } from "../lib/api";
import { VisibilityMark } from "./ProjectWorkspace";

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
                  <dt className="text-[10px] uppercase tracking-wide text-ink-faint">{k.replace(/_/g, " ")}</dt>
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

const STATE_TONE: Record<ProjectState, "brand" | "warn" | "stop" | "ok" | "neutral"> = {
  running: "brand",
  awaiting_human: "warn",
  failed: "stop",
  completed: "ok",
  idle: "neutral",
};

function stateLabel(project: HomeProject): string {
  switch (project.state) {
    case "running":
      return t("Running");
    case "awaiting_human":
      return t("Waiting for you");
    case "failed":
      return t("Needs attention");
    case "completed":
      return t("Completed");
    default:
      return project.status === "saved" ? t("Ready to run") : t("Not started");
  }
}

/** A project opens in its workspace; a project with runs opens on its latest. */
export function projectHref(project: HomeProject): string {
  return `/projects?project=${encodeURIComponent(project.project_id)}`;
}

export function outputHref(output: HomeOutput): string | null {
  if (!output.project_id) return null;
  const base = `/projects?project=${encodeURIComponent(output.project_id)}`;
  return output.automation_id
    ? `${base}&automation=${encodeURIComponent(output.automation_id)}&view=executions&run=${encodeURIComponent(output.run_id)}`
    : base;
}

/**
 * The home page is the only top-level browsing surface after #111: projects are
 * the sole first-class thing, so this leads with each project's state (what is
 * running, what is waiting on a person) and carries the discovery job the four
 * deleted global catalogues used to do — one search across project names and
 * the outputs projects produced, so a report whose project you have forgotten
 * still leads back to it.
 */
export function Home() {
  const [search, setSearch] = useState("");
  const { data, error, loading } = useAsync<HomeOverview>(() => api.home(search), [search]);
  const totals = data?.totals;
  const projects = data?.projects ?? [];
  const recent = data?.recent ?? [];

  return (
    <Page
      title={t("Your projects")}
      subtitle={t("Everything lives inside a project — see what each is doing and what it has produced.")}
    >
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-2">
          <Metric label={t("Projects")} value={String(totals?.projects ?? "—")} />
          <Metric label={t("Running")} value={String(totals?.running ?? "—")} />
          <Metric label={t("Waiting")} value={String(totals?.awaiting_human ?? "—")} />
          <Metric label={t("Needs attention")} value={String(totals?.failed ?? "—")} />
          <Metric label={t("Completed")} value={String(totals?.completed ?? "—")} />
        </div>
        <Link to="/projects" className="btn-primary">
          {t("New project")} →
        </Link>
      </div>

      <input
        type="search"
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder={t("Search projects and outputs…")}
        className="field mb-4 w-full max-w-md text-sm"
      />

      {loading && <Spinner label={t("Loading…")} />}
      {error && <p className="text-sm text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}

      {!loading && !error && (
        <div className="grid gap-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">{t("Projects")}</h3>
            {projects.length === 0 ? (
              search
                ? <Empty title={t("No projects match your search")} hint={t("Try a different name, or clear the search.")} />
                : <Empty title={t("No projects yet")} hint={t("Create a project and its data, runs and reports will appear here.")} />
            ) : (
              <div className="space-y-2">
                {projects.map((project) => (
                  <Link
                    key={project.project_id}
                    to={projectHref(project)}
                    className="card flex items-center gap-3 px-4 py-3 hover:border-brand-500"
                  >
                    <div className="min-w-0 flex-1">
                      <h4 className="flex items-center gap-2 text-sm font-semibold text-ink">
                        <span className="truncate">{project.name}</span>
                        <VisibilityMark visibility={project.visibility} />
                      </h4>
                      <p className="mt-0.5 text-xs text-ink-mute">
                        {project.execution_count > 0
                          ? t("{count} runs", { count: project.execution_count })
                          : t("No runs yet")}
                        {!project.source_id && ` · ${t("No data attached")}`}
                      </p>
                    </div>
                    <Badge tone={STATE_TONE[project.state]}>{stateLabel(project)}</Badge>
                  </Link>
                ))}
              </div>
            )}
          </section>

          <section>
            <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">{t("Recently produced")}</h3>
            {recent.length === 0 ? (
              search
                ? <Empty title={t("No outputs match your search")} />
                : <Empty title={t("Nothing produced yet")} hint={t("Models and reports appear here as your projects finish runs.")} />
            ) : (
              <div className="space-y-2">
                {recent.map((output) => {
                  const href = outputHref(output);
                  const body = (
                    <>
                      <div className="flex items-center gap-2">
                        <Badge tone={output.kind === "model" ? "brand" : "ok"}>
                          {output.kind === "model" ? t("Model") : t("Report")}
                        </Badge>
                        <span className="min-w-0 flex-1 truncate text-sm text-ink">{output.label}</span>
                      </div>
                      <p className="mt-1 truncate text-xs text-ink-mute">
                        {output.project_name ? t("in {project}", { project: output.project_name }) : t("No project")}
                      </p>
                    </>
                  );
                  return href ? (
                    <Link key={output.artifact_id} to={href} className="card block px-4 py-3 hover:border-brand-500">{body}</Link>
                  ) : (
                    <article key={output.artifact_id} className="card px-4 py-3">{body}</article>
                  );
                })}
              </div>
            )}
          </section>
        </div>
      )}
    </Page>
  );
}
