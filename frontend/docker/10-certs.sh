#!/bin/sh
# Generates a self-signed certificate on first start if none is present.
# It can be replaced from Settings → SSL (the backend writes into the same
# volume; 20-cert-watch.sh reloads nginx on change).
set -eu
DIR=/etc/nginx/certs
if [ ! -s "$DIR/bloodhound.crt" ] || [ ! -s "$DIR/bloodhound.key" ]; then
  mkdir -p "$DIR"
  CN="${TLS_COMMON_NAME:-bloodhound}"
  SAN="DNS:${CN},DNS:localhost${TLS_EXTRA_SAN:+,$TLS_EXTRA_SAN}"
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
    -keyout "$DIR/bloodhound.key" -out "$DIR/bloodhound.crt" \
    -subj "/O=Bloodhound/CN=${CN}" -addext "subjectAltName=${SAN}" >/dev/null 2>&1
  echo "10-certs.sh: generated self-signed certificate for ${SAN}"
fi
chmod 600 "$DIR/bloodhound.key"
