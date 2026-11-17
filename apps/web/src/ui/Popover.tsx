import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import { cn } from "../components/ui/utils";

/**
 * Small anchored popover (user menu, calendar export): no extra dependency.
 * Closes on Escape and on a click outside; focus goes back to the trigger.
 * Rendered inside the trigger's wrapper, so it stays within `.pbo-ui`.
 */
export function Popover({
  trigger,
  children,
  label,
  role = "dialog",
  placement = "bottom-end",
  className,
  triggerClassName,
  open: controlledOpen,
  onOpenChange,
}: {
  trigger: ReactNode;
  children: ReactNode | ((close: () => void) => ReactNode);
  label: string;
  role?: "dialog" | "menu";
  placement?: "bottom-end" | "bottom-start" | "top-start";
  className?: string;
  triggerClassName?: string;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  const [innerOpen, setInnerOpen] = useState(false);
  const open = controlledOpen ?? innerOpen;
  const setOpen = (value: boolean) => {
    setInnerOpen(value);
    onOpenChange?.(value);
  };
  const id = useId();
  const root = useRef<HTMLDivElement | null>(null);
  const button = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (root.current && !root.current.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const close = () => setOpen(false);
  return (
    <div ref={root} className="relative">
      <button
        ref={button}
        type="button"
        aria-haspopup={role}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen(!open)}
        className={triggerClassName}
      >
        {trigger}
      </button>
      {open ? (
        <div
          id={id}
          role={role}
          aria-label={label}
          className={cn(
            "absolute z-40 rounded-lg border border-solid border-border-strong bg-surface text-fg shadow-[0_16px_48px_rgba(0,0,0,0.45)] animate-fade-in",
            placement === "bottom-end" && "right-0 top-[calc(100%+6px)]",
            placement === "bottom-start" && "left-0 top-[calc(100%+6px)]",
            placement === "top-start" && "bottom-[calc(100%+6px)] left-0",
            className,
          )}
        >
          {typeof children === "function" ? children(close) : children}
        </div>
      ) : null}
    </div>
  );
}
