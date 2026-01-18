import React from "react";
import { useAutoSaveSettings } from "../useAutoSaveSettings";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { listSavedPeersSelected, routerPeers, createRouterPeer, listRouters, getActiveRouter, setActiveRouter, routerInterfaceDetail, getMonthlySummary, getSummaryRaw, getPeersSummary, getMetrics, getLastActions, type LastAction, type SavedPeer, type PeerView, type Router, type MonthlySummaryPoint, type SummaryRawPoint, type PeerUsageSummary, type Metrics } from "../api";
import { useNavigate } from "react-router-dom";
import QRCode from "react-qr-code";
import nacl from "tweetnacl";

function Card({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const base = "rounded-3xl overflow-hidden ring-1 ring-gray-200 ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5 dark:ring-gray-800 dark:ring-offset-gray-950 dark:bg-gray-900";
  return <div className={`${base} ${className}`} {...props} />;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const fmtBytes = (n: number) => {
    if (!n || n <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"]; let u = 0; let x = n;
    while (x >= 1024 && u < units.length - 1) { x /= 1024; u++; }
    return `${x.toFixed(x >= 100 ? 0 : x >= 10 ? 1 : 2)} ${units[u]}`;
  };
  const [setupOk, setSetupOk] = React.useState<null | boolean>(null); // null=checking
  const [activeRouterId, setActiveRouterId] = React.useState<number | null>(null);
  const [monthly, setMonthly] = React.useState<MonthlySummaryPoint[]>([]);
  const [raw, setRaw] = React.useState<SummaryRawPoint[]>([]);
  const [peers, setPeers] = React.useState<SavedPeer[]>([]);
  const [peerUsageMap, setPeerUsageMap] = React.useState<Record<number, { rx: number; tx: number }>>({});
  const [statusMap, setStatusMap] = React.useState<Record<number, { online: boolean; last: string; raw_last_handshake: number }>>({});
  type ScopeUnit = "minutes" | "hours" | "days";
  const [showAdd, setShowAdd] = React.useState(false);
  const [addBusy, setAddBusy] = React.useState(false);
  const [addErr, setAddErr] = React.useState("");
  const [form, setForm] = React.useState(() => ({
    interface: "wgmik",
    name: "",
    allowed: "10.65.74.100/32",
    privateKey: "",
    publicKey: "",
    usePsk: false,
    psk: "",
    serverPublicKey: "",
    endpoint: "",
    // Client config extras (for QR/config). Clearing a field omits that line.
    dns: "8.8.8.8, 1.1.1.1",
    mtu: "1280",
    persistentKeepalive: "25",
    allowedIps: "0.0.0.0/0, ::/0",
  }));
  const [metrics, setMetrics] = React.useState<Metrics | null>(null);
  const [timezone, setTimezone] = React.useState<string>("UTC");

  // Settings Hook
  const { settings, update } = useAutoSaveSettings();

  const showKindPills = settings?.show_kind_pills ?? true;
  const showHwStats = settings?.show_hw_stats ?? true;

  // Helpers for safe access to settings (with defaults while loading)
  const refreshSec = settings?.dashboard_refresh_seconds ?? 30;
  const scopeValue = settings?.dashboard_scope_value ?? 14;
  const scopeUnit = (settings?.dashboard_scope_unit as ScopeUnit) ?? "days";
  // User asked for "status filter and sort", not text search. I'll keep text search local.
  const [localFilterText, setLocalFilterText] = React.useState("");

  const filterStatus = (settings?.dashboard_filter_status as "all" | "online" | "offline" | "enabled" | "disabled") ?? "all";
  const sortBy = (settings?.dashboard_sort_by as "name" | "last_seen" | "created" | "usage") ?? "created";

  const filteredPeers = React.useMemo(() => {
    let out = [...peers];
    // Filter by text
    const text = localFilterText.trim().toLowerCase();
    if (text) {
      out = out.filter(p =>
        p.name.toLowerCase().includes(text) ||
        p.public_key.toLowerCase().includes(text) ||
        p.allowed_address.toLowerCase().includes(text)
      );
    }
    if (filterStatus === "online") {
      out = out.filter(p => statusMap[p.id]?.online);
    } else if (filterStatus === "offline") {
      out = out.filter(p => !statusMap[p.id]?.online);
    } else if (filterStatus === "enabled") {
      out = out.filter(p => !p.disabled);
    } else if (filterStatus === "disabled") {
      out = out.filter(p => p.disabled);
    }

    // Sort
    out.sort((a, b) => {
      if (sortBy === "name") return a.name.localeCompare(b.name);
      if (sortBy === "last_seen") {
        const sa = statusMap[a.id]?.raw_last_handshake || 0;
        const sb = statusMap[b.id]?.raw_last_handshake || 0;
        // logic: smaller value > 0 = more recent. 0 = never.
        // sort ascending, but handle 0 as max
        const valA = sa === 0 ? Number.MAX_SAFE_INTEGER : sa;
        const valB = sb === 0 ? Number.MAX_SAFE_INTEGER : sb;
        return valA - valB;
      }
      if (sortBy === "usage") {
        const ua = peerUsageMap[a.id] || { rx: 0, tx: 0 };
        const ub = peerUsageMap[b.id] || { rx: 0, tx: 0 };
        return (ub.rx + ub.tx) - (ua.rx + ua.tx);
      }
      return b.id - a.id; // created (newest ID first)
    });
    return out;
  }, [peers, localFilterText, filterStatus, sortBy, statusMap, peerUsageMap]);

  // Hard gate: if there are no RouterOS profiles, redirect to Setup (Wizard).
  React.useEffect(() => {
    (async () => {
      try {
        const rs = await listRouters();
        if (!rs || rs.length === 0) {
          setSetupOk(false);
          navigate("/setup", { replace: true });
          return;
        }
        const ar = await getActiveRouter();
        let rid = ar?.router_id ?? null;
        if (!rid) {
          // Convenience: if there's only one profile, auto-select it.
          if (rs.length === 1) {
            try {
              const setRes = await setActiveRouter(rs[0].id);
              rid = setRes?.router_id ?? rs[0].id;
            } catch {
              rid = rs[0].id;
            }
          } else {
            setSetupOk(false);
            navigate("/setup", { replace: true });
            return;
          }
        }
        setActiveRouterId(rid);
        setSetupOk(true);
      } catch {
        setSetupOk(false);
        navigate("/setup", { replace: true });
      }
    })();
  }, [navigate]);

  function clampX25519Secret(d: Uint8Array) {
    d[0] &= 248; d[31] &= 127; d[31] |= 64;
    return d;
  }
  function bytesToBase64(bytes: Uint8Array) {
    let bin = "";
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    // btoa expects binary string
    return btoa(bin);
  }
  function base64ToBytes(b64: string) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  function generateKeypair() {
    const priv = clampX25519Secret(crypto.getRandomValues(new Uint8Array(32)));
    const pub = nacl.scalarMult.base(priv);
    setForm(f => ({ ...f, privateKey: bytesToBase64(priv), publicKey: bytesToBase64(pub) }));
  }
  function generatePsk() {
    const p = crypto.getRandomValues(new Uint8Array(32));
    setForm(f => ({ ...f, psk: bytesToBase64(p), usePsk: true }));
  }
  // Auto-derive public key if user pastes a valid base64 32-byte private key
  React.useEffect(() => {
    const pk = (form.privateKey || "").trim();
    if (!pk) {
      if (form.publicKey) setForm(f => ({ ...f, publicKey: "" }));
      return;
    }
    try {
      const priv = base64ToBytes(pk);
      if (priv.length === 32) {
        const pub = nacl.scalarMult.base(priv);
        const pubB64 = bytesToBase64(pub);
        if (form.publicKey !== pubB64) {
          setForm(f => ({ ...f, publicKey: pubB64 }));
        }
      }
    } catch {
      // ignore invalid base64
    }
  }, [form.privateKey]);
  const qrConfig = React.useMemo(() => {
    if (!form.privateKey || !form.allowed) return "";
    const dns = (form.dns || "").trim();
    const mtuNum = (form.mtu || "").trim();
    const keepaliveNum = (form.persistentKeepalive || "").trim();
    const allowedIps = (form.allowedIps || "").trim();
    const lines = [
      "[Interface]",
      `PrivateKey = ${form.privateKey}`,
      `Address = ${form.allowed}`,
      ...(dns ? [`DNS = ${dns}`] : []),
      ...(() => {
        if (!mtuNum) return [];
        const n = Number(mtuNum);
        if (!Number.isFinite(n) || n <= 0) return [];
        return [`MTU = ${Math.floor(n)}`];
      })(),
      "",
      "[Peer]",
      `PublicKey = ${form.serverPublicKey || "SERVER_PUBLIC_KEY"}`,
      ...(form.usePsk && form.psk ? [`PresharedKey = ${form.psk}`] : []),
      `Endpoint = ${form.endpoint || "HOST:PORT"}`,
      ...(allowedIps ? [`AllowedIPs = ${allowedIps}`] : []),
      ...(() => {
        if (!keepaliveNum) return [];
        const n = Number(keepaliveNum);
        if (!Number.isFinite(n) || n <= 0) return [];
        return [`PersistentKeepalive = ${Math.floor(n)}`];
      })(),
    ];
    return lines.join("\n");
  }, [form.privateKey, form.allowed, form.serverPublicKey, form.psk, form.usePsk, form.endpoint, form.dns, form.mtu, form.persistentKeepalive, form.allowedIps]);
  const loadPeers = React.useCallback(async () => {
    try {
      const p = await listSavedPeersSelected(activeRouterId);
      setPeers(p);
    } catch {
      setPeers([]);
    }
  }, [activeRouterId]);

  const loadMonthly = React.useCallback(async () => {
    try {
      const rows = await getMonthlySummary(scopeUnit === "days" ? scopeValue : undefined, activeRouterId);
      setMonthly(rows);
    } catch {
      setMonthly([]);
    }
  }, [scopeUnit, scopeValue, activeRouterId]);

  const loadRaw = React.useCallback(async () => {
    try {
      if (scopeUnit === "days") { setRaw([]); return; }
      const seconds = scopeUnit === "minutes"
        ? Math.max(1, scopeValue) * 60
        : Math.max(1, scopeValue) * 3600;
      const interval = scopeUnit === "minutes" ? 60 : 3600;
      const rows = await getSummaryRaw(seconds, activeRouterId, interval);
      setRaw(rows);
    } catch {
      setRaw([]);
    }
  }, [scopeUnit, scopeValue, activeRouterId]);

  const loadUsageMap = React.useCallback(async () => {
    try {
      const opts: any = { routerId: activeRouterId };
      if (scopeUnit === "days") {
        opts.days = scopeValue;
      } else {
        opts.seconds = scopeUnit === "minutes"
          ? Math.max(1, scopeValue) * 60
          : Math.max(1, scopeValue) * 3600;
      }
      const sums = await getPeersSummary(opts);
      const m: Record<number, { rx: number; tx: number }> = {};
      for (const s of sums) m[s.peer_id] = { rx: s.rx, tx: s.tx };
      setPeerUsageMap(m);
    } catch {
      setPeerUsageMap({});
    }
  }, [scopeUnit, scopeValue, activeRouterId]);

  const loadMetrics = React.useCallback(async () => {
    try {
      const m = await getMetrics();
      setMetrics(m);
    } catch {
      setMetrics(null);
    }
  }, []);

  // Load UI settings is now handled by useAutoSaveSettings, but we need to wait for it before heavy loading?
  // Actually, getSettings is called by the hook. We can just react to 'settings'.
  // However, we need 'metrics', 'showKindPills' etc which were local previously.
  // showKindPills is now derived from settings.

  // We should remove the old manual setting loading useEffect.

  React.useEffect(() => {
    if (setupOk !== true) return;
    if (!settings) return; // Wait for settings to load
    (async () => {
      await loadPeers();
      await loadMonthly();
      await loadRaw();
      await loadUsageMap();
      await loadMetrics();
    })();
  }, [setupOk, loadPeers, loadMonthly, loadRaw, loadMetrics, loadUsageMap, settings]); // Re-run when settings (like scope) change

  // Auto-refresh dashboard data based on configurable interval
  React.useEffect(() => {
    if (setupOk !== true) return;
    if (!refreshSec || refreshSec <= 0) return;
    const id = window.setInterval(() => {
      loadPeers();
      loadMonthly();
      loadRaw();
      loadUsageMap();
      loadMetrics();
    }, refreshSec * 1000);
    return () => window.clearInterval(id);
  }, [setupOk, refreshSec, loadPeers, loadMonthly, loadRaw, loadMetrics, loadUsageMap]);

  // When opening the Add Peer modal, try to auto-fill server public key and endpoint
  React.useEffect(() => {
    if (setupOk !== true) return;
    if (!showAdd) return;
    if (!peers || peers.length === 0) return;
    (async () => {
      try {
        // Prefer a peer on the same interface as the current form, otherwise first peer
        const currentIface = form.interface || peers[0].interface;
        const basePeer = peers.find(p => p.interface === currentIface) || peers[0];
        const [routers, ifaceCfg] = await Promise.all([
          listRouters() as Promise<Router[]>,
          routerInterfaceDetail(basePeer.router_id, basePeer.interface),
        ]);
        const router = routers.find(r => r.id === basePeer.router_id);
        const endpointHost = ifaceCfg.public_host || router?.host || "";
        const endpointPort = ifaceCfg.listen_port || 51820;
        setForm(f => ({
          ...f,
          interface: basePeer.interface,
          serverPublicKey: f.serverPublicKey || ifaceCfg.public_key || "",
          endpoint: f.endpoint || (endpointHost && endpointPort ? `${endpointHost}:${endpointPort}` : f.endpoint),
        }));
      } catch {
        // If router not reachable or interface not found, leave fields as-is for manual entry
      }
    })();
  }, [showAdd, peers, form.interface]);

  // Fetch live status (online/last seen) per interface group
  React.useEffect(() => {
    if (setupOk !== true) return;
    if (!peers || peers.length === 0) { setStatusMap({}); return; }
    (async () => {
      const groups = new Map<string, { routerId: number; iface: string; peers: SavedPeer[] }>();
      for (const p of peers) {
        const key = `${p.router_id}::${p.interface}`;
        const g = groups.get(key) || { routerId: p.router_id, iface: p.interface, peers: [] as SavedPeer[] };
        g.peers.push(p);
        groups.set(key, g);
      }
      const next: Record<number, { online: boolean; last: string; raw_last_handshake: number }> = {};
      // last_handshake is age in seconds (0 = never)
      const formatRel = (ageSec?: number) => {
        if (!ageSec || ageSec <= 0) return "—";
        const m = Math.floor(ageSec / 60);
        const h = Math.floor(m / 60);
        const d = Math.floor(h / 24);
        const mon = Math.floor(d / 30);
        if (ageSec < 60) return `${ageSec}s ago`;
        if (m < 60) return `${m}m ago`;
        if (h < 24) return `${h}h ago`;
        if (d < 30) return `${d}d ago`;
        return `${mon}mo ago`;
      };
      for (const g of groups.values()) {
        try {
          const live: PeerView[] = await routerPeers(g.routerId, g.iface);
          for (const l of live) {
            const saved = g.peers.find(sp => sp.public_key === l.public_key);
            if (saved) {
              next[saved.id] = { online: !!l.online, last: formatRel(l.last_handshake), raw_last_handshake: l.last_handshake || 0 };
            }
          }
        } catch {
          // ignore if router not reachable
        }
      }
      setStatusMap(next);
    })();
  }, [setupOk, peers]);

  if (setupOk === null) {
    // Brief gate while we check if the app has any RouterOS profiles.
    return (
      <div className="mx-auto px-4 md:px-6 py-6">
        <div className="text-sm text-gray-500 dark:text-gray-400">Loading…</div>
      </div>
    );
  }
  return (
    <div className="mx-auto px-4 md:px-6 py-6">
      <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Overview</h1>
      <div className="mx-auto my-12 md:my-16 w-full max-w-[960px] rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 p-6 grid gap-6">
        <div className="p-0">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between mb-2 gap-2">
            <div className="text-sm text-gray-700 dark:text-gray-200">Usage</div>
            <div className="flex flex-wrap items-center gap-3 text-xs text-gray-600 dark:text-gray-300">
              <div className="flex items-center gap-2">
                <span>Auto refresh</span>
                <input
                  type="number"
                  min={5}
                  value={refreshSec}
                  onChange={(e) => update({ dashboard_refresh_seconds: Math.max(5, Number(e.target.value) || 5) })}
                  className="w-16 rounded-full border border-gray-900 bg-gray-900 text-white px-2 py-1 text-xs focus:ring-1 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                />
                <span>s</span>
              </div>
              <div className="flex items-center gap-2">
                <span>Last</span>
                <input
                  type="number"
                  min={1}
                  value={scopeValue}
                  onChange={(e) => update({ dashboard_scope_value: Math.max(1, Number(e.target.value) || 1) })}
                  className="w-16 rounded-full border border-gray-900 bg-gray-900 text-white px-2 py-1 text-xs focus:ring-1 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                />
                <select
                  value={scopeUnit}
                  onChange={(e) => update({ dashboard_scope_unit: e.target.value })}
                  className="rounded-full border border-gray-200 dark:border-gray-800 px-2 py-1 text-xs focus:ring-1 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950"
                >
                  <option value="minutes">minutes</option>
                  <option value="hours">hours</option>
                  <option value="days">days</option>
                </select>
              </div>
            </div>
          </div>
          <div className="h-56">
            {(scopeUnit === "days" ? monthly.length === 0 : raw.length === 0) ? (
              <div className="h-full flex items-center justify-center text-sm text-gray-500 dark:text-gray-400">No usage data yet</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={(scopeUnit === "days"
                    ? monthly.map((d) => ({
                      // backend day is UTC YYYY-MM-DD
                      x: d.day,
                      rx: d.rx / (1024 * 1024),
                      tx: d.tx / (1024 * 1024),
                    }))
                    : raw.map((p) => ({
                      x: p.ts,
                      rx: p.rx / (1024 * 1024),
                      tx: p.tx / (1024 * 1024),
                    }))
                  )}
                  margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="dashGibT" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--chart-fill-1)" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="var(--chart-fill-1)" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="dashGibR" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--chart-fill-2)" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="var(--chart-fill-2)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                  <XAxis
                    dataKey="x"
                    tick={{ fill: "var(--chart-tick)", fontSize: 12 }}
                    tickFormatter={(val: string) => {
                      try {
                        if (scopeUnit === "days") {
                          const d = new Date(`${val}T00:00:00Z`);
                          return new Intl.DateTimeFormat(undefined, {
                            timeZone: settings?.timezone || "UTC",
                            month: "numeric",
                            day: "numeric",
                          }).format(d);
                        }

                        const d = new Date(val);
                        return new Intl.DateTimeFormat(undefined, {
                          timeZone: settings?.timezone || "UTC",
                          hour: "2-digit",
                          minute: "2-digit",
                        }).format(d);
                      } catch {
                        return val;
                      }
                    }}
                  />
                  <YAxis
                    tick={{ fill: "var(--chart-tick)", fontSize: 12 }}
                    tickFormatter={(val: number) => `${Math.round(val)} MB`}
                  />
                  <Tooltip
                    formatter={(value: number, name: string) => [
                      `${(value as number).toFixed(1)} MB`,
                      name === "rx" ? "RX" : "TX",
                    ]}
                    labelFormatter={(label) => {
                      try {
                        if (scopeUnit === "days") {
                          const d = new Date(`${label}T00:00:00Z`);
                          return new Intl.DateTimeFormat(undefined, {
                            timeZone: settings?.timezone || "UTC",
                            dateStyle: "full",
                          }).format(d);
                        }
                        const d = new Date(label);
                        return new Intl.DateTimeFormat(undefined, {
                          timeZone: settings?.timezone || "UTC",
                          dateStyle: "medium",
                          timeStyle: "medium",
                        }).format(d);
                      } catch {
                        return label;
                      }
                    }}
                    contentStyle={{
                      background: "var(--chart-tooltip-bg)",
                      border: "1px solid var(--chart-tooltip-border)",
                      color: "var(--chart-tooltip-text)",
                      borderRadius: 12,
                    }}
                    labelStyle={{ color: "var(--chart-tooltip-text)" }}
                  />
                  <Area
                    type="monotone"
                    dataKey="tx"
                    name="tx"
                    stroke="var(--chart-line-1)"
                    fill="url(#dashGibT)"
                    strokeWidth={2}
                  />
                  <Area
                    type="monotone"
                    dataKey="rx"
                    name="rx"
                    stroke="var(--chart-line-2)"
                    fill="url(#dashGibR)"
                    strokeWidth={2}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="flex items-center justify-center gap-6 mt-2 text-xs text-gray-500 dark:text-gray-400">
            {(() => {
              const src = scopeUnit === "days" ? monthly : raw;
              const totRx = src.reduce((a, b) => a + (b.rx || 0), 0);
              const totTx = src.reduce((a, b) => a + (b.tx || 0), 0);
              return (
                <>
                  <div>Total Download: <span className="font-medium text-gray-700 dark:text-gray-300">{fmtBytes(totTx)}</span></div>
                  <div>Total Upload: <span className="font-medium text-gray-700 dark:text-gray-300">{fmtBytes(totRx)}</span></div>
                </>
              );
            })()}
          </div>
        </div>

        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex flex-col sm:flex-row gap-3 w-full md:w-auto">
            <input
              className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700 w-full sm:w-64"
              placeholder="Search peers..."
              value={localFilterText}
              onChange={e => setLocalFilterText(e.target.value)}
            />
            <select
              className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
              value={filterStatus}
              onChange={e => update({ dashboard_filter_status: e.target.value })}
            >
              <option value="all">All Status</option>
              <option value="online">Online</option>
              <option value="offline">Offline</option>
              <option value="enabled">Active</option>
              <option value="disabled">Deactivated</option>
            </select>
            <select
              className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
              value={sortBy}
              onChange={e => update({ dashboard_sort_by: e.target.value })}
            >
              <option value="created">Recently Added</option>
              <option value="last_seen">Last Seen</option>
              <option value="usage">Total Usage</option>
              <option value="name">Name</option>
            </select>
          </div>
          <button
            onClick={() => { setAddErr(""); setShowAdd(true); }}
            className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white whitespace-nowrap"
          >
            Add peer +
          </button>
        </div>
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {filteredPeers.map((p) => (
            <Card key={p.id} className="p-4 ring-gray-300 shadow hover:shadow-lg hover:-translate-y-0.5 cursor-pointer rounded-xl" onClick={() => navigate(`/peer/${p.id}`)}>
              <div className="flex items-center justify-between">
                <div className="min-w-0">
                  <div className="text-sm text-gray-500 dark:text-gray-400">{p.name}</div>
                  {(() => {
                    const full = (p.allowed_address || "").replace(/\/32/g, "");
                    const maxChars = 20;
                    const shown = full.length > maxChars ? `${full.slice(0, maxChars)}…` : full;
                    return (
                      <div
                        className="text-lg text-gray-900 dark:text-gray-100 truncate"
                        title={full}
                      >
                        {shown}
                      </div>
                    );
                  })()}
                </div>
                <div className="flex flex-col items-end gap-2">
                  <div className="text-xs text-gray-500 dark:text-gray-400">
                    {(() => {
                      const u = peerUsageMap[p.id];
                      if (!u) return null;
                      return (
                        <span title="Usage in selected timeframe">
                          ↓ {fmtBytes(u.tx)} · ↑ {fmtBytes(u.rx)}
                        </span>
                      );
                    })()}
                  </div>
                  {/* Allowance (enabled/disabled) */}
                  <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${p.disabled ? 'bg-rose-100 text-rose-800' : 'bg-indigo-100 text-indigo-800'}`}>
                    <span className={`inline-block w-2 h-2 rounded-full ${p.disabled ? 'bg-rose-500' : 'bg-indigo-500'}`} />
                    {p.disabled ? 'Deactivated' : 'Active'}
                  </span>
                  {showKindPills && (() => {
                    const addr = p.allowed_address.trim(); const outbound = addr === "0.0.0.0/0" || addr === "::/0"; return (
                      <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${outbound ? 'bg-amber-100 text-amber-800' : 'bg-blue-100 text-blue-800'}`}>
                        <span className={`inline-block w-2 h-2 rounded-full ${outbound ? 'bg-amber-500' : 'bg-blue-500'}`} />
                        {outbound ? 'Outbound' : 'Inbound'}
                      </span>
                    );
                  })()}
                  {(() => {
                    const st = statusMap[p.id]; if (!st) return null; return (
                      <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${st.online ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200'}`}>
                        <span className={`dot ${st.online ? 'bg-green-500 pulse' : 'bg-gray-400 dark:bg-gray-500'}`} />
                        {st.online ? 'Online' : `Last seen ${st.last}`}
                      </span>
                    );
                  })()}
                </div>
              </div>
            </Card>
          ))}
        </div>
        <div className="fixed left-4 bottom-4 flex flex-col items-start gap-2">
          <a href="/settings" className="h-12 w-12 rounded-full bg-white text-gray-900 ring-1 ring-gray-300 shadow flex items-center justify-center hover:shadow-md dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-700">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c0 .66.26 1.3.73 1.77.47.47 1.11.73 1.77.73H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </a>
          {showHwStats && (
            <div className="rounded-full bg-white/95 text-[11px] text-gray-800 px-3 py-1 shadow ring-1 ring-gray-200 dark:bg-gray-900/95 dark:text-gray-200 dark:ring-gray-800">
              {metrics
                ? `CPU ${metrics.cpu_percent != null ? Math.round(metrics.cpu_percent) : "–"}% · Mem ${metrics.mem_percent != null ? Math.round(metrics.mem_percent) : "–"
                }%`
                : "CPU/Mem: …"}
            </div>
          )}
        </div>
        {/* Add Peer Modal */}
        {showAdd && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 p-4">
            <div className="w-full max-w-[960px] max-h-[90vh] overflow-y-auto bg-white dark:bg-gray-900 rounded-3xl ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg grid md:grid-cols-2 gap-6 p-6 relative">
              <button
                onClick={() => setShowAdd(false)}
                className="absolute top-3 right-3 h-8 w-8 rounded-full bg-gray-100 text-gray-800 flex items-center justify-center hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                title="Close"
              >
                ✕
              </button>
              <div className="grid gap-4">
                <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">Add peer</div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Name</label>
                  <input
                    value={form.name}
                    onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="alice"
                  />
                </div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Interface</label>
                  <input
                    value={form.interface}
                    onChange={e => setForm(f => ({ ...f, interface: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="wgmik"
                  />
                </div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Allowed address (inbound)</label>
                  <input
                    value={form.allowed}
                    onChange={e => setForm(f => ({ ...f, allowed: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="10.65.74.100/32"
                  />
                </div>
                <div className="grid gap-2">
                  <div className="flex items-center justify-between">
                    <label className="text-xs text-gray-500 dark:text-gray-400">Keys</label>
                    <button onClick={generateKeypair} className="text-xs rounded-full bg-gray-900 text-white px-3 py-1 shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white">Generate</button>
                  </div>
                  <label className="text-xs text-gray-500 dark:text-gray-400">Private key (base64)</label>
                  <input
                    value={form.privateKey}
                    onChange={e => setForm(f => ({ ...f, privateKey: e.target.value, publicKey: "" }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="PrivateKey (base64)"
                  />
                  <label className="text-xs text-gray-500 dark:text-gray-400">Public key (auto)</label>
                  <input
                    readOnly
                    value={form.publicKey}
                    onFocus={(e) => e.currentTarget.select()}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm bg-gray-50 text-gray-900 font-mono placeholder:text-gray-400 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:placeholder:text-gray-500"
                    placeholder="PublicKey (auto from PrivateKey)"
                  />
                </div>
                <div className="grid gap-2">
                  <div className="flex items-center justify-between">
                    <label className="text-xs text-gray-500 dark:text-gray-400">Preshared key (optional)</label>
                    <button onClick={generatePsk} className="text-xs rounded-full bg-gray-100 text-gray-800 px-3 py-1 shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">Generate</button>
                  </div>
                  <input
                    value={form.psk}
                    onChange={e => setForm(f => ({ ...f, psk: e.target.value, usePsk: !!e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="PresharedKey (base64)"
                  />
                </div>
                <div className="flex items-center gap-3 pt-2">
                  <button
                    disabled={addBusy || !form.name || !form.publicKey || !form.allowed || form.allowed.trim() === "0.0.0.0/0" || form.allowed.trim() === "::/0"}
                    onClick={async () => {
                      setAddErr("");
                      try {
                        setAddBusy(true);
                        // Prefer the router already backing the dashboard peers; otherwise fall back to first router profile.
                        let routerId = activeRouterId;
                        if (!routerId) throw new Error("No active router. Go to Setup and select a profile.");

                        const saved = await createRouterPeer(routerId, {
                          interface: form.interface || "wgmik",
                          name: form.name.trim(),
                          public_key: form.publicKey,
                          allowed_address: form.allowed.trim(),
                          comment: "",
                        });
                        setPeers(prev => [saved, ...prev]);
                        setShowAdd(false);
                      } catch (e: any) {
                        setAddErr(e?.message || "Failed to add peer");
                      } finally {
                        setAddBusy(false);
                      }
                    }}
                    className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setShowAdd(false)}
                    className="inline-flex items-center gap-2 rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                  >
                    Cancel
                  </button>
                </div>
                {addErr && <div className="text-sm text-red-600">{addErr}</div>}
                <div className="text-xs text-gray-500 dark:text-gray-400">Note: Save creates the peer on RouterOS and stores it in the DB. Private key stays client-side.</div>
              </div>
              <div className="grid gap-3">
                <div className="text-sm text-gray-700 dark:text-gray-200">Client config (QR)</div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Server public key (for QR)</label>
                  <input
                    value={form.serverPublicKey}
                    onChange={e => setForm(f => ({ ...f, serverPublicKey: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="Base64"
                  />
                </div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Endpoint (for QR)</label>
                  <input
                    value={form.endpoint}
                    onChange={e => setForm(f => ({ ...f, endpoint: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="host:port"
                  />
                </div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500 dark:text-gray-400">DNS</label>
                  <input
                    value={form.dns}
                    onChange={e => setForm(f => ({ ...f, dns: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="8.8.8.8, 1.1.1.1"
                  />
                  <div className="text-[11px] text-gray-500 dark:text-gray-400">Empty = omit from config.</div>
                </div>
                <div className="grid md:grid-cols-2 gap-3">
                  <div className="grid gap-2">
                    <label className="text-xs text-gray-500 dark:text-gray-400">MTU</label>
                    <input
                      type="number"
                      inputMode="numeric"
                      value={form.mtu}
                      onChange={e => setForm(f => ({ ...f, mtu: e.target.value }))}
                      className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                      placeholder="1280"
                    />
                    <div className="text-[11px] text-gray-500 dark:text-gray-400">Empty = omit.</div>
                  </div>
                  <div className="grid gap-2">
                    <label className="text-xs text-gray-500 dark:text-gray-400">Persistent keepalive (s)</label>
                    <input
                      type="number"
                      inputMode="numeric"
                      value={form.persistentKeepalive}
                      onChange={e => setForm(f => ({ ...f, persistentKeepalive: e.target.value }))}
                      className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                      placeholder="25"
                    />
                    <div className="text-[11px] text-gray-500 dark:text-gray-400">Empty = omit.</div>
                  </div>
                </div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500 dark:text-gray-400">AllowedIPs</label>
                  <input
                    value={form.allowedIps}
                    onChange={e => setForm(f => ({ ...f, allowedIps: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="0.0.0.0/0, ::/0"
                  />
                  <div className="text-[11px] text-gray-500 dark:text-gray-400">Empty = omit from config.</div>
                </div>
                <div className="rounded-xl ring-1 ring-gray-200 dark:ring-gray-800 p-3 bg-gray-50 dark:bg-gray-950 text-xs text-gray-700 dark:text-gray-200 whitespace-pre-wrap break-all">
                  {qrConfig || "Fill private key and address to see config"}
                </div>
                <div className="flex items-center justify-center p-4 bg-white dark:bg-gray-950 rounded-xl ring-1 ring-gray-200 dark:ring-gray-800">
                  {qrConfig ? <QRCode value={qrConfig} size={176} /> : <div className="text-xs text-gray-500 dark:text-gray-400">QR will appear here</div>}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div >
  );
}


