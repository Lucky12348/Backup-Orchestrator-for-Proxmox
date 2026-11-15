"""Agent pairing and one-line installation.

* `router` (session required): pairing codes, list / revoke agents.
* `public_router`: registration, authenticated by the pairing code.
* `install_router` (root, public): install.sh and the agent archive.
"""

import time

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.api.dependencies import DbSession
from app.core.config import get_settings
from app.schemas.agents import (
    AgentRegisterRequest,
    AgentRegisterResponse,
    PairingCodeRead,
    RegisteredAgentRead,
)
from app.services.agent_bootstrap import (
    AppUrlError,
    agent_source_dir,
    app_certificate_fingerprint,
    app_url_from_base,
    build_agent_archive,
    install_commands,
    render_install_script,
)
from app.services.agent_registry import (
    PairingError,
    RegistrationValidationError,
    TooManyAttemptsError,
    check_register_rate,
    create_pairing_code,
    list_agents,
    record_register_failure,
    register_agent,
    revoke_agent,
)

router = APIRouter(prefix="/agents", tags=["agents"])
public_router = APIRouter(prefix="/agents", tags=["agents"])
install_router = APIRouter(prefix="/agent", tags=["agents"])


def _app_url(request: Request) -> str:
    try:
        return app_url_from_base(str(request.base_url))
    except AppUrlError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _agent_read(agent) -> RegisteredAgentRead:  # noqa: ANN001
    return RegisteredAgentRead(
        id=agent.id,
        role=agent.role,
        hostname=agent.hostname,
        base_url=agent.base_url,
        fingerprint=agent.fingerprint,
        created_at=agent.created_at,
        last_seen_at=agent.last_seen_at,
        revoked_at=agent.revoked_at,
        active=agent.revoked_at is None,
    )


@router.post("/pairing", response_model=PairingCodeRead, status_code=status.HTTP_201_CREATED)
def create_pairing(request: Request, db: DbSession) -> PairingCodeRead:
    app_url = _app_url(request)
    pairing = create_pairing_code(db)
    return PairingCodeRead(
        code=pairing.code,
        expires_at=pairing.expires_at,
        install_commands=install_commands(app_url, pairing.code),
        app_url=app_url,
        app_fingerprint=app_certificate_fingerprint(get_settings().tls_cert_file),
    )


@router.get("", response_model=list[RegisteredAgentRead])
def get_agents(db: DbSession) -> list[RegisteredAgentRead]:
    return [_agent_read(agent) for agent in list_agents(db)]


@router.delete("/{agent_id}", response_model=RegisteredAgentRead)
def delete_agent(agent_id: int, db: DbSession) -> RegisteredAgentRead:
    agent = revoke_agent(db, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found.")
    return _agent_read(agent)


@public_router.post("/register", response_model=AgentRegisterResponse, status_code=status.HTTP_201_CREATED)
def register(payload: AgentRegisterRequest, request: Request, db: DbSession) -> AgentRegisterResponse:
    client_key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    try:
        check_register_rate(client_key, now)
    except TooManyAttemptsError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)) from exc
    try:
        registration = register_agent(
            db,
            code=payload.code,
            role=payload.role,
            hostname=payload.hostname,
            base_url=payload.base_url,
            fingerprint=payload.fingerprint,
        )
    except RegistrationValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except PairingError as exc:
        record_register_failure(client_key, now)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return AgentRegisterResponse(
        agent_id=registration.agent.id,
        role=registration.agent.role,
        agent_token=registration.agent_token,
        callback_token=registration.callback_token,
        api_base_url=f"{_app_url(request)}/api/v1",
        app_fingerprint=app_certificate_fingerprint(get_settings().tls_cert_file),
    )


@install_router.get("/install.sh", include_in_schema=False)
def get_install_script(request: Request) -> Response:
    script = render_install_script(_app_url(request))
    return Response(
        content=script,
        media_type="text/x-shellscript; charset=utf-8",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@install_router.get("/agent.tar.gz", include_in_schema=False)
def get_agent_archive() -> Response:
    try:
        archive = build_agent_archive(str(agent_source_dir()))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(
        content=archive,
        media_type="application/gzip",
        headers={"Cache-Control": "no-store", "Content-Disposition": 'attachment; filename="pbo-agent.tar.gz"'},
    )
