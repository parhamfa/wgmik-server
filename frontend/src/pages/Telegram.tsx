import React from "react";
import { Link } from "react-router-dom";
import {
  getTelegramConfig,
  updateTelegramConfig,
  getTelegramStatus,
  restartTelegramBot,
  createTelegramToken,
  listTelegramTokens,
  revokeTelegramToken,
  listTelegramUsers,
  deleteTelegramUser,
  patchTelegramUser,
  setTelegramUserPeers,
  getTelegramNotifConfig,
  updateTelegramNotifConfig,
  createTelegramBroadcast,
  listTelegramBroadcasts,
  getTelegramBroadcast,
  retryFailedTelegramBroadcast,
  testTelegramNotify,
  testTelegramNotifyEvent,
  listRouters,
  listSavedPeers,
  getSettings,
  type TelegramConfigDTO,
  type TelegramStatusDTO,
  type TelegramTokenDTO,
  type TelegramUserDTO,
  type TelegramNotifConfigDTO,
  type TelegramBroadcastDTO,
  type TelegramBroadcastDetailDTO,
  type PeerListDTO,
  type Router,
  type SettingsDTO,
} from "../api";
import { formatCalendarDateTime } from "../datetimeLocal";

function Card({ className = "", ...props }: React.HTMLAttributes<HTMLDivElement>) {
  const base =
    "rounded-3xl overflow-hidden ring-1 ring-gray-200 ring-offset-2 ring-offset-gray-50 bg-white shadow-md hover:shadow-lg transition transform hover:-translate-y-0.5 dark:ring-gray-800 dark:ring-offset-gray-950 dark:bg-gray-900";
  return <div className={`${base} ${className}`} {...props} />;
}

const EVENT_LABELS: Record<string, string> = {
  quota_warning_80: "Quota Warning (80%)",
  quota_warning_90: "Quota Warning (90%)",
  quota_hit: "Quota Reached / Throttled",
  quota_lifted: "Quota Lifted / Reset",
  daily_summary: "Daily Summary",
  weekly_summary: "Weekly Summary",
};

const BROADCAST_STATUS_LABELS: Record<string, string> = {
  pending: "Pending",
  queued: "Queued",
  sending: "Sending",
  sent: "Sent",
  acknowledged: "Acknowledged",
  partial_failed: "Partial failure",
  failed: "Failed",
};

function broadcastStatusLabel(status: string) {
  return BROADCAST_STATUS_LABELS[status] || status;
}

function telegramUserLabel(user: TelegramUserDTO) {
  if (user.telegram_username) return `@${user.telegram_username}`;
  const name = `${user.first_name || ""} ${user.last_name || ""}`.trim();
  return name || `User #${user.id}`;
}

function filterPeers(peers: PeerListDTO[], search: string, routerById: Record<number, Router>) {
  if (!search.trim()) return peers;
  const q = search.toLowerCase();
  return peers.filter(p =>
    p.name.toLowerCase().includes(q) ||
    p.allowed_address.toLowerCase().includes(q) ||
    (routerById[p.router_id]?.name || "").toLowerCase().includes(q)
  );
}

function PeerSelectField({
  label,
  search,
  onSearchChange,
  peers,
  selectedIds,
  onToggle,
  routerById,
}: {
  label: string;
  search: string;
  onSearchChange: (value: string) => void;
  peers: PeerListDTO[];
  selectedIds: number[];
  onToggle: (peerId: number, checked: boolean) => void;
  routerById: Record<number, Router>;
}) {
  const filtered = React.useMemo(() => {
    const matched = filterPeers(peers, search, routerById);
    const selected = new Set(selectedIds);
    return [...matched].sort((a, b) => {
      const aSelected = selected.has(a.id);
      const bSelected = selected.has(b.id);
      if (aSelected !== bSelected) return aSelected ? -1 : 1;
      return (a.name || a.public_key).localeCompare(b.name || b.public_key, undefined, { sensitivity: "base" });
    });
  }, [peers, search, routerById, selectedIds]);

  return (
    <div className="grid gap-1">
      <label className="text-xs text-gray-500 dark:text-gray-400">{label}</label>
      <input
        value={search}
        onChange={e => onSearchChange(e.target.value)}
        placeholder="Search peers..."
        className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
      />
      <div className="max-h-48 overflow-y-auto rounded-xl border border-gray-100 dark:border-gray-800 mt-1">
        {filtered.map(p => (
          <label key={p.id} className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-800/50 cursor-pointer text-sm">
            <input
              type="checkbox"
              checked={selectedIds.includes(p.id)}
              onChange={e => onToggle(p.id, e.target.checked)}
              className="rounded"
            />
            <span className="text-gray-700 dark:text-gray-300">{p.name || p.public_key.slice(0, 16)}</span>
            <span className="text-xs text-gray-400 ml-auto">
              {routerById[p.router_id]?.name || `R#${p.router_id}`} / {p.interface}
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}

export default function TelegramPage() {
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState("");
  const [success, setSuccess] = React.useState("");

  // Config
  const [config, setConfig] = React.useState<TelegramConfigDTO | null>(null);
  const [botStatus, setBotStatus] = React.useState<TelegramStatusDTO | null>(null);
  const [tokenInput, setTokenInput] = React.useState("");
  const savedTokenRef = React.useRef("");
  const [showToken, setShowToken] = React.useState(false);
  const [chatIdInput, setChatIdInput] = React.useState("");
  const [langInput, setLangInput] = React.useState("both");
  const [enabledInput, setEnabledInput] = React.useState(false);

  // Notifications
  const [notifConfigs, setNotifConfigs] = React.useState<TelegramNotifConfigDTO[]>([]);
  const [testingNotifEvent, setTestingNotifEvent] = React.useState<string | null>(null);

  // Tokens
  const [tokens, setTokens] = React.useState<TelegramTokenDTO[]>([]);
  const [showTokenForm, setShowTokenForm] = React.useState(false);
  const [tokenPeerIds, setTokenPeerIds] = React.useState<number[]>([]);
  const [tokenExpiry, setTokenExpiry] = React.useState(72);
  const [tokenSingleUse, setTokenSingleUse] = React.useState(true);
  const [tokenSearch, setTokenSearch] = React.useState("");
  const [createdToken, setCreatedToken] = React.useState<TelegramTokenDTO | null>(null);

  // Users
  const [users, setUsers] = React.useState<TelegramUserDTO[]>([]);
  const [userSearch, setUserSearch] = React.useState("");
  const [userPeersModalUser, setUserPeersModalUser] = React.useState<TelegramUserDTO | null>(null);
  const [userPeerSearch, setUserPeerSearch] = React.useState("");
  const [userPeerDraft, setUserPeerDraft] = React.useState<Record<number, number[]>>({});
  const [savingUserPeers, setSavingUserPeers] = React.useState<number | null>(null);

  // Broadcasts
  const [broadcasts, setBroadcasts] = React.useState<TelegramBroadcastDTO[]>([]);
  const [broadcastTotal, setBroadcastTotal] = React.useState(0);
  const [broadcastText, setBroadcastText] = React.useState("");
  const [broadcastMode, setBroadcastMode] = React.useState<"all" | "selected">("all");
  const [broadcastRecipientIds, setBroadcastRecipientIds] = React.useState<number[]>([]);
  const [broadcastUserSearch, setBroadcastUserSearch] = React.useState("");
  const [broadcastPhoto, setBroadcastPhoto] = React.useState<File | null>(null);
  const [sendingBroadcast, setSendingBroadcast] = React.useState(false);
  const [broadcastDetail, setBroadcastDetail] = React.useState<TelegramBroadcastDetailDTO | null>(null);
  const [loadingBroadcastDetail, setLoadingBroadcastDetail] = React.useState(false);
  const [retryingBroadcastId, setRetryingBroadcastId] = React.useState<number | null>(null);

  // Peers + Routers for token creation
  const [peers, setPeers] = React.useState<PeerListDTO[]>([]);
  const [routers, setRouters] = React.useState<Router[]>([]);
  const [settings, setSettings] = React.useState<SettingsDTO | null>(null);

  const routerById = React.useMemo(() => {
    const m: Record<number, Router> = {};
    for (const r of routers) m[r.id] = r;
    return m;
  }, [routers]);

  const load = React.useCallback(async () => {
    try {
      const [cfg, st, toks, usrs, nc, rts, prs, appSettings, bcasts] = await Promise.all([
        getTelegramConfig(),
        getTelegramStatus(),
        listTelegramTokens(),
        listTelegramUsers(),
        getTelegramNotifConfig(),
        listRouters(),
        listSavedPeers(),
        getSettings(),
        listTelegramBroadcasts({ limit: 25 }),
      ]);
      setConfig(cfg);
      setBotStatus(st);
      setTokens(toks);
      setUsers(usrs);
      setNotifConfigs(nc);
      setRouters(rts);
      setPeers(prs);
      setSettings(appSettings);
      setBroadcasts(bcasts.items);
      setBroadcastTotal(bcasts.total);
      const token = cfg.tg_bot_token || "";
      setTokenInput(token);
      savedTokenRef.current = token;
      setChatIdInput(cfg.tg_admin_chat_id || "");
      setLangInput(cfg.tg_bot_language || "both");
      setEnabledInput(cfg.tg_bot_enabled === "true" || cfg.tg_bot_enabled === "1");
    } catch (e: any) {
      setErr(e?.message || "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => { load(); }, [load]);

  const flash = (msg: string) => { setSuccess(msg); setTimeout(() => setSuccess(""), 3000); };

  const saveConfig = async () => {
    setErr("");
    try {
      const payload: any = {
        tg_bot_enabled: enabledInput,
        tg_admin_chat_id: chatIdInput,
        tg_bot_language: langInput,
      };
      if (tokenInput !== savedTokenRef.current) payload.tg_bot_token = tokenInput;
      await updateTelegramConfig(payload);
      flash("Configuration saved");
      await load();
    } catch (e: any) {
      setErr(e?.message || "Save failed");
    }
  };

  const handleRestart = async () => {
    setErr("");
    try {
      const res = await restartTelegramBot();
      flash(res.started ? "Bot restarted" : "Bot could not start (check config)");
      await load();
    } catch (e: any) {
      setErr(e?.message || "Restart failed");
    }
  };

  const handleTestNotify = async () => {
    setErr("");
    try {
      await testTelegramNotify();
      flash("Test message sent!");
    } catch (e: any) {
      setErr(e?.message || "Test failed");
    }
  };

  const handleTestEventNotify = async (eventType: string) => {
    setErr("");
    setTestingNotifEvent(eventType);
    try {
      await testTelegramNotifyEvent(eventType);
      flash(`Test sent for ${EVENT_LABELS[eventType] || eventType}`);
    } catch (e: any) {
      setErr(e?.message || "Test failed");
    } finally {
      setTestingNotifEvent(null);
    }
  };

  const handleCreateToken = async () => {
    if (tokenPeerIds.length === 0) { setErr("Select at least one peer"); return; }
    setErr("");
    try {
      const tok = await createTelegramToken({
        peer_ids: tokenPeerIds,
        expires_hours: tokenExpiry > 0 ? tokenExpiry : undefined,
        single_use: tokenSingleUse,
      });
      setCreatedToken(tok);
      setShowTokenForm(false);
      setTokenPeerIds([]);
      await load();
    } catch (e: any) {
      setErr(e?.message || "Token creation failed");
    }
  };

  const handleNotifToggle = async (eventType: string, field: "notify_clients" | "notify_admin" | "enabled", value: boolean) => {
    setErr("");
    try {
      await updateTelegramNotifConfig([{ event_type: eventType, [field]: value }]);
      setNotifConfigs(prev => prev.map(c => c.event_type === eventType ? { ...c, [field]: value } : c));
    } catch (e: any) {
      setErr(e?.message || "Update failed");
    }
  };

  const filteredUsers = React.useMemo(() => {
    if (!userSearch.trim()) return users;
    const q = userSearch.toLowerCase();
    return users.filter(u =>
      u.telegram_username.toLowerCase().includes(q) ||
      u.first_name.toLowerCase().includes(q) ||
      u.last_name.toLowerCase().includes(q) ||
      u.peers.some(p => p.peer_name.toLowerCase().includes(q))
    );
  }, [users, userSearch]);

  const eligibleBroadcastUsers = React.useMemo(() => users.filter(u => !u.is_blocked), [users]);

  const filteredBroadcastUsers = React.useMemo(() => {
    const q = broadcastUserSearch.trim().toLowerCase();
    const selected = new Set(broadcastRecipientIds);
    const matched = !q
      ? eligibleBroadcastUsers
      : eligibleBroadcastUsers.filter(u =>
          telegramUserLabel(u).toLowerCase().includes(q) ||
          u.peers.some(p => p.peer_name.toLowerCase().includes(q))
        );
    return [...matched].sort((a, b) => {
      const aSelected = selected.has(a.id);
      const bSelected = selected.has(b.id);
      if (aSelected !== bSelected) return aSelected ? -1 : 1;
      return telegramUserLabel(a).localeCompare(telegramUserLabel(b), undefined, { sensitivity: "base" });
    });
  }, [eligibleBroadcastUsers, broadcastRecipientIds, broadcastUserSearch]);

  const broadcastTargetCount = broadcastMode === "all" ? eligibleBroadcastUsers.length : broadcastRecipientIds.length;

  const toggleBroadcastRecipient = (userId: number, checked: boolean) => {
    setBroadcastRecipientIds(prev => {
      if (checked) return prev.includes(userId) ? prev : [...prev, userId];
      return prev.filter(id => id !== userId);
    });
  };

  const refreshBroadcasts = async () => {
    const rows = await listTelegramBroadcasts({ limit: 25 });
    setBroadcasts(rows.items);
    setBroadcastTotal(rows.total);
  };

  const handleSendBroadcast = async () => {
    const text = broadcastText.trim();
    if (!text) { setErr("Message text is required"); return; }
    if (broadcastMode === "selected" && broadcastRecipientIds.length === 0) { setErr("Select at least one recipient"); return; }
    if (broadcastTargetCount === 0) { setErr("No eligible recipients"); return; }
    if (broadcastPhoto && broadcastPhoto.size > 10 * 1024 * 1024) { setErr("Photo must be 10 MB or smaller"); return; }
    if (broadcastPhoto && text.length > 1024) { setErr("Photo captions must be 1024 characters or less"); return; }
    if (!broadcastPhoto && text.length > 4096) { setErr("Text messages must be 4096 characters or less"); return; }
    if (!confirm(`Send this broadcast to ${broadcastTargetCount} user(s)?`)) return;
    setErr("");
    setSendingBroadcast(true);
    try {
      await createTelegramBroadcast({
        text,
        recipient_mode: broadcastMode,
        recipient_ids: broadcastMode === "selected" ? broadcastRecipientIds : [],
        photo: broadcastPhoto,
      });
      setBroadcastText("");
      setBroadcastPhoto(null);
      setBroadcastRecipientIds([]);
      setBroadcastMode("all");
      await refreshBroadcasts();
      flash("Broadcast queued");
    } catch (e: any) {
      setErr(e?.message || "Broadcast failed");
    } finally {
      setSendingBroadcast(false);
    }
  };

  const openBroadcastDetail = async (id: number) => {
    setErr("");
    setLoadingBroadcastDetail(true);
    try {
      setBroadcastDetail(await getTelegramBroadcast(id));
    } catch (e: any) {
      setErr(e?.message || "Failed to load broadcast");
    } finally {
      setLoadingBroadcastDetail(false);
    }
  };

  const handleRetryBroadcast = async (id: number) => {
    setErr("");
    setRetryingBroadcastId(id);
    try {
      const res = await retryFailedTelegramBroadcast(id);
      flash(res.queued ? `Retry queued for ${res.queued} recipient(s)` : "No failed recipients to retry");
      await refreshBroadcasts();
      if (broadcastDetail?.id === id) setBroadcastDetail(await getTelegramBroadcast(id));
    } catch (e: any) {
      setErr(e?.message || "Retry failed");
    } finally {
      setRetryingBroadcastId(null);
    }
  };

  const toggleUserPeerDraft = (userId: number, peerId: number, checked: boolean) => {
    setUserPeerDraft(prev => {
      const current = prev[userId] || [];
      if (checked) {
        if (current.includes(peerId)) return prev;
        return { ...prev, [userId]: [...current, peerId] };
      }
      return { ...prev, [userId]: current.filter(id => id !== peerId) };
    });
  };

  const openUserPeersModal = (u: TelegramUserDTO) => {
    setUserPeerSearch("");
    setUserPeersModalUser(u);
    setUserPeerDraft(prev => ({
      ...prev,
      [u.id]: prev[u.id] ?? u.peers.map(p => p.peer_id),
    }));
  };

  const applyUserPeers = async (u: TelegramUserDTO) => {
    setErr("");
    setSavingUserPeers(u.id);
    try {
      const peer_ids = userPeerDraft[u.id] || [];
      await setTelegramUserPeers(u.id, peer_ids);
      await load();
      setUserPeersModalUser(null);
      flash("Peer bindings updated");
    } catch (e: any) {
      setErr(e?.message || "Failed to update peer bindings");
    } finally {
      setSavingUserPeers(null);
    }
  };

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
        <h1 className="text-xl font-semibold text-gray-900 dark:text-gray-100">Telegram Bot</h1>
        <Link
          to="/"
          className="inline-flex items-center gap-2 rounded-full bg-white text-gray-900 px-4 py-1.5 text-sm ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-800"
        >
          &larr; Dashboard
        </Link>
      </div>

      {err && (
        <div className="mb-4 rounded-xl bg-red-50 text-red-700 px-4 py-2 text-sm ring-1 ring-red-200 dark:bg-red-900/30 dark:text-red-300 dark:ring-red-800">
          {err}
        </div>
      )}
      {success && (
        <div className="mb-4 rounded-xl bg-green-50 text-green-700 px-4 py-2 text-sm ring-1 ring-green-200 dark:bg-green-900/30 dark:text-green-300 dark:ring-green-800">
          {success}
        </div>
      )}

      <div className="mx-auto w-full max-w-[960px] grid gap-6">

        {/* ── Bot Configuration ── */}
        <Card className="p-5 md:p-6 !hover:shadow-md !hover:-translate-y-0">
          <div className="flex items-center justify-between mb-4">
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Bot Configuration</div>
            <div className="flex items-center gap-2">
              <span className={`inline-block w-2.5 h-2.5 rounded-full ${botStatus?.running ? "bg-green-500" : "bg-red-400"}`} />
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {botStatus?.running ? `Running (${Math.round((botStatus.uptime_seconds || 0) / 60)}m)` : "Stopped"}
              </span>
            </div>
          </div>

          <div className="grid gap-4">
            <div className="grid gap-4 md:grid-cols-2">
              <div className="grid gap-1">
                <label className="text-xs text-gray-500 dark:text-gray-400">Bot Token</label>
                <div className="relative">
                  <input
                    type={showToken ? "text" : "password"}
                    value={tokenInput}
                    onChange={e => setTokenInput(e.target.value)}
                    placeholder="Paste bot token from @BotFather"
                    className="w-full rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm pr-16 focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  />
                  <button type="button" onClick={() => setShowToken(s => !s)} className="absolute right-2 top-1/2 -translate-y-1/2 text-xs text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
                    {showToken ? "Hide" : "Show"}
                  </button>
                </div>
              </div>
              <div className="grid gap-1">
                <label className="text-xs text-gray-500 dark:text-gray-400">
                  Admin Chat ID
                  <span className="ml-1 text-gray-400 cursor-help" title="Send /start to @userinfobot on Telegram to get your chat ID">(?)</span>
                </label>
                <input
                  value={chatIdInput}
                  onChange={e => setChatIdInput(e.target.value)}
                  placeholder="Your Telegram chat ID"
                  className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                />
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-2">
              <div className="grid gap-1">
                <label className="text-xs text-gray-500 dark:text-gray-400">Default Language</label>
                <select
                  value={langInput}
                  onChange={e => setLangInput(e.target.value)}
                  className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                >
                  <option value="en">English</option>
                  <option value="fa">Persian (Farsi)</option>
                  <option value="both">Both (user chooses)</option>
                </select>
              </div>
              <div className="flex items-end gap-3 pb-1">
                <label className="flex items-center gap-2 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={enabledInput}
                    onChange={e => setEnabledInput(e.target.checked)}
                    className="rounded"
                  />
                  <span className="text-sm text-gray-700 dark:text-gray-300">Enable Bot</span>
                </label>
              </div>
            </div>

            <div className="flex items-center gap-2 pt-2">
              <button
                onClick={saveConfig}
                className="rounded-full bg-gray-900 text-white px-5 py-1.5 text-sm shadow hover:bg-gray-800 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-gray-200"
              >
                Save
              </button>
              <button
                onClick={handleRestart}
                className="rounded-full bg-white text-gray-900 px-4 py-1.5 text-sm ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 dark:bg-gray-800 dark:text-gray-100 dark:ring-gray-700"
              >
                Restart Bot
              </button>
              <button
                onClick={handleTestNotify}
                className="rounded-full bg-white text-gray-900 px-4 py-1.5 text-sm ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 dark:bg-gray-800 dark:text-gray-100 dark:ring-gray-700"
              >
                Send Test
              </button>
            </div>
          </div>
        </Card>

        {/* ── Notification Settings ── */}
        <Card className="p-5 md:p-6 !hover:shadow-md !hover:-translate-y-0">
          <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-4">Notification Settings</div>
          <div className="overflow-x-auto">
            <table className="w-full table-fixed text-sm">
              <colgroup>
                <col />
                <col className="w-11 md:w-16" />
                <col className="w-11 md:w-16" />
                <col className="w-11 md:w-16" />
                <col className="w-[4.25rem] md:w-20" />
              </colgroup>
              <thead>
                <tr className="text-left text-xs text-gray-500 dark:text-gray-400 border-b border-gray-100 dark:border-gray-800">
                  <th className="py-2 pr-2 md:pr-4">Event</th>
                  <th className="py-2 px-1 md:px-4 text-center">Clients</th>
                  <th className="py-2 px-1 md:px-4 text-center">Admin</th>
                  <th className="py-2 px-1 md:px-4 text-center">Active</th>
                  <th className="py-2 px-1 md:px-4 text-center">Test</th>
                </tr>
              </thead>
              <tbody>
                {notifConfigs.map(c => (
                  <tr key={c.event_type} className="border-b border-gray-50 dark:border-gray-800/50">
                    <td className="py-2.5 pr-2 md:pr-4 text-gray-700 dark:text-gray-300">{EVENT_LABELS[c.event_type] || c.event_type}</td>
                    <td className="py-2.5 px-1 md:px-4 text-center">
                      <input type="checkbox" checked={c.notify_clients} onChange={e => handleNotifToggle(c.event_type, "notify_clients", e.target.checked)} className="rounded" />
                    </td>
                    <td className="py-2.5 px-1 md:px-4 text-center">
                      <input type="checkbox" checked={c.notify_admin} onChange={e => handleNotifToggle(c.event_type, "notify_admin", e.target.checked)} className="rounded" />
                    </td>
                    <td className="py-2.5 px-1 md:px-4 text-center">
                      <input type="checkbox" checked={c.enabled} onChange={e => handleNotifToggle(c.event_type, "enabled", e.target.checked)} className="rounded" />
                    </td>
                    <td className="py-2.5 px-1 md:px-4 text-center">
                      <button
                        type="button"
                        disabled={testingNotifEvent === c.event_type}
                        onClick={() => handleTestEventNotify(c.event_type)}
                        className="rounded-full bg-white text-gray-700 px-3 py-1 text-xs ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 disabled:opacity-60 dark:bg-gray-800 dark:text-gray-200 dark:ring-gray-700"
                      >
                        {testingNotifEvent === c.event_type ? "Sending..." : "Test"}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        {/* ── Broadcasts ── */}
        <Card className="p-5 md:p-6 !hover:shadow-md !hover:-translate-y-0">
          <div className="flex items-center justify-between gap-3 mb-4">
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Broadcasts</div>
            <button
              type="button"
              onClick={refreshBroadcasts}
              className="rounded-full bg-white text-gray-700 px-3 py-1 text-xs ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 dark:bg-gray-800 dark:text-gray-200 dark:ring-gray-700"
            >
              Refresh
            </button>
          </div>

          <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(280px,360px)]">
            <div className="grid gap-3">
              <div className="grid gap-1">
                <label className="text-xs text-gray-500 dark:text-gray-400">Message</label>
                <textarea
                  value={broadcastText}
                  onChange={e => setBroadcastText(e.target.value)}
                  rows={5}
                  placeholder="Message text"
                  className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm resize-y focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                />
                <div className={`text-[11px] ${broadcastPhoto && broadcastText.trim().length > 1024 ? "text-red-500" : "text-gray-400 dark:text-gray-500"}`}>
                  {broadcastText.trim().length}/{broadcastPhoto ? 1024 : 4096}
                </div>
              </div>

              <div className="grid gap-1">
                <label className="text-xs text-gray-500 dark:text-gray-400">Photo</label>
                <input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  onChange={e => setBroadcastPhoto(e.target.files?.[0] || null)}
                  className="block w-full text-sm text-gray-600 file:mr-3 file:rounded-full file:border-0 file:bg-gray-100 file:px-3 file:py-1.5 file:text-xs file:text-gray-700 hover:file:bg-gray-200 dark:text-gray-300 dark:file:bg-gray-800 dark:file:text-gray-200 dark:hover:file:bg-gray-700"
                />
                {broadcastPhoto && (
                  <div className="flex items-center justify-between gap-2 text-xs text-gray-500 dark:text-gray-400">
                    <span className="truncate">{broadcastPhoto.name}</span>
                    <button type="button" onClick={() => setBroadcastPhoto(null)} className="text-red-500 hover:text-red-700">Remove</button>
                  </div>
                )}
              </div>

              <div className="grid gap-2">
                <label className="text-xs text-gray-500 dark:text-gray-400">Audience</label>
                <div className="inline-flex w-fit rounded-full bg-gray-100 p-1 text-xs dark:bg-gray-800">
                  <button
                    type="button"
                    onClick={() => setBroadcastMode("all")}
                    className={`rounded-full px-3 py-1 ${broadcastMode === "all" ? "bg-white text-gray-900 shadow dark:bg-gray-950 dark:text-gray-100" : "text-gray-500 dark:text-gray-400"}`}
                  >
                    All ({eligibleBroadcastUsers.length})
                  </button>
                  <button
                    type="button"
                    onClick={() => setBroadcastMode("selected")}
                    className={`rounded-full px-3 py-1 ${broadcastMode === "selected" ? "bg-white text-gray-900 shadow dark:bg-gray-950 dark:text-gray-100" : "text-gray-500 dark:text-gray-400"}`}
                  >
                    Selected ({broadcastRecipientIds.length})
                  </button>
                </div>
              </div>

              {broadcastMode === "selected" && (
                <div className="grid gap-1">
                  <input
                    value={broadcastUserSearch}
                    onChange={e => setBroadcastUserSearch(e.target.value)}
                    placeholder="Search users or peers..."
                    className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  />
                  <div className="max-h-48 overflow-y-auto rounded-xl border border-gray-100 dark:border-gray-800">
                    {filteredBroadcastUsers.map(u => (
                      <label key={u.id} className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-800/50 cursor-pointer text-sm">
                        <input
                          type="checkbox"
                          checked={broadcastRecipientIds.includes(u.id)}
                          onChange={e => toggleBroadcastRecipient(u.id, e.target.checked)}
                          className="rounded"
                        />
                        <span className="min-w-0 flex-1 truncate text-gray-700 dark:text-gray-300">{telegramUserLabel(u)}</span>
                        <span className="text-xs text-gray-400">{u.peers.length} peer(s)</span>
                      </label>
                    ))}
                    {filteredBroadcastUsers.length === 0 && (
                      <div className="px-3 py-2 text-sm text-gray-500 dark:text-gray-400">No matching users.</div>
                    )}
                  </div>
                </div>
              )}

              <div className="flex items-center justify-between gap-3 pt-1">
                <span className="text-xs text-gray-500 dark:text-gray-400">{broadcastTargetCount} recipient(s)</span>
                <button
                  type="button"
                  disabled={sendingBroadcast}
                  onClick={handleSendBroadcast}
                  className="rounded-full bg-gray-900 text-white px-5 py-1.5 text-sm shadow hover:bg-gray-800 disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-gray-200"
                >
                  {sendingBroadcast ? "Queueing..." : "Send broadcast"}
                </button>
              </div>
            </div>

            <div className="min-w-0">
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs font-medium uppercase tracking-wide text-gray-500 dark:text-gray-400">Outbox</div>
                <div className="text-xs text-gray-400 dark:text-gray-500">{broadcastTotal} total</div>
              </div>
              <div className="space-y-2">
                {broadcasts.length === 0 && (
                  <div className="text-sm text-gray-500 dark:text-gray-400">No broadcasts yet.</div>
                )}
                {broadcasts.map(b => (
                  <div key={b.id} className="rounded-xl bg-gray-50 dark:bg-gray-800/50 px-3 py-2 text-sm">
                    <button
                      type="button"
                      onClick={() => openBroadcastDetail(b.id)}
                      className="block w-full text-left"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-gray-900 dark:text-gray-100">#{b.id} {broadcastStatusLabel(b.status)}</span>
                        <span className="text-xs text-gray-400 dark:text-gray-500">
                          {b.sent_count}/{b.total_count}
                        </span>
                      </div>
                      <div className="mt-1 max-h-10 overflow-hidden break-words text-xs text-gray-500 dark:text-gray-400">{b.body_preview}</div>
                      <div className="mt-1 text-[11px] text-gray-400 dark:text-gray-500">
                        Ack {b.acknowledged_count} &middot; Failed {b.failed_count}{b.has_photo ? " · Photo" : ""}
                      </div>
                    </button>
                    {b.failed_count > 0 && (
                      <button
                        type="button"
                        disabled={retryingBroadcastId === b.id}
                        onClick={() => handleRetryBroadcast(b.id)}
                        className="mt-2 rounded-full bg-white text-gray-700 px-3 py-1 text-xs ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 disabled:opacity-60 dark:bg-gray-900 dark:text-gray-200 dark:ring-gray-700"
                      >
                        {retryingBroadcastId === b.id ? "Retrying..." : "Retry failed"}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Card>

        {/* ── Signup Tokens ── */}
        <Card className="p-5 md:p-6 !hover:shadow-md !hover:-translate-y-0">
          <div className="flex items-center justify-between mb-4">
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Signup Tokens</div>
            <button
              onClick={() => { setShowTokenForm(true); setTokenPeerIds([]); setCreatedToken(null); }}
              className="rounded-full bg-gray-900 text-white px-4 py-1.5 text-xs shadow hover:bg-gray-800 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-gray-200"
            >
              + Generate Token
            </button>
          </div>

          {createdToken && (
            <div className="mb-4 rounded-xl bg-blue-50 dark:bg-blue-900/30 p-4 ring-1 ring-blue-200 dark:ring-blue-800">
              <div className="text-xs text-blue-600 dark:text-blue-300 mb-1 font-medium">Token Created</div>
              <div className="flex items-center gap-2">
                <code className="text-sm bg-white dark:bg-gray-950 px-2 py-1 rounded ring-1 ring-gray-200 dark:ring-gray-700 select-all break-all">
                  {createdToken.deep_link || createdToken.token}
                </code>
                <button
                  onClick={() => { navigator.clipboard.writeText(createdToken.deep_link || createdToken.token); flash("Copied!"); }}
                  className="text-xs text-blue-600 hover:text-blue-800 dark:text-blue-400"
                >
                  Copy
                </button>
              </div>
              {createdToken.deep_link && (
                <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">Send this link to your client. They open it in Telegram to bind their account.</div>
              )}
            </div>
          )}

          {tokens.length === 0 && !showTokenForm && (
            <div className="text-sm text-gray-500 dark:text-gray-400">No tokens yet.</div>
          )}

          {tokens.length > 0 && (
            <div className="space-y-2">
              {tokens.map(tok => (
                <div key={tok.id} className="flex items-center justify-between rounded-xl bg-gray-50 dark:bg-gray-800/50 px-3 py-2 text-sm">
                  <div className="min-w-0">
                    <code className="text-xs text-gray-600 dark:text-gray-400 break-all">{tok.token.slice(0, 16)}...</code>
                    <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                      Peers: {tok.peer_ids.length} &middot;
                      {tok.used_by ? ` Used by @${tok.used_by.telegram_username || tok.used_by.first_name}` : " Unused"}
                      {tok.expires_at && ` · Expires ${formatCalendarDateTime(tok.expires_at, {
                        timeZone: settings?.timezone || "UTC",
                        dateCalendar: settings?.date_calendar || "gregorian",
                        includeTime: false,
                      })}`}
                    </div>
                  </div>
                  <button
                    onClick={async () => { await revokeTelegramToken(tok.id); await load(); }}
                    className="text-xs text-red-500 hover:text-red-700 ml-2 shrink-0"
                  >
                    Revoke
                  </button>
                </div>
              ))}
            </div>
          )}
        </Card>

        {/* ── Registered Users ── */}
        <Card className="p-5 md:p-6 !hover:shadow-md !hover:-translate-y-0">
          <div className="flex items-center justify-between mb-4">
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Registered Users ({users.length})</div>
            {users.length > 3 && (
              <input
                value={userSearch}
                onChange={e => setUserSearch(e.target.value)}
                placeholder="Search users..."
                className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-1.5 text-xs w-48 focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
              />
            )}
          </div>
          <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
            People who signed up through the bot. Use Peers to choose which VPN connections each user can view and manage.
          </p>

          {filteredUsers.length === 0 && (
            <div className="text-sm text-gray-500 dark:text-gray-400">No users registered via the bot yet.</div>
          )}

          <div className="space-y-2">
            {filteredUsers.map(u => {
              const subscribedNotifications = u.subscribed_notifications.map(eventType => EVENT_LABELS[eventType] || eventType);
              return (
                <div key={u.id} className="rounded-xl bg-gray-50 dark:bg-gray-800/50 px-3 py-2">
                  <div className="flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3 min-w-0 flex-1">
                      <div className="h-8 w-8 rounded-full bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-xs font-medium text-gray-600 dark:text-gray-300 shrink-0">
                        {(u.first_name?.[0] || u.telegram_username?.[0] || "?").toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                          {u.first_name} {u.last_name}
                          {u.telegram_username && (
                            <span className="text-gray-400 dark:text-gray-500 ml-1">@{u.telegram_username}</span>
                          )}
                        </div>
                        <div className="text-xs text-gray-400 dark:text-gray-500">
                          {u.peers.length} peer(s) · {u.language.toUpperCase()}
                          {u.is_blocked && <span className="text-red-500 ml-1">[Blocked]</span>}
                        </div>
                      </div>
                    </div>
                    <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
                      <button
                        onClick={() => openUserPeersModal(u)}
                        className="rounded-full bg-gray-100 text-gray-800 px-3 py-1 text-xs shadow hover:bg-gray-200 dark:bg-gray-800 dark:text-gray-100 dark:hover:bg-gray-700"
                      >
                        Peers
                      </button>
                      <button
                        onClick={async () => { await patchTelegramUser(u.id, { is_blocked: !u.is_blocked }); await load(); }}
                        className={`rounded-full px-3 py-1 text-xs shadow ${
                          u.is_blocked
                            ? "bg-green-50 text-green-700 hover:bg-green-100 dark:bg-green-500/10 dark:text-green-300 dark:hover:bg-green-500/20"
                            : "bg-orange-50 text-orange-700 hover:bg-orange-100 dark:bg-orange-500/10 dark:text-orange-300 dark:hover:bg-orange-500/20"
                        }`}
                      >
                        {u.is_blocked ? "Unblock" : "Block"}
                      </button>
                      <button
                        onClick={async () => { if (confirm("Delete this user and all bindings?")) { await deleteTelegramUser(u.id); await load(); } }}
                        className="rounded-full bg-rose-50 text-rose-700 px-3 py-1 text-xs shadow hover:bg-rose-100 dark:bg-rose-500/10 dark:text-rose-300 dark:hover:bg-rose-500/20"
                      >
                        Delete
                      </button>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-x-1.5 gap-y-1 border-t border-gray-200/70 pt-2 dark:border-gray-700/50 sm:ml-11 sm:border-0 sm:pt-1">
                    <span className="text-[11px] text-gray-400 dark:text-gray-500 shrink-0">Subscribed:</span>
                    {subscribedNotifications.length > 0 ? (
                      subscribedNotifications.map(label => (
                        <span
                          key={label}
                          className="whitespace-nowrap rounded-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 px-2 py-0.5 text-[11px] text-gray-600 dark:text-gray-300"
                        >
                          {label}
                        </span>
                      ))
                    ) : (
                      <span className="text-[11px] text-gray-400 dark:text-gray-500">None</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      </div>

      {broadcastDetail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 p-4">
          <div className="w-full max-w-2xl bg-white dark:bg-gray-900 rounded-3xl ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 relative">
            <button
              onClick={() => setBroadcastDetail(null)}
              className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
            >
              &times;
            </button>
            <div className="flex items-start justify-between gap-6 pr-8 mb-4">
              <div>
                <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">Broadcast #{broadcastDetail.id}</div>
                <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                  {broadcastStatusLabel(broadcastDetail.status)} &middot; {broadcastDetail.sent_count}/{broadcastDetail.total_count} sent &middot; {broadcastDetail.acknowledged_count} acknowledged
                </div>
              </div>
              {broadcastDetail.failed_count > 0 && (
                <button
                  type="button"
                  disabled={retryingBroadcastId === broadcastDetail.id}
                  onClick={() => handleRetryBroadcast(broadcastDetail.id)}
                  className="rounded-full bg-white text-gray-700 px-3 py-1 text-xs ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 disabled:opacity-60 dark:bg-gray-800 dark:text-gray-200 dark:ring-gray-700"
                >
                  {retryingBroadcastId === broadcastDetail.id ? "Retrying..." : "Retry failed"}
                </button>
              )}
            </div>

            <div className="mb-4 rounded-xl bg-gray-50 dark:bg-gray-800/50 px-3 py-2 text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap break-words">
              {broadcastDetail.body}
            </div>

            <div className="max-h-[420px] overflow-y-auto rounded-xl border border-gray-100 dark:border-gray-800">
              <table className="w-full text-sm">
                <thead className="sticky top-0 bg-white dark:bg-gray-900">
                  <tr className="border-b border-gray-100 text-left text-xs text-gray-500 dark:border-gray-800 dark:text-gray-400">
                    <th className="px-3 py-2">Recipient</th>
                    <th className="px-3 py-2">Status</th>
                    <th className="px-3 py-2">Error</th>
                  </tr>
                </thead>
                <tbody>
                  {broadcastDetail.recipients.map(r => (
                    <tr key={r.id} className="border-b border-gray-50 last:border-0 dark:border-gray-800/50">
                      <td className="px-3 py-2 text-gray-800 dark:text-gray-200">{r.display_name || r.chat_id}</td>
                      <td className="px-3 py-2 text-gray-600 dark:text-gray-300">{broadcastStatusLabel(r.status)}</td>
                      <td className="px-3 py-2 text-xs text-red-500">{r.error_message || r.error_code || ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {loadingBroadcastDetail && !broadcastDetail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 p-4">
          <div className="rounded-3xl bg-white px-5 py-3 text-sm shadow-lg ring-1 ring-gray-200 dark:bg-gray-900 dark:text-gray-100 dark:ring-gray-800">
            Loading broadcast...
          </div>
        </div>
      )}

      {/* ── Generate Token Modal ── */}
      {userPeersModalUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 p-4">
          <div className="w-full max-w-lg bg-white dark:bg-gray-900 rounded-3xl ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 relative">
            <button
              onClick={() => setUserPeersModalUser(null)}
              className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
            >
              &times;
            </button>
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-4">
              Assign Peers
              {userPeersModalUser.telegram_username ? (
                <span className="ml-1 font-normal text-gray-400 dark:text-gray-500">@{userPeersModalUser.telegram_username}</span>
              ) : null}
            </div>

            <div className="grid gap-4">
              <PeerSelectField
                label={`Select Peers (${(userPeerDraft[userPeersModalUser.id] || []).length} selected)`}
                search={userPeerSearch}
                onSearchChange={setUserPeerSearch}
                peers={peers}
                selectedIds={userPeerDraft[userPeersModalUser.id] || []}
                onToggle={(peerId, checked) => toggleUserPeerDraft(userPeersModalUser.id, peerId, checked)}
                routerById={routerById}
              />

              <div className="flex justify-end gap-2 pt-2">
                <button
                  onClick={() => setUserPeersModalUser(null)}
                  className="rounded-full bg-white text-gray-900 px-4 py-1.5 text-sm ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 dark:bg-gray-800 dark:text-gray-100 dark:ring-gray-700"
                >
                  Cancel
                </button>
                <button
                  disabled={savingUserPeers === userPeersModalUser.id}
                  onClick={() => applyUserPeers(userPeersModalUser)}
                  className="rounded-full bg-gray-900 text-white px-5 py-1.5 text-sm shadow hover:bg-gray-800 disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-gray-200"
                >
                  {savingUserPeers === userPeersModalUser.id ? "Saving..." : "Apply changes"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showTokenForm && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 p-4">
          <div className="w-full max-w-lg bg-white dark:bg-gray-900 rounded-3xl ring-1 ring-gray-200 dark:ring-gray-800 shadow-lg p-6 relative">
            <button
              onClick={() => setShowTokenForm(false)}
              className="absolute top-4 right-4 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
            >
              &times;
            </button>
            <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mb-4">Generate Signup Token</div>

            <div className="grid gap-4">
              <PeerSelectField
                label="Select Peers to Bind"
                search={tokenSearch}
                onSearchChange={setTokenSearch}
                peers={peers}
                selectedIds={tokenPeerIds}
                onToggle={(peerId, checked) => {
                  setTokenPeerIds(prev =>
                    checked ? [...prev, peerId] : prev.filter(id => id !== peerId)
                  );
                }}
                routerById={routerById}
              />

              <div className="grid gap-4 grid-cols-1 sm:grid-cols-2">
                <div className="grid gap-1 min-w-0">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Expires in (hours)</label>
                  <input
                    type="number"
                    value={tokenExpiry}
                    onChange={e => setTokenExpiry(Number(e.target.value))}
                    min={0}
                    className="w-full rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  />
                </div>
                <label className="flex items-center gap-2 cursor-pointer sm:self-end sm:pb-2.5">
                  <input
                    type="checkbox"
                    checked={tokenSingleUse}
                    onChange={e => setTokenSingleUse(e.target.checked)}
                    className="rounded"
                  />
                  <span className="text-sm text-gray-700 dark:text-gray-300">Single use</span>
                </label>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  onClick={() => setShowTokenForm(false)}
                  className="rounded-full bg-white text-gray-900 px-4 py-1.5 text-sm ring-1 ring-gray-200 shadow-sm hover:ring-gray-300 dark:bg-gray-800 dark:text-gray-100 dark:ring-gray-700"
                >
                  Cancel
                </button>
                <button
                  onClick={handleCreateToken}
                  className="rounded-full bg-gray-900 text-white px-5 py-1.5 text-sm shadow hover:bg-gray-800 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-gray-200"
                >
                  Generate
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
