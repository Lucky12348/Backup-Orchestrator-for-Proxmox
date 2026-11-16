// Proxmox backup jobs (Phase 6c): readable names and schedules, job form.
// Pure (node:test).

export const DAY_CODES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type DayCode = (typeof DAY_CODES)[number];
export type Lang = "fr" | "en";

const SHORT: Record<Lang, string[]> = {
  fr: ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."],
  en: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
};
const EVERY_DAY: Record<Lang, string> = { fr: "tous les jours", en: "every day" };

export interface ParsedSchedule {
  days: DayCode[];
  time: string;
}

function dayList(spec: string): DayCode[] | null {
  const result = new Set<DayCode>();
  for (const part of spec.toLowerCase().split(",")) {
    const range = part.split("..");
    if (range.length === 2) {
      const start = DAY_CODES.indexOf(range[0] as DayCode);
      const end = DAY_CODES.indexOf(range[1] as DayCode);
      if (start < 0 || end < 0) return null;
      for (let index = start; ; index = (index + 1) % 7) {
        result.add(DAY_CODES[index]);
        if (index === end) break;
      }
    } else if ((DAY_CODES as readonly string[]).includes(part)) {
      result.add(part as DayCode);
    } else {
      return null;
    }
  }
  return DAY_CODES.filter((day) => result.has(day));
}

/** "sun 03:00", "mon..fri 22:00", "mon,wed 2:00", "21:00", "daily" — else null. */
export function parseSchedule(schedule: string | null | undefined): ParsedSchedule | null {
  const text = (schedule ?? "").trim().toLowerCase();
  if (!text) return null;
  if (text === "daily") return { days: [...DAY_CODES], time: "00:00" };
  const time = (value: string) => {
    const match = /^(\d{1,2}):(\d{2})$/.exec(value);
    return match && Number(match[1]) < 24 && Number(match[2]) < 60 ? `${match[1].padStart(2, "0")}:${match[2]}` : null;
  };
  const parts = text.split(/\s+/);
  if (parts.length === 1) {
    const only = time(parts[0]);
    return only ? { days: [...DAY_CODES], time: only } : null;
  }
  if (parts.length === 2) {
    const days = dayList(parts[0]);
    const at = time(parts[1]);
    return days && days.length && at ? { days, time: at } : null;
  }
  return null;
}

/** "dim. 03:00", "lun.–ven. 22:00", "tous les jours 03:00"; the raw value when not understood. */
export function describeSchedule(schedule: string | null | undefined, lang: Lang): string {
  const parsed = parseSchedule(schedule);
  if (!parsed) return schedule ?? "";
  const indexes = parsed.days.map((day) => DAY_CODES.indexOf(day));
  let days: string;
  if (indexes.length === 7) days = EVERY_DAY[lang];
  else if (indexes.length > 2 && indexes.every((value, i) => i === 0 || value === indexes[i - 1] + 1)) {
    days = `${SHORT[lang][indexes[0]]}–${SHORT[lang][indexes[indexes.length - 1]]}`;
  } else days = indexes.map((index) => SHORT[lang][index]).join(", ");
  return `${days} ${parsed.time}`;
}

export interface JobLike {
  job_id: string;
  storage: string | null;
  schedule: string | null;
  comment?: string | null;
}

/** « Sauvegarde Proxmox → pbs-test · dim. 03:00 » (the raw id only in the details). */
export function jobLabel(job: JobLike, template: string, lang: Lang): string {
  const base = template.replace("{storage}", job.storage || job.job_id);
  const when = describeSchedule(job.schedule, lang);
  return when ? `${base} · ${when}` : base;
}

export function buildSchedule(days: readonly DayCode[], time: string): string {
  const ordered = DAY_CODES.filter((day) => days.includes(day));
  return `${(ordered.length ? ordered : DAY_CODES).join(",")} ${time || "03:00"}`;
}

export interface JobForm {
  storage: string;
  days: DayCode[];
  time: string;
  node: string;
  enabled: boolean;
  comment: string;
  keepLast: string;
  keepDaily: string;
  keepWeekly: string;
  keepMonthly: string;
  keepYearly: string;
  vmids: number[];
}

export function blankJobForm(storage = ""): JobForm {
  return {
    storage,
    days: [...DAY_CODES],
    time: "03:00",
    node: "",
    enabled: true,
    comment: "",
    keepLast: "",
    keepDaily: "7",
    keepWeekly: "4",
    keepMonthly: "",
    keepYearly: "",
    vmids: [],
  };
}

export function parseRetention(retention: string | null | undefined): Record<string, string> {
  const parsed: Record<string, string> = {};
  for (const part of (retention ?? "").split(",")) {
    const [key, value] = part.split("=");
    if (key && value) parsed[key.trim()] = value.trim();
  }
  return parsed;
}

export interface JobFull extends JobLike {
  node: string | null;
  enabled: boolean;
  retention: string | null;
  selected_vmids: number[];
}

export function formFromJob(job: JobFull): JobForm {
  const retention = parseRetention(job.retention);
  const schedule = parseSchedule(job.schedule) ?? { days: [...DAY_CODES], time: "03:00" };
  return {
    storage: job.storage ?? "",
    days: schedule.days,
    time: schedule.time,
    node: job.node ?? "",
    enabled: job.enabled,
    comment: job.comment ?? "",
    keepLast: retention["keep-last"] ?? "",
    keepDaily: retention["keep-daily"] ?? "",
    keepWeekly: retention["keep-weekly"] ?? "",
    keepMonthly: retention["keep-monthly"] ?? "",
    keepYearly: retention["keep-yearly"] ?? "",
    vmids: [...job.selected_vmids],
  };
}

export function jobProblems(form: JobForm): ("storage" | "days" | "time" | "keep")[] {
  const problems: ("storage" | "days" | "time" | "keep")[] = [];
  if (!form.storage.trim()) problems.push("storage");
  if (!form.days.length) problems.push("days");
  if (!/^\d{2}:\d{2}$/.test(form.time)) problems.push("time");
  const keeps = [form.keepLast, form.keepDaily, form.keepWeekly, form.keepMonthly, form.keepYearly];
  if (keeps.some((value) => value.trim() !== "" && !(Number.isInteger(Number(value)) && Number(value) >= 0))) problems.push("keep");
  return problems;
}

/** Form → ProxmoxBackupJobFormValues (POST/PUT /proxmox/backup-jobs). */
export function formToValues(form: JobForm) {
  const keep = (value: string) => (value.trim() === "" ? null : Number(value));
  return {
    storage: form.storage.trim(),
    schedule: buildSchedule(form.days, form.time),
    node: form.node.trim() || null,
    selected_vmids: [...form.vmids].sort((a, b) => a - b),
    mode: "snapshot",
    enabled: form.enabled,
    comment: form.comment.trim() || null,
    keep_last: keep(form.keepLast),
    keep_daily: keep(form.keepDaily),
    keep_weekly: keep(form.keepWeekly),
    keep_monthly: keep(form.keepMonthly),
    keep_yearly: keep(form.keepYearly),
  };
}
