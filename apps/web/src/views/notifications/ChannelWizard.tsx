import { useEffect, useState } from "react";
import { AlertTriangle, ArrowLeft, Check, Loader2, Send } from "lucide-react";

import { createNotificationChannel, updateNotificationChannel, type NotificationCatalog, type NotificationChannel } from "../../api";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../../components/ui/dialog";
import { cn } from "../../components/ui/utils";
import type { TranslationDictionary } from "../../i18n";
import { CloseButton } from "../../ui/CloseButton";
import { fill } from "../format";
import { ChannelLogo } from "./ChannelLogo";
import { ConnectionFields, EventsPicker, ServiceGrid, TestResult, WhenFields, apiErrorCode, primary, secondary, useDraftTest } from "./ChannelForm";
import {
  WIZARD_STEPS,
  channelTarget,
  connectionKey,
  draftErrors,
  draftForKind,
  draftFromChannel,
  presetOf,
  stepErrors,
  toPayload,
  type ChannelDraft,
  type WizardStep,
} from "./notificationsModel";

const STEP_OF_FIELD = (field: string): WizardStep => (field === "events" ? "events" : field === "quiet" || field === "min_level" || field === "digest_time" ? "when" : "connection");

/**
 * B55: a channel in five steps — service (logos), connection + a test that
 * must answer before going on (or skipped, confirmed), events (presets,
 * folding groups), when (level, quiet hours, summary), summary. Editing a
 * channel opens the same dialog on the step chosen, every step reachable.
 */
export function ChannelWizard({
  open,
  catalog,
  channel,
  initialStep,
  onClose,
  onSaved,
  t,
}: {
  open: boolean;
  catalog: NotificationCatalog;
  channel: NotificationChannel | null;
  initialStep: WizardStep;
  onClose: () => void;
  onSaved: (channel: NotificationChannel, created: boolean) => void;
  t: TranslationDictionary;
}) {
  const text = t.notificationsV2;
  const editing = channel !== null;
  const allEvents = catalog.events.map((event) => event.key);
  const [draft, setDraft] = useState<ChannelDraft | null>(channel ? draftFromChannel(channel) : null);
  const [step, setStep] = useState<WizardStep>(initialStep);
  const [reached, setReached] = useState(editing ? WIZARD_STEPS.length - 1 : WIZARD_STEPS.indexOf(initialStep));
  const [skipAsked, setSkipAsked] = useState(false);
  const [skipped, setSkipped] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [originalKey] = useState(() => (channel ? connectionKey(draftFromChannel(channel)) : null));
  const test = useDraftTest(t);

  useEffect(() => {
    if (!open) return;
    setDraft(channel ? draftFromChannel(channel) : null);
    setStep(initialStep);
    setReached(channel ? WIZARD_STEPS.length - 1 : WIZARD_STEPS.indexOf(initialStep));
    setSkipAsked(false);
    setSkipped(null);
    setServerErrors({});
    test.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, channel, initialStep]);

  const index = WIZARD_STEPS.indexOf(step);
  const go = (next: WizardStep) => {
    setStep(next);
    setReached((current) => Math.max(current, WIZARD_STEPS.indexOf(next)));
    setSkipAsked(false);
  };
  const key = draft ? connectionKey(draft) : "";
  // The connection answered (test), was left as it was (edit), or the test was skipped knowingly.
  const connectionOk = Boolean(draft && (test.testedKey === key || (editing && key === originalKey) || skipped === key));
  const blocking = draft ? stepErrors(step, draft) : {};
  // Skipping the test never skips the required fields.
  const fieldsOk = draft !== null && Object.keys(stepErrors("connection", draft)).length === 0;

  const update = (next: ChannelDraft) => {
    setServerErrors({});
    setDraft(next);
  };

  async function save() {
    if (!draft || Object.keys(draftErrors(draft)).length) return;
    setSaving(true);
    setServerErrors({});
    try {
      const payload = toPayload(draft);
      const saved = draft.id === null ? await createNotificationChannel(payload) : await updateNotificationChannel(draft.id, payload);
      onSaved(saved, draft.id === null);
    } catch (error) {
      const { code, field } = apiErrorCode(error);
      setServerErrors({ [field ?? "name"]: code });
      setStep(STEP_OF_FIELD(field ?? "name"));
    } finally {
      setSaving(false);
    }
  }

  const next = () => {
    if (step === "summary") void save();
    else go(WIZARD_STEPS[index + 1]);
  };

  const canGoOn = draft !== null && Object.keys(blocking).length === 0 && (step !== "connection" || connectionOk);

  return (
    <Dialog open={open} onOpenChange={(value) => (!value ? onClose() : undefined)}>
      {open ? (
        <DialogContent
          className="max-w-[820px]"
          aria-describedby="channel-wizard-help"
          data-channel-wizard={step}
          onOpenAutoFocus={(event) => {
            // The first field of the step, not the X (no focus ring on the close button).
            event.preventDefault();
            (event.currentTarget as HTMLElement | null)?.querySelector<HTMLElement>("[data-step-body] input, [data-step-body] button")?.focus();
          }}
        >
          <div className="flex items-center gap-3 border-0 border-b border-solid border-border px-5 py-3.5">
            {draft ? <ChannelLogo kind={draft.kind} className="text-muted" /> : null}
            <DialogTitle className="m-0 flex-1 truncate text-base font-semibold">
              {editing ? fill(text.editTitle, { name: channel?.name ?? "" }) : text.addTitle}
            </DialogTitle>
            <CloseButton label={t.shell.close} onClick={onClose} />
          </div>

          <ol className="m-0 flex list-none gap-1 overflow-x-auto border-0 border-b border-solid border-border px-5 py-2.5" aria-label={text.wizard.stepsLabel}>
            {WIZARD_STEPS.map((item, position) => {
              const reachable = draft !== null && position <= reached && (item !== "service" || !editing);
              const current = item === step;
              return (
                <li key={item} className="flex-none">
                  <button
                    type="button"
                    disabled={!reachable || current}
                    onClick={() => go(item)}
                    aria-current={current ? "step" : undefined}
                    className={cn(
                      "flex items-center gap-2 rounded-md border-0 bg-transparent px-2 py-1 text-[13px]",
                      current ? "font-medium text-fg" : reachable ? "text-muted hover:bg-hover hover:text-fg" : "text-faint",
                      "disabled:cursor-default",
                    )}
                    data-wizard-step={item}
                  >
                    <span
                      className={cn(
                        "flex h-5 w-5 items-center justify-center rounded-full text-[11px] tabular-nums",
                        current ? "bg-accent text-accent-fg" : position < index || (editing && reachable) ? "bg-subtle text-fg" : "border border-solid border-border text-faint",
                      )}
                    >
                      {position < index && !editing ? <Check className="h-3 w-3" /> : position + 1}
                    </span>
                    {/* Small screens: only the current step is named, every step stays visible. */}
                    <span className={cn(!current && "sr-only sm:not-sr-only")}>{text.wizard.steps[item]}</span>
                  </button>
                </li>
              );
            })}
          </ol>

          <div className="flex min-h-[320px] flex-col gap-4 overflow-y-auto px-5 py-4" data-step-body>
            <DialogDescription id="channel-wizard-help" className="m-0 text-[13px] leading-[1.45] text-muted">
              {text.wizard.help[step]}
            </DialogDescription>

            {step === "service" ? (
              <ServiceGrid
                value={draft?.kind ?? null}
                onChoose={(kind) => {
                  const fresh = draft === null || draft.kind !== kind;
                  update(fresh ? draftForKind(kind, allEvents, text.kinds[kind]) : draft);
                  go("connection");
                }}
                t={t}
              />
            ) : null}

            {step === "connection" && draft ? (
              <>
                <ConnectionFields draft={draft} onChange={(value) => (test.reset(), update(value))} t={t} kindLocked showKind={false} serverErrors={serverErrors} />
                <div className="flex flex-col gap-2 rounded-lg bg-subtle px-3.5 py-3" data-test-box>
                  <div className="flex flex-wrap items-center gap-3">
                    <button
                      type="button"
                      className={secondary}
                      disabled={test.testing || Object.keys(stepErrors("connection", draft)).length > 0}
                      onClick={() => void test.run(draft)}
                      data-test-channel
                    >
                      {test.testing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                      {text.test}
                    </button>
                    <TestResult result={test.result} t={t} />
                    {!test.result && !connectionOk ? <span className="text-xs text-muted">{text.wizard.testFirst}</span> : null}
                  </div>
                </div>
              </>
            ) : null}

            {step === "events" && draft ? <EventsPicker draft={draft} onChange={update} catalog={catalog} t={t} /> : null}
            {step === "when" && draft ? <WhenFields draft={draft} onChange={update} t={t} /> : null}
            {step === "summary" && draft ? <Summary draft={draft} catalog={catalog} t={t} tested={test.testedKey === key} skipped={skipped === key} onEdit={go} /> : null}
            {serverErrors.name && step === "summary" ? <span className="text-xs text-danger-text">{(text.errors as Record<string, string>)[serverErrors.name] ?? text.errors.unknown}</span> : null}
          </div>

          {skipAsked && step === "connection" ? (
            <div className="flex flex-wrap items-center gap-3 border-0 border-t border-solid border-border bg-warning/10 px-5 py-3" role="alertdialog" aria-label={text.wizard.skipTitle} data-skip-confirm>
              <AlertTriangle className="h-4 w-4 flex-none text-warning-text" aria-hidden />
              <span className="min-w-[200px] flex-1 text-[13px] text-fg">{text.wizard.skipText}</span>
              <button type="button" className={secondary} onClick={() => setSkipAsked(false)}>
                {text.wizard.skipBack}
              </button>
              <button
                type="button"
                className={primary}
                disabled={!fieldsOk}
                onClick={() => {
                  setSkipped(key);
                  go("events");
                }}
                data-skip-test
              >
                {text.wizard.skipConfirm}
              </button>
            </div>
          ) : null}

          {/* The service step has no button: choosing a service goes on. */}
          <div className={cn("flex flex-wrap items-center justify-end gap-2 border-0 border-t border-solid border-border px-5 py-3", step === "service" && "hidden")}>
            {index > 0 && !(editing && step === "connection") ? (
              <button type="button" className={cn(secondary, "mr-auto")} onClick={() => go(WIZARD_STEPS[index - 1])} data-wizard-back>
                <ArrowLeft className="h-4 w-4" />
                {text.wizard.back}
              </button>
            ) : null}
            {step === "connection" && !connectionOk && !skipAsked ? (
              <button
                type="button"
                disabled={!fieldsOk}
                className="border-0 bg-transparent px-2 py-1.5 text-[13px] text-muted underline hover:text-fg disabled:cursor-not-allowed disabled:no-underline disabled:opacity-50"
                onClick={() => setSkipAsked(true)}
                data-ask-skip
              >
                {text.wizard.skip}
              </button>
            ) : null}
            {editing && step !== "summary" ? (
              <button type="button" className={secondary} disabled={saving || !canGoOn} onClick={() => void save()} data-save-channel>
                {text.save}
              </button>
            ) : null}
            {step !== "service" ? (
              <button type="button" className={primary} disabled={!canGoOn || saving} onClick={next} data-wizard-next>
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {step === "summary" ? (editing ? text.save : text.wizard.create) : text.wizard.next}
              </button>
            ) : null}
          </div>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}

function Summary({
  draft,
  catalog,
  t,
  tested,
  skipped,
  onEdit,
}: {
  draft: ChannelDraft;
  catalog: NotificationCatalog;
  t: TranslationDictionary;
  tested: boolean;
  skipped: boolean;
  onEdit: (step: WizardStep) => void;
}) {
  const text = t.notificationsV2;
  const all = catalog.events.map((event) => event.key);
  const preset = presetOf(draft.events, all);
  const target = channelTarget({ kind: draft.kind, config: toPayload(draft).config });
  const rows: { step: WizardStep; label: string; value: string }[] = [
    {
      step: "connection",
      label: text.wizard.steps.connection,
      value: [draft.name, draft.name === text.kinds[draft.kind] ? null : text.kinds[draft.kind], target, tested ? text.wizard.tested : skipped ? text.wizard.untested : null]
        .filter(Boolean)
        .join(" · "),
    },
    {
      step: "events",
      label: text.wizard.steps.events,
      value: `${text.presets[preset]} · ${fill(text.eventCount, { n: draft.events.length })}`,
    },
    {
      step: "when",
      label: text.wizard.steps.when,
      value: [
        fill(text.wizard.fromLevel, { level: text.levels[draft.minLevel] }),
        draft.quiet ? fill(text.quietRange, { from: draft.quietStart, to: draft.quietEnd }) : text.wizard.noQuiet,
        draft.digest ? fill(text.digestShort, { time: draft.digestTime }) : null,
      ]
        .filter(Boolean)
        .join(" · "),
    },
  ];
  return (
    <dl className="m-0 flex flex-col divide-y divide-border rounded-lg border border-solid border-border" data-summary>
      {rows.map((row) => (
        <div key={row.step} className="flex flex-wrap items-start gap-x-4 gap-y-1 px-3.5 py-3">
          <dt className="w-32 flex-none text-[13px] text-muted">{row.label}</dt>
          <dd className="m-0 min-w-0 flex-1 text-[13px] text-fg [overflow-wrap:anywhere]">{row.value}</dd>
          <button type="button" className="border-0 bg-transparent p-0 text-[13px] text-accent-text hover:underline" onClick={() => onEdit(row.step)} data-summary-edit={row.step}>
            {text.edit}
          </button>
        </div>
      ))}
    </dl>
  );
}
