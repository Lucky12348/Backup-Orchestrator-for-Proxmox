import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, Unplug, X, XCircle } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { cn } from "../components/ui/utils";
import { StatusIcon } from "./StatusIcon";
import { addToast, makeToast, remainingAfter, removeToast, type Toast, type ToastInput } from "./toastStore";

type ToastFn = (input: ToastInput) => string;

const ToastContext = createContext<{ toast: ToastFn; dismiss: (id: string) => void } | null>(null);

/** Global toasts (new UI). `useToast().toast({ title, tone, action, diskId })`.
 *  Info/success 8 s, warnings 12 s, errors until closed; the timer pauses while
 *  the pointer (or the focus) is on the toast; three at most. */
export function ToastProvider({
  children,
  closeLabel,
  viewLabel,
  renderDisk,
}: {
  children: ReactNode;
  closeLabel: string;
  /** Label of the action button of a toast that has a target ("See"). */
  viewLabel: string;
  /** The disk of a toast (colour, photo or icon), from the disk directory. */
  renderDisk?: (diskId: number) => ReactNode;
}) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const navigate = useNavigate();

  const dismiss = useCallback((id: string) => setToasts((current) => removeToast(current, id)), []);

  const toast = useCallback<ToastFn>((input) => {
    const next = makeToast(input);
    setToasts((current) => addToast(current, next));
    return next.id;
  }, []);

  return (
    <ToastContext.Provider value={{ toast, dismiss }}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[min(420px,calc(100vw-40px))] flex-col gap-2" role="status" aria-live="polite">
        {toasts.map((item) => (
          <ToastView
            key={`${item.id}:${item.seq}`}
            toast={item}
            closeLabel={closeLabel}
            viewLabel={viewLabel}
            disk={item.diskId !== undefined && renderDisk ? renderDisk(item.diskId) : null}
            onClose={() => dismiss(item.id)}
            onAction={() => {
              dismiss(item.id);
              if (item.action?.run) item.action.run();
              if (item.action?.href) navigate(item.action.href);
            }}
          />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

const ICONS = { info: Info, success: CheckCircle2, warning: AlertTriangle, danger: XCircle, neutral: Unplug } as const;
const ICON_COLORS = {
  info: "text-accent-text",
  success: "text-success-text",
  warning: "text-warning-text",
  danger: "text-danger-text",
  neutral: "text-faint",
} as const;

function ToastView({
  toast,
  closeLabel,
  viewLabel,
  disk,
  onClose,
  onAction,
}: {
  toast: Toast;
  closeLabel: string;
  viewLabel: string;
  disk: ReactNode;
  onClose: () => void;
  onAction: () => void;
}) {
  const Icon = ICONS[toast.tone];
  const [paused, setPaused] = useState(false);
  const remaining = useRef(toast.duration);
  useEffect(() => {
    if (!toast.duration || paused) return undefined;
    const startedAt = Date.now();
    const timer = window.setTimeout(onClose, remaining.current);
    return () => {
      window.clearTimeout(timer);
      remaining.current = remainingAfter(remaining.current, startedAt, Date.now());
    };
    // onClose changes on every render of the provider: the timer only follows pause/duration.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paused, toast.duration]);
  const label = toast.action?.label ?? (toast.action?.href ? viewLabel : undefined);
  return (
    <div
      className="pointer-events-auto flex items-start gap-3 rounded-xl border border-border-strong bg-hover px-4 py-3 shadow-2xl animate-fade-in"
      data-tone={toast.tone}
      data-sticky={toast.duration === 0 ? "" : undefined}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      onFocus={() => setPaused(true)}
      onBlur={() => setPaused(false)}
      role={toast.tone === "danger" ? "alert" : undefined}
    >
      {disk ??
        (toast.tone === "success" || toast.tone === "danger" ? (
          <StatusIcon kind={toast.tone === "success" ? "success" : "failed"} className="mt-0.5" />
        ) : (
          <Icon className={cn("mt-0.5 h-4 w-4", ICON_COLORS[toast.tone])} />
        ))}
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="m-0 text-sm font-medium text-fg">{toast.title}</p>
        {toast.description ? <p className="m-0 text-[13px] text-muted">{toast.description}</p> : null}
      </div>
      {label ? (
        <button type="button" onClick={onAction} className="shrink-0 rounded-md border-0 bg-transparent px-2 py-1 text-[13px] font-medium text-accent-text hover:bg-subtle">
          {label}
        </button>
      ) : null}
      <button type="button" onClick={onClose} aria-label={closeLabel} className="shrink-0 rounded-md border-0 bg-transparent p-1 text-faint hover:bg-subtle hover:text-fg">
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast outside ToastProvider");
  return context;
}
