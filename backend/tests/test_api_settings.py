from fastapi.testclient import TestClient

from backend.db import SessionLocal
from backend.models import SettingsKV


def test_read_settings(client: TestClient):
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    # app_name is not part of the public SettingsDTO
    # assert "app_name" in data
    
    # Ensure default value is returned (from pydantic model default)
    assert "poll_interval_seconds" in data
    assert isinstance(data["poll_interval_seconds"], int)
    assert data["date_calendar"] == "gregorian"
    assert data.get("dashboard_peer_preview_count") == 6
    assert data["dashboard_scope_value"] == 24
    assert data["dashboard_scope_unit"] == "hours"
    assert data["peer_default_scope_value"] == 60
    assert data["peer_default_scope_unit"] == "minutes"
    assert "dashboard_refresh_seconds" not in data
    assert "peer_refresh_seconds" not in data


def test_update_settings_removes_retired_refresh_keys(client: TestClient):
    db = SessionLocal()
    try:
        db.add_all(
            [
                SettingsKV(key="dashboard_refresh_seconds", value="9"),
                SettingsKV(key="peer_refresh_seconds", value="11"),
            ]
        )
        db.commit()
    finally:
        db.close()

    payload = client.get("/api/settings").json()
    payload["show_hw_stats"] = not payload["show_hw_stats"]

    response = client.put("/api/settings", json=payload)
    assert response.status_code == 200, response.text

    db = SessionLocal()
    try:
        assert db.get(SettingsKV, "dashboard_refresh_seconds") is None
        assert db.get(SettingsKV, "peer_refresh_seconds") is None
    finally:
        db.close()
