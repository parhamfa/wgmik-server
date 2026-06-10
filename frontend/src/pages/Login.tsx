import React from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { getAuthBootstrap, getSetupState } from "../api";
import { useAuth } from "../auth";

function resolvePostLoginPath(needsOnboarding: boolean, mustChangePassword: boolean, fromPath?: string) {
  if (mustChangePassword) return "/settings";
  if (needsOnboarding) return "/setup";
  if (!fromPath || fromPath === "/login") return "/";
  return fromPath;
}

export default function LoginPage() {
  const { user, loading, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const fromPath = (location.state as { from?: { pathname?: string } } | null)?.from?.pathname;
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [redirecting, setRedirecting] = React.useState(false);
  const [checkingSetup, setCheckingSetup] = React.useState(true);
  const [needsSetup, setNeedsSetup] = React.useState(false);

  const resolveAndNavigate = React.useCallback(async () => {
    setRedirecting(true);
    try {
      const bootstrap = await getAuthBootstrap();
      navigate(resolvePostLoginPath(bootstrap.needs_onboarding, bootstrap.user.must_change_password, fromPath), { replace: true });
    } catch (err: any) {
      setError(err?.message || "Failed to load workspace state");
      setRedirecting(false);
    }
  }, [fromPath, navigate]);

  React.useEffect(() => {
    if (!loading && user) {
      void resolveAndNavigate();
    }
  }, [loading, user, resolveAndNavigate]);

  React.useEffect(() => {
    if (loading || user) {
      // Authenticated (or still resolving auth): no first-run check needed.
      setCheckingSetup(false);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const state = await getSetupState();
        if (!cancelled && state.needs_initial_setup) setNeedsSetup(true);
      } catch {
        // Ignore; fall back to showing the login form.
      } finally {
        if (!cancelled) setCheckingSetup(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loading, user]);

  if (!loading && !user && needsSetup) {
    return <Navigate to="/install" replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login({ username, password });
      await resolveAndNavigate();
    } catch (err: any) {
      const message = String(err?.message || "").trim();
      if (message === "Incorrect username or password" || message === "Unauthorized") {
        setError("Invalid username or password");
      } else {
        setError(message || "Sign in failed");
      }
    } finally {
      setBusy(false);
    }
  };

  if (loading || redirecting || checkingSetup) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4 py-8 text-gray-900 dark:bg-gray-950 dark:text-gray-100">
        <div className="flex aspect-square w-full max-w-[34rem] items-center justify-center rounded-[2rem] border border-gray-200 bg-white p-10 text-center text-sm text-gray-500 shadow-sm dark:border-gray-800 dark:bg-gray-900 dark:text-gray-400">
          Loading...
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4 py-8 text-gray-900 dark:bg-gray-950 dark:text-gray-100">
      <section className="flex aspect-square w-full max-w-[34rem] flex-col justify-center rounded-[2rem] border border-gray-200 bg-white p-8 shadow-sm dark:border-gray-800 dark:bg-gray-900 sm:p-10">
        <div className="mx-auto flex w-full max-w-sm flex-1 flex-col justify-center">
          <div className="mb-8 text-center">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white sm:text-3xl">
              Welcome Back
            </h1>
            <p className="mx-auto mt-2 max-w-xs text-sm text-gray-500 dark:text-gray-400">
              Sign in to manage your WireGuard workspace.
            </p>
          </div>

          <form className="space-y-4" onSubmit={handleSubmit}>
            <div className="space-y-2">
              <label htmlFor="login-username" className="block text-sm font-medium text-gray-600 dark:text-gray-300">
                Username
              </label>
              <div className="relative">
                <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4 text-gray-400 dark:text-gray-500">
                  <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M20 21a8 8 0 1 0-16 0" />
                    <circle cx="12" cy="8" r="4" />
                  </svg>
                </span>
                <input
                  id="login-username"
                  type="text"
                  required
                  className="block h-14 w-full rounded-full border border-gray-200 bg-gray-50 pl-12 pr-4 text-base text-gray-900 transition placeholder:text-gray-400 focus:border-gray-400 focus:bg-white focus:outline-none focus:ring-4 focus:ring-gray-200 dark:border-gray-800 dark:bg-gray-950 dark:text-white dark:placeholder:text-gray-500 dark:focus:border-gray-600 dark:focus:bg-gray-900 dark:focus:ring-gray-800"
                  placeholder="Enter your username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </div>
            </div>

            <div className="space-y-2">
              <label htmlFor="login-password" className="block text-sm font-medium text-gray-600 dark:text-gray-300">
                Password
              </label>
              <div className="relative">
                <span className="pointer-events-none absolute inset-y-0 left-0 flex items-center pl-4 text-gray-400 dark:text-gray-500">
                  <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="4" y="11" width="16" height="9" rx="2" />
                    <path d="M8 11V8a4 4 0 1 1 8 0v3" />
                  </svg>
                </span>
                <input
                  id="login-password"
                  type="password"
                  required
                  className="block h-14 w-full rounded-full border border-gray-200 bg-gray-50 pl-12 pr-4 text-base text-gray-900 transition placeholder:text-gray-400 focus:border-gray-400 focus:bg-white focus:outline-none focus:ring-4 focus:ring-gray-200 dark:border-gray-800 dark:bg-gray-950 dark:text-white dark:placeholder:text-gray-500 dark:focus:border-gray-600 dark:focus:bg-gray-900 dark:focus:ring-gray-800"
                  placeholder="Enter your password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </div>
            </div>

            <div className="min-h-6 pt-1">
              {error && <div className="text-sm font-medium text-rose-600 dark:text-rose-400">{error}</div>}
            </div>

            <button
              type="submit"
              disabled={busy}
              className="flex h-14 w-full items-center justify-center rounded-full bg-gray-900 px-4 text-sm font-semibold text-white transition hover:bg-black disabled:opacity-50 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100"
            >
              {busy ? "Signing in..." : "Sign in"}
            </button>
          </form>
        </div>
      </section>
    </div>
  );
}
