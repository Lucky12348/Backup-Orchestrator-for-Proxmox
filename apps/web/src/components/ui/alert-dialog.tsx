import * as React from "react";
import * as AlertDialogPrimitive from "@radix-ui/react-alert-dialog";

import { usePortalContainer } from "../../ui/portal";
import { cn } from "./utils";

const AlertDialog = AlertDialogPrimitive.Root;
const AlertDialogTitle = AlertDialogPrimitive.Title;
const AlertDialogDescription = AlertDialogPrimitive.Description;
const AlertDialogAction = AlertDialogPrimitive.Action;
const AlertDialogCancel = AlertDialogPrimitive.Cancel;

/** shadcn/ui AlertDialog (confirmations: focus trapped, Escape = cancel). */
const AlertDialogContent = React.forwardRef<
  React.ElementRef<typeof AlertDialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof AlertDialogPrimitive.Content>
>(({ className, ...props }, ref) => {
  const container = usePortalContainer();
  return (
    <AlertDialogPrimitive.Portal container={container}>
      <AlertDialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-[3px] animate-fade-in" />
      <AlertDialogPrimitive.Content
        ref={ref}
        className={cn(
          "fixed left-1/2 top-1/2 z-50 flex w-[calc(100%-40px)] max-w-[460px] -translate-x-1/2 -translate-y-1/2 flex-col gap-4 rounded-xl border border-border bg-surface p-6 text-fg shadow-2xl animate-fade-in",
          className,
        )}
        {...props}
      />
    </AlertDialogPrimitive.Portal>
  );
});
AlertDialogContent.displayName = AlertDialogPrimitive.Content.displayName;

export { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogTitle };
