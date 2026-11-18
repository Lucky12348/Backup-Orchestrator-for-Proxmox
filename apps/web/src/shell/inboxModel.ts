// B55: the inbox, pure parts (node:test).
import type { InboxFilter, NotificationLevel } from "../api.ts";

export const INBOX_FILTERS: InboxFilter[] = ["all", "unread", "urgent"];

/** Badge of the top-bar button: nothing at 0, "99+" beyond. */
export function badgeText(unread: number): string {
  if (!unread || unread < 0) return "";
  return unread > 99 ? "99+" : String(unread);
}

export function levelTone(level: NotificationLevel | string): "info" | "warning" | "error" {
  return level === "error" ? "error" : level === "warning" ? "warning" : "info";
}
