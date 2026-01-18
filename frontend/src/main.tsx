import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, Link } from "react-router-dom";

import SettingsPage from "./pages/Settings";
import PeerDetail from "./pages/PeerDetail";
import DashboardPage from "./pages/Dashboard";
import NotFound from "./pages/NotFound";
import "./styles.css";

type ThemeMode = "light" | "dark";
const THEME_KEY = "wgmik.theme";

function applyTheme(mode: ThemeMode) {
  const root = document.documentElement;
  if (mode === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
}

function getInitialTheme(): ThemeMode {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "dark" || saved === "light") return saved;
  } catch { }
  // Default: follow OS preference
  try {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

function ThemeFab() {
  const [mode, setMode] = React.useState<ThemeMode>(() => getInitialTheme());

  React.useEffect(() => {
    applyTheme(mode);
    try { localStorage.setItem(THEME_KEY, mode); } catch { }
  }, [mode]);

  return (
    <button
      type="button"
      onClick={() => setMode((m) => (m === "dark" ? "light" : "dark"))}
      className="fixed bottom-4 right-4 z-50 h-12 w-12 rounded-full ring-1 shadow-sm hover:shadow-md transition flex items-center justify-center bg-white text-gray-900 ring-gray-300 dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-700"
      aria-label={mode === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      title={mode === "dark" ? "Light mode" : "Dark mode"}
    >
      {mode === "dark" ? (
        // Sun icon
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
        </svg>
      ) : (
        // Moon icon
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3a7 7 0 0 0 9.79 9.79z" />
        </svg>
      )}
    </button>
  );
}

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

import { AuthProvider, useAuth } from "./auth";
import LoginSetup from "./pages/LoginSetup";

import { Navigate, useLocation, Outlet } from "react-router-dom";

function RequireAuth() {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <div className="p-10 text-center">Loading...</div>;
  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }
  return <Outlet />;
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginSetup />} />
          <Route path="/setup" element={<LoginSetup />} />

          <Route element={<RequireAuth />}>
            <Route path="/" element={<Dashboard />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="/peer/:id" element={<PeerDetail />} />

          </Route>

          <Route path="*" element={<NotFound />} />
        </Routes>
        <ThemeFab />
      </BrowserRouter>
    </AuthProvider>
  );
}

const root = createRoot(document.getElementById("root")!);
root.render(<App />);


