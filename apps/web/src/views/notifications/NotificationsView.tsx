import { useCallback, useEffect, useMemo, useState } from "react";
import { BellRing, ChevronLeft, ChevronRight, Clock, Loader2, Pencil, Plus, RotateCcw, Search, Send, SlidersHorizontal, Trash2 } from "lucide-react";

import {
  deleteNotificationChannel,
  getNotificationCatalog,
  getNotificationChannels,
  getNotificationDeliveries,
  retryNotificationDelivery,
  testNotificationChannel,
  updateNotificationChannel,
  type DeliveryPage,
  type NotificationCatalog,
  type NotificationChannel,
  type NotificationDelivery,
  type NotificationTestResult,
} from "../../api";
import { cn } from "../../components/ui/utils";
import type { Language, TranslationDictionary } from "../../i18n";
import { ActionMenuButton, type MenuAction } from "../../ui/ActionMenu";
import { PageTitle } from "../../ui/PageTitle";
import { StatusDot } from "../../ui/StatusDot";
import { StatusIcon } from "../../ui/StatusIcon";
import { useToast } from "../../ui/toast";
import { useNow } from "../../ui/useNow";
import { fill, formatTime, formatWhen } from "../format";
import { input, primary, secondary } from "./ChannelForm";
import { ChannelLogo } from "./ChannelLogo";
import { ChannelWizard } from "./ChannelWizard";
import {
  JOURNAL_PERIODS,
  LEVELS,
  NO_FILTERS,
  channelTarget,
  deliveryKind,
  draftFromChannel,
  journalQuery,
  pageCount,
  toPayload,
  waitingState,
  type JournalFilters,
  type WizardStep,
} from "./notificationsModel";

const section = "rounded-lg border border-solid border-border bg-surface";
const STATUSES: NotificationDelivery["status"][] = ["pending", "digest", "sent", "failed", "cancelled"];

/** Opens the channel assistant (also from the ⌘K palette: "New channel"). */
export const NEW_CHANNEL_HASH = "#new-channel";

/** Phase 5j / B55: notification channels (Apprise) and the send journal. */
export function NotificationsView({ language, t }: { language: Language; t: TranslationDictionary }) {
  const text = t.notificationsV2;
  const lang = language === "fr" ? "fr" : "en";
  const { toast } = useToast();
  const now = useNow();
  const [catalog, setCatalog] = useState<NotificationCatalog | null>(null);
  const [channels, setChannels] = useState<NotificationChannel[] | null>(null);
  const [wizard, setWizard] = useState<{ channel: NotificationChannel | null; step: WizardStep } | null>(null);
  const [tests, setTests] = useState<Record<number, NotificationTestResult | "running">>({});
  const [filters, setFilters] = useState<JournalFilters>(NO_FILTERS);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [journal, setJournal] = useState<DeliveryPage | null>(null);

  const loadChannels = useCallback(async () => {
    const [nextCatalog, nextChannels] = await Promise.all([getNotificationCatalog(), getNotificationChannels()]);
    setCatalog(nextCatalog);
    setChannels(nextChannels);
  }, []);

  const loadJournal = useCallback(async () => {
    setJournal(await getNotificationDeliveries(journalQuery(filters, page, new Date())));
  }, [filters, page]);

  useEffect(() => {
    loadChannels().catch((error) => toast({ tone: "danger", title: error instanceof Error ? error.message : t.error }));
  }, [loadChannels, toast, t.error]);

  useEffect(() => {
    loadJournal().catch(() => setJournal({ items: [], total: 0, offset: 0, limit: 25 }));
  }, [loadJournal]);

  // The search applies after a short pause (one request, not one per key).
  useEffect(() => {
    const id = window.setTimeout(() => {
      setFilters((current) => (current.q === search ? current : { ...current, q: search }));
      setPage(0);
    }, 300);
    return () => window.clearTimeout(id);
  }, [search]);

  // "New channel" from the command palette lands here with #new-channel.
  useEffect(() => {
    if (catalog && window.location.hash === NEW_CHANNEL_HASH) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
      setWizard({ channel: null, step: "service" });
    }
  }, [catalog]);

  const setFilter = (patch: Partial<JournalFilters>) => {
    setFilters((current) => ({ ...current, ...patch }));
    setPage(0);
  };

  async function remove(channel: NotificationChannel) {
    await deleteNotificationChannel(channel.id);
    toast({ tone: "success", title: fill(text.deleted, { name: channel.name }) });
    await Promise.all([loadChannels(), loadJournal()]);
  }

  async function test(channel: NotificationChannel) {
    setTests((current) => ({ ...current, [channel.id]: "running" }));
    try {
      const result = await testNotificationChannel(channel.id);
      setTests((current) => ({ ...current, [channel.id]: result }));
    } catch (error) {
      setTests((current) => ({ ...current, [channel.id]: { ok: false, error: error instanceof Error ? error.message : t.error } }));
    }
    await loadJournal();
  }

  async function toggleEnabled(channel: NotificationChannel) {
    const next = { ...draftFromChannel(channel), enabled: !channel.enabled };
    await updateNotificationChannel(channel.id, toPayload(next));
    await Promise.all([loadChannels(), loadJournal()]);
  }

  async function retry(delivery: NotificationDelivery) {
    await retryNotificationDelivery(delivery.id);
    await loadJournal();
  }

  const channelMenu = (channel: NotificationChannel): MenuAction[] => [
    { key: "test", label: text.test, icon: Send, onSelect: () => test(channel) },
    { key: "connection", label: text.editConnection, icon: Pencil, onSelect: () => setWizard({ channel, step: "connection" }) },
    { key: "events", label: text.editEvents, icon: BellRing, onSelect: () => setWizard({ channel, step: "events" }) },
    { key: "when", label: text.editWhen, icon: Clock, onSelect: () => setWizard({ channel, step: "when" }) },
    {
      key: "delete",
      label: text.delete,
      icon: Trash2,
      danger: true,
      confirm: { title: fill(text.deleteTitle, { name: channel.name }), description: text.deleteText, confirmLabel: text.delete },
      onSelect: () => remove(channel),
    },
  ];

  const pages = pageCount(journal?.total ?? 0);
  const filtered = useMemo(() => JSON.stringify({ ...filters, q: "" }) !== JSON.stringify({ ...NO_FILTERS, q: "" }) || Boolean(filters.q), [filters]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center gap-3">
        <PageTitle tip={text.info}>{text.title}</PageTitle>
        <span className="flex-1" />
        <button type="button" className={primary} disabled={!catalog} onClick={() => setWizard({ channel: null, step: "service" })} data-add-channel>
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
                  <span className={cn("flex h-9 w-9 flex-none items-center justify-center rounded-md bg-subtle", channel.enabled ? "text-fg" : "text-faint")}>
                    <ChannelLogo kind={channel.kind} />
                  </span>
                  <div className="flex min-w-[200px] flex-1 flex-col gap-0.5">
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
                  <button
                    type="button"
                    className={secondary}
                    aria-label={fill(text.editNamed, { name: channel.name })}
                    title={text.edit}
                    onClick={() => setWizard({ channel, step: "summary" })}
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <ActionMenuButton actions={channelMenu(channel)} label={fill(t.menu.label, { name: channel.name })} />
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className={section} aria-labelledby="journal-title">
        <div className="flex flex-wrap items-center gap-2 border-0 border-b border-solid border-border px-4 py-3">
          <h2 id="journal-title" className="m-0 mr-auto text-sm font-semibold">
            {text.journal}
            {journal ? <span className="ml-2 font-normal text-faint">{fill(text.journalTotal, { n: journal.total })}</span> : null}
          </h2>
          <label className="relative flex min-w-[180px] flex-[1_1_200px] items-center sm:max-w-[260px]">
            <Search className="pointer-events-none absolute left-2.5 h-4 w-4 text-faint" aria-hidden />
            <input className={cn(input, "pl-8")} type="search" value={search} placeholder={text.journalSearch} aria-label={text.journalSearch} onChange={(event) => setSearch(event.target.value)} data-journal-search />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2 border-0 border-b border-solid border-border bg-subtle/60 px-4 py-2.5 text-[13px]" data-journal-filters>
          <SlidersHorizontal className="h-4 w-4 text-faint" aria-hidden />
          <select className={cn(input, "w-auto py-1")} aria-label={text.filterChannel} value={filters.channelId ?? ""} onChange={(event) => setFilter({ channelId: event.target.value ? Number(event.target.value) : null })}>
            <option value="">{text.filterAllChannels}</option>
            {(channels ?? []).map((channel) => (
              <option key={channel.id} value={channel.id}>
                {channel.name}
              </option>
            ))}
          </select>
          <select className={cn(input, "w-auto py-1")} aria-label={text.filterStatus} value={filters.status} onChange={(event) => setFilter({ status: event.target.value as JournalFilters["status"] })}>
            <option value="">{text.filterAllStatuses}</option>
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {(text.status as Record<string, string>)[status]}
              </option>
            ))}
          </select>
          <select className={cn(input, "w-auto py-1")} aria-label={text.filterLevel} value={filters.level} onChange={(event) => setFilter({ level: event.target.value as JournalFilters["level"] })}>
            <option value="">{text.filterAllLevels}</option>
            {LEVELS.map((level) => (
              <option key={level} value={level}>
                {text.levels[level]}
              </option>
            ))}
          </select>
          <select className={cn(input, "w-auto py-1")} aria-label={text.filterPeriod} value={filters.period} onChange={(event) => setFilter({ period: event.target.value as JournalFilters["period"] })}>
            {JOURNAL_PERIODS.map((period) => (
              <option key={period} value={period}>
                {text.periods[period]}
              </option>
            ))}
          </select>
          {filtered ? (
            <button type="button" className="border-0 bg-transparent px-1 text-[13px] text-accent-text hover:underline" onClick={() => (setSearch(""), setFilters(NO_FILTERS), setPage(0))}>
              {text.filterReset}
            </button>
          ) : null}
        </div>
        {journal === null ? (
          <p className="m-0 flex items-center gap-2 px-4 py-4 text-[13px] text-faint">
            <Loader2 className="h-4 w-4 animate-spin" />
            {text.loading}
          </p>
        ) : journal.items.length === 0 ? (
          <p className="m-0 px-4 py-4 text-[13px] text-faint">{filtered ? text.journalNoMatch : text.journalEmpty}</p>
        ) : (
          <ul className="m-0 list-none p-0" data-journal>
            {journal.items.map((delivery) => {
              const waiting = waitingState(delivery, now);
              const at = delivery.next_attempt_at ? formatTime(delivery.next_attempt_at, lang) : "";
              return (
                <li key={delivery.id} className="flex items-start gap-3 border-0 border-b border-solid border-border px-4 py-2.5 last:border-b-0" data-delivery={delivery.status} data-waiting={waiting ?? undefined}>
                  {waiting === "scheduled" || waiting === "retry" ? (
                    <Clock className="mt-0.5 h-4 w-4 flex-none text-muted" aria-hidden />
                  ) : (
                    <StatusIcon kind={deliveryKind(delivery.status)} className="mt-0.5" />
                  )}
                  <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="text-[13px] text-fg">
                      {delivery.event === "test" ? text.testEvent : delivery.title}
                      {delivery.duplicates ? <span className="text-faint"> ×{delivery.duplicates + 1}</span> : null}
                    </span>
                    <span className="text-xs text-muted">
                      {[
                        delivery.channel_name,
                        waiting === "scheduled"
                          ? fill(text.scheduledAt, { time: at })
                          : waiting === "retry"
                            ? fill(text.retryAt, { time: at })
                            : waiting === "sending"
                              ? text.sending
                              : (text.status as Record<string, string>)[delivery.status],
                        delivery.attempts > 1 ? fill(text.attempts, { n: delivery.attempts }) : null,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
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
              );
            })}
          </ul>
        )}
        {journal && journal.total > journal.limit ? (
          <nav className="flex items-center justify-end gap-2 border-0 border-t border-solid border-border px-4 py-2.5 text-[13px] text-muted" aria-label={text.pagination}>
            <span className="mr-auto tabular-nums">{fill(text.pageOf, { page: page + 1, pages })}</span>
            <button type="button" className={secondary} disabled={page === 0} onClick={() => setPage(page - 1)} aria-label={text.previousPage} title={text.previousPage}>
              <ChevronLeft className="h-4 w-4" />
            </button>
            <button type="button" className={secondary} disabled={page + 1 >= pages} onClick={() => setPage(page + 1)} aria-label={text.nextPage} title={text.nextPage}>
              <ChevronRight className="h-4 w-4" />
            </button>
          </nav>
        ) : null}
      </section>

      {catalog ? (
        <ChannelWizard
          open={wizard !== null}
          catalog={catalog}
          channel={wizard?.channel ?? null}
          initialStep={wizard?.step ?? "service"}
          onClose={() => setWizard(null)}
          onSaved={(saved, created) => {
            setWizard(null);
            toast({ tone: "success", title: created ? fill(text.createdNamed, { name: saved.name }) : text.saved });
            void loadChannels();
            void loadJournal();
          }}
          t={t}
        />
      ) : null}
    </div>
  );
}
