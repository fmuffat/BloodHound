#!/bin/bash
#
# Bloodhound — Factory reset before building the OVA template.
#
# Wipes:
#   - All log data (every graylog_* OpenSearch index)
#   - Client history snapshots (bloodhound_client_history index)
#   - All Redis data: Ruckus One / Unleashed / SmartZone configs & caches,
#     active platform selection, auth credentials (resets to default
#     bloodhound/password), JWT secret, DNS cache, retention/preferences
#   - Platform credentials and Graylog secrets in BOTH .env files
#     (syslog-platform/.env and syslog-platform/backend/.env — the latter is
#     where the Settings UI writes credentials)
#   - Docker container logs, journald, nginx logs, shell histories, /tmp
#   - SSH host keys and /etc/machine-id (regenerated on first boot)
#
# Arms bloodhound-firstboot.service, which generates per-appliance secrets
# (SSH host keys, Graylog password, TLS cert) on the customer's first boot.
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
# After this script: power the VM off and export the OVA. Do NOT reboot it
# first — that would consume the first-boot step on the template itself.
#
set -euo pipefail

PROJECT_DIR="/opt/bloodhound/syslog-platform"
OPENSEARCH_URL="http://localhost:9200"
FIRSTBOOT_MARKER="/opt/bloodhound/.firstboot"

# ── Network target ─────────────────────────────────────────────────────────
NET_IP="192.168.1.200"
NET_CIDR="24"
NET_GATEWAY="192.168.1.1"
NET_DNS="8.8.8.8"
NETPLAN_FILE="/etc/netplan/00-installer-config.yaml"
NETPLAN_BACKUP_DIR="/etc/netplan/backups"

# ── Hostname target ───────────────────────────────────────────────────────
TARGET_HOSTNAME="Bloodhound"

# Keys that must never ship in the OVA, in either .env file
SECRET_ENV_KEYS=(
    RUCKUS_ONE_TENANT_ID RUCKUS_ONE_CLIENT_ID RUCKUS_ONE_CLIENT_SECRET
    UNLEASHED_IP UNLEASHED_PASSWORD
    SMARTZONE_HOST SMARTZONE_USERNAME SMARTZONE_PASSWORD
    GRAYLOG_PASSWORD GRAYLOG_ROOT_PASSWORD_SHA2 GRAYLOG_PASSWORD_SECRET
    BLOODHOUND_JWT_SECRET
)

echo "=========================================="
echo " Bloodhound — Factory Reset"
echo "=========================================="
echo "This will permanently erase:"
echo "  - All ingested logs (Graylog/OpenSearch)"
echo "  - Client history snapshots"
echo "  - Ruckus One / Unleashed / SmartZone configs, credentials & cached data"
echo "  - Active platform selection"
echo "  - Bloodhound login credentials (reset to default: bloodhound/password)"
echo "  - DNS cache, retention & preference settings"
echo "  - System logs, shell histories, SSH host keys, machine-id"
echo
echo "It will also reconfigure networking to:"
echo "  IP      : $NET_IP/$NET_CIDR"
echo "  Gateway : $NET_GATEWAY"
echo "  DNS     : $NET_DNS"
echo
echo "And reset the hostname to: $TARGET_HOSTNAME"
echo
echo "Run this from the VM CONSOLE, not SSH: the network change at the end"
echo "will cut an SSH session. When it finishes, power off and export the"
echo "OVA — do not reboot first."
echo
echo "Graylog's own internal config (syslog input, pipeline rules,"
echo "streams) is preserved."
echo "=========================================="
read -rp "Type RESET to confirm: " CONFIRM
if [[ "$CONFIRM" != "RESET" ]]; then
    echo "Aborted. Nothing was changed."
    exit 0
fi

# Delete every document of an index, lifting Graylog's write lock on rotated
# indices just long enough to do it, then physically expunge the deleted
# documents so no personal data remains in the segment files of the OVA disk.
wipe_index() {
    local idx="$1" was_locked
    was_locked=$(curl -s "$OPENSEARCH_URL/$idx/_settings?include_defaults=true" \
        | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$idx',{}).get('settings',{}).get('index',{}).get('blocks',{}).get('write','false'))" 2>/dev/null || echo "false")

    if [[ "$was_locked" == "true" ]]; then
        curl -s -X PUT "$OPENSEARCH_URL/$idx/_settings" \
            -H "Content-Type: application/json" \
            -d '{"index.blocks.write": false}' -o /dev/null
    fi

    curl -s -X POST "$OPENSEARCH_URL/$idx/_delete_by_query?conflicts=proceed&refresh=true&wait_for_completion=true" \
        -H "Content-Type: application/json" \
        -d '{"query": {"match_all": {}}}' \
        -o /dev/null -w "  $idx: delete HTTP %{http_code}\n" || true

    curl -s -X POST "$OPENSEARCH_URL/$idx/_forcemerge?only_expunge_deletes=true" \
        -o /dev/null -w "  $idx: expunge HTTP %{http_code}\n" || true

    if [[ "$was_locked" == "true" ]]; then
        curl -s -X PUT "$OPENSEARCH_URL/$idx/_settings" \
            -H "Content-Type: application/json" \
            -d '{"index.blocks.write": true}' -o /dev/null
    fi
}

echo
echo "--- Wiping log indices ---"
# Discover all graylog_* indices dynamically (Graylog rotates indices over
# time: graylog_0, graylog_1, graylog_2, ...).
INDICES=$(curl -s "$OPENSEARCH_URL/_cat/indices/graylog_*?h=index" | tr -d ' ')
for idx in $INDICES; do
    wipe_index "$idx"
done

echo
echo "--- Wiping client history snapshots (bloodhound_client_history) ---"
wipe_index bloodhound_client_history

echo
echo "--- Flushing Redis (configs, caches, credentials, JWT secret, active platform) ---"
docker exec syslog_redis redis-cli FLUSHALL

echo
echo "--- Clearing credentials and secrets in .env files ---"
# Use sudo: these files may be owned by an account the current user can't
# write to directly.
for ENV_FILE in "$PROJECT_DIR/.env" "$PROJECT_DIR/backend/.env"; do
    [[ -f "$ENV_FILE" ]] || continue
    for key in "${SECRET_ENV_KEYS[@]}"; do
        sudo sed -i "/^${key}=/d" "$ENV_FILE"
    done
    sudo sed -i \
        -e 's/^UNLEASHED_USERNAME=.*/UNLEASHED_USERNAME=admin/' \
        -e 's/^UNLEASHED_ENABLED=.*/UNLEASHED_ENABLED=false/' \
        "$ENV_FILE"
    echo "  $ENV_FILE cleared."
done
# Fail loudly rather than ship a template that still contains a secret
if sudo grep -qE "^(RUCKUS_ONE_CLIENT_SECRET|UNLEASHED_PASSWORD|SMARTZONE_PASSWORD)=." \
        "$PROJECT_DIR/.env" "$PROJECT_DIR/backend/.env" 2>/dev/null; then
    echo "  ERROR: a credential is still present in an .env file — aborting."
    exit 1
fi
sudo rm -f /opt/bloodhound/graylog-admin-password

echo
echo "--- Installing the current systemd units ---"
# Older templates had dev credentials baked into the Unleashed unit with
# Environment= lines; the current unit reads backend/.env instead.
sudo cp "$PROJECT_DIR/bloodhound-unleashed.service" /etc/systemd/system/
sudo cp "$PROJECT_DIR"/deploy/systemd/bloodhound-firstboot.service \
        "$PROJECT_DIR"/deploy/systemd/bloodhound-nginx-reload.path \
        "$PROJECT_DIR"/deploy/systemd/bloodhound-nginx-reload.service \
        /etc/systemd/system/
sudo chmod +x "$PROJECT_DIR/deploy/firstboot.sh"
sudo systemctl daemon-reload
sudo systemctl enable bloodhound-firstboot.service bloodhound-nginx-reload.path
sudo systemctl restart bloodhound-unleashed
if sudo grep -q "UNLEASHED_PASSWORD=" /etc/systemd/system/bloodhound-unleashed.service; then
    echo "  ERROR: the Unleashed unit still contains a password — aborting."
    exit 1
fi

echo
echo "--- Restarting backend (clear in-memory settings) ---"
cd "$PROJECT_DIR"
docker compose restart backend

sleep 5

echo
echo "--- Removing backups, temp files, logs and shell histories ---"
sudo rm -fv /tmp/bloodhound-backup-*.tar.gz 2>&1 || true
sudo find /tmp /var/tmp -mindepth 1 -maxdepth 1 -mtime +0 -exec rm -rf {} + 2>/dev/null || true
# Docker container logs contain backend/worker output (MACs, hostnames…)
sudo sh -c 'truncate -s 0 /var/lib/docker/containers/*/*-json.log' 2>/dev/null || true
sudo sh -c 'find /var/log/nginx -type f -name "*.log" -exec truncate -s 0 {} +; rm -f /var/log/nginx/*.gz /var/log/nginx/*.1' 2>/dev/null || true
sudo journalctl --rotate >/dev/null 2>&1 || true
sudo journalctl --vacuum-time=1s >/dev/null 2>&1 || true
sudo sh -c 'rm -f /root/.bash_history /home/*/.bash_history /home/*/.python_history /home/*/.lesshst' || true
history -c 2>/dev/null || true
echo "  Done."

echo
echo "--- Resetting machine identity (regenerated on first boot) ---"
sudo truncate -s 0 /etc/machine-id
[[ -L /var/lib/dbus/machine-id ]] || sudo rm -f /var/lib/dbus/machine-id
sudo rm -f /etc/ssh/ssh_host_*
echo "  machine-id cleared, SSH host keys removed."

echo
echo "--- Arming first boot ---"
sudo touch "$FIRSTBOOT_MARKER"
echo "  $FIRSTBOOT_MARKER created — secrets will be generated on the next boot."

echo
echo "--- Forcing password change on next admin login ---"
sudo chage -d 0 admin || echo "  WARNING: could not force admin password change"
echo "  admin will be required to set a new password on next login."

echo
echo "--- Resetting hostname to $TARGET_HOSTNAME ---"
sudo hostnamectl set-hostname "$TARGET_HOSTNAME"
echo "  Hostname set to $TARGET_HOSTNAME."

echo
echo "--- Reconfiguring network (last step: an SSH session will drop here) ---"
IFACE=$(ip -o link show | awk -F': ' '!/lo:|docker|veth|br-/{print $2; exit}')
if [[ -z "$IFACE" ]]; then
    echo "  Could not auto-detect a network interface — skipping network reset."
else
    sudo mkdir -p "$NETPLAN_BACKUP_DIR" 2>/dev/null || true
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
    # The template must not carry netplan backups of the dev network either
    sudo rm -f "$NETPLAN_BACKUP_DIR"/*.bak
    echo "  Applying new network configuration ($NET_IP/$NET_CIDR via $NET_GATEWAY, DNS $NET_DNS)..."
    sudo netplan apply
fi

echo
echo "=========================================="
echo " Factory reset complete."
echo " - Login: bloodhound / password (default)"
echo " - Ruckus One / Unleashed / SmartZone: all disabled, no creds"
echo " - No logs, no client history"
echo " - Graylog inputs & pipeline rules: untouched"
echo " - Hostname: $TARGET_HOSTNAME"
echo " - Network: $NET_IP/$NET_CIDR, gateway $NET_GATEWAY, DNS $NET_DNS"
echo
echo " NEXT: sudo poweroff, then export the OVA."
echo "       Do NOT reboot before exporting."
echo "=========================================="
