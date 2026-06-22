export const base = "";

function formatApiErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const parts = detail
      .map((item) => {
        if (!item || typeof item !== "object") return String(item);
        const row = item as { loc?: unknown; msg?: unknown; message?: unknown };
        const message = typeof row.msg === "string" ? row.msg : typeof row.message === "string" ? row.message : "";
        const loc = Array.isArray(row.loc) ? row.loc.filter((part) => part !== "body").join(".") : "";
        return [loc, message].filter(Boolean).join(": ");
      })
      .filter(Boolean);
    return parts.join("; ");
  }
  if (detail && typeof detail === "object") {
    const row = detail as { detail?: unknown; error?: unknown; message?: unknown };
    return (
      formatApiErrorDetail(row.detail) ||
      formatApiErrorDetail(row.message) ||
      formatApiErrorDetail(row.error) ||
      JSON.stringify(detail)
    );
  }
  return detail == null ? "" : String(detail);
}

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
      if (json.detail) msg = formatApiErrorDetail(json.detail);
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
  date_calendar: "gregorian" | "persian";
  week_start_day: number;
  /** Collapsed router sections: max peer pill cards before "Show all" (1–50). */
  dashboard_peer_preview_count: number;
  peer_default_scope_unit: string;
  peer_default_scope_value: number;
  dashboard_scope_unit: string;
  dashboard_scope_value: number;
  dashboard_router_scope: "all" | "selected";
  dashboard_selected_router_ids: number[];
  dashboard_filter_status: string;
  dashboard_sort_by: string;
  dashboard_time_mode: "today" | "this_month" | "all_time" | "rolling" | "custom";
  dashboard_custom_start: string;
  dashboard_custom_end: string;
  /** Dashboard usage chart: fixed start at local midnight, end tracks "now" */
  dashboard_time_frame_today: boolean;
  peer_time_mode: "today" | "this_month" | "all_time" | "rolling" | "custom";
  peer_custom_start: string;
  peer_custom_end: string;
  /** Peer detail usage chart: same semantics as dashboard_time_frame_today */
  peer_time_frame_today: boolean;
  show_hw_stats: boolean; // Alias field often used
  raw_sample_retention_hours: number;
  minute_rollup_retention_days: number;
  daily_rollup_retention_days: number;
  usage_maintenance_auto_enabled: boolean;
  usage_maintenance_auto_frequency: "daily" | "every_n_days" | "weekly";
  usage_maintenance_auto_interval_days: number;
  usage_maintenance_auto_weekday: number;
  usage_maintenance_auto_time: string;
  usage_maintenance_backup_keep: number;
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

export type LocalUserDTO = {
  id: number;
  username: string;
  is_admin: boolean;
  is_active: boolean;
  last_login_at: string | null;
  locked_until: string | null;
  must_change_password: boolean;
  created_at: string;
};

export type AuthBootstrapDTO = {
  user: LocalUserDTO;
  router_count: number;
  enabled_router_count: number;
  peer_count: number;
  selected_peer_count: number;
  needs_onboarding: boolean;
  needs_peer_import: boolean;
};

export async function getAuthBootstrap(): Promise<AuthBootstrapDTO> {
  return fetchJson("/api/auth/bootstrap");
}

export type SetupStateDTO = {
  needs_initial_setup: boolean;
};

export async function getSetupState(): Promise<SetupStateDTO> {
  return fetchJson("/api/auth/setup-state");
}

export async function createInitialAdmin(data: { username: string; password: string }): Promise<{ ok: boolean }> {
  return fetchJson("/api/auth/setup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function listUsers(): Promise<LocalUserDTO[]> {
  return fetchJson("/api/users");
}

export async function createUser(data: { username: string; password: string }): Promise<LocalUserDTO> {
  return fetchJson("/api/users", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function updateUserAccount(
  userId: number,
  data: { is_active?: boolean; unlock?: boolean },
): Promise<LocalUserDTO> {
  return fetchJson(`/api/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function resetUserPassword(userId: number, data: { new_password: string }): Promise<{ ok: boolean }> {
  return fetchJson(`/api/users/${userId}/reset-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export async function deleteUserAccount(userId: number): Promise<{ ok: boolean }> {
  return fetchJson(`/api/users/${userId}`, { method: "DELETE" });
}

export async function changePassword(data: { current_password: string; new_password: string }): Promise<{ ok: boolean }> {
  return fetchJson("/api/auth/change-password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
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
  enabled: boolean;
  ros_version: string;
  ros_version_checked_at: string | null;
  ros_supported: boolean;
};

// Aliases for backward compat
export type Router = RouterDTO;

export type RouterDeleteImpactDTO = {
  router_id: number;
  router_name: string;
  dashboard_selected: boolean;
  peer_count: number;
  selected_peer_count: number;
  usage_sample_rows: number;
  usage_minute_rows: number;
  usage_daily_rows: number;
  usage_monthly_rows: number;
  quota_count: number;
  action_count: number;
  telegram_binding_count: number;
  telegram_log_count: number;
  signup_token_count: number;
  fair_usage_assignment_count: number;
  fair_usage_state_count: number;
  router_rule_count: number;
  merge_ledger_count: number;
  peer_setting_count: number;
};

export type RouterDeleteResultDTO = RouterDeleteImpactDTO & {
  signup_tokens_updated: number;
  signup_tokens_deleted: number;
  backup_path?: string | null;
  post_delete_quick_check?: string;
};

function applyRouterFilters(params: URLSearchParams, routerId?: number | null, routerIds?: number[] | null) {
  if (routerIds && routerIds.length > 0) {
    for (const id of routerIds) {
      if (id > 0) params.append("router_ids", String(id));
    }
    return;
  }
  if (routerId && routerId > 0) params.set("router_id", String(routerId));
}

export type RouterCreateDTO = {
  name: string;
  host: string;
  proto: RouterProto;
  port: number;
  username: string;
  tls_verify: boolean;
  password: string;
  enabled?: boolean;
};
export type RouterUpdateDTO = Partial<Omit<RouterCreateDTO, "password">> & { password?: string };

export async function setRouterEnabled(routerId: number, enabled: boolean): Promise<RouterDTO> {
  return updateRouter(routerId, { enabled }) as Promise<RouterDTO>;
}

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
export async function updateRouter(routerId: number, dto: RouterUpdateDTO) {
  return fetchJson(`/api/routers/${routerId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dto)
  });
}

export async function deleteRouter(routerId: number): Promise<RouterDeleteResultDTO> {
  return fetchJson(`/api/routers/${routerId}`, { method: "DELETE" });
}

export async function getRouterDeleteImpact(routerId: number): Promise<RouterDeleteImpactDTO> {
  return fetchJson(`/api/routers/${routerId}/delete-impact`);
}

export async function listInterfaces(routerId: number): Promise<string[]> {
  return fetchJson(`/api/routers/${routerId}/interfaces`);
}
export const routerInterfaces = listInterfaces; // Alias

// RESTORED: routerInterfaceDetail
export type WGInterfaceConfig = { name: string; public_key: string; listen_port: number; public_host: string; addresses?: string[] };
export async function routerInterfaceDetail(routerId: number, iface: string): Promise<WGInterfaceConfig> {
  return fetchJson(`/api/routers/${routerId}/interfaces/${encodeURIComponent(iface)}`);
}

export async function testRouter(routerId: number): Promise<{ ok: boolean; ros_version?: string; ros_version_checked_at?: string | null; ros_supported?: boolean }> {
  return fetchJson(`/api/routers/${routerId}/test`, { method: "POST" });
}

// ... Automated TLS setup ...

export type TlsSetupMethod = "self_signed" | "letsencrypt";

export type TlsSetupStepDTO = {
  id: "check" | "certificate" | "service" | "verify";
  label: string;
  status: "pending" | "running" | "ok" | "failed";
  detail: string;
};

export type TlsSetupJobDTO = {
  router_id: number;
  method: TlsSetupMethod;
  status: "running" | "ok" | "failed";
  error: string;
  steps: TlsSetupStepDTO[];
  result: {
    proto?: RouterProto;
    host?: string;
    port?: number;
    tls_verify?: boolean;
    service?: string;
    plain_service?: string;
    cert_name?: string;
    fingerprint?: string;
    expires_after?: string;
    ros_version?: string;
  };
};

export async function startTlsSetup(
  routerId: number,
  dto: { method: TlsSetupMethod; common_name?: string; days_valid?: number; dns_name?: string },
): Promise<TlsSetupJobDTO> {
  return fetchJson(`/api/routers/${routerId}/tls-setup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dto),
  });
}

export async function getTlsSetupStatus(routerId: number): Promise<TlsSetupJobDTO> {
  return fetchJson(`/api/routers/${routerId}/tls-setup/status`);
}

export async function applyTlsSetup(routerId: number, dto: { disable_plain?: boolean } = {}): Promise<RouterDTO> {
  return fetchJson(`/api/routers/${routerId}/tls-setup/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(dto),
  });
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
  disabled: boolean;
  selected: boolean;
  router_sync_status: "synced" | "missing" | "new";
  router_sync_first_seen_at?: string | null;
  router_sync_last_seen_at?: string | null;
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
export async function patchPeer(
  peerId: number,
  body: Partial<{ selected: boolean; disabled: boolean; name: string }>,
): Promise<PeerListDTO> {
  const p = await fetchJson(`/api/peers/${peerId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return normalizePeer(p);
}

export async function resolvePeerRouterSync(
  peerId: number,
  action: "hide" | "delete" | "accept",
) {
  return fetchJson(`/api/peers/${peerId}/router-sync/resolve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
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
  phase_label?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at?: string | null;
  cancelled_at?: string | null;
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
  cancel_requested: boolean;
  can_cancel: boolean;
  trigger?: "manual" | "scheduled";
  next_scheduled_run?: string | null;
  last_auto_run?: string | null;
  elapsed_seconds: number;
  estimated_remaining_seconds?: number | null;
  progress_percent: number;
  phase_progress_percent: number;
  processed_units: number;
  total_units: number;
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
  public_key: string;
  allowed_address: string;
  private_key?: string;
  preshared_key?: string;
  config_name?: string;
  custom_endpoint?: string;
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

export type PeerClientExportPrefsDTO = {
  config_name: string;
  custom_endpoint: string;
  preshared_key: string | null;
};

export async function getPeerClientExportPrefs(peerId: number): Promise<PeerClientExportPrefsDTO> {
  return fetchJson(`/api/peers/${peerId}/client_export_prefs`);
}

export async function patchPeerClientExportPrefs(
  peerId: number,
  body: Partial<{ config_name: string; custom_endpoint: string; preshared_key: string }>
): Promise<PeerClientExportPrefsDTO> {
  return fetchJson(`/api/peers/${peerId}/client_export_prefs`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export type PeerRenewKeysDTO = {
  peer: PeerListDTO;
  private_key: string;
};

export async function renewPeerKeys(peerId: number): Promise<PeerRenewKeysDTO> {
  return fetchJson(`/api/peers/${peerId}/renew_keys`, { method: "POST" });
}

export type PeerImportItem = { interface: string; public_key: string; selected: boolean };
export async function importPeers(routerId: number, items: PeerImportItem[]) {
  return fetchJson(`/api/routers/${routerId}/peers/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(items),
  });
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

export type FairUsageTierDTO = {
  id: number;
  sort_order: number;
  threshold_bytes: number;
  name: string;
  throttle_download_kbps: number;
  throttle_upload_kbps: number;
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
  sort_order: number;
  passthrough: boolean;
  enabled: boolean;
  tiered: boolean;
  tiers: FairUsageTierDTO[];
  created_at: string;
  updated_at: string;
  assigned_peer_count: number;
  assigned_peers: FairUsageAssignedPeer[];
};

export type FairUsageTierInputDTO = {
  threshold_bytes: number;
  name?: string;
  throttle_download_kbps: number;
  throttle_upload_kbps: number;
  sort_order?: number;
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
  sort_order?: number;
  passthrough?: boolean;
  enabled?: boolean;
  tiered?: boolean;
  tiers?: FairUsageTierInputDTO[];
};

export type FairUsageTierStatusDTO = {
  tier_id: number;
  sort_order: number;
  threshold_bytes: number;
  name: string;
  throttle_download_kbps: number;
  throttle_upload_kbps: number;
  is_active: boolean;
};

export type FairUsageRuleStatusItemDTO = {
  rule_id: number;
  rule_name: string;
  quota_mode: string;
  download_quota_bytes: number;
  upload_quota_bytes: number | null;
  throttle_download_kbps: number;
  throttle_upload_kbps: number;
  time_scope: string | null;
  scope_period_count: number;
  scope_period_unit: string;
  scope_label: string;
  scope_type: string | null;
  sort_order?: number;
  passthrough?: boolean;
  used_rx: number;
  used_tx: number;
  over_quota: boolean;
  is_effective?: boolean;
  next_reset: string | null;
  tiered?: boolean;
  tiers?: FairUsageTierStatusDTO[];
};

export type FairUsagePeerStatusDTO = {
  peer_id: number;
  /** Each applicable fair-usage rule with its own period usage and reset time. */
  rules: FairUsageRuleStatusItemDTO[];
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
  sort_order?: number;
  passthrough?: boolean;
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
export async function cancelUsageMaintenance(): Promise<UsageMaintenanceStatusDTO> {
  return fetchJson("/api/admin/usage_maintenance/cancel", { method: "POST" });
}

export type BackupStatusDTO = {
  running: boolean;
  phase: string;
  phase_label?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  updated_at?: string | null;
  last_error?: string | null;
  detail?: string | null;
  file_size?: number | null;
  download_token?: string | null;
  download_filename?: string | null;
  secret_key?: string | null;
  elapsed_seconds: number;
  progress_percent: number;
};

export type BackupRestoreResultDTO = {
  ok: boolean;
  message: string;
  pre_restore_backup?: string | null;
};

export async function getBackupStatus(): Promise<BackupStatusDTO> {
  return fetchJson("/api/admin/backup");
}

export async function runBackup(): Promise<BackupStatusDTO> {
  return fetchJson("/api/admin/backup/run", { method: "POST" });
}

export function backupDownloadUrl(token: string): string {
  return `${base}/api/admin/backup/download?token=${encodeURIComponent(token)}`;
}

export async function restoreBackup(file: File, key: string): Promise<BackupRestoreResultDTO> {
  const form = new FormData();
  form.append("file", file);
  form.append("key", key.trim());
  const res = await fetch(`${base}/api/admin/backup/restore`, {
    method: "POST",
    body: form,
    credentials: "include",
  });
  if (res.status === 401) {
    if (window.location.pathname !== "/login") {
      window.location.href = "/login";
    }
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const text = await res.text();
    let msg = text;
    try {
      const json = JSON.parse(text);
      if (json.detail) msg = formatApiErrorDetail(json.detail);
    } catch { /* ignore */ }
    throw new Error(msg || res.statusText);
  }
  return res.json();
}

export async function waitForHealth(timeoutMs = 120000, intervalMs = 2000): Promise<void> {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    try {
      const res = await fetch(`${base}/health`, { credentials: "include" });
      if (res.ok) return;
    } catch { /* server restarting */ }
    await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
  }
  throw new Error("Timed out waiting for the server to restart");
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
  subscribed_notifications: string[];
};

export type TelegramNotifConfigDTO = {
  id: number;
  event_type: string;
  notify_clients: boolean;
  notify_admin: boolean;
  enabled: boolean;
};

export type TelegramBroadcastDTO = {
  id: number;
  body: string;
  body_preview: string;
  has_photo: boolean;
  photo_filename: string;
  photo_mime: string;
  recipient_mode: "all" | "selected";
  status: string;
  total_count: number;
  sent_count: number;
  failed_count: number;
  acknowledged_count: number;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type TelegramBroadcastRecipientDTO = {
  id: number;
  telegram_user_id: number | null;
  chat_id: number;
  display_name: string;
  status: string;
  telegram_message_id: number | null;
  error_code: string;
  error_message: string;
  sent_at: string | null;
  acknowledged_at: string | null;
};

export type TelegramBroadcastDetailDTO = TelegramBroadcastDTO & {
  recipients: TelegramBroadcastRecipientDTO[];
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

export async function setTelegramUserPeers(id: number, peer_ids: number[]): Promise<void> {
  await fetchJson(`/api/telegram/users/${id}/peers`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ peer_ids }),
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

export async function createTelegramBroadcast(data: {
  text: string;
  recipient_mode: "all" | "selected";
  recipient_ids: number[];
  photo?: File | null;
}): Promise<TelegramBroadcastDTO> {
  const form = new FormData();
  form.set("text", data.text);
  form.set("recipient_mode", data.recipient_mode);
  form.set("recipient_ids", JSON.stringify(data.recipient_ids));
  if (data.photo) form.set("photo", data.photo);
  return fetchJson("/api/telegram/broadcasts", {
    method: "POST",
    body: form,
  });
}

export async function listTelegramBroadcasts(params?: { limit?: number; offset?: number }): Promise<{ items: TelegramBroadcastDTO[]; total: number }> {
  const qs = new URLSearchParams();
  qs.set("limit", String(params?.limit ?? 25));
  qs.set("offset", String(params?.offset ?? 0));
  return fetchJson(`/api/telegram/broadcasts?${qs.toString()}`);
}

export async function getTelegramBroadcast(id: number): Promise<TelegramBroadcastDetailDTO> {
  return fetchJson(`/api/telegram/broadcasts/${id}`);
}

export async function retryFailedTelegramBroadcast(id: number): Promise<{ ok: boolean; queued: number }> {
  return fetchJson(`/api/telegram/broadcasts/${id}/retry-failed`, { method: "POST" });
}

export async function testTelegramNotify(): Promise<{ ok: boolean }> {
  return fetchJson("/api/telegram/test-notify", { method: "POST" });
}

export async function testTelegramNotifyEvent(eventType: string): Promise<{ ok: boolean }> {
  return fetchJson(`/api/telegram/test-notify/${encodeURIComponent(eventType)}`, { method: "POST" });
}
