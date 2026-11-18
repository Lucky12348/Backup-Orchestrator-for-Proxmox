import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { getSettingsGroups, patchSettings } from "../api";
import { setFormatPreferences } from "../views/format";
import { DEFAULT_UI_PREFS, prefsFromValues, rootAttributes, settingValues, type UiPrefs } from "./appearance";

const THEME_KEY = "pbo:theme"; // the login page reads it (no session there)

interface AppearanceValue {
  prefs: UiPrefs;
  attributes: Record<string, string>;
  loaded: boolean;
  /** Save some preferences (PATCH /settings) and apply them at once. */
  update: (patch: Partial<UiPrefs>) => Promise<void>;
  /** Re-read them (another section saved them). */
  reload: () => Promise<void>;
}

const AppearanceContext = createContext<AppearanceValue | null>(null);

function cachedTheme(): UiPrefs["theme"] {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return value === "light" || value === "dark" || value === "system" ? value : "system";
  } catch {
    return "system";
  }
}

function cacheTheme(theme: UiPrefs["theme"]) {
  try {
    window.localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* private mode: this page only */
  }
}

function useSystemDark(): boolean {
  const media = useMemo(() => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null), []);
  const [dark, setDark] = useState(media?.matches ?? true);
  useEffect(() => {
    if (!media) return undefined;
    const listener = (event: MediaQueryListEvent) => setDark(event.matches);
    media.addEventListener("change", listener);
    return () => media.removeEventListener("change", listener);
  }, [media]);
  return dark;
}

/** Phase 5j: display preferences of the account, applied to the shell root. */
export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState<UiPrefs>(() => ({ ...DEFAULT_UI_PREFS, theme: typeof window === "undefined" ? "system" : cachedTheme() }));
  const [loaded, setLoaded] = useState(false);
  const systemDark = useSystemDark();

  const reload = useCallback(async () => {
    try {
      const items = await getSettingsGroups(["general", "appearance", "backups"]);
      const next = prefsFromValues(Object.fromEntries(items.map((item) => [item.key, item.value])));
      setPrefs(next);
      cacheTheme(next.theme);
    } catch {
      /* not signed in yet / offline: keep the defaults */
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    setFormatPreferences({ dateOrder: prefs.dateOrder, timeFormat: prefs.timeFormat });
  }, [prefs.dateOrder, prefs.timeFormat]);

  const update = useCallback(async (patch: Partial<UiPrefs>) => {
    setPrefs((current) => ({ ...current, ...patch }));
    if (patch.theme) cacheTheme(patch.theme);
    await patchSettings(settingValues(patch));
  }, []);

  const value = useMemo<AppearanceValue>(
    () => ({ prefs, attributes: rootAttributes(prefs, systemDark), loaded, update, reload }),
    [prefs, systemDark, loaded, update, reload],
  );
  return <AppearanceContext.Provider value={value}>{children}</AppearanceContext.Provider>;
}

const FALLBACK: AppearanceValue = {
  prefs: DEFAULT_UI_PREFS,
  attributes: rootAttributes(DEFAULT_UI_PREFS, true),
  loaded: false,
  update: async () => undefined,
  reload: async () => undefined,
};

export function useAppearance(): AppearanceValue {
  return useContext(AppearanceContext) ?? FALLBACK;
}
