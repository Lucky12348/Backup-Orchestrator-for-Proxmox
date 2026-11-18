"""Cancelling a run (B56).

`POST /external-backups/runs/{id}/cancel` only records the request; the run's
own thread stops itself at the next safe point:

* while it follows a PBS task that may be stopped (sync, prune, GC, verify),
  `PBSClient.wait_for_task(cancellable=True)` stops that task, waits for PBS
  to finish it and raises `RunCancelled`;
* between steps, `checkpoint()` raises `RunCancelled`.

Preparing a datastore (wipe, mkfs, datastore creation) and the final release
or eject are never interrupted: the request then waits for the end of that
step. The run thread catches `RunCancelled`, releases the disk (unmount on
PBS, USB removed from the VM) and ends the run as cancelled.

State is in memory: one uvicorn process runs every run thread (see
`_external_backup_run_lock`), and a restart ends those threads anyway.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

CANCELLED_STEP = "cancelled"


class RunCancelled(Exception):
    """The operator cancelled the run that is executing in this thread."""


_lock = threading.Lock()
_requested: set[int] = set()
_local = threading.local()


def request(run_id: int) -> bool:
    """Ask the run to stop. False when it was already asked."""
    with _lock:
        if run_id in _requested:
            return False
        _requested.add(run_id)
        return True


def is_requested(run_id: int | None) -> bool:
    if run_id is None:
        return False
    with _lock:
        return run_id in _requested


def clear(run_id: int) -> None:
    with _lock:
        _requested.discard(run_id)


def current_run_id() -> int | None:
    return getattr(_local, "run_id", None)


@contextmanager
def bound(run_id: int) -> Iterator[None]:
    """The calling thread executes `run_id` (its PBS waits become cancellable)."""
    previous = current_run_id()
    _local.run_id = run_id
    try:
        yield
    finally:
        _local.run_id = previous


def current_cancelled() -> bool:
    return is_requested(current_run_id())


def checkpoint() -> None:
    if current_cancelled():
        raise RunCancelled()
