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

### Confirmation passwords, drafts and private logs

Rotation confirmation uses the current panel-account password, not the RCON password. A wrong confirmation preserves the session and draft; redirects occur only after session expiry is verified. For origin mismatch, check the browser address, `PANEL_PUBLIC_ORIGIN` and proxy configuration; retain origin validation.

Server Diagnostics now includes Panel logs. Only the owner can read them after confirming the current password for each fetch. The UI hides records on navigation or after 5 minutes. Logs live in the panel data directory's `panel-logs/`, with asynchronous writes and rotation around 50MB. They record request status/timing/IDs, stable error codes, runtime failures, catalog sync, moderation audit summaries and frontend error types. Frontend messages/state, passwords, Bearers, API keys, cookies, query strings, request bodies and raw configuration are excluded. Operational logging starts with this version; existing moderation audits remain accessible. Share only necessary reviewed error codes, timestamps and request IDs.

The installed version comes from the backend and does not prove that the browser loaded the matching frontend. Upgrade both backend and the Release's `frontend/dist`, then rebuild the panel container; source archives require a frontend build first. If new controls are missing, hard-refresh with `Ctrl+F5` and compare the dialog's frontend build version. HTML entry points use no-store; do not override this with long-lived proxy/CDN HTML caching. Content-hashed assets can retain normal caching.

Verify health, page access, login, and read-only queries. Do not test disruptive game commands while players are online. Investigate uncertain writes before any retry.

The navigation checks stable GitHub and Gitee releases. Automatic installation requires the host service below and an explicit owner opt-in. Manual updates remain available: back up the database and environment, verify `SHA256SUMS.txt`, replace application/frontend build files, and run `docker compose -f deploy/compose.yaml up -d --build`. Retain the previous version for rollback.
## Automatic updates and Gitee publishing

The panel compares stable GitHub and Gitee releases, preferring Gitee for equal versions. If a Release is missing or lacks a verifiable package, it reads `latest.json` from the `updates` branch containing a prebuilt package and SHA-256. It never installs unbuilt main-branch source. Failed sources are reported explicitly.

<a id="host-updater"></a>

### Install the host updater (Linux Docker)

**Starting the panel container does not install the host updater.** Version checks, Release links and manual downloads work without it; one-click installation and background automatic installation require it. RCON and other management features are unaffected. Use manual upgrades on Windows, without systemd, or with an unsupported deployment layout.

For the standard Linux Docker deployment at `/opt/wardogs-rcon-panel` (container `wardogs-rcon-panel`, database `data/panel.sqlite3`), back up the database and environment first, then install:

```bash
cd /opt/wardogs-rcon-panel
sudo install -m 700 deploy/update-agent.py /usr/local/sbin/wardogs-panel-updater
sudo install -m 644 deploy/wardogs-panel-updater.service /etc/systemd/system/
sudo install -m 644 deploy/wardogs-panel-updater.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wardogs-panel-updater.timer
sudo systemctl start wardogs-panel-updater.service
```

The last command immediately writes a heartbeat rather than waiting for the timer. With automatic installation off and no queued request, this does not install a release. If automatic installation was previously enabled or a request is queued, it may start that task; check the policy and task first. Click “Recheck updater” afterward. Heartbeats normally refresh every 60 seconds. A completed oneshot service may be `inactive (dead)`; inspect its exit result, timer and heartbeat together.

Before installing for a different directory, edit the service's `ExecStart --root` to the actual project root. It must contain the project Compose layout and a real `data/panel.sqlite3`. The container database's parent directory must mount that same host `data/` directory. Changing only the service path is insufficient. The host service runs as root with Docker privileges; the panel container does not mount the Docker socket. Automatic installation is **off by default**. The owner must confirm the current password to install once or enable hourly background updates. Only the panel restarts; the game server stays running.

The service independently validates the package checksum, paths and version, backs up SQLite, builds the image and checks health. It attempts image/database rollback on failure; interrupted jobs are never automatically replayed. Backups are retained in `.updates-host/`, status in `data/updates/`. Review `systemctl status wardogs-panel-updater.service` on failure. Environment, Compose and accounts are preserved. Reinstall the host helper when its implementation changes.

### Troubleshooting the host updater

Click “Recheck updater” first. The panel distinguishes these conditions:

| Status | What to check |
| --- | --- |
| No heartbeat | Service installation, timer scheduling, and matching service/container data paths |
| Expired heartbeat (180 seconds or older) | Last service result and timer; a long-running update can also expire the heartbeat, so inspect logs before retrying |
| Unreadable heartbeat | Shared mount and container permissions (UID/GID `10001:10001` in the standard template); do not use `chmod 777` |
| Invalid format / clock mismatch | Helper logs/version and host clock synchronization; never fabricate `agent.json` |
| Cannot read updater status | Browser-to-panel request failure; check login, reverse proxy and container before concluding that the service is absent |

Run these read-only commands on the **panel host**, adjusting the project path when needed:

```bash
sudo systemctl status wardogs-panel-updater.timer wardogs-panel-updater.service --no-pager
sudo systemctl list-timers --all wardogs-panel-updater.timer
sudo journalctl -u wardogs-panel-updater.service -n 80 --no-pager
sudo systemctl cat wardogs-panel-updater.service
docker inspect wardogs-rcon-panel --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
sudo stat /opt/wardogs-rcon-panel/data/updates/agent.json
docker exec wardogs-rcon-panel python -c 'from app.config import load_settings; from app.upgrade import update_state; s=update_state(load_settings()); print(s.get("agent", {"agentAvailable": s.get("agentAvailable")}))'
```

Share only necessary status and redacted errors. Do not publish environment files, databases, or full private logs. Install the helper only after confirming it is absent. Investigate failed jobs before retrying; restarting the game is not required.

### Manual upgrades without the host service

Download the Release ZIP and `SHA256SUMS.txt` from GitHub/Gitee and verify SHA-256. Back up the database, environment and old version. Replace application/frontend build files while preserving `data/`, `deploy/panel.env`, existing Compose and reverse proxy configuration. Run `docker compose -f deploy/compose.yaml up -d --build panel`, verify health/login and retain the rollback copy. Do not overwrite existing credentials with example environment files. Non-Docker installations should update and restart the panel using their existing service mechanism.

### Gitee publishing and fallback

Set the protected GitHub Actions secret `GITEE_TOKEN` with Gitee repository and Release write permissions. A stable GitHub Release containing `Wardogs_RCON_PANEL-vVERSION.zip` and `SHA256SUMS.txt` triggers Gitee code/tag synchronization, fallback branch publication, and Release creation with attachments. The workflow can also be dispatched with an existing tag. Never commit tokens. If Gitee Releases are empty, use the prebuilt fallback branch; automatic mode also checks GitHub. Stop updating if all sources fail. Checksums from the same trusted repository are not independent signatures; protect maintainer accounts.
