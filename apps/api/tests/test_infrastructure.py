"""Phase 5b item 6: the PBS VM and the PBO container are infrastructure,
ignored in the coverage by default, and re-includable for good."""

from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models import VirtualMachine
from app.models.virtual_machine import VMType
from app.services import infrastructure
from app.services.asset_ignores import upsert_asset_ignore
from app.services.overview import get_overview_metrics


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    monkeypatch.setattr(infrastructure, "configured_pbs_vmid", lambda db: 100)
    monkeypatch.setattr(infrastructure, "_hostnames", lambda: {"pbo"})


def _vm(db, name, vmid, vm_type, backed_up=False):
    vm = VirtualMachine(
        name=name, vm_type=vm_type, source="proxmox", external_id=str(vmid), node_name="pve", enabled=True,
        last_backup_at=datetime(2026, 11, 1) if backed_up else None,
    )
    db.add(vm)
    db.commit()
    return vm


def test_pbs_vm_and_pbo_container_are_ignored_by_default(db):
    _vm(db, "pbs", 100, VMType.VM)
    _vm(db, "pbo", 105, VMType.CT)
    _vm(db, "web", 101, VMType.VM, backed_up=True)
    _vm(db, "db", 102, VMType.CT)
    metrics = get_overview_metrics(db)
    assert metrics.infrastructure_ignored == 2
    assert metrics.ignored_vms == 2
    assert metrics.total_vms == 2 and metrics.protected_vms == 1
    assert metrics.coverage_percent == 50.0


def test_a_re_included_resource_is_never_ignored_again(db):
    pbs = _vm(db, "pbs", 100, VMType.VM, backed_up=True)
    get_overview_metrics(db)
    upsert_asset_ignore(db, source="proxmox", node="pve", vmid=pbs.external_id, ignored=False, reason=None)
    metrics = get_overview_metrics(db)
    assert metrics.infrastructure_ignored == 0 and metrics.total_vms == 1


def test_detection_rules():
    kind = infrastructure.infrastructure_kind
    assert kind(SimpleNamespace(vm_type=VMType.VM, external_id="100", name="x"), pbs_vmid=100, hostnames={"pbo"}) == "pbs"
    assert kind(SimpleNamespace(vm_type=VMType.CT, external_id="100", name="x"), pbs_vmid=100, hostnames={"pbo"}) is None
    assert kind(SimpleNamespace(vm_type=VMType.CT, external_id="7", name="PBO.lan"), pbs_vmid=100, hostnames={"pbo"}) == "pbo"
    assert kind(SimpleNamespace(vm_type=VMType.VM, external_id="7", name="pbo"), pbs_vmid=100, hostnames={"pbo"}) is None


def test_the_default_pbs_vmid_is_not_enough(db, monkeypatch):
    """Settings default to VM 100: without a stored PBS_EXECUTION_VM_ID, VM 100 stays a workload."""
    monkeypatch.undo()
    monkeypatch.setattr(infrastructure, "_hostnames", lambda: {"pbo"})
    assert infrastructure.configured_pbs_vmid(db) is None
    _vm(db, "vm-100", 100, VMType.VM)
    assert get_overview_metrics(db).infrastructure_ignored == 0

    from app.services.settings_service import update_settings

    update_settings(db, {"PBS_EXECUTION_VM_ID": 100})
    assert infrastructure.configured_pbs_vmid(db) == 100
