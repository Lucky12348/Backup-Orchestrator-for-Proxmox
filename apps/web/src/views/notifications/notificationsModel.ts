// Phase 5j: notification channels — the form of each kind, the draft ↔ API
// payload, the journal. Pure (node:test).

import type { NotificationChannel, NotificationChannelInput, NotificationDelivery, NotificationKind, NotificationLevel } from "../../api.ts";

export const KINDS: NotificationKind[] = ["ntfy", "gotify", "discord", "telegram", "email", "webhook", "apprise"];
export const LEVELS: NotificationLevel[] = ["info", "warning", "error"];

export type FieldType = "text" | "url" | "secret" | "number" | "select" | "emails";

export interface FormField {
  name: string;
  type: FieldType;
  required?: boolean;
  /** Placeholder shown in the input (not a translation: an example value). */
  example?: string;
  choices?: string[];
}

/** The dedicated form of each kind (mirrors notification_channels.KINDS on the API side). */
export const FORMS: Record<NotificationKind, FormField[]> = {
  ntfy: [
    { name: "server_url", type: "url", example: "https://ntfy.sh" },
    { name: "topic", type: "text", required: true, example: "pbo-alerts" },
    { name: "token", type: "secret", example: "tk_…" },
    { name: "username", type: "text" },
    { name: "password", type: "secret" },
  ],
  gotify: [
    { name: "server_url", type: "url", required: true, example: "https://gotify.example.org" },
    { name: "app_token", type: "secret", required: true },
  ],
  discord: [{ name: "webhook_url", type: "secret", required: true, example: "https://discord.com/api/webhooks/…" }],
  telegram: [
    { name: "bot_token", type: "secret", required: true, example: "123456:ABC…" },
    { name: "chat_id", type: "text", required: true, example: "-1001234567890" },
  ],
  email: [
    { name: "host", type: "text", required: true, example: "smtp.example.org" },
    { name: "security", type: "select", choices: ["starttls", "ssl", "none"] },
    { name: "port", type: "number", example: "587" },
    { name: "username", type: "text" },
    { name: "password", type: "secret" },
    { name: "from_address", type: "text", required: true, example: "pbo@example.org" },
    { name: "to_addresses", type: "emails", required: true, example: "me@example.org, you@example.org" },
  ],
  webhook: [{ name: "url", type: "url", required: true, example: "https://hooks.example.org/pbo" }],
  apprise: [{ name: "url", type: "secret", required: true, example: "pover://user@token" }],
};

export interface HeaderRow {
  name: string;
  /** Typed value; empty with `stored` = keep the stored value. */
  value: string;
  stored: boolean;
}

export interface ChannelDraft {
  id: number | null;
  name: string;
  kind: NotificationKind;
  enabled: boolean;
  values: Record<string, string>;
  /** Secrets already stored (the field then shows "Set" and stays empty). */
  storedSecrets: string[];
  /** Stored secrets the operator asked to remove. */
  clearSecrets: string[];
  headers: HeaderRow[];
  events: string[];
  minLevel: NotificationLevel;
  quiet: boolean;
  quietStart: string;
  quietEnd: string;
  digest: boolean;
  digestTime: string;
}

export function emptyDraft(kind: NotificationKind, allEvents: string[]): ChannelDraft {
  return {
    id: null,
    name: "",
    kind,
    enabled: true,
    values: kind === "ntfy" ? { server_url: "https://ntfy.sh" } : kind === "email" ? { security: "starttls" } : {},
    storedSecrets: [],
    clearSecrets: [],
    headers: [],
    events: [...allEvents],
    minLevel: "info",
    quiet: false,
    quietStart: "22:00",
    quietEnd: "07:00",
    digest: false,
    digestTime: "08:00",
  };
}

export function draftFromChannel(channel: NotificationChannel): ChannelDraft {
  const values: Record<string, string> = {};
  for (const [key, value] of Object.entries(channel.config ?? {})) {
    if (key === "header_names") continue;
    values[key] = Array.isArray(value) ? value.join(", ") : value === null || value === undefined ? "" : String(value);
  }
  const headerNames = Array.isArray(channel.config?.header_names) ? (channel.config.header_names as string[]) : [];
  return {
    id: channel.id,
    name: channel.name,
    kind: channel.kind,
    enabled: channel.enabled,
    values,
    storedSecrets: channel.secrets_set.filter((name) => !name.startsWith("header.")),
    clearSecrets: [],
    headers: headerNames.map((name) => ({ name, value: "", stored: channel.secrets_set.includes(`header.${name}`) })),
    events: [...channel.events],
    minLevel: channel.min_level,
    quiet: Boolean(channel.quiet_start && channel.quiet_end),
    quietStart: channel.quiet_start ?? "22:00",
    quietEnd: channel.quiet_end ?? "07:00",
    digest: channel.digest,
    digestTime: channel.digest_time || "08:00",
  };
}

/** The API body: non-secret fields in `config`, typed secrets only (absent = keep, null = clear). */
export function toPayload(draft: ChannelDraft): NotificationChannelInput {
  const config: Record<string, unknown> = {};
  const secrets: Record<string, string | null> = {};
  for (const field of FORMS[draft.kind]) {
    const value = (draft.values[field.name] ?? "").trim();
    if (field.type === "secret") {
      if (value) secrets[field.name] = value;
      else if (draft.clearSecrets.includes(field.name)) secrets[field.name] = null;
      continue;
    }
    if (!value) continue;
    config[field.name] = field.type === "number" ? Number(value) : value;
  }
  if (draft.kind === "webhook") {
    const rows = draft.headers.filter((row) => row.name.trim());
    config.header_names = rows.map((row) => row.name.trim());
    for (const row of rows) {
      if (row.value) secrets[`header.${row.name.trim()}`] = row.value;
    }
  }
  return {
    ...(draft.id !== null ? { channel_id: draft.id } : {}),
    name: draft.name.trim(),
    kind: draft.kind,
    enabled: draft.enabled,
    config,
    secrets,
    events: draft.events,
    min_level: draft.minLevel,
    quiet_start: draft.quiet ? draft.quietStart : null,
    quiet_end: draft.quiet ? draft.quietEnd : null,
    digest: draft.digest,
    digest_time: draft.digestTime,
  };
}

/** Client-side checks before calling the API (the API checks again): field → error code. */
export function draftErrors(draft: ChannelDraft): Record<string, string> {
  const errors: Record<string, string> = {};
  if (!draft.name.trim()) errors.name = "required";
  for (const field of FORMS[draft.kind]) {
    const value = (draft.values[field.name] ?? "").trim();
    const stored = field.type === "secret" && draft.storedSecrets.includes(field.name) && !draft.clearSecrets.includes(field.name);
    if (field.required && !value && !stored) errors[field.name] = "required";
    else if (value && field.type === "url" && !/^https?:\/\/[^\s/]+/.test(value)) errors[field.name] = "invalid_url";
    else if (value && field.type === "emails" && !value.split(/[,;\s]+/).filter(Boolean).every((item) => /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(item))) errors[field.name] = "invalid_email";
    else if (value && field.type === "number" && !(Number(value) >= 1 && Number(value) <= 65535)) errors[field.name] = "invalid_port";
  }
  if (draft.kind === "webhook" && draft.headers.some((row) => row.name.trim() && !/^[A-Za-z0-9-]{1,64}$/.test(row.name.trim()))) errors.headers = "invalid_header";
  if (draft.quiet && draft.quietStart === draft.quietEnd) errors.quiet = "invalid_quiet_hours";
  if (!draft.events.length) errors.events = "invalid_events";
  return errors;
}

/** Events grouped as the catalog says (runs, disks, agents, integrations). */
export function groupEvents(events: { key: string; group: string }[], groups: string[]): { group: string; keys: string[] }[] {
  return groups.map((group) => ({ group, keys: events.filter((event) => event.group === group).map((event) => event.key) })).filter((item) => item.keys.length);
}

export function toggle(list: string[], value: string, on: boolean): string[] {
  return on ? (list.includes(value) ? list : [...list, value]) : list.filter((item) => item !== value);
}

/** StatusIcon kind of a delivery of the journal. */
export function deliveryKind(status: NotificationDelivery["status"]): "success" | "failed" | "running" | "pending" | "skipped" {
  switch (status) {
    case "sent":
      return "success";
    case "failed":
      return "failed";
    case "pending":
      return "running";
    case "digest":
      return "pending";
    default:
      return "skipped";
  }
}

/** One short line about a channel: its target, never a secret. */
export function channelTarget(channel: Pick<NotificationChannel, "kind" | "config">): string {
  const config = channel.config ?? {};
  const host = (value: unknown) => {
    try {
      return new URL(String(value)).host;
    } catch {
      return "";
    }
  };
  switch (channel.kind) {
    case "ntfy":
      return `${host(config.server_url) || "ntfy.sh"}/${config.topic ?? ""}`;
    case "gotify":
      return host(config.server_url);
    case "telegram":
      return String(config.chat_id ?? "");
    case "email":
      return Array.isArray(config.to_addresses) ? (config.to_addresses as string[]).join(", ") : "";
    case "webhook":
      return host(config.url);
    default:
      return "";
  }
}
