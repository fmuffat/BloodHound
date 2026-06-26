#!/bin/bash
#
# Bloodhound — Factory reset before building the OVA template.
#
# Wipes:
#   - All log data (graylog_0, graylog_1 OpenSearch indices)
#   - Client history snapshots (bloodhound_client_history index)
#   - All Redis data: Ruckus One / Unleashed / SmartZone configs & caches,
#     active platform selection, auth credentials (resets to default
#     bloodhound/password), DNS cache, retention/preferences settings
#   - Ruckus One / Unleashed credentials in .env files
#
# Reconfigures networking to the OVA template's default address:
#   192.168.1.200/24, gateway 192.168.1.1, DNS 8.8.8.8
#
# Resets the hostname back to "Bloodhound" (dev VM is named "Bloodhound-Dev").
#
# Preserves:
#   - Graylog's own internal configuration (inputs, pipeline rules, streams)
#     stored in MongoDB — NOT touched, so the syslog input + zone_name
#     extraction pipeline rule survive and work immediately after OVA deploy.
#
set -euo pipefail

PROJECT_DIR="/opt/bloodhound/syslog-platform"
OPENSEARCH_URL="http://localhost:9200"

# ── Network target ─────────────────────────────────────────────────────────
NET_IP="192.168.1.200"
NET_CIDR="24"
NET_GATEWAY="192.168.1.1"
NET_DNS="8.8.8.8"
NETPLAN_FILE="/etc/netplan/00-installer-config.yaml"
NETPLAN_BACKUP_DIR="/etc/netplan/backups"

# ── Hostname target ───────────────────────────────────────────────────────
TARGET_HOSTNAME="Bloodhound"

echo "=========================================="
echo " Bloodhound — Factory Reset"
echo "=========================================="
echo "This will permanently erase:"
echo "  - All ingested logs (Graylog/OpenSearch)"
echo "  - Client history snapshots"
echo "  - Ruckus One / Unleashed / SmartZone configs & cached data"
echo "  - Active platform selection"
echo "  - Bloodhound login credentials (reset to default: bloodhound/password)"
echo "  - DNS cache, retention & preference settings"
echo
echo "It will also reconfigure networking to:"
echo "  IP      : $NET_IP/$NET_CIDR"
echo "  Gateway : $NET_GATEWAY"
echo "  DNS     : $NET_DNS"
echo
echo "And reset the hostname to: $TARGET_HOSTNAME"
echo
echo "WARNING: if you are connected over SSH, this will likely"
echo "disconnect you once the IP address changes. Run this from the"
echo "console if possible, or be ready to reconnect on the new IP."
echo
echo "Graylog's own internal config (syslog input, pipeline rules,"
echo "streams) is preserved."
echo "=========================================="
read -rp "Type RESET to confirm: " CONFIRM
if [[ "$CONFIRM" != "RESET" ]]; then
    echo "Aborted. Nothing was changed."
    exit 0
fi

echo
echo "--- Wiping log indices ---"
# Discover all graylog_* indices dynamically (Graylog rotates indices over
# time: graylog_0, graylog_1, graylog_2, ...). Rotated/closed indices are
# locked read-only by Graylog (index.blocks.write) — lift the lock just
# long enough to delete, then restore it to match Graylog's own bookkeeping.
INDICES=$(curl -s "$OPENSEARCH_URL/_cat/indices/graylog_*?h=index" | tr -d ' ')
for idx in $INDICES; do
    WAS_LOCKED=$(curl -s "$OPENSEARCH_URL/$idx/_settings?include_defaults=true" \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$idx',{}).get('settings',{}).get('index',{}).get('blocks',{}).get('write','false'))" 2>/dev/null || echo "false")

    if [[ "$WAS_LOCKED" == "true" ]]; then
        curl -s -X PUT "$OPENSEARCH_URL/$idx/_settings" \
            -H "Content-Type: application/json" \
            -d '{"index.blocks.write": false}' -o /dev/null
    fi

    curl -s -X POST "$OPENSEARCH_URL/$idx/_delete_by_query?conflicts=proceed&refresh=true&wait_for_completion=true" \
        -H "Content-Type: application/json" \
        -d '{"query": {"match_all": {}}}' \
        -o /dev/null -w "  $idx: HTTP %{http_code}\n"

    if [[ "$WAS_LOCKED" == "true" ]]; then
        curl -s -X PUT "$OPENSEARCH_URL/$idx/_settings" \
            -H "Content-Type: application/json" \
            -d '{"index.blocks.write": true}' -o /dev/null
    fi
done

echo
echo "--- Wiping client history snapshots (bloodhound_client_history) ---"
curl -s -X POST "$OPENSEARCH_URL/bloodhound_client_history/_delete_by_query?conflicts=proceed&refresh=true" \
    -H "Content-Type: application/json" \
    -d '{"query": {"match_all": {}}}' \
    -o /dev/null -w "  bloodhound_client_history: HTTP %{http_code}\n" || true

echo
echo "--- Flushing Redis (configs, caches, credentials, active platform) ---"
docker exec syslog_redis redis-cli FLUSHALL

echo
echo "--- Clearing Ruckus One credentials in .env ---"
# Use sudo: this script must work for whichever account runs it (admin on
# a real deployment, after fmuffat is removed), and these files may be
# owned by a dev account that the current user can't write to directly.
ENV_FILE="$PROJECT_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
    sudo sed -i \
        -e 's/^RUCKUS_ONE_TENANT_ID=.*/RUCKUS_ONE_TENANT_ID=/' \
        -e 's/^RUCKUS_ONE_CLIENT_ID=.*/RUCKUS_ONE_CLIENT_ID=/' \
        -e 's/^RUCKUS_ONE_CLIENT_SECRET=.*/RUCKUS_ONE_CLIENT_SECRET=/' \
        "$ENV_FILE" || echo "  WARNING: could not clear $ENV_FILE (continuing anyway)"
    echo "  $ENV_FILE cleared."
fi

echo
echo "--- Resetting Unleashed credentials in backend/.env ---"
BACKEND_ENV_FILE="$PROJECT_DIR/backend/.env"
if [[ -f "$BACKEND_ENV_FILE" ]]; then
    sudo sed -i \
        -e 's/^UNLEASHED_USERNAME=.*/UNLEASHED_USERNAME=admin/' \
        -e 's/^UNLEASHED_ENABLED=.*/UNLEASHED_ENABLED=false/' \
        "$BACKEND_ENV_FILE" || echo "  WARNING: could not reset $BACKEND_ENV_FILE (continuing anyway)"
    echo "  $BACKEND_ENV_FILE reset."
fi

echo
echo "--- Clearing dev Unleashed credentials baked into the systemd unit ---"
# bloodhound-unleashed.service has its own hardcoded Environment= lines,
# separate from .env and from Redis. These are the worker's bootstrap
# fallback if Redis has nothing yet (e.g. right after this reset, before
# the customer configures anything) — leaving real lab credentials here
# would otherwise have the OVA template try to connect to this dev lab's
# Master AP with its real password on a customer site's first boot.
UNLEASHED_UNIT="/etc/systemd/system/bloodhound-unleashed.service"
if [[ -f "$UNLEASHED_UNIT" ]]; then
    sudo sed -i \
        -e 's/^Environment="UNLEASHED_IP=.*"/Environment="UNLEASHED_IP="/' \
        -e 's/^Environment="UNLEASHED_USERNAME=.*"/Environment="UNLEASHED_USERNAME=admin"/' \
        -e 's/^Environment="UNLEASHED_PASSWORD=.*"/Environment="UNLEASHED_PASSWORD="/' \
        "$UNLEASHED_UNIT"
    sudo systemctl daemon-reload
    sudo systemctl restart bloodhound-unleashed
    echo "  $UNLEASHED_UNIT credentials cleared."
fi

echo
echo "--- Restarting backend (clear in-memory settings) ---"
cd "$PROJECT_DIR"
docker compose restart backend

sleep 5

echo
echo "--- Removing dev-only backups from /tmp ---"
rm -fv /tmp/bloodhound-backup-*.tar.gz 2>&1 || true

echo
echo "--- Forcing password change on next admin login ---"
sudo chage -d 0 admin || echo "  WARNING: could not force admin password change"
echo "  admin will be required to set a new password on next login."

echo
echo "--- Resetting hostname to $TARGET_HOSTNAME ---"
sudo hostnamectl set-hostname "$TARGET_HOSTNAME"
echo "  Hostname set to $TARGET_HOSTNAME."

echo
echo "--- Reconfiguring network ---"
IFACE=$(ip -o link show | awk -F': ' '!/lo:|docker|veth|br-/{print $2; exit}')
if [[ -z "$IFACE" ]]; then
    echo "  Could not auto-detect a network interface — skipping network reset."
else
    sudo mkdir -p "$NETPLAN_BACKUP_DIR" 2>/dev/null || true
    if [[ -f "$NETPLAN_FILE" ]]; then
        sudo cp -a "$NETPLAN_FILE" "$NETPLAN_BACKUP_DIR/00-installer-config.yaml.$(date +%Y%m%d_%H%M%S).bak" 2>/dev/null || true
    fi
    sudo bash -c "cat > '$NETPLAN_FILE' << EOF
network:
  version: 2
  renderer: networkd
  ethernets:
    $IFACE:
      addresses: [$NET_IP/$NET_CIDR]
      routes:
        - to: default
          via: $NET_GATEWAY
      nameservers:
        addresses: [$NET_DNS]
EOF"
    sudo chmod 600 "$NETPLAN_FILE"
    echo "  Applying new network configuration ($NET_IP/$NET_CIDR via $NET_GATEWAY, DNS $NET_DNS)..."
    sudo netplan apply
    echo "  Network reconfigured. If you were connected via SSH, reconnect at $NET_IP."
fi

echo
echo "=========================================="
echo " Factory reset complete."
echo " - Login: bloodhound / password (default)"
echo " - Ruckus One / Unleashed / SmartZone: all disabled, no creds (incl. systemd unit)"
echo " - No logs, no client history"
echo " - Graylog inputs & pipeline rules: untouched"
echo " - Hostname: $TARGET_HOSTNAME"
echo " - Network: $NET_IP/$NET_CIDR, gateway $NET_GATEWAY, DNS $NET_DNS"
echo "=========================================="
