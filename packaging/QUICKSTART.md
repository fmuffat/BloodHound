# Bloodhound — Quick start

## VMware appliance (OVA)

1. Deploy the `.ova` on ESXi 7.0+ (**Create/Register VM → Deploy a virtual
   machine from an OVF or OVA file**). Thin provisioning is fine.
2. Power it on and open the **VM console**. A setup assistant asks for:
   network (DHCP or static), host name, time zone, NTP, the password of the
   `admin` system account and the password of the web user `bloodhound`.
   It then installs the application (about 5 minutes).
3. Open `https://<appliance-ip>/` and sign in as `bloodhound`.
4. **Settings → Users**: create accounts for your colleagues — *Administrator*,
   *Logs manager* or *Read-only* (search, investigations and exports only). Each new account
   gets a random password and chooses its own at first sign-in. A *Logs
   manager* can also erase a client's logs and manage retention, without
   access to the server administration.
5. **Settings**: choose the WiFi platform (Ruckus One, Unleashed or
   SmartZone) and enter its credentials.
6. Point the syslog of the APs / controller to `<appliance-ip>`, UDP 514.

Console menu (status, network, passwords, restart…): log in as `admin` on the
console or over SSH, then `sudo bloodhound-console`.

Unattended deployment: put the answers in `/etc/bloodhound/setup.conf` before
the first boot (template: `/etc/bloodhound/setup.conf.example`).

## Installation on an existing Linux server

Ubuntu 22.04/24.04 or Debian 12, x86_64, 8 GB RAM minimum:

    tar xzf bloodhound-*.tar.gz && cd bloodhound-*/ && sudo ./install.sh

Running it again with a newer package upgrades in place (data kept).

## Network exposure

| Port | Use |
|------|-----|
| 443/tcp, 80/tcp | Web interface and API (80 redirects to 443) |
| 514/udp, 514/tcp | Syslog from the APs |

Graylog, OpenSearch, Redis and the backend are not reachable from the LAN.

## Graylog

The Graylog UI only listens on the appliance itself. From your workstation:

    ssh -L 9000:localhost:9000 admin@<appliance-ip>

then open `http://localhost:9000` — user `admin`, password shown by
`sudo bloodhound-console` → *Graylog password*.

Logs are parsed by the pipeline rule `extract_ruckus_5tuple` (stream
"Ruckus APs - 5-tuple flows"), created at installation. If logs arrive but
searches return nothing, run `sudo /opt/bloodhound/graylog-bootstrap.sh`: it
recreates whatever is missing.

## Files

| Path | Content |
|------|---------|
| `/opt/bloodhound/.env` | Configuration and generated secrets |
| `/opt/bloodhound/compose.yml` | Docker Compose stack |
| `/var/log/bloodhound-setup.log` | First-boot setup log |

    cd /opt/bloodhound && sudo docker compose ps        # status
    cd /opt/bloodhound && sudo docker compose logs -f backend
