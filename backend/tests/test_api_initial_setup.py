from fastapi.testclient import TestClient

from backend.db import Base, get_db, engine, SessionLocal
from backend.main import app


def _fresh_client() -> TestClient:
    """A client backed by an empty user table (no seeded admin)."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def test_setup_state_true_then_false_after_setup():
    c = _fresh_client()
    try:
        state = c.get("/api/auth/setup-state")
        assert state.status_code == 200
        assert state.json()["needs_initial_setup"] is True

        created = c.post(
            "/api/auth/setup",
            json={"username": "owner", "password": "super-secret-1"},
        )
        assert created.status_code == 200, created.text
        assert created.json()["ok"] is True

        # Setup also logs the new admin in (auth cookie set).
        me = c.get("/api/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["username"] == "owner"
        assert me.json()["is_admin"] is True

        state = c.get("/api/auth/setup-state")
        assert state.status_code == 200
        assert state.json()["needs_initial_setup"] is False
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


def test_setup_rejected_once_a_user_exists():
    c = _fresh_client()
    try:
        first = c.post(
            "/api/auth/setup",
            json={"username": "owner", "password": "super-secret-1"},
        )
        assert first.status_code == 200, first.text

        second = c.post(
            "/api/auth/setup",
            json={"username": "intruder", "password": "another-secret-1"},
        )
        assert second.status_code == 409, second.text
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)


def test_setup_enforces_password_policy():
    c = _fresh_client()
    try:
        resp = c.post(
            "/api/auth/setup",
            json={"username": "owner", "password": "short"},
        )
        assert resp.status_code == 400, resp.text
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
