import React from "react";
import type { FairUsagePeerStatusDTO, FairUsageRuleStatusItemDTO } from "../api";

function fmtBytes(n: number) {
  if (!n || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let u = 0;
  let x = n;
  while (x >= 1024 && u < units.length - 1) { x /= 1024; u++; }
  return `${x.toFixed(x >= 100 ? 0 : x >= 10 ? 1 : 2)} ${units[u]}`;
}

function effectiveThrottleForRule(fr: FairUsageRuleStatusItemDTO): { dl: number; ul: number; label: string } {
  if (fr.tiered && fr.tiers?.length) {
    const a = fr.tiers.find((t) => t.is_active);
    if (a) return { dl: a.throttle_download_kbps, ul: a.throttle_upload_kbps, label: (a.name || "").trim() || fr.rule_name };
  }
  return { dl: fr.throttle_download_kbps, ul: fr.throttle_upload_kbps, label: fr.rule_name };
}

export default function FairUsageRender() {
  const [data, setData] = React.useState<{ status: FairUsagePeerStatusDTO; peerName: string } | null>(null);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    try {
      const raw = window.location.hash.slice(1);
      if (!raw) { setError("No data"); return; }
      const json = JSON.parse(decodeURIComponent(raw));
      setData(json);
    } catch (e: any) {
      setError(e.message || "Parse error");
    }
  }, []);

  if (error) return <div className="p-4 text-red-600">{error}</div>;
  if (!data) return null;

  const fuStatus = data.status;
  const fuRules = fuStatus.rules || [];
  const peerName = data.peerName || "Peer";

  return (
    <div id="fu-card" className="p-5 bg-white dark:bg-gray-950 inline-block" style={{ minWidth: 420, maxWidth: 560 }}>
      <div className="grid gap-4">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-gray-500 dark:text-gray-400">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
            <span className="text-sm font-medium text-gray-700 dark:text-gray-200">Fair Usage</span>
            {fuRules.length > 1 ? (
              <span className="text-[11px] text-gray-500 dark:text-gray-400">({fuRules.length} rules)</span>
            ) : null}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`rounded-full px-2.5 py-0.5 text-[11px] ${fuStatus.throttled ? "bg-amber-100 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300" : "bg-green-100 text-green-800 dark:bg-green-500/10 dark:text-green-300"}`}>
              {fuStatus.throttled ? "Throttled" : "Normal"}
            </span>
            {fuStatus.throttled && (() => {
              const v = fuRules.filter((r) => r.over_quota);
              const eff = (r: FairUsageRuleStatusItemDTO) => effectiveThrottleForRule(r);
              const d = v.length ? Math.min(...v.map((r) => eff(r).dl)) : fuStatus.throttle_download_kbps;
              const u = v.length ? Math.min(...v.map((r) => eff(r).ul)) : fuStatus.throttle_upload_kbps;
              const dlNames = v.filter((r) => eff(r).dl === d).map((r) => eff(r).label);
              const ulNames = v.filter((r) => eff(r).ul === u).map((r) => eff(r).label);
              const names = [...new Set([...dlNames, ...ulNames])];
              const label = names.length > 0 ? names.join(" · ") : fuStatus.rule_name ?? "Rule";
              return (
                <span className="text-[11px] text-amber-700 dark:text-amber-300">
                  {label}: {(d / 1000).toFixed(1)}/{(u / 1000).toFixed(1)} Mbps
                </span>
              );
            })()}
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
                  {fr.over_quota ? <span className="ml-2 text-amber-700 dark:text-amber-300">Over quota</span> : null}
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="rounded-full bg-gray-100 text-gray-700 px-2 py-0.5 text-[11px] dark:bg-gray-800 dark:text-gray-300">{fr.scope_label}</span>
                  <span className="rounded-full bg-indigo-50 text-indigo-700 px-2 py-0.5 text-[11px] dark:bg-indigo-500/10 dark:text-indigo-300 capitalize">{fr.scope_type}</span>
                </div>
              </div>
              <div className="grid gap-3">
                {fr.tiered && fr.tiers && fr.tiers.length > 0 ? (
                  <div className="grid gap-2">
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
                    ? new Date(fr.next_reset).toLocaleString(undefined, {
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                        hour: "numeric",
                        minute: "2-digit",
                      })
                    : new Date(fr.next_reset).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      })}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
      <div className="mt-3 text-[10px] text-gray-400 dark:text-gray-600">{peerName}</div>
    </div>
  );
}
