// Phase 5b: disk states, tabs, search, actions, approval data warning,
// anti double click, notices (toasts instead of banners).
import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, noticeFromError, noticeTitle } from "../src/notices.ts";
import { createRequestGuard } from "../src/requestGuard.ts";
import {
  dataWarning,
  diskActions,
  diskHistory,
  diskRows,
  diskUiStatus,
  matchesQuery,
  nameConfirmed,
  tabCounts,
  visibleRows,
  type DiskLike,
  type RunLike,
} from "../src/views/disks/diskModel.ts";

function disk(overrides: Partial<DiskLike> = {}): DiskLike {
  return {
    id: 1,
    serial_number: "WXD2DA1L1E7C",
    display_name: "WD 4 To",
    model_name: "WDC WD40NMZW",
    capacity_gb: 4000,
    connected: true,
    trusted: true,
    dedicated_backup_disk: true,
    status: "known",
    reject_reason: null,
    operator_decision: null,
    ...overrides,
  };
}

function run(overrides: Partial<RunLike> = {}): RunLike {
  return {
    id: 10,
    disk_id: 1,
    status: "success",
    started_at: "2026-11-15T13:00:00Z",
    finished_at: "2026-11-15T14:02:00Z",
    current_step: "success",
    progress_percent: 100,
    ...overrides,
  };
}

test("disk statuses: new, approved, running, ejected, unplugged, rejected, ignored", () => {
  assert.equal(diskUiStatus(disk({ trusted: false, status: "new" })), "new");
  assert.equal(diskUiStatus(disk()), "approved");
  assert.equal(diskUiStatus(disk(), [run({ status: "running", current_step: "sync" })]), "running");
  assert.equal(diskUiStatus(disk({ connected: false, status: "ejected", handoff_status: "ejected" })), "ejected");
  assert.equal(diskUiStatus(disk({ connected: false, status: "unplugged" })), "unplugged");
  assert.equal(diskUiStatus(disk({ capacity_gb: 0, status: "rejected", reject_reason: "zero_size" })), "rejected");
  assert.equal(diskUiStatus(disk({ trusted: false, status: "rejected", reject_reason: "operator", operator_decision: "rejected" })), "rejected");
  assert.equal(diskUiStatus(disk({ trusted: false, status: "ignored", operator_decision: "ignored" })), "ignored");
  // Another disk's run does not make this one "running".
  assert.equal(diskUiStatus(disk(), [run({ disk_id: 2, status: "running" })]), "approved");
});

test("B25: an ejected disk offers 'unplug then replug' instead of a backup", () => {
  const ejected = disk({ connected: false, status: "ejected", handoff_status: "ejected" });
  const actions = diskActions(ejected, diskUiStatus(ejected));
  assert.deepEqual(actions, ["reactivate", "replug", "rescan"]);
  assert.ok(!actions.includes("backup"));
});

test("actions per state", () => {
  assert.deepEqual(diskActions(disk(), "approved"), ["backup", "rescan"]);
  assert.deepEqual(diskActions(disk({ pbs_visible: true, handoff_status: "visible_on_pbs" }), "approved"), ["backup", "eject", "rescan"]);
  assert.deepEqual(diskActions(disk(), "running"), ["follow", "eject"]);
  assert.deepEqual(diskActions(disk({ operator_decision: "rejected" }), "rejected"), ["unreject", "hide", "rescan"]);
  assert.deepEqual(diskActions(disk({ reject_reason: "zero_size" }), "rejected"), ["hide", "rescan"]);
  assert.deepEqual(diskActions(disk(), "new"), []);
});

test("tabs and counters (plugged includes an ejected disk still there)", () => {
  const disks = [
    disk({ id: 1 }),
    disk({ id: 2, display_name: "Toshiba", connected: false, status: "unplugged" }),
    disk({ id: 3, display_name: "SanDisk", trusted: false, status: "new" }),
    disk({ id: 4, display_name: "Game Drive", trusted: false, capacity_gb: 0, status: "rejected", reject_reason: "zero_size" }),
    disk({ id: 5, display_name: "Ejected", connected: false, status: "ejected", handoff_status: "ejected" }),
  ];
  const rows = diskRows(disks, [run({ disk_id: 1, status: "running" })]);
  assert.deepEqual(tabCounts(rows), { all: 5, plugged: 3, approved: 3, review: 1, rejected: 1, hidden: 0 });
  assert.deepEqual(visibleRows(rows, "review", "").map((row) => row.disk.id), [3]);
  assert.equal(rows[0].status, "running"); // the running disk comes first
});

test("search by name, model or serial, ignoring case, dashes and spaces", () => {
  const wd = disk({ serial_aliases: ["WD-WXD2DA1L1E7C"] });
  assert.ok(matchesQuery(wd, "wd 4"));
  assert.ok(matchesQuery(wd, "wxd2-da1l"));
  assert.ok(matchesQuery(wd, "WD40NMZW"));
  assert.ok(!matchesQuery(wd, "toshiba"));
});

test("approval: data warning from partitions, filesystem and used size", () => {
  const withData = dataWarning(
    disk({ trusted: false, partitions: [{ name: "sdb1", filesystem_type: "exfat", size_gb: 931, label: "Photos-2024" }], filesystem_used_gb: 412 }),
  );
  assert.equal(withData.hasData, true);
  assert.equal(withData.filesystem, "exfat");
  assert.equal(withData.usedGb, 412);
  const blank = dataWarning(disk({ trusted: false, partitions: [] }));
  assert.equal(blank.hasData, false);
  assert.equal(blank.partitionsUnknown, false);
  assert.equal(dataWarning(disk({ trusted: false, partitions: null })).partitionsUnknown, true);
});

test("approval: the typed name must match exactly", () => {
  const sandisk = disk({ display_name: "SanDisk" });
  assert.equal(nameConfirmed(sandisk, ""), false);
  assert.equal(nameConfirmed(sandisk, "sandisk"), false);
  assert.equal(nameConfirmed(sandisk, " SanDisk "), true);
});

test("history: newest first, backup vs eject, tones", () => {
  const items = diskHistory(disk(), [
    run({ id: 1, started_at: "2026-11-01T09:00:00Z" }),
    run({ id: 2, started_at: "2026-11-15T13:00:00Z", status: "failed", current_step: "disk_not_plugged" }),
    run({ id: 3, started_at: "2026-11-15T15:00:00Z", current_step: "ejected" }),
    run({ id: 4, started_at: "2026-11-16T09:00:00Z", current_step: "ejected", auto_eject_after_success: true }),
    run({ id: 5, disk_id: 2 }),
  ]);
  assert.deepEqual(
    items.map((item) => [item.id, item.kind, item.tone]),
    [
      [4, "backup", "ok"], // B24: backup + auto-eject = one backup run
      [3, "eject", "ok"],
      [2, "backup", "err"],
      [1, "backup", "ok"],
    ],
  );
});

test("anti double click: a second request with the same key is not sent", async () => {
  const guard = createRequestGuard();
  let calls = 0;
  let release!: () => void;
  const pending = new Promise<void>((resolve) => (release = resolve));
  const first = guard.run("external-backup-1", async () => {
    calls += 1;
    await pending;
    return "run-1";
  });
  const second = await guard.run("external-backup-1", async () => {
    calls += 1;
    return "run-2";
  });
  assert.equal(second, null);
  assert.equal(guard.busy("external-backup-1"), true);
  release();
  assert.equal(await first, "run-1");
  assert.equal(calls, 1);
  assert.equal(guard.busy("external-backup-1"), false);
  assert.equal(await guard.run("external-backup-1", async () => "again"), "again"); // usable again
});

test("notices: a 409 code is translated, other errors keep their message", () => {
  const readiness = { disk_ejected: "Débranchez puis rebranchez le disque." };
  const refused = noticeFromError(new ApiError("The disk was ejected", 409, "disk_ejected"));
  assert.equal(refused.tone, "danger");
  assert.equal(noticeTitle(refused, [readiness]), "Débranchez puis rebranchez le disque.");
  assert.equal(noticeTitle(noticeFromError(new Error("boom")), [readiness]), "boom");
  assert.equal(noticeTitle(noticeFromError(new ApiError("Unknown", 409, "nope")), [readiness]), "Unknown");
});
