import React from "react";
import { useNavigate } from "react-router-dom";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import QRCode from "react-qr-code";
import nacl from "tweetnacl";
import { useAutoSaveSettings } from "../useAutoSaveSettings";
import UsageTimeControls, { normalizeUsageTimeMode } from "../UsageTimeControls";
import {
  formatCalendarDateTime,
  formatCalendarDayLabel,
  startOfLocalTodayValue,
  startOfSelectedCalendarMonthValue,
  zonedWallTimeValueToUtcIso,
} from "../datetimeLocal";
import {
  createRouterPeer,
  getDashboardLiveStatus,
  getMetrics,
  getMonthlySummary,
  getMonthlySummaryByRouter,
  getPeersSummary,
  getSummaryRaw,
  getSummaryRawByRouter,
  listInterfaces,
  listPeers,
  listRouters,
  routerPeers,
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
type RouterScope = "all" | "selected";

type PeerStatus = { online: boolean; last: string; raw_last_handshake: number };
type UsageTotals = { rx: number; tx: number };

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

function RouterSyncWarningBadge({ status }: { status?: string }) {
  if (!status || status === "synced") return null;
  const label = status === "missing" ? "Missing on RouterOS" : "New on RouterOS";
  return (
    <span
      className="absolute right-3 top-3 z-10 inline-flex h-6 w-6 items-center justify-center rounded-full bg-rose-600 shadow ring-2 ring-white dark:ring-gray-900"
      title={label}
      aria-label={label}
    >
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" className="h-3.5 w-3.5 text-white" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d="M12 8v5" />
        <path d="M12 17h.01" />
        <path d="M10.2 3.4 2.7 18a2 2 0 0 0 1.8 2.9h15a2 2 0 0 0 1.8-2.9L13.8 3.4a2 2 0 0 0-3.6 0Z" />
      </svg>
    </span>
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

function initialAddPeerForm(routerId: number | null) {
  return {
    routerId,
    interface: "wgmik",
    name: "",
    allowed: "",
    privateKey: "",
    publicKey: "",
    serverPublicKey: "",
    endpointHost: "",
    endpointPort: "51820",
    configName: "",
    customEndpoint: "",
    dns: "8.8.8.8, 1.1.1.1",
    mtu: "1280",
    persistentKeepalive: "25",
    allowedIps: "0.0.0.0/0, ::/0",
    presharedKey: "",
  };
}

function resolveClientEndpoint(customEndpoint: string, endpointHost: string, endpointPort: string | number) {
  const port = Number(endpointPort) || 51820;
  const customEp = customEndpoint.trim();
  const defaultHost = endpointHost.trim();
  const defaultEndpoint = defaultHost ? `${defaultHost}:${port}` : "HOST:PORT";
  if (!customEp) return defaultEndpoint;
  if (customEp.startsWith("[") && customEp.includes("]")) {
    return customEp.includes("]:") ? customEp : `${customEp}:${port}`;
  }
  if (/:[0-9]{1,5}$/.test(customEp)) return customEp;
  if (customEp.includes(":")) return `[${customEp}]:${port}`;
  return `${customEp}:${port}`;
}

function isValidWgBase64Key(value: string) {
  const key = value.trim();
  if (!key) return false;
  try {
    return atob(key).length === 32;
  } catch {
    return false;
  }
}

function sanitizeConfigFileBase(name: string, fallback: string) {
  const raw = name.trim() || fallback.trim() || "wg-peer";
  const safe = raw.replace(/[/\\?%*:|"<>]/g, "_").replace(/\s+/g, "_").slice(0, 120);
  return safe || "wg-peer";
}

function ipv4ToBigInt(value: string): bigint | null {
  const parts = value.trim().split(".");
  if (parts.length !== 4) return null;
  let out = 0n;
  for (const part of parts) {
    if (!/^\d+$/.test(part)) return null;
    const n = Number(part);
    if (!Number.isInteger(n) || n < 0 || n > 255) return null;
    out = (out << 8n) + BigInt(n);
  }
  return out;
}

function bigIntToIpv4(value: bigint) {
  return [24n, 16n, 8n, 0n].map((shift) => Number((value >> shift) & 255n)).join(".");
}

function firstAddressPart(value: string) {
  return value.split(",")[0]?.trim() || "";
}

function findAvailableInterfaceAddress(interfaceAddresses: string[] = [], peersForInterface: Array<{ allowed_address?: string }>) {
  const used = new Set<bigint>();
  for (const peer of peersForInterface) {
    const first = firstAddressPart(peer.allowed_address || "");
    const ip = ipv4ToBigInt(first.split("/")[0] || "");
    if (ip !== null) used.add(ip);
  }

  for (const addr of interfaceAddresses) {
    const [ipText, prefixText] = addr.trim().split("/");
    const routerIp = ipv4ToBigInt(ipText || "");
    const prefix = Number(prefixText);
    if (routerIp === null || !Number.isInteger(prefix) || prefix < 1 || prefix > 30) continue;

    const hostBits = 32 - prefix;
    const size = 1n << BigInt(hostBits);
    const mask = (0xffffffffn << BigInt(hostBits)) & 0xffffffffn;
    const network = routerIp & mask;
    const firstHost = network + 1n;
    const lastHost = network + size - 2n;
    if (firstHost > lastHost) continue;

    const scan = (start: bigint, end: bigint): bigint | null => {
      for (let candidate = start; candidate <= end; candidate += 1n) {
        if (candidate === routerIp || used.has(candidate)) continue;
        return candidate;
      }
      return null;
    };

    const afterRouter = routerIp < lastHost ? scan(routerIp + 1n, lastHost) : null;
    const wrapped = afterRouter ?? scan(firstHost, routerIp > firstHost ? routerIp - 1n : lastHost);
    if (wrapped !== null) return `${bigIntToIpv4(wrapped)}/32`;
  }
  return "";
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
  dateCalendar,
  emptyLabel,
}: {
  scopeUnit: ScopeUnit;
  monthly: MonthlySummaryPoint[];
  raw: SummaryRawPoint[];
  timezone: string;
  dateCalendar: string;
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
                return formatCalendarDayLabel(val, { timeZone: timezone || "UTC", dateCalendar });
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
                return formatCalendarDayLabel(String(label), { timeZone: timezone || "UTC", dateCalendar, long: true });
              }
              return formatCalendarDateTime(new Date(label), { timeZone: timezone || "UTC", dateCalendar, includeTime: true });
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
  const [todayTick, setTodayTick] = React.useState(0);
  const [localFilterText, setLocalFilterText] = React.useState("");
  const [expandedRouters, setExpandedRouters] = React.useState<Record<number, boolean>>({});
  const [showHiddenPeersByRouter, setShowHiddenPeersByRouter] = React.useState<Record<number, boolean>>({});
  const [routerLoadError, setRouterLoadError] = React.useState("");

  const [showAdd, setShowAdd] = React.useState(false);
  const [fabOpen, setFabOpen] = React.useState(false);
  const [addBusy, setAddBusy] = React.useState(false);
  const [addErr, setAddErr] = React.useState("");
  const [interfaceOptions, setInterfaceOptions] = React.useState<string[]>([]);
  const [interfaceLoadFailed, setInterfaceLoadFailed] = React.useState(false);
  const [lastAddRouterId, setLastAddRouterId] = React.useState<number | null>(null);
  const [form, setForm] = React.useState(() => initialAddPeerForm(null));
  const [showAddPrivateKey, setShowAddPrivateKey] = React.useState(false);
  const [showAddPresharedKey, setShowAddPresharedKey] = React.useState(false);

  const refreshSec = Math.max(5, Number(settings?.poll_interval_seconds) || 30);
  const dashboardPeerPreviewCount = Math.max(1, Math.min(50, Number(settings?.dashboard_peer_preview_count) || 6));
  const scopeValue = Math.max(1, Number(settings?.dashboard_scope_value) || 24);
  const scopeUnit: ScopeUnit = settings?.dashboard_scope_unit === "minutes" || settings?.dashboard_scope_unit === "days"
    ? settings.dashboard_scope_unit
    : "hours";
  const timezone = settings?.timezone ?? "UTC";
  const dateCalendar = settings?.date_calendar ?? "gregorian";
  const weekStartDay = Number(settings?.week_start_day ?? 0);
  const timeMode = normalizeUsageTimeMode(
    settings?.dashboard_time_mode ?? (settings?.dashboard_time_frame_today ? "today" : "rolling"),
  );
  const customStart = settings?.dashboard_custom_start ?? "";
  const customEnd = settings?.dashboard_custom_end ?? "";
  const showKindPills = settings?.show_kind_pills ?? true;
  const showHwStats = settings?.show_hw_stats ?? true;
  const filterStatus = FILTER_STATUS_VALUES.has(settings?.dashboard_filter_status ?? "")
    ? (settings?.dashboard_filter_status as "all" | "online" | "offline" | "enabled" | "disabled")
    : "all";
  const sortBy = (settings?.dashboard_sort_by as "name" | "last_seen" | "created" | "usage") ?? "created";
  const rawScope = settings?.dashboard_router_scope === "selected" ? "selected" : "all";
  const rawSelectedRouterIdsSource = settings?.dashboard_selected_router_ids;
  const rawSelectedRouterIds = React.useMemo(
    () => normalizeRouterIds(rawSelectedRouterIdsSource),
    [rawSelectedRouterIdsSource],
  );
  const customRangeMs = React.useMemo(() => {
    if (timeMode !== "custom" || !customStart || !customEnd) return 0;
    const startMs = new Date(customStart).getTime();
    const endMs = new Date(customEnd).getTime();
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return 0;
    return endMs - startMs;
  }, [customEnd, customStart, timeMode]);
  const chartScopeUnit: ScopeUnit = timeMode === "today"
    ? "hours"
    : timeMode === "this_month" || timeMode === "all_time"
      ? "days"
      : timeMode === "custom"
        ? customRangeMs > 3 * 24 * 3600 * 1000
          ? "days"
          : "hours"
        : scopeUnit;
  const effectiveAllTime = timeMode === "all_time";

  const toIso = React.useCallback((value: string) => {
    return zonedWallTimeValueToUtcIso(value, timezone);
  }, [timezone]);
  const customStartIso = toIso(customStart);
  const customEndIso = toIso(customEnd);

  const effectiveStartIso = React.useMemo(() => {
    if (timeMode === "today") return toIso(startOfLocalTodayValue(timezone));
    if (timeMode === "this_month") return toIso(startOfSelectedCalendarMonthValue(dateCalendar, timezone));
    if (timeMode === "custom") return customStartIso;
    return undefined;
  }, [customStartIso, dateCalendar, timeMode, timezone, toIso, todayTick]);

  const effectiveEndIso = React.useMemo(() => {
    if (timeMode === "today" || timeMode === "this_month") return new Date().toISOString();
    if (timeMode === "custom") return customEndIso;
    return undefined;
  }, [customEndIso, timeMode, todayTick]);

  React.useEffect(() => {
    if (timeMode !== "today" && timeMode !== "this_month") return;
    setTodayTick((t) => t + 1);
    const ms = Math.max(5000, refreshSec * 1000);
    const id = window.setInterval(() => setTodayTick((t) => t + 1), ms);
    return () => window.clearInterval(id);
  }, [timeMode, refreshSec]);

  const loadRouters = React.useCallback(async () => {
    try {
      setRouterLoadError("");
      const routerRows = await listRouters();
      setRouters(routerRows);
      setSetupOk(routerRows.length > 0);
    } catch (e: any) {
      setRouters([]);
      setSetupOk(false);
      setRouterLoadError(e?.message || "Failed to load router profiles.");
    }
  }, []);

  React.useEffect(() => {
    void loadRouters();
  }, [loadRouters]);

  const routerIdsSet = React.useMemo(() => new Set(routers.map((router) => router.id)), [routers]);

  const normalizedScopeState = React.useMemo(() => {
    const validSelected = rawSelectedRouterIds.filter((id) => routerIdsSet.has(id));
    let scope: RouterScope = rawScope === "selected" || rawScope === "all" ? rawScope : "all";
    if (scope === "selected" && validSelected.length === 0) scope = "all";
    return { scope, selectedRouterIds: validSelected };
  }, [rawScope, rawSelectedRouterIds, routerIdsSet]);

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

  const enabledRoutersByName = React.useMemo(
    () => routersByName.filter((r) => r.enabled !== false),
    [routersByName],
  );

  const inScopeRouters = React.useMemo(() => {
    const isEnabled = (r: Router | undefined): r is Router => !!r && r.enabled !== false;
    if (normalizedScopeState.scope === "selected") {
      return normalizedScopeState.selectedRouterIds
        .map((id) => routersById[id])
        .filter(isEnabled);
    }
    return enabledRoutersByName;
  }, [normalizedScopeState, routersById, enabledRoutersByName]);

  const inScopeRouterIds = React.useMemo(() => inScopeRouters.map((router) => router.id), [inScopeRouters]);
  const inScopeRouterIdsKey = React.useMemo(() => inScopeRouterIds.join(","), [inScopeRouterIds]);
  const singleInScopeRouterId = inScopeRouterIds.length === 1 ? inScopeRouterIds[0] : null;
  const showScopeOverviewChart = inScopeRouters.length > 1;
  const scopeSignature = React.useMemo(
    () => JSON.stringify({
      routerIds: inScopeRouterIds,
      scopeUnit: chartScopeUnit,
      scopeValue,
      timeMode,
      todayTick,
      startIso: effectiveStartIso || "",
      endIso: effectiveEndIso || "",
    }),
    [inScopeRouterIds, chartScopeUnit, scopeValue, timeMode, todayTick, effectiveStartIso, effectiveEndIso],
  );
  const scopeSignatureRef = React.useRef(scopeSignature);
  const statusScopeRef = React.useRef(inScopeRouterIdsKey);
  const rangeSeconds = React.useMemo(() => {
    if (chartScopeUnit === "days") return null;
    const baseSeconds = chartScopeUnit === "minutes" ? Math.max(1, scopeValue) * 60 : Math.max(1, scopeValue) * 3600;
    if (timeMode === "today") return 24 * 3600;
    if (!effectiveStartIso) return baseSeconds;
    const startMs = new Date(effectiveStartIso).getTime();
    const endMs = new Date(effectiveEndIso || new Date().toISOString()).getTime();
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || endMs <= startMs) return baseSeconds;
    return Math.max(60, Math.floor((endMs - startMs) / 1000));
  }, [chartScopeUnit, scopeValue, timeMode, effectiveStartIso, effectiveEndIso]);

  React.useEffect(() => {
    scopeSignatureRef.current = scopeSignature;
  }, [scopeSignature]);

  React.useEffect(() => {
    statusScopeRef.current = inScopeRouterIdsKey;
  }, [inScopeRouterIdsKey]);

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
        const routerAllPeers = filteredPeers.filter((peer) => peer.router_id === router.id);
        const activePeers = routerAllPeers.filter((peer) => peer.selected !== false);
        const pendingNewPeers = routerAllPeers.filter((peer) => peer.selected === false && peer.router_sync_status === "new");
        const hiddenPeers = routerAllPeers.filter((peer) => peer.selected === false && peer.router_sync_status !== "new");
        const showHiddenPeers = !!showHiddenPeersByRouter[router.id];
        const baseRouterPeers = showHiddenPeers ? [...activePeers, ...pendingNewPeers, ...hiddenPeers] : [...activePeers, ...pendingNewPeers];
        // Float peers with a router-sync warning (missing / new drift) to the top so the
        // red caution overlay is always visible, even when the list is collapsed. Array.sort
        // is stable, so the existing relative order is preserved within each group.
        const hasSyncWarning = (peer: SavedPeer) => (peer.router_sync_status || "synced") !== "synced";
        const routerPeers = [...baseRouterPeers].sort(
          (a, b) => Number(hasSyncWarning(b)) - Number(hasSyncWarning(a)),
        );
        const totals = activePeers.reduce(
          (acc, peer) => {
            const usage = peerUsageMap[peer.id] || { rx: 0, tx: 0 };
            acc.rx += usage.rx;
            acc.tx += usage.tx;
            return acc;
          },
          { rx: 0, tx: 0 },
        );
        const online = activePeers.filter((peer) => statusMap[peer.id]?.online).length;
        const disabled = activePeers.filter((peer) => peer.disabled).length;
        const monthlyRows = routerMonthlyMap[router.id] || [];
        const rawRows = routerRawMap[router.id] || [];
        return {
          router,
          peers: routerPeers,
          totals,
          online,
          disabled,
          hidden: hiddenPeers.length,
          showHiddenPeers,
          monthly: monthlyRows,
          raw: rawRows,
        };
      })
      .filter((section) => !hasPeerFilters || section.peers.length > 0);
  }, [inScopeRouters, filteredPeers, peerUsageMap, statusMap, routerMonthlyMap, routerRawMap, hasPeerFilters, showHiddenPeersByRouter]);

  const toggleRouterExpanded = React.useCallback((routerId: number) => {
    setExpandedRouters((current) => ({ ...current, [routerId]: !current[routerId] }));
  }, []);

  const toggleRouterHiddenPeers = React.useCallback((routerId: number) => {
    setShowHiddenPeersByRouter((current) => ({ ...current, [routerId]: !current[routerId] }));
  }, []);

  const loadPeers = React.useCallback(async (signature: string) => {
    try {
      const rows = await listPeers(0, false, undefined, inScopeRouterIds);
      if (signature !== scopeSignatureRef.current) return;
      setPeers(rows);
    } catch {
      if (signature !== scopeSignatureRef.current) return;
      setPeers([]);
    }
  }, [inScopeRouterIds]);

  const loadChartData = React.useCallback(async (signature: string) => {
    try {
      if (chartScopeUnit === "days") {
        if (singleInScopeRouterId) {
          let aggregateRows: MonthlySummaryPoint[] = [];
          if (effectiveAllTime) {
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
          if (effectiveAllTime) {
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
      const interval = chartScopeUnit === "minutes" ? 60 : 3600;
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
      if (chartScopeUnit === "days") {
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
  }, [chartScopeUnit, singleInScopeRouterId, effectiveAllTime, effectiveStartIso, effectiveEndIso, scopeValue, inScopeRouterIds, rangeSeconds]);

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
      if (chartScopeUnit === "days") {
        if (effectiveAllTime) {
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
  }, [chartScopeUnit, scopeValue, effectiveAllTime, effectiveStartIso, effectiveEndIso, inScopeRouterIds, rangeSeconds]);

  const loadMetrics = React.useCallback(async () => {
    try {
      const next = await getMetrics();
      setMetrics(next);
    } catch {
      setMetrics(null);
    }
  }, []);

  const loadStatusMap = React.useCallback(async () => {
    const scopeKey = inScopeRouterIdsKey;
    if (inScopeRouterIds.length === 0) {
      setStatusMap({});
      return;
    }
    try {
      const rows = await getDashboardLiveStatus(undefined, inScopeRouterIds);
      // Only discard if the router scope changed while this request was in flight.
      // Overlapping polls in the same scope are intentionally merged so a batched response
      // delayed by one unreachable router still lands in state on the next poll.
      if (scopeKey !== statusScopeRef.current) return;
      setStatusMap((prev) => {
        if (scopeKey !== statusScopeRef.current) return prev;
        const merged: Record<number, PeerStatus> = { ...prev };
        for (const row of rows) {
          merged[row.peer_id] = {
            online: row.online,
            last: formatRelativeHandshake(row.raw_last_handshake),
            raw_last_handshake: row.raw_last_handshake || 0,
          };
        }
        return merged;
      });
    } catch {
      // Network error; keep previously-known status.
    }
  }, [inScopeRouterIds, inScopeRouterIdsKey]);

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

  const refreshDashboardDataRef = React.useRef(refreshDashboardData);
  React.useEffect(() => {
    refreshDashboardDataRef.current = refreshDashboardData;
  }, [refreshDashboardData]);

  React.useEffect(() => {
    if (setupOk !== true || !settings || inScopeRouterIds.length === 0) return;
    void refreshDashboardDataRef.current();
  }, [
    setupOk,
    settings ? 1 : 0,
    inScopeRouterIdsKey,
    chartScopeUnit,
    scopeValue,
    timeMode,
    todayTick,
    effectiveStartIso,
    effectiveEndIso,
  ]);

  React.useEffect(() => {
    if (setupOk !== true || !refreshSec || inScopeRouterIds.length === 0) return;
    const id = window.setInterval(() => {
      void refreshDashboardDataRef.current();
    }, refreshSec * 1000);
    return () => window.clearInterval(id);
  }, [setupOk, refreshSec, inScopeRouterIdsKey]);

  React.useEffect(() => {
    if (setupOk !== true || inScopeRouterIds.length === 0) {
      setStatusMap({});
    }
  }, [setupOk, inScopeRouterIdsKey]);

  const defaultAddRouterId = React.useMemo(() => {
    const preferredIds = inScopeRouterIds.length > 0
      ? inScopeRouterIds
      : enabledRoutersByName.length > 0
        ? enabledRoutersByName.map((router) => router.id)
        : routersByName.map((router) => router.id);
    if (lastAddRouterId && preferredIds.includes(lastAddRouterId)) return lastAddRouterId;
    return preferredIds[0] ?? null;
  }, [inScopeRouterIds, enabledRoutersByName, routersByName, lastAddRouterId]);

  React.useEffect(() => {
    if (!showAdd) return;
    setAddErr("");
    setShowAddPrivateKey(false);
    setShowAddPresharedKey(false);
    setForm(initialAddPeerForm(defaultAddRouterId));
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
        const [routersList, ifaceCfg, livePeers] = await Promise.all([
          Promise.resolve(routers),
          routerInterfaceDetail(routerId, form.interface),
          routerPeers(routerId, form.interface).catch(() => []),
        ]);
        if (cancelled) return;
        const router = routersList.find((row) => row.id === routerId);
        const endpointHost = (ifaceCfg.public_host || router?.host || "").trim();
        const endpointPort = String(ifaceCfg.listen_port || 51820);
        const suggestedAddress = findAvailableInterfaceAddress(ifaceCfg.addresses || [], livePeers);
        setForm((prev) => ({
          ...prev,
          serverPublicKey: ifaceCfg.public_key || "",
          endpointHost,
          endpointPort,
          allowed: !prev.allowed.trim() || prev.allowed.trim() === "10.65.74.100/32"
            ? suggestedAddress || prev.allowed
            : prev.allowed,
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
    setForm((prev) => ({ ...prev, presharedKey: bytesToBase64(p) }));
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

  const addEndpoint = React.useMemo(
    () => resolveClientEndpoint(form.customEndpoint, form.endpointHost, form.endpointPort),
    [form.customEndpoint, form.endpointHost, form.endpointPort],
  );

  const isValidAddPrivateKey = React.useMemo(
    () => isValidWgBase64Key(form.privateKey),
    [form.privateKey],
  );

  const isValidAddPresharedKey = React.useMemo(() => {
    const key = form.presharedKey.trim();
    return !key || isValidWgBase64Key(key);
  }, [form.presharedKey]);

  const addClientConfig = React.useMemo(() => {
    const priv = form.privateKey.trim();
    const addr = form.allowed.trim();
    const dns = form.dns.trim();
    const mtuNum = form.mtu.trim();
    const keepaliveNum = form.persistentKeepalive.trim();
    const allowedIps = form.allowedIps.trim();
    const psk = form.presharedKey.trim();
    const lines = [
      "[Interface]",
      `PrivateKey = ${priv || "YOUR_PRIVATE_KEY"}`,
      ...(addr ? [`Address = ${addr}`] : []),
      ...(dns ? [`DNS = ${dns}`] : []),
      ...(() => {
        if (!mtuNum) return [];
        const n = Number(mtuNum);
        return Number.isFinite(n) && n > 0 ? [`MTU = ${Math.floor(n)}`] : [];
      })(),
      "",
      "[Peer]",
      `PublicKey = ${form.serverPublicKey.trim() || "SERVER_PUBLIC_KEY"}`,
      ...(psk && isValidAddPresharedKey ? [`PresharedKey = ${psk}`] : []),
      `Endpoint = ${addEndpoint}`,
      ...(allowedIps ? [`AllowedIPs = ${allowedIps}`] : []),
      ...(() => {
        if (!keepaliveNum) return [];
        const n = Number(keepaliveNum);
        return Number.isFinite(n) && n > 0 ? [`PersistentKeepalive = ${Math.floor(n)}`] : [];
      })(),
    ];
    return lines.join("\n");
  }, [form, addEndpoint, isValidAddPresharedKey]);

  const downloadAddClientConfigFile = React.useCallback(() => {
    const base = sanitizeConfigFileBase(form.configName, form.name);
    const blob = new Blob([addClientConfig], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${base}.conf`;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [addClientConfig, form.configName, form.name]);

  if (setupOk === null) {
    return (
      <div className="mx-auto px-4 md:px-6 py-6">
        <div className="text-sm text-gray-500 dark:text-gray-400">Loading...</div>
      </div>
    );
  }

  if (routerLoadError) {
    return (
      <div className="mx-auto px-4 md:px-6 py-6">
        <div className="mx-auto my-12 md:my-16 w-full max-w-[720px] rounded-3xl ring-1 ring-rose-200 bg-white dark:bg-gray-900 dark:ring-rose-500/30 shadow-sm p-10 text-center grid gap-4">
          <div className="text-2xl font-semibold text-gray-900 dark:text-gray-100">Dashboard unavailable</div>
          <div className="text-sm text-gray-500 dark:text-gray-400">{routerLoadError}</div>
          <div className="flex items-center justify-center gap-3 pt-2">
            <button
              type="button"
              onClick={() => void loadRouters()}
              className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
            >
              Retry
            </button>
            <button
              type="button"
              onClick={() => navigate("/settings")}
              className="inline-flex items-center gap-2 rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
            >
              Open Settings
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (routers.length === 0) {
    return (
      <div className="mx-auto px-4 md:px-6 py-6">
        <div className="mx-auto my-12 md:my-16 w-full max-w-[720px] rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 shadow-sm p-10 text-center grid gap-4">
          <div className="text-2xl font-semibold text-gray-900 dark:text-gray-100">No router profiles yet</div>
          <div className="text-sm text-gray-500 dark:text-gray-400">
            The dashboard is the home for an existing workspace. First-time router setup and peer import now live in the dedicated setup flow.
          </div>
          <div className="flex items-center justify-center gap-3 pt-2">
            <button
              type="button"
              onClick={() => navigate("/setup")}
              className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
            >
              Open Setup
            </button>
            <button
              type="button"
              onClick={() => navigate("/settings")}
              className="inline-flex items-center gap-2 rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
            >
              Open Settings
            </button>
          </div>
        </div>
      </div>
    );
  }

  const globalSeries = showScopeOverviewChart ? (chartScopeUnit === "days" ? monthly : raw) : [];
  const globalTotals = globalSeries.reduce(
    (acc, point) => {
      acc.rx += point.rx || 0;
      acc.tx += point.tx || 0;
      return acc;
    },
    { rx: 0, tx: 0 },
  );
  const showRouterSections = routerSections.length > 0;
  const usageSubtitle = inScopeRouters.length === 0
    ? "No active routers in the current scope."
    : inScopeRouters.length === 1
      ? `Showing usage for ${inScopeRouters[0].name}.`
      : "Combined traffic across all routers currently in scope.";

  return (
    <div className="mx-auto px-4 md:px-6 py-6">
      <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">Overview</h1>

      <div className="mx-auto my-8 w-full min-w-0 max-w-[1120px] rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 p-6 grid gap-6">
        <div className="flex flex-col gap-4">
          <div className="flex flex-col lg:flex-row lg:items-center lg:justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Usage</div>
              <div className="text-xs text-gray-500 dark:text-gray-400">{usageSubtitle}</div>
            </div>
            {enabledRoutersByName.length > 1 && (
              <div className="flex flex-wrap items-center gap-2">
                {(["all", "selected"] as RouterScope[]).map((scope) => (
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
                    {scope === "all" ? "All routers" : "Selected routers"}
                  </button>
                ))}
              </div>
            )}
          </div>

          {enabledRoutersByName.length > 1 && normalizedScopeState.scope === "selected" && (
            <div className="flex flex-wrap items-center gap-2">
              {routersByName.map((router) => {
                const selected = normalizedScopeState.selectedRouterIds.includes(router.id);
                const paused = router.enabled === false;
                return (
                  <button
                    key={router.id}
                    type="button"
                    disabled={paused}
                    title={paused ? "Router is paused — resume it in Settings to use." : undefined}
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
                      paused
                        ? "bg-gray-100 text-gray-400 cursor-not-allowed dark:bg-gray-800 dark:text-gray-500"
                        : selected
                        ? "bg-indigo-100 text-indigo-800 dark:bg-indigo-500/10 dark:text-indigo-300"
                        : "bg-gray-100 text-gray-700 hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-200 dark:hover:bg-gray-700"
                    }`}
                  >
                    {router.name}{paused ? " · paused" : ""}
                  </button>
                );
              })}
            </div>
          )}

          <UsageTimeControls
            mode={timeMode}
            rollingValue={scopeValue}
            rollingUnit={scopeUnit}
            customStart={customStart}
            customEnd={customEnd}
            autoRefreshSeconds={refreshSec}
            showAutoRefresh={false}
            dateCalendar={dateCalendar}
            timezone={timezone}
            weekStartDay={weekStartDay}
            onModeChange={(mode) => update({ dashboard_time_mode: mode, dashboard_time_frame_today: mode === "today" })}
            onRollingValueChange={(value) => update({ dashboard_scope_value: value, dashboard_time_mode: "rolling", dashboard_time_frame_today: false })}
            onRollingUnitChange={(unit) => update({ dashboard_scope_unit: unit, dashboard_time_mode: "rolling", dashboard_time_frame_today: false })}
            onCustomStartChange={(value) => update({ dashboard_custom_start: value, dashboard_time_mode: "custom", dashboard_time_frame_today: false })}
            onCustomEndChange={(value) => update({ dashboard_custom_end: value, dashboard_time_mode: "custom", dashboard_time_frame_today: false })}
            peerPreviewMax={dashboardPeerPreviewCount}
            onPeerPreviewMaxChange={(value) => update({ dashboard_peer_preview_count: value })}
          />

          {showScopeOverviewChart && (
            <>
              <div className="h-56">
                <MemoUsageChart scopeUnit={chartScopeUnit} monthly={monthly} raw={raw} timezone={timezone} dateCalendar={dateCalendar} emptyLabel="No usage data yet" />
              </div>

              <div className="flex flex-wrap items-center gap-4 text-xs text-gray-500 dark:text-gray-400">
                <div>Total Download: <span className="font-medium text-gray-700 dark:text-gray-300">{fmtBytes(globalTotals.tx)}</span></div>
                <div>Total Upload: <span className="font-medium text-gray-700 dark:text-gray-300">{fmtBytes(globalTotals.rx)}</span></div>
                <div>Routers in scope: <span className="font-medium text-gray-700 dark:text-gray-300">{inScopeRouters.length}</span></div>
              </div>
            </>
          )}
        </div>

        <div className="flex flex-col gap-4 md:flex-row md:flex-wrap md:items-center md:justify-between">
          <div className="flex min-w-0 w-full flex-col gap-3 sm:flex-row md:flex-1">
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
            className="inline-flex shrink-0 items-center gap-2 self-start rounded-full bg-gray-900 px-4 py-2 text-sm text-white shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white sm:whitespace-nowrap"
          >
            Add peer to router +
          </button>
        </div>
      </div>

      <div className="mx-auto w-full max-w-[1120px] grid gap-6">
        {!showRouterSections ? (
          <div className="rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 p-6 text-sm text-gray-500 dark:text-gray-400">
            No peers in scope for the current router selection and filters.
          </div>
        ) : (
          routerSections.map((section) => (
            (() => {
              const canCollapsePeers = section.peers.length > dashboardPeerPreviewCount;
              const peersExpanded = canCollapsePeers ? !!expandedRouters[section.router.id] : true;
              const visiblePeers = peersExpanded ? section.peers : section.peers.slice(0, dashboardPeerPreviewCount);
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
                      {section.hidden > 0 && (
                        <span className="text-gray-400 dark:text-gray-500">Hidden <span className="font-medium">{section.hidden}</span></span>
                      )}
                      <span>Down <span className="font-medium text-gray-800 dark:text-gray-100">{fmtBytes(section.totals.tx)}</span></span>
                      <span>Up <span className="font-medium text-gray-800 dark:text-gray-100">{fmtBytes(section.totals.rx)}</span></span>
                    </div>
                  </div>

                  <LazySectionBody placeholderHeight={section.peers.length > 0 ? 420 : 220}>
                    <div className="grid gap-5">
                      <div className="h-52">
                        <MemoUsageChart
                          scopeUnit={chartScopeUnit}
                          monthly={section.monthly}
                          raw={section.raw}
                          timezone={timezone}
                          dateCalendar={dateCalendar}
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
	                                  className={`relative p-4 ring-gray-300 shadow hover:shadow-lg hover:-translate-y-0.5 cursor-pointer rounded-xl ${
	                                    peer.selected === false ? "opacity-70 ring-dashed" : ""
	                                  }`}
	                                  onClick={() => navigate(`/peer/${peer.id}`)}
	                                >
	                                  <RouterSyncWarningBadge status={peer.router_sync_status} />
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
                                      <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${
	                                        peer.router_sync_status === "new"
	                                          ? "bg-rose-100 text-rose-800 dark:bg-rose-500/10 dark:text-rose-300"
	                                          : peer.selected === false
	                                          ? "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300"
	                                          : peer.disabled
	                                            ? "bg-rose-100 text-rose-800"
                                            : "bg-indigo-100 text-indigo-800"
                                      }`}>
                                        <span className={`inline-block w-2 h-2 rounded-full ${
	                                          peer.router_sync_status === "new" ? "bg-rose-500" : peer.selected === false ? "bg-gray-400" : peer.disabled ? "bg-rose-500" : "bg-indigo-500"
	                                        }`} />
	                                        {peer.router_sync_status === "new" ? "Pending" : peer.selected === false ? "Hidden" : peer.disabled ? "Deactivated" : "Active"}
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

                          {(canCollapsePeers || (peersExpanded && section.hidden > 0)) && (
                            <div className="flex flex-col items-center gap-1 pt-1">
                              {peersExpanded && section.hidden > 0 && (
                                <button
                                  type="button"
                                  onClick={() => toggleRouterHiddenPeers(section.router.id)}
                                  className="text-[11px] font-normal text-gray-400 underline-offset-2 hover:text-gray-600 hover:underline dark:text-gray-600 dark:hover:text-gray-400"
                                >
                                  {section.showHiddenPeers ? "hide hidden peers" : `show hidden peers (${section.hidden})`}
                                </button>
                              )}
                              {canCollapsePeers && (
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
                              )}
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

        <div className={`w-12 flex flex-col items-center gap-2 transition-all duration-200 origin-bottom ${fabOpen ? "scale-100 opacity-100" : "scale-75 opacity-0 pointer-events-none"}`}>
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
                  onChange={(e) => setForm((prev) => ({
                    ...prev,
                    routerId: Number(e.target.value) || null,
                    allowed: "",
                    serverPublicKey: "",
                    endpointHost: "",
                    endpointPort: "51820",
                  }))}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                >
                  <option value="">Select router</option>
                  {routersByName.map((router) => (
                    <option key={router.id} value={router.id}>
                      {router.name} ({router.host}){router.enabled === false ? " · Paused" : ""}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">Interface</label>
                {interfaceOptions.length > 0 && !interfaceLoadFailed ? (
                  <select
                    value={form.interface}
                    onChange={(e) => setForm((prev) => ({ ...prev, interface: e.target.value, allowed: "" }))}
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
                    onChange={(e) => setForm((prev) => ({ ...prev, interface: e.target.value, allowed: "" }))}
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
                <label className="text-xs text-gray-500 dark:text-gray-400">Config name (optional)</label>
                <input
                  value={form.configName}
                  onChange={(e) => setForm((prev) => ({ ...prev, configName: e.target.value }))}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                  placeholder="Used as download filename"
                />
              </div>

              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">Allowed address (inbound)</label>
                <input
                  value={form.allowed}
                  onChange={(e) => setForm((prev) => ({ ...prev, allowed: e.target.value }))}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                  placeholder="Auto-suggested from interface subnet"
                />
              </div>

              <div className="grid gap-2">
                <div className="flex items-center justify-between">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Keys</label>
                  <button type="button" onClick={generateKeypair} className="text-xs rounded-full bg-gray-900 text-white px-3 py-1 shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white">Generate</button>
                </div>
                <label className="text-xs text-gray-500 dark:text-gray-400">Private key</label>
                <div className="flex items-center gap-2">
                  <input
                    type={showAddPrivateKey ? "text" : "password"}
                    value={form.privateKey}
                    onChange={(e) => setForm((prev) => ({ ...prev, privateKey: e.target.value, publicKey: "" }))}
                    className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="base64 32-byte key"
                  />
                  <button
                    type="button"
                    onClick={() => setShowAddPrivateKey((s) => !s)}
                    className="shrink-0 rounded-full border border-gray-200 dark:border-gray-800 px-3 py-2 text-xs bg-white dark:bg-gray-950"
                  >
                    {showAddPrivateKey ? "Hide" : "Show"}
                  </button>
                </div>
                {form.privateKey.trim() && !isValidAddPrivateKey && (
                  <div className="text-xs text-rose-600">Private key must be base64 (32 bytes).</div>
                )}
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
                  <button type="button" onClick={generatePsk} className="text-xs rounded-full bg-gray-100 text-gray-800 px-3 py-1 shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700">Generate</button>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type={showAddPresharedKey ? "text" : "password"}
                    value={form.presharedKey}
                    onChange={(e) => setForm((prev) => ({ ...prev, presharedKey: e.target.value }))}
                    className="w-full rounded-xl border border-gray-200 px-3 py-2 text-sm font-mono focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                    placeholder="base64 32-byte key"
                  />
                  <button
                    type="button"
                    onClick={() => setShowAddPresharedKey((s) => !s)}
                    className="shrink-0 rounded-full border border-gray-200 dark:border-gray-800 px-3 py-2 text-xs bg-white dark:bg-gray-950"
                  >
                    {showAddPresharedKey ? "Hide" : "Show"}
                  </button>
                </div>
                {form.presharedKey.trim() && !isValidAddPresharedKey && (
                  <div className="text-xs text-rose-600">Preshared key must be base64 (32 bytes), or leave empty.</div>
                )}
              </div>

              <div className="flex items-center gap-3 pt-2">
                <button
                  disabled={
                    addBusy ||
                    !form.routerId ||
                    !form.name.trim() ||
                    !isValidAddPrivateKey ||
                    !form.publicKey ||
                    !form.allowed.trim() ||
                    form.allowed.trim() === "0.0.0.0/0" ||
                    form.allowed.trim() === "::/0" ||
                    !isValidAddPresharedKey
                  }
                  onClick={async () => {
                    setAddErr("");
                    try {
                      if (!form.routerId) throw new Error("Select a router first.");
                      if (!isValidAddPrivateKey) throw new Error("Generate or enter a valid private key first.");
                      if (!isValidAddPresharedKey) throw new Error("Fix the preshared key or leave it empty.");
                      setAddBusy(true);
                      await createRouterPeer(form.routerId, {
                        interface: form.interface || "wgmik",
                        name: form.name.trim(),
                        public_key: form.publicKey,
                        private_key: form.privateKey.trim(),
                        preshared_key: form.presharedKey.trim() || undefined,
                        config_name: form.configName.trim() || undefined,
                        custom_endpoint: form.customEndpoint.trim() || undefined,
                        allowed_address: form.allowed.trim(),
                      });
                      setLastAddRouterId(form.routerId);
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
              <div className="text-xs text-gray-500 dark:text-gray-400">Save creates the peer on the selected router and stores the client private key, preshared key, config name, and custom endpoint for the peer detail view.</div>
            </div>

            <div className="grid gap-3">
              <div className="flex items-center justify-between">
                <div className="text-sm text-gray-700 dark:text-gray-200">Client config</div>
                <button
                  type="button"
                  onClick={async () => {
                    try {
                      await navigator.clipboard.writeText(addClientConfig);
                    } catch {
                      // ignore
                    }
                  }}
                  className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                >
                  Copy
                </button>
              </div>
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
                <label className="text-xs text-gray-500 dark:text-gray-400">Custom endpoint (optional)</label>
                <input
                  value={form.customEndpoint}
                  onChange={(e) => setForm((prev) => ({ ...prev, customEndpoint: e.target.value }))}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800 dark:focus:ring-gray-700"
                  placeholder="vpn.example.com or 1.2.3.4:8080"
                />
              </div>
              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">Endpoint preview</label>
                <input
                  readOnly
                  value={addEndpoint}
                  className="rounded-xl border border-gray-200 px-3 py-2 text-sm bg-gray-50 text-gray-900 dark:bg-gray-950 dark:text-gray-100 dark:border-gray-800"
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
                {isValidAddPrivateKey ? (
                  <QRCode value={addClientConfig} size={256} />
                ) : (
                  <div className="text-sm text-gray-500 dark:text-gray-400 text-center">
                    Generate or paste a valid private key to render a QR code.
                  </div>
                )}
              </div>
              <textarea
                readOnly
                value={addClientConfig}
                className="min-h-[180px] rounded-2xl border border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-950 px-3 py-2 text-xs font-mono text-gray-800 dark:text-gray-100"
              />
              <button
                type="button"
                onClick={downloadAddClientConfigFile}
                disabled={!addClientConfig}
                className="justify-self-start rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
              >
                Save config file
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
