// Context menus (Phase 5h): pure helpers (node:test).

/** Opens the menu from the keyboard: Shift+F10 or the "Menu" key. */
export function isMenuKey(event: { key: string; shiftKey: boolean }): boolean {
  return event.key === "ContextMenu" || (event.shiftKey && event.key === "F10");
}

/** Keeps the menu inside the viewport (8 px margin), flipped when needed. */
export function menuPosition(
  point: { x: number; y: number },
  size: { width: number; height: number },
  viewport: { width: number; height: number },
): { left: number; top: number } {
  const margin = 8;
  let left = point.x;
  let top = point.y;
  if (left + size.width + margin > viewport.width) left = Math.max(margin, point.x - size.width);
  if (top + size.height + margin > viewport.height) top = Math.max(margin, point.y - size.height);
  return { left: Math.max(margin, left), top: Math.max(margin, top) };
}

/** Next enabled item for ArrowUp / ArrowDown / Home / End (wraps around). */
export function nextIndex(current: number, key: string, disabled: readonly boolean[]): number {
  const count = disabled.length;
  if (!count) return -1;
  const enabled = disabled.map((value, index) => (value ? -1 : index)).filter((index) => index >= 0);
  if (!enabled.length) return -1;
  if (key === "Home") return enabled[0];
  if (key === "End") return enabled[enabled.length - 1];
  const step = key === "ArrowUp" ? -1 : 1;
  for (let offset = 1; offset <= count; offset += 1) {
    const index = (((current + step * offset) % count) + count) % count;
    if (!disabled[index]) return index;
  }
  return current;
}

export const LONG_PRESS_MS = 550;
