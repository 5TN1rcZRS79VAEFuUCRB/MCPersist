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
2. Also point a dedicated hostname at the same IP for the control/data channels
   specifically - e.g. `relay.tunnel.yourdomain.com -> VPS_IP` (a plain A record, or
   just let the wildcard above cover it if the name fits under it already). This is
   what `configure-relay`'s `relay_host` needs now - a real hostname, not a bare IP,
   since the control/data channels are TLS (see below) and certificate verification
   needs a hostname to check the cert against.

## TLS (required)

The control/data channels carry per-user tokens and are open to the whole internet,
so `relay_server.py` requires a real TLS certificate for them - it won't start
without one. The public Minecraft port (`:25565`) is deliberately NOT wrapped in TLS -
that's raw Minecraft protocol traffic to vanilla clients, which have no concept of
TLS at the transport layer.

```bash
sudo apt-get install -y certbot
# Needs port 80 reachable from the internet (see Firewall below) - certbot's own
# standalone webserver answers the ACME HTTP-01 challenge on it, briefly, both now
# and on every future renewal.
sudo certbot certonly --standalone -d relay.tunnel.yourdomain.com \
    --non-interactive --agree-tos -m you@example.com --no-eff-email
```

Certbot installs its own renewal timer automatically (`systemctl status certbot.timer`).
The cert it issues lands in `/etc/letsencrypt/live/relay.tunnel.yourdomain.com/`,
readable only by root - but `mcrelay.service` runs as the unprivileged `mcrelay` user
(see Deploy below), so a renewal hook is needed to copy the fresh cert somewhere that
user can actually read, every time it renews:

```bash
sudo tee /etc/letsencrypt/renewal-hooks/deploy/mcrelay-cert-sync.sh > /dev/null << 'EOF'
#!/bin/bash
set -e
CERT_DIR=/opt/mcrelay/certs
SRC_DIR=/etc/letsencrypt/live/relay.tunnel.yourdomain.com
mkdir -p "$CERT_DIR"
cp "$SRC_DIR/fullchain.pem" "$CERT_DIR/fullchain.pem"
cp "$SRC_DIR/privkey.pem" "$CERT_DIR/privkey.pem"
chown mcrelay:mcrelay "$CERT_DIR/fullchain.pem" "$CERT_DIR/privkey.pem"
chmod 644 "$CERT_DIR/fullchain.pem"
chmod 600 "$CERT_DIR/privkey.pem"
systemctl restart mcrelay
EOF
sudo chmod +x /etc/letsencrypt/renewal-hooks/deploy/mcrelay-cert-sync.sh
sudo /etc/letsencrypt/renewal-hooks/deploy/mcrelay-cert-sync.sh  # run once now for the initial copy
```

That last line's `systemctl restart mcrelay` means a few seconds of downtime every
renewal (~60 days) - simpler and more robust than live in-process cert reloading, and
fine for a relay at this scale. `relay_server.py` looks for the cert/key at
`<relay dir>/certs/fullchain.pem` / `privkey.pem` by default (override with
`--tls-cert`/`--tls-key` if you'd rather point it elsewhere).

## Firewall

Allow inbound TCP on:
- `25565` - the public Minecraft port, open to everyone (this is what players connect to).
- `7000` (control) and `7001` (data) - only MCPersist clients need these, but it's
  simplest to just open them too. Access control is TLS plus the per-user token plus
  unguessable per-connection UUIDs - reasonable for a small trusted beta, not hardened
  for a large public service.
- `80` - only needed for certbot's ACME HTTP-01 challenge, but needed *permanently*,
  not just once - certbot repeats this same challenge on every renewal (~every 60
  days), so closing it after the first certificate would break future renewals.

`relay_server.py` also caps concurrent raw connections per source IP
(`MAX_CONNECTIONS_PER_IP`) and simultaneous in-flight join attempts against any one
backend (`MAX_PENDING_PER_SUBDOMAIN`), so a single source can't flood the relay or
hammer one person's tunnel client with fake connection attempts - real multiplayer use
never gets close to either limit. A separate rolling-window rate limiter
(`CONN_RATE_LIMIT`, 30 attempts/minute per source IP by default) catches a different
pattern neither cap does on its own: rapid connect/disconnect cycling, where each
individual connection is too brief to ever build up against the concurrency caps.
Still no per-user bandwidth caps.

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

Set up TLS (above) before this will actually start - `relay_server.py` refuses to run
without a cert.

`users.json` (reserved subdomains) and `auto_assignments.json` (auto-registered ones,
persisted per source IP) get created next to `admin_cli.py`/`relay_server.py`. Back
them up if you care about not re-issuing tokens or losing auto-assigned addresses.

## Adding a user

```bash
cd /opt/mcrelay
python3 admin_cli.py add-user alice
```

Prints a subdomain + token - send both to that person, along with the relay's
hostname (`relay_host` - `relay.tunnel.yourdomain.com` in this doc's example, not the
bare IP) and the ports above. They run `run.bat configure-relay` on their MCPersist
install and paste all of it in.

`remove-user <subdomain>` and `list-users` are also available.

## Checking who's connected right now

```bash
python3 admin_cli.py status
```

Reads a status snapshot `relay_server.py` refreshes every ~10s
(`relay_status.json`, next to the other runtime files) - shows uptime, which
subdomains are currently registered, and concurrent connection counts by source IP.
Faster than digging through `journalctl` for "is anyone actually connected."

## Logs / troubleshooting

`relay_server.py` logs to stdout - under systemd, `journalctl -u mcrelay -f`. A
"no backend registered for ..." line means a player connected to a hostname whose
subdomain isn't currently registered/online; "didn't respond in time" means the
client's MCPersist tunnel isn't actually connected right now (their PC/server is off,
or their tunnel process crashed).
