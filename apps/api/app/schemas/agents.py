from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.base import UTCDateTimeModel


class PairingCodeRead(UTCDateTimeModel):
    code: str
    expires_at: datetime
    # Commands to run on the Proxmox host (as root), in order: download, run.
    install_commands: list[str]
    app_url: str
    # SHA-256 of the app certificate, to compare with what install.sh prints
    # (it downloads over `curl -k` the first time). None when unknown.
    app_fingerprint: str | None = None


class AgentRegisterRequest(BaseModel):
    code: str = Field(min_length=1, max_length=32)
    role: str = Field(min_length=1, max_length=16)
    hostname: str = Field(min_length=1, max_length=253)
    base_url: str = Field(min_length=1, max_length=512)
    fingerprint: str = Field(min_length=1, max_length=128)


class AgentRegisterResponse(BaseModel):
    agent_id: int
    role: str
    # app -> agent: the agent expects it in X-Agent-Token (AGENT_SERVER_TOKEN).
    agent_token: str
    # agent -> app: heartbeat, disk reports, run logs (AGENT_CALLBACK_TOKEN).
    callback_token: str
    api_base_url: str
    app_fingerprint: str | None = None


class RegisteredAgentRead(UTCDateTimeModel):
    id: int
    role: str
    hostname: str
    base_url: str
    fingerprint: str
    created_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None
    active: bool
