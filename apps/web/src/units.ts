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
  if (gib < 1024) return `${Math.round(gib)} ${UNITS[lang][3]}`;
  return formatBytes(gib * 1024 ** 3, lang);
}

/** Used and total in ONE unit (Phase 5f): "820 / 931 Gio", "0,85 / 3,64 Tio". */
export function formatUsage(usedGib: number | null | undefined, totalGib: number | null | undefined, lang: Lang): string {
  if (usedGib === null || usedGib === undefined || !totalGib) return "—";
  const tera = totalGib >= 1024;
  const scale = tera ? 1024 : 1;
  const digits = tera ? 2 : 0;
  return `${number(Math.max(0, usedGib) / scale, digits, lang)} / ${number(totalGib / scale, digits, lang)} ${UNITS[lang][tera ? 4 : 3]}`;
}
