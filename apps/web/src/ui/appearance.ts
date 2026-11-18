// Phase 5j: the account's display preferences (Settings › General and
// Appearance, Backups defaults), stored server-side, applied to the .pbo-ui
// root. Pure (node:test).

export type ThemeChoice = "system" | "light" | "dark";
export type Accent = "steel" | "teal" | "green" | "amber" | "rose" | "violet";
export type Density = "comfortable" | "compact";
export type MotionChoice = "system" | "on" | "off";
export type DateOrder = "auto" | "dmy" | "mdy" | "ymd";
export type TimeFormat = "24h" | "12h";
export type HomePage = "dashboard" | "disks" | "planning" | "activity" | "resources";

export const ACCENTS: Accent[] = ["steel", "teal", "green", "amber", "rose", "violet"];
export const HOME_PAGES: HomePage[] = ["dashboard", "disks", "planning", "activity", "resources"];
export const HOME_PATH: Record<HomePage, string> = { dashboard: "/", disks: "/disks", planning: "/planning", activity: "/activity", resources: "/assets" };
/** Swatch colours of the accent picker (light-theme accent values, docs/DESIGN.md). */
export const ACCENT_SWATCH: Record<Accent, string> = {
  steel: "#2F5D8A",
  teal: "#1F6B6B",
  green: "#2F6B45",
  amber: "#8A5A12",
  rose: "#A13A55",
  violet: "#5E4A9E",
};

export interface UiPrefs {
  theme: ThemeChoice;
  accent: Accent;
  density: Density;
  sidebarCollapsed: boolean;
  reduceMotion: MotionChoice;
  dateOrder: DateOrder;
  timeFormat: TimeFormat;
  homePage: HomePage;
  defaultVerify: boolean;
  defaultEject: boolean;
}

export const DEFAULT_UI_PREFS: UiPrefs = {
  theme: "system",
  accent: "steel",
  density: "comfortable",
  sidebarCollapsed: false,
  reduceMotion: "system",
  dateOrder: "auto",
  timeFormat: "24h",
  homePage: "dashboard",
  defaultVerify: false,
  defaultEject: true,
};

/** Setting key of each preference (SETTING_SPECS on the API side). */
export const PREF_KEYS: Record<keyof UiPrefs, string> = {
  theme: "UI_THEME",
  accent: "UI_ACCENT",
  density: "UI_DENSITY",
  sidebarCollapsed: "UI_SIDEBAR_COLLAPSED",
  reduceMotion: "UI_REDUCE_MOTION",
  dateOrder: "UI_DATE_FORMAT",
  timeFormat: "UI_TIME_FORMAT",
  homePage: "UI_HOME_PAGE",
  defaultVerify: "DEFAULT_VERIFY_AFTER_BACKUP",
  defaultEject: "DEFAULT_EJECT_AT_END",
};

const oneOf = <T extends string>(value: unknown, choices: readonly T[], fallback: T): T => (choices.includes(value as T) ? (value as T) : fallback);

export function prefsFromValues(values: Record<string, unknown>): UiPrefs {
  const d = DEFAULT_UI_PREFS;
  const bool = (key: string, fallback: boolean) => (typeof values[key] === "boolean" ? (values[key] as boolean) : fallback);
  return {
    theme: oneOf(values.UI_THEME, ["system", "light", "dark"] as const, d.theme),
    accent: oneOf(values.UI_ACCENT, ACCENTS, d.accent),
    density: oneOf(values.UI_DENSITY, ["comfortable", "compact"] as const, d.density),
    sidebarCollapsed: bool("UI_SIDEBAR_COLLAPSED", d.sidebarCollapsed),
    reduceMotion: oneOf(values.UI_REDUCE_MOTION, ["system", "on", "off"] as const, d.reduceMotion),
    dateOrder: oneOf(values.UI_DATE_FORMAT, ["auto", "dmy", "mdy", "ymd"] as const, d.dateOrder),
    timeFormat: oneOf(values.UI_TIME_FORMAT, ["24h", "12h"] as const, d.timeFormat),
    homePage: oneOf(values.UI_HOME_PAGE, HOME_PAGES, d.homePage),
    defaultVerify: bool("DEFAULT_VERIFY_AFTER_BACKUP", d.defaultVerify),
    defaultEject: bool("DEFAULT_EJECT_AT_END", d.defaultEject),
  };
}

/** The setting values of a preference change (PATCH /settings). */
export function settingValues(patch: Partial<UiPrefs>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(patch).map(([key, value]) => [PREF_KEYS[key as keyof UiPrefs], value]));
}

/** data-* attributes of the .pbo-ui root. */
export function rootAttributes(prefs: UiPrefs, systemDark: boolean): Record<string, string> {
  return {
    "data-theme": prefs.theme === "system" ? (systemDark ? "dark" : "light") : prefs.theme,
    "data-accent": prefs.accent,
    "data-density": prefs.density,
    "data-motion": prefs.reduceMotion === "on" ? "reduce" : prefs.reduceMotion === "off" ? "full" : "system",
    "data-sidebar": prefs.sidebarCollapsed ? "collapsed" : "expanded",
  };
}

/** Where "/" leads on the first page of a session (Settings › General › Home page). */
export function homeRedirect(pathname: string, prefs: UiPrefs, alreadyRedirected: boolean): string | null {
  if (alreadyRedirected || pathname !== "/" || prefs.homePage === "dashboard") return null;
  return HOME_PATH[prefs.homePage];
}
