"""
Authentication and accounts for Bloodhound.

Accounts live in Redis:
  users                 SET of usernames
  user:{username}       JSON {password_hash, role, must_change_password,
                              created_at, last_login_at, token_version}
Roles (same model as sFlow Analytics):
  admin   everything
  viewer  read-only: search, investigations, exports; no settings, no
          erasure, no account management (can change their own password)

Sessions are JWTs in an httpOnly cookie, signed with a per-appliance secret
(auth:jwt_secret, generated on first start, never shipped). Each token
carries the account's token_version: bumping it (password, role change,
deletion) closes that account's sessions only.

The single account of earlier versions (auth:username / auth:password) is
migrated to an admin account on first start.
"""

import json
import logging
import os
import re
import secrets
from datetime import datetime, timezone, timedelta

import bcrypt
import jwt

from app.services.cache import get_redis

log = logging.getLogger(__name__)

ALGORITHM      = "HS256"
TOKEN_EXPIRE_H = 24

ROLES = ("admin", "viewer")
DEFAULT_USERNAME = "bloodhound"
DEFAULT_PASSWORD = "password"
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_BYTES = 72  # bcrypt limit — bcrypt>=5 raises beyond this
USERNAME_RE = re.compile(r"^[A-Za-z0-9._@-]{2,64}$")

USERS_SET         = "users"
LEGACY_USERNAME   = "auth:username"
LEGACY_PASSWORD   = "auth:password"
REDIS_SECRET_KEY  = "auth:jwt_secret"

# Login throttling: max failed attempts per client IP within the window
MAX_FAILED_LOGINS   = 10
FAILED_LOGIN_WINDOW = 15 * 60  # seconds

_secret_key: str | None = None
# bcrypt of a random value: checked when the username does not exist, so the
# response time does not reveal which usernames exist.
_DUMMY_HASH = bcrypt.hashpw(secrets.token_bytes(16), bcrypt.gensalt()).decode()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _key(username: str) -> str:
    return f"user:{username}"


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password_policy(password: str, username: str = "") -> None:
    """Raises ValueError with a user-facing message."""
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise ValueError(f"Password must be at most {MAX_PASSWORD_BYTES} bytes")
    if username and password.lower() == username.lower():
        raise ValueError("Password must differ from the username")


def public(user: dict) -> dict:
    """Account fields that may be sent to the browser."""
    return {k: user.get(k) for k in ("username", "role", "must_change_password", "created_at", "last_login_at")}


# ── JWT secret ───────────────────────────────────────────────────────────────

async def init_secret() -> None:
    """Load (or create on first boot) the per-appliance JWT secret, and make
    sure at least one account exists."""
    global _secret_key
    env_secret = os.getenv("BLOODHOUND_JWT_SECRET")
    if env_secret:
        _secret_key = env_secret
    else:
        r = await get_redis()
        secret = await r.get(REDIS_SECRET_KEY)
        if not secret:
            # NX: if two processes race on first boot, both end up with the same value
            await r.set(REDIS_SECRET_KEY, secrets.token_urlsafe(48), nx=True)
            secret = await r.get(REDIS_SECRET_KEY)
            log.info("Generated a new per-appliance JWT secret")
        _secret_key = secret
    await ensure_accounts()


def _secret() -> str:
    if not _secret_key:
        raise RuntimeError("JWT secret not initialized — init_secret() must run at startup")
    return _secret_key


# ── Accounts ─────────────────────────────────────────────────────────────────

async def ensure_accounts() -> None:
    """First start: migrate the legacy single account, or create the default one."""
    r = await get_redis()
    if await r.scard(USERS_SET):
        return
    legacy_user = await r.get(LEGACY_USERNAME)
    legacy_hash = await r.get(LEGACY_PASSWORD)
    if legacy_user and legacy_hash:
        await _store(legacy_user, {
            "password_hash": legacy_hash, "role": "admin", "must_change_password": False,
            "created_at": _now(), "last_login_at": None, "token_version": 1,
        })
        await r.delete(LEGACY_USERNAME, LEGACY_PASSWORD)
        log.info(f"Migrated the existing account '{legacy_user}' (admin)")
    else:
        await _store(DEFAULT_USERNAME, {
            "password_hash": _hash(DEFAULT_PASSWORD), "role": "admin", "must_change_password": True,
            "created_at": _now(), "last_login_at": None, "token_version": 1,
        })
        log.info(f"Created the default account '{DEFAULT_USERNAME}' (password change required)")


async def _store(username: str, data: dict) -> None:
    r = await get_redis()
    await r.set(_key(username), json.dumps(data))
    await r.sadd(USERS_SET, username)


async def get_user(username: str) -> dict | None:
    if not username:
        return None
    r = await get_redis()
    raw = await r.get(_key(username))
    return {**json.loads(raw), "username": username} if raw else None


async def list_users() -> list[dict]:
    r = await get_redis()
    names = sorted(await r.smembers(USERS_SET), key=str.lower)
    users = [await get_user(n) for n in names]
    return [u for u in users if u]


async def count_admins() -> int:
    return sum(1 for u in await list_users() if u["role"] == "admin")


async def _update(username: str, **changes) -> dict:
    user = await get_user(username)
    if not user:
        raise KeyError(username)
    data = {k: v for k, v in user.items() if k != "username"}
    if changes.pop("close_sessions", False):
        data["token_version"] = int(data.get("token_version", 1)) + 1
    data.update(changes)
    await _store(username, data)
    return {**data, "username": username}


async def create_user(username: str, role: str, password: str, must_change: bool = True) -> dict:
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("Username: 2-64 characters, letters, digits and . _ @ - only")
    if role not in ROLES:
        raise ValueError("Unknown role")
    if await get_user(username):
        raise FileExistsError(username)
    check_password_policy(password, username)
    data = {"password_hash": _hash(password), "role": role, "must_change_password": must_change,
            "created_at": _now(), "last_login_at": None, "token_version": 1}
    await _store(username, data)
    log.info(f"Account created: {username} ({role})")
    return {**data, "username": username}


async def set_password(username: str, password: str, must_change: bool = False) -> dict:
    """New password; closes the account's sessions."""
    check_password_policy(password, username)
    log.info(f"Password changed for: {username}")
    return await _update(username, password_hash=_hash(password),
                         must_change_password=must_change, close_sessions=True)


async def set_role(username: str, role: str) -> dict:
    if role not in ROLES:
        raise ValueError("Unknown role")
    log.info(f"Role of {username} set to {role}")
    return await _update(username, role=role, close_sessions=True)


async def delete_user(username: str) -> None:
    r = await get_redis()
    await r.delete(_key(username))
    await r.srem(USERS_SET, username)
    log.info(f"Account deleted: {username}")


async def ensure_user_password(username: str, password: str) -> None:
    """Appliance console / first-boot assistant: set this account's password,
    creating it as an admin if needed (no change required at sign-in). Also
    makes it an admin: the console is the way back in when no web
    administrator can sign in any more."""
    await ensure_accounts()
    user = await get_user(username)
    if user:
        await set_password(username, password, must_change=False)
        if user["role"] != "admin":
            await set_role(username, "admin")
    else:
        await create_user(username, "admin", password, must_change=False)


async def verify_credentials(username: str, password: str) -> dict | None:
    """The account if the password is right, else None (constant-ish time)."""
    user = await get_user(username)
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        return None
    stored = user["password_hash"] if user else _DUMMY_HASH
    ok = bcrypt.checkpw(password.encode(), stored.encode())
    return user if (user and ok) else None


async def record_login(username: str) -> None:
    try:
        await _update(username, last_login_at=_now())
    except KeyError:
        pass


# ── Login throttling ─────────────────────────────────────────────────────────

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


# ── Sessions (JWT) ───────────────────────────────────────────────────────────

def create_token(user: dict) -> str:
    """JWT for this account, valid TOKEN_EXPIRE_H hours or until its token_version changes."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["username"],
        "tv":  int(user.get("token_version", 1)),
        "exp": now + timedelta(hours=TOKEN_EXPIRE_H),
        "iat": now,
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


def _decode(token: str) -> dict | None:
    try:
        return jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        log.debug("Token expired")
    except jwt.InvalidTokenError:
        log.debug("Invalid token")
    return None


def verify_token(token: str) -> str | None:
    """Signature/expiry check only; returns the username. The authoritative
    check (account still exists, sessions not closed) is session_user()."""
    payload = _decode(token) if token else None
    return payload.get("sub") if payload else None


async def session_user(token: str | None) -> dict | None:
    payload = _decode(token) if token else None
    if not payload:
        return None
    user = await get_user(payload.get("sub", ""))
    if not user or int(user.get("token_version", 1)) != int(payload.get("tv", -1)):
        return None
    return user
