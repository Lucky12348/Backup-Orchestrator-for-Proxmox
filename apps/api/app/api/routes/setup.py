"""First-run setup: admin account creation and the Proxmox/PBS connection wizard.

`public_router` is reachable without a session (status + first admin only);
`router` is mounted behind the session dependency.
"""

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.dependencies import DbSession
from app.auth import SessionInfo, start_session
from app.schemas.setup import (
    ConnectionProbeRequest,
    ManualCommandsRead,
    PbsCandidatesRead,
    PbsDatastoreSelect,
    PbsFingerprintRead,
    PbsFingerprintRequest,
    PbsProbeRead,
    PbsProvisionRead,
    PbsProvisionRequest,
    PbsSetupUpdate,
    ProxmoxProbeRead,
    ProxmoxSetupUpdate,
    PveProvisionRead,
    SetupAdminCreate,
    SetupAgentStatusRead,
    SetupProgressRead,
    SetupProgressUpdate,
    SetupStatusRead,
)
from app.services import setup_wizard
from app.services.pbs_provisioning import PbsProvisionError, provision_pbs_access
from app.services.settings_service import (
    effective_settings,
    stored_secret,
    update_settings,
)
from app.services.setup_probe import (
    ProbeError,
    normalize_api_url,
    normalize_fingerprint,
    probe_pbs,
    probe_proxmox,
)
from app.services.users import (
    InvalidCredentialsFormatError,
    SetupAlreadyDoneError,
    create_first_admin,
    has_users,
)

logger = logging.getLogger(__name__)

public_router = APIRouter(prefix="/setup", tags=["setup"])
router = APIRouter(prefix="/setup", tags=["setup"])


def _setup_error(exc: setup_wizard.SetupError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": str(exc)})


@public_router.get("/status", response_model=SetupStatusRead)
def get_setup_status(db: DbSession) -> SetupStatusRead:
    settings = effective_settings(db)
    return SetupStatusRead(
        needs_setup=not has_users(db),
        proxmox_configured=bool(settings.pve_api_url and settings.pve_api_token_id and settings.pve_api_token_secret),
        pbs_configured=bool(
            settings.pbs_api_url and settings.pbs_token_id and settings.pbs_token_secret and settings.pbs_datastore
        ),
        setup_completed=setup_wizard.setup_completed(db),
        current_step=setup_wizard.current_step(db),
    )


@public_router.post("/admin", response_model=SessionInfo, status_code=status.HTTP_201_CREATED)
def create_admin(payload: SetupAdminCreate, response: Response, db: DbSession) -> SessionInfo:
    try:
        user = create_first_admin(db, payload.username, payload.password)
    except SetupAlreadyDoneError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except InvalidCredentialsFormatError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    # A fresh install starts the wizard at the agent step.
    if not setup_wizard.setup_completed(db):
        setup_wizard.save_progress(db, step="agent")
    return start_session(response, user.username)


# --- Wizard state ---------------------------------------------------------------


@router.get("/progress", response_model=SetupProgressRead)
def get_progress(db: DbSession) -> SetupProgressRead:
    return SetupProgressRead(**setup_wizard.get_progress(db))


@router.put("/progress", response_model=SetupProgressRead)
def put_progress(payload: SetupProgressUpdate, db: DbSession) -> SetupProgressRead:
    if payload.step == "done":
        raise _setup_error(setup_wizard.SetupError("invalid_step", "Use POST /setup/complete to finish the setup."))
    try:
        return SetupProgressRead(**setup_wizard.save_progress(db, step=payload.step, data=payload.data))
    except setup_wizard.SetupError as exc:
        raise _setup_error(exc) from None


@router.post("/complete", response_model=SetupProgressRead)
def complete(db: DbSession) -> SetupProgressRead:
    try:
        return SetupProgressRead(**setup_wizard.complete_setup(db))
    except setup_wizard.SetupError as exc:
        raise _setup_error(exc) from None


@router.get("/agent", response_model=SetupAgentStatusRead)
def get_agent(db: DbSession) -> SetupAgentStatusRead:
    return SetupAgentStatusRead(**setup_wizard.agent_status(db))


@router.get("/manual-commands", response_model=ManualCommandsRead)
def get_manual_commands() -> ManualCommandsRead:
    return ManualCommandsRead(**setup_wizard.manual_commands())


# --- Step 3: Proxmox VE (automatic) ---------------------------------------------


@router.post("/pve/provision", response_model=PveProvisionRead)
def provision_pve(db: DbSession) -> PveProvisionRead:
    try:
        return PveProvisionRead(**setup_wizard.provision_pve(db))
    except setup_wizard.SetupError as exc:
        raise _setup_error(exc) from None


# --- Step 4: PBS (automatic) -----------------------------------------------------


@router.get("/pbs/candidates", response_model=PbsCandidatesRead)
def get_pbs_candidates(db: DbSession) -> PbsCandidatesRead:
    try:
        return PbsCandidatesRead(**setup_wizard.pbs_candidates(db))
    except setup_wizard.SetupError as exc:
        raise _setup_error(exc) from None


@router.post("/pbs/fingerprint", response_model=PbsFingerprintRead)
def get_pbs_fingerprint(payload: PbsFingerprintRequest) -> PbsFingerprintRead:
    try:
        return PbsFingerprintRead(**setup_wizard.pbs_fingerprint(payload.api_url))
    except setup_wizard.SetupError as exc:
        raise _setup_error(exc) from None


@router.post("/pbs/provision", response_model=PbsProvisionRead)
def provision_pbs(payload: PbsProvisionRequest, request: Request, db: DbSession) -> PbsProvisionRead:
    client_key = request.client.host if request.client else "unknown"
    limiter = setup_wizard.pbs_provision_limiter
    if limiter.blocked(client_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "too_many_attempts", "message": "Too many failed attempts: wait 15 minutes, then retry."},
        )
    if not payload.fingerprint_confirmed:
        raise _setup_error(
            setup_wizard.SetupError(
                "fingerprint_not_confirmed",
                "Compare the fingerprint with PBS › Dashboard › Show fingerprint, then tick the confirmation box.",
            )
        )
    try:
        result = provision_pbs_access(
            payload.api_url,
            payload.fingerprint,
            payload.password.get_secret_value(),
            username=payload.username.strip(),
        )
    except PbsProvisionError as exc:
        if exc.code in {"bad_credentials", "fingerprint_mismatch"}:
            limiter.record(client_key)
        # The message never contains the password (see pbs_provisioning).
        logger.warning("Setup: PBS provisioning failed (%s): %s", exc.code, exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": exc.code, "message": str(exc)}) from None
    return PbsProvisionRead(**setup_wizard.store_pbs_access(db, result, pbs_vmid=payload.pbs_vm_id))


@router.put("/pbs/datastore")
def put_pbs_datastore(payload: PbsDatastoreSelect, db: DbSession) -> dict:
    try:
        return setup_wizard.select_pbs_datastore(db, payload.datastore.strip())
    except setup_wizard.SetupError as exc:
        raise _setup_error(exc) from None


# --- Manual configuration (advanced) ---------------------------------------------


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
        }
        if payload.pbs_vm_id is not None:
            values["PBS_EXECUTION_VM_ID"] = payload.pbs_vm_id
            values["PBS_EXECUTION_VM_NODE"] = (payload.pbs_vm_node or payload.node_name).strip()
    except ProbeError as exc:
        raise _bad_request(exc) from exc
    if payload.token_secret:
        values["PVE_API_TOKEN_SECRET"] = payload.token_secret
    elif not stored_secret(db, "PVE_API_TOKEN_SECRET"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API token secret is required.")
    update_settings(db, values)
    if not setup_wizard.setup_completed(db):
        setup_wizard.record_manual_pve(db)
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
        if payload.pbs_vm_id is not None:
            values["PBS_EXECUTION_VM_ID"] = payload.pbs_vm_id
            values["PBS_EXECUTION_VM_NODE"] = effective_settings(db).pve_node_name
    except ProbeError as exc:
        raise _bad_request(exc) from exc
    if payload.token_secret:
        values["PBS_TOKEN_SECRET"] = payload.token_secret
    elif not stored_secret(db, "PBS_TOKEN_SECRET"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API token secret is required.")
    update_settings(db, values)
    if not setup_wizard.setup_completed(db):
        setup_wizard.save_progress(
            db,
            data={
                "pbs": {
                    "api_url": values["PBS_API_URL"],
                    "fingerprint": values["PBS_FINGERPRINT"],
                    "token_id": values["PBS_TOKEN_ID"],
                    "datastore": values["PBS_DATASTORE"],
                    "vmid": payload.pbs_vm_id,
                    "manual": True,
                }
            },
        )
    return get_setup_status(db)
