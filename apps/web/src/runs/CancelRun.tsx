// B56: one way to cancel a run, everywhere it shows (modal, top-bar pill,
// ⋯ menu, dashboard). Always confirmed; the server stops the PBS task,
// releases the disk and ends the run as "cancelled" (services/run_control.py).
import { Loader2, Square } from "lucide-react";

import { cancelExternalBackupRun } from "../api";
import type { TranslationDictionary } from "../i18n";
import { ApiError } from "../notices";
import { cn } from "../components/ui/utils";
import { useConfirm } from "../ui/confirm";
import { useToast } from "../ui/toast";

import { canCancel, cancelKind, isStopping, type CancellableRun } from "./cancelModel";

export { canCancel, cancelKind, isStopping, type CancellableRun } from "./cancelModel";

export function cancelConfirmOptions(run: CancellableRun, t: TranslationDictionary) {
  const text = t.runModal;
  return {
    title: text.cancelTitle[cancelKind(run)],
    description: text.cancelConfirm,
    confirmLabel: text.confirmCancel,
  };
}

/** `cancel(run)` without a question (the caller asked); `confirmAndCancel(run)` asks first. */
export function useCancelRun(t: TranslationDictionary, onDone?: () => void) {
  const confirm = useConfirm();
  const { toast } = useToast();
  const text = t.runModal;

  const cancel = async (run: CancellableRun): Promise<boolean> => {
    try {
      await cancelExternalBackupRun(run.id);
      toast({ tone: "warning", title: text.cancelRequested, description: text.cancelRequestedHint, diskId: run.disk_id, inbox: false });
      onDone?.();
      return true;
    } catch (error) {
      const code = error instanceof ApiError ? error.code : undefined;
      const known = code ? (text.cancelErrors as Record<string, string>)[code] : undefined;
      toast({ tone: "danger", title: text.cancelFailed, description: known ?? (error instanceof Error ? error.message : undefined), diskId: run.disk_id });
      onDone?.();
      return false;
    }
  };

  const confirmAndCancel = async (run: CancellableRun): Promise<boolean> => {
    const ok = await confirm({ ...cancelConfirmOptions(run, t), cancelLabel: text.keepGoing, tone: "danger" });
    return ok ? cancel(run) : false;
  };

  return { cancel, confirmAndCancel };
}

/** "Cancel" button (or "Stopping…" once asked). Renders nothing for a run that cannot be cancelled. */
export function CancelRunButton({
  run,
  t,
  onDone,
  compact = false,
  className,
}: {
  run: CancellableRun;
  t: TranslationDictionary;
  onDone?: () => void;
  compact?: boolean;
  className?: string;
}) {
  const { confirmAndCancel } = useCancelRun(t, onDone);
  const text = t.runModal;
  if (isStopping(run)) {
    return (
      <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap text-[13px] text-muted", className)} role="status" data-run-stopping={run.id}>
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
        {compact ? null : text.stopping}
      </span>
    );
  }
  if (!canCancel(run)) return null;
  const label = text.cancelKind[cancelKind(run)];
  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        void confirmAndCancel(run);
      }}
      aria-label={label}
      title={compact ? label : undefined}
      data-run-cancel={run.id}
      className={cn(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border border-solid border-border bg-transparent text-[13px] font-medium text-danger-text hover:bg-hover",
        compact ? "h-8 w-8 justify-center p-0" : "px-3 py-1.5",
        className,
      )}
    >
      <Square className="h-3.5 w-3.5" />
      {compact ? null : label}
    </button>
  );
}
