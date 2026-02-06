import os
import pytest
from fastapi.testclient import TestClient

# FORCE environment variables before any other imports that might read settings
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["DEBUG"] = "true"
os.environ["INITIAL_ADMIN_USERNAME"] = "admin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "test-admin-password"

import sys
from pathlib import Path

# Add project root to sys.path so we can import 'backend'
# This assumes conftest.py is in backend/tests/
sys.path.append(str(Path(__file__).parent.parent.parent))

from backend.bootstrap import ensure_initial_admin
from backend.db import Base, get_db, engine, SessionLocal
from backend.main import app

@pytest.fixture(scope="function")
def client():
    # Ensure a clean DB per test (sqlite :memory: persists due to StaticPool)
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    ensure_initial_admin()

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
