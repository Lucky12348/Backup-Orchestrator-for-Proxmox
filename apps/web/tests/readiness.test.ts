import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

import { readinessTitle } from "../src/readiness.ts";

const texts = {
  checking: "Checking…",
  unknown: "Not ready.",
  disk_not_connected: "Plug the disk first.",
  pbs_agent_missing: "No PBS agent.",
};

test("ready: no tooltip; unknown yet: checking", () => {
  assert.equal(readinessTitle({ ready: true, reasons: [] }, texts), undefined);
  assert.equal(readinessTitle(undefined, texts), "Checking…");
});

test("the tooltip gives the real reasons, translated", () => {
  const title = readinessTitle(
    {
      ready: false,
      reasons: [
        { code: "disk_not_connected", message: "The disk is not plugged in." },
        { code: "brand_new_code", message: "Server explanation." },
      ],
    },
    texts,
  );
  assert.equal(title, "Plug the disk first.\nServer explanation.");
});

test("API mode never asks to update a PBS agent", () => {
  // The old front-end check required a PBS agent even with PBS_MODE=api.
  const disks = readFileSync(join(import.meta.dirname, "..", "src", "pages", "DisksPage.tsx"), "utf8");
  assert.ok(!disks.includes("Mettre a jour l'agent PBS"));
  assert.ok(!disks.includes("agentsReadyForExternalBackup"));
  assert.ok(disks.includes("getExternalBackupReadiness"));
});
