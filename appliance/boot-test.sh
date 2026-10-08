#!/usr/bin/env bash
# First boot of the appliance under QEMU/KVM, exactly as shipped in the OVA:
# the system disk (qcow2 from build-ova.sh) plus an empty data disk, with the
# first-boot assistant in unattended mode. Succeeds when the web interface
# accepts the password set by the assistant and refuses the default one.
#
#   appliance/boot-test.sh <work-dir>      # contains system.qcow2 (WORK of build-ova.sh)
#
# Needs root (or libguestfs + /dev/kvm access), qemu-system-x86, ~8 GB of free RAM.
# Env: BOOT_MEM_MB (default 8192), BOOT_TIMEOUT_MIN (default 30), BOOT_HTTPS_PORT (default 8443)
set -euo pipefail
WORK="${1:?usage: boot-test.sh <work-dir containing system.qcow2>}"
MEM="${BOOT_MEM_MB:-8192}" TIMEOUT_MIN="${BOOT_TIMEOUT_MIN:-30}" PORT="${BOOT_HTTPS_PORT:-8443}"
WEB_PW="Ci-web-12345"
[ -e /dev/kvm ] || { echo "boot test: no /dev/kvm (enable nested virtualization), skipped" >&2; exit 3; }

cd "$WORK"
rm -f boot-test.qcow2 data-test.qcow2 serial.log qemu.pid
qemu-img create -q -f qcow2 -b system.qcow2 -F qcow2 boot-test.qcow2
qemu-img create -q -f qcow2 data-test.qcow2 150G
# Answers for the unattended first boot (setup.conf.example)
answers="$(mktemp -d)"
printf '%s\n' NETWORK=dhcp HOSTNAME_NEW=bh-test TIMEZONE=Europe/Paris \
  "ADMIN_PASSWORD='Ci-admin-12345'" "WEB_PASSWORD='$WEB_PW'" > "$answers/setup.conf"
virt-customize -q -a boot-test.qcow2 --no-network --copy-in "$answers/setup.conf:/etc/bloodhound"
rm -rf "$answers"

cleanup() {
  [ -f qemu.pid ] && kill "$(cat qemu.pid)" 2>/dev/null && sleep 3 || true
}
trap cleanup EXIT

qemu-system-x86_64 -enable-kvm -cpu host -m "$MEM" -smp 4 \
  -drive file=boot-test.qcow2,if=virtio -drive file=data-test.qcow2,if=virtio \
  -nic "user,model=virtio-net-pci,hostfwd=tcp::$PORT-:443" \
  -display none -serial file:serial.log -daemonize -pidfile qemu.pid

login() {
  curl -sk -o /dev/null -w '%{http_code}' -H 'Content-Type: application/json' \
    -d "{\"username\":\"bloodhound\",\"password\":\"$1\"}" "https://127.0.0.1:$PORT/api/v1/auth/login" || true
}
for i in $(seq 1 $((TIMEOUT_MIN * 6))); do
  code="$(login "$WEB_PW")"
  if [ "$code" = 200 ]; then echo "boot test: signed in with the password set by the assistant after $((i * 10)) s"; break; fi
  if [ "$i" = $((TIMEOUT_MIN * 6)) ]; then
    echo "boot test: appliance not ready after $TIMEOUT_MIN min (last HTTP $code)" >&2
    cleanup; trap - EXIT
    tail -40 serial.log >&2 || true
    virt-cat -a boot-test.qcow2 /var/log/bloodhound-setup.log 2>/dev/null | tail -60 >&2 || true
    exit 1
  fi
  sleep 10
done
code="$(login password)"
[ "$code" = 401 ] || { echo "boot test: default web password still accepted ($code)" >&2; exit 1; }
echo "boot test: default password refused — OK"
