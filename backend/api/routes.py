from fastapi import APIRouter, Depends, HTTPException, Response, Request, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..db import get_db, Base, engine, SessionLocal
from ..settings import settings
from ..scheduler import update_scheduler_interval
from ..security import SecretBox
from ..models import Router, SettingsKV, Peer, UsageDaily, UsageMinute, UsageMonthly, UsageSample, Quota, Action, User, FairUsageRule, FairUsageAssignment, FairUsageState
from ..auth import verify_password, get_password_hash, create_access_token, verify_token, ACCESS_TOKEN_EXPIRE_MINUTES
from ..routeros.factory import make_client
from ..usage_maintenance import (
    get_usage_maintenance_status,
    is_usage_maintenance_running,
    start_usage_maintenance,
)
from ..fair_usage_sync import apply_fair_usage_policy, FairUsageRouterError, get_effective_fair_usage_rule, peer_ids_with_applicable_fair_usage
from ..fair_usage_usage import (
    SCOPE_UNIT_MAX,
    app_zoneinfo,
    compute_next_reset_utc_for_rule,
    format_scope_label,
    normalize_scope_period,
    peer_scope_usage_for_rule,
    sync_legacy_time_scope_field,
)
from ..usage_bucketing import aggregate_rows_to_local_buckets, aggregate_router_rows_to_local_buckets
from datetime import datetime, timedelta, timezone
from typing import List, Optional
import traceback
from ipaddress import ip_network
import os
from zoneinfo import ZoneInfo
import base64
import json

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
            peer_id,
            ts,
            rx,
            tx,
            LAG(rx) OVER (PARTITION BY peer_id ORDER BY ts) as prev_rx,
            LAG(tx) OVER (PARTITION BY peer_id ORDER BY ts) as prev_tx
        FROM filtered_samples
    )
    SELECT
        peer_id,
        SUM(CASE 
            WHEN prev_rx IS NULL THEN 0
            WHEN rx < prev_rx THEN rx
            ELSE rx - prev_rx 
        END) as total_rx,
        SUM(CASE 
            WHEN prev_tx IS NULL THEN 0
            WHEN tx < prev_tx THEN tx
            ELSE tx - prev_tx 
        END) as total_tx
    FROM deltas
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
            peer_id,
            ts,
            rx,
            tx,
            LAG(rx) OVER (PARTITION BY peer_id ORDER BY ts) as prev_rx,
            LAG(tx) OVER (PARTITION BY peer_id ORDER BY ts) as prev_tx
        FROM filtered_samples
    )
    SELECT
        ts,
        CASE
            WHEN rx < prev_rx THEN rx
            ELSE rx - prev_rx
        END as d_rx,
        CASE
            WHEN tx < prev_tx THEN tx
            ELSE tx - prev_tx
        END as d_tx
    FROM deltas
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
            router_id,
            peer_id,
            ts,
            rx,
            tx,
            LAG(rx) OVER (PARTITION BY peer_id ORDER BY ts) as prev_rx,
            LAG(tx) OVER (PARTITION BY peer_id ORDER BY ts) as prev_tx
        FROM filtered_samples
    )
    SELECT
        router_id,
        ts,
        CASE
            WHEN rx < prev_rx THEN rx
            ELSE rx - prev_rx
        END as d_rx,
        CASE
            WHEN tx < prev_tx THEN tx
            ELSE tx - prev_tx
        END as d_tx
    FROM deltas
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


def _fetch_all_router_peers(router_row: Router):
    client = make_client(router_row)
    return router_row.id, client.list_all_wireguard_peers()


# --- Authentication & Users ---

def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get("access_token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    username = verify_token(token)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(creds: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == creds.username).first()
    if not user or not verify_password(creds.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    
    # Set HTTP-only cookie
    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        samesite="lax",
        secure=False, # Set to True in production with HTTPS
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"ok": True}


class UserDTO(BaseModel):
    id: int
    username: str
    is_admin: bool
    created_at: datetime


@router.get("/auth/me", response_model=UserDTO)
def read_users_me(current_user: User = Depends(get_current_user)):
    return user_to_dto(current_user)


def user_to_dto(u: User) -> UserDTO:
    return UserDTO(
        id=u.id, 
        username=u.username, 
        is_admin=u.is_admin, 
        created_at=u.created_at
    )


class CreateUserRequest(BaseModel):
    username: str
    password: str


@router.post("/users", response_model=UserDTO)
def create_user(req: CreateUserRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    existing = db.query(User).filter(User.username == req.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    user = User(
        username=req.username,
        hashed_password=get_password_hash(req.password),
        is_admin=True # For now only creating admins
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user_to_dto(user)


@router.get("/users", response_model=List[UserDTO])
def list_users(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    users = db.query(User).all()
    return [user_to_dto(u) for u in users]


@router.delete("/users/{user_id}")
def delete_user(user_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Don't delete yourself
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
        
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    db.delete(user)
    db.commit()
    return {"ok": True}


# --- Existing Routes (Protected) ---


class SettingsDTO(BaseModel):
    poll_interval_seconds: int
    online_threshold_seconds: int
    monthly_reset_day: int
    timezone: str
    week_start_day: int
    show_kind_pills: bool
    show_hw_stats: bool
    dashboard_refresh_seconds: int
    peer_default_scope_unit: str
    peer_default_scope_value: int
    dashboard_scope_unit: str
    dashboard_scope_value: int
    dashboard_router_scope: str
    dashboard_selected_router_ids: List[int]
    dashboard_filter_status: str
    dashboard_sort_by: str
    peer_refresh_seconds: int
    raw_sample_retention_hours: int
    minute_rollup_retention_days: int
    daily_rollup_retention_days: int


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
        "week_start_day": 0,
        "show_kind_pills": True,
        "show_hw_stats": True,
        "dashboard_refresh_seconds": 30,
        "peer_default_scope_unit": "days",
        "peer_default_scope_value": 14,
        "dashboard_scope_unit": "days",
        "dashboard_scope_value": 14,
        "dashboard_router_scope": "all",
        "dashboard_selected_router_ids": [],
        "dashboard_filter_status": "all",
        "dashboard_sort_by": "created",
        "peer_refresh_seconds": 30,
        "raw_sample_retention_hours": 24,
        "minute_rollup_retention_days": 90,
        "daily_rollup_retention_days": 0,
    }
    # Overlay any values persisted in SettingsKV
    for key in (
        "week_start_day",
        "show_kind_pills",
        "show_hw_stats",
        "dashboard_refresh_seconds",
        "peer_default_scope_unit",
        "peer_default_scope_value",
        "dashboard_scope_unit",
        "dashboard_scope_value",
        "dashboard_router_scope",
        "dashboard_selected_router_ids",
        "dashboard_filter_status",
        "dashboard_sort_by",
        "peer_refresh_seconds",
        "raw_sample_retention_hours",
        "minute_rollup_retention_days",
        "daily_rollup_retention_days",
    ):
        kv = db.get(SettingsKV, key)
        if not kv:
            continue
        if key in (
            "week_start_day",
            "dashboard_refresh_seconds",
            "peer_default_scope_value",
            "dashboard_scope_value",
            "peer_refresh_seconds",
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
        elif key in ("show_kind_pills", "show_hw_stats"):
            data[key] = kv.value.lower() == "true"
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
    data["raw_sample_retention_hours"] = max(1, min(24 * 365, int(data["raw_sample_retention_hours"])))
    data["minute_rollup_retention_days"] = max(1, min(3650, int(data["minute_rollup_retention_days"])))
    data["daily_rollup_retention_days"] = max(0, min(36500, int(data["daily_rollup_retention_days"])))
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
        "monthly_reset_day": str(dto.monthly_reset_day),
        "timezone": dto.timezone,
        "week_start_day": str(max(0, min(6, int(dto.week_start_day)))),
        # UI preferences
        "show_kind_pills": str(dto.show_kind_pills).lower(),
        "show_hw_stats": str(dto.show_hw_stats).lower(),
        "dashboard_refresh_seconds": str(dto.dashboard_refresh_seconds),
        "peer_default_scope_unit": dto.peer_default_scope_unit,
        "peer_default_scope_value": str(dto.peer_default_scope_value),
        "dashboard_scope_unit": dto.dashboard_scope_unit,
        "dashboard_scope_value": str(dto.dashboard_scope_value),
        "dashboard_router_scope": dto.dashboard_router_scope,
        "dashboard_selected_router_ids": json.dumps(_normalize_router_ids(dto.dashboard_selected_router_ids) or []),
        "dashboard_filter_status": dto.dashboard_filter_status,
        "dashboard_sort_by": dto.dashboard_sort_by,
        "peer_refresh_seconds": str(dto.peer_refresh_seconds),
        "raw_sample_retention_hours": str(max(1, min(24 * 365, int(dto.raw_sample_retention_hours)))),
        "minute_rollup_retention_days": str(max(1, min(3650, int(dto.minute_rollup_retention_days)))),
        "daily_rollup_retention_days": str(max(0, min(36500, int(dto.daily_rollup_retention_days)))),
    }

    for k, v in overrides.items():
        kv = db.get(SettingsKV, k)
        if not kv:
            kv = SettingsKV(key=k, value=v)
            db.add(kv)
        else:
            kv.value = v
    
    # Update runtime config for core logic (immediate effect without restart)
    settings.poll_interval_seconds = dto.poll_interval_seconds
    settings.online_threshold_seconds = dto.online_threshold_seconds
    settings.monthly_reset_day = dto.monthly_reset_day
    settings.timezone = dto.timezone
    
    # Hot-reload scheduler interval
    try:
        update_scheduler_interval(dto.poll_interval_seconds)
    except Exception:
        pass

    db.commit()
    return get_settings(db)


class RouterCreateDTO(BaseModel):
    name: str
    host: str
    proto: str  # rest | rest-http | api | api-plain
    port: int
    username: str
    password: str
    tls_verify: bool = True


class RouterUpdateDTO(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    proto: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    tls_verify: Optional[bool] = None


class RouterDTO(BaseModel):
    id: int
    name: str
    host: str
    proto: str
    port: int
    username: str
    tls_verify: bool

    class Config:
        from_attributes = True
class WGInterfaceDTO(BaseModel):
    name: str
    public_key: str
    listen_port: int
    public_host: str



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
    )
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
    db.commit()
    db.refresh(row)
    return row


@router.delete("/routers/{router_id}")
def delete_router(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Router, router_id)
    if not row:
        raise HTTPException(status_code=404, detail="router not found")
    # Clear active router if it points to this router
    kv = db.get(SettingsKV, "active_router_id")
    if kv and kv.value.strip() == str(router_id):
        db.delete(kv)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/routers", response_model=list[RouterDTO])
def list_routers(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(Router).all()


class ActiveRouterDTO(BaseModel):
    router_id: Optional[int] = None


@router.get("/active_router", response_model=ActiveRouterDTO)
def get_active_router(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    kv = db.get(SettingsKV, "active_router_id")
    if not kv or not kv.value.strip():
        return ActiveRouterDTO(router_id=None)
    try:
        rid = int(kv.value.strip())
        if rid <= 0:
            return ActiveRouterDTO(router_id=None)
        return ActiveRouterDTO(router_id=rid)
    except ValueError:
        return ActiveRouterDTO(router_id=None)


@router.post("/active_router", response_model=ActiveRouterDTO)
def set_active_router(dto: ActiveRouterDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if dto.router_id is None:
        kv = db.get(SettingsKV, "active_router_id")
        if kv:
            db.delete(kv)
            db.commit()
        return ActiveRouterDTO(router_id=None)

    rid = int(dto.router_id)
    if rid <= 0:
        raise HTTPException(status_code=400, detail="router_id must be positive")
    router = db.get(Router, rid)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")

    kv = db.get(SettingsKV, "active_router_id")
    if kv is None:
        kv = SettingsKV(key="active_router_id", value=str(rid))
        db.add(kv)
    else:
        kv.value = str(rid)
    db.commit()
    return ActiveRouterDTO(router_id=rid)


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
        return WGInterfaceDTO(name=cfg.name, public_key=cfg.public_key, listen_port=cfg.listen_port, public_host=host)
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

    routers = db.query(Router).filter(Router.id.in_(list(peer_lookup.keys()))).all()
    if not routers:
        return []

    out: list[DashboardLiveStatusDTO] = []
    max_workers = min(max(len(routers), 1), 4)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_fetch_all_router_peers, router_row): router_row.id
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
            comment="",
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
    comment: Optional[str] = ""
    private_key: Optional[str] = None


def _is_valid_wg_private_key_b64(s: str) -> bool:
    try:
        raw = base64.b64decode(s.encode("utf-8"), validate=True)
        return len(raw) == 32
    except Exception:
        return False


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
                private_key=(dto.private_key.strip() if dto.private_key else None),
                allowed_address=dto.allowed_address,
                name=dto.name or "",
                comment=dto.comment or "",
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
        comment=dto.comment or "",
        disabled=disabled,
        selected=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)

    # Optional: store client private key encrypted in DB (RouterOS doesn't store peer private keys).
    if dto.private_key is not None:
        pk = dto.private_key.strip()
        if pk == "":
            kv = db.get(SettingsKV, f"peer_private_key:{row.id}")
            if kv:
                db.delete(kv)
                db.commit()
        else:
            if not _is_valid_wg_private_key_b64(pk):
                raise HTTPException(status_code=400, detail="invalid private_key (must be base64 32 bytes)")
            box = SecretBox(settings.secret_key)
            token = box.encrypt(pk)
            kv = db.get(SettingsKV, f"peer_private_key:{row.id}") or SettingsKV(key=f"peer_private_key:{row.id}", value="")
            kv.value = token
            db.add(kv)
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

    if not _is_valid_wg_private_key_b64(pk):
        raise HTTPException(status_code=400, detail="invalid private_key (must be base64 32 bytes)")
    box = SecretBox(settings.secret_key)
    token = box.encrypt(pk)
    kv = db.get(SettingsKV, f"peer_private_key:{peer_id}") or SettingsKV(key=f"peer_private_key:{peer_id}", value="")
    kv.value = token
    db.add(kv)
    db.commit()
    return PeerPrivateKeyDTO(private_key=pk)


@router.post("/routers/{router_id}/test")
def test_router_connection(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        _ = client.list_wireguard_interfaces()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")


class RouterSyncResultDTO(BaseModel):
    ok: bool
    router_id: int
    interfaces: list[str]
    updated: int
    created: int
    missing: int


@router.post("/routers/{router_id}/sync", response_model=RouterSyncResultDTO)
def sync_router(router_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """
    One-shot DB ↔ RouterOS reconciliation for a router.
    - Updates existing DB peers (ros_id/name/allowed_address/disabled) to match RouterOS
    - Discovers new RouterOS peers into DB (selected=false)
    - Marks DB peers missing on RouterOS as selected=false
    """
    router = db.get(Router, router_id)
    if not router:
        raise HTTPException(status_code=404, detail="router not found")
    client = make_client(router)
    try:
        ifaces = client.list_wireguard_interfaces()
        live_rows = client.list_all_wireguard_peers()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"router connection failed: {e}")

    # Prefetch DB peers for this router
    db_peers = db.query(Peer).filter(Peer.router_id == router_id).all()
    by_key: dict[tuple[str, str], Peer] = {(p.interface, p.public_key): p for p in db_peers}

    seen: set[tuple[str, str]] = set()
    updated = 0
    created = 0
    now_utc = datetime.now(timezone.utc)

    for lp in live_rows:
        key = (lp.interface, lp.public_key)
        seen.add(key)
        row = by_key.get(key)
        if row:
            changed = False
            if lp.ros_id and row.ros_id != lp.ros_id:
                row.ros_id = lp.ros_id
                changed = True
            if (row.name or "") != (lp.name or ""):
                row.name = lp.name or ""
                changed = True
            if (row.allowed_address or "") != (lp.allowed_address or ""):
                row.allowed_address = lp.allowed_address or ""
                changed = True
            if bool(row.disabled) != bool(lp.disabled):
                row.disabled = bool(lp.disabled)
                changed = True
                db.add(
                    Action(
                        peer_id=row.id,
                        ts=now_utc,
                        action="router_disable" if row.disabled else "router_enable",
                        note="Detected router state change during sync",
                    )
                )
            if changed:
                updated += 1
        else:
            disabled = lp.disabled
            if isinstance(disabled, str):
                disabled = disabled.strip().lower() in ("1", "true", "yes", "on", "enabled")
            row = Peer(
                router_id=router_id,
                interface=lp.interface,
                ros_id=lp.ros_id or "",
                name=lp.name or "",
                public_key=lp.public_key,
                allowed_address=lp.allowed_address,
                comment="",
                disabled=bool(disabled),
                selected=False,
            )
            db.add(row)
            db.flush()
            by_key[key] = row
            created += 1
            db.add(
                Action(
                    peer_id=row.id,
                    ts=now_utc,
                    action="router_discovered",
                    note="Discovered on router during sync (selected=false)",
                )
            )

    # Mark missing peers as unselected (keeps history)
    missing = 0
    for row in db_peers:
        k = (row.interface, row.public_key)
        if row.interface in ifaces and k not in seen:
            if row.selected:
                row.selected = False
                missing += 1
                db.add(
                    Action(
                        peer_id=row.id,
                        ts=now_utc,
                        action="router_missing",
                        note=f"Peer not found on router during sync; unselected",
                    )
                )

    db.commit()
    return RouterSyncResultDTO(
        ok=True,
        router_id=router_id,
        interfaces=ifaces,
        updated=updated,
        created=created,
        missing=missing,
    )


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


@router.patch("/peers/{peer_id}", response_model=PeerListDTO)
def update_peer(peer_id: int, dto: PeerUpdateDTO, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = db.get(Peer, peer_id)
    if not row:
        raise HTTPException(status_code=404, detail="peer not found")
    if dto.selected is not None:
        row.selected = dto.selected
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
    # If it's a RouterOS-backed peer, delete it on the router first (unless skipped).
    r = db.get(Router, row.router_id)
    if r and row.ros_id and not skip_router:
        client = make_client(r)
        try:
            client.remove_wireguard_peer(row.interface, row.ros_id)
            router_deleted = True
        except Exception as e:
            # Don't lie: if router delete failed, keep DB record.
            raise HTTPException(status_code=502, detail=f"router delete failed: {e}")
            
    # Remove any stored client secret material for this peer.
    kv = db.get(SettingsKV, f"peer_private_key:{peer_id}")
    if kv:
        db.delete(kv)

    db.delete(row)
    db.commit()
    return {"ok": True, "deleted_peer_id": peer_id, "router_deleted": router_deleted}


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
        for s in samples:
            if prev is None:
                prev = s
                continue
            drx = s.rx if s.rx < prev.rx else s.rx - prev.rx
            dtx = s.tx if s.tx < prev.tx else s.tx - prev.tx
            prev = s
            if drx <= 0 and dtx <= 0:
                continue
            ts_naive = s.ts.replace(tzinfo=None) if s.ts.tzinfo else s.ts
            delta_rows.append((ts_naive, int(drx or 0), int(dtx or 0)))
        aggregated = aggregate_rows_to_local_buckets(delta_rows, resolved_interval, tz)
        return [
            UsagePointDTO(day=b.replace(tzinfo=timezone.utc).isoformat(), rx=rx, tx=tx)
            for b, rx, tx in aggregated
        ]

    raise HTTPException(status_code=400, detail="window must be 'daily' or 'raw'")



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
    # usage this month
    prefix = datetime.utcnow().strftime("%Y-%m-")
    rows = db.query(UsageDaily).filter(UsageDaily.peer_id == peer_id, UsageDaily.day.like(f"{prefix}%")).all()
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
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
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


@router.get("/admin/usage_maintenance", response_model=UsageMaintenanceStatusDTO)
def read_usage_maintenance_status(
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    return UsageMaintenanceStatusDTO(**get_usage_maintenance_status())


@router.post("/admin/usage_maintenance/run", response_model=UsageMaintenanceStatusDTO, status_code=202)
def run_usage_maintenance(
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    try:
        started, status = start_usage_maintenance()
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not started:
        raise HTTPException(status_code=409, detail="Usage maintenance is already running")
    return UsageMaintenanceStatusDTO(**status)


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
    enabled: bool = True


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
    enabled: Optional[bool] = None


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
    enabled: bool
    created_at: str
    updated_at: str
    assigned_peer_count: int = 0
    assigned_peers: List[FairUsageAssignedPeerDTO] = []


class FairUsagePeerStatusDTO(BaseModel):
    peer_id: int
    rule_id: Optional[int] = None
    rule_name: Optional[str] = None
    quota_mode: Optional[str] = None
    download_quota_bytes: int = 0
    upload_quota_bytes: Optional[int] = None
    throttle_download_kbps: int = 0
    throttle_upload_kbps: int = 0
    time_scope: Optional[str] = None
    scope_period_count: int = 1
    scope_period_unit: str = "month"
    scope_label: str = "Monthly"
    scope_type: Optional[str] = None
    used_rx: int = 0
    used_tx: int = 0
    throttled: bool = False
    throttled_at: Optional[str] = None
    next_reset: Optional[str] = None


def _fu_rule_to_dto(rule: FairUsageRule, db: Session, include_peers: bool = False) -> FairUsageRuleDTO:
    assignments = db.query(FairUsageAssignment).filter(FairUsageAssignment.rule_id == rule.id).all()
    peers_out: List[FairUsageAssignedPeerDTO] = []
    if include_peers and assignments:
        peer_ids = [a.peer_id for a in assignments]
        peers = db.query(Peer).filter(Peer.id.in_(peer_ids)).all()
        for p in peers:
            peers_out.append(FairUsageAssignedPeerDTO(
                peer_id=p.id, name=p.name, allowed_address=p.allowed_address,
                router_id=p.router_id, disabled=p.disabled,
            ))
    cnt, unit = normalize_scope_period(rule)
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
        enabled=rule.enabled,
        created_at=rule.created_at.replace(tzinfo=timezone.utc).isoformat() if rule.created_at else "",
        updated_at=rule.updated_at.replace(tzinfo=timezone.utc).isoformat() if rule.updated_at else "",
        assigned_peer_count=len(assignments),
        assigned_peers=peers_out,
    )


def _get_peer_scope_usage(peer_id: int, rule: FairUsageRule, db: Session) -> tuple[int, int]:
    """Return (used_rx, used_tx) for the peer in the current fair-usage period."""
    return peer_scope_usage_for_rule(peer_id, rule, db)


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

    rule = FairUsageRule(
        name=dto.name,
        description=dto.description,
        quota_mode=dto.quota_mode,
        download_quota_bytes=dto.download_quota_bytes,
        upload_quota_bytes=dto.upload_quota_bytes if dto.quota_mode == "independent" else None,
        throttle_download_kbps=dto.throttle_download_kbps,
        throttle_upload_kbps=dto.throttle_upload_kbps,
        scope_period_count=sc,
        scope_period_unit=su,
        time_scope="monthly",
        scope_type=dto.scope_type,
        router_id=dto.router_id if dto.scope_type == "router" else None,
        enabled=dto.enabled,
    )
    sync_legacy_time_scope_field(rule)
    db.add(rule)
    db.flush()

    if dto.scope_type == "peer" and dto.peer_ids:
        for pid in dto.peer_ids:
            peer = db.get(Peer, pid)
            if not peer:
                continue
            db.add(FairUsageAssignment(rule_id=rule.id, peer_id=pid))
    db.commit()
    db.refresh(rule)
    return _fu_rule_to_dto(rule, db, include_peers=True)


@router.get("/fair-usage/rules", response_model=List[FairUsageRuleDTO])
def list_fair_usage_rules(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rules = db.query(FairUsageRule).order_by(FairUsageRule.id.asc()).all()
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

    if dto.name is not None:
        rule.name = dto.name
    if dto.description is not None:
        rule.description = dto.description
    if dto.quota_mode is not None:
        if dto.quota_mode not in ("combined", "independent"):
            raise HTTPException(status_code=400, detail="quota_mode must be 'combined' or 'independent'")
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
    if dto.router_id is not None:
        rule.router_id = dto.router_id
    if dto.enabled is not None:
        rule.enabled = dto.enabled
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
        if not peer:
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

    rule = get_effective_fair_usage_rule(peer, db)
    if not rule:
        return FairUsagePeerStatusDTO(peer_id=peer_id)

    used_rx, used_tx = _get_peer_scope_usage(peer_id, rule, db)
    cnt, unit = normalize_scope_period(rule)
    next_reset = compute_next_reset_utc_for_rule(rule, db)

    state = db.query(FairUsageState).filter(FairUsageState.peer_id == peer_id).first()
    throttled = state.throttled if state else False
    throttled_at = None
    if state and state.throttled_at:
        t = state.throttled_at
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        throttled_at = t.isoformat()

    return FairUsagePeerStatusDTO(
        peer_id=peer_id,
        rule_id=rule.id,
        rule_name=rule.name,
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
        used_rx=used_rx,
        used_tx=used_tx,
        throttled=throttled,
        throttled_at=throttled_at,
        next_reset=next_reset.isoformat(),
    )


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


class TelegramConfigPayload(BaseModel):
    tg_bot_token: Optional[str] = None
    tg_bot_enabled: Optional[bool] = None
    tg_admin_chat_id: Optional[str] = None
    tg_bot_language: Optional[str] = None


@router.get("/telegram/config")
def get_telegram_config(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    keys = ("tg_bot_token", "tg_bot_enabled", "tg_admin_chat_id", "tg_bot_language")
    result = {}
    for k in keys:
        kv = db.get(SettingsKV, k)
        v = kv.value if kv else ""
        if k == "tg_bot_token" and v:
            v = v[:8] + "..." if len(v) > 8 else "***"
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
