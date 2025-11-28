<!-- b3ab9e9a-01ff-4262-b314-6d2d2d4cd1ce 8688a25c-ed07-4714-bd2d-55a94d80308a -->
# MikroTik WireGuard Accounting Panel (macOS dev, cross‑platform deploy)

## Scope

A dev-friendly app you can run first on macOS, then deploy like any web app anywhere. It (1) polls RouterOS v7 for per-peer WireGuard counters, (2) stores deltas with daily/monthly rollups, (3) enforces quotas by disabling peers, and (4) provides a dashboard aligned with your existing design language. Supports both RouterOS REST and classic API with fully configurable host/port/TLS.

## Stack

- Backend: FastAPI, Uvicorn, APScheduler, SQLAlchemy, SQLite (dev), Postgres (optional later), httpx, librouteros, cryptography
- Frontend: React + Vite + Tailwind (+ Framer Motion), Recharts
- Packaging: Dockerfile, docker-compose.yml, .env.example (portable to Linux/K8s)
- Dev UX: `run.py` orchestrates uvicorn + Vite (hot reload) on macOS

## Directory layout (repo `wgmik-server`)

- `run.py` (dev harness; --dev runs Vite+Uvicorn)
- `backend/`
- `main.py` (FastAPI app, static serving for built frontend)
- `api/routes.py` (routers, peers, quotas, usage, actions)
- `routeros/`
- `client_base.py` (interface: list peers, get stats, set peer disabled)
- `rest_client.py` (HTTP(S) /rest; custom port, TLS verify toggle)
- `api_client.py` (binary API; custom port, TLS verify toggle)
- `scheduler.py` (polling, rollups, quota checks)
- `db.py`, `models.py` (Routers, WGInterfaces, Peers, UsageSample, UsageDaily, UsageMonthly, Quotas, Actions)
- `security.py` (credential encryption via master key env)
- `settings.py` (pydantic config; poll interval, cycle day, etc.)
- `frontend/` (Vite React app; existing design language)
- `src/pages/` (Wizard, Dashboard, PeerDetail, Settings)
- `src/components/` (Cards, Charts, Tables, StatusPill)
- `src/styles.css`, `tailwind.config.ts`, `vite.config.ts`
- `Dockerfile`, `docker-compose.yml`, `.env.example`, `README.md`

## Design language (aligned with your current UI)

- Tokens
- Colors: white surfaces; text `gray-900/700/500`; borders `gray-200/300`; page bg `gray-50`; focus ring `gray-300`.
- Radii: cards `rounded-xl`; controls `rounded`.
- Shadows: `shadow-sm` default, `hover:shadow-md` on interactive cards.
- Rings: `ring-1 ring-gray-200` on cards for crisp edges.
- Motion: Tailwind `transition` + Framer Motion subtle fade/scale; optional `hover:-translate-y-[1px]` for cards.
- Components (to port 1:1)
- Card: rounded-xl, ring, white surface, hover shadow.
- Button/Input/AutoTextarea: gray palette, focus rings per tokens.
- Shimmer: skeleton loader utility.
- StatusPill: rounded pill with pulsing dot.
- Online: green pill, dot with CSS pulse.
- Offline: red pill, shows "Last seen <timeago>".

## Online/offline model

- Store raw `last_handshake` timestamp per poll.
- Online if `now - last_handshake <= online_threshold_seconds` (default 15s; configurable in Settings).
- Surface computed status in API and UI; backend also returns `last_seen_at`.

## Settings

- Global (editable in Settings page; persisted in DB):
- Poll interval (seconds)
- Online threshold seconds (default 15)
- Monthly reset day (1–28) and timezone
- Default monthly quota (GB)
- Enforcement mode (monitor only | auto-disable)
- Router TLS verify (per-router also)
- Endpoints: GET/PUT `/api/settings`.

## Core flows

- Poll every 30–60s: fetch peers (rx/tx bytes, endpoint, last_handshake); compute deltas; update daily/monthly; store last handshake.
- Online state: classifier based on threshold; backend exposes `online` boolean and `last_seen_at`.
- Enforce: if monthly_total > limit → disable peer on router; record action; optional auto re-enable at cycle reset.
- Wizard: connect router (REST or API; custom port/TLS), select WG interface(s), list peers; outbound peers unchecked by default; import.

## API endpoints

- Routers: POST/GET/DELETE `/api/routers`; GET `/api/routers/{id}/interfaces`; GET `/api/routers/{id}/peers`
- Peers: POST `/api/routers/{id}/peers/import`; PATCH `/api/peers/{id}` (selected/disabled)
- Usage: GET `/api/peers/{id}/usage?from&to&window=daily|raw`; GET `/api/summary/month`
- Quotas: POST/PATCH `/api/quotas`; GET `/api/peers/{id}/quota`
- Actions: POST `/api/peers/{id}/enforce` (enable/disable); GET `/api/peers/{id}/actions`
- Settings: GET/PUT `/api/settings`

## Local run (macOS)

- `python -m venv .venv && source .venv/bin/activate && pip install -r backend/requirements.txt`
- `cd frontend && npm i`
- `python run.py --dev`
- Open http://localhost:5173 (frontend), API at http://localhost:8000
- Optional: `docker compose up --build` to run both services

## Deployment

- Linux VM: `docker compose up -d` (uses `.env` for secrets/ports)
- Kubernetes (optional later): container image from Dockerfile
- RouterOS Container (optional later): if you want it on CHR itself

## Milestones

- M1 (MVP): polling, deltas, online status, monthly quota disable, wizard, dashboard with daily chart, Settings page
- M2 (Packaging): Dockerfile, compose, `.env.example`, README quick start

References: quota/accounting patterns inspired by 3x-ui (Xray), adapted for RouterOS/WireGuard. See: [3x-ui](https://github.com/MHSanaei/3x-ui)

### To-dos

- [ ] Scaffold FastAPI app and config on macOS
- [ ] Implement REST client for peers list, stats, disable/enable
- [ ] Implement binary API client (TLS 8729) with custom port
- [ ] Implement polling, delta computation, daily/monthly rollups
- [ ] Implement monthly quota checks and peer disable/enable
- [ ] Create SQLAlchemy models and migrations (SQLite dev)
- [ ] Scaffold Vite React with Tailwind and prodpic-maker styles
- [ ] Build setup wizard: connect, interfaces, peer import
- [ ] Build dashboard: per-peer cards, daily/month charts
- [ ] Add run.py to orchestrate uvicorn + Vite with hot reload