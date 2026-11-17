// Phase 5g: agent updates, exact copy/retention messages (B37/B39), launch
// dialog (B48), rejected disks (hide, approve anyway, size next to the minimum).
import assert from "node:assert/strict";
import test from "node:test";

import { translations } from "../src/i18n.ts";
import { toastForDiskEvent } from "../src/liveDisks.ts";
import { reasonText } from "../src/readiness.ts";
import { copySummary, entryText, formatRate, journalText, zonedStamp } from "../src/runs/runModel.ts";
import { diskActions, diskRows, inTab, tabCounts } from "../src/views/disks/diskModel.ts";
import { estimateKind } from "../src/views/disks/diskProfile.ts";
import { canUpdateNow, shortBuild, UPDATE_TONE } from "../src/views/integrations/integrationsModel.ts";
import { parseMinSize } from "../src/views/settings/settingsModel.ts";

const entry = (code: string, params: Record<string, string>, at = "2026-11-17T01:40:03Z") => ({
  at,
  step: "cleanup",
  source: "pbo" as const,
  level: "info" as const,
  code,
  params,
  text: "raw",
  count: 1,
});

test("B37: versions copied and data transferred are two things", () => {
  for (const lang of ["fr", "en"] as const) {
    const texts = translations[lang].runModal;
    assert.equal(copySummary({ transferred_bytes: 0, no_new_data: true, versions_copied: 0 }, texts, lang), texts.noNewData);
    const none = copySummary({ transferred_bytes: 0, no_new_data: true, versions_copied: 4 }, texts, lang) ?? "";
    assert.ok(none.includes("4") && none.includes(lang === "fr" ? "0 o transféré" : "0 B transferred"), none);
    const some = copySummary({ transferred_bytes: 2 * 1024 ** 2, no_new_data: false, versions_copied: 1 }, texts, lang) ?? "";
    assert.ok(some.startsWith(texts.copiedOne) && some.includes("2"), some);
  }
  assert.equal(
    copySummary({ transferred_bytes: 0, no_new_data: true, versions_copied: 4 }, translations.fr.runModal, "fr"),
    "4 nouvelles versions copiées, 0 o transféré (déjà présentes sur le disque)",
  );
  // Older journals without the count keep the previous wording.
  assert.equal(copySummary({ transferred_bytes: 0, no_new_data: true }, translations.en.runModal, "en"), translations.en.runModal.noNewData);
});

test("B37: no 0 B/s", () => {
  assert.equal(formatRate("0 B/s", "fr"), null);
  assert.equal(formatRate("0.000 MiB/s", "en"), null);
  assert.ok(formatRate("64.208 MiB/s", "en"));
});

test("B39: space freed only when the GC freed some, bytes in one unit", () => {
  const fr = translations.fr.runLog as Record<string, string>;
  const freed = entryText(entry("cleanup_freed", { store: "pbo-a", options: "keep-last=2", removed: "3", freed_bytes: String(3 * 1024 ** 3) }), fr, "fr");
  assert.ok(freed.includes("3 ancienne(s) version(s)") && freed.includes("Gio") && freed.includes("libérés"), freed);
  const pending = entryText(entry("cleanup_pending", { store: "pbo-a", options: "keep-last=2", removed: "3" }), fr, "fr");
  assert.ok(pending.includes("après 24 h") && !pending.includes("libérés,"), pending);
  for (const code of ["cleanup_nothing", "cleanup_freed", "cleanup_pending"]) {
    assert.ok((translations.en.runLog as Record<string, string>)[code] && fr[code], code);
  }
});

test("B37: the exported journal writes every time in PBO's time zone", () => {
  assert.equal(zonedStamp("2026-11-17T01:40:03Z", "Europe/Paris"), "2026-11-17T02:40:03+01:00");
  assert.equal(zonedStamp("2026-11-17T02:40:03+01:00", "Europe/Paris"), "2026-11-17T02:40:03+01:00");
  assert.equal(zonedStamp("2026-07-01T00:00:00Z", "Europe/Paris"), "2026-07-01T02:00:00+02:00");
  const text = journalText(
    [entry("x", {}, "2026-11-17T01:40:03Z"), { ...entry("y", {}, "2026-11-17T02:40:04+01:00"), source: "pbs" as const, code: null }],
    {},
    "Europe/Paris",
  );
  assert.deepEqual(
    text.split("\n").map((line) => line.slice(0, 25)),
    ["2026-11-17T02:40:03+01:00", "2026-11-17T02:40:04+01:00"],
  );
});

test("B48: the launch dialog never shows a guess as an estimate", () => {
  assert.equal(estimateKind({ new_snapshots: 2, known: false, contents_known: false, sufficient: null }), "impossible");
  assert.equal(estimateKind({ new_snapshots: 0, known: true, contents_known: true, sufficient: true }), "nothing");
  assert.equal(estimateKind({ new_snapshots: 2, known: true, contents_known: true, sufficient: false }), "insufficient");
  assert.equal(estimateKind({ new_snapshots: 2, known: true, contents_known: true, sufficient: true }), "known");
  for (const lang of ["fr", "en"] as const) {
    assert.ok(translations[lang].launch.estimateImpossible && translations[lang].launch.nothingNew);
  }
});

test("rejected disks: size next to the minimum, approve anyway, hide", () => {
  const base = { id: 1, serial_number: "S", display_name: "Petit", capacity_gb: 16, connected: true, trusted: false };
  // Phase 5h.1: still plugged in → it can be ejected too.
  assert.deepEqual(diskActions({ ...base, reject_reason: "too_small" }, "rejected"), ["approveAnyway", "eject", "hide", "rescan"]);
  assert.deepEqual(diskActions({ ...base, reject_reason: "zero_size" }, "rejected"), ["eject", "hide", "rescan"]);
  const hidden = { ...base, id: 2, status: "hidden", operator_decision: "hidden" };
  assert.deepEqual(diskActions(hidden, "hidden"), ["unhide"]);
  const rows = diskRows([{ ...base, reject_reason: "too_small" }, hidden], []);
  assert.equal(inTab("hidden", hidden, "all"), false);
  assert.deepEqual(tabCounts(rows), { all: 1, plugged: 1, approved: 0, review: 0, rejected: 1, hidden: 1 });
  const reason = { code: "disk_too_small", message: "x", size_gb: 16, min_size_gb: 32 };
  assert.equal(reasonText(reason, translations.fr.readiness as Record<string, string>, "fr"), "Le disque est trop petit pour un datastore PBS : 16 Gio, minimum 32 Gio.");
  assert.ok(translations.fr.disksV2.rejectReason.too_small.includes("{size}") && translations.en.disksV2.rejectReason.too_small.includes("{size}"));
  assert.ok(translations.fr.disksV2.approval.anywayRisk.includes("{size}") && translations.fr.disksV2.approval.anywayRisk.includes("{min}"));
});

test("a hidden disk makes no noise when plugged in", () => {
  const data = { id: 3, serial_number: "S", display_name: "d", model_name: null, capacity_gb: 16, trusted: false, status: "hidden", reject_reason: "too_small", min_size_gb: 32 };
  assert.equal(toastForDiskEvent({ id: "1", type: "disk.added", data } as never, "fr"), null);
});

test("agent update states and settings", () => {
  assert.equal(canUpdateNow("outdated"), true);
  assert.equal(canUpdateNow("failed"), true);
  assert.equal(canUpdateNow("up_to_date"), false);
  assert.equal(canUpdateNow("updating"), false);
  assert.equal(canUpdateNow("too_old"), false);
  assert.equal(UPDATE_TONE.failed, "err");
  assert.equal(shortBuild("42ec4ad47c64f7f0"), "42ec4ad4");
  for (const lang of ["fr", "en"] as const) {
    const update = translations[lang].integrationsV2.update;
    for (const state of ["up_to_date", "outdated", "updating", "failed", "too_old", "unknown"] as const) assert.ok(update.state[state]);
    for (const code of ["run_active", "update_running", "no_package", "agent_not_found"]) assert.ok((update.errors as Record<string, string>)[code]);
  }
  assert.equal(parseMinSize("32"), 32);
  assert.equal(parseMinSize("0"), null);
  assert.equal(parseMinSize("abc"), null);
  assert.equal(parseMinSize("70000"), null);
});
