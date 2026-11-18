// B55: the ⌘K command palette, pure parts (node:test): entries, search,
// grouping, keyboard selection.

export type PaletteGroup = "actions" | "pages" | "disks" | "guests" | "runs" | "settings";
export const PALETTE_GROUPS: PaletteGroup[] = ["actions", "pages", "disks", "guests", "runs", "settings"];

export type PaletteTarget =
  | { kind: "navigate"; to: string }
  | { kind: "backup"; diskId: number }
  | { kind: "eject"; diskId: number }
  | { kind: "verify"; diskId: number }
  | { kind: "run"; runId: number }
  | { kind: "theme"; value: "system" | "light" | "dark" }
  | { kind: "language"; value: "en" | "fr" };

export interface PaletteEntry {
  id: string;
  group: PaletteGroup;
  label: string;
  /** Second line: serial, node, date… */
  hint?: string;
  /** Extra words that find it (serial, model, VMID…). */
  keywords?: string;
  target: PaletteTarget;
  /** Shown when the query is empty (common actions, every page). */
  suggested?: boolean;
  diskId?: number;
}

/** Lower case, no accents: "Réglages" is found with "reglages". */
export function normalize(text: string): string {
  return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
}

/** 0 = no match. Every word of the query must be found; the label counts most. */
export function score(entry: PaletteEntry, query: string): number {
  const words = normalize(query).split(/\s+/).filter(Boolean);
  if (!words.length) return entry.suggested ? 1 : 0;
  const label = normalize(entry.label);
  const rest = normalize(`${entry.hint ?? ""} ${entry.keywords ?? ""}`);
  let total = 0;
  for (const word of words) {
    if (label.startsWith(word)) total += 6;
    else if (new RegExp(`(^|[\\s·/(«“-])${word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`).test(label)) total += 4;
    else if (label.includes(word)) total += 2;
    else if (rest.includes(word)) total += 1;
    else return 0;
  }
  return total;
}

export interface PaletteSection {
  group: PaletteGroup;
  entries: PaletteEntry[];
}

/** Results grouped (fixed group order), best first inside a group, a few per group. */
export function searchPalette(entries: readonly PaletteEntry[], query: string, perGroup = 6): PaletteSection[] {
  const scored = entries.map((entry) => ({ entry, value: score(entry, query) })).filter((item) => item.value > 0);
  return PALETTE_GROUPS.map((group) => ({
    group,
    entries: scored
      .filter((item) => item.entry.group === group)
      .sort((a, b) => b.value - a.value || a.entry.label.localeCompare(b.entry.label))
      .slice(0, perGroup)
      .map((item) => item.entry),
  })).filter((section) => section.entries.length > 0);
}

/** The flat order of the keyboard selection. */
export function flatten(sections: readonly PaletteSection[]): PaletteEntry[] {
  return sections.flatMap((section) => section.entries);
}

/** ↑ / ↓ wrap around; Home / End. */
export function moveSelection(index: number, count: number, key: string): number {
  if (!count) return -1;
  if (key === "ArrowDown") return (index + 1 + count) % count;
  if (key === "ArrowUp") return (index - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return index;
}

/** "⌘K" on Apple systems, "Ctrl K" elsewhere. */
export function shortcutLabel(platform: string): string {
  return /mac|iphone|ipad|ipod/i.test(platform) ? "⌘K" : "Ctrl K";
}

// --- open from anywhere (header button, mobile magnifier, ⌘K) -------------------------

export const OPEN_PALETTE_EVENT = "pbo:open-palette";

export function openCommandPalette(): void {
  window.dispatchEvent(new CustomEvent(OPEN_PALETTE_EVENT));
}
