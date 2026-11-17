import { useId, type ReactNode } from "react";

import { cn } from "../components/ui/utils";

/**
 * Page title (docs/DESIGN.md): 20 px, no "i" button next to it. The page's
 * help is a tooltip on the title itself (hover and keyboard focus).
 */
export function PageTitle({ children, tip, className, id }: { children: ReactNode; tip?: string; className?: string; id?: string }) {
  const tipId = useId();
  const heading = cn("m-0 text-[20px] font-semibold leading-tight tracking-[-0.015em] text-fg", className);
  if (!tip) return <h1 id={id} className={heading}>{children}</h1>;
  return (
    <span className="group relative inline-flex min-w-0">
      <h1 id={id} tabIndex={0} aria-describedby={tipId} className={cn(heading, "cursor-help rounded-sm outline-offset-4")} data-title-tip>
        {children}
      </h1>
      <span
        id={tipId}
        role="tooltip"
        className="invisible absolute left-0 top-full z-30 mt-2 w-[320px] max-w-[80vw] rounded-md border border-solid border-border bg-surface px-3 py-2 text-[13px] font-normal leading-[1.45] text-muted opacity-0 shadow-lg transition-opacity delay-200 group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
      >
        {tip}
      </span>
    </span>
  );
}
