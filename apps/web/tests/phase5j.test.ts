// Phase 5j: notification channels (form ↔ API payload), settings sections,
// appearance preferences, date/time formats.
import assert from "node:assert/strict";
import test from "node:test";

import { translations } from "../src/i18n.ts";
import { DEFAULT_UI_PREFS, homeRedirect, prefsFromValues, rootAttributes, settingValues } from "../src/ui/appearance.ts";
import { formatTime, formatWhen, setFormatPreferences } from "../src/views/format.ts";
import {
  FORMS,
  KINDS,
  channelTarget,
  deliveryKind,
  draftErrors,
  draftFromChannel,
  emptyDraft,
  groupEvents,
  toPayload,
} from "../src/views/notifications/notificationsModel.ts";
import { SECTION_KEYS, changed, retentionPolicy, sectionErrors, typedValues } from "../src/views/settings/settingsModel.ts";

const EVENTS = ["run_failed", "disk_new"];

test("a new channel: dedicated form per kind, secrets only when typed", () => {
  const draft = { ...emptyDraft("telegram", EVENTS), name: "Phone", values: { bot_token: "123456:ABCdefGHIjklMNOpqrSTUvwxYZ", chat_id: "-1001" } };
  assert.deepEqual(draftErrors(draft), {});
  const payload = toPayload(draft);
  assert.deepEqual(payload.config, { chat_id: "-1001" });
  assert.deepEqual(payload.secrets, { bot_token: "123456:ABCdefGHIjklMNOpqrSTUvwxYZ" });
  assert.deepEqual(payload.events, EVENTS);
  assert.equal(payload.quiet_start, null);
  for (const kind of KINDS) assert.ok(FORMS[kind].length > 0, kind);
  assert.equal(draftErrors(emptyDraft("gotify", EVENTS)).server_url, "required");
});

test("editing: stored secrets stay unless replaced or removed; webhook headers are secrets", () => {
  const channel = {
    id: 3,
    name: "Hook",
    kind: "webhook" as const,
    enabled: true,
    config: { url: "https://hooks.lan/pbo", header_names: ["Authorization"] },
    secrets_set: ["header.Authorization"],
    events: EVENTS,
    min_level: "warning" as const,
    quiet_start: "22:00",
    quiet_end: "07:00",
    digest: true,
    digest_time: "08:30",
  };
  const draft = draftFromChannel(channel);
  assert.equal(draft.headers[0].stored, true);
  assert.ok(draft.quiet && draft.digest);
  let payload = toPayload(draft);
  assert.deepEqual(payload.secrets, {}, "absent = keep");
  assert.deepEqual(payload.config, { url: "https://hooks.lan/pbo", header_names: ["Authorization"] });
  assert.equal(payload.channel_id, 3);
  payload = toPayload({ ...draft, headers: [{ name: "Authorization", value: "Bearer new", stored: true }] });
  assert.deepEqual(payload.secrets, { "header.Authorization": "Bearer new" });
  const ntfy = draftFromChannel({ ...channel, kind: "ntfy", config: { topic: "t", server_url: "https://ntfy.sh" }, secrets_set: ["token"] });
  assert.deepEqual(draftErrors(ntfy), {});
  assert.deepEqual(toPayload({ ...ntfy, clearSecrets: ["token"] }).secrets, { token: null }, "null = clear");
});

test("client checks: urls, e-mails, port, quiet hours, at least one event", () => {
  const email = { ...emptyDraft("email", EVENTS), name: "Mail", values: { host: "smtp", from_address: "pbo@x.org", to_addresses: "a@x.org, nope", port: "99999" } };
  assert.equal(draftErrors(email).to_addresses, "invalid_email");
  assert.equal(draftErrors(email).port, "invalid_port");
  const quiet = { ...emptyDraft("discord", []), name: "D", values: { webhook_url: "https://discord.com/api/webhooks/1/a" }, quiet: true, quietStart: "08:00", quietEnd: "08:00" };
  assert.equal(draftErrors(quiet).quiet, "invalid_quiet_hours");
  assert.equal(draftErrors(quiet).events, "invalid_events");
  assert.equal(draftErrors({ ...emptyDraft("gotify", EVENTS), name: "G", values: { server_url: "gotify.lan" } }).server_url, "invalid_url");
});

test("events by group, journal glyphs, target line without secrets", () => {
  assert.deepEqual(groupEvents([{ key: "a", group: "runs" }, { key: "b", group: "disks" }, { key: "c", group: "runs" }], ["runs", "disks", "agents"]), [
    { group: "runs", keys: ["a", "c"] },
    { group: "disks", keys: ["b"] },
  ]);
  assert.deepEqual(["sent", "failed", "pending", "digest", "cancelled"].map((status) => deliveryKind(status as never)), ["success", "failed", "running", "pending", "skipped"]);
  assert.equal(channelTarget({ kind: "ntfy", config: { server_url: "https://ntfy.home.lan", topic: "pbo" } }), "ntfy.home.lan/pbo");
  assert.equal(channelTarget({ kind: "discord", config: {} }), "");
});

test("every event, kind, field, level and error code is translated in both languages", () => {
  const events = ["run_success", "run_failed", "run_interrupted", "verify_failed", "repair_completed", "unsafe_removal", "disk_to_plug", "rotation_missed", "backup_stale", "disk_space", "smart_degraded", "disk_new", "agent_offline", "agent_update_failed", "pve_unreachable", "pbs_unreachable", "guest_agent_missing"];
  for (const lang of ["en", "fr"] as const) {
    const text = translations[lang].notificationsV2;
    for (const event of events) assert.ok((text.eventLabels as Record<string, string>)[event], `${lang} ${event}`);
    for (const kind of KINDS) {
      assert.ok(text.kinds[kind], `${lang} ${kind}`);
      for (const field of FORMS[kind]) assert.ok((text.fields as Record<string, string>)[field.name], `${lang} ${field.name}`);
    }
    for (const code of ["required", "invalid_url", "invalid_email", "invalid_port", "invalid_header", "invalid_quiet_hours", "invalid_events", "invalid_topic", "invalid_chat_id", "invalid_discord_webhook", "invalid_bot_token", "invalid_apprise_url"]) {
      assert.ok((text.errors as Record<string, string>)[code], `${lang} ${code}`);
    }
    assert.ok(translations[lang].settingsV2.sections.retention && translations[lang].settingsV2.preview.includes("{runs}"));
  }
});

test("settings sections: one PATCH per section, validation, retention preview", () => {
  const saved = { TIMEZONE: "Europe/Paris", UI_DATE_FORMAT: "auto", UI_TIME_FORMAT: "24h", UI_HOME_PAGE: "dashboard", PUBLIC_URL: "" };
  assert.deepEqual(changed("general", saved, { ...saved, PUBLIC_URL: "https://pbo.lan" }), { PUBLIC_URL: "https://pbo.lan" });
  assert.equal(sectionErrors("general", { ...saved, PUBLIC_URL: "pbo.lan" }).PUBLIC_URL, "url");
  const backups = { DEFAULT_RETENTION_KEEP_LAST: "0", DEFAULT_RETENTION_KEEP_WEEKLY: 0, DEFAULT_RETENTION_KEEP_MONTHLY: 0, ROTATION_STALE_DAYS: 30, MIN_DISK_SIZE_GIB: 32 };
  assert.equal(sectionErrors("backups", backups).DEFAULT_RETENTION_KEEP_LAST, "retention_empty");
  assert.equal(sectionErrors("backups", { ...backups, DEFAULT_RETENTION_KEEP_LAST: "4", ROTATION_STALE_DAYS: "400" }).ROTATION_STALE_DAYS, "range");
  assert.deepEqual(typedValues({ DEFAULT_RETENTION_KEEP_LAST: "4", TIMEZONE: "UTC", PUBLIC_URL: "" }), { DEFAULT_RETENTION_KEEP_LAST: 4, TIMEZONE: "UTC", PUBLIC_URL: "" });
  assert.deepEqual(retentionPolicy({ RUN_HISTORY_MODE: "per_disk", RUN_HISTORY_KEEP_PER_DISK: "20" }), { mode: "per_disk", days: 180, keep_per_disk: 20, log_detail_days: 0 });
  assert.equal(sectionErrors("retention", { RUN_HISTORY_MODE: "per_disk", RUN_HISTORY_KEEP_PER_DISK: "0" }).RUN_HISTORY_KEEP_PER_DISK, "range");
  assert.ok(SECTION_KEYS.agents.includes("AUTO_UPDATE_AGENTS") && SECTION_KEYS.backups.includes("MIN_DISK_SIZE_GIB"));
});

test("appearance: account settings → root attributes, home page once per session", () => {
  const prefs = prefsFromValues({ UI_THEME: "system", UI_ACCENT: "teal", UI_DENSITY: "compact", UI_SIDEBAR_COLLAPSED: true, UI_REDUCE_MOTION: "off", UI_HOME_PAGE: "disks", DEFAULT_EJECT_AT_END: false });
  assert.deepEqual(rootAttributes(prefs, true), { "data-theme": "dark", "data-accent": "teal", "data-density": "compact", "data-motion": "full", "data-sidebar": "collapsed" });
  assert.equal(rootAttributes({ ...prefs, theme: "light" }, true)["data-theme"], "light");
  assert.equal(prefsFromValues({ UI_ACCENT: "pink" }).accent, "steel", "unknown values fall back");
  assert.equal(prefs.defaultEject, false);
  assert.deepEqual(settingValues({ theme: "dark", sidebarCollapsed: false }), { UI_THEME: "dark", UI_SIDEBAR_COLLAPSED: false });
  assert.equal(homeRedirect("/", prefs, false), "/disks");
  assert.equal(homeRedirect("/", prefs, true), null);
  assert.equal(homeRedirect("/planning", prefs, false), null);
  assert.equal(homeRedirect("/", DEFAULT_UI_PREFS, false), null);
});

test("date order and 12 / 24 h clock", () => {
  const now = new Date("2026-11-18T12:00:00Z");
  try {
    setFormatPreferences({ dateOrder: "auto", timeFormat: "24h" });
    assert.equal(formatWhen("2026-03-08T10:00:00Z", "en", now, { today: "today {time}", yesterday: "y", never: "-" }), "8 Mar");
    setFormatPreferences({ dateOrder: "mdy", timeFormat: "12h" });
    assert.equal(formatWhen("2026-03-08T10:00:00Z", "en", now, { today: "today {time}", yesterday: "y", never: "-" }), "Mar 8");
    assert.match(formatTime("2026-03-08T15:00:00Z", "en", "UTC"), /03:00\s?pm/i);
    setFormatPreferences({ dateOrder: "ymd", timeFormat: "24h" });
    assert.equal(formatWhen("2025-03-08T10:00:00Z", "fr", now, { today: "t", yesterday: "y", never: "-" }), "2025 mars 8");
  } finally {
    setFormatPreferences({ dateOrder: "auto", timeFormat: "24h" });
  }
});
