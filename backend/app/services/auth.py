"""
Authentication service for Bloodhound.
- Credentials stored in Redis (bcrypt hashed password)
- JWT token in httpOnly cookie
- Default: bloodhound / password

The JWT signing secret is unique per appliance: generated randomly on first
start and persisted in Redis (auth:jwt_secret). It is never shipped in the
source or the OVA — a factory reset (FLUSHALL) makes the next boot generate
a fresh one. It is also rotated on every password change, which invalidates
all previously issued sessions.
"""

import logging
import os
import secrets
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt

from app.services.cache import get_redis

log = logging.getLogger(__name__)

ALGORITHM      = "HS256"
TOKEN_EXPIRE_H = 24

DEFAULT_USERNAME = "bloodhound"
DEFAULT_PASSWORD = "password"
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_BYTES = 72  # bcrypt limit — bcrypt>=5 raises beyond this

REDIS_USERNAME_KEY = "auth:username"
REDIS_PASSWORD_KEY = "auth:password"
REDIS_SECRET_KEY   = "auth:jwt_secret"

# Login throttling: max failed attempts per client IP within the window
MAX_FAILED_LOGINS   = 10
FAILED_LOGIN_WINDOW = 15 * 60  # seconds

_secret_key: str | None = None


async def init_secret() -> None:
    """Load (or create on first boot) the per-appliance JWT secret."""
    global _secret_key
    env_secret = os.getenv("BLOODHOUND_JWT_SECRET")
    if env_secret:
        _secret_key = env_secret
        return
    r = await get_redis()
    secret = await r.get(REDIS_SECRET_KEY)
    if not secret:
        secret = secrets.token_urlsafe(48)
        # NX: if two processes race on first boot, both end up with the same value
        await r.set(REDIS_SECRET_KEY, secret, nx=True)
        secret = await r.get(REDIS_SECRET_KEY)
        log.info("Generated a new per-appliance JWT secret")
    _secret_key = secret


async def _rotate_secret() -> None:
    """Replace the JWT secret — invalidates every existing session."""
    global _secret_key
    if os.getenv("BLOODHOUND_JWT_SECRET"):
        return  # pinned by the environment, nothing to rotate
    r = await get_redis()
    secret = secrets.token_urlsafe(48)
    await r.set(REDIS_SECRET_KEY, secret)
    _secret_key = secret


def _secret() -> str:
    if not _secret_key:
        raise RuntimeError("JWT secret not initialized — init_secret() must run at startup")
    return _secret_key


async def _get_credentials() -> tuple[str, str]:
    """Return (username, hashed_password) from Redis, or defaults."""
    r = await get_redis()
    username = await r.get(REDIS_USERNAME_KEY)
    password = await r.get(REDIS_PASSWORD_KEY)

    if not username or not password:
        # First run — initialize with defaults
        hashed = bcrypt.hashpw(DEFAULT_PASSWORD.encode(), bcrypt.gensalt()).decode()
        await r.set(REDIS_USERNAME_KEY, DEFAULT_USERNAME)
        await r.set(REDIS_PASSWORD_KEY, hashed)
        return DEFAULT_USERNAME, hashed

    return username, password


async def verify_credentials(username: str, password: str) -> bool:
    """Verify username and password against stored credentials."""
    stored_user, stored_hash = await _get_credentials()
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return False
    # Always run bcrypt, even on a wrong username, so response timing
    # doesn't reveal whether the username exists.
    password_ok = bcrypt.checkpw(password.encode(), stored_hash.encode())
    return secrets.compare_digest(username, stored_user) and password_ok


async def is_login_blocked(client_ip: str) -> bool:
    r = await get_redis()
    fails = await r.get(f"auth:fail:{client_ip}")
    return int(fails or 0) >= MAX_FAILED_LOGINS


async def record_failed_login(client_ip: str) -> None:
    r = await get_redis()
    key = f"auth:fail:{client_ip}"
    fails = await r.incr(key)
    if fails == 1:
        await r.expire(key, FAILED_LOGIN_WINDOW)
    log.warning(f"Failed login attempt from {client_ip} ({fails}/{MAX_FAILED_LOGINS})")


async def clear_failed_logins(client_ip: str) -> None:
    r = await get_redis()
    await r.delete(f"auth:fail:{client_ip}")


def create_token(username: str) -> str:
    """Create a JWT token valid for TOKEN_EXPIRE_H hours."""
    payload = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_H),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def verify_token(token: str) -> str | None:
    """Verify JWT token and return username, or None if invalid."""
    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        log.debug("Token expired")
        return None
    except jwt.InvalidTokenError:
        log.debug("Invalid token")
        return None


async def change_credentials(new_username: str, new_password: str) -> bool:
    """Change username and password, and invalidate all existing sessions."""
    if not new_username or not new_username.strip():
        raise ValueError("Username cannot be empty")
    if len(new_password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if len(new_password.encode()) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
    r = await get_redis()
    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    await r.set(REDIS_USERNAME_KEY, new_username.strip())
    await r.set(REDIS_PASSWORD_KEY, hashed)
    await _rotate_secret()
    log.info(f"Credentials updated for user: {new_username}")
    return True


async def get_username() -> str:
    """Return current username."""
    stored_user, _ = await _get_credentials()
    return stored_user
