// B56: whether a run can be cancelled now, pure (node:test).

export interface CancellableRun {
  id: number;
  disk_id: number;
  status: string;
  verify_only?: boolean | null;
  repair?: boolean | null;
  cancellable?: boolean;
  cancel_requested?: boolean;
}

export type CancelKind = "backup" | "verify" | "repair";

export function cancelKind(run: Pick<CancellableRun, "verify_only" | "repair">): CancelKind {
  return run.repair ? "repair" : run.verify_only ? "verify" : "backup";
}

/** The run can be cancelled now (running, not releasing/ejecting, not already asked). */
export function canCancel(run: CancellableRun | null | undefined): boolean {
  return Boolean(run && (run.status === "running" || run.status === "pending") && run.cancellable && !run.cancel_requested);
}

/** The run is stopping (asked, not ended yet). */
export function isStopping(run: CancellableRun | null | undefined): boolean {
  return Boolean(run && (run.status === "running" || run.status === "pending") && run.cancel_requested);
}
