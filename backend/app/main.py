from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup, clean up on shutdown."""
    if settings.dev_mode:
        logger.info("DEV MODE enabled — skipping DB init, Celery, and Redis")
    else:
        from app.db.session import init_db
        await init_db()
    yield


app = FastAPI(
    title="GeoProspector API",
    description="Multi-agent mineral prospecting API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount the appropriate analysis router based on mode
if settings.dev_mode:
    from app.api import analysis_dev
    app.include_router(analysis_dev.router, prefix="/api/v1")
    logger.info("Mounted dev-mode analysis routes (in-process, no DB)")
else:
    from app.api import channels, features, analysis
    app.include_router(channels.router, prefix="/api/v1")
    app.include_router(features.router, prefix="/api/v1")
    app.include_router(analysis.router, prefix="/api/v1")

# Cached-coverage and reference layers are mode-independent: both read files on
# disk (SQLite / static GeoJSON), not Postgres, so they work either way.
from app.api import analysis_dev as _cache_routes  # noqa: E402
from app.api import reference  # noqa: E402

app.include_router(_cache_routes.cache_router, prefix="/api/v1")
app.include_router(reference.router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "service": "geoprospector-api",
        "dev_mode": settings.dev_mode,
    }
