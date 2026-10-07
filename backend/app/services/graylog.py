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


async def _enrich_log(raw: dict, hist: dict | None = None) -> dict:
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
        if platform_source != "smartzone" and hist:
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
            entry["client_label"]  = get_client_label({**hist, "alias": entry["alias"], "mac": mac})

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


# Lucene OR-lists of MACs/WLANs are capped so the query stays under
# OpenSearch's max_clause_count (1024 by default).
MAX_PREFILTER_TERMS = 500

# Enrichment-only filters (username, venue, …) can't be expressed in the
# Graylog query, so matching logs are found by scanning pages of results.
# This bounds the work for a single request.
POST_FILTER_PAGE     = 500
POST_FILTER_MAX_SCAN = 5000


def _q(value: str) -> str:
    """Quote a value as a Lucene phrase, escaping backslashes and double quotes."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_ts(ts: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


async def _scan_keys(r, pattern: str) -> list[str]:
    """SCAN instead of KEYS — KEYS blocks Redis for the whole keyspace walk."""
    return [k async for k in r.scan_iter(match=pattern, count=1000)]


async def _macs_matching(r, predicate) -> list[str]:
    """Upper-case MACs from the live client caches whose info matches."""
    macs = []
    for pattern, prefix in (("ruckus:mac:*", "ruckus:mac:"), ("sz:mac:*", "sz:mac:")):
        for key in await _scan_keys(r, pattern):
            raw = await r.get(key)
            if not raw:
                continue
            try:
                if predicate(json.loads(raw)):
                    m = key.replace(prefix, "").upper()
                    if m not in macs:
                        macs.append(m)
            except Exception:
                pass
    return macs


def _or_clause(field: str, values: list[str], what: str) -> str:
    if len(values) > MAX_PREFILTER_TERMS:
        log.warning(f"{what} pre-filter: {len(values)} values, truncated to {MAX_PREFILTER_TERMS}")
        values = values[:MAX_PREFILTER_TERMS]
    return "(" + " OR ".join(f"{field}:{_q(v)}" for v in values) + ")"


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
    import asyncio
    from app.services.cache import get_active_platform
    from app.services.history import get_snapshots_until

    empty = {"logs": [], "total": 0, "returned": 0, "query": "*", "error": None}

    # Build Lucene query from structured filters
    parts = []
    if query and query != "*":
        if re.match(r'^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$', query.strip()):
            parts.append(f'client_mac:{_q(query.strip().upper())}')
        else:
            parts.append(f"({query})")

    # Filter logs by the currently active platform (R1/Unleashed use GUID zone_name,
    # SmartZone uses a readable zone_name). Always applied — including MAC-specific
    # searches (client detail view) — since only one platform is active at a time
    # in Bloodhound; a client's logs from an inactive platform are deliberately
    # hidden, consistent with the rest of the app.
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
        # SmartZone (readable name).
        parts.append('NOT _exists_:zone_name')
    if src_ip:
        parts.append(f'src_ip:{_q(src_ip)}')
    if dst_ip:
        parts.append(f'dst_ip:{_q(dst_ip)}')
    if client_mac:
        parts.append(f'client_mac:{_q(client_mac.upper())}')
    if proto:
        if not re.fullmatch(r"[A-Za-z0-9]+", proto):
            return {**empty, "error": "Invalid protocol"}
        parts.append(f'proto:{proto.upper()}')
    if dst_port:
        parts.append(f'dst_port:{int(dst_port)}')
    if ap_name:
        parts.append(f'ap_name:{_q(ap_name)}')

    r = await get_redis()

    # Pre-filter by SSID: find wlan_id in Redis, filter by wlan_id in Graylog
    if ssid and not client_mac:
        wlan_ids = []
        for pattern in ["ruckus:wlan:*", "unleashed:*:wlan:*"]:
            for key in await _scan_keys(r, pattern):
                raw = await r.get(key)
                if raw:
                    try:
                        info = json.loads(raw)
                        if ssid.lower() in info.get("ssid", "").lower():
                            wlan_id = key.split(":")[-1]
                            if wlan_id not in wlan_ids:
                                wlan_ids.append(wlan_id)
                    except Exception:
                        pass

        # SmartZone: wlan_id in Graylog IS the SSID name directly
        for key in await _scan_keys(r, "sz:wlan_by_name:*"):
            name = key.replace("sz:wlan_by_name:", "")
            if ssid.lower() in name.lower() and name not in wlan_ids:
                wlan_ids.append(name)

        if wlan_ids:
            parts.append(_or_clause("wlan_id", wlan_ids, "SSID"))
        else:
            # Fallback: MACs whose cached info is on this SSID
            ssid_macs = await _macs_matching(r, lambda i: ssid.lower() in i.get("ssid", "").lower())
            if not ssid_macs:
                return empty
            parts.append(_or_clause("client_mac", ssid_macs, "SSID"))

    # Pre-filter by is_guest: guest MACs from the live caches (all platforms)
    if is_guest is not None and not client_mac and not ssid:
        guest_macs = await _macs_matching(r, lambda i: bool(i.get("is_guest", False)) == is_guest)
        if not guest_macs:
            return empty
        parts.append(_or_clause("client_mac", guest_macs, "Guest"))

    lucene_query = " AND ".join(parts) if parts else "*"

    # Time range — no explicit range means "All time", matching the
    # frontend's DateRangePicker label.
    now = datetime.now(timezone.utc)
    base_params: dict[str, Any] = {
        "query": lucene_query,
        "sort":  "timestamp:desc",
        "from":  (from_dt or datetime(2000, 1, 1, tzinfo=timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "to":    (to_dt or now).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    endpoint = "/search/universal/absolute"

    async def fetch(page_limit: int, page_offset: int) -> tuple[list, int]:
        raw_result = await _graylog_get(endpoint, {**base_params, "limit": page_limit, "offset": page_offset})
        msgs = [m.get("message", m) for m in raw_result.get("messages", [])]
        return msgs, raw_result.get("total_results", len(msgs))

    async def enrich(messages: list) -> list:
        # History: one query per MAC, then the snapshot in effect at each
        # log's own timestamp (not one reference time for the whole page).
        newest_by_mac: dict[str, str] = {}
        for m in messages:
            mac, ts = m.get("client_mac", ""), m.get("timestamp", "")
            if mac and ts and ts > newest_by_mac.get(mac, ""):
                newest_by_mac[mac] = ts

        async def fetch_hist(mac, until):
            try:
                snaps = await get_snapshots_until(mac, until)
            except Exception:
                snaps = []
            parsed = []
            for s in snaps:
                t = _parse_ts(s.get("snapshot_at", ""))
                if t:
                    parsed.append((t, s))
            return mac, parsed  # newest first

        hist_by_mac = dict(await asyncio.gather(*[fetch_hist(m, t) for m, t in newest_by_mac.items()]))

        def snapshot_for(msg) -> dict | None:
            log_ts = _parse_ts(msg.get("timestamp", ""))
            if not log_ts:
                return None
            for snap_ts, snap in hist_by_mac.get(msg.get("client_mac", ""), []):
                if snap_ts <= log_ts:
                    return snap
            return None

        # Concurrency limit to avoid exhausting Redis connections
        semaphore = asyncio.Semaphore(20)

        async def enrich_with_limit(m):
            async with semaphore:
                return await _enrich_log(m, snapshot_for(m))

        return list(await asyncio.gather(*[enrich_with_limit(m) for m in messages]))

    def matches(entry: dict) -> bool:
        if dst_hostname and dst_hostname.lower() not in entry.get("dst_hostname", "").lower():
            return False
        if username and username.lower() not in entry.get("username", "").lower():
            return False
        if client_label and client_label.lower() not in entry.get("client_label", "").lower():
            return False
        if venue and venue.lower() not in entry.get("venue", "").lower():
            return False
        return True

    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    post_filtered = any([dst_hostname, username, client_label, venue])

    try:
        if not post_filtered:
            messages, total = await fetch(limit, offset)
            results = await enrich(messages)
            truncated = False
        else:
            # These fields only exist after enrichment. Filtering a single
            # Graylog page made pagination and `total` wrong (matches beyond
            # the first page were never seen) — scan pages instead, up to a
            # bound, and paginate over the matches.
            found: list = []
            scanned = 0
            exhausted = False
            while scanned < POST_FILTER_MAX_SCAN:
                messages, graylog_total = await fetch(POST_FILTER_PAGE, scanned)
                if not messages:
                    exhausted = True
                    break
                found.extend(e for e in await enrich(messages) if matches(e))
                scanned += len(messages)
                if scanned >= graylog_total:
                    exhausted = True
                    break
                # Enough for this page plus look-ahead for the next one
                if len(found) > offset + limit:
                    break
            results = found[offset:offset + limit]
            total = len(found)
            truncated = not exhausted
    except httpx.HTTPStatusError as e:
        log.error(f"Graylog search error: {e.response.status_code} {e.response.text}")
        return {"logs": [], "total": 0, "returned": 0, "query": lucene_query, "error": str(e)}

    return {
        "logs":      results,
        "total":     total,
        "returned":  len(results),
        "query":     lucene_query,
        # True when more matches may exist beyond what was scanned (post-filters)
        "truncated": truncated,
    }
