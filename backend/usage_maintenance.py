from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import SessionLocal, engine, prepare_sqlite_database, sqlite_database_path
from .destructive_ops import exclusive_operation_gate
from .fair_usage_usage import app_zoneinfo
from .models import Peer, SettingsKV, UsageDaily, UsageMinute, UsageSample
from .scheduler import pause_scheduler, resume_scheduler
from .usage_deltas import CounterQuarantineState, counter_day_key, counter_delta, guarded_delta_sql, near_32bit_drop_sql
from .usage_storage import floor_to_minute_utc


STATUS_KEY = "usage_maintenance_status"
DEFAULT_STATUS: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "phase_label": "Idle",
    "started_at": None,
    "finished_at": None,
    "updated_at": None,
    "cancelled_at": None,
    "last_error": None,
    "last_completed_phase": None,
    "resume_cursor": None,
    "detail": None,
    "backup_path": None,
    "file_size_before": None,
    "file_size_after": None,
    "backfilled_minutes": 0,
    "deleted_samples": 0,
    "deleted_minutes": 0,
    "deleted_daily": 0,
    "backfill_cutoff": None,
    "raw_prune_before": None,
    "minute_prune_before": None,
    "daily_prune_before": None,
    "cancel_requested": False,
    "can_cancel": False,
    "trigger": "manual",
    "elapsed_seconds": 0,
    "estimated_remaining_seconds": None,
    "progress_percent": 0.0,
    "phase_progress_percent": 0.0,
    "processed_units": 0,
    "total_units": 0,
    "phase_processed_units": 0,
    "phase_total_units": 0,
    "backup_pages_total": 0,
    "backfill_samples_total": 0,
    "raw_prune_total": 0,
    "minute_prune_total": 0,
    "daily_prune_total": 0,
    "backfill_peer_counts": [],
}
DEFAULT_RETENTION = {
    "raw_sample_retention_hours": 24,
    "minute_rollup_retention_days": 90,
    "daily_rollup_retention_days": 0,
}
AUTO_FREQUENCIES = ("daily", "every_n_days", "weekly")
DEFAULT_AUTO_SCHEDULE: dict[str, Any] = {
    "usage_maintenance_auto_enabled": True,
    "usage_maintenance_auto_frequency": "daily",
    "usage_maintenance_auto_interval_days": 2,
    "usage_maintenance_auto_weekday": 6,  # 0=Monday ... 6=Sunday
    "usage_maintenance_auto_time": "03:00",
    "usage_maintenance_backup_keep": 2,
}
LAST_AUTO_RUN_KEY = "usage_maintenance_last_auto_run"
BACKUP_FILE_SUFFIX = "-maintenance-backup.db"
DELETE_BATCH_SIZE = 100_000
COMPACT_UNITS = 1
VALIDATE_UNITS = 1

_maintenance_lock = threading.Lock()
_maintenance_thread: Optional[threading.Thread] = None
_cancel_event = threading.Event()
_active_conn_lock = threading.Lock()
_active_conn: Optional[sqlite3.Connection] = None
_runtime_status_lock = threading.Lock()
_runtime_status: Optional[dict[str, Any]] = None


class UsageMaintenanceCancelled(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=None)


def _sqlite_ts(dt: datetime) -> str:
    return _naive_utc(dt).strftime("%Y-%m-%d %H:%M:%S.%f")


def _parse_sqlite_ts(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return datetime.fromisoformat(value.replace("T", " ")).replace(tzinfo=None)


def _default_status() -> dict[str, Any]:
    return dict(DEFAULT_STATUS)


def _decorate_status(status: dict[str, Any]) -> dict[str, Any]:
    data = _default_status()
    data.update(status or {})
    started = _parse_iso(data.get("started_at"))
    finished = _parse_iso(data.get("finished_at"))
    end = finished or (_utc_now() if data.get("running") else None)
    if started and end:
        data["elapsed_seconds"] = max(0, int((end - started).total_seconds()))
    else:
        data["elapsed_seconds"] = int(data.get("elapsed_seconds") or 0)

    processed = max(0, int(data.get("processed_units") or 0))
    total = max(0, int(data.get("total_units") or 0))
    phase_processed = max(0, int(data.get("phase_processed_units") or 0))
    phase_total = max(0, int(data.get("phase_total_units") or 0))
    data["progress_percent"] = round(min(100.0, (processed / total) * 100), 1) if total else 0.0
    data["phase_progress_percent"] = round(min(100.0, (phase_processed / phase_total) * 100), 1) if phase_total else 0.0

    if data.get("running") and phase_processed > 0 and phase_total > phase_processed:
        # Phase-local ETA is less misleading than mixing backup pages with row counts.
        rate = data["elapsed_seconds"] / phase_processed if phase_processed else 0
        data["estimated_remaining_seconds"] = int((phase_total - phase_processed) * rate) if rate > 0 else None
    elif data.get("running"):
        data["estimated_remaining_seconds"] = None
    else:
        data["estimated_remaining_seconds"] = 0 if data.get("phase") == "complete" else None
    return data


def load_usage_maintenance_status(db: Session) -> dict[str, Any]:
    data = _default_status()
    kv = db.get(SettingsKV, STATUS_KEY)
    if not kv or not kv.value:
        return data
    try:
        parsed = json.loads(kv.value)
        if isinstance(parsed, dict):
            data.update(parsed)
    except Exception:
        return data
    return data


def save_usage_maintenance_status(db: Session, status: dict[str, Any]) -> dict[str, Any]:
    merged = _default_status()
    merged.update(status or {})
    merged["updated_at"] = _iso(_utc_now())
    payload = json.dumps(merged)
    kv = db.get(SettingsKV, STATUS_KEY)
    if kv is None:
        kv = SettingsKV(key=STATUS_KEY, value=payload)
        db.add(kv)
    else:
        kv.value = payload
    return merged


def _set_runtime_status(status: Optional[dict[str, Any]]) -> None:
    with _runtime_status_lock:
        global _runtime_status
        _runtime_status = dict(status) if status else None


def _merged_runtime_status(status: dict[str, Any]) -> dict[str, Any]:
    with _runtime_status_lock:
        if not _runtime_status:
            return status
        merged = dict(status)
        merged.update(_runtime_status)
        return merged


def _persist_status(status: dict[str, Any]) -> dict[str, Any]:
    db = SessionLocal()
    try:
        saved = save_usage_maintenance_status(db, status)
        db.commit()
        _set_runtime_status(saved if saved.get("running") else None)
        return saved
    finally:
        db.close()


def get_usage_maintenance_status() -> dict[str, Any]:
    db = SessionLocal()
    try:
        return _decorate_status(_merged_runtime_status(load_usage_maintenance_status(db)))
    finally:
        db.close()


def is_usage_maintenance_running(db: Session) -> bool:
    return bool(load_usage_maintenance_status(db).get("running"))


def reset_stale_usage_maintenance_status() -> None:
    """Clear a leftover 'running' status after a crash/restart.

    Called once at process startup, before any maintenance thread can exist. If the
    persisted status still says running, the previous process died mid-run and the
    flag would otherwise block manual and scheduled maintenance forever.
    """
    db = SessionLocal()
    try:
        status = load_usage_maintenance_status(db)
        if not status.get("running"):
            return
        status.update(
            {
                "running": False,
                "phase": "failed",
                "phase_label": "Failed",
                "finished_at": _iso(_utc_now()),
                "cancel_requested": False,
                "can_cancel": False,
                "last_error": "Interrupted by application restart",
                "detail": "Usage maintenance was interrupted by an application restart. Run it again.",
            }
        )
        save_usage_maintenance_status(db, status)
        db.commit()
        _set_runtime_status(None)
    finally:
        db.close()


def load_retention_settings(db: Session) -> dict[str, int]:
    values = dict(DEFAULT_RETENTION)
    for key, default in DEFAULT_RETENTION.items():
        kv = db.get(SettingsKV, key)
        if not kv or kv.value is None:
            continue
        try:
            values[key] = int(kv.value)
        except Exception:
            values[key] = default
    values["raw_sample_retention_hours"] = max(1, min(24 * 365, values["raw_sample_retention_hours"]))
    values["minute_rollup_retention_days"] = max(1, min(3650, values["minute_rollup_retention_days"]))
    values["daily_rollup_retention_days"] = max(0, min(36500, values["daily_rollup_retention_days"]))
    return values


def normalize_auto_maintenance_settings(values: dict[str, Any]) -> dict[str, Any]:
    data = dict(DEFAULT_AUTO_SCHEDULE)
    data.update(values or {})

    raw_enabled = data.get("usage_maintenance_auto_enabled")
    if isinstance(raw_enabled, str):
        enabled = raw_enabled.strip().lower() in ("1", "true", "yes", "on")
    else:
        enabled = bool(raw_enabled)
    data["usage_maintenance_auto_enabled"] = enabled

    freq = str(data.get("usage_maintenance_auto_frequency") or "daily").strip().lower()
    data["usage_maintenance_auto_frequency"] = freq if freq in AUTO_FREQUENCIES else "daily"

    try:
        interval = int(data.get("usage_maintenance_auto_interval_days"))
    except Exception:
        interval = int(DEFAULT_AUTO_SCHEDULE["usage_maintenance_auto_interval_days"])
    data["usage_maintenance_auto_interval_days"] = max(2, min(30, interval))

    try:
        weekday = int(data.get("usage_maintenance_auto_weekday"))
    except Exception:
        weekday = int(DEFAULT_AUTO_SCHEDULE["usage_maintenance_auto_weekday"])
    data["usage_maintenance_auto_weekday"] = max(0, min(6, weekday))

    time_value = str(data.get("usage_maintenance_auto_time") or "").strip()
    try:
        hh, mm = time_value.split(":")
        hour, minute = int(hh), int(mm)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(time_value)
        data["usage_maintenance_auto_time"] = f"{hour:02d}:{minute:02d}"
    except Exception:
        data["usage_maintenance_auto_time"] = DEFAULT_AUTO_SCHEDULE["usage_maintenance_auto_time"]

    try:
        keep = int(data.get("usage_maintenance_backup_keep"))
    except Exception:
        keep = int(DEFAULT_AUTO_SCHEDULE["usage_maintenance_backup_keep"])
    data["usage_maintenance_backup_keep"] = max(1, min(50, keep))
    return data


def load_auto_maintenance_settings(db: Session) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for key in DEFAULT_AUTO_SCHEDULE:
        kv = db.get(SettingsKV, key)
        if kv is not None and kv.value is not None:
            values[key] = kv.value
    return normalize_auto_maintenance_settings(values)


def get_last_auto_run(db: Session) -> Optional[str]:
    kv = db.get(SettingsKV, LAST_AUTO_RUN_KEY)
    return kv.value if kv and kv.value else None


def record_last_auto_run(db: Session, when: Optional[datetime] = None) -> None:
    value = _iso(when or _utc_now())
    kv = db.get(SettingsKV, LAST_AUTO_RUN_KEY)
    if kv is None:
        db.add(SettingsKV(key=LAST_AUTO_RUN_KEY, value=value))
    else:
        kv.value = value


def rotate_maintenance_backups(backup_dir: str, keep: int) -> list[str]:
    """Delete oldest maintenance backups, keeping the newest `keep`. Returns removed paths."""
    keep = max(1, int(keep))
    try:
        entries = [
            name
            for name in os.listdir(backup_dir)
            if name.startswith("wgmik-") and name.endswith(BACKUP_FILE_SUFFIX)
        ]
    except FileNotFoundError:
        return []
    # Filenames embed a sortable UTC timestamp (wgmik-YYYYMMDD-HHMMSS-...).
    entries.sort(reverse=True)
    removed: list[str] = []
    for name in entries[keep:]:
        path = os.path.join(backup_dir, name)
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    return removed


def start_usage_maintenance(trigger: str = "manual") -> tuple[bool, dict[str, Any]]:
    global _maintenance_thread
    db_path = sqlite_database_path()
    if not db_path:
        raise RuntimeError("Usage maintenance requires a file-based SQLite database")
    if not os.path.exists(db_path):
        raise RuntimeError(f"Database file not found: {db_path}")

    with _maintenance_lock:
        db = SessionLocal()
        try:
            status = load_usage_maintenance_status(db)
            if status.get("running") or (_maintenance_thread and _maintenance_thread.is_alive()):
                return False, _decorate_status(status)

            _cancel_event.clear()
            now = _utc_now()
            status = _default_status()
            status.update(
                {
                    "running": True,
                    "phase": "queued",
                    "phase_label": "Queued",
                    "started_at": _iso(now),
                    "finished_at": None,
                    "cancelled_at": None,
                    "last_error": None,
                    "detail": "Usage maintenance is queued.",
                    "can_cancel": True,
                    "trigger": "scheduled" if trigger == "scheduled" else "manual",
                }
            )
            saved = save_usage_maintenance_status(db, status)
            db.commit()
            _set_runtime_status(saved)
        finally:
            db.close()

        _maintenance_thread = threading.Thread(
            target=_maintenance_worker,
            name="usage-maintenance",
            daemon=True,
        )
        _maintenance_thread.start()
        return True, _decorate_status(saved)


def cancel_usage_maintenance() -> dict[str, Any]:
    _cancel_event.set()
    with _active_conn_lock:
        if _active_conn is not None:
            try:
                _active_conn.interrupt()
            except Exception:
                pass

    db = SessionLocal()
    try:
        status = _merged_runtime_status(load_usage_maintenance_status(db))
        if not status.get("running"):
            return _decorate_status(status)
        status.update(
            {
                "cancel_requested": True,
                "can_cancel": False,
                "detail": "Cancellation requested. Waiting for the current SQLite step to stop.",
            }
        )
        saved = save_usage_maintenance_status(db, status)
        db.commit()
        _set_runtime_status(saved)
        return _decorate_status(saved)
    finally:
        db.close()


def run_usage_maintenance_once() -> dict[str, Any]:
    db_path = sqlite_database_path()
    if not db_path:
        raise RuntimeError("Usage maintenance requires a file-based SQLite database")
    if not os.path.exists(db_path):
        raise RuntimeError(f"Database file not found: {db_path}")

    pause_scheduler()
    try:
        with exclusive_operation_gate.begin(
            "usage_maintenance",
            "Usage maintenance",
            "Usage maintenance is running. Wait for it to finish before changing usage data.",
        ):
            _run_usage_maintenance(db_path)
    finally:
        resume_scheduler()
        global _maintenance_thread
        with _maintenance_lock:
            _maintenance_thread = None
        with _active_conn_lock:
            global _active_conn
            _active_conn = None
    return get_usage_maintenance_status()


def _maintenance_worker() -> None:
    try:
        run_usage_maintenance_once()
    except UsageMaintenanceCancelled:
        _mark_cancelled()
    except sqlite3.OperationalError as exc:
        if _cancel_event.is_set():
            _mark_cancelled()
        else:
            _mark_failed(str(exc))
    except Exception as exc:
        _mark_failed(str(exc))


def _mark_cancelled() -> None:
    db = SessionLocal()
    try:
        status = load_usage_maintenance_status(db)
        status.update(
            {
                "running": False,
                "phase": "cancelled",
                "phase_label": "Cancelled",
                "finished_at": _iso(_utc_now()),
                "cancelled_at": _iso(_utc_now()),
                "cancel_requested": False,
                "can_cancel": False,
                "last_error": "Usage maintenance was cancelled.",
                "resume_cursor": None,
                "detail": "Usage maintenance was cancelled. Raw samples remain available for a future rebuild.",
            }
        )
        save_usage_maintenance_status(db, status)
        db.commit()
        _set_runtime_status(None)
    finally:
        db.close()


def _mark_failed(message: str) -> None:
    db = SessionLocal()
    try:
        status = load_usage_maintenance_status(db)
        status.update(
            {
                "running": False,
                "phase": "failed",
                "phase_label": "Failed",
                "finished_at": _iso(_utc_now()),
                "cancel_requested": False,
                "can_cancel": False,
                "last_error": message,
                "detail": "Usage maintenance failed.",
            }
        )
        save_usage_maintenance_status(db, status)
        db.commit()
        _set_runtime_status(None)
    finally:
        db.close()


def _run_usage_maintenance(db_path: str) -> None:
    _check_cancel()
    status = _phase_preflight(db_path)
    _phase_backup(db_path, status)
    _phase_backfill(db_path)
    _phase_validate()
    _phase_prune_table(
        db_path,
        table="usage_samples",
        column="ts",
        index="ix_usage_samples_ts",
        cutoff_status_key="raw_prune_before",
        status_field="deleted_samples",
        total_key="raw_prune_total",
        phase="prune_raw",
        phase_label="Prune raw samples",
        detail_label="raw samples",
        processed_before=_units_before("prune_raw", status),
    )
    status = get_usage_maintenance_status()
    _phase_prune_table(
        db_path,
        table="usage_minute",
        column="minute_ts",
        index="ix_usage_minute_minute_ts",
        cutoff_status_key="minute_prune_before",
        status_field="deleted_minutes",
        total_key="minute_prune_total",
        phase="prune_minute",
        phase_label="Prune minute rollups",
        detail_label="minute rollups",
        processed_before=_units_before("prune_minute", status),
    )
    status = get_usage_maintenance_status()
    daily_total = int(status.get("daily_prune_total") or 0)
    if daily_total > 0:
        _phase_prune_table(
            db_path,
            table="usage_daily",
            column="day",
            index="ix_usage_daily_day",
            cutoff_status_key="daily_prune_before",
            status_field="deleted_daily",
            total_key="daily_prune_total",
            phase="prune_daily",
            phase_label="Prune daily rollups",
            detail_label="daily rollups",
            processed_before=_units_before("prune_daily", get_usage_maintenance_status()),
            day_string=True,
        )
    else:
        _update_progress(
            phase="prune_daily",
            phase_label="Prune daily rollups",
            detail="Daily rollup retention is set to keep forever.",
            phase_processed=0,
            phase_total=0,
            processed=_units_before("compact", get_usage_maintenance_status()),
            last_completed_phase="prune_daily",
        )
    _phase_compact(db_path)

    db = SessionLocal()
    try:
        status = load_usage_maintenance_status(db)
        total = int(status.get("total_units") or 0)
        status.update(
            {
                "running": False,
                "phase": "complete",
                "phase_label": "Complete",
                "finished_at": _iso(_utc_now()),
                "last_error": None,
                "last_completed_phase": "compact",
                "resume_cursor": None,
                "cancel_requested": False,
                "can_cancel": False,
                "detail": "Usage maintenance completed successfully.",
                "processed_units": total,
                "phase_processed_units": 1,
                "phase_total_units": 1,
            }
        )
        save_usage_maintenance_status(db, status)
        db.commit()
        _set_runtime_status(None)
    finally:
        db.close()


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA temp_store=FILE")
    return conn


def _set_active_conn(conn: Optional[sqlite3.Connection]) -> None:
    with _active_conn_lock:
        global _active_conn
        _active_conn = conn


def _check_cancel() -> None:
    if _cancel_event.is_set():
        raise UsageMaintenanceCancelled()


def _phase_preflight(db_path: str) -> dict[str, Any]:
    db = SessionLocal()
    try:
        status = load_usage_maintenance_status(db)
        retention = load_retention_settings(db)
    finally:
        db.close()

    now = _utc_now()
    backfill_cutoff = _naive_utc(now - timedelta(days=retention["minute_rollup_retention_days"]))
    raw_prune_before = _naive_utc(now - timedelta(hours=retention["raw_sample_retention_hours"]))
    minute_prune_before = _naive_utc(now - timedelta(days=retention["minute_rollup_retention_days"]))
    daily_prune_before = (
        _naive_utc(now - timedelta(days=retention["daily_rollup_retention_days"]))
        if retention["daily_rollup_retention_days"] > 0
        else None
    )

    file_size_before = os.path.getsize(db_path)
    free_bytes = shutil.disk_usage(os.path.dirname(db_path)).free
    required_free = max(int(file_size_before * 2.2), 2 * 1024 * 1024 * 1024)
    if free_bytes < required_free:
        raise RuntimeError(
            f"Not enough free disk for maintenance. Need at least {required_free} bytes free, found {free_bytes}."
        )

    with _connect(db_path) as conn:
        _set_active_conn(conn)
        try:
            quick_check = conn.execute("PRAGMA quick_check").fetchone()
            if not quick_check or quick_check[0] != "ok":
                raise RuntimeError(f"Database quick_check failed: {quick_check[0] if quick_check else 'unknown'}")
            backup_pages_total = int(conn.execute("PRAGMA page_count").fetchone()[0] or 0)
            # Minute rollups can only be rebuilt where raw samples still exist. Clamp the
            # backfill window to the raw-sample coverage so older minute history (whose raw
            # samples were already pruned) is never deleted and lost.
            oldest_raw_value = conn.execute("SELECT MIN(ts) FROM usage_samples").fetchone()[0]
            if oldest_raw_value is None:
                backfill_cutoff = _naive_utc(now)
            else:
                oldest_raw_ts = _parse_sqlite_ts(str(oldest_raw_value))
                # Skip the (possibly partial) minute containing the oldest sample: its live
                # rollup may include deltas from samples that have since been pruned.
                raw_coverage_start = oldest_raw_ts.replace(second=0, microsecond=0) + timedelta(minutes=1)
                backfill_cutoff = max(backfill_cutoff, raw_coverage_start)
            peer_rows = conn.execute(
                """
                SELECT peer_id, COUNT(*) AS sample_count
                FROM usage_samples INDEXED BY ix_usage_samples_peer_id_ts
                WHERE ts >= ?
                GROUP BY peer_id
                ORDER BY peer_id
                """,
                (_sqlite_ts(backfill_cutoff),),
            ).fetchall()
            backfill_peer_counts = [[int(row["peer_id"]), int(row["sample_count"])] for row in peer_rows]
            backfill_samples_total = sum(row[1] for row in backfill_peer_counts)
            raw_prune_total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM usage_samples INDEXED BY ix_usage_samples_ts WHERE ts < ?",
                    (_sqlite_ts(raw_prune_before),),
                ).fetchone()[0]
                or 0
            )
            minute_prune_total = int(
                conn.execute(
                    "SELECT COUNT(*) FROM usage_minute INDEXED BY ix_usage_minute_minute_ts WHERE minute_ts < ?",
                    (_sqlite_ts(minute_prune_before),),
                ).fetchone()[0]
                or 0
            )
            daily_prune_total = 0
            if daily_prune_before is not None:
                daily_prune_total = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM usage_daily INDEXED BY ix_usage_daily_day WHERE day < ?",
                        (daily_prune_before.date().strftime("%Y-%m-%d"),),
                    ).fetchone()[0]
                    or 0
                )
        finally:
            _set_active_conn(None)

    total_units = (
        backup_pages_total
        + backfill_samples_total
        + raw_prune_total
        + minute_prune_total
        + daily_prune_total
        + VALIDATE_UNITS
        + COMPACT_UNITS
    )
    status.update(
        {
            "phase": "preflight",
            "phase_label": "Preflight",
            "detail": "Preflight checks completed.",
            "file_size_before": file_size_before,
            "backfill_cutoff": _iso(backfill_cutoff.replace(tzinfo=timezone.utc)),
            "raw_prune_before": _iso(raw_prune_before.replace(tzinfo=timezone.utc)),
            "minute_prune_before": _iso(minute_prune_before.replace(tzinfo=timezone.utc)),
            "daily_prune_before": _iso(daily_prune_before.replace(tzinfo=timezone.utc)) if daily_prune_before else None,
            "backup_pages_total": backup_pages_total,
            "backfill_samples_total": backfill_samples_total,
            "raw_prune_total": raw_prune_total,
            "minute_prune_total": minute_prune_total,
            "daily_prune_total": daily_prune_total,
            "backfill_peer_counts": backfill_peer_counts,
            "total_units": total_units,
            "processed_units": 0,
            "phase_processed_units": 1,
            "phase_total_units": 1,
            "last_completed_phase": "preflight",
            "can_cancel": True,
        }
    )
    return _persist_status(status)


def _phase_backup(db_path: str, status: dict[str, Any]) -> None:
    _check_cancel()
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(
        backup_dir,
        f"wgmik-{_utc_now().strftime('%Y%m%d-%H%M%S')}-maintenance-backup.db",
    )

    total_pages = int(status.get("backup_pages_total") or 0)
    _update_progress(
        phase="backup",
        phase_label="Backup",
        detail="Creating a database snapshot before maintenance.",
        phase_processed=0,
        phase_total=total_pages,
        processed=0,
        extra={"backup_path": backup_path},
    )

    source = _connect(db_path)
    target = sqlite3.connect(backup_path, timeout=30)
    _set_active_conn(source)
    last_update = 0.0

    def progress(_status: int, remaining: int, total: int) -> None:
        nonlocal last_update, total_pages
        _check_cancel()
        total_pages = max(total_pages, int(total or 0))
        done = max(0, total_pages - int(remaining or 0))
        now = time.monotonic()
        if now - last_update < 1 and remaining:
            return
        last_update = now
        try:
            _update_progress(
                phase="backup",
                phase_label="Backup",
                detail=f"Copied {done:,} of {total_pages:,} backup pages.",
                phase_processed=done,
                phase_total=total_pages,
                processed=done,
                extra={"backup_pages_total": total_pages, "backup_path": backup_path},
                persist=False,
            )
        except Exception:
            pass

    try:
        source.backup(target, pages=1000, progress=progress)
    except sqlite3.OperationalError:
        if _cancel_event.is_set():
            raise UsageMaintenanceCancelled()
        raise
    finally:
        _set_active_conn(None)
        target.close()
        source.close()

    # Rotate only after the fresh snapshot exists, so we never drop the last good backup.
    db = SessionLocal()
    try:
        keep = load_auto_maintenance_settings(db)["usage_maintenance_backup_keep"]
    finally:
        db.close()
    removed = rotate_maintenance_backups(backup_dir, keep)
    rotation_note = f" Removed {len(removed)} old backup(s)." if removed else ""

    _update_progress(
        phase="backup",
        phase_label="Backup",
        detail=f"Database snapshot created at {backup_path}.{rotation_note}",
        phase_processed=total_pages,
        phase_total=total_pages,
        processed=total_pages,
        extra={"backup_path": backup_path},
        last_completed_phase="backup",
    )


def _phase_backfill(db_path: str) -> None:
    _check_cancel()
    status = get_usage_maintenance_status()
    cutoff = _parse_iso(status.get("backfill_cutoff"))
    if cutoff is None:
        raise RuntimeError("Backfill cutoff is missing from maintenance status")
    cutoff_value = _sqlite_ts(cutoff)
    peer_counts = [(int(row[0]), int(row[1])) for row in (status.get("backfill_peer_counts") or [])]
    total_samples = int(status.get("backfill_samples_total") or 0)
    processed_before = _units_before("backfill", status)

    if not peer_counts and total_samples <= 0:
        # Nothing to rebuild; do not touch existing minute rollups.
        _update_progress(
            phase="backfill",
            phase_label="Backfill minute rollups",
            detail="No raw samples in the backfill window; minute rollups left untouched.",
            phase_processed=0,
            phase_total=0,
            processed=processed_before,
            extra={"backfilled_minutes": 0},
            last_completed_phase="backfill",
        )
        return

    conn = _connect(db_path)
    _set_active_conn(conn)
    try:
        _update_progress(
            phase="backfill",
            phase_label="Backfill minute rollups",
            detail="Clearing existing minute rollups in the backfill window.",
            phase_processed=0,
            phase_total=total_samples,
            processed=processed_before,
        )
        conn.execute("DELETE FROM usage_minute WHERE minute_ts >= ?", (cutoff_value,))
        conn.commit()

        processed_samples = 0
        backfilled_minutes = 0
        for peer_id, sample_count in peer_counts:
            _check_cancel()
            before_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM usage_minute WHERE peer_id = ? AND minute_ts >= ?",
                    (peer_id, cutoff_value),
                ).fetchone()[0]
                or 0
            )
            conn.execute(
                f"""
                INSERT INTO usage_minute (peer_id, minute_ts, rx, tx)
                SELECT peer_id, minute_ts, SUM(delta_rx), SUM(delta_tx)
                FROM (
                    SELECT
                        peer_id,
                        strftime('%Y-%m-%d %H:%M:00.000000', ts) AS minute_ts,
                        {guarded_delta_sql("rx", "prev_rx", "rx_unstable")} AS delta_rx,
                        {guarded_delta_sql("tx", "prev_tx", "tx_unstable")} AS delta_tx,
                        ts
                    FROM (
                        SELECT
                            peer_id,
                            id,
                            ts,
                            rx,
                            tx,
                            prev_rx,
                            prev_tx,
                            SUM(rx_near_32bit_drop) OVER (
                                PARTITION BY peer_id, substr(ts, 1, 10)
                                ORDER BY ts, id
                                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                            ) AS rx_unstable,
                            SUM(tx_near_32bit_drop) OVER (
                                PARTITION BY peer_id, substr(ts, 1, 10)
                                ORDER BY ts, id
                                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                            ) AS tx_unstable
                        FROM (
                            SELECT
                                peer_id,
                                id,
                                ts,
                                rx,
                                tx,
                                prev_rx,
                                prev_tx,
                                {near_32bit_drop_sql("rx", "prev_rx")} AS rx_near_32bit_drop,
                                {near_32bit_drop_sql("tx", "prev_tx")} AS tx_near_32bit_drop
                            FROM (
                                SELECT
                                    peer_id,
                                    id,
                                    ts,
                                    rx,
                                    tx,
                                    LAG(rx) OVER (ORDER BY ts, id) AS prev_rx,
                                    LAG(tx) OVER (ORDER BY ts, id) AS prev_tx
                                FROM usage_samples INDEXED BY ix_usage_samples_peer_id_ts
                                WHERE peer_id = ?
                                  AND (
                                    ts >= ?
                                    OR id = (
                                        SELECT id
                                        FROM usage_samples INDEXED BY ix_usage_samples_peer_id_ts
                                        WHERE peer_id = ? AND ts < ?
                                        ORDER BY ts DESC, id DESC
                                        LIMIT 1
                                    )
                                  )
                            )
                        )
                    )
                )
                WHERE ts >= ? AND (delta_rx <> 0 OR delta_tx <> 0)
                GROUP BY peer_id, minute_ts
                """,
                (peer_id, cutoff_value, peer_id, cutoff_value, cutoff_value),
            )
            conn.commit()
            after_count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM usage_minute WHERE peer_id = ? AND minute_ts >= ?",
                    (peer_id, cutoff_value),
                ).fetchone()[0]
                or 0
            )
            backfilled_minutes += max(0, after_count - before_count)
            processed_samples += sample_count
            _update_progress(
                phase="backfill",
                phase_label="Backfill minute rollups",
                detail=f"Backfilled peer {peer_id} ({processed_samples:,} of {total_samples:,} samples).",
                phase_processed=processed_samples,
                phase_total=total_samples,
                processed=processed_before + processed_samples,
                extra={"backfilled_minutes": backfilled_minutes},
            )
    except sqlite3.OperationalError:
        if _cancel_event.is_set():
            raise UsageMaintenanceCancelled()
        raise
    finally:
        _set_active_conn(None)
        conn.close()

    _update_progress(
        phase="backfill",
        phase_label="Backfill minute rollups",
        detail="Minute rollup backfill completed.",
        phase_processed=total_samples,
        phase_total=total_samples,
        processed=processed_before + total_samples,
        extra={"backfilled_minutes": backfilled_minutes},
        last_completed_phase="backfill",
    )


def _phase_validate() -> None:
    _check_cancel()
    status = get_usage_maintenance_status()
    processed = _units_before("validate", status) + VALIDATE_UNITS
    db = SessionLocal()
    try:
        _validate_rollups(db, status)
    finally:
        db.close()
    _update_progress(
        phase="validate",
        phase_label="Validate",
        detail="Minute rollup validation completed.",
        phase_processed=VALIDATE_UNITS,
        phase_total=VALIDATE_UNITS,
        processed=processed,
        last_completed_phase="validate",
    )


def _phase_prune_table(
    db_path: str,
    *,
    table: str,
    column: str,
    index: str,
    cutoff_status_key: str,
    status_field: str,
    total_key: str,
    phase: str,
    phase_label: str,
    detail_label: str,
    processed_before: int,
    day_string: bool = False,
) -> None:
    status = get_usage_maintenance_status()
    cutoff = _parse_iso(status.get(cutoff_status_key))
    if cutoff is None:
        raise RuntimeError(f"{cutoff_status_key} is missing from maintenance status")
    cutoff_value = cutoff.date().strftime("%Y-%m-%d") if day_string else _sqlite_ts(cutoff)
    phase_total = int(status.get(total_key) or 0)
    deleted_total = 0

    conn = _connect(db_path)
    _set_active_conn(conn)
    try:
        while True:
            _check_cancel()
            rows = conn.execute(
                f"""
                SELECT id
                FROM {table} INDEXED BY {index}
                WHERE {column} < ?
                ORDER BY {column} ASC
                LIMIT ?
                """,
                (cutoff_value, DELETE_BATCH_SIZE),
            ).fetchall()
            if not rows:
                break
            ids = [int(row["id"]) for row in rows]
            placeholders = ", ".join("?" for _ in ids)
            conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
            conn.commit()
            deleted_total += len(ids)
            _update_progress(
                phase=phase,
                phase_label=phase_label,
                detail=f"Deleted {deleted_total:,} {detail_label}.",
                phase_processed=min(deleted_total, phase_total),
                phase_total=phase_total,
                processed=processed_before + min(deleted_total, phase_total),
                extra={status_field: deleted_total},
            )
    except sqlite3.OperationalError:
        if _cancel_event.is_set():
            raise UsageMaintenanceCancelled()
        raise
    finally:
        _set_active_conn(None)
        conn.close()

    _update_progress(
        phase=phase,
        phase_label=phase_label,
        detail=f"Pruned old {detail_label}.",
        phase_processed=phase_total,
        phase_total=phase_total,
        processed=processed_before + phase_total,
        extra={status_field: deleted_total},
        last_completed_phase=phase,
    )


def _phase_compact(db_path: str) -> None:
    _check_cancel()
    status = get_usage_maintenance_status()
    processed_before = _units_before("compact", status)
    compacted_path = f"{db_path}.compacted"
    if os.path.exists(compacted_path):
        os.remove(compacted_path)

    _update_progress(
        phase="compact",
        phase_label="Compact database",
        detail="Compacting the SQLite database file.",
        phase_processed=0,
        phase_total=COMPACT_UNITS,
        processed=processed_before,
    )

    escaped = compacted_path.replace("'", "''")
    conn = _connect(db_path)
    _set_active_conn(conn)
    try:
        conn.execute(f"VACUUM INTO '{escaped}'")
    except sqlite3.OperationalError:
        if _cancel_event.is_set():
            raise UsageMaintenanceCancelled()
        raise
    finally:
        _set_active_conn(None)
        conn.close()

    file_size_after = os.path.getsize(compacted_path)
    engine.dispose()
    os.replace(compacted_path, db_path)
    prepare_sqlite_database()
    _update_progress(
        phase="compact",
        phase_label="Compact database",
        detail="Database compaction completed.",
        phase_processed=COMPACT_UNITS,
        phase_total=COMPACT_UNITS,
        processed=processed_before + COMPACT_UNITS,
        extra={"file_size_after": file_size_after},
        last_completed_phase="compact",
    )


def _update_progress(
    *,
    phase: str,
    phase_label: str,
    detail: str,
    phase_processed: int,
    phase_total: int,
    processed: int,
    extra: Optional[dict[str, Any]] = None,
    last_completed_phase: Optional[str] = None,
    persist: bool = True,
) -> dict[str, Any]:
    _check_cancel()
    if not persist:
        db = SessionLocal()
        try:
            status = _merged_runtime_status(load_usage_maintenance_status(db))
        finally:
            db.close()
        status.update(
            {
                "phase": phase,
                "phase_label": phase_label,
                "detail": detail,
                "phase_processed_units": max(0, int(phase_processed or 0)),
                "phase_total_units": max(0, int(phase_total or 0)),
                "processed_units": max(0, int(processed or 0)),
                "updated_at": _iso(_utc_now()),
                "can_cancel": True,
                "cancel_requested": False,
            }
        )
        if last_completed_phase:
            status["last_completed_phase"] = last_completed_phase
        if extra:
            status.update(extra)
        _set_runtime_status(status)
        return status

    db = SessionLocal()
    try:
        status = load_usage_maintenance_status(db)
        status.update(
            {
                "phase": phase,
                "phase_label": phase_label,
                "detail": detail,
                "phase_processed_units": max(0, int(phase_processed or 0)),
                "phase_total_units": max(0, int(phase_total or 0)),
                "processed_units": max(0, int(processed or 0)),
                "can_cancel": True,
                "cancel_requested": False,
            }
        )
        if last_completed_phase:
            status["last_completed_phase"] = last_completed_phase
        if extra:
            status.update(extra)
        saved = save_usage_maintenance_status(db, status)
        db.commit()
        _set_runtime_status(saved)
        return saved
    finally:
        db.close()


def _units_before(phase: str, status: dict[str, Any]) -> int:
    backup = int(status.get("backup_pages_total") or 0)
    backfill = int(status.get("backfill_samples_total") or 0)
    raw = int(status.get("raw_prune_total") or 0)
    minute = int(status.get("minute_prune_total") or 0)
    daily = int(status.get("daily_prune_total") or 0)
    if phase == "backup":
        return 0
    if phase == "backfill":
        return backup
    if phase == "validate":
        return backup + backfill
    if phase == "prune_raw":
        return backup + backfill + VALIDATE_UNITS
    if phase == "prune_minute":
        return backup + backfill + VALIDATE_UNITS + raw
    if phase == "prune_daily":
        return backup + backfill + VALIDATE_UNITS + raw + minute
    if phase == "compact":
        return backup + backfill + VALIDATE_UNITS + raw + minute + daily
    return 0


def _validate_rollups(db: Session, status: dict[str, Any]) -> None:
    cutoff = _parse_iso(status.get("backfill_cutoff"))
    if cutoff is None:
        raise RuntimeError("Backfill cutoff is missing from maintenance status")
    cutoff_naive = _naive_utc(cutoff)
    end_naive = _naive_utc(_utc_now())

    sample_peer_ids = [
        int(row[0])
        for row in db.query(UsageMinute.peer_id)
        .filter(UsageMinute.minute_ts >= cutoff_naive)
        .distinct()
        .order_by(UsageMinute.peer_id.asc())
        .limit(5)
        .all()
    ]

    for peer_id in sample_peer_ids:
        day_rows = (
            db.query(func.substr(UsageMinute.minute_ts, 1, 10))
            .filter(UsageMinute.peer_id == peer_id, UsageMinute.minute_ts >= cutoff_naive)
            .distinct()
            .order_by(func.substr(UsageMinute.minute_ts, 1, 10).desc())
            .limit(3)
            .all()
        )
        for (day_key,) in day_rows:
            day_start = datetime.strptime(day_key, "%Y-%m-%d")
            if day_start < cutoff_naive:
                # Partial raw coverage for this day (cutoff falls inside it); the
                # pre-cutoff minute rows are live data raw samples cannot reproduce.
                continue
            minute_rx, minute_tx = _minute_day_total(db, peer_id, day_key)
            day_end = day_start + timedelta(days=1)
            raw_rx, raw_tx = _raw_peer_total_range(db, peer_id, start_naive=day_start, end_naive=day_end)
            if minute_rx != raw_rx or minute_tx != raw_tx:
                raise RuntimeError(
                    f"Minute backfill validation failed for peer {peer_id} day {day_key}: "
                    f"minute=({minute_rx},{minute_tx}) raw=({raw_rx},{raw_tx})"
                )

        recent_start = floor_to_minute_utc(max(cutoff_naive, end_naive - timedelta(hours=24)))
        peer_raw = _raw_peer_total(db, peer_id, cutoff_naive=recent_start, end_naive=end_naive)
        peer_minute = _minute_peer_total(db, peer_id, cutoff_naive=recent_start, end_naive=end_naive)
        if peer_raw != peer_minute:
            raise RuntimeError(f"24h peer validation failed for peer {peer_id}: raw={peer_raw} minute={peer_minute}")

    sample_router_ids = [
        int(row[0])
        for row in db.query(Peer.router_id)
        .join(UsageMinute, UsageMinute.peer_id == Peer.id)
        .filter(UsageMinute.minute_ts >= cutoff_naive)
        .distinct()
        .order_by(Peer.router_id.asc())
        .limit(2)
        .all()
    ]
    for router_id in sample_router_ids:
        recent_start = floor_to_minute_utc(max(cutoff_naive, end_naive - timedelta(hours=24)))
        raw_router = _raw_router_total(db, router_id, cutoff_naive=recent_start, end_naive=end_naive)
        minute_router = _minute_router_total(db, router_id, cutoff_naive=recent_start, end_naive=end_naive)
        if raw_router != minute_router:
            raise RuntimeError(f"24h router validation failed for router {router_id}: raw={raw_router} minute={minute_router}")


def _minute_day_total(db: Session, peer_id: int, day_key: str) -> tuple[int, int]:
    start = datetime.strptime(day_key, "%Y-%m-%d")
    end = start + timedelta(days=1)
    row = (
        db.query(
            func.coalesce(func.sum(UsageMinute.rx), 0),
            func.coalesce(func.sum(UsageMinute.tx), 0),
        )
        .filter(UsageMinute.peer_id == peer_id, UsageMinute.minute_ts >= start, UsageMinute.minute_ts < end)
        .first()
    )
    return int(row[0] or 0), int(row[1] or 0)


def _minute_peer_total(db: Session, peer_id: int, *, cutoff_naive: datetime, end_naive: datetime) -> tuple[int, int]:
    row = (
        db.query(
            func.coalesce(func.sum(UsageMinute.rx), 0),
            func.coalesce(func.sum(UsageMinute.tx), 0),
        )
        .filter(
            UsageMinute.peer_id == peer_id,
            UsageMinute.minute_ts >= floor_to_minute_utc(cutoff_naive),
            UsageMinute.minute_ts <= floor_to_minute_utc(end_naive),
        )
        .first()
    )
    return int(row[0] or 0), int(row[1] or 0)


def _minute_router_total(db: Session, router_id: int, *, cutoff_naive: datetime, end_naive: datetime) -> tuple[int, int]:
    row = (
        db.query(
            func.coalesce(func.sum(UsageMinute.rx), 0),
            func.coalesce(func.sum(UsageMinute.tx), 0),
        )
        .join(Peer, Peer.id == UsageMinute.peer_id)
        .filter(
            Peer.router_id == router_id,
            UsageMinute.minute_ts >= floor_to_minute_utc(cutoff_naive),
            UsageMinute.minute_ts <= floor_to_minute_utc(end_naive),
        )
        .first()
    )
    return int(row[0] or 0), int(row[1] or 0)


def _raw_peer_total(db: Session, peer_id: int, *, cutoff_naive: datetime, end_naive: datetime) -> tuple[int, int]:
    return _raw_peer_total_range(db, peer_id, start_naive=cutoff_naive, end_naive=end_naive, include_end=True)


def _raw_peer_total_range(
    db: Session,
    peer_id: int,
    *,
    start_naive: datetime,
    end_naive: datetime,
    include_end: bool = False,
) -> tuple[int, int]:
    prev = (
        db.query(UsageSample)
        .filter(UsageSample.peer_id == peer_id, UsageSample.ts < start_naive)
        .order_by(UsageSample.ts.desc(), UsageSample.id.desc())
        .first()
    )
    end_filter = UsageSample.ts <= end_naive if include_end else UsageSample.ts < end_naive
    rows = (
        db.query(UsageSample)
        .filter(UsageSample.peer_id == peer_id, UsageSample.ts >= start_naive, end_filter)
        .order_by(UsageSample.ts.asc(), UsageSample.id.asc())
        .all()
    )
    rx = 0
    tx = 0
    current_prev = prev
    quarantine = CounterQuarantineState()
    tz = app_zoneinfo()
    for row in rows:
        if current_prev is None:
            current_prev = row
            continue
        ts_naive = row.ts.replace(tzinfo=None) if row.ts.tzinfo else row.ts
        day_key = counter_day_key(ts_naive, tz)
        rx += quarantine.apply("rx", counter_delta(current_prev.rx, row.rx), day_key)
        tx += quarantine.apply("tx", counter_delta(current_prev.tx, row.tx), day_key)
        current_prev = row
    return int(rx), int(tx)


def _raw_router_total(db: Session, router_id: int, *, cutoff_naive: datetime, end_naive: datetime) -> tuple[int, int]:
    peer_ids = [int(row[0]) for row in db.query(Peer.id).filter(Peer.router_id == router_id).all()]
    total_rx = 0
    total_tx = 0
    for peer_id in peer_ids:
        rx, tx = _raw_peer_total(db, peer_id, cutoff_naive=cutoff_naive, end_naive=end_naive)
        total_rx += rx
        total_tx += tx
    return total_rx, total_tx
