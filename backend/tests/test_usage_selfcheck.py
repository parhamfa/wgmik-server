import json
from datetime import datetime, timedelta, timezone

import backend.usage_selfcheck as selfcheck_module
from backend.db import SessionLocal
from backend.models import Action, SettingsKV, UsageMinute
from backend.settings import settings
from backend.tests.test_usage_holds import _poll, _seed_peer
from backend.usage_selfcheck import format_selfcheck_result, run_usage_selfcheck, run_usage_selfcheck_locked

GIB = 1024 ** 3


def _seed_counted_day(monkeypatch) -> tuple[int, datetime]:
    monkeypatch.setattr(settings, "timezone", "Asia/Tehran")
    base = datetime(2026, 6, 16, 8, 0, 0, tzinfo=timezone.utc)
    # Baseline sample from the previous local day (2026-06-15 23:58 Tehran).
    peer_id = _seed_peer(name="checked", first_ts=datetime(2026, 6, 15, 20, 28, tzinfo=timezone.utc), rx=5_000, tx=9_000)
    _poll(
        monkeypatch,
        "checked",
        [
            (base, 105_000, 2_009_000),
            (base + timedelta(minutes=1), 205_000, 4_009_000),
            (base + timedelta(minutes=2), 50_000, 1_000_000),  # counter reset
            (base + timedelta(minutes=3), 150_000, 3_000_000),
        ],
    )
    return peer_id, base


def test_selfcheck_is_quiet_when_counts_match(client, monkeypatch):
    peer_id, base = _seed_counted_day(monkeypatch)

    db = SessionLocal()
    try:
        summary = run_usage_selfcheck(db, now_utc=base + timedelta(minutes=5))
        db.commit()
        saved = json.loads(db.get(SettingsKV, "usage_selfcheck_last").value)
        mismatch_actions = db.query(Action).filter(Action.action == "usage_selfcheck_mismatch").count()
    finally:
        db.close()

    assert summary["mismatches"] == []
    assert summary["peers_checked"] == 1
    assert summary["window_start"] == "2026-06-15T20:30:00+00:00"
    assert saved["mismatches"] == [] and saved["peers_checked"] == 1
    assert set(saved) >= {"ran_at", "window_start", "window_end", "peers_checked", "mismatches"}
    assert mismatch_actions == 0
    assert format_selfcheck_result(summary) == "Self-check for today so far: 1 peer, all match (largest gap 0.0%)."


def test_selfcheck_reports_planted_mismatch_once(client, monkeypatch):
    peer_id, base = _seed_counted_day(monkeypatch)
    db = SessionLocal()
    try:
        row = db.query(UsageMinute).filter(UsageMinute.peer_id == peer_id).first()
        row.tx += GIB
        db.commit()
    finally:
        db.close()

    sent: list[str] = []
    window = (datetime(2026, 6, 15, 20, 30, tzinfo=timezone.utc), base + timedelta(minutes=5))
    monkeypatch.setattr(selfcheck_module, "today_window", lambda now_utc=None: window)
    monkeypatch.setattr(selfcheck_module, "send_admin_telegram", lambda text, **kw: sent.append(text) or True)

    summary = run_usage_selfcheck_locked(notify=True)

    assert summary["mismatches"] == [
        {
            "peer_id": peer_id,
            "name": "checked",
            "direction": "tx",
            # +2 MB, +2 MB, reset to 1 MB (fresh start), +2 MB, plus the planted 1 GiB.
            "counted": 7_000_000 + GIB,
            "recomputed": 7_000_000,
        }
    ]
    assert len(sent) == 1
    assert "checked: download counted" in sent[0]
    db = SessionLocal()
    try:
        actions = db.query(Action).filter(Action.action == "usage_selfcheck_mismatch").all()
        assert [a.peer_id for a in actions] == [peer_id]
    finally:
        db.close()
    assert "1 mismatch:" in format_selfcheck_result(summary)
