# TODO — roadmap

Priority: **P1** = needed soon, **P2** = strong value, **P3** = later.

Done in 1.1.0 (released 2026-10-08): security hardening, appliance OVA (first-boot
assistant, console menu, data disk, local build, CI boot test), accounts with roles
(administrator / logs manager / read-only), sign-in log, light theme, modern
Settings, OVA under GitHub's 2 GiB limit, UI screenshots with demo mode.

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
- [ ] Note: GitHub release assets are limited to 2 GiB. In 1.1.0 the OVA is
      1.86 GiB and the package 1.25 GiB (images: uncompressed layers + zstd,
      `scripts/repack-images.py`). Watch the OVA size on every release — little
      margin left (next steps if needed: slimmer OpenSearch/Graylog images,
      Chromium only in the Unleashed worker image).

## Appliance / OVA
- [x] P1 First-boot assistant, console, web, syslog validated on ESXi
      (OVA 1.1.0-local.2, 2026-10-08).
- [ ] P1 Test the published `v1.1.0` OVA on ESXi (zstd images, snapd removed,
      sign-in log, new picture).
- [x] P1 Merge into `main`, tag `v1.1.0`, GitHub Release (OVA + package),
      tag workflow verification passed (package, e2e, KVM boot test).
- [ ] P2 Backup / restore from the console menu and the web interface
      (Redis + Graylog MongoDB + `.env` + certificates, optionally logs).
- [ ] P2 Remove the legacy template scripts now that the OVA is validated:
      `factory-reset.sh`, `remove-dev-account.sh`, `setup.sh`, `clear-logs.sh`,
      `deploy/`, root `bloodhound-unleashed.service`.
- [ ] P2 Convert the dev VM to the appliance layout (`/opt/bloodhound`, all in
      Docker) so dev and production are identical.
- [ ] P2 Enable nested virtualization (and ~16 GB RAM) on the build VM so
      `build-ova-local.sh --boot-test` can boot-test the OVA locally.
- [ ] P3 GitHub Actions: move to Node 24 actions (`checkout@v5`, `upload-artifact@v5`…)
      and check the `ubuntu-latest` → Ubuntu 26 migration (2026-10-19).

## User interface
- [ ] P2 Flow logs table: the timestamp column is truncated ("11:39:1…").
- [ ] P2 Investigation page: "First / Last seen" are shown in UTC while the
      logs use the timezone preference (2 h apart in summer).

## Security follow-ups
- [ ] P1 Purge the leaked Unleashed password from the git history
      (`git filter-repo --replace-text`, then force-push `main` and the tags)
      — after changing it on the AP. Only `main` is left on GitHub.
- [ ] P1 Dev VM: `sudo systemctl restart bloodhound-unleashed` (new worker
      code) and remove any password from the installed unit file (the
      `sysadmin` console account now has sudo).
- [x] P2 Force a password change at first web sign-in on non-appliance installs
      (default `bloodhound` / `password`).
- [x] P1 Accounts with roles (Settings → Users), as in sFlow Analytics: administrator,
      logs manager (erase / retention / purge, no server administration), read-only.
- [x] P2 Sign-in log (successes / failures, per account) in Settings → Users & sign-ins, kept 180 days.
- [ ] P3 Replace remaining `KEYS` calls (`/options`, `/status`) with `SCAN`.

## Dev environment
- [ ] P1 Ruckus One → syslog server profile pointing to the dev VM
      (192.168.1.25, UDP 514) applied to the venue; check flows arrive.
- [x] Clock: chrony steps the time whenever it drifts (the VM was ~3 h ahead).
- [x] Console rescue account `sysadmin` (sudo) on the dev VMs.
