import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Home, Settings } from "./pages/Catalog";
import { Automation } from "./pages/Automation";

/**
 * Redirect that carries the query string across. The project workspace keeps
 * the open project in `?automation=<id>`, so a bare `<Navigate>` from the old
 * /automation route would drop the deep-link and land on the empty library.
 */
function RedirectWithQuery({ to }: { to: string }) {
  const { search } = useLocation();
  return <Navigate to={`${to}${search}`} replace />;
}

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Home />} />
        {/* #111: "projects" is the customer's word, so /projects is the canonical
            route. The internal storage/execution vocabulary stays "automation"
            (persisted ids, /api/automations, the ?automation=<id> deep link),
            an explicit compatibility boundary rather than a half-rename. */}
        <Route path="/projects" element={<Automation />} />
        <Route path="/automation" element={<RedirectWithQuery to="/projects" />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/staging" element={<Navigate to="/projects" replace />} />
        <Route path="/workflows" element={<Navigate to="/projects?view=runs" replace />} />
        {/* The four global catalogues are gone as destinations (#80); their
            outputs live inside the project that produced them now. Old bookmarks
            land on the project-first home rather than dead-ending on a 404. */}
        <Route path="/explore" element={<Navigate to="/" replace />} />
        <Route path="/datasets" element={<Navigate to="/" replace />} />
        <Route path="/experiments" element={<Navigate to="/" replace />} />
        <Route path="/models" element={<Navigate to="/" replace />} />
        <Route path="/reports" element={<Navigate to="/" replace />} />
        <Route path="*" element={<Home />} />
      </Routes>
    </Shell>
  );
}
