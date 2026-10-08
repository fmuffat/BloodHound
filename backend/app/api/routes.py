"""
FastAPI router — search, enrichment and admin endpoints.
"""

import csv
import io
import os
import re
import json
import logging
from datetime import datetime, timezone
from app.config import settings
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, Body, Request
from fastapi.responses import StreamingResponse

from app.models.schemas import (
    SearchResponse, SearchRequest, SyncStatus,
    WorkerResult, InvestigationReport,
)
from app.services import graylog, ruckus
from app.workers.dns_resolver import resolve_batch
from app.services.cache import get_redis

log = logging.getLogger(__name__)
router = APIRouter()

ENV_PATH = "/app/.env"


# ── Search ────────────────────────────────────────────────────────────────────

@router.post("/search", response_model=SearchResponse)
async def search_logs(req: SearchRequest):
    return await graylog.search_logs(
        query=req.query, from_dt=req.from_dt, to_dt=req.to_dt,
        src_ip=req.src_ip, dst_ip=req.dst_ip, dst_hostname=req.dst_hostname,
        client_mac=req.client_mac, client_label=req.client_label,
        username=req.username, ap_name=req.ap_name, venue=req.venue,
        ssid=req.ssid, proto=req.proto, dst_port=req.dst_port,
        is_guest=req.is_guest, limit=req.limit, offset=req.offset,
    )


@router.get("/search", response_model=SearchResponse)
async def search_logs_get(
    q:            str           = Query(default="*"),
    from_dt:      Optional[datetime] = Query(default=None, alias="from"),
    to_dt:        Optional[datetime] = Query(default=None, alias="to"),
    dst_hostname: Optional[str] = Query(default=None),
    client_label: Optional[str] = Query(default=None),
    username:     Optional[str] = Query(default=None),
    client_mac:   Optional[str] = Query(default=None),
    ap_name:      Optional[str] = Query(default=None),
    venue:        Optional[str] = Query(default=None),
    ssid:         Optional[str] = Query(default=None),
    proto:        Optional[str] = Query(default=None),
    dst_port:     Optional[int] = Query(default=None),
    is_guest:     Optional[bool] = Query(default=None),
    limit:        int           = Query(default=100, le=500),
    offset:       int           = Query(default=0),
):
    return await graylog.search_logs(
        query=q, from_dt=from_dt, to_dt=to_dt,
        dst_hostname=dst_hostname, client_label=client_label,
        username=username, client_mac=client_mac,
        ap_name=ap_name, venue=venue, ssid=ssid,
        proto=proto, dst_port=dst_port, is_guest=is_guest,
        limit=limit, offset=offset,
    )


# ── Filter options ────────────────────────────────────────────────────────────

@router.get("/options")
async def get_filter_options():
    """Return APs, venues and SSIDs actually seen in logs (via OpenSearch aggregations)."""
    import httpx
    from app.config import settings

    r = await get_redis()

    # Query OpenSearch for distinct ap_name and wlan_id values seen in logs
    os_query = {
        "size": 0,
        "aggs": {
            "ap_names": {"terms": {"field": "ap_name", "size": 100}},
            "wlan_ids": {"terms": {"field": "wlan_id", "size": 100}},
            "ap_macs":     {"terms": {"field": "ap_mac",     "size": 100}},
            "client_macs": {"terms": {"field": "client_mac", "size": 200}},
            "zone_names":  {"terms": {"field": "zone_name",  "size": 100}},
        }
    }

    aps    = []
    venues = set()
    ssids  = []
    aggs   = {}  # stays empty if OpenSearch is unreachable or errors

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r_os = await client.post(
                f"{settings.opensearch_url}/graylog_*/_search",
                json=os_query,
                headers={"Content-Type": "application/json"},
            )
            if r_os.status_code == 200:
                data = r_os.json()
                aggs = data.get("aggregations", {})

                # APs from logs
                for bucket in aggs.get("ap_names", {}).get("buckets", []):
                    ap_name = bucket["key"].rstrip('"')
                    if ap_name and ap_name not in aps:
                        aps.append(ap_name)
                        # Get venue from Redis
                        venue = await r.get(f"ruckus:ap_venue:{ap_name}")
                        if venue:
                            venues.add(venue)

                # APs from Unleashed (via ap_mac → ap info in Redis)
                for bucket in aggs.get("ap_macs", {}).get("buckets", []):
                    ap_mac = bucket["key"].lower()
                    ul_keys = await r.keys(f"unleashed:*:ap:{ap_mac}")
                    for key in ul_keys:
                        raw = await r.get(key)
                        if raw:
                            info = json.loads(raw)
                            ap_name = info.get("ap_name", "")
                            if ap_name and ap_name not in aps:
                                aps.append(ap_name)
                            venue = info.get("venue", "Unleashed")
                            if venue:
                                venues.add(venue)

                # SmartZone venues: zone_name that is NOT a GUID is a readable zone name
                import re as _re
                for bucket in aggs.get("zone_names", {}).get("buckets", []):
                    zn = bucket["key"]
                    if zn and not _re.fullmatch(r"[0-9a-fA-F]{32}", zn):
                        venues.add(zn)

                # SSIDs via wlan_ids seen in logs → Redis lookup
                for bucket in aggs.get("wlan_ids", {}).get("buckets", []):
                    wlan_id = bucket["key"]
                    # Try Ruckus One wlan
                    raw = await r.get(f"ruckus:wlan:{wlan_id}")
                    if raw:
                        info = json.loads(raw)
                        ssid = info.get("ssid", "")
                        if ssid and ssid not in ssids:
                            ssids.append(ssid)
                    else:
                        # Try Unleashed wlan
                        ul_wlan_keys = await r.keys(f"unleashed:*:wlan:{wlan_id}")
                        found_unleashed = False
                        for key in ul_wlan_keys:
                            raw = await r.get(key)
                            if raw:
                                found_unleashed = True
                                info = json.loads(raw)
                                ssid = info.get("ssid", "")
                                if ssid and ssid not in ssids:
                                    ssids.append(ssid)
                        # Not a GUID, not in Unleashed → SmartZone sends SSID name directly as wlan_id
                        if (not found_unleashed
                                and not _re.fullmatch(r"[0-9a-fA-F]{32}", wlan_id)
                                and wlan_id not in ssids):
                            ssids.append(wlan_id)

    except Exception as e:
        log.warning(f"Options aggregation failed: {e}")

    # Add SSIDs from MACs actually seen in logs (cross-reference with Redis)
    for bucket in aggs.get("client_macs", {}).get("buckets", []):
        mac = bucket["key"].lower()
        raw = await r.get(f"ruckus:mac:{mac}")
        if raw:
            try:
                info = json.loads(raw)
                ssid = info.get("ssid", "")
                if ssid and ssid not in ssids:
                    ssids.append(ssid)
            except Exception:
                pass

    return {
        "aps":    sorted(aps),
        "venues": sorted(list(venues)),
        "ssids":  sorted(ssids),
    }


# ── Investigation export ──────────────────────────────────────────────────────

EXPORT_PAGE     = 500
EXPORT_MAX_ROWS = 10000


def _csv_cell(value) -> str:
    """
    Neutralize spreadsheet formula injection. Hostnames, usernames and guest
    names come from client devices (e.g. a DHCP hostname like "=HYPERLINK(...)")
    and would be executed when the export is opened in Excel/LibreOffice.
    """
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def _writerow(writer, row: list) -> None:
    writer.writerow([_csv_cell(v) for v in row])


async def _export_logs(req: SearchRequest) -> list[dict]:
    """All logs matching the request (every filter), across pages, bounded."""
    logs: list[dict] = []
    offset = 0
    while len(logs) < EXPORT_MAX_ROWS:
        result = await graylog.search_logs(
            query=req.query, from_dt=req.from_dt, to_dt=req.to_dt,
            src_ip=req.src_ip, dst_ip=req.dst_ip, dst_hostname=req.dst_hostname,
            client_mac=req.client_mac, client_label=req.client_label,
            username=req.username, ap_name=req.ap_name, venue=req.venue,
            ssid=req.ssid, proto=req.proto, dst_port=req.dst_port,
            is_guest=req.is_guest, limit=EXPORT_PAGE, offset=offset,
        )
        page = result.get("logs", [])
        logs.extend(page)
        if len(page) < EXPORT_PAGE:
            break
        offset += EXPORT_PAGE
    if len(logs) >= EXPORT_MAX_ROWS:
        log.warning(f"Export truncated to {EXPORT_MAX_ROWS} rows")
    return logs[:EXPORT_MAX_ROWS]


def _export_password(request: Request) -> str:
    """
    ZIP password from a request header — never from the URL, where it would
    end up in nginx access logs and browser history.
    """
    from urllib.parse import unquote
    # URI-encoded by the frontend: header values can't carry non-ASCII text
    password = unquote(request.headers.get("x-export-password", ""))
    if not password:
        raise HTTPException(status_code=400, detail="Export password is required")
    return password

@router.post("/investigation/export")
async def export_investigation(req: SearchRequest):
    logs = await _export_logs(req)

    output = io.StringIO()
    writer = csv.writer(output)
    _writerow(writer, [
        "Timestamp", "AP", "Venue", "SSID",
        "Src IP", "Dst IP", "Dst Hostname", "Src Port", "Dst Port", "Protocol",
        "Client MAC", "Client Label", "Alias", "Hostname", "Username",
        "OS Type", "Device Type", "Is Guest",
        "Guest Name", "Email", "Phone",
    ])
    for log_entry in logs:
        _writerow(writer, [
            log_entry.get("timestamp", ""), log_entry.get("ap_name", ""),
            log_entry.get("venue", ""),     log_entry.get("ssid", ""),
            log_entry.get("src_ip", ""),    log_entry.get("dst_ip", ""),
            log_entry.get("dst_hostname", ""), log_entry.get("src_port", ""),
            log_entry.get("dst_port", ""),  log_entry.get("proto", ""),
            log_entry.get("client_mac", ""), log_entry.get("client_label", ""),
            log_entry.get("alias", ""),      log_entry.get("hostname", ""),
            log_entry.get("username", ""),   log_entry.get("os_type", ""),
            log_entry.get("device_type", ""), log_entry.get("is_guest", False),
            log_entry.get("guest_name", ""), log_entry.get("email", ""),
            log_entry.get("phone", ""),
        ])

    output.seek(0)
    filename = f"investigation_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ── Lookups ───────────────────────────────────────────────────────────────────

@router.get("/lookup/mac/{mac}")
async def lookup_mac(mac: str, platform: str = Query(default=None), timestamp: str = Query(default=None)):
    from app.services.cache import get_client_info, get_active_platform
    from app.services.history import get_snapshot_at, get_latest_snapshot
    # Use the currently active platform to decide which cache to check first,
    # rather than relying solely on the (often empty/stale) client-supplied
    # platform query param. This matches Bloodhound's single-active-platform
    # design and fixes lookups for clients that only ever existed on the
    # currently active platform (e.g. SmartZone-only guests with no R1/
    # Unleashed history at all).
    active = platform or await get_active_platform()
    info = None
    if active == "smartzone":
        r = await get_redis()
        raw = await r.get(f"sz:mac:{mac.upper()}")
        if raw:
            info = json.loads(raw)
    if not info:
        info = await get_client_info(mac)
    if not info:
        info = await ruckus.lookup_mac(mac)

    # Live cache miss (client currently offline/disconnected on every
    # platform) — fall back to the historical snapshot index so investigating
    # an old log entry still shows who/what the device was, instead of a
    # dead end. Prefer the snapshot closest to (and before) the log's own
    # timestamp for point-in-time accuracy; fall back to the latest known
    # snapshot if no timestamp was supplied or none exists before it.
    if not info:
        if timestamp:
            info = await get_snapshot_at(mac, timestamp)
        if not info:
            info = await get_latest_snapshot(mac)

    if not info:
        raise HTTPException(status_code=404, detail=f"MAC {mac} not found in cache or history")
    return info


@router.get("/lookup/ip/{ip}")
async def lookup_ip(ip: str):
    from app.services.cache import get_hostname, queue_for_dns_resolution
    hostname = await get_hostname(ip)
    if hostname:
        return {"ip": ip, "hostname": hostname, "cached": True}
    await queue_for_dns_resolution(ip)
    return {"ip": ip, "hostname": ip, "cached": False, "queued": True}


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status", response_model=SyncStatus)
async def get_status():
    r = await get_redis()
    mac_keys    = await r.keys("ruckus:mac:*")
    dns_keys    = await r.keys("dns:*")
    dns_pending = await r.scard("dns:pending")
    last_sync   = await r.get("ruckus:last_sync")
    return SyncStatus(
        ruckus_last_sync=last_sync,
        ruckus_clients_cached=len(mac_keys),
        dns_pending=int(dns_pending or 0),
        dns_cached=len([k for k in dns_keys if k != "dns:pending"]),
    )


# ── Workers ───────────────────────────────────────────────────────────────────

@router.post("/workers/sync-ruckus", response_model=WorkerResult)
async def trigger_ruckus_sync():
    try:
        result = await ruckus.full_sync()
        return WorkerResult(status="ok", detail=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workers/resolve-dns", response_model=WorkerResult)
async def trigger_dns_resolve():
    try:
        result = await resolve_batch()
        return WorkerResult(status="ok", detail=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/workers/sync-unleashed", response_model=WorkerResult)
async def trigger_unleashed_sync():
    try:
        from app.services import unleashed as ul_service
        result = await ul_service.sync()
        return WorkerResult(status="ok", detail=result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Settings helpers ──────────────────────────────────────────────────────────

def _read_env() -> dict:
    env_vars = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env_vars[k.strip()] = v.strip()
    return env_vars


def _write_env(env_vars: dict) -> None:
    with open(ENV_PATH, "w") as f:
        for k, v in env_vars.items():
            # A newline in a UI-supplied value would inject extra variables
            v = str(v).replace("\r", "").replace("\n", "")
            f.write(f"{k}={v}\n")


# ── Ruckus One Settings ───────────────────────────────────────────────────────

@router.get("/settings")
async def get_settings():
    from app.services.cache import is_platform_enabled
    r1_enabled = await is_platform_enabled("ruckus_one")

    # Live source of truth is Redis (ruckus_one:config) — same reasoning as
    # Unleashed/SmartZone: the backend process only reads env vars once at
    # startup, so it never sees a credential change saved via the UI
    # without a manual restart. Reading fresh from Redis fixes that.
    r = await get_redis()
    raw = await r.get("ruckus_one:config")
    if raw:
        cfg = json.loads(raw)
        ruckus_one = {
            "region":        cfg.get("region", "EU"),
            "tenant_id":     cfg.get("tenant_id", ""),
            "client_id":     cfg.get("client_id", ""),
            "client_secret": "***" if cfg.get("client_secret") else "",
            "enabled":       r1_enabled,
        }
    else:
        ruckus_one = {
            "region":        os.getenv("RUCKUS_ONE_REGION", "EU"),
            "tenant_id":     os.getenv("RUCKUS_ONE_TENANT_ID", ""),
            "client_id":     os.getenv("RUCKUS_ONE_CLIENT_ID", ""),
            "client_secret": "***" if os.getenv("RUCKUS_ONE_CLIENT_SECRET") else "",
            "enabled":       r1_enabled,
        }

    return {
        "ruckus_one": ruckus_one,
        "unleashed": {"enabled": False},
        "smartzone": {"enabled": False},
    }


@router.post("/settings/ruckus-one/test")
async def test_ruckus_settings():
    """Actually attempt a fresh R1 OAuth token request, not just a cache read."""
    try:
        result = await ruckus.test_connection()
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/settings/ruckus-one")
async def save_ruckus_settings(payload: dict = Body(...)):
    region        = payload.get("region", "EU")
    tenant_id     = payload.get("tenant_id", "")
    client_id     = payload.get("client_id", "")
    client_secret = payload.get("client_secret", "")
    enabled       = payload.get("enabled", False)

    r = await get_redis()
    # Preserve the previously-saved secret if the form sent back the masked
    # placeholder rather than a real new value.
    if not client_secret or client_secret == "***":
        existing_raw = await r.get("ruckus_one:config")
        existing = json.loads(existing_raw) if existing_raw else {}
        client_secret = existing.get("client_secret", "")

    # Redis is the live source of truth — ruckus.py reads this fresh on
    # every API call, so a saved change takes effect immediately, no
    # backend restart needed.
    await r.set("ruckus_one:config", json.dumps({
        "region": region, "tenant_id": tenant_id,
        "client_id": client_id, "client_secret": client_secret,
    }))

    env_vars = _read_env()
    env_vars["RUCKUS_ONE_REGION"]    = region
    env_vars["RUCKUS_ONE_TENANT_ID"] = tenant_id
    env_vars["RUCKUS_ONE_CLIENT_ID"] = client_id
    if client_secret:
        env_vars["RUCKUS_ONE_CLIENT_SECRET"] = client_secret
    _write_env(env_vars)

    if enabled:
        from app.services.cache import set_active_platform
        await set_active_platform("ruckus_one")
    else:
        r = await get_redis()
        await r.set("platform:ruckus_one:enabled", "false")

    if enabled:
        try:
            await ruckus.full_sync()
        except Exception as e:
            log.warning(f"Re-sync after settings change: {e}")

    return {"status": "saved"}


# ── Unleashed Settings ────────────────────────────────────────────────────────

@router.get("/settings/unleashed")
async def get_unleashed_settings():
    # Live source of truth is Redis (unleashed:config), kept in sync with
    # what the worker actually uses — not the static .env file, which the
    # always-running systemd worker never re-reads after its own startup.
    r = await get_redis()
    raw = await r.get("unleashed:config")
    if raw:
        cfg = json.loads(raw)
        return {
            "ip":       cfg.get("ip", ""),
            "username": cfg.get("username", "admin"),
            "password": "***" if cfg.get("password") else "",
            "enabled":  cfg.get("enabled", False),
        }
    # Fallback for first boot, before any save has ever happened
    return {
        "ip":       os.getenv("UNLEASHED_IP", ""),
        "username": os.getenv("UNLEASHED_USERNAME", "admin"),
        "password": "***" if os.getenv("UNLEASHED_PASSWORD") else "",
        "enabled":  False,
    }


@router.post("/settings/unleashed")
async def save_unleashed_settings(payload: dict = Body(...)):
    ip       = payload.get("ip", "")
    username = payload.get("username", "admin")
    password = payload.get("password", "")
    enabled  = payload.get("enabled", False)

    r = await get_redis()

    # Preserve the previously-saved password if the form sent back the
    # masked placeholder rather than a real new value.
    existing_raw = await r.get("unleashed:config")
    existing = json.loads(existing_raw) if existing_raw else {}
    if not password or password == "***":
        password = existing.get("password", "")

    # Redis is the live source of truth the worker reads fresh every sync
    # cycle — this is what actually takes effect immediately, no restart
    # needed.
    await r.set("unleashed:config", json.dumps({
        "ip": ip, "username": username, "password": password, "enabled": enabled,
    }))

    await r.set("unleashed:enabled", "true" if enabled else "false")
    if enabled:
        from app.services.cache import set_active_platform
        await set_active_platform("unleashed")
    else:
        await r.set("platform:unleashed:enabled", "false")

    # Also mirror into .env for historical/manual-inspection purposes only
    # — not read live by anything anymore.
    env_vars = _read_env()
    env_vars["UNLEASHED_IP"]       = ip
    env_vars["UNLEASHED_USERNAME"] = username
    env_vars["UNLEASHED_ENABLED"]  = str(enabled).lower()
    if password:
        env_vars["UNLEASHED_PASSWORD"] = password
    _write_env(env_vars)

    return {"status": "saved"}


@router.post("/settings/unleashed/test")
async def test_unleashed_connection(payload: dict = Body(...)):
    """Read last sync status from Redis instead of launching Selenium."""
    r = await get_redis()
    status_raw = await r.get("unleashed:status")
    if status_raw:
        status = json.loads(status_raw)
        return status
    return {"ok": False, "error": "No sync performed yet - worker may not be running"}


# ── Retention Settings ────────────────────────────────────────────────────────

@router.get("/settings/retention")
async def get_retention():
    """Get current log retention settings."""
    r = await get_redis()
    days = await r.get("retention:days")
    last_purge = await r.get("retention:last_purge")
    return {
        "days": int(days) if days else 30,
        "last_purge": last_purge or None,
    }


@router.post("/settings/retention")
async def save_retention(payload: dict = Body(...)):
    """Save retention settings and trigger purge."""
    days = int(payload.get("days", 30))
    days = max(1, min(180, days))  # Clamp between 1 and 180

    r = await get_redis()
    await r.set("retention:days", str(days))

    # Trigger purge immediately
    result = await _purge_old_logs(days)
    return {"status": "saved", "days": days, "purge": result}


async def _purge_old_logs(days: int) -> dict:
    """
    Delete logs AND client history snapshots older than N days.

    Both stores hold personal data (MACs, hostnames, guest names, emails,
    phone numbers), so the retention period applies to both.
    """
    import httpx
    from datetime import timedelta
    from app.services.opensearch import graylog_indices, delete_by_query, HISTORY_INDEX

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )
    # "format" tells OpenSearch how to parse *this* value, whatever date
    # format the field itself is mapped with (Graylog maps `timestamp` with
    # its own non-ISO format, which made an ISO cutoff fail to parse).
    log_query = {"query": {"range": {"timestamp": {
        "lt": cutoff, "format": "strict_date_optional_time",
    }}}}
    hist_query = {"query": {"range": {"snapshot_at": {
        "lt": cutoff, "format": "strict_date_optional_time",
    }}}}

    deleted_logs = 0
    deleted_history = 0
    errors = []

    # delete_by_query on a large index can take minutes
    async with httpx.AsyncClient(timeout=600) as client:
        try:
            indices = await graylog_indices(client)
        except Exception as e:
            indices = []
            errors.append(f"index listing: {e}")
        for index in indices:
            try:
                deleted = await delete_by_query(client, index, log_query)
                deleted_logs += deleted
                if deleted:
                    log.info(f"Purged {deleted} logs from {index} older than {days} days")
            except Exception as e:
                errors.append(f"{index}: {e}")
                log.warning(f"Purge failed for {index}: {e}")

        try:
            deleted_history = await delete_by_query(client, HISTORY_INDEX, hist_query)
            if deleted_history:
                log.info(f"Purged {deleted_history} client history snapshots older than {days} days")
        except Exception as e:
            errors.append(f"{HISTORY_INDEX}: {e}")
            log.warning(f"History purge failed: {e}")

    r = await get_redis()
    await r.set("retention:last_purge", datetime.now(timezone.utc).isoformat())

    return {
        "deleted": deleted_logs,
        "deleted_history": deleted_history,
        "cutoff": cutoff,
        "errors": errors,
    }


@router.post("/workers/purge-logs")
async def trigger_purge():
    """Manually trigger log purge based on current retention settings."""
    r = await get_redis()
    days_raw = await r.get("retention:days")
    days = int(days_raw) if days_raw else 30
    result = await _purge_old_logs(days)
    return WorkerResult(status="ok", detail=result)


@router.get("/settings/disk")
async def get_disk_usage():
    """Get OpenSearch disk usage."""
    import httpx
    from app.config import settings as app_settings
    import shutil

    disk = shutil.disk_usage("/")

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{app_settings.opensearch_url}/_cat/indices?h=index,store.size,docs.count&format=json")
        indices = r.json() if r.status_code == 200 else []

    graylog_indices = [i for i in indices if i.get("index", "").startswith("graylog")]
    total_docs = sum(int(i.get("docs.count", 0)) for i in graylog_indices)

    return {
        "disk_total_gb":  round(disk.total / 1e9, 1),
        "disk_used_gb":   round(disk.used  / 1e9, 1),
        "disk_free_gb":   round(disk.free  / 1e9, 1),
        "disk_used_pct":  round(disk.used / disk.total * 100, 1),
        "log_count":      total_docs,
        "indices":        graylog_indices,
    }


# ── User Preferences ──────────────────────────────────────────────────────────

@router.get("/settings/preferences")
async def get_preferences():
    r = await get_redis()
    tz     = await r.get("pref:timezone") or "Europe/Paris"
    clock  = await r.get("pref:clock")    or "24h"
    return {"timezone": tz, "clock": clock}


@router.post("/settings/preferences")
async def save_preferences(payload: dict = Body(...)):
    r = await get_redis()
    await r.set("pref:timezone", payload.get("timezone", "Europe/Paris"))
    await r.set("pref:clock",    payload.get("clock",    "24h"))
    return {"status": "saved"}


# ── MAC Timeline ──────────────────────────────────────────────────────────────

@router.get("/lookup/mac/{mac}/timeline")
async def get_mac_timeline(mac: str):
    """Get first seen / last seen dates for a MAC address from OpenSearch."""
    import httpx
    from app.config import settings as app_settings

    query = {
        "size": 0,
        "query": {"term": {"client_mac": mac.upper()}},
        "aggs": {
            "first_seen": {"min": {"field": "timestamp"}},
            "last_seen":  {"max": {"field": "timestamp"}},
            "total":      {"value_count": {"field": "timestamp"}},
        }
    }

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{app_settings.opensearch_url}/graylog_*/_search",
            json=query,
            headers={"Content-Type": "application/json"},
        )
        if r.status_code != 200:
            raise HTTPException(status_code=500, detail="OpenSearch error")

        data = r.json()
        aggs = data.get("aggregations", {})
        return {
            "mac":        mac,
            "first_seen": aggs.get("first_seen", {}).get("value_as_string"),
            "last_seen":  aggs.get("last_seen",  {}).get("value_as_string"),
            "total_logs": aggs.get("total",      {}).get("value", 0),
        }


# ── Authentication ────────────────────────────────────────────────────────────

from fastapi import Request, Cookie
from fastapi.responses import JSONResponse


def _client_ip(request: Request) -> str:
    # The backend is only reachable through nginx (port bound to 127.0.0.1),
    # so X-Real-IP set by nginx is the real client address.
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


def _set_session_cookie(resp: JSONResponse, user: dict) -> None:
    from app.services.auth import create_token
    resp.set_cookie(
        key="bh_token",
        value=create_token(user),
        httponly=True,
        secure=True,
        max_age=86400,
        samesite="strict",
    )


def _me(request: Request) -> dict:
    """The signed-in account (set by AuthMiddleware)."""
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _require_admin(request: Request) -> dict:
    user = _me(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Administrator only")
    return user


@router.post("/auth/login")
async def login(request: Request, payload: dict = Body(...)):
    from app.services.auth import (
        verify_credentials, is_login_blocked, record_failed_login, clear_failed_logins,
        record_login, public,
    )
    username = payload.get("username", "")
    password = payload.get("password", "")
    ip = _client_ip(request)

    if await is_login_blocked(ip):
        raise HTTPException(status_code=429, detail="Too many failed attempts, try again later")

    user = await verify_credentials(username, password)
    if not user:
        await record_failed_login(ip)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    await clear_failed_logins(ip)
    await record_login(user["username"])
    resp = JSONResponse({"status": "ok", **public(user)})
    _set_session_cookie(resp, user)
    return resp


@router.post("/auth/logout")
async def logout():
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie("bh_token")
    return resp


@router.get("/auth/me")
async def get_me(request: Request):
    from app.services.auth import public
    return public(_me(request))


@router.post("/auth/change-password")
async def change_password(request: Request, payload: dict = Body(...)):
    """Own password (any role). Closes this account's other sessions."""
    from app.services.auth import verify_credentials, set_password
    me = _me(request)
    current_password = payload.get("current_password", "")
    new_password     = payload.get("new_password", "")

    if not await verify_credentials(me["username"], current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    if new_password == current_password:
        raise HTTPException(status_code=400, detail="The new password must differ from the current one")
    try:
        user = await set_password(me["username"], new_password, must_change=False)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # The token version changed: give this browser a fresh session
    resp = JSONResponse({"status": "ok"})
    _set_session_cookie(resp, user)
    return resp


# ── Accounts (administrators only) ────────────────────────────────────────────

def _random_password() -> str:
    import secrets
    return secrets.token_urlsafe(12)


@router.get("/users")
async def users_list(request: Request):
    from app.services.auth import list_users, public, ROLES
    _require_admin(request)
    return {"items": [public(u) for u in await list_users()], "roles": list(ROLES)}


@router.post("/users")
async def users_create(request: Request, payload: dict = Body(...)):
    """New account with a random password, to change at first sign-in."""
    from app.services.auth import create_user, public
    _require_admin(request)
    password = _random_password()
    try:
        user = await create_user((payload.get("username") or "").strip(), payload.get("role") or "viewer",
                                 password, must_change=True)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="This account already exists")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {**public(user), "password": password}


async def _target(username: str) -> dict:
    from app.services.auth import get_user
    user = await get_user(username)
    if not user:
        raise HTTPException(status_code=404, detail="No such account")
    return user


@router.put("/users/{username}/role")
async def users_set_role(username: str, request: Request, payload: dict = Body(...)):
    from app.services.auth import set_role, count_admins, public
    _require_admin(request)
    user = await _target(username)
    role = payload.get("role", "")
    if user["role"] == "admin" and role != "admin" and await count_admins() <= 1:
        raise HTTPException(status_code=409, detail="At least one administrator is required")
    try:
        return public(await set_role(username, role))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/users/{username}/reset-password")
async def users_reset_password(username: str, request: Request):
    """New random password, to change at next sign-in (closes the account's sessions)."""
    from app.services.auth import set_password
    me = _require_admin(request)
    if username == me["username"]:
        raise HTTPException(status_code=409, detail="Use Password to change your own password")
    await _target(username)
    password = _random_password()
    await set_password(username, password, must_change=True)
    return {"username": username, "password": password}


@router.delete("/users/{username}")
async def users_delete(username: str, request: Request):
    from app.services.auth import delete_user, count_admins
    me = _require_admin(request)
    if username == me["username"]:
        raise HTTPException(status_code=409, detail="You cannot delete your own account")
    user = await _target(username)
    if user["role"] == "admin" and await count_admins() <= 1:
        raise HTTPException(status_code=409, detail="At least one administrator is required")
    await delete_user(username)
    return {"deleted": username}


ERASE_LOG_KEY = "clients:erase_log"


async def _migrate_erase_log(r) -> None:
    """Keep the entries recorded under the audit key used by earlier versions."""
    if await r.exists("gdpr:audit_log") and not await r.exists(ERASE_LOG_KEY):
        await r.rename("gdpr:audit_log", ERASE_LOG_KEY)


@router.post("/clients/erase")
async def erase_client(
    request: Request,
    payload: dict = Body(...),
):
    """
    Permanently erase all logs, history, and cached data for a single MAC
    address (right-to-erasure requests). Requires re-entering the
    current password — this is a destructive, irreversible action, so it
    is deliberately gated the same way a password change is, not just a
    UI confirmation dialog that could be clicked through accidentally.
    """
    import httpx
    from app.services.auth import verify_credentials

    me = _require_admin(request)
    mac = (payload.get("mac") or "").strip()
    password = payload.get("password", "")
    if not mac:
        raise HTTPException(status_code=400, detail="mac is required")

    current_username = me["username"]
    if not await verify_credentials(current_username, password):
        raise HTTPException(status_code=401, detail="Password is incorrect")

    mac_upper = mac.upper()
    mac_lower = mac.lower()
    deleted_logs = 0
    deleted_history = 0

    from app.services.opensearch import graylog_indices, delete_by_query, HISTORY_INDEX

    def _mac_query(field: str) -> dict:
        return {"query": {"bool": {
            "should": [
                {"term": {field: mac_upper}},
                {"term": {field: mac_lower}},
            ],
            "minimum_should_match": 1,
        }}}

    async with httpx.AsyncClient(timeout=600) as client:
        # All graylog_* indices, rotated (write-locked) ones included
        for idx in await graylog_indices(client):
            deleted_logs += await delete_by_query(client, idx, _mac_query("client_mac"))

        # Client history snapshots
        deleted_history = await delete_by_query(client, HISTORY_INDEX, _mac_query("mac"))

    # Note: the live cache (ruckus:mac:*/sz:mac:*) is deliberately NOT
    # cleared here. It's the device's current operational state as
    # reported by the controller/AP — not a historical record — and gets
    # overwritten on every sync cycle regardless (every 5-10 min) as long
    # as the device stays connected. Clearing it just creates a temporary
    # gap where the client card shows nothing until the next sync, with no
    # real erasure benefit. The actual personal data retained over time —
    # logs and history snapshots — is what's deleted above.
    r = await get_redis()

    # Audit trail — demonstrates that erasure requests were honoured. Records *that*
    # an erasure happened, who performed it, and when — not the erased
    # person's own data, so this doesn't reintroduce what was just erased.
    audit_entry = {
        "mac":         mac_upper,
        "performed_by": current_username,
        "at":          datetime.now(timezone.utc).isoformat(),
        "deleted_logs":    deleted_logs,
        "deleted_history": deleted_history,
    }
    await _migrate_erase_log(r)
    await r.rpush(ERASE_LOG_KEY, json.dumps(audit_entry))
    log.warning(f"Client erasure performed: {audit_entry}")

    return {
        "status": "ok",
        "mac": mac_upper,
        "deleted_logs": deleted_logs,
        "deleted_history": deleted_history,
    }


@router.get("/clients/erase-log")
async def erase_log(request: Request):
    """Return the client erasure audit trail (who erased what, and when)."""
    _require_admin(request)

    r = await get_redis()
    await _migrate_erase_log(r)
    entries = await r.lrange(ERASE_LOG_KEY, 0, -1)
    return {"entries": [json.loads(e) for e in entries]}


# ── ZIP Export ────────────────────────────────────────────────────────────────

@router.post("/investigation/export-zip")
async def export_investigation_zip(
    req: SearchRequest,
    request: Request,
    bh_token: str = Cookie(default=None),
):
    """Export investigation as password-protected ZIP with CSV inside."""
    import pyzipper
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    password = _export_password(request)

    # Same filters as the on-screen search and the CSV export
    logs = await _export_logs(req)

    # Build CSV
    csv_buf = io.StringIO()
    writer  = csv.writer(csv_buf)
    _writerow(writer, [
        "Timestamp", "Client MAC", "Client Name", "Guest Type",
        "Sponsor Email", "Src IP", "Dst IP", "Dst Hostname",
        "Src Port", "Dst Port", "Protocol", "SSID", "AP", "Venue",
    ])
    for entry in logs:
        _writerow(writer, [
            entry.get("timestamp", ""),
            entry.get("client_mac", ""),
            entry.get("client_label", ""),
            entry.get("guest_type", ""),
            entry.get("sponsor_email", ""),
            entry.get("src_ip", ""),
            entry.get("dst_ip", ""),
            entry.get("dst_hostname", ""),
            entry.get("src_port", ""),
            entry.get("dst_port", ""),
            entry.get("proto", ""),
            entry.get("ssid", ""),
            entry.get("ap_name", ""),
            entry.get("venue", ""),
        ])

    csv_bytes = csv_buf.getvalue().encode("utf-8-sig")

    # Build encrypted ZIP in memory
    mac_clean = re.sub(r"[^0-9A-Za-z]", "", req.client_mac or "") or "investigation"
    ts        = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    csv_name  = f"bloodhound_{mac_clean}_{ts}.csv"
    zip_name  = f"bloodhound_{mac_clean}_{ts}.zip"

    zip_buf = io.BytesIO()
    with pyzipper.AESZipFile(zip_buf, 'w',
                              compression=pyzipper.ZIP_DEFLATED,
                              encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(password.encode())
        zf.writestr(csv_name, csv_bytes)

    zip_buf.seek(0)
    return StreamingResponse(
        iter([zip_buf.read()]),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={zip_name}"},
    )


@router.post("/investigation/export-zip-event")
async def export_event_zip(
    request: Request,
    payload: dict = Body(...),
    bh_token: str = Cookie(default=None),
):
    """Export a single event as password-protected ZIP."""
    import pyzipper
    from app.services.auth import verify_token
    from app.services.cache import get_client_info
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    password = _export_password(request)

    mac        = payload.get("mac", "")
    event_data = payload.get("event", {})

    # Get client info
    client = await get_client_info(mac) or {}

    # Build CSV
    csv_buf = io.StringIO()
    writer  = csv.writer(csv_buf)
    _writerow(writer, ['Field', 'Value'])
    _writerow(writer, ['--- CLIENT INFO ---', ''])
    _writerow(writer, ['MAC Address', mac])
    _writerow(writer, ['Name', client.get('alias') or client.get('guest_name') or client.get('hostname', '')])
    _writerow(writer, ['Hostname', client.get('hostname', '')])
    _writerow(writer, ['Username', client.get('username', '')])
    _writerow(writer, ['OS', client.get('os_type', '')])
    _writerow(writer, ['Device', client.get('device_type', '')])
    _writerow(writer, ['Venue', client.get('venue', '')])
    _writerow(writer, ['SSID', client.get('ssid', '')])

    if client.get('guest_type') == 'HostGuest':
        _writerow(writer, ['--- SPONSORED ACCESS ---', ''])
        _writerow(writer, ['Guest Name', client.get('guest_name', '')])
        _writerow(writer, ['Phone', client.get('phone', '')])
        _writerow(writer, ['Sponsor Email', client.get('sponsor_email', '')])
        _writerow(writer, ['Duration', f"{client.get('pass_duration_hours', '')}h"])
        _writerow(writer, ['Created', client.get('creation_date', '')])
        _writerow(writer, ['Expires', client.get('expiry_date', '')])
    elif client.get('is_guest'):
        _writerow(writer, ['--- GUEST INFO ---', ''])
        _writerow(writer, ['Guest Name', client.get('guest_name', '')])
        _writerow(writer, ['Email', client.get('email', '')])
        _writerow(writer, ['Phone', client.get('phone', '')])

    _writerow(writer, ['--- EVENT ---', ''])
    _writerow(writer, ['Timestamp', event_data.get('timestamp', '')])
    _writerow(writer, ['Source IP', event_data.get('src_ip', '')])
    _writerow(writer, ['Destination IP', event_data.get('dst_ip', '')])
    _writerow(writer, ['Destination Host', event_data.get('dst_host', '')])
    _writerow(writer, ['Port', event_data.get('dst_port', '')])
    _writerow(writer, ['Protocol', event_data.get('proto', '')])
    _writerow(writer, ['SSID', event_data.get('ssid', '')])
    _writerow(writer, ['AP', event_data.get('ap_name', '')])
    _writerow(writer, ['Venue', event_data.get('venue', '')])

    csv_bytes = csv_buf.getvalue().encode('utf-8-sig')
    mac_clean = re.sub(r"[^0-9A-Za-z]", "", mac) or "event"
    ts        = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    csv_name  = f"event_{mac_clean}_{ts}.csv"
    zip_name  = f"event_{mac_clean}_{ts}.zip"

    zip_buf = io.BytesIO()
    with pyzipper.AESZipFile(zip_buf, 'w',
                              compression=pyzipper.ZIP_DEFLATED,
                              encryption=pyzipper.WZ_AES) as zf:
        zf.setpassword(password.encode())
        zf.writestr(csv_name, csv_bytes)

    zip_buf.seek(0)
    return StreamingResponse(
        iter([zip_buf.read()]),
        media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename={zip_name}'},
    )


# ── SSL Certificate Upload ────────────────────────────────────────────────────

from fastapi import UploadFile, File

SSL_DIR       = os.getenv("BLOODHOUND_SSL_DIR", "/opt/bloodhound/ssl")
SSL_CERT_PATH = os.path.join(SSL_DIR, "bloodhound.crt")
SSL_KEY_PATH  = os.path.join(SSL_DIR, "bloodhound.key")
_HOSTNAME_RE  = r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"


def _install_cert_pair(tmp_cert: str, tmp_key: str) -> None:
    """
    Check that the certificate and key form a valid pair, then atomically
    move them into place. A mismatched pair must never reach nginx: it would
    refuse to start on the next reboot and the UI would become unreachable.

    The backend runs in Docker and cannot reload the host's nginx itself —
    bloodhound-nginx-reload.path (host) watches the certificate file and
    reloads nginx after a successful `nginx -t`. The key is moved first so
    the reload only fires once both files are in place.
    """
    import ssl
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=tmp_cert, keyfile=tmp_key)
    except (ssl.SSLError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"Certificate and key do not match or are invalid: {e}")
    os.chmod(tmp_key, 0o600)
    os.replace(tmp_key, SSL_KEY_PATH)
    os.replace(tmp_cert, SSL_CERT_PATH)


@router.post("/settings/ssl")
async def upload_ssl_certificate(
    cert: UploadFile = File(...),
    key:  UploadFile = File(...),
    bh_token: str = Cookie(default=None),
):
    """Upload custom SSL certificate and key (nginx reloads on the host)."""
    from app.services.auth import verify_token
    import tempfile
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    cert_content = await cert.read()
    key_content  = await key.read()

    # Validate cert/key are PEM format
    if not cert_content.lstrip().startswith(b"-----BEGIN CERTIFICATE"):
        raise HTTPException(status_code=400, detail="Invalid certificate format (must be PEM)")
    if not key_content.lstrip().startswith(b"-----BEGIN"):
        raise HTTPException(status_code=400, detail="Invalid key format (must be PEM)")

    os.makedirs(SSL_DIR, exist_ok=True)
    # Temp files in the same directory so os.replace() is atomic
    fd_c, tmp_cert = tempfile.mkstemp(dir=SSL_DIR, suffix=".crt.tmp")
    fd_k, tmp_key  = tempfile.mkstemp(dir=SSL_DIR, suffix=".key.tmp")
    try:
        with os.fdopen(fd_c, "wb") as f:
            f.write(cert_content)
        with os.fdopen(fd_k, "wb") as f:
            f.write(key_content)
        _install_cert_pair(tmp_cert, tmp_key)
    finally:
        for p in (tmp_cert, tmp_key):
            if os.path.exists(p):
                os.remove(p)

    return {"status": "ok", "message": "Certificate installed — nginx reloads automatically within a few seconds"}


@router.post("/settings/ssl/self-signed")
async def generate_self_signed(
    request: Request,
    payload: dict = Body(default={}),
    bh_token: str = Cookie(default=None),
):
    """Regenerate a self-signed certificate."""
    import ipaddress
    import re
    import subprocess
    import tempfile
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    cn = (payload.get("cn") or "bloodhound.local").strip()
    if not re.fullmatch(_HOSTNAME_RE, cn):
        raise HTTPException(status_code=400, detail="Invalid common name (must be a hostname)")
    try:
        days = int(payload.get("days", 3650))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid validity period")
    days = max(1, min(3650, days))

    # IP for the SAN: the one requested, else the address the admin used to
    # reach this page (Host header forwarded by nginx). The backend runs in
    # Docker, so detecting "our own" IP from here would return the
    # container's internal address, not the appliance's.
    requested_ip = (payload.get("ip") or "").strip()
    if not requested_ip:
        host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]")
        requested_ip = host
    try:
        ip = str(ipaddress.ip_address(requested_ip))
    except ValueError:
        if payload.get("ip"):
            raise HTTPException(status_code=400, detail="Invalid IP address")
        ip = None

    san = f"DNS:{cn}" + (f",IP:{ip}" if ip else "")

    os.makedirs(SSL_DIR, exist_ok=True)
    fd_c, tmp_cert = tempfile.mkstemp(dir=SSL_DIR, suffix=".crt.tmp")
    fd_k, tmp_key  = tempfile.mkstemp(dir=SSL_DIR, suffix=".key.tmp")
    os.close(fd_c)
    os.close(fd_k)
    try:
        cmd = [
            "openssl", "req", "-x509", "-nodes",
            "-days", str(days),
            "-newkey", "rsa:2048",
            "-keyout", tmp_key,
            "-out",    tmp_cert,
            "-subj",   f"/O=Bloodhound/CN={cn}",
            "-addext", f"subjectAltName={san}",
        ]
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=result.stderr.decode())
        _install_cert_pair(tmp_cert, tmp_key)
    finally:
        for p in (tmp_cert, tmp_key):
            if os.path.exists(p):
                os.remove(p)

    return {"status": "ok", "message": f"Self-signed certificate generated for {cn}" + (f" / {ip}" if ip else "")}


# ── SmartZone Settings ────────────────────────────────────────────────────────

@router.get("/settings/smartzone")
async def get_smartzone_settings(bh_token: str = Cookie(default=None)):
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    r = await get_redis()
    raw = await r.get("sz:config")
    if raw:
        cfg = json.loads(raw)
        # Never send the stored password back to the browser
        cfg["password"] = "***" if cfg.get("password") else ""
        return cfg
    return {"host": settings.smartzone_host, "port": settings.smartzone_port,
            "username": settings.smartzone_username,
            "password": "***" if settings.smartzone_password else "",
            "enabled": settings.smartzone_enabled}


@router.post("/settings/smartzone")
async def save_smartzone_settings(
    payload: dict = Body(...),
    bh_token: str = Cookie(default=None),
):
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    r = await get_redis()
    # Preserve the previously-saved password if the form sent back the
    # masked placeholder rather than a real new value.
    if not payload.get("password") or payload.get("password") == "***":
        existing_raw = await r.get("sz:config")
        existing = json.loads(existing_raw) if existing_raw else {}
        payload["password"] = existing.get("password", "")
    await r.set("sz:config", json.dumps(payload))
    # Update settings in memory
    settings.smartzone_host     = payload.get("host", "")
    settings.smartzone_port     = int(payload.get("port", 8443))
    settings.smartzone_username = payload.get("username", "")
    settings.smartzone_password = payload.get("password", "")
    settings.smartzone_enabled  = payload.get("enabled", False)

    if settings.smartzone_enabled:
        from app.services.cache import set_active_platform
        await set_active_platform("smartzone")
    else:
        await r.set("platform:smartzone:enabled", "false")

    return {"status": "saved"}


@router.post("/settings/smartzone/test")
async def test_smartzone(bh_token: str = Cookie(default=None)):
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    from app.services import smartzone
    result = await smartzone.sync()
    return result


@router.get("/settings/smartzone/status")
async def get_smartzone_status(bh_token: str = Cookie(default=None)):
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    r = await get_redis()
    raw = await r.get("sz:status")
    return json.loads(raw) if raw else {"status": "unknown"}


@router.get("/settings/smartzone/zones")
async def get_smartzone_zones(bh_token: str = Cookie(default=None)):
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    r = await get_redis()
    raw = await r.get("sz:zones")
    return json.loads(raw) if raw else []


# ── Platform Exclusivity ──────────────────────────────────────────────────────

@router.get("/settings/active-platform")
async def get_active_platform_endpoint(bh_token: str = Cookie(default=None)):
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    from app.services.cache import get_active_platform
    platform = await get_active_platform()
    return {"active": platform}


@router.post("/settings/active-platform")
async def set_active_platform_endpoint(
    payload: dict = Body(...),
    bh_token: str = Cookie(default=None),
):
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    from app.services.cache import set_active_platform

    platform = payload.get("platform", "")
    if platform not in ("ruckus_one", "unleashed", "smartzone", ""):
        raise HTTPException(status_code=400, detail="Invalid platform")

    if platform:
        await set_active_platform(platform)
    else:
        # Disable all
        from app.services.cache import PLATFORMS, get_redis as _gr
        r = await _gr()
        for p in PLATFORMS:
            await r.set(f"platform:{p}:enabled", "false")

    return {"status": "ok", "active": platform or None}
