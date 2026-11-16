import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, Loader2, Monitor, Moon, Sun } from "lucide-react";


import { useAuth } from "../AuthContext";
import { Card } from "../components/ui/card";
import { cn } from "../components/ui/utils";
import { wizardApi, type PbsSummary, type PveSummary, type SetupProgress } from "./api";
import { WIZARD_TEXT } from "./i18n";
import {
  nextThemePreference,
  nextStep,
  normalizeStep,
  NOTIFICATIONS_STEP_ENABLED,
  previousStep,
  resolveTheme,
  stepIndex,
  WIZARD_STEPS,
  type ThemePreference,
  type WizardStep,
} from "./logic";
import { ErrorAlert, TextContext } from "./shared";
import { AdminStep } from "./steps/AdminStep";
import { AgentStep } from "./steps/AgentStep";
import { DoneStep } from "./steps/DoneStep";
import { PbsStep } from "./steps/PbsStep";
import { NotificationsStep } from "./steps/NotificationsStep";
import { PveStep } from "./steps/PveStep";
import { SUPPORTED_LANGUAGES, type UiLanguage } from "../language";

const THEME_KEY = "pbo:theme";

function readThemePreference(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" || value === "system" ? value : "system";
  } catch {
    return "system";
  }
}

function useTheme() {
  const [preference, setPreference] = useState<ThemePreference>(readThemePreference);
  const media = useMemo(() => window.matchMedia("(prefers-color-scheme: dark)"), []);
  const [systemDark, setSystemDark] = useState(media.matches);
  useEffect(() => {
    const listener = (event: MediaQueryListEvent) => setSystemDark(event.matches);
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, [media]);
  const cycle = () => {
    const next = nextThemePreference(preference);
    setPreference(next);
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      /* private mode: the choice lasts for this page only */
    }
  };
  return { preference, theme: resolveTheme(preference, systemDark), cycle };
}

/**
 * First-run wizard (/setup), full screen, no sidebar. The current step and
 * the non-secret results live server-side (`/api/v1/setup/progress`), so a
 * reload resumes where the operator left off.
 */
export function SetupWizard({ onFinished }: { onFinished: (path?: string) => void }) {
  // English by default; the selector below is on every screen, the first one included.
  const { status: authStatus, language, setLanguage } = useAuth();
  const text = WIZARD_TEXT[language];
  const { preference, theme, cycle } = useTheme();
  const [progress, setProgress] = useState<SetupProgress | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [moving, setMoving] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setProgress(await wizardApi.progress());
    } catch (err) {
      setError(err);
    }
  }, []);

  useEffect(() => {
    if (authStatus === "authenticated") void load();
  }, [authStatus, load]);

  const step: WizardStep = authStatus === "needs-setup" ? "admin" : normalizeStep(progress?.current_step);

  const goTo = useCallback(async (target: WizardStep) => {
    setMoving(true);
    setError(null);
    try {
      const next = target === "done" ? await wizardApi.complete() : target === "admin" ? null : await wizardApi.setStep(target);
      if (next) setProgress(next);
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (err) {
      setError(err);
    } finally {
      setMoving(false);
    }
  }, []);

  const mergeData = (key: "pve" | "pbs", value: PveSummary | PbsSummary) =>
    setProgress((current) => (current ? { ...current, data: { ...current.data, [key]: value } } : current));

  const back = () => {
    const target = previousStep(step);
    if (target) void goTo(target);
  };

  const ThemeIcon = preference === "system" ? Monitor : preference === "light" ? Sun : Moon;
  const loading = authStatus === "authenticated" && !progress && !error;

  return (
    <TextContext.Provider value={text}>
      <div id="pbo-wizard" className="pbo-ui" data-theme={theme}>
        <div className="mx-auto flex min-h-screen w-full max-w-[640px] flex-col px-4 pb-16 pt-6 sm:px-6 sm:pt-10">
          <header className="mb-8 flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-[13px] font-semibold text-accent-fg">
                PBO
              </div>
              <span className="text-sm font-medium text-muted">{text.product}</span>
            </div>
            <div className="flex items-center gap-1">
            <LanguageSwitch value={language} onChange={setLanguage} label={text.languageLabel} />
            <button
              type="button"
              onClick={cycle}
              className="flex h-9 w-9 items-center justify-center rounded-md text-muted transition-colors hover:bg-subtle hover:text-fg"
              title={text.theme[preference]}
              aria-label={text.theme[preference]}
            >
              <ThemeIcon className="h-4 w-4" />
            </button>
            </div>
          </header>

          <Stepper current={step} labels={text.steps} ariaLabel={text.stepsLabel} />
          <p className="mb-4 mt-6 text-[13px] text-faint">{text.stepOf(stepIndex(step) + 1, WIZARD_STEPS.length)}</p>

          <Card className={cn("overflow-hidden shadow-sm transition-opacity", moving && "pointer-events-none opacity-60")}>
            {loading ? (
              <div className="flex items-center justify-center gap-2 px-6 py-16 text-sm text-muted">
                <Loader2 className="h-4 w-4 animate-spin" />
                {text.loading}
              </div>
            ) : error && !progress && authStatus === "authenticated" ? (
              <div className="flex flex-col gap-4 p-6">
                <ErrorAlert error={error} />
                <button type="button" className="self-start text-sm text-accent hover:underline" onClick={() => void load()}>
                  {text.retry}
                </button>
              </div>
            ) : (
              <div key={step} className="animate-fade-in">
                {step === "admin" ? <AdminStep /> : null}
                {step === "agent" ? <AgentStep onNext={() => void goTo("pve")} /> : null}
                {step === "pve" ? (
                  <PveStep
                    saved={progress?.data.pve}
                    onSaved={(summary) => mergeData("pve", summary)}
                    onBack={back}
                    onNext={() => void goTo("pbs")}
                  />
                ) : null}
                {step === "pbs" ? (
                  <PbsStep
                    saved={progress?.data.pbs}
                    onSaved={(summary) => mergeData("pbs", summary)}
                    onBack={back}
                    onNext={() => void goTo(nextStep("pbs") ?? "done")}
                  />
                ) : null}
                {step === "notifications" && NOTIFICATIONS_STEP_ENABLED ? (
                  <NotificationsStep onBack={back} onNext={() => void goTo(nextStep("notifications") ?? "done")} />
                ) : null}
                {step === "done" && progress ? <DoneStep progress={progress} language={language} onFinish={onFinished} /> : null}
                {error && progress ? (
                  <div className="px-6 pb-6">
                    <ErrorAlert error={error} />
                  </div>
                ) : null}
              </div>
            )}
          </Card>
        </div>
      </div>
    </TextContext.Provider>
  );
}

function LanguageSwitch({ value, onChange, label }: { value: UiLanguage; onChange: (value: UiLanguage) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-md border border-border p-0.5 text-[12px] font-medium">
      {SUPPORTED_LANGUAGES.map((code) => (
        <button
          key={code}
          type="button"
          role="radio"
          aria-checked={value === code}
          onClick={() => onChange(code)}
          className={cn(
            "rounded px-2 py-1 uppercase transition-colors",
            value === code ? "bg-subtle text-fg" : "text-faint hover:text-fg",
          )}
        >
          {code}
        </button>
      ))}
    </div>
  );
}

function Stepper({ current, labels, ariaLabel }: { current: WizardStep; labels: Record<WizardStep, string>; ariaLabel: string }) {
  const currentIndex = stepIndex(current);
  return (
    <ol className="flex items-center gap-2" aria-label={ariaLabel}>
      {WIZARD_STEPS.map((step, index) => {
        const done = index < currentIndex || current === "done";
        const active = index === currentIndex && current !== "done";
        return (
          <li key={step} className="flex flex-1 items-center gap-2" aria-current={active ? "step" : undefined}>
            <span
              className={cn(
                "flex h-7 w-7 shrink-0 items-center justify-center rounded-full border text-xs font-medium transition-colors",
                done && "border-accent bg-accent text-accent-fg",
                active && "border-accent bg-accent/15 text-accent",
                !done && !active && "border-border text-faint",
              )}
            >
              {done ? <Check className="h-3.5 w-3.5" strokeWidth={3} /> : index + 1}
            </span>
            <span className={cn("hidden truncate text-[13px] sm:block", active ? "font-medium text-fg" : "text-faint")}>
              {labels[step]}
            </span>
            {index < WIZARD_STEPS.length - 1 ? (
              <span className={cn("h-px min-w-3 flex-1", index < currentIndex ? "bg-accent" : "bg-border")} />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}
