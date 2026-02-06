import secrets

from sqlalchemy import select, func

from .auth import get_password_hash
from .db import SessionLocal
from .models import User
from .settings import settings


def ensure_initial_admin() -> None:
    """
    If the DB has no users, create an initial admin user.

    - Username comes from INITIAL_ADMIN_USERNAME (defaults to "admin")
    - Password comes from INITIAL_ADMIN_PASSWORD; if empty, generate one and print it once.
    """
    db = SessionLocal()
    try:
        user_count = db.scalar(select(func.count()).select_from(User)) or 0
        if user_count > 0:
            return

        username = (settings.initial_admin_username or "admin").strip() or "admin"
        password = (settings.initial_admin_password or "").strip()
        generated = False
        if not password:
            password = secrets.token_urlsafe(18)
            generated = True

        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            is_admin=True,
        )
        db.add(user)
        db.commit()

        if generated:
            print(
                "Bootstrapped initial admin user (first run): "
                f"username='{username}' password='{password}'"
            )
        else:
            print(
                "Bootstrapped initial admin user (first run): "
                f"username='{username}' (password set via env)"
            )
    finally:
        db.close()

