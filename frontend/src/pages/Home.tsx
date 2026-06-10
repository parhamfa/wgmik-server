import React from "react";
import { Navigate } from "react-router-dom";
import { getAuthBootstrap } from "../api";
import DashboardPage from "./Dashboard";

export default function HomePage() {
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [needsOnboarding, setNeedsOnboarding] = React.useState(false);

  const loadBootstrap = React.useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const bootstrap = await getAuthBootstrap();
      setNeedsOnboarding(bootstrap.needs_onboarding);
    } catch (err: any) {
      setError(err?.message || "Failed to load workspace state");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    void loadBootstrap();
  }, [loadBootstrap]);

  if (loading) {
    return (
      <div className="mx-auto px-4 md:px-6 py-6">
        <div className="text-sm text-gray-500 dark:text-gray-400">Loading...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="mx-auto px-4 md:px-6 py-6">
        <div className="mx-auto my-12 md:my-16 w-full max-w-[720px] rounded-3xl ring-1 ring-rose-200 bg-white dark:bg-gray-900 dark:ring-rose-500/30 shadow-sm p-10 text-center grid gap-4">
          <div className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Workspace unavailable</div>
          <div className="text-sm text-gray-500 dark:text-gray-400">{error}</div>
          <div className="pt-2">
            <button
              type="button"
              onClick={() => void loadBootstrap()}
              className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (needsOnboarding) {
    return <Navigate to="/setup" replace />;
  }

  return <DashboardPage />;
}
