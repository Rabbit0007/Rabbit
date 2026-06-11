from __future__ import annotations

from fastapi import Depends, FastAPI

from cairn.server import auth_db, product_db
from cairn.server.middleware.auth import require_auth
from cairn.server.routers import (
    activity,
    auth,
    campaign,
    templates,
    timeline,
    vulnerabilities,
    workers,
)

_protected = [Depends(require_auth)]


def init_extension_state() -> None:
    auth_db.configure_auth_db()
    product_db.configure_product_db()


def include_extension_routers(app: FastAPI) -> None:
    app.include_router(auth.router)
    app.include_router(vulnerabilities.router, dependencies=_protected)
    app.include_router(workers.router, dependencies=_protected)
    app.include_router(templates.router, dependencies=_protected)
    app.include_router(timeline.router, dependencies=_protected)
    app.include_router(activity.router, dependencies=_protected)
    app.include_router(campaign.router, dependencies=_protected)
