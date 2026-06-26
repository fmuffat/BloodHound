"""
Graylog API client and log enrichment service.

Search flow:
  1. Query Graylog Search API (Lucene syntax)
  2. Enrich each log from Redis:
       - client_mac → hostname, alias, guest_name, email, venue, ap_name  (Ruckus cache)
       - dst_ip     → dst_hostname                                          (DNS cache)
       - unknown dst_ip → queue for next DNS batch
  3. Return enriched logs to frontend
"""

import logging
import json
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import settings
from app.services.cache import (
    get_client_info,
    get_hostname,
    get_redis,
    queue_for_dns_resolution,
)
from app.services.ruckus import get_client_label

log = logging.getLogger(__name__)

GRAYLOG_AUTH    = (settings.graylog_user, settings.graylog_password)
GRAYLOG_HEADERS = {"Accept": "application/json", "X-Requested-By": "syslog-backend"}


async def _graylog_get(path: str, params: dict | None = None) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{settings.graylog_api_url}{path}",
            auth=GRAYLOG_AUTH,
            headers=GRAYLOG_HEADERS,
            params=params or {},
        )
        r.raise_for_status()
        return r.json()


async def _enrich_log(raw: dict, history_cache: dict | None = None) -> dict:
    """Enrich a raw Graylog log with Ruckus and DNS data from Redis."""
    zone_name_raw = raw.get("zone_name", "")
    # R1 sends a GUID (32 hex chars); SmartZone sends a readable zone name;
    # Unleashed's raw flow format sends no zone_name field at all.
    is_guid = bool(re.fullmatch(r"[0-9a-fA-F]{32}", zone_name_raw))
    platform_source = "ruckus_one" if is_guid else ("smartzone" if zone_name_raw else "unleashed")

    entry = {
        "id":        raw.get("_id") or raw.get("id", ""),
        "timestamp": raw.get("timestamp", ""),
        "source":    raw.get("source", ""),
        "message":   raw.get("message", ""),
        "src_ip":   raw.get("src_ip", ""),
        "dst_ip":   raw.get("dst_ip", ""),
        "src_port": raw.get("src_port"),
        "dst_port": raw.get("dst_port"),
        "proto":    raw.get("proto", ""),
        "ap_name":  raw.get("ap_name", ""),
        "ap_mac":   raw.get("ap_mac", ""),
        "wlan_id":  raw.get("wlan_id", ""),
        "platform": platform_source,
        "client_mac":   raw.get("client_mac", ""),
        "client_label": raw.get("client_mac", ""),
        "hostname":     "",
        "alias":        "",
        "username":     "",
        "os_type":      "",
        "device_type":  "",
        "venue":        "",
        "ssid":         "",
        "is_guest":     False,
        "guest_name": "",
        "email":      "",
        "phone":      "",
        "guest_type":          "",
        "sponsor_email":       "",
        "pass_duration_hours": 0,
        "expiry_date":         "",
        "creation_date":       "",
        "dst_hostname": raw.get("dst_ip", ""),
    }

    # Enrich from the correct platform cache based on log source
    mac = raw.get("client_mac", "")
    if mac:
        if platform_source == "smartzone":
            r = await get_redis()
            sz_raw = await r.get(f"sz:mac:{mac.upper()}")
            client_info = json.loads(sz_raw) if sz_raw else None
        else:
            client_info = await get_client_info(mac)
        if client_info:
            # ruckus:mac:{mac} is shared by both R1's and Unleashed's sync —
            # a device that roams between both (as can happen in a lab with
            # both deployed, never in a real single-platform deployment)
            # could have this cache last written by whichever platform
            # synced most recently. Network-context fields (ssid/venue/
            # ap_name) are only trustworthy when the cache entry's own
            # recorded source actually matches this log's platform — a
            # mismatch means we're looking at a stale value from the other
            # platform, not this log's real network. Device-identity fields
            # (hostname/username/os/device type) aren't network-specific,
            # so they're still safe to use regardless of source.
            cached_source = client_info.get("source")
            source_matches = (cached_source is None) or (cached_source == platform_source)

            entry["hostname"]            = client_info.get("hostname", "")
            entry["alias"]               = client_info.get("alias", "")
            entry["username"]            = client_info.get("username", "")
            entry["os_type"]             = client_info.get("os_type", "")
            entry["device_type"]         = client_info.get("device_type", "")
            entry["is_guest"]            = client_info.get("is_guest", False)
            entry["guest_name"]          = client_info.get("guest_name", "")
            entry["email"]               = client_info.get("email", "")
            entry["phone"]               = client_info.get("phone", "")
            entry["guest_type"]          = client_info.get("guest_type", "")
            entry["sponsor_email"]       = client_info.get("sponsor_email", "")
            entry["pass_duration_hours"] = client_info.get("pass_duration_hours", 0)
            entry["expiry_date"]         = client_info.get("expiry_date", "")
            entry["creation_date"]       = client_info.get("creation_date", "")
            entry["client_label"]        = get_client_label({**client_info, "mac": mac})
            if source_matches:
                entry["venue"] = client_info.get("venue", "")
                entry["ssid"]  = client_info.get("ssid", "")
                if not entry["ap_name"]:
                    entry["ap_name"] = client_info.get("ap_name", "")

        # Override with historical snapshot if available (pre-fetched) — R1/Unleashed only.
        # Same cross-platform contamination risk as the live cache above: a
        # snapshot may belong to a different platform than this log if the
        # device has roamed between them — only trust ssid/venue when the
        # snapshot's own source matches.
        if platform_source != "smartzone" and history_cache and mac in history_cache:
            hist = history_cache[mac]
            if hist:
                hist_source_matches = (hist.get("source") is None) or (hist.get("source") == platform_source)
                entry["hostname"]      = hist.get("hostname", entry["hostname"])
                if hist_source_matches:
                    entry["ssid"]      = hist.get("ssid", entry["ssid"])
                    entry["venue"]     = hist.get("venue", entry["venue"])
                entry["is_guest"]      = hist.get("is_guest", entry["is_guest"])
                entry["guest_type"]    = hist.get("guest_type", entry["guest_type"])
                entry["guest_name"]    = hist.get("guest_name", entry["guest_name"])
                entry["email"]         = hist.get("email", entry["email"])
                entry["phone"]         = hist.get("phone", entry["phone"])
                entry["sponsor_email"] = hist.get("sponsor_email", entry["sponsor_email"])
                entry["client_label"]  = get_client_label({**hist, "mac": mac})

    if platform_source == "smartzone":
        # SmartZone: wlan_id is already the SSID name, zone_name is already the venue
        if not entry["ssid"]:
            entry["ssid"] = entry["wlan_id"]
        if not entry["venue"] and zone_name_raw:
            entry["venue"] = zone_name_raw
        # Guest detection via Redis SZ wlan cache (keyed by SSID name for this platform)
        if entry["wlan_id"]:
            r = await get_redis()
            wlan_raw = await r.get(f"sz:wlan_by_name:{entry['wlan_id']}")
            if wlan_raw:
                entry["is_guest"] = json.loads(wlan_raw).get("is_guest", False)
    else:
        # R1 / Unleashed: wlan_id and zone_name are GUIDs requiring Redis lookup
        if not entry["ssid"] and entry["wlan_id"]:
            r = await get_redis()
            wlan_raw = await r.get(f"ruckus:wlan:{entry['wlan_id']}")
            if wlan_raw:
                wlan_info = json.loads(wlan_raw)
                entry["ssid"]     = wlan_info.get("ssid", "")
                entry["is_guest"] = wlan_info.get("is_guest", False)

        if not entry["venue"] and entry["ap_name"]:
            r = await get_redis()
            venue = await r.get(f"ruckus:ap_venue:{entry['ap_name']}")
            if venue:
                entry["venue"] = venue

    # Enrich dst_ip with DNS hostname
    dst_ip = raw.get("dst_ip", "")
    if dst_ip:
        hostname = await get_hostname(dst_ip)
        if hostname:
            entry["dst_hostname"] = hostname
        else:
            await queue_for_dns_resolution(dst_ip)

    return entry


async def search_logs(
    query: str = "*",
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    src_ip: str | None = None,
    dst_ip: str | None = None,
    dst_hostname: str | None = None,
    client_mac: str | None = None,
    client_label: str | None = None,
    username: str | None = None,
    ap_name: str | None = None,
    venue: str | None = None,
    proto: str | None = None,
    dst_port: int | None = None,
    ssid: str | None = None,
    is_guest: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Search Graylog with automatic enrichment.
    Query follows Graylog Lucene syntax.
    """

    # Build Lucene query from structured filters
    parts = []
    if query and query != "*":
        if re.match(r'^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$', query.strip()):
            parts.append(f'client_mac:"{query.strip().upper()}"')
        else:
            parts.append(f"({query})")

    # Filter logs by the currently active platform (R1/Unleashed use GUID zone_name,
    # SmartZone uses a readable zone_name). Always applied — including MAC-specific
    # searches (client detail view) — since only one platform is active at a time
    # in Bloodhound; a client's logs from an inactive platform are deliberately
    # hidden, consistent with the rest of the app.
    from app.services.cache import get_active_platform
    active = await get_active_platform()
    if active == "smartzone":
        # SmartZone zone_name is a readable name — present, but NOT a
        # 32-char hex GUID.
        parts.append('_exists_:zone_name AND NOT zone_name:/[0-9a-fA-F]{32}/')
    elif active == "ruckus_one":
        # Ruckus One zone_name IS a 32-char hex GUID.
        parts.append('zone_name:/[0-9a-fA-F]{32}/')
    elif active == "unleashed":
        # Unleashed's own syslog format sends no zone_name field at all —
        # this is the actual distinguishing signal versus R1 (GUID) and
        # SmartZone (readable name), not a shared "GUID" bucket as
        # previously assumed.
        parts.append('NOT _exists_:zone_name')
    if src_ip:
        parts.append(f'src_ip:"{src_ip}"')
    if dst_ip:
        parts.append(f'dst_ip:"{dst_ip}"')
    if client_mac:
        parts.append(f'client_mac:"{client_mac.upper()}"')
    if proto:
        parts.append(f'proto:{proto.upper()}')
    if dst_port:
        parts.append(f'dst_port:{dst_port}')
    if ap_name:
        parts.append(f'ap_name:"{ap_name}"')

    # Pre-filter by SSID: find wlan_id in Redis, filter by wlan_id in Graylog
    if ssid and not client_mac:
        r = await get_redis()
        import json as _json
        wlan_ids = []
        # Search all wlan keys for matching SSID
        for prefix in ["ruckus:wlan:*", "unleashed:*:wlan:*"]:
            wlan_keys = await r.keys(prefix)
            for key in wlan_keys:
                raw = await r.get(key)
                if raw:
                    try:
                        info = _json.loads(raw)
                        if ssid.lower() in info.get("ssid", "").lower():
                            # Extract wlan_id from key
                            wlan_id = key.split(":")[-1]
                            if wlan_id not in wlan_ids:
                                wlan_ids.append(wlan_id)
                    except Exception:
                        pass

        # SmartZone: wlan_id in Graylog IS the SSID name directly — check sz:wlan_by_name
        sz_wlan_raw = await r.get(f"sz:wlan_by_name:{ssid}")
        if sz_wlan_raw and ssid not in wlan_ids:
            wlan_ids.append(ssid)
        else:
            # Even without cache, the SSID name itself may directly be the wlan_id used by SmartZone
            sz_keys = await r.keys("sz:wlan_by_name:*")
            for key in sz_keys:
                name = key.replace("sz:wlan_by_name:", "")
                if ssid.lower() in name.lower() and name not in wlan_ids:
                    wlan_ids.append(name)
        if wlan_ids:
            wlan_query = " OR ".join([f'wlan_id:"{w}"' for w in wlan_ids])
            parts.append(f"({wlan_query})")
        else:
            # Fallback: search by MAC
            mac_keys = await r.keys("ruckus:mac:*")
            ssid_macs = []
            for key in mac_keys:
                raw = await r.get(key)
                if raw:
                    try:
                        info = _json.loads(raw)
                        if ssid.lower() in info.get("ssid", "").lower():
                            m = key.replace("ruckus:mac:", "").upper()
                            ssid_macs.append(m)
                    except Exception:
                        pass
            if ssid_macs:
                mac_query = " OR ".join([f'client_mac:"{m}"' for m in ssid_macs[:50]])
                parts.append(f"({mac_query})")
            else:
                return {"logs": [], "total": 0, "returned": 0, "query": "*", "error": None}

    # Pre-filter by is_guest: find guest MACs in Redis
    if is_guest is not None and not client_mac and not ssid:
        r = await get_redis()
        import json as _json
        mac_keys = await r.keys("ruckus:mac:*")
        guest_macs = []
        for key in mac_keys:
            raw = await r.get(key)
            if raw:
                try:
                    info = _json.loads(raw)
                    if info.get("is_guest", False) == is_guest:
                        m = key.replace("ruckus:mac:", "").upper()
                        guest_macs.append(m)
                except Exception:
                    pass
        if guest_macs:
            mac_query = " OR ".join([f'client_mac:"{m}"' for m in guest_macs[:100]])
            parts.append(f"({mac_query})")
        else:
            return {"logs": [], "total": 0, "returned": 0, "query": "*", "error": None}

    lucene_query = " AND ".join(parts) if parts else "*" 

    # Build time range params
    params: dict[str, Any] = {
        "query": lucene_query,
        "limit": min(limit, 500),
        "offset": offset,
        "sort": "timestamp:desc",
    }

    if from_dt or to_dt:
        now = datetime.now(timezone.utc)
        params["from"] = (from_dt or datetime(2020, 1, 1, tzinfo=timezone.utc)).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        params["to"] = (to_dt or now).strftime(
            "%Y-%m-%dT%H:%M:%S.000Z"
        )
        endpoint = "/search/universal/absolute"
    else:
        # No explicit range provided. The frontend's DateRangePicker displays
        # this same null/null state as "All time", so match that label by
        # searching the full history instead of silently restricting to a
        # relative 24h window (previous behavior — caused logs to appear
        # "missing" whenever a platform had no traffic in the last 24h).
        now = datetime.now(timezone.utc)
        params["from"] = "2000-01-01T00:00:00.000Z"
        params["to"]   = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        endpoint = "/search/universal/absolute"

    # Query Graylog
    try:
        raw_result = await _graylog_get(endpoint, params)
    except httpx.HTTPStatusError as e:
        log.error(f"Graylog search error: {e.response.status_code} {e.response.text}")
        return {"logs": [], "total": 0, "returned": 0, "query": lucene_query, "error": str(e)}

    messages = raw_result.get("messages", [])
    total    = raw_result.get("total_results", len(messages))

    # Pre-fetch history snapshots for unique MACs (one query per MAC, not per log)
    import asyncio
    from app.services.history import get_snapshot_at

    unique_macs = set()
    for m in messages:
        msg = m.get("message", m)
        mac = msg.get("client_mac", "")
        if mac:
            unique_macs.add(mac)

    # Fetch history for all unique MACs in parallel (much fewer queries)
    history_cache = {}
    if unique_macs:
        # Use the earliest timestamp from the results as reference
        timestamps = [m.get("message", m).get("timestamp", "") for m in messages if m.get("message", m).get("timestamp")]
        ref_timestamp = min(timestamps) if timestamps else datetime.now(timezone.utc).isoformat()

        async def fetch_hist(mac):
            try:
                hist = await get_snapshot_at(mac, ref_timestamp)
                return mac, hist
            except Exception:
                return mac, None

        hist_results = await asyncio.gather(*[fetch_hist(mac) for mac in unique_macs])
        history_cache = {mac: hist for mac, hist in hist_results}

    # Enrich all logs with concurrency limit to avoid exhausting Redis connections
    semaphore = asyncio.Semaphore(20)

    async def enrich_with_limit(m):
        async with semaphore:
            return await _enrich_log(m.get("message", m), history_cache)

    enriched = await asyncio.gather(*[enrich_with_limit(m) for m in messages])

    # Post-enrichment filters
    results = list(enriched)
    if dst_hostname:
        results = [r for r in results if dst_hostname.lower() in r.get("dst_hostname", "").lower()]
    if username:
        results = [r for r in results if username.lower() in r.get("username", "").lower()]
    if client_label:
        results = [r for r in results if client_label.lower() in r.get("client_label", "").lower()]
    if venue:
        results = [r for r in results if venue.lower() in r.get("venue", "").lower()]
    # SSID post-filter removed — pre-filter by MAC is sufficient and more accurate
    # is_guest post-filter removed — handled via pre-filter above

    return {
        "logs":     results,
        "total":    total,
        "returned": len(results),
        "query":    lucene_query,
    }
