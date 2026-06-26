"""
FastAPI application entry point.
Starts periodic background workers via APScheduler on startup.
"""

import logging
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.services import smartzone
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from app.config import settings
from app.services import ruckus, unleashed
from app.workers.dns_resolver import resolve_batch

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="Europe/Paris")


async def daily_purge():
    """Purge logs older than retention period. Runs daily at midnight."""
    try:
        from app.services.cache import get_redis
        from app.api.routes import _purge_old_logs
        r = await get_redis()
        days_raw = await r.get("retention:days")
        days = int(days_raw) if days_raw else 30
        log.info(f"Running daily log purge — retention: {days} days")
        result = await _purge_old_logs(days)
        log.info(f"Daily purge complete: {result}")
    except Exception as e:
        log.error(f"Daily purge failed: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting syslog-backend")

    # Initial Ruckus One sync
    try:
        await ruckus.full_sync()
    except Exception as e:
        log.warning(f"Initial Ruckus One sync failed: {e}")

    # Initial SmartZone sync
    try:
        await smartzone.sync()
    except Exception as e:
        log.warning(f"Initial SmartZone sync failed: {e}")

    # Initial Unleashed sync
    try:
        await unleashed.sync()
    except Exception as e:
        log.warning(f"Initial Unleashed sync failed: {e}")

    # Ruckus One sync every N minutes
    scheduler.add_job(
        ruckus.full_sync,
        "interval",
        minutes=settings.ruckus_sync_interval_minutes,
        id="ruckus_sync",
        max_instances=1,
        coalesce=True,
    )

    # Unleashed sync every N minutes
    scheduler.add_job(
        unleashed.sync,
        "interval",
        minutes=settings.unleashed_sync_interval_minutes,
        id="unleashed_sync",
        max_instances=1,
        coalesce=True,
    )

    # DNS batch resolution every N minutes
    scheduler.add_job(
        resolve_batch,
        "interval",
        minutes=settings.dns_resolve_interval_minutes,
        id="dns_resolve",
        max_instances=1,
        coalesce=True,
    )

    # SmartZone sync every N minutes
    scheduler.add_job(
        smartzone.sync,
        "interval",
        minutes=settings.smartzone_sync_interval_minutes,
        id="smartzone_sync",
        max_instances=1,
        coalesce=True,
    )

    # Daily log purge at midnight
    scheduler.add_job(
        daily_purge,
        "cron",
        hour=0,
        minute=0,
        id="daily_purge",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    log.info(
        f"Workers started — "
        f"Ruckus One every {settings.ruckus_sync_interval_minutes}min, "
        f"Unleashed every {settings.unleashed_sync_interval_minutes}min, "
        f"DNS batch every {settings.dns_resolve_interval_minutes}min, "
        f"Log purge daily at midnight"
    )

    yield

    scheduler.shutdown(wait=False)
    log.info("Backend shutdown complete")


app = FastAPI(
    title="Syslog Platform API",
    description="Search and investigate 5-tuple flow logs from Ruckus APs",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AuthMiddleware(BaseHTTPMiddleware):
    """Protect all API routes except /auth/login and /health."""
    EXEMPT = {"/api/v1/auth/login", "/health"}

    async def dispatch(self, request: Request, call_next):
        if request.url.path in self.EXEMPT:
            return await call_next(request)
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        from app.services.auth import verify_token
        token = request.cookies.get("bh_token")
        if not token or not verify_token(token):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return await call_next(request)

app.add_middleware(AuthMiddleware)
app.include_router(router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
