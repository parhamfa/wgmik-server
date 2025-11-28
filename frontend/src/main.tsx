import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, Link } from "react-router-dom";
import Wizard from "./pages/Wizard";
import SettingsPage from "./pages/Settings";
import PeerDetail from "./pages/PeerDetail";
import DashboardPage from "./pages/Dashboard";
import NotFound from "./pages/NotFound";
import "./styles.css";

function Card(props: React.HTMLAttributes<HTMLDivElement>) {
  const base = "rounded-xl ring-1 ring-gray-200 bg-white shadow-sm hover:shadow-md transition";
  return <div className={base + (props.className ? " " + props.className : "")} {...props} />;
}

function StatusPill({ online, last }: { online: boolean; last?: string }) {
  const bg = online ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800";
  const dot = online ? "bg-green-500" : "bg-red-500";
  return (
    <div className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${bg}`}>
      <span className={`dot ${dot} pulse`} />
      {online ? "Online" : `Last seen ${last ?? "—"}`}
    </div>
  );
}

const Dashboard = DashboardPage;

function Settings() {
  return (
    <div className="max-w-2xl mx-auto px-4 md:px-6 py-6">
      <h1 className="text-xl font-semibold text-gray-900 mb-4">Settings</h1>
      <Card className="p-4">Coming soon</Card>
    </div>
  );
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/setup" element={<Wizard />} />
        <Route path="/peer/:id" element={<PeerDetail />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </BrowserRouter>
  );
}

const root = createRoot(document.getElementById("root")!);
root.render(<App />);


