import React from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  listFairUsageRules,
  createFairUsageRule,
  updateFairUsageRule,
  deleteFairUsageRule,
  assignPeersToRule,
  unassignPeerFromRule,
  listRouters,
  listSavedPeers,
  type FairUsageRuleDTO,
  type FairUsageRuleCreateDTO,
  type Router,
  type PeerListDTO,
} from "../api";

function Card({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const base = "rounded-3xl overflow-hidden ring-1 ring-gray-200 ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5 dark:ring-gray-800 dark:ring-offset-gray-950 dark:bg-gray-900";
  return <div className={`${base} ${className}`} {...props} />;
}

function formatBytes(bytes: number) {
  if (bytes <= 0) return "0";
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1) return `${gb % 1 === 0 ? gb.toFixed(0) : gb.toFixed(1)} GB`;
  const mb = bytes / (1024 * 1024);
  return `${mb % 1 === 0 ? mb.toFixed(0) : mb.toFixed(1)} MB`;
}

const SCOPE_UNIT_MAX: Record<string, number> = { hour: 168, day: 90, week: 52, month: 24 };

function clampScopeCount(unit: string, count: number): number {
  const cap = SCOPE_UNIT_MAX[unit] ?? 24;
  return Math.max(1, Math.min(cap, Math.floor(count || 1)));
}

const EMPTY_FORM = {
  name: "",
  description: "",
  quota_mode: "combined" as "combined" | "independent",
  download_value: 50,
  download_unit: "gb" as "gb" | "mb",
  upload_value: 25,
  upload_unit: "gb" as "gb" | "mb",
  throttle_download_kbps: 2000,
  throttle_upload_kbps: 1000,
  scope_period_count: 1,
  scope_period_unit: "month" as "hour" | "day" | "week" | "month",
  scope_type: "global" as "global" | "router" | "peer",
  router_id: null as number | null,
  peer_ids: [] as number[],
  enabled: true,
};

function toBytes(value: number, unit: "gb" | "mb") {
  return unit === "gb" ? value * 1024 * 1024 * 1024 : value * 1024 * 1024;
}

function fromBytes(bytes: number): { value: number; unit: "gb" | "mb" } {
  const gb = bytes / (1024 * 1024 * 1024);
  if (gb >= 1 && gb === Math.floor(gb)) return { value: gb, unit: "gb" };
  if (gb >= 1) return { value: parseFloat(gb.toFixed(2)), unit: "gb" };
  return { value: Math.round(bytes / (1024 * 1024)), unit: "mb" };
}

export default function FairUsagePage() {
  const navigate = useNavigate();
  const [rules, setRules] = React.useState<FairUsageRuleDTO[]>([]);
  const [routers, setRouters] = React.useState<Router[]>([]);
  const [peers, setPeers] = React.useState<PeerListDTO[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState("");
  const [form, setForm] = React.useState({ ...EMPTY_FORM });
  const [editingId, setEditingId] = React.useState<number | null>(null);
  const [saving, setSaving] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState<FairUsageRuleDTO | null>(null);
  const [assignModal, setAssignModal] = React.useState<FairUsageRuleDTO | null>(null);
  const [assignSearch, setAssignSearch] = React.useState("");
  const [assignSelected, setAssignSelected] = React.useState<number[]>([]);

  const load = React.useCallback(async () => {
    try {
      const [r, rt, p] = await Promise.all([listFairUsageRules(), listRouters(), listSavedPeers()]);
      setRules(r);
      setRouters(rt);
      setPeers(p);
    } catch (e: any) {
      setErr(e?.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { load(); }, [load]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) { setErr("Rule name is required"); return; }
    setErr("");
    setSaving(true);
    try {
      const dto: FairUsageRuleCreateDTO = {
        name: form.name.trim(),
        description: form.description.trim(),
        quota_mode: form.quota_mode,
        download_quota_bytes: toBytes(form.download_value, form.download_unit),
        upload_quota_bytes: form.quota_mode === "independent" ? toBytes(form.upload_value, form.upload_unit) : null,
        throttle_download_kbps: form.throttle_download_kbps,
        throttle_upload_kbps: form.throttle_upload_kbps,
        scope_period_count: clampScopeCount(form.scope_period_unit, form.scope_period_count),
        scope_period_unit: form.scope_period_unit,
        scope_type: form.scope_type,
        router_id: form.scope_type === "router" ? form.router_id : null,
        peer_ids: form.scope_type === "peer" ? form.peer_ids : [],
        enabled: form.enabled,
      };
      if (editingId) {
        await updateFairUsageRule(editingId, dto);
      } else {
        await createFairUsageRule(dto);
      }
      setForm({ ...EMPTY_FORM });
      setEditingId(null);
      await load();
    } catch (e: any) {
      setErr(e?.message || "Failed to save rule");
    } finally {
      setSaving(false);
    }
  };

  const startEdit = (rule: FairUsageRuleDTO) => {
    const dl = fromBytes(rule.download_quota_bytes);
    const ul = rule.upload_quota_bytes ? fromBytes(rule.upload_quota_bytes) : { value: 25, unit: "gb" as const };
    setForm({
      name: rule.name,
      description: rule.description,
      quota_mode: rule.quota_mode as "combined" | "independent",
      download_value: dl.value,
      download_unit: dl.unit,
      upload_value: ul.value,
      upload_unit: ul.unit,
      throttle_download_kbps: rule.throttle_download_kbps,
      throttle_upload_kbps: rule.throttle_upload_kbps,
      scope_period_count: rule.scope_period_count,
      scope_period_unit: rule.scope_period_unit,
      scope_type: rule.scope_type,
      router_id: rule.router_id,
      peer_ids: rule.assigned_peers.map(p => p.peer_id),
      enabled: rule.enabled,
    });
    setEditingId(rule.id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleDelete = async () => {
    if (!confirmDelete) return;
    try {
      await deleteFairUsageRule(confirmDelete.id);
      setConfirmDelete(null);
      await load();
    } catch (e: any) {
      setErr(e?.message || "Failed to delete rule");
      setConfirmDelete(null);
    }
  };

  const openAssignModal = (rule: FairUsageRuleDTO) => {
    setAssignModal(rule);
    setAssignSearch("");
    setAssignSelected(rule.assigned_peers.map(p => p.peer_id));
  };

  const handleAssignSave = async () => {
    if (!assignModal) return;
    try {
      const currentIds = new Set(assignModal.assigned_peers.map(p => p.peer_id));
      const toAdd = assignSelected.filter(id => !currentIds.has(id));
      const toRemove = [...currentIds].filter(id => !assignSelected.includes(id));
      if (toAdd.length > 0) await assignPeersToRule(assignModal.id, toAdd);
      for (const pid of toRemove) await unassignPeerFromRule(assignModal.id, pid);
      setAssignModal(null);
      await load();
    } catch (e: any) {
      setErr(e?.message || "Failed to update assignments");
    }
  };

  const routerById = React.useMemo(() => {
    const m: Record<number, Router> = {};
    for (const r of routers) m[r.id] = r;
    return m;
  }, [routers]);

  const routerName = (routerId: number) => routerById[routerId]?.name?.trim() || `Router #${routerId}`;

  const filteredAssignPeers = React.useMemo(() => {
    if (!assignSearch.trim()) return peers;
    const q = assignSearch.toLowerCase();
    return peers.filter((p) => {
      const rname = (routerById[p.router_id]?.name?.trim() || `Router #${p.router_id}`).toLowerCase();
      return (
        p.name.toLowerCase().includes(q) ||
        p.allowed_address.toLowerCase().includes(q) ||
        p.public_key.toLowerCase().includes(q) ||
        rname.includes(q) ||
        String(p.router_id).includes(q)
      );
    });
  }, [peers, assignSearch, routerById]);

  if (loading) {
    return (
      <div className="mx-auto px-4 md:px-6 py-6">
        <div className="text-sm text-gray-500 dark:text-gray-400">Loading...</div>
      </div>
    );
  }

  return (
    <div className="mx-auto px-4 md:px-6 py-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Fair Usage</h1>
        <Link to="/" className="inline-flex items-center gap-2 rounded-full bg-white text-gray-900 px-4 py-1.5 text-sm ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-800">
          ← Dashboard
        </Link>
      </div>

      <div className="mx-auto w-full max-w-[960px]">
        {/* Rule Builder */}
        <Card className="p-5 md:p-6 mb-6 !hover:shadow-md !hover:-translate-y-0">
          <form onSubmit={handleSubmit}>
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                {editingId ? "Edit rule" : "Create rule"}
              </div>
              {editingId && (
                <button
                  type="button"
                  onClick={() => { setForm({ ...EMPTY_FORM }); setEditingId(null); }}
                  className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                >
                  Cancel edit
                </button>
              )}
            </div>

            <div className="grid gap-4">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Rule name</label>
                  <input
                    required
                    value={form.name}
                    onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                    className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                    placeholder="e.g. Office 50GB Monthly"
                  />
                </div>
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Description (optional)</label>
                  <input
                    value={form.description}
                    onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                    className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                    placeholder="Optional note"
                  />
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Quota mode</label>
                  <div className="flex gap-3">
                    <label className="inline-flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                      <input type="radio" name="quota_mode" checked={form.quota_mode === "combined"} onChange={() => setForm(f => ({ ...f, quota_mode: "combined" }))} className="text-gray-900 dark:text-gray-100" />
                      Combined
                    </label>
                    <label className="inline-flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-200 cursor-pointer">
                      <input type="radio" name="quota_mode" checked={form.quota_mode === "independent"} onChange={() => setForm(f => ({ ...f, quota_mode: "independent" }))} className="text-gray-900 dark:text-gray-100" />
                      Separate
                    </label>
                  </div>
                </div>
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Time scope</label>
                  <div className="flex flex-wrap gap-2 items-center">
                    <span className="text-xs text-gray-500 dark:text-gray-400">Every</span>
                    <input
                      type="number"
                      min={1}
                      max={SCOPE_UNIT_MAX[form.scope_period_unit] ?? 24}
                      value={form.scope_period_count}
                      onChange={e =>
                        setForm(f => ({
                          ...f,
                          scope_period_count: clampScopeCount(f.scope_period_unit, Number(e.target.value || 1)),
                        }))
                      }
                      className="w-20 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                    />
                    <select
                      value={form.scope_period_unit}
                      onChange={e => {
                        const u = e.target.value as "hour" | "day" | "week" | "month";
                        setForm(f => ({
                          ...f,
                          scope_period_unit: u,
                          scope_period_count: clampScopeCount(u, f.scope_period_count),
                        }));
                      }}
                      className="min-w-[140px] rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950 dark:text-gray-100"
                    >
                      <option value="hour">hour(s)</option>
                      <option value="day">day(s)</option>
                      <option value="week">week(s)</option>
                      <option value="month">month(s)</option>
                    </select>
                  </div>
                  <p className="text-[11px] text-gray-500 dark:text-gray-400">Quota resets at the end of each period (aligned to your timezone in Settings).</p>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">
                    {form.quota_mode === "combined" ? "Total quota" : "Download quota"}
                  </label>
                  <div className="flex gap-2">
                    <input
                      type="number"
                      min={1}
                      value={form.download_value}
                      onChange={e => setForm(f => ({ ...f, download_value: Math.max(1, Number(e.target.value || 1)) }))}
                      className="w-24 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                    />
                    <select
                      value={form.download_unit}
                      onChange={e => setForm(f => ({ ...f, download_unit: e.target.value as "gb" | "mb" }))}
                      className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950 dark:text-gray-100"
                    >
                      <option value="gb">GB</option>
                      <option value="mb">MB</option>
                    </select>
                  </div>
                </div>
                {form.quota_mode === "independent" && (
                  <div className="grid gap-1">
                    <label className="text-xs text-gray-500 dark:text-gray-400">Upload quota</label>
                    <div className="flex gap-2">
                      <input
                        type="number"
                        min={1}
                        value={form.upload_value}
                        onChange={e => setForm(f => ({ ...f, upload_value: Math.max(1, Number(e.target.value || 1)) }))}
                        className="w-24 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                      />
                      <select
                        value={form.upload_unit}
                        onChange={e => setForm(f => ({ ...f, upload_unit: e.target.value as "gb" | "mb" }))}
                        className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950 dark:text-gray-100"
                      >
                        <option value="gb">GB</option>
                        <option value="mb">MB</option>
                      </select>
                    </div>
                  </div>
                )}
              </div>

              <div className="grid gap-4 md:grid-cols-2">
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Throttle download (kbps)</label>
                  <input
                    type="number"
                    min={100}
                    value={form.throttle_download_kbps}
                    onChange={e => setForm(f => ({ ...f, throttle_download_kbps: Math.max(100, Number(e.target.value || 100)) }))}
                    className="w-32 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  />
                  <span className="text-[11px] text-gray-400 dark:text-gray-500">{(form.throttle_download_kbps / 1000).toFixed(1)} Mbps</span>
                </div>
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Throttle upload (kbps)</label>
                  <input
                    type="number"
                    min={100}
                    value={form.throttle_upload_kbps}
                    onChange={e => setForm(f => ({ ...f, throttle_upload_kbps: Math.max(100, Number(e.target.value || 100)) }))}
                    className="w-32 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  />
                  <span className="text-[11px] text-gray-400 dark:text-gray-500">{(form.throttle_upload_kbps / 1000).toFixed(1)} Mbps</span>
                </div>
              </div>

              <div className="grid gap-4 md:grid-cols-2 pt-3 border-t border-gray-100 dark:border-gray-800">
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Scope</label>
                  <div className="flex gap-3">
                    {(["global", "router", "peer"] as const).map(s => (
                      <label key={s} className="inline-flex items-center gap-1.5 text-sm text-gray-700 dark:text-gray-200 cursor-pointer capitalize">
                        <input type="radio" name="scope_type" checked={form.scope_type === s} onChange={() => setForm(f => ({ ...f, scope_type: s }))} className="text-gray-900 dark:text-gray-100" />
                        {s}
                      </label>
                    ))}
                  </div>
                </div>

                {form.scope_type === "router" && (
                  <div className="grid gap-1">
                    <label className="text-xs text-gray-500 dark:text-gray-400">Router</label>
                    <select
                      value={form.router_id ?? ""}
                      onChange={e => setForm(f => ({ ...f, router_id: Number(e.target.value) || null }))}
                      className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950 dark:text-gray-100"
                    >
                      <option value="">Select router</option>
                      {routers.map(r => (
                        <option key={r.id} value={r.id}>{r.name} ({r.host})</option>
                      ))}
                    </select>
                  </div>
                )}

                {form.scope_type === "peer" && (
                  <div className="grid gap-1">
                    <label className="text-xs text-gray-500 dark:text-gray-400">Peers ({form.peer_ids.length} selected)</label>
                    <div className="max-h-40 overflow-y-auto rounded-xl border border-gray-200 dark:border-gray-800 p-2">
                      {peers.map(p => (
                        <label key={p.id} className="flex items-start gap-2 px-2 py-1 text-xs text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 rounded cursor-pointer">
                          <input
                            type="checkbox"
                            checked={form.peer_ids.includes(p.id)}
                            onChange={e => {
                              setForm(f => ({
                                ...f,
                                peer_ids: e.target.checked
                                  ? [...f.peer_ids, p.id]
                                  : f.peer_ids.filter(id => id !== p.id),
                              }));
                            }}
                            className="rounded border-gray-300 dark:border-gray-700 mt-0.5 shrink-0"
                          />
                          <span className="min-w-0 flex flex-col gap-0.5">
                            <span>
                              <span className="font-medium">{p.name || p.public_key.slice(0, 12)}</span>
                              <span className="text-gray-500 dark:text-gray-400"> · {routerName(p.router_id)}</span>
                            </span>
                            <span className="text-gray-400">{p.allowed_address}</span>
                          </span>
                        </label>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>

            {err && <div className="text-sm text-red-600 mt-3">{err}</div>}

            <div className="flex items-center justify-end gap-3 mt-4 pt-4 border-t border-gray-100 dark:border-gray-800">
              <label className="inline-flex items-center gap-2 text-xs text-gray-700 dark:text-gray-200 mr-auto">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={e => setForm(f => ({ ...f, enabled: e.target.checked }))}
                  className="rounded border-gray-300 dark:border-gray-700"
                />
                Enabled
              </label>
              <button
                type="submit"
                disabled={saving}
                className="rounded-full bg-gray-900 text-white px-5 py-2 text-sm shadow hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
              >
                {saving ? "Saving..." : editingId ? "Update rule" : "Create rule"}
              </button>
            </div>
          </form>
        </Card>

        {/* Active Rules */}
        <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-3">
          Active rules ({rules.length})
        </div>

        {rules.length === 0 ? (
          <Card className="p-5 !hover:shadow-md !hover:-translate-y-0">
            <div className="text-sm text-gray-500 dark:text-gray-400">No fair usage rules yet. Create your first rule above.</div>
          </Card>
        ) : (
          <div className="grid gap-4">
            {rules.map(rule => (
              <Card key={rule.id} className="p-5 !hover:shadow-md !hover:-translate-y-0">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{rule.name}</span>
                      <span className={`rounded-full px-2 py-0.5 text-[11px] ${rule.enabled ? "bg-green-100 text-green-800 dark:bg-green-500/10 dark:text-green-300" : "bg-gray-200 text-gray-600 dark:bg-gray-800 dark:text-gray-400"}`}>
                        {rule.enabled ? "Active" : "Disabled"}
                      </span>
                      <span className="rounded-full bg-gray-100 text-gray-700 px-2 py-0.5 text-[11px] dark:bg-gray-800 dark:text-gray-300">
                        {rule.scope_label}
                      </span>
                      <span className="rounded-full bg-indigo-50 text-indigo-700 px-2 py-0.5 text-[11px] dark:bg-indigo-500/10 dark:text-indigo-300 capitalize">
                        {rule.scope_type}{rule.scope_type === "router" && rule.router_id ? ` · ${routerById[rule.router_id]?.name || rule.router_id}` : ""}
                      </span>
                    </div>
                    {rule.description && (
                      <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">{rule.description}</div>
                    )}
                    <div className="text-xs text-gray-600 dark:text-gray-300 mt-2">
                      Quota: {formatBytes(rule.download_quota_bytes)}
                      {rule.quota_mode === "independent" && rule.upload_quota_bytes ? ` ↓ / ${formatBytes(rule.upload_quota_bytes)} ↑` : ""}
                      {rule.quota_mode === "combined" ? " total" : ""}
                      {" · "}Throttle: {(rule.throttle_download_kbps / 1000).toFixed(1)}/{(rule.throttle_upload_kbps / 1000).toFixed(1)} Mbps
                    </div>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    <button onClick={() => openAssignModal(rule)} className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">
                      Peers
                    </button>
                    <button onClick={() => startEdit(rule)} className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">
                      Edit
                    </button>
                    <button onClick={() => setConfirmDelete(rule)} className="rounded-full bg-rose-50 text-rose-700 px-3 py-1 text-xs shadow hover:bg-rose-100 dark:bg-rose-500/10 dark:text-rose-300 dark:hover:bg-rose-500/20">
                      Delete
                    </button>
                  </div>
                </div>
                {rule.assigned_peers.length > 0 && (
                  <div className="flex flex-wrap gap-2 mt-3 pt-3 border-t border-gray-100 dark:border-gray-800">
                    {rule.assigned_peers.map(p => (
                      <button
                        key={p.peer_id}
                        onClick={() => navigate(`/peer/${p.peer_id}`)}
                        className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700 transition max-w-full text-left"
                        title={`${routerName(p.router_id)} · ${p.allowed_address}`}
                      >
                        <span className="font-medium">{p.name || p.allowed_address}</span>
                        <span className="text-gray-500 dark:text-gray-400"> · {routerName(p.router_id)}</span>
                      </button>
                    ))}
                    {rule.scope_type !== "peer" && (
                      <span className="rounded-full bg-gray-50 text-gray-500 px-3 py-1 text-[11px] dark:bg-gray-800/50 dark:text-gray-400">
                        +{rule.scope_type === "global" ? " all peers" : ` all peers on ${routerById[rule.router_id!]?.name || "router"}`}
                      </span>
                    )}
                  </div>
                )}
              </Card>
            ))}
          </div>
        )}
      </div>

      {/* Delete Confirmation */}
      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-sm rounded-2xl bg-white dark:bg-gray-900 ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 grid gap-4">
            <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">Delete rule</div>
            <div className="text-sm text-gray-600 dark:text-gray-300">
              Delete <span className="font-medium text-gray-900 dark:text-gray-100">{confirmDelete.name}</span>? This will remove any active throttle queues from RouterOS.
            </div>
            <div className="flex items-center justify-end gap-3">
              <button onClick={() => setConfirmDelete(null)} className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">Cancel</button>
              <button onClick={handleDelete} className="rounded-full bg-rose-600 text-white px-4 py-2 text-sm shadow hover:bg-rose-700">Delete</button>
            </div>
          </div>
        </div>
      )}

      {/* Assign Peers Modal */}
      {assignModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
          <div className="w-full max-w-lg max-h-[80vh] rounded-3xl bg-white dark:bg-gray-900 ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 grid gap-4 overflow-hidden">
            <div className="flex items-center justify-between">
              <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">Assign peers to {assignModal.name}</div>
              <button onClick={() => setAssignModal(null)} className="rounded-full bg-gray-100 text-gray-800 h-8 w-8 flex items-center justify-center hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">✕</button>
            </div>
            <input
              placeholder="Search by peer, router, or address..."
              value={assignSearch}
              onChange={e => setAssignSearch(e.target.value)}
              className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
            />
            <div className="overflow-y-auto max-h-64 rounded-xl border border-gray-200 dark:border-gray-800">
              {filteredAssignPeers.map(p => (
                <label key={p.id} className="flex items-center gap-3 px-3 py-2 text-sm text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800 cursor-pointer border-b border-gray-100 dark:border-gray-800 last:border-0">
                  <input
                    type="checkbox"
                    checked={assignSelected.includes(p.id)}
                    onChange={e => {
                      setAssignSelected(prev =>
                        e.target.checked
                          ? [...prev, p.id]
                          : prev.filter(id => id !== p.id)
                      );
                    }}
                    className="rounded border-gray-300 dark:border-gray-700"
                  />
                  <div className="flex-1 min-w-0">
                    <div className="font-medium truncate">{p.name || p.public_key.slice(0, 16)}</div>
                    <div className="text-xs text-gray-500 dark:text-gray-400 truncate">{routerName(p.router_id)}</div>
                    <div className="text-xs text-gray-400 truncate">{p.allowed_address}</div>
                  </div>
                </label>
              ))}
              {filteredAssignPeers.length === 0 && (
                <div className="p-4 text-sm text-gray-500 dark:text-gray-400">No peers match.</div>
              )}
            </div>
            <div className="flex items-center justify-between pt-2">
              <span className="text-xs text-gray-500 dark:text-gray-400">{assignSelected.length} selected</span>
              <div className="flex gap-3">
                <button onClick={() => setAssignModal(null)} className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">Cancel</button>
                <button onClick={handleAssignSave} className="rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white">Save</button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
