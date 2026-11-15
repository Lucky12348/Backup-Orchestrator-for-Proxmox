"""Serve the built web UI (apps/web/dist) from the API, same origin.

* Real files under the dist directory (JS/CSS bundles, favicons…) are served
  as-is; hashed bundles under /assets get a long cache lifetime.
* Any other GET outside /api and /health returns index.html so client-side
  routes (/disks, /planning…) survive a reload.
* Unknown /api/* paths keep returning JSON 404s.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

API_ROOT = Path(__file__).resolve().parents[1]
_CANDIDATE_DIST_DIRS = (
    API_ROOT.parent / "web" / "dist",  # source checkout: apps/api -> apps/web/dist
    API_ROOT / "web-dist",  # container / packaged layout
)
_RESERVED_PREFIXES = ("api/", "health")
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


def resolve_dist_dir(configured: str = "") -> Path | None:
    candidates = (Path(configured),) if configured else _CANDIDATE_DIST_DIRS
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


def mount_frontend(app: FastAPI, dist_dir: Path | None) -> None:
    if dist_dir is None:
        logger.warning("Web UI build not found (apps/web/dist); only the API is served. Run `npm run build` in apps/web.")
        return

    index_file = dist_dir / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_frontend(full_path: str, request: Request) -> Response:
        if full_path.startswith(_RESERVED_PREFIXES):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = (dist_dir / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(dist_dir):
            cache = "public, max-age=31536000, immutable" if full_path.startswith("assets/") else "no-cache"
            return FileResponse(candidate, headers={"Cache-Control": cache, **_SECURITY_HEADERS})
        return FileResponse(index_file, headers={"Cache-Control": "no-cache", **_SECURITY_HEADERS})
