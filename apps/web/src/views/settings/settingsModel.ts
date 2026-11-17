// Settings › Agents and disks (Phase 5g). Pure (node:test).

/** Valid minimum disk size (MIN_DISK_SIZE_GIB, 1–65536 GiB) or null. */
export function parseMinSize(value: string): number | null {
  if (!/^\d+$/.test(value.trim())) return null;
  const size = Number(value);
  return size >= 1 && size <= 65536 ? size : null;
}
