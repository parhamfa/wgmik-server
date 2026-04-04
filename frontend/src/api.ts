export const base = "";

export async function fetchJson(url: string, init?: RequestInit) {
  const res = await fetch(base + url, init);
  if (res.status === 401) {
    if (window.location.pathname !== '/login') {
      window.location.href = '/login';
    }
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const text = await res.text();
    let msg = text;
    try {
      const json = JSON.parse(text);
      if (json.detail) msg = json.detail;
    } catch { }
    throw new Error(msg || res.statusText);
  }
  return res.json();
}

export type SettingsDTO = {
  poll_interval_seconds: number;
  online_threshold_seconds: number;
  show_kind_pills: boolean;
  show_hardware_stats?: boolean; // Make optional
  monthly_reset_day: number;
  // Previously used fields in Settings.tsx
  timezone: string;
  week_start_day: number;
  dashboard_refresh_seconds: number;
  peer_refresh_seconds: number;
  peer_default_scope_unit: string;
  peer_default_scope_value: number;
  dashboard_scope_unit: string;
  dashboard_scope_value: number;
  dashboard_router_scope: "all" | "active" | "selected";
  dashboard_selected_router_ids: number[];
  dashboard_filter_status: string;
  dashboard_sort_by: string;
  show_hw_stats: boolean; // Alias field often used
  raw_sample_retention_hours: number;
  minute_rollup_retention_days: number;
  daily_rollup_retention_days: number;
};

export type QuotaDTO = {
  peer_id: number;
  monthly_limit_bytes: number;
  reset_day: number;
  // Extended fields for frontend
  valid_from?: string | null;
  valid_until?: string | null;
  used_rx: number;
  used_tx: number;
}
export type Quota = QuotaDTO;

export async function getSettings(): Promise<SettingsDTO> {
  return fetchJson("/api/settings");
}

export async function updateSettings(dto: SettingsDTO): Promise<SettingsDTO> {
  return fetchJson("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dto),
  });
}

// ... Router Types ...

export type RouterProto = "rest" | "rest-http" | "api" | "api-plain";

export type RouterDTO = {
  id: number;
  name: string;
  host: string;
  proto: RouterProto;
  port: number;
  username: string;
  tls_verify: boolean;
};

// Aliases for backward compat
export type Router = RouterDTO;

function applyRouterFilters(params: URLSearchParams, routerId?: number | null, routerIds?: number[] | null) {
  if (routerIds && routerIds.length > 0) {
    for (const id of routerIds) {
      if (id > 0) params.append("router_ids", String(id));
    }
    return;
  }
  if (routerId && routerId > 0) params.set("router_id", String(routerId));
}

export type RouterCreateDTO = Omit<RouterDTO, "id"> & {
  password?: string;
};

export async function listRouters(): Promise<RouterDTO[]> {
  return fetchJson("/api/routers");
}

export async function createRouter(dto: RouterCreateDTO): Promise<RouterDTO> {
  return fetchJson("/api/routers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dto),
  });
}

// RESTORED: updateRouter
export async function updateRouter(routerId: number, dto: Partial<RouterDTO>) {
  return fetchJson(`/api/routers/${routerId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dto)
  });
}

export async function deleteRouter(routerId: number) {
  return fetchJson(`/api/routers/${routerId}`, { method: "DELETE" });
}

export async function listInterfaces(routerId: number): Promise<string[]> {
  return fetchJson(`/api/routers/${routerId}/interfaces`);
}
export const routerInterfaces = listInterfaces; // Alias

// RESTORED: routerInterfaceDetail
export type WGInterfaceConfig = { name: string; public_key: string; listen_port: number; public_host: string };
export async function routerInterfaceDetail(routerId: number, iface: string): Promise<WGInterfaceConfig> {
  return fetchJson(`/api/routers/${routerId}/interfaces/${encodeURIComponent(iface)}`);
}

export async function getActiveRouter(): Promise<{ router_id: number | null }> {
  return fetchJson("/api/active_router");
}

export async function setActiveRouter(routerId: number): Promise<any> {
  return fetchJson("/api/active_router", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ router_id: routerId }),
  });
}

export async function testRouter(routerId: number): Promise<{ ok: boolean }> {
  return fetchJson(`/api/routers/${routerId}/test`, { method: "POST" });
}

// ... Peer Types ...

export type PeerListDTO = {
  id: number;
  router_id: number;
  interface: string;
  ros_id: string;
  name: string;
  public_key: string;
  allowed_address: string;
  comment: string;
  disabled: boolean;
  selected: boolean;
  endpoint?: string;
  status: "online" | "offline";
  online: boolean;
  last_handshake: any; // Relaxed type
  last_seen_seconds: number;
  // Usage summary
  current_rx: number;
  current_tx: number;
  total_rx: number;
  total_tx: number;
};
export type PeerView = PeerListDTO;
export type SavedPeer = PeerListDTO;

// Helper to backend response normalization if needed
function normalizePeer(p: any): PeerListDTO {
  return {
    ...p,
    online: !!p.online, // Backend returns online boolean directly
  };
}

export async function listPeers(routerId: number, selectedOnly = false, iface?: string, routerIds?: number[] | null): Promise<PeerListDTO[]> {
  const params = new URLSearchParams();
  applyRouterFilters(params, routerId, routerIds);
  if (selectedOnly) params.set("selected_only", "true");
  if (iface) params.set("interface", iface);

  const rows = await fetchJson(`/api/peers?${params.toString()}`);
  return rows.map(normalizePeer);
}

// Wrapper for Wizard.tsx which uses (routerId, interface) signature
export async function routerPeers(routerId: number, iface: string): Promise<PeerListDTO[]> {
  const rows = await fetchJson(`/api/routers/${routerId}/peers?interface=${encodeURIComponent(iface)}`);
  return rows.map(normalizePeer);
}

// RESTORED: listSavedPeers and listSavedPeersSelected aliases
export async function listSavedPeers(): Promise<SavedPeer[]> {
  const rows = await fetchJson("/api/peers");
  return rows.map(normalizePeer);
}
export async function listSavedPeersSelected(routerId?: number | null, routerIds?: number[] | null): Promise<SavedPeer[]> {
  const params = new URLSearchParams();
  params.set("selected_only", "true");
  applyRouterFilters(params, routerId, routerIds);
  const rows = await fetchJson(`/api/peers?${params.toString()}`);
  return rows.map(normalizePeer);
}


export async function getPeer(id: number): Promise<PeerListDTO> {
  const p = await fetchJson(`/api/peers/${id}`);
  return normalizePeer(p);
}

// RESTORED: patchPeer
export async function patchPeer(peerId: number, body: Partial<{ selected: boolean; disabled: boolean }>) {
  return fetchJson(`/api/peers/${peerId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deletePeer(peerId: number, skipRouter = false) {
  const q = skipRouter ? "?skip_router=true" : "";
  return fetchJson(`/api/peers/${peerId}${q}`, { method: "DELETE" });
}

// ... Usage & Metrics ...

export type PeerUsageSummaryDTO = {
  peer_id: number;
  total_rx: number;
  total_tx: number;
  rx: number; // Alias
  tx: number; // Alias
  /** True when an enabled fair-usage rule applies (assignment, router, or global). */
  has_fair_usage?: boolean;
  /** True when fair usage has applied a throttle (simple queue) for this peer. */
  fair_usage_throttled?: boolean;
};
export type PeerUsageSummary = PeerUsageSummaryDTO; // Alias

export type DashboardLiveStatusDTO = {
  peer_id: number;
  online: boolean;
  raw_last_handshake: number;
};

export type UsageMaintenanceStatusDTO = {
  running: boolean;
  phase: string;
  started_at?: string | null;
  finished_at?: string | null;
  last_error?: string | null;
  last_completed_phase?: string | null;
  resume_cursor?: Record<string, unknown> | null;
  detail?: string | null;
  backup_path?: string | null;
  file_size_before?: number | null;
  file_size_after?: number | null;
  backfilled_minutes: number;
  deleted_samples: number;
  deleted_minutes: number;
  deleted_daily: number;
  backfill_cutoff?: string | null;
  raw_prune_before?: string | null;
  minute_prune_before?: string | null;
  daily_prune_before?: string | null;
};

export async function getPeersSummary(opts: { seconds?: number; days?: number; routerId?: number | null; routerIds?: number[] | null; start?: string; end?: string; allTime?: boolean }): Promise<PeerUsageSummaryDTO[]> {
  const params = new URLSearchParams();
  if (opts.seconds) params.set("seconds", String(opts.seconds));
  if (opts.days) params.set("days", String(opts.days));
  applyRouterFilters(params, opts.routerId, opts.routerIds);
  if (opts.start) params.set("start", opts.start);
  if (opts.end) params.set("end", opts.end);
  if (opts.allTime) params.set("all_time", "true");
  const rows = await fetchJson(`/api/summary/peers?${params.toString()}`);
  return rows.map((r: any) => ({
    peer_id: r.peer_id,
    rx: r.rx ?? 0,
    tx: r.tx ?? 0,
    total_rx: r.rx ?? 0,
    total_tx: r.tx ?? 0,
    has_fair_usage: r.has_fair_usage === true,
    fair_usage_throttled: r.fair_usage_throttled === true,
  }));
}

export async function getDashboardLiveStatus(routerId?: number | null, routerIds?: number[] | null): Promise<DashboardLiveStatusDTO[]> {
  const params = new URLSearchParams();
  applyRouterFilters(params, routerId, routerIds);
  const q = params.toString();
  return fetchJson(`/api/dashboard/live_status${q ? `?${q}` : ""}`);
}

export type SummaryRawPointDTO = {
  ts: string;
  rx: number;
  tx: number;
};
export type SummaryRawPoint = SummaryRawPointDTO; // Alias

export async function getSummaryRaw(seconds: number, routerId?: number | null, interval?: number, start?: string, end?: string, routerIds?: number[] | null): Promise<SummaryRawPointDTO[]> {
  const params = new URLSearchParams({ seconds: String(seconds) });
  applyRouterFilters(params, routerId, routerIds);
  if (interval && interval > 0) params.set("interval", String(interval));
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  return fetchJson(`/api/summary/raw?${params.toString()}`);
}

// RESTORED: getMonthlySummary + MonthlySummaryPoint
export type MonthlySummaryPoint = { day: string; rx: number; tx: number };
export async function getMonthlySummary(days?: number, routerId?: number | null, opts?: { start?: string; end?: string; allTime?: boolean; routerIds?: number[] | null }): Promise<MonthlySummaryPoint[]> {
  const params = new URLSearchParams();
  if (days && days > 0) params.set("days", String(days));
  applyRouterFilters(params, routerId, opts?.routerIds);
  if (opts?.start) params.set("start", opts.start);
  if (opts?.end) params.set("end", opts.end);
  if (opts?.allTime) params.set("all_time", "true");
  const q = params.toString();
  return fetchJson(`/api/summary/month${q ? `?${q}` : ""}`);
}

export type RouterMonthlySummaryPoint = { router_id: number; day: string; rx: number; tx: number };
export async function getMonthlySummaryByRouter(days?: number, routerId?: number | null, opts?: { start?: string; end?: string; allTime?: boolean; routerIds?: number[] | null }): Promise<RouterMonthlySummaryPoint[]> {
  const params = new URLSearchParams();
  if (days && days > 0) params.set("days", String(days));
  applyRouterFilters(params, routerId, opts?.routerIds);
  if (opts?.start) params.set("start", opts.start);
  if (opts?.end) params.set("end", opts.end);
  if (opts?.allTime) params.set("all_time", "true");
  const q = params.toString();
  return fetchJson(`/api/summary/month/by_router${q ? `?${q}` : ""}`);
}

export type RouterSummaryRawPoint = { router_id: number; ts: string; rx: number; tx: number };
export async function getSummaryRawByRouter(seconds: number, routerId?: number | null, interval?: number, start?: string, end?: string, routerIds?: number[] | null): Promise<RouterSummaryRawPoint[]> {
  const params = new URLSearchParams({ seconds: String(seconds) });
  applyRouterFilters(params, routerId, routerIds);
  if (interval && interval > 0) params.set("interval", String(interval));
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  return fetchJson(`/api/summary/raw/by_router?${params.toString()}`);
}

// RESTORED: getPeerUsage + UsagePoint
export type UsagePoint = { day: string; rx: number; tx: number };
export async function getPeerUsage(peerId: number, opts?: { window?: "daily" | "raw"; seconds?: number, interval?: number; start?: string; end?: string; allTime?: boolean }): Promise<UsagePoint[]> {
  const window = opts?.window || "daily";
  const params = new URLSearchParams({ window });
  if (opts?.seconds && opts.seconds > 0) params.set("seconds", String(opts.seconds));
  if (opts?.interval && opts.interval > 0) params.set("interval", String(opts.interval));
  if (opts?.start) params.set("start", opts.start);
  if (opts?.end) params.set("end", opts.end);
  if (opts?.allTime) params.set("all_time", "true");
  return fetchJson(`/api/peers/${peerId}/usage?${params.toString()}`);
}

// RESTORED: Metrics
export type Metrics = {
  cpu_percent: number | null;
  load_1: number | null;
  load_5: number | null;
  load_15: number | null;
  mem_percent: number | null;
  mem_used: number | null;
  mem_total: number | null;
};
export async function getMetrics(): Promise<Metrics> {
  return fetchJson("/api/metrics");
}

// RESTORED: resetPeerMetrics
export async function resetPeerMetrics(peerId: number) {
  return fetchJson(`/api/peers/${peerId}/reset_metrics`, { method: "POST" });
}

// RESTORED: reconcilePeer
export async function reconcilePeer(peerId: number) {
  return fetchJson(`/api/peers/${peerId}/reconcile`, { method: "POST" });
}

export type PeerCreateRouterDTO = {
  interface: string;
  name: string;
  comment: string;
  public_key: string;
  allowed_address: string;
  private_key?: string;
  preshared_key?: string;
  disabled?: boolean;
};

export async function createPeerOnRouter(routerId: number, dto: PeerCreateRouterDTO) {
  return fetchJson(`/api/routers/${routerId}/peers/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dto),
  });
}
export const createRouterPeer = createPeerOnRouter; // Alias

export async function getPeerClientPrivateKey(peerId: number): Promise<{ private_key: string | null }> {
  return fetchJson(`/api/peers/${peerId}/client_private_key`);
}

export type PeerImportItem = { interface: string; public_key: string; selected: boolean };
export async function importPeers(routerId: number, items: PeerImportItem[]) {
  return fetchJson(`/api/routers/${routerId}/peers/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(items),
  });
}

export async function syncRouter(routerId: number) {
  return fetchJson(`/api/routers/${routerId}/sync`, { method: "POST" });
}

// ...

// ... (removed duplicate SettingsDTO)

// ...

export type ActionDTO = {
  id: number;
  peer_id: number | null;
  ts: string;
  action: string;
  note: string;
};
export type PeerAction = ActionDTO; // Alias

export async function getPeerActions(peerId: number, limit?: number): Promise<ActionDTO[]> {
  const q = limit ? `?limit=${limit}` : "";
  return fetchJson(`/api/peers/${peerId}/actions${q}`);
}

export type LastActionDTO = {
  peer_id: number;
  action: string;
  ts: string;
  note: string;
};
export type LastAction = LastActionDTO; // Alias

export async function getLastActions(peerIds: number[]): Promise<LastActionDTO[]> {
  return fetchJson(`/api/actions/last?peer_ids=${peerIds.join(",")}`);
}

export async function togglePeer(peerId: number, disabled: boolean) {
  return fetchJson(`/api/peers/${peerId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ disabled }),
  });
}

export async function getQuota(peerId: number): Promise<QuotaDTO> {
  const q: QuotaDTO = await fetchJson(`/api/peers/${peerId}/quota`);
  // Polyfill missing fields if backend doesn't send them yet
  return {
    valid_from: null,
    valid_until: null,
    // defaults for rx/tx implied by q if present, or handled by component
    ...q
  };
}
export const getPeerQuota = getQuota; // Alias

export async function updateQuota(peerId: number, body: Partial<QuotaDTO>) {
  return fetchJson(`/api/peers/${peerId}/quota`, {
    method: 'PATCH',
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body)
  });
}
// Alias for useAutoSaveSettings.ts & PeerDetail.tsx
export const putSettings = updateSettings;
export const patchPeerQuota = updateQuota;


// ── Fair Usage ─────────────────────────────────────────────────────────

export type FairUsageAssignedPeer = {
  peer_id: number;
  name: string;
  allowed_address: string;
  router_id: number;
  disabled: boolean;
};

export type FairUsageRuleDTO = {
  id: number;
  name: string;
  description: string;
  quota_mode: "combined" | "independent";
  download_quota_bytes: number;
  upload_quota_bytes: number | null;
  throttle_download_kbps: number;
  throttle_upload_kbps: number;
  /** Legacy short code (e.g. monthly, 5h) */
  time_scope: string;
  scope_period_count: number;
  scope_period_unit: "hour" | "day" | "week" | "month";
  scope_label: string;
  scope_type: "global" | "router" | "peer";
  router_id: number | null;
  enabled: boolean;
  created_at: string;
  updated_at: string;
  assigned_peer_count: number;
  assigned_peers: FairUsageAssignedPeer[];
};

export type FairUsageRuleCreateDTO = {
  name: string;
  description?: string;
  quota_mode?: "combined" | "independent";
  download_quota_bytes: number;
  upload_quota_bytes?: number | null;
  throttle_download_kbps: number;
  throttle_upload_kbps: number;
  scope_period_count: number;
  scope_period_unit: "hour" | "day" | "week" | "month";
  /** @deprecated maps to scope_period_* when set to hourly/daily/weekly/monthly */
  time_scope?: "hourly" | "daily" | "weekly" | "monthly";
  scope_type: "global" | "router" | "peer";
  router_id?: number | null;
  peer_ids?: number[];
  enabled?: boolean;
};

export type FairUsagePeerStatusDTO = {
  peer_id: number;
  rule_id: number | null;
  rule_name: string | null;
  quota_mode: string | null;
  download_quota_bytes: number;
  upload_quota_bytes: number | null;
  throttle_download_kbps: number;
  throttle_upload_kbps: number;
  time_scope: string | null;
  scope_period_count: number;
  scope_period_unit: string;
  scope_label: string;
  scope_type: string | null;
  used_rx: number;
  used_tx: number;
  throttled: boolean;
  throttled_at: string | null;
  next_reset: string | null;
};

export async function listFairUsageRules(): Promise<FairUsageRuleDTO[]> {
  return fetchJson("/api/fair-usage/rules");
}

export async function getFairUsageRule(ruleId: number): Promise<FairUsageRuleDTO> {
  return fetchJson(`/api/fair-usage/rules/${ruleId}`);
}

export async function createFairUsageRule(dto: FairUsageRuleCreateDTO): Promise<FairUsageRuleDTO> {
  return fetchJson("/api/fair-usage/rules", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dto),
  });
}

export async function updateFairUsageRule(ruleId: number, dto: Partial<FairUsageRuleCreateDTO>): Promise<FairUsageRuleDTO> {
  return fetchJson(`/api/fair-usage/rules/${ruleId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dto),
  });
}

export async function deleteFairUsageRule(ruleId: number): Promise<void> {
  await fetchJson(`/api/fair-usage/rules/${ruleId}`, { method: "DELETE" });
}

export async function assignPeersToRule(ruleId: number, peerIds: number[]): Promise<FairUsageRuleDTO> {
  return fetchJson(`/api/fair-usage/rules/${ruleId}/assign`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ peer_ids: peerIds }),
  });
}

export async function unassignPeerFromRule(ruleId: number, peerId: number): Promise<void> {
  await fetchJson(`/api/fair-usage/rules/${ruleId}/assign/${peerId}`, { method: "DELETE" });
}

export async function getFairUsagePeerStatus(peerId: number): Promise<FairUsagePeerStatusDTO> {
  return fetchJson(`/api/fair-usage/peers/${peerId}/status`);
}

export async function resetFairUsagePeer(peerId: number): Promise<void> {
  await fetchJson(`/api/fair-usage/peers/${peerId}/reset`, { method: "POST" });
}

// RESTORED: Admin Actions
export async function purgeUsage() {
  return fetchJson("/api/admin/purge_usage", { method: "POST" });
}
export async function purgePeers() {
  return fetchJson("/api/admin/purge_peers", { method: "POST" });
}
export async function getUsageMaintenanceStatus(): Promise<UsageMaintenanceStatusDTO> {
  return fetchJson("/api/admin/usage_maintenance");
}
export async function runUsageMaintenance(): Promise<UsageMaintenanceStatusDTO> {
  return fetchJson("/api/admin/usage_maintenance/run", { method: "POST" });
}

// ── Telegram Bot ────────────────────────────────────────────────────

export type TelegramConfigDTO = {
  tg_bot_token: string;
  tg_bot_enabled: string;
  tg_admin_chat_id: string;
  tg_bot_language: string;
};

export type TelegramStatusDTO = {
  running: boolean;
  started_at: string | null;
  uptime_seconds: number;
};

export type TelegramTokenDTO = {
  id: number;
  token: string;
  peer_ids: number[];
  deep_link?: string;
  created_at: string | null;
  used_at: string | null;
  used_by: { telegram_username: string; first_name: string } | null;
  expires_at: string | null;
  single_use: boolean;
};

export type TelegramPeerInfo = {
  binding_id: number;
  peer_id: number;
  peer_name: string;
  router_name: string;
  interface: string;
  visible: boolean;
};

export type TelegramUserDTO = {
  id: number;
  telegram_user_id: number;
  telegram_username: string;
  first_name: string;
  last_name: string;
  language: string;
  is_blocked: boolean;
  created_at: string | null;
  peers: TelegramPeerInfo[];
};

export type TelegramNotifConfigDTO = {
  id: number;
  event_type: string;
  notify_clients: boolean;
  notify_admin: boolean;
  enabled: boolean;
};

export async function getTelegramConfig(): Promise<TelegramConfigDTO> {
  return fetchJson("/api/telegram/config");
}

export async function updateTelegramConfig(cfg: Partial<{
  tg_bot_token: string;
  tg_bot_enabled: boolean;
  tg_admin_chat_id: string;
  tg_bot_language: string;
}>): Promise<{ ok: boolean }> {
  return fetchJson("/api/telegram/config", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(cfg),
  });
}

export async function getTelegramStatus(): Promise<TelegramStatusDTO> {
  return fetchJson("/api/telegram/status");
}

export async function restartTelegramBot(): Promise<{ ok: boolean; started: boolean }> {
  return fetchJson("/api/telegram/restart", { method: "POST" });
}

export async function createTelegramToken(data: {
  peer_ids: number[];
  expires_hours?: number;
  single_use?: boolean;
}): Promise<TelegramTokenDTO> {
  return fetchJson("/api/telegram/tokens", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function listTelegramTokens(): Promise<TelegramTokenDTO[]> {
  return fetchJson("/api/telegram/tokens");
}

export async function revokeTelegramToken(id: number): Promise<void> {
  await fetchJson(`/api/telegram/tokens/${id}`, { method: "DELETE" });
}

export async function listTelegramUsers(): Promise<TelegramUserDTO[]> {
  return fetchJson("/api/telegram/users");
}

export async function deleteTelegramUser(id: number): Promise<void> {
  await fetchJson(`/api/telegram/users/${id}`, { method: "DELETE" });
}

export async function patchTelegramUser(id: number, data: { is_blocked?: boolean }): Promise<void> {
  await fetchJson(`/api/telegram/users/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function patchTelegramBinding(id: number, data: { visible?: boolean }): Promise<void> {
  await fetchJson(`/api/telegram/bindings/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function deleteTelegramBinding(id: number): Promise<void> {
  await fetchJson(`/api/telegram/bindings/${id}`, { method: "DELETE" });
}

export async function getTelegramNotifConfig(): Promise<TelegramNotifConfigDTO[]> {
  return fetchJson("/api/telegram/notifications");
}

export async function updateTelegramNotifConfig(configs: Array<{
  event_type: string;
  notify_clients?: boolean;
  notify_admin?: boolean;
  enabled?: boolean;
}>): Promise<void> {
  await fetchJson("/api/telegram/notifications", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ configs }),
  });
}

export async function testTelegramNotify(): Promise<{ ok: boolean }> {
  return fetchJson("/api/telegram/test-notify", { method: "POST" });
}
