from pydantic_settings import BaseSettings
from pydantic import Field


class AppSettings(BaseSettings):
    app_name: str = Field(default="wgmik-server")
    secret_key: str = Field(default="change-me")
    database_url: str = Field(default="sqlite:///./wgmik.db")
    debug: bool = Field(default=True)

    # Authentication bootstrap (first run)
    initial_admin_username: str = Field(default="admin")
    # If empty, a random password will be generated and printed to logs once.
    initial_admin_password: str = Field(default="")

    # Polling and accounting
    poll_interval_seconds: int = Field(default=30)
    online_threshold_seconds: int = Field(default=15)
    monthly_reset_day: int = Field(default=1)
    timezone: str = Field(default="UTC")

    class Config:
        env_file = ".env"


settings = AppSettings()

