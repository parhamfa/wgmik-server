from datetime import datetime, timedelta, timezone
from typing import Optional, TypedDict
import jwt
from jwt.exceptions import PyJWTError
from passlib.context import CryptContext
from fastapi import HTTPException, status
from .settings import settings

# Configuration
SECRET_KEY = settings.secret_key
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class TokenClaims(TypedDict):
    user_id: int
    session_version: int


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(user_id: int, session_version: int, expires_delta: Optional[timedelta] = None) -> str:
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    encoded_jwt = jwt.encode(
        {"sub": str(user_id), "sv": int(session_version), "exp": expire},
        SECRET_KEY,
        algorithm=ALGORITHM,
    )
    return encoded_jwt


def verify_token(token: str) -> Optional[TokenClaims]:
    """Return user-id and session-version claims if valid, else None."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        raw_sub = payload.get("sub")
        raw_sv = payload.get("sv")
        if raw_sub is None or raw_sv is None:
            return None
        return {
            "user_id": int(raw_sub),
            "session_version": int(raw_sv),
        }
    except PyJWTError:
        return None
    except (TypeError, ValueError):
        return None
