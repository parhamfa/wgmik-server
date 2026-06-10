import os
import secrets
import stat
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings
from pydantic import Field


class AppSettings(BaseSettings):
    app_name: str = Field(default="wgmik-server")
    secret_key: str = Field(default="change-me")
    database_url: str = Field(default="sqlite:///./wgmik.db")
    debug: bool = Field(default=True)

    # Polling and accounting
    poll_interval_seconds: int = Field(default=30)
    online_threshold_seconds: int = Field(default=15)
    monthly_reset_day: int = Field(default=1)
    timezone: str = Field(default="UTC")
    date_calendar: str = Field(default="gregorian")

    class Config:
        env_file = ".env"


settings = AppSettings()


# --- SECRET_KEY auto-management (WordPress-salt style) ---------------------
#
# SECRET_KEY does double duty: it signs JWTs *and* derives the Fernet key that
# encrypts RouterOS passwords, WireGuard private/preshared keys, and the
# Telegram token. It must therefore be generated once and persisted; it can
# never be regenerated per boot or all stored secrets become undecryptable.
#
# Resolution priority:
#   1. An explicit SECRET_KEY env value (anything other than empty/"change-me")
#      always wins, so advanced/multi-instance deployments stay in control.
#   2. Otherwise read/create a key file co-located with the SQLite database
#      (e.g. sqlite:////data/wgmik.db -> /data/secret_key), overridable via
#      the SECRET_KEY_FILE env var.

_PLACEHOLDER_SECRETS = {"", "change-me"}


def _sqlite_path_from_url(database_url: str) -> Optional[Path]:
    # Everything after sqlite:/// is the file path:
    #   sqlite:///relative.db   -> relative.db
    #   sqlite:////abs/path.db  -> /abs/path.db
    if database_url.startswith("sqlite:////"):
        path_str = "/" + database_url[len("sqlite:////"):]
    elif database_url.startswith("sqlite:///"):
        path_str = database_url[len("sqlite:///"):]
    else:
        return None
    if not path_str or ":memory:" in path_str:
        return None
    return Path(path_str)


def _default_secret_key_file() -> Path:
    explicit = os.environ.get("SECRET_KEY_FILE", "").strip()
    if explicit:
        return Path(explicit)
    db_path = _sqlite_path_from_url(settings.database_url)
    if db_path is not None:
        return db_path.parent / "secret_key"
    # Non-sqlite (or in-memory) deployments: fall back to a local file.
    return Path("./secret_key")


def _load_or_create_secret_key() -> str:
    key_file = _default_secret_key_file()
    try:
        if key_file.exists():
            existing = key_file.read_text(encoding="utf-8").strip()
            if existing:
                return existing
    except OSError:
        pass

    new_key = secrets.token_urlsafe(48)
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(new_key, encoding="utf-8")
        try:
            key_file.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
    except OSError:
        # If we cannot persist (read-only FS, etc.) fall back to an in-memory
        # key. Encrypted data won't survive a restart, but the app still boots.
        pass
    return new_key


def _resolve_secret_key() -> str:
    env_value = (os.environ.get("SECRET_KEY") or "").strip()
    if env_value and env_value not in _PLACEHOLDER_SECRETS:
        return env_value
    if (settings.secret_key or "").strip() not in _PLACEHOLDER_SECRETS:
        return settings.secret_key
    return _load_or_create_secret_key()


settings.secret_key = _resolve_secret_key()
