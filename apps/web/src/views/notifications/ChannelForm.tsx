import { useState } from "react";
import { ChevronRight, Loader2, Plus, Send, Trash2 } from "lucide-react";

import { testNotificationDraft, type NotificationCatalog, type NotificationKind, type NotificationTestResult } from "../../api";
import { cn } from "../../components/ui/utils";
import type { TranslationDictionary } from "../../i18n";
import { ApiError } from "../../notices";
import { StatusIcon } from "../../ui/StatusIcon";
import { ChannelLogo } from "./ChannelLogo";
import {
  FORMS,
  KINDS,
  LEVELS,
  connectionKey,
  draftErrors,
  eventsForPreset,
  groupEvents,
  presetOf,
  toPayload,
  toggle,
  type ChannelDraft,
  type EventPreset,
} from "./notificationsModel";

export const input = "w-full rounded-md border border-solid border-border-strong bg-bg px-2.5 py-1.5 text-sm text-fg outline-none focus:border-accent";
export const secondary =
  "flex items-center justify-center gap-1.5 rounded-md border border-solid border-border bg-transparent px-3 py-1.5 text-[13px] font-medium text-fg hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50";
export const primary =
  "flex items-center justify-center gap-1.5 rounded-md border-0 bg-accent px-3 py-1.5 text-[13px] font-medium text-accent-fg hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50";
const label = "flex flex-col gap-1 text-[13px] text-muted";

/** The error code of an API answer ({code, field}) or of a thrown error. */
export function apiErrorCode(error: unknown): { code: string; field?: string } {
  if (error instanceof ApiError) {
    // A {code, field} detail without a message: request() keeps the raw body.
    try {
      const body = JSON.parse(error.message) as { detail?: { code?: string; field?: string } };
      if (body?.detail?.code) return { code: body.detail.code, field: body.detail.field };
    } catch {
      /* not JSON */
    }
    return { code: error.code ?? "unknown" };
  }
  return { code: "unknown" };
}

type Errors = Record<string, string>;

/** Errors shown once a field was edited (or the API refused it), not on an empty new form. */
function useTouched(draft: ChannelDraft, serverErrors: Errors) {
  const [touched, setTouched] = useState<Set<string>>(() => new Set(draft.id !== null ? ["*"] : []));
  const touch = (...fields: string[]) => setTouched((current) => new Set([...current, ...fields]));
  const all = { ...draftErrors(draft), ...serverErrors };
  const errors = Object.fromEntries(Object.entries(all).filter(([field]) => touched.has("*") || touched.has(field) || field in serverErrors));
  return { touch, errors, touchAll: () => touch("*") };
}

function errorLine(t: TranslationDictionary, errors: Errors, field: string) {
  const text = t.notificationsV2;
  if (!errors[field]) return null;
  return <span className="text-xs text-danger-text">{(text.errors as Record<string, string>)[errors[field]] ?? text.errors.unknown}</span>;
}

/** "Send a test" of a draft; `testedKey` = the connection that answered (B55: required before continuing). */
export function useDraftTest(t: TranslationDictionary) {
  const text = t.notificationsV2;
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<NotificationTestResult | null>(null);
  const [testedKey, setTestedKey] = useState<string | null>(null);
  async function run(draft: ChannelDraft) {
    setTesting(true);
    setResult(null);
    try {
      const answer = await testNotificationDraft(toPayload(draft));
      setResult(answer);
      setTestedKey(answer.ok ? connectionKey(draft) : null);
    } catch (error) {
      const { code } = apiErrorCode(error);
      setResult({ ok: false, error: (text.errors as Record<string, string>)[code] ?? code });
      setTestedKey(null);
    } finally {
      setTesting(false);
    }
  }
  return { testing, result, testedKey, run, reset: () => setResult(null) };
}

export function TestResult({ result, t }: { result: NotificationTestResult | null; t: TranslationDictionary }) {
  const text = t.notificationsV2;
  if (!result) return null;
  return (
    <span className={cn("flex items-center gap-2 text-[13px]", result.ok ? "text-success-text" : "text-danger-text")} role="status" data-test-result={result.ok ? "ok" : "failed"}>
      <StatusIcon kind={result.ok ? "success" : "failed"} />
      <span className="[overflow-wrap:anywhere]">{result.ok ? text.testOk : `${text.testFailed} ${result.error ?? ""}`}</span>
    </span>
  );
}

/** Name, service and its dedicated fields (+ webhook headers). */
export function ConnectionFields({
  draft,
  onChange,
  t,
  kindLocked = false,
  serverErrors = {},
  showKind = true,
}: {
  draft: ChannelDraft;
  onChange: (next: ChannelDraft) => void;
  t: TranslationDictionary;
  kindLocked?: boolean;
  serverErrors?: Errors;
  showKind?: boolean;
}) {
  const text = t.notificationsV2;
  const { touch, errors } = useTouched(draft, serverErrors);
  const set = (patch: Partial<ChannelDraft>) => onChange({ ...draft, ...patch });
  const setValue = (name: string, value: string) => {
    touch(name);
    set({ values: { ...draft.values, [name]: value } });
  };
  const fieldHelp = text.fieldHelp as Record<string, string>;
  const kindHelp = text.kindHelp as Record<string, string>;
  return (
    <div className="flex flex-col gap-4" data-channel-form={draft.kind}>
      <div className={cn("grid gap-3", showKind && "sm:grid-cols-2")}>
        <label className={label}>
          {text.name}
          <input className={input} value={draft.name} maxLength={120} placeholder={text.kinds[draft.kind]} onChange={(event) => (touch("name"), set({ name: event.target.value }))} />
          {errorLine(t, errors, "name")}
        </label>
        {showKind ? (
          <label className={label}>
            {text.kind}
            <select
              className={input}
              value={draft.kind}
              disabled={kindLocked}
              onChange={(event) => onChange({ ...draft, kind: event.target.value as NotificationKind, values: {}, storedSecrets: [], clearSecrets: [], headers: [] })}
            >
              {KINDS.map((kind) => (
                <option key={kind} value={kind}>
                  {text.kinds[kind]}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>
      {kindHelp[draft.kind] ? (
        <p className="m-0 text-[13px] leading-[1.45] text-muted" data-kind-help>
          {kindHelp[draft.kind]}
        </p>
      ) : null}

      <div className="grid gap-3 sm:grid-cols-2">
        {FORMS[draft.kind].map((field) => {
          const stored = field.type === "secret" && draft.storedSecrets.includes(field.name) && !draft.clearSecrets.includes(field.name);
          const name = (text.fields as Record<string, string>)[field.name] ?? field.name;
          return (
            <label key={field.name} className={cn(label, (field.name === "webhook_url" || field.name === "url" || field.name === "to_addresses") && "sm:col-span-2")}>
              <span className="flex items-center gap-2">
                {name}
                {field.required ? null : <span className="text-xs text-faint">{text.optional}</span>}
              </span>
              {field.type === "select" ? (
                <select className={input} value={draft.values[field.name] ?? field.choices?.[0]} onChange={(event) => setValue(field.name, event.target.value)}>
                  {field.choices?.map((choice) => (
                    <option key={choice} value={choice}>
                      {(text.security as Record<string, string>)[choice] ?? choice}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  className={cn(input, field.type === "secret" && "font-mono")}
                  type={field.type === "secret" ? "password" : field.type === "number" ? "number" : "text"}
                  autoComplete={field.type === "secret" ? "new-password" : "off"}
                  value={draft.values[field.name] ?? ""}
                  placeholder={stored ? text.secretSet : field.example}
                  onChange={(event) => setValue(field.name, event.target.value)}
                  data-secret={field.type === "secret" ? (stored ? "set" : "unset") : undefined}
                />
              )}
              {stored ? (
                <button
                  type="button"
                  className="w-fit border-0 bg-transparent p-0 text-xs text-muted underline hover:text-fg"
                  onClick={() => set({ clearSecrets: toggle(draft.clearSecrets, field.name, true), values: { ...draft.values, [field.name]: "" } })}
                >
                  {text.secretRemove}
                </button>
              ) : null}
              {fieldHelp[`${draft.kind}.${field.name}`] ? <span className="text-xs leading-[1.45] text-faint">{fieldHelp[`${draft.kind}.${field.name}`]}</span> : null}
              {errorLine(t, errors, field.name)}
            </label>
          );
        })}
      </div>

      {draft.kind === "webhook" ? (
        <fieldset className="m-0 flex flex-col gap-2 border-0 p-0" data-headers>
          <legend className="mb-1 p-0 text-[13px] text-muted">{text.headers}</legend>
          {draft.headers.map((row, index) => (
            <div key={index} className="flex gap-2">
              <input
                className={cn(input, "w-2/5")}
                aria-label={text.headerName}
                placeholder="Authorization"
                value={row.name}
                onChange={(event) => set({ headers: draft.headers.map((item, i) => (i === index ? { ...item, name: event.target.value } : item)) })}
              />
              <input
                className={cn(input, "font-mono")}
                type="password"
                autoComplete="new-password"
                aria-label={text.headerValue}
                placeholder={row.stored ? text.secretSet : "Bearer …"}
                value={row.value}
                onChange={(event) => set({ headers: draft.headers.map((item, i) => (i === index ? { ...item, value: event.target.value } : item)) })}
              />
              <button type="button" className={secondary} aria-label={text.headerRemove} title={text.headerRemove} onClick={() => set({ headers: draft.headers.filter((_, i) => i !== index) })}>
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
          <button type="button" className={cn(secondary, "w-fit")} onClick={() => set({ headers: [...draft.headers, { name: "", value: "", stored: false }] })}>
            <Plus className="h-4 w-4" />
            {text.headerAdd}
          </button>
          <span className="text-xs leading-[1.45] text-faint">{text.webhookPayload}</span>
          {errorLine(t, errors, "headers")}
        </fieldset>
      ) : null}
    </div>
  );
}

/** Events: presets Essential / All / Custom, groups that fold (B55). */
export function EventsPicker({ draft, onChange, catalog, t }: { draft: ChannelDraft; onChange: (next: ChannelDraft) => void; catalog: NotificationCatalog; t: TranslationDictionary }) {
  const text = t.notificationsV2;
  const all = catalog.events.map((event) => event.key);
  const preset = presetOf(draft.events, all);
  const [custom, setCustom] = useState(preset === "custom");
  const shown: EventPreset = custom ? "custom" : preset;
  const choose = (next: EventPreset) => {
    setCustom(next === "custom");
    if (next !== "custom") onChange({ ...draft, events: eventsForPreset(next, all) });
  };
  const labels = text.eventLabels as Record<string, string>;
  return (
    <fieldset className="m-0 flex flex-col gap-3 border-0 p-0" data-events>
      <legend className="sr-only">{text.events}</legend>
      <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label={text.events}>
        {(["essential", "all", "custom"] as EventPreset[]).map((key) => (
          <label
            key={key}
            className={cn(
              "flex cursor-pointer flex-col gap-0.5 rounded-lg border border-solid px-3 py-2.5",
              shown === key ? "border-accent bg-accent/5" : "border-border hover:bg-hover",
            )}
            data-preset={key}
          >
            <span className="flex items-center gap-2 text-[13px] font-medium text-fg">
              <input type="radio" name="event-preset" className="m-0 h-4 w-4 accent-accent" checked={shown === key} onChange={() => choose(key)} />
              {text.presets[key]}
            </span>
            <span className="pl-6 text-xs leading-[1.4] text-muted">{text.presetHelp[key]}</span>
          </label>
        ))}
      </div>
      <div className="flex flex-col divide-y divide-border rounded-lg border border-solid border-border">
        {groupEvents(catalog.events, catalog.groups).map(({ group, keys }) => {
          const chosen = keys.filter((key) => draft.events.includes(key)).length;
          return (
            <details key={group} open={shown === "custom"} className="group/events" data-event-group={group}>
              <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2.5 text-[13px] text-fg [&::-webkit-details-marker]:hidden">
                <ChevronRight className="h-4 w-4 text-muted transition-transform group-open/events:rotate-90" aria-hidden />
                <span className="flex-1 font-medium">{(text.groups as Record<string, string>)[group] ?? group}</span>
                <span className="text-xs tabular-nums text-muted">{`${chosen}/${keys.length}`}</span>
              </summary>
              <div className="grid gap-x-6 gap-y-2 px-3 pb-3 pl-9 sm:grid-cols-2">
                {keys.map((key) => (
                  <label key={key} className="flex items-center gap-2 text-[13px] text-fg">
                    <input
                      type="checkbox"
                      className="m-0 h-4 w-4 accent-accent"
                      checked={draft.events.includes(key)}
                      onChange={(event) => (setCustom(true), onChange({ ...draft, events: toggle(draft.events, key, event.target.checked) }))}
                    />
                    {labels[key] ?? key}
                  </label>
                ))}
              </div>
            </details>
          );
        })}
      </div>
      {draft.events.length === 0 ? <span className="text-xs text-danger-text">{text.errors.invalid_events}</span> : null}
    </fieldset>
  );
}

/** Minimum level, quiet hours, daily summary. */
export function WhenFields({ draft, onChange, t }: { draft: ChannelDraft; onChange: (next: ChannelDraft) => void; t: TranslationDictionary }) {
  const text = t.notificationsV2;
  const set = (patch: Partial<ChannelDraft>) => onChange({ ...draft, ...patch });
  const quietError = draftErrors(draft).quiet;
  return (
    <div className="flex flex-col gap-5" data-when>
      <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
        <legend className="mb-1 p-0 text-[13px] font-medium text-fg">{text.minLevel}</legend>
        <div className="grid gap-2 sm:grid-cols-3" role="radiogroup" aria-label={text.minLevel}>
          {LEVELS.map((level) => (
            <label
              key={level}
              className={cn("flex cursor-pointer flex-col gap-0.5 rounded-lg border border-solid px-3 py-2.5", draft.minLevel === level ? "border-accent bg-accent/5" : "border-border hover:bg-hover")}
            >
              <span className="flex items-center gap-2 text-[13px] font-medium text-fg">
                <input type="radio" name="min-level" className="m-0 h-4 w-4 accent-accent" checked={draft.minLevel === level} onChange={() => set({ minLevel: level })} />
                {text.levels[level]}
              </span>
              <span className="pl-6 text-xs leading-[1.4] text-muted">{text.levelHelp[level]}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="grid gap-5 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5 text-[13px] text-muted">
          <label className="flex items-center gap-2 font-medium text-fg">
            <input type="checkbox" className="m-0 h-4 w-4 accent-accent" checked={draft.quiet} onChange={(event) => set({ quiet: event.target.checked })} />
            {text.quiet}
          </label>
          <div className="flex items-center gap-2">
            <input className={cn(input, "w-28")} type="time" aria-label={text.quietFrom} disabled={!draft.quiet} value={draft.quietStart} onChange={(event) => set({ quietStart: event.target.value })} />
            <span aria-hidden>–</span>
            <input className={cn(input, "w-28")} type="time" aria-label={text.quietTo} disabled={!draft.quiet} value={draft.quietEnd} onChange={(event) => set({ quietEnd: event.target.value })} />
          </div>
          <span className="text-xs leading-[1.45] text-faint">{text.quietHelp}</span>
          {draft.quiet && quietError ? <span className="text-xs text-danger-text">{(text.errors as Record<string, string>)[quietError]}</span> : null}
        </div>
        <div className="flex flex-col gap-1.5 text-[13px] text-muted">
          <label className="flex items-center gap-2 font-medium text-fg">
            <input type="checkbox" className="m-0 h-4 w-4 accent-accent" checked={draft.digest} onChange={(event) => set({ digest: event.target.checked })} />
            {text.digest}
          </label>
          <input className={cn(input, "w-28")} type="time" aria-label={text.digestAt} disabled={!draft.digest} value={draft.digestTime} onChange={(event) => set({ digestTime: event.target.value })} />
          <span className="text-xs leading-[1.45] text-faint">{text.digestHelp}</span>
        </div>
      </div>
    </div>
  );
}

/** The service grid of the assistant's first step (logos, monochrome). */
export function ServiceGrid({ value, onChoose, t }: { value: NotificationKind | null; onChoose: (kind: NotificationKind) => void; t: TranslationDictionary }) {
  const text = t.notificationsV2;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4" role="radiogroup" aria-label={text.kind} data-service-grid>
      {KINDS.map((kind) => (
        <button
          key={kind}
          type="button"
          role="radio"
          aria-checked={value === kind}
          onClick={() => onChoose(kind)}
          data-service={kind}
          className={cn(
            "flex flex-col items-start gap-2 rounded-lg border border-solid bg-transparent px-3 py-3 text-left text-fg hover:bg-hover",
            value === kind ? "border-accent bg-accent/5" : "border-border",
            kind === "apprise" && "sm:col-span-2",
          )}
        >
          <ChannelLogo kind={kind} className="h-6 w-6" />
          <span className="text-[13px] font-medium">{text.kinds[kind]}</span>
          <span className="text-xs leading-[1.4] text-muted">{text.kindShort[kind]}</span>
        </button>
      ))}
    </div>
  );
}

/**
 * Phase 5j: one channel in one form (the setup wizard's optional step):
 * connection + "Send a test", and with `compact` off, events and when.
 */
export function ChannelForm({
  draft,
  onChange,
  catalog,
  t,
  kindLocked = false,
  serverErrors = {},
  compact = false,
}: {
  draft: ChannelDraft;
  onChange: (next: ChannelDraft) => void;
  catalog: NotificationCatalog;
  t: TranslationDictionary;
  kindLocked?: boolean;
  serverErrors?: Errors;
  compact?: boolean;
}) {
  const text = t.notificationsV2;
  const test = useDraftTest(t);
  const update = (next: ChannelDraft) => {
    test.reset();
    onChange(next);
  };
  return (
    <div className="flex flex-col gap-4">
      <ConnectionFields draft={draft} onChange={update} t={t} kindLocked={kindLocked} serverErrors={serverErrors} />
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          className={secondary}
          disabled={test.testing || Object.keys(draftErrors(draft)).some((key) => key !== "events")}
          onClick={() => void test.run(draft)}
          data-test-channel
        >
          {test.testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {text.test}
        </button>
        <TestResult result={test.result} t={t} />
      </div>
      {compact ? null : (
        <>
          <div className="border-0 border-t border-solid border-border pt-4">
            <EventsPicker draft={draft} onChange={update} catalog={catalog} t={t} />
          </div>
          <div className="border-0 border-t border-solid border-border pt-4">
            <WhenFields draft={draft} onChange={update} t={t} />
          </div>
        </>
      )}
    </div>
  );
}
