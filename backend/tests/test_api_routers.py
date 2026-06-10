from fastapi.testclient import TestClient

import backend.api.routes as routes_module
from backend.db import SessionLocal
from backend.models import Peer


def test_create_and_list_router(client: TestClient, monkeypatch):
    class StubClient:
        def get_system_version(self):
            return "7.15"

    monkeypatch.setattr(routes_module, "make_client", lambda router: StubClient())

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


def test_auth_bootstrap_tracks_onboarding_state(client: TestClient, monkeypatch):
    class StubClient:
        def get_system_version(self):
            return "7.15"

    monkeypatch.setattr(routes_module, "make_client", lambda router: StubClient())

    bootstrap = client.get("/api/auth/bootstrap")
    assert bootstrap.status_code == 200
    assert bootstrap.json()["needs_onboarding"] is True
    assert bootstrap.json()["router_count"] == 0
    assert bootstrap.json()["needs_peer_import"] is False

    create = client.post("/api/routers", json={
        "name": "Bootstrap Router",
        "host": "192.168.88.1",
        "proto": "rest",
        "port": 443,
        "username": "admin",
        "password": "secret_password",
        "tls_verify": False,
    })
    assert create.status_code == 200
    router_id = create.json()["id"]

    bootstrap = client.get("/api/auth/bootstrap")
    assert bootstrap.status_code == 200
    payload = bootstrap.json()
    assert payload["needs_onboarding"] is False
    assert payload["router_count"] == 1
    assert payload["enabled_router_count"] == 1
    assert payload["peer_count"] == 0
    assert payload["needs_peer_import"] is True

    db = SessionLocal()
    try:
        db.add(Peer(
            router_id=router_id,
            interface="wgmik",
            ros_id="*1",
            name="Imported Peer",
            public_key="peer-public-key",
            allowed_address="10.0.0.2/32",
            comment="",
            disabled=False,
            selected=True,
        ))
        db.commit()
    finally:
        db.close()

    bootstrap = client.get("/api/auth/bootstrap")
    assert bootstrap.status_code == 200
    payload = bootstrap.json()
    assert payload["peer_count"] == 1
    assert payload["selected_peer_count"] == 1
    assert payload["needs_peer_import"] is False
