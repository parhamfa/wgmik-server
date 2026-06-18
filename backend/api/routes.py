from fastapi import APIRouter, Depends, HTTPException, Response, Request, Query, status, UploadFile, File, Form
from fastapi.responses import JSONResponse, FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy import func, or_, text
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..db import get_db, Base, engine, SessionLocal, sqlite_database_path
from ..destructive_ops import exclusive_operation_gate, ExclusiveOperationInProgress
from ..settings import settings
from ..scheduler import (
    update_scheduler_interval,
    set_polling_paused,
    reschedule_usage_maintenance_job,
    get_usage_maintenance_next_run,
)
from ..security import SecretBox
from ..models import Router, SettingsKV, Peer, UsageDaily, UsageMinute, UsageMonthly, UsageSample, Quota, Action, User, UserSecurityEvent, FairUsageRule, FairUsageAssignment, FairUsageState, FairUsageTier, PeerTotalsMerge, TelegramPeerBinding, TelegramSignupToken, TelegramNotificationLog
from ..auth import verify_password, get_password_hash, create_access_token, verify_token, ACCESS_TOKEN_EXPIRE_MINUTES
from ..routeros.factory import make_client
from ..routeros import tls_setup as router_tls_setup
from ..routeros.version import assert_routeros_supported, is_routeros_supported
from ..usage_maintenance import (
    cancel_usage_maintenance,
    get_last_auto_run,
    get_usage_maintenance_status,
    is_usage_maintenance_running,
    load_auto_maintenance_settings,
    normalize_auto_maintenance_settings,
    start_usage_maintenance,
)
from ..backup_restore import (
    get_backup_status,
    is_backup_running,
    resolve_backup_download,
    restore_backup_from_upload,
    start_backup,
)
from ..fair_usage_sync import (
    FU_QUEUE_PREFIX,
    apply_fair_usage_policy,
    FairUsageRouterError,
    peer_ids_with_applicable_fair_usage,
)
from ..fair_usage_usage import (
    SCOPE_UNIT_MAX,
    app_zoneinfo,
    format_scope_label,
    normalize_scope_period,
    sync_legacy_time_scope_field,
)
from ..fair_usage_peer_status_dto import (
    FairUsagePeerStatusDTO,
    build_fair_usage_peer_status_dto,
)
from ..fair_usage_tiers import ordered_tiers_for_rule, replace_rule_tiers
from ..usage_deltas import CounterQuarantineState, counter_day_key, counter_delta, guarded_delta_sql, near_32bit_drop_sql
from ..usage_bucketing import aggregate_rows_to_local_buckets, aggregate_router_rows_to_local_buckets
from ..calendar_utils import (
    app_date_calendar,
    normalize_date_calendar,
    selected_month_bounds_utc,
)
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import traceback
from ipaddress import ip_network
import os
import sqlite3
import httpx
from zoneinfo import ZoneInfo
import base64
import json
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    psutil = None


router = APIRouter(prefix="/api", tags=["api"])


def _dt_to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _normalize_time_range(
    start: Optional[datetime], end: Optional[datetime]
) -> tuple[Optional[datetime], Optional[datetime]]:
    start_utc = _dt_to_utc(start) if start is not None else None
    end_utc = _dt_to_utc(end) if end is not None else None
    if start_utc is not None and end_utc is not None and end_utc < start_utc:
        raise HTTPException(status_code=400, detail="end must be >= start")
    return start_utc, end_utc


def _normalize_router_ids(router_ids: Optional[List[int]]) -> Optional[List[int]]:
    if router_ids is None:
        return None
    out: list[int] = []
    seen: set[int] = set()
    for raw in router_ids:
        try:
            rid = int(raw)
        except Exception:
            continue
        if rid <= 0 or rid in seen:
            continue
        seen.add(rid)
        out.append(rid)
    return out


def _normalize_dashboard_router_scope(value: Optional[str]) -> str:
    value = (value or "all").strip().lower()
    return value if value in {"all", "selected"} else "all"


def _resolve_router_filter(
    router_id: Optional[int],
    router_ids: Optional[List[int]],
) -> tuple[Optional[int], Optional[List[int]]]:
    normalized_ids = _normalize_router_ids(router_ids)
    if normalized_ids is not None:
        return None, normalized_ids
    if router_id is None:
        return None, None
    try:
        rid = int(router_id)
    except Exception:
        return None, None
    return (rid if rid > 0 else None), None


def _apply_router_filter(q, router_id: Optional[int], router_ids: Optional[List[int]]):
    if router_ids is not None:
        if not router_ids:
            return q.filter(False)
        return q.filter(Peer.router_id.in_(router_ids))
    if router_id is not None:
        return q.filter(Peer.router_id == router_id)
    return q


def _router_sql_filter(router_id: Optional[int], router_ids: Optional[List[int]]) -> str:
    if router_ids is not None:
        if not router_ids:
            return "AND 1 = 0"
        safe_ids = ", ".join(str(int(rid)) for rid in router_ids)
        return f"AND p.router_id IN ({safe_ids})"
    if router_id is not None:
        return f"AND p.router_id = {int(router_id)}"
    return ""


def _require_admin(current_user: User) -> None:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")


def _normalize_raw_window(
    *,
    seconds: Optional[int],
    start_utc: Optional[datetime],
    end_utc: Optional[datetime],
) -> tuple[int, datetime, datetime]:
    try:
        resolved_seconds = int(seconds or 3600)
    except Exception:
        resolved_seconds = 3600
    resolved_seconds = max(60, min(7 * 24 * 3600, resolved_seconds))
    now_utc = datetime.now(timezone.utc)
    end_dt = end_utc or now_utc
    if start_utc is not None:
        cutoff = start_utc
        span = (end_dt - cutoff).total_seconds()
        if span > 7 * 24 * 3600:
            raise HTTPException(status_code=400, detail="raw window max span is 7 days")
        resolved_seconds = int(max(60, min(7 * 24 * 3600, span)))
    else:
        cutoff = end_dt - timedelta(seconds=resolved_seconds)
    return resolved_seconds, cutoff, end_dt


def _normalize_raw_interval(seconds: int, interval: Optional[int]) -> int:
    resolved = interval if interval and interval > 0 else 0
    if resolved > 0:
        return resolved
    if seconds <= 3600:
        return 60
    if seconds <= 86400:
        return 3600
    return 6 * 3600


def _floor_to_minute_utc_naive(dt: datetime) -> datetime:
    return _dt_to_utc(dt).replace(second=0, microsecond=0, tzinfo=None)


def _ts_cell_to_utc_naive(val: object) -> datetime:
    """SQLite text() queries often return timestamps as str; ORM returns datetime."""
    if isinstance(val, datetime):
        return val.replace(tzinfo=None) if val.tzinfo else val
    if isinstance(val, str):
        s = val.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        if len(s) == 10 and s[4] == "-" and s[7] == "-":
            return datetime.fromisoformat(s + "T00:00:00")
        return datetime.fromisoformat(s.replace(" ", "T", 1))
    raise TypeError(f"expected datetime-like, got {type(val)}")


def _minute_coverage_covers_window_start(
    db: Session,
    *,
    peer_id: Optional[int] = None,
    router_id: Optional[int] = None,
    router_ids: Optional[List[int]] = None,
    selected_only: bool = True,
    cutoff: datetime,
    end_dt: datetime,
) -> bool:
    q = db.query(func.min(UsageMinute.minute_ts))
    if peer_id is not None:
        q = q.filter(UsageMinute.peer_id == peer_id)
    else:
        q = q.join(Peer, Peer.id == UsageMinute.peer_id)
        if selected_only:
            q = q.filter(Peer.selected == True)
        q = _apply_router_filter(q, router_id, router_ids)
    earliest = q.scalar()
    if earliest is None:
        return False
    cutoff_floor = _floor_to_minute_utc_naive(cutoff)
    if earliest <= cutoff_floor:
        return True

    raw_q = db.query(UsageSample.id)
    if peer_id is not None:
        raw_q = raw_q.filter(
            UsageSample.peer_id == peer_id,
            UsageSample.ts >= cutoff.replace(tzinfo=None),
            UsageSample.ts < earliest,
        )
    else:
        raw_q = (
            raw_q.join(Peer, Peer.id == UsageSample.peer_id)
            .filter(
                UsageSample.ts >= cutoff.replace(tzinfo=None),
                UsageSample.ts < earliest,
            )
        )
        if selected_only:
            raw_q = raw_q.filter(Peer.selected == True)
        raw_q = _apply_router_filter(raw_q, router_id, router_ids)
    return raw_q.first() is None


def _query_raw_peer_summaries(
    db: Session,
    *,
    cutoff: datetime,
    end_dt: datetime,
    router_id: Optional[int],
    router_ids: Optional[List[int]],
) -> list[tuple[int, int, int]]:
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    router_filter = _router_sql_filter(router_id, router_ids)
    query = text(f"""
    WITH filtered_peers AS (
        SELECT p.id
        FROM peers p
        WHERE p.selected = 1
          {router_filter}
    ),
    filtered_samples AS (
        SELECT 
            u.id,
            u.peer_id,
            u.ts,
            u.rx,
            u.tx
        FROM filtered_peers fp
        JOIN usage_samples u ON u.peer_id = fp.id
        WHERE u.ts >= :cutoff
          AND u.ts <= :end
    ),
    deltas AS (
        SELECT
            id,
            peer_id,
            ts,
            rx,
            tx,
            LAG(rx) OVER (PARTITION BY peer_id ORDER BY ts, id) as prev_rx,
            LAG(tx) OVER (PARTITION BY peer_id ORDER BY ts, id) as prev_tx
        FROM filtered_samples
    ),
    marked AS (
        SELECT
            id,
            peer_id,
            ts,
            rx,
            tx,
            prev_rx,
            prev_tx,
            {near_32bit_drop_sql("rx", "prev_rx")} AS rx_near_32bit_drop,
            {near_32bit_drop_sql("tx", "prev_tx")} AS tx_near_32bit_drop
        FROM deltas
    ),
    guarded AS (
        SELECT
            id,
            peer_id,
            ts,
            rx,
            tx,
            prev_rx,
            prev_tx,
            SUM(rx_near_32bit_drop) OVER (
                PARTITION BY peer_id, substr(ts, 1, 10)
                ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS rx_unstable,
            SUM(tx_near_32bit_drop) OVER (
                PARTITION BY peer_id, substr(ts, 1, 10)
                ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS tx_unstable
        FROM marked
    )
    SELECT
        peer_id,
        SUM({guarded_delta_sql("rx", "prev_rx", "rx_unstable")}) as total_rx,
        SUM({guarded_delta_sql("tx", "prev_tx", "tx_unstable")}) as total_tx
    FROM guarded
    WHERE prev_rx IS NOT NULL
    GROUP BY peer_id
    """)
    return [(int(r[0]), int(r[1] or 0), int(r[2] or 0)) for r in db.execute(query, {"cutoff": cutoff_str, "end": end_str})]


def _query_raw_sample_delta_rows(
    db: Session,
    *,
    cutoff: datetime,
    end_dt: datetime,
    router_id: Optional[int],
    router_ids: Optional[List[int]],
) -> list[tuple[datetime, int, int]]:
    """Per-sample traffic deltas for aggregate chart; caller buckets in app timezone."""
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    router_filter = _router_sql_filter(router_id, router_ids)
    query = text(f"""
    WITH filtered_peers AS (
        SELECT p.id
        FROM peers p
        WHERE p.selected = 1
          {router_filter}
    ),
    filtered_samples AS (
        SELECT
            u.id,
            u.peer_id,
            u.ts,
            u.rx,
            u.tx
        FROM filtered_peers fp
        JOIN usage_samples u ON u.peer_id = fp.id
        WHERE u.ts >= :cutoff
          AND u.ts <= :end
    ),
    deltas AS (
        SELECT
            id,
            peer_id,
            ts,
            rx,
            tx,
            LAG(rx) OVER (PARTITION BY peer_id ORDER BY ts, id) as prev_rx,
            LAG(tx) OVER (PARTITION BY peer_id ORDER BY ts, id) as prev_tx
        FROM filtered_samples
    ),
    marked AS (
        SELECT
            id,
            peer_id,
            ts,
            rx,
            tx,
            prev_rx,
            prev_tx,
            {near_32bit_drop_sql("rx", "prev_rx")} AS rx_near_32bit_drop,
            {near_32bit_drop_sql("tx", "prev_tx")} AS tx_near_32bit_drop
        FROM deltas
    ),
    guarded AS (
        SELECT
            id,
            peer_id,
            ts,
            rx,
            tx,
            prev_rx,
            prev_tx,
            SUM(rx_near_32bit_drop) OVER (
                PARTITION BY peer_id, substr(ts, 1, 10)
                ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS rx_unstable,
            SUM(tx_near_32bit_drop) OVER (
                PARTITION BY peer_id, substr(ts, 1, 10)
                ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS tx_unstable
        FROM marked
    )
    SELECT
        ts,
        {guarded_delta_sql("rx", "prev_rx", "rx_unstable")} as d_rx,
        {guarded_delta_sql("tx", "prev_tx", "tx_unstable")} as d_tx
    FROM guarded
    WHERE prev_rx IS NOT NULL
    ORDER BY ts
    """)
    out: list[tuple[datetime, int, int]] = []
    for r in db.execute(query, {"cutoff": cutoff_str, "end": end_str}).fetchall():
        ts_naive = _ts_cell_to_utc_naive(r[0])
        out.append((ts_naive, int(r[1] or 0), int(r[2] or 0)))
    return out


def _query_raw_router_sample_delta_rows(
    db: Session,
    *,
    cutoff: datetime,
    end_dt: datetime,
    router_id: Optional[int],
    router_ids: Optional[List[int]],
) -> list[tuple[int, datetime, int, int]]:
    """Per-sample deltas per router for by-router chart; caller buckets in app timezone."""
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
    router_filter = _router_sql_filter(router_id, router_ids)
    query = text(f"""
    WITH filtered_peers AS (
        SELECT
            p.id,
            p.router_id
        FROM peers p
        WHERE p.selected = 1
          {router_filter}
    ),
    filtered_samples AS (
        SELECT
            u.id,
            fp.router_id,
            u.peer_id,
            u.ts,
            u.rx,
            u.tx
        FROM filtered_peers fp
        JOIN usage_samples u ON u.peer_id = fp.id
        WHERE u.ts >= :cutoff
          AND u.ts <= :end
    ),
    deltas AS (
        SELECT
            id,
            router_id,
            peer_id,
            ts,
            rx,
            tx,
            LAG(rx) OVER (PARTITION BY peer_id ORDER BY ts, id) as prev_rx,
            LAG(tx) OVER (PARTITION BY peer_id ORDER BY ts, id) as prev_tx
        FROM filtered_samples
    ),
    marked AS (
        SELECT
            id,
            router_id,
            peer_id,
            ts,
            rx,
            tx,
            prev_rx,
            prev_tx,
            {near_32bit_drop_sql("rx", "prev_rx")} AS rx_near_32bit_drop,
            {near_32bit_drop_sql("tx", "prev_tx")} AS tx_near_32bit_drop
        FROM deltas
    ),
    guarded AS (
        SELECT
            id,
            router_id,
            peer_id,
            ts,
            rx,
            tx,
            prev_rx,
            prev_tx,
            SUM(rx_near_32bit_drop) OVER (
                PARTITION BY peer_id, substr(ts, 1, 10)
                ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS rx_unstable,
            SUM(tx_near_32bit_drop) OVER (
                PARTITION BY peer_id, substr(ts, 1, 10)
                ORDER BY ts, id
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS tx_unstable
        FROM marked
    )
    SELECT
        router_id,
        ts,
        {guarded_delta_sql("rx", "prev_rx", "rx_unstable")} as d_rx,
        {guarded_delta_sql("tx", "prev_tx", "tx_unstable")} as d_tx
    FROM guarded
    WHERE prev_rx IS NOT NULL
    ORDER BY router_id, ts
    """)
    out: list[tuple[int, datetime, int, int]] = []
    for r in db.execute(query, {"cutoff": cutoff_str, "end": end_str}).fetchall():
        ts_naive = _ts_cell_to_utc_naive(r[1])
        out.append((int(r[0]), ts_naive, int(r[2] or 0), int(r[3] or 0)))
    return out


def _peer_is_online(last_handshake: Optional[int], disabled: bool) -> bool:
    if not last_handshake or disabled:
        return False
    return last_handshake <= settings.online_threshold_seconds


def _fetch_all_router_peers(router_row: Router, timeout: Optional[float] = None):
    client = make_client(router_row, timeout=timeout) if timeout is not None else make_client(router_row)
    return router_row.id, client.list_all_wireguard_peers()


# Per-router timeout for the dashboard live_status batched call. An unreachable router
# should fail fast so the overall HTTP response doesn't stall (and get clobbered by the
# next poll on the client side).
_DASHBOARD_LIVE_STATUS_ROUTER_TIMEOUT = 4.0


# --- Authentication & Users ---
_LOGIN_FAILURE_LIMIT = 5
_LOGIN_LOCK_MINUTES = 15
_PASSWORD_MIN_LENGTH = 12


def _record_user_security_event(
    db: Session,
    event_type: str,
    *,
    actor_user_id: Optional[int] = None,
    target_user_id: Optional[int] = None,
    detail: Optional[object] = None,
) -> None:
    encoded = ""
    if detail is not None:
        encoded = detail if isinstance(detail, str) else json.dumps(detail, separators=(",", ":"), sort_keys=True)
    db.add(
        UserSecurityEvent(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            event_type=event_type,
            detail=encoded,
        )
    )


def _validate_password_policy(password: str) -> None:
    if len(password) < _PASSWORD_MIN_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Password must be at least {_PASSWORD_MIN_LENGTH} characters long",
        )


def _is_user_locked(user: User, now: Optional[datetime] = None) -> bool:
    if user.locked_until is None:
        return False
    ref = now or datetime.utcnow()
    return user.locked_until > ref


def _revoke_user_sessions(user: User) -> None:
    user.session_version = int(user.session_version or 0) + 1


def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    claims = verify_token(token)
    if claims is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    user = db.get(User, claims["user_id"])
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User inactive",
        )
    if int(user.session_version or 0) != claims["session_version"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    return user


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(creds: LoginRequest, response: Response, db: Session = Depends(get_db)):
    username = creds.username.strip()
    now = datetime.utcnow()
    user = db.query(User).filter(User.username == username).first()
    if not user:
        _record_user_security_event(
            db,
            "login_failure",
            detail={"username": username, "reason": "unknown_username"},
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    if not user.is_active:
        _record_user_security_event(
            db,
            "login_failure",
            target_user_id=user.id,
            detail={"username": username, "reason": "inactive"},
        )
        db.commit()
        raise HTTPException(status_code=403, detail="Account is inactive")

    if _is_user_locked(user, now):
        _record_user_security_event(
            db,
            "login_failure",
            target_user_id=user.id,
            detail={"username": username, "reason": "locked", "locked_until": user.locked_until.isoformat()},
        )
        db.commit()
        raise HTTPException(
            status_code=423,
            detail="Account is temporarily locked. Ask another admin to unlock it or wait 15 minutes.",
        )

    if not verify_password(creds.password, user.hashed_password):
        user.failed_login_attempts = int(user.failed_login_attempts or 0) + 1
        _record_user_security_event(
            db,
            "login_failure",
            target_user_id=user.id,
            detail={"username": username, "reason": "bad_password", "attempts": user.failed_login_attempts},
        )
        if user.failed_login_attempts >= _LOGIN_FAILURE_LIMIT:
            user.locked_until = now + timedelta(minutes=_LOGIN_LOCK_MINUTES)
            _record_user_security_event(
                db,
                "account_lock",
                target_user_id=user.id,
                detail={"username": username, "locked_until": user.locked_until.isoformat()},
            )
            db.commit()
            raise HTTPException(
                status_code=423,
                detail="Account is temporarily locked. Ask another admin to unlock it or wait 15 minutes.",
            )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        user.id,
        int(user.session_version or 1),
        expires_delta=access_token_expires,
    )
    _record_user_security_event(
        db,
        "login_success",
        actor_user_id=user.id,
        target_user_id=user.id,
        detail={"username": username},
    )
    db.commit()
    _set_auth_cookie(response, access_token)
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


class SetupStateDTO(BaseModel):
    needs_initial_setup: bool


@router.get("/auth/setup-state", response_model=SetupStateDTO)
def setup_state(db: Session = Depends(get_db)):
    user_count = int(db.query(func.count(User.id)).scalar() or 0)
    return SetupStateDTO(needs_initial_setup=user_count == 0)


class InitialSetupRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/setup")
def initial_setup(req: InitialSetupRequest, response: Response, db: Session = Depends(get_db)):
    """Create the first admin account during first-run onboarding.

    Only succeeds while the user table is empty; once any user exists this
    becomes a 409 so the endpoint can't be used to seed extra admins.
    """
    existing_count = int(db.query(func.count(User.id)).scalar() or 0)
    if existing_count > 0:
        raise HTTPException(status_code=409, detail="Setup already completed")

    username = (req.username or "").strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    _validate_password_policy(req.password)

    now = datetime.utcnow()
    user = User(
        username=username,
        hashed_password=get_password_hash(req.password),
        is_admin=True,
        is_active=True,
        session_version=1,
        password_changed_at=now,
        must_change_password=False,
        last_login_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token = create_access_token(
        user.id,
        int(user.session_version or 1),
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    _record_user_security_event(
        db,
        "initial_setup",
        actor_user_id=user.id,
        target_user_id=user.id,
        detail={"username": username},
    )
    db.commit()
    _set_auth_cookie(response, access_token)
    return {"ok": True}


class UserDTO(BaseModel):
    id: int
    username: str
    is_admin: bool
    is_active: bool
    last_login_at: Optional[datetime]
    locked_until: Optional[datetime]
    must_change_password: bool
    created_at: datetime


class AuthBootstrapDTO(BaseModel):
    user: UserDTO
    router_count: int
    enabled_router_count: int
    peer_count: int
    selected_peer_count: int
    needs_onboarding: bool
    needs_peer_import: bool


@router.get("/auth/me", response_model=UserDTO)
def read_users_me(current_user: User = Depends(get_current_user)):
    return user_to_dto(current_user)


def user_to_dto(u: User) -> UserDTO:
    return UserDTO(
        id=u.id,
        username=u.username,
        is_admin=u.is_admin,
        is_active=u.is_active,
        last_login_at=u.last_login_at,
        locked_until=u.locked_until,
        must_change_password=u.must_change_password,
        created_at=u.created_at,
    )


@router.get("/auth/bootstrap", response_model=AuthBootstrapDTO)
def auth_bootstrap(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    router_count = int(db.query(func.count(Router.id)).scalar() or 0)
    enabled_router_count = int(
        db.query(func.count(Router.id)).filter(Router.enabled == True).scalar() or 0  # noqa: E712
    )
    peer_count = int(db.query(func.count(Peer.id)).scalar() or 0)
    selected_peer_count = int(
        db.query(func.count(Peer.id)).filter(Peer.selected == True).scalar() or 0  # noqa: E712
    )
    return AuthBootstrapDTO(
        user=user_to_dto(current_user),
        router_count=router_count,
        enabled_router_count=enabled_router_count,
        peer_count=peer_count,
        selected_peer_count=selected_peer_count,
        needs_onboarding=router_count == 0,
        needs_peer_import=router_count > 0 and peer_count == 0,
    )


class CreateUserRequest(BaseModel):
    username: str
    password: str


@router.post("/users", response_model=UserDTO)
def create_user(req: CreateUserRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    username = req.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    _validate_password_policy(req.password)
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")

    now = datetime.utcnow()
    user = User(
        username=username,
        hashed_password=get_password_hash(req.password),
        is_admin=True,
        is_active=True,
        session_version=1,
        password_changed_at=now,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    _record_user_security_event(
        db,
        "user_create",
        actor_user_id=current_user.id,
        target_user_id=user.id,
        detail={"username": user.username},
    )
    db.commit()
    return user_to_dto(user)


@router.get("/users", response_model=List[UserDTO])
def list_users(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    users = db.query(User).order_by(User.username.asc()).all()
    return [user_to_dto(u) for u in users]


class UpdateUserRequest(BaseModel):
    is_active: Optional[bool] = None
    unlock: Optional[bool] = None


@router.patch("/users/{user_id}", response_model=UserDTO)
def update_user(
    user_id: int,
    req: UpdateUserRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    if req.is_active is None and not req.unlock:
        raise HTTPException(status_code=400, detail="No user changes requested")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if req.unlock:
        user.failed_login_attempts = 0
        user.locked_until = None
        _record_user_security_event(
            db,
            "user_unlock",
            actor_user_id=current_user.id,
            target_user_id=user.id,
            detail={"username": user.username},
        )

    if req.is_active is not None and req.is_active != user.is_active:
        if user.id == current_user.id and req.is_active is False:
            raise HTTPException(status_code=400, detail="Cannot deactivate your own account")
        user.is_active = req.is_active
        _revoke_user_sessions(user)
        _record_user_security_event(
            db,
            "user_reactivate" if req.is_active else "user_deactivate",
            actor_user_id=current_user.id,
            target_user_id=user.id,
            detail={"username": user.username},
        )

    db.commit()
    db.refresh(user)
    return user_to_dto(user)


class AdminResetPasswordRequest(BaseModel):
    new_password: str


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    req: AdminResetPasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(current_user)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Use change password for your own account")
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    _validate_password_policy(req.new_password)
    now = datetime.utcnow()
    user.hashed_password = get_password_hash(req.new_password)
    user.password_changed_at = now
    user.must_change_password = True
    user.failed_login_attempts = 0
    user.locked_until = None
    _revoke_user_sessions(user)
    _record_user_security_event(
        db,
        "admin_password_reset",
        actor_user_id=current_user.id,
        target_user_id=user.id,
        detail={"username": user.username},
    )
    db.commit()
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _require_admin(current_user)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.is_active:
        raise HTTPException(status_code=400, detail="Deactivate the account before deleting it")

    _record_user_security_event(
        db,
        "user_delete",
        actor_user_id=current_user.id,
        detail={"username": user.username, "deleted_user_id": user.id},
    )
    db.delete(user)
    db.commit()
    return {"ok": True}


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/change-password")
def change_password(
    req: ChangePasswordRequest,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(req.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    _validate_password_policy(req.new_password)
    if verify_password(req.new_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="New password must be different")

    current_user.hashed_password = get_password_hash(req.new_password)
    current_user.password_changed_at = datetime.utcnow()
    current_user.must_change_password = False
    current_user.failed_login_attempts = 0
    current_user.locked_until = None
    _revoke_user_sessions(current_user)
    _record_user_security_event(
        db,
        "password_change",
        actor_user_id=current_user.id,
        target_user_id=current_user.id,
        detail={"username": current_user.username},
    )
    db.commit()
    response.delete_cookie("access_token")
    return {"ok": True}


# --- Existing Routes (Protected) ---


class SettingsDTO(BaseModel):
    poll_interval_seconds: int
    online_threshold_seconds: int
    monthly_reset_day: int
    timezone: str
    date_calendar: str = "gregorian"
    week_start_day: int
    show_kind_pills: bool
    show_hw_stats: bool
    dashboard_peer_preview_count: int = 6
    peer_default_scope_unit: str
    peer_default_scope_value: int
    dashboard_scope_unit: str
    dashboard_scope_value: int
    dashboard_router_scope: str
    dashboard_selected_router_ids: List[int]
    dashboard_filter_status: str
    dashboard_sort_by: str
    dashboard_time_mode: str = "rolling"
    dashboard_custom_start: str = ""
    dashboard_custom_end: str = ""
    dashboard_time_frame_today: bool = False
    peer_time_mode: str = "rolling"
    peer_custom_start: str = ""
    peer_custom_end: str = ""
    peer_time_frame_today: bool = False
    raw_sample_retention_hours: int
    minute_rollup_retention_days: int
    daily_rollup_retention_days: int
    usage_maintenance_auto_enabled: bool = True
    usage_maintenance_auto_frequency: str = "daily"
    usage_maintenance_auto_interval_days: int = 2
    usage_maintenance_auto_weekday: int = 6
    usage_maintenance_auto_time: str = "03:00"
    usage_maintenance_backup_keep: int = 2


def _normalize_usage_time_mode(value: Optional[str]) -> str:
    value = (value or "rolling").strip().lower()
    return value if value in {"today", "this_month", "all_time", "rolling", "custom"} else "rolling"


class MetricsDTO(BaseModel):
    cpu_percent: Optional[float] = None
    load_1: Optional[float] = None
    load_5: Optional[float] = None
    load_15: Optional[float] = None
    mem_percent: Optional[float] = None
    mem_used: Optional[int] = None
    mem_total: Optional[int] = None




@router.get("/metrics", response_model=MetricsDTO)
def get_metrics():
    """Lightweight system metrics for dashboard display."""
    cpu_percent: Optional[float] = None
    mem_percent: Optional[float] = None
    mem_used: Optional[int] = None
    mem_total: Optional[int] = None
    load_1: Optional[float] = None
    load_5: Optional[float] = None
    load_15: Optional[float] = None

    if psutil:
        try:
            cpu_percent = float(psutil.cpu_percent(interval=0.0))
            vm = psutil.virtual_memory()
            mem_percent = float(vm.percent)
            mem_used = int(vm.used)
            mem_total = int(vm.total)
        except Exception:
            pass

    # Fallback to load average if available
    try:
        l1, l5, l15 = os.getloadavg()
        load_1, load_5, load_15 = float(l1), float(l5), float(l15)
    except (OSError, AttributeError):
        pass

    return MetricsDTO(
        cpu_percent=cpu_percent,
        load_1=load_1,
        load_5=load_5,
        load_15=load_15,
        mem_percent=mem_percent,
        mem_used=mem_used,
        mem_total=mem_total,
    )


@router.get("/settings", response_model=SettingsDTO)
def get_settings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Base values from in-memory settings (already hydrated from DB on startup)
    data: dict = {
        "poll_interval_seconds": settings.poll_interval_seconds,
        "online_threshold_seconds": settings.online_threshold_seconds,
        "monthly_reset_day": settings.monthly_reset_day,
        "timezone": settings.timezone,
        "date_calendar": app_date_calendar(),
        "week_start_day": 0,
        "show_kind_pills": True,
        "show_hw_stats": True,
        "dashboard_peer_preview_count": 6,
        "peer_default_scope_unit": "minutes",
        "peer_default_scope_value": 60,
        "dashboard_scope_unit": "hours",
        "dashboard_scope_value": 24,
        "dashboard_router_scope": "all",
        "dashboard_selected_router_ids": [],
        "dashboard_filter_status": "all",
        "dashboard_sort_by": "created",
        "dashboard_time_mode": "rolling",
        "dashboard_custom_start": "",
        "dashboard_custom_end": "",
        "dashboard_time_frame_today": False,
        "peer_time_mode": "rolling",
        "peer_custom_start": "",
        "peer_custom_end": "",
        "peer_time_frame_today": False,
        "raw_sample_retention_hours": 24,
        "minute_rollup_retention_days": 90,
        "daily_rollup_retention_days": 0,
    }
    # Overlay any values persisted in SettingsKV
    for key in (
        "week_start_day",
        "date_calendar",
        "show_kind_pills",
        "show_hw_stats",
        "dashboard_peer_preview_count",
        "peer_default_scope_unit",
        "peer_default_scope_value",
        "dashboard_scope_unit",
        "dashboard_scope_value",
        "dashboard_router_scope",
        "dashboard_selected_router_ids",
        "dashboard_filter_status",
        "dashboard_sort_by",
        "dashboard_time_mode",
        "dashboard_custom_start",
        "dashboard_custom_end",
        "dashboard_time_frame_today",
        "peer_time_mode",
        "peer_custom_start",
        "peer_custom_end",
        "peer_time_frame_today",
        "raw_sample_retention_hours",
        "minute_rollup_retention_days",
        "daily_rollup_retention_days",
    ):
        kv = db.get(SettingsKV, key)
        if not kv:
            continue
        if key in (
            "week_start_day",
            "dashboard_peer_preview_count",
            "peer_default_scope_value",
            "dashboard_scope_value",
            "raw_sample_retention_hours",
            "minute_rollup_retention_days",
            "daily_rollup_retention_days",
        ):
            try:
                data[key] = int(kv.value)
            except ValueError:
                continue
        elif key == "dashboard_selected_router_ids":
            try:
                parsed = json.loads(kv.value)
                if isinstance(parsed, list):
                    data[key] = [rid for rid in _normalize_router_ids(parsed) or []]
            except Exception:
                data[key] = []
        elif key == "dashboard_router_scope":
            data[key] = _normalize_dashboard_router_scope(kv.value)
        elif key in (
            "show_kind_pills",
            "show_hw_stats",
            "dashboard_time_frame_today",
            "peer_time_frame_today",
        ):
            data[key] = kv.value.lower() == "true"
        elif key == "date_calendar":
            data[key] = normalize_date_calendar(kv.value)
        else:
            data[key] = kv.value
    # Back-compat: if dashboard_scope_days exists but new keys don't, treat it as days.
    if not db.get(SettingsKV, "dashboard_scope_unit") and not db.get(SettingsKV, "dashboard_scope_value"):
        legacy = db.get(SettingsKV, "dashboard_scope_days")
        if legacy:
            try:
                data["dashboard_scope_unit"] = "days"
                data["dashboard_scope_value"] = int(legacy.value)
            except ValueError:
                pass
    if not db.get(SettingsKV, "dashboard_time_mode") and data.get("dashboard_time_frame_today"):
        data["dashboard_time_mode"] = "today"
    if not db.get(SettingsKV, "peer_time_mode") and data.get("peer_time_frame_today"):
        data["peer_time_mode"] = "today"
    data["dashboard_time_mode"] = _normalize_usage_time_mode(data.get("dashboard_time_mode"))
    data["peer_time_mode"] = _normalize_usage_time_mode(data.get("peer_time_mode"))
    data["raw_sample_retention_hours"] = max(1, min(24 * 365, int(data["raw_sample_retention_hours"])))
    data["minute_rollup_retention_days"] = max(1, min(3650, int(data["minute_rollup_retention_days"])))
    data["daily_rollup_retention_days"] = max(0, min(36500, int(data["daily_rollup_retention_days"])))
    data.update(load_auto_maintenance_settings(db))
    data["monthly_reset_day"] = max(1, min(31, int(data["monthly_reset_day"])))
    data["dashboard_peer_preview_count"] = max(1, min(50, int(data.get("dashboard_peer_preview_count", 6))))
    data["date_calendar"] = normalize_date_calendar(data.get("date_calendar"))
    return SettingsDTO(**data)


@router.put("/settings", response_model=SettingsDTO)
def update_settings(dto: SettingsDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if is_usage_maintenance_running(db):
        current = get_settings(db, current_user)
        if (
            int(dto.raw_sample_retention_hours) != int(current.raw_sample_retention_hours)
            or int(dto.minute_rollup_retention_days) != int(current.minute_rollup_retention_days)
            or int(dto.daily_rollup_retention_days) != int(current.daily_rollup_retention_days)
        ):
            raise HTTPException(status_code=409, detail="Retention settings are locked while usage maintenance is running")
    # Persist overrides to SettingsKV (both core and UI prefs)
    overrides = {
        # Core settings
        "poll_interval_seconds": str(dto.poll_interval_seconds),
        "online_threshold_seconds": str(dto.online_threshold_seconds),
        "monthly_reset_day": str(max(1, min(31, int(dto.monthly_reset_day)))),
        "timezone": dto.timezone,
        "date_calendar": normalize_date_calendar(dto.date_calendar),
        "week_start_day": str(max(0, min(6, int(dto.week_start_day)))),
        # UI preferences
        "show_kind_pills": str(dto.show_kind_pills).lower(),
        "show_hw_stats": str(dto.show_hw_stats).lower(),
        "dashboard_peer_preview_count": str(max(1, min(50, int(dto.dashboard_peer_preview_count)))),
        "peer_default_scope_unit": dto.peer_default_scope_unit,
        "peer_default_scope_value": str(dto.peer_default_scope_value),
        "dashboard_scope_unit": dto.dashboard_scope_unit,
        "dashboard_scope_value": str(dto.dashboard_scope_value),
        "dashboard_router_scope": _normalize_dashboard_router_scope(dto.dashboard_router_scope),
        "dashboard_selected_router_ids": json.dumps(_normalize_router_ids(dto.dashboard_selected_router_ids) or []),
        "dashboard_filter_status": dto.dashboard_filter_status,
        "dashboard_sort_by": dto.dashboard_sort_by,
        "dashboard_time_mode": _normalize_usage_time_mode(dto.dashboard_time_mode),
        "dashboard_custom_start": dto.dashboard_custom_start,
        "dashboard_custom_end": dto.dashboard_custom_end,
        "dashboard_time_frame_today": str(dto.dashboard_time_frame_today).lower(),
        "peer_time_mode": _normalize_usage_time_mode(dto.peer_time_mode),
        "peer_custom_start": dto.peer_custom_start,
        "peer_custom_end": dto.peer_custom_end,
        "peer_time_frame_today": str(dto.peer_time_frame_today).lower(),
        "raw_sample_retention_hours": str(max(1, min(24 * 365, int(dto.raw_sample_retention_hours)))),
        "minute_rollup_retention_days": str(max(1, min(3650, int(dto.minute_rollup_retention_days)))),
        "daily_rollup_retention_days": str(max(0, min(36500, int(dto.daily_rollup_retention_days)))),
    }
    auto_schedule = normalize_auto_maintenance_settings(
        {
            "usage_maintenance_auto_enabled": dto.usage_maintenance_auto_enabled,
            "usage_maintenance_auto_frequency": dto.usage_maintenance_auto_frequency,
            "usage_maintenance_auto_interval_days": dto.usage_maintenance_auto_interval_days,
            "usage_maintenance_auto_weekday": dto.usage_maintenance_auto_weekday,
            "usage_maintenance_auto_time": dto.usage_maintenance_auto_time,
            "usage_maintenance_backup_keep": dto.usage_maintenance_backup_keep,
        }
    )
    overrides.update(
        {
            "usage_maintenance_auto_enabled": "1" if auto_schedule["usage_maintenance_auto_enabled"] else "0",
            "usage_maintenance_auto_frequency": auto_schedule["usage_maintenance_auto_frequency"],
            "usage_maintenance_auto_interval_days": str(auto_schedule["usage_maintenance_auto_interval_days"]),
            "usage_maintenance_auto_weekday": str(auto_schedule["usage_maintenance_auto_weekday"]),
            "usage_maintenance_auto_time": auto_schedule["usage_maintenance_auto_time"],
            "usage_maintenance_backup_keep": str(auto_schedule["usage_maintenance_backup_keep"]),
        }
    )

    for k, v in overrides.items():
        kv = db.get(SettingsKV, k)
        if not kv:
            kv = SettingsKV(key=k, value=v)
            db.add(kv)
        else:
            kv.value = v

    for retired_key in ("dashboard_refresh_seconds", "peer_refresh_seconds", "active_router_id"):
        retired = db.get(SettingsKV, retired_key)
        if retired:
            db.delete(retired)
    
    # Update runtime config for core logic (immediate effect without restart)
    settings.poll_interval_seconds = dto.poll_interval_seconds
    settings.online_threshold_seconds = dto.online_threshold_seconds
    settings.monthly_reset_day = max(1, min(31, int(dto.monthly_reset_day)))
    settings.timezone = dto.timezone
    settings.date_calendar = normalize_date_calendar(dto.date_calendar)
    
    # Hot-reload scheduler interval
    try:
        update_scheduler_interval(dto.poll_interval_seconds)
    except Exception:
        pass

    db.commit()

    # Hot-reload the auto-maintenance job (time/timezone may have changed)
    try:
        reschedule_usage_maintenance_job()
    except Exception:
        pass

    return get_settings(db)


class RouterCreateDTO(BaseModel):
    name: str
    host: str
    proto: str  # rest | rest-http | api | api-plain
    port: int
    username: str
    password: str
    tls_verify: bool = True
    enabled: bool = True


class RouterUpdateDTO(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    proto: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    tls_verify: Optional[bool] = None
    enabled: Optional[bool] = None


class RouterDTO(BaseModel):
    id: int
    name: str
    host: str
    proto: str
    port: int
    username: str
    tls_verify: bool
    enabled: bool = True
    ros_version: str = ""
    ros_version_checked_at: Optional[datetime] = None
    ros_supported: bool = False

    class Config:
        from_attributes = True
class WGInterfaceDTO(BaseModel):
    name: str
    public_key: str
    listen_port: int
    public_host: str
    addresses: List[str] = Field(default_factory=list)


class RouterDeleteImpactDTO(BaseModel):
    router_id: int
    router_name: str
    dashboard_selected: bool = False
    peer_count: int = 0
    selected_peer_count: int = 0
    usage_sample_rows: int = 0
    usage_minute_rows: int = 0
    usage_daily_rows: int = 0
    usage_monthly_rows: int = 0
    quota_count: int = 0
    action_count: int = 0
    telegram_binding_count: int = 0
    telegram_log_count: int = 0
    signup_token_count: int = 0
    fair_usage_assignment_count: int = 0
    fair_usage_state_count: int = 0
    router_rule_count: int = 0
    merge_ledger_count: int = 0
    peer_setting_count: int = 0


class RouterDeleteResultDTO(RouterDeleteImpactDTO):
    signup_tokens_updated: int = 0
    signup_tokens_deleted: int = 0
    backup_path: Optional[str] = None
    post_delete_quick_check: str = ""


_PEER_SCOPED_SETTINGS_PREFIXES = (
    "peer_private_key",
    "peer_preshared_key",
    "peer_export_config_name",
    "peer_export_endpoint",
    "quota_valid_from",
    "quota_valid_until",
)


def _peer_scoped_setting_keys(peer_ids: list[int]) -> list[str]:
    keys: list[str] = []
    for peer_id in peer_ids:
        for prefix in _PEER_SCOPED_SETTINGS_PREFIXES:
            keys.append(f"{prefix}:{peer_id}")
    return keys


def _normalized_dashboard_selected_router_ids(db: Session) -> list[int]:
    kv = db.get(SettingsKV, "dashboard_selected_router_ids")
    if not kv or not (kv.value or "").strip():
        return []
    try:
        raw = json.loads(kv.value)
    except Exception:
        return []
    return _normalize_router_ids(raw) or []


def _router_delete_backup_path(db_path: str, router_id: int, router_name: str) -> str:
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_name = "".join(ch.lower() if ch.isalnum() else "-" for ch in router_name).strip("-") or "router"
    return os.path.join(backup_dir, f"router-delete-{router_id}-{safe_name}-{stamp}.db")


def _backup_sqlite_database_for_router_delete(router_id: int, router_name: str) -> Optional[str]:
    db_path = sqlite_database_path()
    if not db_path or not os.path.exists(db_path):
        return None

    backup_path = _router_delete_backup_path(db_path, router_id, router_name)
    source = sqlite3.connect(f"file:{os.path.abspath(db_path)}?mode=ro", uri=True, timeout=30)
    target = sqlite3.connect(backup_path, timeout=30)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    return backup_path


def _assert_sqlite_quick_check(db: Session, phase: str) -> str:
    if not settings.database_url.startswith("sqlite:"):
        return "skipped"
    try:
        rows = [str(row[0]) for row in db.execute(text("PRAGMA quick_check")).fetchall()]
    except Exception as exc:
        raise HTTPException(status_code=409, detail=f"database quick_check failed {phase}: {exc}") from exc
    if rows == ["ok"]:
        return "ok"
    detail = "; ".join(rows[:5]) if rows else "unknown"
    if len(rows) > 5:
        detail += f"; ... ({len(rows)} messages)"
    raise HTTPException(status_code=409, detail=f"database quick_check failed {phase}: {detail}")


def _count_signup_tokens_referencing_peers(db: Session, peer_id_set: set[int]) -> int:
    if not peer_id_set:
        return 0
    count = 0
    for token in db.query(TelegramSignupToken).all():
        try:
            token_peer_ids = {
                int(pid)
                for pid in json.loads(token.peer_ids or "[]")
                if int(pid) > 0
            }
        except Exception:
            continue
        if token_peer_ids & peer_id_set:
            count += 1
    return count


def _router_delete_residue_counts(
    db: Session,
    *,
    router_id: int,
    peer_ids: list[int],
    rule_ids: list[int],
) -> dict[str, int]:
    peer_id_set = set(peer_ids)
    counts: dict[str, int] = {
        "routers": db.query(Router).filter(Router.id == router_id).count(),
        "peers": db.query(Peer).filter(Peer.router_id == router_id).count(),
        "router_rules": db.query(FairUsageRule).filter(FairUsageRule.router_id == router_id).count(),
    }
    if peer_ids:
        peer_setting_keys = _peer_scoped_setting_keys(peer_ids)
        counts.update(
            {
                "usage_samples": db.query(UsageSample).filter(UsageSample.peer_id.in_(peer_ids)).count(),
                "usage_minute": db.query(UsageMinute).filter(UsageMinute.peer_id.in_(peer_ids)).count(),
                "usage_daily": db.query(UsageDaily).filter(UsageDaily.peer_id.in_(peer_ids)).count(),
                "usage_monthly": db.query(UsageMonthly).filter(UsageMonthly.peer_id.in_(peer_ids)).count(),
                "quotas": db.query(Quota).filter(Quota.peer_id.in_(peer_ids)).count(),
                "actions": db.query(Action).filter(Action.peer_id.in_(peer_ids)).count(),
                "telegram_bindings": db.query(TelegramPeerBinding).filter(TelegramPeerBinding.peer_id.in_(peer_ids)).count(),
                "telegram_logs": db.query(TelegramNotificationLog).filter(TelegramNotificationLog.peer_id.in_(peer_ids)).count(),
                "peer_settings": db.query(SettingsKV).filter(SettingsKV.key.in_(peer_setting_keys)).count() if peer_setting_keys else 0,
                "signup_tokens": _count_signup_tokens_referencing_peers(db, peer_id_set),
            }
        )
    if peer_ids or rule_ids:
        assignment_clauses = []
        state_clauses = []
        if peer_ids:
            assignment_clauses.append(FairUsageAssignment.peer_id.in_(peer_ids))
            state_clauses.append(FairUsageState.peer_id.in_(peer_ids))
        if rule_ids:
            assignment_clauses.append(FairUsageAssignment.rule_id.in_(rule_ids))
            state_clauses.append(FairUsageState.rule_id.in_(rule_ids))
        counts["fair_usage_assignments"] = db.query(FairUsageAssignment).filter(or_(*assignment_clauses)).count()
        counts["fair_usage_state"] = db.query(FairUsageState).filter(or_(*state_clauses)).count()
    if rule_ids:
        counts["fair_usage_tiers"] = db.query(FairUsageTier).filter(FairUsageTier.rule_id.in_(rule_ids)).count()
        counts["fair_usage_rules"] = db.query(FairUsageRule).filter(FairUsageRule.id.in_(rule_ids)).count()

    merge_clauses = [PeerTotalsMerge.source_router_id == router_id, PeerTotalsMerge.target_router_id == router_id]
    if peer_ids:
        merge_clauses.extend([
            PeerTotalsMerge.source_peer_id.in_(peer_ids),
            PeerTotalsMerge.target_peer_id.in_(peer_ids),
        ])
    counts["merge_ledger"] = db.query(PeerTotalsMerge).filter(or_(*merge_clauses)).count()
    return counts


def _assert_router_delete_has_no_residue(db: Session, router_id: int, peer_ids: list[int], rule_ids: list[int]) -> None:
    residues = {
        name: count
        for name, count in _router_delete_residue_counts(
            db,
            router_id=router_id,
            peer_ids=peer_ids,
            rule_ids=rule_ids,
        ).items()
        if count
    }
    if residues:
        detail = ", ".join(f"{name}={count}" for name, count in sorted(residues.items()))
        raise HTTPException(status_code=500, detail=f"router delete incomplete; rolled back: {detail}")


def _router_delete_impact(
    db: Session,
    row: Router,
) -> tuple[RouterDeleteImpactDTO, list[int], list[int]]:
    peer_ids = [int(pid) for (pid,) in db.query(Peer.id).filter(Peer.router_id == row.id).all()]
    rule_ids = [int(rid) for (rid,) in db.query(FairUsageRule.id).filter(FairUsageRule.router_id == row.id).all()]
    dashboard_selected_router_ids = _normalized_dashboard_selected_router_ids(db)
    peer_setting_keys = _peer_scoped_setting_keys(peer_ids)
    peer_id_set = set(peer_ids)

    signup_token_count = 0
    if peer_id_set:
        for token in db.query(TelegramSignupToken).all():
            try:
                token_peer_ids = {
                    int(pid)
                    for pid in json.loads(token.peer_ids or "[]")
                    if int(pid) > 0
                }
            except Exception:
                continue
            if token_peer_ids & peer_id_set:
                signup_token_count += 1

    rule_or_peer_clauses = []
    if peer_ids:
        rule_or_peer_clauses.append(FairUsageAssignment.peer_id.in_(peer_ids))
    if rule_ids:
        rule_or_peer_clauses.append(FairUsageAssignment.rule_id.in_(rule_ids))

    state_clauses = []
    if peer_ids:
        state_clauses.append(FairUsageState.peer_id.in_(peer_ids))
    if rule_ids:
        state_clauses.append(FairUsageState.rule_id.in_(rule_ids))

    merge_clauses = [PeerTotalsMerge.source_router_id == row.id, PeerTotalsMerge.target_router_id == row.id]
    if peer_ids:
        merge_clauses.extend([
            PeerTotalsMerge.source_peer_id.in_(peer_ids),
            PeerTotalsMerge.target_peer_id.in_(peer_ids),
        ])

    impact = RouterDeleteImpactDTO(
        router_id=row.id,
        router_name=row.name,
        dashboard_selected=row.id in dashboard_selected_router_ids,
        peer_count=len(peer_ids),
        selected_peer_count=db.query(Peer).filter(Peer.router_id == row.id, Peer.selected == True).count(),
        usage_sample_rows=db.query(UsageSample).filter(UsageSample.peer_id.in_(peer_ids)).count() if peer_ids else 0,
        usage_minute_rows=db.query(UsageMinute).filter(UsageMinute.peer_id.in_(peer_ids)).count() if peer_ids else 0,
        usage_daily_rows=db.query(UsageDaily).filter(UsageDaily.peer_id.in_(peer_ids)).count() if peer_ids else 0,
        usage_monthly_rows=db.query(UsageMonthly).filter(UsageMonthly.peer_id.in_(peer_ids)).count() if peer_ids else 0,
        quota_count=db.query(Quota).filter(Quota.peer_id.in_(peer_ids)).count() if peer_ids else 0,
        action_count=db.query(Action).filter(Action.peer_id.in_(peer_ids)).count() if peer_ids else 0,
        telegram_binding_count=db.query(TelegramPeerBinding).filter(TelegramPeerBinding.peer_id.in_(peer_ids)).count() if peer_ids else 0,
        telegram_log_count=db.query(TelegramNotificationLog).filter(TelegramNotificationLog.peer_id.in_(peer_ids)).count() if peer_ids else 0,
        signup_token_count=signup_token_count,
        fair_usage_assignment_count=db.query(FairUsageAssignment).filter(or_(*rule_or_peer_clauses)).count() if rule_or_peer_clauses else 0,
        fair_usage_state_count=db.query(FairUsageState).filter(or_(*state_clauses)).count() if state_clauses else 0,
        router_rule_count=len(rule_ids),
        merge_ledger_count=db.query(PeerTotalsMerge).filter(or_(*merge_clauses)).count(),
        peer_setting_count=db.query(SettingsKV).filter(SettingsKV.key.in_(peer_setting_keys)).count() if peer_setting_keys else 0,
    )
    return impact, peer_ids, rule_ids


def _refresh_router_version_or_raise(router_row: Router) -> None:
    try:
        client = make_client(router_row)
        version = (client.get_system_version() or "").strip()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"router connection failed: {exc}") from exc

    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    router_row.ros_version = version
    router_row.ros_version_checked_at = now_utc
    router_row.ros_supported = is_routeros_supported(version)
    try:
        assert_routeros_supported(version)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/routers", response_model=RouterDTO)
def create_router(dto: RouterCreateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if dto.proto not in ("rest", "api", "rest-http", "api-plain"):
        raise HTTPException(status_code=400, detail="proto must be one of 'rest', 'rest-http', 'api', 'api-plain'")
    box = SecretBox(settings.secret_key)
    r = Router(
        name=dto.name,
        host=dto.host,
        proto=dto.proto,
        port=dto.port,
        username=dto.username,
        secret_enc=box.encrypt(dto.password),
        tls_verify=dto.tls_verify,
        enabled=dto.enabled,
    )
    _refresh_router_version_or_raise(r)
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@router.get("/routers/{router_id}", response_model=RouterDTO)
def get_router(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    return row


@router.put("/routers/{router_id}", response_model=RouterDTO)
def update_router(router_id: int, dto: RouterUpdateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    if dto.proto is not None:
        if dto.proto not in ("rest", "rest-http", "api", "api-plain"):
            raise HTTPException(status_code=400, detail="invalid proto")
        row.proto = dto.proto
    if dto.name is not None:
        row.name = dto.name
    if dto.host is not None:
        row.host = dto.host
    if dto.port is not None:
        row.port = dto.port
    if dto.username is not None:
        row.username = dto.username
    if dto.password:
        box = SecretBox(settings.secret_key)
        row.secret_enc = box.encrypt(dto.password)
    if dto.tls_verify is not None:
        row.tls_verify = dto.tls_verify
    if dto.enabled is not None:
        row.enabled = bool(dto.enabled)
    _refresh_router_version_or_raise(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/routers/{router_id}/delete-impact", response_model=RouterDeleteImpactDTO)
def get_router_delete_impact(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    impact, _, _ = _router_delete_impact(db, row)
    return impact


@router.delete("/routers/{router_id}", response_model=RouterDeleteResultDTO)
def delete_router(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if is_usage_maintenance_running(db):
        raise HTTPException(
            status_code=409,
            detail="Usage maintenance is running. Wait for it to finish before deleting a router.",
        )

    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    try:
        with exclusive_operation_gate.begin(
            key="router-delete",
            label=f"Deleting router {row.name}",
            detail=f"Deleting router {row.name}. Dashboard polling is temporarily paused; retry shortly.",
        ):
            set_polling_paused(True)
            backup_path: Optional[str] = None
            try:
                router_name = row.name
                # Release any read transaction opened while loading the router
                # before taking a SQLite backup from a separate connection.
                db.rollback()
                backup_path = _backup_sqlite_database_for_router_delete(router_id, router_name)
                _assert_sqlite_quick_check(db, "before router delete")

                row = db.get(Router, router_id)
                if not row:
                    raise HTTPException(status_code=404, detail="router not found")
                impact, peer_ids, rule_ids = _router_delete_impact(db, row)
                peer_id_set = set(peer_ids)

                # End the read transaction before taking the SQLite write lock explicitly.
                db.rollback()
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")

                row = db.get(Router, router_id)
                if not row:
                    raise HTTPException(status_code=404, detail="router not found")

                # Drop the retired active-router preference if it still exists.
                kv = db.get(SettingsKV, "active_router_id")
                if kv:
                    db.delete(kv)

                # Remove deleted router from dashboard-selected router preferences.
                dashboard_selected_kv = db.get(SettingsKV, "dashboard_selected_router_ids")
                if dashboard_selected_kv:
                    router_ids = [rid for rid in _normalized_dashboard_selected_router_ids(db) if rid != router_id]
                    dashboard_selected_kv.value = json.dumps(router_ids)

                signup_tokens_updated = 0
                signup_tokens_deleted = 0
                if peer_id_set:
                    for token in db.query(TelegramSignupToken).all():
                        try:
                            token_peer_ids = [int(pid) for pid in json.loads(token.peer_ids or "[]") if int(pid) > 0]
                        except Exception:
                            continue
                        filtered_peer_ids = [pid for pid in token_peer_ids if pid not in peer_id_set]
                        if filtered_peer_ids == token_peer_ids:
                            continue
                        if filtered_peer_ids:
                            token.peer_ids = json.dumps(filtered_peer_ids)
                            signup_tokens_updated += 1
                        else:
                            db.delete(token)
                            signup_tokens_deleted += 1

                if peer_ids:
                    db.query(TelegramPeerBinding).filter(TelegramPeerBinding.peer_id.in_(peer_ids)).delete(synchronize_session=False)
                    db.query(TelegramNotificationLog).filter(TelegramNotificationLog.peer_id.in_(peer_ids)).delete(synchronize_session=False)
                    db.query(Quota).filter(Quota.peer_id.in_(peer_ids)).delete(synchronize_session=False)
                    db.query(UsageSample).filter(UsageSample.peer_id.in_(peer_ids)).delete(synchronize_session=False)
                    db.query(UsageMinute).filter(UsageMinute.peer_id.in_(peer_ids)).delete(synchronize_session=False)
                    db.query(UsageDaily).filter(UsageDaily.peer_id.in_(peer_ids)).delete(synchronize_session=False)
                    db.query(UsageMonthly).filter(UsageMonthly.peer_id.in_(peer_ids)).delete(synchronize_session=False)
                    db.query(Action).filter(Action.peer_id.in_(peer_ids)).delete(synchronize_session=False)

                    peer_setting_keys = _peer_scoped_setting_keys(peer_ids)
                    if peer_setting_keys:
                        db.query(SettingsKV).filter(SettingsKV.key.in_(peer_setting_keys)).delete(synchronize_session=False)

                assignment_clauses = []
                if peer_ids:
                    assignment_clauses.append(FairUsageAssignment.peer_id.in_(peer_ids))
                if rule_ids:
                    assignment_clauses.append(FairUsageAssignment.rule_id.in_(rule_ids))
                if assignment_clauses:
                    db.query(FairUsageAssignment).filter(or_(*assignment_clauses)).delete(synchronize_session=False)

                state_clauses = []
                if peer_ids:
                    state_clauses.append(FairUsageState.peer_id.in_(peer_ids))
                if rule_ids:
                    state_clauses.append(FairUsageState.rule_id.in_(rule_ids))
                if state_clauses:
                    db.query(FairUsageState).filter(or_(*state_clauses)).delete(synchronize_session=False)

                if rule_ids:
                    db.query(FairUsageTier).filter(FairUsageTier.rule_id.in_(rule_ids)).delete(synchronize_session=False)
                    db.query(FairUsageRule).filter(FairUsageRule.id.in_(rule_ids)).delete(synchronize_session=False)

                merge_clauses = [PeerTotalsMerge.source_router_id == router_id, PeerTotalsMerge.target_router_id == router_id]
                if peer_ids:
                    merge_clauses.extend([
                        PeerTotalsMerge.source_peer_id.in_(peer_ids),
                        PeerTotalsMerge.target_peer_id.in_(peer_ids),
                    ])
                db.query(PeerTotalsMerge).filter(or_(*merge_clauses)).delete(synchronize_session=False)

                if peer_ids:
                    db.query(Peer).filter(Peer.id.in_(peer_ids)).delete(synchronize_session=False)

                db.delete(row)
                db.flush()
                _assert_router_delete_has_no_residue(db, router_id, peer_ids, rule_ids)
                post_delete_quick_check = _assert_sqlite_quick_check(db, "after router delete")
                db.commit()
                return RouterDeleteResultDTO(
                    **impact.model_dump(),
                    signup_tokens_updated=signup_tokens_updated,
                    signup_tokens_deleted=signup_tokens_deleted,
                    backup_path=backup_path,
                    post_delete_quick_check=post_delete_quick_check,
                )
            except Exception:
                db.rollback()
                raise
            finally:
                set_polling_paused(False)
    except ExclusiveOperationInProgress as exc:
        raise HTTPException(status_code=409, detail=exc.user_message)


@router.get("/routers", response_model=list[RouterDTO])
def list_routers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Router).all()


@router.get("/routers/{router_id}/interfaces", response_model=List[str])
def list_interfaces(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        return client.list_wireguard_interfaces()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")


@router.get("/routers/{router_id}/interfaces/{iface}", response_model=WGInterfaceDTO)
def get_interface(router_id: int, iface: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        cfg = client.get_wireguard_interface(iface)
        primary_host = ""
        try:
            primary_host = client.get_primary_ipv4()
        except Exception:
            primary_host = ""
        host = primary_host or router.host
        return WGInterfaceDTO(
            name=cfg.name,
            public_key=cfg.public_key,
            listen_port=cfg.listen_port,
            public_host=host,
            addresses=cfg.addresses or [],
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="interface not found")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")


class PeerDTO(BaseModel):
    id: Optional[int] = None
    interface: str
    name: str
    public_key: str
    allowed_address: str
    disabled: bool
    endpoint: str
    last_handshake: Optional[int] = None
    online: bool


@router.get("/routers/{router_id}/peers", response_model=List[PeerDTO])
def list_peers(router_id: int, interface: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        rows = client.list_wireguard_peers(interface)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")
    existing_by_pub = {
        row.public_key: row.id
        for row in db.query(Peer.id, Peer.public_key).filter(
            Peer.router_id == router_id,
            Peer.interface == interface,
        ).all()
    }
    out: List[PeerDTO] = []
    for p in rows:
        online = _peer_is_online(p.last_handshake, p.disabled)
        out.append(PeerDTO(
            id=existing_by_pub.get(p.public_key),
            interface=p.interface,
            name=p.name,
            public_key=p.public_key,
            allowed_address=p.allowed_address,
            disabled=p.disabled,
            endpoint=p.endpoint,
            last_handshake=p.last_handshake,
            online=online,
        ))
    return out


class PeerImportItem(BaseModel):
    interface: str
    public_key: str
    selected: bool = True


class PeerListDTO(BaseModel):
    id: int
    router_id: int
    interface: str
    name: str
    public_key: str
    allowed_address: str
    disabled: bool
    selected: bool
    router_sync_status: str = "synced"
    router_sync_first_seen_at: Optional[datetime] = None
    router_sync_last_seen_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DashboardLiveStatusDTO(BaseModel):
    peer_id: int
    online: bool
    raw_last_handshake: int


@router.get("/dashboard/live_status", response_model=List[DashboardLiveStatusDTO])
def get_dashboard_live_status(
    router_id: Optional[int] = None,
    router_ids: Optional[List[int]] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    router_id, router_ids = _resolve_router_filter(router_id, router_ids)
    rows = _apply_router_filter(
        db.query(
            Peer.id.label("peer_id"),
            Peer.router_id.label("router_id"),
            Peer.interface.label("interface"),
            Peer.public_key.label("public_key"),
        ).filter(Peer.selected == True),
        router_id,
        router_ids,
    ).all()
    if not rows:
        return []

    peer_lookup: dict[int, dict[tuple[str, str], int]] = {}
    for row in rows:
        peer_lookup.setdefault(int(row.router_id), {})[(row.interface, row.public_key)] = int(row.peer_id)

    routers = (
        db.query(Router)
        .filter(
            Router.id.in_(list(peer_lookup.keys())),
            Router.enabled == True,  # noqa: E712
            Router.ros_supported == True,  # noqa: E712
        )
        .all()
    )
    if not routers:
        return []

    # ORM instances are not safe to use from worker threads; detach before ThreadPoolExecutor.
    for r in routers:
        db.expunge(r)

    out: list[DashboardLiveStatusDTO] = []
    max_workers = min(max(len(routers), 1), 4)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(
                _fetch_all_router_peers,
                router_row,
                _DASHBOARD_LIVE_STATUS_ROUTER_TIMEOUT,
            ): router_row.id
            for router_row in routers
        }
        for future in as_completed(future_map):
            router_row_id = future_map[future]
            try:
                _, live_rows = future.result()
            except Exception:
                continue
            lookup = peer_lookup.get(router_row_id, {})
            for live_peer in live_rows:
                peer_id = lookup.get((live_peer.interface, live_peer.public_key))
                if peer_id is None:
                    continue
                out.append(
                    DashboardLiveStatusDTO(
                        peer_id=peer_id,
                        online=_peer_is_online(live_peer.last_handshake, live_peer.disabled),
                        raw_last_handshake=int(live_peer.last_handshake or 0),
                    )
                )
    return out


@router.post("/routers/{router_id}/peers/import")
def import_peers(router_id: int, items: list[PeerImportItem], db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    # Build a map from public_key to live peer to pull fields
    interfaces = set(i.interface for i in items)
    live: dict[tuple[str, str], object] = {}
    for iface in interfaces:
        for p in client.list_wireguard_peers(iface):
            live[(iface, p.public_key)] = p
    imported = 0
    for it in items:
        key = (it.interface, it.public_key)
        row = live.get(key)
        if not row:
            continue
        exists = db.query(Peer).filter(
            Peer.router_id == router_id,
            Peer.interface == it.interface,
            Peer.public_key == it.public_key,
        ).first()
        if exists:
            # Keep DB row synced to RouterOS when re-importing.
            exists.selected = it.selected
            exists.ros_id = row.ros_id or exists.ros_id
            exists.name = row.name or ""
            exists.allowed_address = row.allowed_address
            disabled = row.disabled
            if isinstance(disabled, str):
                disabled = disabled.strip().lower() in ("1", "true", "yes", "on", "enabled")
            exists.disabled = bool(disabled)
            continue
        disabled = row.disabled
        if isinstance(disabled, str):
            disabled = disabled.strip().lower() in ("1", "true", "yes", "on", "enabled")
        db.add(Peer(
            router_id=router_id,
            interface=row.interface,
            ros_id=row.ros_id,
            name=row.name or "",
            public_key=row.public_key,
            allowed_address=row.allowed_address,
            disabled=bool(disabled),
            selected=it.selected,
        ))
        imported += 1
    db.commit()
    return {"imported": imported}


class PeerCreateRouterDTO(BaseModel):
    interface: str
    name: str
    public_key: str
    allowed_address: str
    private_key: Optional[str] = None
    preshared_key: Optional[str] = None
    config_name: Optional[str] = None
    custom_endpoint: Optional[str] = None


def _is_valid_wg_private_key_b64(s: str) -> bool:
    try:
        raw = base64.b64decode(s.encode("utf-8"), validate=True)
        return len(raw) == 32
    except Exception:
        return False


def _generate_wg_keypair_b64() -> tuple[str, str]:
    private_key = x25519.X25519PrivateKey.generate()
    private_raw = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (
        base64.b64encode(private_raw).decode("utf-8"),
        base64.b64encode(public_raw).decode("utf-8"),
    )


def _store_peer_private_key(db: Session, peer_id: int, private_key: str) -> None:
    pk = (private_key or "").strip()
    if not _is_valid_wg_private_key_b64(pk):
        raise HTTPException(status_code=400, detail="invalid private_key (must be base64 32 bytes)")
    box = SecretBox(settings.secret_key)
    token = box.encrypt(pk)
    kv = db.get(SettingsKV, f"peer_private_key:{peer_id}") or SettingsKV(key=f"peer_private_key:{peer_id}", value="")
    kv.value = token
    db.add(kv)


def _store_peer_preshared_key(db: Session, peer_id: int, preshared_key: str) -> None:
    pk = (preshared_key or "").strip()
    if not _is_valid_wg_private_key_b64(pk):
        raise HTTPException(status_code=400, detail="invalid preshared_key (must be base64 32 bytes)")
    box = SecretBox(settings.secret_key)
    token = box.encrypt(pk)
    kv = db.get(SettingsKV, f"peer_preshared_key:{peer_id}") or SettingsKV(key=f"peer_preshared_key:{peer_id}", value="")
    kv.value = token
    db.add(kv)


def _get_peer_preshared_key_decrypted(db: Session, peer_id: int) -> Optional[str]:
    kv = db.get(SettingsKV, f"peer_preshared_key:{peer_id}")
    if kv and (kv.value or "").strip():
        box = SecretBox(settings.secret_key)
        dec = box.decrypt(kv.value)
        if dec:
            return dec
    return None


class PeerClientExportPrefsOutDTO(BaseModel):
    config_name: str = ""
    custom_endpoint: str = ""
    preshared_key: Optional[str] = None


class PeerClientExportPrefsPatchDTO(BaseModel):
    config_name: Optional[str] = None
    custom_endpoint: Optional[str] = None
    preshared_key: Optional[str] = None


def _build_client_export_prefs_out(db: Session, row: Peer) -> PeerClientExportPrefsOutDTO:
    kv_name = db.get(SettingsKV, f"peer_export_config_name:{row.id}")
    kv_ep = db.get(SettingsKV, f"peer_export_endpoint:{row.id}")
    name = (kv_name.value or "").strip() if kv_name and kv_name.value else ""
    ep = (kv_ep.value or "").strip() if kv_ep and kv_ep.value else ""
    psk = _get_peer_preshared_key_decrypted(db, row.id)
    if psk is None and row.router_id and row.ros_id:
        router = db.get(Router, row.router_id)
        if router:
            try:
                client = make_client(router)
                pk = client.get_wireguard_peer_preshared_key(row.interface, row.ros_id)
                if pk:
                    box = SecretBox(settings.secret_key)
                    token = box.encrypt(pk)
                    kv2 = db.get(SettingsKV, f"peer_preshared_key:{row.id}") or SettingsKV(
                        key=f"peer_preshared_key:{row.id}", value=""
                    )
                    kv2.value = token
                    db.add(kv2)
                    db.commit()
                    psk = pk
            except Exception:
                pass
    return PeerClientExportPrefsOutDTO(config_name=name, custom_endpoint=ep, preshared_key=psk)


@router.get("/peers/{peer_id}/client_export_prefs", response_model=PeerClientExportPrefsOutDTO)
def get_peer_client_export_prefs(peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    return _build_client_export_prefs_out(db, row)


@router.patch("/peers/{peer_id}/client_export_prefs", response_model=PeerClientExportPrefsOutDTO)
def patch_peer_client_export_prefs(
    peer_id: int,
    dto: PeerClientExportPrefsPatchDTO,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    router = db.get(Router, row.router_id) if row.router_id else None
    client = make_client(router) if router and row.ros_id else None

    if dto.config_name is not None:
        key = f"peer_export_config_name:{peer_id}"
        if dto.config_name.strip() == "":
            kv = db.get(SettingsKV, key)
            if kv:
                db.delete(kv)
        else:
            kv = db.get(SettingsKV, key) or SettingsKV(key=key, value="")
            kv.value = dto.config_name.strip()
            db.add(kv)

    if dto.custom_endpoint is not None:
        ep_st = dto.custom_endpoint.strip()
        key_ep = f"peer_export_endpoint:{peer_id}"
        if ep_st == "":
            kv = db.get(SettingsKV, key_ep)
            if kv:
                db.delete(kv)
        else:
            kv = db.get(SettingsKV, key_ep) or SettingsKV(key=key_ep, value="")
            kv.value = ep_st
            db.add(kv)
        if client:
            try:
                client.set_peer_client_endpoint(row.interface, row.ros_id, ep_st or None)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"router client-endpoint update failed: {e}")
    if dto.preshared_key is not None:
        pk = dto.preshared_key.strip()
        if pk == "":
            kv = db.get(SettingsKV, f"peer_preshared_key:{peer_id}")
            if kv:
                db.delete(kv)
            if client:
                try:
                    client.set_peer_preshared_key(row.interface, row.ros_id, None)
                except Exception as e:
                    raise HTTPException(status_code=502, detail=f"router preshared-key clear failed: {e}")
        else:
            if not _is_valid_wg_private_key_b64(pk):
                raise HTTPException(status_code=400, detail="invalid preshared_key (must be base64 32 bytes)")
            _store_peer_preshared_key(db, peer_id, pk)
            if client:
                try:
                    client.set_peer_preshared_key(row.interface, row.ros_id, pk)
                except Exception as e:
                    raise HTTPException(status_code=502, detail=f"router preshared-key update failed: {e}")

    db.commit()
    db.refresh(row)
    return _build_client_export_prefs_out(db, row)


@router.post("/routers/{router_id}/peers/add", response_model=PeerListDTO)
def create_router_peer(router_id: int, dto: PeerCreateRouterDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")

    # Inbound only guard
    if dto.allowed_address.strip() in ("0.0.0.0/0", "::/0"):
        raise HTTPException(status_code=400, detail="only inbound peers are allowed (address must not be 0.0.0.0/0 or ::/0)")

    # CIDR sanity (support comma-separated list)
    try:
        for part in [p.strip() for p in dto.allowed_address.split(",") if p.strip()]:
            _ = ip_network(part, strict=False)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid allowed_address format")

    private_key = dto.private_key.strip() if dto.private_key is not None else None
    if private_key:
        if not _is_valid_wg_private_key_b64(private_key):
            raise HTTPException(status_code=400, detail="invalid private_key (must be base64 32 bytes)")

    preshared_key = dto.preshared_key.strip() if dto.preshared_key is not None else None
    if preshared_key:
        if not _is_valid_wg_private_key_b64(preshared_key):
            raise HTTPException(status_code=400, detail="invalid preshared_key (must be base64 32 bytes)")

    config_name = (dto.config_name or "").strip()
    custom_endpoint = (dto.custom_endpoint or "").strip()

    client = make_client(router)

    # If DB row already exists, prevent accidental duplicates
    existing_db = db.query(Peer).filter(
        Peer.router_id == router_id,
        Peer.interface == dto.interface,
        Peer.public_key == dto.public_key,
    ).first()
    if existing_db:
        raise HTTPException(status_code=409, detail="peer with same public_key already exists in database on this interface")

    # Check if the peer already exists on RouterOS (by public key)
    try:
        live = client.list_wireguard_peers(dto.interface)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")
    live_match = next((p for p in live if p.public_key == dto.public_key), None)

    ros_id = ""
    disabled = False
    allowed_address = dto.allowed_address
    name = dto.name
    if live_match:
        # If it exists on router, don't overwrite; instead block with conflict.
        raise HTTPException(status_code=409, detail="peer with same public_key already exists on RouterOS on this interface")
    else:
        try:
            ros_id = client.add_wireguard_peer(
                interface=dto.interface,
                public_key=dto.public_key,
                private_key=private_key,
                preshared_key=preshared_key,
                client_endpoint=custom_endpoint or None,
                allowed_address=dto.allowed_address,
                name=dto.name or "",
                disabled=False,
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"router create failed: {e}")

    row = Peer(
        router_id=router_id,
        interface=dto.interface,
        ros_id=ros_id,
        name=name or "",
        public_key=dto.public_key,
        allowed_address=allowed_address,
        disabled=disabled,
        selected=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # Optional: store client private key encrypted in DB (RouterOS doesn't store peer private keys).
    if private_key is not None:
        pk = private_key
        if pk == "":
            kv = db.get(SettingsKV, f"peer_private_key:{row.id}")
            if kv:
                db.delete(kv)
                db.commit()
        else:
            box = SecretBox(settings.secret_key)
            token = box.encrypt(pk)
            kv = db.get(SettingsKV, f"peer_private_key:{row.id}") or SettingsKV(key=f"peer_private_key:{row.id}", value="")
            kv.value = token
            db.add(kv)
            db.commit()
    if preshared_key:
        _store_peer_preshared_key(db, row.id, preshared_key)
    if config_name:
        kv = db.get(SettingsKV, f"peer_export_config_name:{row.id}") or SettingsKV(key=f"peer_export_config_name:{row.id}", value="")
        kv.value = config_name
        db.add(kv)
    if custom_endpoint:
        kv = db.get(SettingsKV, f"peer_export_endpoint:{row.id}") or SettingsKV(key=f"peer_export_endpoint:{row.id}", value="")
        kv.value = custom_endpoint
        db.add(kv)
    if preshared_key or config_name or custom_endpoint:
        db.commit()
    return row


class PeerPrivateKeyDTO(BaseModel):
    private_key: Optional[str] = None


@router.get("/peers/{peer_id}/client_private_key", response_model=PeerPrivateKeyDTO)
def get_peer_client_private_key(peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    kv = db.get(SettingsKV, f"peer_private_key:{peer_id}")
    if kv and (kv.value or "").strip():
        box = SecretBox(settings.secret_key)
        dec = box.decrypt(kv.value)
        if dec:
            return PeerPrivateKeyDTO(private_key=dec)

    # Fallback: if RouterOS has a private-key stored for this peer, read it and cache encrypted in DB.
    router = db.get(Router, row.router_id) if row.router_id else None
    if router and row.ros_id:
        try:
            client = make_client(router)
            pk = client.get_wireguard_peer_private_key(row.interface, row.ros_id)
            if pk:
                box = SecretBox(settings.secret_key)
                token = box.encrypt(pk)
                kv2 = db.get(SettingsKV, f"peer_private_key:{peer_id}") or SettingsKV(key=f"peer_private_key:{peer_id}", value="")
                kv2.value = token
                db.add(kv2)
                db.commit()
                return PeerPrivateKeyDTO(private_key=pk)
        except Exception:
            pass

    return PeerPrivateKeyDTO(private_key=None)


class PeerPrivateKeyUpdateDTO(BaseModel):
    private_key: Optional[str] = None  # set "" to clear


@router.patch("/peers/{peer_id}/client_private_key", response_model=PeerPrivateKeyDTO)
def patch_peer_client_private_key(peer_id: int, dto: PeerPrivateKeyUpdateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    if dto.private_key is None:
        kv = db.get(SettingsKV, f"peer_private_key:{peer_id}")
        if not kv:
            return PeerPrivateKeyDTO(private_key=None)
        box = SecretBox(settings.secret_key)
        return PeerPrivateKeyDTO(private_key=box.decrypt(kv.value))

    pk = dto.private_key.strip()
    if pk == "":
        kv = db.get(SettingsKV, f"peer_private_key:{peer_id}")
        if kv:
            db.delete(kv)
            db.commit()
        return PeerPrivateKeyDTO(private_key=None)

    _store_peer_private_key(db, peer_id, pk)
    db.commit()
    return PeerPrivateKeyDTO(private_key=pk)


class PeerRenewKeysDTO(BaseModel):
    peer: PeerListDTO
    private_key: str


@router.post("/peers/{peer_id}/renew_keys", response_model=PeerRenewKeysDTO)
def renew_peer_keys(peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    router = db.get(Router, row.router_id) if row.router_id else None
    if not router or not row.ros_id:
        raise HTTPException(status_code=400, detail="peer is not backed by a router")

    private_key, public_key = _generate_wg_keypair_b64()
    conflict = db.query(Peer.id).filter(
        Peer.router_id == row.router_id,
        Peer.interface == row.interface,
        Peer.public_key == public_key,
        Peer.id != row.id,
    ).first()
    if conflict:
        raise HTTPException(status_code=409, detail="generated public_key already exists on this interface")

    client = make_client(router)
    try:
        client.set_peer_keys(row.interface, row.ros_id, public_key, private_key)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router key renewal failed: {e}")

    row.public_key = public_key
    _store_peer_private_key(db, row.id, private_key)
    db.add(
        Action(
            peer_id=row.id,
            ts=datetime.now(timezone.utc),
            action="renew_keys",
            note="generated new WireGuard keypair via API",
        )
    )
    db.commit()
    db.refresh(row)
    return PeerRenewKeysDTO(peer=PeerListDTO.model_validate(row), private_key=private_key)


@router.post("/routers/{router_id}/test")
def test_router_connection(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    try:
        client = make_client(router)
        version = (client.get_system_version() or "").strip()
        router.ros_version = version
        router.ros_version_checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        router.ros_supported = is_routeros_supported(version)
        if not router.ros_supported:
            db.commit()
            try:
                assert_routeros_supported(version)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail="RouterOS 7.15 or newer is required")
        _ = client.list_wireguard_interfaces()
        db.commit()
        return {
            "ok": True,
            "ros_version": router.ros_version,
            "ros_version_checked_at": router.ros_version_checked_at,
            "ros_supported": router.ros_supported,
        }
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")


class TlsSetupStartDTO(BaseModel):
    method: str  # self_signed | letsencrypt
    common_name: Optional[str] = None
    days_valid: int = 3650
    dns_name: Optional[str] = None


class TlsSetupApplyDTO(BaseModel):
    disable_plain: bool = False


def _router_plain_password(row: Router) -> str:
    box = SecretBox(settings.secret_key)
    return box.decrypt(row.secret_enc) or ""


@router.post("/routers/{router_id}/tls-setup")
def start_router_tls_setup(router_id: int, dto: TlsSetupStartDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    if row.proto not in router_tls_setup.TLS_TARGETS:
        raise HTTPException(status_code=400, detail="this profile already uses TLS")
    try:
        return router_tls_setup.start_tls_setup(
            router_id=row.id,
            proto=row.proto,
            host=row.host,
            port=row.port,
            username=row.username,
            password=_router_plain_password(row),
            method=dto.method,
            common_name=(dto.common_name or "").strip(),
            days_valid=dto.days_valid,
            dns_name=(dto.dns_name or "").strip(),
        )
    except router_tls_setup.TlsSetupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/routers/{router_id}/tls-setup/status")
def get_router_tls_setup_status(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    snapshot = router_tls_setup.get_job(router_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="no TLS setup job for this router")
    return snapshot


@router.post("/routers/{router_id}/tls-setup/apply", response_model=RouterDTO)
def apply_router_tls_setup(router_id: int, dto: TlsSetupApplyDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    snapshot = router_tls_setup.get_job(router_id)
    if not snapshot or snapshot.get("status") != "ok":
        raise HTTPException(status_code=409, detail="no successful TLS setup to apply; run the setup first")
    result = snapshot.get("result") or {}
    password = _router_plain_password(row)

    if row.proto in router_tls_setup.TLS_TARGETS:
        # Re-verify before flipping the profile; nothing is changed if this fails.
        try:
            router_tls_setup.verify_target(result, row.username, password)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"TLS verification failed: {exc}") from exc
        row.proto = str(result.get("proto") or row.proto)
        row.host = str(result.get("host") or row.host)
        row.port = int(result.get("port") or row.port)
        row.tls_verify = bool(result.get("tls_verify"))
        _refresh_router_version_or_raise(row)
        db.commit()
        db.refresh(row)

    if dto.disable_plain:
        try:
            router_tls_setup.disable_plain_service(
                proto=row.proto,
                host=row.host,
                port=row.port,
                username=row.username,
                password=password,
                tls_verify=row.tls_verify,
                plain_service=str(result.get("plain_service") or ""),
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"profile switched to TLS, but disabling the plaintext service failed: {exc}") from exc

    return row


#
# Demo endpoints removed (project is now RouterOS-backed only).
#


@router.get("/peers", response_model=List[PeerListDTO])
def list_saved_peers(
    router_id: Optional[int] = None,
    router_ids: Optional[List[int]] = Query(None),
    interface: Optional[str] = None,
    selected_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    router_id, router_ids = _resolve_router_filter(router_id, router_ids)
    q = db.query(Peer)
    q = _apply_router_filter(q, router_id, router_ids)
    if interface is not None:
        q = q.filter(Peer.interface == interface)
    if selected_only:
        q = q.filter(Peer.selected == True)
    return q.order_by(Peer.id.asc()).all()


class PeerUpdateDTO(BaseModel):
    selected: Optional[bool] = None
    disabled: Optional[bool] = None
    name: Optional[str] = Field(default=None, max_length=255)


def _router_delete_missing(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response is not None and exc.response.status_code == 404:
            return True
        try:
            payload = exc.response.json() if exc.response is not None else {}
        except Exception:
            payload = {}
        if isinstance(payload, dict):
            msg = " ".join(str(payload.get(key, "")) for key in ("message", "detail", "error")).lower()
            if "not found" in msg or "no such item" in msg:
                return True
    msg = str(exc).lower()
    return "404 not found" in msg or "not found" in msg or "no such item" in msg


def _router_delete_failure_can_be_local_only(peer: Peer, exc: Exception) -> bool:
    if _router_delete_missing(exc):
        return True
    # Hidden/unselected peers are already outside normal management. RouterOS can return
    # 500 for stale ids on DELETE; don't let that trap the local ghost record forever.
    return peer.selected is False and isinstance(exc, httpx.HTTPStatusError)


def _delete_peer_local_data(db: Session, row: Peer) -> None:
    peer_id = int(row.id)
    for suffix in (
        f"peer_private_key:{peer_id}",
        f"peer_preshared_key:{peer_id}",
        f"peer_export_config_name:{peer_id}",
        f"peer_export_endpoint:{peer_id}",
        f"quota_valid_from:{peer_id}",
        f"quota_valid_until:{peer_id}",
    ):
        kv = db.get(SettingsKV, suffix)
        if kv:
            db.delete(kv)

    db.query(UsageSample).filter(UsageSample.peer_id == peer_id).delete(synchronize_session=False)
    db.query(UsageMinute).filter(UsageMinute.peer_id == peer_id).delete(synchronize_session=False)
    db.query(UsageDaily).filter(UsageDaily.peer_id == peer_id).delete(synchronize_session=False)
    db.query(UsageMonthly).filter(UsageMonthly.peer_id == peer_id).delete(synchronize_session=False)
    db.query(Quota).filter(Quota.peer_id == peer_id).delete(synchronize_session=False)
    db.query(Action).filter(Action.peer_id == peer_id).delete(synchronize_session=False)
    db.query(FairUsageAssignment).filter(FairUsageAssignment.peer_id == peer_id).delete(synchronize_session=False)
    db.query(FairUsageState).filter(FairUsageState.peer_id == peer_id).delete(synchronize_session=False)
    db.query(TelegramPeerBinding).filter(TelegramPeerBinding.peer_id == peer_id).delete(synchronize_session=False)
    db.query(TelegramNotificationLog).filter(TelegramNotificationLog.peer_id == peer_id).delete(synchronize_session=False)
    db.query(PeerTotalsMerge).filter(
        or_(PeerTotalsMerge.source_peer_id == peer_id, PeerTotalsMerge.target_peer_id == peer_id)
    ).delete(synchronize_session=False)
    for token in db.query(TelegramSignupToken).all():
        try:
            peer_ids = [int(pid) for pid in json.loads(token.peer_ids or "[]") if int(pid) > 0]
        except Exception:
            continue
        filtered = [pid for pid in peer_ids if pid != peer_id]
        if filtered == peer_ids:
            continue
        if filtered:
            token.peer_ids = json.dumps(filtered)
        else:
            db.delete(token)

    db.delete(row)


@router.patch("/peers/{peer_id}", response_model=PeerListDTO)
def update_peer(peer_id: int, dto: PeerUpdateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    if dto.selected is not None:
        desired_selected = bool(dto.selected)
        if row.selected != desired_selected:
            row.selected = desired_selected
            db.add(
                Action(
                    peer_id=row.id,
                    ts=datetime.now(timezone.utc),
                    action="manual_show" if desired_selected else "manual_hide",
                    note="via API",
                )
            )
    if dto.name is not None:
        new_name = dto.name.strip()
        if (row.name or "") != new_name:
            prev = row.name or ""
            r = db.get(Router, row.router_id)
            old_fu = f"{FU_QUEUE_PREFIX}{row.name or row.id}"
            client = None
            if r and (row.ros_id or "").strip():
                client = make_client(r)
                try:
                    client.set_peer_name(row.interface, row.ros_id, new_name)
                except Exception as e:
                    raise HTTPException(status_code=502, detail=f"router update failed: {e}")
            row.name = new_name
            new_fu = f"{FU_QUEUE_PREFIX}{row.name or row.id}"
            if client and old_fu != new_fu:
                fus = db.query(FairUsageState).filter(FairUsageState.peer_id == row.id).first()
                if fus and fus.throttled and (fus.ros_queue_id or "").strip():
                    try:
                        client.set_simple_queue_name((fus.ros_queue_id or "").strip(), new_fu)
                    except Exception:
                        pass
            db.add(
                Action(
                    peer_id=row.id,
                    ts=datetime.now(timezone.utc),
                    action="peer_rename",
                    note=f'"{prev}" -> "{new_name}"',
                )
            )
    if dto.disabled is not None:
        desired = bool(dto.disabled)
        # For real RouterOS peers, only flip DB state if router call succeeds.
        r = db.get(Router, row.router_id)
        if r and row.ros_id:
            client = make_client(r)
            try:
                client.set_peer_disabled(row.interface, row.ros_id, desired)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"router update failed: {e}")
        # If no backing router/ros_id, just update DB.
        row.disabled = desired
        db.add(
            Action(
                peer_id=row.id,
                ts=datetime.now(timezone.utc),
                action="manual_disable" if desired else "manual_enable",
                note="via API",
            )
        )
    db.commit()
    db.refresh(row)
    return row


@router.delete("/peers/{peer_id}")
def delete_peer(peer_id: int, skip_router: bool = False, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    
    router_deleted = False
    # If it's an active RouterOS-backed peer, delete it on the router first (unless skipped).
    # Hidden/unselected rows are historical local records; their ros_id may be stale or
    # reused by a real peer, so deleting them must not touch RouterOS.
    r = db.get(Router, row.router_id)
    if r and row.ros_id and row.selected is not False and not skip_router:
        client = make_client(r)
        try:
            client.remove_wireguard_peer(row.interface, row.ros_id)
            router_deleted = True
        except Exception as e:
            if _router_delete_failure_can_be_local_only(row, e):
                router_deleted = False
            else:
                # Don't lie: if router delete failed, keep DB record.
                raise HTTPException(status_code=502, detail=f"router delete failed: {e}")

    # Remove any active fair-usage queues for this peer before deleting local state.
    if r:
        for st in db.query(FairUsageState).filter(FairUsageState.peer_id == peer_id).all():
            if st.ros_queue_id:
                try:
                    client = make_client(r)
                    client.remove_simple_queue(st.ros_queue_id)
                except Exception:
                    pass

    _delete_peer_local_data(db, row)
    db.commit()
    return {"ok": True, "deleted_peer_id": peer_id, "router_deleted": router_deleted}


class PeerRouterSyncResolveDTO(BaseModel):
    action: str


def _clear_peer_router_sync_status(row: Peer) -> None:
    row.router_sync_status = "synced"
    row.router_sync_first_seen_at = None
    row.router_sync_last_seen_at = None


@router.post("/peers/{peer_id}/router-sync/resolve")
def resolve_peer_router_sync(
    peer_id: int,
    dto: PeerRouterSyncResolveDTO,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")

    status_value = (row.router_sync_status or "synced").strip().lower()
    action_value = (dto.action or "").strip().lower()
    now_utc = datetime.now(timezone.utc)

    if status_value == "missing":
        if action_value == "hide":
            row.selected = False
            _clear_peer_router_sync_status(row)
            db.add(Action(peer_id=row.id, ts=now_utc, action="router_missing_hide", note="Admin hid missing peer"))
            db.commit()
            db.refresh(row)
            return row
        if action_value == "delete":
            _delete_peer_local_data(db, row)
            db.commit()
            return {"ok": True, "deleted_peer_id": peer_id, "router_deleted": False}
        raise HTTPException(status_code=400, detail="missing peers support actions: hide, delete")

    if status_value == "new":
        if action_value == "accept":
            row.selected = True
            _clear_peer_router_sync_status(row)
            db.add(Action(peer_id=row.id, ts=now_utc, action="router_new_accept", note="Admin added RouterOS-discovered peer"))
            db.commit()
            db.refresh(row)
            return row
        if action_value == "hide":
            row.selected = False
            _clear_peer_router_sync_status(row)
            db.add(Action(peer_id=row.id, ts=now_utc, action="router_new_hide", note="Admin kept RouterOS-discovered peer hidden"))
            db.commit()
            db.refresh(row)
            return row
        raise HTTPException(status_code=400, detail="new peers support actions: accept, hide")

    raise HTTPException(status_code=400, detail="peer has no pending RouterOS sync decision")


class ActionDTO(BaseModel):
    ts: str
    action: str
    note: str


@router.get("/peers/{peer_id}/actions", response_model=List[ActionDTO])
def list_peer_actions(peer_id: int, limit: int = 25, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Keep limit sane
    limit = max(1, min(200, int(limit or 25)))
    rows = (
        db.query(Action)
        .filter(Action.peer_id == peer_id)
        .order_by(Action.ts.desc())
        .limit(limit)
        .all()
    )
    out: List[ActionDTO] = []
    for a in rows:
        ts = a.ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append(ActionDTO(ts=ts.isoformat(), action=a.action, note=a.note or ""))
    return out


class LastActionDTO(BaseModel):
    peer_id: int
    ts: str
    action: str
    note: str


@router.get("/actions/last", response_model=List[LastActionDTO])
def get_last_actions(peer_ids: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Return the latest Action per peer for a comma-separated peer_ids list.
    Designed for the Dashboard so we don't do N requests.
    """
    ids: list[int] = []
    for part in (peer_ids or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    ids = [i for i in ids if i > 0]
    if not ids:
        return []

    sub = (
        db.query(Action.peer_id.label("peer_id"), func.max(Action.ts).label("max_ts"))
        .filter(Action.peer_id.in_(ids))
        .group_by(Action.peer_id)
        .subquery()
    )
    rows = (
        db.query(Action)
        .join(sub, (Action.peer_id == sub.c.peer_id) & (Action.ts == sub.c.max_ts))
        .all()
    )
    by_peer: dict[int, Action] = {}
    for a in rows:
        if a.peer_id is None:
            continue
        # In case of ties, keep the first (same ts)
        by_peer.setdefault(int(a.peer_id), a)

    out: list[LastActionDTO] = []
    for pid in ids:
        a = by_peer.get(pid)
        if not a:
            continue
        ts = a.ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append(LastActionDTO(peer_id=pid, ts=ts.isoformat(), action=a.action, note=a.note or ""))
    return out


@router.post("/peers/{peer_id}/reconcile", response_model=PeerListDTO)
def reconcile_peer(peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    Apply current fair-usage policy to a peer immediately (throttle/unthrottle via Simple Queue on RouterOS).
    Uses the same logic as the scheduler tick (limits sync + missing-queue repair).
    """
    peer = db.get(Peer, peer_id)
    if not peer:
        raise HTTPException(status_code=404, detail="peer not found")
    r = db.get(Router, peer.router_id)
    client = make_client(r) if (r and peer.ros_id) else None
    now_utc = datetime.now(timezone.utc)

    try:
        apply_fair_usage_policy(db, peer, client, now_utc, strict_router_errors=True)
    except FairUsageRouterError as e:
        raise HTTPException(status_code=502, detail=str(e))
    db.commit()
    db.refresh(peer)
    return peer


class UsagePointDTO(BaseModel):
    day: str
    rx: int
    tx: int


def compute_peer_usage_points(
    db: Session,
    peer_id: int,
    window: str = "daily",
    seconds: Optional[int] = None,
    interval: Optional[int] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    all_time: bool = False,
) -> List[UsagePointDTO]:
    """
    Same logic as GET /peers/{id}/usage — shared with Telegram usage-chart screenshots.
    """
    start_utc, end_utc = _normalize_time_range(start, end)

    if window == "daily":
        q = db.query(UsageDaily).filter(UsageDaily.peer_id == peer_id)
        if not all_time and (start_utc is not None or end_utc is not None):
            if start_utc is not None:
                q = q.filter(UsageDaily.day >= start_utc.date().strftime("%Y-%m-%d"))
            if end_utc is not None:
                q = q.filter(UsageDaily.day <= end_utc.date().strftime("%Y-%m-%d"))
        rows = q.order_by(UsageDaily.day.asc()).all()
        return [UsagePointDTO(day=r.day, rx=r.rx, tx=r.tx) for r in rows]

    if window == "raw":
        resolved_seconds, cutoff, end_dt = _normalize_raw_window(
            seconds=seconds,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        resolved_interval = _normalize_raw_interval(resolved_seconds, interval)

        tz = app_zoneinfo()
        if _minute_coverage_covers_window_start(
            db,
            peer_id=peer_id,
            selected_only=False,
            cutoff=cutoff,
            end_dt=end_dt,
        ):
            query = text(
                """
                SELECT minute_ts, rx, tx
                FROM usage_minute
                WHERE peer_id = :peer_id
                  AND minute_ts >= :cutoff
                  AND minute_ts <= :end
                ORDER BY minute_ts
                """
            )
            rows = db.execute(
                query,
                {
                    "peer_id": peer_id,
                    "cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                },
            ).fetchall()
            minute_rows = [(_ts_cell_to_utc_naive(r[0]), int(r[1] or 0), int(r[2] or 0)) for r in rows]
            aggregated = aggregate_rows_to_local_buckets(minute_rows, resolved_interval, tz)
            return [
                UsagePointDTO(
                    day=b.replace(tzinfo=timezone.utc).isoformat(),
                    rx=rx,
                    tx=tx,
                )
                for b, rx, tx in aggregated
            ]

        samples = (
            db.query(UsageSample)
            .filter(
                UsageSample.peer_id == peer_id,
                UsageSample.ts >= cutoff.replace(tzinfo=None),
                UsageSample.ts <= end_dt.replace(tzinfo=None),
            )
            .order_by(UsageSample.ts.asc())
            .all()
        )
        delta_rows: list[tuple[datetime, int, int]] = []
        prev: Optional[UsageSample] = None
        quarantine = CounterQuarantineState()
        for s in samples:
            if prev is None:
                prev = s
                continue
            ts_naive = s.ts.replace(tzinfo=None) if s.ts.tzinfo else s.ts
            day_key = counter_day_key(ts_naive, tz)
            drx = quarantine.apply("rx", counter_delta(prev.rx, s.rx), day_key)
            dtx = quarantine.apply("tx", counter_delta(prev.tx, s.tx), day_key)
            prev = s
            if drx <= 0 and dtx <= 0:
                continue
            delta_rows.append((ts_naive, int(drx or 0), int(dtx or 0)))
        aggregated = aggregate_rows_to_local_buckets(delta_rows, resolved_interval, tz)
        return [
            UsagePointDTO(day=b.replace(tzinfo=timezone.utc).isoformat(), rx=rx, tx=tx)
            for b, rx, tx in aggregated
        ]

    raise HTTPException(status_code=400, detail="window must be 'daily' or 'raw'")


@router.get("/peers/{peer_id}/usage", response_model=List[UsagePointDTO])
def get_peer_usage(
    peer_id: int,
    window: str = "daily",
    seconds: Optional[int] = None,
    interval: Optional[int] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    all_time: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    window=daily: aggregate per day from UsageDaily (existing behaviour).
    window=raw: aggregate minute rollups for the requested window. If minute rollups
    do not exist yet for the window, fall back to raw samples.
    """
    return compute_peer_usage_points(
        db,
        peer_id,
        window,
        seconds=seconds,
        interval=interval,
        start=start,
        end=end,
        all_time=all_time,
    )



@router.post("/peers/{peer_id}/reset_metrics")
def reset_peer_metrics(peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # Validate peer exists
    peer = db.get(Peer, peer_id)
    if not peer:
        raise HTTPException(status_code=404, detail="peer not found")
    deleted_samples = db.query(UsageSample).filter(UsageSample.peer_id == peer_id).delete()
    deleted_minutes = db.query(UsageMinute).filter(UsageMinute.peer_id == peer_id).delete()
    deleted_daily = db.query(UsageDaily).filter(UsageDaily.peer_id == peer_id).delete()
    deleted_monthly = db.query(UsageMonthly).filter(UsageMonthly.peer_id == peer_id).delete()
    db.commit()
    return {
        "ok": True,
        "deleted_samples": deleted_samples,
        "deleted_minutes": deleted_minutes,
        "deleted_daily": deleted_daily,
        "deleted_monthly": deleted_monthly,
    }


# Quota endpoints (time-based fields stored in SettingsKV)
class QuotaDTO(BaseModel):
    monthly_limit_bytes: Optional[int] = None
    reset_day: int
    valid_from: Optional[str] = None  # ISO8601
    valid_until: Optional[str] = None  # ISO8601
    used_rx: int
    used_tx: int


@router.get("/peers/{peer_id}/quota", response_model=QuotaDTO)
def get_peer_quota(peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    q = db.query(Quota).filter(Quota.peer_id == peer_id).first()
    monthly_limit_bytes = q.monthly_limit_bytes if q and q.monthly_limit_bytes else None
    reset_day_val = q.reset_day if q else settings.monthly_reset_day
    # time-based via SettingsKV
    vf = db.get(SettingsKV, f"quota_valid_from:{peer_id}")
    vu = db.get(SettingsKV, f"quota_valid_until:{peer_id}")
    valid_from = vf.value if vf else None
    valid_until = vu.value if vu else None
    # Usage in the selected calendar month. Daily rollups are still stored with Gregorian
    # keys, so use the selected calendar boundary converted back to UTC/Gregorian dates.
    start_month_utc, end_month_utc = selected_month_bounds_utc(
        datetime.now(timezone.utc),
        app_zoneinfo(),
        app_date_calendar(),
    )
    rows = (
        db.query(UsageDaily)
        .filter(
            UsageDaily.peer_id == peer_id,
            UsageDaily.day >= start_month_utc.date().strftime("%Y-%m-%d"),
            UsageDaily.day <= end_month_utc.date().strftime("%Y-%m-%d"),
        )
        .all()
    )
    used_rx = sum(r.rx for r in rows)
    used_tx = sum(r.tx for r in rows)
    return QuotaDTO(
        monthly_limit_bytes=monthly_limit_bytes,
        reset_day=reset_day_val,
        valid_from=valid_from,
        valid_until=valid_until,
        used_rx=used_rx,
        used_tx=used_tx,
    )


class QuotaUpdateDTO(BaseModel):
    monthly_limit_bytes: Optional[int] = None  # set 0 or None to clear
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None


@router.patch("/peers/{peer_id}/quota", response_model=QuotaDTO)
def patch_peer_quota(peer_id: int, dto: QuotaUpdateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    # upsert quota
    q = db.query(Quota).filter(Quota.peer_id == peer_id).first()
    if not q:
        q = Quota(peer_id=peer_id, monthly_limit_bytes=0, reset_day=settings.monthly_reset_day)
        db.add(q)
        db.flush()
    if dto.monthly_limit_bytes is not None:
        q.monthly_limit_bytes = int(dto.monthly_limit_bytes or 0)
    # store time-based in SettingsKV
    if dto.valid_from is not None:
        if dto.valid_from == "":
            kv = db.get(SettingsKV, f"quota_valid_from:{peer_id}")
            if kv:
                db.delete(kv)
        else:
            kv = db.get(SettingsKV, f"quota_valid_from:{peer_id}") or SettingsKV(key=f"quota_valid_from:{peer_id}", value="")
            kv.value = dto.valid_from
            db.add(kv)
    if dto.valid_until is not None:
        if dto.valid_until == "":
            kv = db.get(SettingsKV, f"quota_valid_until:{peer_id}")
            if kv:
                db.delete(kv)
        else:
            kv = db.get(SettingsKV, f"quota_valid_until:{peer_id}") or SettingsKV(key=f"quota_valid_until:{peer_id}", value="")
            kv.value = dto.valid_until
            db.add(kv)
    db.commit()
    return get_peer_quota(peer_id, db)


class MonthlySummaryPointDTO(BaseModel):
    day: str
    rx: int
    tx: int


@router.get("/summary/month", response_model=List[MonthlySummaryPointDTO])
def get_monthly_summary(
    days: int = 14,
    router_id: Optional[int] = None,
    router_ids: Optional[List[int]] = Query(None),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    all_time: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Aggregate total RX/TX per day across selected peers.
    - Default: last `days` days (filled with zeros like before).
    - If `start`/`end` are provided: filter by day range (returns only days with data).
    - If `all_time=true`: return all available days (returns only days with data).
    """
    router_id, router_ids = _resolve_router_filter(router_id, router_ids)
    start_utc, end_utc = _normalize_time_range(start, end)

    base = (
        db.query(
            UsageDaily.day.label("day"),
            func.coalesce(func.sum(UsageDaily.rx), 0).label("rx"),
            func.coalesce(func.sum(UsageDaily.tx), 0).label("tx"),
        )
        .join(Peer, UsageDaily.peer_id == Peer.id)
        .filter(Peer.selected == True)
    )
    base = _apply_router_filter(base, router_id, router_ids)

    if all_time or start_utc is not None or end_utc is not None:
        q = base
        if start_utc is not None:
            q = q.filter(UsageDaily.day >= start_utc.date().strftime("%Y-%m-%d"))
        if end_utc is not None:
            q = q.filter(UsageDaily.day <= end_utc.date().strftime("%Y-%m-%d"))
        rows = q.group_by(UsageDaily.day).order_by(UsageDaily.day.asc()).all()
        return [MonthlySummaryPointDTO(day=r.day, rx=int(r.rx or 0), tx=int(r.tx or 0)) for r in rows]

    try:
        days = int(days)
    except Exception:
        days = 14
    days = max(1, min(180, days))
    today = datetime.utcnow().date()
    day_keys = [(today - timedelta(days=o)).strftime("%Y-%m-%d") for o in range(days)]

    rows = (
        base.filter(UsageDaily.day.in_(day_keys))
        .group_by(UsageDaily.day)
        .order_by(UsageDaily.day.asc())
        .all()
    )
    by_day = {r.day: (int(r.rx or 0), int(r.tx or 0)) for r in rows}
    out: List[MonthlySummaryPointDTO] = []
    for day in sorted(day_keys):
        rx, tx = by_day.get(day, (0, 0))
        out.append(MonthlySummaryPointDTO(day=day, rx=rx, tx=tx))
    return out


class RouterMonthlySummaryPointDTO(BaseModel):
    router_id: int
    day: str
    rx: int
    tx: int


@router.get("/summary/month/by_router", response_model=List[RouterMonthlySummaryPointDTO])
def get_monthly_summary_by_router(
    days: int = 14,
    router_id: Optional[int] = None,
    router_ids: Optional[List[int]] = Query(None),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    all_time: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    router_id, router_ids = _resolve_router_filter(router_id, router_ids)
    start_utc, end_utc = _normalize_time_range(start, end)

    base = (
        db.query(
            Peer.router_id.label("router_id"),
            UsageDaily.day.label("day"),
            func.coalesce(func.sum(UsageDaily.rx), 0).label("rx"),
            func.coalesce(func.sum(UsageDaily.tx), 0).label("tx"),
        )
        .join(Peer, UsageDaily.peer_id == Peer.id)
        .filter(Peer.selected == True)
    )
    base = _apply_router_filter(base, router_id, router_ids)

    if all_time or start_utc is not None or end_utc is not None:
        q = base
        if start_utc is not None:
            q = q.filter(UsageDaily.day >= start_utc.date().strftime("%Y-%m-%d"))
        if end_utc is not None:
            q = q.filter(UsageDaily.day <= end_utc.date().strftime("%Y-%m-%d"))
        rows = (
            q.group_by(Peer.router_id, UsageDaily.day)
            .order_by(Peer.router_id.asc(), UsageDaily.day.asc())
            .all()
        )
        return [
            RouterMonthlySummaryPointDTO(router_id=int(r.router_id), day=r.day, rx=int(r.rx or 0), tx=int(r.tx or 0))
            for r in rows
        ]

    try:
        days = int(days)
    except Exception:
        days = 14
    days = max(1, min(180, days))
    today = datetime.utcnow().date()
    day_keys = [(today - timedelta(days=o)).strftime("%Y-%m-%d") for o in range(days)]

    rows = (
        base.filter(UsageDaily.day.in_(day_keys))
        .group_by(Peer.router_id, UsageDaily.day)
        .order_by(Peer.router_id.asc(), UsageDaily.day.asc())
        .all()
    )
    return [
        RouterMonthlySummaryPointDTO(router_id=int(r.router_id), day=r.day, rx=int(r.rx or 0), tx=int(r.tx or 0))
        for r in rows
    ]


class PeerUsageSummaryDTO(BaseModel):
    peer_id: int
    rx: int
    tx: int
    has_fair_usage: bool = False
    fair_usage_throttled: bool = False


@router.get("/summary/peers", response_model=List[PeerUsageSummaryDTO])
def get_peers_summary(
    days: Optional[int] = None,
    seconds: Optional[int] = None,
    router_id: Optional[int] = None,
    router_ids: Optional[List[int]] = Query(None),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    all_time: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Aggregate total RX/TX per peer for the specified window.
    - If `days` is provided (e.g. 1, 7, 30), aggregates UsageDaily.
    - If `seconds` is provided (e.g. 3600), aggregates raw UsageSample deltas.
    - If neither, defaults to days=1.
    """
    router_id, router_ids = _resolve_router_filter(router_id, router_ids)
    start_utc, end_utc = _normalize_time_range(start, end)
    summary: dict[int, dict[str, int]] = {}  # peer_id -> {rx, tx}

    if seconds and seconds > 0:
        resolved_seconds, cutoff, end_dt = _normalize_raw_window(
            seconds=seconds,
            start_utc=start_utc,
            end_utc=end_utc,
        )
        if _minute_coverage_covers_window_start(
            db,
            router_id=router_id,
            router_ids=router_ids,
            cutoff=cutoff,
            end_dt=end_dt,
        ):
            router_filter = _router_sql_filter(router_id, router_ids)
            query = text(f"""
            SELECT
                m.peer_id,
                COALESCE(SUM(m.rx), 0) as total_rx,
                COALESCE(SUM(m.tx), 0) as total_tx
            FROM usage_minute m
            JOIN peers p ON p.id = m.peer_id
            WHERE p.selected = 1
              {router_filter}
              AND m.minute_ts >= :cutoff
              AND m.minute_ts <= :end
            GROUP BY m.peer_id
            """)
            result = db.execute(
                query,
                {
                    "cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            for r in result:
                summary[int(r[0])] = {"rx": int(r[1] or 0), "tx": int(r[2] or 0)}
        else:
            for peer_id, rx, tx in _query_raw_peer_summaries(
                db,
                cutoff=cutoff,
                end_dt=end_dt,
                router_id=router_id,
                router_ids=router_ids,
            ):
                summary[peer_id] = {"rx": rx, "tx": tx}
            
    else:
        # DAILY WINDOW (UsageDaily)
        q = (
            db.query(
                UsageDaily.peer_id.label("peer_id"),
                func.coalesce(func.sum(UsageDaily.rx), 0).label("rx"),
                func.coalesce(func.sum(UsageDaily.tx), 0).label("tx"),
            )
            .join(Peer, UsageDaily.peer_id == Peer.id)
            .filter(Peer.selected == True)
        )
        q = _apply_router_filter(q, router_id, router_ids)
        if not all_time:
            if start_utc is not None:
                q = q.filter(UsageDaily.day >= start_utc.date().strftime("%Y-%m-%d"))
            if end_utc is not None:
                q = q.filter(UsageDaily.day <= end_utc.date().strftime("%Y-%m-%d"))
            if start_utc is None and end_utc is None:
                d = days if days and days > 0 else 1
                d = max(1, min(180, int(d)))
                start_day = (datetime.utcnow().date() - timedelta(days=d - 1)).strftime("%Y-%m-%d")
                q = q.filter(UsageDaily.day >= start_day)

        rows = q.group_by(UsageDaily.peer_id).all()
        for r in rows:
            summary[int(r.peer_id)] = {"rx": int(r.rx or 0), "tx": int(r.tx or 0)}

    # Peers with no usage in this window were omitted above; include them so clients can show
    # fair-usage flags and "0 B" totals for never-connected / idle peers.
    scope_peers = db.query(Peer.id).filter(Peer.selected == True)
    scope_peers = _apply_router_filter(scope_peers, router_id, router_ids)
    for row in scope_peers.all():
        summary.setdefault(int(row.id), {"rx": 0, "tx": 0})

    peer_ids = list(summary.keys())
    fu_set: set[int] = set()
    fu_throttled: dict[int, bool] = {}
    if peer_ids:
        peers_for_fu = db.query(Peer).filter(Peer.id.in_(peer_ids)).all()
        fu_set = peer_ids_with_applicable_fair_usage(db, peers_for_fu)
        for st in db.query(FairUsageState).filter(FairUsageState.peer_id.in_(peer_ids)).all():
            fu_throttled[int(st.peer_id)] = bool(st.throttled)

    return [
        PeerUsageSummaryDTO(
            peer_id=pid,
            rx=vals["rx"],
            tx=vals["tx"],
            has_fair_usage=pid in fu_set,
            fair_usage_throttled=fu_throttled.get(pid, False),
        )
        for pid, vals in summary.items()
    ]

class SummaryRawPointDTO(BaseModel):
    ts: str
    rx: int
    tx: int


@router.get("/summary/raw", response_model=List[SummaryRawPointDTO])
def get_summary_raw(
    seconds: int = 3600,
    router_id: Optional[int] = None,
    router_ids: Optional[List[int]] = Query(None),
    interval: Optional[int] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Aggregate minute rollups across selected peers for the requested raw-range window."""
    router_id, router_ids = _resolve_router_filter(router_id, router_ids)
    start_utc, end_utc = _normalize_time_range(start, end)
    resolved_seconds, cutoff, end_dt = _normalize_raw_window(
        seconds=seconds,
        start_utc=start_utc,
        end_utc=end_utc,
    )
    resolved_interval = _normalize_raw_interval(resolved_seconds, interval)
    tz = app_zoneinfo()
    if _minute_coverage_covers_window_start(
        db,
        router_id=router_id,
        router_ids=router_ids,
        cutoff=cutoff,
        end_dt=end_dt,
    ):
        router_filter = _router_sql_filter(router_id, router_ids)
        query = text(f"""
        SELECT
            m.minute_ts,
            COALESCE(SUM(m.rx), 0) as s_rx,
            COALESCE(SUM(m.tx), 0) as s_tx
        FROM usage_minute m
        JOIN peers p ON p.id = m.peer_id
        WHERE p.selected = 1
          {router_filter}
          AND m.minute_ts >= :cutoff
          AND m.minute_ts <= :end
        GROUP BY m.minute_ts
        ORDER BY m.minute_ts
        """)
        rows = db.execute(
            query,
            {
                "cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
                "end": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            },
        ).fetchall()
        minute_rows = [(_ts_cell_to_utc_naive(r[0]), int(r[1] or 0), int(r[2] or 0)) for r in rows]
        aggregated = aggregate_rows_to_local_buckets(minute_rows, resolved_interval, tz)
    else:
        delta_rows = _query_raw_sample_delta_rows(
            db,
            cutoff=cutoff,
            end_dt=end_dt,
            router_id=router_id,
            router_ids=router_ids,
        )
        aggregated = aggregate_rows_to_local_buckets(delta_rows, resolved_interval, tz)

    return [
        SummaryRawPointDTO(ts=b.replace(tzinfo=timezone.utc).isoformat(), rx=rx, tx=tx)
        for b, rx, tx in aggregated
    ]


class RouterSummaryRawPointDTO(BaseModel):
    router_id: int
    ts: str
    rx: int
    tx: int


@router.get("/summary/raw/by_router", response_model=List[RouterSummaryRawPointDTO])
def get_summary_raw_by_router(
    seconds: int = 3600,
    router_id: Optional[int] = None,
    router_ids: Optional[List[int]] = Query(None),
    interval: Optional[int] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    router_id, router_ids = _resolve_router_filter(router_id, router_ids)
    start_utc, end_utc = _normalize_time_range(start, end)
    resolved_seconds, cutoff, end_dt = _normalize_raw_window(
        seconds=seconds,
        start_utc=start_utc,
        end_utc=end_utc,
    )
    resolved_interval = _normalize_raw_interval(resolved_seconds, interval)
    tz = app_zoneinfo()
    if _minute_coverage_covers_window_start(
        db,
        router_id=router_id,
        router_ids=router_ids,
        cutoff=cutoff,
        end_dt=end_dt,
    ):
        router_filter = _router_sql_filter(router_id, router_ids)
        query = text(f"""
        SELECT
            p.router_id,
            m.minute_ts,
            COALESCE(SUM(m.rx), 0) as s_rx,
            COALESCE(SUM(m.tx), 0) as s_tx
        FROM usage_minute m
        JOIN peers p ON p.id = m.peer_id
        WHERE p.selected = 1
          {router_filter}
          AND m.minute_ts >= :cutoff
          AND m.minute_ts <= :end
        GROUP BY p.router_id, m.minute_ts
        ORDER BY p.router_id, m.minute_ts
        """)
        rows = db.execute(
            query,
            {
                "cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S"),
                "end": end_dt.strftime("%Y-%m-%d %H:%M:%S"),
            },
        ).fetchall()
        rrows = [(int(r[0]), _ts_cell_to_utc_naive(r[1]), int(r[2] or 0), int(r[3] or 0)) for r in rows]
        aggregated = aggregate_router_rows_to_local_buckets(rrows, resolved_interval, tz)
    else:
        delta_rows = _query_raw_router_sample_delta_rows(
            db,
            cutoff=cutoff,
            end_dt=end_dt,
            router_id=router_id,
            router_ids=router_ids,
        )
        aggregated = aggregate_router_rows_to_local_buckets(delta_rows, resolved_interval, tz)
    return [
        RouterSummaryRawPointDTO(
            router_id=rid,
            ts=b.replace(tzinfo=timezone.utc).isoformat(),
            rx=rx,
            tx=tx,
        )
        for rid, b, rx, tx in aggregated
    ]


class UsageMaintenanceStatusDTO(BaseModel):
    running: bool
    phase: str
    phase_label: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    updated_at: Optional[str] = None
    cancelled_at: Optional[str] = None
    last_error: Optional[str] = None
    last_completed_phase: Optional[str] = None
    resume_cursor: Optional[dict] = None
    detail: Optional[str] = None
    backup_path: Optional[str] = None
    file_size_before: Optional[int] = None
    file_size_after: Optional[int] = None
    backfilled_minutes: int = 0
    deleted_samples: int = 0
    deleted_minutes: int = 0
    deleted_daily: int = 0
    backfill_cutoff: Optional[str] = None
    raw_prune_before: Optional[str] = None
    minute_prune_before: Optional[str] = None
    daily_prune_before: Optional[str] = None
    cancel_requested: bool = False
    can_cancel: bool = False
    trigger: str = "manual"
    next_scheduled_run: Optional[str] = None
    last_auto_run: Optional[str] = None
    elapsed_seconds: int = 0
    estimated_remaining_seconds: Optional[int] = None
    progress_percent: float = 0
    phase_progress_percent: float = 0
    processed_units: int = 0
    total_units: int = 0


@router.get("/admin/usage_maintenance", response_model=UsageMaintenanceStatusDTO)
def read_usage_maintenance_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    status = get_usage_maintenance_status()
    next_run = get_usage_maintenance_next_run()
    schedule_enabled = load_auto_maintenance_settings(db)["usage_maintenance_auto_enabled"]
    status["next_scheduled_run"] = next_run.isoformat() if (next_run and schedule_enabled) else None
    status["last_auto_run"] = get_last_auto_run(db)
    return UsageMaintenanceStatusDTO(**status)


@router.post("/admin/usage_maintenance/run", response_model=UsageMaintenanceStatusDTO, status_code=202)
def run_usage_maintenance(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    if is_backup_running(db):
        raise HTTPException(status_code=409, detail="A manual backup is running. Wait for it to finish before starting maintenance.")
    try:
        started, status = start_usage_maintenance()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not started:
        raise HTTPException(status_code=409, detail="Usage maintenance is already running")
    return UsageMaintenanceStatusDTO(**status)


@router.post("/admin/usage_maintenance/cancel", response_model=UsageMaintenanceStatusDTO)
def cancel_running_usage_maintenance(
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    return UsageMaintenanceStatusDTO(**cancel_usage_maintenance())


class BackupStatusDTO(BaseModel):
    running: bool
    phase: str
    phase_label: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_error: Optional[str] = None
    detail: Optional[str] = None
    file_size: Optional[int] = None
    download_token: Optional[str] = None
    download_filename: Optional[str] = None
    secret_key: Optional[str] = None
    elapsed_seconds: int = 0
    progress_percent: float = 0


class BackupRestoreResultDTO(BaseModel):
    ok: bool = True
    message: str
    pre_restore_backup: Optional[str] = None


@router.get("/admin/backup", response_model=BackupStatusDTO)
def read_backup_status(
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    return BackupStatusDTO(**get_backup_status())


@router.post("/admin/backup/run", response_model=BackupStatusDTO, status_code=202)
def run_manual_backup(
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    try:
        started, status = start_backup()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not started:
        raise HTTPException(status_code=409, detail="A backup is already running")
    return BackupStatusDTO(**status)


@router.get("/admin/backup/download")
def download_manual_backup(
    token: str = Query(..., min_length=8),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    try:
        path, filename = resolve_backup_download(token)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/admin/backup/restore", response_model=BackupRestoreResultDTO)
async def restore_manual_backup(
    file: UploadFile = File(...),
    key: str = Form(...),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="Backup file is empty")
    try:
        result = restore_backup_from_upload(payload, key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return BackupRestoreResultDTO(**result)


@router.post("/admin/purge_usage")
def purge_usage(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Delete all usage samples and rollups, keep peers/routers/settings."""
    _require_admin(current_user)
    deleted_samples = db.query(UsageSample).delete()
    deleted_minutes = db.query(UsageMinute).delete()
    deleted_daily = db.query(UsageDaily).delete()
    deleted_monthly = db.query(UsageMonthly).delete()
    db.commit()
    return {
        "ok": True,
        "deleted_samples": deleted_samples,
        "deleted_minutes": deleted_minutes,
        "deleted_daily": deleted_daily,
        "deleted_monthly": deleted_monthly,
    }


@router.post("/admin/purge_peers")
def purge_peers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Delete all peers (and cascading usage/quotas), keep routers/settings."""
    _require_admin(current_user)
    deleted_peers = db.query(Peer).delete()
    # Cascades will remove usage + quotas via FK; ensure rollups are cleared
    db.query(UsageSample).delete()
    db.query(UsageMinute).delete()
    db.query(UsageDaily).delete()
    db.query(UsageMonthly).delete()
    db.query(Quota).delete()
    db.commit()
    return {"ok": True, "deleted_peers": deleted_peers}


class DashboardMetricsDTO(BaseModel):
    cpu_percent: float
    ram_percent: float
    disk_percent: float
    uptime_seconds: int


@router.get("/dashboard/metrics", response_model=DashboardMetricsDTO)
def get_dashboard_metrics(db: Session = Depends(get_db)):
    # Placeholder: In a real app, use psutil to get system stats.
    # For now, return standard healthy values to prevent 404s.
    return DashboardMetricsDTO(
        cpu_percent=10.5,
        ram_percent=45.2,
        disk_percent=60.0,
        uptime_seconds=3600
    )


# ── Fair Usage ───────────────────────────────────────────────────────────

def _validate_fair_usage_scope(count: int, unit: str) -> None:
    u = (unit or "").lower().strip()
    if u not in ("hour", "day", "week", "month"):
        raise HTTPException(status_code=400, detail="scope_period_unit must be hour, day, week, or month")
    if count < 1:
        raise HTTPException(status_code=400, detail="scope_period_count must be >= 1")
    cap = SCOPE_UNIT_MAX.get(u, 24)
    if count > cap:
        raise HTTPException(status_code=400, detail=f"scope_period_count for {u} must be <= {cap}")


def _apply_legacy_time_scope_to_scope(
    scope_count: int,
    scope_unit: str,
    legacy_time_scope: Optional[str],
) -> tuple[int, str]:
    """If legacy time_scope is set (hourly/daily/...), map to period fields."""
    if not legacy_time_scope:
        return scope_count, scope_unit
    m = {"hourly": ("hour", 1), "daily": ("day", 1), "weekly": ("week", 1), "monthly": ("month", 1)}
    if legacy_time_scope in m:
        u, c = m[legacy_time_scope]
        return c, u
    return scope_count, scope_unit


class FairUsageTierInputDTO(BaseModel):
    threshold_bytes: int
    name: str = ""
    throttle_download_kbps: int = 1000
    throttle_upload_kbps: int = 1000
    sort_order: int = 0


class FairUsageTierDTO(BaseModel):
    id: int
    sort_order: int
    threshold_bytes: int
    name: str = ""
    throttle_download_kbps: int
    throttle_upload_kbps: int


def _validate_tier_list(tiers: List[FairUsageTierInputDTO]) -> None:
    if not tiers:
        raise HTTPException(status_code=400, detail="tiered rules require at least one tier")
    ths = [t.threshold_bytes for t in tiers]
    if any(x < 0 for x in ths):
        raise HTTPException(status_code=400, detail="tier thresholds must be >= 0")
    if len(set(ths)) != len(ths):
        raise HTTPException(status_code=400, detail="duplicate tier thresholds are not allowed")


def _persist_rule_tiers(db: Session, rule_id: int, tiered: bool, tiers_in: Optional[List[FairUsageTierInputDTO]]) -> None:
    if tiered and tiers_in:
        _validate_tier_list(tiers_in)
        ordered = sorted(tiers_in, key=lambda x: (x.threshold_bytes, x.sort_order))
        rows = [
            (i, t.threshold_bytes, t.name or "", t.throttle_download_kbps, t.throttle_upload_kbps)
            for i, t in enumerate(ordered)
        ]
        replace_rule_tiers(db, rule_id, rows)
    else:
        db.query(FairUsageTier).filter(FairUsageTier.rule_id == rule_id).delete()


def _mirror_rule_caps_from_tiers(rule: FairUsageRule, tiers_in: List[FairUsageTierInputDTO]) -> None:
    ordered = sorted(tiers_in, key=lambda x: x.threshold_bytes)
    top = ordered[-1]
    rule.download_quota_bytes = top.threshold_bytes
    rule.throttle_download_kbps = top.throttle_download_kbps
    rule.throttle_upload_kbps = top.throttle_upload_kbps


class FairUsageRuleCreateDTO(BaseModel):
    name: str
    description: str = ""
    quota_mode: str = "combined"  # combined | independent
    download_quota_bytes: int = 0
    upload_quota_bytes: Optional[int] = None
    throttle_download_kbps: int = 1000
    throttle_upload_kbps: int = 1000
    scope_period_count: int = 1
    scope_period_unit: str = "month"  # hour | day | week | month
    time_scope: Optional[str] = None  # deprecated; hourly/daily/weekly/monthly maps to period=1
    scope_type: str = "global"  # global | router | peer
    router_id: Optional[int] = None
    peer_ids: Optional[List[int]] = None
    sort_order: int = 0
    passthrough: bool = False
    enabled: bool = True
    #: When True with non-empty ``tiers``, one combined-usage ladder per period (soft → hard on one meter).
    tiered: bool = False
    tiers: Optional[List[FairUsageTierInputDTO]] = None


class FairUsageRuleUpdateDTO(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    quota_mode: Optional[str] = None
    download_quota_bytes: Optional[int] = None
    upload_quota_bytes: Optional[int] = None
    throttle_download_kbps: Optional[int] = None
    throttle_upload_kbps: Optional[int] = None
    scope_period_count: Optional[int] = None
    scope_period_unit: Optional[str] = None
    time_scope: Optional[str] = None
    scope_type: Optional[str] = None
    router_id: Optional[int] = None
    peer_ids: Optional[List[int]] = None
    sort_order: Optional[int] = None
    passthrough: Optional[bool] = None
    enabled: Optional[bool] = None
    tiered: Optional[bool] = None
    tiers: Optional[List[FairUsageTierInputDTO]] = None


class FairUsageAssignedPeerDTO(BaseModel):
    peer_id: int
    name: str
    allowed_address: str
    router_id: int
    disabled: bool


class FairUsageRuleDTO(BaseModel):
    id: int
    name: str
    description: str
    quota_mode: str
    download_quota_bytes: int
    upload_quota_bytes: Optional[int]
    throttle_download_kbps: int
    throttle_upload_kbps: int
    time_scope: str
    scope_period_count: int = 1
    scope_period_unit: str = "month"
    scope_label: str = "Monthly"
    scope_type: str
    router_id: Optional[int]
    sort_order: int = 0
    passthrough: bool = False
    enabled: bool
    tiered: bool = False
    tiers: List[FairUsageTierDTO] = []
    created_at: str
    updated_at: str
    assigned_peer_count: int = 0
    assigned_peers: List[FairUsageAssignedPeerDTO] = []


def _fu_rule_to_dto(rule: FairUsageRule, db: Session, include_peers: bool = False) -> FairUsageRuleDTO:
    assignments = (
        db.query(FairUsageAssignment)
        .join(Peer, Peer.id == FairUsageAssignment.peer_id)
        .filter(FairUsageAssignment.rule_id == rule.id, Peer.selected == True)
        .all()
    )
    peers_out: List[FairUsageAssignedPeerDTO] = []
    if include_peers and assignments:
        peer_ids = [a.peer_id for a in assignments]
        peers = db.query(Peer).filter(Peer.id.in_(peer_ids), Peer.selected == True).all()
        for p in peers:
            peers_out.append(FairUsageAssignedPeerDTO(
                peer_id=p.id, name=p.name, allowed_address=p.allowed_address,
                router_id=p.router_id, disabled=p.disabled,
            ))
    cnt, unit = normalize_scope_period(rule)
    tier_out: List[FairUsageTierDTO] = []
    if rule.tiered:
        for t in ordered_tiers_for_rule(db, rule.id):
            tier_out.append(
                FairUsageTierDTO(
                    id=t.id,
                    sort_order=t.sort_order,
                    threshold_bytes=t.threshold_bytes,
                    name=t.name or "",
                    throttle_download_kbps=t.throttle_download_kbps,
                    throttle_upload_kbps=t.throttle_upload_kbps,
                )
            )
    return FairUsageRuleDTO(
        id=rule.id,
        name=rule.name,
        description=rule.description or "",
        quota_mode=rule.quota_mode,
        download_quota_bytes=rule.download_quota_bytes,
        upload_quota_bytes=rule.upload_quota_bytes,
        throttle_download_kbps=rule.throttle_download_kbps,
        throttle_upload_kbps=rule.throttle_upload_kbps,
        time_scope=rule.time_scope,
        scope_period_count=cnt,
        scope_period_unit=unit,
        scope_label=format_scope_label(cnt, unit),
        scope_type=rule.scope_type,
        router_id=rule.router_id,
        sort_order=rule.sort_order,
        passthrough=rule.passthrough,
        enabled=rule.enabled,
        tiered=rule.tiered,
        tiers=tier_out,
        created_at=rule.created_at.replace(tzinfo=timezone.utc).isoformat() if rule.created_at else "",
        updated_at=rule.updated_at.replace(tzinfo=timezone.utc).isoformat() if rule.updated_at else "",
        assigned_peer_count=len(assignments),
        assigned_peers=peers_out,
    )


def _replace_fair_usage_assignments(db: Session, rule_id: int, peer_ids: Optional[List[int]]) -> None:
    db.query(FairUsageAssignment).filter(FairUsageAssignment.rule_id == rule_id).delete(synchronize_session=False)
    seen: set[int] = set()
    for pid in peer_ids or []:
        if pid in seen:
            continue
        seen.add(pid)
        peer = db.get(Peer, pid)
        if not peer or not peer.selected:
            continue
        db.add(FairUsageAssignment(rule_id=rule_id, peer_id=pid))


@router.post("/fair-usage/rules", response_model=FairUsageRuleDTO)
def create_fair_usage_rule(dto: FairUsageRuleCreateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if dto.quota_mode not in ("combined", "independent"):
        raise HTTPException(status_code=400, detail="quota_mode must be 'combined' or 'independent'")
    if dto.scope_type not in ("global", "router", "peer"):
        raise HTTPException(status_code=400, detail="scope_type must be 'global', 'router', or 'peer'")
    if dto.scope_type == "router" and not dto.router_id:
        raise HTTPException(status_code=400, detail="router_id is required for router-scoped rules")

    sc, su = _apply_legacy_time_scope_to_scope(dto.scope_period_count, dto.scope_period_unit, dto.time_scope)
    _validate_fair_usage_scope(sc, su)

    want_tiered = bool(dto.tiered and dto.tiers and len(dto.tiers) >= 1)
    if want_tiered:
        if dto.quota_mode != "combined":
            raise HTTPException(status_code=400, detail="tiered rules require combined quota mode")
        _validate_tier_list(dto.tiers)
        top = max(dto.tiers, key=lambda x: x.threshold_bytes)
        dl_bytes = top.threshold_bytes
        qm = "combined"
        up_bytes = None
        tdl, tul = top.throttle_download_kbps, top.throttle_upload_kbps
    else:
        dl_bytes = dto.download_quota_bytes
        qm = dto.quota_mode
        up_bytes = dto.upload_quota_bytes if dto.quota_mode == "independent" else None
        tdl, tul = dto.throttle_download_kbps, dto.throttle_upload_kbps

    rule = FairUsageRule(
        name=dto.name,
        description=dto.description,
        quota_mode=qm,
        download_quota_bytes=dl_bytes,
        upload_quota_bytes=up_bytes,
        throttle_download_kbps=tdl,
        throttle_upload_kbps=tul,
        scope_period_count=sc,
        scope_period_unit=su,
        time_scope="monthly",
        scope_type=dto.scope_type,
        router_id=dto.router_id if dto.scope_type == "router" else None,
        sort_order=dto.sort_order,
        passthrough=dto.passthrough,
        enabled=dto.enabled,
        tiered=want_tiered,
    )
    sync_legacy_time_scope_field(rule)
    db.add(rule)
    db.flush()
    _persist_rule_tiers(db, rule.id, want_tiered, dto.tiers if want_tiered else None)

    if dto.scope_type == "peer" and dto.peer_ids:
        _replace_fair_usage_assignments(db, rule.id, dto.peer_ids)
    db.commit()
    db.refresh(rule)
    return _fu_rule_to_dto(rule, db, include_peers=True)


@router.get("/fair-usage/rules", response_model=List[FairUsageRuleDTO])
def list_fair_usage_rules(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rules = db.query(FairUsageRule).order_by(FairUsageRule.sort_order.asc(), FairUsageRule.id.asc()).all()
    return [_fu_rule_to_dto(r, db, include_peers=True) for r in rules]


@router.get("/fair-usage/rules/{rule_id}", response_model=FairUsageRuleDTO)
def get_fair_usage_rule(rule_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rule = db.get(FairUsageRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="rule not found")
    return _fu_rule_to_dto(rule, db, include_peers=True)


@router.put("/fair-usage/rules/{rule_id}", response_model=FairUsageRuleDTO)
def update_fair_usage_rule(rule_id: int, dto: FairUsageRuleUpdateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rule = db.get(FairUsageRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="rule not found")
    fields_set = getattr(dto, "model_fields_set", set())

    if dto.name is not None:
        rule.name = dto.name
    if dto.description is not None:
        rule.description = dto.description
    if dto.quota_mode is not None:
        if dto.quota_mode not in ("combined", "independent"):
            raise HTTPException(status_code=400, detail="quota_mode must be 'combined' or 'independent'")
        if rule.tiered and dto.quota_mode != "combined":
            raise HTTPException(status_code=400, detail="tiered rules must use combined quota mode")
        rule.quota_mode = dto.quota_mode
    if dto.download_quota_bytes is not None:
        rule.download_quota_bytes = dto.download_quota_bytes
    if dto.upload_quota_bytes is not None:
        rule.upload_quota_bytes = dto.upload_quota_bytes
    if dto.throttle_download_kbps is not None:
        rule.throttle_download_kbps = dto.throttle_download_kbps
    if dto.throttle_upload_kbps is not None:
        rule.throttle_upload_kbps = dto.throttle_upload_kbps
    if dto.scope_period_count is not None:
        rule.scope_period_count = dto.scope_period_count
    if dto.scope_period_unit is not None:
        rule.scope_period_unit = dto.scope_period_unit
    if dto.time_scope is not None:
        sc, su = _apply_legacy_time_scope_to_scope(rule.scope_period_count, rule.scope_period_unit, dto.time_scope)
        rule.scope_period_count = sc
        rule.scope_period_unit = su
    _validate_fair_usage_scope(rule.scope_period_count, rule.scope_period_unit)
    sync_legacy_time_scope_field(rule)
    if dto.scope_type is not None:
        if dto.scope_type not in ("global", "router", "peer"):
            raise HTTPException(status_code=400, detail="scope_type must be 'global', 'router', or 'peer'")
        rule.scope_type = dto.scope_type
    if "router_id" in fields_set:
        rule.router_id = dto.router_id
    if rule.scope_type == "router" and not rule.router_id:
        raise HTTPException(status_code=400, detail="router_id is required for router-scoped rules")
    if rule.scope_type != "router":
        rule.router_id = None
    if "peer_ids" in fields_set:
        _replace_fair_usage_assignments(db, rule.id, dto.peer_ids if rule.scope_type == "peer" else [])
    if dto.sort_order is not None:
        rule.sort_order = dto.sort_order
    if dto.passthrough is not None:
        rule.passthrough = dto.passthrough
    if dto.enabled is not None:
        rule.enabled = dto.enabled

    if dto.tiers is not None:
        if len(dto.tiers) >= 1:
            qm = dto.quota_mode if dto.quota_mode is not None else rule.quota_mode
            if qm != "combined":
                raise HTTPException(status_code=400, detail="tiered rules require combined quota mode")
            rule.tiered = True
            rule.quota_mode = "combined"
            rule.upload_quota_bytes = None
            _mirror_rule_caps_from_tiers(rule, dto.tiers)
            _persist_rule_tiers(db, rule.id, True, dto.tiers)
        else:
            rule.tiered = False
            _persist_rule_tiers(db, rule.id, False, None)
    elif dto.tiered is False:
        rule.tiered = False
        _persist_rule_tiers(db, rule.id, False, None)

    rule.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(rule)
    return _fu_rule_to_dto(rule, db, include_peers=True)


@router.delete("/fair-usage/rules/{rule_id}")
def delete_fair_usage_rule(rule_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rule = db.get(FairUsageRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="rule not found")
    # Clean up any active throttle queues on RouterOS
    states = db.query(FairUsageState).filter(FairUsageState.rule_id == rule_id).all()
    for st in states:
        if st.throttled and st.ros_queue_id:
            peer = db.get(Peer, st.peer_id)
            if peer:
                r = db.get(Router, peer.router_id)
                if r:
                    try:
                        client = make_client(r)
                        client.remove_simple_queue(st.ros_queue_id)
                    except Exception:
                        pass
        db.delete(st)
    db.delete(rule)
    db.commit()
    return {"ok": True}


class FairUsageAssignDTO(BaseModel):
    peer_ids: List[int]


@router.post("/fair-usage/rules/{rule_id}/assign", response_model=FairUsageRuleDTO)
def assign_peers_to_rule(rule_id: int, dto: FairUsageAssignDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rule = db.get(FairUsageRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="rule not found")
    for pid in dto.peer_ids:
        peer = db.get(Peer, pid)
        if not peer or not peer.selected:
            continue
        existing = (
            db.query(FairUsageAssignment)
            .filter(FairUsageAssignment.rule_id == rule_id, FairUsageAssignment.peer_id == pid)
            .first()
        )
        if not existing:
            db.add(FairUsageAssignment(rule_id=rule_id, peer_id=pid))
    db.commit()
    db.refresh(rule)
    return _fu_rule_to_dto(rule, db, include_peers=True)


@router.delete("/fair-usage/rules/{rule_id}/assign/{peer_id}")
def unassign_peer_from_rule(rule_id: int, peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    assignment = (
        db.query(FairUsageAssignment)
        .filter(FairUsageAssignment.rule_id == rule_id, FairUsageAssignment.peer_id == peer_id)
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="assignment not found")
    # If peer is currently throttled by this rule, remove queue
    state = db.query(FairUsageState).filter(FairUsageState.peer_id == peer_id, FairUsageState.rule_id == rule_id).first()
    if state and state.throttled and state.ros_queue_id:
        peer = db.get(Peer, peer_id)
        if peer:
            r = db.get(Router, peer.router_id)
            if r:
                try:
                    client = make_client(r)
                    client.remove_simple_queue(state.ros_queue_id)
                except Exception:
                    pass
        db.delete(state)
    db.delete(assignment)
    db.commit()
    return {"ok": True}


@router.get("/fair-usage/peers/{peer_id}/status", response_model=FairUsagePeerStatusDTO)
def get_fair_usage_peer_status(peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    peer = db.get(Peer, peer_id)
    if not peer:
        raise HTTPException(status_code=404, detail="peer not found")
    return build_fair_usage_peer_status_dto(db, peer)


@router.post("/fair-usage/peers/{peer_id}/reset")
def reset_fair_usage_peer(peer_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Manually remove throttle for a peer."""
    peer = db.get(Peer, peer_id)
    if not peer:
        raise HTTPException(status_code=404, detail="peer not found")
    state = db.query(FairUsageState).filter(FairUsageState.peer_id == peer_id).first()
    if not state:
        return {"ok": True, "was_throttled": False}
    was_throttled = state.throttled
    if state.throttled and state.ros_queue_id:
        r = db.get(Router, peer.router_id)
        if r:
            try:
                client = make_client(r)
                client.remove_simple_queue(state.ros_queue_id)
            except Exception:
                pass
    db.delete(state)
    now_utc = datetime.now(timezone.utc)
    db.add(Action(peer_id=peer_id, ts=now_utc, action="fu_manual_reset", note="Manual fair-usage reset"))
    db.commit()
    return {"ok": True, "was_throttled": was_throttled}


# ── Telegram bot management ─────────────────────────────────────────

from ..models import TelegramUser, TelegramPeerBinding, TelegramSignupToken, TelegramNotificationConfig
from ..telegram.notifications import USER_NOTIFICATION_EVENT_TYPES, effective_user_notification_enabled


class TelegramConfigPayload(BaseModel):
    tg_bot_token: Optional[str] = None
    tg_bot_enabled: Optional[bool] = None
    tg_admin_chat_id: Optional[str] = None
    tg_bot_language: Optional[str] = None


@router.get("/telegram/config")
def get_telegram_config(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..telegram.bot import _decrypt_token

    keys = ("tg_bot_token", "tg_bot_enabled", "tg_admin_chat_id", "tg_bot_language")
    result = {}
    for k in keys:
        kv = db.get(SettingsKV, k)
        v = kv.value if kv else ""
        if k == "tg_bot_token" and v:
            v = _decrypt_token(v)
        result[k] = v
    return result


@router.put("/telegram/config")
def update_telegram_config(payload: TelegramConfigPayload, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    box = SecretBox(settings.secret_key)
    token_changed = False
    for field, key in [
        ("tg_bot_token", "tg_bot_token"),
        ("tg_bot_enabled", "tg_bot_enabled"),
        ("tg_admin_chat_id", "tg_admin_chat_id"),
        ("tg_bot_language", "tg_bot_language"),
    ]:
        val = getattr(payload, field, None)
        if val is None:
            continue
        str_val = str(val)
        if key == "tg_bot_token" and str_val:
            str_val = box.encrypt(str_val)
            token_changed = True
        elif key == "tg_bot_enabled":
            str_val = "true" if val else "false"
        kv = db.get(SettingsKV, key)
        if kv:
            kv.value = str_val
        else:
            db.add(SettingsKV(key=key, value=str_val))
    db.commit()

    if token_changed or payload.tg_bot_enabled is not None:
        from ..telegram.bot import restart_bot
        restart_bot()

    return {"ok": True}


@router.get("/telegram/status")
def get_telegram_status(current_user: User = Depends(get_current_user)):
    from ..telegram.bot import get_bot_status
    return get_bot_status()


@router.post("/telegram/restart")
def restart_telegram_bot(current_user: User = Depends(get_current_user)):
    from ..telegram.bot import restart_bot
    started = restart_bot()
    return {"ok": True, "started": started}


class TokenCreatePayload(BaseModel):
    peer_ids: List[int]
    expires_hours: Optional[int] = None
    single_use: bool = True


@router.post("/telegram/tokens")
def create_signup_token(payload: TokenCreatePayload, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..telegram.tokens import generate_token
    expires_at = None
    if payload.expires_hours and payload.expires_hours > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(hours=payload.expires_hours)
    tok = generate_token(db, payload.peer_ids, current_user.id, expires_at, payload.single_use)
    db.commit()

    # Try to get bot username for deep link
    bot_username = ""
    try:
        from ..telegram.bot import _get_tg_settings, _decrypt_token
        import asyncio as _aio
        cfg = _get_tg_settings()
        _token = _decrypt_token(cfg.get("tg_bot_token", ""))
        if _token and len(_token) >= 20:
            from aiogram import Bot as _Bot
            async def _get_username():
                b = _Bot(token=_token)
                try:
                    me = await b.get_me()
                    return me.username or ""
                finally:
                    await b.session.close()
            bot_username = _aio.run(_get_username())
    except Exception:
        pass

    deep_link = f"https://t.me/{bot_username}?start={tok.token}" if bot_username else ""
    return {
        "id": tok.id,
        "token": tok.token,
        "peer_ids": json.loads(tok.peer_ids),
        "deep_link": deep_link,
        "expires_at": tok.expires_at.isoformat() if tok.expires_at else None,
        "single_use": tok.single_use,
    }


@router.get("/telegram/tokens")
def list_signup_tokens(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tokens = db.query(TelegramSignupToken).order_by(TelegramSignupToken.created_at.desc()).all()
    result = []
    for tok in tokens:
        used_by_info = None
        if tok.used_by:
            tg_user = db.get(TelegramUser, tok.used_by)
            if tg_user:
                used_by_info = {
                    "telegram_username": tg_user.telegram_username,
                    "first_name": tg_user.first_name,
                }
        result.append({
            "id": tok.id,
            "token": tok.token,
            "peer_ids": json.loads(tok.peer_ids or "[]"),
            "created_at": tok.created_at.isoformat() if tok.created_at else None,
            "used_at": tok.used_at.isoformat() if tok.used_at else None,
            "used_by": used_by_info,
            "expires_at": tok.expires_at.isoformat() if tok.expires_at else None,
            "single_use": tok.single_use,
        })
    return result


@router.delete("/telegram/tokens/{token_id}")
def revoke_token(token_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    tok = db.get(TelegramSignupToken, token_id)
    if not tok:
        raise HTTPException(status_code=404, detail="token not found")
    db.delete(tok)
    db.commit()
    return {"ok": True}


@router.get("/telegram/users")
def list_telegram_users(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    users = db.query(TelegramUser).order_by(TelegramUser.created_at.desc()).all()
    result = []
    for u in users:
        bindings = db.query(TelegramPeerBinding).filter_by(telegram_user_id=u.id).all()
        peer_info = []
        for b in bindings:
            peer = db.get(Peer, b.peer_id)
            rtr = db.get(Router, peer.router_id) if peer else None
            peer_info.append({
                "binding_id": b.id,
                "peer_id": b.peer_id,
                "peer_name": peer.name if peer else "?",
                "router_name": rtr.name if rtr else "?",
                "interface": peer.interface if peer else "?",
                "visible": b.visible,
            })
        result.append({
            "id": u.id,
            "telegram_user_id": u.telegram_user_id,
            "telegram_username": u.telegram_username,
            "first_name": u.first_name,
            "last_name": u.last_name,
            "language": u.language,
            "is_blocked": u.is_blocked,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "peers": peer_info,
            "subscribed_notifications": [
                event_type
                for event_type in USER_NOTIFICATION_EVENT_TYPES
                if effective_user_notification_enabled(db, u.id, event_type)
            ],
        })
    return result


@router.delete("/telegram/users/{tg_user_db_id}")
def delete_telegram_user(tg_user_db_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    u = db.get(TelegramUser, tg_user_db_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    db.delete(u)
    db.commit()
    return {"ok": True}


class TelegramUserPatch(BaseModel):
    is_blocked: Optional[bool] = None


@router.patch("/telegram/users/{tg_user_db_id}")
def patch_telegram_user(tg_user_db_id: int, payload: TelegramUserPatch, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    u = db.get(TelegramUser, tg_user_db_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    if payload.is_blocked is not None:
        u.is_blocked = payload.is_blocked
    db.commit()
    return {"ok": True}


class TelegramUserPeersPatch(BaseModel):
    peer_ids: List[int] = []
    default_visible: bool = True


@router.put("/telegram/users/{tg_user_db_id}/peers")
def set_telegram_user_peers(
    tg_user_db_id: int,
    payload: TelegramUserPeersPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    u = db.get(TelegramUser, tg_user_db_id)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")

    wanted: set[int] = set()
    for pid in payload.peer_ids:
        try:
            ipid = int(pid)
        except Exception:
            continue
        if ipid <= 0:
            continue
        if db.get(Peer, ipid) is None:
            continue
        wanted.add(ipid)

    existing = (
        db.query(TelegramPeerBinding)
        .filter(TelegramPeerBinding.telegram_user_id == u.id)
        .all()
    )
    by_peer = {b.peer_id: b for b in existing}

    for b in existing:
        if b.peer_id not in wanted:
            db.delete(b)

    for pid in wanted:
        if pid not in by_peer:
            db.add(
                TelegramPeerBinding(
                    telegram_user_id=u.id,
                    peer_id=pid,
                    visible=payload.default_visible,
                )
            )

    db.commit()
    return {"ok": True}


@router.get("/telegram/bindings")
def list_bindings(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    bindings = db.query(TelegramPeerBinding).all()
    result = []
    for b in bindings:
        tg_user = db.get(TelegramUser, b.telegram_user_id)
        peer = db.get(Peer, b.peer_id)
        result.append({
            "id": b.id,
            "telegram_username": tg_user.telegram_username if tg_user else "?",
            "peer_name": peer.name if peer else "?",
            "peer_id": b.peer_id,
            "visible": b.visible,
        })
    return result


class BindingPatch(BaseModel):
    visible: Optional[bool] = None


@router.patch("/telegram/bindings/{binding_id}")
def patch_binding(binding_id: int, payload: BindingPatch, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    b = db.get(TelegramPeerBinding, binding_id)
    if not b:
        raise HTTPException(status_code=404, detail="binding not found")
    if payload.visible is not None:
        b.visible = payload.visible
    db.commit()
    return {"ok": True}


@router.delete("/telegram/bindings/{binding_id}")
def delete_binding(binding_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    b = db.get(TelegramPeerBinding, binding_id)
    if not b:
        raise HTTPException(status_code=404, detail="binding not found")
    db.delete(b)
    db.commit()
    return {"ok": True}


@router.get("/telegram/notifications")
def get_notification_config(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    configs = db.query(TelegramNotificationConfig).all()
    return [
        {
            "id": c.id,
            "event_type": c.event_type,
            "notify_clients": c.notify_clients,
            "notify_admin": c.notify_admin,
            "enabled": c.enabled,
        }
        for c in configs
    ]


class NotifConfigUpdate(BaseModel):
    configs: List[dict]


@router.put("/telegram/notifications")
def update_notification_config(payload: NotifConfigUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    for item in payload.configs:
        cfg = db.query(TelegramNotificationConfig).filter_by(event_type=item.get("event_type")).first()
        if not cfg:
            continue
        if "notify_clients" in item:
            cfg.notify_clients = bool(item["notify_clients"])
        if "notify_admin" in item:
            cfg.notify_admin = bool(item["notify_admin"])
        if "enabled" in item:
            cfg.enabled = bool(item["enabled"])
    db.commit()
    return {"ok": True}


@router.post("/telegram/test-notify")
def test_notify(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..telegram.notifications import _send_message_sync, _get_admin_chat_id
    admin_id = _get_admin_chat_id(db)
    if not admin_id:
        raise HTTPException(status_code=400, detail="Admin chat ID not configured")
    ok = _send_message_sync(admin_id, "WGMik test notification - bot is working!")
    if not ok:
        raise HTTPException(status_code=502, detail="Failed to send message. Check bot token and chat ID.")
    return {"ok": True}


@router.post("/telegram/test-notify/{event_type}")
def test_notify_event(event_type: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    from ..telegram.notifications import _get_admin_chat_id, _send_message_sync

    allowed = {
        "quota_warning_80",
        "quota_warning_90",
        "quota_hit",
        "quota_lifted",
        "daily_summary",
        "weekly_summary",
    }
    if event_type not in allowed:
        raise HTTPException(status_code=400, detail="unsupported event type")

    admin_id = _get_admin_chat_id(db)
    if not admin_id:
        raise HTTPException(status_code=400, detail="Admin chat ID not configured")

    sample_peer = "peer-test"
    sample_rule = "Daily"
    sample_used = "1.2 GB"
    sample_total = "4.0 GB"
    if event_type == "quota_warning_80":
        text = f"[Admin][Test] Warning: peer \"{sample_peer}\" — rule \"{sample_rule}\" — 80% of quota ({sample_used} / {sample_total})."
    elif event_type == "quota_warning_90":
        text = f"[Admin][Test] Warning: peer \"{sample_peer}\" — rule \"{sample_rule}\" — 90% of quota ({sample_used} / {sample_total})."
    elif event_type == "quota_hit":
        text = (
            "[Admin][Test] Throttle notification sample\n\n"
            f"Rule: {sample_rule}\n"
            "Tier: (example) Soft step\n"
            "⬇️ 2 Mbps · ⬆️ 1 Mbps\n"
            f"Usage: 98% ({sample_used} / {sample_total})"
        )
    elif event_type == "quota_lifted":
        text = f"[Admin][Test] Peer \"{sample_peer}\" is no longer throttled. Quota has been reset."
    elif event_type == "daily_summary":
        text = "[Admin][Test] Daily Summary\n  peer-test (@user): ↓1.2 GB ↑300 MB"
    else:  # weekly_summary
        text = "[Admin][Test] Weekly Summary\n  peer-test (@user): ↓6.8 GB ↑1.5 GB"

    ok = _send_message_sync(admin_id, text)
    if not ok:
        raise HTTPException(status_code=502, detail="Failed to send message. Check bot token and chat ID.")
    return {"ok": True}
