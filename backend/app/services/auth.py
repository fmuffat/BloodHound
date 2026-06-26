"""
Authentication service for Bloodhound.
- Credentials stored in Redis (bcrypt hashed password)
- JWT token in httpOnly cookie
- Default: bloodhound / password
"""

import logging
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt

from app.services.cache import get_redis

log = logging.getLogger(__name__)

SECRET_KEY     = "bloodhound-secret-key-change-in-production"
ALGORITHM      = "HS256"
TOKEN_EXPIRE_H = 24

DEFAULT_USERNAME = "bloodhound"
DEFAULT_PASSWORD = "password"

REDIS_USERNAME_KEY = "auth:username"
REDIS_PASSWORD_KEY = "auth:password"


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
    if username != stored_user:
        return False
    return bcrypt.checkpw(password.encode(), stored_hash.encode())


def create_token(username: str) -> str:
    """Create a JWT token valid for TOKEN_EXPIRE_H hours."""
    payload = {
        "sub": username,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_H),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> str | None:
    """Verify JWT token and return username, or None if invalid."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except jwt.ExpiredSignatureError:
        log.debug("Token expired")
        return None
    except jwt.InvalidTokenError:
        log.debug("Invalid token")
        return None


async def change_credentials(new_username: str, new_password: str) -> bool:
    """Change username and password."""
    r = await get_redis()
    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    await r.set(REDIS_USERNAME_KEY, new_username)
    await r.set(REDIS_PASSWORD_KEY, hashed)
    log.info(f"Credentials updated for user: {new_username}")
    return True


async def get_username() -> str:
    """Return current username."""
    stored_user, _ = await _get_credentials()
    return stored_user
