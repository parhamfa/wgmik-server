from fastapi.testclient import TestClient

from backend.db import SessionLocal
from backend.main import app
from backend.models import User, UserSecurityEvent


def _fresh_client() -> TestClient:
    return TestClient(app)


def _event_types() -> list[str]:
    db = SessionLocal()
    try:
        return [row.event_type for row in db.query(UserSecurityEvent).order_by(UserSecurityEvent.id.asc()).all()]
    finally:
        db.close()


def test_login_success_updates_last_login_and_audit(client: TestClient):
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        assert admin is not None
        previous_last_login = admin.last_login_at
    finally:
        db.close()

    client.post("/api/auth/logout")
    response = client.post("/api/auth/login", json={"username": "admin", "password": "test-admin-password"})
    assert response.status_code == 200, response.text

    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.username == "admin").first()
        assert admin is not None
        assert admin.last_login_at is not None
        assert previous_last_login is None or admin.last_login_at >= previous_last_login
        assert db.query(UserSecurityEvent).filter(UserSecurityEvent.event_type == "login_success").count() >= 1
    finally:
        db.close()


def test_failed_logins_lock_account_and_admin_can_unlock(client: TestClient):
    create = client.post("/api/users", json={"username": "operator", "password": "operator-pass-123"})
    assert create.status_code == 200, create.text
    operator_id = create.json()["id"]

    client.post("/api/auth/logout")
    for _ in range(4):
        response = client.post("/api/auth/login", json={"username": "operator", "password": "wrong-password"})
        assert response.status_code == 401, response.text

    locked = client.post("/api/auth/login", json={"username": "operator", "password": "wrong-password"})
    assert locked.status_code == 423

    db = SessionLocal()
    try:
        operator = db.get(User, operator_id)
        assert operator is not None
        assert operator.locked_until is not None
    finally:
        db.close()

    blocked = client.post("/api/auth/login", json={"username": "operator", "password": "operator-pass-123"})
    assert blocked.status_code == 423

    admin_login = client.post("/api/auth/login", json={"username": "admin", "password": "test-admin-password"})
    assert admin_login.status_code == 200, admin_login.text
    unlock = client.patch(f"/api/users/{operator_id}", json={"unlock": True})
    assert unlock.status_code == 200, unlock.text

    client.post("/api/auth/logout")
    success = client.post("/api/auth/login", json={"username": "operator", "password": "operator-pass-123"})
    assert success.status_code == 200, success.text

    events = _event_types()
    assert events.count("login_failure") >= 5
    assert "account_lock" in events
    assert "user_unlock" in events


def test_self_password_change_revokes_current_session(client: TestClient):
    response = client.post(
        "/api/auth/change-password",
        json={"current_password": "test-admin-password", "new_password": "new-admin-pass-123"},
    )
    assert response.status_code == 200, response.text

    me = client.get("/api/auth/me")
    assert me.status_code == 401

    old_login = client.post("/api/auth/login", json={"username": "admin", "password": "test-admin-password"})
    assert old_login.status_code == 401

    new_login = client.post("/api/auth/login", json={"username": "admin", "password": "new-admin-pass-123"})
    assert new_login.status_code == 200, new_login.text

    assert "password_change" in _event_types()


def test_admin_reset_deactivate_reactivate_and_delete_user(client: TestClient):
    create = client.post("/api/users", json={"username": "alice", "password": "alice-pass-123"})
    assert create.status_code == 200, create.text
    alice_id = create.json()["id"]

    with _fresh_client() as other:
        other_login = other.post("/api/auth/login", json={"username": "alice", "password": "alice-pass-123"})
        assert other_login.status_code == 200, other_login.text

        reset = client.post(f"/api/users/{alice_id}/reset-password", json={"new_password": "alice-temp-pass-456"})
        assert reset.status_code == 200, reset.text

        stale_session = other.get("/api/auth/me")
        assert stale_session.status_code == 401

        old_login = other.post("/api/auth/login", json={"username": "alice", "password": "alice-pass-123"})
        assert old_login.status_code == 401

        temp_login = other.post("/api/auth/login", json={"username": "alice", "password": "alice-temp-pass-456"})
        assert temp_login.status_code == 200, temp_login.text

        bootstrap = other.get("/api/auth/bootstrap")
        assert bootstrap.status_code == 200, bootstrap.text
        assert bootstrap.json()["user"]["must_change_password"] is True

        delete_active = client.delete(f"/api/users/{alice_id}")
        assert delete_active.status_code == 400

        deactivate = client.patch(f"/api/users/{alice_id}", json={"is_active": False})
        assert deactivate.status_code == 200, deactivate.text

        after_deactivate = other.get("/api/auth/me")
        assert after_deactivate.status_code == 401

        reactivate = client.patch(f"/api/users/{alice_id}", json={"is_active": True})
        assert reactivate.status_code == 200, reactivate.text

        relogin = other.post("/api/auth/login", json={"username": "alice", "password": "alice-temp-pass-456"})
        assert relogin.status_code == 200, relogin.text

    deactivate_again = client.patch(f"/api/users/{alice_id}", json={"is_active": False})
    assert deactivate_again.status_code == 200, deactivate_again.text

    delete_ok = client.delete(f"/api/users/{alice_id}")
    assert delete_ok.status_code == 200, delete_ok.text

    self_delete = client.delete("/api/users/1")
    assert self_delete.status_code == 400

    db = SessionLocal()
    try:
        assert db.get(User, alice_id) is None
    finally:
        db.close()

    events = _event_types()
    assert "admin_password_reset" in events
    assert "user_deactivate" in events
    assert "user_reactivate" in events
    assert "user_delete" in events
