// Pure helpers of the first-run wizard (no React, no imports: run by node:test).

export const SETUP_PATH = "/setup";

// Every step the wizard knows, in order. "notifications" (optional, between
// PBS and Done: a first channel + test, or "Later") is on since Phase 5j.
export const ALL_WIZARD_STEPS = ["admin", "agent", "pve", "pbs", "notifications", "done"] as const;
export type WizardStep = (typeof ALL_WIZARD_STEPS)[number];
export const NOTIFICATIONS_STEP_ENABLED = true;

export function visibleSteps(notificationsEnabled: boolean = NOTIFICATIONS_STEP_ENABLED): WizardStep[] {
  return ALL_WIZARD_STEPS.filter((step) => step !== "notifications" || notificationsEnabled);
}

/** Steps shown in the indicator. */
export const WIZARD_STEPS: readonly WizardStep[] = visibleSteps();

export type WizardLanguage = "fr" | "en";
export type ThemePreference = "system" | "light" | "dark";

/**
 * Where the router must go, or null to stay. While the setup is not finished
 * every page leads to /setup; once finished, /setup leads to the dashboard.
 */
export function setupRedirect(pathname: string, setupCompleted: boolean | null): string | null {
  if (setupCompleted === null) return null; // status still loading
  const onSetup = pathname === SETUP_PATH || pathname.startsWith(`${SETUP_PATH}/`);
  if (!setupCompleted && !onSetup) return SETUP_PATH;
  if (setupCompleted && onSetup && !reconfigureStep(pathname)) return "/";
  return null;
}

/** Integrations "Reconfigure": /setup/reconfigure/<agent|pve|pbs> opens that step only. */
export const RECONFIGURE_PATH = `${SETUP_PATH}/reconfigure`;
export const RECONFIGURABLE_STEPS = ["agent", "pve", "pbs"] as const;
export type ReconfigurableStep = (typeof RECONFIGURABLE_STEPS)[number];

export function reconfigureStep(pathname: string): ReconfigurableStep | null {
  const value = pathname.startsWith(`${RECONFIGURE_PATH}/`) ? pathname.slice(RECONFIGURE_PATH.length + 1).replace(/\/$/, "") : "";
  return (RECONFIGURABLE_STEPS as readonly string[]).includes(value) ? (value as ReconfigurableStep) : null;
}

export function normalizeStep(
  value: string | null | undefined,
  notificationsEnabled: boolean = NOTIFICATIONS_STEP_ENABLED,
): WizardStep {
  const steps = visibleSteps(notificationsEnabled) as string[];
  if (steps.includes(value ?? "")) return value as WizardStep;
  // A hidden step stored server-side (feature switched off) resumes on Done's side.
  return value === "notifications" ? "pbs" : "agent";
}

export function stepIndex(step: WizardStep, notificationsEnabled: boolean = NOTIFICATIONS_STEP_ENABLED): number {
  return visibleSteps(notificationsEnabled).indexOf(step);
}

/** Step after `step` among the visible ones (null after Done). */
export function nextStep(step: WizardStep, notificationsEnabled: boolean = NOTIFICATIONS_STEP_ENABLED): WizardStep | null {
  const steps = visibleSteps(notificationsEnabled);
  const index = steps.indexOf(step);
  return index >= 0 && index < steps.length - 1 ? steps[index + 1] : null;
}

/** Back target, or null when going back makes no sense (account created, setup done). */
export function previousStep(step: WizardStep, notificationsEnabled: boolean = NOTIFICATIONS_STEP_ENABLED): WizardStep | null {
  if (step === "admin" || step === "agent" || step === "done") return null;
  const steps = visibleSteps(notificationsEnabled);
  const index = steps.indexOf(step);
  return index > 0 ? steps[index - 1] : null;
}

export function detectLanguage(languages: readonly string[] | undefined): WizardLanguage {
  for (const language of languages ?? []) {
    const code = language.toLowerCase().slice(0, 2);
    if (code === "fr") return "fr";
    if (code === "en") return "en";
  }
  return "en";
}

export function resolveTheme(preference: ThemePreference, systemPrefersDark: boolean): "light" | "dark" {
  if (preference === "system") return systemPrefersDark ? "dark" : "light";
  return preference;
}

export function nextThemePreference(preference: ThemePreference): ThemePreference {
  return preference === "system" ? "light" : preference === "light" ? "dark" : "system";
}

export interface ApiErrorDetail {
  code: string;
  message: string;
}

/**
 * FastAPI error bodies → one readable message: {"detail": {code, message}},
 * {"detail": "text"}, or validation errors [{loc, msg}]. Never raw JSON.
 */
export function parseApiError(status: number, body: string): ApiErrorDetail {
  let detail: unknown;
  try {
    detail = (JSON.parse(body) as { detail?: unknown }).detail;
  } catch {
    detail = undefined;
  }
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const value = detail as { code?: unknown; message?: unknown };
    if (typeof value.message === "string") {
      return { code: typeof value.code === "string" ? value.code : `http_${status}`, message: value.message };
    }
  }
  if (typeof detail === "string") return { code: `http_${status}`, message: detail };
  if (Array.isArray(detail)) {
    const lines = detail
      .map((item) => {
        const entry = item as { loc?: unknown[]; msg?: string };
        const field = (entry.loc ?? []).filter((part) => part !== "body").join(".");
        return field ? `${field}: ${entry.msg ?? ""}` : entry.msg ?? "";
      })
      .filter(Boolean);
    if (lines.length) return { code: "validation", message: lines.join("\n") };
  }
  if (status === 429) return { code: "too_many_attempts", message: "" };
  if (status >= 500) return { code: `http_${status}`, message: "" };
  return { code: `http_${status}`, message: "" };
}

/** Seconds left before an ISO date, never negative. */
export function secondsUntil(iso: string, now: number): number {
  const target = Date.parse(iso);
  return Number.isNaN(target) ? 0 : Math.max(0, Math.round((target - now) / 1000));
}

export function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${String(seconds % 60).padStart(2, "0")}`;
}

export interface PbsCandidateLike {
  vmid: number;
  suggested_url: string | null;
}

/** URL to pre-fill for a selected VM (empty when nothing was detected). */
export function suggestedPbsUrl(candidates: readonly PbsCandidateLike[], vmid: number | null): string {
  return candidates.find((candidate) => candidate.vmid === vmid)?.suggested_url ?? "";
}
