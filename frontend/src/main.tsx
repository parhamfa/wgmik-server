import React from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, Link } from "react-router-dom";

import SettingsPage from "./pages/Settings";
import PeerDetail from "./pages/PeerDetail";
import HomePage from "./pages/Home";
import FairUsagePage from "./pages/FairUsage";
import TelegramPage from "./pages/Telegram";
import NotFound from "./pages/NotFound";
import LoginPage from "./pages/Login";
import InstallSetupPage from "./pages/InstallSetup";
import "./styles.css";

type ThemeMode = "light" | "dark" | "system";
const THEME_KEY = "wgmik.theme";

function resolveTheme(mode: ThemeMode): "light" | "dark" {
  if (mode === "system") {
    try {
      return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    } catch {
      return "light";
    }
  }
  return mode;
}

function applyTheme(mode: ThemeMode) {
  const root = document.documentElement;
  if (resolveTheme(mode) === "dark") root.classList.add("dark");
  else root.classList.remove("dark");
}

function getInitialTheme(): ThemeMode {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "dark" || saved === "light" || saved === "system") return saved;
  } catch { }
  return "system";
}

const THEME_CYCLE: Record<ThemeMode, ThemeMode> = {
  light: "dark",
  dark: "system",
  system: "light",
};

const THEME_LABELS: Record<ThemeMode, { title: string; next: string }> = {
  light: { title: "Light mode", next: "Switch to dark mode" },
  dark: { title: "Dark mode", next: "Switch to system theme" },
  system: { title: "System theme", next: "Switch to light mode" },
};

function ThemeFab() {
  const [mode, setMode] = React.useState<ThemeMode>(() => getInitialTheme());

  React.useEffect(() => {
    applyTheme(mode);
    try { localStorage.setItem(THEME_KEY, mode); } catch { }

    if (mode !== "system") return;

    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => applyTheme("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [mode]);

  const { title, next } = THEME_LABELS[mode];

  return (
    <button
      type="button"
      onClick={() => setMode((m) => THEME_CYCLE[m])}
      className="fixed bottom-4 right-4 z-50 h-12 w-12 rounded-full ring-1 shadow-sm hover:shadow-md transition flex items-center justify-center bg-white text-gray-900 ring-gray-300 dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-700"
      aria-label={next}
      title={title}
    >
      {mode === "light" ? (
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
        </svg>
      ) : mode === "dark" ? (
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 12.79A9 9 0 1 1 11.21 3a7 7 0 0 0 9.79 9.79z" />
        </svg>
      ) : (
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <defs>
            <clipPath id="theme-system-right">
              <path d="M12 3a9 9 0 0 1 0 18V3Z" />
            </clipPath>
          </defs>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 3v18" />
          <g clipPath="url(#theme-system-right)">
            <path d="M12 6h9M12 9.5h9M12 13h9M12 16.5h9" />
          </g>
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
  if (user.must_change_password && location.pathname !== "/settings") {
    return <Navigate to="/settings" replace />;
  }
  return <Outlet />;
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="*" element={
          <AuthProvider>
            <Routes>
              <Route path="/install" element={<InstallSetupPage />} />
              <Route path="/login" element={<LoginPage />} />

              <Route element={<RequireAuth />}>
                <Route path="/" element={<HomePage />} />
                <Route path="/setup" element={<LoginSetup />} />
                <Route path="/settings" element={<SettingsPage />} />
                <Route path="/fair-usage" element={<FairUsagePage />} />
                <Route path="/peer/:id" element={<PeerDetail />} />
                <Route path="/telegram" element={<TelegramPage />} />
              </Route>

              <Route path="*" element={<NotFound />} />
            </Routes>
            <ThemeFab />
          </AuthProvider>
        } />
      </Routes>
    </BrowserRouter>
  );
}

const root = createRoot(document.getElementById("root")!);
root.render(<App />);
