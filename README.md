<div align="center">

# wgmik-server

**A self-hosted WireGuard accounting & management panel for MikroTik RouterOS.**

Track per-peer usage, enforce fair-usage quotas, manage peers across multiple routers, and let users check their own usage from Telegram — all from one clean, modern panel.

<img src="assets/wgmik-intro.gif" alt="wgmik-server dashboard" width="100%" />

</div>

---

## Why

RouterOS gives you WireGuard, but no real visibility: no history, no quotas, no per-peer accounting, no way to hand usage info to your users. `wgmik-server` sits next to your router(s), polls them on a schedule, and turns raw interface counters into a usable panel — without touching your router config beyond what you ask it to.

It's built to be **simple to run** (one `docker compose up`), **robust** (encrypted secrets, DB recovery tooling, crash-safe maintenance), and **nice to look at** (modern React UI, light/dark, live charts).

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
- Rendered usage **charts and images** delivered straight in chat.
- Push **notifications**: quota warnings, daily and weekly summaries.
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
docker compose up --build
```

That's it — no `.env` to edit, no secrets to generate.

- **Web UI:** http://localhost:5173
- **API:** http://localhost:8000

### First run

1. Open http://localhost:5173 — on a fresh install you land on **Create admin account**.
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

## Dev mode (hot reload)

```bash
docker compose -f docker-compose.dev.yml up --build
```

- **Frontend** hot reload: http://localhost:5173
- **Backend** hot reload: http://localhost:8000

Stop:

```bash
docker compose -f docker-compose.dev.yml down
```

## Tech stack

- **Backend:** FastAPI (Python), SQLAlchemy, APScheduler, SQLite.
- **Frontend:** React + Vite + TypeScript + Tailwind.
- **Delivery:** Docker Compose (nginx serves the built frontend and proxies the API), with healthchecks and single-volume persistence.

## Verify after boot

- **Health:** http://localhost:8000/health returns `{"status": "ok"}`.
- **First run:** the web UI shows the Create admin account screen, then the router setup wizard.
- **Restart:** `docker compose down && docker compose up` keeps your admin account and skips setup.
- **Router test:** Settings → Connection profiles → Test returns OK (or a clear error).
- **No secrets in logs:** `docker compose logs api` contains no admin password.
