#!/bin/bash
#
# Bloodhound — Remove the developer account (fmuffat) before shipping the OVA.
#
# IMPORTANT: run this as a DIFFERENT user than fmuffat (e.g. admin, from the
# console), and make sure fmuffat is fully logged out first. A user cannot
# delete its own active account.
#
set -euo pipefail

TARGET_USER="fmuffat"

if [[ "$EUID" -ne 0 ]]; then
    exec sudo "$0" "$@"
fi

if [[ "$(whoami)" == "$TARGET_USER" ]] || [[ "${SUDO_USER:-}" == "$TARGET_USER" ]]; then
    echo "Refusing to run: you appear to be logged in as $TARGET_USER."
    echo "Log in as a different user (e.g. admin) and run this again."
    exit 1
fi

if ! id "$TARGET_USER" &>/dev/null; then
    echo "User $TARGET_USER does not exist — nothing to do."
    exit 0
fi

# Check for an actual logged-in session (who/utmp), not just any process
# whose UID happens to match. Checking by UID alone (e.g. via `pgrep -u`)
# produces false positives: Docker containers commonly run their internal
# processes as UID 1000 by default, which can coincidentally match this
# dev account's host UID even though those processes have nothing to do
# with it and will never go away — that would permanently block this
# script from ever running.
ACTIVE_SESSIONS=$(who | awk -v u="$TARGET_USER" '$1 == u' || true)
if [[ -n "$ACTIVE_SESSIONS" ]]; then
    echo "User $TARGET_USER still has an active login session:"
    echo "$ACTIVE_SESSIONS"
    echo
    echo "Make sure $TARGET_USER is fully logged out (close all SSH sessions),"
    echo "then run this script again."
    exit 1
fi

echo "=========================================="
echo " This will permanently delete the account"
echo " '$TARGET_USER' and its home directory."
echo "=========================================="
read -rp "Type DELETE to confirm: " CONFIRM
if [[ "$CONFIRM" != "DELETE" ]]; then
    echo "Aborted. Nothing was changed."
    exit 0
fi

userdel -r "$TARGET_USER"
echo
echo "Account '$TARGET_USER' and its home directory have been removed."
echo "Project files previously owned by $TARGET_USER now show a numeric"
echo "UID in 'ls -l' — this is cosmetic only, permissions still apply normally."
