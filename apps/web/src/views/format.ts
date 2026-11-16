// Dates and sizes of the redesigned pages, as in the mockups ("auj. 14:02",
// "8 nov.", "Dimanche 22 nov.", "2,9 To"). Pure (node:test).

export type Lang = "fr" | "en";

const locale = (lang: Lang) => (lang === "fr" ? "fr-FR" : "en-GB");

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function formatTime(iso: string, lang: Lang, timeZone?: string): string {
  return new Intl.DateTimeFormat(locale(lang), { hour: "2-digit", minute: "2-digit", timeZone }).format(new Date(iso));
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
  const date = new Date(iso);
  const time = formatTime(iso, lang, timeZone);
  if (sameDay(date, now)) return texts.today.replace("{time}", time);
  const yesterday = new Date(now.getTime() - 24 * 3600 * 1000);
  if (sameDay(date, yesterday)) return texts.yesterday.replace("{time}", time);
  return new Intl.DateTimeFormat(locale(lang), {
    day: "numeric",
    month: "short",
    year: date.getFullYear() === now.getFullYear() ? undefined : "numeric",
    timeZone,
  }).format(date);
}

/** "Dimanche 22 nov." / "Sunday 22 Nov". */
export function formatDay(iso: string, lang: Lang, timeZone?: string): string {
  const text = new Intl.DateTimeFormat(locale(lang), { weekday: "long", day: "numeric", month: "short", timeZone }).format(new Date(iso));
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Decimal GB (what the agent reports) → "931 Go" / "3,66 To". */
export function formatSize(gb: number | null | undefined, lang: Lang): string {
  const value = gb ?? 0;
  if (value >= 1000) {
    const tb = value / 1000;
    const digits = tb >= 10 ? 1 : 2;
    const text = tb.toFixed(digits).replace(/\.?0+$/, "");
    return `${lang === "fr" ? text.replace(".", ",") : text} ${lang === "fr" ? "To" : "TB"}`;
  }
  return `${Math.round(value)} ${lang === "fr" ? "Go" : "GB"}`;
}

export function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) => (key in values ? String(values[key]) : match));
}
