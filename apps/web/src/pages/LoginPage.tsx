import { useEffect, useState, type FormEvent } from "react";
import { AlertTriangle, Loader2, Lock, LogIn, Monitor, Moon, Sun } from "lucide-react";

import { useAuth } from "../AuthContext";
import { cn } from "../components/ui/utils";
import type { TranslationDictionary } from "../i18n";
import { loginMessage, waitText, type LoginFailure } from "../loginModel";
import { LogoMark } from "../ui/Logo";
import { useTheme } from "../ui/theme";

interface LoginPageProps {
  t: TranslationDictionary;
}

const LANGUAGES = ["fr", "en"] as const;

/** Sign-in, in the style of the setup wizard (Phase 6b). */
export function LoginPage({ t }: LoginPageProps) {
  const { login, language, setLanguage } = useAuth();
  const { preference, theme, cycle } = useTheme();
  const text = t.login;
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [lockedUntil, setLockedUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [loading, setLoading] = useState(false);
  const locked = lockedUntil !== null && lockedUntil > now;
  const ThemeIcon = preference === "system" ? Monitor : preference === "light" ? Sun : Moon;

  useEffect(() => {
    if (lockedUntil === null) return;
    const timer = window.setInterval(() => {
      setNow(Date.now());
      if (Date.now() >= lockedUntil) {
        setLockedUntil(null);
        setError(null);
      }
    }, 1000);
    return () => window.clearInterval(timer);
  }, [lockedUntil]);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (locked) return;
    setError(null);
    setLoading(true);
    try {
      await login(username, password);
    } catch (failure) {
      const message = loginMessage(failure as LoginFailure, text, Date.now(), language);
      setError(message.text);
      setLockedUntil(message.lockedUntil);
      setNow(Date.now());
      setPassword("");
    } finally {
      setLoading(false);
    }
  }

  const field =
    "w-full rounded-lg border border-solid border-border-strong bg-bg px-3 py-2.5 text-sm text-fg outline-none placeholder:text-faint focus:border-accent disabled:opacity-60";

  return (
    <div className="pbo-ui min-h-screen bg-bg text-fg" data-theme={theme}>
      <div className="mx-auto flex min-h-screen w-full max-w-[420px] flex-col px-4 pb-16 pt-6 sm:pt-10">
        <header className="mb-10 flex items-center justify-end gap-1">
          <div role="radiogroup" aria-label={t.shell.language} className="flex rounded-md border border-solid border-border p-0.5 text-xs font-medium">
            {LANGUAGES.map((code) => (
              <button
                key={code}
                type="button"
                role="radio"
                aria-checked={language === code}
                onClick={() => setLanguage(code)}
                className={cn(
                  "rounded border-0 px-2 py-1 uppercase",
                  language === code ? "bg-subtle text-fg" : "bg-transparent text-muted hover:text-fg",
                )}
              >
                {code === "fr" ? "FR" : "EN"}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={cycle}
            aria-label={t.shell.theme[preference]}
            title={t.shell.theme[preference]}
            className="flex h-9 w-9 items-center justify-center rounded-md border-0 bg-transparent text-muted hover:bg-subtle hover:text-fg"
          >
            <ThemeIcon className="h-4 w-4" />
          </button>
        </header>

        <div className="flex flex-col items-center gap-3 text-center">
          <LogoMark size={32} className="text-fg" />
          <h1 className="m-0 text-[20px] font-semibold tracking-[-0.015em]">{text.title}</h1>
          <p className="m-0 text-sm text-muted">{text.subtitleV2}</p>
        </div>

        <form onSubmit={handleSubmit} className="mt-8 flex flex-col gap-4 rounded-lg border border-solid border-border bg-surface p-6" noValidate>
          {error ? (
            <div role="alert" className={cn("flex gap-2.5 rounded-md p-3 text-[13px] leading-[1.45]", locked ? "bg-warning/15 text-warning-text" : "bg-danger/15 text-danger-text")}>
              {locked ? <Lock className="mt-0.5 h-4 w-4 flex-none" /> : <AlertTriangle className="mt-0.5 h-4 w-4 flex-none" />}
              <span>
                {error}
                {locked && lockedUntil ? <span className="mt-1 block text-xs">{waitText((lockedUntil - now) / 1000)}</span> : null}
              </span>
            </div>
          ) : null}
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium">{text.usernameLabel}</span>
            <input
              id="pbo-username"
              className={field}
              type="text"
              autoCapitalize="none"
              autoComplete="username"
              autoCorrect="off"
              autoFocus
              spellCheck={false}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              disabled={loading || locked}
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[13px] font-medium">{text.passwordLabel}</span>
            <input
              id="pbo-password"
              className={field}
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={loading || locked}
            />
          </label>
          <button
            type="submit"
            disabled={loading || locked || !username || !password}
            className="mt-1 flex items-center justify-center gap-2 rounded-lg border-0 bg-accent px-4 py-2.5 text-sm font-medium text-white hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <LogIn className="h-4 w-4" />}
            {loading ? text.submittingV2 : text.submitV2}
          </button>
        </form>
        <p className="mt-4 text-center text-xs text-faint">{text.lockHint}</p>
      </div>
    </div>
  );
}
