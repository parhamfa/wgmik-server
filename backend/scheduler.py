from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from .destructive_ops import exclusive_operation_gate
from .settings import settings
from .db import SessionLocal
from .models import Router, Peer, UsageSample, UsageMinute, UsageDaily, UsageMonthly, SettingsKV, Action
from .fair_usage_sync import apply_fair_usage_policy, get_applicable_fair_usage_rules
from .routeros.factory import make_client
from .routeros.version import is_routeros_supported
from .usage_deltas import USAGE_COUNTING_VERSION, USAGE_COUNTING_VERSION_KEY, counter_day_key, counter_delta
from .usage_holds import create_hold, format_bytes, max_peer_rate_bytes_per_second, notify_holds_created
from .usage_storage import floor_to_minute_utc, upsert_usage_minute


_scheduler: Optional[BackgroundScheduler] = None


def _enforce_fair_usage(db: Session, peer: Peer, client, now_utc: datetime):
    """Fair usage: throttle/unthrottle and keep simple-queue limits synced every poll tick."""
    apply_fair_usage_policy(db, peer, client, now_utc)


def _poll_once():
    """Poll enabled/supported routers, reconcile peer drift, store selected-peer usage, and enforce policy."""
    if exclusive_operation_gate.is_active():
        return

    # Telegram alerts for new holds go out after the poll releases the activity lock.
    hold_notices: list[tuple[int, str]] = []
    with exclusive_operation_gate.coordinated_activity():
        if exclusive_operation_gate.is_active():
            return

        now_utc = datetime.now(timezone.utc)
        try:
            tz = ZoneInfo(getattr(settings, "timezone", "UTC") or "UTC")
        except Exception:
            tz = ZoneInfo("UTC")
        day_key = counter_day_key(now_utc, tz)
        month_key = now_utc.strftime("%Y-%m")

        db: Session = SessionLocal()
        try:
            routers = (
                db.query(Router)
                .filter(Router.enabled == True, Router.ros_supported == True)  # noqa: E712
                .all()
            )
            if not routers:
                return

            now_naive = now_utc.replace(tzinfo=None)
            max_rate_bytes_per_sec = max_peer_rate_bytes_per_second(db)

            for router in routers:
                if not is_routeros_supported(getattr(router, "ros_version", "")):
                    continue
                try:
                    client = make_client(router)
                    live_peers = client.list_all_wireguard_peers()
                except Exception:
                    # If a router is unreachable, skip it without changing drift state.
                    continue

                db_peers = db.query(Peer).filter(Peer.router_id == router.id).all()
                by_key = {(p.interface, p.public_key): p for p in db_peers}
                live_by_key = {
                    (lp.interface, lp.public_key): lp
                    for lp in live_peers
                    if getattr(lp, "interface", "") and getattr(lp, "public_key", "")
                }

                for key, lp in live_by_key.items():
                    peer = by_key.get(key)
                    if peer is None:
                        peer = Peer(
                            router_id=router.id,
                            interface=lp.interface,
                            ros_id=lp.ros_id or "",
                            name=lp.name or "",
                            public_key=lp.public_key,
                            allowed_address=lp.allowed_address or "",
                            disabled=bool(lp.disabled),
                            selected=False,
                            router_sync_status="new",
                            router_sync_first_seen_at=now_naive,
                            router_sync_last_seen_at=now_naive,
                        )
                        db.add(peer)
                        db.flush()
                        by_key[key] = peer
                        db_peers.append(peer)
                        db.add(
                            Action(
                                peer_id=peer.id,
                                ts=now_utc,
                                action="router_discovered",
                                note="Discovered on RouterOS; pending admin decision",
                            )
                        )
                        continue

                    previous_status = (peer.router_sync_status or "synced").strip().lower()
                    # Only a peer that previously went "missing" should auto-resolve to
                    # "synced" when it reappears. A "new" (RouterOS-discovered) peer must
                    # stay flagged until an admin explicitly accepts or hides it; otherwise
                    # its warning silently clears on the next poll and it drops into the
                    # hidden list while still present on the router.
                    if previous_status == "missing":
                        peer.router_sync_status = "synced"
                        peer.router_sync_first_seen_at = None
                        peer.router_sync_last_seen_at = None
                        db.add(
                            Action(
                                peer_id=peer.id,
                                ts=now_utc,
                                action="router_reappeared",
                                note="Peer is present on RouterOS again",
                            )
                        )

                    if lp.ros_id and peer.ros_id != lp.ros_id:
                        peer.ros_id = lp.ros_id
                        db.add(
                            Action(
                                peer_id=peer.id,
                                ts=now_utc,
                                action="router_update",
                                note=f"Updated ros_id from router: {lp.ros_id}",
                            )
                        )
                    if (lp.name or "") != (peer.name or ""):
                        peer.name = lp.name or ""
                    if (lp.allowed_address or "") != (peer.allowed_address or ""):
                        peer.allowed_address = lp.allowed_address or ""
                    cep = (getattr(lp, "client_endpoint", "") or "").strip()
                    if cep:
                        kv_ep = db.get(SettingsKV, f"peer_export_endpoint:{peer.id}") or SettingsKV(
                            key=f"peer_export_endpoint:{peer.id}", value=""
                        )
                        if (kv_ep.value or "").strip() != cep:
                            kv_ep.value = cep
                            db.add(kv_ep)
                    if bool(peer.disabled) != bool(lp.disabled):
                        peer.disabled = bool(lp.disabled)
                        db.add(
                            Action(
                                peer_id=peer.id,
                                ts=now_utc,
                                action="router_disable" if peer.disabled else "router_enable",
                                note="Detected router state change during poll",
                            )
                        )

                for peer in list(db_peers):
                    key = (peer.interface, peer.public_key)
                    if key in live_by_key:
                        continue
                    status_value = (peer.router_sync_status or "synced").strip().lower()
                    if status_value == "new":
                        db.query(Action).filter(Action.peer_id == peer.id).delete(synchronize_session=False)
                        db.delete(peer)
                        continue
                    if peer.selected:
                        if status_value != "missing":
                            peer.router_sync_first_seen_at = now_naive
                            db.add(
                                Action(
                                    peer_id=peer.id,
                                    ts=now_utc,
                                    action="router_missing",
                                    note="Peer not found on RouterOS; pending admin decision",
                                )
                            )
                        peer.router_sync_status = "missing"
                        peer.router_sync_last_seen_at = now_naive

                db.commit()

                polled_peer_ids: list[int] = []
                router_hold_notices: list[tuple[int, str]] = []

                selected_peers = (
                    db.query(Peer)
                    .filter(
                        Peer.router_id == router.id,
                        Peer.selected == True,  # noqa: E712
                        Peer.router_sync_status == "synced",
                    )
                    .all()
                )
                for peer in selected_peers:
                    lp = live_by_key.get((peer.interface, peer.public_key))
                    if not lp:
                        continue
                    polled_peer_ids.append(int(peer.id))

                    # Fetch previous sample before writing a new one
                    last_sample: UsageSample | None = (
                        db.query(UsageSample)
                        .filter(UsageSample.peer_id == peer.id)
                        .order_by(UsageSample.ts.desc())
                        .first()
                    )

                    # Record raw sample
                    sample = UsageSample(
                        peer_id=peer.id,
                        ts=now_utc,
                        rx=lp.rx_bytes,
                        tx=lp.tx_bytes,
                        endpoint=lp.endpoint or "",
                    )
                    db.add(sample)

                    # Compute deltas. A counter that went down started over (router reboot,
                    # peer disable/enable, 32-bit wrap): count its new value as fresh traffic.
                    delta_rx = 0
                    delta_tx = 0
                    if last_sample is not None:
                        rx_result = counter_delta(last_sample.rx, lp.rx_bytes)
                        tx_result = counter_delta(last_sample.tx, lp.tx_bytes)
                        delta_rx = rx_result.delta
                        delta_tx = tx_result.delta

                        # One reading faster than the per-peer rate cap is a counter glitch
                        # (bounce-back, or a peer moved to a router whose counter is already
                        # high). Hold it for an admin instead of counting it.
                        last_ts = last_sample.ts.replace(tzinfo=None) if last_sample.ts.tzinfo else last_sample.ts
                        elapsed_seconds = max((now_naive - last_ts).total_seconds(), 1.0)
                        max_bytes = int(max_rate_bytes_per_sec * elapsed_seconds)
                        for direction, delta, reverse in (("rx", delta_rx, delta_tx), ("tx", delta_tx, delta_rx)):
                            if delta <= max_bytes:
                                continue
                            hold, note = create_hold(
                                db,
                                peer=peer,
                                direction=direction,
                                reason="implausible_jump",
                                minutes={floor_to_minute_utc(now_utc): delta},
                                start_ts=last_ts,
                                end_ts=now_naive,
                                reverse_bytes=reverse,
                                reason_detail=(
                                    f"{format_bytes(delta)} in {elapsed_seconds:.0f} s is faster than the "
                                    f"{max_rate_bytes_per_sec * 8 // 1_000_000} Mbit/s cap"
                                ),
                                now_utc=now_utc,
                            )
                            router_hold_notices.append((hold.id, note))
                            if direction == "rx":
                                delta_rx = 0
                            else:
                                delta_tx = 0

                        if rx_result.dropped or tx_result.dropped:
                            db.add(
                                Action(
                                    peer_id=peer.id,
                                    ts=now_utc,
                                    action="counter_reset",
                                    note=f"Detected counter reset: rx {last_sample.rx}->{lp.rx_bytes}, tx {last_sample.tx}->{lp.tx_bytes}",
                                )
                            )
                    monthly = (
                        db.query(UsageMonthly)
                        .filter(UsageMonthly.peer_id == peer.id, UsageMonthly.month_key == month_key)
                        .first()
                    )

                    # Update rollups only when there's actual traffic in this poll
                    if delta_rx != 0 or delta_tx != 0:
                        minute_ts = floor_to_minute_utc(now_utc)
                        upsert_usage_minute(db, peer.id, minute_ts, delta_rx, delta_tx)

                        # Update / upsert UsageDaily
                        daily = (
                            db.query(UsageDaily)
                            .filter(UsageDaily.peer_id == peer.id, UsageDaily.day == day_key)
                            .first()
                        )
                        if daily is None:
                            daily = UsageDaily(peer_id=peer.id, day=day_key, rx=delta_rx, tx=delta_tx)
                            db.add(daily)
                        else:
                            daily.rx += delta_rx
                            daily.tx += delta_tx

                        # Update / upsert UsageMonthly
                        if monthly is None:
                            monthly = UsageMonthly(peer_id=peer.id, month_key=month_key, rx=delta_rx, tx=delta_tx)
                            db.add(monthly)
                        else:
                            monthly.rx += delta_rx
                            monthly.tx += delta_tx
                # Commit usage/history updates immediately so a later slow/offline router
                # cannot hold SQLite write locks over already-saved data.
                db.commit()
                hold_notices.extend(router_hold_notices)

                for peer_id in polled_peer_ids:
                    peer = db.get(Peer, peer_id)
                    if not peer:
                        continue

                    # ── Fair Usage enforcement (replaces old quota hard-disable) ──
                    _enforce_fair_usage(db, peer, client, now_utc)

                    # ── Telegram notifications (best-effort, never blocks polling) ──
                    try:
                        from .telegram.notifications import check_and_send_notifications
                        from .fair_usage_usage import peer_scope_usage_for_rule as _fu_usage
                        for fu_rule in get_applicable_fair_usage_rules(peer, db):
                            _urx, _utx = _fu_usage(peer.id, fu_rule, db, now_utc)
                            check_and_send_notifications(db, peer, fu_rule, _urx, _utx, now_utc)
                    except Exception:
                        pass

                db.commit()
        except OperationalError as exc:
            db.rollback()
            print(f"Polling skipped due to database error: {exc}")
        finally:
            db.close()

    notify_holds_created(hold_notices)


def _usage_one_sided_guard():
    """Every 5 minutes: hold download/upload bursts with almost no traffic the other way."""
    if exclusive_operation_gate.is_active():
        return
    from .usage_guard import run_one_sided_guard

    notices: list[tuple[int, str]] = []
    with exclusive_operation_gate.coordinated_activity():
        if exclusive_operation_gate.is_active():
            return
        db: Session = SessionLocal()
        try:
            notices = run_one_sided_guard(db)
            db.commit()
        except Exception as exc:
            db.rollback()
            notices = []
            print(f"One-sided usage guard skipped: {exc}")
        finally:
            db.close()
    notify_holds_created(notices)


def _usage_selfcheck_job():
    """Nightly: recompute today's usage from raw samples and compare with the minute rollups."""
    from .usage_selfcheck import run_usage_selfcheck_locked

    try:
        run_usage_selfcheck_locked(notify=True)
    except Exception as exc:
        print(f"Usage self-check failed: {exc}")


def record_usage_counting_version() -> None:
    """Mark this database as counting usage with the fresh-start rule (see usage_deltas)."""
    db: Session = SessionLocal()
    try:
        kv = db.get(SettingsKV, USAGE_COUNTING_VERSION_KEY)
        if kv is None or kv.value != USAGE_COUNTING_VERSION:
            if kv is None:
                db.add(SettingsKV(key=USAGE_COUNTING_VERSION_KEY, value=USAGE_COUNTING_VERSION))
            else:
                kv.value = USAGE_COUNTING_VERSION
            # When this rule went live; the repair tool refuses to run after a later
            # maintenance rebuild of the minute rollups.
            since_key = f"{USAGE_COUNTING_VERSION_KEY}_since"
            since = db.get(SettingsKV, since_key) or SettingsKV(key=since_key, value="")
            since.value = datetime.now(timezone.utc).isoformat()
            db.add(since)
        db.commit()
    finally:
        db.close()


def _local_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(getattr(settings, "timezone", "UTC") or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def auto_maintenance_due(schedule: dict, last_run_utc: Optional[datetime], now_local: datetime) -> bool:
    """Decide whether a scheduled maintenance run is due at the daily check time.

    Pure function so the frequency logic is unit-testable. `now_local` must be
    timezone-aware in the panel's local timezone.
    """
    if not schedule.get("usage_maintenance_auto_enabled"):
        return False
    last_local_date = None
    if last_run_utc is not None:
        if last_run_utc.tzinfo is None:
            last_run_utc = last_run_utc.replace(tzinfo=timezone.utc)
        last_local_date = last_run_utc.astimezone(now_local.tzinfo).date()
    # Never run twice on the same local day (protects against misfire replays).
    if last_local_date == now_local.date():
        return False

    frequency = schedule.get("usage_maintenance_auto_frequency") or "daily"
    if frequency == "weekly":
        return now_local.weekday() == int(schedule.get("usage_maintenance_auto_weekday") or 0)
    if frequency == "every_n_days":
        if last_local_date is None:
            return True
        interval = max(2, int(schedule.get("usage_maintenance_auto_interval_days") or 2))
        return (now_local.date() - last_local_date).days >= interval
    return True  # daily


def _usage_maintenance_auto_check():
    from .usage_maintenance import (
        get_last_auto_run,
        is_usage_maintenance_running,
        load_auto_maintenance_settings,
        record_last_auto_run,
        start_usage_maintenance,
    )

    db: Session = SessionLocal()
    try:
        schedule = load_auto_maintenance_settings(db)
        if not schedule["usage_maintenance_auto_enabled"]:
            return
        last_run_raw = get_last_auto_run(db)
        last_run = None
        if last_run_raw:
            try:
                last_run = datetime.fromisoformat(last_run_raw)
            except ValueError:
                last_run = None
        now_local = datetime.now(_local_timezone())
        if not auto_maintenance_due(schedule, last_run, now_local):
            return
        if exclusive_operation_gate.is_active() or is_usage_maintenance_running(db):
            return
    finally:
        db.close()

    try:
        started, _status = start_usage_maintenance(trigger="scheduled")
    except Exception as exc:
        print(f"Scheduled usage maintenance failed to start: {exc}")
        return
    if not started:
        return

    db = SessionLocal()
    try:
        record_last_auto_run(db)
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _usage_maintenance_trigger() -> CronTrigger:
    from .usage_maintenance import load_auto_maintenance_settings

    db: Session = SessionLocal()
    try:
        schedule = load_auto_maintenance_settings(db)
    finally:
        db.close()
    hour, minute = (int(part) for part in schedule["usage_maintenance_auto_time"].split(":"))
    return CronTrigger(hour=hour, minute=minute, timezone=_local_timezone())


def reschedule_usage_maintenance_job() -> None:
    """Hot-reload the auto-maintenance check time after settings change."""
    global _scheduler
    if _scheduler is None:
        return
    trigger = _usage_maintenance_trigger()
    try:
        _scheduler.reschedule_job("usage-maintenance-auto", trigger=trigger)
    except Exception:
        _scheduler.add_job(
            _usage_maintenance_auto_check,
            trigger,
            id="usage-maintenance-auto",
            replace_existing=True,
            misfire_grace_time=6 * 3600,
        )


def get_usage_maintenance_next_run() -> Optional[datetime]:
    global _scheduler
    if _scheduler is None:
        return None
    try:
        job = _scheduler.get_job("usage-maintenance-auto")
        return job.next_run_time if job else None
    except Exception:
        return None


def _maybe_catch_up_usage_maintenance() -> None:
    """If the app was down (or restarted) past today's slot and a run is still due,
    schedule a one-shot catch-up shortly after startup."""
    from .usage_maintenance import get_last_auto_run, load_auto_maintenance_settings

    global _scheduler
    if _scheduler is None:
        return
    db: Session = SessionLocal()
    try:
        schedule = load_auto_maintenance_settings(db)
        if not schedule["usage_maintenance_auto_enabled"]:
            return
        last_run_raw = get_last_auto_run(db)
    finally:
        db.close()

    last_run = None
    if last_run_raw:
        try:
            last_run = datetime.fromisoformat(last_run_raw)
        except ValueError:
            last_run = None
    now_local = datetime.now(_local_timezone())
    hour, minute = (int(part) for part in schedule["usage_maintenance_auto_time"].split(":"))
    slot_passed = (now_local.hour, now_local.minute) >= (hour, minute)
    if slot_passed and auto_maintenance_due(schedule, last_run, now_local):
        from apscheduler.triggers.date import DateTrigger
        from datetime import timedelta as _td

        _scheduler.add_job(
            _usage_maintenance_auto_check,
            DateTrigger(run_date=datetime.now(timezone.utc) + _td(seconds=120)),
            id="usage-maintenance-catchup",
            replace_existing=True,
        )


def ensure_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        # Read persisted poll interval if present
        db: Session = SessionLocal()
        try:
            kv = db.get(SettingsKV, "poll_interval_seconds")
            interval = (
                int(kv.value)
                if kv and kv.value.isdigit() and int(kv.value) > 0
                else settings.poll_interval_seconds
            )
        finally:
            db.close()
        _scheduler = BackgroundScheduler(
            timezone="UTC",
            job_defaults={"coalesce": True, "max_instances": 1},
        )
        _scheduler.add_job(
            _poll_once,
            IntervalTrigger(seconds=interval),
            id="polling-job",
            replace_existing=True,
        )
        _scheduler.add_job(
            _tg_daily_summary,
            CronTrigger(hour=20, minute=0),
            id="tg-daily-summary",
            replace_existing=True,
        )
        _scheduler.add_job(
            _tg_weekly_summary,
            CronTrigger(day_of_week="sun", hour=20, minute=0),
            id="tg-weekly-summary",
            replace_existing=True,
        )
        _scheduler.add_job(
            _usage_maintenance_auto_check,
            _usage_maintenance_trigger(),
            id="usage-maintenance-auto",
            replace_existing=True,
            misfire_grace_time=6 * 3600,
        )
        _scheduler.add_job(
            _usage_one_sided_guard,
            IntervalTrigger(minutes=5),
            id="usage-one-sided-guard",
            replace_existing=True,
        )
        # 23:50 local: raw samples (kept 24 h) still cover the whole local day.
        _scheduler.add_job(
            _usage_selfcheck_job,
            CronTrigger(hour=23, minute=50, timezone=_local_timezone()),
            id="usage-selfcheck",
            replace_existing=True,
            misfire_grace_time=600,
        )
        _scheduler.start()
        try:
            _maybe_catch_up_usage_maintenance()
        except Exception as exc:
            print(f"Usage maintenance catch-up check skipped: {exc}")
    return _scheduler


def _tg_daily_summary():
    if exclusive_operation_gate.is_active():
        return
    with exclusive_operation_gate.coordinated_activity():
        if exclusive_operation_gate.is_active():
            return
        db: Session = SessionLocal()
        try:
            from .telegram.notifications import send_daily_summary
            send_daily_summary(db)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


def _tg_weekly_summary():
    if exclusive_operation_gate.is_active():
        return
    with exclusive_operation_gate.coordinated_activity():
        if exclusive_operation_gate.is_active():
            return
        db: Session = SessionLocal()
        try:
            from .telegram.notifications import send_weekly_summary
            send_weekly_summary(db)
            db.commit()
        except Exception:
            db.rollback()
        finally:
            db.close()


def update_scheduler_interval(seconds: int) -> None:
    """Hot-reload the polling interval without restarting the server."""
    global _scheduler
    if _scheduler is None or seconds <= 0:
        return
    trigger = IntervalTrigger(seconds=seconds)
    try:
        _scheduler.reschedule_job("polling-job", trigger=trigger)
    except Exception:
        # If job missing for some reason, recreate it
        _scheduler.add_job(
            _poll_once,
            trigger,
            id="polling-job",
            replace_existing=True,
        )


def set_polling_paused(paused: bool) -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        if paused:
            _scheduler.pause_job("polling-job")
        else:
            _scheduler.resume_job("polling-job")
    except Exception:
        pass


def pause_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.pause_job("polling-job")
    except Exception:
        pass


def resume_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.resume_job("polling-job")
    except Exception:
        pass
