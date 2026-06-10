/** Format for HTML `datetime-local` inputs (local wall time, minute precision). */
export function formatDatetimeLocalValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export type DateCalendar = "gregorian" | "persian";

export const PERSIAN_MONTH_NAMES = [
  "Farvardin",
  "Ordibehesht",
  "Khordad",
  "Tir",
  "Mordad",
  "Shahrivar",
  "Mehr",
  "Aban",
  "Azar",
  "Dey",
  "Bahman",
  "Esfand",
];

export const GREGORIAN_MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

export function normalizeDateCalendar(value?: string | null): DateCalendar {
  return value === "persian" ? "persian" : "gregorian";
}

export function gregorianToJalali(gy: number, gm: number, gd: number): [number, number, number] {
  const gDaysInMonth = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const jDaysInMonth = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29];
  gy -= 1600;
  gm -= 1;
  gd -= 1;
  let gDayNo = 365 * gy + Math.floor((gy + 3) / 4) - Math.floor((gy + 99) / 100) + Math.floor((gy + 399) / 400);
  for (let i = 0; i < gm; i += 1) gDayNo += gDaysInMonth[i];
  if (gm > 1 && ((gy + 1600) % 4 === 0 && ((gy + 1600) % 100 !== 0 || (gy + 1600) % 400 === 0))) gDayNo += 1;
  gDayNo += gd;

  let jDayNo = gDayNo - 79;
  const jNp = Math.floor(jDayNo / 12053);
  jDayNo %= 12053;
  let jy = 979 + 33 * jNp + 4 * Math.floor(jDayNo / 1461);
  jDayNo %= 1461;
  if (jDayNo >= 366) {
    jy += Math.floor((jDayNo - 1) / 365);
    jDayNo = (jDayNo - 1) % 365;
  }
  let jm = 0;
  while (jm < 11 && jDayNo >= jDaysInMonth[jm]) {
    jDayNo -= jDaysInMonth[jm];
    jm += 1;
  }
  return [jy, jm + 1, jDayNo + 1];
}

export function jalaliToGregorian(jy: number, jm: number, jd: number): [number, number, number] {
  const gDaysInMonth = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  const jDaysInMonth = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29];
  jy -= 979;
  jm -= 1;
  jd -= 1;
  let jDayNo = 365 * jy + Math.floor(jy / 33) * 8 + Math.floor(((jy % 33) + 3) / 4);
  for (let i = 0; i < jm; i += 1) jDayNo += jDaysInMonth[i];
  jDayNo += jd;

  let gDayNo = jDayNo + 79;
  let gy = 1600 + 400 * Math.floor(gDayNo / 146097);
  gDayNo %= 146097;

  let leap = true;
  if (gDayNo >= 36525) {
    gDayNo -= 1;
    gy += 100 * Math.floor(gDayNo / 36524);
    gDayNo %= 36524;
    if (gDayNo >= 365) gDayNo += 1;
    else leap = false;
  }

  gy += 4 * Math.floor(gDayNo / 1461);
  gDayNo %= 1461;
  if (gDayNo >= 366) {
    leap = false;
    gDayNo -= 1;
    gy += Math.floor(gDayNo / 365);
    gDayNo %= 365;
  }

  let gm = 0;
  while (gm < 11) {
    const dim = gDaysInMonth[gm] + (gm === 1 && leap ? 1 : 0);
    if (gDayNo < dim) break;
    gDayNo -= dim;
    gm += 1;
  }
  return [gy, gm + 1, gDayNo + 1];
}

export function isJalaliLeapYear(jy: number): boolean {
  const [gy, gm, gd] = jalaliToGregorian(jy, 1, 1);
  const [ny, nm, nd] = jalaliToGregorian(jy + 1, 1, 1);
  const start = new Date(gy, gm - 1, gd);
  const next = new Date(ny, nm - 1, nd);
  return Math.round((next.getTime() - start.getTime()) / 86400000) === 366;
}

export function daysInCalendarMonth(year: number, month: number, dateCalendar?: string | null): number {
  const calendar = normalizeDateCalendar(dateCalendar);
  if (calendar === "persian") {
    if (month <= 6) return 31;
    if (month <= 11) return 30;
    return isJalaliLeapYear(year) ? 30 : 29;
  }
  return new Date(year, month, 0).getDate();
}

export function addCalendarMonths(year: number, month: number, delta: number): { year: number; month: number } {
  let nextMonth = month + delta;
  let nextYear = year + Math.floor((nextMonth - 1) / 12);
  nextMonth = ((nextMonth - 1) % 12 + 12) % 12 + 1;
  return { year: nextYear, month: nextMonth };
}

export type CalendarDateTimeParts = {
  year: number;
  month: number;
  day: number;
  hour: number;
  minute: number;
};

function pad2(n: number): string {
  return String(n).padStart(2, "0");
}

function localValueFromParts(parts: CalendarDateTimeParts): string {
  return `${parts.year}-${pad2(parts.month)}-${pad2(parts.day)}T${pad2(parts.hour)}:${pad2(parts.minute)}`;
}

function parseDatetimeLocalValue(value: string): CalendarDateTimeParts | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value || "");
  if (!match) return null;
  const [, year, month, day, hour, minute] = match;
  const parts = {
    year: Number(year),
    month: Number(month),
    day: Number(day),
    hour: Number(hour),
    minute: Number(minute),
  };
  if (
    !Number.isFinite(parts.year) ||
    !Number.isFinite(parts.month) ||
    !Number.isFinite(parts.day) ||
    !Number.isFinite(parts.hour) ||
    !Number.isFinite(parts.minute)
  ) {
    return null;
  }
  return parts;
}

export function localValueToCalendarParts(value: string, dateCalendar?: string | null): CalendarDateTimeParts {
  const d = value ? new Date(value) : new Date();
  const safe = Number.isFinite(d.getTime()) ? d : new Date();
  const hour = safe.getHours();
  const minute = safe.getMinutes();
  if (normalizeDateCalendar(dateCalendar) === "persian") {
    const [year, month, day] = gregorianToJalali(safe.getFullYear(), safe.getMonth() + 1, safe.getDate());
    return { year, month, day, hour, minute };
  }
  return { year: safe.getFullYear(), month: safe.getMonth() + 1, day: safe.getDate(), hour, minute };
}

export function calendarPartsToLocalValue(parts: CalendarDateTimeParts, dateCalendar?: string | null): string {
  const calendar = normalizeDateCalendar(dateCalendar);
  const month = Math.max(1, Math.min(12, parts.month));
  const day = Math.max(1, Math.min(daysInCalendarMonth(parts.year, month, calendar), parts.day));
  const hour = Math.max(0, Math.min(23, parts.hour));
  const minute = Math.max(0, Math.min(59, parts.minute));
  if (calendar === "persian") {
    const [gy, gm, gd] = jalaliToGregorian(parts.year, month, day);
    return formatDatetimeLocalValue(new Date(gy, gm - 1, gd, hour, minute, 0, 0));
  }
  return formatDatetimeLocalValue(new Date(parts.year, month - 1, day, hour, minute, 0, 0));
}

function partsInTimeZone(date: Date, timeZone: string) {
  const dtf = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  const out: Record<string, string> = {};
  for (const p of dtf.formatToParts(date)) {
    if (p.type !== "literal") out[p.type] = p.value;
  }
  return {
    year: Number(out.year),
    month: Number(out.month),
    day: Number(out.day),
    hour: Number(out.hour),
    minute: Number(out.minute),
  };
}

export function zonedNowToDatetimeLocalValue(timeZone?: string | null): string {
  if (!timeZone) return formatDatetimeLocalValue(new Date());
  try {
    return localValueFromParts(partsInTimeZone(new Date(), timeZone));
  } catch {
    return formatDatetimeLocalValue(new Date());
  }
}

export function startOfLocalTodayValue(timeZone?: string | null): string {
  const current = parseDatetimeLocalValue(zonedNowToDatetimeLocalValue(timeZone));
  if (!current) {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return formatDatetimeLocalValue(d);
  }
  return localValueFromParts({ ...current, hour: 0, minute: 0 });
}

export function startOfSelectedCalendarMonthValue(dateCalendar?: string | null, timeZone?: string | null): string {
  const current = localValueToCalendarParts(zonedNowToDatetimeLocalValue(timeZone), dateCalendar);
  return calendarPartsToLocalValue(
    {
      year: current.year,
      month: current.month,
      day: 1,
      hour: 0,
      minute: 0,
    },
    dateCalendar,
  );
}

export function zonedWallTimeValueToUtcIso(value: string, timeZone?: string | null): string | undefined {
  const target = parseDatetimeLocalValue(value);
  if (!target) return undefined;
  if (!timeZone) {
    const localDate = new Date(value);
    return Number.isFinite(localDate.getTime()) ? localDate.toISOString() : undefined;
  }
  try {
    const targetMs = Date.UTC(target.year, target.month - 1, target.day, target.hour, target.minute, 0, 0);
    let utcMs = targetMs;
    for (let i = 0; i < 3; i += 1) {
      const actual = partsInTimeZone(new Date(utcMs), timeZone);
      const actualMs = Date.UTC(actual.year, actual.month - 1, actual.day, actual.hour, actual.minute, 0, 0);
      const diff = targetMs - actualMs;
      if (diff === 0) return new Date(utcMs).toISOString();
      utcMs += diff;
    }
    return new Date(utcMs).toISOString();
  } catch {
    const localDate = new Date(value);
    return Number.isFinite(localDate.getTime()) ? localDate.toISOString() : undefined;
  }
}

export function formatCalendarLocalValue(value: string, opts: { dateCalendar?: string; includeTime?: boolean } = {}): string {
  if (!value) return "";
  const parts = localValueToCalendarParts(value, opts.dateCalendar);
  const calendar = normalizeDateCalendar(opts.dateCalendar);
  const monthNames = calendar === "persian" ? PERSIAN_MONTH_NAMES : GREGORIAN_MONTH_NAMES;
  const base = `${monthNames[parts.month - 1]} ${parts.day}, ${parts.year}`;
  return opts.includeTime === false ? base : `${base}, ${pad2(parts.hour)}:${pad2(parts.minute)}`;
}

export function formatCalendarDateTime(
  value: string | Date,
  opts: { timeZone?: string; dateCalendar?: string; includeTime?: boolean; shortMonth?: boolean } = {},
): string {
  const date = value instanceof Date ? value : new Date(value);
  if (!Number.isFinite(date.getTime())) return String(value || "");
  const timeZone = opts.timeZone || "UTC";
  const dateCalendar = normalizeDateCalendar(opts.dateCalendar);
  const includeTime = opts.includeTime ?? true;
  if (dateCalendar === "persian") {
    const parts = partsInTimeZone(date, timeZone);
    const [jy, jm, jd] = gregorianToJalali(parts.year, parts.month, parts.day);
    const month = opts.shortMonth ? PERSIAN_MONTH_NAMES[jm - 1].slice(0, 3) : PERSIAN_MONTH_NAMES[jm - 1];
    const base = `${month} ${jd}, ${jy}`;
    return includeTime ? `${base}, ${String(parts.hour).padStart(2, "0")}:${String(parts.minute).padStart(2, "0")}` : base;
  }
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    dateStyle: opts.shortMonth ? "medium" : "medium",
    ...(includeTime ? { timeStyle: "medium" as const } : {}),
  }).format(date);
}

export function formatCalendarDayLabel(value: string, opts: { timeZone?: string; dateCalendar?: string; long?: boolean } = {}): string {
  const date = new Date(`${value}T00:00:00Z`);
  if (!Number.isFinite(date.getTime())) return value;
  const timeZone = opts.timeZone || "UTC";
  const dateCalendar = normalizeDateCalendar(opts.dateCalendar);
  if (dateCalendar === "persian") {
    const parts = partsInTimeZone(date, timeZone);
    const [jy, jm, jd] = gregorianToJalali(parts.year, parts.month, parts.day);
    return opts.long ? `${PERSIAN_MONTH_NAMES[jm - 1]} ${jd}, ${jy}` : `${jm}/${jd}`;
  }
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    ...(opts.long ? { dateStyle: "full" as const } : { month: "numeric" as const, day: "numeric" as const }),
  }).format(date);
}
