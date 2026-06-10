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
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA temp_store=MEMORY")
        finally:
            cursor.close()


def ensure_fair_usage_scope_columns() -> None:
    """Add scope_period_* columns and backfill from legacy time_scope (SQLite)."""
    if not db_url.startswith("sqlite:"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(fair_usage_rules)")).fetchall()
        col_names = {r[1] for r in rows}
        if "scope_period_count" not in col_names:
            conn.execute(
                text("ALTER TABLE fair_usage_rules ADD COLUMN scope_period_count INTEGER DEFAULT 1")
            )
        if "scope_period_unit" not in col_names:
            conn.execute(
                text("ALTER TABLE fair_usage_rules ADD COLUMN scope_period_unit VARCHAR(8) DEFAULT 'month'")
            )
    db = SessionLocal()
    try:
        db.execute(
            text(
                """
                UPDATE fair_usage_rules
                SET
                    scope_period_count = COALESCE(NULLIF(scope_period_count, 0), 1),
                    scope_period_unit = CASE
                        WHEN scope_period_unit IS NOT NULL AND LENGTH(TRIM(scope_period_unit)) > 0
                            THEN scope_period_unit
                        ELSE CASE time_scope
                            WHEN 'hourly' THEN 'hour'
                            WHEN 'daily' THEN 'day'
                            WHEN 'weekly' THEN 'week'
                            WHEN 'monthly' THEN 'month'
                            ELSE 'month'
                        END
                    END
                """
            )
        )
        db.commit()
    finally:
        db.close()


def ensure_router_enabled_column() -> None:
    """Add `routers.enabled` (default 1) when running on legacy SQLite databases."""
    if not db_url.startswith("sqlite:"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(routers)")).fetchall()
        col_names = {r[1] for r in rows}
        if "enabled" not in col_names:
            conn.execute(text("ALTER TABLE routers ADD COLUMN enabled BOOLEAN DEFAULT 1 NOT NULL"))


def ensure_router_version_columns() -> None:
    """Add RouterOS compatibility fields for legacy SQLite databases."""
    if not db_url.startswith("sqlite:"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(routers)")).fetchall()
        col_names = {r[1] for r in rows}
        if "ros_version" not in col_names:
            conn.execute(text("ALTER TABLE routers ADD COLUMN ros_version VARCHAR(64) DEFAULT '' NOT NULL"))
        if "ros_version_checked_at" not in col_names:
            conn.execute(text("ALTER TABLE routers ADD COLUMN ros_version_checked_at DATETIME"))
        if "ros_supported" not in col_names:
            conn.execute(text("ALTER TABLE routers ADD COLUMN ros_supported BOOLEAN DEFAULT 0 NOT NULL"))


def ensure_peer_router_sync_columns() -> None:
    """Add peer drift state fields for legacy SQLite databases."""
    if not db_url.startswith("sqlite:"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(peers)")).fetchall()
        col_names = {r[1] for r in rows}
        if "router_sync_status" not in col_names:
            conn.execute(text("ALTER TABLE peers ADD COLUMN router_sync_status VARCHAR(16) DEFAULT 'synced' NOT NULL"))
        if "router_sync_first_seen_at" not in col_names:
            conn.execute(text("ALTER TABLE peers ADD COLUMN router_sync_first_seen_at DATETIME"))
        if "router_sync_last_seen_at" not in col_names:
            conn.execute(text("ALTER TABLE peers ADD COLUMN router_sync_last_seen_at DATETIME"))


def ensure_user_auth_schema() -> None:
    """Add user-account hardening columns and audit table for legacy SQLite databases."""
    if not db_url.startswith("sqlite:"):
        return
    with engine.begin() as conn:
        rows = conn.execute(text("PRAGMA table_info(users)")).fetchall()
        col_names = {r[1] for r in rows}
        if "is_active" not in col_names:
            conn.execute(text("ALTER TABLE users ADD COLUMN is_active BOOLEAN DEFAULT 1 NOT NULL"))
        if "session_version" not in col_names:
            conn.execute(text("ALTER TABLE users ADD COLUMN session_version INTEGER DEFAULT 1 NOT NULL"))
        if "password_changed_at" not in col_names:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_changed_at DATETIME"))
        if "last_login_at" not in col_names:
            conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at DATETIME"))
        if "failed_login_attempts" not in col_names:
            conn.execute(text("ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER DEFAULT 0 NOT NULL"))
        if "locked_until" not in col_names:
            conn.execute(text("ALTER TABLE users ADD COLUMN locked_until DATETIME"))
        if "must_change_password" not in col_names:
            conn.execute(text("ALTER TABLE users ADD COLUMN must_change_password BOOLEAN DEFAULT 0 NOT NULL"))
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS user_security_events (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    actor_user_id INTEGER,
                    target_user_id INTEGER,
                    event_type VARCHAR(64) NOT NULL,
                    detail VARCHAR DEFAULT '' NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(actor_user_id) REFERENCES users (id) ON DELETE SET NULL,
                    FOREIGN KEY(target_user_id) REFERENCES users (id) ON DELETE SET NULL
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_security_events_actor_user_id ON user_security_events (actor_user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_security_events_target_user_id ON user_security_events (target_user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_security_events_event_type ON user_security_events (event_type)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_user_security_events_created_at ON user_security_events (created_at)"))


def ensure_fair_usage_tier_schema() -> None:
    """Add tier/ordering columns used by fair-usage rules (SQLite migrations)."""
    if not db_url.startswith("sqlite:"):
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS fair_usage_tiers (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    rule_id INTEGER NOT NULL,
                    sort_order INTEGER DEFAULT 0 NOT NULL,
                    threshold_bytes BIGINT NOT NULL,
                    name VARCHAR(128) DEFAULT '' NOT NULL,
                    throttle_download_kbps INTEGER NOT NULL,
                    throttle_upload_kbps INTEGER NOT NULL,
                    FOREIGN KEY(rule_id) REFERENCES fair_usage_rules (id) ON DELETE CASCADE
                )
                """
            )
        )
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_fair_usage_tiers_rule_id ON fair_usage_tiers (rule_id)"))
        rows = conn.execute(text("PRAGMA table_info(fair_usage_rules)")).fetchall()
        col_names = {r[1] for r in rows}
        if "tiered" not in col_names:
            conn.execute(text("ALTER TABLE fair_usage_rules ADD COLUMN tiered BOOLEAN DEFAULT 0"))
        if "sort_order" not in col_names:
            conn.execute(text("ALTER TABLE fair_usage_rules ADD COLUMN sort_order INTEGER DEFAULT 0"))
        if "passthrough" not in col_names:
            conn.execute(text("ALTER TABLE fair_usage_rules ADD COLUMN passthrough BOOLEAN DEFAULT 0"))
        rows_s = conn.execute(text("PRAGMA table_info(fair_usage_state)")).fetchall()
        col_names_s = {r[1] for r in rows_s}
        if "tier_id" not in col_names_s:
            conn.execute(text("ALTER TABLE fair_usage_state ADD COLUMN tier_id INTEGER REFERENCES fair_usage_tiers (id) ON DELETE SET NULL"))
        conn.execute(
            text(
                """
                UPDATE fair_usage_rules
                SET sort_order = CASE
                    WHEN sort_order IS NULL OR sort_order = 0 THEN id
                    ELSE sort_order
                END
                """
            )
        )


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
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")


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
