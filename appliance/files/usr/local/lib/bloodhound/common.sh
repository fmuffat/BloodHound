# Shared helpers of the Bloodhound appliance console tools (sourced, bash).

BH_DIR=/opt/bloodhound
BH_PKG=/opt/bloodhound-package
BH_STATE=/etc/bloodhound
TITLE="Bloodhound appliance"
LOG=/var/log/bloodhound-setup.log

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*" >>"$LOG"; }

# whiptail helpers (answers on stdout; non-zero exit when cancelled)
ask()    { whiptail --title "$TITLE" --inputbox "$1" 10 72 "${2:-}" 3>&1 1>&2 2>&3; }
askpw()  { whiptail --title "$TITLE" --passwordbox "$1" 10 72 3>&1 1>&2 2>&3; }
info()   { whiptail --title "$TITLE" --msgbox "$1" "${2:-12}" 76; }
yesno()  { whiptail --title "$TITLE" --yesno "$1" 10 72; }

primary_nic() {
  ls /sys/class/net | grep -E '^(en|eth)' | head -1
}

current_ip() {
  ip -4 -o addr show scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1
}

valid_ip()   { [[ "$1" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] && ! [[ "$1" =~ (^|\.)(25[6-9]|2[6-9][0-9]|[3-9][0-9]{2}) ]]; }
valid_cidr() { [[ "$1" =~ ^(.+)/([0-9]|[12][0-9]|3[0-2])$ ]] && valid_ip "${BASH_REMATCH[1]}"; }

# Asks for a password twice, with a minimum length. Prints it on stdout.
ask_new_password() {
  local label="$1" min="$2" p1 p2
  while :; do
    p1=$(askpw "$label ($min characters minimum)") || return 1
    p2=$(askpw "Same password again") || return 1
    [ "$p1" = "$p2" ] && [ "${#p1}" -ge "$min" ] && { printf '%s' "$p1"; return 0; }
    # >&2: whiptail draws the message box on stdout, which is captured as the
    # password by the caller ($(ask_new_password ...)) — it must go to the screen.
    info "The passwords differ or are shorter than $min characters." 8 >&2
  done
}

# Network: DHCP or static, written to /etc/netplan/50-bloodhound.yaml.
configure_network() {
  local nic mode addr gw dns
  nic="$(primary_nic)"
  mode=$(whiptail --title "$TITLE" --menu "Network configuration of $nic" 12 72 2 \
    dhcp "Automatic (DHCP)" static "Static address" 3>&1 1>&2 2>&3) || return 1
  if [ "$mode" = static ]; then
    while :; do addr=$(ask "IP address with prefix length, e.g. 192.168.10.50/24") || return 1; valid_cidr "$addr" && break; info "Invalid address: $addr" 8; done
    while :; do gw=$(ask "Default gateway, e.g. 192.168.10.1") || return 1; valid_ip "$gw" && break; info "Invalid gateway: $gw" 8; done
    dns=$(ask "DNS servers (comma separated)" "$gw") || return 1
    cat >/etc/netplan/50-bloodhound.yaml <<EOF
network:
  version: 2
  ethernets:
    $nic:
      dhcp4: false
      addresses: [$addr]
      routes: [{to: default, via: $gw}]
      nameservers: {addresses: [$(echo "$dns" | tr -d ' ')]}
EOF
  else
    cat >/etc/netplan/50-bloodhound.yaml <<EOF
network:
  version: 2
  ethernets:
    $nic:
      dhcp4: true
EOF
  fi
  chmod 600 /etc/netplan/50-bloodhound.yaml
  rm -f /etc/netplan/01-bloodhound-dhcp.yaml
  netplan apply >>"$LOG" 2>&1
  sleep 3
  log "network: $mode ${addr:-} gw=${gw:-} dns=${dns:-} -> $(current_ip)"
}

# Sets the password of the web user "bloodhound" (and closes its sessions).
set_web_password() {
  local pw="$1"
  (cd "$BH_DIR" && BH_NEWPW="$pw" docker compose exec -T -e BH_NEWPW backend python -c '
import asyncio, os
from app.services.auth import init_secret, ensure_user_password
async def main():
    await init_secret()
    await ensure_user_password("bloodhound", os.environ["BH_NEWPW"])
asyncio.run(main())
' && docker compose restart backend) >>"$LOG" 2>&1
}

# Certificate names: regenerate the self-signed certificate so that it
# contains the current address (only if it is still the self-signed one).
refresh_certificate() {
  [ -f "$BH_DIR/.env" ] || return 0
  sed -i "s/^TLS_EXTRA_SAN=.*/TLS_EXTRA_SAN=IP:$(current_ip)/" "$BH_DIR/.env"
  sed -i "s/^TLS_COMMON_NAME=.*/TLS_COMMON_NAME=$(hostname)/" "$BH_DIR/.env"
  (cd "$BH_DIR" \
    && if docker compose exec -T web sh -c 'openssl x509 -in /etc/nginx/certs/bloodhound.crt -noout -subject' 2>/dev/null | grep -q 'O *= *Bloodhound'; then
         docker compose exec -T web sh -c 'rm -f /etc/nginx/certs/bloodhound.crt /etc/nginx/certs/bloodhound.key'
         docker compose up -d --force-recreate web
       fi) >>"$LOG" 2>&1 || true
}
