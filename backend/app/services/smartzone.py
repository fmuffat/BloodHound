"""
SmartZone sync worker.
Uses Service Ticket authentication (different from Ruckus One OAuth).
API base: https://<host>:8443/wsg/api/public/v11_1/
"""

import json
import logging
from datetime import datetime, timezone

import httpx

from app.services.cache import get_redis
from app.services.history import write_snapshot
from app.config import settings

log = logging.getLogger(__name__)

API_BASE = None
_ticket  = None


def _base() -> str:
    return f"https://{settings.smartzone_host}:{settings.smartzone_port}/wsg/api/public/v11_1"


async def _get_ticket() -> str | None:
    """Login and get a service ticket."""
    try:
        async with httpx.AsyncClient(verify=False, timeout=15) as client:
            r = await client.post(
                f"{_base()}/serviceTicket",
                json={"username": settings.smartzone_username, "password": settings.smartzone_password},
            )
            if r.status_code == 200:
                return r.json().get("serviceTicket")
    except Exception as e:
        log.error(f"SmartZone login failed: {e}")
    return None


async def _get(path: str, ticket: str) -> dict:
    """
    GET request with service ticket.
    `path` may already contain its own query string (e.g. "aps?listSize=500")
    — use "&" in that case instead of blindly appending "?serviceTicket=...",
    which previously produced a malformed double-"?" URL. That bug caused
    the serviceTicket to never be parsed, so every call using a path with
    query params (APs) silently got a 401 and was treated as an empty list.
    """
    separator = "&" if "?" in path else "?"
    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        r = await client.get(
            f"{_base()}/{path}{separator}serviceTicket={ticket}",
        )
        return r.json() if r.status_code == 200 else {}


async def _post(path: str, ticket: str, payload: dict) -> dict:
    """POST request with service ticket."""
    async with httpx.AsyncClient(verify=False, timeout=15) as client:
        r = await client.post(
            f"{_base()}/{path}?serviceTicket={ticket}",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        return r.json() if r.status_code == 200 else {}


async def sync() -> dict:
    """Main sync: fetch APs, WLANs, and connected clients from SmartZone."""
    # Read config from Redis first (overrides env settings)
    redis = await get_redis()
    raw = await redis.get("sz:config")
    if raw:
        cfg = json.loads(raw)
        settings.smartzone_host     = cfg.get("host", "")
        settings.smartzone_port     = int(cfg.get("port", 8443))
        settings.smartzone_username = cfg.get("username", "")
        settings.smartzone_password = cfg.get("password", "")
        settings.smartzone_enabled  = cfg.get("enabled", False)

    from app.services.cache import is_platform_enabled
    if not await is_platform_enabled("smartzone"):
        return {"status": "disabled"}

    if not settings.smartzone_host or not settings.smartzone_enabled:
        return {"status": "disabled"}

    ticket = await _get_ticket()
    if not ticket:
        await _store_status("error", "Login failed")
        return {"status": "error", "detail": "Login failed"}

    redis = await get_redis()
    counts = {"aps": 0, "wlans": 0, "clients": 0}

    # ── Fetch zones ──────────────────────────────────────────────────────────
    zones_data = await _get("rkszones?listSize=500", ticket)
    all_zones  = {z["id"]: z["name"] for z in zones_data.get("list", [])}

    # Filter to configured zone(s) if set
    cfg = json.loads(await redis.get("sz:config") or "{}")
    zone_filter = cfg.get("zone_ids", [])  # list of selected zone IDs
    if zone_filter:
        zone_ids = [z for z in all_zones if z in zone_filter]
    else:
        zone_ids = list(all_zones.keys())

    # Store zone list for frontend
    await redis.set("sz:zones", json.dumps([{"id": k, "name": v} for k, v in all_zones.items()]))

    # ── Fetch WLANs per zone ─────────────────────────────────────────────────
    wlan_map = {}  # wlan_id → {ssid, is_guest}
    for zone_id in zone_ids:
        wlans = await _get(f"rkszones/{zone_id}/wlans?listSize=500", ticket)
        for w in wlans.get("list", []):
            wid  = w.get("id", "")
            ssid = w.get("ssid", "")
            auth = w.get("authServiceOrProfile", {})
            is_guest = "guest" in w.get("accessTunnelType", "").lower() or \
                       "guest" in w.get("name", "").lower()
            wlan_map[str(wid)] = {"ssid": ssid, "is_guest": is_guest}
            wlan_data = {"ssid": ssid, "is_guest": is_guest, "source": "smartzone"}
            await redis.setex(f"sz:wlan:{wid}", 3600, json.dumps(wlan_data))
            # Also store by SSID name (SZ sends SSID name directly as wlan_id in syslog)
            await redis.setex(f"sz:wlan_by_name:{ssid}", 3600, json.dumps(wlan_data))
            counts["wlans"] += 1

    # ── Fetch APs ─────────────────────────────────────────────────────────────
    aps_data = await _get(f"aps?listSize=500", ticket)
    for ap in aps_data.get("list", []):
        ap_mac  = ap.get("mac", "").upper()
        ap_name = ap.get("name", ap_mac)
        zone_id = ap.get("zoneId", "")
        zone_name = all_zones.get(zone_id, "")
        await redis.setex(f"sz:ap:{ap_mac}", 3600, json.dumps({
            "name":      ap_name,
            "model":     ap.get("model", ""),
            "zone_id":   zone_id,
            "zone_name": zone_name,
        }))
        # Store AP venue mapping
        await redis.setex(f"ruckus:ap_venue:{ap_name}", 86400, zone_name)
        counts["aps"] += 1

    # ── Fetch connected clients ───────────────────────────────────────────────
    # The SmartZone Northbound API paginates this query (response includes
    # totalCount/hasMore/firstIndex) — loop until hasMore is false, or a
    # zone with more clients than one page would be silently truncated
    # (same class of bug found and fixed in the Ruckus One client sync).
    for zone_id in zone_ids:
        all_clients = []
        start = 0
        page_size = 100
        while True:
            clients_data = await _post("query/client", ticket, {
                "filters": [{"type": "ZONE", "value": zone_id}],
                "fullTextSearch": {"type": "AND", "value": ""},
                "start": start,
                "limit": page_size,
            })
            page_list = clients_data.get("list", [])
            all_clients.extend(page_list)
            if not clients_data.get("hasMore") or not page_list:
                break
            start += page_size

        for c in all_clients:
            mac     = c.get("clientMac", "").upper().replace("-", ":")
            if not mac:
                continue
            wlan_id = str(c.get("wlanId", ""))
            wlan    = wlan_map.get(wlan_id, {})
            ap_mac  = c.get("apMac", "").upper()

            ap_name        = c.get("apName", "")
            client_zone_id = c.get("zoneId", zone_id)
            zone_name      = all_zones.get(client_zone_id, all_zones.get(zone_id, ""))
            user_name = c.get("userName", "")
            if user_name in ("", "N/A"):
                user_name = ""

            client_info = {
                "mac":         mac,
                "hostname":    c.get("hostname", ""),
                "username":    user_name,
                "ip":          c.get("ipAddress", ""),
                "ssid":        wlan.get("ssid", c.get("ssid", "")),
                "wlan_id":     wlan_id,
                "ap_name":     ap_name,
                "venue":       zone_name,
                "is_guest":    wlan.get("is_guest", False),
                "os_type":     c.get("osType", ""),
                "device_type": c.get("deviceType", ""),
                "source":      "smartzone",
            }
            await redis.set(f"sz:mac:{mac}", json.dumps(client_info), ex=600)
            await write_snapshot(mac, client_info, source="smartzone")
            counts["clients"] += 1

    # Logout
    try:
        async with httpx.AsyncClient(verify=False, timeout=10) as client:
            await client.delete(f"{_base()}/serviceTicket?serviceTicket={ticket}")
    except Exception:
        pass

    await _store_status("ok", f"APs: {counts['aps']}, WLANs: {counts['wlans']}, Clients: {counts['clients']}")
    log.info(f"SmartZone sync complete: {counts}")
    return {"status": "ok", "detail": counts}


async def _store_status(status: str, message: str):
    redis = await get_redis()
    await redis.set("sz:status", json.dumps({
        "status":     status,
        "message":    message,
        "synced_at":  datetime.now(timezone.utc).isoformat(),
    }))


async def get_client_info(mac: str) -> dict | None:
    """Get client info from SmartZone Redis cache."""
    redis = await get_redis()
    raw = await redis.get(f"sz:mac:{mac.upper()}")
    if raw:
        return json.loads(raw)
    return None
