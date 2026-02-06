# wgmik-server

WireGuard accounting panel for MikroTik RouterOS (FastAPI + React/Vite).

## Docker quickstart (recommended)

- **Prereqs**: Docker Desktop (or Docker Engine) + docker compose.

### Production-style (build UI, serve via nginx, proxy `/api`)

```bash
git clone https://github.com/parhamfa/wgmik-server.git
cd wgmik-server
cp env.example .env
# edit .env and set SECRET_KEY to something non-trivial
# (optional but recommended) set INITIAL_ADMIN_PASSWORD to a known value
docker compose up --build
```

- **Web UI**: `http://localhost:5173`
- **API**: `http://localhost:8000`

On a fresh install (empty DB), the backend will create an initial admin user:

- `INITIAL_ADMIN_USERNAME` (default: `admin`)
- `INITIAL_ADMIN_PASSWORD` (if empty, a random password is generated and printed once in the API logs)

To retrieve an auto-generated password:

```bash
docker compose logs api
```

The SQLite DB is persisted in `./data` (bind-mounted into the container).

Stop:

```bash
docker compose down
```

### Dev mode (hot reload for backend + frontend)

```bash
cp env.example .env
docker compose -f docker-compose.dev.yml up --build
```

- **Frontend** hot reload: `http://localhost:5173`
- **Backend** hot reload: `http://localhost:8000`

Stop:

```bash
docker compose -f docker-compose.dev.yml down
```

## What to test after boot

- **UI loads** at `http://localhost:5173`
- **API health**: open `http://localhost:8000/health` (should return `{"status": "ok"}`)
- **Router actions**: Settings → Connection profiles → Test should return OK (or a clear error)
