import React from "react";
import {
  GREGORIAN_MONTH_NAMES,
  PERSIAN_MONTH_NAMES,
  addCalendarMonths,
  calendarPartsToLocalValue,
  daysInCalendarMonth,
  jalaliToGregorian,
  localValueToCalendarParts,
  normalizeDateCalendar,
  zonedNowToDatetimeLocalValue,
  type CalendarDateTimeParts,
} from "./datetimeLocal";

type Props = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  className?: string;
  dateCalendar?: string;
  weekStartDay?: number;
  timezone?: string;
};

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const HOURS = Array.from({ length: 24 }, (_, i) => i);
const MINUTES = Array.from({ length: 60 }, (_, i) => i);

function clampWeekStartDay(value?: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(6, Number(value)));
}

function appWeekdayForCalendarDay(year: number, month: number, day: number, dateCalendar: string): number {
  if (normalizeDateCalendar(dateCalendar) === "persian") {
    const [gy, gm, gd] = jalaliToGregorian(year, month, day);
    return (new Date(gy, gm - 1, gd).getDay() + 6) % 7;
  }
  return (new Date(year, month - 1, day).getDay() + 6) % 7;
}

function sameCalendarDay(a: CalendarDateTimeParts, b: CalendarDateTimeParts): boolean {
  return a.year === b.year && a.month === b.month && a.day === b.day;
}

function formatLocalCalendarLabel(parts: CalendarDateTimeParts, dateCalendar: string): string {
  const calendar = normalizeDateCalendar(dateCalendar);
  const monthNames = calendar === "persian" ? PERSIAN_MONTH_NAMES : GREGORIAN_MONTH_NAMES;
  return `${monthNames[parts.month - 1]} ${parts.day}, ${parts.year}, ${String(parts.hour).padStart(2, "0")}:${String(parts.minute).padStart(2, "0")}`;
}

export default function CalendarDateTimeInput({
  value,
  onChange,
  disabled,
  className,
  dateCalendar,
  weekStartDay,
  timezone,
}: Props) {
  const calendar = normalizeDateCalendar(dateCalendar);
  const startDay = clampWeekStartDay(weekStartDay);
  const [open, setOpen] = React.useState(false);
  const rootRef = React.useRef<HTMLDivElement | null>(null);
  const selected = React.useMemo(() => localValueToCalendarParts(value, calendar), [value, calendar]);
  const today = React.useMemo(() => localValueToCalendarParts(zonedNowToDatetimeLocalValue(timezone), calendar), [calendar, timezone]);
  const [view, setView] = React.useState(() => ({ year: selected.year, month: selected.month }));

  React.useEffect(() => {
    if (open) setView({ year: selected.year, month: selected.month });
  }, [open, selected.year, selected.month]);

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

  const monthNames = calendar === "persian" ? PERSIAN_MONTH_NAMES : GREGORIAN_MONTH_NAMES;
  const monthLabel = `${monthNames[view.month - 1]} ${view.year}`;
  const weekdayLabels = React.useMemo(
    () => Array.from({ length: 7 }, (_, i) => WEEKDAYS[(startDay + i) % 7]),
    [startDay],
  );
  const days = React.useMemo(() => {
    const count = daysInCalendarMonth(view.year, view.month, calendar);
    const firstDow = appWeekdayForCalendarDay(view.year, view.month, 1, calendar);
    const offset = (firstDow - startDay + 7) % 7;
    return [
      ...Array.from({ length: offset }, () => null),
      ...Array.from({ length: count }, (_, i) => i + 1),
    ];
  }, [calendar, startDay, view.month, view.year]);

  const commit = React.useCallback(
    (patch: Partial<CalendarDateTimeParts>) => {
      const next = {
        ...selected,
        ...patch,
      };
      onChange(calendarPartsToLocalValue(next, calendar));
    },
    [calendar, onChange, selected],
  );

  const moveMonth = (delta: number) => {
    setView((prev) => addCalendarMonths(prev.year, prev.month, delta));
  };

  const selectToday = () => {
    setView({ year: today.year, month: today.month });
    onChange(calendarPartsToLocalValue(today, calendar));
    setOpen(false);
  };

  const label = value ? formatLocalCalendarLabel(selected, calendar) : "Pick date";

  return (
    <div ref={rootRef} className="relative inline-block">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`${className || ""} inline-flex items-center justify-between gap-2 text-left disabled:opacity-60 disabled:cursor-not-allowed`}
        title={value ? label : undefined}
      >
        <span className={`truncate ${value ? "" : "text-gray-400 dark:text-gray-500"}`}>{label}</span>
        <span className="text-gray-400 dark:text-gray-500" aria-hidden="true">▾</span>
      </button>
      {open && !disabled ? (
        <div className="absolute left-0 top-full z-50 mt-2 w-[320px] rounded-2xl border border-gray-200 bg-white p-3 shadow-xl dark:border-gray-800 dark:bg-gray-950">
          <div className="flex items-center justify-between gap-2">
            <button
              type="button"
              onClick={() => moveMonth(-1)}
              className="h-8 w-8 rounded-full border border-gray-200 text-sm text-gray-700 hover:bg-gray-50 dark:border-gray-800 dark:text-gray-200 dark:hover:bg-gray-900"
              aria-label="Previous month"
            >
              ‹
            </button>
            <div className="min-w-0 text-center">
              <div className="text-sm font-medium text-gray-900 dark:text-gray-100">{monthLabel}</div>
              <div className="text-[11px] text-gray-500 dark:text-gray-400">{calendar === "persian" ? "Persian calendar" : "Gregorian calendar"}</div>
            </div>
            <button
              type="button"
              onClick={() => moveMonth(1)}
              className="h-8 w-8 rounded-full border border-gray-200 text-sm text-gray-700 hover:bg-gray-50 dark:border-gray-800 dark:text-gray-200 dark:hover:bg-gray-900"
              aria-label="Next month"
            >
              ›
            </button>
          </div>

          <div className="mt-3 grid grid-cols-7 gap-1 text-center">
            {weekdayLabels.map((day) => (
              <div key={day} className="py-1 text-[11px] font-medium text-gray-500 dark:text-gray-400">
                {day}
              </div>
            ))}
            {days.map((day, idx) => {
              if (day == null) return <div key={`empty-${idx}`} className="h-9" />;
              const parts = { ...selected, year: view.year, month: view.month, day };
              const isSelected = sameCalendarDay(parts, selected);
              const isToday = sameCalendarDay(parts, today);
              return (
                <button
                  key={day}
                  type="button"
                  onClick={() => {
                    commit({ year: view.year, month: view.month, day });
                  }}
                  className={[
                    "h-9 rounded-full text-sm transition",
                    isSelected
                      ? "bg-gray-900 text-white shadow dark:bg-gray-100 dark:text-gray-900"
                      : isToday
                        ? "bg-indigo-50 text-indigo-700 ring-1 ring-indigo-200 dark:bg-indigo-500/10 dark:text-indigo-300 dark:ring-indigo-500/20"
                        : "text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-900",
                  ].join(" ")}
                >
                  {day}
                </button>
              );
            })}
          </div>

          <div className="mt-3 flex items-center justify-between gap-3 border-t border-gray-100 pt-3 dark:border-gray-800">
            <button
              type="button"
              onClick={selectToday}
              className="rounded-full border border-gray-200 px-3 py-1.5 text-xs text-gray-700 hover:bg-gray-50 dark:border-gray-800 dark:text-gray-200 dark:hover:bg-gray-900"
            >
              Today
            </button>
            <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
              <div className="flex items-center gap-1">
              <select
                value={selected.hour}
                onChange={(e) => commit({ hour: Number(e.target.value) })}
                className="rounded-full border border-gray-200 bg-white px-2 py-1 text-xs text-gray-900 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-100"
                aria-label="Hour"
              >
                {HOURS.map((hour) => (
                  <option key={hour} value={hour}>{String(hour).padStart(2, "0")}</option>
                ))}
              </select>
              <span>:</span>
              <select
                value={selected.minute}
                onChange={(e) => commit({ minute: Number(e.target.value) })}
                className="rounded-full border border-gray-200 bg-white px-2 py-1 text-xs text-gray-900 dark:border-gray-800 dark:bg-gray-950 dark:text-gray-100"
                aria-label="Minute"
              >
                {MINUTES.map((minute) => (
                  <option key={minute} value={minute}>{String(minute).padStart(2, "0")}</option>
                ))}
              </select>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-full bg-gray-900 px-3 py-1.5 text-xs text-white shadow hover:bg-black dark:bg-gray-100 dark:text-gray-900 dark:hover:bg-white"
              >
                Done
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
