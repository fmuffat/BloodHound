"""
Redis cache client and key conventions.

Key schema:
  dns:{ip}              → resolved hostname          (TTL: dns_cache_ttl_seconds)
  dns:pending           → SET of IPs awaiting resolution
  ruckus:mac:{mac}      → JSON client info           (TTL: 10min)
  ruckus:wlan:{id}      → JSON wlan info             (TTL: 1h)
  ruckus:venue:{id}     → JSON venue info            (TTL: 1h)
  ruckus:guest:{id}     → JSON guest info            (TTL: 1h)
  ruckus:token          → current Ruckus One access token
  ruckus:last_sync      → ISO timestamp of last full sync
"""

import redis.asyncio as aioredis
from app.config import settings

_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = await aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis


# ── DNS helpers ───────────────────────────────────────────────────────────────

async def get_hostname(ip: str) -> str | None:
    r = await get_redis()
    return await r.get(f"dns:{ip}")


async def set_hostname(ip: str, hostname: str, ttl: int | None = None) -> None:
    r = await get_redis()
    ttl = ttl or settings.dns_cache_ttl_seconds
    await r.setex(f"dns:{ip}", ttl, hostname)


async def queue_for_dns_resolution(ip: str) -> None:
    """Add an IP to the pending resolution SET."""
    r = await get_redis()
    await r.sadd("dns:pending", ip)


async def pop_pending_ips(count: int = 500) -> list[str]:
    r = await get_redis()
    ips = await r.spop("dns:pending", count)
    return list(ips) if ips else []


# ── Ruckus client helpers ─────────────────────────────────────────────────────

async def get_client_info(mac: str) -> dict | None:
    import json
    r = await get_redis()
    raw = await r.get(f"ruckus:mac:{mac.lower()}")
    if raw:
        return json.loads(raw)
    # Fallback to SmartZone cache
    raw = await r.get(f"sz:mac:{mac.upper()}")
    return json.loads(raw) if raw else None


async def set_client_info(mac: str, info: dict, ttl: int = 600) -> None:
    import json
    r = await get_redis()
    await r.setex(f"ruckus:mac:{mac.lower()}", ttl, json.dumps(info))


# ── Ruckus token helpers ──────────────────────────────────────────────────────

async def get_ruckus_token() -> str | None:
    r = await get_redis()
    return await r.get("ruckus:token")


async def set_ruckus_token(token: str, ttl: int = 3500) -> None:
    r = await get_redis()
    await r.setex("ruckus:token", ttl, token)


# ── Platform exclusivity ──────────────────────────────────────────────────────

PLATFORMS = ["ruckus_one", "unleashed", "smartzone"]


async def set_active_platform(platform: str) -> None:
    """Activate one platform and disable the other two (mutually exclusive)."""
    import json
    r = await get_redis()
    for p in PLATFORMS:
        if p == platform:
            await r.set(f"platform:{p}:enabled", "true")
        else:
            await r.set(f"platform:{p}:enabled", "false")
            # Also disable in their own config flags
            if p == "unleashed":
                await r.set("unleashed:enabled", "false")
            elif p == "smartzone":
                raw = await r.get("sz:config")
                if raw:
                    cfg = json.loads(raw)
                    cfg["enabled"] = False
                    await r.set("sz:config", json.dumps(cfg))


async def get_active_platform() -> str | None:
    r = await get_redis()
    for p in PLATFORMS:
        val = await r.get(f"platform:{p}:enabled")
        if val == "true":
            return p
    return None


async def is_platform_enabled(platform: str) -> bool:
    r = await get_redis()
    val = await r.get(f"platform:{platform}:enabled")
    return val == "true"
