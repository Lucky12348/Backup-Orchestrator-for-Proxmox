import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ChevronRight, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { Link } from "react-router-dom";

import { getAbout, getSettingsGroups, patchSettings, previewRetention, updateAccountLanguage, type AboutInfo, type RetentionPreview } from "../../api";
import { useAuth } from "../../AuthContext";
import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { SUPPORTED_LANGUAGES, supportedTimeZones, type UiLanguage } from "../../language";
import { ACCENT_SWATCH, ACCENTS, HOME_PAGES, prefsFromValues } from "../../ui/appearance";
import { useAppearance } from "../../ui/AppearanceProvider";
import { PageTitle } from "../../ui/PageTitle";
import { StatusDot } from "../../ui/StatusDot";
import { useToast } from "../../ui/toast";
import { fill } from "../format";
import {
  HISTORY_DAYS,
  LOG_DAYS,
  SECTIONS,
  activeSection,
  changed,
  retentionPolicy,
  sectionErrors,
  sectionFromHash,
  typedValues,
  valuesOf,
  type SectionKey,
  type Values,
} from "./settingsModel";

const card = "flex flex-col gap-4 rounded-lg border border-solid border-border bg-surface p-5 scroll-mt-6";
// B57: the section nearest the top of the window (scroll-spy) is highlighted.
const SPY_OFFSET = 120;

/**
 * `ready`: the sections are rendered. The section the operator chose (menu,
 * "#appearance" from the ⌘K palette) stays highlighted until they scroll
 * themselves; otherwise the section nearest the top is (scroll-spy).
 */
function useScrollSpy(ready: boolean): [SectionKey, (key: SectionKey) => void] {
  const [active, setActive] = useState<SectionKey>("general");
  const chosen = useRef<SectionKey | null>(null);
  useEffect(() => {
    if (!ready) return undefined;
    let frame = 0;
    const measure = () => {
      frame = 0;
      if (chosen.current) return;
      const tops = SECTIONS.map((key) => ({ key, top: document.getElementById(`settings-${key}`)?.getBoundingClientRect().top ?? Number.POSITIVE_INFINITY }));
      const atBottom = window.scrollY > 0 && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
      setActive(activeSection(tops, SPY_OFFSET, atBottom));
    };
    const schedule = () => {
      if (!frame) frame = window.requestAnimationFrame(measure);
    };
    // The operator scrolls: the spy follows again.
    const release = () => {
      chosen.current = null;
    };
    const target = sectionFromHash(window.location.hash);
    if (target) {
      chosen.current = target;
      setActive(target);
      document.getElementById(`settings-${target}`)?.scrollIntoView({ block: "start" });
    } else {
      measure();
    }
    // Late content (About, retention preview) changes the page height.
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    resize?.observe(document.body);
    window.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    for (const type of ["wheel", "touchmove", "keydown"]) window.addEventListener(type, release, { passive: true });
    return () => {
      resize?.disconnect();
      window.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      for (const type of ["wheel", "touchmove", "keydown"]) window.removeEventListener(type, release);
      if (frame) window.cancelAnimationFrame(frame);
    };
  }, [ready]);
  const go = (key: SectionKey) => {
    chosen.current = key;
    setActive(key);
    document.getElementById(`settings-${key}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
    window.history.replaceState(null, "", `#settings-${key}`);
  };
  return [active, go];
}
const input = "rounded-md border border-solid border-border-strong bg-bg px-2.5 py-1.5 text-sm text-fg outline-none focus:border-accent";
const primary =
  "flex w-fit items-center justify-center gap-1.5 rounded-md border-0 bg-accent px-3 py-1.5 text-[13px] font-medium text-accent-fg hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50";
const secondary =
  "flex w-fit items-center justify-center gap-1.5 rounded-md border border-solid border-border bg-transparent px-3 py-1.5 text-[13px] font-medium text-fg hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50";
const row = "grid gap-1.5 sm:grid-cols-[220px_minmax(0,1fr)] sm:items-start sm:gap-4";

type Editable = Exclude<SectionKey, "about">;

function Field({ label, help, error, children }: { label: string; help?: string; error?: string | null; children: ReactNode }) {
  return (
    <div className={row}>
      <span className="pt-1.5 text-[13px] text-muted">{label}</span>
      <div className="flex min-w-0 flex-col gap-1">
        {children}
        {help ? <span className="text-xs leading-[1.45] text-faint">{help}</span> : null}
        {error ? <span className="text-xs text-danger-text">{error}</span> : null}
      </div>
    </div>
  );
}

function Choice<T extends string>({ value, options, labels, onChange, name }: { value: T; options: readonly T[]; labels: Record<string, string>; onChange: (next: T) => void; name: string }) {
  return (
    <div role="radiogroup" aria-label={name} className="flex w-fit flex-wrap rounded-md border border-solid border-border p-0.5">
      {options.map((option) => (
        <button
          key={option}
          type="button"
          role="radio"
          aria-checked={value === option}
          onClick={() => onChange(option)}
          className={cn("rounded border-0 px-2.5 py-1 text-[13px]", value === option ? "bg-hover font-medium text-fg" : "bg-transparent text-muted hover:text-fg")}
        >
          {labels[option] ?? option}
        </button>
      ))}
    </div>
  );
}

/** Phase 5j: Settings, one section at a time (General, Appearance, Backups, Retention, Agents, About). */
export function SettingsView({ language, t }: { language: Language; t: TranslationDictionary }) {
  const text = t.settingsV2;
  const { toast } = useToast();
  const auth = useAuth();
  const appearance = useAppearance();
  const [saved, setSaved] = useState<Values | null>(null);
  const [draft, setDraft] = useState<Values>({});
  const [lang, setLang] = useState<UiLanguage>(auth.language);
  const [busy, setBusy] = useState<Editable | null>(null);
  const [preview, setPreview] = useState<RetentionPreview | null>(null);
  const [about, setAbout] = useState<AboutInfo | null>(null);
  const [aboutBusy, setAboutBusy] = useState(false);
  const zones = useMemo(() => supportedTimeZones(), []);
  const [active, goTo] = useScrollSpy(saved !== null);

  async function load() {
    const values = valuesOf(await getSettingsGroups(["general", "appearance", "backups", "retention", "agents"]));
    setSaved(values);
    setDraft(values);
  }

  useEffect(() => {
    load().catch((error) => toast({ tone: "danger", title: error instanceof Error ? error.message : t.error }));
    getAbout().then(setAbout, () => setAbout(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // "X runs would be deleted" for the values being edited.
  const policy = JSON.stringify(retentionPolicy(draft));
  useEffect(() => {
    if (!saved) return undefined;
    const timer = window.setTimeout(() => {
      previewRetention(JSON.parse(policy)).then(setPreview, () => setPreview(null));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [policy, saved]);

  const set = (key: string, value: unknown) => setDraft((current) => ({ ...current, [key]: value }));
  const errorText = (code: string | undefined) => (code ? (text.errors as Record<string, string>)[code] ?? code : null);

  async function save(section: Editable) {
    if (!saved) return;
    const values = typedValues(changed(section, saved, draft));
    const languageChanged = section === "general" && lang !== auth.language;
    if (!Object.keys(values).length && !languageChanged) return;
    setBusy(section);
    try {
      if (Object.keys(values).length) await patchSettings(values);
      if (languageChanged) {
        // B55: the account's language is what notifications are written in:
        // saved for sure (an error is shown), not fire-and-forget.
        await updateAccountLanguage(lang);
        auth.setLanguage(lang);
      }
      if (section === "general" || section === "appearance" || section === "backups") await appearance.reload();
      await load();
      toast({ tone: "success", title: fill(text.sectionSaved, { section: text.sections[section] }) });
    } catch (error) {
      toast({ tone: "danger", title: error instanceof Error ? error.message : t.error });
    } finally {
      setBusy(null);
    }
  }

  async function refreshAbout() {
    setAboutBusy(true);
    try {
      setAbout(await getAbout(true));
    } finally {
      setAboutBusy(false);
    }
  }

  if (!saved) {
    return (
      <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-6" data-settings-layout>
        <PageTitle tip={text.info}>{text.title}</PageTitle>
        <p className="m-0 flex items-center gap-2 text-[13px] text-faint">
          <Loader2 className="h-4 w-4 animate-spin" />
          {text.loading}
        </p>
      </div>
    );
  }

  const footer = (section: Editable) => {
    const errors = sectionErrors(section, draft);
    const dirty = Object.keys(changed(section, saved, draft)).length > 0 || (section === "general" && lang !== auth.language);
    return (
      <div className="flex items-center gap-3 border-0 border-t border-solid border-border pt-4">
        <button type="button" className={primary} disabled={!dirty || busy !== null || Object.keys(errors).length > 0} onClick={() => void save(section)} data-save-section={section}>
          {busy === section ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
          {text.save}
        </button>
        {dirty ? <span className="text-xs text-faint">{text.unsaved}</span> : null}
      </div>
    );
  };
  const prefs = prefsFromValues(draft);
  const dirtySections = saved
    ? SECTIONS.filter((key) => key !== "about" && (Object.keys(changed(key, saved, draft)).length > 0 || (key === "general" && lang !== auth.language)))
    : [];
  const general = sectionErrors("general", draft);
  const backups = sectionErrors("backups", draft);
  const retention = sectionErrors("retention", draft);

  return (
    // B57: centred, a readable width; sticky section menu on the left (lg+),
    // a list of sections on small screens.
    <div className="mx-auto flex w-full max-w-[1120px] flex-col gap-6" data-settings-layout>
      <PageTitle tip={text.info}>{text.title}</PageTitle>
      <div className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)] lg:gap-8">
        <nav aria-label={text.sectionsNav} className="lg:sticky lg:top-6 lg:self-start" data-settings-nav>
          <ul className="m-0 flex list-none flex-col divide-y divide-border overflow-hidden rounded-lg border border-solid border-border bg-surface p-0 lg:divide-y-0 lg:border-0 lg:bg-transparent">
            {SECTIONS.map((section) => (
              <li key={section}>
                <a
                  href={`#settings-${section}`}
                  onClick={(event) => {
                    event.preventDefault();
                    goTo(section);
                  }}
                  aria-current={active === section ? "location" : undefined}
                  data-active={active === section ? "" : undefined}
                  className={cn(
                    "flex items-center gap-2 px-3.5 py-3 text-[13px] no-underline lg:rounded-md lg:px-3 lg:py-1.5",
                    active === section
                      ? "font-medium text-fg lg:bg-subtle lg:shadow-[inset_2px_0_0_rgb(var(--pbo-accent))]"
                      : "text-muted hover:bg-hover hover:text-fg",
                  )}
                >
                  <span className="flex-1">{text.sections[section]}</span>
                  {dirtySections.includes(section) ? <span className="h-1.5 w-1.5 rounded-full bg-warning" aria-label={text.unsaved} /> : null}
                  <ChevronRight className="h-4 w-4 text-faint lg:hidden" aria-hidden />
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex min-w-0 flex-col gap-6">
          <section id="settings-general" className={card} aria-labelledby="settings-general-title" data-section="general">
            <h2 id="settings-general-title" className="m-0 text-[15px] font-semibold">{text.sections.general}</h2>
            <Field label={text.language}>
              <Choice value={lang} options={SUPPORTED_LANGUAGES} labels={text.languages} onChange={setLang} name={text.language} />
            </Field>
            <Field label={text.timezone} help={text.timezoneHelp} error={errorText(general.TIMEZONE)}>
              <select className={input} value={String(draft.TIMEZONE ?? "")} onChange={(event) => set("TIMEZONE", event.target.value)}>
                {(zones.includes(String(draft.TIMEZONE)) ? zones : [String(draft.TIMEZONE), ...zones]).map((zone) => (
                  <option key={zone} value={zone}>
                    {zone}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={text.dateFormat}>
              <Choice value={prefs.dateOrder} options={["auto", "dmy", "mdy", "ymd"] as const} labels={text.dateFormats} onChange={(value) => set("UI_DATE_FORMAT", value)} name={text.dateFormat} />
            </Field>
            <Field label={text.timeFormat}>
              <Choice value={prefs.timeFormat} options={["24h", "12h"] as const} labels={text.timeFormats} onChange={(value) => set("UI_TIME_FORMAT", value)} name={text.timeFormat} />
            </Field>
            <Field label={text.homePage}>
              <select className={input} value={prefs.homePage} onChange={(event) => set("UI_HOME_PAGE", event.target.value)}>
                {HOME_PAGES.map((page) => (
                  <option key={page} value={page}>
                    {text.homePages[page]}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={text.publicUrl} help={text.publicUrlHelp} error={errorText(general.PUBLIC_URL)}>
              <input className={input} value={String(draft.PUBLIC_URL ?? "")} placeholder="https://pbo.example.lan" onChange={(event) => set("PUBLIC_URL", event.target.value)} />
            </Field>
            {footer("general")}
          </section>

          <section id="settings-appearance" className={card} aria-labelledby="settings-appearance-title" data-section="appearance">
            <h2 id="settings-appearance-title" className="m-0 text-[15px] font-semibold">{text.sections.appearance}</h2>
            <Field label={text.theme}>
              <Choice value={prefs.theme} options={["system", "light", "dark"] as const} labels={text.themes} onChange={(value) => set("UI_THEME", value)} name={text.theme} />
            </Field>
            <Field label={text.accent} help={text.accentHelp}>
              <div role="radiogroup" aria-label={text.accent} className="flex flex-wrap gap-2">
                {ACCENTS.map((accent) => (
                  <button
                    key={accent}
                    type="button"
                    role="radio"
                    aria-checked={prefs.accent === accent}
                    aria-label={text.accents[accent]}
                    title={text.accents[accent]}
                    onClick={() => set("UI_ACCENT", accent)}
                    className={cn("h-7 w-7 rounded-full border-2 border-solid", prefs.accent === accent ? "border-fg" : "border-transparent")}
                    style={{ background: ACCENT_SWATCH[accent] }}
                    data-accent-choice={accent}
                  />
                ))}
              </div>
            </Field>
            <Field label={text.density}>
              <Choice value={prefs.density} options={["comfortable", "compact"] as const} labels={text.densities} onChange={(value) => set("UI_DENSITY", value)} name={text.density} />
            </Field>
            <Field label={text.sidebar}>
              <label className="flex items-center gap-2 pt-1.5 text-[13px] text-fg">
                <input type="checkbox" className="m-0 h-4 w-4 accent-accent" checked={prefs.sidebarCollapsed} onChange={(event) => set("UI_SIDEBAR_COLLAPSED", event.target.checked)} />
                {text.sidebarCollapsed}
              </label>
            </Field>
            <Field label={text.motion}>
              <Choice value={prefs.reduceMotion} options={["system", "on", "off"] as const} labels={text.motions} onChange={(value) => set("UI_REDUCE_MOTION", value)} name={text.motion} />
            </Field>
            {footer("appearance")}
          </section>

          <section id="settings-backups" className={card} aria-labelledby="settings-backups-title" data-section="backups">
            <h2 id="settings-backups-title" className="m-0 text-[15px] font-semibold">{text.sections.backups}</h2>
            <Field label={text.defaults} help={text.defaultsHelp}>
              <label className="flex items-center gap-2 pt-1.5 text-[13px] text-fg">
                <input type="checkbox" className="m-0 h-4 w-4 accent-accent" checked={prefs.defaultVerify} onChange={(event) => set("DEFAULT_VERIFY_AFTER_BACKUP", event.target.checked)} />
                {text.defaultVerify}
              </label>
              <label className="flex items-center gap-2 text-[13px] text-fg">
                <input type="checkbox" className="m-0 h-4 w-4 accent-accent" checked={prefs.defaultEject} onChange={(event) => set("DEFAULT_EJECT_AT_END", event.target.checked)} />
                {text.defaultEject}
              </label>
            </Field>
            <Field label={text.defaultRetention} help={text.defaultRetentionHelp} error={errorText(backups.DEFAULT_RETENTION_KEEP_LAST ?? backups.DEFAULT_RETENTION_KEEP_WEEKLY ?? backups.DEFAULT_RETENTION_KEEP_MONTHLY)}>
              <div className="flex flex-wrap gap-3">
                {(["DEFAULT_RETENTION_KEEP_LAST", "DEFAULT_RETENTION_KEEP_WEEKLY", "DEFAULT_RETENTION_KEEP_MONTHLY"] as const).map((key) => (
                  <label key={key} className="flex items-center gap-2 text-[13px] text-muted">
                    <input className={cn(input, "w-20 text-right")} inputMode="numeric" value={String(draft[key] ?? "")} onChange={(event) => set(key, event.target.value)} />
                    {text.keep[key]}
                  </label>
                ))}
              </div>
            </Field>
            <Field label={text.staleDays} help={text.staleDaysHelp} error={errorText(backups.ROTATION_STALE_DAYS)}>
              <input className={cn(input, "w-24 text-right")} inputMode="numeric" value={String(draft.ROTATION_STALE_DAYS ?? "")} onChange={(event) => set("ROTATION_STALE_DAYS", event.target.value)} />
            </Field>
            <Field label={text.minSize} help={text.minSizeHint} error={errorText(backups.MIN_DISK_SIZE_GIB)}>
              <input className={cn(input, "w-24 text-right")} inputMode="numeric" value={String(draft.MIN_DISK_SIZE_GIB ?? "")} onChange={(event) => set("MIN_DISK_SIZE_GIB", event.target.value)} />
            </Field>
            {footer("backups")}
          </section>

          <section id="settings-retention" className={card} aria-labelledby="settings-retention-title" data-section="retention">
            <h2 id="settings-retention-title" className="m-0 text-[15px] font-semibold">{text.sections.retention}</h2>
            <Field label={text.history}>
              <div className="flex flex-col gap-2 pt-1.5 text-[13px] text-fg" role="radiogroup" aria-label={text.history}>
                <label className="flex items-center gap-2">
                  <input type="radio" name="history" className="m-0 accent-accent" checked={draft.RUN_HISTORY_MODE === "keep_all"} onChange={() => set("RUN_HISTORY_MODE", "keep_all")} />
                  {text.historyKeepAll}
                </label>
                <label className="flex flex-wrap items-center gap-2">
                  <input type="radio" name="history" className="m-0 accent-accent" checked={draft.RUN_HISTORY_MODE === "days"} onChange={() => set("RUN_HISTORY_MODE", "days")} />
                  {text.historyDays}
                  <select className={input} aria-label={text.historyDays} value={String(draft.RUN_HISTORY_DAYS ?? 180)} disabled={draft.RUN_HISTORY_MODE !== "days"} onChange={(event) => set("RUN_HISTORY_DAYS", Number(event.target.value))}>
                    {HISTORY_DAYS.map((days) => (
                      <option key={days} value={days}>
                        {fill(text.days, { n: days })}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-wrap items-center gap-2">
                  <input type="radio" name="history" className="m-0 accent-accent" checked={draft.RUN_HISTORY_MODE === "per_disk"} onChange={() => set("RUN_HISTORY_MODE", "per_disk")} />
                  {text.historyPerDisk}
                  <input
                    className={cn(input, "w-20 text-right")}
                    inputMode="numeric"
                    aria-label={text.historyPerDisk}
                    disabled={draft.RUN_HISTORY_MODE !== "per_disk"}
                    value={String(draft.RUN_HISTORY_KEEP_PER_DISK ?? 50)}
                    onChange={(event) => set("RUN_HISTORY_KEEP_PER_DISK", event.target.value)}
                  />
                </label>
                {errorText(retention.RUN_HISTORY_KEEP_PER_DISK) ? <span className="text-xs text-danger-text">{errorText(retention.RUN_HISTORY_KEEP_PER_DISK)}</span> : null}
              </div>
            </Field>
            <Field label={text.logs} help={text.logsHelp}>
              <select className={cn(input, "w-fit")} value={String(draft.RUN_LOG_DETAIL_DAYS ?? 0)} onChange={(event) => set("RUN_LOG_DETAIL_DAYS", Number(event.target.value))}>
                {LOG_DAYS.map((days) => (
                  <option key={days} value={days}>
                    {days ? fill(text.logsAfter, { n: days }) : text.logsKeep}
                  </option>
                ))}
              </select>
            </Field>
            <p className="m-0 rounded-md bg-subtle px-3 py-2 text-[13px] text-muted" role="status" data-retention-preview>
              {preview ? fill(text.preview, { runs: preview.runs, logs: preview.logs }) : text.previewUnknown}
              <span className="block text-xs text-faint">{text.protectedHelp}</span>
            </p>
            {footer("retention")}
          </section>

          <section id="settings-agents" className={card} aria-labelledby="settings-agents-title" data-section="agents">
            <h2 id="settings-agents-title" className="m-0 text-[15px] font-semibold">{text.sections.agents}</h2>
            <Field label={text.autoUpdate} help={text.autoUpdateHint}>
              <label className="flex items-center gap-2 pt-1.5 text-[13px] text-fg">
                <input type="checkbox" className="m-0 h-4 w-4 accent-accent" checked={draft.AUTO_UPDATE_AGENTS !== false} onChange={(event) => set("AUTO_UPDATE_AGENTS", event.target.checked)} />
                {text.autoUpdateOn}
              </label>
              <Link to="/integrations" className="text-[13px] text-accent-text no-underline hover:underline">
                {text.agentsLink}
              </Link>
            </Field>
            {footer("agents")}
          </section>

          <section id="settings-about" className={card} aria-labelledby="settings-about-title" data-section="about">
            <div className="flex items-center gap-3">
              <h2 id="settings-about-title" className="m-0 flex-1 text-[15px] font-semibold">{text.sections.about}</h2>
              <button type="button" className={secondary} disabled={aboutBusy} onClick={() => void refreshAbout()}>
                {aboutBusy ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                {text.checkUpdates}
              </button>
            </div>
            {about ? (
              <>
                <Field label={text.version}>
                  <div className="flex flex-wrap items-center gap-3 pt-1.5 text-[13px]">
                    <span className="font-medium text-fg" data-version>{about.version}</span>
                    {about.update_available === true && about.latest ? (
                      <StatusDot tone="warn">{fill(text.updateAvailable, { version: about.latest.version })}</StatusDot>
                    ) : about.update_available === false ? (
                      <StatusDot tone="ok">{text.upToDate}</StatusDot>
                    ) : (
                      <StatusDot tone="off">{text.updateUnknown}</StatusDot>
                    )}
                  </div>
                  {about.update_available ? (
                    <span className="text-xs text-faint">
                      {text.updateHow} <code className="font-mono text-fg">{about.update_command}</code>
                    </span>
                  ) : null}
                </Field>
                <Field label={text.agentsVersions}>
                  {about.agents.length ? (
                    <ul className="m-0 flex list-none flex-col gap-1 p-0 pt-1.5 text-[13px]">
                      {about.agents.map((agent) => (
                        <li key={`${agent.role}-${agent.hostname}`} className="flex flex-wrap items-center gap-2">
                          <span className="text-fg">{fill(text.agentLine, { role: agent.role, host: agent.hostname })}</span>
                          <span className="text-muted">{agent.version ?? "—"}</span>
                          <StatusDot tone={agent.up_to_date ? "ok" : "warn"}>{agent.up_to_date ? text.upToDate : text.agentOutdated}</StatusDot>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <span className="pt-1.5 text-[13px] text-faint">{text.noAgents}</span>
                  )}
                </Field>
                <Field label={text.links}>
                  <div className="flex flex-wrap gap-4 pt-1.5 text-[13px]">
                    {(["docs", "repository", "releases"] as const).map((key) => (
                      <a key={key} href={about.links[key]} target="_blank" rel="noreferrer" className="flex items-center gap-1 text-accent-text no-underline hover:underline">
                        {text.linkLabels[key]}
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    ))}
                  </div>
                </Field>
              </>
            ) : (
              <p className="m-0 text-[13px] text-faint">{text.aboutUnavailable}</p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}
