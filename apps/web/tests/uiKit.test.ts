import assert from "node:assert/strict";
import test from "node:test";

import { newDiskCount } from "../src/shell/shellModel.ts";
import { addToast, makeToast, MAX_TOASTS, removeToast } from "../src/ui/toastStore.ts";

test("toasts: same id replaces, oldest dropped beyond the limit, sticky allowed", () => {
  let list = addToast([], makeToast({ id: "a", title: "A" }));
  list = addToast(list, makeToast({ id: "a", title: "A again" }));
  assert.deepEqual(list.map((toast) => toast.title), ["A again"]);
  for (let index = 0; index < MAX_TOASTS + 2; index += 1) list = addToast(list, makeToast({ title: `T${index}` }));
  assert.equal(list.length, MAX_TOASTS);
  assert.equal(list.at(-1)?.title, `T${MAX_TOASTS + 1}`);
  assert.deepEqual(removeToast(list, list[0].id).length, MAX_TOASTS - 1);
  // Phase 5h: info/success 8 s, warnings 12 s, errors stay until closed.
  assert.equal(makeToast({ title: "x", tone: "danger" }).duration, 0);
  assert.equal(makeToast({ title: "x", tone: "warning" }).duration, 12000);
  assert.equal(makeToast({ title: "x", tone: "success" }).duration, 8000);
  assert.equal(makeToast({ title: "x", duration: 0 }).duration, 0);
});

test("sidebar badge counts plugged, unapproved, usable disks", () => {
  assert.equal(
    newDiskCount([
      { connected: true, trusted: false, capacity_gb: 1000 },
      { connected: true, trusted: true, capacity_gb: 1000 },
      { connected: false, trusted: false, capacity_gb: 1000 },
      { connected: true, trusted: false, capacity_gb: 0 },
      { connected: true, trusted: false, capacity_gb: 500, candidate_type: "unusable" },
    ]),
    1,
  );
});
