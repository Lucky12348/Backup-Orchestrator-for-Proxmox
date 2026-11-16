// Disk sheet and guards (Phase 5f). Pure module (node:test), no React.
// The server decides the alerts (GET /disks/alerts) and the readiness; this
// module only shapes them for the cards, the sheet and the launch dialog.

export type DiskColor = "slate" | "blue" | "green" | "amber" | "red" | "violet";
export type DiskKind = "hdd_portable" | "hdd_desktop" | "ssd" | "usb_stick" | "nvme_enclosure" | "unknown";

export const DISK_COLORS: readonly DiskColor[] = ["slate", "blue", "green", "amber", "red", "violet"];

/** Swatch / accent classes per colour (Tailwind palette, readable in both themes). */
export const COLOR_CLASS: Record<DiskColor, { ring: string; swatch: string; stripe: string; soft: string }> = {
  slate: { ring: "ring-slate-500", swatch: "bg-slate-500", stripe: "border-l-slate-500", soft: "bg-slate-500/15 text-slate-400" },
  blue: { ring: "ring-sky-500", swatch: "bg-sky-500", stripe: "border-l-sky-500", soft: "bg-sky-500/15 text-sky-400" },
  green: { ring: "ring-emerald-500", swatch: "bg-emerald-500", stripe: "border-l-emerald-500", soft: "bg-emerald-500/15 text-emerald-400" },
  amber: { ring: "ring-amber-500", swatch: "bg-amber-500", stripe: "border-l-amber-500", soft: "bg-amber-500/15 text-amber-400" },
  red: { ring: "ring-rose-500", swatch: "bg-rose-500", stripe: "border-l-rose-500", soft: "bg-rose-500/15 text-rose-400" },
  violet: { ring: "ring-violet-500", swatch: "bg-violet-500", stripe: "border-l-violet-500", soft: "bg-violet-500/15 text-violet-400" },
};

export function diskColor(value: string | null | undefined): DiskColor | null {
  return DISK_COLORS.includes(value as DiskColor) ? (value as DiskColor) : null;
}

export function diskKind(value: string | null | undefined): DiskKind {
  const kinds: DiskKind[] = ["hdd_portable", "hdd_desktop", "ssd", "usb_stick", "nvme_enclosure"];
  return kinds.includes(value as DiskKind) ? (value as DiskKind) : "unknown";
}

// --- alerts -----------------------------------------------------------------------------
export interface AlertLike {
  disk_id: number;
  disk_name?: string;
  code: string;
  level: "warning" | "danger";
  params: Record<string, unknown>;
}

const ALERT_ORDER = ["smart_danger", "usage_critical", "verify_recommended", "smart_warning", "usage_warning", "rotation_stale", "usb2_slow"];

export function alertsFor<A extends AlertLike>(alerts: readonly A[] | null | undefined, diskId: number): A[] {
  return (alerts ?? [])
    .filter((alert) => alert.disk_id === diskId)
    .sort((a, b) => ALERT_ORDER.indexOf(a.code) - ALERT_ORDER.indexOf(b.code));
}

/** Dashboard "To do": danger first, then warnings (USB 2.0 is only shown on cards). */
export function todoAlerts<A extends AlertLike>(alerts: readonly A[] | null | undefined): A[] {
  return (alerts ?? [])
    .filter((alert) => alert.code !== "usb2_slow")
    .sort((a, b) => (a.level === b.level ? ALERT_ORDER.indexOf(a.code) - ALERT_ORDER.indexOf(b.code) : a.level === "danger" ? -1 : 1));
}

/** "{name}", "{percent}", "{days}"… values of an alert's text. */
export function alertValues(alert: AlertLike, formatDate: (iso: string) => string): Record<string, string | number> {
  const params = alert.params ?? {};
  const values: Record<string, string | number> = { name: alert.disk_name ?? "" };
  for (const [key, value] of Object.entries(params)) {
    if (typeof value === "number") values[key] = Number.isInteger(value) ? value : Math.round(value);
    else if (typeof value === "string") values[key] = /^\d{4}-\d\d-\d\dT/.test(value) ? formatDate(value) : value;
  }
  return values;
}

// --- usage -------------------------------------------------------------------------------
export interface UsageSampleLike {
  at: string;
  used_gb: number;
  total_gb: number;
}

export interface UsagePoint {
  at: string;
  time: number;
  percent: number;
  usedGb: number;
  totalGb: number;
}

export function usageSeries(samples: readonly UsageSampleLike[] | null | undefined): UsagePoint[] {
  return (samples ?? [])
    .filter((sample) => sample.total_gb > 0)
    .map((sample) => ({
      at: sample.at,
      time: Date.parse(sample.at),
      percent: Math.max(0, Math.min(100, (100 * sample.used_gb) / sample.total_gb)),
      usedGb: sample.used_gb,
      totalGb: sample.total_gb,
    }))
    .filter((point) => !Number.isNaN(point.time))
    .sort((a, b) => a.time - b.time);
}

/** SVG geometry of the usage chart: x by time, y by percent (0–100, one axis). */
export function chartGeometry(points: readonly UsagePoint[], width: number, height: number) {
  if (points.length === 0) return { line: "", area: "", xs: [] as number[], ys: [] as number[] };
  const first = points[0].time;
  const span = Math.max(1, points[points.length - 1].time - first);
  const xs = points.map((point) => (points.length === 1 ? width : (width * (point.time - first)) / span));
  const ys = points.map((point) => height - (height * point.percent) / 100);
  const line = xs.map((x, index) => `${index ? "L" : "M"}${x.toFixed(1)},${ys[index].toFixed(1)}`).join(" ");
  const area = `${line} L${xs[xs.length - 1].toFixed(1)},${height} L${xs[0].toFixed(1)},${height} Z`;
  return { line, area, xs, ys };
}

export function nearestIndex(xs: readonly number[], x: number): number {
  let best = 0;
  xs.forEach((value, index) => {
    if (Math.abs(value - x) < Math.abs(xs[best] - x)) best = index;
  });
  return best;
}

export type UsageLevel = "ok" | "warning" | "danger";

export function usageLevel(percent: number | null): UsageLevel {
  if (percent === null) return "ok";
  return percent >= 90 ? "danger" : percent >= 80 ? "warning" : "ok";
}

// --- retention ---------------------------------------------------------------------------
export interface RetentionLike {
  keep_last: number | null;
  keep_weekly: number | null;
  keep_monthly: number | null;
}

export interface RetentionForm {
  keepLast: string;
  keepWeekly: string;
  keepMonthly: string;
}

export function retentionForm(retention: RetentionLike | null | undefined): RetentionForm {
  const value = retention ?? { keep_last: 4, keep_weekly: null, keep_monthly: null };
  return {
    keepLast: value.keep_last ? String(value.keep_last) : "",
    keepWeekly: value.keep_weekly ? String(value.keep_weekly) : "",
    keepMonthly: value.keep_monthly ? String(value.keep_monthly) : "",
  };
}

/** null when invalid: integers 1–1000, at least one (a prune without keep deletes everything). */
export function retentionPayload(form: RetentionForm): RetentionLike | null {
  const parse = (text: string): number | null | undefined => {
    const trimmed = text.trim();
    if (!trimmed) return null;
    if (!/^\d+$/.test(trimmed)) return undefined;
    const value = Number(trimmed);
    return value >= 1 && value <= 1000 ? value : undefined;
  };
  const values = [parse(form.keepLast), parse(form.keepWeekly), parse(form.keepMonthly)];
  if (values.some((value) => value === undefined) || values.every((value) => value === null)) return null;
  return { keep_last: values[0] ?? null, keep_weekly: values[1] ?? null, keep_monthly: values[2] ?? null };
}

// --- launch dialog ------------------------------------------------------------------------
export interface ReadinessLike {
  ready: boolean;
  reasons: { code: string; [key: string]: unknown }[];
  warnings?: { code: string; [key: string]: unknown }[];
  estimate?: { needed_bytes: number; free_bytes: number | null; known: boolean; sufficient: boolean | null; new_snapshots: number } | null;
}

export interface LaunchState {
  /** Blocking reasons once an explicit SMART override is given. */
  blocking: { code: string; [key: string]: unknown }[];
  smartFailed: boolean;
  canStart: boolean;
}

export function launchState(readiness: ReadinessLike | null | undefined, smartOverride: boolean): LaunchState {
  const reasons = readiness?.reasons ?? [];
  const smartFailed = reasons.some((reason) => reason.code === "smart_failed");
  const blocking = reasons.filter((reason) => !(reason.code === "smart_failed" && smartOverride));
  return { blocking, smartFailed, canStart: Boolean(readiness) && blocking.length === 0 };
}
