<div align="center">

# wgmik-server

**A self-hosted WireGuard accounting & management panel for MikroTik RouterOS.**

Track per-peer usage, enforce fair-usage quotas, manage peers across multiple routers, and let users check their own usage from Telegram — all from one clean, modern panel.

<img src="assets/wgmik-intro.gif" alt="wgmik-server dashboard" width="100%" />

</div>

---

## Why

RouterOS gives you WireGuard, but no real visibility: no history, no quotas, no per-peer accounting, no way to hand usage info to your users. `wgmik-server` sits next to your router(s), polls them on a schedule, and turns raw interface counters into a usable panel — without touching your router config beyond what you ask it to.

It's built to be **simple to run** (one `docker compose up` — or [directly on the router itself](#run-on-a-mikrotik-router-routeros-723) via RouterOS Apps), **robust** (encrypted secrets, DB recovery tooling, crash-safe maintenance), and **nice to look at** (modern React UI, light/dark, live charts).

## Features

**Routers & peers**
- Manage **multiple RouterOS routers** from one panel (REST or legacy API, with version auto-detection).
- Import existing WireGuard peers, add new ones, edit, rename, delete, and renew keys.
- Generate and export ready-to-use client configs, with per-peer export preferences.
- Live online/offline status with a configurable "online" threshold.

**Usage accounting**
- Background polling turns interface counters into real **per-peer usage history**.
- Today / monthly / all-time views, monthly summaries, and per-router breakdowns.
- Live traffic **charts** (TX/RX) on the dashboard and per peer.
- Configurable monthly reset day and timezone.

**Fair usage & quotas**
- Define **fair-usage rules and tiers**, assign them to peers, and let the panel enforce quotas automatically.
- Quota warnings at 80% / 90%, quota-hit and quota-lifted events.
- Per-peer quota status and one-click reset.

**Telegram bot (self-service for your users)**
- Users link their peer via a one-time **deep-link signup token** — no accounts to hand out.
- Commands: `/today`, `/monthly`, `/alltime`, `/calendar`, `/fair`, `/settings`.
- Rendered usage **charts and images** delivered straight in chat — generated in-process (SVG → PNG), no headless browser.
- Push **notifications**: quota warnings and throttle alerts with fair-usage status cards, daily and weekly summaries with charts.
- **Admin menu** (`/admin`): aggregate dashboard reports (today / month / all-time) and per-user reports.
- **Multi-language** (i18n) and **Jalali / Gregorian** calendar support.

**Admin & operations**
- Multiple admin users, password resets, forced password change on first login.
- Scheduled background jobs (polling, daily/weekly summaries, automated usage maintenance) with hot-reloadable intervals.
- An **exclusive-operation gate** that safely locks the app during destructive/maintenance work instead of corrupting data.
- DB recovery and diagnostic tooling in `bin/` for when things go wrong.

**Built-in by default**
- Secrets at rest (RouterOS passwords, WireGuard keys, Telegram token) are **encrypted**.
- No admin password in logs, no secrets in config files.
- Light/dark theme that follows your OS.

## Quickstart

**Requirements:** Docker + Docker Compose.

```bash
git clone https://github.com/parhamfa/wgmik-server.git
cd wgmik-server
docker compose pull   # fetch prebuilt images from GHCR
docker compose up -d
```

That's it — no `.env` to edit, no secrets to generate, nothing to compile. The prebuilt image is published to GitHub Container Registry (`ghcr.io/parhamfa/wgmik-server`), so you only download what changed.

> Prefer to **build from source** instead of pulling? Use `docker compose up --build` — same compose file, it just builds the image locally.

- **Web UI + API:** http://localhost:6574

### First run

1. Open http://localhost:6574 — on a fresh install you land on **Create admin account**.
2. Pick a username and a password (min 12 characters). You're logged in immediately.
3. The setup wizard walks you through connecting a RouterOS profile and importing peers.

Your SQLite database and the auto-generated encryption key are persisted in `./data` (bind-mounted), so everything survives restarts.

Stop the stack:

```bash
docker compose down
```

## Configuration (all optional)

A fresh install needs no configuration. To override a default, copy `env.example` to `.env` and uncomment what you need.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signs login sessions **and** derives the key that encrypts stored RouterOS/WireGuard/Telegram secrets. Left unset, a strong key is generated on first boot and persisted to `/data/secret_key`. Set a fixed value only for advanced/multi-instance deployments. |
| `DATABASE_URL` | Defaults to the SQLite DB in the data volume. |
| `DEBUG` | FastAPI debug + verbose logs. |

> **Do not change `SECRET_KEY` on an existing install.** It encrypts stored secrets — changing it makes previously saved RouterOS/WireGuard/Telegram credentials undecryptable, and they'd need to be re-entered.

## Updating

Your data lives in `./data` (DB + encryption key) and is never touched by an update. Schema changes are applied automatically on startup, so there's no manual migration step.

```bash
# Optional but recommended: back up your data first
cp -r data data.backup-$(date +%Y%m%d)

git pull
docker compose pull     # grab the new prebuilt image (only changed layers)
docker compose up -d
```

Building from source instead? Replace the pull with `docker compose up --build -d`. Docker's layer cache means packages are only re-downloaded when `package-lock.json` / `requirements.txt` actually change — avoid `--no-cache` for routine updates.

To pin a specific release instead of tracking the latest image, set `WGMIK_TAG` (e.g. `WGMIK_TAG=v1.0.0 docker compose up -d`).

## Run on a MikroTik router (RouterOS 7.23+)

Besides normal Docker hosting, wgmik-server can run **directly on a container-capable MikroTik router** using the RouterOS [Apps](https://help.mikrotik.com/docs/spaces/ROS/pages/343244823/Apps) feature. The published image is multi-arch (`amd64` + `arm64`), so it works on ARM64 MikroTik hardware.

**Prerequisites**

- RouterOS **7.23 or newer** with the `container` package installed (custom apps exist since 7.22, but the app YAML uses the newer 7.23 port syntax).
- An ARM64 or x86 device that supports containers — e.g. RB5009, CCR2004/2116, hAP ax series. **≥1 GB RAM recommended.**
- External storage (USB / NVMe / SATA) — the image is roughly 250 MB uncompressed, too large for internal flash on most devices.
- Container device-mode enabled (one-time, requires physical access):

```
/system/device-mode update container=yes
```

Then power-cycle or press the reset button within 5 minutes to confirm.

**Install**

1. Upload [`deploy/mikrotik/wgmik.tikapp.yaml`](deploy/mikrotik/wgmik.tikapp.yaml) to the router (Files menu, WinBox drag-and-drop, or `scp`).
2. Create and start the app, pointing its root directory at your external storage:

```
/app add yaml=[/file get wgmik.tikapp.yaml contents]
/app set [find name=wgmik-server] root-dir=usb1/wgmik
/app start [find name=wgmik-server]
```

(Alternatively: `/app add network=lan`, then `/app edit app yaml` and paste the YAML into the editor, save with Ctrl+O.)

3. Open `http://<router-ip>:6574` and follow the normal first-run setup.

RouterOS automatically creates the veth interface, NAT, and port-forward rules — no manual container networking needed.

**Caveats when self-hosting on the router it manages**

- When adding the router in the setup wizard, use the **router's bridge/LAN IP** as the host — not `localhost` (the container has its own network namespace).
- The Let's Encrypt TLS wizard needs public TCP/80 reachable on the router; the self-signed option works without it.
- Your data (SQLite DB, encryption key, backups) lives under the app's mount on router storage and survives restarts and updates. **`Cleanup` in `/app` deletes it** — copy the `wgmik/data` directory off the router first if you want a backup.
- Be careful with firewall changes made through the panel: a bad rule can cut the panel off from the router it runs on.

## Dev mode (hot reload)

Single container — Vite and uvicorn run together with bind mounts for hot reload.

```bash
docker compose -f docker-compose.dev.yml up --build
```

- **Web UI (Vite):** http://localhost:6574
- **API (uvicorn --reload):** http://localhost:6575

Stop:

```bash
docker compose -f docker-compose.dev.yml down
```

## Tech stack

- **Backend:** FastAPI (Python), SQLAlchemy, APScheduler, SQLite. Telegram card/chart images are rendered with [resvg](https://github.com/baseplate-admin/resvg-py) (no Chromium/Playwright).
- **Frontend:** React + Vite + TypeScript + Tailwind.
- **Delivery:** Docker Compose with a single prebuilt multi-arch image (`amd64` + `arm64`) on GHCR (GitHub Actions CI). FastAPI serves the built frontend and API on one port (6574), healthchecks, and single-volume persistence. The same image runs on container-capable MikroTik routers via RouterOS Apps.

## Verify after boot

- **Health:** http://localhost:6574/health returns `{"status": "ok"}`.
- **First run:** the web UI shows the Create admin account screen, then the router setup wizard.
- **Restart:** `docker compose down && docker compose up` keeps your admin account and skips setup.
- **Router test:** Settings → Connection profiles → Test returns OK (or a clear error).
- **No secrets in logs:** `docker compose logs api` contains no admin password.
