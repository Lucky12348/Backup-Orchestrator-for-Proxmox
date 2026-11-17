"""Stale mounts stacked on a removable datastore path (B41, Phase 5f.3).

A disk pulled out leaves its ext4 mount behind (device gone, `shutdown`).
PBS's mount check reads the filesystem UUID of the mount source, so such a
layer is invisible to the REST API (`mount-status` says `notmounted`) and PBS
mounts the next disk on top of it: /dev/sdc1 then /dev/sdd1 on the same path.

What the PBS REST API does not offer (checked against the 4.x schema): no
endpoint lists the mounts of a path, and `POST /admin/datastore/{store}/unmount`
only acts on a datastore PBS counts as mounted, without a lazy option. So the
check needs the PBS agent (role `pbs`, routes `/datastore/mounts[/cleanup]`,
capability `datastore-mounts`): it reads /proc/self/mountinfo and removes dead
layers from the top with `umount -l`, never a live one.

Without a PBS agent the check is skipped (journal line when a datastore is
mounted) and the manual command is documented (docs/OPERATIONS.md).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

DATASTORE_ROOT = "/mnt/datastore"


def pbs_root_agent(settings: Any) -> Any | None:
    """The PBS agent (root, role pbs) when one is configured, even in API mode."""
    if not settings.pbs_agent_base_url:
        return None
    from app.services.host_agent import HostAgentClient

    return HostAgentClient(
        base_url=settings.pbs_agent_base_url,
        token=settings.pbs_agent_token,
        timeout_seconds=min(settings.pbs_agent_timeout_seconds, 60.0),
        label="PBS agent",
        fingerprint=settings.pbs_agent_fingerprint,
    )


def clean(store: str, *, settings: Any, client: Any | None = None, log: Callable[[str], None] | None = None) -> dict[str, Any] | None:
    """Removes the dead layers on /mnt/datastore/<store>. None = cannot check
    (no PBS agent, or one too old)."""
    agent = client or pbs_root_agent(settings)
    if agent is None:
        return None
    path = f"{DATASTORE_ROOT}/{store}"
    try:
        result = agent.post("/datastore/mounts/cleanup", {"path": path})
    except Exception as exc:  # noqa: BLE001 — old agent (no route) or unreachable
        logger.info("Stale mount check of %s unavailable: %s", path, exc)
        return None
    payload = result.payload if isinstance(getattr(result, "payload", None), dict) else {}
    for item in payload.get("removed") or []:
        reason = "device gone" if item.get("reason") == "device_gone" else "filesystem shut down"
        message = f"Stale mount removed from {path}: {item.get('source')} ({reason}), lazy unmount."
        logger.warning(message)
        if log is not None:
            log(message)
    return payload
