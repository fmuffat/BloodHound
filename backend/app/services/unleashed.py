"""
Ruckus Unleashed API client.
Uses Selenium headless browser for authentication (required by Unleashed web interface).
Syncs to Redis:
  - connected clients : MAC → {hostname, dvcinfo, dvctype, ip, ssid, ap_name, is_guest}
  - APs               : ap_mac → {ap_name, model, ip, firmware, num_sta, role}
  - WLANs             : wlan_id → {ssid, is_guest}
"""

import json
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

from app.config import settings
from app.services.cache import get_redis

log = logging.getLogger(__name__)

GUEST_USAGES = {"guest", "wispr", "hotspot"}


def _get_unleashed_config():
    """Return Unleashed connection config from settings."""
    return {
        "ip":       getattr(settings, "unleashed_ip", ""),
        "username": getattr(settings, "unleashed_username", "admin"),
        "password": getattr(settings, "unleashed_password", ""),
        "enabled":  getattr(settings, "unleashed_enabled", False),
    }


def _parse_xml(body: str):
    """Parse XML response safely."""
    try:
        return ET.fromstring(body)
    except ET.ParseError as e:
        log.error(f"Unleashed XML parse error: {e}")
        return None


def _get_driver(ip: str):
    """Create headless Chrome driver."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--log-level=3")
    options.binary_location = "/usr/bin/chromium"
    options.add_argument("--disable-gpu")
    options.add_argument("--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 192.168.0.0/16")
    options.add_argument("--dns-servers=192.168.1.1")

    try:
        # Try system chromedriver first (installed in Docker)
        driver = webdriver.Chrome(options=options)
        return driver
    except Exception:
        pass

    try:
        # Fallback to webdriver-manager
        from webdriver_manager.chrome import ChromeDriverManager
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )
        return driver
    except Exception as e:
        log.error(f"Chrome/ChromeDriver not available: {e}")
        raise


def _api_call(driver, action: str, comp: str, body: str = "",
              api_url: str = "", dash_url: str = "") -> dict:
    """Execute an API call via browser XHR."""
    result = driver.execute_async_script("""
        var callback = arguments[arguments.length - 1];
        var ts  = Date.now();
        var rnd = Math.floor(Math.random() * 9000) + 1000;
        var updater = arguments[1] + '.' + ts + '.' + rnd;
        var xml = "<ajax-request action='" + arguments[0] + "' updater='" + updater
                + "' comp='" + arguments[1] + "'>" + arguments[2] + "</ajax-request>";
        var xhr = new XMLHttpRequest();
        xhr.open('POST', arguments[3], true);
        xhr.setRequestHeader('content-type',
            'application/x-www-form-urlencoded; charset=UTF-8');
        xhr.setRequestHeader('x-csrf-token', window.csfrToken || '');
        xhr.setRequestHeader('Referer', arguments[4]);
        xhr.addEventListener('load', function() {
            callback({status: xhr.status, body: xhr.responseText});
        });
        xhr.addEventListener('error', function() {
            callback({status: -1, body: ''});
        });
        xhr.send(xml);
    """, action, comp, body, api_url, dash_url)
    return result or {}


async def _sync_with_driver(driver, cfg: dict) -> dict:
    """
    Perform full sync using an authenticated browser session.
    Returns counts of synced items.
    """
    import time
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    ip       = cfg["ip"]
    base_url = f"https://{ip}"
    login_url = f"{base_url}/admin/login.jsp"
    dash_url  = f"{base_url}/admin/dashboard.jsp"
    api_url   = f"{base_url}/admin/api/unv1/cmd.jsp"

    # ── Login ─────────────────────────────────────────────────────────────────
    log.info(f"Unleashed: logging in to {base_url}...")
    driver.get(login_url)
    wait = WebDriverWait(driver, 20)
    wait.until(EC.presence_of_element_located((By.ID, "username"))).send_keys(cfg["username"])
    driver.find_element(By.ID, "password").send_keys(cfg["password"])
    driver.find_element(By.XPATH, "//button[@type='submit']").click()
    time.sleep(3)

    if "dashboard" not in driver.current_url:
        raise RuntimeError(f"Unleashed login failed — URL: {driver.current_url}")

    log.info("Unleashed: login successful")
    redis = await get_redis()

    # ── Sync WLANs ────────────────────────────────────────────────────────────
    log.info("Unleashed: syncing WLANs...")
    resp = _api_call(driver, "getstat", "stamgr", "<wlan LEVEL='1' />", api_url, dash_url)
    root = _parse_xml(resp.get("body", ""))
    wlan_count = 0
    wlan_map = {}  # wlan_id → {ssid, is_guest}

    if root is not None:
        for wlan in root.findall(".//wlan"):
            wlan_id = wlan.get("id", "")
            ssid    = wlan.get("ssid", "")
            usage   = wlan.get("usage", "user").lower()
            is_guest = usage in GUEST_USAGES

            if wlan_id:
                info = {"ssid": ssid, "is_guest": is_guest, "usage": usage}
                await redis.setex(
                    f"unleashed:{ip}:wlan:{wlan_id}", 3600, json.dumps(info)
                )
                wlan_map[wlan_id] = info
                wlan_count += 1

    log.info(f"Unleashed: WLANs synced: {wlan_count}")

    # ── Sync APs ──────────────────────────────────────────────────────────────
    log.info("Unleashed: syncing APs...")
    resp = _api_call(driver, "getstat", "stamgr", "<ap LEVEL='1' />", api_url, dash_url)
    root = _parse_xml(resp.get("body", ""))
    ap_count = 0
    ap_map = {}  # ap_mac → ap_name

    if root is not None:
        for ap in root.findall(".//ap"):
            ap_mac  = ap.get("mac", "").lower()
            ap_name = ap.get("ap-name") or ap.get("devname", ap_mac)

            if ap_mac:
                info = {
                    "ap_name":  ap_name,
                    "model":    ap.get("display-model") or ap.get("model", ""),
                    "ip":       ap.get("ip", ""),
                    "firmware": ap.get("firmware-version", ""),
                    "num_sta":  ap.get("num-sta", "0"),
                    "role":     ap.get("role", ""),
                    "serial":   ap.get("serial-number", ""),
                }
                await redis.setex(
                    f"unleashed:{ip}:ap:{ap_mac}", 3600, json.dumps(info)
                )
                ap_map[ap_mac] = ap_name
                ap_count += 1

    log.info(f"Unleashed: APs synced: {ap_count}")

    # ── Sync Clients ──────────────────────────────────────────────────────────
    log.info("Unleashed: syncing clients...")
    resp = _api_call(driver, "getstat", "stamgr", "<client LEVEL='1' />", api_url, dash_url)
    root = _parse_xml(resp.get("body", ""))
    client_count = 0

    if root is not None:
        for client in root.findall(".//client"):
            mac = client.get("mac", "").lower()
            if not mac:
                continue

            wlan_id  = client.get("wlan-id", "")
            ap_mac   = client.get("ap", "").lower()
            wlan_info = wlan_map.get(wlan_id, {})
            is_guest  = wlan_info.get("is_guest", False)

            info = {
                "hostname":    client.get("hostname", mac),
                "username":    client.get("user", ""),
                "os_type":     client.get("dvcinfo", ""),
                "device_type": client.get("dvctype", ""),
                "model":       client.get("model", ""),
                "ip":          client.get("ip", ""),
                "ssid":        client.get("ssid", ""),
                "ap_name":     client.get("ap-name") or ap_map.get(ap_mac, ""),
                "ap_mac":      ap_mac,
                "wlan_id":     wlan_id,
                "is_guest":    is_guest,
                "rssi":        client.get("received-signal-strength", ""),
                "band":        client.get("radio-band", ""),
                "source":      "unleashed",
                "synced_at":   datetime.now(timezone.utc).isoformat(),
            }

            # Store under standard ruckus:mac key so enrichment works for both
            await redis.setex(f"ruckus:mac:{mac}", 600, json.dumps(info))

            # Also store ap_name → venue mapping (no venue in Unleashed)
            ap_name = info["ap_name"]
            if ap_name:
                await redis.setex(f"ruckus:ap_venue:{ap_name}", 86400, "")

            client_count += 1

    log.info(f"Unleashed: clients synced: {client_count}")

    result = {
        "wlans":   wlan_count,
        "aps":     ap_count,
        "clients": client_count,
    }
    await redis.set(f"unleashed:{ip}:last_sync", datetime.now(timezone.utc).isoformat())
    return result


async def sync() -> dict:
    """
    Full Unleashed sync.
    Starts a headless browser session, logs in, syncs clients/APs/WLANs.
    """
    from app.services.cache import is_platform_enabled
    if not await is_platform_enabled("unleashed"):
        return {"skipped": True, "reason": "platform disabled"}

    cfg = _get_unleashed_config()

    if not cfg["enabled"] or not cfg["ip"] or not cfg["password"]:
        log.debug("Unleashed sync skipped — not configured")
        return {"skipped": True}

    driver = None
    try:
        driver = _get_driver(cfg["ip"])
        result = await _sync_with_driver(driver, cfg)
        log.info(f"Unleashed full sync complete: {result}")
        return result
    except Exception as e:
        log.error(f"Unleashed sync failed: {e}")
        return {"error": str(e)}
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


async def test_connection(ip: str, username: str, password: str) -> dict:
    """Test Unleashed connection — returns AP count and client count."""
    cfg = {"ip": ip, "username": username, "password": password, "enabled": True}
    driver = None
    try:
        driver = _get_driver(ip)
        result = await _sync_with_driver(driver, cfg)
        return {"ok": True, "aps": result.get("aps", 0), "clients": result.get("clients", 0)}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
