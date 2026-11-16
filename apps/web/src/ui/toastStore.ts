// Toast queue, pure (node:test). The provider in toast.tsx drives it.

export type ToastTone = "info" | "success" | "warning" | "danger" | "neutral";

export interface ToastAction {
  label: string;
  /** Opaque value handed back to the provider's onAction (e.g. a URL to open). */
  href?: string;
  run?: () => void;
}

export interface Toast {
  id: string;
  tone: ToastTone;
  title: string;
  description?: string;
  action?: ToastAction;
  /** Milliseconds before auto-dismiss; 0 = sticky. */
  duration: number;
}

export type ToastInput = Omit<Toast, "id" | "duration" | "tone"> & {
  id?: string;
  tone?: ToastTone;
  duration?: number;
};

export const MAX_TOASTS = 4;
export const DEFAULT_DURATION = 6000;

let counter = 0;

export function makeToast(input: ToastInput): Toast {
  counter += 1;
  return {
    id: input.id ?? `toast-${Date.now()}-${counter}`,
    tone: input.tone ?? "info",
    title: input.title,
    description: input.description,
    action: input.action,
    duration: input.duration ?? (input.tone === "danger" ? 10000 : DEFAULT_DURATION),
  };
}

/** Same id replaces (no duplicates), oldest dropped beyond MAX_TOASTS. */
export function addToast(list: Toast[], toast: Toast): Toast[] {
  return [...list.filter((item) => item.id !== toast.id), toast].slice(-MAX_TOASTS);
}

export function removeToast(list: Toast[], id: string): Toast[] {
  return list.filter((item) => item.id !== id);
}
