<div align="center">

# wgmik-server

**A self-hosted WireGuard accounting & management panel for MikroTik RouterOS.**

Track per-peer usage, enforce fair-usage quotas, manage peers across multiple routers, and let users check their own usage from Telegram — all from one clean, modern panel.

<img src="assets/wgmik-intro.gif" alt="wgmik-server dashboard" width="100%" />

</div>

---

## Why

RouterOS gives you WireGuard, but no real visibility: no history, no quotas, no per-peer accounting, no way to hand usage info to your users. `wgmik-server` sits next to your router(s), polls them on a schedule, and turns raw interface counters into a usable panel — without touching your router config beyond what you ask it to.

It's built to be **simple to run** (one `docker compose up` — or [directly on the router itself](#run-on-a-mikrotik-router-routeros-715) as a RouterOS container), **robust** (encrypted secrets, DB recovery tooling, crash-safe maintenance), and **nice to look at** (modern React UI, light/dark, live charts).

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

## Run on a MikroTik router (RouterOS 7.15+)

Besides normal Docker hosting, wgmik-server can run **directly on a container-capable MikroTik router** using the standard [`/container`](https://help.mikrotik.com/docs/spaces/ROS/pages/84901929/Container) feature. No Apps YAML, no env vars, no mounts — just a veth, a bridge, and `root-dir`. The published image is multi-arch (`amd64` + `arm64`).

The default install uses ordinary RouterOS relative paths:

- `wgmik-server.tar.gz` for the downloaded image tarball.
- `containers/wgmik` as the container `root-dir`.

If internal storage has less than ~500 MiB free, the install script automatically switches to the first mounted disk (`usb1`, `pcie1`, ...) instead. Container network: `10.99.0.0/24` (gateway `10.99.0.1`, container `10.99.0.2`).

### Prerequisites

- RouterOS **7.15 or newer** with the `container` package installed (`/system package print`).
- An ARM64 or x86/CHR device — e.g. RB5009, CCR2004/2116, hAP ax series. **≥1 GB RAM recommended** (512 MB works but is tight).
- Enough storage for the image and runtime data. The image is ~250 MB on disk; low-flash devices should use external storage.

### Step 1 — Enable container support (one-time)

```
/system/device-mode update container=yes
```

On physical routers, confirm by power-cycling or pressing the reset button **within 5 minutes**. On CHR, a reboot is enough. Verify: `/system/device-mode print` should show `container: yes`.

### Step 2 — Install

Fetch and import the install script:

```
/tool fetch url="https://raw.githubusercontent.com/parhamfa/wgmik-server/main/deploy/mikrotik/wgmik-container.rsc" dst-path=wgmik-container.rsc
/import wgmik-container.rsc
```

The script detects the router architecture (`x86_64` or `arm64`), picks a storage location, downloads the matching release tarball, sets up networking, imports the image, and starts the container. It is **idempotent** — if anything fails halfway (network drop, full disk), fix the cause and just `/import` it again; finished steps are skipped.

> **RouterOS 7.15–7.17:** `/tool fetch` on these versions can't follow GitHub's download redirects. Download the tarball on a PC ([release page](https://github.com/parhamfa/wgmik-server/releases/tag/mikrotik-container-images-2026-06-11)), upload it to the router as `wgmik-server.tar.gz`, then run `/import wgmik-container.rsc` — the script skips the download when the file is already there.

Or run the commands manually:

```
/tool fetch url="https://github.com/parhamfa/wgmik-server/releases/download/mikrotik-container-images-2026-06-11/wgmik-server-linux-amd64.tar.gz" dst-path=wgmik-server.tar.gz http-max-redirect-count=5
/interface/veth/add name=veth-wgmik address=10.99.0.2/24 gateway=10.99.0.1
/interface/bridge/add name=wgmik-net
/ip/address/add address=10.99.0.1/24 interface=wgmik-net
/interface/bridge/port/add bridge=wgmik-net interface=veth-wgmik
/ip/firewall/nat/add chain=srcnat action=masquerade src-address=10.99.0.0/24 comment=wgmik
/ip/firewall/nat/add chain=dstnat action=dst-nat dst-port=6574 protocol=tcp to-addresses=10.99.0.2 to-ports=6574 comment=wgmik
/container/add comment=wgmik file=wgmik-server.tar.gz interface=veth-wgmik root-dir="containers/wgmik"
/container/set [find comment=wgmik] cmd="uvicorn backend.main:app --host 0.0.0.0 --port 6574" start-on-boot=yes logging=yes
:local started false
:for i from=1 to=12 do={ :if (!$started) do={ :do { /container/start [find comment=wgmik]; :set started true } on-error={ :delay 5s } } }
```

Release assets (swap the URL for ARM64 routers):

- CHR / x86_64: `wgmik-server-linux-amd64.tar.gz`
- ARM64 routers: `wgmik-server-linux-arm64.tar.gz`

### External storage

The script handles this automatically: when internal free space is under ~500 MiB it stores the tarball and `root-dir` on the first mounted disk. If you install manually, prefix the paths yourself (e.g. `usb1/wgmik-server.tar.gz` and `root-dir="usb1/containers/wgmik"`). Format an empty disk first if needed: `/disk format-drive <slot> file-system=ext4` (**erases it**).

### Step 3 — First-run setup

Open `http://<router-ip>:6574` and create your admin account as usual.

When the setup wizard asks for the RouterOS connection:

- Use the **router's own LAN/bridge IP** as the host — never `localhost` or `10.99.0.2`.
- Make sure the API or REST service is enabled in `/ip service` for the chosen protocol.

### Data and updates

Data (SQLite DB, encryption key) lives inside the container rootfs at `root-dir` — by default `containers/wgmik`. It survives restarts and reboots, but **`/container repull` or an image update recreates the rootfs and wipes it**. Back up through the panel before updating, or add an optional `/data` mount for update-safe persistence:

```
/container/mounts/add list=MOUNT_WGMIK src=containers/wgmik-data dst=/data
/container/set [find comment=wgmik] mountlists=MOUNT_WGMIK
```

### Troubleshooting


| Symptom                                | Fix                                                                                                                        |
| -------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| Download fails on RouterOS 7.15–7.17   | `/tool fetch` can't follow GitHub redirects on those versions — upload the tarball manually, then re-import the script     |
| Container won't import image           | Check the tarball matches your CPU architecture; if storage is full, attach a disk and re-import (the script will use it)  |
| Can't reach UI on port 6574            | Check dst-nat rule exists: `/ip/firewall/nat print where comment=wgmik`; container running: `/container print`             |
| Container stuck `stopped`              | Wait for extraction to finish, then `/container/start [find comment=wgmik]`; logs: `/log print where topics~"container"`   |
| Out of memory / container killed       | Too little free RAM; 512 MB is the floor, 1 GB+ recommended. Set limits: `/container/set [find comment=wgmik] memory-high=200M` |


### Caveats

- The Let's Encrypt TLS wizard inside the panel needs public TCP/80 reachable on the router; the self-signed option works without it.
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
- **Delivery:** Docker Compose with a single prebuilt multi-arch image (`amd64` + `arm64`) on GHCR (GitHub Actions CI). FastAPI serves the built frontend and API on one port (6574), healthchecks, and single-volume persistence. The same image runs on container-capable MikroTik routers via raw `/container`.

## Verify after boot

- **Health:** http://localhost:6574/health returns `{"status": "ok"}`.
- **First run:** the web UI shows the Create admin account screen, then the router setup wizard.
- **Restart:** `docker compose down && docker compose up` keeps your admin account and skips setup.
- **Router test:** Settings → Connection profiles → Test returns OK (or a clear error).
- **No secrets in logs:** `docker compose logs api` contains no admin password.

## Security

Report suspected vulnerabilities privately by following the [security policy](SECURITY.md).

## License

wgmik-server is available under the [MIT License](LICENSE).
