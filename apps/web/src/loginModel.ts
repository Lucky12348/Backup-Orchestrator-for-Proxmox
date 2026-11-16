// Login screen messages (Phase 6b): bad credentials, attempts left before the
// temporary lock, how long the lock lasts. Pure (node:test).

export interface LoginTexts {
  invalid: string;
  attemptsLeft: string; // "{n} attempt(s) left before a {minutes}-minute lock."
  lockedNow: string; // "Sign-in locked for {minutes} min after too many failures."
  locked: string; // "Too many failed attempts: try again in {wait} (at {time})."
  failed: string;
}

export interface LoginFailure {
  status?: number;
  details?: Record<string, unknown>;
}

export interface LoginMessage {
  text: string;
  /** Epoch ms until which the form stays disabled (429). */
  lockedUntil: number | null;
}

/** "4 min 05 s" / "35 s". */
export function waitText(seconds: number): string {
  const value = Math.max(0, Math.ceil(seconds));
  if (value < 60) return `${value} s`;
  return `${Math.floor(value / 60)} min ${String(value % 60).padStart(2, "0")} s`;
}

export function loginMessage(failure: LoginFailure, texts: LoginTexts, now: number, lang: "fr" | "en"): LoginMessage {
  const details = failure.details ?? {};
  if (failure.status === 429) {
    const retry = Number(details.retry_after) || 300;
    const until = now + retry * 1000;
    const time = new Intl.DateTimeFormat(lang === "fr" ? "fr-FR" : "en-GB", { hour: "2-digit", minute: "2-digit" }).format(new Date(until));
    return { text: texts.locked.replace("{wait}", waitText(retry)).replace("{time}", time), lockedUntil: until };
  }
  if (failure.status === 401) {
    const left = Number(details.attempts_left);
    const minutes = String(Number(details.lock_minutes) || 5);
    if (Number.isFinite(left) && left === 0) return { text: `${texts.invalid} ${texts.lockedNow.replace("{minutes}", minutes)}`, lockedUntil: null };
    if (Number.isFinite(left) && left <= 2) {
      return { text: `${texts.invalid} ${texts.attemptsLeft.replace("{n}", String(left)).replace("{minutes}", minutes)}`, lockedUntil: null };
    }
    return { text: texts.invalid, lockedUntil: null };
  }
  return { text: texts.failed, lockedUntil: null };
}
