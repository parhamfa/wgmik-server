import React from "react";
import { Navigate } from "react-router-dom";
import { createInitialAdmin, getSetupState } from "../api";

const PASSWORD_MIN_LENGTH = 12;

export default function InstallSetupPage() {
  const [checking, setChecking] = React.useState(true);
  const [alreadySetup, setAlreadySetup] = React.useState(false);
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [confirm, setConfirm] = React.useState("");
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const state = await getSetupState();
        if (!cancelled) setAlreadySetup(!state.needs_initial_setup);
      } catch {
        // If we can't determine state, let the user try; the API still guards.
      } finally {
        if (!cancelled) setChecking(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (!username.trim()) {
      setError("Choose a username");
      return;
    }
    if (password.length < PASSWORD_MIN_LENGTH) {
      setError(`Password must be at least ${PASSWORD_MIN_LENGTH} characters`);
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match");
      return;
    }
    setBusy(true);
    try {
      await createInitialAdmin({ username: username.trim(), password });
      // Setup logs the admin in (cookie set). Hard navigate so the auth
      // provider re-initializes and picks up the session.
      window.location.href = "/";
    } catch (err: any) {
      const message = String(err?.message || "").trim();
      if (message === "Setup already completed") {
        setAlreadySetup(true);
      } else {
        setError(message || "Could not create your account");
      }
      setBusy(false);
    }
  };

  if (checking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4 py-8 text-gray-900 dark:bg-gray-950 dark:text-gray-100">
        <div className="flex aspect-square w-full max-w-[34rem] items-center justify-center rounded-[2rem] border border-gray-200 bg-white p-10 text-center text-sm text-gray-500 shadow-sm dark:border-gray-800 dark:bg-gray-900 dark:text-gray-400">
          Loading...
        </div>
      </div>
    );
  }

  if (alreadySetup) {
    return <Navigate to="/login" replace />;
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-gray-50 px-4 py-8 text-gray-900 dark:bg-gray-950 dark:text-gray-100">
      <section className="flex w-full max-w-[34rem] flex-col justify-center rounded-[2rem] border border-gray-200 bg-white p-8 shadow-sm dark:border-gray-800 dark:bg-gray-900 sm:p-10">
        <div className="mx-auto flex w-full max-w-sm flex-col">
          <div className="mb-8 text-center">
            <h1 className="text-2xl font-bold text-gray-900 dark:text-white sm:text-3xl">
              Welcome
            </h1>
            <p className="mx-auto mt-2 max-w-xs text-sm text-gray-500 dark:text-gray-400">
              Create the administrator account to finish setting up your WireGuard workspace.
            </p>
          </div>

          <form className="space-y-4" onSubmit={handleSubmit}>
            <div className="space-y-2">
              <label htmlFor="setup-username" className="block text-sm font-medium text-gray-600 dark:text-gray-300">
                Username
              </label>
              <input
                id="setup-username"
                type="text"
                autoComplete="username"
                required
                className="block h-14 w-full rounded-full border border-gray-200 bg-gray-50 px-5 text-base text-gray-900 transition placeholder:text-gray-400 focus:border-gray-400 focus:bg-white focus:outline-none focus:ring-4 focus:ring-gray-200 dark:border-gray-800 dark:bg-gray-950 dark:text-white dark:placeholder:text-gray-500 dark:focus:border-gray-600 dark:focus:bg-gray-900 dark:focus:ring-gray-800"
                placeholder="Choose a username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <label htmlFor="setup-password" className="block text-sm font-medium text-gray-600 dark:text-gray-300">
                Password
              </label>
              <input
                id="setup-password"
                type="password"
                autoComplete="new-password"
                required
                className="block h-14 w-full rounded-full border border-gray-200 bg-gray-50 px-5 text-base text-gray-900 transition placeholder:text-gray-400 focus:border-gray-400 focus:bg-white focus:outline-none focus:ring-4 focus:ring-gray-200 dark:border-gray-800 dark:bg-gray-950 dark:text-white dark:placeholder:text-gray-500 dark:focus:border-gray-600 dark:focus:bg-gray-900 dark:focus:ring-gray-800"
                placeholder={`At least ${PASSWORD_MIN_LENGTH} characters`}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>

            <div className="space-y-2">
              <label htmlFor="setup-confirm" className="block text-sm font-medium text-gray-600 dark:text-gray-300">
                Confirm password
              </label>
              <input
                id="setup-confirm"
                type="password"
                autoComplete="new-password"
                required
                className="block h-14 w-full rounded-full border border-gray-200 bg-gray-50 px-5 text-base text-gray-900 transition placeholder:text-gray-400 focus:border-gray-400 focus:bg-white focus:outline-none focus:ring-4 focus:ring-gray-200 dark:border-gray-800 dark:bg-gray-950 dark:text-white dark:placeholder:text-gray-500 dark:focus:border-gray-600 dark:focus:bg-gray-900 dark:focus:ring-gray-800"
                placeholder="Re-enter your password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
              />
            </div>

            <div className="min-h-6 pt-1">
              {error && <div className="text-sm font-medium text-rose-600 dark:text-rose-400">{error}</div>}
            </div>

            <button
              type="submit"
              disabled={busy}
              className="flex h-14 w-full items-center justify-center rounded-full bg-gray-900 px-4 text-sm font-semibold text-white transition hover:bg-black disabled:opacity-50 dark:bg-white dark:text-gray-900 dark:hover:bg-gray-100"
            >
              {busy ? "Creating account..." : "Create admin account"}
            </button>
          </form>
        </div>
      </section>
    </div>
  );
}
