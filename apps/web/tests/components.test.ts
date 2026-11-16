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
