import type { ReactNode } from "react";

import { PageTitle } from "../ui/PageTitle";

interface PageHeaderProps {
  title: string;
  /** The page's help: a tooltip on the title (no "i" button, no subtitle). */
  description?: string;
  actions?: ReactNode;
}

export function PageHeader({ title, description, actions }: PageHeaderProps) {
  return (
    <header className="mb-6 flex flex-wrap items-center gap-3">
      <PageTitle tip={description}>{title}</PageTitle>
      <span className="flex-grow" />
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
