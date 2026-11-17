import { Inbox } from "lucide-react";

interface EmptyStateProps {
  title: string;
  description?: string;
}

/**
 * Empty state of the legacy pages (inside their dark cards): new layout and
 * icon, but the legacy palette (styles.css variables) so it matches the card
 * in both shell themes until each page is redesigned.
 */
export function EmptyState({ title, description }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed border-[color:var(--bd2)] bg-transparent px-6 py-10 text-center">
      <Inbox className="mb-1 h-4 w-4 text-[color:var(--t3)]" />
      <p className="m-0 text-[15px] font-medium text-[color:var(--t1)]">{title}</p>
      {description ? <p className="m-0 max-w-[460px] text-[13px] leading-relaxed text-[color:var(--t2)]">{description}</p> : null}
    </div>
  );
}
