// Planning form (Phase 6c): three questions — When? On which disk(s)? What?
// Sensible defaults, first occurrence computed, automatic name. Pure (node:test).

import { addDays, addMonths, daysInMonth, today, weekdayName, zoned, zonedToUtc, type Day } from "./calendarModel.ts";

export type Lang = "fr" | "en";
export type Preset = "weekly" | "biweekly" | "monthly" | "custom";
export type Unit = "daily" | "weekly" | "monthly";
export const PRESETS: readonly Preset[] = ["weekly", "biweekly", "monthly", "custom"];
export const WINDOW_CHOICES = [60, 120, 240, 480, 720, 1440] as const;

export interface PlanForm {
  title: string;
  /** The operator typed a name: it is not regenerated any more. */
  titleTouched: boolean;
  preset: Preset;
  unit: Unit;
  interval: number;
  /** 0 = Monday … 6 = Sunday */
  weekdays: number[];
  monthDay: number;
  hour: number;
  minute: number;
  diskSerials: string[];
  allGuests: boolean;
  vmGroups: string[];
  windowMinutes: number;
  confirmation: boolean;
  autoEject: boolean;
  /** Phase 5f: PBS verify of the copied snapshots after each backup. */
  verify: boolean;
  datastore: string;
  enabled: boolean;
  /** Edit: the first occurrence is kept while the schedule is unchanged (rotation order). */
  original?: { start: string; key: string };
}

export function newPlanForm(datastore: string, start?: { day: Day; hour: number }): PlanForm {
  const weekday = start ? (new Date(Date.UTC(start.day.y, start.day.m - 1, start.day.d)).getUTCDay() + 6) % 7 : 6;
  return {
    title: "",
    titleTouched: false,
    preset: "weekly",
    unit: "weekly",
    interval: 1,
    weekdays: [weekday],
    monthDay: start?.day.d ?? 1,
    hour: start?.hour ?? 9,
    minute: 0,
    diskSerials: [],
    allGuests: true,
    vmGroups: [],
    windowMinutes: 240,
    confirmation: false,
    autoEject: true,
    verify: false,
    datastore,
    enabled: true,
  };
}

export function effective(form: PlanForm): { unit: Unit; interval: number } {
  if (form.preset === "weekly") return { unit: "weekly", interval: 1 };
  if (form.preset === "biweekly") return { unit: "weekly", interval: 2 };
  if (form.preset === "monthly") return { unit: "monthly", interval: 1 };
  return { unit: form.unit, interval: form.interval };
}

export function scheduleKey(form: PlanForm): string {
  const { unit, interval } = effective(form);
  return JSON.stringify([unit, interval, unit === "weekly" ? [...form.weekdays].sort() : [], unit === "monthly" ? form.monthDay : 0, form.hour, form.minute]);
}

const weekdayOf = (day: Day) => (new Date(Date.UTC(day.y, day.m - 1, day.d)).getUTCDay() + 6) % 7;

/** The next date matching the schedule (later than now), in the planning zone. */
export function firstOccurrence(form: PlanForm, now: Date, tz: string): Date {
  const { unit } = effective(form);
  const start = today(now, tz);
  const at = (day: Day) => zonedToUtc(day, form.hour, form.minute, tz);
  if (unit === "monthly") {
    for (let offset = 0; offset < 3; offset += 1) {
      const month = addMonths({ ...start, d: 1 }, offset);
      const day = { ...month, d: Math.min(form.monthDay, daysInMonth(month.y, month.m)) };
      if (at(day) > now) return at(day);
    }
  }
  for (let offset = 0; offset < 15; offset += 1) {
    const day = addDays(start, offset);
    if ((unit === "daily" || form.weekdays.includes(weekdayOf(day))) && at(day) > now) return at(day);
  }
  return at(addDays(start, 1));
}

export interface EventLike {
  title: string;
  enabled: boolean;
  disk_serial: string;
  rotation_disk_serials?: string[] | null;
  vm_groups?: string[] | null;
  datastore: string;
  recurrence_type: "once" | "daily" | "weekly" | "monthly";
  recurrence_config: Record<string, unknown> | null;
  window_starts_at: string;
  window_duration_minutes: number;
  start_mode: "auto_on_disk_detected" | "manual_confirmation";
  auto_eject_after_success: boolean;
  verify_after_backup?: boolean | null;
}

export function planFormFromEvent(event: EventLike, tz: string): PlanForm {
  const local = zoned(new Date(event.window_starts_at), tz);
  const config = event.recurrence_config ?? {};
  const interval = Number(config.interval ?? 1) || 1;
  const days = Array.isArray(config.weekdays) ? (config.weekdays as number[]) : [local.weekday];
  const unit: Unit = event.recurrence_type === "daily" ? "daily" : event.recurrence_type === "monthly" ? "monthly" : "weekly";
  const preset: Preset =
    unit === "weekly" && interval === 1 ? "weekly" : unit === "weekly" && interval === 2 ? "biweekly" : unit === "monthly" && interval === 1 ? "monthly" : "custom";
  const form: PlanForm = {
    title: event.title,
    titleTouched: true,
    preset,
    unit,
    interval,
    weekdays: days,
    monthDay: local.d,
    hour: local.h,
    minute: local.min,
    diskSerials: event.rotation_disk_serials?.length ? [...event.rotation_disk_serials] : [event.disk_serial],
    allGuests: !(event.vm_groups ?? []).length,
    vmGroups: [...(event.vm_groups ?? [])],
    windowMinutes: event.window_duration_minutes,
    confirmation: event.start_mode === "manual_confirmation",
    autoEject: event.auto_eject_after_success,
    verify: Boolean(event.verify_after_backup),
    datastore: event.datastore,
    enabled: event.enabled,
  };
  return { ...form, original: { start: event.window_starts_at, key: scheduleKey(form) } };
}

export interface FormTexts {
  autoTitle: { weekly: string; biweekly: string; monthly: string; daily: string; every: string };
  sentence: { daily: string; dailyN: string; weekly: string; weeklyN: string; monthly: string; monthlyN: string; onDisk: string; rotation: string; then: string };
  and: string;
  allGuests: string;
  someGuests: string;
}

const pad = (value: number) => String(value).padStart(2, "0");

export function daysText(weekdays: readonly number[], lang: Lang, and: string): string {
  const names = [...weekdays].sort().map((day) => weekdayName(day, lang, "long"));
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")}${and}${names[names.length - 1]}`;
}

/** « Rotation hebdo · dimanche 09:00 » */
export function autoTitle(form: PlanForm, texts: FormTexts, lang: Lang): string {
  const { unit, interval } = effective(form);
  const time = `${pad(form.hour)}:${pad(form.minute)}`;
  const kind =
    form.preset === "custom" ? texts.autoTitle.every.replace("{n}", String(interval)) : texts.autoTitle[form.preset];
  if (unit === "monthly") return `${kind} · ${form.monthDay} · ${time}`;
  if (unit === "daily") return `${kind} · ${time}`;
  return `${kind} · ${daysText(form.weekdays, lang, texts.and)} ${time}`;
}

/** « Chaque dimanche à 09:00, sur WD 4 To puis Toshiba 3 To en alternance · toutes vos sauvegardes (main) » */
export function planSentence(form: PlanForm, diskNames: readonly string[], texts: FormTexts, lang: Lang): string {
  const { unit, interval } = effective(form);
  const s = texts.sentence;
  const time = `${pad(form.hour)}:${pad(form.minute)}`;
  const days = daysText(form.weekdays, lang, texts.and);
  const base = (
    unit === "daily" ? (interval > 1 ? s.dailyN : s.daily) : unit === "monthly" ? (interval > 1 ? s.monthlyN : s.monthly) : interval > 1 ? s.weeklyN : s.weekly
  )
    .replace("{time}", time)
    .replace("{weekday}", days)
    .replace("{n}", String(interval))
    .replace("{day}", String(form.monthDay));
  const disks = !diskNames.length ? "" : diskNames.length === 1 ? s.onDisk.replace("{disk}", diskNames[0]) : s.rotation.replace("{disks}", diskNames.join(s.then));
  const what = form.allGuests ? texts.allGuests.replace("{datastore}", form.datastore) : texts.someGuests.replace("{n}", String(form.vmGroups.length));
  return `${base}${disks} · ${what}`;
}

/** "Wait for the disk until 13:00" (+1 day when it goes past midnight). */
export function windowEnd(form: PlanForm): { time: string; nextDay: boolean } {
  const total = form.hour * 60 + form.minute + form.windowMinutes;
  return { time: `${pad(Math.floor(total / 60) % 24)}:${pad(total % 60)}`, nextDay: total >= 24 * 60 };
}

export type PlanProblem = "title" | "weekdays" | "disks" | "guests" | "datastore" | "interval";

export function planProblems(form: PlanForm): PlanProblem[] {
  const { unit, interval } = effective(form);
  const problems: PlanProblem[] = [];
  if (unit === "weekly" && form.weekdays.length === 0) problems.push("weekdays");
  if (!(interval >= 1 && interval <= 52)) problems.push("interval");
  if (!form.diskSerials.length) problems.push("disks");
  if (!form.allGuests && !form.vmGroups.length) problems.push("guests");
  if (!form.datastore.trim()) problems.push("datastore");
  if (form.titleTouched && !form.title.trim()) problems.push("title");
  return problems;
}

export function planPayload(form: PlanForm, texts: FormTexts, lang: Lang, now: Date, tz: string) {
  const { unit, interval } = effective(form);
  const start = form.original && form.original.key === scheduleKey(form) ? form.original.start : firstOccurrence(form, now, tz).toISOString();
  return {
    title: (form.titleTouched ? form.title : autoTitle(form, texts, lang)).trim(),
    enabled: form.enabled,
    disk_serial: form.diskSerials[0],
    rotation_disk_serials: form.diskSerials,
    vm_groups: form.allGuests ? [] : form.vmGroups,
    datastore: form.datastore.trim(),
    recurrence_type: unit,
    recurrence_config: unit === "weekly" ? { interval, weekdays: [...form.weekdays].sort() } : { interval },
    timezone: tz,
    window_starts_at: start,
    window_duration_minutes: form.windowMinutes,
    start_mode: form.confirmation ? "manual_confirmation" : "auto_on_disk_detected",
    auto_eject_after_success: form.autoEject,
    verify_after_backup: form.verify,
  } as const;
}
