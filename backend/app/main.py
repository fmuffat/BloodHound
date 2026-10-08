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

    # Per-appliance JWT secret — must be loaded before serving any request
    from app.services.auth import init_secret
    await init_secret()

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

API = "/api/v1"

# Reachable while the account must still change its password
MUST_CHANGE_OK = {f"{API}/auth/me", f"{API}/auth/change-password", f"{API}/auth/logout"}
# Read-only accounts (role "viewer"): reading, searching and exporting only
VIEWER_POST_OK = {
    f"{API}/search",
    f"{API}/investigation/export",
    f"{API}/investigation/export-zip",
    f"{API}/investigation/export-zip-event",
    f"{API}/auth/logout",
    f"{API}/auth/change-password",
}
# ...and no configuration, account management or erasure log, even to read
VIEWER_GET_DENIED = (f"{API}/settings", f"{API}/users", f"{API}/clients/erase-log")
VIEWER_GET_OK = {f"{API}/settings/preferences", f"{API}/settings/disk"}  # used by every page


# Logs managers (role "manager"): everything a viewer can, plus handling the
# logs themselves — erase a client, the erasure log, retention and purge.
# No server administration (platforms, SSL, preferences, accounts).
MANAGER_EXTRA = {
    ("POST", f"{API}/clients/erase"),
    ("GET",  f"{API}/clients/erase-log"),
    ("GET",  f"{API}/settings/retention"),
    ("POST", f"{API}/settings/retention"),
    ("POST", f"{API}/workers/purge-logs"),
}


def viewer_allowed(method: str, path: str) -> bool:
    if method in ("GET", "HEAD", "OPTIONS"):
        return path in VIEWER_GET_OK or not path.startswith(VIEWER_GET_DENIED)
    return method == "POST" and path in VIEWER_POST_OK


def role_allowed(role: str, method: str, path: str) -> bool:
    if role == "admin":
        return True
    if role == "manager" and (("GET" if method == "HEAD" else method), path) in MANAGER_EXTRA:
        return True
    return viewer_allowed(method, path)


class AuthMiddleware(BaseHTTPMiddleware):
    """Every API route needs a valid session (except login), and the
    account's role decides what it may do. The account is made available to
    the routes as request.state.user."""
    EXEMPT = {f"{API}/auth/login", "/health"}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        if path in self.EXEMPT or not path.startswith("/api/"):
            return await call_next(request)

        from app.services.auth import session_user
        user = await session_user(request.cookies.get("bh_token"))
        if not user:
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        if user.get("must_change_password") and path not in MUST_CHANGE_OK:
            return JSONResponse({"detail": "Password change required"}, status_code=403)
        if not role_allowed(user.get("role", ""), request.method, path):
            return JSONResponse({"detail": "Not allowed for your account"}, status_code=403)
        request.state.user = user
        return await call_next(request)

app.add_middleware(AuthMiddleware)
app.include_router(router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok"}
