// Phase 6: planning calendar views, time zone, recurrence sentence, export URL.
import assert from "node:assert/strict";
import test from "node:test";

import {
  emptyForm,
  feedUrl,
  formFromEvent,
  formProblems,
  formToPayload,
  itemActions,
  itemTone,
  monthGrid,
  nowMinutes,
  periodTitle,
  placeDay,
  recurrenceSentence,
  stepAnchor,
  viewRange,
  visibleDays,
  zoned,
  zonedToUtc,
  type AgendaItem,
  type SentenceTexts,
} from "../src/views/planning/calendarModel.ts";

const PARIS = "Europe/Paris";
const SUNDAY = { y: 2026, m: 11, d: 15 };

function item(overrides: Partial<AgendaItem> = {}): AgendaItem {
  return {
    id: "a",
    kind: "external",
    title: "Rotation hebdo",
    starts_at: "2026-11-15T08:00:00Z",
    ends_at: "2026-11-15T09:30:00Z",
    scheduled_for: "2026-11-15T08:00:00Z",
    window_ends_at: null,
    status: "planned",
    event_id: 1,
    planning_run_id: null,
    backup_run_id: null,
    disk_serial: "WD-A",
    disk_label: "WD 4 To",
    progress_percent: null,
    message: null,
    read_only: false,
    ...overrides,
  };
}

test("time zone: wall time ↔ UTC, across daylight saving", () => {
  assert.equal(zonedToUtc(SUNDAY, 9, 0, PARIS).toISOString(), "2026-11-15T08:00:00.000Z"); // UTC+1
  assert.equal(zonedToUtc({ y: 2027, m: 7, d: 4 }, 9, 0, PARIS).toISOString(), "2027-07-04T07:00:00.000Z"); // UTC+2
  assert.equal(zonedToUtc(SUNDAY, 9, 0, "America/New_York").toISOString(), "2026-11-15T14:00:00.000Z");
  const parts = zoned(new Date("2026-11-15T23:30:00Z"), PARIS);
  assert.deepEqual([parts.y, parts.m, parts.d, parts.h, parts.min, parts.weekday], [2026, 11, 16, 0, 30, 0]); // Monday 00:30
});

test("views: visible days, week starting on Monday, month grid, ranges", () => {
  const week = visibleDays("week", { y: 2026, m: 11, d: 18 });
  assert.deepEqual([week[0], week[6]], [{ y: 2026, m: 11, d: 16 }, { y: 2026, m: 11, d: 22 }]);
  assert.equal(visibleDays("day", SUNDAY).length, 1);
  const grid = monthGrid(2026, 11);
  assert.deepEqual(grid[0], { y: 2026, m: 10, d: 26 });
  assert.equal(grid.length, 42); // November 2026 needs 6 rows
  assert.equal(monthGrid(2027, 2).length, 35);
  assert.equal(visibleDays("year", SUNDAY).length, 12);
  const range = viewRange("week", SUNDAY, PARIS);
  assert.equal(range.start.toISOString(), "2026-11-08T23:00:00.000Z");
  assert.equal(range.end.toISOString(), "2026-11-15T23:00:00.000Z");
});

test("navigation and titles (FR/EN)", () => {
  assert.deepEqual(stepAnchor("week", SUNDAY, 1), { y: 2026, m: 11, d: 22 });
  assert.deepEqual(stepAnchor("month", { y: 2026, m: 12, d: 31 }, 1), { y: 2027, m: 1, d: 1 });
  assert.deepEqual(stepAnchor("year", SUNDAY, -1), { y: 2025, m: 11, d: 1 });
  assert.equal(periodTitle("week", SUNDAY, "fr"), "9 – 15 nov. 2026");
  assert.equal(periodTitle("week", { y: 2026, m: 12, d: 1 }, "fr"), "30 nov. – 6 déc. 2026");
  assert.equal(periodTitle("month", SUNDAY, "fr"), "Novembre 2026");
  assert.equal(periodTitle("day", SUNDAY, "fr"), "Dimanche 15 novembre 2026");
  assert.equal(periodTitle("day", SUNDAY, "en"), "Sunday 15 November 2026");
  assert.equal(periodTitle("year", SUNDAY, "en"), "2026");
});

test("time grid: placement in the planning zone, overlapping items share the width", () => {
  const items = [
    item({ id: "a" }),
    item({ id: "b", starts_at: "2026-11-15T08:30:00Z", ends_at: "2026-11-15T09:00:00Z", kind: "manual" }),
    item({ id: "c", starts_at: "2026-11-15T13:00:00Z", ends_at: "2026-11-15T13:05:00Z" }),
    item({ id: "other-day", starts_at: "2026-11-16T08:00:00Z", ends_at: "2026-11-16T09:00:00Z" }),
  ];
  const placed = placeDay(items, SUNDAY, PARIS);
  assert.deepEqual(
    placed.map((entry) => [entry.item.id, entry.top, entry.height, entry.lane, entry.lanes]),
    [
      ["a", 540, 90, 0, 2],
      ["b", 570, 60, 1, 2],
      ["c", 840, 60, 0, 1], // short items keep a readable height (two lines)
    ],
  );
});

test("red line of the current time, only on today", () => {
  const now = new Date("2026-11-15T16:20:00Z");
  assert.equal(nowMinutes(now, SUNDAY, PARIS), 17 * 60 + 20);
  assert.equal(nowMinutes(now, { y: 2026, m: 11, d: 14 }, PARIS), null);
});

test("colours by state and actions of the detail panel", () => {
  assert.equal(itemTone(item()), "planned");
  assert.equal(itemTone(item({ kind: "monthly" })), "plannedMonthly");
  assert.equal(itemTone(item({ status: "running" })), "running");
  assert.equal(itemTone(item({ status: "success" })), "success");
  assert.equal(itemTone(item({ status: "missed" })), "failed");
  assert.equal(itemTone(item({ status: "to_confirm" })), "confirm");
  assert.equal(itemTone(item({ kind: "proxmox", read_only: true })), "proxmox");
  assert.deepEqual(itemActions(item()), ["run_now", "edit"]);
  assert.deepEqual(itemActions(item({ status: "running", backup_run_id: 4 })), ["follow", "edit"]);
  assert.deepEqual(itemActions(item({ status: "success", backup_run_id: 4 })), ["report", "edit"]);
  assert.deepEqual(itemActions(item({ status: "to_confirm", planning_run_id: 9 })), ["confirm", "cancel", "edit"]);
  assert.deepEqual(itemActions(item({ kind: "manual", event_id: null, status: "success", backup_run_id: 3 })), ["report"]);
  assert.deepEqual(itemActions(item({ kind: "proxmox", read_only: true })), []);
});

const FR: SentenceTexts = {
  once: "Le {date} à {time}",
  daily: "Chaque jour à {time}",
  dailyN: "Tous les {n} jours à {time}",
  weekly: "Chaque {weekday} à {time}",
  weeklyN: "Un {weekday} sur {n} à {time}",
  monthly: "Le {day} de chaque mois à {time}",
  monthlyN: "Le {day}, tous les {n} mois, à {time}",
  onDisk: ", sur {disk}",
  rotation: ", sur {disks} en alternance",
  then: " puis ",
};

test("recurrence sentence (mockup example)", () => {
  const form = { ...emptyForm(new Date("2026-11-15T08:00:00Z"), PARIS, "main"), recurrence: "weekly" as const };
  assert.equal(
    recurrenceSentence(form, ["WD 4 To", "Toshiba 3 To"], FR, "fr"),
    "Chaque dimanche à 09:00, sur WD 4 To puis Toshiba 3 To en alternance",
  );
  assert.equal(recurrenceSentence({ ...form, interval: 2 }, ["WD 4 To"], FR, "fr"), "Un dimanche sur 2 à 09:00, sur WD 4 To");
  assert.equal(recurrenceSentence({ ...form, recurrence: "monthly" }, [], FR, "fr"), "Le 15 de chaque mois à 09:00");
  assert.equal(recurrenceSentence({ ...form, recurrence: "once" }, [], FR, "fr"), "Le 15 novembre 2026 à 09:00");
});

test("dialog ⇄ API payload, in the planning zone", () => {
  const form = {
    ...emptyForm(new Date("2026-11-15T08:00:00Z"), PARIS, "main"),
    title: "Rotation",
    diskSerials: ["WD-A", "TOSH-B"],
    interval: 2,
    confirmation: true,
    vmGroups: ["vm/100"],
  };
  assert.deepEqual(formProblems(form), []);
  const payload = formToPayload(form, PARIS);
  assert.equal(payload.window_starts_at, "2026-11-15T08:00:00.000Z");
  assert.equal(payload.disk_serial, "WD-A");
  assert.deepEqual(payload.rotation_disk_serials, ["WD-A", "TOSH-B"]);
  assert.deepEqual(payload.recurrence_config, { interval: 2 });
  assert.equal(payload.start_mode, "manual_confirmation");
  assert.equal(payload.timezone, PARIS);
  const back = formFromEvent({ ...payload, recurrence_type: "weekly", enabled: true } as never, PARIS);
  assert.deepEqual([back.hour, back.minute, back.interval, back.diskSerials], [9, 0, 2, ["WD-A", "TOSH-B"]]);
  assert.deepEqual(formProblems({ ...form, title: " ", diskSerials: [], interval: 60 }), ["title", "disks", "interval"]);
});

test("export URL keeps the token and adds the options", () => {
  const base = "https://pbo.lan/calendar.ics?token=abc";
  assert.equal(feedUrl(base, { history: true, proxmox: false }), "https://pbo.lan/calendar.ics?token=abc&history=true");
  assert.equal(
    feedUrl(base, { history: false, proxmox: true, download: true }),
    "https://pbo.lan/calendar.ics?token=abc&proxmox=true&download=true",
  );
});
