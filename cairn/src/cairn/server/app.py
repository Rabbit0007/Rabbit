from contextlib import asynccontextmanager
import os
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from cairn import __version__
from cairn.server import db
from cairn.server.app_extensions import include_extension_routers, init_extension_state
from cairn.server.middleware.auth import require_auth
from cairn.server.report_agent_service import start_report_agent, stop_report_agent
from cairn.server.routers import export, findings, goals, hints, intents, projects, settings, steps

STATIC_DIR = Path(__file__).parent / "static"
API_DOCS_ENABLED = os.environ.get("CAIRN_ENABLE_API_DOCS", "").strip().lower() in {"1", "true", "yes", "on"}
_protected = [Depends(require_auth)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.configure(db.DEFAULT_DB)
    init_extension_state()
    start_report_agent()
    try:
        yield
    finally:
        stop_report_agent()


app = FastAPI(
    title="Cairn",
    description="Fact-graph based collaborative exploration protocol",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if API_DOCS_ENABLED else None,
    redoc_url="/redoc" if API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if API_DOCS_ENABLED else None,
)

app.include_router(settings.router, dependencies=_protected)
app.include_router(projects.router, dependencies=_protected)
app.include_router(hints.router, dependencies=_protected)
app.include_router(intents.router, dependencies=_protected)
app.include_router(steps.router, dependencies=_protected)
app.include_router(goals.router, dependencies=_protected)
app.include_router(findings.router, dependencies=_protected)
app.include_router(export.router, dependencies=_protected)
include_extension_routers(app)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
