#!/usr/bin/env bash
# Builds the OVA on a Linux host of the lab — faster to iterate on than the
# GitHub workflow, and the OVA ends up on the LAN, ready to deploy.
#
# From your PC (Git Bash): sync the working tree to the build host over SSH,
# build there, print where the OVA is.
#   scripts/build-ova-local.sh --host fmuffat@192.168.1.25 --key ~/.ssh/id_bloodhound
#   scripts/build-ova-local.sh --host ... --fetch            # also copy the OVA back here (2.5 GB)
#
# On the build host itself (needs Docker only — no sudo, no libguestfs):
#   scripts/build-ova-local.sh
#
# Options:
#   --version X.Y.Z    default: <VERSION>-local.<date>
#   --boot-test        boot the image under QEMU/KVM and sign in (needs /dev/kvm
#                      and ~8 GB free RAM on the build host; skipped otherwise)
#   --fetch [dir]      (with --host) download the OVA to dir (default: ./dist)
# Env: BH_SSH (ssh command, default "ssh"), BH_BUILD_DIR (on the build host,
#      default ~/bloodhound-build)
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="" KEY="" VERSION="" BOOT=0 FETCH=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    --version) VERSION="$2"; shift 2 ;;
    --boot-test) BOOT=1; shift ;;
    --fetch) FETCH="${2:-dist}"; [ $# -ge 2 ] && [[ "$2" != --* ]] && shift 2 || { FETCH=dist; shift; } ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
VERSION="${VERSION:-$(tr -d '[:space:]' < VERSION)-local.$(date +%Y%m%d%H%M)}"
say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }

# ── Mode 1: from the PC, drive a remote build host ──────────────────────────
if [ -n "$HOST" ]; then
  SSH=(${BH_SSH:-ssh} -o BatchMode=yes)
  [ -n "$KEY" ] && SSH+=(-i "$KEY")
  # Working tree including uncommitted changes to tracked files; LF endings
  # (a plain `git archive` on Windows converts to CRLF and breaks the scripts).
  REF="$(git stash create)"; REF="${REF:-HEAD}"
  [ -n "$(git status --porcelain --untracked-files=normal | grep '^??' || true)" ] \
    && echo "note: untracked files are not sent (git add them to include them)"
  say "Sending the sources to $HOST"
  git -c core.autocrlf=false -c core.eol=lf archive "$REF" \
    | "${SSH[@]}" "$HOST" 'D=${BH_BUILD_DIR:-$HOME/bloodhound-build}/src; rm -rf "$D" && mkdir -p "$D" && tar xf - -C "$D"'
  say "Building $VERSION on $HOST"
  "${SSH[@]}" "$HOST" "cd \${BH_BUILD_DIR:-\$HOME/bloodhound-build}/src && bash scripts/build-ova-local.sh --version '$VERSION' $( [ "$BOOT" = 1 ] && echo --boot-test )"
  if [ -n "$FETCH" ]; then
    mkdir -p "$FETCH"
    say "Downloading the OVA to $FETCH/"
    for f in "bloodhound-$VERSION.ova" "bloodhound-$VERSION.ova.sha256"; do
      "${SSH[@]}" "$HOST" "cat \${BH_BUILD_DIR:-\$HOME/bloodhound-build}/dist/$f" > "$FETCH/$f"
    done
    (cd "$FETCH" && sha256sum -c "bloodhound-$VERSION.ova.sha256")
  fi
  exit 0
fi

# ── Mode 2: on the build host ────────────────────────────────────────────────
command -v docker >/dev/null || { echo "Docker is required on the build host" >&2; exit 1; }
SRC="$(pwd)"
BASE="${BH_BUILD_DIR:-$HOME/bloodhound-build}"
WORK="$BASE/work" OUT="$BASE/dist"
mkdir -p "$WORK" "$OUT"

say "Package $VERSION (Docker images included)"
bash scripts/build-package.sh "$VERSION"
mv -f "dist/bloodhound-$VERSION.tar.gz" "dist/bloodhound-$VERSION.tar.gz.sha256" "$OUT/"

say "OVA builder image"
docker build -q -t bloodhound/ova-builder:1 -f appliance/builder.Dockerfile appliance >/dev/null

KVM=()
if [ -e /dev/kvm ]; then KVM=(--device /dev/kvm)
else say "no /dev/kvm: image customization runs in software emulation (slower)"; fi
RUN_BOOT=0
if [ "$BOOT" = 1 ]; then
  free_mb=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
  if [ -z "${KVM[*]}" ]; then
    say "boot test skipped: no /dev/kvm (ESXi: enable \"Expose hardware assisted virtualization\" on this VM)"
  elif [ "$free_mb" -lt 7000 ]; then
    say "boot test skipped: only ${free_mb} MB of free RAM (needs ~8 GB)"
  else RUN_BOOT=1; fi
fi

say "OVA (10–30 min)"
# Same paths inside and outside the container: build-ova.sh starts a helper
# container through the host's Docker daemon with -v "$WORK/...".
docker run --rm --privileged "${KVM[@]}" \
  -v /var/run/docker.sock:/var/run/docker.sock -v "$SRC:$SRC" -v "$BASE:$BASE" -w "$SRC" \
  -e WORK="$WORK" -e OUT="$OUT" -e RUN_BOOT="$RUN_BOOT" -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  bloodhound/ova-builder:1 bash -c '
    set -e
    trap "chown -R $HOST_UID:$HOST_GID \"$OUT\" \"$WORK\" 2>/dev/null || true" EXIT
    bash appliance/build-ova.sh "$OUT/bloodhound-'"$VERSION"'.tar.gz"
    if [ "$RUN_BOOT" = 1 ]; then bash appliance/boot-test.sh "$WORK"; fi'

OVA="$OUT/bloodhound-$VERSION.ova"
echo
echo "  ────────────────────────────────────────────────────────────────────"
echo "   OVA       $OVA ($(du -h "$OVA" | cut -f1))"
echo "   SHA-256   $(cut -d' ' -f1 "$OVA.sha256")"
echo "   Package   $OUT/bloodhound-$VERSION.tar.gz"
[ "$RUN_BOOT" = 1 ] && echo "   Boot test passed" || echo "   Boot test not run"
echo "  ────────────────────────────────────────────────────────────────────"
