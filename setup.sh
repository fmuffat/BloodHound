#!/bin/bash
#
# Bloodhound OVA — network setup wizard
# Run manually from the console after first login: `sudo setup` or `setup` (auto-elevates).
#
set -euo pipefail

NETPLAN_FILE="/etc/netplan/00-installer-config.yaml"
BACKUP_DIR="/etc/netplan/backups"

# ── Re-exec as root if needed (console-only tool, password prompt is fine) ───
if [[ "$EUID" -ne 0 ]]; then
    exec sudo "$0" "$@"
fi

# ── Helpers ────────────────────────────────────────────────────────────────
IPV4_RE='^([0-9]{1,3}\.){3}[0-9]{1,3}$'

valid_ipv4() {
    local ip="$1"
    [[ "$ip" =~ $IPV4_RE ]] || return 1
    local IFS='.'
    local -a oct=($ip)
    for o in "${oct[@]}"; do
        ((o >= 0 && o <= 255)) || return 1
    done
    return 0
}

valid_cidr_suffix() {
    [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 32 ))
}

ask() {
    local prompt="$1" default="${2:-}" answer
    if [[ -n "$default" ]]; then
        read -rp "$prompt [$default]: " answer
        echo "${answer:-$default}"
    else
        read -rp "$prompt: " answer
        echo "$answer"
    fi
}

# ── Detect interface ──────────────────────────────────────────────────────
IFACE=$(ip -o link show | awk -F': ' '!/lo:|docker|veth|br-/{print $2; exit}')
if [[ -z "$IFACE" ]]; then
    echo "Could not auto-detect a network interface. Aborting."
    exit 1
fi

CUR_ADDR=$(ip -4 -o addr show "$IFACE" | awk '{print $4}' | head -1)
CUR_GW=$(ip route show default 2>/dev/null | awk '/default/{print $3; exit}')
CUR_DNS=$(resolvectl dns "$IFACE" 2>/dev/null | awk -F': ' '{print $2}')

echo "=========================================="
echo " Bloodhound — Network Setup Wizard"
echo "=========================================="
echo "Interface detected : $IFACE"
echo "Current address     : ${CUR_ADDR:-none}"
echo "Current gateway     : ${CUR_GW:-none}"
echo "Current DNS         : ${CUR_DNS:-none}"
echo "------------------------------------------"
echo "You can re-run this wizard anytime by typing: setup"
echo "------------------------------------------"
echo

# ── Collect new config ────────────────────────────────────────────────────
while true; do
    IP_ADDR=$(ask "IP address" "${CUR_ADDR%/*}")
    if valid_ipv4 "$IP_ADDR"; then break; fi
    echo "  -> Invalid IPv4 address, try again."
done

while true; do
    CIDR=$(ask "Subnet mask (CIDR suffix, e.g. 24 for 255.255.255.0)" "${CUR_ADDR#*/}")
    if valid_cidr_suffix "$CIDR"; then break; fi
    echo "  -> Invalid CIDR suffix (must be 1-32), try again."
done

while true; do
    GATEWAY=$(ask "Gateway" "${CUR_GW:-}")
    if valid_ipv4 "$GATEWAY"; then break; fi
    echo "  -> Invalid IPv4 address, try again."
done

while true; do
    DNS1=$(ask "Primary DNS" "${CUR_DNS:-8.8.8.8}")
    if valid_ipv4 "$DNS1"; then break; fi
    echo "  -> Invalid IPv4 address, try again."
done

DNS2=$(ask "Secondary DNS (optional, press Enter to skip)" "")
if [[ -n "$DNS2" ]] && ! valid_ipv4 "$DNS2"; then
    echo "  -> Invalid IPv4 address, ignoring secondary DNS."
    DNS2=""
fi

SEARCH_DOMAIN=$(ask "DNS search domain (optional, press Enter to skip)" "")

CUR_HOSTNAME=$(hostname)
NEW_HOSTNAME=$(ask "Hostname" "$CUR_HOSTNAME")

# ── Build DNS list ────────────────────────────────────────────────────────
if [[ -n "$DNS2" ]]; then
    DNS_LIST="[$DNS1, $DNS2]"
else
    DNS_LIST="[$DNS1]"
fi

# ── Summary & confirmation ───────────────────────────────────────────────
echo
echo "------------------------------------------"
echo " Summary"
echo "------------------------------------------"
echo "Interface     : $IFACE"
echo "Address       : $IP_ADDR/$CIDR"
echo "Gateway       : $GATEWAY"
echo "DNS           : $DNS1${DNS2:+, $DNS2}"
echo "Search domain : ${SEARCH_DOMAIN:-(none)}"
echo "Hostname      : $NEW_HOSTNAME"
echo "------------------------------------------"
read -rp "Apply this configuration now? [y/N]: " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Aborted. No changes were made. Run 'setup' again to retry."
    exit 0
fi

# ── Backup current netplan config ────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
if [[ -f "$NETPLAN_FILE" ]]; then
    cp -a "$NETPLAN_FILE" "$BACKUP_DIR/00-installer-config.yaml.$(date +%Y%m%d_%H%M%S).bak"
fi

# ── Write new netplan config ─────────────────────────────────────────────
{
    echo "network:"
    echo "  version: 2"
    echo "  renderer: networkd"
    echo "  ethernets:"
    echo "    $IFACE:"
    echo "      addresses: [$IP_ADDR/$CIDR]"
    echo "      routes:"
    echo "        - to: default"
    echo "          via: $GATEWAY"
    echo "      nameservers:"
    echo "        addresses: $DNS_LIST"
    if [[ -n "$SEARCH_DOMAIN" ]]; then
        echo "        search: [$SEARCH_DOMAIN]"
    fi
} > "$NETPLAN_FILE"

chmod 600 "$NETPLAN_FILE"

# ── Apply hostname if changed ─────────────────────────────────────────────
if [[ "$NEW_HOSTNAME" != "$CUR_HOSTNAME" ]]; then
    hostnamectl set-hostname "$NEW_HOSTNAME"
fi

# ── Apply network config ─────────────────────────────────────────────────
echo
echo "Applying network configuration..."
if netplan apply; then
    echo
    echo "=========================================="
    echo " Network configuration applied."
    echo " New address: $IP_ADDR/$CIDR"
    echo " If something looks wrong, run 'setup' again to fix it."
    echo "=========================================="
else
    echo
    echo "netplan apply failed. Restoring previous configuration..."
    LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/*.bak 2>/dev/null | head -1)
    if [[ -n "$LATEST_BACKUP" ]]; then
        cp -a "$LATEST_BACKUP" "$NETPLAN_FILE"
        netplan apply || true
        echo "Previous configuration restored. Run 'setup' to try again."
    fi
    exit 1
fi
