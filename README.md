# wgmik-server

WireGuard accounting panel for MikroTik RouterOS (FastAPI + React/Vite).

## Quickstart

**Prereqs:** Docker Desktop (or Docker Engine) + docker compose.

```bash
git clone https://github.com/parhamfa/wgmik-server.git
cd wgmik-server
docker compose up --build
```

That's it — no `.env` to edit, no secrets to generate.

- **Web UI:** http://localhost:5173
- **API:** http://localhost:8000

### First run

1. Open http://localhost:5173. On a fresh install you're sent to the **Create admin account** screen.
2. Choose a username and password (min 12 characters). You're logged in immediately.
3. The router setup wizard walks you through connecting a RouterOS profile and importing peers.

There is no admin password in the logs and nothing to copy out of a config file.

Stop:

```bash
docker compose down
```

The SQLite DB and the encryption key are persisted in `./data` (bind-mounted into the container), so your data and sessions survive restarts.

## Dev mode (hot reload for backend + frontend)

```bash
docker compose -f docker-compose.dev.yml up --build
```

- **Frontend** hot reload: http://localhost:5173
- **Backend** hot reload: http://localhost:8000

Stop:

```bash
docker compose -f docker-compose.dev.yml down
```

## Configuration (all optional)

A fresh install needs no configuration. To override a default, copy `env.example` to `.env` and uncomment what you need.

- **`SECRET_KEY`** — signs login sessions (JWTs) **and** derives the key that encrypts RouterOS passwords, WireGuard private/preshared keys, and the Telegram token. If left unset, the backend generates a strong key on first boot and persists it to `/data/secret_key` (reused on every restart). Set a fixed value only for advanced/multi-instance deployments that must share a key.
- **`DATABASE_URL`** — defaults to the SQLite DB in the data volume.
- **`DEBUG`** — FastAPI debug + verbose logs.

> Because `SECRET_KEY` encrypts stored secrets, never change it on an existing install — doing so makes previously stored RouterOS/WireGuard/Telegram credentials undecryptable and they'd need to be re-entered.

### Upgrading from an older version

Earlier versions used a default `SECRET_KEY=change-me` and an `INITIAL_ADMIN_PASSWORD` env/log-based admin bootstrap; both are gone. If your old deployment relied on the literal `change-me` default, the new auto-generated key won't match the old one and stored router/WG/Telegram secrets will need to be re-entered. If you had set a real `SECRET_KEY`, keep setting it and nothing changes.

## What to test after boot

- **First run:** http://localhost:5173 shows the Create admin account screen; after submitting you land in the router setup wizard.
- **Restart:** `docker compose down && docker compose up` keeps your admin account and skips the setup screen.
- **API health:** http://localhost:8000/health returns `{"status": "ok"}`.
- **Router actions:** Settings → Connection profiles → Test returns OK (or a clear error).
- **No secrets in logs:** `docker compose logs api` contains no admin password.
