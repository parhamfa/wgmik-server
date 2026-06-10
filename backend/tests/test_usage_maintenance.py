import sqlite3
from datetime import datetime, timedelta, timezone

from backend.usage_maintenance import (
    _default_status,
    _phase_backfill,
    _phase_preflight,
    _phase_prune_table,
    _persist_status,
    _validate_rollups,
    load_usage_maintenance_status,
    normalize_auto_maintenance_settings,
    reset_stale_usage_maintenance_status,
    rotate_maintenance_backups,
)
from backend.scheduler import auto_maintenance_due
from backend.destructive_ops import exclusive_operation_gate
from backend.db import SessionLocal
from backend.models import Peer, Router, UsageMinute, UsageSample


def _dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def _create_usage_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE usage_samples (
            id INTEGER NOT NULL PRIMARY KEY,
            peer_id INTEGER NOT NULL,
            ts DATETIME NOT NULL,
            rx BIGINT NOT NULL,
            tx BIGINT NOT NULL,
            endpoint VARCHAR(255) DEFAULT ''
        );
        CREATE INDEX ix_usage_samples_ts ON usage_samples (ts);
        CREATE INDEX ix_usage_samples_peer_id_ts ON usage_samples (peer_id, ts);
        CREATE TABLE usage_minute (
            id INTEGER NOT NULL PRIMARY KEY,
            peer_id INTEGER NOT NULL,
            minute_ts DATETIME NOT NULL,
            rx BIGINT NOT NULL DEFAULT 0,
            tx BIGINT NOT NULL DEFAULT 0
        );
        CREATE UNIQUE INDEX uq_usage_minute_peer_ts ON usage_minute (peer_id, minute_ts);
        CREATE INDEX ix_usage_minute_minute_ts ON usage_minute (minute_ts);
        CREATE INDEX ix_usage_minute_peer_id_minute_ts ON usage_minute (peer_id, minute_ts);
        CREATE TABLE usage_daily (
            id INTEGER NOT NULL PRIMARY KEY,
            peer_id INTEGER NOT NULL,
            day VARCHAR(10) NOT NULL,
            rx BIGINT NOT NULL DEFAULT 0,
            tx BIGINT NOT NULL DEFAULT 0
        );
        CREATE INDEX ix_usage_daily_day ON usage_daily (day);
        """
    )
    return conn


def _seed_status(**overrides):
    status = _default_status()
    status.update(
        {
            "running": True,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "backup_pages_total": 0,
            "total_units": 1,
            "can_cancel": True,
        }
    )
    status.update(overrides)
    return _persist_status(status)


def test_sqlite_backfill_uses_precutoff_sample_and_counter_reset(tmp_path, client):
    db_path = tmp_path / "usage.db"
    conn = _create_usage_db(db_path)
    conn.executemany(
        "INSERT INTO usage_samples (id, peer_id, ts, rx, tx, endpoint) VALUES (?, ?, ?, ?, ?, '')",
        [
            (1, 1, "2026-01-01 00:00:00.000000", 100, 50),
            (2, 1, "2026-01-01 00:01:05.000000", 150, 90),
            (3, 1, "2026-01-01 00:01:30.000000", 10, 5),
            (4, 1, "2026-01-01 00:02:00.000000", 25, 15),
        ],
    )
    conn.commit()
    conn.close()

    cutoff = _dt("2026-01-01 00:01:00").replace(tzinfo=timezone.utc)
    _seed_status(
        phase="backfill",
        backfill_cutoff=cutoff.isoformat(),
        backfill_peer_counts=[[1, 3]],
        backfill_samples_total=3,
        total_units=3,
    )

    _phase_backfill(str(db_path))

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT peer_id, minute_ts, rx, tx FROM usage_minute ORDER BY minute_ts"
    ).fetchall()
    conn.close()
    assert rows == [
        (1, "2026-01-01 00:01:00.000000", 60, 45),
        (1, "2026-01-01 00:02:00.000000", 15, 10),
    ]
    conn = sqlite3.connect(db_path)
    lower_bound_total = conn.execute(
        "SELECT COALESCE(SUM(rx), 0), COALESCE(SUM(tx), 0) FROM usage_minute WHERE minute_ts >= ?",
        ("2026-01-01 00:01:00.000000",),
    ).fetchone()
    conn.close()
    assert lower_bound_total == (75, 55)


def test_prune_uses_retention_cutoff_and_preserves_newer_rows(tmp_path, client):
    db_path = tmp_path / "usage.db"
    conn = _create_usage_db(db_path)
    conn.executemany(
        "INSERT INTO usage_samples (id, peer_id, ts, rx, tx, endpoint) VALUES (?, 1, ?, 0, 0, '')",
        [
            (1, "2026-01-01 00:00:00.000000"),
            (2, "2026-01-02 00:00:00.000000"),
            (3, "2026-01-03 00:00:00.000000"),
        ],
    )
    conn.commit()
    conn.close()

    cutoff = _dt("2026-01-02 12:00:00").replace(tzinfo=timezone.utc)
    _seed_status(
        phase="prune_raw",
        raw_prune_before=cutoff.isoformat(),
        raw_prune_total=2,
        total_units=2,
    )

    _phase_prune_table(
        str(db_path),
        table="usage_samples",
        column="ts",
        index="ix_usage_samples_ts",
        cutoff_status_key="raw_prune_before",
        status_field="deleted_samples",
        total_key="raw_prune_total",
        phase="prune_raw",
        phase_label="Prune raw samples",
        detail_label="raw samples",
        processed_before=0,
    )

    conn = sqlite3.connect(db_path)
    ids = [row[0] for row in conn.execute("SELECT id FROM usage_samples ORDER BY id")]
    conn.close()
    assert ids == [3]


def test_validate_rollups_uses_minute_aligned_recent_window(client, monkeypatch):
    monkeypatch.setattr(
        "backend.usage_maintenance._utc_now",
        lambda: _dt("2026-06-02 08:50:04").replace(tzinfo=timezone.utc),
    )
    db = SessionLocal()
    try:
        router = Router(
            name="Asia",
            host="10.0.0.1",
            proto="rest",
            port=443,
            username="admin",
            secret_enc="secret",
        )
        db.add(router)
        db.flush()
        peer = Peer(
            router_id=router.id,
            interface="wgmik",
            ros_id="*1",
            name="sadra",
            public_key="pub",
            allowed_address="10.0.0.2/32",
        )
        db.add(peer)
        db.flush()
        db.add_all(
            [
                UsageSample(peer_id=peer.id, ts=_dt("2026-06-01 08:49:37"), rx=1000, tx=1000, endpoint=""),
                UsageSample(peer_id=peer.id, ts=_dt("2026-06-01 08:50:02"), rx=10600, tx=10600, endpoint=""),
                UsageSample(peer_id=peer.id, ts=_dt("2026-06-01 08:50:27"), rx=20600, tx=20600, endpoint=""),
                UsageMinute(peer_id=peer.id, minute_ts=_dt("2026-06-01 08:50:00"), rx=19600, tx=19600),
            ]
        )
        db.commit()

        _validate_rollups(
            db,
            {
                "backfill_cutoff": _dt("2026-03-04 08:49:02").replace(tzinfo=timezone.utc).isoformat()
            },
        )
    finally:
        db.close()


def test_preflight_clamps_backfill_cutoff_to_raw_sample_coverage(tmp_path, client, monkeypatch):
    """Minute rollups older than the oldest raw sample must never be deleted/rebuilt."""
    now = _dt("2026-06-01 12:00:00").replace(tzinfo=timezone.utc)
    monkeypatch.setattr("backend.usage_maintenance._utc_now", lambda: now)

    db_path = tmp_path / "usage.db"
    conn = _create_usage_db(db_path)
    # Oldest raw sample is only 1 day old; minute retention window is 90 days.
    conn.executemany(
        "INSERT INTO usage_samples (id, peer_id, ts, rx, tx, endpoint) VALUES (?, 1, ?, ?, ?, '')",
        [
            (1, "2026-05-31 12:00:30.000000", 100, 50),
            (2, "2026-05-31 12:01:05.000000", 200, 90),
        ],
    )
    # Old minute history with no surviving raw samples.
    conn.execute(
        "INSERT INTO usage_minute (peer_id, minute_ts, rx, tx) VALUES (1, '2026-04-01 00:00:00.000000', 999, 888)"
    )
    conn.commit()
    conn.close()

    _seed_status()
    status = _phase_preflight(str(db_path))

    cutoff = datetime.fromisoformat(status["backfill_cutoff"])
    # Clamped to the minute after the oldest raw sample, not now-90d.
    assert cutoff == _dt("2026-05-31 12:01:00").replace(tzinfo=timezone.utc)

    _phase_backfill(str(db_path))
    conn = sqlite3.connect(db_path)
    old_row = conn.execute(
        "SELECT rx, tx FROM usage_minute WHERE minute_ts = '2026-04-01 00:00:00.000000'"
    ).fetchone()
    conn.close()
    assert old_row == (999, 888)


def test_preflight_and_backfill_with_no_raw_samples_keep_minute_rollups(tmp_path, client, monkeypatch):
    now = _dt("2026-06-01 12:00:00").replace(tzinfo=timezone.utc)
    monkeypatch.setattr("backend.usage_maintenance._utc_now", lambda: now)

    db_path = tmp_path / "usage.db"
    conn = _create_usage_db(db_path)
    conn.execute(
        "INSERT INTO usage_minute (peer_id, minute_ts, rx, tx) VALUES (1, '2026-05-30 00:00:00.000000', 11, 22)"
    )
    conn.commit()
    conn.close()

    _seed_status()
    status = _phase_preflight(str(db_path))
    assert status["backfill_samples_total"] == 0
    assert status["backfill_peer_counts"] == []

    _phase_backfill(str(db_path))
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT peer_id, rx, tx FROM usage_minute").fetchall()
    conn.close()
    assert rows == [(1, 11, 22)]


def test_reset_stale_usage_maintenance_status(client):
    _seed_status(phase="backup", phase_label="Backup")
    reset_stale_usage_maintenance_status()
    db = SessionLocal()
    try:
        status = load_usage_maintenance_status(db)
    finally:
        db.close()
    assert status["running"] is False
    assert status["phase"] == "failed"
    assert "restart" in (status["last_error"] or "").lower()

    # Idempotent when nothing is running.
    reset_stale_usage_maintenance_status()


def test_rotate_maintenance_backups_keeps_newest(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    names = [
        "wgmik-20260101-000000-maintenance-backup.db",
        "wgmik-20260201-000000-maintenance-backup.db",
        "wgmik-20260301-000000-maintenance-backup.db",
        "unrelated-file.db",
    ]
    for name in names:
        (backup_dir / name).write_bytes(b"x")

    removed = rotate_maintenance_backups(str(backup_dir), keep=2)
    assert removed == [str(backup_dir / "wgmik-20260101-000000-maintenance-backup.db")]
    remaining = sorted(p.name for p in backup_dir.iterdir())
    assert remaining == [
        "unrelated-file.db",
        "wgmik-20260201-000000-maintenance-backup.db",
        "wgmik-20260301-000000-maintenance-backup.db",
    ]


def test_normalize_auto_maintenance_settings_validates_inputs():
    data = normalize_auto_maintenance_settings(
        {
            "usage_maintenance_auto_enabled": "1",
            "usage_maintenance_auto_frequency": "bogus",
            "usage_maintenance_auto_interval_days": "99",
            "usage_maintenance_auto_weekday": "12",
            "usage_maintenance_auto_time": "25:99",
            "usage_maintenance_backup_keep": "0",
        }
    )
    assert data["usage_maintenance_auto_enabled"] is True
    assert data["usage_maintenance_auto_frequency"] == "daily"
    assert data["usage_maintenance_auto_interval_days"] == 30
    assert data["usage_maintenance_auto_weekday"] == 6
    assert data["usage_maintenance_auto_time"] == "03:00"
    assert data["usage_maintenance_backup_keep"] == 1


def test_auto_maintenance_due_frequencies():
    tz = timezone.utc
    now = datetime(2026, 6, 3, 4, 30, tzinfo=tz)  # Wednesday

    base = {
        "usage_maintenance_auto_enabled": True,
        "usage_maintenance_auto_frequency": "daily",
        "usage_maintenance_auto_interval_days": 3,
        "usage_maintenance_auto_weekday": 2,  # Wednesday
    }

    # Disabled never runs
    assert auto_maintenance_due({**base, "usage_maintenance_auto_enabled": False}, None, now) is False
    # Daily: due, but never twice the same day
    assert auto_maintenance_due(base, None, now) is True
    assert auto_maintenance_due(base, now - timedelta(days=1), now) is True
    assert auto_maintenance_due(base, now - timedelta(hours=1), now) is False
    # Weekly: only on the configured weekday
    weekly = {**base, "usage_maintenance_auto_frequency": "weekly"}
    assert auto_maintenance_due(weekly, None, now) is True
    assert auto_maintenance_due({**weekly, "usage_maintenance_auto_weekday": 3}, None, now) is False
    # Every N days: first run always due, then interval-gated
    every_n = {**base, "usage_maintenance_auto_frequency": "every_n_days"}
    assert auto_maintenance_due(every_n, None, now) is True
    assert auto_maintenance_due(every_n, now - timedelta(days=2), now) is False
    assert auto_maintenance_due(every_n, now - timedelta(days=3), now) is True


def test_cancel_endpoint_is_idempotent(client, monkeypatch):
    def fake_cancel():
        return {
            "running": False,
            "phase": "idle",
            "phase_label": "Idle",
            "backfilled_minutes": 0,
            "deleted_samples": 0,
            "deleted_minutes": 0,
            "deleted_daily": 0,
            "cancel_requested": False,
            "can_cancel": False,
            "elapsed_seconds": 0,
            "progress_percent": 0,
            "phase_progress_percent": 0,
            "processed_units": 0,
            "total_units": 0,
        }

    monkeypatch.setattr("backend.api.routes.cancel_usage_maintenance", fake_cancel)
    response = client.post("/api/admin/usage_maintenance/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["phase"] == "idle"


def test_auth_and_maintenance_status_are_available_during_exclusive_operation(client, monkeypatch):
    monkeypatch.setattr(
        "backend.api.routes.get_usage_maintenance_status",
        lambda: {
            "running": True,
            "phase": "backup",
            "phase_label": "Backup",
            "backfilled_minutes": 0,
            "deleted_samples": 0,
            "deleted_minutes": 0,
            "deleted_daily": 0,
            "cancel_requested": False,
            "can_cancel": True,
            "elapsed_seconds": 1,
            "progress_percent": 1,
            "phase_progress_percent": 1,
            "processed_units": 1,
            "total_units": 100,
        },
    )
    monkeypatch.setattr(
        "backend.api.routes.cancel_usage_maintenance",
        lambda: {
            "running": True,
            "phase": "backup",
            "phase_label": "Backup",
            "backfilled_minutes": 0,
            "deleted_samples": 0,
            "deleted_minutes": 0,
            "deleted_daily": 0,
            "cancel_requested": True,
            "can_cancel": False,
            "elapsed_seconds": 1,
            "progress_percent": 1,
            "phase_progress_percent": 1,
            "processed_units": 1,
            "total_units": 100,
        },
    )

    with exclusive_operation_gate.begin("test", "Test operation", "Blocked"):
        assert client.get("/api/auth/me").status_code == 200
        assert client.get("/api/admin/usage_maintenance").status_code == 200
        assert client.post("/api/admin/usage_maintenance/cancel").status_code == 200
        assert client.get("/api/settings").status_code == 200
        assert client.get("/api/routers").status_code == 200
        assert client.get("/api/peers").status_code == 200
        assert client.get("/api/fair-usage/rules").status_code == 200
        assert client.post("/api/fair-usage/rules", json={}).status_code == 503
