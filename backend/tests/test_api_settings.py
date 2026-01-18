from fastapi.testclient import TestClient

def test_read_settings(client: TestClient):
    response = client.get("/api/settings")
    assert response.status_code == 200
    data = response.json()
    # app_name is not part of the public SettingsDTO
    # assert "app_name" in data
    
    # Ensure default value is returned (from pydantic model default)
    assert "poll_interval_seconds" in data
    assert isinstance(data["poll_interval_seconds"], int)
