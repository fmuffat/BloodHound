"""
Reverse DNS worker (PTR lookups), as in sFlow Analytics.

Every N minutes:
  1. Pop pending IPs from Redis SET dns:pending (queued by the search)
  2. Resolve them in parallel against the configured DNS servers
  3. Store results in Redis dns:{ip} → hostname (or the IP itself when no name)

Settings (Redis dns:config, Settings → DNS):
  enabled       resolve at all (default true)
  servers       up to 4 resolver IPs; empty = the system resolver
  max_per_run   lookups per run (default 500)
Private addresses are only looked up when servers are configured: the
system resolver (public DNS) has no PTR records for them, an internal DNS
server usually does.
"""

import asyncio
import ipaddress
import json
import logging

import dns.exception
import dns.resolver
import dns.reversename

from app.config import settings
from app.services.cache import get_redis, pop_pending_ips, set_hostname

log = logging.getLogger(__name__)

CONFIG_KEY = "dns:config"
MAX_SERVERS = 4
NEGATIVE_TTL = 300        # no name / error: retried after 5 min
CHUNK = 50                # parallel lookups


async def get_config() -> dict:
    r = await get_redis()
    raw = await r.get(CONFIG_KEY)
    cfg = json.loads(raw) if raw else {}
    return {
        "enabled": cfg.get("enabled", True),
        "servers": cfg.get("servers", []),
        "max_per_run": int(cfg.get("max_per_run", 500)),
    }


async def save_config(body: dict) -> dict:
    """Validates and stores the settings; raises ValueError with a user message."""
    cfg = await get_config()
    if "enabled" in body:
        cfg["enabled"] = bool(body["enabled"])
    if "servers" in body:
        servers = [str(s).strip() for s in body["servers"] if str(s).strip()]
        for s in servers:
            try:
                ipaddress.ip_address(s)
            except ValueError:
                raise ValueError(f"Not an IP address: {s}")
        if len(servers) > MAX_SERVERS:
            raise ValueError(f"At most {MAX_SERVERS} DNS servers")
        servers_changed = servers != cfg["servers"]
        cfg["servers"] = servers
    else:
        servers_changed = False
    if "max_per_run" in body:
        n = int(body["max_per_run"])
        if not 10 <= n <= 5000:
            raise ValueError("Lookups per run must be between 10 and 5000")
        cfg["max_per_run"] = n
    r = await get_redis()
    await r.set(CONFIG_KEY, json.dumps(cfg))
    if servers_changed:
        # Names that failed with the previous servers are retried at once
        retried = await forget_failures()
        log.info(f"DNS servers changed to {cfg['servers'] or 'system resolver'}: {retried} failed lookups retried")
    return cfg


async def forget_failures() -> int:
    """Drop cached failures (value == IP) and queue them again."""
    r = await get_redis()
    n = 0
    async for key in r.scan_iter(match="dns:*", count=1000):
        if key in ("dns:pending", CONFIG_KEY):
            continue
        ip = key[4:]
        if await r.get(key) == ip:
            await r.delete(key)
            await r.sadd("dns:pending", ip)
            n += 1
    return n


async def cache_stats() -> dict:
    r = await get_redis()
    names = no_name = 0
    async for key in r.scan_iter(match="dns:*", count=1000):
        if key in ("dns:pending", CONFIG_KEY):
            continue
        if await r.get(key) == key[4:]:
            no_name += 1
        else:
            names += 1
    return {"names": names, "no_name": no_name, "pending": int(await r.scard("dns:pending") or 0)}


def _make_lookup(servers: list[str]):
    resolver = dns.resolver.Resolver(configure=not servers)
    if servers:
        resolver.nameservers = servers
    resolver.timeout = resolver.lifetime = settings.dns_timeout_seconds

    def lookup(ip: str) -> str | None:
        try:
            answer = resolver.resolve(dns.reversename.from_address(ip), "PTR")
            return str(answer[0]).rstrip(".")
        except (dns.exception.DNSException, OSError, ValueError):
            return None

    return lookup


def _is_private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True   # not an IP literal: never looked up


async def resolve_batch() -> dict:
    """Resolve pending IPs (up to max_per_run)."""
    cfg = await get_config()
    if not cfg["enabled"]:
        return {"enabled": False, "resolved": 0, "failed": 0, "skipped": 0}

    ips = await pop_pending_ips(count=cfg["max_per_run"])
    if not ips:
        return {"enabled": True, "resolved": 0, "failed": 0, "skipped": 0}

    custom = bool(cfg["servers"])
    to_lookup = [ip for ip in ips if custom or not _is_private(ip)]
    skipped = len(ips) - len(to_lookup)
    for ip in ips:
        if ip not in to_lookup:
            # private address and no internal DNS server: keep the IP as name
            await set_hostname(ip, ip, ttl=settings.dns_cache_ttl_seconds)

    lookup = _make_lookup(cfg["servers"])
    loop = asyncio.get_running_loop()
    resolved = failed = 0
    for i in range(0, len(to_lookup), CHUNK):
        chunk = to_lookup[i:i + CHUNK]
        names = await asyncio.gather(*[loop.run_in_executor(None, lookup, ip) for ip in chunk])
        for ip, name in zip(chunk, names):
            if name:
                await set_hostname(ip, name, ttl=settings.dns_cache_ttl_seconds)
                resolved += 1
            else:
                await set_hostname(ip, ip, ttl=NEGATIVE_TTL)
                failed += 1

    log.info(f"DNS batch: {resolved} resolved, {failed} without name, {skipped} private skipped"
             f" ({'servers ' + ', '.join(cfg['servers']) if custom else 'system resolver'})")
    return {"enabled": True, "resolved": resolved, "failed": failed, "skipped": skipped}
