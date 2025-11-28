export type RouterProto = "rest" | "rest-http" | "api" | "api-plain";
export type Router = {
  id: number; name: string; host: string; proto: RouterProto; port: number; username: string; tls_verify: boolean;
};

const base = ""; // proxied via Vite

async function fetchJson(input: RequestInfo, init?: RequestInit, timeoutMs = 10000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  const url = typeof input === "string" ? input : (input as any)?.url || "";
  try {
    const res = await fetch(input, { ...(init || {}), signal: ctrl.signal });
    if (!res.ok) {
      let detail = "";
      try {
        const ct = res.headers.get("content-type") || "";
        if (ct.includes("application/json")) {
          const j = await res.json();
          detail = j?.detail ? String(j.detail) : JSON.stringify(j);
        } else {
          detail = await res.text();
        }
      } catch {}
      throw new Error(`HTTP ${res.status} ${res.statusText} at ${url}${detail ? `: ${detail}` : ""}`);
    }
    const ct = res.headers.get("content-type") || "";
    if (ct.includes("application/json")) return res.json();
    return res.text();
  } catch (e: any) {
    if (e?.name === "AbortError") {
      throw new Error(`Timed out after ${Math.round(timeoutMs/1000)}s calling ${url}`);
    }
    throw new Error(`Network error calling ${url}: ${e?.message || e}`);
  } finally {
    clearTimeout(t);
  }
}

export async function getSettings() { return fetchJson(`${base}/api/settings`); }

export async function putSettings(body: any) { return fetchJson(`${base}/api/settings`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); }

export async function listRouters(): Promise<Router[]> { return fetchJson(`${base}/api/routers`); }

export async function createRouter(body: { name: string; host: string; proto: RouterProto; port: number; username: string; password: string; tls_verify: boolean; }) {
  return fetchJson(`${base}/api/routers`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

export async function updateRouter(routerId: number, body: Partial<{ name: string; host: string; proto: RouterProto; port: number; username: string; password: string; tls_verify: boolean; }>) {
  return fetchJson(`${base}/api/routers/${routerId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

export async function deleteRouter(routerId: number) {
  return fetchJson(`${base}/api/routers/${routerId}`, { method: "DELETE" });
}

export async function routerInterfaces(routerId: number): Promise<string[]> { return fetchJson(`${base}/api/routers/${routerId}/interfaces`); }

export type WGInterfaceConfig = { name: string; public_key: string; listen_port: number; public_host: string };
export async function routerInterfaceDetail(routerId: number, iface: string): Promise<WGInterfaceConfig> {
  return fetchJson(`${base}/api/routers/${routerId}/interfaces/${encodeURIComponent(iface)}`);
}

export type MonthlySummaryPoint = { day: string; rx: number; tx: number };
export async function getMonthlySummary(): Promise<MonthlySummaryPoint[]> {
  return fetchJson(`${base}/api/summary/month`);
}

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
  return fetchJson(`${base}/api/metrics`);
}

export type PeerView = {
  id?: number; interface: string; name: string; public_key: string; allowed_address: string; disabled: boolean; endpoint: string; last_handshake?: number; online: boolean;
};

export async function routerPeers(routerId: number, iface: string): Promise<PeerView[]> { return fetchJson(`${base}/api/routers/${routerId}/peers?interface=${encodeURIComponent(iface)}`); }

export async function testRouter(routerId: number): Promise<{ ok: boolean }>{ return fetchJson(`${base}/api/routers/${routerId}/test`, { method: "POST" }, 5000); }

// Demo endpoints for frontend development
export async function seedDemo() { return fetchJson(`${base}/api/demo/seed`, { method: "POST" }); }
export type SavedPeer = { id: number; router_id: number; interface: string; name: string; public_key: string; allowed_address: string; disabled: boolean; selected: boolean };
export async function listSavedPeers(): Promise<SavedPeer[]> { return fetchJson(`${base}/api/peers`); }
export async function listSavedPeersSelected(): Promise<SavedPeer[]> { return fetchJson(`${base}/api/peers?selected_only=true`); }
export async function createDemoPeer(body: { interface: string; name: string; public_key: string; allowed_address: string; comment?: string }): Promise<SavedPeer> {
  return fetchJson(`${base}/api/demo/peers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
export type UsagePoint = { day: string; rx: number; tx: number };
export async function getPeerUsage(peerId: number, opts?: { window?: "daily" | "raw"; seconds?: number }): Promise<UsagePoint[]> {
  const window = opts?.window || "daily";
  const params = new URLSearchParams({ window });
  if (opts?.seconds && opts.seconds > 0) params.set("seconds", String(opts.seconds));
  return fetchJson(`${base}/api/peers/${peerId}/usage?${params.toString()}`);
}

export async function importPeers(routerId: number, items: { interface: string; public_key: string; selected: boolean }[]) {
  return fetchJson(`${base}/api/routers/${routerId}/peers/import`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(items),
  });
}

export async function patchPeer(peerId: number, body: Partial<{ selected: boolean; disabled: boolean }>) {
  return fetchJson(`${base}/api/peers/${peerId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export type Quota = {
  monthly_limit_bytes?: number | null;
  reset_day: number;
  valid_from?: string | null;
  valid_until?: string | null;
  used_rx: number;
  used_tx: number;
};

export async function getPeerQuota(peerId: number): Promise<Quota> { return fetchJson(`${base}/api/peers/${peerId}/quota`); }
export async function patchPeerQuota(peerId: number, body: Partial<{ monthly_limit_bytes: number | null; valid_from: string | null; valid_until: string | null }>) {
  return fetchJson(`${base}/api/peers/${peerId}/quota`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function resetPeerMetrics(peerId: number) {
  return fetchJson(`${base}/api/peers/${peerId}/reset_metrics`, { method: "POST" });
}

export async function deletePeer(peerId: number) {
  return fetchJson(`${base}/api/peers/${peerId}`, { method: "DELETE" });
}

// Admin maintenance
export async function purgeUsage() {
  return fetchJson(`${base}/api/admin/purge_usage`, { method: "POST" });
}

export async function purgePeers() {
  return fetchJson(`${base}/api/admin/purge_peers`, { method: "POST" });
}


