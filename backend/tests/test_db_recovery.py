import shutil
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.db_recovery import _rebuild_usage_rollups, recover_database


SCHEMA_SQL = """
CREATE TABLE routers (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    proto TEXT NOT NULL,
    port INTEGER NOT NULL,
    username TEXT NOT NULL,
    secret_enc TEXT NOT NULL,
    tls_verify INTEGER NOT NULL
);
CREATE TABLE peers (
    id INTEGER PRIMARY KEY,
    router_id INTEGER NOT NULL,
    interface TEXT NOT NULL,
    ros_id TEXT NOT NULL,
    name TEXT NOT NULL,
    public_key TEXT NOT NULL,
    allowed_address TEXT NOT NULL,
    comment TEXT NOT NULL,
    disabled INTEGER NOT NULL,
    selected INTEGER NOT NULL
);
CREATE TABLE quotas (
    id INTEGER PRIMARY KEY,
    peer_id INTEGER NOT NULL,
    monthly_limit_bytes INTEGER NOT NULL,
    reset_day INTEGER NOT NULL
);
CREATE TABLE settings_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    hashed_password TEXT NOT NULL,
    is_admin INTEGER NOT NULL,
    created_at DATETIME NOT NULL
);
CREATE TABLE usage_samples (
    id INTEGER PRIMARY KEY,
    peer_id INTEGER NOT NULL,
    ts DATETIME NOT NULL,
    rx INTEGER NOT NULL,
    tx INTEGER NOT NULL,
    endpoint TEXT NOT NULL
);
CREATE TABLE usage_minute (
    id INTEGER PRIMARY KEY,
    peer_id INTEGER NOT NULL,
    minute_ts DATETIME NOT NULL,
    rx INTEGER NOT NULL,
    tx INTEGER NOT NULL
);
CREATE UNIQUE INDEX uq_usage_minute_peer_ts ON usage_minute (peer_id, minute_ts);
CREATE INDEX ix_usage_minute_minute_ts ON usage_minute (minute_ts);
CREATE TABLE usage_daily (
    id INTEGER PRIMARY KEY,
    peer_id INTEGER NOT NULL,
    day TEXT NOT NULL,
    rx INTEGER NOT NULL,
    tx INTEGER NOT NULL
);
CREATE UNIQUE INDEX uq_daily_peer_day ON usage_daily (peer_id, day);
CREATE TABLE usage_monthly (
    id INTEGER PRIMARY KEY,
    peer_id INTEGER NOT NULL,
    month_key TEXT NOT NULL,
    rx INTEGER NOT NULL,
    tx INTEGER NOT NULL
);
CREATE UNIQUE INDEX uq_month_peer ON usage_monthly (peer_id, month_key);
CREATE TABLE actions (
    id INTEGER PRIMARY KEY,
    peer_id INTEGER,
    ts DATETIME NOT NULL,
    action TEXT NOT NULL,
    note TEXT NOT NULL
);
CREATE INDEX ix_usage_samples_ts ON usage_samples (ts);
CREATE INDEX ix_usage_samples_peer_id_ts ON usage_samples (peer_id, ts);
"""


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _create_backup_db(path: Path, now: datetime) -> None:
    with _connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO routers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "Router A", "10.0.0.1", "rest", 443, "admin", "secret", 1),
        )
        conn.execute(
            "INSERT INTO peers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 1, "wg0", "*1", "peer-a", "pub-a", "10.0.0.10/32", "", 0, 1),
        )
        conn.execute(
            "INSERT INTO settings_kv VALUES (?, ?)",
            ("peer_default_scope_unit", "minutes"),
        )
        conn.execute(
            "INSERT INTO settings_kv VALUES (?, ?)",
            ("raw_sample_retention_hours", "24"),
        )
        conn.execute(
            "INSERT INTO settings_kv VALUES (?, ?)",
            ("minute_rollup_retention_days", "90"),
        )
        conn.execute(
            "INSERT INTO settings_kv VALUES (?, ?)",
            ("daily_rollup_retention_days", "0"),
        )
        conn.execute(
            "INSERT INTO users VALUES (?, ?, ?, ?, ?)",
            (1, "admin", "hash", 1, now.strftime("%Y-%m-%d %H:%M:%S")),
        )
        base_rows = [
            (1, 1, (now - timedelta(days=2, hours=2)).strftime("%Y-%m-%d %H:%M:%S"), 100, 50, ""),
            (2, 1, (now - timedelta(days=2, hours=1)).strftime("%Y-%m-%d %H:%M:%S"), 180, 110, ""),
            (3, 1, (now - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"), 240, 150, ""),
        ]
        conn.executemany(
            "INSERT INTO usage_samples (id, peer_id, ts, rx, tx, endpoint) VALUES (?, ?, ?, ?, ?, ?)",
            base_rows,
        )
        conn.execute(
            "INSERT INTO usage_samples (id, peer_id, ts, rx, tx, endpoint) VALUES (?, ?, ?, ?, ?, ?)",
            (6, 99, (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"), 10, 10, ""),
        )
        conn.execute(
            "INSERT INTO usage_daily (id, peer_id, day, rx, tx) VALUES (?, ?, ?, ?, ?)",
            (1, 99, (now - timedelta(days=1)).strftime("%Y-%m-%d"), 10, 10),
        )
        conn.execute(
            "INSERT INTO actions VALUES (?, ?, ?, ?, ?)",
            (1, 1, (now - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S"), "seed", "backup"),
        )
        conn.commit()


def test_rebuild_usage_rollups_quarantines_near_32bit_counter_spike(tmp_path):
    db_path = tmp_path / "rollups.db"
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA_SQL)
        conn.execute(
            "INSERT INTO routers VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "Router A", "10.0.0.1", "rest", 443, "admin", "secret", 1),
        )
        conn.execute(
            "INSERT INTO peers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, 1, "wg0", "*1", "peer-a", "pub-a", "10.0.0.10/32", "", 0, 1),
        )
        conn.executemany(
            "INSERT INTO usage_samples (id, peer_id, ts, rx, tx, endpoint) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (1, 1, "2026-06-16 00:00:00", 100, 4_200_000_000, ""),
                (2, 1, "2026-06-16 00:01:00", 150, 12_000_000, ""),
                (3, 1, "2026-06-16 00:02:00", 200, 1_500_000_000, ""),
            ],
        )
        conn.commit()

        _rebuild_usage_rollups(conn, minute_cutoff=datetime(2026, 6, 16, tzinfo=timezone.utc))

        daily = conn.execute("SELECT rx, tx FROM usage_daily").fetchone()
        monthly = conn.execute("SELECT rx, tx FROM usage_monthly").fetchone()
        minute = conn.execute("SELECT COALESCE(SUM(rx), 0) AS rx, COALESCE(SUM(tx), 0) AS tx FROM usage_minute").fetchone()

    assert (daily["rx"], daily["tx"]) == (100, 0)
    assert (monthly["rx"], monthly["tx"]) == (100, 0)
    assert (minute["rx"], minute["tx"]) == (100, 0)


def test_recover_database_salvages_delta_rebuilds_rollups_and_prunes_raw(tmp_path):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    clean_backup = tmp_path / "clean.db"
    corrupt_source = tmp_path / "corrupt.db"
    output = tmp_path / "recovered.db"

    _create_backup_db(clean_backup, now)
    shutil.copy2(clean_backup, corrupt_source)

    with _connect(corrupt_source) as conn:
        conn.execute("UPDATE peers SET disabled = 1 WHERE id = 1")
        conn.execute("UPDATE settings_kv SET value = 'hours' WHERE key = 'peer_default_scope_unit'")
        conn.executemany(
            "INSERT INTO usage_samples (id, peer_id, ts, rx, tx, endpoint) VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (7, 1, (now - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"), 300, 210, ""),
                    (8, 1, (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S"), 20, 5, ""),
                ],
            )
        conn.execute(
            "INSERT INTO actions VALUES (?, ?, ?, ?, ?)",
            (2, 1, (now - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"), "router_disable", "delta"),
        )
        conn.commit()

    result = recover_database(
        clean_backup_path=str(clean_backup),
        corrupt_db_path=str(corrupt_source),
        output_path=str(output),
        work_dir=str(tmp_path / "work"),
    )

    assert result.quick_check == "ok"
    assert result.salvaged_samples == 2
    assert result.salvaged_actions == 1
    assert result.usage_samples_max_ts == (now - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")

    with _connect(output) as conn:
        peer_row = conn.execute("SELECT disabled FROM peers WHERE id = 1").fetchone()
        assert int(peer_row["disabled"]) == 1

        setting_row = conn.execute(
            "SELECT value FROM settings_kv WHERE key = 'peer_default_scope_unit'"
        ).fetchone()
        assert setting_row["value"] == "hours"

        sample_count = conn.execute("SELECT COUNT(*) AS count FROM usage_samples").fetchone()
        assert int(sample_count["count"]) == 3

        daily_rows = conn.execute(
            "SELECT day, rx, tx FROM usage_daily ORDER BY day"
        ).fetchall()
        assert len(daily_rows) == 2
        assert [(row["rx"], row["tx"]) for row in daily_rows] == [(80, 60), (120, 100)]

        monthly_rows = conn.execute(
            "SELECT month_key, rx, tx FROM usage_monthly ORDER BY month_key"
        ).fetchall()
        assert len(monthly_rows) == 1
        assert (monthly_rows[0]["rx"], monthly_rows[0]["tx"]) == (200, 160)

        minute_rows = conn.execute(
            "SELECT COUNT(*) AS count, MIN(minute_ts) AS min_ts, MAX(minute_ts) AS max_ts FROM usage_minute"
        ).fetchone()
        assert int(minute_rows["count"]) >= 3
        assert minute_rows["min_ts"] is not None
        assert minute_rows["max_ts"] is not None

        orphan_counts = conn.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM usage_samples WHERE peer_id = 99) AS raw_orphans,
                (SELECT COUNT(*) FROM usage_daily WHERE peer_id = 99) AS daily_orphans
            """
        ).fetchone()
        assert int(orphan_counts["raw_orphans"]) == 0
        assert int(orphan_counts["daily_orphans"]) == 0

        actions = conn.execute("SELECT MAX(id) AS max_id FROM actions").fetchone()
        assert int(actions["max_id"]) == 2
