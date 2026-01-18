# wgmik-server

WireGuard accounting panel for MikroTik RouterOS (FastAPI + React/Vite).

## Docker quickstart (recommended)

- **Prereqs**: Docker Desktop (or Docker Engine) + docker compose.

### Production-style (build UI, serve via nginx, proxy `/api`)

```bash
cd /Users/parhamfatemi/Developer/mikrotik/wgmik-server
cp env.example .env
# edit .env and set SECRET_KEY to something non-trivial
docker compose up --build
```

- **Web UI**: `http://localhost:5173`
- **API**: `http://localhost:8000`

The SQLite DB is persisted in a named docker volume (`wgmik_data`).

Stop:

```bash
docker compose down
```

### Dev mode (hot reload for backend + frontend)

```bash
cd /Users/parhamfatemi/Developer/mikrotik/wgmik-server
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
- **API health**: open `http://localhost:8000/api/settings` (should return JSON)
- **Router actions**: Settings → Connection profiles → Test should return OK (or a clear error)


