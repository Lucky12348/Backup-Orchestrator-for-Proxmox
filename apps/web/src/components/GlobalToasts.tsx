import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import type { Language } from "../i18n";
import { diskPageUrl, toastForDiskEvent, type DiskToast } from "../liveDisks";
import { useEvents } from "../useEvents";

const TOAST_MS = 8000;
const MAX_TOASTS = 4;

/** App-wide toasts for live disk events ("New disk detected … — Review"). */
export function GlobalToasts({ language }: { language: Language }) {
  const navigate = useNavigate();
  const [toasts, setToasts] = useState<DiskToast[]>([]);
  const timers = useRef(new Map<string, number>());

  function dismiss(key: string) {
    window.clearTimeout(timers.current.get(key));
    timers.current.delete(key);
    setToasts((current) => current.filter((toast) => toast.key !== key));
  }

  useEvents(["disk.added", "disk.removed"], (event) => {
    const toast = toastForDiskEvent(event, language === "fr" ? "fr" : "en");
    if (!toast) return;
    setToasts((current) => [...current.filter((item) => item.key !== toast.key), toast].slice(-MAX_TOASTS));
    timers.current.set(toast.key, window.setTimeout(() => dismiss(toast.key), TOAST_MS));
  });

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((timer) => window.clearTimeout(timer));
  }, []);

  if (toasts.length === 0) return null;
  return (
    <div className="pbo-toasts" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.key} className={`pbo-toast pbo-toast-${toast.tone}`}>
          <span className="pbo-toast-message">{toast.message}</span>
          {toast.action ? (
            <button
              type="button"
              className="pbo-toast-action"
              onClick={() => {
                dismiss(toast.key);
                navigate(diskPageUrl(toast.action!.diskId));
              }}
            >
              {toast.action.label}
            </button>
          ) : null}
          <button type="button" className="pbo-toast-close" aria-label="×" onClick={() => dismiss(toast.key)}>
            ×
          </button>
        </div>
      ))}
    </div>
  );
}
