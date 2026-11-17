import { X } from "lucide-react";

/** The one "close" button of dialogs and panels: an X, no border, no fill, a ring on keyboard focus only. */
export function CloseButton({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="flex h-8 w-8 flex-none items-center justify-center rounded-md border-0 bg-transparent p-0 text-muted outline-none hover:bg-hover hover:text-fg focus-visible:ring-2 focus-visible:ring-focus"
      data-close
    >
      <X className="h-4 w-4" />
    </button>
  );
}
