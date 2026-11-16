import assert from "node:assert/strict";
import test from "node:test";

import { HANDOFF_CODES, handoffMessage, handoffStepLabel } from "../src/handoff.ts";

const texts = Object.fromEntries(HANDOFF_CODES.map((code) => [code, { step: `step:${code}`, message: `msg:${code}` }])) as never;

test("handoff failure codes and messages are translated", () => {
  assert.equal(handoffStepLabel("usb_device_ambiguous", texts), "step:usb_device_ambiguous");
  assert.equal(handoffStepLabel("sync", texts), null);
  assert.equal(handoffStepLabel(null, texts), null);
  assert.equal(handoffMessage("The disk is no longer plugged into the Proxmox host.", texts), "msg:disk_not_plugged");
  assert.equal(handoffMessage("Something else.", texts), null);
});
