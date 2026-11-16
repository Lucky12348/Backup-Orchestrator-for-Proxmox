import { useEffect, useMemo, useState } from "react";

import { nextThemePreference, resolveTheme, type ThemePreference } from "../setup/logic";

const THEME_KEY = "pbo:theme";

function readThemePreference(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" || value === "system" ? value : "system";
  } catch {
    return "system";
  }
}

/** Theme of the new UI (wizard + shell): follows the system, manual toggle. */
export function useTheme() {
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
      /* private mode: this page only */
    }
  };
  const set = (next: ThemePreference) => {
    setPreference(next);
    try {
      window.localStorage.setItem(THEME_KEY, next);
    } catch {
      /* private mode: this page only */
    }
  };
  return { preference, theme: resolveTheme(preference, systemDark), cycle, set };
}
