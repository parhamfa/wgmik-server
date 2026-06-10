from io import BytesIO

from backend.backup_restore import (
    BUNDLE_FORMAT_VERSION,
    _build_tar_gz,
    _decrypt_payload,
    _encrypt_payload,
)


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
    manifest = {"format_version": BUNDLE_FORMAT_VERSION, "app_name": "wgmik-server"}
    archive = _build_tar_gz(str(db_path), "secret-value", manifest)
    assert isinstance(archive, bytes)
    assert len(archive) > 0


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


def test_restore_rejects_when_secret_key_env_set(client):
    payload = BytesIO(b"not-a-real-backup")
    response = client.post(
        "/api/admin/backup/restore",
        files={"file": ("backup.wgmik", payload, "application/octet-stream")},
        data={"key": "some-key"},
    )
    assert response.status_code == 409
    assert "SECRET_KEY" in response.json()["detail"]
