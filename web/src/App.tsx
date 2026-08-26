import { Navigate, Route, Routes, useNavigate } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Datasets, Experiments, Home, Models, Reports, Settings } from "./pages/Catalog";
import { Automation } from "./pages/Automation";
import { YourData } from "./pages/YourData";

export default function App() {
  const navigate = useNavigate();
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route
          path="/explore"
          element={<YourData onOpen={(id) => navigate(`/automation?source=${encodeURIComponent(id)}`)} />}
        />
        <Route path="/automation" element={<Automation />} />
        <Route path="/staging" element={<Navigate to="/automation" replace />} />
        <Route path="/workflows" element={<Navigate to="/automation?view=runs" replace />} />
        <Route path="/datasets" element={<Datasets />} />
        <Route path="/experiments" element={<Experiments />} />
        <Route path="/models" element={<Models />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Home />} />
      </Routes>
    </Shell>
  );
}
