import React from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import QRCode from "react-qr-code";
import { listSavedPeers, getPeerUsage, listRouters, routerPeers, routerInterfaceDetail, getPeerClientPrivateKey, getPeerClientExportPrefs, patchPeerClientExportPrefs, renewPeerKeys, patchPeer, resolvePeerRouterSync, getPeerQuota, patchPeerQuota, resetPeerMetrics, deletePeer, reconcilePeer, getPeerActions, type PeerAction, getSettings, type SavedPeer, type UsagePoint, type Router, type PeerView, type Quota, type WGInterfaceConfig, getFairUsagePeerStatus, resetFairUsagePeer, type FairUsagePeerStatusDTO, type FairUsageRuleStatusItemDTO } from "../api";

function effectiveThrottleForRule(fr: FairUsageRuleStatusItemDTO): { dl: number; ul: number; label: string } {
  if (fr.tiered && fr.tiers?.length) {
    const a = fr.tiers.find((t) => t.is_active);
    if (a) {
      return {
        dl: a.throttle_download_kbps,
        ul: a.throttle_upload_kbps,
        label: (a.name || "").trim() ? `${fr.rule_name} · ${a.name.trim()}` : fr.rule_name,
      };
    }
  }
  return { dl: fr.throttle_download_kbps, ul: fr.throttle_upload_kbps, label: fr.rule_name };
}
import { useAutoSaveSettings, type ScopeUnit } from "../useAutoSaveSettings";
import UsageTimeControls, { normalizeUsageTimeMode } from "../UsageTimeControls";
import {
  formatCalendarDateTime,
  formatCalendarDayLabel,
  startOfLocalTodayValue,
  startOfSelectedCalendarMonthValue,
  zonedWallTimeValueToUtcIso,
} from "../datetimeLocal";

function RenewKeyButton({
  onClick,
  disabled,
  title,
}: {
  onClick: () => void;
  disabled?: boolean;
  title: string;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      className="shrink-0 rounded-full border border-gray-200 dark:border-gray-800 p-2 text-gray-600 dark:text-gray-300 bg-white dark:bg-gray-950 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-50"
    >
      <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 12a9 9 0 1 1-2.64-6.36" />
        <path d="M21 3v6h-6" />
      </svg>
    </button>
  );
}

function generatePresharedKey(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

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
        mono ? "break-all" : "break-words",
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
  const [router, setRouter] = React.useState<Router | null>(null);
  const [ifaceCfg, setIfaceCfg] = React.useState<WGInterfaceConfig | null>(null);
  const [quota, setQuota] = React.useState<Quota | null>(null);
  const [quotaErr, setQuotaErr] = React.useState("");
  const [quotaSaveState, setQuotaSaveState] = React.useState<"idle" | "dirty" | "saving" | "saved" | "error">("idle");
  const quotaSaveTimerRef = React.useRef<number | null>(null);
  const quotaSavingRef = React.useRef(false);
  const quotaPendingRef = React.useRef(false);
  const quotaDraftInitRef = React.useRef(false);
  const userEditedRef = React.useRef(false);

  const [peerNameDraft, setPeerNameDraft] = React.useState("");
  const [nameSaving, setNameSaving] = React.useState(false);
  const [nameErr, setNameErr] = React.useState("");

  const [fuStatus, setFuStatus] = React.useState<FairUsagePeerStatusDTO | null>(null);
  const [fuResetBusy, setFuResetBusy] = React.useState(false);

  const fuRules = React.useMemo((): FairUsageRuleStatusItemDTO[] => {
    if (!fuStatus) return [];
    if (fuStatus.rules && fuStatus.rules.length > 0) return fuStatus.rules;
    if (fuStatus.rule_id) {
      return [
        {
          rule_id: fuStatus.rule_id,
          rule_name: fuStatus.rule_name ?? "",
          quota_mode: fuStatus.quota_mode ?? "combined",
          download_quota_bytes: fuStatus.download_quota_bytes,
          upload_quota_bytes: fuStatus.upload_quota_bytes,
          throttle_download_kbps: fuStatus.throttle_download_kbps,
          throttle_upload_kbps: fuStatus.throttle_upload_kbps,
          time_scope: fuStatus.time_scope,
          scope_period_count: fuStatus.scope_period_count,
          scope_period_unit: fuStatus.scope_period_unit,
          scope_label: fuStatus.scope_label,
          scope_type: fuStatus.scope_type,
          sort_order: fuStatus.sort_order,
          passthrough: fuStatus.passthrough,
          used_rx: fuStatus.used_rx,
          used_tx: fuStatus.used_tx,
          over_quota: fuStatus.throttled,
          is_effective: true,
          next_reset: fuStatus.next_reset,
        },
      ];
    }
    return [];
  }, [fuStatus]);

  const effectiveFuRule = React.useMemo(
    () => fuRules.find((r) => r.is_effective) ?? (fuStatus?.rule_id ? fuRules.find((r) => r.rule_id === fuStatus.rule_id) : undefined),
    [fuRules, fuStatus],
  );
  const hasFairUsageRules = !!fuStatus && fuRules.length > 0;

  const [todayTick, setTodayTick] = React.useState(0);

  const [clientCfg, setClientCfg] = React.useState(() => ({
    privateKey: "",
    dns: "8.8.8.8, 1.1.1.1",
    mtu: "1280",
    persistentKeepalive: "25",
    allowedIps: "0.0.0.0/0, ::/0",
    /** Display / download filename only; not part of WireGuard config body */
    configName: "",
    /** Overrides Endpoint= line when set (hostname, IP, or host:port) */
    customEndpoint: "",
    presharedKey: "",
  }));
  const [showClientSecrets, setShowClientSecrets] = React.useState(false);
  const [exportPrefsServer, setExportPrefsServer] = React.useState<{
    config_name: string;
    custom_endpoint: string;
    preshared_key: string;
  } | null>(null);
  const [exportPrefsSaveState, setExportPrefsSaveState] = React.useState<
    "idle" | "dirty" | "saving" | "saved" | "error"
  >("idle");
  const [exportPrefsErr, setExportPrefsErr] = React.useState("");
  const exportPrefsSaveTimerRef = React.useRef<number | null>(null);
  const exportPrefsSavingRef = React.useRef(false);
  const exportPrefsPendingRef = React.useRef(false);

  const [quotaDraft, setQuotaDraft] = React.useState<{ limitGb: number; valid_from: string; valid_until: string }>({
    limitGb: 0,
    valid_from: "",
    valid_until: "",
  });
  const [confirmRenewKeys, setConfirmRenewKeys] = React.useState(false);
  const [confirmRenewPsk, setConfirmRenewPsk] = React.useState(false);
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
	          setRouter(r || null);
	        } catch { }
	      } catch { setPeer(null); }
	    })();
	  }, [peerId]);

	  React.useEffect(() => {
	    if (peer) setPeerNameDraft(peer.name || "");
	  }, [peer?.name, peer?.id]);

	  const commitPeerName = React.useCallback(async () => {
	    if (!peer) return;
	    const next = peerNameDraft.trim();
	    if ((peer.name || "") === next) {
	      setNameErr("");
	      return;
	    }
	    setNameSaving(true);
	    setNameErr("");
	    try {
	      const saved = await patchPeer(peer.id, { name: next });
	      setPeer((prev) => (prev ? { ...prev, name: saved.name ?? next } : null));
	      setPeerNameDraft(saved.name ?? next);
	    } catch (e: unknown) {
	      const msg = e instanceof Error ? e.message : "Could not save name";
	      setNameErr(msg);
	      setPeerNameDraft(peer.name || "");
	    } finally {
	      setNameSaving(false);
	    }
	  }, [peer, peerNameDraft]);

	  React.useEffect(() => {
	    if (!peer) { setIfaceCfg(null); return; }
	    (async () => {
	      try {
	        const cfg = await routerInterfaceDetail(peer.router_id, peer.interface);
	        setIfaceCfg(cfg);
	      } catch {
	        setIfaceCfg(null);
	      }
	    })();
	  }, [peer?.router_id, peer?.interface]);

	  React.useEffect(() => {
	    if (!peerId) return;
	    (async () => {
	      try {
	        const res = await getPeerClientPrivateKey(peerId);
	        if (res?.private_key && !(clientCfg.privateKey || "").trim()) {
	          setClientCfg((c) => ({ ...c, privateKey: res.private_key || "" }));
	        }
	      } catch {
	        // ignore
	      }
	    })();
	    // Intentionally ignore clientCfg.privateKey in deps: we only want to auto-fill once if empty.
	    // eslint-disable-next-line react-hooks/exhaustive-deps
	  }, [peerId]);

	  React.useEffect(() => {
	    if (!peerId) return;
	    setExportPrefsServer(null);
	    setClientCfg((c) => ({
	      ...c,
	      configName: "",
	      customEndpoint: "",
	      presharedKey: "",
	    }));
	    let cancelled = false;
	    (async () => {
	      try {
	        const p = await getPeerClientExportPrefs(peerId);
	        if (cancelled) return;
	        const server = {
	          config_name: p.config_name ?? "",
	          custom_endpoint: p.custom_endpoint ?? "",
	          preshared_key: p.preshared_key ?? "",
	        };
	        setClientCfg((c) => ({
	          ...c,
	          configName: server.config_name,
	          customEndpoint: server.custom_endpoint,
	          presharedKey: server.preshared_key,
	        }));
	        setExportPrefsServer(server);
	      } catch {
	        if (cancelled) return;
	        setExportPrefsServer({ config_name: "", custom_endpoint: "", preshared_key: "" });
	      }
	    })();
	    return () => {
	      cancelled = true;
	    };
	  }, [peerId]);

  const refreshPeer = React.useCallback(async () => {
    const peers = await listSavedPeers();
    const p = peers.find(x => x.id === peerId) || null;
    setPeer(p);
  }, [peerId]);
  // Settings Hook
  const { settings, update } = useAutoSaveSettings();

  // Helpers
  const refreshSec = Math.max(5, Number(settings?.poll_interval_seconds) || 30);
  const scopeValue = Math.max(1, Number(settings?.peer_default_scope_value) || 60);
  const scopeUnit: ScopeUnit = settings?.peer_default_scope_unit === "hours" || settings?.peer_default_scope_unit === "days"
    ? settings.peer_default_scope_unit
    : "minutes";
  const timezone = settings?.timezone ?? "UTC";
  const dateCalendar = settings?.date_calendar ?? "gregorian";
  const weekStartDay = Number(settings?.week_start_day ?? 0);
  const timeMode = normalizeUsageTimeMode(
    settings?.peer_time_mode ?? (settings?.peer_time_frame_today ? "today" : "rolling"),
  );
  const customStart = settings?.peer_custom_start ?? "";
  const customEnd = settings?.peer_custom_end ?? "";
  const showKindPills = settings?.show_kind_pills ?? true;

  const clientConfig = React.useMemo(() => {
    if (!peer) return "";
    const priv = (clientCfg.privateKey || "").trim();
    const addr = (peer.allowed_address || "").trim();
    const dns = (clientCfg.dns || "").trim();
    const mtu = (clientCfg.mtu || "").trim();
    const keepalive = (clientCfg.persistentKeepalive || "").trim();
    const allowedIps = (clientCfg.allowedIps || "").trim();

    const serverPublicKey = (ifaceCfg?.public_key || "").trim() || "SERVER_PUBLIC_KEY";
    const endpointHost = (ifaceCfg?.public_host || "").trim() || (router?.host || "").trim();
    const endpointPort = ifaceCfg?.listen_port || 51820;
    const customEp = (clientCfg.customEndpoint || "").trim();
    const defaultEndpoint = endpointHost ? `${endpointHost}:${endpointPort}` : "HOST:PORT";
    let endpoint = defaultEndpoint;
    if (customEp) {
      if (customEp.startsWith("[") && customEp.includes("]")) {
        endpoint = customEp.includes("]:") ? customEp : `${customEp}:${endpointPort}`;
      } else if (/:[0-9]{1,5}$/.test(customEp)) {
        endpoint = customEp;
      } else if (customEp.includes(":")) {
        endpoint = `[${customEp}]:${endpointPort}`;
      } else {
        endpoint = `${customEp}:${endpointPort}`;
      }
    }

    const psk = (clientCfg.presharedKey || "").trim();
    const pskLineOk = (() => {
      if (!psk) return false;
      try {
        return atob(psk).length === 32;
      } catch {
        return false;
      }
    })();

    const lines = [
      "[Interface]",
      `PrivateKey = ${priv || "YOUR_PRIVATE_KEY"}`,
      ...(addr ? [`Address = ${addr}`] : []),
      ...(dns ? [`DNS = ${dns}`] : []),
      ...(() => {
        if (!mtu) return [];
        const n = Number(mtu);
        if (!Number.isFinite(n) || n <= 0) return [];
        return [`MTU = ${Math.floor(n)}`];
      })(),
      "",
      "[Peer]",
      `PublicKey = ${serverPublicKey}`,
      ...(pskLineOk ? [`PresharedKey = ${psk}`] : []),
      `Endpoint = ${endpoint}`,
      ...(allowedIps ? [`AllowedIPs = ${allowedIps}`] : []),
      ...(() => {
        if (!keepalive) return [];
        const n = Number(keepalive);
        if (!Number.isFinite(n) || n <= 0) return [];
        return [`PersistentKeepalive = ${Math.floor(n)}`];
      })(),
    ];
    return lines.join("\n");
  }, [peer?.id, peer?.allowed_address, clientCfg.privateKey, clientCfg.dns, clientCfg.mtu, clientCfg.persistentKeepalive, clientCfg.allowedIps, clientCfg.presharedKey, clientCfg.customEndpoint, ifaceCfg?.public_key, ifaceCfg?.public_host, ifaceCfg?.listen_port, router?.host]);

  const sanitizeConfigFileBase = React.useCallback((name: string, fallback: string) => {
    const raw = (name || "").trim() || (fallback || "").trim() || "wg-peer";
    const safe = raw.replace(/[/\\?%*:|"<>]/g, "_").replace(/\s+/g, "_").slice(0, 120);
    return safe || "wg-peer";
  }, []);

  const downloadClientConfigFile = React.useCallback(() => {
    if (!peer || !clientConfig) return;
    const base = sanitizeConfigFileBase(clientCfg.configName, peer.name || "");
    const blob = new Blob([clientConfig], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${base}.conf`;
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }, [peer, clientConfig, clientCfg.configName, sanitizeConfigFileBase]);

  const isValidWgPrivateKey = React.useMemo(() => {
    const pk = (clientCfg.privateKey || "").trim();
    if (!pk) return false;
    try {
      const bin = atob(pk);
      return bin.length === 32;
    } catch {
      return false;
    }
  }, [clientCfg.privateKey]);

  const isValidWgPresharedKey = React.useMemo(() => {
    const pk = (clientCfg.presharedKey || "").trim();
    if (!pk) return true;
    try {
      return atob(pk).length === 32;
    } catch {
      return false;
    }
  }, [clientCfg.presharedKey]);

  const isExportPrefsDirty = React.useMemo(() => {
    if (!exportPrefsServer || !peer) return false;
    return (
      clientCfg.configName !== exportPrefsServer.config_name ||
      clientCfg.customEndpoint !== exportPrefsServer.custom_endpoint ||
      (clientCfg.presharedKey || "") !== (exportPrefsServer.preshared_key || "")
    );
  }, [exportPrefsServer, peer, clientCfg.configName, clientCfg.customEndpoint, clientCfg.presharedKey]);

  const validateExportPrefsDraft = React.useCallback((): string | null => {
    const pk = (clientCfg.presharedKey || "").trim();
    if (!pk) return null;
    try {
      if (atob(pk).length !== 32) return "Preshared key must be base64 (32 bytes), or leave empty.";
    } catch {
      return "Preshared key must be base64 (32 bytes), or leave empty.";
    }
    return null;
  }, [clientCfg.presharedKey]);

  const doAutoSaveExportPrefs = React.useCallback(async () => {
    if (!peer || !exportPrefsServer) return;
    const validationErr = validateExportPrefsDraft();
    if (validationErr) {
      setExportPrefsErr(validationErr);
      setExportPrefsSaveState("error");
      return;
    }
    if (!isExportPrefsDirty) {
      setExportPrefsSaveState((s) => (s === "dirty" ? "idle" : s));
      return;
    }

    if (exportPrefsSavingRef.current) {
      exportPrefsPendingRef.current = true;
      return;
    }

    const snapshot = {
      config_name: clientCfg.configName,
      custom_endpoint: clientCfg.customEndpoint,
      preshared_key: clientCfg.presharedKey,
    };
    exportPrefsSavingRef.current = true;
    setExportPrefsErr("");
    setExportPrefsSaveState("saving");
    try {
      const saved = await patchPeerClientExportPrefs(peer.id, {
        config_name: snapshot.config_name,
        custom_endpoint: snapshot.custom_endpoint,
        preshared_key: snapshot.preshared_key,
      });
      const next = {
        config_name: saved.config_name ?? "",
        custom_endpoint: saved.custom_endpoint ?? "",
        preshared_key: saved.preshared_key ?? "",
      };
      setExportPrefsServer(next);
      const stillSame =
        clientCfg.configName === snapshot.config_name &&
        clientCfg.customEndpoint === snapshot.custom_endpoint &&
        (clientCfg.presharedKey || "") === (snapshot.preshared_key || "");
      if (stillSame) {
        setClientCfg((c) => ({
          ...c,
          configName: next.config_name,
          customEndpoint: next.custom_endpoint,
          presharedKey: next.preshared_key,
        }));
      }
      await refreshPeer();
      setExportPrefsSaveState("saved");
      window.setTimeout(() => {
        setExportPrefsSaveState((s) => (s === "saved" ? "idle" : s));
      }, 1200);
    } catch (e: any) {
      setExportPrefsErr(e?.message || "Failed to save export preferences");
      setExportPrefsSaveState("error");
    } finally {
      exportPrefsSavingRef.current = false;
      if (exportPrefsPendingRef.current) {
        exportPrefsPendingRef.current = false;
        doAutoSaveExportPrefs();
      }
    }
  }, [
    peer,
    exportPrefsServer,
    isExportPrefsDirty,
    validateExportPrefsDraft,
    clientCfg.configName,
    clientCfg.customEndpoint,
    clientCfg.presharedKey,
    refreshPeer,
  ]);

  // Debounced auto-save for config name, custom endpoint, preshared key (server + RouterOS).
  React.useEffect(() => {
    if (!exportPrefsServer || !peer) return;
    const validationErr = validateExportPrefsDraft();
    if (validationErr) {
      setExportPrefsErr(validationErr);
      setExportPrefsSaveState("error");
      if (exportPrefsSaveTimerRef.current) window.clearTimeout(exportPrefsSaveTimerRef.current);
      exportPrefsSaveTimerRef.current = null;
      return;
    }
    setExportPrefsErr("");
    if (isExportPrefsDirty) {
      setExportPrefsSaveState((s) => (s === "saving" ? s : "dirty"));
      if (exportPrefsSaveTimerRef.current) window.clearTimeout(exportPrefsSaveTimerRef.current);
      exportPrefsSaveTimerRef.current = window.setTimeout(() => {
        doAutoSaveExportPrefs();
      }, 800);
      return () => {
        if (exportPrefsSaveTimerRef.current) window.clearTimeout(exportPrefsSaveTimerRef.current);
      };
    }
    if (exportPrefsSaveTimerRef.current) window.clearTimeout(exportPrefsSaveTimerRef.current);
    exportPrefsSaveTimerRef.current = null;
    setExportPrefsSaveState((s) => (s === "dirty" ? "idle" : s));
  }, [
    clientCfg.configName,
    clientCfg.customEndpoint,
    clientCfg.presharedKey,
    isExportPrefsDirty,
    validateExportPrefsDraft,
    doAutoSaveExportPrefs,
    exportPrefsServer,
    peer,
  ]);

  const toIso = React.useCallback((v: string) => {
    return zonedWallTimeValueToUtcIso(v, timezone);
  }, [timezone]);
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

  const loadFuStatus = React.useCallback(async () => {
    try {
      const s = await getFairUsagePeerStatus(peerId);
      setFuStatus(s);
    } catch {
      setFuStatus(null);
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
      if (chartScopeUnit === "days") {
        if (effectiveAllTime) {
          const points = await getPeerUsage(peerId, { window: "daily", allTime: true });
          setUsage(points);
          return;
        }
        if (effectiveStartIso || effectiveEndIso) {
          const points = await getPeerUsage(peerId, { window: "daily", start: effectiveStartIso, end: effectiveEndIso });
          setUsage(points);
          return;
        }
        const points = await getPeerUsage(peerId, { window: "daily" });
        const trimmed = scopeValue > 0 && points.length > scopeValue ? points.slice(points.length - scopeValue) : points;
        setUsage(trimmed);
      } else {
        const seconds =
          chartScopeUnit === "minutes"
            ? Math.max(1, scopeValue) * 60
            : Math.max(1, scopeValue) * 3600;
        const interval = chartScopeUnit === "minutes" ? 60 : 3600;
        if (effectiveStartIso || effectiveEndIso) {
          const points = await getPeerUsage(peerId, { window: "raw", seconds: effectiveStartIso ? undefined : seconds, interval, start: effectiveStartIso, end: effectiveEndIso });
          setUsage(points);
          return;
        }
        const points = await getPeerUsage(peerId, { window: "raw", seconds, interval });
        setUsage(points);
      }
    } catch {
      setUsage([]);
    }
  }, [peerId, chartScopeUnit, scopeValue, effectiveAllTime, effectiveStartIso, effectiveEndIso]);




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

	  const resolveRouterSync = React.useCallback(async (action: "hide" | "delete" | "accept") => {
	    if (!peer) return;
	    setActionErr("");
	    try {
	      setActionBusy(true);
	      const result = await resolvePeerRouterSync(peer.id, action);
	      if (action === "delete") {
	        navigate("/");
	        return;
	      }
	      if (result && typeof result === "object" && "id" in result) {
	        setPeer(result as SavedPeer);
	      } else {
	        await loadPeer();
	      }
	      await fetchActions(actionsLimit);
	    } catch (e: any) {
	      setActionErr(e?.message || "Failed to resolve RouterOS sync status");
	    } finally {
	      setActionBusy(false);
	    }
	  }, [actionsLimit, fetchActions, loadPeer, navigate, peer]);


	  React.useEffect(() => {
    loadQuota();
    loadUsage();
    loadFuStatus();
    void fetchActions(actionsLimit);
  }, [loadQuota, loadUsage, loadFuStatus, fetchActions, actionsLimit]);

  // Auto-refresh peer usage at roughly the poll interval (default 30s)
  React.useEffect(() => {
    const intervalSec = Math.max(5, refreshSec || 30);
    const id = window.setInterval(() => {
      loadPeer();
      loadQuota();
      loadUsage();
      loadFuStatus();
      void fetchActions(actionsLimit);
    }, intervalSec * 1000);
    return () => window.clearInterval(id);
  }, [refreshSec, loadPeer, loadQuota, loadUsage, loadFuStatus, fetchActions, actionsLimit]);

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
      <div className="mx-auto my-12 md:my-16 w-full max-w-[960px]">
        <div className="mb-3 flex justify-end">
          <Link
            to="/"
            className="inline-flex items-center gap-2 rounded-full bg-white text-gray-900 px-4 py-1.5 text-sm ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-800"
          >
            ← Dashboard
          </Link>
        </div>
        <div className="rounded-3xl ring-1 ring-gray-200 bg-white dark:bg-gray-900 dark:ring-gray-800 shadow-sm p-5 md:p-6 overflow-y-auto overflow-x-hidden">
        {!peer ? (
          <div className="text-sm text-gray-500 dark:text-gray-400">Loading…</div>
        ) : (
          <div className="grid grid-cols-1 gap-6">
            <div className="flex items-center justify-between mb-2">
              <div className="min-w-0 max-w-[420px] md:max-w-[520px]">
                <div className="min-w-0">
                  <input
                    type="text"
                    value={peerNameDraft}
                    onChange={(e) => setPeerNameDraft(e.target.value)}
                    onBlur={() => void commitPeerName()}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        (e.target as HTMLInputElement).blur();
                      }
                      if (e.key === "Escape" && peer) {
                        setPeerNameDraft(peer.name || "");
                        setNameErr("");
                        (e.target as HTMLInputElement).blur();
                      }
                    }}
                    disabled={nameSaving}
                    className="w-full min-w-0 max-w-full rounded border border-transparent bg-transparent px-1 -mx-1 text-base text-gray-500 dark:text-gray-400 focus:border-indigo-500/40 focus:outline-none focus:ring-0 disabled:opacity-60"
                    aria-label="Peer name"
                  />
                  {nameErr ? (
                    <div className="mt-0.5 text-xs text-rose-600 dark:text-rose-400">{nameErr}</div>
                  ) : null}
                </div>
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
	                  <span className={`inline-flex items-center gap-2 rounded-full px-2.5 py-1 text-xs ${
	                    peer.router_sync_status === "new"
	                      ? "bg-rose-100 text-rose-800 dark:bg-rose-500/10 dark:text-rose-300"
	                      : peer.selected === false
	                      ? "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-300"
	                      : peer.disabled
	                        ? 'bg-rose-100 text-rose-800'
	                        : 'bg-indigo-100 text-indigo-800'
	                  }`}>
	                    <span className={`inline-block w-2 h-2 rounded-full ${
	                      peer.router_sync_status === "new" ? "bg-rose-500" : peer.selected === false ? "bg-gray-400" : peer.disabled ? 'bg-rose-500' : 'bg-indigo-500'
	                    }`} />
	                    {peer.router_sync_status === "new" ? 'Pending' : peer.selected === false ? 'Hidden' : peer.disabled ? 'Deactivated' : 'Active'}
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

	            {peer.router_sync_status !== "synced" && (
	              <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-900 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-100">
	                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
	                  <div>
	                    <div className="font-medium">
	                      {peer.router_sync_status === "missing"
	                        ? "This peer is no longer present on RouterOS."
	                        : "This peer was found on RouterOS but has not been added to WGMik."}
	                    </div>
	                    <div className="mt-1 text-xs text-rose-700 dark:text-rose-200">
	                      Choose how WGMik should treat this RouterOS change.
	                    </div>
	                  </div>
	                  <div className="flex flex-wrap items-center gap-2">
	                    {peer.router_sync_status === "missing" ? (
	                      <>
	                        <button
	                          disabled={actionBusy}
	                          onClick={() => resolveRouterSync("hide")}
	                          className="rounded-full bg-white px-3 py-1.5 text-xs font-medium text-rose-800 shadow hover:bg-rose-100 disabled:opacity-50 dark:bg-gray-950 dark:text-rose-100 dark:hover:bg-rose-500/20"
	                        >
	                          Hide peer
	                        </button>
	                        <button
	                          disabled={actionBusy}
	                          onClick={() => resolveRouterSync("delete")}
	                          className="rounded-full bg-rose-700 px-3 py-1.5 text-xs font-medium text-white shadow hover:bg-rose-800 disabled:opacity-50"
	                        >
	                          Delete local record
	                        </button>
	                      </>
	                    ) : (
	                      <>
	                        <button
	                          disabled={actionBusy}
	                          onClick={() => resolveRouterSync("accept")}
	                          className="rounded-full bg-rose-700 px-3 py-1.5 text-xs font-medium text-white shadow hover:bg-rose-800 disabled:opacity-50"
	                        >
	                          Add to list
	                        </button>
	                        <button
	                          disabled={actionBusy}
	                          onClick={() => resolveRouterSync("hide")}
	                          className="rounded-full bg-white px-3 py-1.5 text-xs font-medium text-rose-800 shadow hover:bg-rose-100 disabled:opacity-50 dark:bg-gray-950 dark:text-rose-100 dark:hover:bg-rose-500/20"
	                        >
	                          Keep hidden
	                        </button>
	                      </>
	                    )}
	                  </div>
	                </div>
	              </div>
	            )}
	
	            {/* Usage (moved to top) */}
            <div className="p-0 mt-2">
              <div className="mb-3 grid gap-3">
                <div>
                  <div className="text-sm text-gray-700 dark:text-gray-200">Usage</div>
                </div>
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
                  onModeChange={(mode) => update({ peer_time_mode: mode, peer_time_frame_today: mode === "today" })}
                  onRollingValueChange={(value) => update({ peer_default_scope_value: value, peer_time_mode: "rolling", peer_time_frame_today: false })}
                  onRollingUnitChange={(unit) => update({ peer_default_scope_unit: unit, peer_time_mode: "rolling", peer_time_frame_today: false })}
                  onCustomStartChange={(value) => update({ peer_custom_start: value, peer_time_mode: "custom", peer_time_frame_today: false })}
                  onCustomEndChange={(value) => update({ peer_custom_end: value, peer_time_mode: "custom", peer_time_frame_today: false })}
                />
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
                            if (chartScopeUnit === "days") {
                              // val is YYYY-MM-DD (UTC)
                              return formatCalendarDayLabel(val, { timeZone: timezone || "UTC", dateCalendar });
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
                            if (chartScopeUnit === "days") {
                              return formatCalendarDayLabel(String(label), { timeZone: timezone || "UTC", dateCalendar, long: true });
                            }
                            const d = new Date(label);
                            return formatCalendarDateTime(d, { timeZone: timezone || "UTC", dateCalendar, includeTime: true });
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
              <div className="mt-2 flex flex-col items-center gap-2 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex flex-wrap items-center justify-center gap-x-6 gap-y-1 text-xs text-gray-500 dark:text-gray-400 sm:flex-1">
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
                <button
                  disabled={actionBusy || !peer}
                  onClick={async () => {
                    if (!peer) return;
                    if (!confirm('Reset all usage metrics for this peer? This cannot be undone.')) return;
                    setActionErr("");
                    try {
                      setActionBusy(true);
                      await resetPeerMetrics(peer.id);
                      try { const points = await getPeerUsage(peer.id); setUsage(points); } catch { }
                      try { const q = await getPeerQuota(peer.id); setQuota(q); } catch { }
                    } catch (e: any) {
                      setActionErr(e?.message || "Failed to reset metrics");
                    } finally {
                      setActionBusy(false);
                    }
                  }}
                  className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 disabled:opacity-50 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                >
                  Reset metrics
                </button>
              </div>
            </div>
            {/* Fair Usage Status — each applicable rule has its own period, usage, and reset */}
            {hasFairUsageRules ? (
              <div className="grid gap-4 pt-4 border-t border-gray-100 dark:border-gray-800">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div className="flex items-center gap-2">
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-gray-500 dark:text-gray-400">
                      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                    </svg>
                    <span className="text-sm text-gray-700 dark:text-gray-200">Fair Usage</span>
                    {fuRules.length > 1 ? (
                      <span className="text-[11px] text-gray-500 dark:text-gray-400">({fuRules.length} rules)</span>
                    ) : null}
                  </div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`rounded-full px-2.5 py-0.5 text-[11px] ${fuStatus.throttled ? "bg-amber-100 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300" : "bg-green-100 text-green-800 dark:bg-green-500/10 dark:text-green-300"}`}>
                      {fuStatus.throttled ? "Throttled" : "Normal"}
                    </span>
                    {fuStatus.throttled && (() => {
                      const effective = effectiveFuRule;
                      const eff = effective ? effectiveThrottleForRule(effective) : null;
                      const d = eff?.dl ?? fuStatus.throttle_download_kbps;
                      const u = eff?.ul ?? fuStatus.throttle_upload_kbps;
                      const label = eff?.label ?? fuStatus.rule_name ?? "Rule";
                      return (
                        <span className="text-[11px] text-amber-700 dark:text-amber-300">
                          {label}: {(d / 1000).toFixed(1)}/{(u / 1000).toFixed(1)} Mbps
                        </span>
                      );
                    })()}
                    {fuStatus.throttled && (
                      <button
                        type="button"
                        disabled={fuResetBusy}
                        onClick={async () => {
                          setFuResetBusy(true);
                          try {
                            await resetFairUsagePeer(peerId);
                            await loadFuStatus();
                          } catch { }
                          setFuResetBusy(false);
                        }}
                        className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                      >
                        {fuResetBusy ? "Resetting..." : "Reset throttle"}
                      </button>
                    )}
                  </div>
                </div>
                <div className="grid gap-4">
                  {fuRules.map((fr) => (
                    <div
                      key={fr.rule_id}
                      className={`rounded-xl ring-1 p-3 grid gap-2 ${
                        fr.over_quota ? "ring-amber-300/60 bg-amber-500/5 dark:ring-amber-500/30" : "ring-gray-200 dark:ring-gray-700 bg-gray-50/50 dark:bg-gray-950/40"
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2 flex-wrap">
                        <div className="text-xs text-gray-600 dark:text-gray-300">
                          Rule: <span className="font-medium text-gray-900 dark:text-gray-100">{fr.rule_name}</span>
                          {fr.over_quota ? <span className="ml-2 text-amber-700 dark:text-amber-300">Matched</span> : null}
                          {fr.is_effective ? <span className="ml-2 text-indigo-700 dark:text-indigo-300">Effective</span> : null}
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="rounded-full bg-gray-100 text-gray-700 px-2 py-0.5 text-[11px] dark:bg-gray-800 dark:text-gray-300">#{fr.sort_order ?? 0}</span>
                          <span className="rounded-full bg-gray-100 text-gray-700 px-2 py-0.5 text-[11px] dark:bg-gray-800 dark:text-gray-300">{fr.scope_label}</span>
                          <span className="rounded-full bg-indigo-50 text-indigo-700 px-2 py-0.5 text-[11px] dark:bg-indigo-500/10 dark:text-indigo-300 capitalize">{fr.scope_type}</span>
                          <span className={`rounded-full px-2 py-0.5 text-[11px] ${fr.passthrough ? "bg-amber-50 text-amber-700 dark:bg-amber-500/10 dark:text-amber-300" : "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400"}`}>
                            {fr.passthrough ? "Pass" : "Stop"}
                          </span>
                        </div>
                      </div>
                      <div className="grid gap-3 md:grid-cols-2">
                        {fr.tiered && fr.tiers && fr.tiers.length > 0 ? (
                          <div className="md:col-span-2 grid gap-2">
                            <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
                              <span>Combined usage (tiered)</span>
                              <span>
                                {fmtBytes(fr.used_rx + fr.used_tx)} / max {fmtBytes(Math.max(...fr.tiers.map((t) => t.threshold_bytes)))}
                              </span>
                            </div>
                            <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
                              <div
                                className={`h-full ${fr.over_quota ? "bg-amber-500" : "bg-gray-900 dark:bg-gray-100"}`}
                                style={{
                                  width: `${(() => {
                                    const used = fr.used_rx + fr.used_tx;
                                    const cap = Math.max(...fr.tiers.map((t) => t.threshold_bytes), 1);
                                    return Math.min(100, Math.round((used / cap) * 100));
                                  })()}%`,
                                }}
                              />
                            </div>
                            <div className="grid gap-1.5 mt-1">
                              {fr.tiers.map((t) => (
                                <div
                                  key={t.tier_id}
                                  className={`flex flex-wrap items-center justify-between gap-2 text-[11px] rounded-lg px-2 py-1.5 ${
                                    t.is_active ? "bg-amber-100/80 dark:bg-amber-500/15 text-amber-900 dark:text-amber-200" : "bg-gray-100/80 dark:bg-gray-800/80 text-gray-600 dark:text-gray-400"
                                  }`}
                                >
                                  <span>
                                    ≥ {fmtBytes(t.threshold_bytes)}
                                    {(t.name || "").trim() ? ` · ${t.name.trim()}` : ""}
                                    {t.is_active ? " · active" : ""}
                                  </span>
                                  <span className="font-mono">
                                    {(t.throttle_download_kbps / 1000).toFixed(1)}/{(t.throttle_upload_kbps / 1000).toFixed(1)} Mbps
                                  </span>
                                </div>
                              ))}
                            </div>
                          </div>
                        ) : (
                        <div>
                          <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
                            <span>{fr.quota_mode === "combined" ? "Total usage" : "Download"}</span>
                            <span>
                              {(() => {
                                const used = fr.quota_mode === "combined" ? fr.used_rx + fr.used_tx : fr.used_rx;
                                const limit = fr.download_quota_bytes;
                                const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
                                return `${fmtBytes(used)} / ${fmtBytes(limit)} (${pct}%)`;
                              })()}
                            </span>
                          </div>
                          <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
                            <div
                              className={`h-full ${fr.over_quota ? "bg-amber-500" : "bg-gray-900 dark:bg-gray-100"}`}
                              style={{
                                width: `${(() => {
                                  const used = fr.quota_mode === "combined" ? fr.used_rx + fr.used_tx : fr.used_rx;
                                  return Math.min(100, Math.round((used / Math.max(1, fr.download_quota_bytes)) * 100));
                                })()}%`,
                              }}
                            />
                          </div>
                        </div>
                        )}
                        {fr.quota_mode === "independent" && fr.upload_quota_bytes ? (
                          <div>
                            <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400 mb-1">
                              <span>Upload</span>
                              <span>
                                {fmtBytes(fr.used_tx)} / {fmtBytes(fr.upload_quota_bytes)} ({Math.min(100, Math.round((fr.used_tx / Math.max(1, fr.upload_quota_bytes)) * 100))}%)
                              </span>
                            </div>
                            <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden">
                              <div
                                className={`h-full ${fr.over_quota ? "bg-amber-500" : "bg-gray-900 dark:bg-gray-100"}`}
                                style={{ width: `${Math.min(100, Math.round((fr.used_tx / Math.max(1, fr.upload_quota_bytes)) * 100))}%` }}
                              />
                            </div>
                          </div>
                        ) : null}
                      </div>
                      {fr.next_reset && (
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                          Resets:{" "}
                          {fr.scope_period_unit === "hour"
                            ? formatCalendarDateTime(fr.next_reset, { timeZone: timezone || "UTC", dateCalendar, includeTime: true })
                            : formatCalendarDateTime(fr.next_reset, { timeZone: timezone || "UTC", dateCalendar, includeTime: false })}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="pt-6 border-t border-gray-100 dark:border-gray-800">
                <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                  <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
                  </svg>
                  No fair usage rule applies to this peer.
                  <a href="/fair-usage" className="text-indigo-600 hover:underline dark:text-indigo-400">Manage rules</a>
                </div>
              </div>
            )}

	            {/* Client config */}
	            <div className="grid gap-3 pt-4 border-t border-gray-100 dark:border-gray-800">
	              <div className="flex items-center justify-between gap-3">
	                <div className="text-sm text-gray-700 dark:text-gray-200">Client config</div>
	                <div className="flex items-center gap-2">
	                  <button
	                    type="button"
	                    onClick={() => setShowClientSecrets((s) => !s)}
	                    className="inline-flex items-center justify-center rounded-full p-2 text-gray-600 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-400 dark:hover:bg-gray-800 dark:hover:text-gray-200"
	                    aria-label={showClientSecrets ? "Hide sensitive config" : "Show sensitive config"}
	                    title={showClientSecrets ? "Hide sensitive config" : "Show sensitive config"}
	                  >
	                    {showClientSecrets ? (
	                      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
	                        <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" />
	                        <circle cx="12" cy="12" r="3" />
	                      </svg>
	                    ) : (
	                      <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
	                        <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
	                        <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
	                        <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
	                        <line x1="2" y1="2" x2="22" y2="22" />
	                      </svg>
	                    )}
	                  </button>
	                  <button
	                    type="button"
	                    role="switch"
	                    aria-checked={showClientSecrets}
	                    aria-label={showClientSecrets ? "Hide sensitive config" : "Show sensitive config"}
	                    onClick={() => setShowClientSecrets((s) => !s)}
	                    className="inline-flex items-center focus:outline-none focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 rounded-full"
	                  >
	                    <span
	                      className={`relative h-6 w-11 rounded-full transition-colors ${
	                        showClientSecrets ? "bg-gray-700 dark:bg-gray-300" : "bg-gray-300 dark:bg-gray-700"
	                      }`}
	                    >
	                      <span
	                        className={`absolute top-[2px] block h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
	                          showClientSecrets ? "translate-x-[22px]" : "translate-x-[2px]"
	                        }`}
	                      />
	                    </span>
	                  </button>
	                </div>
	              </div>
	              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
	                <div className="grid gap-3">
	                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
	                    <div className="grid gap-1">
	                      <div className="text-gray-500 dark:text-gray-400">Private key</div>
	                      <div className="flex items-center gap-2">
	                        <input
	                          type={showClientSecrets ? "text" : "password"}
	                          value={clientCfg.privateKey}
	                          onChange={(e) => setClientCfg((c) => ({ ...c, privateKey: e.target.value }))}
	                          placeholder="base64 32-byte key"
	                          className="w-full rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 font-mono"
	                        />
	                        <RenewKeyButton
	                          onClick={() => setConfirmRenewKeys(true)}
	                          disabled={actionBusy || !peer}
	                          title="Renew private keys"
	                        />
	                      </div>
	                      {(clientCfg.privateKey || "").trim() && !isValidWgPrivateKey && (
	                        <div className="text-xs text-rose-600">Private key must be base64 (32 bytes).</div>
	                      )}
	                    </div>
	                    <div className="grid gap-1">
	                      <div className="text-gray-500 dark:text-gray-400">DNS (optional)</div>
	                      <input
	                        type="text"
	                        value={clientCfg.dns}
	                        onChange={(e) => setClientCfg((c) => ({ ...c, dns: e.target.value }))}
	                        className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
	                      />
	                    </div>
	                    <div className="grid gap-1">
	                      <div className="text-gray-500 dark:text-gray-400">MTU (optional)</div>
	                      <input
	                        type="text"
	                        value={clientCfg.mtu}
	                        onChange={(e) => setClientCfg((c) => ({ ...c, mtu: e.target.value }))}
	                        className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
	                      />
	                    </div>
	                    <div className="grid gap-1">
	                      <div className="text-gray-500 dark:text-gray-400">Keepalive (optional)</div>
	                      <input
	                        type="text"
	                        value={clientCfg.persistentKeepalive}
	                        onChange={(e) => setClientCfg((c) => ({ ...c, persistentKeepalive: e.target.value }))}
	                        className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
	                      />
	                    </div>
	                    <div className="grid gap-1 md:col-span-2">
	                      <div className="text-gray-500 dark:text-gray-400">Allowed IPs (optional)</div>
	                      <input
	                        type="text"
	                        value={clientCfg.allowedIps}
	                        onChange={(e) => setClientCfg((c) => ({ ...c, allowedIps: e.target.value }))}
	                        className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
	                      />
	                    </div>
	                    <div className="grid gap-1 md:col-span-2">
	                      <div className="text-gray-500 dark:text-gray-400">Preshared key (optional)</div>
	                      <div className="flex items-center gap-2">
	                        <input
	                          type={showClientSecrets ? "text" : "password"}
	                          value={clientCfg.presharedKey}
	                          onChange={(e) => setClientCfg((c) => ({ ...c, presharedKey: e.target.value }))}
	                          placeholder="base64 32-byte key"
	                          className="w-full rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 font-mono"
	                        />
	                        <RenewKeyButton
	                          onClick={() => setConfirmRenewPsk(true)}
	                          disabled={!peer}
	                          title="Generate new preshared key"
	                        />
	                      </div>
	                      {(clientCfg.presharedKey || "").trim() && !isValidWgPresharedKey && (
	                        <div className="text-xs text-rose-600">Preshared key must be base64 (32 bytes), or leave empty.</div>
	                      )}
	                    </div>
	                    <div className="grid gap-1">
	                      <div className="text-gray-500 dark:text-gray-400">Config name (optional)</div>
	                      <input
	                        type="text"
	                        value={clientCfg.configName}
	                        onChange={(e) => setClientCfg((c) => ({ ...c, configName: e.target.value }))}
	                        placeholder="Used as download filename"
	                        className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
	                      />
	                    </div>
	                    <div className="grid gap-1">
	                      <div className="text-gray-500 dark:text-gray-400">Custom endpoint (optional)</div>
	                      <input
	                        type="text"
	                        value={clientCfg.customEndpoint}
	                        onChange={(e) => setClientCfg((c) => ({ ...c, customEndpoint: e.target.value }))}
	                        placeholder="e.g. vpn.example.com or 1.2.3.4:8080"
	                        className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700"
	                      />
	                    </div>
	                    <div className="md:col-span-2 flex flex-wrap items-center gap-x-3 gap-y-1 min-h-[1.25rem]">
	                      {exportPrefsSaveState === "saving" ? (
	                        <span className="text-xs text-gray-500 dark:text-gray-400">Saving…</span>
	                      ) : null}
	                      {exportPrefsSaveState === "saved" ? (
	                        <span className="text-xs text-emerald-600 dark:text-emerald-400">Saved</span>
	                      ) : null}
	                      {exportPrefsErr ? <span className="text-xs text-rose-600">{exportPrefsErr}</span> : null}
	                    </div>
	                  </div>
	                  <div className="grid gap-1">
	                    <div className="text-gray-500 dark:text-gray-400 text-sm">Config</div>
	                    <textarea
	                      readOnly
	                      value={clientConfig}
	                      rows={12}
	                      className={`w-full rounded-2xl border border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-950 px-3 py-2 text-xs font-mono text-gray-900 dark:text-gray-100 focus:outline-none transition-[filter] ${
	                        showClientSecrets ? "" : "blur-[3px] select-none"
	                      }`}
	                    />
	                    <div className="mt-3 flex flex-wrap items-center gap-2">
	                      <button
	                        type="button"
	                        onClick={async () => {
	                          try {
	                            await navigator.clipboard.writeText(clientConfig);
	                          } catch {
	                            // ignore
	                          }
	                        }}
	                        disabled={!clientConfig}
	                        className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 disabled:opacity-50 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
	                      >
	                        Copy
	                      </button>
	                      <button
	                        type="button"
	                        onClick={downloadClientConfigFile}
	                        disabled={!clientConfig}
	                        className="rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
	                      >
	                        Save config file
	                      </button>
	                    </div>
	                  </div>
	                </div>
	                <div className="rounded-3xl ring-1 ring-gray-200 dark:ring-gray-800 bg-white dark:bg-gray-950 p-4 flex items-center justify-center min-h-[240px]">
	                  {isValidWgPrivateKey ? (
	                    <div className={`transition-[filter] ${showClientSecrets ? "" : "blur-[3px] select-none"}`}>
	                      <QRCode value={clientConfig} size={220} />
	                    </div>
	                  ) : (
	                    <div className="text-sm text-gray-500 dark:text-gray-400 text-center">
	                      Paste a valid private key to render a QR code.
	                    </div>
	                  )}
	                </div>
	              </div>
	            </div>

            {/* Details + Activity log */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-4 border-t border-gray-100 dark:border-gray-800">
              <div className="grid gap-3 self-start">
                <div className="text-sm text-gray-700 dark:text-gray-200">Details</div>
                <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                  <div className="text-gray-500 dark:text-gray-400">Interface</div>
                  <LockedField value={peer.interface} />
                  <div className="text-gray-500 dark:text-gray-400">Router</div>
                  <LockedField value={routerName || `#${peer.router_id} `} />
                  <div className="text-gray-500 dark:text-gray-400">Public key</div>
                  <LockedField value={peer.public_key} mono />
                  <div className="text-gray-500 dark:text-gray-400">Endpoint</div>
                  <LockedField value={liveEndpoint} mono />
                  <div className="text-gray-500 dark:text-gray-400">Last seen</div>
                  <LockedField value={lastSeenLabel} />
                </div>
              </div>
              <div className="grid gap-3 self-start">
                <div className="flex items-center justify-between gap-3">
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
                <div className="grid max-h-72 gap-2 overflow-y-auto overscroll-y-contain pr-1">
                  {actions.map((a, idx) => {
                    const tsLabel = (() => {
                      try {
                        const d = new Date(a.ts);
                        return formatCalendarDateTime(d, { timeZone: timezone || "UTC", dateCalendar, includeTime: true });
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
                        key={`${a.ts}-${a.action}-${idx}`}
                        className="rounded-xl border border-dashed border-gray-200 dark:border-gray-700 bg-white/50 dark:bg-gray-950 px-3 py-2"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] ${badgeCls}`}>
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
            </div>
            <div className="grid gap-2">
              <div className="flex flex-wrap items-center justify-end gap-3">
                {peer.selected === false && peer.router_sync_status === "synced" && (
                  <button
                    disabled={actionBusy || !peer}
                    onClick={async () => {
                      if (!peer) return;
                      setActionErr("");
                      try {
                        setActionBusy(true);
                        const saved = await patchPeer(peer.id, { selected: true });
                        setPeer({ ...peer, selected: saved.selected });
                        await fetchActions(actionsLimit);
                      } catch (e: any) {
                        setActionErr(e?.message || "Failed to show peer");
                      } finally {
                        setActionBusy(false);
                      }
                    }}
                    className="inline-flex items-center gap-2 rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200 disabled:opacity-50 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                  >
                    Show peer
                  </button>
                )}
                <button
                  disabled={actionBusy || !peer}
                  onClick={() => setConfirmDelete(true)}
                  className="inline-flex items-center gap-2 rounded-full bg-rose-600 text-white px-4 py-2 text-sm shadow hover:bg-rose-700 disabled:opacity-50"
                >
                  Remove peer
                </button>
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
                  className="inline-flex items-center gap-2 rounded-full bg-gray-900 text-white px-4 py-2 text-sm shadow hover:bg-black disabled:opacity-50 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
                >
                  {peer?.disabled ? 'Enable peer' : 'Disable peer'}
                </button>
              </div>
              {actionErr && <div className="text-sm text-red-600 text-right">{actionErr}</div>}
            </div>
          </div>
        )}
        {confirmRenewKeys && peer && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
            <div className="w-full max-w-sm rounded-2xl bg-white ring-1 ring-gray-200 shadow-lg p-6 grid gap-4">
              <div className="text-lg font-semibold text-gray-900">Renew private keys</div>
              <div className="text-sm text-gray-600">
                This generates a new WireGuard keypair for <span className="font-medium text-gray-900">{peer.name}</span>, updates the router, and replaces the current client config. Existing clients using the old keypair will stop working until they import the new config.
              </div>
              <div className="flex items-center justify-end gap-3">
                <button
                  onClick={() => setConfirmRenewKeys(false)}
                  className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200"
                >
                  Cancel
                </button>
                <button
                  disabled={actionBusy}
                  onClick={async () => {
                    if (!peer) return;
                    setActionErr("");
                    try {
                      setActionBusy(true);
                      const res = await renewPeerKeys(peer.id);
                      setPeer((current) => (current ? { ...current, ...res.peer } : current));
                      setClientCfg((c) => ({ ...c, privateKey: res.private_key || "" }));
                      await fetchActions(actionsLimit);
                      setConfirmRenewKeys(false);
                    } catch (e: any) {
                      setActionErr(e?.message || "Failed to renew private keys");
                      setConfirmRenewKeys(false);
                    } finally {
                      setActionBusy(false);
                    }
                  }}
                  className="rounded-full bg-amber-500 text-white px-4 py-2 text-sm shadow hover:bg-amber-600 disabled:opacity-50"
                >
                  Renew keys
                </button>
              </div>
            </div>
          </div>
        )}
        {confirmRenewPsk && peer && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
            <div className="w-full max-w-sm rounded-2xl bg-white ring-1 ring-gray-200 shadow-lg p-6 grid gap-4">
              <div className="text-lg font-semibold text-gray-900">Generate new preshared key</div>
              <div className="text-sm text-gray-600">
                This generates a new preshared key for <span className="font-medium text-gray-900">{peer.name}</span>, updates the router, and replaces the current client config. Existing clients using the old preshared key will stop working until they import the new config.
              </div>
              <div className="flex items-center justify-end gap-3">
                <button
                  onClick={() => setConfirmRenewPsk(false)}
                  className="rounded-full bg-gray-100 text-gray-800 px-4 py-2 text-sm shadow hover:bg-gray-200"
                >
                  Cancel
                </button>
                <button
                  onClick={() => {
                    setClientCfg((c) => ({ ...c, presharedKey: generatePresharedKey() }));
                    setConfirmRenewPsk(false);
                  }}
                  className="rounded-full bg-amber-500 text-white px-4 py-2 text-sm shadow hover:bg-amber-600 disabled:opacity-50"
                >
                  Generate key
                </button>
              </div>
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
    </div>
  );
}
