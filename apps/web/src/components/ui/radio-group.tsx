import * as React from "react";
import * as RadioGroupPrimitive from "@radix-ui/react-radio-group";

import { cn } from "./utils";

const RadioGroup = React.forwardRef<
  React.ElementRef<typeof RadioGroupPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof RadioGroupPrimitive.Root>
>(({ className, ...props }, ref) => (
  <RadioGroupPrimitive.Root className={cn("grid gap-2", className)} {...props} ref={ref} />
));
RadioGroup.displayName = RadioGroupPrimitive.Root.displayName;

/** A whole selectable row (shadcn "radio card"): the item is the clickable surface. */
const RadioCard = React.forwardRef<
  React.ElementRef<typeof RadioGroupPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof RadioGroupPrimitive.Item>
>(({ className, children, ...props }, ref) => (
  <RadioGroupPrimitive.Item
    ref={ref}
    className={cn(
      "group flex w-full items-center gap-3 rounded-lg border border-border bg-bg px-4 py-3 text-left transition-colors hover:border-faint data-[state=checked]:border-accent data-[state=checked]:bg-accent/5",
      className,
    )}
    {...props}
  >
    <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-faint group-data-[state=checked]:border-accent">
      <RadioGroupPrimitive.Indicator className="h-2 w-2 rounded-full bg-accent" />
    </span>
    {children}
  </RadioGroupPrimitive.Item>
));
RadioCard.displayName = "RadioCard";

export { RadioCard, RadioGroup };
