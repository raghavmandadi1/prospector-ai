from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db.session import init_db
from app.api import channels, features, analysis
from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources on startup, clean up on shutdown."""
    await init_db()
    yield
    # Cleanup (close DB pool, etc.) happens automatically via SQLAlchemy


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

app.include_router(channels.router, prefix="/api/v1")
app.include_router(features.router, prefix="/api/v1")
app.include_router(analysis.router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "geoprospector-api"}
