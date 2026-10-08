# Bloodhound

**WiFi network investigation platform for Ruckus infrastructure.**

Bloodhound ingests and enriches syslog/flow logs from Ruckus WiFi
deployments (Ruckus One, Unleashed, or SmartZone), letting you
investigate client activity across your wireless network: who connected,
when, from where, to what destination, and on which SSID/AP/venue.

Distributed as a ready-to-deploy **OVA virtual appliance** — no manual
install required.

---

## Features

- **Multi-platform support** — Ruckus One (cloud), Unleashed, and
  SmartZone, with mutually exclusive platform selection (one active at a
  time, all configurations preserved for easy switching)
- **Flow log investigation** — search by client, MAC, IP, SSID, AP,
  venue, protocol, port, and more, with a per-client investigation view
  showing full connection history
- **Client identity enrichment** — hostname, OS, device type, username,
  and guest/sponsor information automatically merged into each log entry
- **User accounts** — administrators, logs managers (search, export, erase
  client logs, retention/purge — no server administration) and read-only
  users, managed in Settings → Users
- **Per-client erasure** — delete all logs and history of one client,
  with password confirmation and an audit trail
- **Encrypted exports** — AES-256 password-protected ZIP, or CSV, for
  sharing investigation results
- **Self-service setup** — a first-boot console assistant configures
  network, host name, time zone and passwords, then installs everything
- **HTTPS by default** — self-signed certificate generated on first
  boot, with support for uploading your own
- **Configurable log retention** — automatic purge of logs older than N
  days, with live disk usage monitoring

## System Requirements

| APs | Retention | vCPU | RAM | Disk |
|-----|-----------|------|-----|------|
| ≤10 (lab/demo) | 30 days | 4 | 8 GB | 50 GB |
| ≤20 | 30 days | 6 | 12 GB | 100 GB |
| ≤50 | 30 days | 8 | 16 GB | 200 GB |
| Any | 90 days | (same as above) | × 1.5 | × 3 |

The OVA ships with a 150 GB thin-provisioned virtual disk by default,
which already covers most deployments without resizing.

Supported hypervisor: VMware ESXi 7.0+ / 8.0+.

## Getting Started

1. Download the latest `.ova` (GitHub Releases, or the artifacts of the
   "Package and OVA" workflow run).
2. Deploy it on your ESXi host (**Create/Register VM → Deploy a virtual
   machine from an OVF or OVA file**). Thin provisioning is fine.
3. Power on the VM and open its **console**. A setup assistant asks for the
   network (DHCP or static), host name, time zone, NTP, the password of the
   `admin` system account and the password of the web user `bloodhound`,
   then installs the application (about 5 minutes). No default password is
   left on the appliance.
4. Open `https://<appliance-ip>` and sign in as `bloodhound`.
5. Go to **Settings**, pick your WiFi platform (Ruckus One / Unleashed /
   SmartZone), and enter its credentials.
6. Point the syslog of the APs / controller to the appliance, UDP 514.

Console menu (status, network, passwords, Graylog access, restart…): log in
as `admin`, then `sudo bloodhound-console`.

Install on an existing Ubuntu/Debian server instead:
`tar xzf bloodhound-*.tar.gz && cd bloodhound-*/ && sudo ./install.sh`.
See [`packaging/QUICKSTART.md`](packaging/QUICKSTART.md).

Full administration guide (backups, disk expansion, troubleshooting,
service management): see [`ADMIN.md`](ADMIN.md).

## Architecture

```
Syslog (Ruckus APs) → Graylog → OpenSearch ← FastAPI backend ← nginx + React frontend
                                     ↑
                                   Redis (client cache, config)
                                     ↑
                      Ruckus One API / Unleashed (Selenium) / SmartZone API
```

- **Frontend**: React + Vite, served by nginx (HTTPS, reverse proxy to the API)
- **Backend**: FastAPI (Python), enriches raw flow logs with client
  identity from the active platform's API
- **Log pipeline**: Graylog (syslog ingestion + extraction rules) → OpenSearch
- **Cache/config**: Redis — also the live source of truth for
  platform credentials, so changes via the UI take effect immediately
- **Unleashed sync**: a Python worker container (headless Chromium, since
  Unleashed has no public REST API)
- **Everything runs in Docker Compose** (`/opt/bloodhound`); only 443/80
  (web) and 514 (syslog) are exposed on the network

## License

This project is **source-available**, not open source. You may
download, deploy, and use Bloodhound (including the OVA) for your own
purposes, but modification, reverse engineering, and redistribution are
not permitted. See [`LICENSE`](LICENSE) for the full terms.

No support is provided. Use at your own risk.

## Contact

For licensing inquiries beyond the scope of the included license,
contact the author directly.
