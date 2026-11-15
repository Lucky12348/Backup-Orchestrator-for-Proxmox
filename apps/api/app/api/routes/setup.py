"""First-run setup: admin account creation and Proxmox/PBS connection wizard.

`public_router` is reachable without a session (status + first admin only);
`router` is mounted behind the session dependency.
"""

from fastapi import APIRouter, HTTPException, Response, status

from app.api.dependencies import DbSession
from app.auth import SessionInfo, start_session
from app.schemas.setup import (
    ConnectionProbeRequest,
    PbsProbeRead,
    PbsSetupUpdate,
    ProxmoxProbeRead,
    ProxmoxSetupUpdate,
    SetupAdminCreate,
    SetupStatusRead,
)
from app.services.settings_service import effective_settings, stored_secret, update_settings
from app.services.setup_probe import ProbeError, normalize_api_url, normalize_fingerprint, probe_pbs, probe_proxmox
from app.services.users import (
    InvalidCredentialsFormatError,
    SetupAlreadyDoneError,
    create_first_admin,
    has_users,
)

public_router = APIRouter(prefix="/setup", tags=["setup"])
router = APIRouter(prefix="/setup", tags=["setup"])


@public_router.get("/status", response_model=SetupStatusRead)
def get_setup_status(db: DbSession) -> SetupStatusRead:
    settings = effective_settings(db)
    return SetupStatusRead(
        needs_setup=not has_users(db),
        proxmox_configured=bool(settings.pve_api_url and settings.pve_api_token_id and settings.pve_api_token_secret),
        pbs_configured=bool(
            settings.pbs_api_url and settings.pbs_token_id and settings.pbs_token_secret and settings.pbs_datastore
        ),
    )


@public_router.post("/admin", response_model=SessionInfo, status_code=status.HTTP_201_CREATED)
def create_admin(payload: SetupAdminCreate, response: Response, db: DbSession) -> SessionInfo:
    try:
        user = create_first_admin(db, payload.username, payload.password)
    except SetupAlreadyDoneError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidCredentialsFormatError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return start_session(response, user.username)


def _secret(db: DbSession, payload: ConnectionProbeRequest, stored_key: str) -> str:
    if payload.token_secret:
        return payload.token_secret
    secret = stored_secret(db, stored_key)
    if not secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API token secret is required.")
    return secret


def _bad_request(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/proxmox/test", response_model=ProxmoxProbeRead)
def test_proxmox(payload: ConnectionProbeRequest, db: DbSession) -> ProxmoxProbeRead:
    try:
        result = probe_proxmox(
            payload.api_url,
            payload.token_id,
            _secret(db, payload, "PVE_API_TOKEN_SECRET"),
            verify_ssl=payload.verify_ssl,
            fingerprint=payload.fingerprint,
        )
    except ProbeError as exc:
        raise _bad_request(exc) from exc
    return ProxmoxProbeRead.model_validate(result, from_attributes=True)


@router.post("/pbs/test", response_model=PbsProbeRead)
def test_pbs(payload: ConnectionProbeRequest, db: DbSession) -> PbsProbeRead:
    try:
        result = probe_pbs(
            payload.api_url,
            payload.token_id,
            _secret(db, payload, "PBS_TOKEN_SECRET"),
            verify_ssl=payload.verify_ssl,
            fingerprint=payload.fingerprint,
        )
    except ProbeError as exc:
        raise _bad_request(exc) from exc
    return PbsProbeRead.model_validate(result, from_attributes=True)


@router.put("/proxmox", response_model=SetupStatusRead)
def save_proxmox(payload: ProxmoxSetupUpdate, db: DbSession) -> SetupStatusRead:
    try:
        values = {
            "PVE_API_URL": normalize_api_url(payload.api_url, 8006),
            "PVE_API_TOKEN_ID": payload.token_id.strip(),
            "PVE_VERIFY_SSL": payload.verify_ssl,
            "PVE_FINGERPRINT": normalize_fingerprint(payload.fingerprint),
            "PVE_NODE_NAME": payload.node_name.strip(),
            "PBS_EXECUTION_VM_ID": payload.pbs_vm_id,
            "PBS_EXECUTION_VM_NODE": (payload.pbs_vm_node or payload.node_name).strip(),
        }
    except ProbeError as exc:
        raise _bad_request(exc) from exc
    if payload.token_secret:
        values["PVE_API_TOKEN_SECRET"] = payload.token_secret
    elif not stored_secret(db, "PVE_API_TOKEN_SECRET"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API token secret is required.")
    update_settings(db, values)
    return get_setup_status(db)


@router.put("/pbs", response_model=SetupStatusRead)
def save_pbs(payload: PbsSetupUpdate, db: DbSession) -> SetupStatusRead:
    try:
        values = {
            "PBS_API_URL": normalize_api_url(payload.api_url, 8007),
            "PBS_TOKEN_ID": payload.token_id.strip(),
            "PBS_VERIFY_SSL": payload.verify_ssl,
            "PBS_FINGERPRINT": normalize_fingerprint(payload.fingerprint),
            "PBS_DATASTORE": payload.datastore.strip(),
        }
        if payload.mode:
            values["PBS_MODE"] = payload.mode
    except ProbeError as exc:
        raise _bad_request(exc) from exc
    if payload.token_secret:
        values["PBS_TOKEN_SECRET"] = payload.token_secret
    elif not stored_secret(db, "PBS_TOKEN_SECRET"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API token secret is required.")
    update_settings(db, values)
    return get_setup_status(db)
