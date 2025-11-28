import React from "react";
import { Link } from "react-router-dom";
import { getSettings, putSettings, listRouters, createRouter, updateRouter, deleteRouter, testRouter, purgeUsage, purgePeers, type Router, type RouterProto } from "../api";

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
  });
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");
  const [ok, setOk] = React.useState("");
  const [routers, setRouters] = React.useState<Router[]>([]);
  const [routerMsg, setRouterMsg] = React.useState("");
  const [routerErr, setRouterErr] = React.useState("");
  const [routerBusy, setRouterBusy] = React.useState(false);
  const [testBusyId, setTestBusyId] = React.useState<number | null>(null);
  const [testStatus, setTestStatus] = React.useState<Record<number, string>>({});
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

  const loadSettings = React.useCallback(async () => {
    try {
      const s = await getSettings();
      setForm(s);
    } catch {
      /* ignore */
    }
  }, []);

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
        <h1 className="text-xl font-semibold text-gray-900">Settings</h1>
        <Link to="/" className="inline-flex items-center gap-2 rounded-full bg-white text-gray-900 px-4 py-1.5 text-sm ring-1 ring-gray-200 shadow-sm hover:ring-gray-300">
          ← Dashboard
        </Link>
      </div>
      <div className="rounded-3xl ring-1 ring-gray-200 bg-white shadow-sm p-5 mb-6">
        <div className="grid gap-4">
          <div>
            <label className="block text-xs text-gray-500 mb-1">Poll interval (seconds)</label>
            <input type="number" min={5} className="w-40 rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300" value={form.poll_interval_seconds} onChange={e=>setForm({ ...form, poll_interval_seconds: Math.max(5, Number(e.target.value||0)) })} />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Online threshold (seconds)</label>
            <input type="number" min={5} className="w-40 rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300" value={form.online_threshold_seconds} onChange={e=>setForm({ ...form, online_threshold_seconds: Math.max(5, Number(e.target.value||0)) })} />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Monthly reset day (1–28)</label>
            <input type="number" min={1} max={28} className="w-40 rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300" value={form.monthly_reset_day} onChange={e=>setForm({ ...form, monthly_reset_day: Math.min(28, Math.max(1, Number(e.target.value||1))) })} />
          </div>
          <div>
            <label className="block text-xs text-gray-500 mb-1">Timezone</label>
            <input className="w-full md:w-80 rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300" value={form.timezone} onChange={e=>setForm({ ...form, timezone: e.target.value })} />
          </div>
          <div className="grid gap-2 pt-2 border-t border-gray-100">
            <label className="inline-flex items-center gap-2 text-xs text-gray-700">
              <input
                type="checkbox"
                className="rounded border-gray-300 text-gray-900 focus:ring-gray-300"
                checked={form.show_kind_pills}
                onChange={(e) => setForm({ ...form, show_kind_pills: e.target.checked })}
              />
              Show inbound/outbound pills on cards
            </label>
            <label className="inline-flex items-center gap-2 text-xs text-gray-700">
              <input
                type="checkbox"
                className="rounded border-gray-300 text-gray-900 focus:ring-gray-300"
                checked={form.show_hw_stats}
                onChange={(e) => setForm({ ...form, show_hw_stats: e.target.checked })}
              />
              Show hardware stats bar on dashboard
            </label>
            <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700">
              <span className="text-gray-500">Dashboard auto refresh default</span>
              <input
                type="number"
                min={5}
                className="w-20 rounded-xl border border-gray-200 px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-300"
                value={form.dashboard_refresh_seconds}
                onChange={(e) =>
                  setForm({
                    ...form,
                    dashboard_refresh_seconds: Math.max(5, Number(e.target.value || 5)),
                  })
                }
              />
              <span className="text-gray-500">seconds</span>
            </div>
            <div className="flex flex-wrap items-center gap-3 text-xs text-gray-700">
              <span className="text-gray-500">Peer detail default scope</span>
              <input
                type="number"
                min={1}
                className="w-16 rounded-xl border border-gray-200 px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-300"
                value={form.peer_default_scope_value}
                onChange={(e) =>
                  setForm({
                    ...form,
                    peer_default_scope_value: Math.max(1, Number(e.target.value || 1)),
                  })
                }
              />
              <select
                className="rounded-xl border border-gray-200 px-3 py-1.5 text-xs focus:ring-2 focus:ring-gray-300 bg-white"
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
            </div>
          </div>
          {err && <div className="text-sm text-red-600">{err}</div>}
          {ok && <div className="text-sm text-green-700">{ok}</div>}
          <div>
            <button disabled={busy} onClick={async()=>{ setErr(""); setOk(""); try { setBusy(true); const saved = await putSettings(form); setForm(saved); setOk("Saved"); } catch (e:any) { setErr(e?.message||"Save failed"); } finally { setBusy(false); } }} className="rounded-full bg-gray-900 text-white px-5 py-2 text-sm shadow hover:bg-black disabled:opacity-50">Save settings</button>
          </div>
        </div>
      </div>
      <div className="rounded-3xl ring-1 ring-gray-200 bg-white shadow-sm p-5 mb-6">
        <div className="flex items-center justify-between mb-4">
          <div>
            <div className="text-sm font-semibold text-gray-900">Connection profiles</div>
            <div className="text-xs text-gray-500">Manage RouterOS endpoints used by the wizard and dashboard.</div>
          </div>
          <button onClick={()=>openRouterModal()} className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black">
            Add profile
          </button>
        </div>
        {routerMsg && <div className="text-sm text-green-700 mb-3">{routerMsg}</div>}
        {routerErr && <div className="text-sm text-red-600 mb-3">{routerErr}</div>}
        <div className="grid gap-4">
          {routers.length === 0 ? (
            <div className="rounded-2xl border border-dashed border-gray-200 p-4 text-sm text-gray-500">No routers yet. Add your first connection profile.</div>
          ) : (
            routers.map(r => (
              <div key={r.id} className="rounded-2xl ring-1 ring-gray-200 p-4 flex flex-col gap-3">
                <div className="flex items-center justify-between">
                  <div>
                    <div className="text-sm font-medium text-gray-900">{r.name}</div>
                    <div className="text-xs text-gray-500">{r.proto.toUpperCase()} · {r.host}:{r.port} · {r.username}</div>
                  </div>
                  <div className="flex items-center gap-2">
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
                      className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200"
                    >
                      {testBusyId === r.id ? "Testing..." : "Test"}
                    </button>
                    <button onClick={()=>openRouterModal(r)} className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200">Edit</button>
                    <button
                      onClick={async () => {
                        if (!confirm(`Remove router "${r.name}"? This deletes its peers.`)) return;
                        try {
                          await deleteRouter(r.id);
                          await loadRouters();
                        } catch (e: any) {
                          setRouterErr(e?.message || "Failed to delete router");
                        }
                      }}
                      className="rounded-full bg-rose-50 text-rose-700 px-3 py-1 text-xs shadow hover:bg-rose-100"
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
      <div className="rounded-3xl ring-1 ring-gray-200 bg-white shadow-sm p-5">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-sm font-semibold text-gray-900">Data maintenance</div>
            <div className="text-xs text-gray-500">Danger zone: permanently remove stored usage and peers.</div>
          </div>
        </div>
        {maintMsg && <div className="text-sm text-green-700 mb-3">{maintMsg}</div>}
        {maintErr && <div className="text-sm text-red-600 mb-3">{maintErr}</div>}
        <div className="grid gap-3 text-sm">
          <div className="flex items-center justify-between">
            <div className="text-xs text-gray-600">Purge all usage data (samples, daily and monthly rollups). Peers and routers stay.</div>
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
            <div className="text-xs text-gray-600">Delete all peers (and their quotas/usages). Routers remain configured.</div>
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
      {showRouterModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-lg rounded-3xl bg-white ring-1 ring-gray-200 shadow-lg p-6 grid gap-4">
            <div className="flex items-center justify-between">
              <div className="text-lg font-semibold text-gray-900">{editingRouter ? "Edit profile" : "Add profile"}</div>
              <button onClick={()=>setShowRouterModal(false)} className="rounded-full bg-gray-100 text-gray-800 h-8 w-8 flex items-center justify-center hover:bg-gray-200">✕</button>
            </div>
            <div className="grid gap-3">
              <div className="grid gap-1">
                <label className="text-xs text-gray-500">Name</label>
                <input value={routerForm.name} onChange={e=>setRouterForm(f=>({ ...f, name: e.target.value }))} className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300" placeholder="CHR Amsterdam" />
              </div>
              <div className="grid gap-1">
                <label className="text-xs text-gray-500">Host / IP</label>
                <input value={routerForm.host} onChange={e=>setRouterForm(f=>({ ...f, host: e.target.value }))} className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300" placeholder="10.0.0.1" />
              </div>
              <div className="grid gap-2 md:grid-cols-2">
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500">Method</label>
                  <select
                    value={routerForm.proto}
                    onChange={e=>{
                      const nextProto = e.target.value as RouterProto;
                      setRouterForm(f=>({ ...f, proto: nextProto, port: f.port || defaultProtoPort[nextProto] }));
                    }}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
                  >
                    <option value="rest">REST HTTPS</option>
                    <option value="rest-http">REST HTTP</option>
                    <option value="api">API TLS</option>
                    <option value="api-plain">API Plain</option>
                  </select>
                </div>
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500">Port</label>
                  <input
                    type="number"
                    value={routerForm.port}
                    onChange={e=>setRouterForm(f=>({ ...f, port: Number(e.target.value) }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
                  />
                </div>
              </div>
              <div className="grid gap-1">
                <label className="text-xs text-gray-500">Username</label>
                <input value={routerForm.username} onChange={e=>setRouterForm(f=>({ ...f, username: e.target.value }))} className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300" placeholder="admin" />
              </div>
              <div className="grid gap-1">
                <label className="text-xs text-gray-500">{editingRouter ? "Password (leave blank to keep)" : "Password"}</label>
                <input type="password" value={routerForm.password} onChange={e=>setRouterForm(f=>({ ...f, password: e.target.value }))} className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300" placeholder="••••••••" />
              </div>
              <label className="inline-flex items-center gap-2 text-sm text-gray-700">
                <input type="checkbox" checked={routerForm.tls_verify} onChange={e=>setRouterForm(f=>({ ...f, tls_verify: e.target.checked }))} className="rounded border-gray-300 text-gray-900 focus:ring-gray-300" />
                Verify TLS certificates
              </label>
            </div>
            {routerErr && <div className="text-sm text-red-600">{routerErr}</div>}
            <div className="flex items-center justify-end gap-3 pt-2">
              <button onClick={()=>setShowRouterModal(false)} className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200">Cancel</button>
              <button disabled={routerBusy} onClick={handleSaveRouter} className="rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50">{editingRouter ? "Save changes" : "Add profile"}</button>
            </div>
          </div>
        </div>
      )}
      {confirmAction && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-sm rounded-2xl bg-white ring-1 ring-gray-200 shadow-lg p-6 grid gap-4">
            <div className="text-lg font-semibold text-gray-900">
              {confirmAction === "usage" ? "Purge usage data" : "Purge all peers"}
            </div>
            <div className="text-sm text-gray-600">
              {confirmAction === "usage"
                ? "This permanently deletes all usage samples and rollups for every peer. Peers and routers are kept."
                : "This removes every peer (and related usage/quotas) from the database. Routers stay configured."}
            </div>
            <div className="flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={() => setConfirmAction(null)}
                className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200"
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
                className={`rounded-full px-4 py-2 text-sm shadow disabled:opacity-50 ${
                  confirmAction === "usage" ? "bg-rose-600 text-white hover:bg-rose-700" : "bg-rose-700 text-white hover:bg-rose-800"
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


