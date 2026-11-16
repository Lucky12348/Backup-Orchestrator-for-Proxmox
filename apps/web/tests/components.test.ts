// Component rendering tests: esbuild compiles the TSX components (node cannot
// strip JSX), react-dom/server renders them to static HTML.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import test, { before } from "node:test";

import { build } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { translations } from "../src/i18n.ts";

const WEB = join(import.meta.dirname, "..");
const OUT = join(import.meta.dirname, ".build", "components.mjs");
const FIXTURES = join(WEB, "..", "..", "docs", "design", "fixtures");

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let ui: Record<string, any>;

before(async () => {
  await build({
    stdin: {
      contents: [
        'export { StepList, LogLines } from "./src/runs/LiveRunModal";',
        'export { PageHeader } from "./src/components/PageHeader";',
        'export { EmptyState } from "./src/components/EmptyState";',
        'export { InfoTip } from "./src/ui/InfoTip";',
        'export { ConfirmModal } from "./src/components/ConfirmModal";',
        'export { DiskCard, DiskDetail, ApprovalFlow, StatusPill } from "./src/views/disks/DisksView";',
        'export { DashboardView } from "./src/views/dashboard/DashboardView";',
        'export { ToastProvider } from "./src/ui/toast";',
        'export { ConfirmProvider } from "./src/ui/confirm";',
        'export { MemoryRouter } from "react-router-dom";',
        'export { PlanningView, DetailCard, EventChip } from "./src/views/planning/PlanningView";',
        'export { ActivityRow } from "./src/views/activity/ActivityView";',
        'export { UserMenu } from "./src/shell/AppShellV2";',
      ].join("\n"),
      resolveDir: WEB,
      loader: "tsx",
    },
    bundle: true,
    format: "esm",
    platform: "node",
    packages: "external",
    jsx: "automatic",
    outfile: OUT,
    logLevel: "silent",
  });
  ui = await import(pathToFileURL(OUT).href);
});

const html = (element: unknown) => renderToStaticMarkup(element as never);

test("StepList: 6 steps with their state", () => {
  const markup = html(
    createElement(ui.StepList, { states: ["done", "done", "done", "active", "upcoming", "upcoming"], t: translations.fr }),
  );
  for (const label of ["Détection", "Passthrough", "Montage", "Synchronisation", "Vérification", "Éjection sûre"]) {
    assert.ok(markup.includes(label), label);
  }
  assert.equal((markup.match(/data-state="done"/g) ?? []).length, 3);
  assert.equal((markup.match(/data-state="active"/g) ?? []).length, 1);
  assert.ok(markup.includes('aria-label="Étapes"'));
});

test("LogLines: real journal, translated PBO lines, PBS lines tagged, no progress lines", () => {
  const { entries } = JSON.parse(readFileSync(join(FIXTURES, "backup-reuse.journal.json"), "utf8"));
  const markup = html(createElement(ui.LogLines, { entries, templates: translations.fr.runLog, empty: "—" }));
  assert.ok(markup.includes("Datastore existant réutilisé, sans formatage."));
  assert.ok(markup.includes('data-source="pbs"'));
  assert.ok(markup.includes("[ct/101]: re-sync snapshot"));
  assert.ok(!markup.includes("percentage done"));
  assert.equal((markup.match(/TASK OK/g) ?? []).length, 1);
});

test("PageHeader: no subtitle, an info button with the text in a tooltip", () => {
  const markup = html(createElement(ui.PageHeader, { title: "Disques", description: "Les disques de rotation." }));
  assert.ok(markup.includes("<h1"));
  assert.ok(markup.includes('role="tooltip"'));
  assert.ok(markup.includes("Les disques de rotation."));
  assert.ok(!markup.includes("page-description"));
});

test("EmptyState and InfoTip", () => {
  assert.ok(html(createElement(ui.EmptyState, { title: "Aucun disque", description: "Branchez-en un." })).includes("Aucun disque"));
  const tip = html(createElement(ui.InfoTip, { text: "Aide", label: "À propos" }));
  assert.ok(tip.includes('aria-label="À propos"') && tip.includes("Aide"));
});

test("ConfirmModal (shadcn AlertDialog) renders nothing while closed", () => {
  const markup = html(
    createElement(ui.ConfirmModal, {
      open: false,
      title: "Éjecter ?",
      description: "",
      confirmLabel: "Éjecter",
      cancelLabel: "Annuler",
      onConfirm: () => undefined,
      onCancel: () => undefined,
    }),
  );
  assert.equal(markup, "");
});

// --- Phase 5b: redesigned Disks page and Dashboard -------------------------------------

/** Router + toasts + dialogs, as inside AppShellV2. */
function inApp(child: unknown) {
  return createElement(
    ui.MemoryRouter,
    null,
    createElement(ui.ToastProvider, { closeLabel: "x" }, createElement(ui.ConfirmProvider, { defaultCancelLabel: "Annuler" }, child)),
  );
}

const baseDisk = {
  id: 1,
  serial_number: "WXD2DA1L1E7C",
  reported_serial_number: "WD-WXD2DA1L1E7C",
  display_name: "WD 4 To",
  model_name: "WDC WD40NMZW",
  capacity_gb: 4000,
  connected: true,
  trusted: true,
  dedicated_backup_disk: true,
  prepared_as_pbs_datastore: true,
  filesystem_total_gb: 3660,
  filesystem_used_gb: 727,
  handoff_status: null,
  pbs_datastore_name: "pbo-wxd2da1l1e7c",
  status: "known",
  reject_reason: null,
  operator_decision: null,
  partitions: [],
  usb_location: { port_id: "2-3" },
  last_seen_at: null,
};

test("DiskCard: name, status pill, capacity", () => {
  const markup = html(
    createElement(ui.DiskCard, { row: { disk: baseDisk, status: "approved" }, selected: true, onSelect: () => undefined, language: "fr", t: translations.fr }),
  );
  assert.ok(markup.includes("WD 4 To"));
  assert.ok(markup.includes('data-status="approved"') && markup.includes("Approuvé"));
  assert.ok(markup.includes("727 Gio utilisés sur 3,57 Tio"));
  assert.ok(markup.includes('aria-pressed="true"'));
});

function detail(disk: Record<string, unknown>, status: string, extra: Record<string, unknown> = {}) {
  return html(
    inApp(
      createElement(ui.DiskDetail, {
        row: { disk, status, ...extra },
        runs: [],
        language: "fr",
        t: translations.fr,
        onBackup: () => undefined,
        onEject: () => undefined,
        starting: false,
        ejecting: false,
        onRescan: () => undefined,
        rescanning: false,
        onChanged: () => undefined,
      }),
    ),
  );
}

test("B25: an ejected disk shows 'unplug then replug', no backup button", () => {
  const markup = detail({ ...baseDisk, connected: false, status: "ejected", handoff_status: "ejected" }, "ejected");
  assert.ok(markup.includes("Débranchez puis rebranchez le disque"));
  assert.ok(!markup.includes("Lancer une sauvegarde"));
  assert.ok(markup.includes("Relancer la détection"));
});

test("approved disk: backup button disabled until readiness answers; technical ids folded", () => {
  const markup = detail(baseDisk, "approved");
  assert.ok(markup.includes("Lancer une sauvegarde"));
  assert.match(markup, /<button[^>]*disabled=""[^>]*>.*?Lancer une sauvegarde/s);
  assert.ok(markup.includes("<details") && markup.includes("Identifiants techniques") && markup.includes("2-3"));
});

test("running disk: follow the backup with its percentage, eject only after it", () => {
  const markup = detail(baseDisk, "running", { run: { id: 9, disk_id: 1, status: "running", progress_percent: 42 } });
  assert.ok(markup.includes("Suivre la sauvegarde · 42 %"));
  assert.ok(markup.includes("Éjecter (après la sauvegarde)"));
});

test("new disk: approval flow with the data warning and a disabled approve button", () => {
  const disk = {
    ...baseDisk,
    display_name: "SanDisk",
    trusted: false,
    status: "new",
    filesystem_used_gb: 412,
    partitions: [{ name: "sdb1", filesystem_type: "exfat", size_gb: 931, label: "Photos-2024" }],
  };
  const markup = detail(disk, "new");
  assert.ok(markup.includes("Ce disque contient des données"));
  assert.ok(markup.includes("sdb1 · exfat"));
  assert.ok(markup.includes("Photos-2024"));
  assert.ok(markup.includes("Tapez") && markup.includes("SanDisk"));
  assert.match(markup, /<button[^>]*disabled=""[^>]*>Formater et approuver<\/button>/);
  assert.ok(markup.includes("Ignorer ce disque") && markup.includes("Toujours rejeter"));
});

test("DashboardView: protected block, next rotation, to do, rotation disks, last runs", () => {
  const markup = html(
    inApp(
      createElement(ui.DashboardView, {
        overview: { total_vms: 11, protected_vms: 11, ignored_vms: 2, infrastructure_ignored: 2, coverage_percent: 100, connected_disks: 1, latest_backup_status: "success", recent_backup_runs: [] },
        disks: [baseDisk, { ...baseDisk, id: 3, display_name: "SanDisk", trusted: false, status: "new" }],
        runs: [
          { id: 5, disk_id: 1, disk_name: "WD 4 To", status: "success", started_at: "2026-11-16T09:00:00Z", finished_at: "2026-11-16T10:00:00Z", current_step: "success", progress_percent: 100 },
        ],
        events: [{ id: 1, title: "Rotation hebdo", enabled: true, disk_serial: "WXD2DA1L1E7C", disk_label_or_model: null, next_occurrence_at: "2026-11-22T08:00:00Z" }],
        scheduledRuns: [],
        agentStatus: { status: "connected" },
        agentConfigured: true,
        pveConnected: true,
        pbsConnected: true,
        language: "fr",
        t: translations.fr,
        now: new Date("2026-11-16T12:00:00Z"),
      }),
    ),
  );
  assert.ok(markup.includes('data-protection="protected"') && markup.includes("Tout est protégé"));
  assert.ok(markup.includes("Vos 11 VM et conteneurs"));
  assert.ok(markup.includes("2 ressources ignorées") && markup.includes("dont 2 d&#x27;infrastructure"));
  assert.ok(markup.includes("Prochaine rotation") && markup.includes("Rotation hebdo") && markup.includes("Branché, prêt"));
  assert.ok(markup.includes('data-todo="new_disk"') && markup.includes("Nouveau disque : SanDisk"));
  assert.ok(markup.includes("Disques de rotation") && markup.includes("Dernières exécutions"));
  assert.ok(markup.includes("Tout est connecté"));
});

test("DashboardView: at risk", () => {
  const markup = html(
    inApp(
      createElement(ui.DashboardView, {
        overview: { total_vms: 10, protected_vms: 7, ignored_vms: 0, coverage_percent: 70, connected_disks: 0, latest_backup_status: null, recent_backup_runs: [] },
        disks: [],
        runs: [],
        events: [],
        scheduledRuns: [],
        agentStatus: { status: "disconnected" },
        agentConfigured: true,
        pveConnected: true,
        pbsConnected: false,
        language: "en",
        t: translations.en,
      }),
    ),
  );
  assert.ok(markup.includes('data-protection="at_risk"') && markup.includes("At risk"));
  assert.ok(markup.includes("3 of your 10 VMs"));
  assert.ok(markup.includes('data-todo="agent_offline"'));
  assert.ok(markup.includes("No rotation planned") && markup.includes("Check the integrations"));
});

// --- Phase 6: planning calendar, activity list, user menu ------------------------------

const agendaItem = {
  id: "plan-1",
  kind: "external",
  title: "Rotation hebdo",
  starts_at: "2026-11-15T08:00:00Z",
  ends_at: "2026-11-15T09:00:00Z",
  scheduled_for: "2026-11-15T08:00:00Z",
  window_ends_at: null,
  status: "running",
  event_id: 1,
  planning_run_id: 3,
  backup_run_id: 7,
  disk_serial: "WD-A",
  disk_label: "WD 4 To",
  progress_percent: 42,
  message: null,
  read_only: false,
};

test("PlanningView: toolbar, views, calendars and the export button (FR)", () => {
  const markup = html(
    inApp(
      createElement(ui.PlanningView, {
        events: [],
        disks: [],
        vms: [],
        defaultDatastore: "main",
        language: "fr",
        t: translations.fr,
        refreshKey: "",
        onChanged: () => undefined,
        fetchAgenda: () => new Promise(() => undefined),
        now: new Date("2026-11-16T12:00:00Z"),
      }),
    ),
  );
  for (const label of ["Nouvelle planification", "Aujourd&#x27;hui", "Jour", "Semaine", "Mois", "Année", "Exporter"]) {
    assert.ok(markup.includes(label), label);
  }
  for (const label of ["Backup externe", "Sync mensuelle", "Sauvegardes manuelles", "Jobs Proxmox (lecture seule)"]) {
    assert.ok(markup.includes(label), label);
  }
  assert.ok(markup.includes('data-period="week"'));
  assert.ok(markup.includes("Chargement du calendrier"));
});

test("DetailCard: state, schedule, disk and actions (running → follow live)", () => {
  const markup = html(
    createElement(ui.DetailCard, { item: agendaItem, tz: "Europe/Paris", lang: "fr", t: translations.fr, onAction: () => undefined }),
  );
  assert.ok(markup.includes("En cours · 42 %"));
  assert.ok(markup.includes("dimanche 15 novembre · 09:00 – 10:00")); // planning zone, not UTC
  assert.ok(markup.includes("WD 4 To"));
  assert.ok(markup.includes("Suivre en direct") && markup.includes("Modifier"));
});

test("EventChip: colour by state, running is striped", () => {
  const running = html(createElement(ui.EventChip, { item: agendaItem, tz: "Europe/Paris", t: translations.fr, selected: false, onSelect: () => undefined, compact: true }));
  assert.ok(running.includes('data-tone="running"') && running.includes("pbo-stripes"));
  const failed = html(
    createElement(ui.EventChip, { item: { ...agendaItem, status: "failed" }, tz: "Europe/Paris", t: translations.fr, selected: false, onSelect: () => undefined, compact: true }),
  );
  assert.ok(failed.includes('data-tone="failed"') && failed.includes("✕"));
  const confirm = html(
    createElement(ui.EventChip, { item: { ...agendaItem, status: "to_confirm" }, tz: "Europe/Paris", t: translations.en, selected: false, onSelect: () => undefined, compact: true }),
  );
  assert.ok(confirm.includes('data-tone="confirm"'));
});

test("DetailCard: a run waiting for confirmation offers Confirm / Cancel", () => {
  const markup = html(
    createElement(ui.DetailCard, {
      item: { ...agendaItem, status: "to_confirm", backup_run_id: null },
      tz: "Europe/Paris",
      lang: "en",
      t: translations.en,
      onAction: () => undefined,
    }),
  );
  assert.ok(markup.includes("To confirm") && markup.includes(">Confirm<") && markup.includes(">Cancel<"));
});

test("ActivityRow: icon state, type, disk, summary, duration; a run opens the modal", () => {
  const markup = html(
    inApp(
      createElement(ui.ActivityRow, {
        item: {
          id: "run-1", type: "backup", status: "failed", at: "2026-11-16T09:00:00Z", finished_at: null, duration_seconds: 125,
          disk_id: 1, disk_label: "WD 4 To", run_id: 1, planned: true, auto_eject: false, step: "disk_not_plugged",
          progress_percent: null, transferred_bytes: null, no_new_data: false, message: "x",
        },
        summary: "Échec de la sauvegarde : Disque débranché",
        language: "fr",
        t: translations.fr,
        now: new Date("2026-11-16T12:00:00Z"),
      }),
    ),
  );
  assert.ok(markup.includes('data-state="failed"') && markup.includes("Sauvegarde") && markup.includes("WD 4 To"));
  assert.ok(markup.includes("planifiée") && markup.includes("2 min"));
  assert.ok(markup.includes("<button") && markup.includes("Ouvrir"));
});

test("UserMenu: avatar, name and role in one button (the menu opens on click)", () => {
  const markup = html(
    createElement(ui.UserMenu, {
      t: translations.fr,
      username: "admin",
      language: "fr",
      onLanguageChange: () => undefined,
      themePreference: "system",
      onThemeChange: () => undefined,
      onLogout: () => undefined,
    }),
  );
  assert.ok(markup.includes('aria-haspopup="menu"') && markup.includes('aria-expanded="false"'));
  assert.ok(markup.includes("admin") && markup.includes("Administrateur"));
  assert.ok(!markup.includes("<select")); // no more stacked selector on the same line
});
