"""
Client history service — stores client state snapshots in OpenSearch.

Each time a client is seen during a Ruckus One sync, a snapshot is written
to the bloodhound_client_history index. When enriching a log entry, the
snapshot closest to the log timestamp is retrieved to get accurate historical
data (SSID, guest type, sponsor, venue, etc.).
"""

import logging
from datetime import datetime, timezone

import httpx

from app.config import settings

log = logging.getLogger(__name__)

OPENSEARCH_URL = settings.opensearch_url
INDEX          = "bloodhound_client_history"
HEADERS        = {"Content-Type": "application/json"}

_index_ready   = False
_http_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        # Large connection pool + longer timeout to handle parallel log enrichment
        limits  = httpx.Limits(max_connections=50, max_keepalive_connections=20)
        timeout = httpx.Timeout(30.0, pool=60.0)
        _http_client = httpx.AsyncClient(timeout=timeout, limits=limits)
    return _http_client


async def _ensure_index():
    """Create index with mapping if it doesn't exist."""
    global _index_ready
    if _index_ready:
        return
    client = await _get_client()
    r = await client.head(f"{OPENSEARCH_URL}/{INDEX}")
    if r.status_code == 200:
        _index_ready = True
        return

    mapping = {
        "mappings": {
            "properties": {
                "mac":           {"type": "keyword"},
                "snapshot_at":   {"type": "date"},
                "hostname":      {"type": "keyword"},
                "username":      {"type": "keyword"},
                "os_type":       {"type": "keyword"},
                "device_type":   {"type": "keyword"},
                "venue":         {"type": "keyword"},
                "ap_name":       {"type": "keyword"},
                "ssid":          {"type": "keyword"},
                "is_guest":      {"type": "boolean"},
                "guest_type":    {"type": "keyword"},
                "guest_name":    {"type": "keyword"},
                "email":         {"type": "keyword"},
                "phone":         {"type": "keyword"},
                "sponsor_email": {"type": "keyword"},
                "source":        {"type": "keyword"},
            }
        }
    }
    r = await client.put(f"{OPENSEARCH_URL}/{INDEX}", json=mapping, headers=HEADERS)
    if r.status_code in (200, 201):
        log.info(f"Created index {INDEX}")
        _index_ready = True
    else:
        log.error(f"Failed to create index {INDEX}: {r.text}")


# Re-write an unchanged snapshot at most this often. Snapshots only need to
# exist when a client's state changes (get_snapshot_at picks the latest one
# before a log's timestamp); the periodic refresh keeps a recent snapshot
# available after the retention purge removes old ones.
SNAPSHOT_REFRESH_SECONDS = 24 * 3600


async def _state_changed(mac: str, doc: dict) -> bool:
    """True if this client's state differs from its last written snapshot."""
    import hashlib
    import json
    from app.services.cache import get_redis

    digest = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
    r = await get_redis()
    key = f"hist:last:{mac}"
    if await r.get(key) == digest:
        return False
    await r.setex(key, SNAPSHOT_REFRESH_SECONDS, digest)
    return True


async def write_snapshot(mac: str, info: dict, source: str = "ruckus_one") -> bool:
    """
    Write a client state snapshot to OpenSearch — only when the state changed
    (or once a day). Syncs run every 5 minutes; writing unconditionally grew
    the index by ~288 documents per client per day.
    """
    await _ensure_index()

    doc = {
        "mac":           mac.lower(),
        "hostname":      info.get("hostname", ""),
        "username":      info.get("username", ""),
        "os_type":       info.get("os_type", ""),
        "device_type":   info.get("device_type", ""),
        "venue":         info.get("venue", ""),
        "ap_name":       info.get("ap_name", ""),
        "ssid":          info.get("ssid", ""),
        "is_guest":      info.get("is_guest", False),
        "guest_type":    info.get("guest_type", ""),
        "guest_name":    info.get("guest_name", ""),
        "email":         info.get("email", ""),
        "phone":         info.get("phone", ""),
        "sponsor_email": info.get("sponsor_email", ""),
        "source":        source,
    }
    if not await _state_changed(doc["mac"], doc):
        return True
    doc["snapshot_at"] = datetime.now(timezone.utc).isoformat()

    client = await _get_client()
    r = await client.post(f"{OPENSEARCH_URL}/{INDEX}/_doc", json=doc, headers=HEADERS)
    if r.status_code in (200, 201):
        return True
    log.error(f"Failed to write snapshot for {mac}: {r.text}")
    # Forget the digest so the next sync retries instead of skipping
    from app.services.cache import get_redis
    await (await get_redis()).delete(f"hist:last:{doc['mac']}")
    return False


async def get_snapshot_at(mac: str, timestamp: str) -> dict | None:
    """
    Retrieve the client snapshot closest to (and before) the given timestamp.
    Used to enrich historical log entries with accurate data.
    """
    await _ensure_index()

    query = {
        "size": 1,
        "query": {
            "bool": {
                "must": [
                    {"term": {"mac": mac.lower()}},
                    {"range": {"snapshot_at": {"lte": timestamp}}}
                ]
            }
        },
        "sort": [{"snapshot_at": {"order": "desc"}}]
    }

    client = await _get_client()
    r = await client.post(
        f"{OPENSEARCH_URL}/{INDEX}/_search",
        json=query,
        headers=HEADERS,
    )
    if r.status_code != 200:
        log.error(f"History lookup failed for {mac}: {r.text}")
        return None

    data = r.json()
    hits = data.get("hits", {}).get("hits", [])
    return hits[0].get("_source") if hits else None


async def get_latest_snapshot(mac: str) -> dict | None:
    """Get the most recent snapshot for a MAC address."""
    await _ensure_index()

    query = {
        "size": 1,
        "query": {"term": {"mac": mac.lower()}},
        "sort": [{"snapshot_at": {"order": "desc"}}]
    }

    client = await _get_client()
    r = await client.post(
        f"{OPENSEARCH_URL}/{INDEX}/_search",
        json=query,
        headers=HEADERS,
    )
    if r.status_code != 200:
        return None

    data = r.json()
    hits = data.get("hits", {}).get("hits", [])
    return hits[0].get("_source") if hits else None


async def get_snapshots_until(mac: str, until: str, size: int = 200) -> list[dict]:
    """
    Snapshots of a MAC taken at or before `until`, newest first. Lets a
    search pick, for each log line, the snapshot in effect at that log's own
    timestamp — with one query per MAC instead of one per log.
    """
    await _ensure_index()

    query = {
        "size": size,
        "query": {
            "bool": {
                "must": [
                    {"term": {"mac": mac.lower()}},
                    {"range": {"snapshot_at": {"lte": until}}},
                ]
            }
        },
        "sort": [{"snapshot_at": {"order": "desc"}}],
    }

    client = await _get_client()
    r = await client.post(f"{OPENSEARCH_URL}/{INDEX}/_search", json=query, headers=HEADERS)
    if r.status_code != 200:
        log.error(f"History lookup failed for {mac}: {r.text}")
        return []
    return [h.get("_source", {}) for h in r.json().get("hits", {}).get("hits", [])]
