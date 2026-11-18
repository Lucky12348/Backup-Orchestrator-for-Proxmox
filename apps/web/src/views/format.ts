// Dates and sizes of the redesigned pages, as in the mockups ("auj. 14:02",
// "8 nov.", "Dimanche 22 nov.", "3,57 Tio"). Pure (node:test).

import { formatGib } from "../units.ts";

export type Lang = "fr" | "en";

const locale = (lang: Lang) => (lang === "fr" ? "fr-FR" : "en-GB");

/** Settings › General (Phase 5j): date order and 12/24 h clock, set by the shell. */
export interface FormatPreferences {
  dateOrder: "auto" | "dmy" | "mdy" | "ymd";
  timeFormat: "24h" | "12h";
}
let preferences: FormatPreferences = { dateOrder: "auto", timeFormat: "24h" };

export function setFormatPreferences(next: FormatPreferences): void {
  preferences = { ...next };
}

/** Day / month (/ year) in the chosen order; "auto" keeps the language's. */
function shortDate(date: Date, lang: Lang, options: Intl.DateTimeFormatOptions): string {
  const format = new Intl.DateTimeFormat(locale(lang), options);
  if (preferences.dateOrder === "auto") return format.format(date);
  const parts = format.formatToParts(date);
  const pick = (type: string) => parts.find((part) => part.type === type)?.value;
  const day = pick("day");
  const month = pick("month");
  const year = pick("year");
  const weekday = pick("weekday");
  const order = { dmy: [day, month, year], mdy: [month, day, year], ymd: [year, month, day] }[preferences.dateOrder];
  const body = order.filter(Boolean).join(" ");
  return weekday ? `${weekday} ${body}` : body;
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** Server timestamps without a zone ("2026-11-17T03:10:00") are UTC (B38):
 *  read as local time they were shown one or two hours off. */
export function serverDate(iso: string): Date {
  return new Date(/^\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d(\.\d+)?)?$/.test(iso) ? `${iso}Z` : iso);
}

export function formatTime(iso: string, lang: Lang, timeZone?: string): string {
  return new Intl.DateTimeFormat(locale(lang), {
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: preferences.timeFormat === "12h" ? "h12" : "h23",
    timeZone,
  }).format(serverDate(iso));
}

/** B38: "Read today at 04:10" / "Read yesterday at 23:50" / "Read on 8 Nov at 09:00", local time. */
export function formatReadAt(
  iso: string,
  lang: Lang,
  now: Date,
  texts: { readToday: string; readYesterday: string; readOn: string },
): string {
  const date = serverDate(iso);
  const time = formatTime(iso, lang);
  if (sameDay(date, now)) return texts.readToday.replace("{time}", time);
  if (sameDay(date, new Date(now.getTime() - 24 * 3600 * 1000))) return texts.readYesterday.replace("{time}", time);
  const day = shortDate(date, lang, {
    day: "numeric",
    month: "short",
    year: date.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
  return texts.readOn.replace("{date}", day).replace("{time}", time);
}

/** "today 14:02" / "yesterday 09:00" / "8 nov." (+ year when not this year). */
export function formatWhen(
  iso: string | null | undefined,
  lang: Lang,
  now: Date,
  texts: { today: string; yesterday: string; never: string },
  timeZone?: string,
): string {
  if (!iso) return texts.never;
  const date = serverDate(iso);
  const time = formatTime(iso, lang, timeZone);
  if (sameDay(date, now)) return texts.today.replace("{time}", time);
  const yesterday = new Date(now.getTime() - 24 * 3600 * 1000);
  if (sameDay(date, yesterday)) return texts.yesterday.replace("{time}", time);
  return shortDate(date, lang, {
    day: "numeric",
    month: "short",
    year: date.getFullYear() === now.getFullYear() ? undefined : "numeric",
    timeZone,
  });
}

/** "Dimanche 22 nov." / "Sunday 22 Nov". */
export function formatDay(iso: string, lang: Lang, timeZone?: string): string {
  const text = shortDate(new Date(iso), lang, { weekday: "long", day: "numeric", month: "short", timeZone });
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Sizes stored in GiB → "931 Gio" / "3,57 Tio" (src/units.ts, one convention). */
export function formatSize(gb: number | null | undefined, lang: Lang): string {
  return formatGib(gb, lang);
}

export function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) => (key in values ? String(values[key]) : match));
}
