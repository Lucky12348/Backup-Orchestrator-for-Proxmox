// UI language: English by default, French available. No runtime import (node:test).
//
// Order of precedence:
//   1. the account preference (stored in the database, applied after login);
//   2. the choice saved in this browser (localStorage, also before login);
//   3. only on the very first load, when nothing was ever chosen: the browser
//      language (French browsers get French), saved at once so it is never
//      derived again;
//   4. English.

export type UiLanguage = "en" | "fr";

export const LANGUAGE_STORAGE_KEY = "pbo:language";
export const DEFAULT_LANGUAGE: UiLanguage = "en";
export const SUPPORTED_LANGUAGES: UiLanguage[] = ["en", "fr"];

export function isLanguage(value: unknown): value is UiLanguage {
  return value === "en" || value === "fr";
}

export function browserLanguage(languages: readonly string[] | undefined): UiLanguage {
  for (const language of languages ?? []) {
    const code = language.toLowerCase().slice(0, 2);
    if (code === "fr") return "fr";
    if (code === "en") return "en";
  }
  return DEFAULT_LANGUAGE;
}

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

/** First language of this browser session (step 2–4 above). */
export function initialLanguage(storage: StorageLike | null, languages: readonly string[] | undefined): UiLanguage {
  let stored: string | null = null;
  try {
    stored = storage?.getItem(LANGUAGE_STORAGE_KEY) ?? null;
  } catch {
    stored = null;
  }
  if (isLanguage(stored)) return stored;
  const first = browserLanguage(languages);
  try {
    storage?.setItem(LANGUAGE_STORAGE_KEY, first); // never derived again
  } catch {
    /* private mode */
  }
  return first;
}

/**
 * After login: which language to show, and whether the local choice must be
 * pushed to the account (the account had none yet).
 */
export function reconcileLanguage(
  account: string | null | undefined,
  local: UiLanguage,
): { language: UiLanguage; pushToAccount: boolean } {
  if (isLanguage(account)) return { language: account, pushToAccount: false };
  return { language: local, pushToAccount: true };
}

export function detectTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function supportedTimeZones(): string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  try {
    return intl.supportedValuesOf ? intl.supportedValuesOf("timeZone") : [];
  } catch {
    return [];
  }
}
