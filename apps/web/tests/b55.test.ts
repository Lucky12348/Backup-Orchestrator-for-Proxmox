// B55 (notifications, inbox, ⌘K palette), B56 (cancel), B57 (settings menu): pure parts.
import assert from "node:assert/strict";
import test from "node:test";

import { translations } from "../src/i18n.ts";
import { canCancel, cancelKind, isStopping } from "../src/runs/cancelModel.ts";
import { badgeText, levelTone } from "../src/shell/inboxModel.ts";
import { flatten, moveSelection, normalize, score, searchPalette, shortcutLabel, type PaletteEntry } from "../src/shell/paletteModel.ts";
import { isInboxAlert } from "../src/ui/toastStore.ts";
import { activityState, activitySummary } from "../src/views/activity/activityModel.ts";
import { recentRuns } from "../src/views/dashboard/dashboardModel.ts";
import { isCancelledRun } from "../src/views/disks/diskModel.ts";
import {
  ESSENTIAL_EVENTS,
  JOURNAL_PAGE,
  NO_FILTERS,
  WIZARD_STEPS,
  connectionKey,
  draftForKind,
  emptyDraft,
  eventsForPreset,
  journalQuery,
  pageCount,
  presetOf,
  stepErrors,
  waitingState,
} from "../src/views/notifications/notificationsModel.ts";
import { SECTIONS, activeSection, sectionFromHash } from "../src/views/settings/settingsModel.ts";

const ALL = ["run_success", "run_failed", "run_interrupted", "verify_failed", "repair_completed", "unsafe_removal", "disk_to_plug", "rotation_missed", "backup_stale", "disk_space", "smart_degraded", "disk_new", "agent_offline", "agent_update_failed", "pve_unreachable", "pbs_unreachable", "guest_agent_missing"];

// --- channel assistant ---------------------------------------------------------------------

test("event presets: Essential / Everything / Custom", () => {
  const essential = eventsForPreset("essential", ALL);
  assert.ok(essential.includes("run_failed") && !essential.includes("run_success") && !essential.includes("disk_new"));
  assert.deepEqual(essential, ALL.filter((key) => ESSENTIAL_EVENTS.includes(key)));
  assert.equal(presetOf(essential, ALL), "essential");
  assert.equal(presetOf([...ALL].reverse(), ALL), "all");
  assert.equal(presetOf(["run_failed"], ALL), "custom");
  // A new channel starts on Essential, named after its service.
  const draft = draftForKind("telegram", ALL, "Telegram");
  assert.equal(presetOf(draft.events, ALL), "essential");
  assert.equal(draft.name, "Telegram");
});

test("assistant steps: what blocks each step; a changed connection needs a new test", () => {
  assert.deepEqual(WIZARD_STEPS, ["service", "connection", "events", "when", "summary"]);
  const draft = { ...emptyDraft("ntfy", []), name: "Phone", values: { server_url: "https://ntfy.sh", topic: "" } };
  assert.deepEqual(Object.keys(stepErrors("connection", draft)), ["topic"]);
  assert.deepEqual(stepErrors("events", draft), { events: "invalid_events" });
  assert.deepEqual(stepErrors("when", { ...draft, quiet: true, quietStart: "22:00", quietEnd: "22:00" }), { quiet: "invalid_quiet_hours" });
  const ok = { ...draft, values: { ...draft.values, topic: "pbo" } };
  assert.deepEqual(stepErrors("connection", ok), {});
  assert.notEqual(connectionKey(ok), connectionKey({ ...ok, values: { ...ok.values, topic: "other" } }));
  assert.equal(connectionKey(ok), connectionKey({ ...ok, events: ["run_failed"], minLevel: "error" }), "events and level do not need a new test");
});

// --- journal ---------------------------------------------------------------------------------

test("journal query: filters, period, search, pages", () => {
  const now = new Date("2026-11-18T12:00:00Z");
  assert.equal(journalQuery(NO_FILTERS, 0, now), `limit=${JOURNAL_PAGE}&offset=0`);
  const query = new URLSearchParams(journalQuery({ channelId: 3, status: "failed", level: "error", period: "24h", q: " refused " }, 2, now));
  assert.equal(query.get("offset"), String(2 * JOURNAL_PAGE));
  assert.equal(query.get("channel_id"), "3");
  assert.equal(query.get("status"), "failed");
  assert.equal(query.get("level"), "error");
  assert.equal(query.get("since"), "2026-11-17T12:00:00.000Z");
  assert.equal(query.get("q"), "refused");
  assert.equal(pageCount(0), 1);
  assert.equal(pageCount(JOURNAL_PAGE + 1), 2);
});

test("a waiting message: a clock with its time, distinct from a message being sent", () => {
  const now = new Date("2026-11-18T22:30:00Z");
  assert.equal(waitingState({ status: "pending", attempts: 0, next_attempt_at: "2026-11-19T06:30:00Z" }, now), "scheduled");
  assert.equal(waitingState({ status: "pending", attempts: 0, next_attempt_at: "2026-11-19T06:30:00" }, now), "scheduled", "naive = UTC");
  assert.equal(waitingState({ status: "pending", attempts: 2, next_attempt_at: "2026-11-18T22:45:00Z" }, now), "retry");
  assert.equal(waitingState({ status: "pending", attempts: 0, next_attempt_at: "2026-11-18T22:30:05Z" }, now), "sending");
  assert.equal(waitingState({ status: "digest", attempts: 0, next_attempt_at: null }, now), "digest");
  assert.equal(waitingState({ status: "sent", attempts: 1, next_attempt_at: null }, now), null);
});

// --- inbox -------------------------------------------------------------------------------------

test("inbox badge and alert toasts", () => {
  assert.equal(badgeText(0), "");
  assert.equal(badgeText(7), "7");
  assert.equal(badgeText(140), "99+");
  assert.equal(levelTone("error"), "error");
  assert.equal(levelTone("whatever"), "info");
  assert.equal(isInboxAlert({ tone: "danger" }), true);
  assert.equal(isInboxAlert({ tone: "warning" }), true);
  assert.equal(isInboxAlert({ tone: "warning", inbox: false }), false, "a form message stays a toast");
  assert.equal(isInboxAlert({ tone: "success" }), false);
});

// --- ⌘K palette --------------------------------------------------------------------------------

const ENTRIES: PaletteEntry[] = [
  { id: "page:/settings", group: "pages", label: "Réglages", target: { kind: "navigate", to: "/settings" }, suggested: true },
  { id: "page:/disks", group: "pages", label: "Disques", target: { kind: "navigate", to: "/disks" }, suggested: true },
  { id: "disk:1", group: "disks", label: "Rotation A", hint: "WD 4 To · WD-123", keywords: "WD-123 pbo-wd-123", target: { kind: "navigate", to: "/disks/1" } },
  { id: "action:backup:1", group: "actions", label: "Sauvegarder Rotation A", keywords: "WD-123", target: { kind: "backup", diskId: 1 }, suggested: true },
  { id: "guest:5", group: "guests", label: "nextcloud", hint: "VM 105 · pve1", keywords: "105 VM pve1", target: { kind: "navigate", to: "/assets?q=nextcloud" } },
  { id: "settings:general", group: "settings", label: "Réglages › Général", keywords: "langue fuseau horaire", target: { kind: "navigate", to: "/settings#general" } },
];

test("palette search: accents, every word, label first, grouped", () => {
  assert.equal(normalize("Réglages Éjecter"), "reglages ejecter");
  assert.ok(score(ENTRIES[0], "regl") > score(ENTRIES[5], "fuseau"));
  assert.equal(score(ENTRIES[2], "rotation zzz"), 0, "every word must match");
  assert.ok(score(ENTRIES[2], "wd-123") > 0, "the serial finds the disk");
  const sections = searchPalette(ENTRIES, "rotation");
  assert.deepEqual(sections.map((section) => section.group), ["actions", "disks"], "fixed group order");
  assert.deepEqual(searchPalette(ENTRIES, "105").map((s) => s.entries[0].id), ["guest:5"], "a VMID finds its VM");
  assert.deepEqual(searchPalette(ENTRIES, "fuseau").map((s) => s.entries[0].id), ["settings:general"]);
  // Empty query: suggestions only.
  assert.deepEqual(flatten(searchPalette(ENTRIES, "")).map((entry) => entry.id), ["action:backup:1", "page:/disks", "page:/settings"]);
});

test("palette keyboard: arrows wrap; shortcut per system", () => {
  assert.equal(moveSelection(0, 3, "ArrowUp"), 2);
  assert.equal(moveSelection(2, 3, "ArrowDown"), 0);
  assert.equal(moveSelection(1, 3, "End"), 2);
  assert.equal(moveSelection(0, 0, "ArrowDown"), -1);
  assert.equal(shortcutLabel("MacIntel"), "⌘K");
  assert.equal(shortcutLabel("Win32"), "Ctrl K");
});

// --- cancel (B56) ----------------------------------------------------------------------------

test("cancel: offered during the whole run, never twice, words per run type", () => {
  const run = { id: 1, disk_id: 1, status: "running", cancellable: true, cancel_requested: false };
  assert.equal(canCancel(run), true);
  assert.equal(canCancel({ ...run, status: "pending" }), true);
  assert.equal(canCancel({ ...run, cancellable: false }), false, "releasing / ejecting: server says no");
  assert.equal(canCancel({ ...run, cancel_requested: true }), false);
  assert.equal(isStopping({ ...run, cancel_requested: true }), true);
  assert.equal(canCancel({ ...run, status: "failed" }), false);
  assert.equal(cancelKind({ repair: true }), "repair");
  assert.equal(cancelKind({ verify_only: true }), "verify");
  for (const lang of ["en", "fr"] as const) {
    const text = translations[lang].runModal;
    assert.ok(text.cancelKind.backup && text.cancelTitle.verify && text.cancelConfirm && text.title.cancelled && text.stopping, lang);
  }
});

// --- settings menu (B57) ---------------------------------------------------------------------

test("settings scroll-spy and hashes", () => {
  const tops = SECTIONS.map((key, index) => ({ key, top: 100 + index * 400 - 900 }));
  // general -800, appearance -400, backups 0, retention 400…
  assert.equal(activeSection(tops, 120), "backups");
  assert.equal(activeSection(tops.map((item) => ({ ...item, top: item.top + 2000 })), 120), "general");
  assert.equal(activeSection(tops, 120, true), "about", "at the bottom: the last section");
  assert.equal(sectionFromHash("#appearance"), "appearance");
  assert.equal(sectionFromHash("#settings-retention"), "retention");
  assert.equal(sectionFromHash("#nope"), null);
});


test("a cancelled run reads as cancelled, not failed (history, dashboard, activity)", () => {
  const run = { id: 9, disk_id: 1, status: "failed", current_step: "cancelled", started_at: "2026-11-18T10:00:00" };
  assert.equal(isCancelledRun(run), true);
  assert.equal(isCancelledRun({ ...run, current_step: "sync_job" }), false);
  assert.equal(recentRuns([run as never])[0].tone, "off");
  const item = { type: "backup", status: "failed", step: "cancelled", message: "Cancelled by the operator." };
  assert.equal(activityState(item as never), "info");
  const texts = translations.fr.activityV2.summary;
  assert.equal(activitySummary(item as never, texts as never, "fr"), "Interrompu par l’utilisateur, le disque a été libéré");
});
