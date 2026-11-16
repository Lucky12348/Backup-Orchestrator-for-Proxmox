"""Integrations page (Phase 6b): health of Proxmox VE, PBS and the host agent."""

from fastapi import APIRouter, HTTPException, status

from app.services import integration_health

router = APIRouter(prefix="/integrations", tags=["integrations"])


@router.get("/health")
def get_health() -> list[dict]:
    """State, version, latency, last sync and pinned fingerprint of each integration."""
    return integration_health.check_all()


@router.post("/{key}/test")
def test_integration(key: str) -> dict:
    """The "Test" button: a real call to that system, now."""
    if key not in integration_health.KEYS:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown integration")
    return integration_health.check(key)
