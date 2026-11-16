import assert from "node:assert/strict";
import test from "node:test";

import {
  applyDiskEvent,
  diskPageUrl,
  focusedDiskId,
  formatCapacity,
  rejectReasonText,
  toastForDiskEvent,
  type DiskCard,
} from "../src/liveDisks.ts";

let nextId = 1;
function event(type: string, data: Record<string, unknown>) {
  return {
    id: nextId++,
    type,
    at: 0,
    data: {
      serial_number: "S1",
      display_name: "Disk S1",
      model_name: "Samsung T7",
      capacity_gb: 1000,
      connected: true,
      trusted: false,
      status: "new",
      reject_reason: null,
      min_size_gb: 34,
      ...data,
    },
  } as never;
}

test("step 5: waiting → disk detected → rejected disk → unplugged", () => {
  let cards: DiskCard[] = [];
  assert.equal(cards.length, 0); // "Waiting for a USB disk…"

  cards = applyDiskEvent(cards, event("disk.added", { id: 1 }));
  assert.equal(cards.length, 1);
  assert.equal(cards[0].status, "new");
  assert.equal(cards[0].title, "Samsung T7");
  assert.equal(cards[0].unplugged, false);

  cards = applyDiskEvent(
    cards,
    event("disk.added", { id: 2, serial_number: "CARD", model_name: "Card Reader", capacity_gb: 0, status: "rejected", reject_reason: "zero_size" }),
  );
  assert.equal(cards.length, 2);
  assert.equal(cards[0].id, 2, "newest card first");
  assert.equal(cards[0].status, "rejected");
  assert.equal(cards[0].rejectReason, "zero_size");

  cards = applyDiskEvent(cards, event("disk.removed", { id: 1, connected: false, status: "unplugged" }));
  const first = cards.find((card) => card.id === 1)!;
  assert.equal(first.unplugged, true);
  assert.equal(first.status, "new", "keeps what it was, shown as unplugged");
  assert.ok(first.revision > 1, "revision bump replays the animation");

  cards = applyDiskEvent(cards, event("disk.added", { id: 1 }));
  assert.equal(cards.find((card) => card.id === 1)!.unplugged, false, "plugged back in");
  assert.equal(cards.length, 2);
});

test("known disks, updates and unrelated events", () => {
  let cards = applyDiskEvent([], event("disk.added", { id: 5, trusted: true, status: "known" }));
  assert.equal(cards[0].status, "known");
  cards = applyDiskEvent(cards, event("disk.updated", { id: 5, trusted: true, capacity_gb: 2000 }));
  assert.equal(cards[0].capacityGb, 2000);
  assert.equal(applyDiskEvent(cards, event("disk.updated", { id: 99 })).length, 1, "updates never add cards");
  assert.equal(applyDiskEvent(cards, event("disk.removed", { id: 99 })).length, 1);
  assert.equal(applyDiskEvent(cards, event("run.progress", { id: 5 })), cards);
});

test("toast: new disk with Review action, rejected, unplugged", () => {
  const added = toastForDiskEvent(event("disk.added", { id: 7, capacity_gb: 2000 }), "fr")!;
  assert.equal(added.message, "Nouveau disque détecté : Samsung T7 2 To");
  assert.deepEqual(added.action, { label: "Examiner", diskId: 7 });
  assert.equal(added.tone, "info");

  const english = toastForDiskEvent(event("disk.added", { id: 7, capacity_gb: 500 }), "en")!;
  assert.equal(english.message, "New disk detected: Samsung T7 500 GB");

  const rejected = toastForDiskEvent(event("disk.added", { id: 8, capacity_gb: 16, reject_reason: "too_small" }), "fr")!;
  assert.equal(rejected.tone, "warning");
  assert.equal(rejected.action, undefined);
  assert.match(rejected.message, /Trop petit : il faut au moins 34 Go/);

  const removed = toastForDiskEvent(event("disk.removed", { id: 7 }), "fr")!;
  assert.equal(removed.message, "Disque débranché : Samsung T7");
  assert.equal(toastForDiskEvent(event("disk.updated", { id: 7 }), "fr"), null);
});

test("sizes, reasons and the review link", () => {
  assert.equal(formatCapacity(1000, "fr"), "1 To");
  assert.equal(formatCapacity(1500, "fr"), "1,5 To");
  assert.equal(formatCapacity(1500, "en"), "1.5 TB");
  assert.equal(formatCapacity(16000, "en"), "16 TB");
  assert.equal(formatCapacity(256, "fr"), "256 Go");
  assert.match(rejectReasonText("zero_size", 34, "en"), /Size 0/);
  assert.equal(rejectReasonText(null, 34, "fr"), "");
  assert.equal(diskPageUrl(12), "/disks?disk=12");
  assert.equal(focusedDiskId("?disk=12"), 12);
  assert.equal(focusedDiskId("?disk=abc"), null);
  assert.equal(focusedDiskId(""), null);
});
