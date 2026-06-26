#!/bin/bash
# =============================================================================
# Bloodhound — Initial configuration script
# Run this script once after cloning/importing the VM on a new network.
# =============================================================================

set -e

GREEN='\033[0;32m'
ORANGE='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo "=================================================="
echo "  🐕 BLOODHOUND — Initial Configuration"
echo "=================================================="
echo ""

# ── Get current IP ────────────────────────────────────────────────────────────
CURRENT_IP=$(ip -4 addr show | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | grep -v 127.0.0.1 | head -1)
echo -e "Current IP detected: ${ORANGE}${CURRENT_IP}${NC}"
echo ""

# ── Ask for new IP ────────────────────────────────────────────────────────────
read -p "Enter the IP address for this VM [${CURRENT_IP}]: " NEW_IP
NEW_IP=${NEW_IP:-$CURRENT_IP}
echo ""

# ── Confirm ───────────────────────────────────────────────────────────────────
echo -e "Configuration will use IP: ${GREEN}${NEW_IP}${NC}"
read -p "Continue? [y/N] " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "Cancelled."
    exit 0
fi
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Update docker-compose.yml ─────────────────────────────────────────────────
echo "📝 Updating docker-compose.yml..."
sed -i "s|GRAYLOG_HTTP_EXTERNAL_URI:.*|GRAYLOG_HTTP_EXTERNAL_URI: \"http://${NEW_IP}:9000/\"|g" docker-compose.yml
echo -e "   ${GREEN}✓${NC} Graylog external URI updated"

# ── Update backend main.py (CORS) ─────────────────────────────────────────────
echo "📝 Updating backend CORS..."
sed -i "s|http://[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+:3000|http://${NEW_IP}:3000|g" backend/app/main.py
echo -e "   ${GREEN}✓${NC} CORS updated"

# ── Update netplan if IP changed ──────────────────────────────────────────────
if [ "$NEW_IP" != "$CURRENT_IP" ]; then
    echo "📝 Updating network configuration..."
    NETPLAN_FILE=$(ls /etc/netplan/*.yaml 2>/dev/null | head -1)
    if [ -n "$NETPLAN_FILE" ]; then
        sudo sed -i "s|${CURRENT_IP}/|${NEW_IP}/|g" "$NETPLAN_FILE"
        echo -e "   ${GREEN}✓${NC} Netplan updated — run 'sudo netplan apply' to apply"
    else
        echo -e "   ${ORANGE}⚠${NC} Netplan file not found — update IP manually"
    fi
fi

# ── Restart services ──────────────────────────────────────────────────────────
echo ""
echo "🔄 Restarting services..."
docker compose down
docker compose up -d mongodb opensearch graylog redis backend
echo -e "   ${GREEN}✓${NC} Services restarted"

# ── Restart frontend ──────────────────────────────────────────────────────────
echo "🔄 Restarting frontend..."
sudo systemctl restart bloodhound-frontend
echo -e "   ${GREEN}✓${NC} Frontend restarted"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "=================================================="
echo -e "  ${GREEN}✓ Bloodhound configured successfully!${NC}"
echo "=================================================="
echo ""
echo -e "  Interface  : ${ORANGE}http://${NEW_IP}:3000${NC}"
echo -e "  Graylog    : ${ORANGE}http://${NEW_IP}:9000${NC}  (admin/admin)"
echo -e "  Syslog UDP : ${ORANGE}${NEW_IP}:514${NC}"
echo ""
echo "  Next steps:"
echo "  1. Point your APs syslog to ${NEW_IP}:514"
echo "  2. Open Settings and configure your Ruckus One credentials"
echo "  3. Change Graylog admin password at http://${NEW_IP}:9000"
echo ""
if [ "$NEW_IP" != "$CURRENT_IP" ]; then
    echo -e "  ${ORANGE}⚠ Don't forget to run: sudo netplan apply${NC}"
    echo ""
fi
