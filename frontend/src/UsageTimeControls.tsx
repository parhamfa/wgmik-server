import React from "react";
import CalendarDateTimeInput from "./CalendarDateTimeInput";
import {
  formatCalendarLocalValue,
  startOfLocalTodayValue,
  startOfSelectedCalendarMonthValue,
  zonedNowToDatetimeLocalValue,
} from "./datetimeLocal";
import { useLooseNumberInput } from "./hooks/useLooseNumberInput";
import type { ScopeUnit } from "./useAutoSaveSettings";

export type UsageTimeMode = "today" | "this_month" | "all_time" | "rolling" | "custom";

type Props = {
  mode: UsageTimeMode;
  rollingValue: number;
  rollingUnit: ScopeUnit;
  customStart: string;
  customEnd: string;
  autoRefreshSeconds: number;
  dateCalendar: string;
  timezone: string;
  weekStartDay: number;
  showAutoRefresh?: boolean;
  onModeChange: (mode: UsageTimeMode) => void;
  onRollingValueChange: (value: number) => void;
  onRollingUnitChange: (unit: ScopeUnit) => void;
  onCustomStartChange: (value: string) => void;
  onCustomEndChange: (value: string) => void;
  /** When set (e.g. dashboard), show “Max peer cards” under Auto refresh and persist via callback. */
  peerPreviewMax?: number;
  onPeerPreviewMaxChange?: (value: number) => void;
};

function noopPeerPreview(_n: number) {}

const MODE_BUTTONS: Array<{ mode: UsageTimeMode; label: string }> = [
  { mode: "today", label: "Today" },
  { mode: "this_month", label: "This month" },
  { mode: "all_time", label: "All time" },
  { mode: "custom", label: "Advanced" },
];

function normalizedMode(mode: string | undefined): UsageTimeMode {
  if (mode === "today" || mode === "this_month" || mode === "all_time" || mode === "rolling" || mode === "custom") return mode;
  return "rolling";
}

function unitLabel(unit: ScopeUnit): string {
  if (unit === "minutes") return "minute";
  if (unit === "hours") return "hour";
  return "day";
}

function pluralize(value: number, unit: ScopeUnit): string {
  const base = unitLabel(unit);
  return `${value} ${base}${value === 1 ? "" : "s"}`;
}

export function normalizeUsageTimeMode(mode: string | undefined): UsageTimeMode {
  return normalizedMode(mode);
}

export function modeSummary({
  mode,
  rollingValue,
  rollingUnit,
  customStart,
  customEnd,
  dateCalendar,
  timezone,
}: Pick<Props, "mode" | "rollingValue" | "rollingUnit" | "customStart" | "customEnd" | "dateCalendar" | "timezone">): string {
  if (mode === "today") return "Today from 00:00 to now · hourly";
  if (mode === "this_month") return "Current selected-calendar month to now · daily";
  if (mode === "all_time") return "Full history · daily";
  if (mode === "custom") {
    const start = customStart ? formatCalendarLocalValue(customStart, { dateCalendar, includeTime: true }) : "Start";
    const end = customEnd ? formatCalendarLocalValue(customEnd, { dateCalendar, includeTime: true }) : "End";
    return `${start} → ${end}`;
  }
  return `Last ${pluralize(Math.max(1, rollingValue || 1), rollingUnit)} · ${rollingUnit === "days" ? "daily" : "hourly"}`;
}

export default function UsageTimeControls({
  mode,
  rollingValue,
  rollingUnit,
  customStart,
  customEnd,
  autoRefreshSeconds,
  dateCalendar,
  timezone,
  weekStartDay,
  showAutoRefresh = true,
  onModeChange,
  onRollingValueChange,
  onRollingUnitChange,
  onCustomStartChange,
  onCustomEndChange,
  peerPreviewMax,
  onPeerPreviewMaxChange,
}: Props) {
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLDivElement | null>(null);
  const activeMode = normalizeUsageTimeMode(mode);
  const activeCustomTab = activeMode === "custom" ? "range" : "rolling";
  const rollingInput = useLooseNumberInput(rollingValue, onRollingValueChange, { min: 1, emptyFallback: 1 });
  const peerPreviewInput = useLooseNumberInput(peerPreviewMax ?? 6, onPeerPreviewMaxChange ?? noopPeerPreview, {
    min: 1,
    max: 50,
    emptyFallback: 6,
  });

  React.useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const setPreset = (nextMode: UsageTimeMode) => {
    if (nextMode === "custom") {
      if (activeMode !== "rolling" && activeMode !== "custom") onModeChange("rolling");
      setOpen((v) => !v);
      return;
    }
    setOpen(false);
    onModeChange(nextMode);
  };

  const ensureCustomRange = () => {
    if (!customStart) onCustomStartChange(startOfLocalTodayValue(timezone));
    if (!customEnd) onCustomEndChange(zonedNowToDatetimeLocalValue(timezone));
  };

  const summary = modeSummary({
    mode: activeMode,
    rollingValue,
    rollingUnit,
    customStart,
    customEnd,
    dateCalendar,
    timezone,
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {MODE_BUTTONS.map((item) => {
            const selected = item.mode === "custom"
              ? activeMode === "rolling" || activeMode === "custom"
              : activeMode === item.mode;
            return (
              <button
                key={item.mode}
                type="button"
                onClick={() => setPreset(item.mode)}
                className={`rounded-full px-3 py-1.5 text-xs border shadow ${
                  selected
                    ? "border-gray-900 bg-gray-900 text-white dark:border-gray-100 dark:bg-gray-100 dark:text-gray-900"
                    : "border-gray-200 bg-white hover:bg-gray-50 dark:border-gray-800 dark:bg-gray-950 dark:hover:bg-gray-900"
                }`}
              >
                {item.label}
              </button>
            );
          })}
        </div>
        <div className="flex w-full shrink-0 flex-col items-start gap-2 sm:w-auto sm:items-end">
          {showAutoRefresh && (
            <div className="text-xs text-gray-600 dark:text-gray-300">
              Auto refresh every {Math.max(5, Number(autoRefreshSeconds) || 30)}s
            </div>
          )}
          {onPeerPreviewMaxChange != null && (
            <div className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-300">
              <span>Max peer cards</span>
              <input
                type="number"
                min={1}
                max={50}
                className="w-16 rounded-full border border-gray-900 bg-gray-900 px-2 py-1 text-xs text-white focus:ring-1 focus:ring-gray-400 dark:border-gray-300 dark:bg-gray-100 dark:text-gray-900"
                {...peerPreviewInput}
              />
            </div>
          )}
        </div>
      </div>

      <div ref={rootRef} className="relative flex flex-wrap items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
        <span>{summary}</span>
        {(activeMode === "rolling" || activeMode === "custom") && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="rounded-full border border-gray-200 bg-white px-3 py-1 text-xs text-gray-700 shadow hover:bg-gray-50 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-200 dark:hover:bg-gray-900"
          >
            Edit
          </button>
        )}

        {open && (
          <div className="absolute left-0 top-full z-40 mt-2 w-[min(560px,calc(100vw-2rem))] rounded-2xl border border-gray-200 bg-white p-4 shadow-xl dark:border-gray-800 dark:bg-gray-950">
            <div className="mb-3 inline-flex rounded-full bg-gray-100 p-1 text-xs dark:bg-gray-900">
              <button
                type="button"
                onClick={() => onModeChange("rolling")}
                className={`rounded-full px-3 py-1 ${activeCustomTab === "rolling" ? "bg-white text-gray-900 shadow dark:bg-gray-100" : "text-gray-600 dark:text-gray-300"}`}
              >
                Rolling
              </button>
              <button
                type="button"
                onClick={() => {
                  ensureCustomRange();
                  onModeChange("custom");
                }}
                className={`rounded-full px-3 py-1 ${activeCustomTab === "range" ? "bg-white text-gray-900 shadow dark:bg-gray-100" : "text-gray-600 dark:text-gray-300"}`}
              >
                Date range
              </button>
            </div>

            {activeCustomTab === "rolling" ? (
              <div className="flex flex-wrap items-center gap-2">
                <span>Last</span>
                <input
                  type="number"
                  min={1}
                  className="w-20 rounded-full border border-gray-900 bg-gray-900 px-3 py-1.5 text-sm text-white focus:ring-1 focus:ring-gray-400 dark:border-gray-300 dark:bg-gray-100 dark:text-gray-900"
                  {...rollingInput}
                />
                <select
                  value={rollingUnit}
                  onChange={(e) => onRollingUnitChange(e.target.value as ScopeUnit)}
                  className="rounded-full border border-gray-200 bg-white px-3 py-1.5 text-sm text-gray-900 focus:ring-1 focus:ring-gray-300 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-100"
                >
                  <option value="minutes">minutes</option>
                  <option value="hours">hours</option>
                  <option value="days">days</option>
                </select>
              </div>
            ) : (
              <div className="grid gap-3">
                <div className="flex flex-wrap items-center gap-2">
                  <CalendarDateTimeInput
                    value={customStart}
                    onChange={onCustomStartChange}
                    dateCalendar={dateCalendar}
                    weekStartDay={weekStartDay}
                    timezone={timezone}
                    className="min-w-[220px] rounded-full border border-gray-200 bg-white px-3 py-1.5 text-sm focus:ring-1 focus:ring-gray-300 dark:border-gray-800 dark:bg-gray-950"
                  />
                  <span>to</span>
                  <CalendarDateTimeInput
                    value={customEnd}
                    onChange={onCustomEndChange}
                    dateCalendar={dateCalendar}
                    weekStartDay={weekStartDay}
                    timezone={timezone}
                    className="min-w-[220px] rounded-full border border-gray-200 bg-white px-3 py-1.5 text-sm focus:ring-1 focus:ring-gray-300 dark:border-gray-800 dark:bg-gray-950"
                  />
                </div>
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      onCustomStartChange(startOfLocalTodayValue(timezone));
                      onCustomEndChange(zonedNowToDatetimeLocalValue(timezone));
                    }}
                    className="rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-700 hover:bg-gray-50 dark:border-gray-800 dark:text-gray-200 dark:hover:bg-gray-900"
                  >
                    Use today
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      onCustomStartChange(startOfSelectedCalendarMonthValue(dateCalendar, timezone));
                      onCustomEndChange(zonedNowToDatetimeLocalValue(timezone));
                    }}
                    className="rounded-full border border-gray-200 px-3 py-1 text-xs text-gray-700 hover:bg-gray-50 dark:border-gray-800 dark:text-gray-200 dark:hover:bg-gray-900"
                  >
                    Use this month
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
