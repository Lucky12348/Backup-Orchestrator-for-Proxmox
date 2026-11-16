// Shell helpers, pure (node:test).

interface DiskLike {
  connected: boolean;
  trusted: boolean;
  capacity_gb?: number | null;
  candidate_type?: string | null;
}

/** Sidebar badge "N new" on Disks: plugged, not approved yet, usable. */
export function newDiskCount(disks: readonly DiskLike[]): number {
  return disks.filter(
    (disk) => disk.connected && !disk.trusted && (disk.capacity_gb ?? 0) > 0 && disk.candidate_type !== "unusable",
  ).length;
}
