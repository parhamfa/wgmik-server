from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from .api.routes import router as api_router
from .bootstrap import ensure_initial_admin
from .scheduler import ensure_scheduler
from .db import (
    Base,
    engine,
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
    prepare_sqlite_database()
    if settings.database_url.startswith("sqlite:") and ":memory:" not in settings.database_url:
        if should_auto_bootstrap_runtime_indexes():
            ensure_runtime_indexes()
    else:
        ensure_runtime_indexes()

    # Ensure there's at least one admin account on a fresh install
    ensure_initial_admin()
    
    # Hydrate runtime settings from DB BEFORE starting scheduler
    from .db import SessionLocal
    from .models import SettingsKV
    db = SessionLocal()
    try:
        for key in ("poll_interval_seconds", "online_threshold_seconds", "monthly_reset_day", "timezone"):
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
    finally:
        db.close()
    
    ensure_scheduler()


app.include_router(api_router)

# Log exceptions to help diagnose 500s
@app.middleware("http")
async def log_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        # Simple print; uvicorn will also log traceback
        print(f"Unhandled error on {request.url}: {e}")
        raise
