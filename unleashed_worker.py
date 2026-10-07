#!/usr/bin/env python3
"""
Bloodhound — Unleashed sync worker
Runs directly on the host (not in Docker) to access local network.
Logs in to Unleashed Master AP via Selenium, syncs clients/APs/WLANs to Redis.

Usage:
    python3 unleashed_worker.py              # run once
    python3 unleashed_worker.py --daemon     # run every 5 minutes
"""

import argparse
import json
import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import redis
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)
log = logging.getLogger("unleashed_worker")

# ── Config from environment ───────────────────────────────────────────────────
UNLEASHED_IP       = os.getenv("UNLEASHED_IP", "")
UNLEASHED_USERNAME = os.getenv("UNLEASHED_USERNAME", "admin")
UNLEASHED_PASSWORD = os.getenv("UNLEASHED_PASSWORD", "")
REDIS_URL          = os.getenv("REDIS_URL", "redis://127.0.0.1:6379")
SYNC_INTERVAL      = int(os.getenv("UNLEASHED_SYNC_INTERVAL", "300"))  # seconds

GUEST_USAGES = {"guest", "wispr", "hotspot"}


OPENSEARCH_URL = os.getenv("OPENSEARCH_URL", "http://localhost:9200")
HISTORY_INDEX = "bloodhound_client_history"
# Same policy as the backend (app/services/history.py): write a snapshot only
# when the client's state changed, or once a day.
SNAPSHOT_REFRESH_SECONDS = 24 * 3600


def write_snapshot(r, mac: str, info: dict) -> None:
    """Write a client state snapshot to OpenSearch (sync, no asyncio here)."""
    import hashlib
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
        "source":        "unleashed",
    }
    digest_key = f"hist:last:{doc['mac']}"
    digest = hashlib.sha256(json.dumps(doc, sort_keys=True).encode()).hexdigest()
    if r.get(digest_key) == digest:
        return
    doc["snapshot_at"] = datetime.now(timezone.utc).isoformat()
    try:
        resp = requests.post(
            f"{OPENSEARCH_URL}/{HISTORY_INDEX}/_doc",
            json=doc, timeout=5,
        )
        if resp.status_code not in (200, 201):
            log.warning(f"Failed to write history snapshot for {mac}: {resp.text}")
            return
        r.setex(digest_key, SNAPSHOT_REFRESH_SECONDS, digest)
    except Exception as e:
        log.warning(f"Failed to write history snapshot for {mac}: {e}")


def get_redis():
    """Connect to Redis."""
    import redis as redis_lib
    return redis_lib.from_url(REDIS_URL, decode_responses=True)


def get_live_config(r) -> dict:
    """
    Fetch the current ip/username/password/enabled from Redis
    (unleashed:config), set live by the Settings UI — re-read every cycle
    so a saved change takes effect on the very next sync, no restart of
    this long-running daemon needed. Falls back to the env vars / CLI args
    this process started with, for first boot before any save has happened.
    """
    raw = r.get("unleashed:config")
    if raw:
        cfg = json.loads(raw)
        return {
            "ip":       cfg.get("ip", UNLEASHED_IP),
            "username": cfg.get("username", UNLEASHED_USERNAME),
            "password": cfg.get("password", UNLEASHED_PASSWORD),
            "enabled":  cfg.get("enabled", False),
        }
    return {
        "ip": UNLEASHED_IP, "username": UNLEASHED_USERNAME,
        "password": UNLEASHED_PASSWORD, "enabled": bool(UNLEASHED_IP and UNLEASHED_PASSWORD),
    }


def parse_xml(body: str):
    try:
        return ET.fromstring(body)
    except ET.ParseError as e:
        log.error(f"XML parse error: {e}")
        return None


def get_driver():
    """
    Create headless Chrome driver on host system.

    Ubuntu no longer ships a non-Snap chromium via apt — "/usr/bin/chromium*"
    and "/usr/bin/chromedriver" are just thin Snap-launcher shell scripts.
    Snap apps need a per-user "~/snap/<app>/" data directory initialized on
    first launch, which silently fails for service accounts (like
    "bloodhound") that have never run them interactively — chromedriver
    exits immediately with status 1, with no useful error message.

    To avoid this entirely, prefer a real, non-Snap Google Chrome install
    (/usr/bin/google-chrome, a native .deb package) together with
    webdriver-manager's own downloaded chromedriver (cached independently
    of Snap, works identically for any user).
    """
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-gpu")
    options.add_argument("--log-level=3")

    # Prefer a real (non-Snap) browser binary. google-chrome is checked
    # first since it's the one we explicitly install for this purpose;
    # the Snap-wrapped chromium paths are kept as a last-resort fallback
    # only (e.g. for older setups that haven't installed Chrome yet).
    snap_wrapped = {"/usr/bin/chromium-browser", "/usr/bin/chromium"}
    chosen_binary = None
    for binary in ["/usr/bin/google-chrome", "/usr/bin/chromium-browser", "/usr/bin/chromium"]:
        if os.path.exists(binary):
            chosen_binary = binary
            options.binary_location = binary
            log.info(f"Using browser: {binary}")
            break

    # Always use webdriver-manager's own chromedriver — it downloads a
    # plain binary matched to the installed browser version, with no Snap
    # involvement, so it works the same for every user account.
    if chosen_binary in snap_wrapped:
        log.warning(
            f"No native Chrome found — falling back to Snap-wrapped {chosen_binary}, "
            "which may fail for service accounts without an initialized Snap "
            "user directory. Install google-chrome for reliability."
        )

    log.info("Resolving chromedriver via webdriver-manager...")
    from webdriver_manager.chrome import ChromeDriverManager
    driver_path = ChromeDriverManager().install()
    return webdriver.Chrome(service=Service(driver_path), options=options)


def api_call(driver, action: str, comp: str, body: str,
             api_url: str, dash_url: str) -> dict:
    """Execute API call via browser XHR."""
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


def sync_once(ip: str, username: str, password: str) -> dict:
    """Full sync — login, fetch clients/APs/WLANs, write to Redis."""
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    base_url  = f"https://{ip}"
    login_url = f"{base_url}/admin/login.jsp"
    dash_url  = f"{base_url}/admin/dashboard.jsp"
    api_url   = f"{base_url}/admin/api/unv1/cmd.jsp"

    driver = get_driver()
    r = get_redis()

    try:
        # ── Login ─────────────────────────────────────────────────────────────
        log.info(f"Logging in to {base_url}...")
        driver.get(login_url)
        wait = WebDriverWait(driver, 20)
        wait.until(EC.presence_of_element_located((By.ID, "username"))).send_keys(username)
        driver.find_element(By.ID, "password").send_keys(password)
        driver.find_element(By.XPATH, "//button[@type='submit']").click()
        time.sleep(3)

        if "dashboard" not in driver.current_url:
            raise RuntimeError(f"Login failed — URL: {driver.current_url}")

        log.info("Login successful")

        # ── Sync WLANs ────────────────────────────────────────────────────────
        resp = api_call(driver, "getstat", "stamgr", "<wlan LEVEL='1' />", api_url, dash_url)
        root = parse_xml(resp.get("body", ""))
        wlan_map = {}
        wlan_count = 0

        if root is not None:
            for wlan in root.findall(".//wlan"):
                wlan_id  = wlan.get("id", "")
                ssid     = wlan.get("ssid", "")
                usage    = wlan.get("usage", "user").lower()
                is_guest = usage in GUEST_USAGES
                if wlan_id:
                    info = {"ssid": ssid, "is_guest": is_guest, "usage": usage}
                    r.setex(f"unleashed:{ip}:wlan:{wlan_id}", 3600, json.dumps(info))
                    wlan_map[wlan_id] = info
                    wlan_count += 1

        log.info(f"WLANs synced: {wlan_count}")

        # ── Sync APs ──────────────────────────────────────────────────────────
        resp = api_call(driver, "getstat", "stamgr", "<ap LEVEL='1' />", api_url, dash_url)
        root = parse_xml(resp.get("body", ""))
        ap_map = {}
        ap_count = 0

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
                    }
                    r.setex(f"unleashed:{ip}:ap:{ap_mac}", 3600, json.dumps(info))
                    ap_map[ap_mac] = ap_name

                    # Store ap_name → venue (empty for Unleashed)
                    if ap_name:
                        r.setex(f"ruckus:ap_venue:{ap_name}", 86400, "Unleashed")

                    ap_count += 1

        log.info(f"APs synced: {ap_count}")

        # ── Sync Clients ──────────────────────────────────────────────────────
        resp = api_call(driver, "getstat", "stamgr", "<client LEVEL='1' />", api_url, dash_url)
        root = parse_xml(resp.get("body", ""))
        client_count = 0

        if root is not None:
            for client in root.findall(".//client"):
                mac = client.get("mac", "").lower()
                if not mac:
                    continue

                wlan_id   = client.get("wlan-id", "")
                ap_mac    = client.get("ap", "").lower()
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
                    "source":      "unleashed",
                    "synced_at":   datetime.now(timezone.utc).isoformat(),
                }

                # Store under standard ruckus:mac key for unified enrichment
                r.setex(f"ruckus:mac:{mac}", 600, json.dumps(info))
                write_snapshot(r, mac, info)
                client_count += 1

        log.info(f"Clients synced: {client_count}")

        result = {"wlans": wlan_count, "aps": ap_count, "clients": client_count}
        r.set(f"unleashed:{ip}:last_sync", datetime.now(timezone.utc).isoformat())
        r.set("unleashed:status", json.dumps({"ok": True, **result,
              "synced_at": datetime.now(timezone.utc).isoformat()}))
        log.info(f"Sync complete: {result}")
        return result

    except Exception as e:
        log.error(f"Sync failed: {e}")
        r.set("unleashed:status", json.dumps({"ok": False, "error": str(e),
              "synced_at": datetime.now(timezone.utc).isoformat()}))
        return {"error": str(e)}
    finally:
        driver.quit()


def main():
    parser = argparse.ArgumentParser(description="Unleashed sync worker")
    parser.add_argument("--daemon", action="store_true",
                        help="Run continuously every UNLEASHED_SYNC_INTERVAL seconds")
    parser.add_argument("--ip",       default=UNLEASHED_IP)
    parser.add_argument("--username", default=UNLEASHED_USERNAME)
    parser.add_argument("--password", default=UNLEASHED_PASSWORD)
    args = parser.parse_args()

    if not args.daemon and (not args.ip or not args.password):
        log.error("UNLEASHED_IP and UNLEASHED_PASSWORD are required for a single run")
        sys.exit(1)

    if args.daemon:
        log.info(f"Starting daemon mode — syncing every {SYNC_INTERVAL}s")
        r = get_redis()
        while True:
            cfg = get_live_config(r)
            if cfg["enabled"] and cfg["ip"] and cfg["password"]:
                sync_once(cfg["ip"], cfg["username"], cfg["password"])
            else:
                log.info("Unleashed disabled or not configured — skipping this cycle")
            time.sleep(SYNC_INTERVAL)
    else:
        sync_once(args.ip, args.username, args.password)


if __name__ == "__main__":
    main()
