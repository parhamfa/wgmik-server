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
  testTelegramNotify,
  testTelegramNotifyEvent,
  listRouters,
  listSavedPeers,
  type TelegramConfigDTO,
  type TelegramStatusDTO,
  type TelegramTokenDTO,
  type TelegramUserDTO,
  type TelegramNotifConfigDTO,
  type PeerListDTO,
  type Router,
} from "../api";

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

export default function TelegramPage() {
  const [loading, setLoading] = React.useState(true);
  const [err, setErr] = React.useState("");
  const [success, setSuccess] = React.useState("");

  // Config
  const [config, setConfig] = React.useState<TelegramConfigDTO | null>(null);
  const [botStatus, setBotStatus] = React.useState<TelegramStatusDTO | null>(null);
  const [tokenInput, setTokenInput] = React.useState("");
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
  const [expandedUser, setExpandedUser] = React.useState<number | null>(null);
  const [userSearch, setUserSearch] = React.useState("");
  const [userPeerDraft, setUserPeerDraft] = React.useState<Record<number, number[]>>({});
  const [savingUserPeers, setSavingUserPeers] = React.useState<number | null>(null);

  // Peers + Routers for token creation
  const [peers, setPeers] = React.useState<PeerListDTO[]>([]);
  const [routers, setRouters] = React.useState<Router[]>([]);

  const routerById = React.useMemo(() => {
    const m: Record<number, Router> = {};
    for (const r of routers) m[r.id] = r;
    return m;
  }, [routers]);

  const load = React.useCallback(async () => {
    try {
      const [cfg, st, toks, usrs, nc, rts, prs] = await Promise.all([
        getTelegramConfig(),
        getTelegramStatus(),
        listTelegramTokens(),
        listTelegramUsers(),
        getTelegramNotifConfig(),
        listRouters(),
        listSavedPeers(),
      ]);
      setConfig(cfg);
      setBotStatus(st);
      setTokens(toks);
      setUsers(usrs);
      setNotifConfigs(nc);
      setRouters(rts);
      setPeers(prs);
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
      if (tokenInput) payload.tg_bot_token = tokenInput;
      await updateTelegramConfig(payload);
      setTokenInput("");
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

  const filteredPeers = React.useMemo(() => {
    if (!tokenSearch.trim()) return peers;
    const q = tokenSearch.toLowerCase();
    return peers.filter(p =>
      p.name.toLowerCase().includes(q) ||
      p.allowed_address.toLowerCase().includes(q) ||
      (routerById[p.router_id]?.name || "").toLowerCase().includes(q)
    );
  }, [peers, tokenSearch, routerById]);

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

  const openUserPeers = (u: TelegramUserDTO) => {
    const willExpand = expandedUser !== u.id;
    setExpandedUser(willExpand ? u.id : null);
    if (!willExpand) return;
    setUserPeerDraft(prev => {
      if (prev[u.id]) return prev;
      return { ...prev, [u.id]: u.peers.map(p => p.peer_id) };
    });
  };

  const applyUserPeers = async (u: TelegramUserDTO) => {
    setErr("");
    setSavingUserPeers(u.id);
    try {
      const peer_ids = userPeerDraft[u.id] || [];
      await setTelegramUserPeers(u.id, peer_ids);
      await load();
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
                    placeholder={config?.tg_bot_token || "Paste bot token from @BotFather"}
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
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-gray-500 dark:text-gray-400 border-b border-gray-100 dark:border-gray-800">
                  <th className="py-2 pr-4">Event</th>
                  <th className="py-2 px-4 text-center">Clients</th>
                  <th className="py-2 px-4 text-center">Admin</th>
                  <th className="py-2 px-4 text-center">Active</th>
                  <th className="py-2 px-4 text-center">Test</th>
                </tr>
              </thead>
              <tbody>
                {notifConfigs.map(c => (
                  <tr key={c.event_type} className="border-b border-gray-50 dark:border-gray-800/50">
                    <td className="py-2.5 pr-4 text-gray-700 dark:text-gray-300">{EVENT_LABELS[c.event_type] || c.event_type}</td>
                    <td className="py-2.5 px-4 text-center">
                      <input type="checkbox" checked={c.notify_clients} onChange={e => handleNotifToggle(c.event_type, "notify_clients", e.target.checked)} className="rounded" />
                    </td>
                    <td className="py-2.5 px-4 text-center">
                      <input type="checkbox" checked={c.notify_admin} onChange={e => handleNotifToggle(c.event_type, "notify_admin", e.target.checked)} className="rounded" />
                    </td>
                    <td className="py-2.5 px-4 text-center">
                      <input type="checkbox" checked={c.enabled} onChange={e => handleNotifToggle(c.event_type, "enabled", e.target.checked)} className="rounded" />
                    </td>
                    <td className="py-2.5 px-4 text-center">
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
                      {tok.expires_at && ` · Expires ${new Date(tok.expires_at).toLocaleDateString()}`}
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

          {filteredUsers.length === 0 && (
            <div className="text-sm text-gray-500 dark:text-gray-400">No users registered via the bot yet.</div>
          )}

          <div className="space-y-2">
            {filteredUsers.map(u => {
              const subscribedNotifications = u.subscribed_notifications.map(eventType => EVENT_LABELS[eventType] || eventType);
              return (
                <div key={u.id} className="rounded-xl bg-gray-50 dark:bg-gray-800/50 px-3 py-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="h-8 w-8 rounded-full bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-xs font-medium text-gray-600 dark:text-gray-300 shrink-0">
                        {(u.first_name?.[0] || u.telegram_username?.[0] || "?").toUpperCase()}
                      </div>
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
                          {u.first_name} {u.last_name}
                          {u.telegram_username && <span className="text-gray-400 dark:text-gray-500 ml-1">@{u.telegram_username}</span>}
                        </div>
                        <div className="text-xs text-gray-400 dark:text-gray-500">
                          {u.peers.length} peer(s) · {u.language.toUpperCase()}
                          {u.is_blocked && <span className="text-red-500 ml-1">[Blocked]</span>}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-1">
                          <span className="text-[11px] text-gray-400 dark:text-gray-500 mr-1">Subscribed:</span>
                          {subscribedNotifications.length > 0 ? (
                            subscribedNotifications.map(label => (
                              <span
                                key={label}
                                className="rounded-full bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 px-2 py-0.5 text-[11px] text-gray-600 dark:text-gray-300"
                              >
                                {label}
                              </span>
                            ))
                          ) : (
                            <span className="text-[11px] text-gray-400 dark:text-gray-500">None</span>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        onClick={() => openUserPeers(u)}
                        className="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                      >
                        {expandedUser === u.id ? "Collapse" : "Peers"}
                      </button>
                      <button
                        onClick={async () => { await patchTelegramUser(u.id, { is_blocked: !u.is_blocked }); await load(); }}
                        className={`text-xs ${u.is_blocked ? "text-green-600 hover:text-green-700" : "text-orange-500 hover:text-orange-700"}`}
                      >
                        {u.is_blocked ? "Unblock" : "Block"}
                      </button>
                      <button
                        onClick={async () => { if (confirm("Delete this user and all bindings?")) { await deleteTelegramUser(u.id); await load(); } }}
                        className="text-xs text-red-500 hover:text-red-700"
                      >
                        Delete
                      </button>
                    </div>
                  </div>

                  {expandedUser === u.id && (
                    <div className="mt-3 ml-11 rounded-xl border border-gray-200 dark:border-gray-700/60 bg-white dark:bg-gray-900/40 p-3">
                      <div className="flex items-center justify-between gap-2 mb-2">
                        <div className="text-xs text-gray-500 dark:text-gray-400">
                          Select peers to assign to this user ({(userPeerDraft[u.id] || []).length} selected)
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setUserPeerDraft(prev => ({ ...prev, [u.id]: peers.map(p => p.id) }))}
                            className="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                          >
                            All
                          </button>
                          <button
                            type="button"
                            onClick={() => setUserPeerDraft(prev => ({ ...prev, [u.id]: [] }))}
                            className="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
                          >
                            None
                          </button>
                        </div>
                      </div>
                      <div className="max-h-56 overflow-y-auto rounded-lg border border-gray-100 dark:border-gray-800">
                        {peers.map(p => {
                          const selected = (userPeerDraft[u.id] || []).includes(p.id);
                          return (
                            <label
                              key={p.id}
                              className="flex items-center gap-2 px-3 py-1.5 text-xs hover:bg-gray-50 dark:hover:bg-gray-800/50 cursor-pointer"
                            >
                              <input
                                type="checkbox"
                                checked={selected}
                                onChange={e => toggleUserPeerDraft(u.id, p.id, e.target.checked)}
                                className="rounded"
                              />
                              <span className="text-gray-700 dark:text-gray-300">
                                {p.name || p.public_key.slice(0, 16)}
                              </span>
                              <span className="text-gray-400 ml-auto">
                                {routerById[p.router_id]?.name || `R#${p.router_id}`} / {p.interface}
                              </span>
                            </label>
                          );
                        })}
                      </div>
                      <div className="mt-2 flex justify-end">
                        <button
                          type="button"
                          disabled={savingUserPeers === u.id}
                          onClick={() => applyUserPeers(u)}
                          className="rounded-full bg-gray-900 text-white px-4 py-1.5 text-xs shadow hover:bg-gray-800 disabled:opacity-60 dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-gray-200"
                        >
                          {savingUserPeers === u.id ? "Saving..." : "Apply changes"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </Card>
      </div>

      {/* ── Generate Token Modal ── */}
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
              <div className="grid gap-1">
                <label className="text-xs text-gray-500 dark:text-gray-400">Select Peers to Bind</label>
                <input
                  value={tokenSearch}
                  onChange={e => setTokenSearch(e.target.value)}
                  placeholder="Search peers..."
                  className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                />
                <div className="max-h-48 overflow-y-auto rounded-xl border border-gray-100 dark:border-gray-800 mt-1">
                  {filteredPeers.map(p => (
                    <label key={p.id} className="flex items-center gap-2 px-3 py-1.5 hover:bg-gray-50 dark:hover:bg-gray-800/50 cursor-pointer text-sm">
                      <input
                        type="checkbox"
                        checked={tokenPeerIds.includes(p.id)}
                        onChange={e => {
                          setTokenPeerIds(prev =>
                            e.target.checked ? [...prev, p.id] : prev.filter(id => id !== p.id)
                          );
                        }}
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

              <div className="grid gap-4 grid-cols-2">
                <div className="grid gap-1">
                  <label className="text-xs text-gray-500 dark:text-gray-400">Expires in (hours)</label>
                  <input
                    type="number"
                    value={tokenExpiry}
                    onChange={e => setTokenExpiry(Number(e.target.value))}
                    min={0}
                    className="rounded-xl border border-gray-200 dark:border-gray-800 px-3 py-2 text-sm focus:ring-2 focus:ring-gray-300 dark:focus:ring-gray-700 dark:bg-gray-950 dark:text-gray-100"
                  />
                </div>
                <div className="flex items-end pb-1">
                  <label className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={tokenSingleUse}
                      onChange={e => setTokenSingleUse(e.target.checked)}
                      className="rounded"
                    />
                    <span className="text-sm text-gray-700 dark:text-gray-300">Single use</span>
                  </label>
                </div>
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
