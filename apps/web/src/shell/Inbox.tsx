// B55: the top-bar inbox (replaces the bell). Every notification event, stored
// by the server whatever the channels, plus the alert toasts of this UI.
import { useCallback, useEffect, useState } from "react";
import { CheckCheck, Inbox as InboxIcon, Loader2 } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { getInbox, getInboxCounts, markInboxRead, recordInboxToast, type InboxCounts, type InboxFilter, type InboxItem } from "../api";
import { cn } from "../components/ui/utils";
import type { Language, TranslationDictionary } from "../i18n";
import { Popover } from "../ui/Popover";
import type { Toast } from "../ui/toastStore";
import { useNow } from "../ui/useNow";
import { useEvents } from "../useEvents";
import { DiskMark } from "../views/disks/DiskTag";
import { fill, formatWhen } from "../views/format";
import { badgeText, INBOX_FILTERS, levelTone } from "./inboxModel";

const POLL_MS = 60_000;

/** ToastProvider.onAlert: an alert toast is kept in the inbox (fire and forget). */
export function recordAlertToast(toast: Toast): void {
  recordInboxToast({
    level: toast.tone === "danger" ? "error" : "warning",
    title: toast.title,
    body: toast.description,
    link: toast.action?.href,
    disk_id: toast.diskId,
  }).catch(() => undefined);
}

const TONE_CLASS = { info: "bg-accent", warning: "bg-warning", error: "bg-danger" } as const;

export function InboxButton({ t, language }: { t: TranslationDictionary; language: Language }) {
  const text = t.shell.inbox;
  const navigate = useNavigate();
  const now = useNow();
  const [counts, setCounts] = useState<InboxCounts>({ unread: 0, urgent_unread: 0 });
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<InboxFilter>("all");
  const [items, setItems] = useState<InboxItem[] | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);

  const refreshCounts = useCallback(() => {
    getInboxCounts()
      .then(setCounts)
      .catch(() => undefined);
  }, []);

  const load = useCallback(async () => {
    try {
      const page = await getInbox(filter);
      setItems(page.items);
      setHasMore(page.has_more);
      setCounts({ unread: page.unread, urgent_unread: page.urgent_unread });
    } catch {
      setItems([]);
    }
  }, [filter]);

  const live = useEvents(["inbox.updated"], () => {
    refreshCounts();
    if (open) void load();
  });

  useEffect(refreshCounts, [refreshCounts]);
  useEffect(() => {
    if (live) return;
    const id = window.setInterval(refreshCounts, POLL_MS);
    return () => window.clearInterval(id);
  }, [live, refreshCounts]);
  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  async function more() {
    if (!items?.length) return;
    setLoadingMore(true);
    try {
      const page = await getInbox(filter, items[items.length - 1].id);
      setItems([...items, ...page.items]);
      setHasMore(page.has_more);
    } finally {
      setLoadingMore(false);
    }
  }

  async function openItem(item: InboxItem, close: () => void) {
    if (!item.read) setCounts(await markInboxRead([item.id]).catch(() => counts));
    close();
    if (item.link) navigate(item.link);
  }

  async function readAll() {
    setCounts(await markInboxRead());
    setItems((current) => current?.map((item) => ({ ...item, read: true })) ?? null);
  }

  const badge = badgeText(counts.unread);
  const label = counts.unread ? fill(text.openUnread, { n: counts.unread }) : text.open;
  return (
    <Popover
      label={text.title}
      open={open}
      onOpenChange={setOpen}
      placement="bottom-end"
      // Phones: the panel spans the screen under the top bar, wherever the button is.
      className="w-[min(400px,calc(100vw-24px))] p-0 max-sm:fixed max-sm:inset-x-3 max-sm:top-[68px] max-sm:w-auto"
      triggerClassName="relative flex h-[34px] w-[34px] items-center justify-center rounded-lg border border-solid border-border bg-transparent text-muted hover:bg-subtle"
      trigger={
        <>
          <InboxIcon className="h-4 w-4" aria-hidden />
          <span className="sr-only">{label}</span>
          {badge ? (
            <span
              className={cn(
                "absolute -right-1.5 -top-1.5 flex h-[18px] min-w-[18px] items-center justify-center rounded-full px-1 text-[10px] font-semibold tabular-nums",
                counts.urgent_unread ? "bg-danger text-white" : "bg-accent text-accent-fg",
              )}
              aria-hidden
              data-inbox-badge={counts.urgent_unread ? "urgent" : "unread"}
            >
              {badge}
            </span>
          ) : null}
        </>
      }
    >
      {(close) => (
        <div className="flex max-h-[min(560px,calc(100vh-96px))] flex-col" data-inbox>
          <div className="flex items-center gap-2 border-0 border-b border-solid border-border px-4 py-3">
            <span className="flex-1 text-sm font-semibold">{text.title}</span>
            <button
              type="button"
              disabled={!counts.unread}
              onClick={() => void readAll()}
              className="flex items-center gap-1.5 border-0 bg-transparent p-0 text-[13px] text-accent-text hover:underline disabled:cursor-default disabled:text-faint disabled:no-underline"
              data-inbox-read-all
            >
              <CheckCheck className="h-4 w-4" aria-hidden />
              {text.markAll}
            </button>
          </div>
          <div className="flex gap-1 border-0 border-b border-solid border-border px-3 py-2" role="tablist" aria-label={text.filtersLabel}>
            {INBOX_FILTERS.map((key) => (
              <button
                key={key}
                type="button"
                role="tab"
                aria-selected={filter === key}
                onClick={() => setFilter(key)}
                className={cn(
                  "flex items-center gap-1.5 rounded-md border-0 px-2.5 py-1 text-[13px]",
                  filter === key ? "bg-subtle font-medium text-fg" : "bg-transparent text-muted hover:bg-hover",
                )}
                data-inbox-filter={key}
              >
                {text.filters[key]}
                {key === "unread" && counts.unread ? <span className="tabular-nums text-faint">{counts.unread}</span> : null}
                {key === "urgent" && counts.urgent_unread ? <span className="h-1.5 w-1.5 rounded-full bg-danger" aria-hidden /> : null}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {items === null ? (
              <p className="m-0 flex items-center gap-2 px-4 py-6 text-[13px] text-faint">
                <Loader2 className="h-4 w-4 animate-spin" />
                {text.loading}
              </p>
            ) : items.length === 0 ? (
              <p className="m-0 px-4 py-8 text-center text-[13px] text-faint">{text.empty[filter]}</p>
            ) : (
              <ul className="m-0 list-none p-0">
                {items.map((item) => (
                  <li key={item.id} className="border-0 border-b border-solid border-border last:border-b-0">
                    <button
                      type="button"
                      onClick={() => void openItem(item, close)}
                      className={cn("flex w-full items-start gap-3 border-0 bg-transparent px-4 py-3 text-left hover:bg-hover", !item.read && "bg-accent/[0.04]")}
                      data-inbox-item={item.event}
                      data-read={item.read ? "" : undefined}
                    >
                      <span className={cn("mt-1.5 h-2 w-2 flex-none rounded-full", TONE_CLASS[levelTone(item.level)], item.read && "opacity-40")} aria-hidden />
                      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                        <span className={cn("flex items-center gap-1.5 text-[13px] text-fg", !item.read && "font-semibold")}>
                          {item.disk_id ? <DiskMark id={item.disk_id} t={t} /> : null}
                          <span className="min-w-0 truncate">{item.title}</span>
                          {item.count > 1 ? <span className="flex-none text-xs font-normal text-faint">{fill(text.count, { n: item.count })}</span> : null}
                        </span>
                        {item.body ? <span className="line-clamp-2 text-xs leading-[1.45] text-muted">{item.body}</span> : null}
                        <span className="text-[11px] text-faint">
                          {formatWhen(item.updated_at, language, now, t.dashboardV2)}
                          {item.read ? null : <span className="sr-only"> · {text.unread}</span>}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {hasMore ? (
              <button
                type="button"
                className="flex w-full items-center justify-center gap-2 border-0 border-t border-solid border-border bg-transparent px-4 py-2.5 text-[13px] text-accent-text hover:bg-hover"
                onClick={() => void more()}
                disabled={loadingMore}
              >
                {loadingMore ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
                {text.more}
              </button>
            ) : null}
          </div>
          <button
            type="button"
            onClick={() => (close(), navigate("/notifications"))}
            className="border-0 border-t border-solid border-border bg-transparent px-4 py-2.5 text-left text-[13px] text-muted hover:bg-hover hover:text-fg"
          >
            {text.settings}
          </button>
        </div>
      )}
    </Popover>
  );
}
