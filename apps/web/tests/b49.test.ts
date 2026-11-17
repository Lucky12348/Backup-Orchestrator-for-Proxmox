// B49: an ejected disk is reactivated instead of unplugged; a launch refused
// because the disk is absent says so (and points to "Reactivate").
import assert from "node:assert/strict";
import test from "node:test";

import { translations } from "../src/i18n.ts";
import { noticeFromError, ApiError } from "../src/notices.ts";
import { diskActions } from "../src/views/disks/diskModel.ts";

test("an ejected disk offers Reactivate first", () => {
  const disk = { id: 1, serial_number: "S", display_name: "d", capacity_gb: 2000, connected: false, trusted: true, status: "ejected" };
  assert.deepEqual(diskActions(disk, "ejected"), ["reactivate", "replug", "rescan"]);
});

test("refusals and reactivation errors are translated", () => {
  for (const lang of ["fr", "en"] as const) {
    const t = translations[lang];
    for (const code of ["disk_not_present", "disk_not_present_reactivable", "disk_ejected"]) assert.ok((t.readiness as Record<string, string>)[code], code);
    for (const code of ["reactivate_failed", "reactivate_agent_outdated", "reactivate_not_ejected", "reactivate_unknown_port", "reactivate_run_active"]) {
      assert.ok((t.disksV2.errors as Record<string, string>)[code], code);
    }
    assert.ok(t.disksV2.action.reactivate && t.disksV2.action.reactivated.includes("{name}"));
  }
  assert.match(translations.fr.disksV2.errors.reactivate_failed, /débranchez-le puis rebranchez-le/);
  const notice = noticeFromError(new ApiError("absent", 409, "disk_not_present_reactivable"));
  assert.equal(notice.code, "disk_not_present_reactivable");
});
