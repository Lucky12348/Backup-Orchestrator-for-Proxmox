import { useCallback, useEffect, useState } from "react";
import { Loader2, Pencil, Plus, RotateCcw, Send, Trash2 } from "lucide-react";

import {
  createNotificationChannel,
  deleteNotificationChannel,
  getNotificationCatalog,
  getNotificationChannels,
  getNotificationDeliveries,
  retryNotificationDelivery,
  testNotificationChannel,
  updateNotificationChannel,
  type NotificationCatalog,
  type NotificationChannel,
  type NotificationDelivery,
  type NotificationTestResult,
} from "../../api";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../../components/ui/dialog";
import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { CloseButton } from "../../ui/CloseButton";
import { useConfirm } from "../../ui/confirm";
import { PageTitle } from "../../ui/PageTitle";
import { StatusDot } from "../../ui/StatusDot";
import { StatusIcon } from "../../ui/StatusIcon";
import { useToast } from "../../ui/toast";
import { useNow } from "../../ui/useNow";
import { fill, formatWhen } from "../format";
import { ChannelForm, apiErrorCode, primary, secondary } from "./ChannelForm";
import { channelTarget, deliveryKind, draftErrors, draftFromChannel, emptyDraft, toPayload, type ChannelDraft } from "./notificationsModel";

const section = "rounded-lg border border-solid border-border bg-surface";

/** Phase 5j: notification channels (Apprise) and the send journal. */
export function NotificationsView({ language, t }: { language: Language; t: TranslationDictionary }) {
  const text = t.notificationsV2;
  const { toast } = useToast();
  const confirm = useConfirm();
  const now = useNow();
  const [catalog, setCatalog] = useState<NotificationCatalog | null>(null);
  const [channels, setChannels] = useState<NotificationChannel[] | null>(null);
  const [deliveries, setDeliveries] = useState<NotificationDelivery[]>([]);
  const [draft, setDraft] = useState<ChannelDraft | null>(null);
  const [serverErrors, setServerErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [tests, setTests] = useState<Record<number, NotificationTestResult | "running">>({});

  const load = useCallback(async () => {
    const [nextCatalog, nextChannels, nextDeliveries] = await Promise.all([getNotificationCatalog(), getNotificationChannels(), getNotificationDeliveries(100)]);
    setCatalog(nextCatalog);
    setChannels(nextChannels);
    setDeliveries(nextDeliveries);
  }, []);

  useEffect(() => {
    load().catch((error) => toast({ tone: "danger", title: error instanceof Error ? error.message : t.error }));
  }, [load, toast, t.error]);

  const allEvents = catalog?.events.map((event) => event.key) ?? [];

  async function save() {
    if (!draft || Object.keys(draftErrors(draft)).length) return;
    setSaving(true);
    setServerErrors({});
    try {
      const payload = toPayload(draft);
      if (draft.id === null) await createNotificationChannel(payload);
      else await updateNotificationChannel(draft.id, payload);
      toast({ tone: "success", title: draft.id === null ? text.created : text.saved });
      setDraft(null);
      await load();
    } catch (error) {
      const { code, field } = apiErrorCode(error);
      setServerErrors({ [field ?? "name"]: code });
    } finally {
      setSaving(false);
    }
  }

  async function remove(channel: NotificationChannel) {
    const ok = await confirm({ title: fill(text.deleteTitle, { name: channel.name }), description: text.deleteText, confirmLabel: text.delete, tone: "danger" });
    if (!ok) return;
    await deleteNotificationChannel(channel.id);
    toast({ tone: "success", title: fill(text.deleted, { name: channel.name }) });
    await load();
  }

  async function test(channel: NotificationChannel) {
    setTests((current) => ({ ...current, [channel.id]: "running" }));
    try {
      const result = await testNotificationChannel(channel.id);
      setTests((current) => ({ ...current, [channel.id]: result }));
    } catch (error) {
      setTests((current) => ({ ...current, [channel.id]: { ok: false, error: error instanceof Error ? error.message : t.error } }));
    }
    setDeliveries(await getNotificationDeliveries(100));
  }

  async function toggleEnabled(channel: NotificationChannel) {
    const next = { ...draftFromChannel(channel), enabled: !channel.enabled };
    await updateNotificationChannel(channel.id, toPayload(next));
    await load();
  }

  async function retry(delivery: NotificationDelivery) {
    await retryNotificationDelivery(delivery.id);
    setDeliveries(await getNotificationDeliveries(100));
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <PageTitle tip={text.info}>{text.title}</PageTitle>
        <span className="flex-1" />
        <button type="button" className={primary} disabled={!catalog} onClick={() => catalog && (setServerErrors({}), setDraft(emptyDraft("ntfy", allEvents)))} data-add-channel>
          <Plus className="h-4 w-4" />
          {text.add}
        </button>
      </div>

      <section className={section} aria-labelledby="channels-title">
        <h2 id="channels-title" className="m-0 border-0 border-b border-solid border-border px-4 py-3 text-sm font-semibold">
          {text.channels}
        </h2>
        {channels === null ? (
          <p className="m-0 flex items-center gap-2 px-4 py-4 text-[13px] text-faint">
            <Loader2 className="h-4 w-4 animate-spin" />
            {text.loading}
          </p>
        ) : channels.length === 0 ? (
          <div className="flex flex-col gap-1 px-4 py-6" data-empty>
            <p className="m-0 text-sm text-fg">{text.empty}</p>
            <p className="m-0 text-[13px] text-muted">{text.emptyHelp}</p>
          </div>
        ) : (
          <ul className="m-0 list-none p-0">
            {channels.map((channel) => {
              const result = tests[channel.id];
              return (
                <li key={channel.id} className="flex flex-wrap items-center gap-3 border-0 border-b border-solid border-border px-4 py-3 last:border-b-0" data-channel={channel.kind}>
                  <div className="flex min-w-[220px] flex-1 flex-col gap-0.5">
                    <span className="flex items-center gap-2 text-sm font-medium text-fg">
                      {channel.name}
                      {channel.enabled ? null : (
                        <StatusDot tone="off" className="text-xs">
                          {text.disabled}
                        </StatusDot>
                      )}
                    </span>
                    <span className="text-[13px] text-muted">
                      {[text.kinds[channel.kind], channelTarget(channel), fill(text.eventCount, { n: channel.events.length }), text.levels[channel.min_level]].filter(Boolean).join(" · ")}
                      {channel.quiet_start ? ` · ${fill(text.quietRange, { from: channel.quiet_start, to: channel.quiet_end ?? "" })}` : ""}
                      {channel.digest ? ` · ${fill(text.digestShort, { time: channel.digest_time })}` : ""}
                    </span>
                    {result && result !== "running" ? (
                      <span className={cn("flex items-center gap-1.5 text-xs", result.ok ? "text-success-text" : "text-danger-text")} role="status" data-test-result={result.ok ? "ok" : "failed"}>
                        <StatusIcon kind={result.ok ? "success" : "failed"} size={14} />
                        {result.ok ? text.testOk : `${text.testFailed} ${result.error ?? ""}`}
                      </span>
                    ) : null}
                  </div>
                  <label className="flex items-center gap-2 text-[13px] text-muted">
                    <input type="checkbox" className="m-0 h-4 w-4 accent-accent" checked={channel.enabled} onChange={() => void toggleEnabled(channel)} />
                    {text.enabledToggle}
                  </label>
                  <button type="button" className={secondary} disabled={result === "running"} onClick={() => void test(channel)}>
                    {result === "running" ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
                    {text.test}
                  </button>
                  <button type="button" className={secondary} aria-label={fill(text.editNamed, { name: channel.name })} title={text.edit} onClick={() => (setServerErrors({}), setDraft(draftFromChannel(channel)))}>
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button type="button" className={secondary} aria-label={fill(text.deleteTitle, { name: channel.name })} title={text.delete} onClick={() => void remove(channel)}>
                    <Trash2 className="h-4 w-4" />
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className={section} aria-labelledby="journal-title">
        <h2 id="journal-title" className="m-0 border-0 border-b border-solid border-border px-4 py-3 text-sm font-semibold">
          {text.journal}
        </h2>
        {deliveries.length === 0 ? (
          <p className="m-0 px-4 py-4 text-[13px] text-faint">{text.journalEmpty}</p>
        ) : (
          <ul className="m-0 list-none p-0" data-journal>
            {deliveries.map((delivery) => (
              <li key={delivery.id} className="flex items-start gap-3 border-0 border-b border-solid border-border px-4 py-2.5 last:border-b-0" data-delivery={delivery.status}>
                <StatusIcon kind={deliveryKind(delivery.status)} className="mt-0.5" />
                <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="text-[13px] text-fg">
                    {delivery.event === "test" ? text.testEvent : delivery.title}
                    {delivery.duplicates ? <span className="text-faint"> ×{delivery.duplicates + 1}</span> : null}
                  </span>
                  <span className="text-xs text-muted">
                    {[delivery.channel_name, (text.status as Record<string, string>)[delivery.status], delivery.attempts > 1 ? fill(text.attempts, { n: delivery.attempts }) : null].filter(Boolean).join(" · ")}
                  </span>
                  {delivery.last_error ? <span className="text-xs text-danger-text [overflow-wrap:anywhere]">{delivery.last_error}</span> : null}
                </div>
                <span className="whitespace-nowrap text-xs text-faint">{formatWhen(delivery.created_at, language, now, t.dashboardV2)}</span>
                {delivery.status === "failed" ? (
                  <button type="button" className={secondary} onClick={() => void retry(delivery)} aria-label={text.retry} title={text.retry}>
                    <RotateCcw className="h-4 w-4" />
                  </button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <Dialog open={draft !== null} onOpenChange={(open) => (!open ? setDraft(null) : undefined)}>
        {draft && catalog ? (
          <DialogContent
            className="max-w-[760px]"
            aria-describedby="channel-dialog-help"
            onOpenAutoFocus={(event) => {
              // The first field, not the X (no focus ring on the close button).
              event.preventDefault();
              (event.currentTarget as HTMLElement | null)?.querySelector<HTMLInputElement>("[data-channel-form] input")?.focus();
            }}
          >
            <div className="flex items-center gap-2 border-0 border-b border-solid border-border px-5 py-3.5">
              <DialogTitle className="m-0 flex-1 text-base font-semibold">{draft.id === null ? text.addTitle : fill(text.editTitle, { name: draft.name })}</DialogTitle>
              <CloseButton label={t.shell.close} onClick={() => setDraft(null)} />
            </div>
            <div className="flex flex-col gap-4 overflow-y-auto px-5 py-4">
              <DialogDescription id="channel-dialog-help" className="m-0 text-[13px] text-muted">
                {text.dialogHelp}
              </DialogDescription>
              <ChannelForm draft={draft} onChange={setDraft} catalog={catalog} t={t} kindLocked={draft.id !== null} serverErrors={serverErrors} />
            </div>
            <div className="flex justify-end gap-2 border-0 border-t border-solid border-border px-5 py-3">
              <button type="button" className={secondary} onClick={() => setDraft(null)}>
                {t.confirmDialogs.cancel}
              </button>
              <button type="button" className={primary} disabled={saving || Object.keys(draftErrors(draft)).length > 0} onClick={() => void save()} data-save-channel>
                {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {text.save}
              </button>
            </div>
          </DialogContent>
        ) : null}
      </Dialog>
    </div>
  );
}
