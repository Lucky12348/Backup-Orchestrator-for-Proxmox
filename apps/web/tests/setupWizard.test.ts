import assert from "node:assert/strict";
import test from "node:test";

import {
  detectLanguage,
  formatCountdown,
  nextThemePreference,
  normalizeStep,
  parseApiError,
  previousStep,
  resolveTheme,
  secondsUntil,
  setupRedirect,
  suggestedPbsUrl,
} from "../src/setup/logic.ts";

test("every page leads to /setup until the setup is finished", () => {
  assert.equal(setupRedirect("/", false), "/setup");
  assert.equal(setupRedirect("/disks", false), "/setup");
  assert.equal(setupRedirect("/settings", false), "/setup");
  assert.equal(setupRedirect("/setup", false), null);
});

test("/setup leads to the dashboard once finished, other pages stay", () => {
  assert.equal(setupRedirect("/setup", true), "/");
  assert.equal(setupRedirect("/disks", true), null);
});

test("no redirect while the setup status is loading", () => {
  assert.equal(setupRedirect("/disks", null), null);
});

test("resume: the server step is used, unknown values fall back to the agent step", () => {
  assert.equal(normalizeStep("pbs"), "pbs");
  assert.equal(normalizeStep("done"), "done");
  assert.equal(normalizeStep("bogus"), "agent");
  assert.equal(normalizeStep(undefined), "agent");
});

test("back never returns to the account step nor leaves the done step", () => {
  assert.equal(previousStep("pbs"), "pve");
  assert.equal(previousStep("pve"), "agent");
  assert.equal(previousStep("agent"), null);
  assert.equal(previousStep("done"), null);
});

test("language follows the browser, English otherwise", () => {
  assert.equal(detectLanguage(["fr-FR", "en-US"]), "fr");
  assert.equal(detectLanguage(["de-DE", "en-GB"]), "en");
  assert.equal(detectLanguage(["de-DE"]), "en");
  assert.equal(detectLanguage([]), "en");
});

test("theme follows the system unless forced", () => {
  assert.equal(resolveTheme("system", true), "dark");
  assert.equal(resolveTheme("system", false), "light");
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(nextThemePreference("system"), "light");
  assert.equal(nextThemePreference("light"), "dark");
  assert.equal(nextThemePreference("dark"), "system");
});

test("API errors become a code and a sentence, never raw JSON", () => {
  assert.deepEqual(parseApiError(400, '{"detail":{"code":"bad_credentials","message":"PBS refused"}}'), {
    code: "bad_credentials",
    message: "PBS refused",
  });
  assert.deepEqual(parseApiError(400, '{"detail":"API token secret is required."}'), {
    code: "http_400",
    message: "API token secret is required.",
  });
  const validation = parseApiError(422, '{"detail":[{"loc":["body","api_url"],"msg":"Field required"}]}');
  assert.equal(validation.code, "validation");
  assert.equal(validation.message, "api_url: Field required");
  const html = parseApiError(502, "<html>Bad gateway</html>");
  assert.equal(html.message, "");
  assert.ok(!html.message.includes("{"));
});

test("pairing countdown", () => {
  const now = Date.parse("2026-11-15T10:00:00Z");
  assert.equal(secondsUntil("2026-11-15T10:14:30Z", now), 870);
  assert.equal(secondsUntil("2026-11-15T09:00:00Z", now), 0);
  assert.equal(formatCountdown(870), "14:30");
  assert.equal(formatCountdown(5), "0:05");
});

test("the PBS URL is pre-filled from the selected VM", () => {
  const vms = [
    { vmid: 110, suggested_url: "https://10.0.0.5:8007" },
    { vmid: 120, suggested_url: null },
  ];
  assert.equal(suggestedPbsUrl(vms, 110), "https://10.0.0.5:8007");
  assert.equal(suggestedPbsUrl(vms, 120), "");
  assert.equal(suggestedPbsUrl(vms, null), "");
});
