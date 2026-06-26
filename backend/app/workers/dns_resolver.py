"""
DNS batch resolver worker.

Every N minutes:
  1. Pop pending IPs from Redis SET dns:pending
  2. Resolve them in parallel (asyncio + socket)
  3. Store results in Redis dns:{ip} → hostname
"""

import asyncio
import ipaddress
import logging
import socket

from app.config import settings
from app.services.cache import pop_pending_ips, set_hostname

log = logging.getLogger(__name__)


def _is_private(ip: str) -> bool:
    """
    True for RFC1918 (IPv4) and equivalent IPv6 ranges (link-local fe80::/10,
    unique local fc00::/7, loopback) — none of these have useful PTR records,
    so skip the (slow, doomed-to-fail) external resolution attempt.
    Uses the standard ipaddress module rather than a hardcoded prefix list,
    so both IPv4 and IPv6 are handled correctly and consistently.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        # Not a valid IP literal (shouldn't normally happen) — treat as
        # private/skip rather than wasting a resolution attempt on it.
        return True
    return addr.is_private


async def _resolve_one(ip: str) -> tuple[str, str | None]:
    """Resolve a single IP to hostname. Returns (ip, hostname|None)."""
    try:
        loop = asyncio.get_event_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(None, socket.gethostbyaddr, ip),
            timeout=settings.dns_timeout_seconds,
        )
        return ip, result[0]
    except (socket.herror, socket.gaierror, asyncio.TimeoutError):
        return ip, None
    except Exception as e:
        log.debug(f"DNS error for {ip}: {e}")
        return ip, None


async def resolve_batch() -> dict:
    """
    Resolve all pending IPs from Redis dns:pending SET.
    Processes in chunks of 50 to avoid overloading DNS.
    """
    ips = await pop_pending_ips(count=500)
    if not ips:
        return {"resolved": 0, "failed": 0, "skipped": 0}

    resolved = 0
    failed = 0
    skipped = 0

    # Private IPs — store as-is, no PTR lookup needed
    private_ips = [ip for ip in ips if _is_private(ip)]
    public_ips  = [ip for ip in ips if not _is_private(ip)]

    for ip in private_ips:
        await set_hostname(ip, ip, ttl=settings.dns_cache_ttl_seconds)
        skipped += 1

    # Public IPs — resolve in parallel chunks
    chunk_size = 50
    for i in range(0, len(public_ips), chunk_size):
        chunk = public_ips[i:i + chunk_size]
        results = await asyncio.gather(*[_resolve_one(ip) for ip in chunk])

        for ip, hostname in results:
            if hostname:
                hostname = hostname.rstrip(".")
                await set_hostname(ip, hostname, ttl=settings.dns_cache_ttl_seconds)
                resolved += 1
                log.debug(f"  {ip} → {hostname}")
            else:
                # Short TTL on failure — retry sooner
                await set_hostname(ip, ip, ttl=300)
                failed += 1

    log.info(f"DNS batch: {resolved} resolved, {failed} failed, {skipped} private")
    return {"resolved": resolved, "failed": failed, "skipped": skipped}
