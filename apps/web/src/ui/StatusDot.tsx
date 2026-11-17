import type { ReactNode } from "react";

import { cn } from "../components/ui/utils";

export type DotTone = "ok" | "run" | "warn" | "err" | "off";

export const DOT_COLOR: Record<DotTone, string> = {
  ok: "bg-success",
  run: "bg-accent",
  warn: "bg-warning",
  err: "bg-danger",
  off: "bg-faint",
};

/** A state as « ● text » (docs/DESIGN.md): no pill, no background. Badges are kept for exceptions. */
export function StatusDot({ tone, children, className, pulse = false, ...data }: { tone: DotTone; children?: ReactNode; className?: string; pulse?: boolean } & Record<`data-${string}`, string>) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap text-[13px] text-muted", className)} {...data}>
      <span aria-hidden className={cn("h-[7px] w-[7px] flex-none rounded-full", DOT_COLOR[tone], pulse && "animate-soft-pulse motion-reduce:animate-none")} />
      {children}
    </span>
  );
}
