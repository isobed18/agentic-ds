import { Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Datasets, Experiments, Home, Models, Reports, Settings } from "./pages/Catalog";
import { Workflows } from "./pages/Workflows";

export default function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/workflows" element={<Workflows />} />
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
