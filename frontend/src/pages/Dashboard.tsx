import React from "react";
import { useNavigate } from "react-router-dom";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import QRCode from "react-qr-code";
import nacl from "tweetnacl";
import { useAutoSaveSettings } from "../useAutoSaveSettings";
import { useLooseNumberInput } from "../hooks/useLooseNumberInput";
import { formatDatetimeLocalValue } from "../datetimeLocal";
import {
  createRouterPeer,
  getDashboardLiveStatus,
  getActiveRouter,
  getMetrics,
  getMonthlySummary,
  getMonthlySummaryByRouter,
  getPeersSummary,
  getSummaryRaw,
  getSummaryRawByRouter,
  listInterfaces,
  listRouters,
  listSavedPeersSelected,
  routerInterfaceDetail,
  type Metrics,
  type MonthlySummaryPoint,
  type Router,
  type RouterMonthlySummaryPoint,
  type RouterSummaryRawPoint,
  type SavedPeer,
  type SummaryRawPoint,
} from "../api";

type ScopeUnit = "minutes" | "hours" | "days";
type RouterScope = "all" | "active" | "selected";

type PeerStatus = { online: boolean; last: string; raw_last_handshake: number };
type UsageTotals = { rx: number; tx: number };

const ROUTER_PEER_PREVIEW_COUNT = 6;
const FILTER_STATUS_VALUES = new Set(["all", "online", "offline", "enabled", "disabled"]);

function Card({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const base = "rounded-3xl overflow-hidden ring-1 ring-gray-200 ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5 dark:ring-gray-800 dark:ring-offset-gray-950 dark:bg-gray-900";
  return <div className={`${base} ${className}`} {...props} />;
}

function FairUsageShieldIcon({ throttled }: { throttled: boolean }) {
  const path = "M12 1L3 5v6c0 5.55 3.84 10.74 9 12 5.16-1.26 9-6.45 9-12V5l-9-4z";
  if (throttled) {
    return (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="currentColor"
        className="h-4 w-4 text-amber-500 dark:text-amber-400"
        aria-hidden
      >
        <path d={path} />
      </svg>
    );
  }
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      className="h-4 w-4 text-gray-400 dark:text-gray-500"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d={path} />
    </svg>
  );
}

function fmtBytes(n: number) {
  if (!n || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let u = 0;
  let x = n;
  while (x >= 1024 && u < units.length - 1) {
    x /= 1024;
    u++;
  }
  return `${x.toFixed(x >= 100 ? 0 : x >= 10 ? 1 : 2)} ${units[u]}`;
}

function normalizeRouterIds(ids: unknown): number[] {
  if (!Array.isArray(ids)) return [];
  const out: number[] = [];
  const seen = new Set<number>();
  for (const raw of ids) {
    const id = Number(raw);
    if (!Number.isInteger(id) || id <= 0 || seen.has(id)) continue;
    seen.add(id);
    out.push(id);
  }
  return out;
}

function formatRelativeHandshake(ageSec?: number) {
  if (!ageSec || ageSec <= 0) return "Never";
  const m = Math.floor(ageSec / 60);
  const h = Math.floor(m / 60);
  const d = Math.floor(h / 24);
  const mon = Math.floor(d / 30);
  if (ageSec < 60) return `${ageSec}s ago`;
  if (m < 60) return `${m}m ago`;
  if (h < 24) return `${h}h ago`;
  if (d < 30) return `${d}d ago`;
  return `${mon}mo ago`;
}

function buildChartData(
  scopeUnit: ScopeUnit,
  monthly: MonthlySummaryPoint[],
  raw: SummaryRawPoint[],
) {
  if (scopeUnit === "days") {
    return monthly.map((point) => ({
      x: point.day,
      rx: point.rx / (1024 * 1024),
      tx: point.tx / (1024 * 1024),
    }));
  }
  return raw.map((point) => ({
    x: point.ts,
    rx: point.rx / (1024 * 1024),
    tx: point.tx / (1024 * 1024),
  }));
}

function buildRouterMonthlyMap(rows: RouterMonthlySummaryPoint[]) {
  const out: Record<number, MonthlySummaryPoint[]> = {};
  for (const row of rows) {
    (out[row.router_id] ||= []).push({ day: row.day, rx: row.rx, tx: row.tx });
  }
  return out;
}

function buildRouterRawMap(rows: RouterSummaryRawPoint[]) {
  const out: Record<number, SummaryRawPoint[]> = {};
  for (const row of rows) {
    (out[row.router_id] ||= []).push({ ts: row.ts, rx: row.rx, tx: row.tx });
  }
  return out;
}

function aggregateMonthlyRows(rows: RouterMonthlySummaryPoint[]): MonthlySummaryPoint[] {
  const out: Record<string, UsageTotals> = {};
  for (const row of rows) {
    const bucket = (out[row.day] ||= { rx: 0, tx: 0 });
    bucket.rx += row.rx;
    bucket.tx += row.tx;
  }
  return Object.entries(out)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, totals]) => ({ day, rx: totals.rx, tx: totals.tx }));
}

function aggregateRawRows(rows: RouterSummaryRawPoint[]): SummaryRawPoint[] {
  const out: Record<string, UsageTotals> = {};
  for (const row of rows) {
    const bucket = (out[row.ts] ||= { rx: 0, tx: 0 });
    bucket.rx += row.rx;
    bucket.tx += row.tx;
  }
  return Object.entries(out)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([ts, totals]) => ({ ts, rx: totals.rx, tx: totals.tx }));
}

function UsageChart({
  scopeUnit,
  monthly,
  raw,
  timezone,
  emptyLabel,
}: {
  scopeUnit: ScopeUnit;
  monthly: MonthlySummaryPoint[];
  raw: SummaryRawPoint[];
  timezone: string;
  emptyLabel: string;
}) {
  const chartId = React.useId().replace(/:/g, "");
  const txGradientId = `${chartId}-tx`;
  const rxGradientId = `${chartId}-rx`;
  const data = React.useMemo(() => buildChartData(scopeUnit, monthly, raw), [scopeUnit, monthly, raw]);
  if (data.length === 0) {
    return <div className="h-full flex items-center justify-center text-sm text-gray-500 dark:text-gray-400">{emptyLabel}</div>;
  }
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id={txGradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--chart-fill-1)" stopOpacity={0.25} />
            <stop offset="100%" stopColor="var(--chart-fill-1)" stopOpacity={0} />
          </linearGradient>
          <linearGradient id={rxGradientId} x1="0" y1="0" x2="0" y2="1">
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
                  timeZone: timezone || "UTC",
                  month: "numeric",
                  day: "numeric",
                }).format(d);
              }
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
        <YAxis tick={{ fill: "var(--chart-tick)", fontSize: 12 }} tickFormatter={(val: number) => `${Math.round(val)} MB`} />
        <Tooltip
          formatter={(value: number, name: string) => [`${(value as number).toFixed(1)} MB`, name === "rx" ? "RX" : "TX"]}
          labelFormatter={(label) => {
            try {
              if (scopeUnit === "days") {
                return new Intl.DateTimeFormat(undefined, {
                  timeZone: timezone || "UTC",
                  dateStyle: "full",
                }).format(new Date(`${label}T00:00:00Z`));
              }
              return new Intl.DateTimeFormat(undefined, {
                timeZone: timezone || "UTC",
                dateStyle: "medium",
                timeStyle: "medium",
              }).format(new Date(label));
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
        <Area type="monotone" dataKey="tx" name="tx" stroke="var(--chart-line-1)" fill={`url(#${txGradientId})`} strokeWidth={2} />
        <Area type="monotone" dataKey="rx" name="rx" stroke="var(--chart-line-2)" fill={`url(#${rxGradientId})`} strokeWidth={2} />
      </AreaChart>
    </ResponsiveContainer>
  );
}

const MemoUsageChart = React.memo(UsageChart);

function LazySectionBody({
  children,
  placeholderHeight = 320,
}: {
  children: React.ReactNode;
  placeholderHeight?: number;
}) {
  const ref = React.useRef<HTMLDivElement | null>(null);
  const [visible, setVisible] = React.useState(false);

  React.useEffect(() => {
    if (visible) return;
    const node = ref.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "300px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [visible]);

  return (
    <div ref={ref}>
      {visible ? children : (
        <div
          className="rounded-2xl border border-dashed border-gray-200 dark:border-gray-800 flex items-center justify-center text-sm text-gray-500 dark:text-gray-400"
          style={{ minHeight: placeholderHeight }}
        >
          Scroll to load router details
        </div>
      )}
    </div>
  );
}

export default function Dashboard() {
  const navigate = useNavigate();
  const { settings, update } = useAutoSaveSettings();

  const [setupOk, setSetupOk] = React.useState<null | boolean>(null);
  const [routers, setRouters] = React.useState<Router[]>([]);
  const [activeRouterId, setActiveRouterId] = React.useState<number | null>(null);
  const [monthly, setMonthly] = React.useState<MonthlySummaryPoint[]>([]);
  const [raw, setRaw] = React.useState<SummaryRawPoint[]>([]);
  const [monthlyByRouter, setMonthlyByRouter] = React.useState<RouterMonthlySummaryPoint[]>([]);
  const [rawByRouter, setRawByRouter] = React.useState<RouterSummaryRawPoint[]>([]);
  const [peers, setPeers] = React.useState<SavedPeer[]>([]);
  const [peerUsageMap, setPeerUsageMap] = React.useState<Record<number, UsageTotals>>({});
  const [fairUsageByPeer, setFairUsageByPeer] = React.useState<Record<number, boolean>>({});
  const [fairUsageThrottledByPeer, setFairUsageThrottledByPeer] = React.useState<Record<number, boolean>>({});
  const [statusMap, setStatusMap] = React.useState<Record<number, PeerStatus>>({});
  const [metrics, setMetrics] = React.useState<Metrics | null>(null);
  const [timeFrom, setTimeFrom] = React.useState("");
  const [timeTo, setTimeTo] = React.useState("");
  const [allTime, setAllTime] = React.useState(false);
  const [todayTick, setTodayTick] = React.useState(0);
  const [localFilterText, setLocalFilterText] = React.useState("");
  const [expandedRouters, setExpandedRouters] = React.useState<Record<number, boolean>>({});

  const [showAdd, setShowAdd] = React.useState(false);
  const [fabOpen, setFabOpen] = React.useState(false);
  const [addBusy, setAddBusy] = React.useState(false);
  const [addErr, setAddErr] = React.useState("");
  const [interfaceOptions, setInterfaceOptions] = React.useState<string[]>([]);
  const [interfaceLoadFailed, setInterfaceLoadFailed] = React.useState(false);
  const [form, setForm] = React.useState(() => ({
    routerId: null as number | null,
    interface: "wgmik",
    name: "",
    allowed: "10.65.74.100/32",
    privateKey: "",
    publicKey: "",
    usePsk: false,
    psk: "",
    serverPublicKey: "",
    endpoint: "",
    dns: "8.8.8.8, 1.1.1.1",
    mtu: "1280",
    persistentKeepalive: "25",
    allowedIps: "0.0.0.0/0, ::/0",
  }));

  const refreshSec = settings?.dashboard_refresh_seconds ?? 30;
  const scopeValue = settings?.dashboard_scope_value ?? 14;
  const scopeUnit = (settings?.dashboard_scope_unit as ScopeUnit) ?? "days";

  const dashRefreshInput = useLooseNumberInput(
    refreshSec,
    (n) => update({ dashboard_refresh_seconds: n }),
    { min: 5, emptyFallback: 5 },
  );
  const dashScopeInput = useLooseNumberInput(
    scopeValue,
    (n) => update({ dashboard_scope_value: n }),
    { min: 1, emptyFallback: 1 },
  );
  const timezone = settings?.timezone ?? "UTC";
  const showKindPills = settings?.show_kind_pills ?? true;
  const showHwStats = settings?.show_hw_stats ?? true;
  const filterStatus = FILTER_STATUS_VALUES.has(settings?.dashboard_filter_status ?? "")
    ? (settings?.dashboard_filter_status as "all" | "online" | "offline" | "enabled" | "disabled")
    : "all";
  const sortBy = (settings?.dashboard_sort_by as "name" | "last_seen" | "created" | "usage") ?? "created";
  const rawScope = ((settings?.dashboard_router_scope as RouterScope | undefined) ?? "all");
  const rawSelectedRouterIds = normalizeRouterIds(settings?.dashboard_selected_router_ids);
  const todayFrame = Boolean(settings?.dashboard_time_frame_today);

  const toIso = React.useCallback((value: string) => {
    if (!value) return undefined;
    const date = new Date(value);
    if (!Number.isFinite(date.getTime())) return undefined;
    return date.toISOString();
  }, []);
  const rawStartIso = toIso(timeFrom);
  const rawEndIso = toIso(timeTo);

  const effectiveStartIso = React.useMemo(() => {
    if (allTime) return undefined;
    if (todayFrame) {
      const d = new Date();
      d.setHours(0, 0, 0, 0);
      return d.toISOString();
    }
    return rawStartIso;
  }, [allTime, todayFrame, rawStartIso, todayTick]);

  const effectiveEndIso = React.useMemo(() => {
    if (allTime) return undefined;
    if (todayFrame) return new Date().toISOString();
    return rawEndIso;
  }, [allTime, todayFrame, rawEndIso, todayTick]);

  const timeFrameActive = allTime || !!rawStartIso || !!rawEndIso || todayFrame;

  const displayTimeFrom = React.useMemo(() => {
    if (todayFrame) {
      const d = new Date();
      d.setHours(0, 0, 0, 0);
      return formatDatetimeLocalValue(d);
    }
    return timeFrom;
  }, [todayFrame, timeFrom, todayTick]);

  React.useEffect(() => {
    if (scopeUnit !== "days" && allTime) setAllTime(false);
  }, [scopeUnit, allTime]);

  React.useEffect(() => {
    if (!todayFrame) return;
    setTodayTick((t) => t + 1);
    const ms = Math.max(5000, refreshSec * 1000);
    const id = window.setInterval(() => setTodayTick((t) => t + 1), ms);
    return () => window.clearInterval(id);
  }, [todayFrame, refreshSec]);

  React.useEffect(() => {
    (async () => {
      try {
        const [routerRows, active] = await Promise.all([listRouters(), getActiveRouter()]);
        if (!routerRows.length) {
          setSetupOk(false);
          navigate("/setup", { replace: true });
          return;
        }
        setRouters(routerRows);
        setActiveRouterId(active?.router_id ?? null);
        setSetupOk(true);
      } catch {
        setSetupOk(false);
        navigate("/setup", { replace: true });
      }
    })();
  }, [navigate]);

  const routerIdsSet = React.useMemo(() => new Set(routers.map((router) => router.id)), [routers]);

  const normalizedScopeState = React.useMemo(() => {
    const validSelected = rawSelectedRouterIds.filter((id) => routerIdsSet.has(id));
    let scope: RouterScope = rawScope === "active" || rawScope === "selected" || rawScope === "all" ? rawScope : "all";
    if (scope === "active" && (!activeRouterId || !routerIdsSet.has(activeRouterId))) scope = "all";
    if (scope === "selected" && validSelected.length === 0) scope = "all";
    return { scope, selectedRouterIds: validSelected };
  }, [rawScope, rawSelectedRouterIds, routerIdsSet, activeRouterId]);

  React.useEffect(() => {
    if (!settings || !routers.length) return;
    const patch: Partial<typeof settings> = {};
    if (rawScope !== normalizedScopeState.scope) patch.dashboard_router_scope = normalizedScopeState.scope;
    if (
      rawSelectedRouterIds.length !== normalizedScopeState.selectedRouterIds.length ||
      rawSelectedRouterIds.some((id, idx) => id !== normalizedScopeState.selectedRouterIds[idx])
    ) {
      patch.dashboard_selected_router_ids = normalizedScopeState.selectedRouterIds;
    }
    if (Object.keys(patch).length > 0) update(patch);
  }, [settings, routers.length, rawScope, rawSelectedRouterIds, normalizedScopeState, update]);

  const routersByName = React.useMemo(
    () => [...routers].sort((a, b) => a.name.localeCompare(b.name) || a.id - b.id),
    [routers],
  );
  const routersById = React.useMemo(
    () => Object.fromEntries(routers.map((router) => [router.id, router])),
    [routers],
  );

  const inScopeRouters = React.useMemo(() => {
    if (normalizedScopeState.scope === "active") {
      return activeRouterId && routersById[activeRouterId] ? [routersById[activeRouterId]] : [];
    }
    if (normalizedScopeState.scope === "selected") {
      return normalizedScopeState.selectedRouterIds
        .map((id) => routersById[id])
        .filter((router): router is Router => !!router);
    }
    return routersByName;
  }, [normalizedScopeState, activeRouterId, routersById, routersByName]);

  const inScopeRouterIds = React.useMemo(() => inScopeRouters.map((router) => router.id), [inScopeRouters]);
  const inScopeRouterIdsKey = React.useMemo(() => inScopeRouterIds.join(","), [inScopeRouterIds]);
  const singleInScopeRouterId = inScopeRouterIds.length === 1 ? inScopeRouterIds[0] : null;
  const scopeSignature = React.useMemo(
    () => JSON.stringify({
      routerIds: inScopeRouterIds,
      scopeUnit,
      scopeValue,
      allTime,
      todayFrame,
      todayTick,
      startIso: todayFrame ? "" : (rawStartIso || ""),
      endIso: todayFrame ? "" : (rawEndIso || ""),
    }),
    [inScopeRouterIds, scopeUnit, scopeValue, allTime, todayFrame, todayTick, rawStartIso, rawEndIso],
  );
  const scopeSignatureRef = React.useRef(scopeSignature);
  const statusRequestRef = React.useRef(0);
  const rangeSeconds = React.useMemo(() => {
    if (scopeUnit === "days") return null;
    const baseSeconds = scopeUnit === "minutes" ? Math.max(1, scopeValue) * 60 : Math.max(1, scopeValue) * 3600;
    if (!effectiveStartIso) return baseSeconds;
    const startMs = new Date(effectiveStartIso).getTime();
    const endMs = new Date(effectiveEndIso || new Date().toISOString()).getTime();
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return baseSeconds;
    return Math.max(60, Math.floor((endMs - startMs) / 1000));
  }, [scopeUnit, scopeValue, effectiveStartIso, effectiveEndIso]);

  React.useEffect(() => {
    scopeSignatureRef.current = scopeSignature;
  }, [scopeSignature]);

  const filteredPeers = React.useMemo(() => {
    let out = [...peers];
    const text = localFilterText.trim().toLowerCase();
    if (text) {
      out = out.filter((peer) =>
        peer.name.toLowerCase().includes(text) ||
        peer.public_key.toLowerCase().includes(text) ||
        peer.allowed_address.toLowerCase().includes(text),
      );
    }
    if (filterStatus === "online") {
      out = out.filter((peer) => statusMap[peer.id]?.online === true);
    } else if (filterStatus === "offline") {
      out = out.filter((peer) => statusMap[peer.id]?.online === false);
    } else if (filterStatus === "enabled") {
      out = out.filter((peer) => !peer.disabled);
    } else if (filterStatus === "disabled") {
      out = out.filter((peer) => peer.disabled);
    }
    out.sort((a, b) => {
      if (sortBy === "name") return a.name.localeCompare(b.name);
      if (sortBy === "last_seen") {
        const aVal = statusMap[a.id]?.raw_last_handshake || Number.MAX_SAFE_INTEGER;
        const bVal = statusMap[b.id]?.raw_last_handshake || Number.MAX_SAFE_INTEGER;
        return aVal - bVal;
      }
      if (sortBy === "usage") {
        const ua = peerUsageMap[a.id] || { rx: 0, tx: 0 };
        const ub = peerUsageMap[b.id] || { rx: 0, tx: 0 };
        return (ub.rx + ub.tx) - (ua.rx + ua.tx);
      }
      return b.id - a.id;
    });
    return out;
  }, [peers, localFilterText, filterStatus, sortBy, statusMap, peerUsageMap]);

  const hasPeerFilters = localFilterText.trim() !== "" || filterStatus !== "all";
  const routerMonthlyMap = React.useMemo(() => buildRouterMonthlyMap(monthlyByRouter), [monthlyByRouter]);
  const routerRawMap = React.useMemo(() => buildRouterRawMap(rawByRouter), [rawByRouter]);

  const routerSections = React.useMemo(() => {
    return inScopeRouters
      .map((router) => {
        const routerPeers = filteredPeers.filter((peer) => peer.router_id === router.id);
        const totals = routerPeers.reduce(
          (acc, peer) => {
            const usage = peerUsageMap[peer.id] || { rx: 0, tx: 0 };
            acc.rx += usage.rx;
            acc.tx += usage.tx;
            return acc;
          },
          { rx: 0, tx: 0 },
        );
        const online = routerPeers.filter((peer) => statusMap[peer.id]?.online).length;
        const disabled = routerPeers.filter((peer) => peer.disabled).length;
        const monthlyRows = routerMonthlyMap[router.id] || [];
        const rawRows = routerRawMap[router.id] || [];
        return {
          router,
          peers: routerPeers,
          totals,
          online,
          disabled,
          monthly: monthlyRows,
          raw: rawRows,
        };
      })
      .filter((section) => !hasPeerFilters || section.peers.length > 0);
  }, [inScopeRouters, filteredPeers, peerUsageMap, statusMap, routerMonthlyMap, routerRawMap, hasPeerFilters]);

  const toggleRouterExpanded = React.useCallback((routerId: number) => {
    setExpandedRouters((current) => ({ ...current, [routerId]: !current[routerId] }));
  }, []);

  const loadPeers = React.useCallback(async (signature: string) => {
    try {
      const rows = await listSavedPeersSelected(undefined, inScopeRouterIds);
      if (signature !== scopeSignatureRef.current) return;
      setPeers(rows);
    } catch {
      if (signature !== scopeSignatureRef.current) return;
      setPeers([]);
    }
  }, [inScopeRouterIds]);

  const loadChartData = React.useCallback(async (signature: string) => {
    try {
      if (scopeUnit === "days") {
        if (singleInScopeRouterId) {
          let aggregateRows: MonthlySummaryPoint[] = [];
          if (allTime) {
            aggregateRows = await getMonthlySummary(undefined, undefined, { allTime: true, routerIds: inScopeRouterIds });
          } else if (effectiveStartIso || effectiveEndIso) {
            aggregateRows = await getMonthlySummary(undefined, undefined, { start: effectiveStartIso, end: effectiveEndIso, routerIds: inScopeRouterIds });
          } else {
            aggregateRows = await getMonthlySummary(scopeValue, undefined, { routerIds: inScopeRouterIds });
          }
          if (signature !== scopeSignatureRef.current) return;
          setMonthly(aggregateRows);
          setMonthlyByRouter(aggregateRows.map((point) => ({
            router_id: singleInScopeRouterId,
            day: point.day,
            rx: point.rx,
            tx: point.tx,
          })));
        } else {
          let routerRows: RouterMonthlySummaryPoint[] = [];
          if (allTime) {
            routerRows = await getMonthlySummaryByRouter(undefined, undefined, { allTime: true, routerIds: inScopeRouterIds });
          } else if (effectiveStartIso || effectiveEndIso) {
            routerRows = await getMonthlySummaryByRouter(undefined, undefined, { start: effectiveStartIso, end: effectiveEndIso, routerIds: inScopeRouterIds });
          } else {
            routerRows = await getMonthlySummaryByRouter(scopeValue, undefined, { routerIds: inScopeRouterIds });
          }
          if (signature !== scopeSignatureRef.current) return;
          setMonthlyByRouter(routerRows);
          setMonthly(aggregateMonthlyRows(routerRows));
        }
        setRaw([]);
        setRawByRouter([]);
        return;
      }

      const seconds = rangeSeconds ?? 3600;
      const interval = scopeUnit === "minutes" ? 60 : 3600;
      if (singleInScopeRouterId) {
        const aggregateRows = await getSummaryRaw(seconds, undefined, interval, effectiveStartIso, effectiveEndIso, inScopeRouterIds);
        if (signature !== scopeSignatureRef.current) return;
        setRaw(aggregateRows);
        setRawByRouter(aggregateRows.map((point) => ({
          router_id: singleInScopeRouterId,
          ts: point.ts,
          rx: point.rx,
          tx: point.tx,
        })));
      } else {
        const routerRows = await getSummaryRawByRouter(seconds, undefined, interval, effectiveStartIso, effectiveEndIso, inScopeRouterIds);
        if (signature !== scopeSignatureRef.current) return;
        setRawByRouter(routerRows);
        setRaw(aggregateRawRows(routerRows));
      }
      setMonthly([]);
      setMonthlyByRouter([]);
    } catch {
      if (signature !== scopeSignatureRef.current) return;
      if (scopeUnit === "days") {
        setMonthly([]);
        setMonthlyByRouter([]);
        setRaw([]);
        setRawByRouter([]);
      } else {
        setRaw([]);
        setRawByRouter([]);
        setMonthly([]);
        setMonthlyByRouter([]);
      }
    }
  }, [scopeUnit, singleInScopeRouterId, allTime, effectiveStartIso, effectiveEndIso, scopeValue, inScopeRouterIds, rangeSeconds]);

  const loadUsageMap = React.useCallback(async (signature: string) => {
    try {
      const opts: {
        seconds?: number;
        days?: number;
        routerIds?: number[];
        start?: string;
        end?: string;
        allTime?: boolean;
      } = { routerIds: inScopeRouterIds };
      if (scopeUnit === "days") {
        if (allTime) {
          opts.allTime = true;
        } else if (effectiveStartIso || effectiveEndIso) {
          opts.start = effectiveStartIso;
          opts.end = effectiveEndIso;
        } else {
          opts.days = scopeValue;
        }
      } else {
        opts.seconds = rangeSeconds ?? 3600;
        opts.start = effectiveStartIso;
        opts.end = effectiveEndIso;
      }
      const sums = await getPeersSummary(opts);
      if (signature !== scopeSignatureRef.current) return;
      const next: Record<number, UsageTotals> = {};
      const nextFu: Record<number, boolean> = {};
      const nextFuTh: Record<number, boolean> = {};
      for (const sum of sums) {
        next[sum.peer_id] = { rx: sum.rx, tx: sum.tx };
        nextFu[sum.peer_id] = sum.has_fair_usage === true;
        nextFuTh[sum.peer_id] = sum.fair_usage_throttled === true;
      }
      setPeerUsageMap(next);
      setFairUsageByPeer(nextFu);
      setFairUsageThrottledByPeer(nextFuTh);
    } catch {
      if (signature !== scopeSignatureRef.current) return;
      setPeerUsageMap({});
      setFairUsageByPeer({});
      setFairUsageThrottledByPeer({});
    }
  }, [scopeUnit, scopeValue, allTime, effectiveStartIso, effectiveEndIso, inScopeRouterIds, rangeSeconds]);

  const loadMetrics = React.useCallback(async () => {
    try {
      const next = await getMetrics();
      setMetrics(next);
    } catch {
      setMetrics(null);
    }
  }, []);

  const loadStatusMap = React.useCallback(async () => {
    const requestId = ++statusRequestRef.current;
    if (inScopeRouterIds.length === 0) {
      setStatusMap({});
      return;
    }
    try {
      const rows = await getDashboardLiveStatus(undefined, inScopeRouterIds);
      if (requestId !== statusRequestRef.current) return;
      const next: Record<number, PeerStatus> = {};
      for (const row of rows) {
        next[row.peer_id] = {
          online: row.online,
          last: formatRelativeHandshake(row.raw_last_handshake),
          raw_last_handshake: row.raw_last_handshake || 0,
        };
      }
      setStatusMap(next);
    } catch {
      if (requestId !== statusRequestRef.current) return;
    }
  }, [inScopeRouterIds]);

  const refreshDashboardData = React.useCallback(async () => {
    const signature = scopeSignature;
    await Promise.all([
      loadPeers(signature),
      loadUsageMap(signature),
      loadChartData(signature),
      loadMetrics(),
      loadStatusMap(),
    ]);
  }, [scopeSignature, loadPeers, loadUsageMap, loadChartData, loadMetrics, loadStatusMap]);

  React.useEffect(() => {
    if (setupOk !== true || !settings || inScopeRouterIds.length === 0) return;
    void refreshDashboardData();
  }, [
    setupOk,
    settings ? 1 : 0,
    inScopeRouterIdsKey,
    scopeUnit,
    scopeValue,
    allTime,
    todayFrame,
    todayTick,
    rawStartIso,
    rawEndIso,
    refreshDashboardData,
  ]);

  React.useEffect(() => {
    if (setupOk !== true || !refreshSec || inScopeRouterIds.length === 0) return;
    const id = window.setInterval(() => {
      void refreshDashboardData();
    }, refreshSec * 1000);
    return () => window.clearInterval(id);
  }, [setupOk, refreshSec, inScopeRouterIdsKey, refreshDashboardData]);

  React.useEffect(() => {
    if (setupOk !== true || inScopeRouterIds.length === 0) {
      statusRequestRef.current += 1;
      setStatusMap({});
    }
  }, [setupOk, inScopeRouterIdsKey]);

  const defaultAddRouterId = React.useMemo(() => {
    if (activeRouterId && inScopeRouterIds.includes(activeRouterId)) return activeRouterId;
    if (inScopeRouterIds.length > 0) return inScopeRouterIds[0];
    if (routers.length > 0) return routers[0].id;
    return null;
  }, [activeRouterId, inScopeRouterIds, routers]);

  React.useEffect(() => {
    if (!showAdd) return;
    setAddErr("");
    setForm((prev) => ({
      ...prev,
      routerId: defaultAddRouterId,
      interface: prev.interface || "wgmik",
    }));
  }, [showAdd, defaultAddRouterId]);

  React.useEffect(() => {
    if (!showAdd || !form.routerId) {
      setInterfaceOptions([]);
      setInterfaceLoadFailed(false);
      return;
    }
    const routerId = form.routerId;
    let cancelled = false;
    (async () => {
      try {
        const rows = await listInterfaces(routerId);
        if (cancelled) return;
        setInterfaceOptions(rows);
        setInterfaceLoadFailed(false);
        setForm((prev) => ({
          ...prev,
          interface: rows.includes(prev.interface) ? prev.interface : rows[0] || prev.interface || "wgmik",
        }));
      } catch {
        if (cancelled) return;
        setInterfaceOptions([]);
        setInterfaceLoadFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [showAdd, form.routerId]);

  React.useEffect(() => {
    if (!showAdd || !form.routerId || !form.interface) return;
    const routerId = form.routerId;
    let cancelled = false;
    (async () => {
      try {
        const [routersList, ifaceCfg] = await Promise.all([
          Promise.resolve(routers),
          routerInterfaceDetail(routerId, form.interface),
        ]);
        if (cancelled) return;
        const router = routersList.find((row) => row.id === routerId);
        const endpointHost = ifaceCfg.public_host || router?.host || "";
        const endpointPort = ifaceCfg.listen_port || 51820;
        setForm((prev) => ({
          ...prev,
          serverPublicKey: ifaceCfg.public_key || "",
          endpoint: endpointHost && endpointPort ? `${endpointHost}:${endpointPort}` : prev.endpoint,
        }));
      } catch {
        // Manual fallback is fine.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [showAdd, form.routerId, form.interface, routers]);

  function clampX25519Secret(bytes: Uint8Array) {
    bytes[0] &= 248;
    bytes[31] &= 127;
    bytes[31] |= 64;
    return bytes;
  }

  function bytesToBase64(bytes: Uint8Array) {
    let bin = "";
    for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
    return btoa(bin);
  }

  function base64ToBytes(value: string) {
    const bin = atob(value);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  function generateKeypair() {
    const priv = clampX25519Secret(crypto.getRandomValues(new Uint8Array(32)));
    const pub = nacl.scalarMult.base(priv);
    setForm((prev) => ({ ...prev, privateKey: bytesToBase64(priv), publicKey: bytesToBase64(pub) }));
  }

  function generatePsk() {
    const p = crypto.getRandomValues(new Uint8Array(32));
    setForm((prev) => ({ ...prev, psk: bytesToBase64(p), usePsk: true }));
  }

  React.useEffect(() => {
    const key = (form.privateKey || "").trim();
    if (!key) {
      if (form.publicKey) setForm((prev) => ({ ...prev, publicKey: "" }));
      return;
    }
    try {
      const priv = base64ToBytes(key);
      if (priv.length === 32) {
        const pub = nacl.scalarMult.base(priv);
        const pubB64 = bytesToBase64(pub);
        if (form.publicKey !== pubB64) {
          setForm((prev) => ({ ...prev, publicKey: pubB64 }));
        }
      }
    } catch {
      // Invalid base64 is ignored until the user fixes it.
    }
  }, [form.privateKey, form.publicKey]);

  const qrConfig = React.useMemo(() => {
    if (!form.privateKey || !form.allowed) return "";
    const dns = form.dns.trim();
    const mtuNum = form.mtu.trim();
    const keepaliveNum = form.persistentKeepalive.trim();
    const allowedIps = form.allowedIps.trim();
    const lines = [
      "[Interface]",
      `PrivateKey = ${form.privateKey}`,
      `Address = ${form.allowed}`,
      ...(dns ? [`DNS = ${dns}`] : []),
      ...(() => {
        if (!mtuNum) return [];
        const n = Number(mtuNum);
        return Number.isFinite(n) && n > 0 ? [`MTU = ${Math.floor(n)}`] : [];
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
        return Number.isFinite(n) && n > 0 ? [`PersistentKeepalive = ${Math.floor(n)}`] : [];
      })(),
    ];
    return lines.join("\n");
  }, [form]);

  if (setupOk === null) {
    return (
      <div className="mx-auto px-4 md:px-6 py-6">
        <div className="text-sm text-gray-500 dark:text-gray-400">Loading...</div>
      </div>
    );
  }

  const globalSeries = scopeUnit === "days" ? monthly : raw;
  const globalTotals = globalSeries.reduce(
    (acc, point) => {
      acc.rx += point.rx || 0;
      acc.tx += point.tx || 0;
      return acc;
    },
    { rx: 0, tx: 0 },
  );

  return (
    <div className="mx-auto px-4 md:px-6 py-6">
      <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Overview</h1>

      <div className="mx-auto my-8 w-full max-w-[1120px] rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 p-6 grid gap-6">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Usage</div>
              <div className="text-xs text-gray-500 dark:text-gray-400">Combined traffic across all routers currently in scope.</div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {(["all", "active", "selected"] as RouterScope[]).map((scope) => (
                <button
                  key={scope}
                  type="button"
                  onClick={() => update({ dashboard_router_scope: scope })}
                  className={`rounded-full px-3 py-1.5 text-xs shadow ${
                    normalizedScopeState.scope === scope
                      ? "bg-gray-900 text-white dark:bg-gray-100 dark:text-gray-900"
                      : "bg-gray-100 text-gray-800 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                  }`}
                >
                  {scope === "all" ? "All routers" : scope === "active" ? "Active router" : "Selected routers"}
                </button>
              ))}
            </div>
          </div>

          {normalizedScopeState.scope === "selected" && (
            <div className="flex flex-wrap items-center gap-2">
              {routersByName.map((router) => {
                const selected = normalizedScopeState.selectedRouterIds.includes(router.id);
                return (
                  <button
                    key={router.id}
                    type="button"
                    onClick={() => {
                      const current = normalizedScopeState.selectedRouterIds;
                      if (selected) {
                        const next = current.filter((id) => id !== router.id);
                        if (next.length === 0) {
                          update({ dashboard_router_scope: "all", dashboard_selected_router_ids: [] });
                        } else {
                          update({ dashboard_selected_router_ids: next });
                        }
                      } else {
                        update({ dashboard_selected_router_ids: [...current, router.id] });
                      }
                    }}
                    className={`rounded-full px-3 py-1.5 text-xs shadow ${
                      selected
                        ? "bg-indigo-100 text-indigo-800 dark:bg-indigo-500/10 dark:text-indigo-300"
                        : "bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
                    }`}
                  >
                    {router.name}
                  </button>
                );
              })}
            </div>
          )}

          <div className="flex flex-col xl:flex-row xl:items-center xl:justify-between gap-3 text-xs text-gray-600 dark:text-gray-300">
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-2">
                <span>Auto refresh</span>
                <input
                  type="number"
                  min={5}
                  className="w-16 rounded-full border border-gray-900 bg-gray-900 text-white px-2 py-1 text-xs focus:ring-1 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                  {...dashRefreshInput}
                />
                <span>s</span>
              </div>
              <div className="flex items-center gap-2">
                <span>Last</span>
                <input
                  type="number"
                  min={1}
                  className="w-16 rounded-full border border-gray-900 bg-gray-900 text-white px-2 py-1 text-xs focus:ring-1 focus:ring-gray-400 dark:bg-gray-100 dark:text-gray-900 dark:border-gray-300"
                  {...dashScopeInput}
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
            <div className="flex flex-wrap items-center gap-2">
              <span>Time frame</span>
              <button
                type="button"
                onClick={() => {
                  if (todayFrame) {
                    const d = new Date();
                    d.setHours(0, 0, 0, 0);
                    setTimeFrom(formatDatetimeLocalValue(d));
                    setTimeTo(formatDatetimeLocalValue(new Date()));
                    update({ dashboard_time_frame_today: false });
                  } else {
                    setAllTime(false);
                    update({ dashboard_time_frame_today: true });
                  }
                }}
                className={`rounded-full px-3 py-1 text-xs border shadow ${
                  todayFrame
                    ? "border-gray-900 bg-gray-900 text-white dark:border-gray-100 dark:bg-gray-100 dark:text-gray-900"
                    : "border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950 hover:bg-gray-50 dark:hover:bg-gray-900"
                }`}
              >
                Today
              </button>
              <input
                type="datetime-local"
                value={displayTimeFrom}
                onChange={(e) => {
                  update({ dashboard_time_frame_today: false });
                  setTimeFrom(e.target.value);
                }}
                disabled={allTime || todayFrame}
                className="rounded-full border border-gray-200 dark:border-gray-800 px-2 py-1 text-xs focus:ring-1 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950 disabled:opacity-60"
              />
              <span>to</span>
              {todayFrame ? (
                <span
                  className="inline-flex items-center rounded-full border border-dashed border-gray-300 bg-gray-50 px-2.5 py-1 text-xs text-gray-600 dark:border-gray-600 dark:bg-gray-900/50 dark:text-gray-300"
                  title="End of range always matches the current time while Today is on"
                >
                  Now
                </span>
              ) : (
                <input
                  type="datetime-local"
                  value={timeTo}
                  onChange={(e) => {
                    update({ dashboard_time_frame_today: false });
                    setTimeTo(e.target.value);
                  }}
                  disabled={allTime}
                  className="rounded-full border border-gray-200 dark:border-gray-800 px-2 py-1 text-xs focus:ring-1 focus:ring-gray-300 dark:focus:ring-gray-700 bg-white dark:bg-gray-950 disabled:opacity-60"
                />
              )}
              {scopeUnit === "days" && (
                <label className="inline-flex items-center gap-2">
                  <input
                    type="checkbox"
                    checked={allTime}
                    onChange={(e) => {
                      const next = e.target.checked;
                      setAllTime(next);
                      update({ dashboard_time_frame_today: false });
                      if (next) {
                        setTimeFrom("");
                        setTimeTo("");
                      }
                    }}
                  />
                  <span>All time</span>
                </label>
              )}
              <button
                type="button"
                onClick={() => {
                  setAllTime(false);
                  update({ dashboard_time_frame_today: false });
                  setTimeFrom("");
                  setTimeTo("");
                }}
                disabled={!timeFrameActive}
                className="rounded-full border border-gray-200 dark:border-gray-800 px-3 py-1 text-xs bg-white dark:bg-gray-950 disabled:opacity-60"
              >
                Clear
              </button>
            </div>
          </div>

          <div className="h-56">
            <MemoUsageChart scopeUnit={scopeUnit} monthly={monthly} raw={raw} timezone={timezone} emptyLabel="No usage data yet" />
          </div>

          <div className="flex flex-wrap items-center gap-4 text-xs text-gray-500 dark:text-gray-400">
            <div>Total Download: <span className="font-medium text-gray-700 dark:text-gray-300">{fmtBytes(globalTotals.tx)}</span></div>
            <div>Total Upload: <span className="font-medium text-gray-700 dark:text-gray-300">{fmtBytes(globalTotals.rx)}</span></div>
            <div>Routers in scope: <span className="font-medium text-gray-700 dark:text-gray-300">{inScopeRouters.length}</span></div>
          </div>
        </div>

        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div className="flex flex-col sm:flex-row gap-3 w-full md:w-auto">
            <input
              className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700 w-full sm:w-64"
              placeholder="Search peers..."
              value={localFilterText}
              onChange={(e) => setLocalFilterText(e.target.value)}
            />
            <select
              className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
              value={filterStatus}
              onChange={(e) => update({ dashboard_filter_status: e.target.value })}
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
              onChange={(e) => update({ dashboard_sort_by: e.target.value })}
            >
              <option value="created">Recently Added</option>
              <option value="last_seen">Last Seen</option>
              <option value="usage">Total Usage</option>
              <option value="name">Name</option>
            </select>
          </div>
          <button
            onClick={() => {
              setAddErr("");
              setShowAdd(true);
            }}
            className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white whitespace-nowrap"
          >
            Add peer to router +
          </button>
        </div>
      </div>

      <div className="mx-auto w-full max-w-[1120px] grid gap-6">
        {filteredPeers.length === 0 ? (
          <div className="rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 p-6 text-sm text-gray-500 dark:text-gray-400">
            No peers in scope for the current router selection and filters.
          </div>
        ) : (
          routerSections.map((section) => (
            (() => {
              const canCollapsePeers = section.peers.length > ROUTER_PEER_PREVIEW_COUNT;
              const peersExpanded = canCollapsePeers ? !!expandedRouters[section.router.id] : true;
              const visiblePeers = peersExpanded ? section.peers : section.peers.slice(0, ROUTER_PEER_PREVIEW_COUNT);
              const hiddenPeerCount = Math.max(0, section.peers.length - visiblePeers.length);

              return (
                <div key={section.router.id} className="rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 p-6 grid gap-5">
                  <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
                    <div>
                      <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">{section.router.name}</div>
                      <div className="text-xs text-gray-500 dark:text-gray-400">
                        {section.router.proto.toUpperCase()} · {section.router.host}:{section.router.port} · {section.router.username}
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-3 text-xs text-gray-600 dark:text-gray-300">
                      <span>Peers <span className="font-medium text-gray-800 dark:text-gray-100">{section.peers.length}</span></span>
                      <span>Online <span className="font-medium text-gray-800 dark:text-gray-100">{section.online}</span></span>
                      <span>Disabled <span className="font-medium text-gray-800 dark:text-gray-100">{section.disabled}</span></span>
                      <span>Down <span className="font-medium text-gray-800 dark:text-gray-100">{fmtBytes(section.totals.tx)}</span></span>
                      <span>Up <span className="font-medium text-gray-800 dark:text-gray-100">{fmtBytes(section.totals.rx)}</span></span>
                    </div>
                  </div>

                  <LazySectionBody placeholderHeight={section.peers.length > 0 ? 420 : 220}>
                    <div className="grid gap-5">
                      <div className="h-52">
                        <MemoUsageChart
                          scopeUnit={scopeUnit}
                          monthly={section.monthly}
                          raw={section.raw}
                          timezone={timezone}
                          emptyLabel="No usage data for this router in the current timeframe"
                        />
                      </div>

                      {section.peers.length === 0 ? (
                        <div className="text-sm text-gray-500 dark:text-gray-400">No matching peers on this router.</div>
                      ) : (
                        <div className="grid gap-4">
                          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
                            {visiblePeers.map((peer) => {
                              const usage = peerUsageMap[peer.id] || { rx: 0, tx: 0 };
                              const status = statusMap[peer.id];
                              return (
                                <Card
                                  key={peer.id}
                                  className="p-4 ring-gray-300 shadow hover:shadow-lg hover:-translate-y-0.5 cursor-pointer rounded-xl"
                                  onClick={() => navigate(`/peer/${peer.id}`)}
                                >
                                  <div className="flex items-center justify-between gap-3">
                                    <div className="min-w-0">
                                      <div className="text-sm text-gray-500 dark:text-gray-400">{peer.name}</div>
                                      <div className="text-lg text-gray-900 dark:text-gray-100 truncate" title={(peer.allowed_address || "").replace(/\/32/g, "")}>
                                        {(peer.allowed_address || "").replace(/\/32/g, "")}
                                      </div>
                                      <div className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">
                                        {routersById[peer.router_id]?.name || `Router #${peer.router_id}`} · {peer.interface}
                                      </div>
                                    </div>
                                    <div className="flex flex-col items-end gap-2">
                                      <div className="flex items-center gap-1.5 text-xs text-gray-500 dark:text-gray-400">
                                        {fairUsageByPeer[peer.id] ? (
                                          <span
                                            className="shrink-0"
                                            title={
                                              fairUsageThrottledByPeer[peer.id]
                                                ? "Fair usage: throttled"
                                                : "Fair usage limit applies (not throttled)"
                                            }
                                          >
                                            <FairUsageShieldIcon throttled={fairUsageThrottledByPeer[peer.id]} />
                                          </span>
                                        ) : null}
                                        <span title="Usage in selected timeframe">↓ {fmtBytes(usage.tx)} · ↑ {fmtBytes(usage.rx)}</span>
                                      </div>
                                      <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${peer.disabled ? "bg-rose-100 text-rose-800" : "bg-indigo-100 text-indigo-800"}`}>
                                        <span className={`inline-block w-2 h-2 rounded-full ${peer.disabled ? "bg-rose-500" : "bg-indigo-500"}`} />
                                        {peer.disabled ? "Deactivated" : "Active"}
                                      </span>
                                      {showKindPills && (() => {
                                        const addr = peer.allowed_address.trim();
                                        const outbound = addr === "0.0.0.0/0" || addr === "::/0";
                                        return (
                                          <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${outbound ? "bg-amber-100 text-amber-800" : "bg-blue-100 text-blue-800"}`}>
                                            <span className={`inline-block w-2 h-2 rounded-full ${outbound ? "bg-amber-500" : "bg-blue-500"}`} />
                                            {outbound ? "Outbound" : "Inbound"}
                                          </span>
                                        );
                                      })()}
                                      <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${
                                        status
                                          ? (status.online ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-800 dark:bg-gray-800 dark:text-gray-200")
                                          : "bg-amber-100 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300"
                                      }`}>
                                        <span className={`inline-block w-2 h-2 rounded-full ${
                                          status
                                            ? (status.online ? "bg-green-500" : "bg-gray-400 dark:bg-gray-500")
                                            : "bg-amber-500"
                                        }`} />
                                        {status ? (status.online ? "Online" : `Last seen ${status.last}`) : "Status unavailable"}
                                      </span>
                                    </div>
                                  </div>
                                </Card>
                              );
                            })}
                          </div>

                          {canCollapsePeers && (
                            <div className="flex justify-center pt-1">
                                <button
                                  type="button"
                                  onClick={() => toggleRouterExpanded(section.router.id)}
                                  className="group inline-flex items-center gap-2 rounded-full border border-gray-200/80 bg-gray-50/90 px-4 py-2 text-sm font-medium text-gray-700 shadow-sm backdrop-blur transition hover:-translate-y-0.5 hover:bg-white hover:shadow-md dark:border-gray-700 dark:bg-gray-800/90 dark:text-gray-200 dark:hover:bg-gray-800"
                                >
                                  <span>{peersExpanded ? "Show less" : `Show ${hiddenPeerCount} more peer${hiddenPeerCount === 1 ? "" : "s"}`}</span>
                                  <svg
                                    viewBox="0 0 20 20"
                                    fill="none"
                                    className={`h-4 w-4 transition-transform ${peersExpanded ? "rotate-180" : ""}`}
                                    aria-hidden="true"
                                  >
                                    <path
                                      d="M5 7.5 10 12.5 15 7.5"
                                      stroke="currentColor"
                                      strokeWidth="1.8"
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                    />
                                  </svg>
                                </button>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  </LazySectionBody>
                </div>
              );
            })()
          ))
        )}
      </div>

      {fabOpen && <div className="fixed inset-0 z-40" onClick={() => setFabOpen(false)} />}
      <div className="fixed left-4 bottom-4 z-50 flex flex-col-reverse items-start gap-2">
        {showHwStats && (
          <div className="rounded-full bg-white/95 text-[11px] text-gray-800 px-3 py-1 shadow ring-1 ring-gray-200 dark:bg-gray-900/95 dark:text-gray-200 dark:ring-gray-800">
            {metrics ? `CPU ${metrics.cpu_percent != null ? Math.round(metrics.cpu_percent) : "-"}% · Mem ${metrics.mem_percent != null ? Math.round(metrics.mem_percent) : "-"}%` : "CPU/Mem: ..."}
          </div>
        )}
        <button
          onClick={() => setFabOpen(o => !o)}
          className={`h-12 w-12 rounded-full bg-white text-gray-900 ring-1 ring-gray-300 shadow flex items-center justify-center hover:shadow-md dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-700 transition-transform duration-200 ${fabOpen ? "rotate-45" : ""}`}
        >
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
        </button>

        <div className={`flex flex-col gap-2 transition-all duration-200 origin-bottom ${fabOpen ? "scale-100 opacity-100" : "scale-75 opacity-0 pointer-events-none"}`}>
          <a href="/settings" className="h-10 w-10 rounded-full bg-white text-gray-900 ring-1 ring-gray-300 shadow flex items-center justify-center hover:shadow-md dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-700 transition-all duration-150" title="Settings" style={{ transitionDelay: fabOpen ? "50ms" : "0ms" }}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9c0 .66.26 1.3.73 1.77.47.47 1.11.73 1.77.73H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </a>
          <a href="/fair-usage" className="h-10 w-10 rounded-full bg-white text-gray-900 ring-1 ring-gray-300 shadow flex items-center justify-center hover:shadow-md dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-700 transition-all duration-150" title="Fair Usage" style={{ transitionDelay: fabOpen ? "100ms" : "0ms" }}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
          </a>
          <a href="/telegram" className="h-10 w-10 rounded-full bg-white text-gray-900 ring-1 ring-gray-300 shadow flex items-center justify-center hover:shadow-md dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-700 transition-all duration-150" title="Telegram Bot" style={{ transitionDelay: fabOpen ? "150ms" : "0ms" }}>
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 2L11 13" /><path d="M22 2L15 22L11 13L2 9L22 2Z" />
            </svg>
          </a>
        </div>
      </div>

      {showAdd && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 p-4">
          <div className="w-full max-w-[960px] max-h-[90vh] overflow-y-auto bg-white dark:bg-gray-900 rounded-3xl ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg grid md:grid-cols-2 gap-6 p-6 relative">
            <button
              onClick={() => setShowAdd(false)}
              className="absolute top-3 right-3 h-8 w-8 rounded-full bg-gray-100 text-gray-800 flex items-center justify-center hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
              title="Close"
            >
              x
            </button>
            <div className="grid gap-4">
              <div className="text-lg font-semibold text-gray-900 dark:text-gray-100">Add peer to router</div>

              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">Router</label>
                <select
                  value={form.routerId ?? ""}
                  onChange={(e) => setForm((prev) => ({ ...prev, routerId: Number(e.target.value) || null }))}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                >
                  <option value="">Select router</option>
                  {routersByName.map((router) => (
                    <option key={router.id} value={router.id}>
                      {router.name} ({router.host})
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">Interface</label>
                {interfaceOptions.length > 0 && !interfaceLoadFailed ? (
                  <select
                    value={form.interface}
                    onChange={(e) => setForm((prev) => ({ ...prev, interface: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                  >
                    {interfaceOptions.map((iface) => (
                      <option key={iface} value={iface}>
                        {iface}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={form.interface}
                    onChange={(e) => setForm((prev) => ({ ...prev, interface: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="wgmik"
                  />
                )}
                {interfaceLoadFailed && <div className="text-[11px] text-gray-500 dark:text-gray-400">Router interfaces could not be fetched. Manual interface entry is enabled.</div>}
              </div>

              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">Name</label>
                <input
                  value={form.name}
                  onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                  placeholder="alice"
                />
              </div>

              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">Allowed address (inbound)</label>
                <input
                  value={form.allowed}
                  onChange={(e) => setForm((prev) => ({ ...prev, allowed: e.target.value }))}
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
                  onChange={(e) => setForm((prev) => ({ ...prev, privateKey: e.target.value, publicKey: "" }))}
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
                  onChange={(e) => setForm((prev) => ({ ...prev, psk: e.target.value, usePsk: !!e.target.value }))}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                  placeholder="PresharedKey (base64)"
                />
              </div>

              <div className="flex items-center gap-3 pt-2">
                <button
                  disabled={addBusy || !form.routerId || !form.name || !form.publicKey || !form.allowed || form.allowed.trim() === "0.0.0.0/0" || form.allowed.trim() === "::/0"}
                  onClick={async () => {
                    setAddErr("");
                    try {
                      if (!form.routerId) throw new Error("Select a router first.");
                      setAddBusy(true);
                      await createRouterPeer(form.routerId, {
                        interface: form.interface || "wgmik",
                        name: form.name.trim(),
                        public_key: form.publicKey,
                        private_key: form.privateKey.trim(),
                        allowed_address: form.allowed.trim(),
                        comment: "",
                      });
                      setShowAdd(false);
                      await refreshDashboardData();
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
              <div className="text-xs text-gray-500 dark:text-gray-400">Save creates the peer on the selected router and stores the client private key encrypted so you can see it later.</div>
            </div>

            <div className="grid gap-3">
              <div className="text-sm text-gray-700 dark:text-gray-200">Client config (QR)</div>
              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">Server public key</label>
                <input
                  value={form.serverPublicKey}
                  onChange={(e) => setForm((prev) => ({ ...prev, serverPublicKey: e.target.value }))}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                  placeholder="Base64"
                />
              </div>
              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">Endpoint</label>
                <input
                  value={form.endpoint}
                  onChange={(e) => setForm((prev) => ({ ...prev, endpoint: e.target.value }))}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                  placeholder="host:port"
                />
              </div>
              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">DNS</label>
                <input
                  value={form.dns}
                  onChange={(e) => setForm((prev) => ({ ...prev, dns: e.target.value }))}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                  placeholder="8.8.8.8, 1.1.1.1"
                />
              </div>
              <div className="grid md:grid-cols-2 gap-3">
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500 dark:text-gray-400">MTU</label>
                  <input
                    type="number"
                    inputMode="numeric"
                    value={form.mtu}
                    onChange={(e) => setForm((prev) => ({ ...prev, mtu: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="1280"
                  />
                </div>
                <div className="grid gap-2">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Persistent keepalive (s)</label>
                  <input
                    type="number"
                    inputMode="numeric"
                    value={form.persistentKeepalive}
                    onChange={(e) => setForm((prev) => ({ ...prev, persistentKeepalive: e.target.value }))}
                    className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="25"
                  />
                </div>
              </div>
              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">AllowedIPs</label>
                <input
                  value={form.allowedIps}
                  onChange={(e) => setForm((prev) => ({ ...prev, allowedIps: e.target.value }))}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                  placeholder="0.0.0.0/0, ::/0"
                />
              </div>
              <div className="rounded-2xl bg-gray-50 dark:bg-gray-950 ring-1 ring-gray-200 dark:ring-gray-800 p-4 min-h-[320px] flex items-center justify-center">
                {qrConfig ? <QRCode value={qrConfig} size={256} /> : <div className="text-sm text-gray-500 dark:text-gray-400">Generate or fill required fields to preview QR</div>}
              </div>
              <textarea
                readOnly
                value={qrConfig}
                className="min-h-[180px] rounded-2xl border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 px-3 py-2 text-xs font-mono text-gray-800 dark:text-gray-100"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
