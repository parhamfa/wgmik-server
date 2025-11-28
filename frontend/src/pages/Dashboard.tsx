import React from "react";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { listSavedPeersSelected, routerPeers, createDemoPeer, listRouters, routerInterfaceDetail, getMonthlySummary, getMetrics, getSettings, type SavedPeer, type PeerView, type Router, type MonthlySummaryPoint, type Metrics } from "../api";
import { useNavigate } from "react-router-dom";
import QRCode from "react-qr-code";
import nacl from "tweetnacl";

function Card({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const base = "rounded-3xl overflow-hidden ring-1 ring-gray-200 ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5";
  return <div className={`${base} ${className}`} {...props} />;
}

export default function Dashboard() {
  const navigate = useNavigate();
  const [monthly, setMonthly] = React.useState<MonthlySummaryPoint[]>([]);
  const [peers, setPeers] = React.useState<SavedPeer[]>([]);
  const [statusMap, setStatusMap] = React.useState<Record<number, { online: boolean; last: string }>>({});
  const [refreshSec, setRefreshSec] = React.useState<number>(30);
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
  }));
  const [metrics, setMetrics] = React.useState<Metrics | null>(null);
  const [showKindPills, setShowKindPills] = React.useState(true);
  const [showHwStats, setShowHwStats] = React.useState(true);

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
    const lines = [
      "[Interface]",
      `PrivateKey = ${form.privateKey}`,
      `Address = ${form.allowed}`,
      `DNS = 1.1.1.1`,
      "",
      "[Peer]",
      `PublicKey = ${form.serverPublicKey || "SERVER_PUBLIC_KEY"}`,
      ...(form.usePsk && form.psk ? [`PresharedKey = ${form.psk}`] : []),
      `Endpoint = ${form.endpoint || "HOST:PORT"}`,
      `AllowedIPs = 0.0.0.0/0, ::/0`,
      `PersistentKeepalive = 25`,
    ];
    return lines.join("\n");
  }, [form.privateKey, form.allowed, form.serverPublicKey, form.psk, form.usePsk, form.endpoint]);
  const loadPeers = React.useCallback(async () => {
    try {
      const p = await listSavedPeersSelected();
      setPeers(p);
    } catch {
      setPeers([]);
    }
  }, []);

  const loadMonthly = React.useCallback(async () => {
    try {
      const rows = await getMonthlySummary();
      setMonthly(rows);
    } catch {
      setMonthly([]);
    }
  }, []);

  const loadMetrics = React.useCallback(async () => {
    try {
      const m = await getMetrics();
      setMetrics(m);
    } catch {
      setMetrics(null);
    }
  }, []);

  // Load UI settings for dashboard defaults
  React.useEffect(() => {
    (async () => {
      try {
        const s = await getSettings();
        if (typeof s.dashboard_refresh_seconds === "number" && s.dashboard_refresh_seconds > 0) {
          setRefreshSec(s.dashboard_refresh_seconds);
        }
        if (typeof s.show_kind_pills === "boolean") {
          setShowKindPills(s.show_kind_pills);
        }
        if (typeof s.show_hw_stats === "boolean") {
          setShowHwStats(s.show_hw_stats);
        }
      } catch {
        // ignore, keep defaults
      }
    })();
  }, []);

  React.useEffect(() => {
    (async () => {
      await loadPeers();
      await loadMonthly();
      await loadMetrics();
    })();
  }, [loadPeers, loadMonthly, loadMetrics]);

  // Auto-refresh dashboard data based on configurable interval
  React.useEffect(() => {
    if (!refreshSec || refreshSec <= 0) return;
    const id = window.setInterval(() => {
      loadPeers();
      loadMonthly();
      loadMetrics();
    }, refreshSec * 1000);
    return () => window.clearInterval(id);
  }, [refreshSec, loadPeers, loadMonthly, loadMetrics]);

  // When opening the Add Peer modal, try to auto-fill server public key and endpoint
  React.useEffect(() => {
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
    if (!peers || peers.length === 0) { setStatusMap({}); return; }
    (async () => {
      const groups = new Map<string, { routerId: number; iface: string; peers: SavedPeer[] }>();
      for (const p of peers) {
        const key = `${p.router_id}::${p.interface}`;
        const g = groups.get(key) || { routerId: p.router_id, iface: p.interface, peers: [] as SavedPeer[] };
        g.peers.push(p);
        groups.set(key, g);
      }
      const next: Record<number, { online: boolean; last: string }> = {};
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
              next[saved.id] = { online: !!l.online, last: formatRel(l.last_handshake) };
            }
          }
        } catch {
          // ignore if router not reachable
        }
      }
      // Fallback demo statuses for any peers missing live data
      for (const p of peers) {
        if (!next[p.id]) {
          const mode = p.id % 3;
          const online = mode === 0;
          const last = online ? "—" : mode === 1 ? "3m ago" : "12m ago";
          next[p.id] = { online, last };
        }
      }
      setStatusMap(next);
    })();
  }, [peers]);
  return (
    <div className="mx-auto px-4 md:px-6 py-6">
      <h1 className="text-xl font-semibold text-gray-900 mb-4">Overview</h1>
      <div className="mx-auto my-12 md:my-16 w-full max-w-[960px] rounded-3xl ring-1 ring-gray-200 bg-white p-6 grid gap-6">
        <div className="p-0">
          <div className="flex items-center justify-between mb-2">
            <div className="text-sm text-gray-700">Monthly usage</div>
            <div className="flex items-center gap-2 text-xs text-gray-600">
              <span>Auto refresh</span>
              <input
                type="number"
                min={5}
                value={refreshSec}
                onChange={(e) => setRefreshSec(Math.max(5, Number(e.target.value) || 5))}
                className="w-16 rounded-full border border-gray-200 px-2 py-1 text-xs focus:ring-1 focus:ring-gray-300"
              />
              <span>s</span>
            </div>
          </div>
          <div className="h-56">
            {monthly.length === 0 ? (
              <div className="h-full flex items-center justify-center text-sm text-gray-500">No usage data yet</div>
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={monthly.map((d) => ({
                    day: d.day[8] === "0" ? d.day.slice(9) : d.day.slice(8), // show DD
                    usage: (d.rx + d.tx) / (1024 * 1024),
                  }))}
                  margin={{ top: 10, right: 10, left: 0, bottom: 0 }}
                >
                  <defs>
                    <linearGradient id="g" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#111827" stopOpacity={0.25} />
                      <stop offset="100%" stopColor="#111827" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(0,0,0,0.08)" vertical={false} />
                  <XAxis dataKey="day" tick={{ fill: "#6b7280", fontSize: 12 }} />
                  <YAxis
                    tick={{ fill: "#6b7280", fontSize: 12 }}
                    tickFormatter={(val: number) => `${Math.round(val)} MB`}
                  />
                  <Tooltip formatter={(value: number) => [`${(value as number).toFixed(1)} MB`, "Usage"]} />
                  <Area type="monotone" dataKey="usage" stroke="#111827" fill="url(#g)" strokeWidth={2} />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
        <div className="flex items-center justify-between">
          <div className="text-sm text-gray-700">Peers</div>
          <button
            onClick={() => { setAddErr(""); setShowAdd(true); }}
            className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black"
          >
            Add peer +
          </button>
        </div>
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {peers.map((p) => (
            <Card key={p.id} className="p-4 ring-gray-300 shadow hover:shadow-lg hover:-translate-y-0.5 cursor-pointer rounded-xl" onClick={() => navigate(`/peer/${p.id}`)}>
              <div className="flex items-center justify-between">
                <div>
                  <div className="text-sm text-gray-500">{p.name}</div>
                  <div className="text-lg text-gray-900">{p.allowed_address.replace("/32", "")}</div>
                </div>
                <div className="flex flex-col items-end gap-2">
                  {/* Allowance (enabled/disabled) */}
                  <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${p.disabled ? 'bg-rose-100 text-rose-800' : 'bg-indigo-100 text-indigo-800'}`}>
                    <span className={`inline-block w-2 h-2 rounded-full ${p.disabled ? 'bg-rose-500' : 'bg-indigo-500'}`} />
                    {p.disabled ? 'Deactivated' : 'Active'}
                  </span>
                  {showKindPills && (() => { const addr = p.allowed_address.trim(); const outbound = addr === "0.0.0.0/0" || addr === "::/0"; return (
                    <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${outbound ? 'bg-amber-100 text-amber-800' : 'bg-blue-100 text-blue-800'}`}>
                      <span className={`inline-block w-2 h-2 rounded-full ${outbound ? 'bg-amber-500' : 'bg-blue-500'}`} />
                      {outbound ? 'Outbound' : 'Inbound'}
                    </span>
                  ); })()}
                  {(() => { const st = statusMap[p.id]; if (!st) return null; return (
                    <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${st.online ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-800'}`}>
                      <span className={`dot ${st.online ? 'bg-green-500 pulse' : 'bg-gray-400'}`} />
                      {st.online ? 'Online' : `Last seen ${st.last}`}
                    </span>
                  ); })()}
                </div>
              </div>
            </Card>
          ))}
        </div>
        <div className="fixed left-4 bottom-4 flex flex-col items-start gap-2">
          <a href="/settings" className="h-12 w-12 rounded-full bg-white text-gray-900 ring-1 ring-gray-300 shadow flex items-center justify-center hover:shadow-md">
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c0 .66.26 1.3.73 1.77.47.47 1.11.73 1.77.73H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </a>
          {showHwStats && (
            <div className="rounded-full bg-white/95 text-[11px] text-gray-800 px-3 py-1 shadow ring-1 ring-gray-200">
              {metrics
                ? `CPU ${metrics.cpu_percent != null ? Math.round(metrics.cpu_percent) : "–"}% · Mem ${
                    metrics.mem_percent != null ? Math.round(metrics.mem_percent) : "–"
                  }%`
                : "CPU/Mem: …"}
            </div>
          )}
        </div>
        {/* Add Peer Modal */}
        {showAdd && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 p-4">
            <div className="w-full max-w-[960px] max-h-[90vh] overflow-y-auto bg-white rounded-3xl ring-1 ring-gray-200 shadow-lg grid md:grid-cols-2 gap-6 p-6 relative">
              <button
                onClick={() => setShowAdd(false)}
                className="absolute top-3 right-3 h-8 w-8 rounded-full bg-gray-100 text-gray-800 flex items-center justify-center hover:bg-gray-200"
                title="Close"
              >
                ✕
              </button>
              <div className="grid gap-4">
                <div className="text-lg font-semibold text-gray-900">Add peer</div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500">Name</label>
                  <input
                    value={form.name}
                    onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
                    placeholder="alice"
                  />
                </div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500">Interface</label>
                  <input
                    value={form.interface}
                    onChange={e => setForm(f => ({ ...f, interface: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
                    placeholder="wgmik"
                  />
                </div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500">Allowed address (inbound)</label>
                  <input
                    value={form.allowed}
                    onChange={e => setForm(f => ({ ...f, allowed: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
                    placeholder="10.65.74.100/32"
                  />
                </div>
                <div className="grid gap-2">
                  <div className="flex items-center justify-between">
                    <label className="text-xs text-gray-500">Keys</label>
                    <button onClick={generateKeypair} className="text-xs rounded-full bg-gray-900 text-white px-3 py-1 shadow hover:bg-black">Generate</button>
                  </div>
                  <label className="text-xs text-gray-500">Private key (base64)</label>
                  <input
                    value={form.privateKey}
                    onChange={e => setForm(f => ({ ...f, privateKey: e.target.value, publicKey: "" }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
                    placeholder="PrivateKey (base64)"
                  />
                  <label className="text-xs text-gray-500">Public key (auto)</label>
                  <input
                    readOnly
                    value={form.publicKey}
                    onFocus={(e) => e.currentTarget.select()}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm bg-gray-50 text-gray-900 font-mono placeholder:text-gray-400"
                    placeholder="PublicKey (auto from PrivateKey)"
                  />
                </div>
                <div className="grid gap-2">
                  <div className="flex items-center justify-between">
                    <label className="text-xs text-gray-500">Preshared key (optional)</label>
                    <button onClick={generatePsk} className="text-xs rounded-full bg-gray-100 text-gray-800 px-3 py-1 shadow hover:bg-gray-200">Generate</button>
                  </div>
                  <input
                    value={form.psk}
                    onChange={e => setForm(f => ({ ...f, psk: e.target.value, usePsk: !!e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
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
                        const saved = await createDemoPeer({
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
                    className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setShowAdd(false)}
                    className="inline-flex items-center gap-2 rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200"
                  >
                    Cancel
                  </button>
                </div>
                {addErr && <div className="text-sm text-red-600">{addErr}</div>}
                <div className="text-xs text-gray-500">Note: Save stores an inbound peer in demo DB. QR is for client convenience.</div>
              </div>
              <div className="grid gap-3">
                <div className="text-sm text-gray-700">Client config (QR)</div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500">Server public key (for QR)</label>
                  <input
                    value={form.serverPublicKey}
                    onChange={e => setForm(f => ({ ...f, serverPublicKey: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
                    placeholder="Base64"
                  />
                </div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500">Endpoint (for QR)</label>
                  <input
                    value={form.endpoint}
                    onChange={e => setForm(f => ({ ...f, endpoint: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300"
                    placeholder="host:port"
                  />
                </div>
                <div className="rounded-xl ring-1 ring-gray-200 p-3 bg-gray-50 text-xs text-gray-700 whitespace-pre-wrap break-all">
                  {qrConfig || "Fill private key and address to see config"}
                </div>
                <div className="flex items-center justify-center p-4 bg-white rounded-xl ring-1 ring-gray-200">
                  {qrConfig ? <QRCode value={qrConfig} size={176} /> : <div className="text-xs text-gray-500">QR will appear here</div>}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}


