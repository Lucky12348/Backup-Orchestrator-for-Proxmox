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
        'export { ActivityRow, ActivityGroupRow } from "./src/views/activity/ActivityView";',
        'export { ResourcesView } from "./src/views/resources/ResourcesView";',
        'export { IntegrationCard } from "./src/views/integrations/IntegrationsView";',
        'export { ProxmoxJobsSection, DayPills } from "./src/views/resources/ProxmoxJobsSection";',
        'export { ChooseDatastoreDialog } from "./src/views/datastores/ChooseDatastoreDialog";',
        'export { PlanningDialog } from "./src/views/planning/PlanningDialog";',
        'export { UserMenu } from "./src/shell/AppShellV2";',
        'export { DiskAvatar, AlertBadges, PbsConfigNotice, DamagedVersions } from "./src/views/disks/DiskBits";',
        'export { ContentsCard } from "./src/views/disks/DiskSheetView";',
        'export { UsageChart } from "./src/views/disks/UsageChart";',
        'export { DiskCopies } from "./src/views/resources/ResourcesView";',
        'export { DiskSheetView } from "./src/views/disks/DiskSheetView";',
        'export { DiskTag, DiskDirectoryProvider } from "./src/views/disks/DiskTag";',
        'export { ActionMenuArea, ActionMenuButton } from "./src/ui/ActionMenu";',
        'export { Routes, Route } from "react-router-dom";',
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

test("StepList: 7 steps with their state", () => {
  const markup = html(
    createElement(ui.StepList, { states: ["done", "done", "done", "active", "upcoming", "upcoming", "upcoming"], t: translations.fr }),
  );
  for (const label of ["Détection", "Passthrough", "Montage", "Synchronisation", "Nettoyage", "Vérification", "Éjection sûre"]) {
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
    createElement(ui.ToastProvider, { closeLabel: "x", viewLabel: "Voir" }, createElement(ui.ConfirmProvider, { defaultCancelLabel: "Annuler" }, child)),
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
    inApp(createElement(ui.DiskCard, { row: { disk: baseDisk, status: "approved" }, selected: true, onSelect: () => undefined, language: "fr", t: translations.fr })),
  );
  assert.ok(markup.includes("WD 4 To"));
  // Phase 5h: the same actions on a right click and on the visible ⋯ button.
  assert.ok(markup.includes("data-context-menu") && markup.includes("data-menu-button") && markup.includes('aria-label="Actions — WD 4 To"'));
  assert.ok(markup.includes('data-status="approved"') && markup.includes("Approuvé"));
  // Phase 5f: used and total in one unit.
  assert.ok(markup.includes("0,71 / 3,57 Tio utilisés · 20 %"));
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
  // B42: everything is protected, but a new disk waits in "To do": attention, never green.
  assert.ok(markup.includes('data-protection="protected"') && markup.includes('data-state="attention"') && markup.includes("Attention requise"));
  assert.ok(!markup.includes("Tout est protégé") && markup.includes("1 point demande votre attention"));
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

// --- Phase 6b: Resources, Integrations, grouped activity -------------------------------

test("ResourcesView: table, protection badges (infrastructure), filters, last external copy", () => {
  const markup = html(
    inApp(
      createElement(ui.ResourcesView, {
        vms: [
          { id: 1, name: "web", vm_type: "vm", external_id: "100", node_name: "pve", runtime_status: "running", size_gb: 32, last_backup_at: "2026-11-15T03:00:00Z", last_external_copy_at: "2026-11-15T10:00:00Z", ignored: false, ignore_reason: null },
          { id: 2, name: "pbs", vm_type: "vm", external_id: "110", node_name: "pve", runtime_status: "running", size_gb: 64, last_backup_at: null, ignored: true, ignore_reason: "infrastructure" },
        ],
        jobs: [{ job_id: "backup-1", enabled: true, storage: "pbs", selection_mode: "include_selected_vms", selected_vmids: [100], supported: true }],
        language: "fr",
        t: translations.fr,
        onIgnore: () => undefined,
        onJobSelection: () => undefined,
        now: new Date("2026-11-16T12:00:00Z"),
      }),
    ),
  );
  assert.ok(markup.includes("<h1") && markup.includes("Ressources"));
  assert.ok(markup.includes('data-protection="protected"') && markup.includes('data-protection="infrastructure"'));
  assert.ok(markup.includes("Infrastructure") && markup.includes("Sauvegarde Proxmox → pbs"));
  assert.ok(!markup.includes(">backup-1<")); // the raw job id only in the job details
  assert.ok(markup.includes("Dernière copie externe") && markup.includes("32 Gio"));
  assert.ok(markup.includes("Nom ou VMID"));
});

test("IntegrationCard: state, version, latency, fingerprint, Test / Sync / Reconfigure", () => {
  const markup = html(
    inApp(
      createElement(ui.IntegrationCard, {
        integration: "pve",
        health: {
          key: "pve", configured: true, ok: true, code: null, url: "https://pve:8006/api2/json", fingerprint: "AA:BB:CC", node: "pve",
          version: "9.0.3", latency_ms: 42, last_sync_at: null, last_sync_error: null, message: null,
        },
        checking: false,
        syncing: false,
        language: "fr",
        t: translations.fr,
        now: new Date("2026-11-16T12:00:00Z"),
        onTest: () => undefined,
        onSync: () => undefined,
      }),
    ),
  );
  assert.ok(markup.includes('data-state="ok"') && markup.includes("Connecté"));
  assert.ok(markup.includes("9.0.3") && markup.includes("42 ms") && markup.includes('value="AA:BB:CC"'));
  assert.ok(markup.includes("Tester") && markup.includes("Synchroniser") && markup.includes("Reconfigurer"));
});

test("ActivityGroupRow: identical failures in one line ×N (folded)", () => {
  const failure = (id: string) => ({
    id, type: "backup", status: "failed", at: "2026-11-15T09:00:00Z", finished_at: null, duration_seconds: 30, disk_id: 1, disk_label: "WD",
    run_id: 1, planned: false, auto_eject: false, step: "disk_not_plugged", progress_percent: null, transferred_bytes: null, no_new_data: false, message: null,
  });
  const members = ["a", "b", "c", "d"].map(failure);
  const markup = html(
    inApp(
      createElement(ui.ActivityGroupRow, {
        group: { item: members[0], count: 4, members },
        summary: () => "Échec de la sauvegarde : Disque débranché",
        language: "fr",
        t: translations.fr,
        now: new Date("2026-11-16T12:00:00Z"),
      }),
    ),
  );
  assert.ok(markup.includes('data-group="4"') && markup.includes("×4") && markup.includes('aria-expanded="false"'));
  assert.equal((markup.match(/data-activity=/g) ?? []).length, 1); // folded: one visible line
});

// --- Phase 6c: Proxmox jobs section, day pills, PBS configuration notes ----------------

test("ProxmoxJobsSection: readable names, the raw id only in the folded details", () => {
  const markup = html(
    inApp(
      createElement(ui.ProxmoxJobsSection, {
        jobs: [
          {
            job_id: "backup-7f3a2c", enabled: true, node: null, schedule: "sun 03:00", storage: "pbs-test", retention: "keep-daily=7",
            selection_mode: "include_selected_vms", selected_vmids: [100], comment: null, next_run: null, supported: true,
            unsupported_reason: null, included_assets: [], available_assets: [],
          },
        ],
        vms: [{ id: 1, name: "web", vm_type: "vm", external_id: "100" }],
        language: "fr",
        t: translations.fr,
        onChanged: () => undefined,
      }),
    ),
  );
  assert.ok(markup.includes("Jobs de sauvegarde Proxmox") && markup.includes("Sauvegarde Proxmox → pbs-test · dim. 03:00"));
  assert.ok(markup.includes("Gérer la sélection") && markup.includes("Modifier") && markup.includes("Supprimer") && markup.includes("Nouveau job"));
  // Visible text: the raw id only inside the folded details (data-job is an attribute).
  assert.ok(markup.includes("<details") && markup.indexOf("backup-7f3a2c · ") > markup.indexOf("<details"));
  assert.equal((markup.match(/>backup-7f3a2c</g) ?? []).length, 0);
  assert.ok(markup.includes("web"));
});

test("DayPills: L M M J V S D, pressed state", () => {
  const markup = html(createElement(ui.DayPills, { days: ["sun"], onToggle: () => undefined, lang: "fr" }));
  assert.equal((markup.match(/aria-pressed="true"/g) ?? []).length, 1);
  assert.ok(markup.includes('aria-label="dimanche"') && markup.includes(">D<"));
});

test("DiskCard: 'PBS configuration missing' note", () => {
  const markup = html(
    inApp(
      createElement(ui.DiskCard, {
        row: { disk: baseDisk, status: "approved" },
        selected: false,
        onSelect: () => undefined,
        language: "fr",
        t: translations.fr,
        configNote: "missing_recoverable",
      }),
    ),
  );
  assert.ok(markup.includes('data-config="missing_recoverable"') && markup.includes("Configuration PBS supprimée · sera recréée"));
});

test("Planning and datastore dialogs render nothing while closed", () => {
  const datastore = html(inApp(createElement(ui.ChooseDatastoreDialog, { open: false, missing: "main", onClose: () => undefined, onChanged: () => undefined, language: "fr", t: translations.fr })));
  assert.ok(!datastore.includes("Choisir un autre datastore"));
  const planning = html(inApp(createElement(ui.PlanningDialog, { state: null, onClose: () => undefined, onSaved: () => undefined, disks: [], vms: [], defaultDatastore: "main", tz: "UTC", language: "fr", t: translations.fr })));
  assert.ok(!planning.includes("Quand ?"));
});

// --- Phase 5f: disk sheet, guards ----------------------------------------------------------------

test("DiskAvatar: kind icon in its colour, or the photo", () => {
  const icon = html(createElement(ui.DiskAvatar, { disk: { ...baseDisk, kind: "hdd_desktop", color: "violet" }, t: translations.fr }));
  assert.ok(icon.includes('data-kind="hdd_desktop"') && icon.includes("bg-violet-500/15") && icon.includes("Disque dur de bureau 3,5"));
  const photo = html(createElement(ui.DiskAvatar, { disk: { ...baseDisk, photo_version: "disk-1-ab.webp" }, t: translations.fr }));
  assert.ok(photo.includes('src="/api/v1/disks/1/photo?v=disk-1-ab.webp"') && photo.includes('alt="Photo de WD 4 To"'));
});

test("DiskCard: one line for its alerts (B42) and usage bar colour", () => {
  const alerts = [
    { disk_id: 1, disk_name: "WD 4 To", code: "usage_critical", level: "danger", params: { percent: 93.4 } },
    { disk_id: 1, disk_name: "WD 4 To", code: "usb2_slow", level: "warning", params: { speed_mbps: 480 } },
  ];
  const disk = { ...baseDisk, filesystem_used_gb: 3420 };
  const markup = html(
    inApp(createElement(ui.DiskCard, { row: { disk, status: "approved" }, alerts, selected: false, onSelect: () => undefined, language: "fr", t: translations.fr })),
  );
  // B42: the most important alert in one line, the others counted; the detail is in the panel.
  assert.ok(markup.includes('data-alert-line="usage_critical"') && markup.includes("Plein à 93 % · +1 autre(s)"));
  assert.ok(!markup.includes("data-alerts="));
  assert.ok(markup.includes("bg-danger"));
});

test("ContentsCard: VM/CT on the disk, as of the cached date", () => {
  const contents = {
    disk_id: 1,
    cached_at: "2026-11-10T08:00:00Z",
    total_versions: 5,
    items: [
      { group: "vm/100", type: "vm", id: "100", name: "web", versions: 4, oldest: "2026-10-20T01:00:00Z", newest: "2026-11-09T01:00:00Z", size_bytes: 32 * 1024 ** 3 },
      { group: "ct/101", type: "ct", id: "101", name: null, versions: 1, oldest: "2026-11-09T01:00:00Z", newest: "2026-11-09T01:00:00Z", size_bytes: null },
    ],
  };
  const markup = html(inApp(createElement(ui.ContentsCard, { disk: baseDisk, t: translations.fr, language: "fr", initial: contents })));
  assert.ok(markup.includes("Contenu") && markup.includes('data-contents="2"'));
  assert.ok(markup.includes("web") && markup.includes("vm/100") && markup.includes("32 Gio"));
  assert.ok(markup.includes("Plus ancienne") && markup.includes("Relire"));
});

test("UsageChart: one series with 80/90 % lines and a table view", () => {
  const points = [
    { at: "2026-10-01T00:00:00Z", time: Date.parse("2026-10-01T00:00:00Z"), percent: 40, usedGb: 1464, totalGb: 3660 },
    { at: "2026-11-01T00:00:00Z", time: Date.parse("2026-11-01T00:00:00Z"), percent: 85, usedGb: 3111, totalGb: 3660 },
  ];
  const markup = html(createElement(ui.UsageChart, { points, name: "WD 4 To", language: "fr", t: translations.fr }));
  assert.ok(markup.includes('role="img"') && markup.includes('aria-label="Occupation de WD 4 To dans le temps, en pourcentage"'));
  assert.ok(markup.includes('data-threshold="80"') && markup.includes('data-threshold="90"'));
  assert.ok(markup.includes("Afficher en tableau") && markup.includes("85 %") && markup.includes("3,04 / 3,57 Tio"));
  const empty = html(createElement(ui.UsageChart, { points: [], name: "x", language: "en", t: translations.en }));
  assert.ok(empty.includes("No usage measured yet"));
});

test("DiskCopies: the disks holding a copy of a VM, newest first", () => {
  const copies = [
    { disk_id: 1, disk_name: "WD 4 To", color: "blue", newest: "2026-11-09T01:00:00Z", versions: 4, cached_at: null },
    { disk_id: 2, disk_name: "Toshiba", color: null, newest: "2026-10-01T01:00:00Z", versions: 2, cached_at: null },
  ];
  const markup = html(inApp(createElement(ui.DiskCopies, { copies, language: "fr", now: new Date("2026-11-16T12:00:00Z"), t: translations.fr })));
  assert.ok(markup.includes('href="/disks/1"') && markup.includes("WD 4 To") && markup.includes("bg-sky-500"));
  assert.ok(html(inApp(createElement(ui.DiskCopies, { copies: [], language: "fr", now: new Date(), t: translations.fr }))).includes("sur aucun disque"));
  // Phase 5f.1: a disk whose contents were never read still shows what its backups copied.
  const fromRuns = html(
    inApp(
      createElement(ui.DiskCopies, {
        copies: [{ disk_id: 3, disk_name: "PBOTESTA", color: null, newest: "2026-11-15T18:00:00Z", versions: null, cached_at: "2026-11-15T18:00:00Z", source: "runs" }],
        language: "fr",
        now: new Date("2026-11-16T12:00:00Z"),
        t: translations.fr,
      }),
    ),
  );
  assert.ok(fromRuns.includes("PBOTESTA") && fromRuns.includes('data-source="runs"') && fromRuns.includes("D’après ses sauvegardes réussies"));
});

test("PbsConfigNotice: what happened, data safe, what to do (manual command when PBO cannot)", () => {
  const recoverable = html(createElement(ui.PbsConfigNotice, { note: "missing_recoverable", datastore: "pbo-pbotesta", serial: "PBOTESTA", t: translations.fr }));
  assert.ok(recoverable.includes("Configuration PBS supprimée · sera recréée") && recoverable.includes("toujours sur le disque"));
  assert.ok(!recoverable.includes("proxmox-backup-manager datastore create"));
  const missing = html(createElement(ui.PbsConfigNotice, { note: "missing", datastore: "pbo-pbotesta", serial: "PBOTESTA", t: translations.fr }));
  assert.ok(missing.includes("action requise") && missing.includes("--reuse-datastore true --comment pbo:serial=PBOTESTA"));
});

test("DamagedVersions: which versions, whether the source still has them, a Repair button", () => {
  const disk = {
    ...baseDisk,
    damaged_snapshots: [
      { key: "ct/101/1", group: "ct/101", time: "2026-11-17T04:05:47Z", in_source: true },
      { key: "ct/102/2", group: "ct/102", time: "2026-11-17T04:01:29Z", in_source: false },
    ],
  };
  const markup = html(inApp(createElement(ui.DamagedVersions, { disk, t: translations.fr })));
  assert.ok(markup.includes('data-damaged="2"') && markup.includes("2 version(s) abîmée(s) sur ce disque"));
  assert.ok(markup.includes("encore dans la source") && markup.includes("n’existe plus qu’abîmée ici"));
  assert.ok(markup.includes("data-repair=") && markup.includes("Réparer"));
});

// --- Phase 5h: identity everywhere, context menus, chart, journal --------------------------------

test("DiskTag: avatar, the operator's name, model and serial as a secondary line", () => {
  const disk = { ...baseDisk, display_name: "Rotation A", model_name: "QEMU HARDDISK", serial_number: "QM00005", color: "green" };
  const markup = html(createElement(ui.DiskDirectoryProvider, { disks: [disk] }, createElement(ui.DiskTag, { id: 1, t: translations.fr, secondary: true })));
  assert.ok(markup.includes('data-disk-tag="1"') && markup.includes("Rotation A") && markup.includes("QEMU HARDDISK · QM00005"));
  assert.ok(markup.indexOf("Rotation A") < markup.indexOf("QEMU HARDDISK"));
  const unknown = html(createElement(ui.DiskTag, { id: 9, fallback: "Ancien disque", color: "blue", t: translations.fr }));
  assert.ok(unknown.includes("Ancien disque") && unknown.includes("bg-sky-500"));
});

test("ActionMenuArea / ActionMenuButton: same actions, focusable for Shift+F10, labelled button", () => {
  const actions = [{ key: "open", label: "Ouvrir", onSelect: () => undefined }];
  const markup = html(
    inApp(createElement(ui.ActionMenuArea, { actions, label: "Actions — x" }, createElement(ui.ActionMenuButton, { actions, label: "Actions — x" }))),
  );
  assert.ok(markup.includes('tabindex="0"') && markup.includes('aria-haspopup="menu"') && markup.includes("data-context-menu"));
  assert.ok(markup.includes('aria-label="Actions — x"') && markup.includes("data-menu-button"));
  assert.equal(html(createElement(ui.ActionMenuButton, { actions: [], label: "x" })), ""); // nothing to offer: no button
});

test("DiskSheetView: the identity comes first, with the menu; usage chart text is HTML", () => {
  const disk = { ...baseDisk, usage_history: [{ at: "2026-11-01T00:00:00Z", used_gb: 700, total_gb: 3660 }, { at: "2026-11-10T00:00:00Z", used_gb: 900, total_gb: 3660 }] };
  const markup = html(
    createElement(
      ui.MemoryRouter,
      { initialEntries: ["/disks/1"] },
      createElement(ui.ToastProvider, { closeLabel: "x", viewLabel: "Voir" },
        createElement(ui.ConfirmProvider, { defaultCancelLabel: "Annuler" },
          createElement(ui.Routes, null, createElement(ui.Route, {
            path: "/disks/:id",
            element: createElement(ui.DiskSheetView, { disks: [disk], runs: [], alerts: [], language: "fr", t: translations.fr, onChanged: () => undefined }),
          })))),
    ),
  );
  assert.ok(markup.includes('id="identity"') && markup.includes("data-preview-name") && markup.includes("data-menu-button"));
  assert.ok(markup.indexOf("data-identity") < markup.indexOf("data-usage-chart"));
  // B38: no SVG <text> (it scaled with the width); labels and thresholds are HTML.
  const chart = markup.slice(markup.indexOf("data-usage-chart"));
  assert.ok(!chart.includes("<text") && chart.includes('data-threshold="80"') && chart.includes("50 %"));
});

test("LogLines: follows the end while running; no resume button at the bottom", () => {
  const entries = [{ at: "2026-11-18T01:00:00Z", step: "sync_job", source: "pbo", level: "info", code: null, params: {}, text: "line", count: 1 }];
  const running = html(createElement(ui.LogLines, { entries, templates: {}, empty: "-", follow: true, resumeLabel: "Reprendre le suivi" }));
  assert.ok(running.includes("data-following") && !running.includes("data-resume"));
  const ended = html(createElement(ui.LogLines, { entries, templates: {}, empty: "-", follow: false, resumeLabel: "Reprendre le suivi" }));
  assert.ok(!ended.includes("data-following"));
});
