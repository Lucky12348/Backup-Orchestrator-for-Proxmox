import { useState } from "react";
import { Loader2, Plus, Send, Trash2 } from "lucide-react";

import { testNotificationDraft, type NotificationCatalog, type NotificationKind, type NotificationTestResult } from "../../api";
import { cn } from "../../components/ui/utils";
import type { TranslationDictionary } from "../../i18n";
import { ApiError } from "../../notices";
import { StatusIcon } from "../../ui/StatusIcon";
import { FORMS, KINDS, LEVELS, draftErrors, groupEvents, toPayload, toggle, type ChannelDraft } from "./notificationsModel";

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

/**
 * Phase 5j: one channel (Notifications page and the wizard's optional step):
 * the dedicated form of its kind, the events, minimum level, quiet hours,
 * daily summary, and "Send a test" with its result.
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
  serverErrors?: Record<string, string>;
  compact?: boolean;
}) {
  const text = t.notificationsV2;
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<NotificationTestResult | null>(null);
  // Errors show once a field was edited (or the API refused it), not on an empty new form.
  const [touched, setTouched] = useState<Set<string>>(() => new Set(draft.id !== null ? ["*"] : []));
  const touch = (...fields: string[]) => setTouched((current) => new Set([...current, ...fields]));
  const all = { ...draftErrors(draft), ...serverErrors };
  const errors = Object.fromEntries(Object.entries(all).filter(([field]) => touched.has("*") || touched.has(field) || field in serverErrors));
  const set = (patch: Partial<ChannelDraft>) => {
    setResult(null);
    onChange({ ...draft, ...patch });
  };
  const setValue = (name: string, value: string) => {
    touch(name);
    set({ values: { ...draft.values, [name]: value } });
  };
  const errorText = (field: string) => (errors[field] ? (text.errors as Record<string, string>)[errors[field]] ?? text.errors.unknown : null);

  async function sendTest() {
    setTesting(true);
    setResult(null);
    try {
      setResult(await testNotificationDraft(toPayload(draft)));
    } catch (error) {
      const { code } = apiErrorCode(error);
      setResult({ ok: false, error: (text.errors as Record<string, string>)[code] ?? code });
    } finally {
      setTesting(false);
    }
  }

  const fieldHelp = text.fieldHelp as Record<string, string>;
  const kindHelp = text.kindHelp as Record<string, string>;
  return (
    <div className="flex flex-col gap-4" data-channel-form={draft.kind}>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className={label}>
          {text.name}
          <input className={input} value={draft.name} maxLength={120} placeholder={text.kinds[draft.kind]} onChange={(event) => (touch("name"), set({ name: event.target.value }))} />
          {errorText("name") ? <span className="text-xs text-danger-text">{errorText("name")}</span> : null}
        </label>
        <label className={label}>
          {text.kind}
          <select className={input} value={draft.kind} disabled={kindLocked} onChange={(event) => onChange({ ...draft, kind: event.target.value as NotificationKind, values: {}, storedSecrets: [], clearSecrets: [], headers: [] })}>
            {KINDS.map((kind) => (
              <option key={kind} value={kind}>
                {text.kinds[kind]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {kindHelp[draft.kind] ? <p className="m-0 text-[13px] leading-[1.45] text-muted" data-kind-help>{kindHelp[draft.kind]}</p> : null}

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
                <button type="button" className="w-fit border-0 bg-transparent p-0 text-xs text-muted underline hover:text-fg" onClick={() => set({ clearSecrets: toggle(draft.clearSecrets, field.name, true), values: { ...draft.values, [field.name]: "" } })}>
                  {text.secretRemove}
                </button>
              ) : null}
              {fieldHelp[`${draft.kind}.${field.name}`] ? <span className="text-xs leading-[1.45] text-faint">{fieldHelp[`${draft.kind}.${field.name}`]}</span> : null}
              {errorText(field.name) ? <span className="text-xs text-danger-text">{errorText(field.name)}</span> : null}
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
          {errorText("headers") ? <span className="text-xs text-danger-text">{errorText("headers")}</span> : null}
        </fieldset>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" className={secondary} disabled={testing || Object.keys(draftErrors(draft)).some((key) => key !== "events")} onClick={() => void sendTest()} data-test-channel>
          {testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
          {text.test}
        </button>
        {result ? (
          <span className={cn("flex items-center gap-2 text-[13px]", result.ok ? "text-success-text" : "text-danger-text")} role="status" data-test-result={result.ok ? "ok" : "failed"}>
            <StatusIcon kind={result.ok ? "success" : "failed"} />
            {result.ok ? text.testOk : `${text.testFailed} ${result.error ?? ""}`}
          </span>
        ) : null}
      </div>

      {compact ? null : (
        <>
          <fieldset className="m-0 flex flex-col gap-2 border-0 border-t border-solid border-border p-0 pt-4" data-events>
            <legend className="float-left mb-2 p-0 text-[13px] font-medium text-fg">{text.events}</legend>
            <div className="clear-both grid gap-x-6 gap-y-3 sm:grid-cols-2">
              {groupEvents(catalog.events, catalog.groups).map(({ group, keys }) => (
                <div key={group} className="flex flex-col gap-1.5">
                  <span className="text-xs text-faint">{(text.groups as Record<string, string>)[group] ?? group}</span>
                  {keys.map((key) => (
                    <label key={key} className="flex items-center gap-2 text-[13px] text-fg">
                      <input type="checkbox" className="m-0 h-4 w-4 accent-accent" checked={draft.events.includes(key)} onChange={(event) => set({ events: toggle(draft.events, key, event.target.checked) })} />
                      {(text.eventLabels as Record<string, string>)[key] ?? key}
                    </label>
                  ))}
                </div>
              ))}
            </div>
            {errorText("events") ? <span className="text-xs text-danger-text">{errorText("events")}</span> : null}
          </fieldset>

          <div className="grid gap-4 border-0 border-t border-solid border-border pt-4 sm:grid-cols-3">
            <label className={label}>
              {text.minLevel}
              <select className={input} value={draft.minLevel} onChange={(event) => set({ minLevel: event.target.value as ChannelDraft["minLevel"] })}>
                {LEVELS.map((level) => (
                  <option key={level} value={level}>
                    {text.levels[level]}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex flex-col gap-1 text-[13px] text-muted">
              <label className="flex items-center gap-2 text-fg">
                <input type="checkbox" className="m-0 h-4 w-4 accent-accent" checked={draft.quiet} onChange={(event) => set({ quiet: event.target.checked })} />
                {text.quiet}
              </label>
              <div className="flex items-center gap-2">
                <input className={cn(input, "w-24")} type="time" aria-label={text.quietFrom} disabled={!draft.quiet} value={draft.quietStart} onChange={(event) => set({ quietStart: event.target.value })} />
                <span aria-hidden>–</span>
                <input className={cn(input, "w-24")} type="time" aria-label={text.quietTo} disabled={!draft.quiet} value={draft.quietEnd} onChange={(event) => set({ quietEnd: event.target.value })} />
              </div>
              <span className="text-xs text-faint">{text.quietHelp}</span>
              {errorText("quiet") ? <span className="text-xs text-danger-text">{errorText("quiet")}</span> : null}
            </div>
            <div className="flex flex-col gap-1 text-[13px] text-muted">
              <label className="flex items-center gap-2 text-fg">
                <input type="checkbox" className="m-0 h-4 w-4 accent-accent" checked={draft.digest} onChange={(event) => set({ digest: event.target.checked })} />
                {text.digest}
              </label>
              <input className={cn(input, "w-24")} type="time" aria-label={text.digestAt} disabled={!draft.digest} value={draft.digestTime} onChange={(event) => set({ digestTime: event.target.value })} />
              <span className="text-xs text-faint">{text.digestHelp}</span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
