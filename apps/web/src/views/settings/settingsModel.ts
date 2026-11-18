// Settings page (Phase 5g, sections in 5j). Pure (node:test).

/** Valid minimum disk size (MIN_DISK_SIZE_GIB, 1–65536 GiB) or null. */
export function parseMinSize(value: string): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  const size = Number(value);
  return size >= 1 && size <= 65536 ? size : null;
}

export type SectionKey = "general" | "appearance" | "backups" | "retention" | "agents" | "about";
export const SECTIONS: SectionKey[] = ["general", "appearance", "backups", "retention", "agents", "about"];

/** Setting keys saved by each section (one PATCH per section). */
export const SECTION_KEYS: Record<Exclude<SectionKey, "about">, string[]> = {
  general: ["TIMEZONE", "UI_DATE_FORMAT", "UI_TIME_FORMAT", "UI_HOME_PAGE", "PUBLIC_URL"],
  appearance: ["UI_THEME", "UI_ACCENT", "UI_DENSITY", "UI_SIDEBAR_COLLAPSED", "UI_REDUCE_MOTION"],
  backups: [
    "DEFAULT_VERIFY_AFTER_BACKUP",
    "DEFAULT_EJECT_AT_END",
    "DEFAULT_RETENTION_KEEP_LAST",
    "DEFAULT_RETENTION_KEEP_WEEKLY",
    "DEFAULT_RETENTION_KEEP_MONTHLY",
    "ROTATION_STALE_DAYS",
    "MIN_DISK_SIZE_GIB",
  ],
  retention: ["RUN_HISTORY_MODE", "RUN_HISTORY_DAYS", "RUN_HISTORY_KEEP_PER_DISK", "RUN_LOG_DETAIL_DAYS"],
  agents: ["AUTO_UPDATE_AGENTS"],
};

export const HISTORY_DAYS = [30, 90, 180, 365];
/** "Detailed journals": 0 = keep them as long as the run. */
export const LOG_DAYS = [0, 30, 90, 180, 365];

export type Values = Record<string, unknown>;

export function valuesOf(items: { key: string; value: unknown }[]): Values {
  return Object.fromEntries(items.map((item) => [item.key, item.value]));
}

/** The keys of a section whose draft differs from the saved value. */
export function changed(section: Exclude<SectionKey, "about">, saved: Values, draft: Values): Values {
  return Object.fromEntries(SECTION_KEYS[section].filter((key) => draft[key] !== saved[key]).map((key) => [key, draft[key]]));
}

const int = (value: unknown) => (typeof value === "number" ? value : /^\d+$/.test(String(value ?? "").trim()) ? Number(value) : NaN);

/** Errors of a section's draft: setting key → error code (translated by the page). */
export function sectionErrors(section: Exclude<SectionKey, "about">, draft: Values): Record<string, string> {
  const errors: Record<string, string> = {};
  const range = (key: string, min: number, max: number) => {
    const value = int(draft[key]);
    if (!(value >= min && value <= max)) errors[key] = "range";
  };
  if (section === "general") {
    const url = String(draft.PUBLIC_URL ?? "").trim();
    if (url && !/^https?:\/\/[^\s/]+(\/\S*)?$/.test(url)) errors.PUBLIC_URL = "url";
    if (!String(draft.TIMEZONE ?? "").trim()) errors.TIMEZONE = "required";
  }
  if (section === "backups") {
    for (const key of ["DEFAULT_RETENTION_KEEP_LAST", "DEFAULT_RETENTION_KEEP_WEEKLY", "DEFAULT_RETENTION_KEEP_MONTHLY"]) range(key, 0, 1000);
    if (!["DEFAULT_RETENTION_KEEP_LAST", "DEFAULT_RETENTION_KEEP_WEEKLY", "DEFAULT_RETENTION_KEEP_MONTHLY"].some((key) => int(draft[key]) > 0)) {
      errors.DEFAULT_RETENTION_KEEP_LAST = "retention_empty";
    }
    range("ROTATION_STALE_DAYS", 1, 365);
    range("MIN_DISK_SIZE_GIB", 1, 65536);
  }
  if (section === "retention" && draft.RUN_HISTORY_MODE === "per_disk") range("RUN_HISTORY_KEEP_PER_DISK", 1, 10000);
  return errors;
}

/** Number inputs are edited as text: the typed values sent to the API. */
export function typedValues(values: Values): Values {
  return Object.fromEntries(
    Object.entries(values).map(([key, value]) => [key, typeof value === "string" && /^\d+$/.test(value.trim()) && !["TIMEZONE", "PUBLIC_URL"].includes(key) ? Number(value) : value]),
  );
}

/** Retention policy of the preview ("X runs would be deleted"). */
export function retentionPolicy(draft: Values): { mode: string; days: number; keep_per_disk: number; log_detail_days: number } {
  return {
    mode: String(draft.RUN_HISTORY_MODE ?? "keep_all"),
    days: int(draft.RUN_HISTORY_DAYS) || 180,
    keep_per_disk: int(draft.RUN_HISTORY_KEEP_PER_DISK) || 50,
    log_detail_days: int(draft.RUN_LOG_DETAIL_DAYS) || 0,
  };
}
