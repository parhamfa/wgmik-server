from __future__ import annotations

import base64
import io
import json
import os
import secrets
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from .db import SessionLocal, engine, prepare_sqlite_database, sqlite_database_path
from .destructive_ops import exclusive_operation_gate
from .models import SettingsKV
from .scheduler import pause_scheduler, resume_scheduler
from .security import SecretBox
from .settings import settings
from .usage_maintenance import _run_usage_maintenance, is_usage_maintenance_running


BUNDLE_FORMAT_VERSION = 1
STATUS_KEY = "manual_backup_status"
DOWNLOAD_TTL_SECONDS = 3600
PLACEHOLDER_SECRETS = {"", "change-me"}

DEFAULT_STATUS: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "phase_label": "Idle",
    "started_at": None,
    "finished_at": None,
    "updated_at": None,
    "last_error": None,
    "detail": None,
    "file_size": None,
    "download_token": None,
    "download_filename": None,
    "elapsed_seconds": 0,
    "progress_percent": 0.0,
}

_backup_lock = threading.Lock()
_backup_thread: Optional[threading.Thread] = None
_runtime_status_lock = threading.Lock()
_runtime_status: Optional[dict[str, Any]] = None
_runtime_secret_key: Optional[str] = None
_download_lock = threading.Lock()
_download_state: Optional[dict[str, Any]] = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def _default_status() -> dict[str, Any]:
    return dict(DEFAULT_STATUS)


def _decorate_status(status: dict[str, Any]) -> dict[str, Any]:
    data = _default_status()
    data.update(status or {})
    started = None
    finished = None
    if data.get("started_at"):
        try:
            started = datetime.fromisoformat(str(data["started_at"]))
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
        except ValueError:
            started = None
    if data.get("finished_at"):
        try:
            finished = datetime.fromisoformat(str(data["finished_at"]))
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
        except ValueError:
            finished = None
    end = finished or (_utc_now() if data.get("running") else None)
    if started and end:
        data["elapsed_seconds"] = max(0, int((end - started).total_seconds()))
    else:
        data["elapsed_seconds"] = int(data.get("elapsed_seconds") or 0)
    return data


def load_backup_status(db: Session) -> dict[str, Any]:
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


def save_backup_status(db: Session, status: dict[str, Any]) -> dict[str, Any]:
    merged = _default_status()
    merged.update(status or {})
    merged.pop("secret_key", None)
    merged.pop("download_token", None)
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
        merged = dict(status)
        if _runtime_status:
            merged.update(_runtime_status)
    global _runtime_secret_key, _download_state
    if _runtime_secret_key:
        merged["secret_key"] = _runtime_secret_key
    with _download_lock:
        if _download_state:
            merged["download_token"] = _download_state.get("token")
            merged["download_filename"] = _download_state.get("filename")
    return merged


def get_backup_status() -> dict[str, Any]:
    db = SessionLocal()
    try:
        saved = load_backup_status(db)
        return _decorate_status(_merged_runtime_status(saved))
    finally:
        db.close()


def is_backup_running(db: Optional[Session] = None) -> bool:
    if db is not None:
        return bool(load_backup_status(db).get("running"))
    db = SessionLocal()
    try:
        return bool(load_backup_status(db).get("running"))
    finally:
        db.close()


def reset_stale_backup_status() -> None:
    db = SessionLocal()
    try:
        status = load_backup_status(db)
        if not status.get("running"):
            return
        status.update(
            {
                "running": False,
                "phase": "failed",
                "phase_label": "Failed",
                "finished_at": _iso(_utc_now()),
                "last_error": "Backup was interrupted by a server restart.",
                "detail": "The previous backup did not finish.",
                "progress_percent": 0.0,
            }
        )
        save_backup_status(db, status)
        db.commit()
        _set_runtime_status(None)
        _clear_download_state()
    finally:
        db.close()


def _clear_download_state() -> None:
    global _runtime_secret_key, _download_state
    with _download_lock:
        if _download_state:
            path = _download_state.get("path")
            if path and os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
        _download_state = None
    _runtime_secret_key = None


def _update_backup_status(**updates: Any) -> dict[str, Any]:
    db = SessionLocal()
    try:
        status = _merged_runtime_status(load_backup_status(db))
        status.update(updates)
        status["updated_at"] = _iso(_utc_now())
        saved = save_backup_status(db, status)
        db.commit()
        _set_runtime_status(saved)
        return _decorate_status(_merged_runtime_status(saved))
    finally:
        db.close()


def _secret_key_file() -> Path:
    db_path = sqlite_database_path()
    if db_path:
        return Path(db_path).parent / "secret_key"
    return Path("./secret_key")


def _backups_dir() -> Path:
    db_path = sqlite_database_path()
    if not db_path:
        raise RuntimeError("Backup requires a file-based SQLite database")
    directory = Path(db_path).parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _secret_key_from_env() -> bool:
    env_value = (os.environ.get("SECRET_KEY") or "").strip()
    return bool(env_value and env_value not in PLACEHOLDER_SECRETS)


def _validate_sqlite_file(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        if not row or str(row[0]).lower() != "ok":
            raise ValueError(f"Database integrity check failed: {row[0] if row else 'unknown'}")
    finally:
        conn.close()


def _snapshot_database(db_path: str, target_path: str) -> None:
    escaped = target_path.replace("'", "''")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(f"VACUUM INTO '{escaped}'")
    finally:
        conn.close()


def _build_tar_gz(db_snapshot_path: str, secret_key_value: str, manifest: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(db_snapshot_path, arcname="wgmik.db")
        secret_bytes = secret_key_value.encode("utf-8")
        secret_info = tarfile.TarInfo(name="secret_key")
        secret_info.size = len(secret_bytes)
        archive.addfile(secret_info, io.BytesIO(secret_bytes))
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        manifest_info = tarfile.TarInfo(name="manifest.json")
        manifest_info.size = len(manifest_bytes)
        archive.addfile(manifest_info, io.BytesIO(manifest_bytes))
    return buffer.getvalue()


def _encrypt_payload(payload: bytes, backup_key: str) -> str:
    encoded = base64.b64encode(payload).decode("ascii")
    return SecretBox(backup_key).encrypt(encoded)


def _decrypt_payload(encrypted_text: str, backup_key: str) -> bytes:
    decoded = SecretBox(backup_key).decrypt(encrypted_text.strip())
    if decoded is None:
        raise ValueError("Wrong backup key or corrupt backup file")
    try:
        return base64.b64decode(decoded)
    except Exception as exc:
        raise ValueError("Wrong backup key or corrupt backup file") from exc


def _extract_bundle(payload: bytes, work_dir: str) -> dict[str, str]:
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        archive.extractall(work_dir)
    db_path = os.path.join(work_dir, "wgmik.db")
    secret_path = os.path.join(work_dir, "secret_key")
    manifest_path = os.path.join(work_dir, "manifest.json")
    if not os.path.isfile(db_path):
        raise ValueError("Backup bundle is missing wgmik.db")
    if not os.path.isfile(secret_path):
        raise ValueError("Backup bundle is missing secret_key")
    if not os.path.isfile(manifest_path):
        raise ValueError("Backup bundle is missing manifest.json")
    return {"db_path": db_path, "secret_path": secret_path, "manifest_path": manifest_path}


def start_backup() -> tuple[bool, dict[str, Any]]:
    global _backup_thread
    db_path = sqlite_database_path()
    if not db_path:
        raise RuntimeError("Backup requires a file-based SQLite database")
    if not os.path.exists(db_path):
        raise RuntimeError(f"Database file not found: {db_path}")
    if exclusive_operation_gate.is_active():
        active = exclusive_operation_gate.snapshot()
        raise RuntimeError(active.detail if active else "Another exclusive operation is already running")
    db_check = SessionLocal()
    try:
        if is_usage_maintenance_running(db_check):
            raise RuntimeError("Usage maintenance is running. Wait for it to finish before creating a backup.")
    finally:
        db_check.close()

    with _backup_lock:
        db = SessionLocal()
        try:
            status = load_backup_status(db)
            if status.get("running") or (_backup_thread and _backup_thread.is_alive()):
                return False, _decorate_status(_merged_runtime_status(status))

            _clear_download_state()
            now = _utc_now()
            status = _default_status()
            status.update(
                {
                    "running": True,
                    "phase": "queued",
                    "phase_label": "Queued",
                    "started_at": _iso(now),
                    "finished_at": None,
                    "last_error": None,
                    "detail": "Backup is queued.",
                    "progress_percent": 0.0,
                }
            )
            saved = save_backup_status(db, status)
            db.commit()
            _set_runtime_status(saved)
        finally:
            db.close()

        _backup_thread = threading.Thread(
            target=_backup_worker,
            name="manual-backup",
            daemon=True,
        )
        _backup_thread.start()
        return True, _decorate_status(_merged_runtime_status(saved))


def _backup_worker() -> None:
    try:
        run_backup_once()
    except Exception as exc:
        _mark_backup_failed(str(exc))


def run_backup_once() -> dict[str, Any]:
    db_path = sqlite_database_path()
    if not db_path:
        raise RuntimeError("Backup requires a file-based SQLite database")

    pause_scheduler()
    try:
        with exclusive_operation_gate.begin(
            "manual_backup",
            "Manual backup",
            "A manual backup is running. Wait for it to finish before changing data.",
        ):
            _update_backup_status(
                phase="maintenance",
                phase_label="Compacting data",
                detail="Running usage maintenance to compact and clean the database before backup.",
                progress_percent=10.0,
            )
            _run_usage_maintenance(db_path)

            _update_backup_status(
                phase="packaging",
                phase_label="Packaging backup",
                detail="Creating a compact database snapshot and bundling encrypted secrets.",
                progress_percent=75.0,
            )
            backup_key = secrets.token_urlsafe(32)
            timestamp = _utc_now().strftime("%Y%m%d-%H%M%S")
            filename = f"wgmik-backup-{timestamp}.wgmik"
            bundle_path = str(_backups_dir() / filename)

            with tempfile.TemporaryDirectory(prefix="wgmik-backup-") as work_dir:
                snapshot_path = os.path.join(work_dir, "wgmik.db")
                engine.dispose()
                _snapshot_database(db_path, snapshot_path)
                prepare_sqlite_database()

                manifest = {
                    "format_version": BUNDLE_FORMAT_VERSION,
                    "app_name": settings.app_name,
                    "created_at": _iso(_utc_now()),
                }
                secret_key_value = settings.secret_key
                tar_payload = _build_tar_gz(snapshot_path, secret_key_value, manifest)

            _update_backup_status(
                phase="encrypting",
                phase_label="Encrypting backup",
                detail="Encrypting the backup bundle with a one-time key.",
                progress_percent=90.0,
            )
            encrypted = _encrypt_payload(tar_payload, backup_key)
            with open(bundle_path, "w", encoding="utf-8") as handle:
                handle.write(encrypted)

            token = secrets.token_urlsafe(24)
            global _runtime_secret_key, _download_state
            _runtime_secret_key = backup_key
            with _download_lock:
                _download_state = {
                    "token": token,
                    "path": bundle_path,
                    "filename": filename,
                    "created_at": _utc_now(),
                }

            file_size = os.path.getsize(bundle_path)
            _update_backup_status(
                running=False,
                phase="complete",
                phase_label="Complete",
                finished_at=_iso(_utc_now()),
                detail="Backup is ready. Copy the secret key and download the encrypted bundle.",
                progress_percent=100.0,
                file_size=file_size,
            )
    finally:
        resume_scheduler()
        global _backup_thread
        with _backup_lock:
            _backup_thread = None
    return get_backup_status()


def _mark_backup_failed(message: str) -> None:
    _update_backup_status(
        running=False,
        phase="failed",
        phase_label="Failed",
        finished_at=_iso(_utc_now()),
        last_error=message,
        detail="Backup failed.",
        progress_percent=0.0,
    )
    _clear_download_state()


def resolve_backup_download(token: str) -> tuple[str, str]:
    with _download_lock:
        state = _download_state
        if not state or state.get("token") != token:
            raise FileNotFoundError("Download link is invalid or expired")
        created_at = state.get("created_at")
        if isinstance(created_at, datetime) and _utc_now() - created_at > timedelta(seconds=DOWNLOAD_TTL_SECONDS):
            raise FileNotFoundError("Download link has expired")
        path = str(state.get("path") or "")
        filename = str(state.get("filename") or "wgmik-backup.wgmik")
    if not path or not os.path.isfile(path):
        raise FileNotFoundError("Backup file is no longer available")
    return path, filename


def restore_backup_from_upload(file_bytes: bytes, backup_key: str) -> dict[str, Any]:
    if _secret_key_from_env():
        raise RuntimeError(
            "Restore is not supported when SECRET_KEY is set via environment variable. "
            "Remove SECRET_KEY from the environment and use the persisted secret_key file instead."
        )
    db_path = sqlite_database_path()
    if not db_path:
        raise RuntimeError("Restore requires a file-based SQLite database")
    if exclusive_operation_gate.is_active():
        active = exclusive_operation_gate.snapshot()
        raise RuntimeError(active.detail if active else "Another exclusive operation is already running")
    db_check = SessionLocal()
    try:
        if is_usage_maintenance_running(db_check):
            raise RuntimeError("Usage maintenance is running. Wait for it to finish before restoring.")
        if is_backup_running(db_check):
            raise RuntimeError("A backup is currently running. Wait for it to finish before restoring.")
    finally:
        db_check.close()

    if not backup_key.strip():
        raise ValueError("Backup secret key is required")

    try:
        payload = _decrypt_payload(file_bytes.decode("utf-8"), backup_key.strip())
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid backup file encoding") from exc

    with tempfile.TemporaryDirectory(prefix="wgmik-restore-") as work_dir:
        extracted = _extract_bundle(payload, work_dir)
        with open(extracted["manifest_path"], encoding="utf-8") as handle:
            manifest = json.load(handle)
        if int(manifest.get("format_version") or 0) != BUNDLE_FORMAT_VERSION:
            raise ValueError("Unsupported backup format version")
        _validate_sqlite_file(extracted["db_path"])
        secret_key_value = Path(extracted["secret_path"]).read_text(encoding="utf-8").strip()
        if not secret_key_value:
            raise ValueError("Backup bundle secret_key is empty")

        backups_dir = _backups_dir()
        pre_restore_name = f"pre-restore-{_utc_now().strftime('%Y%m%d-%H%M%S')}.db"
        pre_restore_path = str(backups_dir / pre_restore_name)

        pause_scheduler()
        try:
            with exclusive_operation_gate.begin(
                "manual_restore",
                "Manual restore",
                "Database restore is in progress. The server will restart shortly.",
            ):
                shutil.copy2(db_path, pre_restore_path)

                engine.dispose()
                os.replace(extracted["db_path"], db_path)
                secret_file = _secret_key_file()
                secret_file.parent.mkdir(parents=True, exist_ok=True)
                secret_file.write_text(secret_key_value + "\n", encoding="utf-8")
                try:
                    secret_file.chmod(stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
                prepare_sqlite_database()
        finally:
            resume_scheduler()

    def _exit_after_response() -> None:
        time.sleep(1.0)
        os._exit(0)

    threading.Thread(target=_exit_after_response, name="restore-exit", daemon=True).start()
    return {
        "ok": True,
        "message": "Restore completed. The server is restarting; sign in again when it is back online.",
        "pre_restore_backup": pre_restore_name,
    }
