#!/usr/bin/env bash
# Builds the offline installation package (all Docker images included):
#
#   scripts/build-package.sh [version]      # default: content of ./VERSION
#   -> dist/bloodhound-<version>.tar.gz  (+ .sha256)
#
# Requires Docker. Installs with `sudo ./install.sh` (see packaging/QUICKSTART.md).
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION="${1:-$(cat VERSION)}"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][A-Za-z0-9.]+)?$ ]] || { echo "bad version: $VERSION" >&2; exit 1; }
NAME="bloodhound-$VERSION"
OUT="dist/$NAME"
THIRD_PARTY=(mongo:7 opensearchproject/opensearch:1.3.14 graylog/graylog:7.1.2 redis:7-alpine)

echo "==> Building images $VERSION"
docker build -t "bloodhound/backend:$VERSION" backend
docker build -t "bloodhound/web:$VERSION" frontend
for img in "${THIRD_PARTY[@]}"; do
  docker image inspect "$img" >/dev/null 2>&1 || docker pull -q "$img"
done

echo "==> Assembling $OUT"
rm -rf "$OUT" && mkdir -p "$OUT/graylog"
cp packaging/install.sh packaging/graylog-bootstrap.sh packaging/compose.yml packaging/QUICKSTART.md LICENSE "$OUT/"
cp packaging/graylog/* "$OUT/graylog/"   # Ruckus parsing rule (pipeline source)
echo "$VERSION" > "$OUT/VERSION"
chmod 755 "$OUT"/*.sh

echo "==> Saving images"
docker save "${THIRD_PARTY[@]}" "bloodhound/backend:$VERSION" "bloodhound/web:$VERSION" \
  | gzip -1 > "$OUT/images.tar.gz"

tar czf "dist/$NAME.tar.gz" -C dist "$NAME"
(cd dist && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
rm -rf "$OUT"
echo "==> dist/$NAME.tar.gz ($(du -h "dist/$NAME.tar.gz" | cut -f1))"
