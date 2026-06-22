from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import shutil
import sqlite3
import stat
import struct
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy.orm import Session

from .db import SessionLocal, engine, prepare_sqlite_database, sqlite_database_path
from .destructive_ops import exclusive_operation_gate
from .models import SettingsKV
from .scheduler import pause_scheduler, resume_scheduler
from .security import SecretBox
from .settings import settings
from .usage_maintenance import _run_usage_maintenance, is_usage_maintenance_running


LEGACY_BUNDLE_FORMAT_VERSION = 1
BUNDLE_FORMAT_VERSION = 2
STATUS_KEY = "manual_backup_status"
DOWNLOAD_TTL_SECONDS = 3600
PLACEHOLDER_SECRETS = {"", "change-me"}
STREAM_CHUNK_SIZE = 1024 * 1024

V2_MAGIC = b"WGMIK2\n"
V2_HEADER_LEN = struct.Struct(">I")
V2_RECORD_HEADER = struct.Struct(">QBII")
V2_AAD = struct.Struct(">QBI")
V2_KDF_INFO = b"wgmik-backup-v2"
EXPECTED_BUNDLE_TOP_LEVELS = {"wgmik.db", "secret_key", "manifest.json", "telegram_outbox"}

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


def _tempfile_kwargs(prefix: str, suffix: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"prefix": prefix, "suffix": suffix, "delete": False}
    try:
        kwargs["dir"] = str(_backups_dir())
    except RuntimeError:
        pass
    return kwargs


def store_backup_upload_to_temp(upload_stream: BinaryIO) -> tuple[str, int]:
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(**_tempfile_kwargs("wgmik-upload-", ".wgmik")) as handle:
            temp_path = handle.name
            shutil.copyfileobj(upload_stream, handle, length=STREAM_CHUNK_SIZE)
            size = handle.tell()
        return temp_path, size
    except Exception:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise


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


def _telegram_outbox_dir() -> Path:
    db_path = sqlite_database_path()
    if db_path:
        return Path(db_path).parent / "telegram_outbox"
    return Path("./data/telegram_outbox")


def _tar_bytes(name: str, data: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = int(time.time())
    return info


def _add_regular_tree(archive: tarfile.TarFile, source_dir: Path, arcname: str) -> None:
    if not source_dir.exists() or not source_dir.is_dir():
        return
    archive.add(str(source_dir), arcname=arcname, recursive=False)
    for path in sorted(source_dir.rglob("*")):
        if path.is_symlink():
            continue
        rel = path.relative_to(source_dir).as_posix()
        archive.add(str(path), arcname=f"{arcname}/{rel}", recursive=False)


def _build_tar_gz_to_path(
    db_snapshot_path: str,
    secret_key_value: str,
    manifest: dict[str, Any],
    target_path: str,
    outbox_dir: Optional[Path] = None,
) -> None:
    with tarfile.open(target_path, mode="w:gz") as archive:
        archive.add(db_snapshot_path, arcname="wgmik.db")
        secret_bytes = secret_key_value.encode("utf-8")
        archive.addfile(_tar_bytes("secret_key", secret_bytes), io.BytesIO(secret_bytes))
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        archive.addfile(_tar_bytes("manifest.json", manifest_bytes), io.BytesIO(manifest_bytes))
        if outbox_dir and outbox_dir.exists():
            _add_regular_tree(archive, outbox_dir, "telegram_outbox")


def _build_tar_gz(db_snapshot_path: str, secret_key_value: str, manifest: dict[str, Any]) -> bytes:
    """Legacy in-memory bundle builder kept for v1 compatibility tests."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        archive.add(db_snapshot_path, arcname="wgmik.db")
        secret_bytes = secret_key_value.encode("utf-8")
        archive.addfile(_tar_bytes("secret_key", secret_bytes), io.BytesIO(secret_bytes))
        manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
        archive.addfile(_tar_bytes("manifest.json", manifest_bytes), io.BytesIO(manifest_bytes))
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


def _derive_v2_backup_key(backup_key: str, salt: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=V2_KDF_INFO,
    ).derive(backup_key.encode("utf-8"))


def _v2_header_digest(header_bytes: bytes) -> bytes:
    return hashlib.sha256(V2_MAGIC + V2_HEADER_LEN.pack(len(header_bytes)) + header_bytes).digest()


def _v2_record_aad(header_digest: bytes, index: int, final_flag: int, plaintext_len: int) -> bytes:
    return header_digest + V2_AAD.pack(index, final_flag, plaintext_len)


def _v2_nonce(prefix: bytes, index: int) -> bytes:
    return prefix + index.to_bytes(8, "big")


def _encrypt_v2_file_to_path(
    source_path: str,
    target_path: str,
    backup_key: str,
    *,
    chunk_size: int = STREAM_CHUNK_SIZE,
) -> None:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    salt = os.urandom(16)
    nonce_prefix = os.urandom(4)
    header = {
        "format_version": BUNDLE_FORMAT_VERSION,
        "cipher": "AES-256-GCM",
        "kdf": "HKDF-SHA256",
        "chunk_size": chunk_size,
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce_prefix": base64.b64encode(nonce_prefix).decode("ascii"),
        "created_at": _iso(_utc_now()),
    }
    header_bytes = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")
    header_digest = _v2_header_digest(header_bytes)
    aesgcm = AESGCM(_derive_v2_backup_key(backup_key, salt))

    with open(source_path, "rb") as source, open(target_path, "wb") as target:
        target.write(V2_MAGIC)
        target.write(V2_HEADER_LEN.pack(len(header_bytes)))
        target.write(header_bytes)
        index = 0
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                final_flag = 1
                ciphertext = aesgcm.encrypt(
                    _v2_nonce(nonce_prefix, index),
                    b"",
                    _v2_record_aad(header_digest, index, final_flag, 0),
                )
                target.write(V2_RECORD_HEADER.pack(index, final_flag, 0, len(ciphertext)))
                target.write(ciphertext)
                break
            final_flag = 0
            ciphertext = aesgcm.encrypt(
                _v2_nonce(nonce_prefix, index),
                chunk,
                _v2_record_aad(header_digest, index, final_flag, len(chunk)),
            )
            target.write(V2_RECORD_HEADER.pack(index, final_flag, len(chunk), len(ciphertext)))
            target.write(ciphertext)
            index += 1


def _read_exact(handle: BinaryIO, size: int) -> bytes:
    data = handle.read(size)
    if len(data) != size:
        raise ValueError("Wrong backup key or corrupt backup file")
    return data


def _decrypt_v2_file_to_path(source_path: str, target_path: str, backup_key: str) -> None:
    with open(source_path, "rb") as source:
        if _read_exact(source, len(V2_MAGIC)) != V2_MAGIC:
            raise ValueError("Unsupported backup format")
        header_len = V2_HEADER_LEN.unpack(_read_exact(source, V2_HEADER_LEN.size))[0]
        if header_len <= 0 or header_len > 64 * 1024:
            raise ValueError("Wrong backup key or corrupt backup file")
        header_bytes = _read_exact(source, header_len)
        try:
            header = json.loads(header_bytes.decode("utf-8"))
            salt = base64.b64decode(header["salt"])
            nonce_prefix = base64.b64decode(header["nonce_prefix"])
            chunk_size = int(header["chunk_size"])
        except Exception as exc:
            raise ValueError("Wrong backup key or corrupt backup file") from exc
        if header.get("format_version") != BUNDLE_FORMAT_VERSION:
            raise ValueError("Unsupported backup format version")
        if header.get("cipher") != "AES-256-GCM" or len(nonce_prefix) != 4 or chunk_size <= 0:
            raise ValueError("Unsupported backup encryption format")

        header_digest = _v2_header_digest(header_bytes)
        aesgcm = AESGCM(_derive_v2_backup_key(backup_key, salt))
        expected_index = 0
        saw_final = False
        with open(target_path, "wb") as target:
            while True:
                record_header = source.read(V2_RECORD_HEADER.size)
                if not record_header:
                    break
                if len(record_header) != V2_RECORD_HEADER.size:
                    raise ValueError("Wrong backup key or corrupt backup file")
                index, final_flag, plaintext_len, ciphertext_len = V2_RECORD_HEADER.unpack(record_header)
                if index != expected_index or final_flag not in (0, 1):
                    raise ValueError("Wrong backup key or corrupt backup file")
                if plaintext_len > chunk_size or ciphertext_len < 16:
                    raise ValueError("Wrong backup key or corrupt backup file")
                ciphertext = _read_exact(source, ciphertext_len)
                try:
                    plaintext = aesgcm.decrypt(
                        _v2_nonce(nonce_prefix, index),
                        ciphertext,
                        _v2_record_aad(header_digest, index, final_flag, plaintext_len),
                    )
                except Exception as exc:
                    raise ValueError("Wrong backup key or corrupt backup file") from exc
                if len(plaintext) != plaintext_len:
                    raise ValueError("Wrong backup key or corrupt backup file")
                target.write(plaintext)
                expected_index += 1
                if final_flag:
                    saw_final = True
                    if source.read(1):
                        raise ValueError("Wrong backup key or corrupt backup file")
                    break
        if not saw_final:
            raise ValueError("Wrong backup key or corrupt backup file")


def _backup_file_is_v2(path: str) -> bool:
    with open(path, "rb") as handle:
        return handle.read(len(V2_MAGIC)) == V2_MAGIC


def _decrypt_backup_file_to_tar_path(source_path: str, backup_key: str, target_path: str) -> None:
    if _backup_file_is_v2(source_path):
        _decrypt_v2_file_to_path(source_path, target_path, backup_key)
        return
    try:
        encrypted_text = Path(source_path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Invalid backup file encoding") from exc
    payload = _decrypt_payload(encrypted_text, backup_key)
    Path(target_path).write_bytes(payload)


def _validate_tar_member(member: tarfile.TarInfo) -> None:
    name = member.name
    parsed = PurePosixPath(name)
    if not name or parsed.is_absolute() or ".." in parsed.parts:
        raise ValueError("Backup bundle contains an unsafe path")
    if not parsed.parts or parsed.parts[0] not in EXPECTED_BUNDLE_TOP_LEVELS:
        raise ValueError("Backup bundle contains an unexpected file")
    if parsed.parts[0] in {"wgmik.db", "secret_key", "manifest.json"} and len(parsed.parts) != 1:
        raise ValueError("Backup bundle contains an unexpected file")
    if member.issym() or member.islnk():
        raise ValueError("Backup bundle contains unsupported links")
    if not (member.isfile() or member.isdir()):
        raise ValueError("Backup bundle contains unsupported file types")


def _extract_bundle_from_tar_path(tar_path: str, work_dir: str) -> dict[str, Optional[str]]:
    with tarfile.open(tar_path, mode="r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            _validate_tar_member(member)
        archive.extractall(work_dir, members=members)
    db_path = os.path.join(work_dir, "wgmik.db")
    secret_path = os.path.join(work_dir, "secret_key")
    manifest_path = os.path.join(work_dir, "manifest.json")
    outbox_path = os.path.join(work_dir, "telegram_outbox")
    if not os.path.isfile(db_path):
        raise ValueError("Backup bundle is missing wgmik.db")
    if not os.path.isfile(secret_path):
        raise ValueError("Backup bundle is missing secret_key")
    if not os.path.isfile(manifest_path):
        raise ValueError("Backup bundle is missing manifest.json")
    return {
        "db_path": db_path,
        "secret_path": secret_path,
        "manifest_path": manifest_path,
        "outbox_path": outbox_path if os.path.isdir(outbox_path) else None,
    }


def _extract_bundle(payload: bytes, work_dir: str) -> dict[str, str]:
    tar_path = os.path.join(work_dir, "legacy-bundle.tar.gz")
    Path(tar_path).write_bytes(payload)
    extracted = _extract_bundle_from_tar_path(tar_path, work_dir)
    return {
        "db_path": str(extracted["db_path"]),
        "secret_path": str(extracted["secret_path"]),
        "manifest_path": str(extracted["manifest_path"]),
    }


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
            backups_dir = _backups_dir()
            bundle_path = str(backups_dir / filename)

            with tempfile.TemporaryDirectory(prefix="wgmik-backup-", dir=str(backups_dir)) as work_dir:
                snapshot_path = os.path.join(work_dir, "wgmik.db")
                tar_path = os.path.join(work_dir, "bundle.tar.gz")
                encrypted_tmp_path = os.path.join(work_dir, filename)
                engine.dispose()
                _snapshot_database(db_path, snapshot_path)
                prepare_sqlite_database()

                outbox_dir = _telegram_outbox_dir()
                contents = ["wgmik.db", "secret_key", "manifest.json"]
                if outbox_dir.exists() and outbox_dir.is_dir():
                    contents.append("telegram_outbox")
                manifest = {
                    "format_version": BUNDLE_FORMAT_VERSION,
                    "app_name": settings.app_name,
                    "created_at": _iso(_utc_now()),
                    "contents": contents,
                }
                secret_key_value = settings.secret_key
                _build_tar_gz_to_path(snapshot_path, secret_key_value, manifest, tar_path, outbox_dir)

                _update_backup_status(
                    phase="encrypting",
                    phase_label="Encrypting backup",
                    detail="Encrypting the backup bundle with a one-time key.",
                    progress_percent=90.0,
                )
                _encrypt_v2_file_to_path(tar_path, encrypted_tmp_path, backup_key)
                shutil.move(encrypted_tmp_path, bundle_path)

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
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(**_tempfile_kwargs("wgmik-upload-", ".wgmik")) as handle:
            temp_path = handle.name
            handle.write(file_bytes)
        return restore_backup_from_upload_path(temp_path, backup_key)
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                pass


def restore_backup_from_upload_path(
    file_path: str,
    backup_key: str,
    *,
    restart: bool = True,
) -> dict[str, Any]:
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

    backups_dir = _backups_dir()
    timestamp = _utc_now().strftime("%Y%m%d-%H%M%S")
    restored_outbox_stage: Optional[Path] = None
    with tempfile.TemporaryDirectory(prefix="wgmik-restore-", dir=str(backups_dir)) as work_dir:
        tar_path = os.path.join(work_dir, "bundle.tar.gz")
        extracted_dir = os.path.join(work_dir, "extracted")
        os.makedirs(extracted_dir, exist_ok=True)
        _decrypt_backup_file_to_tar_path(file_path, backup_key.strip(), tar_path)
        extracted = _extract_bundle_from_tar_path(tar_path, extracted_dir)
        with open(extracted["manifest_path"], encoding="utf-8") as handle:
            manifest = json.load(handle)
        try:
            format_version = int(manifest.get("format_version") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("Unsupported backup format version") from exc
        if format_version not in {LEGACY_BUNDLE_FORMAT_VERSION, BUNDLE_FORMAT_VERSION}:
            raise ValueError("Unsupported backup format version")
        _validate_sqlite_file(extracted["db_path"])
        secret_key_value = Path(extracted["secret_path"]).read_text(encoding="utf-8").strip()
        if not secret_key_value:
            raise ValueError("Backup bundle secret_key is empty")

        pre_restore_name = f"pre-restore-{timestamp}.db"
        pre_restore_path = str(backups_dir / pre_restore_name)
        pre_restore_outbox_path = backups_dir / f"pre-restore-{timestamp}-telegram_outbox"
        if format_version >= BUNDLE_FORMAT_VERSION:
            restored_outbox_stage = backups_dir / f".telegram_outbox-restore-{timestamp}"
            if restored_outbox_stage.exists():
                shutil.rmtree(restored_outbox_stage)
            outbox_path = extracted.get("outbox_path")
            if outbox_path:
                shutil.copytree(str(outbox_path), str(restored_outbox_stage))
            else:
                restored_outbox_stage.mkdir(parents=True, exist_ok=True)

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
                if format_version >= BUNDLE_FORMAT_VERSION:
                    target_outbox = _telegram_outbox_dir()
                    target_outbox.parent.mkdir(parents=True, exist_ok=True)
                    if target_outbox.exists():
                        if pre_restore_outbox_path.exists():
                            shutil.rmtree(pre_restore_outbox_path)
                        shutil.move(str(target_outbox), str(pre_restore_outbox_path))
                    if restored_outbox_stage:
                        shutil.move(str(restored_outbox_stage), str(target_outbox))
                prepare_sqlite_database()
        finally:
            resume_scheduler()
            if restored_outbox_stage and restored_outbox_stage.exists():
                shutil.rmtree(restored_outbox_stage, ignore_errors=True)

    if restart:
        def _exit_after_response() -> None:
            time.sleep(1.0)
            os._exit(0)

        threading.Thread(target=_exit_after_response, name="restore-exit", daemon=True).start()
    return {
        "ok": True,
        "message": "Restore completed. The server is restarting; sign in again when it is back online.",
        "pre_restore_backup": pre_restore_name,
    }
