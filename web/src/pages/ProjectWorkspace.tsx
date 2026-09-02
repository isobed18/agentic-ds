import { useEffect, useRef, useState } from "react";
import { LanguagePicker } from "../components/Shell";
import { Notifications } from "../components/Notifications";
// Shared with the automation-scoped models tab so the two views cannot disagree
// about how an enhanced model reads.
import { EnhancedRow } from "../components/ProjectContents";
import { Badge, Empty, Globe, Lock, Metric, NAME_FIELD_WIDTH, Spinner, cx } from "../components/ui";
import {
  api,
  type AutomationDefinition,
  type AutomationInputFile,
  type DataSource,
  type ModelSummary,
  type ProjectContents,
  type ProjectDataSource,
  type ProjectDefinition,
  type ProjectVisibility,
  type ReportSummary,
} from "../lib/api";
import { t } from "../lib/i18n";

export type ProjectView = "overview" | "data" | "automations" | "models" | "reports";

export function ProjectLibrary({ onOpen }: { onOpen: (projectId: string) => void }) {
  const [projects, setProjects] = useState<ProjectDefinition[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<ProjectDefinition | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  function load() {
    return api.projects().then(setProjects).catch((caught) => setError(messageOf(caught)));
  }
  useEffect(() => {
    void load().finally(() => setBusy(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function create() {
    setBusy(true); setError(null);
    try {
      const project = await api.createProject(t("Untitled project"));
      onOpen(project.project_id);
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  // #181: an obsolete project needed a way out of the library, and deletion is
  // consequential enough to warn before it happens. Refresh the library from
  // the server rather than filtering locally, so the counts stay honest.
  async function confirmDelete() {
    if (!deleting || deleteBusy) return;
    setDeleteBusy(true); setError(null);
    try { await api.deleteProject(deleting.project_id); setDeleting(null); await load(); }
    catch (caught) { setError(messageOf(caught)); }
    finally { setDeleteBusy(false); }
  }

  return (
    <div className="h-full overflow-y-auto bg-surface-sunken px-6 py-8 lg:px-10">
      <div className="mx-auto max-w-6xl">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div><h1 className="text-2xl font-semibold text-ink">{t("Projects")}</h1><p className="mt-1 text-sm text-ink-mute">{t("A project keeps data, automations, models, and reports together.")}</p></div>
          <button type="button" className="btn-primary" onClick={() => void create()} disabled={busy}>{busy && projects.length ? t("Creating…") : `+ ${t("New project")}`}</button>
        </header>
        {error && <p className="mt-4 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
        {busy && !projects.length ? <div className="mt-16"><Spinner label={t("Loading…")} /></div> : (
          <div className="mt-7 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {projects.map((project) => (
              <article key={project.project_id} className="relative rounded-xl border border-line bg-surface shadow-card transition hover:-translate-y-0.5 hover:border-brand-300 hover:shadow-pop">
                <button type="button" onClick={() => onOpen(project.project_id)} className="block w-full p-5 pr-12 text-left">
                  <div className="flex items-start justify-between gap-3"><h2 className="truncate text-sm font-semibold text-ink">{project.name}</h2><span className="flex shrink-0 items-center gap-2"><VisibilityMark visibility={project.visibility} /><Badge tone="neutral">{t("Project")}</Badge></span></div>
                  <div className="mt-5 flex gap-4 text-xs text-ink-mute"><span>{t("{count} data sources", { count: project.source_ids.length })}</span><span>{t("{count} automations", { count: project.automation_ids.length })}</span></div>
                  <p className="mt-2 text-[10px] text-ink-faint">{new Date(project.updated_at).toLocaleString()}</p>
                </button>
                <button type="button" aria-label={t("Delete project")} title={t("Delete project")} onClick={() => setDeleting(project)} className="absolute right-2.5 top-2.5 grid h-8 w-8 place-items-center rounded-lg text-ink-faint transition hover:bg-stop-50 hover:text-stop-700">
                  <TrashIcon />
                </button>
              </article>
            ))}
            {!projects.length && !busy && <div className="col-span-full rounded-2xl border border-dashed border-line bg-surface py-16"><Empty title={t("No projects yet")} hint={t("Create a project, add data, then create as many automations as you need.")} /></div>}
          </div>
        )}
      </div>
      {deleting && <ProjectDeleteDialog project={deleting} busy={deleteBusy} onCancel={() => setDeleting(null)} onConfirm={() => void confirmDelete()} />}
    </div>
  );
}

/**
 * The same warned delete an automation gets (#181), one level up: a named
 * target and what survives it. The project and its automations go; the uploaded
 * data is reusable and stays in the library, and each run's history stays
 * readable exactly as automation deletion promises.
 */
export function ProjectDeleteDialog({ project, busy, onCancel, onConfirm }: { project: ProjectDefinition; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/35 p-4" role="dialog" aria-modal="true" aria-labelledby="delete-project-title" aria-describedby="delete-project-description">
      <div className="w-full max-w-md rounded-2xl bg-surface p-6 shadow-2xl">
        <div className="grid h-10 w-10 place-items-center rounded-full bg-stop-50 text-stop-700" aria-hidden="true"><TrashIcon /></div>
        <h2 id="delete-project-title" className="mt-4 text-lg font-semibold text-ink">{t("Delete {name}?", { name: project.name })}</h2>
        <div id="delete-project-description" className="mt-2 space-y-2 text-sm leading-relaxed text-ink-mute">
          <p>{t("This removes the project and its {count} automations.", { count: project.automation_ids.length })}</p>
          <p>{t("The uploaded data and every run's history are kept.")}</p>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" className="btn-ghost" disabled={busy} onClick={onCancel}>{t("Cancel")}</button>
          <button type="button" className="rounded-lg bg-stop-600 px-4 py-2 text-sm font-semibold text-white hover:bg-stop-700 disabled:opacity-50" disabled={busy} onClick={onConfirm}>{busy ? t("Deleting…") : t("Delete project")}</button>
        </div>
      </div>
    </div>
  );
}

export function ProjectWorkspace({
  projectId,
  view,
  onView,
  onBack,
  onOpenAutomation,
}: {
  projectId: string;
  view: ProjectView;
  onView: (view: ProjectView) => void;
  onBack: () => void;
  onOpenAutomation: (automationId: string) => void;
}) {
  const [project, setProject] = useState<ProjectDefinition | null>(null);
  const [name, setName] = useState("");
  const [contents, setContents] = useState<ProjectContents | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    const next = await api.projectContents(projectId);
    setProject(next.project); setName(next.project.name); setContents(next);
  }

  useEffect(() => {
    setBusy(true);
    void refresh().catch((caught) => setError(messageOf(caught))).finally(() => setBusy(false));
    // projectId is the complete identity of this screen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function persistName() {
    const trimmed = name.trim();
    if (!project || !trimmed || trimmed === project.name) { setName(project?.name ?? name); return; }
    try {
      const saved = await api.updateProjectSafely(projectId, project.revision, { name: trimmed });
      setProject(saved); setName(saved.name);
    } catch (caught) {
      setError(messageOf(caught)); setName(project.name);
    }
  }

  if (busy && !project) return <div className="grid h-full place-items-center"><Spinner label={t("Opening project…")} /></div>;

  const data = contents?.data ?? [];
  const automations = contents?.automations ?? [];
  return (
    <div className="flex h-full min-h-0 flex-col bg-surface-sunken">
      <header className="relative flex min-h-[64px] shrink-0 items-center gap-3 border-b border-line bg-surface px-4">
        <button type="button" className="btn-ghost !px-2 text-xs" onClick={onBack}>← {t("Projects")}</button>
        <label className={cx("flex items-center gap-2 rounded-lg border border-line bg-surface-sunken px-3 py-1.5", NAME_FIELD_WIDTH)} title={t("Rename project")}>
          <span aria-hidden="true" className="text-ink-faint">✎</span>
          <input value={name} onChange={(event) => setName(event.target.value)} onBlur={() => void persistName()} aria-label={t("Project name")} title={name} className="min-w-0 flex-1 truncate bg-transparent text-sm font-semibold text-ink outline-none" />
        </label>
        <nav aria-label={t("Project sections")} className="absolute left-1/2 flex -translate-x-1/2 rounded-lg bg-surface-sunken p-1">
          {PROJECT_VIEWS.map((item) => <button key={item} type="button" onClick={() => onView(item)} className={cx("rounded-md px-3.5 py-2 text-sm font-medium", view === item ? "bg-surface text-ink shadow-sm" : "text-ink-mute hover:text-ink")}>{projectViewLabel(item)}</button>)}
        </nav>
        {/* #286: the shared top bar is suppressed for every page inside a
            project, so this contextual header is the only place notifications
            can live here. Composed in rather than duplicated: it is the same
            component the shell renders everywhere else. */}
        <div className="ml-auto flex items-center gap-1"><LanguagePicker /><Notifications /></div>
      </header>
      {error && <p className="mx-4 mt-3 shrink-0 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
      <main className="min-h-0 flex-1 overflow-y-auto p-6">
        <div className="mx-auto max-w-6xl">
          {view === "overview" && <ProjectOverview data={data} automations={automations} contents={contents} project={project} onView={onView} onVisibility={setProject} />}
          {view === "data" && <ProjectData projectId={projectId} data={data} onChanged={() => void refresh()} />}
          {view === "automations" && <ProjectAutomations projectId={projectId} automations={automations} onChanged={() => void refresh()} onOpen={onOpenAutomation} />}
          {view === "models" && <ProjectModels contents={contents} onChanged={() => void refresh()} />}
          {view === "reports" && <ProjectReports contents={contents} onChanged={() => void refresh()} />}
        </div>
      </main>
    </div>
  );
}

function ProjectOverview({ data, automations, contents, project, onView, onVisibility }: { data: ProjectDataSource[]; automations: AutomationDefinition[]; contents: ProjectContents | null; project: ProjectDefinition | null; onView: (view: ProjectView) => void; onVisibility: (project: ProjectDefinition) => void }) {
  const models = contents?.models.length ?? 0;
  const reports = contents?.reports.length ?? 0;
  return (
    <div className="space-y-5">
      <div><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-brand-600">{t("Project overview")}</p><h1 className="mt-1 text-2xl font-semibold text-ink">{t("Everything in this project, at a glance")}</h1><p className="mt-1 text-sm text-ink-mute">{t("Data comes first. Each automation chooses from it and keeps its own graph and outputs.")}</p></div>
      {project && <VisibilityCard project={project} onChanged={onVisibility} />}
      {!data.length && <section className="rounded-2xl border border-brand-200 bg-brand-50 p-6"><h2 className="text-lg font-semibold text-ink">{t("This project needs data")}</h2><p className="mt-1 text-sm text-ink-mute">{t("Upload files or choose data you uploaded before. You do not need an automation first.")}</p><button type="button" className="btn-primary mt-4" onClick={() => onView("data")}>+ {t("Add data")}</button></section>}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <OverviewCard label={t("Data")} value={data.reduce((sum, source) => sum + source.files.length, 0)} hint={t("files in the project")} onClick={() => onView("data")} />
        <OverviewCard label={t("Automations")} value={automations.length} hint={t("independent graphs")} onClick={() => onView("automations")} />
        <OverviewCard label={t("Models")} value={models} hint={t("from every automation")} onClick={() => onView("models")} />
        <OverviewCard label={t("Reports")} value={reports} hint={t("from every automation")} onClick={() => onView("reports")} />
      </div>
      {data.length > 0 && !automations.length && <section className="card p-6"><h2 className="text-base font-semibold text-ink">{t("Your data is ready")}</h2><p className="mt-1 text-sm text-ink-mute">{t("Create an automation and choose which project files it should use.")}</p><button type="button" className="btn-primary mt-4" onClick={() => onView("automations")}>+ {t("New automation")}</button></section>}
    </div>
  );
}

/**
 * The visibility mark, wherever a project is named (#207). Standalone next to
 * a name, so it carries its own accessible name rather than relying on a label
 * beside it.
 */
export function VisibilityMark({ visibility, className }: { visibility: ProjectVisibility; className?: string }) {
  return visibility === "public"
    ? <Globe label={t("Public project")} className={cx("text-ink-mute", className)} />
    : <Lock label={t("Private project")} className={cx("text-ink-mute", className)} />;
}

/**
 * Who can see this project, on the Overview tab.
 *
 * The owner gets a real toggle; everybody else gets the same two options
 * disabled, with the reason written out. Visibility is the one thing a
 * non-owner may not change in an otherwise shared project, so it should look
 * deliberate rather than broken -- a control that says why, not one that
 * silently 403s.
 */
function VisibilityCard({ project, onChanged }: { project: ProjectDefinition; onChanged: (project: ProjectDefinition) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const owned = project.mine;

  async function choose(visibility: ProjectVisibility) {
    if (busy || visibility === project.visibility) return;
    setBusy(true); setError(null);
    try {
      onChanged(await api.setProjectVisibility(project.project_id, visibility));
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setBusy(false);
    }
  }

  const reason = owned
    ? project.visibility === "public"
      ? t("Visible to everyone signed in")
      : t("Only you can see this project")
    : project.owner
      ? t("Only the owner can change who sees this project.")
      : t("This project has no recorded owner, so its visibility cannot be changed.");

  return (
    <section className="card flex flex-wrap items-center gap-x-5 gap-y-3 p-5">
      <div className="min-w-0 flex-1">
        <h2 className="flex items-center gap-2 text-base font-semibold text-ink">
          <VisibilityMark visibility={project.visibility} className="text-ink" />
          {t("Who can see this project")}
        </h2>
        <p className="mt-1 text-sm text-ink-mute">{reason}</p>
        <p className="mt-1 text-xs text-ink-faint">{t("Publishing a project does not publish its data: a file its owner kept private stays private.")}</p>
      </div>
      <div className="flex shrink-0 rounded-lg bg-surface-sunken p-1" role="group" aria-label={t("Who can see this project")}>
        {(["private", "public"] as const).map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => void choose(option)}
            disabled={!owned || busy}
            aria-pressed={project.visibility === option}
            title={owned ? undefined : reason}
            className={cx(
              "flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-medium",
              project.visibility === option ? "bg-surface text-ink shadow-sm" : "text-ink-mute",
              owned && !busy ? "hover:text-ink" : "cursor-not-allowed opacity-60",
            )}
          >
            {option === "public" ? <Globe /> : <Lock />}
            {option === "public" ? t("Public") : t("Private")}
          </button>
        ))}
      </div>
      {error && <p className="w-full rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
    </section>
  );
}

function OverviewCard({ label, value, hint, onClick }: { label: string; value: number; hint: string; onClick: () => void }) {
  return <button type="button" onClick={onClick} className="card p-5 text-left hover:border-brand-300"><p className="text-xs font-medium text-ink-mute">{label}</p><p className="mt-2 text-3xl font-semibold text-ink">{value}</p><p className="mt-1 text-xs text-ink-faint">{hint}</p></button>;
}

function ProjectData({ projectId, data, onChanged }: { projectId: string; data: ProjectDataSource[]; onChanged: () => void }) {
  const [sources, setSources] = useState<DataSource[]>([]);
  const [selectedSource, setSelectedSource] = useState("");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState<{ name: string; index: number; total: number; fraction: number } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => { void api.dataSources().then(setSources).catch(() => setSources([])); }, [data]);

  async function upload(files: FileList | null) {
    const chosen = Array.from(files ?? []);
    if (!chosen.length || uploading) return;
    const abort = new AbortController();
    controller.current = abort; setUploading(true); setError(null);
    try {
      let group: string | undefined;
      for (let index = 0; index < chosen.length; index++) {
        const file = chosen[index];
        setProgress({ name: file.name, index, total: chosen.length, fraction: 0 });
        const result = await api.upload(file, group, {
          signal: abort.signal,
          onProgress: (fraction) => setProgress({ name: file.name, index, total: chosen.length, fraction }),
        });
        group = result.source_id;
      }
      if (group) await api.addProjectSource(projectId, group);
      onChanged();
    } catch (caught) {
      if (!abort.signal.aborted) setError(messageOf(caught));
    } finally {
      setUploading(false); setProgress(null); controller.current = null;
    }
  }

  async function addExisting() {
    if (!selectedSource || uploading) return;
    setUploading(true); setError(null);
    try { await api.addProjectSource(projectId, selectedSource); setSelectedSource(""); onChanged(); }
    catch (caught) { setError(messageOf(caught)); }
    finally { setUploading(false); }
  }

  async function usePdfDemo() {
    if (uploading) return;
    setUploading(true); setError(null);
    try {
      const demo = await api.installPdfDemo();
      if (!data.some((source) => source.source_id === demo.source_id)) {
        await api.addProjectSource(projectId, demo.source_id);
      }
      onChanged();
    } catch (caught) {
      setError(messageOf(caught));
    } finally {
      setUploading(false);
    }
  }

  const attached = new Set(data.map((source) => source.source_id));
  const available = sources.filter((source) => !attached.has(source.source_id));
  return (
    <section>
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h1 className="text-xl font-semibold text-ink">{t("Project data")}</h1><p className="mt-1 text-sm text-ink-mute">{t("Add data before or after automations. Existing automation snapshots do not change.")}</p></div></div>
      <input ref={input} type="file" multiple accept=".csv,.tsv,.txt,.xlsx,.xls,.parquet,.pdf" className="hidden" onChange={(event) => { void upload(event.target.files); event.target.value = ""; }} />
      {progress && <div className="mt-5 rounded-xl border border-brand-200 bg-brand-50 p-4"><div className="flex justify-between gap-3 text-xs"><span className="truncate">{t("Uploading {name}", { name: progress.name })}{progress.total > 1 ? ` (${progress.index + 1}/${progress.total})` : ""}</span><span>{Math.round(progress.fraction * 100)}%</span></div><div className="mt-2 h-2 overflow-hidden rounded-full bg-surface"><div className="h-full rounded-full bg-brand-500" style={{ width: `${Math.round(progress.fraction * 100)}%` }} /></div><button type="button" className="mt-2 text-xs text-stop-700" onClick={() => controller.current?.abort()}>{t("Cancel")}</button></div>}
      <div className="mt-5 flex flex-wrap items-end gap-3 rounded-xl border border-line bg-surface p-4">
        <div><p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Upload new files")}</p><button type="button" className="btn-primary" disabled={uploading} onClick={() => input.current?.click()}>+ {uploading ? t("Uploading…") : t("Upload files")}</button></div>
        <div className="border-l border-line-soft pl-3"><p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Ready local dataset")}</p><button type="button" className="btn-ghost" disabled={uploading} title={t("A PDF table plus matching CSV truth.")} onClick={() => void usePdfDemo()}>{uploading ? t("Adding PDF demo…") : t("Use PDF demo")}</button></div>
        <span className="pb-2 text-xs text-ink-faint">{t("or")}</span>
        <label className="min-w-[260px] flex-1"><span className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t("Choose previously uploaded data")}</span><select className="field w-full text-sm" value={selectedSource} disabled={uploading} onChange={(event) => setSelectedSource(event.target.value)}><option value="">{t("Choose uploaded data")}</option>{available.map((source) => <option key={source.source_id} value={source.source_id}>{source.label}{source.files?.length ? ` · ${source.files.length} ${t("files")}` : ""}</option>)}</select></label>
        <button type="button" className="btn-ghost" disabled={!selectedSource || uploading} onClick={() => void addExisting()}>{t("Add to project")}</button>
      </div>
      {error && <p className="mt-3 rounded-lg bg-stop-50 px-3 py-2 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
      <div className="mt-5 grid gap-3 md:grid-cols-2">
        {data.map((source) => <article key={source.source_id} className="card p-5"><div className="flex items-start justify-between gap-3"><h2 className="min-w-0 flex-1 truncate text-sm font-semibold text-ink" title={source.label}>{source.label}</h2><Metric label={t("Files")} value={String(source.files.length)} /></div><ul className="mt-4 space-y-2">{source.files.map((file) => <li key={file} className="flex items-center gap-2 rounded-lg bg-surface-sunken px-3 py-2 text-xs text-ink"><span aria-hidden="true">▤</span><span className="min-w-0 truncate">{file}</span></li>)}</ul></article>)}
        {!data.length && <div className="md:col-span-2"><Empty title={t("No data in this project")} hint={t("Upload files or choose data you uploaded before.")} /></div>}
      </div>
    </section>
  );
}

function ProjectAutomations({ projectId, automations, onChanged, onOpen }: { projectId: string; automations: AutomationDefinition[]; onChanged: () => void; onOpen: (automationId: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<AutomationDefinition | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function create() {
    setBusy(true); setError(null);
    try { const created = await api.createProjectAutomation(projectId, t("Untitled automation")); onChanged(); onOpen(created.automation_id); }
    catch (caught) { setError(messageOf(caught)); }
    finally { setBusy(false); }
  }
  /**
   * #185: the project-first rework dropped the only delete entry point, so an
   * automation created by a stray click stayed forever. The refresh is the
   * project's, not a local filter: the backend also detaches the automation
   * from its parent, and the card counts elsewhere read that parent.
   */
  async function confirmDelete() {
    if (!deleting || deleteBusy) return;
    setDeleteBusy(true); setError(null);
    try { await api.deleteAutomation(deleting.automation_id); setDeleting(null); onChanged(); }
    catch (caught) { setError(messageOf(caught)); }
    finally { setDeleteBusy(false); }
  }
  return (
    <section>
      <div className="flex items-start justify-between gap-3"><div><h1 className="text-xl font-semibold text-ink">{t("Automations")}</h1><p className="mt-1 text-sm text-ink-mute">{t("Each automation has its own data selection, graph, runs, models, and reports.")}</p></div><button type="button" className="btn-primary" disabled={busy} onClick={() => void create()}>+ {busy ? t("Creating…") : t("New automation")}</button></div>
      {error && <p className="mt-3 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}
      <div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {automations.map((automation) => (
          <article key={automation.automation_id} className="card relative transition hover:border-brand-300">
            <button type="button" className="block w-full p-5 pr-12 text-left" onClick={() => onOpen(automation.automation_id)}>
              <div className="flex items-start justify-between gap-2"><h2 className="truncate text-sm font-semibold text-ink">{automation.name}</h2><Badge tone={automation.status === "saved" ? "ok" : automation.status === "error" ? "stop" : "neutral"}>{t(automation.status === "saved" ? "Saved" : automation.status === "error" ? "Error" : "Draft")}</Badge></div>
              <p className="mt-5 text-xs text-ink-mute">{automation.selected_files?.length ? t("{count} selected files", { count: automation.selected_files.length }) : t("No data selected")}</p>
              <p className="mt-1 text-xs text-ink-faint">{automation.execution_ids.length ? t("{count} runs", { count: automation.execution_ids.length }) : t("No runs yet")}</p>
            </button>
            <button type="button" aria-label={t("Delete automation")} title={t("Delete automation")} onClick={() => setDeleting(automation)} className="absolute right-2.5 top-2.5 grid h-8 w-8 place-items-center rounded-lg text-ink-faint transition hover:bg-stop-50 hover:text-stop-700">
              <TrashIcon />
            </button>
          </article>
        ))}
        {!automations.length && <div className="col-span-full"><Empty title={t("No automations yet")} hint={t("Create an automation. Its graph will open only when you open that automation.")} /></div>}
      </div>
      {deleting && <AutomationDeleteDialog automation={deleting} busy={deleteBusy} onCancel={() => setDeleting(null)} onConfirm={() => void confirmDelete()} />}
    </section>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 20 20" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <path d="M3.75 5.5h12.5M8 5.5V4.25c0-.41.34-.75.75-.75h2.5c.41 0 .75.34.75.75V5.5" strokeLinecap="round" />
      <path d="M5.75 5.5l.62 9.9c.04.61.55 1.1 1.16 1.1h4.94c.61 0 1.12-.49 1.16-1.1l.62-9.9" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M8.5 8.5v5M11.5 8.5v5" strokeLinecap="round" />
    </svg>
  );
}

/**
 * A destructive step gets a named target and the list of what survives it,
 * because the automation is the only copy of a graph while its runs are
 * audited separately and stay readable after the deletion.
 */
export function AutomationDeleteDialog({ automation, busy, onCancel, onConfirm }: { automation: AutomationDefinition; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/35 p-4" role="dialog" aria-modal="true" aria-labelledby="delete-automation-title" aria-describedby="delete-automation-description">
      <div className="w-full max-w-md rounded-2xl bg-surface p-6 shadow-2xl">
        <div className="grid h-10 w-10 place-items-center rounded-full bg-stop-50 text-stop-700" aria-hidden="true"><TrashIcon /></div>
        <h2 id="delete-automation-title" className="mt-4 text-lg font-semibold text-ink">{t("Delete {name}?", { name: automation.name })}</h2>
        <div id="delete-automation-description" className="mt-2 space-y-2 text-sm leading-relaxed text-ink-mute">
          <p>{t("This removes the automation, its graph, and its data selection.")}</p>
          <p>{t("Its execution history and the project data are kept.")}</p>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" className="btn-ghost" disabled={busy} onClick={onCancel}>{t("Cancel")}</button>
          <button type="button" className="rounded-lg bg-stop-600 px-4 py-2 text-sm font-semibold text-white hover:bg-stop-700 disabled:opacity-50" disabled={busy} onClick={onConfirm}>{busy ? t("Deleting…") : t("Delete automation")}</button>
        </div>
      </div>
    </div>
  );
}

function ProjectModels({ contents, onChanged }: { contents: ProjectContents | null; onChanged: () => void }) {
  const models = contents?.models ?? [];
  const [deleting, setDeleting] = useState<ModelSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function confirmDelete() {
    if (!deleting || deleteBusy) return;
    setDeleteBusy(true); setError(null);
    try { await api.deleteModel(deleting.artifact_id); setDeleting(null); onChanged(); }
    catch (caught) { setError(messageOf(caught)); }
    finally { setDeleteBusy(false); }
  }
  if (!models.length) return <Empty title={t("No models yet")} hint={t("Models from every automation in this project will appear here.")} />;
  return <section><h1 className="text-xl font-semibold text-ink">{t("Project models")}</h1><p className="mt-1 text-sm text-ink-mute">{t("Every model is labelled with the automation that produced it.")}</p>{error && <p className="mt-3 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}<div className="mt-5 grid gap-3 md:grid-cols-2 xl:grid-cols-3">{models.map((model) => <article key={model.artifact_id} className="card relative p-4 pr-12"><button type="button" aria-label={t("Delete model")} title={t("Delete model")} onClick={() => setDeleting(model)} className="absolute right-2.5 top-2.5 grid h-8 w-8 place-items-center rounded-lg text-ink-faint transition hover:bg-stop-50 hover:text-stop-700"><TrashIcon /></button><Badge tone="brand">{t("From {automation}", { automation: model.automation_name })}</Badge><h2 className="mt-3 text-sm font-semibold text-ink">{model.display_name}</h2><p className="text-xs text-ink-mute">{model.estimator}</p><dl className="mt-3 grid grid-cols-2 gap-2"><div><dt className="text-[10px] text-ink-faint">Holdout {model.metric}</dt><dd className="text-sm font-semibold">{model.holdout_score.toFixed(2)}</dd></div><div><dt className="text-[10px] text-ink-faint">{t("Training rows")}</dt><dd className="text-sm font-semibold">{model.training_rows.toLocaleString()}</dd></div></dl>{model.enhanced && <EnhancedRow enhanced={model.enhanced} metric={model.metric} />}<div className="mt-3 flex flex-wrap items-center gap-1.5">{model.saved && <a href={`/api/models/${model.artifact_id}/download`} className="btn-ghost inline-flex !py-1 text-xs" download>{t("Download original")}</a>}{model.enhanced?.saved && <a href={`/api/models/${model.enhanced.artifact_id}/download`} className="btn-ghost inline-flex !py-1 text-xs" download>{t("Download RL-enhanced")}</a>}</div></article>)}</div>{deleting && <ArtifactDeleteDialog title={t("Delete {name}?", { name: deleting.display_name })} confirmLabel={t("Delete model")} busy={deleteBusy} onCancel={() => setDeleting(null)} onConfirm={() => void confirmDelete()} />}</section>;
}

function ProjectReports({ contents, onChanged }: { contents: ProjectContents | null; onChanged: () => void }) {
  const reports = contents?.reports ?? [];
  const [deleting, setDeleting] = useState<ReportSummary | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function confirmDelete() {
    if (!deleting || deleteBusy) return;
    setDeleteBusy(true); setError(null);
    try { await api.deleteReport(deleting.artifact_id); setDeleting(null); onChanged(); }
    catch (caught) { setError(messageOf(caught)); }
    finally { setDeleteBusy(false); }
  }
  if (!reports.length) return <Empty title={t("No reports yet")} hint={t("Reports from every automation in this project will appear here.")} />;
  return <section><h1 className="text-xl font-semibold text-ink">{t("Project reports")}</h1><p className="mt-1 text-sm text-ink-mute">{t("Every report is labelled with the automation that produced it.")}</p>{error && <p className="mt-3 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}<div className="mt-5 space-y-2">{reports.map((report) => <article key={report.artifact_id} className="card flex items-center gap-3 p-4"><div className="min-w-0 flex-1"><Badge tone="ok">{t("From {automation}", { automation: report.automation_name })}</Badge><h2 className="mt-2 truncate text-sm font-medium text-ink">{String(report.title ?? report.preview ?? t("Evaluation report"))}</h2></div><a href={`/api/reports/${report.artifact_id}/download`} className="btn-ghost text-xs" download>{t("Download")}</a><button type="button" aria-label={t("Delete report")} title={t("Delete report")} onClick={() => setDeleting(report)} className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-ink-faint transition hover:bg-stop-50 hover:text-stop-700"><TrashIcon /></button></article>)}</div>{deleting && <ArtifactDeleteDialog title={t("Delete this report?")} confirmLabel={t("Delete report")} busy={deleteBusy} onCancel={() => setDeleting(null)} onConfirm={() => void confirmDelete()} />}</section>;
}

/**
 * A model or report is a byproduct of a run, not a top-level record with its
 * own name -- there's nothing more specific to warn about than "this output",
 * unlike the automation/project dialogs which name what else is affected.
 */
function ArtifactDeleteDialog({ title, confirmLabel, busy, onCancel, onConfirm }: { title: string; confirmLabel: string; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-ink/35 p-4" role="dialog" aria-modal="true" aria-labelledby="delete-artifact-title">
      <div className="w-full max-w-md rounded-2xl bg-surface p-6 shadow-2xl">
        <div className="grid h-10 w-10 place-items-center rounded-full bg-stop-50 text-stop-700" aria-hidden="true"><TrashIcon /></div>
        <h2 id="delete-artifact-title" className="mt-4 text-lg font-semibold text-ink">{title}</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-mute">{t("The run that produced it keeps its history; only this saved output is removed.")}</p>
        <div className="mt-6 flex justify-end gap-2">
          <button type="button" className="btn-ghost" disabled={busy} onClick={onCancel}>{t("Cancel")}</button>
          <button type="button" className="rounded-lg bg-stop-600 px-4 py-2 text-sm font-semibold text-white hover:bg-stop-700 disabled:opacity-50" disabled={busy} onClick={onConfirm}>{busy ? t("Deleting…") : confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

export function AutomationInputSelector({ projectId, automation, onSelected, onProjectData }: { projectId: string; automation: AutomationDefinition; onSelected: (saved: AutomationDefinition) => void; onProjectData: () => void }) {
  const [data, setData] = useState<ProjectDataSource[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set((automation.selected_files ?? []).map((item) => keyOf(item))));
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { void api.projectData(projectId).then(setData).catch((caught) => setError(messageOf(caught))).finally(() => setBusy(false)); }, [projectId]);
  if (busy) return <div className="grid h-full place-items-center"><Spinner label={t("Loading project data…")} /></div>;
  const files = data.flatMap((source) => source.files.map((path) => ({ source_id: source.source_id, path, label: source.label })));
  const allSelected = everyFileSelected(files, selected);
  async function save() {
    const selections = files.filter((item) => selected.has(keyOf(item))).map(({ source_id, path }) => ({ source_id, path }));
    if (!selections.length) return;
    setBusy(true); setError(null);
    try { onSelected(await api.selectAutomationInputs(automation.automation_id, automation.revision, selections)); }
    catch (caught) { setError(messageOf(caught)); setBusy(false); }
  }
  if (!files.length) return <div className="grid h-full place-items-center p-8"><div className="max-w-xl rounded-2xl border border-brand-200 bg-brand-50 p-8 text-center"><Empty title={t("Add project data first")} hint={t("Automations choose files from the project. Uploading never starts inside an automation.")} /><button type="button" className="btn-primary mt-5" onClick={onProjectData}>{t("Go to project data")}</button></div></div>;
  return <div className="h-full overflow-y-auto p-6"><div className="mx-auto max-w-3xl"><p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-brand-600">{t("Automation data")}</p><h1 className="mt-1 text-xl font-semibold text-ink">{t("Select project files for this automation")}</h1><p className="mt-1 text-sm text-ink-mute">{t("This automation receives a private snapshot. Adding project data later will not change it.")}</p>{error && <p className="mt-3 text-xs text-stop-700">{t("Something went wrong: {detail}", { detail: error })}</p>}<div className="mt-5 flex justify-end"><button type="button" className="btn-ghost text-xs" onClick={() => setSelected(allSelected ? new Set() : new Set(files.map(keyOf)))}>{allSelected ? t("Unselect all") : t("Select all")}</button></div><div className="mt-2 space-y-2">{files.map((file) => { const key = keyOf(file); return <label key={key} className="card flex cursor-pointer items-center gap-3 p-4"><input type="checkbox" checked={selected.has(key)} onChange={(event) => setSelected((current) => { const next = new Set(current); if (event.target.checked) next.add(key); else next.delete(key); return next; })} /><span className="min-w-0 flex-1"><span className="block truncate text-sm font-medium text-ink">{file.path}</span><span className="block truncate text-xs text-ink-mute">{file.label}</span></span></label>; })}</div><button type="button" className="btn-primary mt-5" disabled={!selected.size || busy} onClick={() => void save()}>{busy ? t("Saving…") : t("Use selected data")}</button></div></div>;
}

const PROJECT_VIEWS: ProjectView[] = ["overview", "data", "automations", "models", "reports"];
function projectViewLabel(view: ProjectView): string { switch (view) { case "data": return t("Data"); case "automations": return t("Automations"); case "models": return t("Models"); case "reports": return t("Reports"); default: return t("Overview"); } }
function keyOf(file: AutomationInputFile): string { return `${file.source_id}\u0000${file.path}`; }

/**
 * #184: the select-all toggle's label is derived, not stored. A separate "all
 * selected" flag would drift the moment someone unticked one row by hand, and
 * the button would then offer to unselect a selection that is no longer
 * complete. An empty list is never "all selected": `[].every()` is true, which
 * would put "Unselect all" above nothing.
 */
export function everyFileSelected(files: AutomationInputFile[], selected: Set<string>): boolean {
  return files.length > 0 && files.every((file) => selected.has(keyOf(file)));
}

function messageOf(caught: unknown): string { return caught instanceof Error ? caught.message : String(caught); }
