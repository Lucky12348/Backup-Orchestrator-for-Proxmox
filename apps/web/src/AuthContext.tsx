import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

/**
 * Session state. The session itself is an HttpOnly cookie set by the API:
 * this code never sees the token, it only asks the API who is logged in.
 */
export type AuthStatus = "loading" | "needs-setup" | "anonymous" | "authenticated";

interface AuthContextValue {
  status: AuthStatus;
  username: string | null;
  isAuthenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  createAdmin: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  /** Local-only reset after the API answered 401 (cookie expired). */
  markSessionExpired: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

async function readError(response: Response, fallback: string): Promise<Error> {
  const data = (await response.json().catch(() => ({}))) as { detail?: unknown };
  if (typeof data.detail === "string") return new Error(data.detail);
  if (Array.isArray(data.detail)) {
    return new Error(data.detail.map((item: { msg?: string }) => item.msg ?? "").filter(Boolean).join("\n") || fallback);
  }
  return new Error(fallback);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [username, setUsername] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const me = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
      if (me.ok) {
        const data = (await me.json()) as { username: string };
        setUsername(data.username);
        setStatus("authenticated");
        return;
      }
      const setup = await fetch("/api/v1/setup/status", { credentials: "same-origin" });
      const setupData = setup.ok ? ((await setup.json()) as { needs_setup: boolean }) : { needs_setup: false };
      setUsername(null);
      setStatus(setupData.needs_setup ? "needs-setup" : "anonymous");
    } catch {
      setUsername(null);
      setStatus("anonymous");
    }
  }, []);

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
    if (!response.ok) throw await readError(response, "Authentication failed");
    const data = (await response.json()) as { username: string };
    setUsername(data.username);
    setStatus("authenticated");
  }, []);

  const createAdmin = useCallback(async (user: string, password: string) => {
    const response = await fetch("/api/v1/setup/admin", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: user, password }),
    });
    if (response.status === 409) {
      // Someone else finished setup first: fall back to the login screen.
      await refresh();
      throw new Error("An administrator account already exists. Please log in.");
    }
    if (!response.ok) throw await readError(response, "Account creation failed");
    const data = (await response.json()) as { username: string };
    setUsername(data.username);
    setStatus("authenticated");
  }, [refresh]);

  const logout = useCallback(async () => {
    try {
      await fetch("/api/v1/auth/logout", { method: "POST", credentials: "same-origin" });
    } finally {
      setUsername(null);
      setStatus("anonymous");
    }
  }, []);

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

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
