// Pure helpers for live disk events (wizard step 5 cards, global toasts).
// No runtime import: run by node:test.
import type { AppEvent, DiskEventData } from "./events";
import { formatGib } from "./units.ts";

export type Lang = "fr" | "en";

export interface DiskCard {
  id: number;
  serial: string;
  title: string;
  model: string | null;
  capacityGb: number;
  status: "new" | "known" | "rejected";
  rejectReason: DiskEventData["reject_reason"];
  minSizeGb: number;
  unplugged: boolean;
  /** Bumped on every change: lets the UI replay the "appear" animation. */
  revision: number;
}

/** capacity_gb is GiB (agent `bytes_to_gb`): "931 Gio" / "3,57 Tio". */
export function formatCapacity(gb: number, lang: Lang): string {
  return formatGib(gb, lang);
}

export function rejectReasonText(reason: DiskEventData["reject_reason"], minSizeGb: number, lang: Lang, sizeGb?: number): string {
  if (reason === "zero_size") {
    return lang === "fr"
      ? "Taille 0 : le disque ne répond pas (câble, port USB ou boîtier défaillant, ou lecteur de carte vide)."
      : "Size 0: the disk does not answer (faulty cable, USB port or enclosure, or an empty card reader).";
  }
  if (reason === "too_small") {
    // Phase 5g: the disk's size next to the minimum.
    const size = sizeGb === undefined ? "" : `${formatCapacity(sizeGb, lang)}, `;
    return lang === "fr"
      ? `Trop petit : ${size}il faut au moins ${minSizeGb} Gio pour un datastore PBS.`
      : `Too small: ${size}a PBS datastore needs at least ${minSizeGb} GiB.`;
  }
  return "";
}

function cardTitle(data: DiskEventData): string {
  return data.model_name || data.display_name || data.serial_number;
}

/** step 5 / any live list: one card per disk, unplugged ones stay visible. */
export function applyDiskEvent(cards: DiskCard[], event: AppEvent): DiskCard[] {
  if (!event.type.startsWith("disk.")) return cards;
  const data = event.data as DiskEventData;
  if (!data || typeof data.id !== "number") return cards;
  const existing = cards.find((card) => card.id === data.id);
  const status: DiskCard["status"] = data.reject_reason ? "rejected" : data.trusted ? "known" : "new";

  if (event.type === "disk.removed") {
    if (!existing) return cards;
    return cards.map((card) => (card.id === data.id ? { ...card, unplugged: true, revision: card.revision + 1 } : card));
  }
  if (event.type === "disk.updated" && !existing) return cards; // only cards already shown
  const next: DiskCard = {
    id: data.id,
    serial: data.serial_number,
    title: cardTitle(data),
    model: data.model_name,
    capacityGb: data.capacity_gb,
    status,
    rejectReason: data.reject_reason,
    minSizeGb: data.min_size_gb,
    unplugged: event.type === "disk.added" ? false : existing?.unplugged ?? false,
    revision: (existing?.revision ?? 0) + 1,
  };
  if (existing) return cards.map((card) => (card.id === data.id ? next : card));
  return [next, ...cards];
}

export interface DiskToast {
  key: string;
  tone: "info" | "warning" | "neutral";
  message: string;
  action?: { label: string; diskId: number };
}

export function toastForDiskEvent(event: AppEvent, lang: Lang): DiskToast | null {
  const data = event.data as DiskEventData;
  if (!data || typeof data.id !== "number") return null;
  // Phase 5g: a disk the operator hid makes no noise.
  if ((data as { status?: string }).status === "hidden") return null;
  const name = cardTitle(data);
  const size = formatCapacity(data.capacity_gb, lang);
  const sep = lang === "fr" ? " : " : ": ";
  if (event.type === "disk.added" && data.verify_recommended && !data.reject_reason) {
    return {
      key: `${event.id}`,
      tone: "warning",
      message:
        lang === "fr"
          ? `Disque rebranché${sep}${name} — il avait été retiré sans éjection : lancez une vérification de sa copie.`
          : `Disk plugged back in${sep}${name} — it was removed without eject: run a verification of its copy.`,
      action: { label: lang === "fr" ? "Vérifier" : "Verify", diskId: data.id },
    };
  }
  if (event.type === "disk.added") {
    if (data.reject_reason) {
      return {
        key: `${event.id}`,
        tone: "warning",
        message: `${lang === "fr" ? "Disque rejeté" : "Disk rejected"}${sep}${name} — ${rejectReasonText(data.reject_reason, data.min_size_gb, lang, data.capacity_gb)}`,
      };
    }
    return {
      key: `${event.id}`,
      tone: "info",
      message: `${lang === "fr" ? "Nouveau disque détecté" : "New disk detected"}${sep}${name} ${size}`,
      action: { label: lang === "fr" ? "Examiner" : "Review", diskId: data.id },
    };
  }
  if (event.type === "disk.removed" && data.status === "pulled") {
    // Phase 5f.2: pulled out while PBS was using it.
    return {
      key: `${event.id}`,
      tone: "warning",
      message:
        lang === "fr"
          ? `Disque retiré sans éjection${sep}${name} — PBO l’a détaché de PBS ; la dernière copie est peut-être incomplète. Rebranchez-le puis lancez une vérification.`
          : `Disk removed without eject${sep}${name} — PBO detached it from PBS; the last copy may be incomplete. Plug it back in, then run a verification.`,
      action: { label: lang === "fr" ? "Voir" : "See", diskId: data.id },
    };
  }
  if (event.type === "disk.removed") {
    return {
      key: `${event.id}`,
      tone: "neutral",
      message: `${lang === "fr" ? "Disque débranché" : "Disk unplugged"}${sep}${name}`,
    };
  }
  return null;
}

/** /disks?disk=<id> — "Examine this disk". */
export function diskPageUrl(diskId: number): string {
  return `/disks?disk=${diskId}`;
}

export function focusedDiskId(search: string): number | null {
  const value = Number(new URLSearchParams(search).get("disk"));
  return Number.isInteger(value) && value > 0 ? value : null;
}
