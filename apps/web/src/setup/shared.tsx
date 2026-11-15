import { createContext, useContext, useId, type InputHTMLAttributes, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";

import { Alert } from "../components/ui/alert";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "../components/ui/collapsible";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { cn } from "../components/ui/utils";
import { WizardError } from "./api";
import type { WizardText } from "./i18n";

export const TextContext = createContext<WizardText | null>(null);

export function useText(): WizardText {
  const text = useContext(TextContext);
  if (!text) throw new Error("useText outside the wizard");
  return text;
}

// Codes whose explanation is complete on its own: the server detail would only repeat it.
const SELF_EXPLANATORY = new Set([
  "network",
  "bad_credentials",
  "fingerprint_not_confirmed",
  "too_many_attempts",
  "pve_missing",
  "pbs_missing",
  "agent_missing",
]);

export function ErrorAlert({ error }: { error: unknown }) {
  const text = useText();
  if (!error) return null;
  const wizardError = error instanceof WizardError ? error : null;
  const code = wizardError?.code ?? "generic";
  const known = (text.errors as Record<string, string>)[code];
  const detail = wizardError?.message || (error instanceof Error && !wizardError ? error.message : "");
  return (
    <Alert variant={code === "pbs_too_old" ? "warning" : "danger"} title={known ?? text.errors.generic}>
      {detail && !SELF_EXPLANATORY.has(code) ? <p className="break-words text-[13px]">{detail}</p> : null}
    </Alert>
  );
}

/**
 * Commands to run on a host: a selectable block, deliberately WITHOUT a copy
 * button (writing shell commands to the clipboard is flagged as "ClickFix"
 * by uBlock Origin). One click selects the whole block.
 */
export function CommandBlock({ lines, className }: { lines: string[]; className?: string }) {
  return (
    <pre
      className={cn(
        "select-all overflow-x-auto whitespace-pre-wrap break-all rounded-lg border border-border bg-bg px-4 py-3 font-mono text-[13px] leading-relaxed text-fg",
        className,
      )}
      style={{ userSelect: "all" }}
    >
      {lines.join("\n")}
    </pre>
  );
}

export function Fingerprint({ value }: { value: string }) {
  return (
    <code className="block break-all rounded-lg border border-border bg-bg px-3 py-2 font-mono text-[12.5px] leading-relaxed text-fg">
      {value}
    </code>
  );
}

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: ReactNode;
}

export function Field({ label, hint, className, ...props }: FieldProps) {
  const id = useId();
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} {...props} />
      {hint ? <p className="text-[13px] text-faint">{hint}</p> : null}
    </div>
  );
}

export function SummaryList({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="divide-y divide-border rounded-lg border border-border">
      {rows.map(([label, value]) => (
        <div key={label} className="flex items-start justify-between gap-4 px-4 py-2.5 text-sm">
          <dt className="text-muted">{label}</dt>
          <dd className="min-w-0 break-words text-right font-medium text-fg">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ManualSection({ children, title }: { children: ReactNode; title: string }) {
  return (
    <Collapsible className="rounded-lg border border-dashed border-border">
      <CollapsibleTrigger className="group flex w-full items-center gap-2 px-4 py-3 text-sm text-muted hover:text-fg">
        <ChevronRight className="h-4 w-4 transition-transform group-data-[state=open]:rotate-90" />
        {title}
      </CollapsibleTrigger>
      <CollapsibleContent className="flex flex-col gap-4 px-4 pb-4">{children}</CollapsibleContent>
    </Collapsible>
  );
}
