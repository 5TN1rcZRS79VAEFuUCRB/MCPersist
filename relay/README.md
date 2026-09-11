# MCPersist relay (self-hosted)

Runs on a small always-on Linux VPS you control. Lets multiple MCPersist users share
one relay, each reachable at their own subdomain on the standard Minecraft port
(`25565`) - no port typed by players - fully self-hosted on your own domain.

## Getting a VPS

Any small Ubuntu/Debian VPS works. **Oracle Cloud's Always Free tier** (an ARM Ampere
instance, up to 4 OCPUs / 24GB RAM, 10TB/month egress) is a genuinely free option at
this scale - the relay itself barely uses any CPU/RAM, it's just forwarding bytes.

## DNS

1. Point a wildcard record at your VPS's public IP: `*.tunnel.yourdomain.com -> VPS_IP`
   (pick whatever subdomain root you like - this doc uses `tunnel.yourdomain.com`).
   Every user you add (`alice`, `bob`, ...) then automatically resolves, with no
   per-user DNS changes.
2. The VPS's bare IP address is all `configure-relay` needs on the client side for
   the control/data connections - no DNS required for that part.

## Firewall

Allow inbound TCP on:
- `25565` - the public Minecraft port, open to everyone (this is what players connect to).
- `7000` (control) and `7001` (data) - only MCPersist clients need these, but it's
  simplest to just open them too. Access control for v1 is the per-user token plus
  unguessable per-connection UUIDs, not network-level restriction - fine for a small
  trusted beta, not hardened for a large public service (no TLS yet either).

`relay_server.py` also caps concurrent raw connections per source IP
(`MAX_CONNECTIONS_PER_IP`) and simultaneous in-flight join attempts against any one
backend (`MAX_PENDING_PER_SUBDOMAIN`), so a single source can't flood the relay or
hammer one person's tunnel client with fake connection attempts - real multiplayer use
never gets close to either limit. Still no TLS and still no per-user bandwidth caps.

## Deploy

```bash
sudo mkdir -p /opt/mcrelay
sudo useradd -r -s /usr/sbin/nologin mcrelay || true
# copy mc_handshake.py, users.py, auto_assignments.py, relay_server.py, admin_cli.py
# into /opt/mcrelay
sudo chown -R mcrelay:mcrelay /opt/mcrelay

sudo cp mcrelay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mcrelay
sudo systemctl status mcrelay
```

`users.json` (reserved subdomains) and `auto_assignments.json` (auto-registered ones,
persisted per source IP) get created next to `admin_cli.py`/`relay_server.py`. Back
them up if you care about not re-issuing tokens or losing auto-assigned addresses.

## Adding a user

```bash
cd /opt/mcrelay
python3 admin_cli.py add-user alice
```

Prints a subdomain + token - send both to that person. They run `run.bat
configure-relay` on their MCPersist install and paste them in, along with your VPS's
IP and the ports above.

`remove-user <subdomain>` and `list-users` are also available.

## Logs / troubleshooting

`relay_server.py` logs to stdout - under systemd, `journalctl -u mcrelay -f`. A
"no backend registered for ..." line means a player connected to a hostname whose
subdomain isn't currently registered/online; "didn't respond in time" means the
client's MCPersist tunnel isn't actually connected right now (their PC/server is off,
or their tunnel process crashed).
