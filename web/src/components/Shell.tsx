import { t } from "../lib/i18n";
/**
 * Application shell: collapsible left rail, top bar, routed content.
 *
 * Fills the viewport and scrolls internally per region, so the pipeline rail and
 * planner stay put while the workspace scrolls — the layout the designs assume.
 */
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { Notifications } from "./Notifications";
import { api } from "../lib/api";
import { LANGUAGES, currentLanguage, setLanguage } from "../lib/i18n";
import { cx } from "./ui";

const NAV = [
  { to: "/", label: "Home", icon: HomeIcon, end: true },
  { to: "/automation", label: "Data projects", icon: FlowIcon },
  { to: "/datasets", label: "Data library", icon: DataIcon },
  { to: "/experiments", label: "Experiments", icon: FlaskIcon },
  { to: "/models", label: "Models", icon: CubeIcon },
  { to: "/reports", label: "Reports", icon: DocIcon },
  { to: "/settings", label: "Settings", icon: GearIcon },
];

const COLLAPSE_KEY = "ads.sidebar.collapsed";

export function Shell({ children, topBar }: { children: ReactNode; topBar?: ReactNode }) {
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(COLLAPSE_KEY) === "1",
  );
  const location = useLocation();
  const isAutomationEditor = location.pathname.startsWith("/automation");

  useEffect(() => {
    localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
  }, [collapsed]);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-surface-sunken">
      <aside
        className={cx(
          "flex shrink-0 flex-col border-r border-line bg-surface transition-[width] duration-200",
          collapsed ? "w-[68px]" : "w-[232px]",
        )}
      >
        <div className="flex h-[60px] items-center gap-2.5 border-b border-line px-4">
          <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-brand-600 text-white">
            <LogoIcon />
          </div>
          {!collapsed && (
            <span className="truncate text-[15px] font-semibold tracking-tight">
              {t("Agentic Data Science")}
            </span>
          )}
        </div>

        <nav className="flex-1 overflow-y-auto p-2.5">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                cx(
                  "mb-0.5 flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors",
                  collapsed && "justify-center px-0",
                  isActive
                    ? "bg-brand-50 text-brand-700"
                    : "text-ink-soft hover:bg-surface-sunken hover:text-ink",
                )
              }
            >
              <Icon />
              {!collapsed && <span className="truncate">{t(label)}</span>}
            </NavLink>
          ))}
        </nav>

        <button
          onClick={() => setCollapsed((c) => !c)}
          className="mx-2.5 mb-1 flex items-center gap-3 rounded-lg px-3 py-2 text-xs font-medium text-ink-faint hover:bg-surface-sunken hover:text-ink-soft"
          title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          <svg viewBox="0 0 20 20" className={cx("h-4 w-4 shrink-0 transition-transform", collapsed && "rotate-180")} fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M12 5 7 10l5 5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          {!collapsed && <span>{t("Collapse")}</span>}
        </button>

        <ProfileCard collapsed={collapsed} />
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {!isAutomationEditor && <header className="flex h-[60px] shrink-0 items-center gap-4 border-b border-line bg-surface px-6">
          {topBar ?? <Breadcrumb path={location.pathname} />}
          <div className="ml-auto flex items-center gap-1">
            <LanguagePicker />
            <Notifications />
          </div>
        </header>}
        <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
      </div>
    </div>
  );
}

function Breadcrumb({ path }: { path: string }) {
  const label = NAV.find((n) => (n.end ? path === n.to : path.startsWith(n.to)))?.label ?? "Home";
  return <h1 className="text-[15px] font-semibold text-ink">{t(label)}</h1>;
}

function ProfileCard({ collapsed }: { collapsed: boolean }) {
  const [open, setOpen] = useState(false);
  const [username, setUsername] = useState<string | null>(null);
  useEffect(() => { void api.authSession().then((session) => setUsername(session.username)).catch(() => setUsername(null)); }, []);
  const initials = (username ?? "ADS").split(/[-_.\s]+/).filter(Boolean).slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "ADS";
  async function signOut() {
    await api.logout().catch(() => undefined);
    window.location.href = "/login";
  }
  return (
    <div className="relative border-t border-line p-2.5">
      {open && !collapsed && (
        <div className="absolute bottom-[68px] left-2.5 right-2.5 rounded-xl border border-line bg-surface p-1.5 shadow-pop">
          <p className="truncate px-3 py-2 text-xs text-ink-mute">{username ?? t("Local session")}</p>
          {["Account", "Preferences"].map((item) => <button key={item} type="button" disabled title={t("Not available yet")} className="w-full rounded-lg px-3 py-2 text-left text-sm text-ink-faint disabled:cursor-not-allowed">{t(item)}</button>)}
          <button onClick={() => void signOut()} className="w-full rounded-lg px-3 py-2 text-left text-sm text-ink-soft hover:bg-surface-sunken">{t("Sign out")}</button>
        </div>
      )}
      <button
        onClick={() => setOpen((o) => !o)}
        className={cx(
          "flex w-full items-center gap-2.5 rounded-lg p-1.5 hover:bg-surface-sunken",
          collapsed && "justify-center",
        )}
      >
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-ink text-xs font-semibold text-white">
          {initials}
        </span>
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1 text-left">
              <span className="block truncate text-sm font-medium text-ink">{username ?? t("Local session")}</span>
              <span className="block truncate text-xs text-ink-mute">{t("Agentic DS user")}</span>
            </span>
            <svg viewBox="0 0 20 20" className="h-4 w-4 shrink-0 text-ink-faint" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="m6 8 4 4 4-4" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </>
        )}
      </button>
    </div>
  );
}

/* --- icons: inline so the app has no external asset or font dependency ----- */
const S = { fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round" as const, strokeLinejoin: "round" as const };
const box = "h-[18px] w-[18px] shrink-0";

function LogoIcon() { return <svg viewBox="0 0 24 24" className="h-4.5 w-4.5" {...S} strokeWidth={2}><path d="M4 17 9 9l4 5 3-4 4 7" /><circle cx="9" cy="9" r="1.6" /></svg>; }
function HomeIcon() { return <svg viewBox="0 0 24 24" className={box} {...S}><path d="M4 10.5 12 4l8 6.5V20a1 1 0 0 1-1 1h-4v-6H9v6H5a1 1 0 0 1-1-1z" /></svg>; }
function FlowIcon() { return <svg viewBox="0 0 24 24" className={box} {...S}><rect x="3" y="4" width="6" height="5" rx="1.4" /><rect x="15" y="4" width="6" height="5" rx="1.4" /><rect x="9" y="15" width="6" height="5" rx="1.4" /><path d="M6 9v3h12V9M12 12v3" /></svg>; }
function DataIcon() { return <svg viewBox="0 0 24 24" className={box} {...S}><ellipse cx="12" cy="6" rx="7.5" ry="3" /><path d="M4.5 6v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6M4.5 12v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6" /></svg>; }
function FlaskIcon() { return <svg viewBox="0 0 24 24" className={box} {...S}><path d="M10 3v6L4.5 18a2 2 0 0 0 1.8 3h11.4a2 2 0 0 0 1.8-3L14 9V3M9 3h6M7.5 14h9" /></svg>; }
function CubeIcon() { return <svg viewBox="0 0 24 24" className={box} {...S}><path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z" /><path d="m4 7.5 8 4.5 8-4.5M12 12v9" /></svg>; }
function DocIcon() { return <svg viewBox="0 0 24 24" className={box} {...S}><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></svg>; }
function GearIcon() { return <svg viewBox="0 0 24 24" className={box} {...S}><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1A1.6 1.6 0 0 0 9 19.4a1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.6 9a1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z" /></svg>; }


/** Language choice. Persisted per browser and sent to the API with every call. */
export function LanguagePicker() {
  const active = currentLanguage();
  return (
    <div className="flex items-center rounded-lg border border-line p-0.5">
      {LANGUAGES.map((l) => (
        <button
          key={l.code}
          onClick={() => l.code !== active && setLanguage(l.code)}
          className={cx(
            "rounded-md px-2 py-1 text-[11px] font-medium transition-colors",
            l.code === active ? "bg-brand-600 text-white" : "text-ink-mute hover:bg-surface-sunken",
          )}
        >
          {l.code.toUpperCase()}
        </button>
      ))}
    </div>
  );
}
