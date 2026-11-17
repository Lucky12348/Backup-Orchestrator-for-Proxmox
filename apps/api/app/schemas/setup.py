from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr


class SetupStatusRead(BaseModel):
    needs_setup: bool
    proxmox_configured: bool
    pbs_configured: bool
    # False until the first-run wizard is finished: the UI redirects to /setup.
    setup_completed: bool = True
    current_step: str = "done"


class SetupProgressRead(BaseModel):
    current_step: str
    completed: bool
    data: dict[str, Any] = {}
    updated_at: datetime | None = None


class SetupProgressUpdate(BaseModel):
    step: Literal["agent", "pve", "pbs", "notifications", "done"] | None = None
    # Non-secret results only: keys that look like secrets are dropped server-side.
    data: dict[str, Any] | None = None


class SetupAgentStatusRead(BaseModel):
    registered: bool
    connected: bool
    agent_id: int | None = None
    hostname: str | None = None
    base_url: str | None = None
    fingerprint: str | None = None
    last_seen_at: datetime | None = None


class SetupGuestRead(BaseModel):
    vmid: int
    name: str
    node: str
    status: str


class PveProvisionRead(BaseModel):
    node: str
    pve_version: str
    api_url: str
    fingerprint: str
    token_id: str
    vm_count: int
    ct_count: int
    vms: list[SetupGuestRead] = []


class PbsCandidateRead(SetupGuestRead):
    addresses: list[str] = []
    pbs_port_open: bool = False
    name_match: bool = False
    score: int = 0
    suggested_url: str | None = None


class PbsCandidatesRead(BaseModel):
    node: str
    suggested_vmid: int | None = None
    vms: list[PbsCandidateRead] = []


class PbsDetectRead(BaseModel):
    vmid: int
    detected: bool
    ip: str | None = None
    source: str | None = None  # "guest-agent" | "neighbour"
    url: str | None = None
    addresses: list[dict[str, Any]] = []
    # B41: QEMU guest agent of that VM ({"enabled", "active", "commands"}; None: unknown).
    guest_agent: dict[str, Any] | None = None


class PbsFingerprintRequest(BaseModel):
    api_url: str = Field(min_length=1, max_length=512)


class PbsFingerprintRead(BaseModel):
    api_url: str
    fingerprint: str


class PbsProvisionRequest(BaseModel):
    api_url: str = Field(min_length=1, max_length=512)
    fingerprint: str = Field(min_length=1, max_length=128)
    # The operator ticked "the fingerprint matches" (compared with PBS › Dashboard).
    fingerprint_confirmed: bool
    username: str = Field(default="root@pam", min_length=3, max_length=128)
    # Used once for a PBS ticket, never stored nor logged (SecretStr hides it in reprs).
    password: SecretStr = Field(min_length=1, max_length=1024)
    pbs_vm_id: int | None = Field(default=None, ge=100, le=999_999_999)


class PbsProvisionRead(BaseModel):
    api_url: str
    fingerprint: str
    version: str
    token_id: str
    vmid: int | None = None
    datastores: list["PbsDatastoreRead"] = []


class PbsDatastoresRead(BaseModel):
    datastores: list["PbsDatastoreRead"] = []


class PbsDatastoreSelect(BaseModel):
    datastore: str = Field(min_length=1, max_length=255)


class ManualCommandsRead(BaseModel):
    pve: list[str]
    pbs: list[str]
    pve_token_id: list[str]
    pbs_token_id: list[str]


class SetupAdminCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    # Chosen on the first wizard screen (language selector, browser time zone).
    language: Literal["en", "fr"] | None = None
    timezone: str | None = Field(default=None, max_length=64)


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
    # Optional: the wizard picks the PBS VM at the PBS step.
    pbs_vm_id: int | None = Field(default=None, ge=100, le=999_999_999)
    pbs_vm_node: str | None = Field(default=None, max_length=255)


class PbsSetupUpdate(ConnectionProbeRequest):
    datastore: str = Field(min_length=1, max_length=255)
    # "api" (PBS >= 3.3, removable datastores, no PBS agent) or "agent" (legacy).
    mode: Literal["api", "agent"] | None = None
    pbs_vm_id: int | None = Field(default=None, ge=100, le=999_999_999)


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


PbsProvisionRead.model_rebuild()
PbsDatastoresRead.model_rebuild()
