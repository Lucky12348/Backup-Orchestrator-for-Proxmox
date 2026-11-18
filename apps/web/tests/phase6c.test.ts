// Phase 6c: Proxmox jobs names/schedules, planning form (3 questions),
// datastore health states.
import assert from "node:assert/strict";
import test from "node:test";

import { translations } from "../src/i18n.ts";
import { todoItems } from "../src/views/dashboard/dashboardModel.ts";
import { pbsConfigNote } from "../src/views/disks/diskModel.ts";
import {
  autoTitle,
  firstOccurrence,
  newPlanForm,
  planFormFromEvent,
  planPayload,
  planProblems,
  planSentence,
  scheduleKey,
  windowEnd,
  type PlanForm,
} from "../src/views/planning/planningForm.ts";
import { describeSchedule, formFromJob, formToValues, jobLabel, jobProblems, parseSchedule } from "../src/views/resources/jobsModel.ts";

const FR = translations.fr.planningV2.form;
const PARIS = "Europe/Paris";
// Monday 16 Nov 2026, 12:00 Paris.
const NOW = new Date("2026-11-16T11:00:00Z");

test("Proxmox jobs: readable schedule and name, raw id kept out", () => {
  assert.equal(describeSchedule("sun 03:00", "fr"), "dim. 03:00");
  assert.equal(describeSchedule("mon..fri 22:30", "fr"), "lun.–ven. 22:30");
  assert.equal(describeSchedule("mon,thu 2:00", "en"), "Mon, Thu 02:00");
  assert.equal(describeSchedule("21:00", "fr"), "tous les jours 21:00");
  assert.equal(describeSchedule("*/5", "fr"), "*/5"); // not understood: shown as is
  const job = { job_id: "backup-7f3a2c", storage: "pbs-test", schedule: "sun 03:00" };
  assert.equal(jobLabel(job, translations.fr.jobsV2.name, "fr"), "Sauvegarde Proxmox → pbs-test · dim. 03:00");
  assert.ok(!jobLabel(job, translations.en.jobsV2.name, "en").includes("backup-7f3a2c"));
  assert.deepEqual(parseSchedule("sat..mon 1:00"), { days: ["mon", "sat", "sun"], time: "01:00" });
});

test("Proxmox job form ⇄ API values", () => {
  const form = formFromJob({
    job_id: "b1", storage: "pbs", schedule: "mon,wed 02:00", node: null, enabled: true, retention: "keep-daily=7,keep-weekly=4", selected_vmids: [101, 100],
  });
  assert.deepEqual([form.days, form.time, form.keepDaily, form.keepWeekly], [["mon", "wed"], "02:00", "7", "4"]);
  const values = formToValues(form);
  assert.equal(values.schedule, "mon,wed 02:00");
  assert.deepEqual(values.selected_vmids, [100, 101]);
  assert.equal(values.keep_daily, 7);
  assert.deepEqual(jobProblems({ ...form, storage: "", days: [], keepLast: "x" }), ["storage", "days", "keep"]);
});

function form(overrides: Partial<PlanForm> = {}): PlanForm {
  return { ...newPlanForm("main"), ...overrides };
}

test("planning form: sensible defaults and first occurrence computed", () => {
  const base = newPlanForm("main");
  assert.deepEqual([base.preset, base.weekdays, base.hour, base.windowMinutes, base.autoEject, base.confirmation, base.allGuests], ["weekly", [6], 9, 240, true, false, true]);
  // Next Sunday 09:00 Paris after Monday noon.
  assert.equal(firstOccurrence(base, NOW, PARIS).toISOString(), "2026-11-22T08:00:00.000Z");
  // Monday and Thursday: Thursday comes first (Monday 09:00 is already past).
  assert.equal(firstOccurrence(form({ weekdays: [0, 3] }), NOW, PARIS).toISOString(), "2026-11-19T08:00:00.000Z");
  // Monday at 18:00 is still today.
  assert.equal(firstOccurrence(form({ weekdays: [0], hour: 18 }), NOW, PARIS).toISOString(), "2026-11-16T17:00:00.000Z");
  // Monthly on the 31st: 30 November.
  assert.equal(firstOccurrence(form({ preset: "monthly", monthDay: 31 }), NOW, PARIS).toISOString(), "2026-11-30T08:00:00.000Z");
});

test("planning form: automatic name, live sentence, wait window", () => {
  assert.equal(autoTitle(form(), FR, "fr"), "Rotation chaque dimanche à 09:00");
  assert.equal(autoTitle(form({ preset: "biweekly", weekdays: [0, 3] }), FR, "fr"), "Rotation un lundi et jeudi sur 2 à 09:00");
  // B55: a custom schedule every 1 week reads naturally, never « tous les 1 ».
  assert.equal(autoTitle(form({ preset: "custom", unit: "weekly", interval: 1, weekdays: [2], hour: 19, minute: 37 }), FR, "fr"), "Rotation chaque mercredi à 19:37");
  assert.equal(
    planSentence(form({ diskSerials: ["a", "b"] }), ["WD 4 To", "Toshiba 3 To"], FR, "fr"),
    "Chaque dimanche à 09:00, sur WD 4 To puis Toshiba 3 To en alternance · Toutes vos sauvegardes (datastore main)",
  );
  assert.match(planSentence(form({ allGuests: false, vmGroups: ["vm/100", "ct/101"] }), [], FR, "fr"), /2 VM\/CT choisies$/);
  assert.deepEqual(windowEnd(form()), { time: "13:00", nextDay: false });
  assert.deepEqual(windowEnd(form({ hour: 22, windowMinutes: 240 })), { time: "02:00", nextDay: true });
});

test("planning form: inline validation", () => {
  assert.deepEqual(planProblems(form()), ["disks"]);
  assert.deepEqual(planProblems(form({ diskSerials: ["a"], weekdays: [], allGuests: false, datastore: "" })), ["weekdays", "guests", "datastore"]);
  assert.deepEqual(planProblems(form({ diskSerials: ["a"], titleTouched: true, title: " " })), ["title"]);
});

test("planning form: payload, and an edit keeps the first occurrence while the schedule is unchanged", () => {
  const created = planPayload(form({ diskSerials: ["WD-A", "TOSH-B"], weekdays: [0, 3] }), FR, "fr", NOW, PARIS);
  assert.equal(created.title, "Rotation chaque lundi et jeudi à 09:00");
  assert.deepEqual(created.recurrence_config, { interval: 1, weekdays: [0, 3] });
  assert.equal(created.window_starts_at, "2026-11-19T08:00:00.000Z");
  assert.deepEqual([created.disk_serial, created.vm_groups, created.start_mode, created.auto_eject_after_success], ["WD-A", [], "auto_on_disk_detected", true]);

  const event = { ...created, recurrence_type: "weekly" as const, window_starts_at: "2026-10-05T07:00:00Z" };
  const editForm = planFormFromEvent(event, PARIS);
  assert.equal(editForm.preset, "weekly");
  assert.equal(planPayload(editForm, FR, "fr", NOW, PARIS).window_starts_at, "2026-10-05T07:00:00Z"); // rotation order kept
  const moved = { ...editForm, hour: 10 };
  assert.notEqual(scheduleKey(moved), editForm.original?.key);
  assert.equal(planPayload(moved, FR, "fr", NOW, PARIS).window_starts_at, "2026-11-19T09:00:00.000Z");
});

test("datastore health: disk notes and the dashboard 'To do'", () => {
  assert.equal(pbsConfigNote({ state: "config_missing", datastore: "pbo-a", recoverable: true }), "missing_recoverable");
  assert.equal(pbsConfigNote({ state: "config_missing", datastore: "pbo-a", recoverable: false }), "missing");
  assert.equal(pbsConfigNote({ state: "conflict", datastore: "pbo-a" }), "conflict");
  assert.equal(pbsConfigNote({ state: "ok", datastore: "pbo-a" }), null);
  const items = todoItems({ disks: [], runs: [], scheduledRuns: [], agentConnected: true, agentConfigured: true, now: NOW, missingDatastore: "main" });
  assert.deepEqual(items.map((item) => [item.kind, item.label]), [["datastore_missing", "main"]]);
});
