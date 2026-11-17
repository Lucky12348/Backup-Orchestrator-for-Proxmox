import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";

import { usePortalContainer } from "../../ui/portal";
import { cn } from "./utils";

const Dialog = DialogPrimitive.Root;
const DialogTrigger = DialogPrimitive.Trigger;
const DialogClose = DialogPrimitive.Close;
const DialogTitle = DialogPrimitive.Title;
const DialogDescription = DialogPrimitive.Description;

/**
 * shadcn/ui Dialog. The portal targets the `.pbo-ui` root (usePortalContainer)
 * so the scoped Tailwind utilities and theme tokens apply to it.
 */
const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & { overlayClassName?: string }
>(({ className, overlayClassName, children, ...props }, ref) => {
  const container = usePortalContainer();
  return (
    <DialogPrimitive.Portal container={container}>
      <DialogPrimitive.Overlay
        className={cn("fixed inset-0 z-50 bg-black/60 backdrop-blur-[3px] animate-fade-in", overlayClassName)}
      />
      <DialogPrimitive.Content
        ref={ref}
        className={cn(
          // The dialog itself takes the focus on open (never a stray ring around it).
          "fixed left-1/2 top-12 z-50 flex max-h-[calc(100vh-96px)] w-[calc(100%-40px)] -translate-x-1/2 flex-col overflow-hidden rounded-lg border border-border bg-surface text-fg shadow-2xl animate-fade-in focus-visible:outline-none",
          className,
        )}
        {...props}
      >
        {children}
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
});
DialogContent.displayName = DialogPrimitive.Content.displayName;

export { Dialog, DialogClose, DialogContent, DialogDescription, DialogTitle, DialogTrigger };
