# 🚀 Panel deployment

[简体中文](README.md) · [Project home](../README.en.md)

> ⚠️ This project is under development. Back up game server configuration and data before connecting or making changes.

HTTP and HTTPS are supported. **Avoid public HTTP deployments**: passwords, sessions, and submitted RCON credentials may be intercepted. A warning cannot provide encryption. Without a domain, consider an SSH tunnel or a controlled private network.

## 1. Prepare and start

Install Docker Compose. Source builds also require Node.js and pnpm. The uploaded Release ZIP contains `frontend/dist`; skip the frontend build for that ZIP. GitHub source archives require building it.

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm run build
cd ..
cp deploy/panel.env.example deploy/panel.env
chmod 600 deploy/panel.env
mkdir -p data
sudo chown 10001:10001 data
```

Set the origin and cookie policy below in `deploy/panel.env`. Generate a persistent encryption key:

```bash
docker compose -f deploy/compose.yaml build panel
docker compose -f deploy/compose.yaml run --rm --no-deps panel python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Save the generated value as `PANEL_CONFIG_KEY`, then start and create an administrator interactively:

```bash
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml exec panel python -m app.cli create-admin
```

Never upload secrets, `panel.env`, databases, or backups. Preserve the key and `data/` across upgrades; changing the key prevents decryption of stored RCON credentials. Compose binds the application to host `127.0.0.1:18000`; run the reverse proxy on that host.

## 2. HTTP (supported, public use discouraged)

Set the exact browser origin, including a custom port if used, without a path or trailing slash:

```dotenv
PANEL_PUBLIC_ORIGIN=http://panel.example.com
PANEL_SESSION_SECURE=false
```

Use the [HTTP Nginx template](wardogs-rcon-panel.http.nginx.conf) and replace `server_name`. Example for Ubuntu/Debian with Nginx installed:

```bash
sudo cp deploy/wardogs-rcon-panel.http.nginx.conf /etc/nginx/sites-available/wardogs-panel
sudo ln -s /etc/nginx/sites-available/wardogs-panel /etc/nginx/sites-enabled/wardogs-panel
sudo nginx -t
sudo systemctl reload nginx
docker compose -f deploy/compose.yaml up -d --force-recreate panel
```

Allow the chosen port through cloud and host firewalls. A custom port requires updating both Nginx `listen` and the origin. Merge with existing sites instead of overwriting them or creating conflicting listeners.

Every HTTP page load displays **“您未部署在https版本 请留意数据安全”** before showing the login screen. The backend logs the same warning at startup. HTTP cookies omit `Secure` while retaining `HttpOnly`, `SameSite=Strict`, and origin checks. If omitted, `PANEL_SESSION_SECURE` is inferred from the origin; an explicit protocol mismatch rejects startup.

## 3. HTTPS (recommended)

### Automatic certificates with Caddy

Point your domain DNS to the panel host and allow inbound ports 80/443. Ensure those ports are available. Install Caddy using the [official instructions](https://caddyserver.com/docs/install). Set `/etc/caddy/Caddyfile`:

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:18000
}
```

Set the panel environment:

```dotenv
PANEL_PUBLIC_ORIGIN=https://panel.example.com
PANEL_SESSION_SECURE=true
```

Validate and reload (official systemd service installation):

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
docker compose -f deploy/compose.yaml up -d --force-recreate panel
```

Caddy obtains/renews certificates and redirects HTTP for eligible domains when DNS, challenge connectivity, and ports are available. See [Automatic HTTPS](https://caddyserver.com/docs/automatic-https). Prefer a domain with a valid certificate rather than applying this domain example to a bare IP.

### Existing Nginx and certificates

Use the [HTTPS Nginx template](wardogs-rcon-panel.nginx.conf); replace the domain and certificate paths. Obtain a valid certificate first, configure renewal with your certificate provider, then run `nginx -t` and reload. Use the same HTTPS environment settings above.

### Migrating from HTTP

Back up the database and environment, configure HTTPS, change the origin and cookie flag, and recreate the container. Preserve the encryption key and data; do not recreate the administrator. Log in again using the HTTPS address. HTTPS access skips the HTTP warning.

## 4. Panel transport and RCON transport

The browser-to-panel connection is configured by the public origin and reverse proxy. The panel-to-game connection uses the RCON URL. HTTPS for the panel does **not** encrypt the Bearer sent to an HTTP RCON endpoint.

Prefer HTTPS or a VPN/private connection for RCON. Place an HTTPS RCON proxy on the game host or a trusted private network so its upstream traffic does not still cross the public internet in cleartext.

Only after accepting that risk, set:

```dotenv
PANEL_ALLOW_PUBLIC_HTTP_RCON=true
```

Recreate the panel:

```bash
docker compose -f deploy/compose.yaml up -d --force-recreate panel
```

In Server Settings, enable public HTTP RCON, enter the address and RCON password (Bearer), confirm the warning, and save. Environment-configured targets additionally require `WARDOGS_ALLOW_PUBLIC_HTTP=true`. Never store the Bearer in frontend variables or Nginx configuration. The gate is disabled by default; the settings page explains how to enable it.

## 5. Verification and upgrades

Verify health, page access, login, and read-only queries. Do not test disruptive game commands while players are online. Investigate uncertain writes before any retry.

The navigation checks stable GitHub and Gitee releases. Automatic installation requires the host service below and an explicit owner opt-in. Manual updates remain available: back up the database and environment, verify `SHA256SUMS.txt`, replace application/frontend build files, and run `docker compose -f deploy/compose.yaml up -d --build`. Retain the previous version for rollback.
## Automatic updates and Gitee publishing

The panel compares stable GitHub and Gitee releases, preferring Gitee for equal versions. If a Release is missing or lacks a verifiable package, it reads `latest.json` from the `updates` branch containing a prebuilt package and SHA-256. It never installs unbuilt main-branch source. Failed sources are reported explicitly.

For the standard Linux Docker deployment at `/opt/wardogs-rcon-panel` (container `wardogs-rcon-panel`, database `data/panel.sqlite3`), back up the database and environment first, then install:

```bash
sudo install -m 700 deploy/update-agent.py /usr/local/sbin/wardogs-panel-updater
sudo install -m 644 deploy/wardogs-panel-updater.service /etc/systemd/system/
sudo install -m 644 deploy/wardogs-panel-updater.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wardogs-panel-updater.timer
```

Adjust the service's `ExecStart` for a different directory. Other deployment layouts require manual updates. The host service runs as root with Docker privileges; the panel container does not mount the Docker socket. Automatic installation is **off by default**. The owner must confirm the current password to install once or enable hourly background updates. Only the panel restarts; the game server stays running.

The service independently validates the package checksum, paths and version, backs up SQLite, builds the image and checks health. It attempts image/database rollback on failure; interrupted jobs are never automatically replayed. Backups are retained in `.updates-host/`, status in `data/updates/`. Review `systemctl status wardogs-panel-updater.service` on failure. Environment, Compose and accounts are preserved. Reinstall the host helper when its implementation changes.

Set the protected GitHub Actions secret `GITEE_TOKEN` with Gitee repository and Release write permissions. A stable GitHub Release containing `Wardogs_RCON_PANEL-vVERSION.zip` and `SHA256SUMS.txt` triggers Gitee code/tag synchronization, fallback branch publication, and Release creation with attachments. The workflow can also be dispatched with an existing tag. Never commit tokens. If Gitee Releases are empty, use the prebuilt fallback branch; automatic mode also checks GitHub. Stop updating if all sources fail. Checksums from the same trusted repository are not independent signatures; protect maintainer accounts.
