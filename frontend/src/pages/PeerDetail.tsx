import React from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { listSavedPeers, getPeerUsage, listRouters, routerPeers, patchPeer, getPeerQuota, patchPeerQuota, resetPeerMetrics, deletePeer, reconcilePeer, getPeerActions, type PeerAction, getSettings, type SavedPeer, type UsagePoint, type Router, type PeerView, type Quota } from "../api";
import { useAutoSaveSettings, type ScopeUnit } from "../useAutoSaveSettings";

function Card({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const base = "rounded-3xl overflow-hidden ring-1 ring-gray-200 ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5 dark:ring-gray-800 dark:ring-offset-gray-950 dark:bg-gray-900";
  return <div className={`${base} ${className} `} {...props} />;
}

function LockedField({ value, mono, className = "" }: { value: string; mono?: boolean; className?: string }) {
  return (
    <div
      className={[
        "rounded-xl border border-dashed border-gray-300 dark:border-gray-700",
        "bg-gray-50 dark:bg-gray-950",
        "px-3 py-2",
        mono ? "font-mono text-xs" : "text-sm",
        "text-gray-900 dark:text-gray-100",
        "break-words",
        className,
      ].join(" ")}
      title={value}
    >
      {value || "—"}
    </div>
  );
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
  const [alsoRemoveRouter, setAlsoRemoveRouter] = React.useState(true);
  const [actions, setActions] = React.useState<PeerAction[]>([]);
  const [actionsErr, setActionsErr] = React.useState("");
  const [actionsLimit, setActionsLimit] = React.useState<number>(3);
  const [actionsHasMore, setActionsHasMore] = React.useState<boolean>(false);
  const [quota, setQuota] = React.useState<Quota | null>(null);
  const [quotaErr, setQuotaErr] = React.useState("");
  const [quotaSaveState, setQuotaSaveState] = React.useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const quotaSaveTimerRef = React.useRef<number | null>(null);
  const quotaSavingRef = React.useRef(false);
  const quotaPendingRef = React.useRef(false);
  const quotaDraftInitRef = React.useRef(false);
  const userEditedRef = React.useRef(false);

  const [quotaDraft, setQuotaDraft] = React.useState<{ limitGb: number; valid_from: string; valid_until: string }>({
    limitGb: 0,
    valid_from: "",
    valid_until: "",
  });
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const windowProgress = React.useMemo(() => {
    if (!quotaDraft.valid_from && !quotaDraft.valid_until) return null;
    const startMs = quotaDraft.valid_from ? new Date(quotaDraft.valid_from).getTime() : NaN;
    const endMs = quotaDraft.valid_until ? new Date(quotaDraft.valid_until).getTime() : NaN;
    if (!isFinite(startMs) || !isFinite(endMs) || endMs <= startMs) return null;
    const nowMs = Date.now();
    // Remaining fraction: 1 at start, 0 at end
    const ratio = (endMs - nowMs) / (endMs - startMs);
    return Math.max(0, Math.min(1, ratio));
  }, [quotaDraft.valid_from, quotaDraft.valid_until]);

  const fmtBytes = (n: number) => {
    if (!n || n <= 0) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"]; let u = 0; let x = n;
    while (x >= 1024 && u < units.length - 1) { x /= 1024; u++; }
    return `${x.toFixed(x >= 100 ? 0 : x >= 10 ? 1 : 2)} ${units[u]} `;
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
        } catch { }
      } catch { setPeer(null); }
    })();
  }, [peerId]);

  const refreshPeer = React.useCallback(async () => {
    const peers = await listSavedPeers();
    const p = peers.find(x => x.id === peerId) || null;
    setPeer(p);
  }, [peerId]);
  // Settings Hook
  const { settings, update } = useAutoSaveSettings();

  // Helpers
  const refreshSec = settings?.peer_refresh_seconds ?? 30;
  const scopeValue = settings?.peer_default_scope_value ?? 14;
  const scopeUnit = (settings?.peer_default_scope_unit as ScopeUnit) ?? "days";
  const timezone = settings?.timezone ?? "UTC";
  const showKindPills = settings?.show_kind_pills ?? true;

  const fetchActions = React.useCallback(async (limit: number) => {
    const lim = Math.max(1, Math.min(200, limit || 25));
    try {
      // Fetch one extra to detect "has more"
      const rows = await getPeerActions(peerId, Math.min(200, lim + 1));
      setActions(rows.slice(0, lim));
      setActionsHasMore(rows.length > lim);
      setActionsErr("");
    } catch (e: any) {
      setActionsErr(e?.message || "Failed to load log");
    }
  }, [peerId]);

  const loadQuota = React.useCallback(async () => {
    try {
      const q = await getPeerQuota(peerId);
      setQuota(q);
    } catch {
      // keep last quota on transient errors
    }
  }, [peerId]);

  const loadPeer = React.useCallback(async () => {
    const peers = await listSavedPeers();
    const p = peers.find((x) => x.id === peerId) || null;
    setPeer(p);
  }, [peerId]);

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
        const interval = scopeUnit === "minutes" ? 60 : 3600;
        const points = await getPeerUsage(peerId, { window: "raw", seconds, interval });
        setUsage(points);
      }
    } catch {
      setUsage([]);
    }
  }, [peerId, scopeUnit, scopeValue]);




  const refreshAll = React.useCallback(async () => {
    try {
      await loadPeer();
      await loadQuota();
      await fetchActions(actionsLimit);
      if (!peer) return;
      try {
        const live: PeerView[] = await routerPeers(peer.router_id, peer.interface);
        const me = live.find((x) => x.public_key === peer.public_key);
        if (me) {
          setLiveEndpoint(me.endpoint || "—");
          setLiveOnline(!!me.online);
          if (me.last_handshake) {
            const ageSec = me.last_handshake;
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
        setLiveOnline(null);
        setLiveEndpoint("—");
        setLastSeenLabel("—");
      }
    } catch {
      // ignore
    }
  }, [peerId, actionsLimit, fetchActions, loadPeer, loadQuota, loadUsage, peer]);


  React.useEffect(() => {
    loadQuota();
    loadUsage();
  }, [loadQuota, loadUsage]);

  // Auto-refresh peer usage at roughly the poll interval (default 30s)
  React.useEffect(() => {
    const intervalSec = Math.max(5, refreshSec || 30);
    const id = window.setInterval(() => {
      loadPeer();
      loadQuota();
      loadUsage();
    }, refreshSec * 1000);
    return () => window.clearInterval(id);
  }, [refreshSec, loadPeer, loadQuota, loadUsage]);

  const serverQuotaDraft = React.useMemo(() => {
    if (!quota) return null;
    return {
      limitGb: quota.monthly_limit_bytes ? Number(((quota.monthly_limit_bytes || 0) / (1024 * 1024 * 1024)).toFixed(2)) : 0,
      valid_from: (quota.valid_from as any) || "",
      valid_until: (quota.valid_until as any) || "",
    };
  }, [quota?.monthly_limit_bytes, quota?.valid_from, quota?.valid_until]);

  // Keep draft in sync with server quota when not actively editing (i.e., no unsaved changes).
  const isQuotaDirty = React.useMemo(() => {
    const last = serverQuotaDraft;
    if (!last) return false;
    return (
      quotaDraft.limitGb !== last.limitGb ||
      quotaDraft.valid_from !== last.valid_from ||
      quotaDraft.valid_until !== last.valid_until
    );
  }, [quotaDraft.limitGb, quotaDraft.valid_from, quotaDraft.valid_until, serverQuotaDraft?.limitGb, serverQuotaDraft?.valid_from, serverQuotaDraft?.valid_until]);

  React.useEffect(() => {
    if (!serverQuotaDraft) return;
    if (!quotaDraftInitRef.current) {
      quotaDraftInitRef.current = true;
      setQuotaDraft(serverQuotaDraft);
      userEditedRef.current = false;
      return;
    }
    // Only sync draft when the user doesn't have unsaved changes.
    // We check userEditedRef to be safer than isQuotaDirty alone.
    if (!userEditedRef.current && quotaSaveState !== "saving") {
      setQuotaDraft(serverQuotaDraft);
    }
  }, [serverQuotaDraft?.limitGb, serverQuotaDraft?.valid_from, serverQuotaDraft?.valid_until, isQuotaDirty, quotaSaveState]);

  const validateQuotaDraft = React.useCallback((): string | null => {
    const gb = quotaDraft.limitGb;
    if (!Number.isFinite(gb) || gb < 0) return "Monthly limit must be 0 or a positive number.";
    if (quotaDraft.valid_from && quotaDraft.valid_until) {
      const a = new Date(quotaDraft.valid_from).getTime();
      const b = new Date(quotaDraft.valid_until).getTime();
      if (isFinite(a) && isFinite(b) && b <= a) return "Access window end must be after start.";
    }
    return null;
  }, [quotaDraft.limitGb, quotaDraft.valid_from, quotaDraft.valid_until]);

  const doAutoSaveQuota = React.useCallback(async () => {
    if (!peerId) return;
    if (!serverQuotaDraft) return; // not initialized yet
    const validationErr = validateQuotaDraft();
    if (validationErr) {
      setQuotaErr(validationErr);
      setQuotaSaveState("error");
      return;
    }
    if (!isQuotaDirty) {
      if (quotaSaveState === "dirty") setQuotaSaveState("idle");
      return;
    }

    // If already saving, queue another run.
    if (quotaSavingRef.current) {
      quotaPendingRef.current = true;
      return;
    }

    const snapshot = { ...quotaDraft };
    quotaSavingRef.current = true;
    setQuotaErr("");
    setQuotaSaveState("saving");
    try {
      const body = {
        monthly_limit_bytes: Math.round(snapshot.limitGb * 1024 * 1024 * 1024),
        valid_from: snapshot.valid_from || "",
        valid_until: snapshot.valid_until || "",
      };
      const saved: Quota = await patchPeerQuota(peerId, body);
      setQuota(saved);

      const normalized = {
        limitGb: saved.monthly_limit_bytes ? Number(((saved.monthly_limit_bytes || 0) / (1024 * 1024 * 1024)).toFixed(2)) : 0,
        valid_from: (saved.valid_from as any) || "",
        valid_until: (saved.valid_until as any) || "",
      };

      // Only overwrite the user's draft if they haven't changed it since this save started.
      const stillSame =
        quotaDraft.limitGb === snapshot.limitGb &&
        quotaDraft.valid_from === snapshot.valid_from &&
        quotaDraft.valid_until === snapshot.valid_until;
      if (stillSame) {
        setQuotaDraft(normalized);
        // We just synced with server and user hasn't typed more, so reset edit flag
        userEditedRef.current = false;
      }

      const updatedPeer = await reconcilePeer(peerId);
      setPeer(updatedPeer);

      setQuotaSaveState("saved");
      window.setTimeout(() => {
        setQuotaSaveState((s) => (s === "saved" ? "idle" : s));
      }, 1200);
    } catch (e: any) {
      setQuotaErr(e?.message || "Failed to save quota/window");
      setQuotaSaveState("error");
    } finally {
      quotaSavingRef.current = false;
      if (quotaPendingRef.current) {
        quotaPendingRef.current = false;
        // run again for latest draft
        doAutoSaveQuota();
      }
    }
  }, [peerId, isQuotaDirty, quotaDraft, quotaSaveState, validateQuotaDraft, serverQuotaDraft]);

  // Debounced auto-save whenever quota draft changes.
  React.useEffect(() => {
    if (!serverQuotaDraft) return;
    const validationErr = validateQuotaDraft();
    if (validationErr) {
      setQuotaErr(validationErr);
      setQuotaSaveState("error");
      return;
    }
    if (isQuotaDirty) {
      // Guard: only auto-save if user actually edited something.
      // This prevents "ghost" saves when server data loads and mismatches default state.
      if (!userEditedRef.current) return;

      setQuotaSaveState((s) => (s === "saving" ? s : "dirty"));
      if (quotaSaveTimerRef.current) window.clearTimeout(quotaSaveTimerRef.current);
      quotaSaveTimerRef.current = window.setTimeout(() => {
        doAutoSaveQuota();
      }, 800);
      return () => {
        if (quotaSaveTimerRef.current) window.clearTimeout(quotaSaveTimerRef.current);
      };
    } else {
      // clear any pending timers
      if (quotaSaveTimerRef.current) window.clearTimeout(quotaSaveTimerRef.current);
      quotaSaveTimerRef.current = null;
      if (quotaSaveState === "dirty") setQuotaSaveState("idle");
    }
  }, [quotaDraft.limitGb, quotaDraft.valid_from, quotaDraft.valid_until, isQuotaDirty, validateQuotaDraft, doAutoSaveQuota, serverQuotaDraft]);

  // Auto-refresh peer/quota/live state on the poll interval so scheduler enforcement shows without reload.
  React.useEffect(() => {
    const sec = Math.max(5, refreshSec || 30);
    const id = window.setInterval(() => {
      refreshAll();
    }, sec * 1000);
    return () => window.clearInterval(id);
  }, [refreshSec, refreshAll]);

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
        // No synthetic data: if router can't be reached, hide the live status pill.
        setLiveOnline(null);
        setLiveEndpoint("—");
        setLastSeenLabel("—");
      }
    })();
  }, [peer?.router_id, peer?.interface, peer?.public_key]);

  const kindPill = (() => {
    if (!showKindPills) return null;
    const addr = (peer?.allowed_address || "").trim();
    const outbound = addr === "0.0.0.0/0" || addr === "::/0";
    return (
      <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${outbound ? 'bg-amber-100 text-amber-800' : 'bg-blue-100 text-blue-800'}`}>
        <span className={`inline-block w-2 h-2 rounded-full ${outbound ? 'bg-amber-500' : 'bg-blue-500'}`} />
        {outbound ? 'Outbound' : 'Inbound'}
      </span>
    );
  })();

  const statusPill = (() => {
    if (liveOnline === null) return null;
    const online = !!liveOnline;
    return (
      <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${online ? 'bg-green-100 text-green-800' : 'bg-rose-100 text-rose-800'}`}>
        <span className={`inline-block w-2 h-2 rounded-full ${online ? 'bg-green-500' : 'bg-rose-500'} ${online ? 'pulse' : ''}`} />
        {online ? 'Online' : `Last seen ${lastSeenLabel}`}
      </span>
    );
  })();

  const disableReason = React.useMemo(() => {
    if (!peer) return null;
    if (!actions || actions.length === 0) return null;
    const want = peer.disabled ? "disable" : "enable";
    const hit = actions.find((a) => a.action.includes(want));
    if (!hit) return null;
    const label = hit.action
      .replace(/^quota_/, "quota ")
      .replace(/^window_/, "window ")
      .replace(/^manual_/, "manual ")
      .replace(/^router_/, "router ");
    return { label, note: hit.note || hit.action };
  }, [peer?.disabled, actions, peer?.id]);

  return (
    <div className="mx-auto px-4 md:px-6 py-6">
      <div className="mx-auto my-12 md:my-16 w-full max-w-[960px] rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 shadow-sm p-5 md:p-6 overflow-y-auto overflow-x-hidden">
        {!peer ? (
          <div className="text-sm text-gray-500 dark:text-gray-400">Loading…</div>
        ) : (
          <div className="grid gap-6">
            <div className="flex items-center justify-between mb-2">
              <div className="min-w-0 max-w-[420px] md:max-w-[520px]">
                <div className="text-base text-gray-500 dark:text-gray-400">{peer.name}</div>
                {(() => {
                  const addr = peer.allowed_address.replace("/32", "");
                  const wrap = addr.length > 20;
                  return (
                    <div
                      className={[
                        "text-xl md:text-2xl text-gray-900 dark:text-gray-100",
                        wrap ? "break-all whitespace-normal leading-snug" : "whitespace-nowrap",
                      ].join(" ")}
                    >
                      {addr}
                    </div>
                  );
                })()}
              </div>
              <div className="flex flex-col items-end gap-2">
                {/* Allowance status */}
                {peer && (
                  <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${peer.disabled ? 'bg-rose-100 text-rose-800' : 'bg-indigo-100 text-indigo-800'}`}>
                    <span className={`inline-block w-2 h-2 rounded-full ${peer.disabled ? 'bg-rose-500' : 'bg-indigo-500'}`} />
                    {peer.disabled ? 'Deactivated' : 'Active'}
                  </span>
                )}
                {disableReason && (
                  <span
                    className="rounded-full border border-dashed border-gray-300 dark:border-gray-600 bg-white/60 dark:bg-gray-950 px-2.5 py-1 text-[11px] text-gray-700 dark:text-gray-200"
                    title={disableReason.note}
                  >
                    Reason: {disableReason.label}
                  </span>
                )}
                {/* switch kind pill to blue/amber scheme */}
                {kindPill}
                {statusPill}
              </div>
            </div>

            {/* Usage (moved to top) */}
            <div className="p-0 mt-2">
              <div className="flex items-center justify-between mb-2">
                <div className="text-sm text-gray-700 dark:text-gray-200">Usage</div>
                <div className="flex flex-wrap items-center gap-4 text-xs text-gray-600 dark:text-gray-300">
                  <div className="flex items-center gap-2">
                    <span>Auto refresh</span>
                    <input
                      type="number"
                      min={5}
                      value={refreshSec}
                      onChange={(e) => update({ peer_refresh_seconds: Math.max(5, Number(e.target.value) || 5) })}
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
                      onChange={(e) => update({ peer_default_scope_value: Math.max(1, Number(e.target.value) || 1) })}
                      className="w-14 rounded-full border border-gray-900 bg-gray-900 text-white px-2 py-1 text-xs focus:ring-1 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                    />
                    <select
                      value={scopeUnit}
                      onChange={(e) => update({ peer_default_scope_unit: e.target.value as ScopeUnit })}
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
                {usage.length === 0 ? (
                  <div className="h-full flex items-center justify-center text-sm text-gray-500 dark:text-gray-400">No data</div>
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={usage} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                      <defs>
                        <linearGradient id="g2" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="var(--chart-fill-1)" stopOpacity={0.25} />
                          <stop offset="100%" stopColor="var(--chart-fill-1)" stopOpacity={0} />
                        </linearGradient>
                        <linearGradient id="g3" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="var(--chart-fill-2)" stopOpacity={0.25} />
                          <stop offset="100%" stopColor="var(--chart-fill-2)" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid stroke="var(--chart-grid)" vertical={false} />
                      <XAxis
                        dataKey="day"
                        tick={{ fill: "var(--chart-tick)", fontSize: 12 }}
                        tickFormatter={(val: string) => {
                          try {
                            if (scopeUnit === "days") {
                              // val is YYYY-MM-DD (UTC)
                              const d = new Date(`${val}T00:00:00Z`);
                              return new Intl.DateTimeFormat(undefined, {
                                timeZone: timezone || "UTC",
                                month: "numeric",
                                day: "numeric",
                              }).format(d);
                            }
                            // raw window: val is ISO timestamp (UTC)
                            const d = new Date(val);
                            return new Intl.DateTimeFormat(undefined, {
                              timeZone: timezone || "UTC",
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
                        tickFormatter={(val: number) => `${(val / (1024 * 1024)).toFixed(0)} MB`}
                      />
                      <Tooltip
                        formatter={(value: number, name: string) => [
                          fmtBytes(value as number),
                          name,
                        ]}
                        labelFormatter={(label) => {
                          try {
                            if (scopeUnit === "days") {
                              const d = new Date(`${label}T00:00:00Z`);
                              return new Intl.DateTimeFormat(undefined, {
                                timeZone: timezone || "UTC",
                                dateStyle: "full",
                              }).format(d);
                            }
                            const d = new Date(label);
                            return new Intl.DateTimeFormat(undefined, {
                              timeZone: timezone || "UTC",
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
                      {/* Separate series so relative magnitudes are clear */}
                      <Area
                        type="monotone"
                        dataKey="tx"
                        name="TX (download)"
                        stroke="var(--chart-line-1)"
                        fill="url(#g2)"
                        strokeWidth={2}
                      />
                      <Area
                        type="monotone"
                        dataKey="rx"
                        name="RX (upload)"
                        stroke="var(--chart-line-2)"
                        fill="url(#g3)"
                        strokeWidth={2}
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </div>
              <div className="flex items-center justify-center gap-6 mt-2 text-xs text-gray-500 dark:text-gray-400">
                {(() => {
                  const totRx = usage.reduce((a, b) => a + (b.rx || 0), 0);
                  const totTx = usage.reduce((a, b) => a + (b.tx || 0), 0);
                  return (
                    <>
                      <div>Total Download: <span className="font-medium text-gray-700 dark:text-gray-300">{fmtBytes(totTx)}</span></div>
                      <div>Total Upload: <span className="font-medium text-gray-700 dark:text-gray-300">{fmtBytes(totRx)}</span></div>
                    </>
                  );
                })()}
              </div>
            </div>
            {/* Quota management */}
            <div className="grid gap-5 mt-6 pt-4 border-t border-gray-100 dark:border-gray-800">
              <div className="flex items-center justify-between">
                <div className="text-sm text-gray-700 dark:text-gray-200">Quota</div>
                <div className="flex items-center gap-3">
                  {quotaSaveState !== "idle" && (
                    <div
                      className={[
                        "text-xs",
                        quotaSaveState === "saving" ? "text-gray-500 dark:text-gray-400"
                          : quotaSaveState === "saved" ? "text-green-700 dark:text-green-300"
                            : quotaSaveState === "error" ? "text-rose-600 dark:text-rose-300"
                              : "text-amber-700 dark:text-amber-300",
                      ].join(" ")}
                    >
                      {quotaSaveState === "saving"
                        ? "Saving…"
                        : quotaSaveState === "saved"
                          ? "Saved"
                          : quotaSaveState === "error"
                            ? "Error"
                            : "Unsaved"}
                    </div>
                  )}
                  {isQuotaDirty && serverQuotaDraft && (
                    <button
                      type="button"
                      onClick={() => {
                        setQuotaErr("");
                        setQuotaSaveState("idle");
                        setQuotaDraft(serverQuotaDraft);
                        userEditedRef.current = false;
                      }}
                      className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                    >
                      Revert
                    </button>
                  )}
                </div>
              </div>
              <div className="grid gap-5 md:grid-cols-2">
                <div className="grid gap-3">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Monthly data limit (GB)</label>
                  <div className="flex items-center gap-3">
                    <input
                      type="number"
                      min={0}
                      step={1}
                      className="w-32 rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
                      value={quotaDraft.limitGb}
                      onChange={(e) => {
                        const gb = Math.max(0, Number(e.target.value || 0));
                        userEditedRef.current = true;
                        setQuotaDraft((d) => ({ ...d, limitGb: gb }));
                      }}
                    />
                  </div>
                  {/* Consumption bar */}
                  {quota && quotaDraft.limitGb > 0 ? (
                    <div className="mt-1">
                      <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
                        <span>Usage</span>
                        <span>
                          {(() => {
                            const limitBytes = quotaDraft.limitGb * 1024 * 1024 * 1024;
                            return `${(((quota.used_rx + quota.used_tx) / Math.max(1, limitBytes)).toLocaleString(undefined, { style: "percent", minimumFractionDigits: 0 }))} `;
                          })()}
                        </span>
                      </div>
                      <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-gray-900 dark:bg-gray-100"
                          style={{
                            width: `${(() => {
                              const limitBytes = quotaDraft.limitGb * 1024 * 1024 * 1024;
                              return Math.min(100, Math.round(((quota.used_rx + quota.used_tx) / Math.max(1, limitBytes)) * 100));
                            })()
                              }% `,
                          }}
                        />
                      </div>
                    </div>
                  ) : <div className="text-xs text-gray-500 dark:text-gray-400">Unlimited (no quota)</div>}
                </div>
                <div className="grid gap-3">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Access window (optional)</label>
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="datetime-local"
                      className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
                      value={quotaDraft.valid_from}
                      onChange={(e) => {
                        userEditedRef.current = true;
                        setQuotaDraft((d) => ({ ...d, valid_from: e.target.value }));
                      }}
                    />
                    <input
                      type="datetime-local"
                      className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
                      value={quotaDraft.valid_until}
                      onChange={(e) => {
                        userEditedRef.current = true;
                        setQuotaDraft((d) => ({ ...d, valid_until: e.target.value }));
                      }}
                    />
                  </div>
                  {/* Time window bar */}
                  {quotaDraft.valid_from || quotaDraft.valid_until ? (
                    <div className="mt-1">
                      <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
                        <span>Window</span>
                        <span>
                          {(() => {
                            const fmt = (val?: string | null) => {
                              if (!val) return "—";
                              try {
                                const d = new Date(val);
                                return new Intl.DateTimeFormat(undefined, {
                                  timeZone: timezone || "UTC",
                                  dateStyle: "short",
                                  timeStyle: "short",
                                }).format(d);
                              } catch {
                                return val;
                              }
                            };
                            return `${fmt(quotaDraft.valid_from)} → ${fmt(quotaDraft.valid_until)} `;
                          })()}
                        </span>
                      </div>
                      <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
                        <div className="h-full bg-gray-900 dark:bg-gray-100" style={{ width: `${Math.round((windowProgress ?? 0) * 100)}% ` }} />
                      </div>
                    </div>
                  ) : <div className="text-xs text-gray-500 dark:text-gray-400">No access window set</div>}
                </div>
              </div>
              {quotaErr && <div className="text-sm text-red-600">{quotaErr}</div>}
            </div>
            {/* Details */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 text-sm mt-6 pt-4 border-t border-gray-100 dark:border-gray-800">
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                <div className="text-gray-500 dark:text-gray-400">Interface</div>
                <LockedField value={peer.interface} />
                <div className="text-gray-500 dark:text-gray-400">Router</div>
                <LockedField value={routerName || `#${peer.router_id} `} />
                <div className="text-gray-500 dark:text-gray-400">Public key</div>
                <LockedField value={peer.public_key} mono className="break-all" />
                <div className="text-gray-500 dark:text-gray-400">Endpoint</div>
                <LockedField value={liveEndpoint} mono />
                <div className="text-gray-500 dark:text-gray-400">Last seen</div>
                <LockedField value={lastSeenLabel} />
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2">
                <div className="text-gray-500 dark:text-gray-400">Selected</div>
                <LockedField value={peer.selected ? 'Yes' : 'No'} />
                <div className="text-gray-500 dark:text-gray-400">Disabled</div>
                <LockedField value={peer.disabled ? 'Yes' : 'No'} />
                <div className="text-gray-500 dark:text-gray-400">Monthly download (TX)</div>
                <LockedField value={fmtBytes(usage.reduce((a, b) => a + (b.tx || 0), 0))} />
                <div className="text-gray-500 dark:text-gray-400">Monthly upload (RX)</div>
                <LockedField value={fmtBytes(usage.reduce((a, b) => a + (b.rx || 0), 0))} />
              </div>
            </div>

            {/* Activity log */}
            <div className="grid gap-3 mt-6 pt-4 border-t border-gray-100 dark:border-gray-800">
              <div className="flex items-center justify-between">
                <div className="text-sm text-gray-700 dark:text-gray-200">Activity log</div>
                <div className="flex items-center gap-2">
                  <div className="text-xs text-gray-500 dark:text-gray-400">Showing {actionsLimit}</div>
                  {actionsHasMore && actionsLimit < 200 && (
                    <button
                      type="button"
                      onClick={async () => {
                        const next = Math.min(200, actionsLimit + 10);
                        setActionsLimit(next);
                        await fetchActions(next);
                      }}
                      className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                    >
                      Show more
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={() => refreshAll()}
                    className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                    title="Refresh"
                  >
                    Refresh
                  </button>
                </div>
              </div>
              {actionsErr && <div className="text-sm text-rose-600 dark:text-rose-300">{actionsErr}</div>}
              {actions.length === 0 ? (
                <div className="text-xs text-gray-500 dark:text-gray-400">No log entries yet.</div>
              ) : (
                <div className="grid gap-2">
                  {actions.map((a, idx) => {
                    const tsLabel = (() => {
                      try {
                        const d = new Date(a.ts);
                        return new Intl.DateTimeFormat(undefined, {
                          timeZone: timezone || "UTC",
                          dateStyle: "medium",
                          timeStyle: "medium",
                        }).format(d);
                      } catch {
                        return a.ts;
                      }
                    })();
                    const isFail = a.action.endsWith("_failed");
                    const isDisable = a.action.includes("disable");
                    const isEnable = a.action.includes("enable");
                    const badgeCls = isFail
                      ? "bg-rose-50 text-rose-700 dark:bg-rose-500/10 dark:text-rose-300"
                      : isDisable
                        ? "bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300"
                        : isEnable
                          ? "bg-green-50 text-green-800 dark:bg-green-500/10 dark:text-green-300"
                          : "bg-gray-50 text-gray-800 dark:bg-gray-950 dark:text-gray-200";
                    return (
                      <div
                        key={`${a.ts} -${a.action} -${idx} `}
                        className="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 bg-white/50 dark:bg-gray-950 px-3 py-2"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <span className={`inline - flex items - center rounded - full px - 2 py - 0.5 text - [11px] ${badgeCls} `}>
                                {a.action}
                              </span>
                              <span className="text-[11px] text-gray-500 dark:text-gray-400">{tsLabel}</span>
                            </div>
                            {a.note ? (
                              <div className="mt-1 text-xs text-gray-700 dark:text-gray-200 break-words">
                                {a.note}
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
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
                      try { const points = await getPeerUsage(peer.id); setUsage(points); } catch { }
                      try { const q = await getPeerQuota(peer.id); setQuota(q); } catch { }
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

              <div className="flex items-center gap-2">
                <input
                  type="checkbox"
                  id="deleteAll"
                  className="rounded border-gray-300 text-rose-600 focus:ring-rose-500"
                  checked={alsoRemoveRouter}
                  onChange={(e) => setAlsoRemoveRouter(e.target.checked)}
                />
                <label htmlFor="deleteAll" className="text-sm text-gray-700">Remove from router too</label>
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
                      // If check is ON, skipRouter = false. If check is OFF, skipRouter = true.
                      await deletePeer(peer.id, !alsoRemoveRouter);
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


