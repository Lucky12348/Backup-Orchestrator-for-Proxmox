import type { ReactNode } from "react";

import { ConfirmDialog } from "../ui/confirm";

interface ConfirmModalProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel: string;
  tone?: "danger" | "warning" | "info";
  onConfirm: () => void;
  onCancel: () => void;
  extra?: ReactNode;
}

/** Legacy API kept; rendered with the shadcn AlertDialog (src/ui/confirm.tsx). */
export function ConfirmModal({ open, title, description, confirmLabel, cancelLabel, tone = "warning", onConfirm, onCancel, extra }: ConfirmModalProps) {
  return (
    <ConfirmDialog
      open={open}
      options={{ title, description, confirmLabel, tone, extra }}
      cancelLabel={cancelLabel}
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
