import React from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";

type Point = { day: string; rx: number; tx: number };

type Payload = {
  peerName: string;
  scopeLabel: string;
  mode: "days" | "raw";
  timezone: string;
  points: Point[];
};

function fmtBytes(n: number) {
  if (!n || n <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let u = 0;
  let x = n;
  while (x >= 1024 && u < units.length - 1) { x /= 1024; u++; }
  return `${x.toFixed(x >= 100 ? 0 : x >= 10 ? 1 : 2)} ${units[u]}`;
}

export default function UsageChartRender() {
  const [data, setData] = React.useState<Payload | null>(null);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    try {
      const raw = window.location.hash.slice(1);
      if (!raw) { setError("No data"); return; }
      const json = JSON.parse(decodeURIComponent(raw)) as Payload;
      setData(json);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Parse error");
    }
  }, []);

  if (error) return <div className="p-4 text-red-600">{error}</div>;
  if (!data) return null;

  const tz = data.timezone || "UTC";
  const usage = data.points || [];

  return (
    <div id="usage-chart-card" className="p-5 bg-white dark:bg-gray-950 inline-block" style={{ minWidth: 480, maxWidth: 640 }}>
      <div className="text-sm font-medium text-gray-800 dark:text-gray-100 mb-1">{data.peerName}</div>
      <div className="text-xs text-gray-500 dark:text-gray-400 mb-3">{data.scopeLabel}</div>
      <div className="h-56 w-[560px]">
        {usage.length === 0 ? (
          <div className="h-full flex items-center justify-center text-sm text-gray-500 dark:text-gray-400">No data</div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            {/* Telegram screenshots: no draw animation (would capture mid-animation). */}
            <AreaChart data={usage} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="tg-g2" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="var(--chart-fill-1)" stopOpacity={0.25} />
                  <stop offset="100%" stopColor="var(--chart-fill-1)" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="tg-g3" x1="0" y1="0" x2="0" y2="1">
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
                    if (data.mode === "days") {
                      const d = new Date(`${val}T00:00:00Z`);
                      return new Intl.DateTimeFormat(undefined, {
                        timeZone: tz,
                        month: "numeric",
                        day: "numeric",
                      }).format(d);
                    }
                    const d = new Date(val);
                    return new Intl.DateTimeFormat(undefined, {
                      timeZone: tz,
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
                formatter={(value: number, name: string) => [fmtBytes(value as number), name]}
                labelFormatter={(label) => {
                  try {
                    if (data.mode === "days") {
                      const d = new Date(`${label}T00:00:00Z`);
                      return new Intl.DateTimeFormat(undefined, {
                        timeZone: tz,
                        dateStyle: "full",
                      }).format(d);
                    }
                    const d = new Date(label);
                    return new Intl.DateTimeFormat(undefined, {
                      timeZone: tz,
                      dateStyle: "medium",
                      timeStyle: "medium",
                    }).format(d);
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
              <Area
                type="monotone"
                dataKey="tx"
                name="TX (download)"
                stroke="var(--chart-line-1)"
                fill="url(#tg-g2)"
                strokeWidth={2}
                isAnimationActive={false}
                animationDuration={0}
              />
              <Area
                type="monotone"
                dataKey="rx"
                name="RX (upload)"
                stroke="var(--chart-line-2)"
                fill="url(#tg-g3)"
                strokeWidth={2}
                isAnimationActive={false}
                animationDuration={0}
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
      {usage.length > 0 ? (
        <div className="flex items-center justify-center gap-6 mt-2 text-xs text-gray-500 dark:text-gray-400">
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
      ) : null}
    </div>
  );
}
