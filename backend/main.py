from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from .api.routes import router as api_router
from .destructive_ops import exclusive_operation_gate
from .scheduler import ensure_scheduler
from .db import (
    Base,
    engine,
    ensure_fair_usage_scope_columns,
    ensure_fair_usage_tier_schema,
    ensure_peer_router_sync_columns,
    ensure_router_enabled_column,
    ensure_router_version_columns,
    ensure_user_auth_schema,
    ensure_runtime_indexes,
    prepare_sqlite_database,
    should_auto_bootstrap_runtime_indexes,
)
from .settings import settings


app = FastAPI(title="WG Accounting", debug=settings.debug)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health_check():
    return {"status": "ok"}

@app.on_event("startup")
def _start():
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    ensure_fair_usage_scope_columns()
    ensure_fair_usage_tier_schema()
    ensure_router_enabled_column()
    ensure_router_version_columns()
    ensure_peer_router_sync_columns()
    ensure_user_auth_schema()
    prepare_sqlite_database()
    if settings.database_url.startswith("sqlite:") and ":memory:" not in settings.database_url:
        if should_auto_bootstrap_runtime_indexes():
            ensure_runtime_indexes()
    else:
        ensure_runtime_indexes()

    # The first admin account is created via the in-app first-run setup flow
    # (POST /api/auth/setup); no env/log-based bootstrap is performed here.

    # Hydrate runtime settings from DB BEFORE starting scheduler
    from .db import SessionLocal
    from .models import SettingsKV
    db = SessionLocal()
    try:
        for key in ("poll_interval_seconds", "online_threshold_seconds", "monthly_reset_day", "timezone", "date_calendar"):
            kv = db.get(SettingsKV, key)
            if not kv:
                continue
            if key in ("poll_interval_seconds", "online_threshold_seconds", "monthly_reset_day"):
                try:
                    setattr(settings, key, int(kv.value))
                except ValueError:
                    continue
            elif key == "timezone":
                settings.timezone = kv.value
            elif key == "date_calendar":
                from .calendar_utils import normalize_date_calendar
                settings.date_calendar = normalize_date_calendar(kv.value)
    finally:
        db.close()
    
    # Seed default Telegram notification config rows
    from .models import TelegramNotificationConfig
    _default_events = [
        "quota_warning_80", "quota_warning_90", "quota_hit",
        "quota_lifted", "daily_summary", "weekly_summary",
    ]
    db2 = SessionLocal()
    try:
        for evt in _default_events:
            if not db2.query(TelegramNotificationConfig).filter_by(event_type=evt).first():
                db2.add(TelegramNotificationConfig(event_type=evt))
        db2.commit()
    finally:
        db2.close()

    # Clear a maintenance status left "running" by a crashed/restarted process
    from .usage_maintenance import reset_stale_usage_maintenance_status
    reset_stale_usage_maintenance_status()

    ensure_scheduler()

    # Start Telegram bot if configured
    try:
        from .telegram.bot import start_bot
        start_bot()
    except Exception as e:
        print(f"Telegram bot start skipped: {e}")


app.include_router(api_router)


def _allow_during_exclusive_operation(request: Request) -> bool:
    path = request.url.path
    if path == "/health":
        return True
    if not path.startswith("/api/"):
        return True
    if path.startswith("/api/auth/"):
        return True
    if path == "/api/admin/usage_maintenance" and request.method == "GET":
        return True
    if path == "/api/admin/usage_maintenance/cancel" and request.method == "POST":
        return True
    if request.method == "GET" and (
        path == "/api/settings"
        or path == "/api/routers"
        or path == "/api/peers"
        or path.startswith("/api/fair-usage/")
    ):
        return True
    return request.method == "DELETE" and path.startswith("/api/routers/")

# Log exceptions to help diagnose 500s
@app.middleware("http")
async def block_during_exclusive_operation(request: Request, call_next):
    active = exclusive_operation_gate.snapshot()
    if active is not None and not _allow_during_exclusive_operation(request):
        detail = active.detail or f"{active.label} is in progress. Retry shortly."
        return JSONResponse(
            status_code=503,
            content={"detail": detail, "operation": active.key},
            headers={"Retry-After": "15"},
        )
    return await call_next(request)


@app.middleware("http")
async def log_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        # Simple print; uvicorn will also log traceback
        print(f"Unhandled error on {request.url}: {e}")
        raise
