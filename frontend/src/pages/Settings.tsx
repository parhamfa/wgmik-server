import React from "react";
import { Link } from "react-router-dom";
import { getSettings, putSettings, listRouters, getActiveRouter, setActiveRouter, createRouter, updateRouter, deleteRouter, testRouter, syncRouter, purgeUsage, purgePeers, fetchJson, type Router, type RouterProto } from "../api";

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

export default function SettingsPage() {
  const [form, setForm] = React.useState({
    poll_interval_seconds: 30,
    online_threshold_seconds: 15,
    monthly_reset_day: 1,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC",
    show_kind_pills: true,
    show_hw_stats: true,
    dashboard_refresh_seconds: 30,
    peer_default_scope_unit: "days",
    peer_default_scope_value: 14,
    dashboard_scope_unit: "days",
    dashboard_scope_value: 14,
    peer_refresh_seconds: 30,
  });
  const [err, setErr] = React.useState("");
  const [saveState, setSaveState] = React.useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const saveTimerRef = React.useRef<number | null>(null);
  const savingRef = React.useRef(false);
  const pendingRef = React.useRef(false);
  const lastSavedRef = React.useRef<any | null>(null);
  const [routers, setRouters] = React.useState<Router[]>([]);
  const [activeRouterId, setActiveRouterId] = React.useState<number | null>(null);
  const [routerMsg, setRouterMsg] = React.useState("");
  const [routerErr, setRouterErr] = React.useState("");
  const [routerBusy, setRouterBusy] = React.useState(false);
  const [testBusyId, setTestBusyId] = React.useState<number | null>(null);
  const [testStatus, setTestStatus] = React.useState<Record<number, string>>({});
  const [syncBusyId, setSyncBusyId] = React.useState<number | null>(null);
  const [syncStatus, setSyncStatus] = React.useState<Record<number, string>>({});
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

  // User management state
  interface User { id: number; username: string; is_admin: boolean; created_at: string; }
  const [users, setUsers] = React.useState<User[]>([]);
  const [newUsername, setNewUsername] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const [userErr, setUserErr] = React.useState("");
  const [userMsg, setUserMsg] = React.useState("");
  const [userBusy, setUserBusy] = React.useState(false);

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
      const rows = await listRouters();
      setRouters(rows);
    } catch {
      setRouters([]);
    }
  }, []);

  React.useEffect(() => { loadSettings(); }, [loadSettings]);
  React.useEffect(() => { loadRouters(); }, [loadRouters]);
  React.useEffect(() => {
    (async () => {
      try {
        const ar = await getActiveRouter();
        setActiveRouterId(ar?.router_id ?? null);
      } catch {
        setActiveRouterId(null);
      }
    })();
  }, []);

  // User management
  const loadUsers = React.useCallback(async () => {
    try {
      const data = await fetchJson("/api/users");
      setUsers(data);
    } catch { setUsers([]); }
  }, []);

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setUserErr(""); setUserMsg("");
    try {
      setUserBusy(true);
      await fetchJson("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: newUsername, password: newPassword }),
      });
      setUserMsg(`User ${newUsername} created.`);
      setNewUsername(""); setNewPassword("");
      loadUsers();
    } catch (err: any) {
      setUserErr(err.message || "Failed to create user");
    } finally { setUserBusy(false); }
  };

  const handleDeleteUser = async (id: number) => {
    if (!confirm("Are you sure you want to delete this user?")) return;
    try {
      await fetchJson(`/api/users/${id}`, { method: "DELETE" });
      loadUsers();
    } catch (err: any) {
      alert(err.message || "Failed to delete user");
    }
  };

  React.useEffect(() => { loadUsers(); }, [loadUsers]);

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

  return (
    <div className="max-w-4xl mx-auto px-4 md:px-6 py-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Settings</h1>
        <Link to="/" className="inline-flex items-center gap-2 rounded-full bg-white text-gray-900 px-4 py-1.5 text-sm ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-800">
          ← Dashboard
        </Link>
      </div>
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
            <input type="number" min={5} className="w-40 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700" value={form.poll_interval_seconds} onChange={e => setForm({ ...form, poll_interval_seconds: Math.max(5, Number(e.target.value || 0)) })} />
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Online threshold (seconds)</label>
            <input type="number" min={5} className="w-40 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700" value={form.online_threshold_seconds} onChange={e => setForm({ ...form, online_threshold_seconds: Math.max(5, Number(e.target.value || 0)) })} />
          </div>
          <div>
            <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Monthly reset day (1–28)</label>
            <input type="number" min={1} max={28} className="w-40 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700" value={form.monthly_reset_day} onChange={e => setForm({ ...form, monthly_reset_day: Math.min(28, Math.max(1, Number(e.target.value || 1))) })} />
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
            <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700 dark:text-gray-200">
              <span className="text-gray-500 dark:text-gray-400">Dashboard auto refresh default</span>
              <input
                type="number"
                min={5}
                className="w-20 rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                value={form.dashboard_refresh_seconds}
                onChange={(e) =>
                  setForm({
                    ...form,
                    dashboard_refresh_seconds: Math.max(5, Number(e.target.value || 5)),
                  })
                }
              />
              <span className="text-gray-500 dark:text-gray-400">seconds</span>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700 dark:text-gray-200">
              <span className="text-gray-500 dark:text-gray-400">Dashboard default scope</span>
              <input
                type="number"
                min={1}
                className="w-16 rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                value={form.dashboard_scope_value}
                onChange={(e) =>
                  setForm({
                    ...form,
                    dashboard_scope_value: Math.max(1, Number(e.target.value || 1)),
                  })
                }
              />
              <select
                className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950"
                value={form.dashboard_scope_unit}
                onChange={(e) =>
                  setForm({
                    ...form,
                    dashboard_scope_unit: e.target.value as "minutes" | "hours" | "days",
                  })
                }
              >
                <option value="minutes">minutes</option>
                <option value="hours">hours</option>
                <option value="days">days</option>
              </select>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700 dark:text-gray-200">
              <span className="text-gray-500 dark:text-gray-400">Peer detail default scope</span>
              <input
                type="number"
                min={1}
                className="w-16 rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                value={form.peer_default_scope_value}
                onChange={(e) =>
                  setForm({
                    ...form,
                    peer_default_scope_value: Math.max(1, Number(e.target.value || 1)),
                  })
                }
              />
              <select
                className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950"
                value={form.peer_default_scope_unit}
                onChange={(e) =>
                  setForm({
                    ...form,
                    peer_default_scope_unit: e.target.value as "minutes" | "hours" | "days",
                  })
                }
              >
                <option value="minutes">minutes</option>
                <option value="hours">hours</option>
                <option value="days">days</option>
              </select>
              <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700 dark:text-gray-200">
                <span className="text-gray-500 dark:text-gray-400">Peer detail auto refresh default</span>
                <input
                  type="number"
                  min={5}
                  className="w-20 rounded-full border border-gray-900 bg-gray-900 text-white px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                  value={form.peer_refresh_seconds}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      peer_refresh_seconds: Math.max(5, Number(e.target.value || 5)),
                    })
                  }
                />
                <span className="text-gray-500 dark:text-gray-400">seconds</span>
              </div>
            </div>
          </div>
          {err && <div className="text-sm text-red-600">{err}</div>}
        </div>
      </div>
      <div className="rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 shadow-sm p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Connection profiles</div>
            <div className="text-xs text-gray-500 dark:text-gray-400">Manage RouterOS endpoints used by the wizard and dashboard.</div>
          </div>
          <button onClick={() => openRouterModal()} className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white">
            Add profile
          </button>
        </div>
        {routerMsg && <div className="text-sm text-green-700 mb-3">{routerMsg}</div>}
        {routerErr && <div className="text-sm text-red-600 mb-3">{routerErr}</div>}
        <div className="grid gap-4">
          {routers.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-800 p-4 text-sm text-gray-500 dark:text-gray-400">No routers yet. Add your first connection profile.</div>
          ) : (
            routers.map(r => (
              <div key={r.id} className="rounded-2xl ring-1 ring-gray-200 dark:ring-gray-800 bg-white dark:bg-gray-950 p-4 flex flex-col gap-3">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{r.name}</div>
                    <div className="text-xs text-gray-500 dark:text-gray-400">{r.proto.toUpperCase()} · {r.host}:{r.port} · {r.username}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    {activeRouterId === r.id && (
                      <span className="rounded-full bg-indigo-100 text-indigo-800 px-2.5 py-1 text-[11px] dark:bg-indigo-500/10 dark:text-indigo-300">
                        Active
                      </span>
                    )}
                    <button
                      onClick={async () => {
                        try {
                          setTestBusyId(r.id);
                          await testRouter(r.id);
                          setTestStatus(prev => ({ ...prev, [r.id]: "OK" }));
                        } catch (e: any) {
                          setTestStatus(prev => ({ ...prev, [r.id]: e?.message || "Failed" }));
                        } finally {
                          setTestBusyId(null);
                        }
                      }}
                      className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                    >
                      {testBusyId === r.id ? "Testing..." : "Test"}
                    </button>
                    <button
                      onClick={async () => {
                        try {
                          await setActiveRouter(r.id);
                          setActiveRouterId(r.id);
                          setRouterMsg(`Active router set: ${r.name}`);
                        } catch (e: any) {
                          setRouterErr(e?.message || "Failed to set active router");
                        }
                      }}
                      className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                    >
                      Set active
                    </button>
                    <button
                      onClick={async () => {
                        try {
                          setSyncBusyId(r.id);
                          const res = await syncRouter(r.id);
                          setSyncStatus((prev) => ({
                            ...prev,
                            [r.id]: `OK · updated ${res.updated}, created ${res.created}, missing ${res.missing}`,
                          }));
                        } catch (e: any) {
                          setSyncStatus((prev) => ({ ...prev, [r.id]: e?.message || "Failed" }));
                        } finally {
                          setSyncBusyId(null);
                        }
                      }}
                      className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                    >
                      {syncBusyId === r.id ? "Syncing..." : "Sync"}
                    </button>
                    <button onClick={() => openRouterModal(r)} className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">Edit</button>
                    <button
                      onClick={async () => {
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
                {typeof syncStatus[r.id] !== "undefined" && (
                  <div className={`text-xs ${syncStatus[r.id].startsWith("OK") ? "text-green-700 dark:text-green-300" : "text-rose-600 dark:text-rose-300"}`}>
                    Sync: {syncStatus[r.id]}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
      <div className="rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 shadow-sm p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Data maintenance</div>
            <div className="text-xs text-gray-500 dark:text-gray-400">Danger zone: permanently remove stored usage and peers.</div>
          </div>
        </div>
        {maintMsg && <div className="text-sm text-green-700 mb-3">{maintMsg}</div>}
        {maintErr && <div className="text-sm text-red-600 mb-3">{maintErr}</div>}
        <div className="grid gap-3 text-sm">
          <div className="flex items-center justify-between">
            <div className="text-xs text-gray-600 dark:text-gray-300">Purge all usage data (samples, daily and monthly rollups). Peers and routers stay.</div>
            <button
              type="button"
              disabled={maintBusy !== null}
              onClick={() => { setMaintErr(""); setMaintMsg(""); setConfirmAction("usage"); }}
              className="rounded-full bg-rose-50 text-rose-700 px-4 py-1.5 text-xs shadow hover:bg-rose-100 disabled:opacity-50"
            >
              Purge usage
            </button>
          </div>
          <div className="flex items-center justify-between">
            <div className="text-xs text-gray-600 dark:text-gray-300">Delete all peers (and their quotas/usages). Routers remain configured.</div>
            <button
              type="button"
              disabled={maintBusy !== null}
              onClick={() => { setMaintErr(""); setMaintMsg(""); setConfirmAction("peers"); }}
              className="rounded-full bg-rose-600 text-white px-4 py-1.5 text-xs shadow hover:bg-rose-700 disabled:opacity-50"
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
            <div className="text-xs text-gray-500 dark:text-gray-400">Manage admin accounts for this application.</div>
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
        <div className="grid gap-2">
          {users.map((u) => (
            <div key={u.id} className="rounded-2xl ring-1 ring-gray-200 dark:ring-gray-800 bg-white dark:bg-gray-950 p-3 flex items-center justify-between">
              <div>
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{u.username}</div>
                <div className="text-xs text-gray-500 dark:text-gray-400">Created: {new Date(u.created_at).toLocaleDateString()}</div>
              </div>
              <button
                onClick={() => handleDeleteUser(u.id)}
                className="rounded-full bg-rose-50 text-rose-700 px-3 py-1 text-xs shadow hover:bg-rose-100 dark:bg-rose-500/10 dark:text-rose-300 dark:hover:bg-rose-500/20"
              >
                Delete
              </button>
            </div>
          ))}
          {users.length === 0 && (
            <div className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-800 p-4 text-sm text-gray-500 dark:text-gray-400">No users found.</div>
          )}
        </div>
      </div>
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
            <div className="flex items-center justify-end gap-3 pt-2">
              <button onClick={() => setShowRouterModal(false)} className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">Cancel</button>
              <button disabled={routerBusy} onClick={handleSaveRouter} className="rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white">{editingRouter ? "Save changes" : "Add profile"}</button>
            </div>
          </div>
        </div>
      )}
      {confirmDeleteRouter && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-sm rounded-2xl bg-white dark:bg-gray-900 ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 grid gap-4">
            <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">Remove profile</div>
            <div className="text-sm text-gray-600 dark:text-gray-300">
              Remove <span className="font-medium text-gray-900 dark:text-gray-100">{confirmDeleteRouter.name}</span> from this app? This also deletes its peers from the DB. It does not delete peers on the router.
            </div>
            <div className="flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => setConfirmDeleteRouter(null)}
                className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={async () => {
                  try {
                    await deleteRouter(confirmDeleteRouter.id);
                    await loadRouters();
                    try {
                      const ar = await getActiveRouter();
                      setActiveRouterId(ar?.router_id ?? null);
                    } catch {
                      setActiveRouterId(null);
                    }
                    setConfirmDeleteRouter(null);
                  } catch (e: any) {
                    setRouterErr(e?.message || "Failed to delete router");
                    setConfirmDeleteRouter(null);
                  }
                }}
                className="rounded-full bg-rose-600 text-white px-4 py-2 text-sm shadow hover:bg-rose-700"
              >
                Delete
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


