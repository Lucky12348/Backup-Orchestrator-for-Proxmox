import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, CheckCircle2, Info, XCircle } from "lucide-react";

import { cn } from "./utils";

const alertVariants = cva("flex gap-3 rounded-lg border px-4 py-3 text-sm", {
  variants: {
    variant: {
      info: "border-border bg-subtle text-fg [&>svg]:text-muted",
      success: "border-success/30 bg-success/10 text-fg [&>svg]:text-success",
      warning: "border-warning/30 bg-warning/10 text-fg [&>svg]:text-warning",
      danger: "border-danger/30 bg-danger/10 text-fg [&>svg]:text-danger",
    },
  },
  defaultVariants: { variant: "info" },
});

const ICONS = { info: Info, success: CheckCircle2, warning: AlertTriangle, danger: XCircle } as const;

interface AlertProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "title">, VariantProps<typeof alertVariants> {
  title?: React.ReactNode;
}

function Alert({ className, variant, title, children, ...props }: AlertProps) {
  const Icon = ICONS[variant ?? "info"];
  return (
    <div role={variant === "danger" ? "alert" : "status"} className={cn(alertVariants({ variant }), className)} {...props}>
      <Icon className="mt-0.5 h-4 w-4" />
      <div className="flex min-w-0 flex-col gap-1">
        {title ? <p className="font-medium">{title}</p> : null}
        {children ? <div className="text-muted [&_strong]:text-fg">{children}</div> : null}
      </div>
    </div>
  );
}

export { Alert };
