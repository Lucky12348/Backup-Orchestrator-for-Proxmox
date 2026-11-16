import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, Unplug, X, XCircle } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { cn } from "../components/ui/utils";
import { addToast, makeToast, removeToast, type Toast, type ToastInput } from "./toastStore";

type ToastFn = (input: ToastInput) => string;

const ToastContext = createContext<{ toast: ToastFn; dismiss: (id: string) => void } | null>(null);

/** Global toasts (new UI). `useToast().toast({ title, tone, action })`. */
export function ToastProvider({ children, closeLabel }: { children: ReactNode; closeLabel: string }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef(new Map<string, number>());
  const navigate = useNavigate();

  const dismiss = useCallback((id: string) => {
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
    setToasts((current) => removeToast(current, id));
  }, []);

  const toast = useCallback<ToastFn>(
    (input) => {
      const next = makeToast(input);
      setToasts((current) => addToast(current, next));
      window.clearTimeout(timers.current.get(next.id));
      if (next.duration > 0) timers.current.set(next.id, window.setTimeout(() => dismiss(next.id), next.duration));
      return next.id;
    },
    [dismiss],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((timer) => window.clearTimeout(timer));
  }, []);

  return (
    <ToastContext.Provider value={{ toast, dismiss }}>
      {children}
      <div className="pointer-events-none fixed bottom-5 right-5 z-[60] flex w-[min(420px,calc(100vw-40px))] flex-col gap-2" role="status" aria-live="polite">
        {toasts.map((item) => (
          <ToastView
            key={item.id}
            toast={item}
            closeLabel={closeLabel}
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
  onClose,
  onAction,
}: {
  toast: Toast;
  closeLabel: string;
  onClose: () => void;
  onAction: () => void;
}) {
  const Icon = ICONS[toast.tone];
  return (
    <div className="pointer-events-auto flex items-start gap-3 rounded-xl border border-border-strong bg-hover px-4 py-3 shadow-2xl animate-fade-in">
      <Icon className={cn("mt-0.5 h-4 w-4", ICON_COLORS[toast.tone])} />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <p className="text-sm font-medium text-fg">{toast.title}</p>
        {toast.description ? <p className="text-[13px] text-muted">{toast.description}</p> : null}
      </div>
      {toast.action ? (
        <button
          type="button"
          onClick={onAction}
          className="shrink-0 rounded-md px-2 py-1 text-[13px] font-medium text-accent-text hover:bg-subtle"
        >
          {toast.action.label}
        </button>
      ) : null}
      <button type="button" onClick={onClose} aria-label={closeLabel} className="shrink-0 rounded-md p-1 text-faint hover:bg-subtle hover:text-fg">
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
