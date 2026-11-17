import { cn } from "../components/ui/utils";

/** The five states of anything that runs: a run, a step, a VM copy, a toast. */
export type StatusKind = "running" | "success" | "failed" | "skipped" | "pending";

const R = 6.25; // radius in a 16 × 16 box
const C = 2 * Math.PI * R;

/**
 * PBO's own status glyphs (docs/DESIGN.md), one component everywhere:
 * a ring that fills (running), a full disc + thin check (success), a disc +
 * cross (failure), a dotted ring (skipped), an empty ring (pending).
 * `progress` (0–100) fills the ring; without it the ring turns (unknown).
 */
export function StatusIcon({
  kind,
  progress,
  size = 16,
  label,
  className,
}: {
  kind: StatusKind;
  progress?: number | null;
  size?: number;
  /** Accessible name; without it the glyph is decorative (the text next to it says the state). */
  label?: string;
  className?: string;
}) {
  const a11y = label ? { role: "img", "aria-label": label } : { "aria-hidden": true };
  const known = typeof progress === "number" && Number.isFinite(progress);
  const fill = known ? Math.max(0.04, Math.min(1, (progress as number) / 100)) : 0.28;
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      // Unknown progress: the whole glyph turns (rotating the arc alone would
      // combine with its own rotate(-90) and leave the box).
      className={cn("flex-none", TONE[kind], kind === "running" && !known && "animate-spin motion-reduce:animate-none", className)}
      data-status-icon={kind}
      {...a11y}
    >
      {kind === "success" || kind === "failed" ? (
        <>
          <circle cx="8" cy="8" r="7" fill="currentColor" />
          {kind === "success" ? (
            <path d="M5.1 8.2l1.95 1.95L10.9 6.1" stroke="rgb(var(--pbo-surface))" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          ) : (
            <path d="M5.75 5.75l4.5 4.5M10.25 5.75l-4.5 4.5" stroke="rgb(var(--pbo-surface))" strokeWidth="1.5" strokeLinecap="round" />
          )}
        </>
      ) : kind === "running" ? (
        <>
          <circle cx="8" cy="8" r={R} stroke="currentColor" strokeOpacity="0.25" strokeWidth="1.75" />
          <circle
            cx="8"
            cy="8"
            r={R}
            stroke="currentColor"
            strokeWidth="1.75"
            strokeLinecap="round"
            strokeDasharray={`${(C * fill).toFixed(2)} ${C.toFixed(2)}`}
            transform="rotate(-90 8 8)"
            className="transition-[stroke-dasharray] duration-500 motion-reduce:transition-none"
          />
        </>
      ) : kind === "skipped" ? (
        <circle cx="8" cy="8" r={R} stroke="currentColor" strokeWidth="1.5" strokeDasharray="1.6 2.3" strokeLinecap="round" />
      ) : (
        <circle cx="8" cy="8" r={R} stroke="currentColor" strokeWidth="1.5" />
      )}
    </svg>
  );
}

const TONE: Record<StatusKind, string> = {
  running: "text-accent",
  success: "text-success",
  failed: "text-danger",
  skipped: "text-faint",
  pending: "text-faint",
};

/** Run / step / row status → glyph. Unknown values read as pending. */
export function statusKind(status: string | null | undefined): StatusKind {
  switch (status) {
    case "running":
    case "active":
    case "pending_run":
      return "running";
    case "success":
    case "done":
    case "ok":
      return "success";
    case "failed":
    case "error":
    case "err":
      return "failed";
    case "skipped":
    case "not_run":
      return "skipped";
    default:
      return "pending";
  }
}
