// Ask the shell to show the live run modal, from anywhere (pages, hooks).
// RunMonitorProvider listens to these window events.

export const OPEN_RUN_EVENT = "pbo:open-run";
export const EXPECT_RUN_EVENT = "pbo:expect-run";

/** Open the modal on this run (backup started: its id is known). */
export function openRunModal(runId: number): void {
  window.dispatchEvent(new CustomEvent(OPEN_RUN_EVENT, { detail: { runId } }));
}

/** Open the modal on the next run that appears (eject: the id comes later, over SSE). */
export function expectRunModal(): void {
  window.dispatchEvent(new CustomEvent(EXPECT_RUN_EVENT));
}
