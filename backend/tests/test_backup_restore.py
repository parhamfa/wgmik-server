import os
import shutil
import sqlite3
import tarfile
from io import BytesIO

import pytest

from backend.db import Base, engine
from backend.backup_restore import (
    BUNDLE_FORMAT_VERSION,
    LEGACY_BUNDLE_FORMAT_VERSION,
    V2_MAGIC,
    _build_tar_gz,
    _build_tar_gz_to_path,
    _decrypt_backup_file_to_tar_path,
    _decrypt_payload,
    _encrypt_v2_file_to_path,
    _encrypt_payload,
    _extract_bundle_from_tar_path,
    run_backup_once,
    restore_backup_from_upload_path,
)


def _create_sqlite_db(path, value: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        conn.execute("INSERT INTO sample (value) VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()


def _read_sqlite_value(path) -> str:
    conn = sqlite3.connect(path)
    try:
        return str(conn.execute("SELECT value FROM sample").fetchone()[0])
    finally:
        conn.close()


def _make_v2_backup(tmp_path, db_path, key: str, outbox_dir=None):
    tar_path = tmp_path / "bundle.tar.gz"
    backup_path = tmp_path / "backup.wgmik"
    manifest = {
        "format_version": BUNDLE_FORMAT_VERSION,
        "app_name": "wgmik-server",
        "contents": ["wgmik.db", "secret_key", "manifest.json"],
    }
    if outbox_dir is not None:
        manifest["contents"].append("telegram_outbox")
    _build_tar_gz_to_path(str(db_path), "restored-secret", manifest, str(tar_path), outbox_dir)
    _encrypt_v2_file_to_path(str(tar_path), str(backup_path), key, chunk_size=7)
    return backup_path


def test_encrypt_decrypt_roundtrip():
    payload = b"test-backup-payload"
    key = "backup-key-for-test-only"
    encrypted = _encrypt_payload(payload, key)
    assert _decrypt_payload(encrypted, key) == payload
    try:
        _decrypt_payload(encrypted, "wrong-key")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_build_tar_gz_contains_expected_files(tmp_path):
    db_path = tmp_path / "wgmik.db"
    db_path.write_bytes(b"sqlite-bytes")
    manifest = {"format_version": LEGACY_BUNDLE_FORMAT_VERSION, "app_name": "wgmik-server"}
    archive = _build_tar_gz(str(db_path), "secret-value", manifest)
    assert isinstance(archive, bytes)
    assert len(archive) > 0


def test_v2_stream_encryption_roundtrip_wrong_key_and_corruption(tmp_path):
    source = tmp_path / "source.tar.gz"
    encrypted = tmp_path / "backup.wgmik"
    decrypted = tmp_path / "decrypted.tar.gz"
    source.write_bytes((b"abcdefg" * 2048) + b"tail")
    key = "backup-key-for-test-only"

    _encrypt_v2_file_to_path(str(source), str(encrypted), key, chunk_size=17)

    assert encrypted.read_bytes().startswith(V2_MAGIC)
    _decrypt_backup_file_to_tar_path(str(encrypted), key, str(decrypted))
    assert decrypted.read_bytes() == source.read_bytes()
    with pytest.raises(ValueError, match="Wrong backup key|corrupt"):
        _decrypt_backup_file_to_tar_path(str(encrypted), "wrong-key", str(tmp_path / "wrong.tar.gz"))

    corrupted = tmp_path / "corrupted.wgmik"
    data = bytearray(encrypted.read_bytes())
    data[-1] ^= 1
    corrupted.write_bytes(data)
    with pytest.raises(ValueError, match="Wrong backup key|corrupt"):
        _decrypt_backup_file_to_tar_path(str(corrupted), key, str(tmp_path / "corrupted.tar.gz"))

    truncated = tmp_path / "truncated.wgmik"
    truncated.write_bytes(encrypted.read_bytes()[:-5])
    with pytest.raises(ValueError, match="Wrong backup key|corrupt"):
        _decrypt_backup_file_to_tar_path(str(truncated), key, str(tmp_path / "truncated.tar.gz"))


def test_tar_bundle_includes_telegram_outbox(tmp_path):
    db_path = tmp_path / "wgmik.db"
    db_path.write_bytes(b"sqlite-bytes")
    outbox = tmp_path / "telegram_outbox_src"
    (outbox / "photos").mkdir(parents=True)
    (outbox / "photos" / "photo.jpg").write_bytes(b"photo-bytes")
    tar_path = tmp_path / "bundle.tar.gz"
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    manifest = {
        "format_version": BUNDLE_FORMAT_VERSION,
        "app_name": "wgmik-server",
        "contents": ["wgmik.db", "secret_key", "manifest.json", "telegram_outbox"],
    }

    _build_tar_gz_to_path(str(db_path), "secret-value", manifest, str(tar_path), outbox)
    extracted = _extract_bundle_from_tar_path(str(tar_path), str(extract_dir))

    assert extracted["outbox_path"] is not None
    assert (extract_dir / "telegram_outbox" / "photos" / "photo.jpg").read_bytes() == b"photo-bytes"


def test_extract_bundle_rejects_path_traversal(tmp_path):
    tar_path = tmp_path / "bad.tar.gz"
    with tarfile.open(tar_path, mode="w:gz") as archive:
        data = b"bad"
        info = tarfile.TarInfo(name="../evil")
        info.size = len(data)
        archive.addfile(info, BytesIO(data))

    with pytest.raises(ValueError, match="unsafe path"):
        _extract_bundle_from_tar_path(str(tar_path), str(tmp_path / "extract"))


def test_extract_bundle_rejects_links(tmp_path):
    tar_path = tmp_path / "bad-link.tar.gz"
    with tarfile.open(tar_path, mode="w:gz") as archive:
        info = tarfile.TarInfo(name="telegram_outbox/link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        archive.addfile(info)

    with pytest.raises(ValueError, match="unsupported links"):
        _extract_bundle_from_tar_path(str(tar_path), str(tmp_path / "extract"))


def test_legacy_v1_backup_can_still_be_decrypted_and_extracted(tmp_path):
    db_path = tmp_path / "wgmik.db"
    db_path.write_bytes(b"sqlite-bytes")
    manifest = {"format_version": LEGACY_BUNDLE_FORMAT_VERSION, "app_name": "wgmik-server"}
    encrypted_path = tmp_path / "legacy.wgmik"
    tar_path = tmp_path / "legacy.tar.gz"
    extract_dir = tmp_path / "extract"
    extract_dir.mkdir()
    key = "backup-key-for-test-only"

    encrypted_path.write_text(_encrypt_payload(_build_tar_gz(str(db_path), "secret-value", manifest), key), encoding="utf-8")
    _decrypt_backup_file_to_tar_path(str(encrypted_path), key, str(tar_path))
    extracted = _extract_bundle_from_tar_path(str(tar_path), str(extract_dir))

    assert extracted["outbox_path"] is None
    assert (extract_dir / "secret_key").read_text(encoding="utf-8") == "secret-value"


def test_restore_backup_from_upload_path_v2_replaces_db_secret_and_outbox(tmp_path, monkeypatch):
    key = "backup-key-for-test-only"
    live_db = tmp_path / "wgmik.db"
    backup_db = tmp_path / "backup-source.db"
    _create_sqlite_db(live_db, "old")
    _create_sqlite_db(backup_db, "new")
    old_outbox = tmp_path / "telegram_outbox"
    old_outbox.mkdir()
    (old_outbox / "old.txt").write_text("old", encoding="utf-8")
    backup_outbox = tmp_path / "backup-outbox"
    backup_outbox.mkdir()
    (backup_outbox / "photo.jpg").write_bytes(b"photo")
    backup_path = _make_v2_backup(tmp_path, backup_db, key, backup_outbox)

    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr("backend.backup_restore.sqlite_database_path", lambda: str(live_db))
    monkeypatch.setattr("backend.backup_restore._secret_key_from_env", lambda: False)
    monkeypatch.setattr("backend.backup_restore.pause_scheduler", lambda: None)
    monkeypatch.setattr("backend.backup_restore.resume_scheduler", lambda: None)
    monkeypatch.setattr("backend.backup_restore.prepare_sqlite_database", lambda: None)
    monkeypatch.setattr("backend.backup_restore.is_usage_maintenance_running", lambda _db: False)
    monkeypatch.setattr("backend.backup_restore.is_backup_running", lambda _db: False)

    result = restore_backup_from_upload_path(str(backup_path), key, restart=False)

    assert result["ok"] is True
    assert _read_sqlite_value(live_db) == "new"
    assert (tmp_path / "secret_key").read_text(encoding="utf-8").strip() == "restored-secret"
    assert not (tmp_path / "telegram_outbox" / "old.txt").exists()
    assert (tmp_path / "telegram_outbox" / "photo.jpg").read_bytes() == b"photo"
    assert (tmp_path / "backups" / result["pre_restore_backup"]).exists()


def test_backup_status_endpoint(client, monkeypatch):
    monkeypatch.setattr(
        "backend.api.routes.get_backup_status",
        lambda: {
            "running": False,
            "phase": "idle",
            "phase_label": "Idle",
            "elapsed_seconds": 0,
            "progress_percent": 0.0,
        },
    )
    response = client.get("/api/admin/backup")
    assert response.status_code == 200
    assert response.json()["phase"] == "idle"


def test_run_backup_once_does_not_run_usage_maintenance(tmp_path, client, monkeypatch):
    db_path = tmp_path / "wgmik.db"
    _create_sqlite_db(db_path, "live")
    backups_dir = tmp_path / "backups"
    backups_dir.mkdir()
    calls = {"validated": False}

    def fake_validate(path):
        assert path == str(db_path)
        calls["validated"] = True

    def fake_snapshot(source, target):
        assert source == str(db_path)
        shutil.copy2(source, target)

    def forbidden_maintenance(_path):
        raise AssertionError("manual backup must not run usage maintenance")

    monkeypatch.setattr("backend.backup_restore.sqlite_database_path", lambda: str(db_path))
    monkeypatch.setattr("backend.backup_restore._backups_dir", lambda: backups_dir)
    monkeypatch.setattr("backend.backup_restore._validate_sqlite_file", fake_validate)
    monkeypatch.setattr("backend.backup_restore._snapshot_database", fake_snapshot)
    monkeypatch.setattr("backend.backup_restore.engine.dispose", lambda: None)
    monkeypatch.setattr("backend.backup_restore.pause_scheduler", lambda: None)
    monkeypatch.setattr("backend.backup_restore.resume_scheduler", lambda: None)
    monkeypatch.setattr("backend.backup_restore.prepare_sqlite_database", lambda: None)
    monkeypatch.setattr("backend.backup_restore.is_usage_maintenance_running", lambda _db: False)
    monkeypatch.setattr("backend.usage_maintenance._run_usage_maintenance", forbidden_maintenance)

    status = run_backup_once()

    assert calls["validated"] is True
    assert status["phase"] == "complete"
    assert status["file_size"] > 0


def test_restore_endpoint_streams_upload_to_temp_path(client, monkeypatch):
    seen = {}

    def fake_restore(path, key):
        seen["path"] = path
        seen["exists_during_restore"] = os.path.exists(path)
        seen["payload"] = open(path, "rb").read()
        seen["key"] = key
        return {"ok": True, "message": "restored", "pre_restore_backup": "pre.db"}

    monkeypatch.setattr("backend.api.routes.restore_backup_from_upload_path", fake_restore)
    response = client.post(
        "/api/admin/backup/restore",
        files={"file": ("backup.wgmik", BytesIO(b"stream-me"), "application/octet-stream")},
        data={"key": "some-key"},
    )

    assert response.status_code == 200
    assert seen["exists_during_restore"] is True
    assert seen["payload"] == b"stream-me"
    assert seen["key"] == "some-key"
    assert not os.path.exists(seen["path"])


def test_restore_rejects_when_secret_key_env_set(client):
    payload = BytesIO(b"not-a-real-backup")
    response = client.post(
        "/api/admin/backup/restore",
        files={"file": ("backup.wgmik", payload, "application/octet-stream")},
        data={"key": "some-key"},
    )
    assert response.status_code == 409
    assert "SECRET_KEY" in response.json()["detail"]
