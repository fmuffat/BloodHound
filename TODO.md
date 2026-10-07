# TODO — roadmap

Priority: **P1** = needed soon, **P2** = strong value, **P3** = later.

---

## Updates — update button in the web interface (same design as sFlow Analytics)
- [ ] P1 **Version detection**: the backend reads the latest GitHub Release of
      `fmuffat/BloodHound` once a day (can be disabled in Settings) and stores
      latest version / notes / release page / last check / error. Nothing about
      the appliance is sent besides the HTTP request.
- [ ] P1 **Settings → System → Updates**: current version, latest version,
      release notes, "Check now", **"Install update"** (admin password
      confirmation), live progress (phase + last log lines), result.
- [ ] P1 **Host-side updater** `packaging/updater.sh` started by a systemd
      `.path` unit when the backend writes the requested version into
      `/opt/bloodhound/updates/request` (shared volume): the container only
      gives a version number, the host script
      - downloads the package from the official release, verifies its SHA-256,
        refuses downgrades;
      - backs up the configuration first (Redis dump, Graylog MongoDB, `.env`,
        certificates);
      - runs the new package's `install.sh` (upgrade in place, data kept);
      - reports progress in `updates/status.json`, full log in `updates/update.log`;
      - runs from a copy of itself (the upgrade replaces the script).
- [ ] P1 `install.sh` installs and enables the updater units (`bloodhound-update.path`
      / `.service`) and writes `updates/agent.json` (updater name, version, dir).
- [ ] P1 "New version available — reload" banner when an open tab still runs the
      previous UI after an update.
- [ ] P2 Offline update: upload a package from the browser (sites without Internet).
- [ ] Note: GitHub release assets are limited to 2 GB — the package is ~1.8 GB
      today. Keep it under the limit (or split images / pull third-party
      images from Docker Hub when online).

## Appliance / OVA
- [ ] P1 Test the OVA from the "Package and OVA" workflow on ESXi (first boot
      assistant, web login, syslog from a real AP, console menu, data disk).
- [ ] P1 Merge `remove-gdpr-wording` into `ova/appliance` and rebuild the OVA.
- [ ] P1 Merge `ova/appliance` (includes `fix/security-hardening`) into `main`
      once the OVA is validated; tag `v1.1.0` → GitHub Release.
- [ ] P2 Backup / restore from the console menu and the web interface
      (Redis + Graylog MongoDB + `.env` + certificates, optionally logs).
- [ ] P2 Remove the legacy template scripts once the OVA is validated:
      `factory-reset.sh`, `remove-dev-account.sh`, `setup.sh`, `clear-logs.sh`,
      `deploy/`, root `bloodhound-unleashed.service`.
- [ ] P2 Convert the dev VM to the appliance layout (`/opt/bloodhound`, all in
      Docker) so dev and production are identical.
- [ ] P3 GitHub Actions: move to Node 24 actions (`checkout@v5`, `upload-artifact@v5`…)
      and check the `ubuntu-latest` → Ubuntu 26 migration (2026-10-19).

## Security follow-ups
- [ ] P1 Purge the leaked Unleashed password from the git history
      (`git filter-repo --replace-text`, force-push all branches) — after
      changing it on the AP.
- [ ] P1 Dev VM: `sudo systemctl restart bloodhound-unleashed` (new worker
      code) and remove any password from the installed unit file.
- [ ] P2 Force a password change at first web sign-in on non-appliance installs
      (default `bloodhound` / `password`).
- [ ] P3 Replace remaining `KEYS` calls (`/options`, `/status`) with `SCAN`.

## Dev environment
- [ ] P1 Ruckus One → syslog server profile pointing to the dev VM
      (192.168.1.25, UDP 514) applied to the venue; check flows arrive.
