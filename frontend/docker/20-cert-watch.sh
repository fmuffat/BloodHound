#!/bin/sh
# Reloads nginx (no downtime) when the certificate files change, e.g. after
# an upload from Settings → SSL. An invalid certificate is not loaded.
(
  last=""
  while sleep 10; do
    cur="$(cat /etc/nginx/certs/bloodhound.crt /etc/nginx/certs/bloodhound.key 2>/dev/null | md5sum)"
    if [ -n "$last" ] && [ "$cur" != "$last" ]; then
      if nginx -t -q 2>/dev/null; then
        nginx -s reload && echo "cert-watch: certificate changed, nginx reloaded"
      else
        echo "cert-watch: new certificate rejected by nginx -t, previous one kept"
      fi
    fi
    last="$cur"
  done
) &
