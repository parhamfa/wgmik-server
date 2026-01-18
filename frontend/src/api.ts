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
  dashboard_refresh_seconds: number;
  peer_refresh_seconds: number;
  peer_default_scope_unit: string;
  peer_default_scope_value: number;
  dashboard_scope_unit: string;
  dashboard_scope_value: number;
  show_hw_stats: boolean; // Alias field often used
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

export async function listPeers(routerId: number, selectedOnly = false, iface?: string): Promise<PeerListDTO[]> {
  const params = new URLSearchParams();
  if (routerId) params.set("router_id", String(routerId));
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
export async function listSavedPeersSelected(routerId?: number | null): Promise<SavedPeer[]> {
  const q = routerId ? `?selected_only=true&router_id=${routerId}` : "?selected_only=true";
  const rows = await fetchJson(`/api/peers${q}`);
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
};
export type PeerUsageSummary = PeerUsageSummaryDTO; // Alias

export async function getPeersSummary(opts: { seconds?: number; days?: number; routerId?: number | null }): Promise<PeerUsageSummaryDTO[]> {
  const params = new URLSearchParams();
  if (opts.seconds) params.set("seconds", String(opts.seconds));
  if (opts.days) params.set("days", String(opts.days));
  if (opts.routerId) params.set("router_id", String(opts.routerId));
  const rows = await fetchJson(`/api/summary/peers?${params.toString()}`);
  return rows.map((r: any) => ({
    peer_id: r.peer_id,
    rx: r.rx ?? 0,
    tx: r.tx ?? 0,
    total_rx: r.rx ?? 0,
    total_tx: r.tx ?? 0,
  }));
}

export type SummaryRawPointDTO = {
  ts: string;
  rx: number;
  tx: number;
};
export type SummaryRawPoint = SummaryRawPointDTO; // Alias

export async function getSummaryRaw(seconds: number, routerId?: number | null, interval?: number): Promise<SummaryRawPointDTO[]> {
  let url = `/api/summary/raw?seconds=${seconds}`;
  if (routerId) url += `&router_id=${routerId}`;
  if (interval && interval > 0) {
    url += `&interval=${interval}`;
  }
  return fetchJson(url);
}

// RESTORED: getMonthlySummary + MonthlySummaryPoint
export type MonthlySummaryPoint = { day: string; rx: number; tx: number };
export async function getMonthlySummary(days?: number, routerId?: number | null): Promise<MonthlySummaryPoint[]> {
  const params = new URLSearchParams();
  if (days && days > 0) params.set("days", String(days));
  if (routerId && routerId > 0) params.set("router_id", String(routerId));
  const q = params.toString();
  return fetchJson(`/api/summary/month${q ? `?${q}` : ""}`);
}

// RESTORED: getPeerUsage + UsagePoint
export type UsagePoint = { day: string; rx: number; tx: number };
export async function getPeerUsage(peerId: number, opts?: { window?: "daily" | "raw"; seconds?: number, interval?: number }): Promise<UsagePoint[]> {
  const window = opts?.window || "daily";
  const params = new URLSearchParams({ window });
  if (opts?.seconds && opts.seconds > 0) params.set("seconds", String(opts.seconds));
  if (opts?.interval && opts.interval > 0) params.set("interval", String(opts.interval));
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


// RESTORED: Admin Actions
export async function purgeUsage() {
  return fetchJson("/api/admin/purge_usage", { method: "POST" });
}
export async function purgePeers() {
  return fetchJson("/api/admin/purge_peers", { method: "POST" });
}
