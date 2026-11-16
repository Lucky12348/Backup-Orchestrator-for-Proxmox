import { Loader2 } from "lucide-react";

interface LoadingBlockProps {
  label: string;
}

/** Works inside the shell and before it exists (the `.pbo-ui` wrapper scopes the utilities). */
export function LoadingBlock({ label }: LoadingBlockProps) {
  return (
    <div className="pbo-ui" style={{ background: "transparent" }}>
      <div className="flex min-h-[160px] items-center justify-center gap-2.5 px-6 py-12 text-sm text-muted" role="status">
        <Loader2 className="h-4 w-4 animate-spin text-accent-text" />
        <p className="m-0">{label}</p>
      </div>
    </div>
  );
}
