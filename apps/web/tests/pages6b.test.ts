// Phase 6b: Resources, Integrations, Login, wizard reconfiguration.
import assert from "node:assert/strict";
import test from "node:test";

import { translations } from "../src/i18n.ts";
import { loginMessage, waitText } from "../src/loginModel.ts";
import { reconfigureStep, setupRedirect } from "../src/setup/logic.ts";
import { integrationState, splitAgents } from "../src/views/integrations/integrationsModel.ts";
import {
  editableJobs,
  filterResources,
  jobsFor,
  nextSelection,
  protectionCounts,
  protectionOf,
  type JobLike,
  type VmLike,
} from "../src/views/resources/resourcesModel.ts";

function vm(overrides: Partial<VmLike> = {}): VmLike {
  return {
    id: 1,
    name: "web",
    vm_type: "vm",
    external_id: "100",
    node_name: "pve",
    runtime_status: "running",
    size_gb: 32,
    last_backup_at: "2026-11-15T03:00:00Z",
    last_external_copy_at: null,
    ignored: false,
    ignore_reason: null,
    ...overrides,
  };
}

const job: JobLike = { job_id: "backup-1", enabled: true, storage: "pbs", selection_mode: "include_selected_vms", selected_vmids: [100, 102], supported: true };

test("resources: protection states, infrastructure badge, counts", () => {
  const list = [
    vm(),
    vm({ id: 2, external_id: "101", last_backup_at: null }),
    vm({ id: 3, external_id: "102", ignored: true, ignore_reason: "test VM" }),
    vm({ id: 4, external_id: "105", vm_type: "ct", name: "pbo", ignored: true, ignore_reason: "infrastructure" }),
  ];
  assert.deepEqual(list.map(protectionOf), ["protected", "unprotected", "ignored", "infrastructure"]);
  assert.deepEqual(protectionCounts(list), { protected: 1, unprotected: 1, ignored: 1, infrastructure: 1 });
  const base = { query: "", type: "", protection: "", node: "" } as const;
  assert.deepEqual(filterResources(list, { ...base, type: "ct" }).map((item) => item.id), [4]);
  assert.deepEqual(filterResources(list, { ...base, protection: "unprotected" }).map((item) => item.id), [2]);
  assert.deepEqual(filterResources(list, { ...base, query: "10" }).map((item) => item.id), [1, 2, 3, 4]);
  assert.deepEqual(filterResources(list, { ...base, query: "102" }).map((item) => item.id), [3]);
});

test("resources: jobs of a VM and bulk add / remove", () => {
  assert.deepEqual(jobsFor(vm(), [job]).map((item) => item.job_id), ["backup-1"]);
  assert.deepEqual(jobsFor(vm({ external_id: "101" }), [job]), []);
  assert.deepEqual(nextSelection(job, [101, 100], "add"), [100, 101, 102]);
  assert.deepEqual(nextSelection(job, [100], "remove"), [102]);
  assert.deepEqual(editableJobs([job, { ...job, job_id: "x", supported: false }]).map((item) => item.job_id), ["backup-1"]);
});

test("integrations: card state and agents (active shown, revoked in the history)", () => {
  assert.equal(integrationState(undefined, false), "checking");
  assert.equal(integrationState({ ok: true, code: null }, true), "checking");
  assert.equal(integrationState({ ok: true, code: null }, false), "ok");
  assert.equal(integrationState({ ok: false, code: "not_configured" }, false), "unconfigured");
  assert.equal(integrationState({ ok: false, code: "unreachable" }, false), "down");
  const { active, revoked } = splitAgents([
    { id: 3, revoked_at: null },
    { id: 2, revoked_at: "2026-11-10T10:00:00Z" },
    { id: 1, revoked_at: "2026-11-12T10:00:00Z" },
  ]);
  assert.deepEqual(active.map((agent) => agent.id), [3]);
  assert.deepEqual(revoked.map((agent) => agent.id), [1, 2]);
});

test("login: bad credentials, attempts left, lock explained with its duration", () => {
  const texts = translations.fr.login;
  const now = new Date(2026, 10, 16, 14, 0, 0).getTime();
  assert.equal(loginMessage({ status: 401, details: { attempts_left: 4, lock_minutes: 5 } }, texts, now, "fr").text, "Nom d'utilisateur ou mot de passe incorrect.");
  assert.match(loginMessage({ status: 401, details: { attempts_left: 2, lock_minutes: 5 } }, texts, now, "fr").text, /Encore 2 essai\(s\) avant un blocage de 5 minutes/);
  assert.match(loginMessage({ status: 401, details: { attempts_left: 0, lock_minutes: 5 } }, texts, now, "fr").text, /bloquée 5 minutes/);
  const locked = loginMessage({ status: 429, details: { retry_after: 245 } }, texts, now, "fr");
  assert.match(locked.text, /Réessayez dans 4 min 05 s \(à 14:04\)/);
  assert.equal(locked.lockedUntil, now + 245_000);
  assert.equal(loginMessage({ status: 500 }, translations.en.login, now, "en").text, translations.en.login.failed);
  assert.equal(waitText(35), "35 s");
});

test("Integrations 'Reconfigure': /setup/reconfigure/<step> opens that wizard step once set up", () => {
  assert.equal(reconfigureStep("/setup/reconfigure/pve"), "pve");
  assert.equal(reconfigureStep("/setup/reconfigure/agent/"), "agent");
  assert.equal(reconfigureStep("/setup/reconfigure/admin"), null);
  assert.equal(setupRedirect("/setup/reconfigure/pbs", true), null);
  assert.equal(setupRedirect("/setup", true), "/");
  assert.equal(setupRedirect("/integrations", false), "/setup");
});
