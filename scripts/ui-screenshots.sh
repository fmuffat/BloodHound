#!/usr/bin/env bash
# Screenshots of every UI page (headless Chromium in Docker) into ./screenshots.
# Run on the Bloodhound host (dev VM or appliance) from a copy of the repository.
#
# Signs in with a dedicated "ui-test" account (never a real administrator),
# created once with a random password kept outside the repository
# ($UI_TEST_CREDENTIALS, default ~/.config/bloodhound-ui-test, mode 600).
# Its role decides what is captured: admin = every Settings tab (default),
# manager or viewer = only what that role sees. Change it in Settings → Users.
#
# Env: BH_STACK_DIR  directory of the running stack (docker compose)
#                    default /opt/bloodhound/syslog-platform, else /opt/bloodhound
#      SCHEME=light|dark   SCALE=1|2 (2 = sharper, for slides)   BASE_URL
#      DEMO=1   presentation mode: real names, MACs, SSIDs, venues... replaced
#               by consistent fictitious values in the browser (server data
#               untouched), leak check, plus a simulated sponsored guest
#
# Without DEMO=1 the captures show real data (client names, MACs, guest
# e-mails...): use DEMO=1 for slides.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="$PWD/screenshots"
mkdir -p "$OUT"

STACK="${BH_STACK_DIR:-}"
if [ -z "$STACK" ]; then
  for d in /opt/bloodhound/syslog-platform /opt/bloodhound; do
    [ -f "$d/docker-compose.yml" ] || [ -f "$d/compose.yml" ] && { STACK="$d"; break; }
  done
fi
[ -n "$STACK" ] || { echo "Bloodhound stack not found (set BH_STACK_DIR)" >&2; exit 1; }

CRED="${UI_TEST_CREDENTIALS:-$HOME/.config/bloodhound-ui-test}"
if [ ! -s "$CRED" ]; then
  mkdir -p "$(dirname "$CRED")"
  PW="$(head -c 64 /dev/urandom | base64 | tr -dc A-Za-z0-9 | cut -c 1-20)"
  # Password through stdin: never on a command line or in the repository
  printf '%s' "$PW" | (cd "$STACK" && docker compose exec -T backend python -c '
import asyncio, sys
from app.services import auth
pw = sys.stdin.read()
async def main():
    await auth.init_secret()
    if await auth.get_user("ui-test"):
        await auth.set_password("ui-test", pw, must_change=False)
    else:
        await auth.create_user("ui-test", "admin", pw, must_change=False)
asyncio.run(main())
' 2>/dev/null)
  umask 077
  printf 'ui-test\n%s\n' "$PW" > "$CRED"
  echo "account ui-test ready (credentials in $CRED)"
fi
UI_USER="$(sed -n 1p "$CRED")"
UI_PASSWORD="$(sed -n 2p "$CRED")"

rm -f "$OUT"/*.png
docker run --rm --network host --init \
  -e SCHEME="${SCHEME:-light}" -e SCALE="${SCALE:-1}" -e DEMO="${DEMO:-0}" \
  -e UI_USER="$UI_USER" -e UI_PASSWORD="$UI_PASSWORD" \
  -v "$PWD/scripts/ui-screenshots.mjs:/work/ui-screenshots.mjs:ro" -v "$OUT:/out" \
  -v bloodhound-playwright-cache:/root/.cache -w /work node:22-bookworm \
  sh -c "npm i -s --no-audit --no-fund playwright@1 >/dev/null 2>&1 && npx -y playwright install --with-deps chromium >/dev/null 2>&1 && node ui-screenshots.mjs ${BASE_URL:-https://127.0.0.1} /out; rc=\$?; chown -R $(id -u):$(id -g) /out; exit \$rc"
echo "screenshots in $OUT"
