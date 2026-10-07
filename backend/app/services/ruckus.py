"""
Ruckus One API client — multi-region support
Syncs to Redis:
  - connected clients : MAC → {hostname, username, os_type, device_type, venue, ap_name, ssid, is_guest}
  - guests            : MAC → {guest_name, email, phone}
  - aliases           : MAC → {alias}
  - wlans             : wlan_id → {ssid, is_guest}
  - venues            : venue_id → {name}
"""

import json
import logging
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.services.cache import get_redis, set_ruckus_token, get_ruckus_token
from app.services.history import write_snapshot


log = logging.getLogger(__name__)

HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# MACs touched by the current full_sync — their history snapshot is written
# once, from the final merged state (client + guest + alias), at the end of
# the sync. Writing it from each step separately produced two alternating
# states per guest (with and without guest fields) on every cycle.
_synced_macs: set[str] = set()

# Region URL mapping
REGION_URLS = {
    "EU":   ("https://api.eu.ruckus.cloud",  "https://eu.ruckus.cloud"),
    "US":   ("https://api.ruckus.cloud",      "https://ruckus.cloud"),
    "ASIA": ("https://api.asia.ruckus.cloud", "https://asia.ruckus.cloud"),
}


async def _get_config() -> dict:
    """
    Fetch fresh Ruckus One config from Redis (ruckus_one:config) — same
    fix as Unleashed/SmartZone. The static `settings.ruckus_one_*` object
    only reflects whatever env vars the process started with; without
    this, saving new credentials via the UI silently does nothing until
    the backend container is manually restarted.
    """
    r = await get_redis()
    raw = await r.get("ruckus_one:config")
    if raw:
        cfg = json.loads(raw)
        return {
            "region":        cfg.get("region") or "EU",
            "tenant_id":     cfg.get("tenant_id", ""),
            "client_id":     cfg.get("client_id", ""),
            "client_secret": cfg.get("client_secret", ""),
        }
    return {
        "region":        getattr(settings, "ruckus_one_region", "EU"),
        "tenant_id":     getattr(settings, "ruckus_one_tenant_id", ""),
        "client_id":     getattr(settings, "ruckus_one_client_id", ""),
        "client_secret": getattr(settings, "ruckus_one_client_secret", ""),
    }


async def _get_urls() -> tuple[str, str]:
    """Return (api_base_url, auth_url) based on configured region."""
    cfg = await _get_config()
    region = (cfg["region"] or "EU").upper()
    api_url, auth_base = REGION_URLS.get(region, REGION_URLS["EU"])
    auth_url = f"{auth_base}/oauth2/token/{cfg['tenant_id']}"
    return api_url, auth_url


# ── Authentication ────────────────────────────────────────────────────────────

async def _get_token() -> str:
    cached = await get_ruckus_token()
    if cached:
        return cached

    cfg = await _get_config()
    region = (cfg["region"] or "EU").upper()
    _, auth_base = REGION_URLS.get(region, REGION_URLS["EU"])
    auth_url = f"{auth_base}/oauth2/token/{cfg['tenant_id']}"

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            auth_url,
            data={
                "grant_type": "client_credentials",
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        data = r.json()
        token = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        await set_ruckus_token(token, ttl=expires_in - 60)
        return token


async def _auth_headers() -> dict:
    token = await _get_token()
    return {**HEADERS, "Authorization": f"Bearer {token}"}


async def _post(path: str, body: dict = None) -> dict:
    headers = await _auth_headers()
    base_url, _ = await _get_urls()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{base_url}{path}", headers=headers, json=body or {})
        if r.status_code == 401:
            # Token expired — force refresh
            redis = await get_redis()
            await redis.delete("ruckus:token")
            headers = await _auth_headers()
            r = await client.post(f"{base_url}{path}", headers=headers, json=body or {})
        r.raise_for_status()
        return r.json()


async def _get(path: str) -> tuple[int, dict]:
    token = await _get_token()
    base_url, _ = await _get_urls()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/vnd.ruckus.v1+json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(f"{base_url}{path}", headers=headers)
        return r.status_code, r.json() if r.text else {}


def _extract_list(data: dict) -> list:
    """Extract list from Ruckus One response (handles list, content, data formats)."""
    return data.get("list", data.get("content", data.get("data", [])))


# ── WLAN sync ─────────────────────────────────────────────────────────────────

GUEST_SUBTYPES = {"guestpass", "wispr", "otp", "guest"}


async def sync_wlans() -> int:
    """Sync all WiFi networks and flag guest networks."""
    log.info("Syncing WLANs...")
    networks = []
    page = 1
    while True:
        data = await _post("/wifiNetworks/query", {"page": page})
        page_items = _extract_list(data)
        networks.extend(page_items)
        total_count = data.get("totalCount", len(networks))
        if not page_items or len(networks) >= total_count:
            break
        page += 1
    redis = await get_redis()
    count = 0

    for n in networks:
        wlan_id = n.get("id", "")
        if not wlan_id:
            continue
        ssid = n.get("ssid") or n.get("name", "")
        subtype = n.get("nwSubType", "").lower()
        is_guest = (
            subtype in GUEST_SUBTYPES
            or "guest" in ssid.lower()
            or "guest" in n.get("name", "").lower()
        )
        info = {"ssid": ssid, "is_guest": is_guest, "subtype": subtype}
        await redis.setex(f"ruckus:wlan:{wlan_id}", 3600, json.dumps(info))
        count += 1

    log.info(f"WLANs synced: {count}")
    return count


# ── Venue sync ────────────────────────────────────────────────────────────────

async def sync_venues() -> int:
    """Sync all venues."""
    log.info("Syncing venues...")
    venues = []
    page = 1
    while True:
        data = await _post("/venues/query", {"page": page})
        page_items = _extract_list(data)
        venues.extend(page_items)
        total_count = data.get("totalCount", len(venues))
        if not page_items or len(venues) >= total_count:
            break
        page += 1
    redis = await get_redis()
    count = 0

    for v in venues:
        venue_id = v.get("id", "")
        if not venue_id:
            continue
        info = {
            "name":    v.get("name", ""),
            "city":    v.get("city", ""),
            "country": v.get("country", ""),
        }
        await redis.setex(f"ruckus:venue:{venue_id}", 3600, json.dumps(info))
        count += 1

    log.info(f"Venues synced: {count}")
    return count


# ── Connected clients sync ────────────────────────────────────────────────────

async def sync_clients() -> int:
    """
    Sync all currently connected clients.
    TTL is short (10 min) since clients connect/disconnect frequently.

    The Ruckus One API paginates this endpoint (observed default page size
    of 25) — a single unpaginated call silently truncates the client list
    on any site with more than one page of connected clients. Loop through
    all pages using the response's totalCount until everything is fetched.
    """
    log.info("Syncing connected clients...")
    clients = []
    page = 1
    while True:
        data = await _post("/venues/aps/clients/query", {"page": page})
        page_clients = _extract_list(data)
        clients.extend(page_clients)
        total_count = data.get("totalCount", len(clients))
        if not page_clients or len(clients) >= total_count:
            break
        page += 1
    log.info(f"Fetched {len(clients)} clients across {page} page(s)")
    redis = await get_redis()
    count = 0

    for c in clients:
        mac = c.get("macAddress", "").lower()
        if not mac:
            continue

        # Check if client is on a guest network
        wlan_id = c.get("networkInformation", {}).get("id", "")
        wlan_raw = await redis.get(f"ruckus:wlan:{wlan_id}")
        wlan_info = json.loads(wlan_raw) if wlan_raw else {}
        is_guest = wlan_info.get("is_guest", False)

        info = {
            "hostname":    c.get("hostname", mac),
            "username":    c.get("username", ""),
            "os_type":     c.get("osType", ""),
            "device_type": c.get("deviceType", ""),
            "model":       c.get("modelName", ""),
            "venue":       c.get("venueInformation", {}).get("name", ""),
            "ap_name":     c.get("apInformation", {}).get("name", ""),
            "ssid":        c.get("networkInformation", {}).get("ssid", "") or wlan_info.get("ssid", ""),
            "wlan_id":     wlan_id,
            "is_guest":    is_guest,
            "ip":          c.get("ipAddress", ""),
            "band":        c.get("band", ""),
            "source":      "ruckus_one",
            "synced_at":   datetime.now(timezone.utc).isoformat(),
        }
        await redis.setex(f"ruckus:mac:{mac}", 600, json.dumps(info))
        _synced_macs.add(mac)

        # Store ap_name → venue mapping for log enrichment
        ap_name = c.get("apInformation", {}).get("name", "")
        venue   = c.get("venueInformation", {}).get("name", "")
        if ap_name and venue:
            await redis.setex(f"ruckus:ap_venue:{ap_name}", 86400, venue)

        count += 1

    log.info(f"Clients synced: {count}")
    return count


# ── Guest users sync ──────────────────────────────────────────────────────────

async def sync_guests() -> int:
    """
    Sync guest users and merge with MAC entries when devicesMac is populated.
    Guest info is stored separately by guest_id for future lookups.
    """
    log.info("Syncing guest users...")
    guests = []
    page = 1
    while True:
        data = await _post("/guestUsers/query", {"page": page})
        page_items = _extract_list(data)
        guests.extend(page_items)
        total_count = data.get("totalCount", len(guests))
        if not page_items or len(guests) >= total_count:
            break
        page += 1

    def guest_priority(g):
        status = g.get("guestStatus", "")
        if "Online" in status:
            return 2
        if g.get("guestType") == "HostGuest":
            return 1
        return 0
    guests = sorted(guests, key=guest_priority)
    redis = await get_redis()
    count = 0

    for g in guests:
        macs = g.get("devicesMac", [])
        guest_info = {
            "guest_name":          g.get("name", ""),
            "email":               g.get("emailAddress", ""),
            "phone":               g.get("mobilePhoneNumber", ""),
            "ssid":                g.get("ssid", ""),
            "status":              g.get("guestStatus", ""),
            "is_guest":            True,
            "guest_type":          g.get("guestType", ""),
            "sponsor_email":       g.get("hostApprovalEmail", ""),
            "pass_duration_hours": g.get("passDurationHours", 0),
            "expiry_date":         g.get("expiryDate", ""),
            "creation_date":       g.get("creationDate", ""),
        }

        # Merge guest info into existing MAC entry
        for mac in macs:
            mac = mac.lower()
            existing_raw = await redis.get(f"ruckus:mac:{mac}")
            existing = json.loads(existing_raw) if existing_raw else {}
            # Keep current SSID and AP from sync_clients
            current_ssid = existing.get("ssid", "")
            current_ap   = existing.get("ap_name", "")

            # If guest is Expired and client is on a different SSID,
            # only update guest identity fields, not guest_type/sponsor
            guest_status = guest_info.get("status", "")
            guest_ssid   = guest_info.get("ssid", "")
            is_expired   = "Expired" in guest_status or "Offline" in guest_status
            on_diff_ssid = current_ssid and guest_ssid and current_ssid != guest_ssid

            if is_expired and on_diff_ssid:
                # Only update name/email/phone, keep current guest_type context
                existing["guest_name"] = guest_info.get("guest_name", existing.get("guest_name", ""))
                existing["email"]      = guest_info.get("email",      existing.get("email", ""))
                existing["phone"]      = guest_info.get("phone",      existing.get("phone", ""))
            else:
                existing.update(guest_info)

            # For Online HostGuest, use the guest SSID (more accurate than wlan_id lookup).
            # Otherwise, never trust the guest pass's own (possibly old/historical)
            # ssid as the device's current network — that's a registration-time
            # value, not a live status. Only use the live client cache's ssid
            # (current_ssid) when we actually have one; if the live client
            # entry expired (TTL 600s) before this guest sync ran, leave ssid
            # blank rather than silently overwriting it with the guest pass's
            # SSID. A blank ssid here still gets correctly resolved from the
            # log's own wlan_id at enrichment time — that's more accurate
            # than this stale fallback ever was.
            guest_is_online = "Online" in guest_info.get("status", "")
            guest_is_host   = guest_info.get("guest_type") == "HostGuest"
            if guest_is_online and guest_is_host:
               # Keep the sponsor SSID from guest_info
               existing["ssid"] = guest_info.get("ssid", current_ssid)
            elif current_ssid:
               existing["ssid"] = current_ssid
            else:
               existing["ssid"] = ""
            if current_ap:
               existing["ap_name"] = current_ap
            await redis.set(f"ruckus:mac:{mac}", json.dumps(existing), ex=600)
            _synced_macs.add(mac)
            count += 1

        # Store by guest_id for future MAC-less lookups
        guest_id = g.get("id", "")
        if guest_id:
            await redis.setex(f"ruckus:guest:{guest_id}", 3600, json.dumps(guest_info))

    log.info(f"Guests synced: {count} (with MAC address)")
    return count


# ── Alias sync ────────────────────────────────────────────────────────────────

async def sync_aliases() -> int:
    """Sync all client aliases and merge into MAC entries."""
    log.info("Syncing client aliases...")
    data = await _post("/clients/aliases/query", {})
    aliases = data.get("content", [])
    redis = await get_redis()
    count = 0

    for a in aliases:
        mac = a.get("macAddress", "").lower()
        if not mac:
            continue
        existing_raw = await redis.get(f"ruckus:mac:{mac}")
        existing = json.loads(existing_raw) if existing_raw else {}
        existing["alias"] = a.get("alias", "")
        await redis.setex(f"ruckus:mac:{mac}", 600, json.dumps(existing))
        count += 1

    log.info(f"Aliases synced: {count}")
    return count


# ── On-demand MAC lookup ──────────────────────────────────────────────────────

async def lookup_mac(mac: str) -> dict | None:
    """
    Immediate lookup for an unknown MAC address.
    Called when a MAC is seen in logs but not in Redis cache.
    """
    mac = mac.lower()
    redis = await get_redis()

    # Check cache first
    cached = await redis.get(f"ruckus:mac:{mac}")
    if cached:
        return json.loads(cached)

    # Try alias endpoint
    status, data = await _get(f"/clients/aliases/{mac}")
    if status == 200:
        alias_info = {"alias": data.get("alias", ""), "mac": mac}
        await redis.setex(f"ruckus:mac:{mac}", 300, json.dumps(alias_info))
        return alias_info

    return None


# ── Client label helper ───────────────────────────────────────────────────────

def get_client_label(info: dict) -> str:
    """
    Returns the best available display name for a client:
    alias > guest_name > hostname > MAC
    """
    if info.get("alias"):
        return info["alias"]
    if info.get("guest_name"):
        return info["guest_name"]
    hostname = info.get("hostname", "")
    mac = info.get("mac", "")
    if hostname and hostname != mac:
        return hostname
    return mac or "Unknown"


# ── Full sync entry point ─────────────────────────────────────────────────────

async def test_connection() -> dict:
    """
    Actually verify R1 connectivity/credentials by forcing a fresh OAuth
    token request — lightweight, no full sync. Used by the Settings UI's
    "Test connection" button, which previously just read cached Redis
    state and reported success regardless of whether the real API
    connection worked at all.
    """
    from app.services.cache import get_redis as _get_redis
    r = await _get_redis()
    await r.delete("ruckus:token")  # force a fresh token request, not a cached one
    await _get_token()
    return {"ok": True}


async def full_sync() -> dict:
    """Full sync — called at startup and every 5 minutes."""
    from app.services.cache import is_platform_enabled
    if not await is_platform_enabled("ruckus_one"):
        return {"status": "disabled"}
    results = {}
    _synced_macs.clear()
    try:
        results["wlans"]   = await sync_wlans()
        results["venues"]  = await sync_venues()
        results["clients"] = await sync_clients()
        results["guests"]  = await sync_guests()
        results["aliases"] = await sync_aliases()

        redis = await get_redis()
        # One snapshot per client, from its final merged state. History is
        # best-effort: OpenSearch being unavailable (e.g. still starting)
        # must not fail the sync itself — the live cache is already written.
        try:
            for mac in _synced_macs:
                raw = await redis.get(f"ruckus:mac:{mac}")
                if raw:
                    await write_snapshot(mac, json.loads(raw), source="ruckus_one")
        except Exception as e:
            log.warning(f"History snapshots skipped this cycle: {e}")

        await redis.set("ruckus:last_sync", datetime.now(timezone.utc).isoformat())
        log.info(f"Full sync complete: {results}")
    except Exception as e:
        log.error(f"Full sync failed: {e}")
        results["error"] = str(e)

    return results
