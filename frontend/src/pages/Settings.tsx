import React from "react";
import { Link } from "react-router-dom";
import { getSettings, putSettings, listRouters, createRouter, updateRouter, deleteRouter, getRouterDeleteImpact, testRouter, setRouterEnabled, purgeUsage, purgePeers, getUsageMaintenanceStatus, runUsageMaintenance, cancelUsageMaintenance, listUsers, createUser, updateUserAccount, resetUserPassword, deleteUserAccount, changePassword, type LocalUserDTO, type Router, type RouterDeleteImpactDTO, type RouterProto, type UsageMaintenanceStatusDTO } from "../api";
import { useAuth } from "../auth";
import { useLooseNumberInput } from "../hooks/useLooseNumberInput";
import { formatCalendarDateTime } from "../datetimeLocal";

function getUtcOffsetMinutes(timeZone: string, date: Date) {
  // Robust cross-browser offset calc without relying on timeZoneName formatting.
  // Returns minutes east of UTC (e.g. Tehran => +210).
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const parts = dtf.formatToParts(date);
  const map: Record<string, string> = {};
  for (const p of parts) {
    if (p.type !== "literal") map[p.type] = p.value;
  }
  const asUtc = Date.UTC(
    Number(map.year),
    Number(map.month) - 1,
    Number(map.day),
    Number(map.hour),
    Number(map.minute),
    Number(map.second)
  );
  return Math.round((asUtc - date.getTime()) / 60000);
}

function fmtUtcOffset(offsetMinutes: number) {
  const sign = offsetMinutes >= 0 ? "+" : "-";
  const abs = Math.abs(offsetMinutes);
  const hh = Math.floor(abs / 60);
  const mm = abs % 60;
  return `${sign}${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`;
}

function formatBytes(bytes?: number | null) {
  if (!bytes || bytes <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes;
  let idx = 0;
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024;
    idx += 1;
  }
  return `${value >= 10 || idx === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[idx]}`;
}

function formatCount(value?: number | null) {
  return Number(value || 0).toLocaleString();
}

function formatDuration(seconds?: number | null) {
  if (seconds === null || typeof seconds === "undefined" || !Number.isFinite(seconds) || seconds < 0) return "Estimating";
  const total = Math.max(0, Math.floor(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m > 0) return `${m}m ${String(s).padStart(2, "0")}s`;
  return `${s}s`;
}

function isAccountLocked(lockedUntil?: string | null) {
  return !!lockedUntil && new Date(lockedUntil).getTime() > Date.now();
}

export default function SettingsPage() {
  const { user, logout } = useAuth();
  const [form, setForm] = React.useState({
    poll_interval_seconds: 30,
    online_threshold_seconds: 15,
    monthly_reset_day: 1,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    date_calendar: "gregorian" as "gregorian" | "persian",
    week_start_day: 0,
    show_kind_pills: true,
    show_hw_stats: true,
    dashboard_peer_preview_count: 6,
    peer_default_scope_unit: "minutes",
    peer_default_scope_value: 60,
    dashboard_scope_unit: "hours",
    dashboard_scope_value: 24,
    dashboard_router_scope: "all" as "all" | "selected",
    dashboard_selected_router_ids: [] as number[],
    dashboard_filter_status: "all",
    dashboard_sort_by: "created",
    dashboard_time_mode: "rolling" as "today" | "this_month" | "all_time" | "rolling" | "custom",
    dashboard_custom_start: "",
    dashboard_custom_end: "",
    dashboard_time_frame_today: false,
    peer_time_mode: "rolling" as "today" | "this_month" | "all_time" | "rolling" | "custom",
    peer_custom_start: "",
    peer_custom_end: "",
    peer_time_frame_today: false,
    raw_sample_retention_hours: 24,
    minute_rollup_retention_days: 90,
    daily_rollup_retention_days: 0,
    usage_maintenance_auto_enabled: false,
    usage_maintenance_auto_frequency: "daily" as "daily" | "every_n_days" | "weekly",
    usage_maintenance_auto_interval_days: 2,
    usage_maintenance_auto_weekday: 6,
    usage_maintenance_auto_time: "04:30",
    usage_maintenance_backup_keep: 3,
  });
  const [err, setErr] = React.useState("");
  const [saveState, setSaveState] = React.useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const saveTimerRef = React.useRef<number | null>(null);
  const savingRef = React.useRef(false);
  const pendingRef = React.useRef(false);
  const lastSavedRef = React.useRef<any | null>(null);
  const [routers, setRouters] = React.useState<Router[]>([]);
  const [routersLoaded, setRoutersLoaded] = React.useState(false);
  const [routerMsg, setRouterMsg] = React.useState("");
  const [routerErr, setRouterErr] = React.useState("");
  const [routerBusy, setRouterBusy] = React.useState(false);
  const [testBusyId, setTestBusyId] = React.useState<number | null>(null);
  const [testStatus, setTestStatus] = React.useState<Record<number, string>>({});
  const [pauseBusyId, setPauseBusyId] = React.useState<number | null>(null);
  const [showRouterModal, setShowRouterModal] = React.useState(false);
  const [editingRouter, setEditingRouter] = React.useState<Router | null>(null);
  const defaultProtoPort: Record<RouterProto, number> = { rest: 443, "rest-http": 80, api: 8729, "api-plain": 8728 };
  const [routerForm, setRouterForm] = React.useState({
    name: "",
    host: "",
    proto: "rest" as RouterProto,
    port: 443,
    username: "",
    password: "",
    tls_verify: true,
  });
  const [maintBusy, setMaintBusy] = React.useState<string | null>(null);
  const [maintMsg, setMaintMsg] = React.useState("");
  const [maintErr, setMaintErr] = React.useState("");
  const [confirmAction, setConfirmAction] = React.useState<"usage" | "peers" | null>(null);
  const [confirmDeleteRouter, setConfirmDeleteRouter] = React.useState<Router | null>(null);
  const [routerDeleteImpact, setRouterDeleteImpact] = React.useState<RouterDeleteImpactDTO | null>(null);
  const [routerDeleteImpactBusy, setRouterDeleteImpactBusy] = React.useState(false);
  const [routerDeleteImpactErr, setRouterDeleteImpactErr] = React.useState("");
  const [routerDeleteBusy, setRouterDeleteBusy] = React.useState(false);
  const [routerDeleteConfirmName, setRouterDeleteConfirmName] = React.useState("");
  const [usageMaintenance, setUsageMaintenance] = React.useState<UsageMaintenanceStatusDTO | null>(null);
  const [showMaintenanceModal, setShowMaintenanceModal] = React.useState(false);
  const [maintenanceCancelBusy, setMaintenanceCancelBusy] = React.useState(false);

  // User management state
  const [users, setUsers] = React.useState<LocalUserDTO[]>([]);
  const [newUsername, setNewUsername] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const [userErr, setUserErr] = React.useState("");
  const [userMsg, setUserMsg] = React.useState("");
  const [userBusy, setUserBusy] = React.useState(false);
  const [resetPasswordUser, setResetPasswordUser] = React.useState<LocalUserDTO | null>(null);
  const [resetPasswordValue, setResetPasswordValue] = React.useState("");
  const [resetPasswordBusy, setResetPasswordBusy] = React.useState(false);
  const [myPasswordCurrent, setMyPasswordCurrent] = React.useState("");
  const [myPasswordNext, setMyPasswordNext] = React.useState("");
  const [myPasswordConfirm, setMyPasswordConfirm] = React.useState("");
  const [showMyPasswordModal, setShowMyPasswordModal] = React.useState(false);
  const [myPasswordBusy, setMyPasswordBusy] = React.useState(false);
  const [myPasswordErr, setMyPasswordErr] = React.useState("");
  const [myPasswordMsg, setMyPasswordMsg] = React.useState("");

  const pollIntervalInput = useLooseNumberInput(
    form.poll_interval_seconds,
    (n) => setForm((f) => ({ ...f, poll_interval_seconds: n })),
    { min: 5, emptyFallback: 5 },
  );
  const onlineThresholdInput = useLooseNumberInput(
    form.online_threshold_seconds,
    (n) => setForm((f) => ({ ...f, online_threshold_seconds: n })),
    { min: 5, emptyFallback: 5 },
  );
  const monthlyResetInput = useLooseNumberInput(
    form.monthly_reset_day,
    (n) => setForm((f) => ({ ...f, monthly_reset_day: n })),
    { min: 1, max: 31, emptyFallback: 1 },
  );
  const rawRetentionInput = useLooseNumberInput(
    form.raw_sample_retention_hours,
    (n) => setForm((f) => ({ ...f, raw_sample_retention_hours: n })),
    { min: 1, max: 8760, emptyFallback: 1 },
  );
  const minuteRetentionInput = useLooseNumberInput(
    form.minute_rollup_retention_days,
    (n) => setForm((f) => ({ ...f, minute_rollup_retention_days: n })),
    { min: 1, max: 3650, emptyFallback: 1 },
  );
  const dailyRetentionInput = useLooseNumberInput(
    form.daily_rollup_retention_days,
    (n) => setForm((f) => ({ ...f, daily_rollup_retention_days: n })),
    { min: 0, max: 36500, emptyFallback: 0 },
  );
  const autoIntervalInput = useLooseNumberInput(
    form.usage_maintenance_auto_interval_days,
    (n) => setForm((f) => ({ ...f, usage_maintenance_auto_interval_days: n })),
    { min: 2, max: 30, emptyFallback: 2 },
  );
  const backupKeepInput = useLooseNumberInput(
    form.usage_maintenance_backup_keep,
    (n) => setForm((f) => ({ ...f, usage_maintenance_backup_keep: n })),
    { min: 1, max: 50, emptyFallback: 3 },
  );

  const maintenanceProgress = Math.max(0, Math.min(100, Number(usageMaintenance?.progress_percent ?? 0)));
  const maintenancePhaseProgress = Math.max(0, Math.min(100, Number(usageMaintenance?.phase_progress_percent ?? 0)));
  const maintenanceIsTerminal = !!usageMaintenance && !usageMaintenance.running && ["complete", "failed", "cancelled"].includes(usageMaintenance.phase);
  const maintenanceStatusLabel = usageMaintenance?.phase_label || usageMaintenance?.phase || "Idle";

  const timezoneOptions = React.useMemo(() => {
    const supportedValuesOf = (Intl as any)?.supportedValuesOf as undefined | ((key: string) => string[]);
    if (!supportedValuesOf) return [];
    let zones: string[] = [];
    try {
      zones = supportedValuesOf("timeZone") || [];
    } catch {
      zones = [];
    }
    const now = new Date();
    return zones
      .map((z) => ({ z, off: getUtcOffsetMinutes(z, now) }))
      .sort((a, b) => a.off - b.off || a.z.localeCompare(b.z))
      .map((x) => ({ value: x.z, label: `(UTC${fmtUtcOffset(x.off)}) ${x.z}` }));
  }, []);

  const loadSettings = React.useCallback(async () => {
    try {
      const s = await getSettings();
      setForm(s);
      lastSavedRef.current = s;
      setSaveState("idle");
      setErr("");
    } catch {
      /* ignore */
    }
  }, []);

  const isDirty = React.useMemo(() => {
    const last = lastSavedRef.current;
    if (!last) return false;
    const keys = Object.keys(form) as (keyof typeof form)[];
    return keys.some((k) => String((form as any)[k]) !== String((last as any)[k]));
  }, [form]);

  const doSave = React.useCallback(async () => {
    if (!lastSavedRef.current) return;
    if (!isDirty) {
      if (saveState === "dirty") setSaveState("idle");
      return;
    }
    if (savingRef.current) {
      pendingRef.current = true;
      return;
    }
    savingRef.current = true;
    setErr("");
    setSaveState("saving");
    try {
      const saved = await putSettings(form);
      setForm(saved);
      lastSavedRef.current = saved;
      setSaveState("saved");
      window.setTimeout(() => {
        setSaveState((s) => (s === "saved" ? "idle" : s));
      }, 1200);
    } catch (e: any) {
      setErr(e?.message || "Save failed");
      setSaveState("error");
    } finally {
      savingRef.current = false;
      if (pendingRef.current) {
        pendingRef.current = false;
        doSave();
      }
    }
  }, [form, isDirty, saveState]);

  // Debounced auto-save for all settings.
  React.useEffect(() => {
    if (!lastSavedRef.current) return;
    if (isDirty) {
      setSaveState((s) => (s === "saving" ? s : "dirty"));
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = window.setTimeout(() => {
        doSave();
      }, 800);
      return () => {
        if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      };
    } else {
      if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
      if (saveState === "dirty") setSaveState("idle");
    }
  }, [form, isDirty, doSave, saveState]);

  const loadRouters = React.useCallback(async () => {
    try {
      setRoutersLoaded(false);
      const rows = await listRouters();
      setRouters(rows);
      setRouterErr("");
    } catch (e: any) {
      setRouterErr(e?.message || "Failed to load connection profiles");
    } finally {
      setRoutersLoaded(true);
    }
  }, []);

  React.useEffect(() => { loadSettings(); }, [loadSettings]);
  React.useEffect(() => { loadRouters(); }, [loadRouters]);
  const loadUsageMaintenance = React.useCallback(async () => {
    try {
      const status = await getUsageMaintenanceStatus();
      setUsageMaintenance(status);
    } catch {
      setUsageMaintenance(null);
    }
  }, []);
  React.useEffect(() => { loadUsageMaintenance(); }, [loadUsageMaintenance]);
  React.useEffect(() => {
    if (!usageMaintenance?.running) return;
    const timer = window.setInterval(() => {
      loadUsageMaintenance();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [usageMaintenance?.running, loadUsageMaintenance]);
  // Refresh next-scheduled-run info after settings (schedule) saves.
  React.useEffect(() => {
    if (saveState === "saved") loadUsageMaintenance();
  }, [saveState, loadUsageMaintenance]);
  React.useEffect(() => {
    if (!confirmDeleteRouter) return;
    let cancelled = false;
    setRouterDeleteImpact(null);
    setRouterDeleteImpactErr("");
    setRouterDeleteImpactBusy(true);
    setRouterDeleteBusy(false);
    setRouterDeleteConfirmName("");
    (async () => {
      try {
        const impact = await getRouterDeleteImpact(confirmDeleteRouter.id);
        if (!cancelled) setRouterDeleteImpact(impact);
      } catch (e: any) {
        if (!cancelled) setRouterDeleteImpactErr(e?.message || "Failed to load delete impact");
      } finally {
        if (!cancelled) setRouterDeleteImpactBusy(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [confirmDeleteRouter]);

  // User management
  const loadUsers = React.useCallback(async () => {
    try {
      const data = await listUsers();
      setUsers(data);
    } catch {
      setUsers([]);
    }
  }, []);

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setUserErr("");
    setUserMsg("");
    try {
      setUserBusy(true);
      await createUser({ username: newUsername, password: newPassword });
      setUserMsg(`User ${newUsername} created.`);
      setNewUsername("");
      setNewPassword("");
      await loadUsers();
    } catch (err: any) {
      setUserErr(err.message || "Failed to create user");
    } finally {
      setUserBusy(false);
    }
  };

  const handleUpdateUser = async (id: number, payload: { is_active?: boolean; unlock?: boolean }, successMessage: string) => {
    try {
      setUserBusy(true);
      await updateUserAccount(id, payload);
      setUserMsg(successMessage);
      setUserErr("");
      await loadUsers();
    } catch (err: any) {
      setUserErr(err.message || "Failed to update user");
    } finally {
      setUserBusy(false);
    }
  };

  const handleDeleteUser = async (row: LocalUserDTO) => {
    if (!confirm(`Delete inactive account ${row.username}? This is permanent.`)) return;
    try {
      setUserBusy(true);
      await deleteUserAccount(row.id);
      setUserMsg(`Deleted ${row.username}.`);
      setUserErr("");
      await loadUsers();
    } catch (err: any) {
      setUserErr(err.message || "Failed to delete user");
    } finally {
      setUserBusy(false);
    }
  };

  const handleResetPassword = async () => {
    if (!resetPasswordUser) return;
    try {
      setResetPasswordBusy(true);
      await resetUserPassword(resetPasswordUser.id, { new_password: resetPasswordValue });
      setUserMsg(`Temporary password set for ${resetPasswordUser.username}. They must change it on next login.`);
      setUserErr("");
      setResetPasswordUser(null);
      setResetPasswordValue("");
      await loadUsers();
    } catch (err: any) {
      setUserErr(err.message || "Failed to reset password");
    } finally {
      setResetPasswordBusy(false);
    }
  };

  const handleMyPasswordChange = async (e: React.FormEvent) => {
    e.preventDefault();
    setMyPasswordErr("");
    setMyPasswordMsg("");
    if (myPasswordNext !== myPasswordConfirm) {
      setMyPasswordErr("New password confirmation does not match");
      return;
    }
    try {
      setMyPasswordBusy(true);
      await changePassword({ current_password: myPasswordCurrent, new_password: myPasswordNext });
      setMyPasswordMsg("Password changed. Sign in again with the new password.");
      setMyPasswordCurrent("");
      setMyPasswordNext("");
      setMyPasswordConfirm("");
      await logout();
    } catch (err: any) {
      setMyPasswordErr(err.message || "Failed to change password");
    } finally {
      setMyPasswordBusy(false);
    }
  };

  React.useEffect(() => { loadUsers(); }, [loadUsers]);
  const retentionLocked = !!usageMaintenance?.running;
  const blockingPasswordChange = !!user?.must_change_password;
  React.useEffect(() => {
    if (blockingPasswordChange) {
      setShowMyPasswordModal(true);
    }
  }, [blockingPasswordChange]);

  function openRouterModal(row?: Router) {
    if (row) {
      setEditingRouter(row);
      setRouterForm({
        name: row.name,
        host: row.host,
        proto: row.proto,
        port: row.port,
        username: row.username,
        password: "",
        tls_verify: row.tls_verify,
      });
    } else {
      setEditingRouter(null);
      setRouterForm({
        name: "",
        host: "",
        proto: "rest",
        port: 443,
        username: "",
        password: "",
        tls_verify: true,
      });
    }
    setRouterErr("");
    setRouterMsg("");
    setShowRouterModal(true);
  }

  async function runRouterTest(row: Router) {
    try {
      setTestBusyId(row.id);
      const res = await testRouter(row.id);
      setTestStatus(prev => ({ ...prev, [row.id]: "OK" }));
      setRouters(prev => prev.map(x => x.id === row.id ? {
        ...x,
        ros_version: res.ros_version ?? x.ros_version,
        ros_version_checked_at: res.ros_version_checked_at ?? x.ros_version_checked_at,
        ros_supported: res.ros_supported ?? x.ros_supported,
      } : x));
    } catch (e: any) {
      setTestStatus(prev => ({ ...prev, [row.id]: e?.message || "Failed" }));
    } finally {
      setTestBusyId(null);
    }
  }

  async function handleSaveRouter() {
    if (!routerForm.name.trim() || !routerForm.host.trim() || !routerForm.username.trim()) {
      setRouterErr("Name, host, and username are required");
      return;
    }
    if (!editingRouter && !routerForm.password) {
      setRouterErr("Password is required");
      return;
    }
    setRouterErr("");
    setRouterMsg("");
    try {
      setRouterBusy(true);
      const payload: any = {
        name: routerForm.name.trim(),
        host: routerForm.host.trim(),
        proto: routerForm.proto,
        port: Number(routerForm.port) || defaultProtoPort[routerForm.proto],
        username: routerForm.username.trim(),
        tls_verify: routerForm.tls_verify,
      };
      if (routerForm.password) {
        payload.password = routerForm.password;
      }
      if (editingRouter) {
        await updateRouter(editingRouter.id, payload);
        setRouterMsg("Router updated");
      } else {
        if (!payload.password) {
          payload.password = routerForm.password;
        }
        await createRouter(payload);
        setRouterMsg("Router added");
      }
      setShowRouterModal(false);
      await loadRouters();
    } catch (e: any) {
      setRouterErr(e?.message || "Failed to save router");
    } finally {
      setRouterBusy(false);
    }
  }

  const routerDeleteNameMatches = !!confirmDeleteRouter && routerDeleteConfirmName.trim() === confirmDeleteRouter.name;
  const routerDeleteSummaryItems = routerDeleteImpact ? [
    { label: "Peers", value: formatCount(routerDeleteImpact.peer_count) },
    { label: "Selected peers", value: formatCount(routerDeleteImpact.selected_peer_count) },
    { label: "Raw samples", value: formatCount(routerDeleteImpact.usage_sample_rows) },
    { label: "Minute rollups", value: formatCount(routerDeleteImpact.usage_minute_rows) },
    { label: "Daily rollups", value: formatCount(routerDeleteImpact.usage_daily_rows) },
    { label: "Monthly rollups", value: formatCount(routerDeleteImpact.usage_monthly_rows) },
    { label: "Actions", value: formatCount(routerDeleteImpact.action_count) },
    { label: "Quotas", value: formatCount(routerDeleteImpact.quota_count) },
    { label: "Telegram bindings", value: formatCount(routerDeleteImpact.telegram_binding_count) },
    { label: "Telegram logs", value: formatCount(routerDeleteImpact.telegram_log_count) },
    { label: "Signup tokens touched", value: formatCount(routerDeleteImpact.signup_token_count) },
    { label: "Fair-usage assignments", value: formatCount(routerDeleteImpact.fair_usage_assignment_count) },
    { label: "Fair-usage state rows", value: formatCount(routerDeleteImpact.fair_usage_state_count) },
    { label: "Router rules", value: formatCount(routerDeleteImpact.router_rule_count) },
    { label: "Merge ledger rows", value: formatCount(routerDeleteImpact.merge_ledger_count) },
    { label: "Stored peer settings", value: formatCount(routerDeleteImpact.peer_setting_count) },
  ] : [];

  return (
    <div className="max-w-4xl mx-auto px-4 md:px-6 py-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Settings</h1>
        <Link to="/" className="inline-flex items-center gap-2 rounded-full bg-white text-gray-900 px-4 py-1.5 text-sm ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-800">
          ← Dashboard
        </Link>
      </div>
      {blockingPasswordChange && (
        <div className="rounded-3xl ring-1 ring-amber-200 bg-amber-50 dark:bg-amber-500/10 dark:ring-amber-400/20 p-5 mb-6">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-amber-900 dark:text-amber-100">Password change required</div>
              <div className="mt-1 text-sm text-amber-800 dark:text-amber-200">
                Another admin reset this account. Change the temporary password before using the rest of the application.
              </div>
            </div>
            <button
              type="button"
              onClick={() => setShowMyPasswordModal(true)}
              className="shrink-0 rounded-full bg-amber-900 text-white px-4 py-2 text-sm shadow hover:bg-amber-950 dark:bg-amber-200 dark:text-amber-950 dark:hover:bg-amber-100"
            >
              Change password
            </button>
          </div>
        </div>
      )}
      {!blockingPasswordChange && (
        <>
      <div className="rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 shadow-sm p-5 mb-6">
        <div className="flex items-center justify-between gap-3 mb-4">
          <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">App settings</div>
          <div className="flex items-center gap-2">
            {saveState !== "idle" && (
              <div
                className={[
                  "text-xs",
                  saveState === "saving" ? "text-gray-500 dark:text-gray-400"
                    : saveState === "saved" ? "text-green-700 dark:text-green-300"
                      : saveState === "error" ? "text-rose-600 dark:text-rose-300"
                        : "text-amber-700 dark:text-amber-300",
                ].join(" ")}
              >
                {saveState === "saving" ? "Saving…" : saveState === "saved" ? "Saved" : saveState === "error" ? "Error" : "Unsaved"}
              </div>
            )}
            {isDirty && lastSavedRef.current && (
              <button
                type="button"
                onClick={() => {
                  setErr("");
                  setSaveState("idle");
                  setForm(lastSavedRef.current);
                }}
                className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
              >
                Revert
              </button>
            )}
          </div>
        </div>
        <div className="grid gap-4">
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Poll interval (seconds)</label>
            <input type="number" min={5} className="w-40 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700" {...pollIntervalInput} />
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Online threshold (seconds)</label>
            <input type="number" min={5} className="w-40 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700" {...onlineThresholdInput} />
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Monthly reset day (selected calendar)</label>
            <input type="number" min={1} max={31} className="w-40 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700" {...monthlyResetInput} />
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Timezone</label>
            {timezoneOptions.length ? (
              <select
                className="w-full md:w-80 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950"
                value={form.timezone}
                onChange={(e) => setForm({ ...form, timezone: e.target.value })}
              >
                {timezoneOptions.map((tz) => (
                  <option key={tz.value} value={tz.value}>
                    {tz.label}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className="w-full md:w-80 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
                value={form.timezone}
                onChange={(e) => setForm({ ...form, timezone: e.target.value })}
              />
            )}
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Calendar</label>
            <select
              className="w-full md:w-80 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950"
              value={form.date_calendar}
              onChange={(e) => setForm({ ...form, date_calendar: e.target.value as "gregorian" | "persian" })}
            >
              <option value="gregorian">Gregorian</option>
              <option value="persian">Persian / Jalali</option>
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Week starts on</label>
            <select
              className="w-full md:w-80 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950"
              value={form.week_start_day}
              onChange={(e) => setForm({ ...form, week_start_day: Number(e.target.value) })}
            >
              <option value={0}>Monday</option>
              <option value={1}>Tuesday</option>
              <option value={2}>Wednesday</option>
              <option value={3}>Thursday</option>
              <option value={4}>Friday</option>
              <option value={5}>Saturday</option>
              <option value={6}>Sunday</option>
            </select>
          </div>
          <div className="grid gap-2 pt-2 border-t border-gray-100 dark:border-gray-800">
            <label className="inline-flex items-center gap-2 text-xs text-gray-700 dark:text-gray-200">
              <input
                type="checkbox"
                className="rounded border-gray-300 text-gray-900 focus:ring-gray-300 dark:border-gray-700 dark:text-gray-100 dark:focus:ring-gray-700"
                checked={form.show_kind_pills}
                onChange={(e) => setForm({ ...form, show_kind_pills: e.target.checked })}
              />
              Show inbound/outbound pills on cards
            </label>
            <label className="inline-flex items-center gap-2 text-xs text-gray-700 dark:text-gray-200">
              <input
                type="checkbox"
                className="rounded border-gray-300 text-gray-900 focus:ring-gray-300 dark:border-gray-700 dark:text-gray-100 dark:focus:ring-gray-700"
                checked={form.show_hw_stats}
                onChange={(e) => setForm({ ...form, show_hw_stats: e.target.checked })}
              />
              Show hardware stats bar on dashboard
            </label>
          </div>
          {err && <div className="text-sm text-red-600">{err}</div>}
        </div>
      </div>
      <div className="rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 shadow-sm p-5 mb-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-4">
          <div>
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Connection profiles</div>
            <div className="text-xs text-gray-500 dark:text-gray-400">Manage RouterOS endpoints used by the wizard and dashboard.</div>
          </div>
          <button onClick={() => openRouterModal()} className="inline-flex shrink-0 items-center gap-2 self-start whitespace-nowrap rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white sm:self-auto">
            Add profile
          </button>
        </div>
        {routerMsg && <div className="text-sm text-green-700 mb-3">{routerMsg}</div>}
        {routerErr && <div className="text-sm text-red-600 mb-3">{routerErr}</div>}
        <div className="grid gap-4">
          {!routersLoaded ? (
            <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-800 p-4 text-sm text-gray-500 dark:text-gray-400">Loading connection profiles...</div>
          ) : routers.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-800 p-4 text-sm text-gray-500 dark:text-gray-400">No routers yet. Add your first connection profile.</div>
          ) : (
            routers.map(r => (
              <div
                key={r.id}
                className={`rounded-2xl ring-1 ring-gray-200 dark:ring-gray-800 bg-white dark:bg-gray-950 p-4 flex flex-col gap-3 ${r.enabled === false ? "opacity-70" : ""}`}
              >
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
	                  <div className="min-w-0">
	                    <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{r.name}</div>
	                    <div className="text-xs text-gray-500 dark:text-gray-400">{r.proto.toUpperCase()} · {r.host}:{r.port} · {r.username}</div>
	                    <div className="mt-1 flex flex-wrap items-center gap-2 text-[11px]">
	                      {r.ros_version ? (
	                        <span className={`whitespace-nowrap rounded-full px-2.5 py-1 ${r.ros_supported ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"}`}>
	                          RouterOS {r.ros_version}
	                        </span>
	                      ) : (
	                        <>
	                          <span className="whitespace-nowrap rounded-full bg-gray-100 text-gray-600 px-2.5 py-1 dark:bg-gray-800 dark:text-gray-300">
	                            Version not checked
	                          </span>
	                          <span className="whitespace-nowrap rounded-full bg-amber-50 text-amber-700 px-2.5 py-1 dark:bg-amber-500/10 dark:text-amber-300">
	                            Polling paused until checked
	                          </span>
	                        </>
	                      )}
	                      {r.ros_version && !r.ros_supported && (
	                        <span className="whitespace-nowrap rounded-full bg-rose-50 text-rose-700 px-2.5 py-1 dark:bg-rose-500/10 dark:text-rose-300">
	                          Unsupported: requires RouterOS 7.15+
	                        </span>
	                      )}
	                    </div>
                  </div>
                  <div className="flex shrink-0 flex-wrap items-center gap-2 self-start sm:self-auto">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={r.enabled !== false}
                      onClick={async () => {
                        const next = !(r.enabled !== false);
                        try {
                          setPauseBusyId(r.id);
                          await setRouterEnabled(r.id, next);
                          setRouters(prev => prev.map(x => x.id === r.id ? { ...x, enabled: next } : x));
                          setRouterMsg(next ? `Set active: ${r.name}` : `Set paused: ${r.name}`);
                          setRouterErr("");
                        } catch (e: any) {
                          setRouterErr(e?.message || "Failed to update router");
                        } finally {
                          setPauseBusyId(null);
                        }
                      }}
                      title={r.enabled === false ? `Set active: ${r.name}` : `Set paused: ${r.name}`}
                      aria-label={r.enabled === false ? `Set active: ${r.name}` : `Set paused: ${r.name}`}
                      className="inline-flex items-center gap-2 text-xs transition-colors focus:outline-none focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
                    >
                      <span className={`font-medium ${r.enabled === false ? "text-amber-700 dark:text-amber-300" : "text-emerald-700 dark:text-emerald-300"}`}>
                        {pauseBusyId === r.id ? "..." : r.enabled === false ? "Paused" : "Active"}
                      </span>
                      <span
                        className={`relative h-6 w-11 rounded-full transition-colors ${
                          r.enabled === false ? "bg-gray-300 dark:bg-gray-700" : "bg-emerald-500 dark:bg-emerald-400"
                        }`}
                      >
                        <span
                          className={`absolute top-[2px] block h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
                            r.enabled === false ? "translate-x-[2px]" : "translate-x-[22px]"
                          }`}
                        />
                      </span>
                    </button>
	                    <button onClick={() => openRouterModal(r)} className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">Edit</button>
                    <button
                      onClick={async () => {
                        setRouterErr("");
                        setRouterMsg("");
                        setConfirmDeleteRouter(r);
                      }}
                      className="rounded-full bg-rose-50 text-rose-700 px-3 py-1 text-xs shadow hover:bg-rose-100 dark:bg-rose-500/10 dark:text-rose-300 dark:hover:bg-rose-500/20"
                    >
                      Delete
                    </button>
                  </div>
                </div>
                {typeof testStatus[r.id] !== "undefined" && (
                  <div className={`text-xs ${testStatus[r.id] === "OK" ? "text-green-700" : "text-rose-600"}`}>
                    Status: {testStatus[r.id]}
                  </div>
                )}
	              </div>
            ))
          )}
        </div>
      </div>
      <div className="rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 shadow-sm p-5 mb-6">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-3">
          <div>
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Data maintenance</div>
            <div className="text-xs text-gray-500 dark:text-gray-400">Run controlled cleanup or use the destructive purge actions below.</div>
          </div>
          <button
            type="button"
            disabled={maintBusy !== null || !!usageMaintenance?.running}
            onClick={async () => {
              setMaintErr("");
              setMaintMsg("");
              setShowMaintenanceModal(true);
              try {
                setMaintBusy("run_usage_maintenance");
                const status = await runUsageMaintenance();
                setUsageMaintenance(status);
                setMaintMsg("Usage maintenance started. The scheduler is paused until it finishes.");
              } catch (e: any) {
                setMaintErr(e?.message || "Failed to start usage maintenance");
                await loadUsageMaintenance();
              } finally {
                setMaintBusy(null);
              }
            }}
            className="inline-flex shrink-0 items-center gap-2 self-start whitespace-nowrap rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white sm:self-auto"
          >
            {usageMaintenance?.running ? "Maintenance running" : maintBusy === "run_usage_maintenance" ? "Starting..." : "Run maintenance"}
          </button>
        </div>
        {maintMsg && <div className="text-sm text-green-700 mb-3">{maintMsg}</div>}
        {maintErr && <div className="text-sm text-red-600 mb-3">{maintErr}</div>}
        <div className="grid gap-3 pt-1 pb-4 mb-4 border-b border-gray-100 dark:border-gray-800">
          <div>
            <div className="text-xs font-semibold text-gray-900 dark:text-gray-100">Usage retention and rollups</div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
              Recommended defaults for a 5-second polling setup. Raw samples should stay short-lived, minute buckets should cover charting, and daily totals can remain long-term. These values now drive the usage maintenance job.
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700 dark:text-gray-200">
            <span className="text-gray-500 dark:text-gray-400">Keep raw samples for</span>
            <input
              type="number"
              min={1}
              max={8760}
              className="w-24 rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
              disabled={retentionLocked}
              {...rawRetentionInput}
            />
            <span className="text-gray-500 dark:text-gray-400">hours</span>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700 dark:text-gray-200">
            <span className="text-gray-500 dark:text-gray-400">Keep minute rollups for</span>
            <input
              type="number"
              min={1}
              max={3650}
              className="w-24 rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
              disabled={retentionLocked}
              {...minuteRetentionInput}
            />
            <span className="text-gray-500 dark:text-gray-400">days</span>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700 dark:text-gray-200">
            <span className="text-gray-500 dark:text-gray-400">Keep daily rollups for</span>
            <input
              type="number"
              min={0}
              max={36500}
              className="w-24 rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
              disabled={retentionLocked}
              {...dailyRetentionInput}
            />
            <span className="text-gray-500 dark:text-gray-400">days</span>
            <span className="text-[11px] text-gray-400 dark:text-gray-500">0 means keep forever</span>
          </div>
          {retentionLocked && (
            <div className="text-[11px] text-amber-700 dark:text-amber-300">
              Retention values are locked while usage maintenance is running.
            </div>
          )}
        </div>
        <div className="grid gap-3 pt-1 pb-4 mb-4 border-b border-gray-100 dark:border-gray-800">
          <div>
            <div className="text-xs font-semibold text-gray-900 dark:text-gray-100">Automatic maintenance</div>
            <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
              Run the maintenance job on a schedule. A pre-compaction backup is kept for each run; old backups are rotated automatically.
            </div>
          </div>
          <label className="inline-flex items-center gap-2 text-xs text-gray-700 dark:text-gray-200">
            <input
              type="checkbox"
              className="rounded border-gray-300 text-gray-900 focus:ring-gray-300 dark:border-gray-700 dark:text-gray-100 dark:focus:ring-gray-700"
              checked={form.usage_maintenance_auto_enabled}
              onChange={(e) => setForm((f) => ({ ...f, usage_maintenance_auto_enabled: e.target.checked }))}
            />
            Enable scheduled maintenance
          </label>
          {form.usage_maintenance_auto_enabled && (
            <>
              <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700 dark:text-gray-200">
                <span className="text-gray-500 dark:text-gray-400">Run</span>
                <select
                  className="rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                  value={form.usage_maintenance_auto_frequency}
                  onChange={(e) => setForm((f) => ({ ...f, usage_maintenance_auto_frequency: e.target.value as "daily" | "every_n_days" | "weekly" }))}
                >
                  <option value="daily">every day</option>
                  <option value="every_n_days">every N days</option>
                  <option value="weekly">weekly</option>
                </select>
                {form.usage_maintenance_auto_frequency === "every_n_days" && (
                  <>
                    <span className="text-gray-500 dark:text-gray-400">N =</span>
                    <input
                      type="number"
                      min={2}
                      max={30}
                      className="w-20 rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                      {...autoIntervalInput}
                    />
                    <span className="text-gray-500 dark:text-gray-400">days</span>
                  </>
                )}
                {form.usage_maintenance_auto_frequency === "weekly" && (
                  <>
                    <span className="text-gray-500 dark:text-gray-400">on</span>
                    <select
                      className="rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                      value={form.usage_maintenance_auto_weekday}
                      onChange={(e) => setForm((f) => ({ ...f, usage_maintenance_auto_weekday: Number(e.target.value) }))}
                    >
                      {["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].map((day, idx) => (
                        <option key={day} value={idx}>{day}</option>
                      ))}
                    </select>
                  </>
                )}
                <span className="text-gray-500 dark:text-gray-400">at</span>
                <input
                  type="time"
                  className="rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                  value={form.usage_maintenance_auto_time}
                  onChange={(e) => setForm((f) => ({ ...f, usage_maintenance_auto_time: e.target.value || "04:30" }))}
                />
                <span className="text-gray-500 dark:text-gray-400">({form.timezone || "UTC"})</span>
              </div>
              <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700 dark:text-gray-200">
                <span className="text-gray-500 dark:text-gray-400">Keep last</span>
                <input
                  type="number"
                  min={1}
                  max={50}
                  className="w-20 rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                  {...backupKeepInput}
                />
                <span className="text-gray-500 dark:text-gray-400">maintenance backups</span>
              </div>
            </>
          )}
          {(usageMaintenance?.next_scheduled_run || usageMaintenance?.last_auto_run) && (
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-gray-500 dark:text-gray-400">
              {form.usage_maintenance_auto_enabled && usageMaintenance?.next_scheduled_run && (
                <span>Next scheduled check: {formatCalendarDateTime(usageMaintenance.next_scheduled_run, { timeZone: form.timezone, dateCalendar: form.date_calendar })}</span>
              )}
              {usageMaintenance?.last_auto_run && (
                <span>Last automatic run: {formatCalendarDateTime(usageMaintenance.last_auto_run, { timeZone: form.timezone, dateCalendar: form.date_calendar })}</span>
              )}
            </div>
          )}
        </div>
        <div className="rounded-2xl ring-1 ring-gray-200 dark:ring-gray-800 bg-gray-50 dark:bg-gray-950 p-4 mb-4 grid gap-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="font-medium text-gray-900 dark:text-gray-100">Usage maintenance status:</span>
            <span className={`rounded-full px-2.5 py-1 ${usageMaintenance?.running ? "bg-amber-100 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300" : "bg-gray-200 text-gray-800 dark:bg-gray-800 dark:text-gray-200"}`}>
              {usageMaintenance?.running
                ? `Running · ${usageMaintenance.phase}${usageMaintenance.trigger === "scheduled" ? " · scheduled" : ""}`
                : `Idle · ${usageMaintenance?.last_completed_phase || "never run"}`}
            </span>
            {usageMaintenance && (
              <button
                type="button"
                onClick={() => setShowMaintenanceModal(true)}
                className="rounded-full bg-white text-gray-700 px-2.5 py-1 shadow ring-1 ring-gray-200 hover:bg-gray-100 dark:bg-gray-900 dark:text-gray-200 dark:ring-gray-800 dark:hover:bg-gray-800"
              >
                View progress
              </button>
            )}
          </div>
          {usageMaintenance?.running && (
            <div className="h-2 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-800">
              <div
                className="h-full rounded-full bg-gray-900 transition-all dark:bg-gray-100"
                style={{ width: `${maintenanceProgress}%` }}
              />
            </div>
          )}
          <div className="text-xs text-gray-600 dark:text-gray-300">
            {usageMaintenance?.detail || "This backfills minute rollups, prunes old data by policy, and compacts the SQLite database file."}
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-2 text-[11px] text-gray-500 dark:text-gray-400">
            <span>Backfilled minute rows: {usageMaintenance?.backfilled_minutes ?? 0}</span>
            <span>Deleted raw samples: {usageMaintenance?.deleted_samples ?? 0}</span>
            <span>Deleted minute rollups: {usageMaintenance?.deleted_minutes ?? 0}</span>
            <span>Deleted daily rollups: {usageMaintenance?.deleted_daily ?? 0}</span>
            <span>DB size before: {formatBytes(usageMaintenance?.file_size_before ?? null)}</span>
            <span>DB size after: {formatBytes(usageMaintenance?.file_size_after ?? null)}</span>
          </div>
          {(usageMaintenance?.started_at || usageMaintenance?.finished_at || usageMaintenance?.last_error || usageMaintenance?.backup_path) && (
            <div className="grid gap-1 text-[11px] text-gray-500 dark:text-gray-400">
              {usageMaintenance?.started_at && <div>Started: {formatCalendarDateTime(usageMaintenance.started_at, { timeZone: form.timezone, dateCalendar: form.date_calendar })}</div>}
              {usageMaintenance?.finished_at && <div>Finished: {formatCalendarDateTime(usageMaintenance.finished_at, { timeZone: form.timezone, dateCalendar: form.date_calendar })}</div>}
              {usageMaintenance?.backup_path && <div>Backup: {usageMaintenance.backup_path}</div>}
              {usageMaintenance?.last_error && <div className="text-rose-600 dark:text-rose-300">Last error: {usageMaintenance.last_error}</div>}
            </div>
          )}
        </div>
        <div className="grid gap-3 text-sm">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
            <div className="text-xs text-gray-600 dark:text-gray-300">Purge all usage data (raw samples, minute, daily and monthly rollups). Peers and routers stay.</div>
            <button
              type="button"
              disabled={maintBusy !== null || !!usageMaintenance?.running}
              onClick={() => { setMaintErr(""); setMaintMsg(""); setConfirmAction("usage"); }}
              className="shrink-0 self-start whitespace-nowrap rounded-full bg-rose-50 text-rose-700 px-4 py-1.5 text-xs shadow hover:bg-rose-100 disabled:opacity-50 sm:self-auto"
            >
              Purge usage
            </button>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between sm:gap-4">
            <div className="text-xs text-gray-600 dark:text-gray-300">Delete all peers (and their quotas/usages). Routers remain configured.</div>
            <button
              type="button"
              disabled={maintBusy !== null || !!usageMaintenance?.running}
              onClick={() => { setMaintErr(""); setMaintMsg(""); setConfirmAction("peers"); }}
              className="shrink-0 self-start whitespace-nowrap rounded-full bg-rose-600 text-white px-4 py-1.5 text-xs shadow hover:bg-rose-700 disabled:opacity-50 sm:self-auto"
            >
              Purge peers
            </button>
          </div>
        </div>
      </div>
      {/* User Management Section */}
      <div className="rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 shadow-sm p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">User Management</div>
            <div className="text-xs text-gray-500 dark:text-gray-400">Manage admin accounts without drifting into full RBAC.</div>
          </div>
        </div>
        {userMsg && <div className="text-sm text-green-700 dark:text-green-300 mb-3">{userMsg}</div>}
        {userErr && <div className="text-sm text-red-600 dark:text-red-300 mb-3">{userErr}</div>}
        <form onSubmit={handleCreateUser} className="flex flex-wrap gap-3 mb-4">
          <input
            type="text"
            placeholder="Username"
            className="flex-1 min-w-[120px] rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
            value={newUsername}
            onChange={(e) => setNewUsername(e.target.value)}
            required
          />
          <input
            type="password"
            placeholder="Password"
            className="flex-1 min-w-[120px] rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
            required
          />
          <button
            type="submit"
            disabled={userBusy}
            className="rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
          >
            {userBusy ? "Adding..." : "Add User"}
          </button>
        </form>
        <div className="text-xs text-gray-500 dark:text-gray-400 mb-4">New local users are full admins. Passwords must be at least 12 characters.</div>
        <div className="grid gap-2">
          {users.map((u) => (
            <div key={u.id} className="rounded-2xl ring-1 ring-gray-200 dark:ring-gray-800 bg-white dark:bg-gray-950 p-4 grid gap-3">
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{u.username}</div>
                    {u.id === user?.id && (
                      <span className="rounded-full bg-gray-100 text-gray-700 px-2.5 py-1 text-[11px] dark:bg-gray-800 dark:text-gray-300">You</span>
                    )}
                    <span className={`rounded-full px-2.5 py-1 text-[11px] ${u.is_active ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"}`}>
                      {u.is_active ? "Active" : "Inactive"}
                    </span>
                    {isAccountLocked(u.locked_until) && (
                      <span className="rounded-full bg-amber-50 text-amber-700 px-2.5 py-1 text-[11px] dark:bg-amber-500/10 dark:text-amber-300">Locked</span>
                    )}
                    {u.must_change_password && (
                      <span className="rounded-full bg-indigo-50 text-indigo-700 px-2.5 py-1 text-[11px] dark:bg-indigo-500/10 dark:text-indigo-300">Must change password</span>
                    )}
                  </div>
                  <div className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                    Created: {formatCalendarDateTime(u.created_at, { timeZone: form.timezone, dateCalendar: form.date_calendar, includeTime: false })}
                  </div>
                  <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                    Last login: {u.last_login_at ? formatCalendarDateTime(u.last_login_at, { timeZone: form.timezone, dateCalendar: form.date_calendar }) : "Never"}
                  </div>
                  {isAccountLocked(u.locked_until) && (
                    <div className="mt-1 text-xs text-amber-700 dark:text-amber-300">
                      Locked until {formatCalendarDateTime(u.locked_until || "", { timeZone: form.timezone, dateCalendar: form.date_calendar })}
                    </div>
                  )}
                </div>
                <div className={`flex gap-2 ${u.id === user?.id ? "flex-col items-end" : "flex-wrap"}`}>
                  {u.id !== user?.id && (
                    <button
                      type="button"
                      disabled={userBusy}
                      onClick={() => setResetPasswordUser(u)}
                      className="rounded-full bg-gray-100 text-gray-800 px-3 py-1.5 text-xs shadow hover:bg-gray-200 disabled:opacity-50 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                    >
                      Reset password
                    </button>
                  )}
                  {isAccountLocked(u.locked_until) && (
                    <button
                      type="button"
                      disabled={userBusy}
                      onClick={() => handleUpdateUser(u.id, { unlock: true }, `Unlocked ${u.username}.`)}
                      className="rounded-full bg-amber-50 text-amber-700 px-3 py-1.5 text-xs shadow hover:bg-amber-100 disabled:opacity-50 dark:bg-amber-500/10 dark:text-amber-300 dark:hover:bg-amber-500/20"
                    >
                      Unlock
                    </button>
                  )}
                  {u.is_active ? (
                    <>
                      <button
                        type="button"
                        disabled={userBusy || u.id === user?.id}
                        onClick={() => handleUpdateUser(u.id, { is_active: false }, `Deactivated ${u.username}.`)}
                        className="rounded-full bg-rose-50 text-rose-700 px-3 py-1.5 text-xs shadow hover:bg-rose-100 disabled:opacity-50 dark:bg-rose-500/10 dark:text-rose-300 dark:hover:bg-rose-500/20"
                      >
                        Deactivate
                      </button>
                      {u.id === user?.id && (
                        <button
                          type="button"
                          onClick={() => setShowMyPasswordModal(true)}
                          className="rounded-full bg-gray-100 text-gray-800 px-3 py-1.5 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                        >
                          Change my password
                        </button>
                      )}
                    </>
                  ) : (
                    <>
                      <button
                        type="button"
                        disabled={userBusy}
                        onClick={() => handleUpdateUser(u.id, { is_active: true }, `Reactivated ${u.username}.`)}
                        className="rounded-full bg-emerald-50 text-emerald-700 px-3 py-1.5 text-xs shadow hover:bg-emerald-100 disabled:opacity-50 dark:bg-emerald-500/10 dark:text-emerald-300 dark:hover:bg-emerald-500/20"
                      >
                        Reactivate
                      </button>
                      <button
                        type="button"
                        disabled={userBusy}
                        onClick={() => handleDeleteUser(u)}
                        className="rounded-full bg-rose-600 text-white px-3 py-1.5 text-xs shadow hover:bg-rose-700 disabled:opacity-50"
                      >
                        Delete
                      </button>
                    </>
                  )}
                </div>
              </div>
            </div>
          ))}
          {users.length === 0 && (
            <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-800 p-4 text-sm text-gray-500 dark:text-gray-400">No users found.</div>
          )}
        </div>
      </div>
        </>
      )}
      {resetPasswordUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-2xl bg-white dark:bg-gray-900 ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 grid gap-4">
            <div>
              <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">Reset password</div>
              <div className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                Set a temporary password for <span className="font-medium text-gray-900 dark:text-gray-100">{resetPasswordUser.username}</span>. They will be forced to change it on next login.
              </div>
            </div>
            <input
              type="password"
              placeholder="Temporary password"
              className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
              value={resetPasswordValue}
              onChange={(e) => setResetPasswordValue(e.target.value)}
            />
            <div className="text-xs text-gray-500 dark:text-gray-400">Minimum 12 characters.</div>
            <div className="flex items-center justify-end gap-3">
              <button
                type="button"
                disabled={resetPasswordBusy}
                onClick={() => {
                  setResetPasswordUser(null);
                  setResetPasswordValue("");
                }}
                className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 disabled:opacity-50 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={resetPasswordBusy || !resetPasswordValue}
                onClick={handleResetPassword}
                className="rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
              >
                {resetPasswordBusy ? "Resetting..." : "Reset password"}
              </button>
            </div>
          </div>
        </div>
      )}
      {showMyPasswordModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white dark:bg-gray-900 ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 grid gap-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">Change password</div>
                <div className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                  Update your password here instead of keeping a permanent form in Settings.
                </div>
              </div>
              {!blockingPasswordChange && (
                <button
                  type="button"
                  onClick={() => {
                    setShowMyPasswordModal(false);
                    setMyPasswordErr("");
                    setMyPasswordMsg("");
                  }}
                  className="rounded-full bg-gray-100 text-gray-800 h-8 w-8 flex items-center justify-center hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                  aria-label="Close password dialog"
                >
                  x
                </button>
              )}
            </div>
            <div className="flex flex-wrap gap-2">
              <span className={`rounded-full px-3 py-1 text-xs ${user?.is_active ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300" : "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"}`}>
                {user?.is_active ? "Active" : "Inactive"}
              </span>
              {isAccountLocked(user?.locked_until) && (
                <span className="rounded-full bg-amber-50 text-amber-700 px-3 py-1 text-xs dark:bg-amber-500/10 dark:text-amber-300">
                  Locked until {formatCalendarDateTime(user?.locked_until || "", { timeZone: form.timezone, dateCalendar: form.date_calendar })}
                </span>
              )}
              {user?.must_change_password && (
                <span className="rounded-full bg-rose-50 text-rose-700 px-3 py-1 text-xs dark:bg-rose-500/10 dark:text-rose-300">
                  Must change password
                </span>
              )}
            </div>
            {myPasswordMsg && <div className="text-sm text-green-700 dark:text-green-300">{myPasswordMsg}</div>}
            {myPasswordErr && <div className="text-sm text-red-600 dark:text-red-300">{myPasswordErr}</div>}
            <form onSubmit={handleMyPasswordChange} className="grid gap-3">
              <input
                type="password"
                placeholder="Current password"
                className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                value={myPasswordCurrent}
                onChange={(e) => setMyPasswordCurrent(e.target.value)}
                required
              />
              <input
                type="password"
                placeholder="New password"
                className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                value={myPasswordNext}
                onChange={(e) => setMyPasswordNext(e.target.value)}
                required
              />
              <input
                type="password"
                placeholder="Confirm new password"
                className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                value={myPasswordConfirm}
                onChange={(e) => setMyPasswordConfirm(e.target.value)}
                required
              />
              <div className="flex items-center justify-between gap-3">
                <div className="text-xs text-gray-500 dark:text-gray-400">Minimum 12 characters.</div>
                <div className="flex items-center gap-3">
                  {!blockingPasswordChange && (
                    <button
                      type="button"
                      onClick={() => {
                        setShowMyPasswordModal(false);
                        setMyPasswordErr("");
                        setMyPasswordMsg("");
                      }}
                      className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                    >
                      Cancel
                    </button>
                  )}
                  <button
                    type="submit"
                    disabled={myPasswordBusy}
                    className="rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
                  >
                    {myPasswordBusy ? "Updating..." : "Change password"}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}
      {showMaintenanceModal && usageMaintenance && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-xl rounded-2xl bg-white dark:bg-gray-900 ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 grid gap-5">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">Usage maintenance</div>
                <div className="text-sm text-gray-600 dark:text-gray-300">
                  {maintenanceStatusLabel}
                  {usageMaintenance.running ? ` · ${maintenanceProgress.toFixed(1)}%` : maintenanceIsTerminal ? ` · ${usageMaintenance.phase}` : ""}
                </div>
              </div>
              <button
                type="button"
                onClick={() => setShowMaintenanceModal(false)}
                disabled={usageMaintenance.running}
                className="rounded-full bg-gray-100 text-gray-800 h-8 w-8 flex items-center justify-center hover:bg-gray-200 disabled:opacity-40 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                aria-label="Close maintenance dialog"
              >
                x
              </button>
            </div>

            <div className="grid gap-3">
              <div className="grid gap-2">
                <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
                  <span>Overall progress</span>
                  <span>{maintenanceProgress.toFixed(1)}%</span>
                </div>
                <div className="h-3 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-800">
                  <div
                    className={`h-full rounded-full transition-all ${usageMaintenance.phase === "failed" ? "bg-rose-600" : usageMaintenance.phase === "cancelled" ? "bg-amber-500" : "bg-gray-900 dark:bg-gray-100"}`}
                    style={{ width: `${maintenanceProgress}%` }}
                  />
                </div>
              </div>
              <div className="grid gap-2">
                <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
                  <span>Current phase</span>
                  <span>{maintenancePhaseProgress.toFixed(1)}%</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-800">
                  <div className="h-full rounded-full bg-indigo-600 transition-all dark:bg-indigo-300" style={{ width: `${maintenancePhaseProgress}%` }} />
                </div>
              </div>
            </div>

            <div className="rounded-2xl bg-gray-50 dark:bg-gray-950 ring-1 ring-gray-200 dark:ring-gray-800 p-4 grid gap-3">
              <div className="text-sm text-gray-700 dark:text-gray-200">
                {usageMaintenance.detail || "Preparing usage maintenance."}
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs">
                <div>
                  <div className="text-gray-500 dark:text-gray-400">Elapsed</div>
                  <div className="font-medium text-gray-900 dark:text-gray-100">{formatDuration(usageMaintenance.elapsed_seconds)}</div>
                </div>
                <div>
                  <div className="text-gray-500 dark:text-gray-400">Remaining</div>
                  <div className="font-medium text-gray-900 dark:text-gray-100">{formatDuration(usageMaintenance.estimated_remaining_seconds)}</div>
                </div>
                <div>
                  <div className="text-gray-500 dark:text-gray-400">Processed units</div>
                  <div className="font-medium text-gray-900 dark:text-gray-100">{formatCount(usageMaintenance.processed_units)} / {formatCount(usageMaintenance.total_units)}</div>
                </div>
                <div>
                  <div className="text-gray-500 dark:text-gray-400">Backfilled minutes</div>
                  <div className="font-medium text-gray-900 dark:text-gray-100">{formatCount(usageMaintenance.backfilled_minutes)}</div>
                </div>
                <div>
                  <div className="text-gray-500 dark:text-gray-400">Deleted raw samples</div>
                  <div className="font-medium text-gray-900 dark:text-gray-100">{formatCount(usageMaintenance.deleted_samples)}</div>
                </div>
                <div>
                  <div className="text-gray-500 dark:text-gray-400">Deleted minute rollups</div>
                  <div className="font-medium text-gray-900 dark:text-gray-100">{formatCount(usageMaintenance.deleted_minutes)}</div>
                </div>
                <div>
                  <div className="text-gray-500 dark:text-gray-400">DB size before</div>
                  <div className="font-medium text-gray-900 dark:text-gray-100">{formatBytes(usageMaintenance.file_size_before ?? null)}</div>
                </div>
                <div>
                  <div className="text-gray-500 dark:text-gray-400">DB size after</div>
                  <div className="font-medium text-gray-900 dark:text-gray-100">{formatBytes(usageMaintenance.file_size_after ?? null)}</div>
                </div>
              </div>
              {usageMaintenance.backup_path && (
                <div className="break-all text-[11px] text-gray-500 dark:text-gray-400">Backup: {usageMaintenance.backup_path}</div>
              )}
              {usageMaintenance.last_error && (
                <div className="text-sm text-rose-600 dark:text-rose-300">{usageMaintenance.last_error}</div>
              )}
            </div>

            <div className="flex items-center justify-end gap-3">
              {!usageMaintenance.running && (
                <button
                  type="button"
                  onClick={() => setShowMaintenanceModal(false)}
                  className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                >
                  Close
                </button>
              )}
              {usageMaintenance.running && (
                <button
                  type="button"
                  disabled={!usageMaintenance.can_cancel || maintenanceCancelBusy}
                  onClick={async () => {
                    try {
                      setMaintenanceCancelBusy(true);
                      const status = await cancelUsageMaintenance();
                      setUsageMaintenance(status);
                    } catch (e: any) {
                      setMaintErr(e?.message || "Failed to cancel usage maintenance");
                      await loadUsageMaintenance();
                    } finally {
                      setMaintenanceCancelBusy(false);
                    }
                  }}
                  className="rounded-full bg-rose-50 text-rose-700 px-4 py-2 text-sm shadow hover:bg-rose-100 disabled:opacity-50 dark:bg-rose-500/10 dark:text-rose-300 dark:hover:bg-rose-500/20"
                >
                  {maintenanceCancelBusy || usageMaintenance.cancel_requested ? "Cancelling..." : "Cancel maintenance"}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
      {showRouterModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-lg rounded-3xl bg-white dark:bg-gray-900 ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 grid gap-4">
            <div className="flex items-center justify-between">
              <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">{editingRouter ? "Edit profile" : "Add profile"}</div>
              <button onClick={() => setShowRouterModal(false)} className="rounded-full bg-gray-100 text-gray-800 h-8 w-8 flex items-center justify-center hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">✕</button>
            </div>
            <div className="grid gap-3">
              <div className="grid gap-1">
                <label className="text-xs text-gray-500 dark:text-gray-400">Name</label>
                <input value={routerForm.name} onChange={e => setRouterForm(f => ({ ...f, name: e.target.value }))} className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700" placeholder="CHR Amsterdam" />
              </div>
              <div className="grid gap-1">
                <label className="text-xs text-gray-500 dark:text-gray-400">Host / IP</label>
                <input value={routerForm.host} onChange={e => setRouterForm(f => ({ ...f, host: e.target.value }))} className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700" placeholder="10.0.0.1" />
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Method</label>
                  <select
                    value={routerForm.proto}
                    onChange={e => {
                      const nextProto = e.target.value as RouterProto;
                      setRouterForm(f => ({ ...f, proto: nextProto, port: f.port || defaultProtoPort[nextProto] }));
                    }}
                    className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950"
                  >
                    <option value="rest">REST HTTPS</option>
                    <option value="rest-http">REST HTTP</option>
                    <option value="api">API TLS</option>
                    <option value="api-plain">API Plain</option>
                  </select>
                </div>
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Port</label>
                  <input
                    type="number"
                    value={routerForm.port}
                    onChange={e => setRouterForm(f => ({ ...f, port: Number(e.target.value) }))}
                    className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
                  />
                </div>
              </div>
              <div className="grid gap-1">
                <label className="text-xs text-gray-500 dark:text-gray-400">Username</label>
                <input value={routerForm.username} onChange={e => setRouterForm(f => ({ ...f, username: e.target.value }))} className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700" placeholder="admin" />
              </div>
              <div className="grid gap-1">
                <label className="text-xs text-gray-500 dark:text-gray-400">{editingRouter ? "Password (leave blank to keep)" : "Password"}</label>
                <input type="password" value={routerForm.password} onChange={e => setRouterForm(f => ({ ...f, password: e.target.value }))} className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700" placeholder="••••••••" />
              </div>
              <label className="inline-flex items-center gap-2 text-sm text-gray-700 dark:text-gray-200">
                <input type="checkbox" checked={routerForm.tls_verify} onChange={e => setRouterForm(f => ({ ...f, tls_verify: e.target.checked }))} className="rounded border-gray-300 text-gray-900 focus:ring-gray-300 dark:border-gray-700 dark:text-gray-100 dark:focus:ring-gray-700" />
                Verify TLS certificates
              </label>
            </div>
            {routerErr && <div className="text-sm text-red-600">{routerErr}</div>}
            {editingRouter && typeof testStatus[editingRouter.id] !== "undefined" && (
              <div className={`text-sm ${testStatus[editingRouter.id] === "OK" ? "text-green-700" : "text-rose-600"}`}>
                Status: {testStatus[editingRouter.id]}
              </div>
            )}
            <div className="flex items-center justify-end gap-3 pt-2">
              <button onClick={() => setShowRouterModal(false)} className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">Cancel</button>
              {editingRouter && (
                <button
                  type="button"
                  disabled={routerBusy || testBusyId === editingRouter.id}
                  onClick={() => void runRouterTest(editingRouter)}
                  className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 disabled:opacity-50 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                >
                  {testBusyId === editingRouter.id ? "Testing..." : "Test connection"}
                </button>
              )}
              <button disabled={routerBusy} onClick={handleSaveRouter} className="rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white">{editingRouter ? "Save changes" : "Add profile"}</button>
            </div>
          </div>
        </div>
      )}
      {confirmDeleteRouter && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white dark:bg-gray-900 ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 grid gap-4">
            <div className="grid gap-1">
              <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">Delete profile and local data</div>
              <div className="text-sm text-gray-600 dark:text-gray-300">
                This permanently removes <span className="font-medium text-gray-900 dark:text-gray-100">{confirmDeleteRouter.name}</span> from this app and deletes the local DB rows linked to it. It does not delete peers on the actual router device.
              </div>
              <div className="text-xs text-gray-500 dark:text-gray-400">
                Large routers can take a while. While this runs, dashboard polling is paused and other API screens may briefly show a retry message.
              </div>
            </div>
            {routerDeleteImpactBusy && (
              <div className="rounded-2xl bg-gray-50 dark:bg-gray-950 ring-1 ring-gray-200 dark:ring-gray-800 px-4 py-3 text-sm text-gray-600 dark:text-gray-300">
                Calculating delete impact...
              </div>
            )}
            {routerDeleteImpactErr && (
              <div className="rounded-2xl bg-rose-50 dark:bg-rose-500/10 ring-1 ring-rose-200 dark:ring-rose-500/20 px-4 py-3 grid gap-2">
                <div className="text-sm text-rose-700 dark:text-rose-300">{routerDeleteImpactErr}</div>
                <div className="text-xs text-rose-700/80 dark:text-rose-300/80">
                  The impact preview must load before deletion is allowed.
                </div>
              </div>
            )}
            {routerDeleteImpact && (
              <>
                <div className="rounded-2xl bg-rose-50 dark:bg-rose-500/10 ring-1 ring-rose-200 dark:ring-rose-500/20 px-4 py-3 grid gap-2 text-sm text-rose-800 dark:text-rose-200">
                  <div>This action is irreversible.</div>
                  {routerDeleteImpact.dashboard_selected && <div>The router will be removed from dashboard selected-router defaults.</div>}
                  {routerDeleteImpact.signup_token_count > 0 && <div>Telegram signup tokens referencing these peers will be rewritten or removed.</div>}
                </div>
                <div className="rounded-2xl bg-gray-50 dark:bg-gray-950 ring-1 ring-gray-200 dark:ring-gray-800 p-4 grid gap-3">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Records that will be removed locally</div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-3">
                    {routerDeleteSummaryItems.map((item) => (
                      <div key={item.label} className="min-w-0">
                        <div className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">{item.label}</div>
                        <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{item.value}</div>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">
                    Type <span className="font-medium text-gray-900 dark:text-gray-100">{confirmDeleteRouter.name}</span> to confirm deletion
                  </label>
                  <input
                    value={routerDeleteConfirmName}
                    onChange={(e) => setRouterDeleteConfirmName(e.target.value)}
                    className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-rose-200 dark:focus:ring-rose-500/30 dark:bg-gray-950 dark:text-gray-100"
                    placeholder={confirmDeleteRouter.name}
                  />
                </div>
              </>
            )}
            <div className="flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => {
                  setConfirmDeleteRouter(null);
                  setRouterDeleteImpact(null);
                  setRouterDeleteImpactErr("");
                  setRouterDeleteConfirmName("");
                }}
                className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={!routerDeleteImpact || !routerDeleteNameMatches || routerDeleteImpactBusy || !!routerDeleteImpactErr || routerDeleteBusy}
                onClick={async () => {
                  try {
                    setRouterDeleteBusy(true);
                    const result = await deleteRouter(confirmDeleteRouter.id);
                    await Promise.all([loadRouters(), loadSettings()]);
                    const backupNote = result.backup_path ? ` Backup: ${result.backup_path}` : "";
                    setRouterMsg(
                      `Deleted ${result.router_name} with ${formatCount(result.peer_count)} peer(s) and ${formatCount(result.usage_sample_rows + result.usage_minute_rows + result.usage_daily_rows + result.usage_monthly_rows)} usage row(s).${backupNote}`
                    );
                    setRouterDeleteImpact(null);
                    setRouterDeleteImpactErr("");
                    setRouterDeleteConfirmName("");
                    setConfirmDeleteRouter(null);
                  } catch (e: any) {
                    setRouterErr(e?.message || "Failed to delete router");
                  } finally {
                    setRouterDeleteBusy(false);
                  }
                }}
                className="rounded-full bg-rose-600 text-white px-4 py-2 text-sm shadow hover:bg-rose-700 disabled:opacity-50"
              >
                {routerDeleteBusy ? "Deleting..." : "Delete permanently"}
              </button>
            </div>
          </div>
        </div>
      )}
      {confirmAction && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-sm rounded-2xl bg-white dark:bg-gray-900 ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 grid gap-4">
            <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">
              {confirmAction === "usage" ? "Purge usage data" : "Purge all peers"}
            </div>
            <div className="text-sm text-gray-600 dark:text-gray-300">
              {confirmAction === "usage"
                ? "This permanently deletes all usage samples and rollups for every peer. Peers and routers are kept."
                : "This removes every peer (and related usage/quotas) from the database. Routers stay configured."}
            </div>
            <div className="flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => setConfirmAction(null)}
                className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={maintBusy !== null}
                onClick={async () => {
                  setMaintErr("");
                  setMaintMsg("");
                  try {
                    setMaintBusy(confirmAction);
                    if (confirmAction === "usage") {
                      await purgeUsage();
                      setMaintMsg("All usage data purged.");
                    } else if (confirmAction === "peers") {
                      await purgePeers();
                      setMaintMsg("All peers purged.");
                      // Refresh router list so wizard/dashboard see empty peers set
                    }
                  } catch (e: any) {
                    setMaintErr(e?.message || "Operation failed");
                  } finally {
                    setMaintBusy(null);
                    setConfirmAction(null);
                  }
                }}
                className={`rounded-full px-4 py-2 text-sm shadow disabled:opacity-50 ${confirmAction === "usage" ? "bg-rose-600 text-white hover:bg-rose-700" : "bg-rose-700 text-white hover:bg-rose-800"
                  }`}
              >
                {maintBusy === confirmAction ? "Working…" : confirmAction === "usage" ? "Yes, purge usage" : "Yes, purge peers"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
