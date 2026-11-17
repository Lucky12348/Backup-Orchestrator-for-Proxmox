// Toast queue, pure (node:test). The provider in toast.tsx drives it.

export type ToastTone = "info" | "success" | "warning" | "danger" | "neutral";

export interface ToastAction {
  /** Defaults to "See" / "Voir" when the toast has a target (href). */
  label?: string;
  /** Page to open (disk sheet, …): the toast gets a "See" button. */
  href?: string;
  run?: () => void;
}

export interface Toast {
  id: string;
  tone: ToastTone;
  title: string;
  description?: string;
  action?: ToastAction;
  /** The disk the toast is about: shown with its colour / photo / icon. */
  diskId?: number;
  /** Milliseconds before auto-dismiss; 0 = sticky (until closed). */
  duration: number;
  /** Bumped on every (re)publication of the same id: restarts its timer. */
  seq: number;
}

export type ToastInput = Omit<Toast, "id" | "duration" | "tone" | "seq"> & {
  id?: string;
  tone?: ToastTone;
  duration?: number;
};

/** At most three stacked (the oldest non-error ones go first). */
export const MAX_TOASTS = 3;
/** Info / success 8 s, warnings 12 s, errors stay until closed. */
export const DURATIONS: Record<ToastTone, number> = { info: 8000, success: 8000, neutral: 8000, warning: 12000, danger: 0 };

let counter = 0;

export function makeToast(input: ToastInput): Toast {
  counter += 1;
  const tone = input.tone ?? "info";
  return {
    id: input.id ?? `toast-${Date.now()}-${counter}`,
    tone,
    title: input.title,
    description: input.description,
    action: input.action,
    diskId: input.diskId,
    duration: input.duration ?? DURATIONS[tone],
    seq: counter,
  };
}

/** Same id replaces (no duplicates); beyond MAX_TOASTS the oldest goes,
 *  an error only when every toast shown is an error. */
export function addToast(list: Toast[], toast: Toast): Toast[] {
  const next = [...list.filter((item) => item.id !== toast.id), toast];
  while (next.length > MAX_TOASTS) {
    const index = next.findIndex((item) => item.tone !== "danger" && item !== toast);
    next.splice(index >= 0 ? index : 0, 1);
  }
  return next;
}

export function removeToast(list: Toast[], id: string): Toast[] {
  return list.filter((item) => item.id !== id);
}

/** Pause on hover: what is left of the timer when the pointer comes in. */
export function remainingAfter(remaining: number, startedAt: number, now: number): number {
  return Math.max(0, remaining - (now - startedAt));
}
