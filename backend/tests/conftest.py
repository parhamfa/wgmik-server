import os
import pytest
from datetime import datetime
from fastapi.testclient import TestClient

# FORCE environment variables before any other imports that might read settings
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DEBUG"] = "true"

import sys
from pathlib import Path

# Add project root to sys.path so we can import 'backend'
# This assumes conftest.py is in backend/tests/
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.auth import get_password_hash
from backend.db import Base, get_db, engine, SessionLocal
from backend.main import app
from backend.models import User


def seed_admin(username: str = "admin", password: str = "test-admin-password") -> None:
    """Insert a single active admin directly (no env/log-based bootstrap)."""
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        db.add(
            User(
                username=username,
                hashed_password=get_password_hash(password),
                is_admin=True,
                is_active=True,
                session_version=1,
                password_changed_at=now,
                must_change_password=False,
            )
        )
        db.commit()
    finally:
        db.close()


@pytest.fixture(scope="function")
def client():
    # Ensure a clean DB per test (sqlite :memory: persists due to StaticPool)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    seed_admin()

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        r = c.post("/api/auth/login", json={"username": "admin", "password": "test-admin-password"})
        assert r.status_code == 200
        yield c

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
