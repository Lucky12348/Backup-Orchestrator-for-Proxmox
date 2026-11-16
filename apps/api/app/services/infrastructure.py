"""The PBS VM and the PBO container are infrastructure, not workloads to
protect: they are ignored in the coverage by default (Phase 5b, item 6).

Detection: the PBS VM is the configured USB passthrough target
(`PBS_EXECUTION_VM_ID`, only once stored); the PBO container is the LXC whose name is this
machine's hostname (the installer names the CT and its hostname alike).

They are ignored ONCE: an `asset_ignores` row (reason "infrastructure") is
written only when none exists for that resource, so an operator who
re-includes it (row kept with ignored=false) is never overridden.
"""

from __future__ import annotations

import socket

from sqlalchemy.orm import Session

from app.models import VirtualMachine
from app.models.virtual_machine import VMType
from app.services.asset_ignores import asset_key_for_vm, get_asset_ignore_map, upsert_asset_ignore
from app.services.settings_service import load_overrides

INFRASTRUCTURE_REASON = "infrastructure"


def _hostnames() -> set[str]:
    names = {socket.gethostname()}
    try:
        names.add(socket.getfqdn())
    except OSError:  # pragma: no cover - resolver failure
        pass
    return {name.split(".")[0].casefold() for name in names if name}


def infrastructure_kind(vm: VirtualMachine, *, pbs_vmid: int | None, hostnames: set[str]) -> str | None:
    """"pbs" | "pbo" | None."""
    if vm.vm_type == VMType.VM and pbs_vmid and str(vm.external_id or "") == str(pbs_vmid):
        return "pbs"
    if vm.vm_type == VMType.CT and (vm.name or "").split(".")[0].casefold() in hostnames:
        return "pbo"
    return None


def configured_pbs_vmid(db: Session) -> int | None:
    """The PBS VM id only when the operator set it (wizard / settings): the
    `Settings` default (100) must never hide an unrelated VM 100."""
    value = load_overrides(db).get("pbs_execution_vm_id")
    return int(value) if value else None


def ensure_infrastructure_ignored(db: Session, vms: list[VirtualMachine]) -> None:
    pbs_vmid = configured_pbs_vmid(db)
    hostnames = _hostnames()
    existing = get_asset_ignore_map(db, vms)
    for vm in vms:
        if vm.source != "proxmox" or infrastructure_kind(vm, pbs_vmid=pbs_vmid, hostnames=hostnames) is None:
            continue
        key = asset_key_for_vm(vm)
        if key in existing:
            continue  # already decided (possibly re-included by the operator)
        upsert_asset_ignore(db, source=key[0], node=key[1], vmid=key[2], ignored=True, reason=INFRASTRUCTURE_REASON)
