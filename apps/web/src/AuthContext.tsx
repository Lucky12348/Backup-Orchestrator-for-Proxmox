import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { initialLanguage, LANGUAGE_STORAGE_KEY, reconcileLanguage, type UiLanguage } from "./language";

/**
 * Session state. The session itself is an HttpOnly cookie set by the API:
 * this code never sees the token, it only asks the API who is logged in.
 */
export type AuthStatus = "loading" | "needs-setup" | "anonymous" | "authenticated";

interface AuthContextValue {
  status: AuthStatus;
  username: string | null;
  isAuthenticated: boolean;
  /** First-run wizard finished (null while loading). Until then every page leads to /setup. */
  setupCompleted: boolean | null;
  markSetupCompleted: () => void;
  /** UI language (English by default). Saved in this browser and on the account. */
  language: UiLanguage;
  setLanguage: (language: UiLanguage) => void;
  login: (username: string, password: string) => Promise<void>;
  createAdmin: (username: string, password: string, options?: { timezone?: string }) => Promise<void>;
  logout: () => Promise<void>;
  /** Local-only reset after the API answered 401 (cookie expired). */
  markSessionExpired: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** Error with the stable API code when there is one ({"detail": {code, message}}). */
export type CodedError = Error & { code?: string; details?: Record<string, unknown> };

async function readError(response: Response, fallback: string): Promise<CodedError> {
  const data = (await response.json().catch(() => ({}))) as { detail?: unknown };
  if (typeof data.detail === "string") return new Error(data.detail);
  if (data.detail && typeof data.detail === "object" && !Array.isArray(data.detail)) {
    const detail = data.detail as { code?: string; message?: string };
    return Object.assign(new Error(detail.message || fallback), { code: detail.code, details: data.detail as Record<string, unknown> });
  }
  if (Array.isArray(data.detail)) {
    return new Error(data.detail.map((item: { msg?: string }) => item.msg ?? "").filter(Boolean).join("\n") || fallback);
  }
  return new Error(fallback);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [username, setUsername] = useState<string | null>(null);
  const [setupCompleted, setSetupCompleted] = useState<boolean | null>(null);
  const [language, setLanguageState] = useState<UiLanguage>(() =>
    initialLanguage(safeStorage(), typeof navigator === "undefined" ? [] : navigator.languages ?? [navigator.language]),
  );

  const saveLocalLanguage = useCallback((value: UiLanguage) => {
    setLanguageState(value);
    try {
      window.localStorage.setItem(LANGUAGE_STORAGE_KEY, value);
    } catch {
      /* private mode: this page only */
    }
    document.documentElement.lang = value;
  }, []);

  const pushAccountLanguage = useCallback((value: UiLanguage) => {
    void fetch("/api/v1/auth/me/preferences", {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ language: value }),
    }).catch(() => undefined);
  }, []);

  /** Account preference wins; an account without one adopts the local choice. */
  const applyAccountLanguage = useCallback(
    (account: string | null | undefined) => {
      setLanguageState((local) => {
        const { language: next, pushToAccount } = reconcileLanguage(account, local);
        if (pushToAccount) pushAccountLanguage(next);
        try {
          window.localStorage.setItem(LANGUAGE_STORAGE_KEY, next);
        } catch {
          /* private mode */
        }
        return next;
      });
    },
    [pushAccountLanguage],
  );

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const readSetupStatus = useCallback(async () => {
    const setup = await fetch("/api/v1/setup/status", { credentials: "same-origin" });
    const data = setup.ok
      ? ((await setup.json()) as { needs_setup: boolean; setup_completed?: boolean })
      : { needs_setup: false, setup_completed: true };
    // Older API without the field: never trap the operator in the wizard.
    setSetupCompleted(data.setup_completed ?? true);
    return data;
  }, []);

  const refresh = useCallback(async () => {
    try {
      const setupData = await readSetupStatus();
      const me = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
      if (me.ok) {
        const data = (await me.json()) as { username: string; language?: string | null };
        applyAccountLanguage(data.language);
        setUsername(data.username);
        setStatus("authenticated");
        return;
      }
      setUsername(null);
      setStatus(setupData.needs_setup ? "needs-setup" : "anonymous");
    } catch {
      setUsername(null);
      setStatus("anonymous");
    }
  }, [readSetupStatus, applyAccountLanguage]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const login = useCallback(async (user: string, password: string) => {
    const body = new URLSearchParams();
    body.append("username", user);
    body.append("password", password);
    const response = await fetch("/api/v1/auth/token", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
    if (!response.ok) {
      // The status gives the translated message (401 bad credentials, 429 locked out).
      throw Object.assign(await readError(response, "Authentication failed"), { status: response.status });
    }
    const data = (await response.json()) as { username: string; language?: string | null };
    await readSetupStatus().catch(() => setSetupCompleted(true));
    applyAccountLanguage(data.language);
    setUsername(data.username);
    setStatus("authenticated");
  }, [readSetupStatus, applyAccountLanguage]);

  const createAdmin = useCallback(async (user: string, password: string, options?: { timezone?: string }) => {
    const response = await fetch("/api/v1/setup/admin", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      // The language picked on the first wizard screen becomes the account's.
      body: JSON.stringify({ username: user, password, language, timezone: options?.timezone || undefined }),
    });
    if (response.status === 409) {
      // Someone else finished setup first: fall back to the login screen.
      await refresh();
      throw Object.assign(new Error("An administrator account already exists. Please log in."), { code: "admin_exists" });
    }
    if (!response.ok) throw await readError(response, "Account creation failed");
    const data = (await response.json()) as { username: string };
    setSetupCompleted(false);
    setUsername(data.username);
    setStatus("authenticated");
  }, [refresh, language]);

  const logout = useCallback(async () => {
    try {
      await fetch("/api/v1/auth/logout", { method: "POST", credentials: "same-origin" });
    } finally {
      setUsername(null);
      setStatus("anonymous");
    }
  }, []);

  const markSetupCompleted = useCallback(() => setSetupCompleted(true), []);

  const setLanguage = useCallback(
    (value: UiLanguage) => {
      saveLocalLanguage(value);
      if (status === "authenticated") pushAccountLanguage(value);
    },
    [saveLocalLanguage, pushAccountLanguage, status],
  );

  const markSessionExpired = useCallback(() => {
    setUsername(null);
    setStatus("anonymous");
  }, []);

  return (
    <AuthContext.Provider
      value={{
        status,
        username,
        isAuthenticated: status === "authenticated",
        setupCompleted,
        markSetupCompleted,
        language,
        setLanguage,
        login,
        createAdmin,
        logout,
        markSessionExpired,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

function safeStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
