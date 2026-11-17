// One unit convention everywhere (B27): BINARY sizes, as PBO stores them
// (the host agent, PBS and Proxmox all report GiB): Kio/Mio/Gio/Tio in French,
// KiB/MiB/GiB/TiB in English. Used for totals AND used/free sizes. Pure (node:test).

export type Lang = "fr" | "en";

const UNITS: Record<Lang, string[]> = {
  fr: ["o", "Kio", "Mio", "Gio", "Tio", "Pio"],
  en: ["B", "KiB", "MiB", "GiB", "TiB", "PiB"],
};

function number(value: number, digits: number, lang: Lang): string {
  const text = value.toFixed(digits).replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
  return lang === "fr" ? text.replace(".", ",") : text;
}

/** Bytes → "1,5 Gio" / "1.5 GiB". */
export function formatBytes(bytes: number | null | undefined, lang: Lang): string {
  if (bytes === null || bytes === undefined) return "—";
  let value = Math.max(0, bytes);
  let unit = 0;
  while (value >= 1024 && unit < UNITS[lang].length - 1) {
    value /= 1024;
    unit += 1;
  }
  const digits = unit === 0 || value >= 100 ? 0 : value >= 10 ? 1 : unit >= 4 ? 2 : 1;
  return `${number(value, digits, lang)} ${UNITS[lang][unit]}`;
}

/** A size stored in GiB (capacity_gb, filesystem_*_gb, size_gb) → "931 Gio" / "3,57 Tio". */
export function formatGib(gib: number | null | undefined, lang: Lang): string {
  if (gib === null || gib === undefined) return "—";
  if (gib <= 0) return `0 ${UNITS[lang][0]}`;
  // B38: small values stay readable ("180 Mio", "2,35 Gio"), never "0 Gio".
  if (gib < 1) return formatBytes(gib * 1024 ** 3, lang);
  if (gib < 10) return `${number(gib, 2, lang)} ${UNITS[lang][3]}`;
  if (gib < 100) return `${number(gib, 1, lang)} ${UNITS[lang][3]}`;
  if (gib < 1024) return `${Math.round(gib)} ${UNITS[lang][3]}`;
  return formatBytes(gib * 1024 ** 3, lang);
}

/** Used and total in ONE unit (Phase 5f): "820 / 931 Gio", "0,85 / 3,64 Tio". */
export function formatUsage(usedGib: number | null | undefined, totalGib: number | null | undefined, lang: Lang): string {
  if (usedGib === null || usedGib === undefined || !totalGib) return "—";
  const used = Math.max(0, usedGib);
  const tera = totalGib >= 1024;
  const scale = tera ? 1024 : 1;
  const unit = UNITS[lang][tera ? 4 : 3];
  const total = number(totalGib / scale, tera ? 2 : 0, lang);
  // B38 / 5h.1: a small used size in the total's unit would read "0 / 491 Gio" or "0,18 / 491 Gio":
  // under 1 Gio (or 0.5 % of a Tio) it keeps its own unit ("184 Mio / 491 Gio").
  if (used > 0 && (used < 1 || used / scale < 0.005)) return `${formatBytes(used * 1024 ** 3, lang)} / ${total} ${unit}`;
  const usedDigits = tera || used < 10 ? 2 : 0;
  return `${number(used / scale, usedDigits, lang)} / ${total} ${unit}`;
}

/** "42 %", "< 1 %" for a small non-zero share (never "0 %" for something there). */
export function formatPercent(value: number | null | undefined, lang: Lang): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const space = lang === "fr" ? " " : " ";
  if (value > 0 && value < 1) return `< 1${space}%`;
  return `${Math.round(Math.min(100, Math.max(0, value)))}${space}%`;
}

/** Phase 5h.1, ONE formatter for every "used / total · %" (cards, dashboard,
 *  disk sheet, tables): "180 Mio / 491 Gio · < 1 %". */
export function usageParts(usedGib: number | null | undefined, totalGib: number | null | undefined, lang: Lang): { usage: string; pct: string } | null {
  if (usedGib === null || usedGib === undefined || !totalGib) return null;
  return { usage: formatUsage(usedGib, totalGib, lang), pct: formatPercent((100 * Math.max(0, usedGib)) / totalGib, lang) };
}
