// Pure helpers of the first-run wizard (no React, no imports: run by node:test).

export const SETUP_PATH = "/setup";

export const WIZARD_STEPS = ["admin", "agent", "pve", "pbs", "done"] as const;
export type WizardStep = (typeof WIZARD_STEPS)[number];

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
  if (setupCompleted && onSetup) return "/";
  return null;
}

export function normalizeStep(value: string | null | undefined): WizardStep {
  return (WIZARD_STEPS as readonly string[]).includes(value ?? "") ? (value as WizardStep) : "agent";
}

export function stepIndex(step: WizardStep): number {
  return WIZARD_STEPS.indexOf(step);
}

/** Back target, or null when going back makes no sense (account created, setup done). */
export function previousStep(step: WizardStep): WizardStep | null {
  if (step === "pve") return "agent";
  if (step === "pbs") return "pve";
  return null;
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
