import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Home, Settings } from "./pages/Catalog";
import { Automation } from "./pages/Automation";

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/automation" element={<Automation />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/staging" element={<Navigate to="/automation" replace />} />
        <Route path="/workflows" element={<Navigate to="/automation?view=runs" replace />} />
        {/* #111: the four global catalogues are gone as destinations -- a
            dataset, run, model or report is browsed inside the project that
            produced it now. Old bookmarks land on the project-first home
            rather than dead-ending on a 404. */}
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
