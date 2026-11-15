from typing import Any, Literal

from pydantic import BaseModel, Field


class SetupStatusRead(BaseModel):
    needs_setup: bool
    proxmox_configured: bool
    pbs_configured: bool


class SetupAdminCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ConnectionProbeRequest(BaseModel):
    api_url: str = Field(min_length=1, max_length=512)
    token_id: str = Field(min_length=1, max_length=256)
    # Omitted = reuse the stored secret (lets the UI re-test without re-typing it).
    token_secret: str | None = Field(default=None, max_length=512)
    verify_ssl: bool = False
    fingerprint: str | None = Field(default=None, max_length=128)


class ProxmoxVmRead(BaseModel):
    vmid: int
    name: str
    node: str
    status: str


class PbsDatastoreRead(BaseModel):
    name: str
    comment: str = ""
    maintenance: Any = None
    mount_status: str | None = None


class ProxmoxProbeRead(BaseModel):
    api_url: str
    version: str
    fingerprint: str
    fingerprint_pinned: bool
    # True when neither CA verification nor a pinned fingerprint was given: only
    # the certificate fingerprint is returned (token not sent). Pin it and retry.
    trust_required: bool = False
    nodes: list[str] = []
    vms: list[ProxmoxVmRead] = []


class PbsProbeRead(BaseModel):
    api_url: str
    version: str
    fingerprint: str
    fingerprint_pinned: bool
    # True when neither CA verification nor a pinned fingerprint was given: only
    # the certificate fingerprint is returned (token not sent). Pin it and retry.
    trust_required: bool = False
    nodes: list[str] = []
    datastores: list[PbsDatastoreRead] = []


class ProxmoxSetupUpdate(ConnectionProbeRequest):
    node_name: str = Field(min_length=1, max_length=255)
    pbs_vm_id: int = Field(ge=100, le=999_999_999)
    pbs_vm_node: str | None = Field(default=None, max_length=255)


class PbsSetupUpdate(ConnectionProbeRequest):
    datastore: str = Field(min_length=1, max_length=255)
    # "api" (PBS >= 3.3, removable datastores, no PBS agent) or "agent" (legacy).
    mode: Literal["api", "agent"] | None = None


class SettingItemRead(BaseModel):
    key: str
    group: str
    type: str
    secret: bool
    value: Any = None
    stored: bool


class SettingsUpdate(BaseModel):
    # Setting key -> new value; null resets the key to its built-in default.
    values: dict[str, Any]
