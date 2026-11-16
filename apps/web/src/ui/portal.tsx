import { createContext, useContext } from "react";

/**
 * Radix portals (dialogs, toasts) must render INSIDE the `.pbo-ui` root: the
 * Tailwind utilities and the theme tokens are scoped to it. The shell (and the
 * wizard) provide that element; outside a provider Radix falls back to body.
 */
export const PortalContainerContext = createContext<HTMLElement | null>(null);

export function usePortalContainer(): HTMLElement | undefined {
  return useContext(PortalContainerContext) ?? undefined;
}
