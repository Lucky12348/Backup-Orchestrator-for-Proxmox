import { cn } from "../components/ui/utils";

/**
 * PBO's mark (docs/DESIGN.md, docs/design/logo.svg): two arcs = disks in
 * rotation, around a spindle. Monochrome (currentColor), no tile behind it.
 */
export function LogoMark({ size = 20, className }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} fill="none" aria-hidden className={cn("flex-none", className)} data-logo>
      <path d="M4.2 13.6A8 8 0 0 1 17.6 6.3" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      <path d="M19.8 10.4A8 8 0 0 1 6.4 17.7" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      <circle cx="12" cy="12" r="2.1" fill="currentColor" />
    </svg>
  );
}

/** Mark + the word "PBO". */
export function Logo({ className, size = 20 }: { className?: string; size?: number }) {
  return (
    <span className={cn("inline-flex items-center gap-2 text-fg", className)}>
      <LogoMark size={size} />
      <span className="text-sm font-semibold tracking-[-0.01em]">PBO</span>
    </span>
  );
}
