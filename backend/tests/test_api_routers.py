from fastapi.testclient import TestClient

def test_create_and_list_router(client: TestClient):
    # 1. Create a router
    payload = {
        "name": "Test Router",
        "host": "192.168.1.1",
        "proto": "rest",
        "port": 443,
        "username": "admin",
        "password": "secret_password",
        "tls_verify": False
    }
    response = client.post("/api/routers", json=payload)
    assert response.status_code == 200
    created = response.json()
    assert created["name"] == payload["name"]
    assert "id" in created

    # 2. List routers
    response = client.get("/api/routers")
    assert response.status_code == 200
    routers = response.json()
    assert len(routers) == 1
    assert routers[0]["id"] == created["id"]
    assert routers[0]["name"] == "Test Router"
    # Password should NOT be returned in plain text or at all in this DTO if not modeled
    # (Checking RouterDTO definition in routes.py might be needed, currently it doesn't return password)
