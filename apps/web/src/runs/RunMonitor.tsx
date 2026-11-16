import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";

import { getExternalBackupRun, getExternalBackupRuns, getRunJournal, type RunJournal } from "../api";
import { useEvents } from "../useEvents";
import { EXPECT_RUN_EVENT, OPEN_RUN_EVENT } from "./openRun";
import { initialMonitorState, isRunning, monitorReducer, type MonitorState, type RunLike } from "./runModel";

interface RunMonitorValue {
  state: MonitorState;
  /** Merged view (last fetch + live SSE fields) of the active run. */
  activeRun: RunLike | null;
  journal: RunJournal | null;
  journalError: boolean;
  diskName: (diskId: number) => string | undefined;
  groupName: (group: string) => string | undefined;
  openRun: (runId: number) => void;
  /** The next run that appears opens the modal (eject, preparation: no run id up front). */
  expectRun: () => void;
  minimize: () => void;
  close: () => void;
  refresh: () => void;
}

const RunMonitorContext = createContext<RunMonitorValue | null>(null);

const JOURNAL_THROTTLE_MS = 1000;

/**
 * Follows long actions (backup, eject…) live through the SSE stream:
 * run.progress patches the run, run.log / run.finished refresh the journal.
 * The modal and the top-bar pill read from here.
 */
export function RunMonitorProvider({
  children,
  diskNames,
  groupNames,
}: {
  children: ReactNode;
  diskNames: Record<number, string>;
  /** "vm/111" → "agents" (Proxmox inventory). */
  groupNames: Record<string, string>;
}) {
  const [state, dispatch] = useReducer(monitorReducer, initialMonitorState);
  const [details, setDetails] = useState<Record<number, RunLike>>({});
  const [journal, setJournal] = useState<RunJournal | null>(null);
  const [journalError, setJournalError] = useState(false);
  const journalTimer = useRef<number | undefined>(undefined);
  const activeId = state.activeRunId;

  // A run already running when the app opens (reload during a backup).
  useEffect(() => {
    void getExternalBackupRuns()
      .then((runs) => {
        const running = runs.find((run) => isRunning(run));
        if (running) dispatch({ type: "loaded", run: running as unknown as RunLike });
      })
      .catch(() => undefined);
  }, []);

  const loadDetails = useCallback((runId: number) => {
    void getExternalBackupRun(runId)
      .then((run) => setDetails((current) => ({ ...current, [runId]: run as unknown as RunLike })))
      .catch(() => undefined);
  }, []);

  const loadJournal = useCallback((runId: number) => {
    void getRunJournal(runId)
      .then((next) => {
        setJournal(next);
        setJournalError(false);
      })
      .catch(() => setJournalError(true));
  }, []);

  const scheduleJournal = useCallback(
    (runId: number) => {
      if (journalTimer.current !== undefined) return;
      journalTimer.current = window.setTimeout(() => {
        journalTimer.current = undefined;
        loadJournal(runId);
      }, JOURNAL_THROTTLE_MS);
    },
    [loadJournal],
  );

  useEffect(() => () => window.clearTimeout(journalTimer.current), []);

  useEffect(() => {
    const onOpen = (event: Event) => {
      const runId = Number((event as CustomEvent<{ runId: number }>).detail?.runId);
      if (Number.isInteger(runId)) dispatch({ type: "open", runId });
    };
    const onExpect = () => dispatch({ type: "expect" });
    window.addEventListener(OPEN_RUN_EVENT, onOpen);
    window.addEventListener(EXPECT_RUN_EVENT, onExpect);
    return () => {
      window.removeEventListener(OPEN_RUN_EVENT, onOpen);
      window.removeEventListener(EXPECT_RUN_EVENT, onExpect);
    };
  }, []);

  useEffect(() => {
    if (activeId === null) return;
    loadDetails(activeId);
    setJournal((current) => (current?.run_id === activeId ? current : null));
    loadJournal(activeId);
  }, [activeId, loadDetails, loadJournal]);

  useEvents(["run.progress", "run.log", "run.finished"], (event) => {
    const data = event.data as Record<string, unknown>;
    if (event.type !== "run.log") dispatch({ type: "event", eventType: event.type, data });
    const runId = Number(data.run_id);
    if (runId !== activeId) return;
    if (event.type === "run.finished") {
      loadDetails(runId);
      loadJournal(runId);
    } else if (event.type === "run.log" || data.current_step !== undefined) {
      scheduleJournal(runId);
    }
  });

  const activeRun = useMemo<RunLike | null>(() => {
    if (activeId === null) return null;
    const merged = { ...(details[activeId] ?? {}), ...(state.runs[activeId] ?? {}) } as RunLike;
    if (details[activeId] && details[activeId].status !== "running" && details[activeId].status !== "pending") {
      merged.status = details[activeId].status; // the API is authoritative once finished
    }
    return merged.started_at ? merged : null;
  }, [activeId, details, state.runs]);

  const value: RunMonitorValue = {
    state,
    activeRun,
    journal: journal && journal.run_id === activeId ? journal : null,
    journalError,
    diskName: (diskId) => diskNames[diskId],
    groupName: (group) => groupNames[group],
    openRun: (runId) => dispatch({ type: "open", runId }),
    expectRun: () => dispatch({ type: "expect" }),
    minimize: () => dispatch({ type: "minimize" }),
    close: () => dispatch({ type: "close" }),
    refresh: () => {
      if (activeId !== null) {
        loadDetails(activeId);
        loadJournal(activeId);
      }
    },
  };

  return <RunMonitorContext.Provider value={value}>{children}</RunMonitorContext.Provider>;
}

export function useRunMonitor(): RunMonitorValue {
  const value = useContext(RunMonitorContext);
  if (!value) throw new Error("useRunMonitor outside RunMonitorProvider");
  return value;
}

/** Optional variant for components that may render outside the shell (tests, wizard). */
export function useOptionalRunMonitor(): RunMonitorValue | null {
  return useContext(RunMonitorContext);
}
