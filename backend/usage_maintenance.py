from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from .db import SessionLocal, engine, prepare_sqlite_database, sqlite_database_path
from .models import Peer, Router, SettingsKV, UsageDaily, UsageMinute, UsageSample
from .scheduler import pause_scheduler, resume_scheduler
from .usage_storage import bulk_upsert_usage_minute, floor_to_minute_utc


STATUS_KEY = "usage_maintenance_status"
DEFAULT_STATUS: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "started_at": None,
    "finished_at": None,
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
}
DEFAULT_RETENTION = {
    "raw_sample_retention_hours": 24,
    "minute_rollup_retention_days": 90,
    "daily_rollup_retention_days": 0,
}
BACKFILL_BATCH_SIZE = 5000
DELETE_BATCH_SIZE = 10000
_maintenance_lock = threading.Lock()
_maintenance_thread: Optional[threading.Thread] = None


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


def _default_status() -> dict[str, Any]:
    return dict(DEFAULT_STATUS)


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
    kv = db.get(SettingsKV, STATUS_KEY)
    payload = json.dumps(merged)
    if kv is None:
        kv = SettingsKV(key=STATUS_KEY, value=payload)
        db.add(kv)
    else:
        kv.value = payload
    return merged


def get_usage_maintenance_status() -> dict[str, Any]:
    db = SessionLocal()
    try:
        return load_usage_maintenance_status(db)
    finally:
        db.close()


def is_usage_maintenance_running(db: Session) -> bool:
    return bool(load_usage_maintenance_status(db).get("running"))


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


def start_usage_maintenance() -> tuple[bool, dict[str, Any]]:
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
                return False, status

            now = _utc_now()
            resume_cursor = status.get("resume_cursor")
            if not resume_cursor:
                status = _default_status()
                status["backfilled_minutes"] = 0
                status["deleted_samples"] = 0
                status["deleted_minutes"] = 0
                status["deleted_daily"] = 0

            status.update(
                {
                    "running": True,
                    "phase": resume_cursor.get("phase") if isinstance(resume_cursor, dict) and resume_cursor.get("phase") else "queued",
                    "started_at": _iso(now),
                    "finished_at": None,
                    "last_error": None,
                }
            )
            save_usage_maintenance_status(db, status)
            db.commit()
        finally:
            db.close()

        _maintenance_thread = threading.Thread(
            target=_maintenance_worker,
            name="usage-maintenance",
            daemon=True,
        )
        _maintenance_thread.start()
        return True, status


def run_usage_maintenance_once() -> dict[str, Any]:
    db_path = sqlite_database_path()
    if not db_path:
        raise RuntimeError("Usage maintenance requires a file-based SQLite database")
    if not os.path.exists(db_path):
        raise RuntimeError(f"Database file not found: {db_path}")

    pause_scheduler()
    try:
        _run_usage_maintenance(db_path)
    finally:
        resume_scheduler()
        global _maintenance_thread
        with _maintenance_lock:
            _maintenance_thread = None
    return get_usage_maintenance_status()


def _maintenance_worker() -> None:
    try:
        run_usage_maintenance_once()
    except Exception as exc:
        db = SessionLocal()
        try:
            status = load_usage_maintenance_status(db)
            status.update(
                {
                    "running": False,
                    "phase": "failed",
                    "finished_at": _iso(_utc_now()),
                    "last_error": str(exc),
                }
            )
            save_usage_maintenance_status(db, status)
            db.commit()
        finally:
            db.close()


def _run_usage_maintenance(db_path: str) -> None:
    db = SessionLocal()
    try:
        status = load_usage_maintenance_status(db)
        resume = status.get("resume_cursor") or {"phase": "preflight"}
        phase = resume.get("phase") or "preflight"

        if phase == "preflight":
            _phase_preflight(db, db_path, status)
            status = load_usage_maintenance_status(db)
            phase = (status.get("resume_cursor") or {}).get("phase") or "backup"

        if phase == "backup":
            _phase_backup(db, db_path, status)
            status = load_usage_maintenance_status(db)
            phase = (status.get("resume_cursor") or {}).get("phase") or "backfill"

        if phase == "backfill":
            _phase_backfill(db_path)
            db.expire_all()
            status = load_usage_maintenance_status(db)
            phase = (status.get("resume_cursor") or {}).get("phase") or "validate"

        if phase == "validate":
            _phase_validate(db, status)
            status = load_usage_maintenance_status(db)
            phase = (status.get("resume_cursor") or {}).get("phase") or "prune_raw"

        if phase == "prune_raw":
            _phase_prune_usage_samples()
            db.expire_all()
            status = load_usage_maintenance_status(db)
            phase = (status.get("resume_cursor") or {}).get("phase") or "prune_minute"

        if phase == "prune_minute":
            _phase_prune_usage_minute()
            db.expire_all()
            status = load_usage_maintenance_status(db)
            phase = (status.get("resume_cursor") or {}).get("phase") or "prune_daily"

        if phase == "prune_daily":
            _phase_prune_usage_daily()
            db.expire_all()
            status = load_usage_maintenance_status(db)
            phase = (status.get("resume_cursor") or {}).get("phase") or "compact"

        if phase == "compact":
            _phase_compact(db_path)
            db.expire_all()
            status = load_usage_maintenance_status(db)

        status.update(
            {
                "running": False,
                "phase": "complete",
                "finished_at": _iso(_utc_now()),
                "last_error": None,
                "last_completed_phase": "compact",
                "resume_cursor": None,
                "detail": "Usage maintenance completed successfully.",
            }
        )
        save_usage_maintenance_status(db, status)
        db.commit()
    finally:
        db.close()


def _phase_preflight(db: Session, db_path: str, status: dict[str, Any]) -> None:
    retention = load_retention_settings(db)
    now = _utc_now()
    file_size_before = os.path.getsize(db_path)
    free_bytes = shutil.disk_usage(os.path.dirname(db_path)).free
    required_free = max(int(file_size_before * 2.2), 2 * 1024 * 1024 * 1024)
    if free_bytes < required_free:
        raise RuntimeError(
            f"Not enough free disk for maintenance. Need at least {required_free} bytes free, found {free_bytes}."
        )

    with sqlite3.connect(db_path, timeout=30) as conn:
        quick_check = conn.execute("PRAGMA quick_check").fetchone()
    if not quick_check or quick_check[0] != "ok":
        raise RuntimeError(f"Database quick_check failed: {quick_check[0] if quick_check else 'unknown'}")

    status.update(
        {
            "phase": "preflight",
            "detail": "Preflight checks completed.",
            "file_size_before": file_size_before,
            "backfill_cutoff": _iso(now - timedelta(days=retention["minute_rollup_retention_days"])),
            "raw_prune_before": _iso(now - timedelta(hours=retention["raw_sample_retention_hours"])),
            "minute_prune_before": _iso(now - timedelta(days=retention["minute_rollup_retention_days"])),
            "daily_prune_before": _iso(now - timedelta(days=retention["daily_rollup_retention_days"])) if retention["daily_rollup_retention_days"] > 0 else None,
            "resume_cursor": {"phase": "backup"},
            "last_completed_phase": "preflight",
        }
    )
    save_usage_maintenance_status(db, status)
    db.commit()


def _phase_backup(db: Session, db_path: str, status: dict[str, Any]) -> None:
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    backup_path = os.path.join(
        backup_dir,
        f"wgmik-{_utc_now().strftime('%Y%m%d-%H%M%S')}-maintenance-backup.db",
    )

    source = sqlite3.connect(db_path, timeout=30)
    target = sqlite3.connect(backup_path, timeout=30)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()

    status.update(
        {
            "phase": "backup",
            "detail": f"Database snapshot created at {backup_path}",
            "backup_path": backup_path,
            "resume_cursor": {"phase": "backfill", "prepared": False, "peer_id": None, "last_ts": None, "last_id": None},
            "last_completed_phase": "backup",
        }
    )
    save_usage_maintenance_status(db, status)
    db.commit()


def _phase_backfill(db_path: str) -> None:
    while True:
        db = SessionLocal()
        try:
            status = load_usage_maintenance_status(db)
            cursor = dict(status.get("resume_cursor") or {})
            cutoff = _parse_iso(status.get("backfill_cutoff"))
            if cutoff is None:
                raise RuntimeError("Backfill cutoff is missing from maintenance status")
            cutoff_naive = _naive_utc(cutoff)

            if not cursor.get("prepared"):
                db.query(UsageMinute).filter(UsageMinute.minute_ts >= cutoff_naive).delete(synchronize_session=False)
                cursor = {"phase": "backfill", "prepared": True, "peer_id": None, "last_ts": None, "last_id": None}
                status.update(
                    {
                        "phase": "backfill",
                        "detail": "Cleared existing minute rollups in the backfill window.",
                        "resume_cursor": cursor,
                    }
                )
                save_usage_maintenance_status(db, status)
                db.commit()
                continue

            peer_ids = [
                int(row[0])
                for row in db.query(UsageSample.peer_id)
                .filter(UsageSample.ts >= cutoff_naive)
                .distinct()
                .order_by(UsageSample.peer_id.asc())
                .all()
            ]
            next_peer = _find_next_peer_id(peer_ids, cursor.get("peer_id"))
            if next_peer is None:
                status.update(
                    {
                        "phase": "backfill",
                        "detail": "Minute rollup backfill completed.",
                        "resume_cursor": {"phase": "validate"},
                        "last_completed_phase": "backfill",
                    }
                )
                save_usage_maintenance_status(db, status)
                db.commit()
                return

            last_ts_value = cursor.get("last_ts") if cursor.get("peer_id") == next_peer else None
            last_id_value = cursor.get("last_id") if cursor.get("peer_id") == next_peer else None
            last_ts = _parse_iso(last_ts_value) if last_ts_value else None

            prev_sample = None
            if last_id_value:
                prev_sample = db.get(UsageSample, int(last_id_value))
            else:
                prev_sample = (
                    db.query(UsageSample)
                    .filter(UsageSample.peer_id == next_peer, UsageSample.ts < cutoff_naive)
                    .order_by(UsageSample.ts.desc(), UsageSample.id.desc())
                    .first()
                )

            query = db.query(UsageSample).filter(UsageSample.peer_id == next_peer, UsageSample.ts >= cutoff_naive)
            if last_ts is not None and last_id_value is not None:
                last_ts_naive = _naive_utc(last_ts)
                query = query.filter(
                    or_(
                        UsageSample.ts > last_ts_naive,
                        and_(UsageSample.ts == last_ts_naive, UsageSample.id > int(last_id_value)),
                    )
                )
            rows = query.order_by(UsageSample.ts.asc(), UsageSample.id.asc()).limit(BACKFILL_BATCH_SIZE).all()

            if not rows:
                next_cursor = {"phase": "backfill", "prepared": True, "peer_id": _next_id_after(peer_ids, next_peer), "last_ts": None, "last_id": None}
                status.update(
                    {
                        "phase": "backfill",
                        "detail": f"Finished peer {next_peer}",
                        "resume_cursor": next_cursor,
                    }
                )
                save_usage_maintenance_status(db, status)
                db.commit()
                continue

            minute_totals: dict[datetime, dict[str, int]] = {}
            current_prev = prev_sample
            for row in rows:
                if current_prev is None:
                    current_prev = row
                    continue
                delta_rx = row.rx if row.rx < current_prev.rx else row.rx - current_prev.rx
                delta_tx = row.tx if row.tx < current_prev.tx else row.tx - current_prev.tx
                current_prev = row
                if delta_rx == 0 and delta_tx == 0:
                    continue
                bucket = floor_to_minute_utc(row.ts)
                totals = minute_totals.setdefault(bucket, {"rx": 0, "tx": 0})
                totals["rx"] += int(delta_rx or 0)
                totals["tx"] += int(delta_tx or 0)

            bulk_upsert_usage_minute(
                db,
                [
                    {
                        "peer_id": next_peer,
                        "minute_ts": bucket,
                        "rx": totals["rx"],
                        "tx": totals["tx"],
                    }
                    for bucket, totals in minute_totals.items()
                ],
            )

            last_row = rows[-1]
            if len(rows) < BACKFILL_BATCH_SIZE:
                resume_cursor = {
                    "phase": "backfill",
                    "prepared": True,
                    "peer_id": _next_id_after(peer_ids, next_peer),
                    "last_ts": None,
                    "last_id": None,
                }
            else:
                resume_cursor = {
                    "phase": "backfill",
                    "prepared": True,
                    "peer_id": next_peer,
                    "last_ts": _iso(last_row.ts.replace(tzinfo=timezone.utc) if last_row.ts.tzinfo is None else last_row.ts),
                    "last_id": int(last_row.id),
                }

            status["backfilled_minutes"] = int(status.get("backfilled_minutes") or 0) + len(minute_totals)
            status.update(
                {
                    "phase": "backfill",
                    "detail": f"Backfilled peer {next_peer} through sample {last_row.id}",
                    "resume_cursor": resume_cursor,
                }
            )
            save_usage_maintenance_status(db, status)
            db.commit()
        finally:
            db.close()


def _phase_validate(db: Session, status: dict[str, Any]) -> None:
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
            minute_rx, minute_tx = _minute_day_total(db, peer_id, day_key)
            daily = db.query(UsageDaily).filter(UsageDaily.peer_id == peer_id, UsageDaily.day == day_key).first()
            daily_rx = int(daily.rx or 0) if daily else 0
            daily_tx = int(daily.tx or 0) if daily else 0
            if minute_rx != daily_rx or minute_tx != daily_tx:
                raise RuntimeError(
                    f"Minute backfill validation failed for peer {peer_id} day {day_key}: "
                    f"minute=({minute_rx},{minute_tx}) daily=({daily_rx},{daily_tx})"
                )

        peer_raw = _raw_peer_total(db, peer_id, cutoff_naive=max(cutoff_naive, end_naive - timedelta(hours=24)), end_naive=end_naive)
        peer_minute = _minute_peer_total(db, peer_id, cutoff_naive=max(cutoff_naive, end_naive - timedelta(hours=24)), end_naive=end_naive)
        if peer_raw != peer_minute:
            raise RuntimeError(
                f"24h peer validation failed for peer {peer_id}: raw={peer_raw} minute={peer_minute}"
            )

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
        raw_router = _raw_router_total(db, router_id, cutoff_naive=max(cutoff_naive, end_naive - timedelta(hours=24)), end_naive=end_naive)
        minute_router = _minute_router_total(db, router_id, cutoff_naive=max(cutoff_naive, end_naive - timedelta(hours=24)), end_naive=end_naive)
        if raw_router != minute_router:
            raise RuntimeError(
                f"24h router validation failed for router {router_id}: raw={raw_router} minute={minute_router}"
            )

    status.update(
        {
            "phase": "validate",
            "detail": "Minute rollup validation completed.",
            "resume_cursor": {"phase": "prune_raw"},
            "last_completed_phase": "validate",
        }
    )
    save_usage_maintenance_status(db, status)
    db.commit()


def _phase_prune_usage_samples() -> None:
    _batch_delete_phase(
        model=UsageSample,
        field_name="ts",
        status_field="deleted_samples",
        next_phase="prune_minute",
        detail_label="raw samples",
        cutoff_status_key="raw_prune_before",
    )


def _phase_prune_usage_minute() -> None:
    _batch_delete_phase(
        model=UsageMinute,
        field_name="minute_ts",
        status_field="deleted_minutes",
        next_phase="prune_daily",
        detail_label="minute rollups",
        cutoff_status_key="minute_prune_before",
    )


def _phase_prune_usage_daily() -> None:
    db = SessionLocal()
    try:
        status = load_usage_maintenance_status(db)
        daily_cutoff = _parse_iso(status.get("daily_prune_before"))
        if daily_cutoff is None:
            status.update(
                {
                    "phase": "prune_daily",
                    "detail": "Daily rollup retention is set to keep forever.",
                    "resume_cursor": {"phase": "compact"},
                    "last_completed_phase": "prune_daily",
                }
            )
            save_usage_maintenance_status(db, status)
            db.commit()
            return
    finally:
        db.close()

    _batch_delete_phase(
        model=UsageDaily,
        field_name="day",
        status_field="deleted_daily",
        next_phase="compact",
        detail_label="daily rollups",
        cutoff_status_key="daily_prune_before",
        day_string=True,
    )


def _phase_compact(db_path: str) -> None:
    compacted_path = f"{db_path}.compacted"
    if os.path.exists(compacted_path):
        os.remove(compacted_path)

    escaped = compacted_path.replace("'", "''")
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute(f"VACUUM INTO '{escaped}'")

    file_size_after = os.path.getsize(compacted_path)
    engine.dispose()
    os.replace(compacted_path, db_path)
    prepare_sqlite_database()

    db = SessionLocal()
    try:
        status = load_usage_maintenance_status(db)
        status.update(
            {
                "phase": "compact",
                "detail": "Database compaction completed.",
                "file_size_after": file_size_after,
                "resume_cursor": {"phase": "complete"},
                "last_completed_phase": "compact",
            }
        )
        save_usage_maintenance_status(db, status)
        db.commit()
    finally:
        db.close()


def _batch_delete_phase(
    *,
    model,
    field_name: str,
    status_field: str,
    next_phase: str,
    detail_label: str,
    cutoff_status_key: str,
    day_string: bool = False,
) -> None:
    while True:
        db = SessionLocal()
        try:
            status = load_usage_maintenance_status(db)
            cutoff = _parse_iso(status.get(cutoff_status_key))
            if cutoff is None and not day_string:
                raise RuntimeError(f"{cutoff_status_key} is missing from maintenance status")
            criterion_value = cutoff.date().strftime("%Y-%m-%d") if day_string and cutoff is not None else _naive_utc(cutoff) if cutoff is not None else None

            model_field = getattr(model, field_name)
            filters = [model_field < criterion_value] if criterion_value is not None else []
            ids = [
                int(row[0])
                for row in db.query(model.id)
                .filter(*filters)
                .order_by(model.id.asc())
                .limit(DELETE_BATCH_SIZE)
                .all()
            ]
            if not ids:
                status.update(
                    {
                        "phase": next_phase if next_phase != "complete" else "compact",
                        "detail": f"Pruned old {detail_label}.",
                        "resume_cursor": {"phase": next_phase},
                        "last_completed_phase": status.get("phase"),
                    }
                )
                save_usage_maintenance_status(db, status)
                db.commit()
                return

            deleted = (
                db.query(model)
                .filter(model.id.in_(ids))
                .delete(synchronize_session=False)
            )
            status[status_field] = int(status.get(status_field) or 0) + int(deleted or 0)
            status.update(
                {
                    "phase": status.get("phase") or next_phase,
                    "detail": f"Deleted {status[status_field]} {detail_label}.",
                    "resume_cursor": {"phase": status.get("phase") or next_phase, "last_id": ids[-1]},
                }
            )
            save_usage_maintenance_status(db, status)
            db.commit()
        finally:
            db.close()


def _find_next_peer_id(peer_ids: list[int], current_peer_id: Optional[int]) -> Optional[int]:
    if not peer_ids:
        return None
    if current_peer_id is None:
        return peer_ids[0]
    for peer_id in peer_ids:
        if peer_id >= current_peer_id:
            return peer_id
    return None


def _next_id_after(ids: list[int], current_id: Optional[int]) -> Optional[int]:
    if current_id is None:
        return ids[0] if ids else None
    for idx, value in enumerate(ids):
        if value == current_id and idx + 1 < len(ids):
            return ids[idx + 1]
    return None


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
    prev = (
        db.query(UsageSample)
        .filter(UsageSample.peer_id == peer_id, UsageSample.ts < cutoff_naive)
        .order_by(UsageSample.ts.desc(), UsageSample.id.desc())
        .first()
    )
    rows = (
        db.query(UsageSample)
        .filter(UsageSample.peer_id == peer_id, UsageSample.ts >= cutoff_naive, UsageSample.ts <= end_naive)
        .order_by(UsageSample.ts.asc(), UsageSample.id.asc())
        .all()
    )
    rx = 0
    tx = 0
    current_prev = prev
    for row in rows:
        if current_prev is None:
            current_prev = row
            continue
        rx += row.rx if row.rx < current_prev.rx else row.rx - current_prev.rx
        tx += row.tx if row.tx < current_prev.tx else row.tx - current_prev.tx
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
