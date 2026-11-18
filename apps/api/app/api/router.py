from fastapi import APIRouter, Depends

from app.api.routes import (
    activity,
    agent,
    agents,
    assets,
    calendar,
    backup_runs,
    events,
    disks,
    external_backups,
    datastores,
    integrations,
    integrations_pbs,
    integrations_proxmox,
    notifications,
    overview,
    planning,
    proxmox,
    settings,
    setup,
    system,
    vms,
)
from app.auth import get_current_user, router as auth_router


public_router = APIRouter(prefix="/api/v1")
public_router.include_router(auth_router)
public_router.include_router(setup.public_router)
public_router.include_router(agent.router)
public_router.include_router(agents.public_router)
public_router.include_router(external_backups.public_callback_router)

protected_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(get_current_user)],
)

protected_router.include_router(overview.router)
protected_router.include_router(events.router)
protected_router.include_router(setup.router)
protected_router.include_router(settings.router)
protected_router.include_router(agent.status_router)
protected_router.include_router(agents.router)
protected_router.include_router(calendar.router)
protected_router.include_router(assets.router)
protected_router.include_router(vms.router)
protected_router.include_router(disks.router)
protected_router.include_router(external_backups.router)
protected_router.include_router(backup_runs.router)
protected_router.include_router(datastores.router)
protected_router.include_router(integrations.router)
protected_router.include_router(integrations_proxmox.router)
protected_router.include_router(integrations_pbs.router)
protected_router.include_router(planning.router)
protected_router.include_router(proxmox.router)
protected_router.include_router(system.router)
protected_router.include_router(notifications.router)
protected_router.include_router(activity.router)

api_router = APIRouter()
api_router.include_router(public_router)
api_router.include_router(protected_router)
# Root-level public routes (outside /api/v1): agent installer and calendar feed.
api_router.include_router(agents.install_router)
api_router.include_router(calendar.feed_router)
