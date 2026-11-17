import { createContext, useContext, type ReactNode } from "react";

import type { ExternalDisk } from "../types";

/**
 * App-level commands the context menus of any page can run (Phase 5h): the
 * same launch dialog and eject confirmation as the Disks page buttons.
 */
export interface Commands {
  backup: (disk: ExternalDisk) => void;
  eject: (disk: ExternalDisk) => void;
  refresh: () => Promise<void> | void;
}

const noop: Commands = { backup: () => undefined, eject: () => undefined, refresh: () => undefined };
const CommandsContext = createContext<Commands>(noop);

export function CommandsProvider({ value, children }: { value: Commands; children: ReactNode }) {
  return <CommandsContext.Provider value={value}>{children}</CommandsContext.Provider>;
}

export function useCommands(): Commands {
  return useContext(CommandsContext);
}
