import React from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { listSavedPeers, getPeerUsage, listRouters, routerPeers, patchPeer, getPeerQuota, patchPeerQuota, resetPeerMetrics, deletePeer, getSettings, type SavedPeer, type UsagePoint, type Router, type PeerView, type Quota } from "../api";

function Card({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const base = "rounded-3xl overflow-hidden ring-1 ring-gray-200 ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5";
  return <div className={`${base} ${className}`} {...props} />;
}

export default function PeerDetail() {
  const params = useParams();
  const navigate = useNavigate();
  const peerId = Number(params.id);
  const [peer, setPeer] = React.useState<SavedPeer | null>(null);
  const [usage, setUsage] = React.useState<UsagePoint[]>([]);
  const [routerName, setRouterName] = React.useState<string>("");
  const [liveEndpoint, setLiveEndpoint] = React.useState<string>("—");
  const [liveOnline, setLiveOnline] = React.useState<boolean | null>(null);
  const [lastSeenLabel, setLastSeenLabel] = React.useState<string>("—");
  const [actionBusy, setActionBusy] = React.useState(false);
  const [actionErr, setActionErr] = React.useState("");
  const [quota, setQuota] = React.useState<Quota | null>(null);
  const [quotaBusy, setQuotaBusy] = React.useState(false);
  const [quotaErr, setQuotaErr] = React.useState("");
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const windowProgress = React.useMemo(() => {
    if (!quota || (!quota.valid_from && !quota.valid_until)) return null;
    const startMs = quota.valid_from ? new Date(quota.valid_from).getTime() : NaN;
    const endMs = quota.valid_until ? new Date(quota.valid_until).getTime() : NaN;
    if (!isFinite(startMs) || !isFinite(endMs) || endMs <= startMs) return null;
    const nowMs = Date.now();
    // Remaining fraction: 1 at start, 0 at end
    const ratio = (endMs - nowMs) / (endMs - startMs);
    return Math.max(0, Math.min(1, ratio));
  }, [quota?.valid_from, quota?.valid_until]);

  const fmtBytes = (n: number) => {
    if (!n || n <= 0) return "0 B";
    const units = ["B","KB","MB","GB","TB"]; let u = 0; let x = n;
    while (x >= 1024 && u < units.length - 1) { x /= 1024; u++; }
    return `${x.toFixed(x >= 100 ? 0 : x >= 10 ? 1 : 2)} ${units[u]}`;
  };
  React.useEffect(() => {
    (async () => {
      try {
        const peers = await listSavedPeers();
        const p = peers.find(x => x.id === peerId) || null;
        setPeer(p);
        // fetch router name for display
        try {
          const routers: Router[] = await listRouters();
          const r = routers.find(r => r.id === (p?.router_id || 0));
          setRouterName(r?.name || "");
        } catch {}
      } catch { setPeer(null); }
    })();
  }, [peerId]);
  type ScopeUnit = "minutes" | "hours" | "days";
  const [scopeUnit, setScopeUnit] = React.useState<ScopeUnit>("days");
  const [scopeValue, setScopeValue] = React.useState<number>(14);
  const [showKindPills, setShowKindPills] = React.useState(true);

  // Load UI defaults for scope + pills
  React.useEffect(() => {
    (async () => {
      try {
        const s = await getSettings();
        if (typeof s.peer_default_scope_value === "number" && s.peer_default_scope_value > 0) {
          setScopeValue(s.peer_default_scope_value);
        }
        if (typeof s.peer_default_scope_unit === "string" && ["minutes", "hours", "days"].includes(s.peer_default_scope_unit)) {
          setScopeUnit(s.peer_default_scope_unit as ScopeUnit);
        }
        if (typeof s.show_kind_pills === "boolean") {
          setShowKindPills(s.show_kind_pills);
        }
      } catch {
        // ignore, keep defaults
      }
    })();
  }, []);

  const loadUsage = React.useCallback(async () => {
    try {
      if (!peerId) return;
      if (scopeUnit === "days") {
        const points = await getPeerUsage(peerId, { window: "daily" });
        const trimmed =
          scopeValue > 0 && points.length > scopeValue
            ? points.slice(points.length - scopeValue)
            : points;
        setUsage(trimmed);
      } else {
        const seconds =
          scopeUnit === "minutes"
            ? Math.max(1, scopeValue) * 60
            : Math.max(1, scopeValue) * 3600;
        const points = await getPeerUsage(peerId, { window: "raw", seconds });
        setUsage(points);
      }
    } catch {
      setUsage([]);
    }
  }, [peerId, scopeUnit, scopeValue]);

  React.useEffect(() => {
    loadUsage();
  }, [loadUsage]);

  // Auto-refresh peer usage at roughly the poll interval (default 30s)
  React.useEffect(() => {
    const intervalSec = scopeUnit === "days" ? 30 : 10;
    const id = window.setInterval(() => {
      loadUsage();
    }, intervalSec * 1000);
    return () => window.clearInterval(id);
  }, [loadUsage, scopeUnit]);

  // Load quota
  React.useEffect(() => {
    (async () => {
      try {
        const q = await getPeerQuota(peerId);
        setQuota(q);
      } catch { setQuota(null); }
    })();
  }, [peerId]);

  // Load live data (endpoint, online, last seen) when we have peer
  React.useEffect(() => {
    (async () => {
      if (!peer) return;
      try {
        const live: PeerView[] = await routerPeers(peer.router_id, peer.interface);
        const me = live.find(x => x.public_key === peer.public_key);
          if (me) {
            setLiveEndpoint(me.endpoint || "—");
            setLiveOnline(!!me.online);
            if (me.last_handshake) {
              const ageSec = me.last_handshake; // age in seconds since last handshake
              const m = Math.floor(ageSec / 60);
              const h = Math.floor(m / 60);
              const d = Math.floor(h / 24);
              const mon = Math.floor(d / 30);
              const label =
                ageSec < 60 ? `${ageSec}s ago`
                : m < 60 ? `${m}m ago`
                : h < 24 ? `${h}h ago`
                : d < 30 ? `${d}d ago`
                : `${mon}mo ago`;
              setLastSeenLabel(label);
            } else {
              setLastSeenLabel("—");
            }
          }
      } catch {
        // Fallback demo: pseudo-random status by id to allow UI testing without live router
        const mode = (peer?.id || 0) % 3;
        const online = mode === 0;
        setLiveOnline(online);
        setLiveEndpoint("—");
        setLastSeenLabel(online ? "—" : mode === 1 ? "5m ago" : "18m ago");
      }
    })();
  }, [peer?.router_id, peer?.interface, peer?.public_key]);

  const kindPill = (() => {
    if (!showKindPills) return null;
    const addr = (peer?.allowed_address || "").trim();
    const outbound = addr === "0.0.0.0/0" || addr === "::/0";
    return (
      <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${outbound ? 'bg-amber-100 text-amber-800' : 'bg-green-100 text-green-800'}`}>
        <span className={`inline-block w-2 h-2 rounded-full ${outbound ? 'bg-amber-500' : 'bg-green-500'}`} />
        {outbound ? 'Outbound' : 'Inbound'}
      </span>
    );
  })();

  const statusPill = (() => {
    if (liveOnline === null) return null;
    const online = !!liveOnline;
    return (
      <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${online ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'}`}>
        <span className={`dot ${online ? 'bg-green-500' : 'bg-red-500'} pulse`} />
        {online ? 'Online' : `Last seen ${lastSeenLabel}`}
      </span>
    );
  })();

  return (
    <div className="mx-auto px-4 md:px-6 py-6">
      <div className="mx-auto my-12 md:my-16 w-full max-w-[960px] rounded-3xl ring-1 ring-gray-200 bg-white shadow-sm p-5 md:p-6 overflow-y-auto overflow-x-hidden">
        {!peer ? (
          <div className="text-sm text-gray-500">Loading…</div>
        ) : (
          <div className="grid gap-6">
            <div className="flex items-center justify-between mb-2">
              <div>
                <div className="text-base text-gray-500">{peer.name}</div>
                <div className="text-xl md:text-2xl text-gray-900">{peer.allowed_address.replace("/32", "")}</div>
              </div>
              <div className="flex flex-col items-end gap-2">
                {/* Allowance status */}
                {peer && (
                  <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${peer.disabled ? 'bg-rose-100 text-rose-800' : 'bg-indigo-100 text-indigo-800'}`}>
                    <span className={`inline-block w-2 h-2 rounded-full ${peer.disabled ? 'bg-rose-500' : 'bg-indigo-500'}`} />
                    {peer.disabled ? 'Deactivated' : 'Active'}
                  </span>
                )}
                {/* switch kind pill to blue/amber scheme */}
                {(() => {
                  const addr = (peer?.allowed_address || "").trim();
                  const outbound = addr === "0.0.0.0/0" || addr === "::/0";
                  return (
                    <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${outbound ? 'bg-amber-100 text-amber-800' : 'bg-blue-100 text-blue-800'}`}>
                      <span className={`inline-block w-2 h-2 rounded-full ${outbound ? 'bg-amber-500' : 'bg-blue-500'}`} />
                      {outbound ? 'Outbound' : 'Inbound'}
                    </span>
                  );
                })()}
                {statusPill}
              </div>
            </div>
            {/* Quota management */}
            <div className="grid gap-5 mt-6 pt-4 border-t border-gray-100">
              <div className="text-sm text-gray-700">Quota</div>
              <div className="grid gap-5 md:grid-cols-2">
                <div className="grid gap-3">
                  <label className="text-xs text-gray-500">Monthly data limit (GB)</label>
                  <div className="flex items-center gap-3">
                    <input
                      type="number"
                      min={0}
                      step={1}
                      className="w-32 rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
                      value={quota?.monthly_limit_bytes ? Math.round((quota.monthly_limit_bytes || 0) / (1024*1024*1024)) : 0}
                      onChange={(e) => {
                        const gb = Math.max(0, Number(e.target.value || 0));
                        setQuota(q => q ? { ...q, monthly_limit_bytes: gb * 1024*1024*1024 } : q);
                      }}
                    />
                    <button
                      disabled={quotaBusy || !quota}
                      onClick={async () => {
                        if (!quota) return;
                        setQuotaErr("");
                        try {
                          setQuotaBusy(true);
                          await patchPeerQuota(peerId, { monthly_limit_bytes: quota.monthly_limit_bytes || 0 });
                        } catch (e: any) { setQuotaErr(e?.message || "Failed to save quota"); }
                        finally { setQuotaBusy(false); }
                      }}
                      className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50"
                    >Save</button>
                  </div>
                  {/* Consumption bar */}
                  {quota && quota.monthly_limit_bytes ? (
                    <div className="mt-1">
                      <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
                        <span>Usage</span>
                        <span>{`${((quota.used_rx + quota.used_tx) / Math.max(1, quota.monthly_limit_bytes)).toLocaleString(undefined, { style: 'percent', minimumFractionDigits: 0 })}`}</span>
                      </div>
                      <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                        <div className="h-full bg-gray-900" style={{ width: `${Math.min(100, Math.round(((quota.used_rx + quota.used_tx) / quota.monthly_limit_bytes) * 100))}%` }} />
                      </div>
                    </div>
                  ) : <div className="text-xs text-gray-500">No data quota set</div>}
                </div>
                <div className="grid gap-3">
                  <label className="text-xs text-gray-500">Access window (optional)</label>
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="datetime-local"
                      className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
                      value={quota?.valid_from || ""}
                      onChange={(e) => setQuota(q => q ? { ...q, valid_from: e.target.value } : q)}
                    />
                    <input
                      type="datetime-local"
                      className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
                      value={quota?.valid_until || ""}
                      onChange={(e) => setQuota(q => q ? { ...q, valid_until: e.target.value } : q)}
                    />
                  </div>
                  <div>
                    <button
                      disabled={quotaBusy || !quota}
                      onClick={async () => {
                        if (!quota) return;
                        setQuotaErr("");
                        try {
                          setQuotaBusy(true);
                          await patchPeerQuota(peerId, { valid_from: quota.valid_from || "", valid_until: quota.valid_until || "" });
                        } catch (e: any) { setQuotaErr(e?.message || "Failed to save window"); }
                        finally { setQuotaBusy(false); }
                      }}
                      className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50"
                    >Save window</button>
                  </div>
                  {/* Time window bar */}
                  {quota?.valid_from || quota?.valid_until ? (
                    <div className="mt-1">
                      <div className="flex items-center justify-between text-xs text-gray-500 mb-1">
                        <span>Window</span>
                        <span>{quota.valid_from || '—'} → {quota.valid_until || '—'}</span>
                      </div>
                      <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                        <div className="h-full bg-gray-900" style={{ width: `${Math.round((windowProgress ?? 0) * 100)}%` }} />
                      </div>
                    </div>
                  ) : <div className="text-xs text-gray-500">No access window set</div>}
                </div>
              </div>
              {quotaErr && <div className="text-sm text-red-600">{quotaErr}</div>}
            </div>
            {/* Details */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 text-sm mt-6 pt-4 border-t border-gray-100">
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                <div className="text-gray-500">Interface</div>
                <div className="text-gray-900">{peer.interface}</div>
                <div className="text-gray-500">Router</div>
                <div className="text-gray-900">{routerName || `#${peer.router_id}`}</div>
                <div className="text-gray-500">Public key</div>
                <div className="text-gray-900 break-all">{peer.public_key}</div>
                <div className="text-gray-500">Endpoint</div>
                <div className="text-gray-900">{liveEndpoint}</div>
                <div className="text-gray-500">Last seen</div>
                <div className="text-gray-900">{lastSeenLabel}</div>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                <div className="text-gray-500">Selected</div>
                <div className="text-gray-900">{peer.selected ? 'Yes' : 'No'}</div>
                <div className="text-gray-500">Disabled</div>
                <div className="text-gray-900">{peer.disabled ? 'Yes' : 'No'}</div>
                <div className="text-gray-500">Monthly download (TX)</div>
                <div className="text-gray-900">{fmtBytes(usage.reduce((a,b)=>a+(b.tx||0),0))}</div>
                <div className="text-gray-500">Monthly upload (RX)</div>
                <div className="text-gray-900">{fmtBytes(usage.reduce((a,b)=>a+(b.rx||0),0))}</div>
              </div>
            </div>
            <div className="p-0 mt-6">
              <div className="flex items-center justify-between mb-2">
                <div className="text-sm text-gray-700">Usage</div>
                <div className="flex items-center gap-2 text-xs text-gray-600">
                  <span>Last</span>
                  <input
                    type="number"
                    min={1}
                    value={scopeValue}
                    onChange={(e) => setScopeValue(Math.max(1, Number(e.target.value) || 1))}
                    className="w-14 rounded-full border border-gray-200 px-2 py-1 text-xs focus:ring-1 focus:ring-gray-300"
                  />
                  <select
                    value={scopeUnit}
                    onChange={(e) => setScopeUnit(e.target.value as ScopeUnit)}
                    className="rounded-full border border-gray-200 px-2 py-1 text-xs focus:ring-1 focus:ring-gray-300 bg-white"
                  >
                    <option value="minutes">minutes</option>
                    <option value="hours">hours</option>
                    <option value="days">days</option>
                  </select>
                </div>
              </div>
              <div className="h-56">
                {usage.length === 0 ? (
                  <div className="h-full flex items-center justify-center text-sm text-gray-500">No data</div>
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={usage} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="g2" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#111827" stopOpacity={0.25} />
                          <stop offset="100%" stopColor="#111827" stopOpacity={0} />
                        </linearGradient>
                        <linearGradient id="g3" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#6b7280" stopOpacity={0.25} />
                          <stop offset="100%" stopColor="#6b7280" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid stroke="rgba(0,0,0,0.08)" vertical={false} />
                      <XAxis dataKey="day" tick={{ fill: "#6b7280", fontSize: 12 }} />
                      <YAxis
                        tick={{ fill: "#6b7280", fontSize: 12 }}
                        tickFormatter={(val: number) => `${(val / (1024 * 1024)).toFixed(0)} MB`}
                      />
                      <Tooltip
                        formatter={(value: number, name: string) => [
                          fmtBytes(value as number),
                          name,
                        ]}
                      />
                      {/* Separate series so relative magnitudes are clear */}
                      <Area
                        type="monotone"
                        dataKey="tx"
                        name="TX (download)"
                        stroke="#111827"
                        fill="url(#g2)"
                        strokeWidth={2}
                      />
                      <Area
                        type="monotone"
                        dataKey="rx"
                        name="RX (upload)"
                        stroke="#6b7280"
                        fill="url(#g3)"
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
            {/* Actions */}
            <div className="flex items-center justify-between mt-6 pt-4 border-t border-gray-100">
              <div className="text-sm text-gray-500">Actions</div>
              <div className="flex items-center gap-3">
                <button
                  disabled={actionBusy || !peer}
                  onClick={async () => {
                    if (!peer) return;
                    setActionErr("");
                    try {
                      setActionBusy(true);
                      const nextDisabled = !peer.disabled;
                      await patchPeer(peer.id, { disabled: nextDisabled });
                      setPeer({ ...peer, disabled: nextDisabled });
                    } catch (e: any) {
                      setActionErr(e?.message || "Failed to update peer");
                    } finally {
                      setActionBusy(false);
                    }
                  }}
                  className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50"
                >
                  {peer?.disabled ? 'Enable peer' : 'Disable peer'}
                </button>
                <button
                  disabled={actionBusy || !peer}
                  onClick={async () => {
                    if (!peer) return;
                    if (!confirm('Reset all usage metrics for this peer? This cannot be undone.')) return;
                    setActionErr("");
                    try {
                      setActionBusy(true);
                      await resetPeerMetrics(peer.id);
                      // Refresh usage + quota
                      try { const points = await getPeerUsage(peer.id); setUsage(points); } catch {}
                      try { const q = await getPeerQuota(peer.id); setQuota(q); } catch {}
                    } catch (e: any) {
                      setActionErr(e?.message || "Failed to reset metrics");
                    } finally {
                      setActionBusy(false);
                    }
                  }}
                  className="inline-flex items-center gap-2 rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 disabled:opacity-50"
                >
                  Reset metrics
                </button>
                <button
                  disabled={actionBusy || !peer}
                  onClick={() => setConfirmDelete(true)}
                  className="inline-flex items-center gap-2 rounded-full bg-rose-600 text-white px-4 py-2 text-sm shadow hover:bg-rose-700 disabled:opacity-50"
                >
                  Remove peer
                </button>
              </div>
            </div>
            {actionErr && <div className="text-sm text-red-600">{actionErr}</div>}
            <div className="flex justify-end">
              <Link to="/" className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black">Back to dashboard</Link>
            </div>
          </div>
        )}
        {confirmDelete && peer && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
            <div className="w-full max-w-sm rounded-2xl bg-white ring-1 ring-gray-200 shadow-lg p-6 grid gap-4">
              <div className="text-lg font-semibold text-gray-900">Remove peer</div>
              <div className="text-sm text-gray-600">
                This deletes <span className="font-medium text-gray-900">{peer.name}</span> and all stored usage history. This action cannot be undone.
              </div>
              <div className="flex items-center justify-end gap-3">
                <button
                  onClick={() => setConfirmDelete(false)}
                  className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200"
                >
                  Cancel
                </button>
                <button
                  disabled={actionBusy}
                  onClick={async () => {
                    setActionErr("");
                    try {
                      setActionBusy(true);
                      await deletePeer(peer.id);
                      setConfirmDelete(false);
                      navigate("/");
                    } catch (e: any) {
                      setActionErr(e?.message || "Failed to delete peer");
                      setConfirmDelete(false);
                    } finally {
                      setActionBusy(false);
                    }
                  }}
                  className="rounded-full bg-rose-600 text-white px-4 py-2 text-sm shadow hover:bg-rose-700 disabled:opacity-50"
                >
                  Delete peer
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}


