import os
from typing import Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import StaticPool
from .settings import settings


class Base(DeclarativeBase):
    pass


db_url = settings.database_url
connect_args = {}
engine_kwargs = {}

if db_url.startswith("sqlite:"):
    connect_args = {"check_same_thread": False, "timeout": 30}
    if ":memory:" in db_url:
        engine_kwargs["poolclass"] = StaticPool

engine = create_engine(
    db_url,
    future=True,
    echo=False,
    pool_pre_ping=True,
    connect_args=connect_args,
    **engine_kwargs,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

RUNTIME_INDEX_STATEMENTS = {
    "ix_usage_samples_peer_id_ts": "CREATE INDEX IF NOT EXISTS ix_usage_samples_peer_id_ts ON usage_samples (peer_id, ts)",
    "ix_usage_minute_peer_id_minute_ts": "CREATE INDEX IF NOT EXISTS ix_usage_minute_peer_id_minute_ts ON usage_minute (peer_id, minute_ts)",
    "ix_peers_selected_router_id_id": "CREATE INDEX IF NOT EXISTS ix_peers_selected_router_id_id ON peers (selected, router_id, id)",
    "ix_peers_router_id_interface": "CREATE INDEX IF NOT EXISTS ix_peers_router_id_interface ON peers (router_id, interface)",
}


if db_url.startswith("sqlite:"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA temp_store=MEMORY")
        finally:
            cursor.close()


def prepare_sqlite_database():
    if not db_url.startswith("sqlite:") or ":memory:" in db_url:
        return

    with engine.begin() as conn:
        # WAL on Docker Desktop bind mounts is brittle enough to break auth and
        # normal reads under load. Use the conservative rollback journal mode.
        try:
            conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass
        conn.exec_driver_sql("PRAGMA journal_mode=DELETE")
        conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
        conn.exec_driver_sql("PRAGMA busy_timeout=30000")


def sqlite_database_path() -> Optional[str]:
    if not db_url.startswith("sqlite:") or ":memory:" in db_url:
        return None
    database = engine.url.database
    if not database:
        return None
    return os.path.abspath(database)


def missing_runtime_indexes() -> list[str]:
    with engine.connect() as conn:
        existing = set(
            conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'index'")
            ).scalars()
        )
    return [name for name in RUNTIME_INDEX_STATEMENTS if name not in existing]


def should_auto_bootstrap_runtime_indexes(max_db_bytes: int = 1_000_000_000) -> bool:
    missing = missing_runtime_indexes()
    if not missing:
        return False

    db_path = sqlite_database_path()
    if db_path and os.path.exists(db_path):
        try:
            size = os.path.getsize(db_path)
        except OSError:
            return True
        if size > max_db_bytes:
            print(
                f"Skipping automatic runtime index bootstrap for large SQLite database "
                f"({size} bytes). Missing indexes: {', '.join(missing)}"
            )
            return False
    return True


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_runtime_indexes():
    with engine.begin() as conn:
        for statement in RUNTIME_INDEX_STATEMENTS.values():
            conn.execute(text(statement))
