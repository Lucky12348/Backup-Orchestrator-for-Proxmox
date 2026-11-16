import type { ReactNode } from "react";

import { InfoTip } from "../ui/InfoTip";

interface PageHeaderProps {
  title: string;
  /** Shown in the "i" tooltip next to the title (no subtitle any more). */
  description?: string;
  actions?: ReactNode;
}

function aboutLabel(): string {
  return typeof document !== "undefined" && document.documentElement.lang === "fr" ? "À propos de cette page" : "About this page";
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <header className="mb-6 flex flex-wrap items-center gap-3">
      <h1 className="m-0 text-[26px] font-semibold tracking-[-0.02em] text-fg">{title}</h1>
      {description ? <InfoTip text={description} label={aboutLabel()} /> : null}
      <span className="flex-grow" />
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
