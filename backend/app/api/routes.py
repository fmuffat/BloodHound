"""
FastAPI router — search, enrichment and admin endpoints.
"""

import csv
import io
import os
import json
import logging
from datetime import datetime, timezone
from app.config import settings
from typing import Optional

from fastapi import APIRouter, Query, HTTPException, Body
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
                        for key in ul_wlan_keys:
                            raw = await r.get(key)
                            if raw:
                                info = json.loads(raw)
                                ssid = info.get("ssid", "")
                                if ssid and ssid not in ssids:
                                    ssids.append(ssid)
                        else:
                            # Not a GUID, not in Unleashed → SmartZone sends SSID name directly as wlan_id
                            if not _re.fullmatch(r"[0-9a-fA-F]{32}", wlan_id) and wlan_id not in ssids:
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

@router.post("/investigation/export")
async def export_investigation(req: SearchRequest):
    req.limit = 500
    result = await graylog.search_logs(
        query=req.query, from_dt=req.from_dt, to_dt=req.to_dt,
        src_ip=req.src_ip, dst_ip=req.dst_ip, dst_hostname=req.dst_hostname,
        client_mac=req.client_mac, client_label=req.client_label,
        username=req.username, ap_name=req.ap_name, venue=req.venue,
        ssid=req.ssid, proto=req.proto, dst_port=req.dst_port,
        is_guest=req.is_guest, limit=500, offset=0,
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Timestamp", "AP", "Venue", "SSID",
        "Src IP", "Dst IP", "Dst Hostname", "Src Port", "Dst Port", "Protocol",
        "Client MAC", "Client Label", "Alias", "Hostname", "Username",
        "OS Type", "Device Type", "Is Guest",
        "Guest Name", "Email", "Phone",
    ])
    for log_entry in result["logs"]:
        writer.writerow([
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
    """Delete logs older than N days from OpenSearch."""
    import httpx
    from app.config import settings as app_settings
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )

    query = {
        "query": {
            "range": {
                "timestamp": {"lt": cutoff}
            }
        }
    }

    total_deleted = 0
    indices = ["graylog_0", "graylog_1", "graylog_2"]

    async with httpx.AsyncClient(timeout=60) as client:
        for index in indices:
            try:
                r = await client.post(
                    f"{app_settings.opensearch_url}/{index}/_delete_by_query?conflicts=proceed",
                    json=query,
                    headers={"Content-Type": "application/json"},
                )
                if r.status_code == 200:
                    deleted = r.json().get("deleted", 0)
                    total_deleted += deleted
                    if deleted > 0:
                        log.info(f"Purged {deleted} logs from {index} older than {days} days")
            except Exception as e:
                log.warning(f"Purge failed for {index}: {e}")

    from app.services.cache import get_redis as get_r
    r = await get_r()
    await r.set("retention:last_purge", datetime.now(timezone.utc).isoformat())

    return {"deleted": total_deleted, "cutoff": cutoff}


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

from fastapi import Response, Cookie
from fastapi.responses import JSONResponse

@router.post("/auth/login")
async def login(payload: dict = Body(...), response: Response = None):
    from app.services.auth import verify_credentials, create_token
    username = payload.get("username", "")
    password = payload.get("password", "")

    if not await verify_credentials(username, password):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    token = create_token(username)
    resp = JSONResponse({"status": "ok", "username": username})
    resp.set_cookie(
        key="bh_token",
        value=token,
        httponly=True,
        max_age=86400,
        samesite="lax",
    )
    return resp


@router.post("/auth/logout")
async def logout():
    resp = JSONResponse({"status": "ok"})
    resp.delete_cookie("bh_token")
    return resp


@router.get("/auth/me")
async def get_me(bh_token: str = Cookie(default=None)):
    from app.services.auth import verify_token, get_username
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    username = await get_username()
    return {"username": username}


@router.post("/auth/change-password")
async def change_password(
    payload: dict = Body(...),
    bh_token: str = Cookie(default=None),
):
    from app.services.auth import verify_token, verify_credentials, change_credentials, get_username
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    current_password = payload.get("current_password", "")
    new_password      = payload.get("new_password", "")
    new_username      = payload.get("new_username", "")

    # Verify current password
    current_username = await get_username()
    if not await verify_credentials(current_username, current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")

    username = new_username or current_username
    await change_credentials(username, new_password)
    return {"status": "ok"}


@router.post("/gdpr/erase-client")
async def gdpr_erase_client(
    payload: dict = Body(...),
    bh_token: str = Cookie(default=None),
):
    """
    Permanently erase all logs, history, and cached data for a single MAC
    address (GDPR right-to-erasure support). Requires re-entering the
    current password — this is a destructive, irreversible action, so it
    is deliberately gated the same way a password change is, not just a
    UI confirmation dialog that could be clicked through accidentally.
    """
    import httpx
    from app.config import settings as app_settings
    from app.services.auth import verify_token, verify_credentials, get_username

    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    mac = (payload.get("mac") or "").strip()
    password = payload.get("password", "")
    if not mac:
        raise HTTPException(status_code=400, detail="mac is required")

    current_username = await get_username()
    if not await verify_credentials(current_username, password):
        raise HTTPException(status_code=401, detail="Password is incorrect")

    mac_upper = mac.upper()
    mac_lower = mac.lower()
    deleted_logs = 0
    deleted_history = 0

    async with httpx.AsyncClient(timeout=60) as client:
        # Discover all graylog_* indices dynamically — Graylog rotates them
        # over time (graylog_0, graylog_1, graylog_2, ...).
        idx_resp = await client.get(f"{app_settings.opensearch_url}/_cat/indices/graylog_*?h=index")
        indices = [i.strip() for i in idx_resp.text.splitlines() if i.strip()]

        match_query = {
            "query": {
                "bool": {
                    "should": [
                        {"term": {"client_mac": mac_upper}},
                        {"term": {"client_mac": mac_lower}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        }

        for idx in indices:
            # Rotated/closed indices are locked read-only by Graylog —
            # lift the lock just long enough to delete, then restore it.
            settings_resp = await client.get(f"{app_settings.opensearch_url}/{idx}/_settings?include_defaults=true")
            was_locked = False
            try:
                was_locked = (
                    settings_resp.json().get(idx, {}).get("settings", {})
                    .get("index", {}).get("blocks", {}).get("write") == "true"
                )
            except Exception:
                pass

            if was_locked:
                await client.put(f"{app_settings.opensearch_url}/{idx}/_settings",
                                  json={"index.blocks.write": False})

            del_resp = await client.post(
                f"{app_settings.opensearch_url}/{idx}/_delete_by_query?conflicts=proceed&refresh=true&wait_for_completion=true",
                json=match_query,
            )
            if del_resp.status_code == 200:
                deleted_logs += del_resp.json().get("deleted", 0)
            else:
                log.warning(f"GDPR erase: failed to delete from {idx}: {del_resp.text}")

            if was_locked:
                await client.put(f"{app_settings.opensearch_url}/{idx}/_settings",
                                  json={"index.blocks.write": True})

        # Client history snapshots
        hist_query = {
            "query": {
                "bool": {
                    "should": [
                        {"term": {"mac": mac_upper}},
                        {"term": {"mac": mac_lower}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        }
        hist_resp = await client.post(
            f"{app_settings.opensearch_url}/bloodhound_client_history/_delete_by_query?conflicts=proceed&refresh=true",
            json=hist_query,
        )
        if hist_resp.status_code == 200:
            deleted_history = hist_resp.json().get("deleted", 0)

    # Note: the live cache (ruckus:mac:*/sz:mac:*) is deliberately NOT
    # cleared here. It's the device's current operational state as
    # reported by the controller/AP — not a historical record — and gets
    # overwritten on every sync cycle regardless (every 5-10 min) as long
    # as the device stays connected. Clearing it just creates a temporary
    # gap where the client card shows nothing until the next sync, with no
    # real erasure benefit. The actual personal data retained over time —
    # logs and history snapshots — is what's deleted above.
    r = await get_redis()

    # Audit trail — required to demonstrate GDPR compliance. Records *that*
    # an erasure happened, who performed it, and when — not the erased
    # person's own data, so this doesn't reintroduce what was just erased.
    audit_entry = {
        "mac":         mac_upper,
        "performed_by": current_username,
        "at":          datetime.now(timezone.utc).isoformat(),
        "deleted_logs":    deleted_logs,
        "deleted_history": deleted_history,
    }
    await r.rpush("gdpr:audit_log", json.dumps(audit_entry))
    log.warning(f"GDPR erasure performed: {audit_entry}")

    return {
        "status": "ok",
        "mac": mac_upper,
        "deleted_logs": deleted_logs,
        "deleted_history": deleted_history,
    }


@router.get("/gdpr/audit-log")
async def gdpr_audit_log(bh_token: str = Cookie(default=None)):
    """Return the GDPR erasure audit trail (who erased what, and when)."""
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    r = await get_redis()
    entries = await r.lrange("gdpr:audit_log", 0, -1)
    return {"entries": [json.loads(e) for e in entries]}


# ── ZIP Export ────────────────────────────────────────────────────────────────

@router.post("/investigation/export-zip")
async def export_investigation_zip(
    req: SearchRequest,
    password: str = Query(..., description="ZIP password"),
    bh_token: str = Cookie(default=None),
):
    """Export investigation as password-protected ZIP with CSV inside."""
    import io, csv, pyzipper
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    req.limit = 500
    result = await graylog.search_logs(
        query=req.query, from_dt=req.from_dt, to_dt=req.to_dt,
        client_mac=req.client_mac, limit=500, offset=0,
    )

    # Build CSV
    csv_buf = io.StringIO()
    writer  = csv.writer(csv_buf)
    writer.writerow([
        "Timestamp", "Client MAC", "Client Name", "Guest Type",
        "Sponsor Email", "Src IP", "Dst IP", "Dst Hostname",
        "Src Port", "Dst Port", "Protocol", "SSID", "AP", "Venue",
    ])
    for log in result["logs"]:
        writer.writerow([
            log.get("timestamp", ""),
            log.get("client_mac", ""),
            log.get("client_label", ""),
            log.get("guest_type", ""),
            log.get("sponsor_email", ""),
            log.get("src_ip", ""),
            log.get("dst_ip", ""),
            log.get("dst_hostname", ""),
            log.get("src_port", ""),
            log.get("dst_port", ""),
            log.get("proto", ""),
            log.get("ssid", ""),
            log.get("ap_name", ""),
            log.get("venue", ""),
        ])

    csv_bytes = csv_buf.getvalue().encode("utf-8-sig")

    # Build encrypted ZIP in memory
    mac_clean = (req.client_mac or "investigation").replace(":", "")
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
    payload: dict = Body(...),
    password: str = Query(...),
    bh_token: str = Cookie(default=None),
):
    """Export a single event as password-protected ZIP."""
    import io, csv, pyzipper
    from app.services.auth import verify_token
    from app.services.cache import get_client_info
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    mac        = payload.get("mac", "")
    event_data = payload.get("event", {})

    # Get client info
    client = await get_client_info(mac) or {}

    # Build CSV
    csv_buf = io.StringIO()
    writer  = csv.writer(csv_buf)
    writer.writerow(['Field', 'Value'])
    writer.writerow(['--- CLIENT INFO ---', ''])
    writer.writerow(['MAC Address', mac])
    writer.writerow(['Name', client.get('alias') or client.get('guest_name') or client.get('hostname', '')])
    writer.writerow(['Hostname', client.get('hostname', '')])
    writer.writerow(['Username', client.get('username', '')])
    writer.writerow(['OS', client.get('os_type', '')])
    writer.writerow(['Device', client.get('device_type', '')])
    writer.writerow(['Venue', client.get('venue', '')])
    writer.writerow(['SSID', client.get('ssid', '')])

    if client.get('guest_type') == 'HostGuest':
        writer.writerow(['--- SPONSORED ACCESS ---', ''])
        writer.writerow(['Guest Name', client.get('guest_name', '')])
        writer.writerow(['Phone', client.get('phone', '')])
        writer.writerow(['Sponsor Email', client.get('sponsor_email', '')])
        writer.writerow(['Duration', f"{client.get('pass_duration_hours', '')}h"])
        writer.writerow(['Created', client.get('creation_date', '')])
        writer.writerow(['Expires', client.get('expiry_date', '')])
    elif client.get('is_guest'):
        writer.writerow(['--- GUEST INFO ---', ''])
        writer.writerow(['Guest Name', client.get('guest_name', '')])
        writer.writerow(['Email', client.get('email', '')])
        writer.writerow(['Phone', client.get('phone', '')])

    writer.writerow(['--- EVENT ---', ''])
    writer.writerow(['Timestamp', event_data.get('timestamp', '')])
    writer.writerow(['Source IP', event_data.get('src_ip', '')])
    writer.writerow(['Destination IP', event_data.get('dst_ip', '')])
    writer.writerow(['Destination Host', event_data.get('dst_host', '')])
    writer.writerow(['Port', event_data.get('dst_port', '')])
    writer.writerow(['Protocol', event_data.get('proto', '')])
    writer.writerow(['SSID', event_data.get('ssid', '')])
    writer.writerow(['AP', event_data.get('ap_name', '')])
    writer.writerow(['Venue', event_data.get('venue', '')])

    csv_bytes = csv_buf.getvalue().encode('utf-8-sig')
    mac_clean = mac.replace(':', '')
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

@router.post("/settings/ssl")
async def upload_ssl_certificate(
    cert: UploadFile = File(...),
    key:  UploadFile = File(...),
    bh_token: str = Cookie(default=None),
):
    """Upload custom SSL certificate and key, then reload nginx."""
    from app.services.auth import verify_token
    import subprocess
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    cert_path = "/opt/bloodhound/ssl/bloodhound.crt"
    key_path  = "/opt/bloodhound/ssl/bloodhound.key"

    cert_content = await cert.read()
    key_content  = await key.read()

    # Validate cert/key are PEM format
    if not cert_content.startswith(b"-----BEGIN CERTIFICATE"):
        raise HTTPException(status_code=400, detail="Invalid certificate format (must be PEM)")
    if not key_content.startswith(b"-----BEGIN"):
        raise HTTPException(status_code=400, detail="Invalid key format (must be PEM)")

    with open(cert_path, "wb") as f:
        f.write(cert_content)
    with open(key_path, "wb") as f:
        f.write(key_content)

    # Reload nginx
    result = subprocess.run(["sudo", "nginx", "-s", "reload"], capture_output=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"nginx reload failed: {result.stderr.decode()}")

    return {"status": "ok", "message": "Certificate uploaded and nginx reloaded"}


@router.post("/settings/ssl/self-signed")
async def generate_self_signed(
    payload: dict = Body(default={}),
    bh_token: str = Cookie(default=None),
):
    """Regenerate a self-signed certificate."""
    from app.services.auth import verify_token
    import subprocess
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    cn      = payload.get("cn", "bloodhound.local")
    days    = payload.get("days", 3650)

    requested_ip = payload.get("ip", "")
    if requested_ip:
        ip = requested_ip
    else:
        # Auto-detect this server's own outbound-facing IP rather than
        # falling back to any hardcoded address — this code ships
        # identically to every customer, so a fixed default would always
        # be wrong for their network.
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"

    cmd = [
        "openssl", "req", "-x509", "-nodes",
        f"-days", str(days),
        "-newkey", "rsa:2048",
        "-keyout", "/opt/bloodhound/ssl/bloodhound.key",
        "-out",    "/opt/bloodhound/ssl/bloodhound.crt",
        "-subj",   f"/C=CH/ST=Geneva/L=Geneva/O=Bloodhound/CN={cn}",
        "-addext", f"subjectAltName=IP:{ip},DNS:{cn}",
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stderr.decode())

    # Reload nginx
    subprocess.run(["sudo", "nginx", "-s", "reload"], capture_output=True)

    return {"status": "ok", "message": f"Self-signed certificate generated for {cn} / {ip}"}


# ── SmartZone Settings ────────────────────────────────────────────────────────

@router.get("/settings/smartzone")
async def get_smartzone_settings(bh_token: str = Cookie(default=None)):
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    r = await get_redis()
    raw = await r.get("sz:config")
    if raw:
        return json.loads(raw)
    return {"host": settings.smartzone_host, "port": settings.smartzone_port,
            "username": settings.smartzone_username, "enabled": settings.smartzone_enabled}


@router.post("/settings/smartzone")
async def save_smartzone_settings(
    payload: dict = Body(...),
    bh_token: str = Cookie(default=None),
):
    from app.services.auth import verify_token
    if not bh_token or not verify_token(bh_token):
        raise HTTPException(status_code=401, detail="Not authenticated")
    r = await get_redis()
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
