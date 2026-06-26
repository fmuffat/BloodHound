# Bloodhound — Administration Guide

## Table of Contents
1. [First Boot — Initial Setup](#first-boot--initial-setup)
2. [Default Credentials](#default-credentials)
3. [Server Architecture](#server-architecture)
4. [Services Management](#services-management)
5. [Updating the Frontend](#updating-the-frontend)
6. [Resource Sizing (CPU / RAM)](#resource-sizing-cpu--ram)
7. [Disk Expansion](#disk-expansion)
8. [Backup & Restore](#backup--restore)
9. [Log Retention](#log-retention)
10. [Troubleshooting](#troubleshooting)

---

## First Boot — Initial Setup

After deploying the Bloodhound OVA, the appliance needs its network configured before it can be reached over the LAN.

### Step 1 — Connect via the VM console

Use your hypervisor's console (VMware, Hyper-V, etc.) — **not SSH** — since the
appliance does not yet have a known IP address on your network.

### Step 2 — Log in as `admin`

```
login: admin
password: password
```

You will immediately be prompted to **set a new password** — this is enforced
on first login and only happens once.

### Step 3 — Run the network setup wizard

Once logged in, type:

```bash
setup
```

The wizard will:
1. Detect the network interface and show the current configuration
2. Ask for the new **IP address**, **subnet mask (CIDR)**, **gateway**,
   **DNS server(s)**, and optionally a **search domain** and **hostname**
3. Show a summary and ask for confirmation before applying anything
4. Apply the configuration with `netplan apply`

```
$ setup
==========================================
 Bloodhound — Network Setup Wizard
==========================================
Interface detected : ens192
Current address     : 192.168.1.200/24
Current gateway     : 192.168.1.1
Current DNS         : 8.8.8.8
------------------------------------------
You can re-run this wizard anytime by typing: setup
------------------------------------------

IP address [192.168.1.200]: 10.0.5.20
Subnet mask (CIDR suffix, e.g. 24 for 255.255.255.0) [24]: 24
Gateway [192.168.1.1]: 10.0.5.1
Primary DNS [8.8.8.8]: 10.0.5.5
Secondary DNS (optional, press Enter to skip):
DNS search domain (optional, press Enter to skip): corp.local
Hostname [Bloodhound]: bloodhound-site1
```

If you make a mistake or need to change the network configuration later
(new VLAN, different DNS, etc.), just run `setup` again at any time — there
is no limit on how many times it can be run.

### Step 4 — Open Bloodhound in your browser

Once the network is configured, open `https://<the-ip-you-just-set>` from any
machine on the same network and continue with the
[Bloodhound Web Interface credentials](#default-credentials) below.

---

## Default Credentials

### Bloodhound Web Interface
- **URL**: `https://<server-ip>`
- **Username**: `bloodhound`
- **Password**: `password`
- ⚠️ Change immediately in **Settings → Password**

### Linux Admin Account
- **Username**: `admin`
- **Password**: `password`
- ⚠️ You will be forced to change this password the first time you log in
  (console or SSH) — see [First Boot](#first-boot--initial-setup)

### Graylog Web Interface
- **URL**: `http://<server-ip>:9000`
- **Username**: `admin`
- **Password**: `admin`

---

## Server Architecture

```
/opt/bloodhound/
└── syslog-platform/
    ├── docker-compose.yml      # Main stack definition
    ├── backend/                # FastAPI backend
    ├── frontend/               # React frontend
    │   └── dist/               # Production build, served by nginx
    ├── unleashed_worker.py     # Unleashed sync worker
    └── bloodhound-unleashed.service
```

The frontend is a **static production build** (`npm run build`), served
directly by nginx — there is no Node.js process running in production.
nginx terminates HTTPS, serves the static files for `/`, and reverse-proxies
`/api/` to the FastAPI backend on port 8000.

### System Users
| User | Purpose |
|------|---------|
| `bloodhound` | Service owner, runs systemd services |
| `admin` | Human admin, sudo + docker access |

### Services
| Service | Type | Description |
|---------|------|-------------|
| `nginx` | systemd | HTTPS termination, serves frontend, proxies `/api/` |
| `syslog_backend` | Docker | FastAPI backend (port 8000) |
| `syslog_graylog` | Docker | Log collection (port 9000, 514) |
| `syslog_opensearch` | Docker | Log indexing (port 9200) |
| `syslog_mongodb` | Docker | Graylog config storage |
| `syslog_redis` | Docker | Client cache |
| `bloodhound-unleashed` | systemd | Unleashed sync worker |
| `bloodhound-docker` | systemd | Ensures the Docker stack starts on boot |

> **Note:** `bloodhound-frontend` (the Vite dev server, port 3000) has been
> retired and is disabled. The frontend is now served as static files by
> nginx directly from `frontend/dist/`. See
> [Updating the Frontend](#updating-the-frontend) below.

---

## Services Management

### Check status
```bash
cd /opt/bloodhound/syslog-platform
docker compose ps
sudo systemctl status nginx
sudo systemctl status bloodhound-unleashed
```

### Restart all services
```bash
cd /opt/bloodhound/syslog-platform
docker compose restart
sudo systemctl reload nginx
sudo systemctl restart bloodhound-unleashed
```

### View logs
```bash
# Backend
docker compose logs backend --tail 50

# Unleashed worker
journalctl -u bloodhound-unleashed -n 50

# nginx (frontend + reverse proxy)
sudo journalctl -u nginx -n 50
sudo tail -f /var/log/nginx/error.log
```

---

## Updating the Frontend

The frontend is a static production build. There is **no hot-reload** in
production — after changing any React/JS/CSS source, you must rebuild:

```bash
cd /opt/bloodhound/syslog-platform/frontend
npm run build
```

This regenerates `frontend/dist/`. nginx serves the new files immediately —
no nginx reload or restart needed. Just refresh the browser (hard refresh
`Ctrl+Shift+R` if the browser cached old assets).

### nginx configuration
The site config lives at `/etc/nginx/sites-available/bloodhound`:
- `location /` — serves static files from `frontend/dist/`, with
  `try_files ... /index.html` fallback so React Router routes work on
  direct load / page refresh.
- `location /api/` — reverse-proxies to the FastAPI backend on `:8000`.

After editing this file, validate and reload:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

---

## Resource Sizing (CPU / RAM)

These are starting recommendations, extrapolated from observed resource
usage on a lab-scale deployment (a handful of APs) — not a formal load
test. Monitor `docker stats` and `free -h` after deployment and adjust if
needed; OpenSearch is the component most sensitive to AP/log volume.

| APs | Retention | vCPU | RAM |
|-----|-----------|------|-----|
| ≤10 (lab/demo) | 30 days | 4 | 8 GB |
| ≤20 | 30 days | 6 | 12 GB |
| ≤50 | 30 days | 8 | 16 GB |
| Any | 90 days | (same as above) | × 1.5 |

### Avoid sustained swap usage
The VM ships with 4 GB of swap as a safety net, not as usable capacity. If
swap usage grows during normal operation, add RAM rather than relying on
swap — OpenSearch and Graylog (both JVM-based) degrade sharply once the
JVM heap gets paged out, with GC pauses and search latency spikes.

### OpenSearch heap size
OpenSearch's JVM heap is fixed in `docker-compose.yml`:
```yaml
OPENSEARCH_JAVA_OPTS=-Xms1g -Xmx1g
```
For the ≤20 AP tier and above, increase this in proportion to the RAM
bump (e.g. `-Xms2g -Xmx2g` for ≤20 APs, `-Xms4g -Xmx4g` for ≤50 APs), then:
```bash
cd /opt/bloodhound/syslog-platform
docker compose up -d opensearch
```
As a rule of thumb, keep the OpenSearch heap at roughly 25–50% of total
VM RAM, leaving the rest for Graylog, MongoDB, the OS page cache, and the
other containers.

---

## Disk Expansion

The Bloodhound OVA ships with a **150 GB virtual disk, fully allocated to
the filesystem** (~146 GB usable after `/boot` and LVM overhead). Since the
disk is thin-provisioned, this costs no actual storage on your host until
data is written — there is no need to run any expansion steps for typical
deployments (see the sizing table below).

You only need the steps in this section if you've grown your VM's disk
**beyond** the default 150 GB (e.g. for a very large/long-retention site).
When the VM disk is expanded in VMware/Hyper-V, Ubuntu does **not**
automatically use the new space. Follow these steps:

### Step 1 — Expand the disk in VMware
1. Shut down the VM
2. Edit VM settings → Hard Disk → Expand to desired size
3. Power on the VM

### Step 2 — Extend the LVM partition
Connect via SSH and run:

```bash
# Resize the physical volume
sudo pvresize /dev/sda3

# Extend the logical volume to use all free space
sudo lvextend -l +100%FREE /dev/ubuntu-vg/ubuntu-lv

# Resize the filesystem
sudo resize2fs /dev/ubuntu-vg/ubuntu-lv

# Verify
df -h /
```

No reboot required. No services to stop.

### Expected output
```
/dev/mapper/ubuntu--vg-ubuntu--lv   XXG   YYG   ZZG  NN% /
```

### Disk usage monitoring
Bloodhound displays disk usage in:
- **Sidebar** — small bar indicator with percentage
- **Settings → System** — detailed disk stats + log count
- **Warning banner** — appears automatically at 80% usage (orange) or 90% (red)

### Recommended disk sizes
| APs | Retention | Recommended |
|-----|-----------|-------------|
| ≤10 | 30 days   | 50 GB |
| ≤20 | 30 days   | 100 GB |
| ≤50 | 30 days   | 200 GB |
| Any | 90 days   | × 3 |

---

## Backup & Restore

### Create backup
```bash
sudo tar czf /tmp/bloodhound-backup-$(date +%Y%m%d).tar.gz \
  --exclude=/opt/bloodhound/syslog-platform/frontend/node_modules \
  --exclude="*/__pycache__" \
  -C /opt/bloodhound syslog-platform
```

### Download backup via SFTP
```
sftp admin@<server-ip>
get /tmp/bloodhound-backup-YYYYMMDD.tar.gz
```

### Restore
```bash
sudo tar xzf bloodhound-backup-YYYYMMDD.tar.gz -C /opt/bloodhound/
sudo chown -R bloodhound:bloodhound /opt/bloodhound/syslog-platform
cd /opt/bloodhound/syslog-platform
docker compose up -d
sudo systemctl reload nginx
sudo systemctl restart bloodhound-unleashed
```

> If the restored backup predates the production frontend build, run
> `npm run build` in `frontend/` after restoring (see
> [Updating the Frontend](#updating-the-frontend)).

---

## Log Retention

Configure in **Settings → System → Log Retention**.

- Default: **30 days**
- Range: 1–180 days
- Purge runs automatically at **midnight** every day
- Manual purge available via "Purge now" button

### Manual purge via CLI
```bash
curl -X POST http://localhost:8000/api/v1/workers/purge-logs \
  -H "Cookie: bh_token=<your-token>"
```

---

## Troubleshooting

### Bloodhound not accessible
```bash
curl -sk -o /dev/null -w "%{http_code}" https://localhost/
sudo systemctl status nginx
sudo nginx -t
sudo systemctl reload nginx
```

### Wrong IP / network unreachable
If the appliance was just deployed (or moved to a new network/VLAN), connect
via the console and re-run the network wizard:

```bash
setup
```

It can be run as many times as needed and always shows a summary before
applying anything.

### Frontend shows a blank page or old version
The browser may have cached old static assets. Hard refresh
(`Ctrl+Shift+R`). If you just rebuilt the frontend and it still looks wrong:
```bash
cd /opt/bloodhound/syslog-platform/frontend
npm run build
sudo nginx -t && sudo systemctl reload nginx
```

### No logs appearing in search
```bash
# Check Graylog is receiving logs
sudo tcpdump -i ens192 -n udp port 514 -c 5

# Check OpenSearch
curl http://localhost:9200/_cluster/health

# Restart Graylog
cd /opt/bloodhound/syslog-platform
docker compose restart graylog
```

### Unleashed sync failing
```bash
journalctl -u bloodhound-unleashed -n 20
sudo systemctl restart bloodhound-unleashed
```

### Backend errors
```bash
cd /opt/bloodhound/syslog-platform
docker compose logs backend --tail 30
docker compose restart backend
```

### Opening encrypted ZIP exports
Bloodhound exports are encrypted with **AES-256**. Windows Explorer does not support this format.

Use one of these tools to open the ZIP:
- **7-Zip** (free): https://www.7-zip.org
- **WinRAR** (paid): https://www.rarlab.com
- On macOS/Linux: `7z x filename.zip`
