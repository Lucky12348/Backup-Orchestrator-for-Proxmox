import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, Info, Trash2 } from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogTitle,
} from "../components/ui/alert-dialog";
import { buttonVariants } from "../components/ui/button";
import { cn } from "../components/ui/utils";

export interface ConfirmOptions {
  title: string;
  description?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  tone?: "danger" | "warning" | "info";
  /** Extra content under the description (e.g. an option checkbox). */
  extra?: ReactNode;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

/**
 * Replaces window.confirm everywhere: `if (!(await confirm({...}))) return;`.
 * One dialog at a time; Escape / Cancel resolve to false.
 */
export function ConfirmProvider({ children, defaultCancelLabel }: { children: ReactNode; defaultCancelLabel: string }) {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const resolver = useRef<((value: boolean) => void) | null>(null);

  const close = useCallback((value: boolean) => {
    resolver.current?.(value);
    resolver.current = null;
    setOptions(null);
  }, []);

  const confirm = useCallback<ConfirmFn>((next) => {
    resolver.current?.(false);
    setOptions(next);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <ConfirmDialog
        open={options !== null}
        options={options ?? { title: "", confirmLabel: "" }}
        cancelLabel={options?.cancelLabel ?? defaultCancelLabel}
        onConfirm={() => close(true)}
        onCancel={() => close(false)}
      />
    </ConfirmContext.Provider>
  );
}

const TONE = {
  danger: { icon: Trash2, badge: "text-danger-text", button: "bg-danger text-white hover:bg-danger/90" },
  warning: { icon: AlertTriangle, badge: "text-warning-text", button: "bg-warning text-black hover:bg-warning/90" },
  info: { icon: Info, badge: "text-accent-text", button: "" },
} as const;

/** Controlled confirmation dialog (also used by the legacy ConfirmModal API). */
export function ConfirmDialog({
  open,
  options,
  cancelLabel,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  options: ConfirmOptions;
  cancelLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const tone = TONE[options.tone ?? "warning"];
  const Icon = tone.icon;
  return (
    <AlertDialog open={open} onOpenChange={(next) => (!next ? onCancel() : undefined)}>
      <AlertDialogContent>
        <div className="flex items-start gap-3">
          <Icon className={cn("mt-[3px] h-4 w-4 shrink-0", tone.badge)} />
          <div className="flex min-w-0 flex-col gap-1.5">
            <AlertDialogTitle className="text-base font-semibold">{options.title}</AlertDialogTitle>
            {options.description ? (
              <AlertDialogDescription className="text-sm leading-relaxed text-muted">{options.description}</AlertDialogDescription>
            ) : null}
          </div>
        </div>
        {options.extra ? <div className="text-sm text-muted">{options.extra}</div> : null}
        <div className="flex flex-wrap justify-end gap-2">
          <AlertDialogCancel className={buttonVariants({ variant: "secondary" })} onClick={onCancel}>
            {cancelLabel}
          </AlertDialogCancel>
          <AlertDialogAction className={cn(buttonVariants(), tone.button)} onClick={onConfirm}>
            {options.confirmLabel}
          </AlertDialogAction>
        </div>
      </AlertDialogContent>
    </AlertDialog>
  );
}

export function useConfirm(): ConfirmFn {
  const confirm = useContext(ConfirmContext);
  if (!confirm) throw new Error("useConfirm outside ConfirmProvider");
  return confirm;
}
