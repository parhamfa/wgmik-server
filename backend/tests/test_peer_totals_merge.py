import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db import Base
from backend.models import Peer, Router, UsageDaily, UsageMinute, UsageMonthly, UsageSample
from backend.peer_totals_merge import apply_peer_totals_merge, build_merge_plan


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace(" ", "T"))


def _make_router(router_id: int, name: str) -> Router:
    return Router(
        id=router_id,
        name=name,
        host=f"10.0.0.{router_id}",
        proto="rest",
        port=443,
        username="admin",
        secret_enc=f"secret-{router_id}",
        tls_verify=True,
        enabled=True,
    )


def _make_peer(
    peer_id: int,
    router_id: int,
    *,
    interface: str,
    name: str,
    public_key: str,
    allowed_address: str,
    selected: bool,
) -> Peer:
    return Peer(
        id=peer_id,
        router_id=router_id,
        interface=interface,
        ros_id=f"*{peer_id}",
        name=name,
        public_key=public_key,
        allowed_address=allowed_address,
        comment="",
        disabled=False,
        selected=selected,
    )


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_merge_db(tmp_path: Path) -> str:
    db_path = tmp_path / "peer-totals-merge.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)

    with Session() as db:
        db.add_all([_make_router(1, "Asia"), _make_router(2, "Arvan")])
        db.add_all(
            [
                _make_peer(
                    1,
                    1,
                    interface="wg0",
                    name="exact-target",
                    public_key="pub-exact",
                    allowed_address="10.0.0.1/32",
                    selected=True,
                ),
                _make_peer(
                    48,
                    2,
                    interface="wg0",
                    name="exact-source",
                    public_key="pub-exact",
                    allowed_address="10.0.0.1/32",
                    selected=True,
                ),
                _make_peer(
                    43,
                    1,
                    interface="wgmik",
                    name="iman-dad",
                    public_key="asia-43",
                    allowed_address="10.65.74.105/32",
                    selected=True,
                ),
                _make_peer(
                    88,
                    2,
                    interface="wgmik",
                    name="iman-dad",
                    public_key="arvan-88",
                    allowed_address="10.65.74.105/32",
                    selected=True,
                ),
                _make_peer(
                    30,
                    1,
                    interface="wgmik",
                    name="iman-PC",
                    public_key="asia-30",
                    allowed_address="10.65.74.4/32",
                    selected=False,
                ),
                _make_peer(
                    98,
                    1,
                    interface="wgmik",
                    name="iman-PC",
                    public_key="asia-98",
                    allowed_address="10.65.74.4/32",
                    selected=False,
                ),
                _make_peer(
                    93,
                    2,
                    interface="wgmik",
                    name="iman-PC",
                    public_key="arvan-93",
                    allowed_address="10.65.74.4/32",
                    selected=True,
                ),
                _make_peer(
                    47,
                    2,
                    interface="tom",
                    name="tom",
                    public_key="arvan-47",
                    allowed_address="0.0.0.0/0",
                    selected=False,
                ),
            ]
        )

        db.add_all(
            [
                UsageMinute(peer_id=1, minute_ts=_dt("2026-04-10 10:00:00"), rx=100, tx=200),
                UsageMinute(peer_id=1, minute_ts=_dt("2026-04-10 10:02:00"), rx=10, tx=20),
                UsageMinute(peer_id=48, minute_ts=_dt("2026-04-10 10:00:00"), rx=5, tx=7),
                UsageMinute(peer_id=48, minute_ts=_dt("2026-04-10 10:01:00"), rx=11, tx=13),
                UsageDaily(peer_id=1, day="2026-04-10", rx=1, tx=2),
                UsageDaily(peer_id=48, day="2026-04-10", rx=50, tx=60),
                UsageDaily(peer_id=48, day="2026-04-11", rx=10, tx=20),
                UsageMonthly(peer_id=1, month_key="2026-04", rx=7, tx=8),
                UsageMonthly(peer_id=48, month_key="2026-04", rx=70, tx=80),
                UsageMinute(peer_id=43, minute_ts=_dt("2026-04-16 20:30:00"), rx=400, tx=500),
                UsageMinute(peer_id=88, minute_ts=_dt("2026-04-16 19:30:00"), rx=40, tx=50),
                UsageDaily(peer_id=43, day="2026-04-16", rx=1, tx=1),
                UsageDaily(peer_id=88, day="2026-04-16", rx=9, tx=4),
                UsageMonthly(peer_id=43, month_key="2026-04", rx=11, tx=13),
                UsageMonthly(peer_id=88, month_key="2026-04", rx=90, tx=40),
                UsageMinute(peer_id=30, minute_ts=_dt("2026-03-11 17:42:00"), rx=200, tx=300),
                UsageDaily(peer_id=30, day="2026-03-11", rx=15, tx=25),
                UsageMonthly(peer_id=30, month_key="2026-03", rx=150, tx=250),
                UsageMinute(peer_id=98, minute_ts=_dt("2026-04-18 14:42:00"), rx=8, tx=9),
                UsageMinute(peer_id=93, minute_ts=_dt("2026-04-18 10:00:00"), rx=80, tx=90),
                UsageDaily(peer_id=98, day="2026-04-18", rx=3, tx=4),
                UsageDaily(peer_id=93, day="2026-04-18", rx=30, tx=40),
                UsageMonthly(peer_id=98, month_key="2026-04", rx=33, tx=44),
                UsageMonthly(peer_id=93, month_key="2026-04", rx=300, tx=400),
                UsageSample(peer_id=98, ts=_dt("2026-04-18 10:00:00"), rx=1000, tx=2000, endpoint=""),
                UsageSample(peer_id=98, ts=_dt("2026-04-18 10:05:00"), rx=1100, tx=2200, endpoint=""),
                UsageSample(peer_id=93, ts=_dt("2026-04-18 09:00:00"), rx=500, tx=700, endpoint=""),
                UsageSample(peer_id=93, ts=_dt("2026-04-18 09:05:00"), rx=700, tx=900, endpoint=""),
            ]
        )
        db.commit()

    engine.dispose()
    return str(db_path)


def test_build_merge_plan_discovers_defaults_and_respects_overrides(tmp_path):
    db_path = _seed_merge_db(tmp_path)

    plan = build_merge_plan(db_path)

    assert [pair.source_peer.id for pair in plan.pairs] == [30, 48, 88, 93]
    assert [pair.target_peer.id for pair in plan.pairs] == [98, 1, 43, 98]
    assert {pair.match_type for pair in plan.pairs} == {
        "exact_public_key",
        "manual_address",
        "manual_canonical",
    }
    assert 47 in plan.ignored_sources
    assert plan.total_usage_minute_rows == 5
    assert plan.total_usage_daily_rows == 5
    assert plan.total_usage_monthly_rows == 4

    override_plan = build_merge_plan(
        db_path,
        explicit_pairs=[(47, 1)],
        explicit_ignores=[88],
    )
    source_ids = [pair.source_peer.id for pair in override_plan.pairs]
    assert 47 in source_ids
    assert 88 not in source_ids
    override_pair = next(pair for pair in override_plan.pairs if pair.source_peer.id == 47)
    assert override_pair.target_peer.id == 1
    assert override_pair.match_type == "manual_override"


def test_apply_peer_totals_merge_sums_rows_and_updates_selection(tmp_path):
    db_path = _seed_merge_db(tmp_path)

    result = apply_peer_totals_merge(db_path, backup_path=str(tmp_path / "backup.db"))

    assert result.no_op is False
    assert len(result.pairs) == 4
    assert result.total_usage_minute_rows == 5
    assert result.total_usage_daily_rows == 5
    assert result.total_usage_monthly_rows == 4
    assert os.path.exists(result.backup_path)

    with _connect(db_path) as conn:
        peer_rows = conn.execute(
            "SELECT id, selected FROM peers WHERE id IN (1,30,43,47,48,88,93,98) ORDER BY id"
        ).fetchall()
        selected = {int(row["id"]): int(row["selected"]) for row in peer_rows}
        assert selected[48] == 0
        assert selected[88] == 0
        assert selected[30] == 0
        assert selected[93] == 0
        assert selected[43] == 1
        assert selected[98] == 1

        minute_exact = conn.execute(
            "SELECT rx, tx FROM usage_minute WHERE peer_id = 1 AND strftime('%Y-%m-%d %H:%M:%S', minute_ts) = '2026-04-10 10:00:00'"
        ).fetchone()
        assert (int(minute_exact["rx"]), int(minute_exact["tx"])) == (105, 207)

        minute_canonical = conn.execute(
            "SELECT COUNT(*) AS count FROM usage_minute WHERE peer_id = 98"
        ).fetchone()
        assert int(minute_canonical["count"]) == 3

        daily_43 = conn.execute(
            "SELECT rx, tx FROM usage_daily WHERE peer_id = 43 AND day = '2026-04-16'"
        ).fetchone()
        assert (int(daily_43["rx"]), int(daily_43["tx"])) == (10, 5)

        daily_98 = conn.execute(
            "SELECT rx, tx FROM usage_daily WHERE peer_id = 98 AND day = '2026-04-18'"
        ).fetchone()
        assert (int(daily_98["rx"]), int(daily_98["tx"])) == (33, 44)

        monthly_98_april = conn.execute(
            "SELECT rx, tx FROM usage_monthly WHERE peer_id = 98 AND month_key = '2026-04'"
        ).fetchone()
        assert (int(monthly_98_april["rx"]), int(monthly_98_april["tx"])) == (333, 444)

        monthly_98_march = conn.execute(
            "SELECT rx, tx FROM usage_monthly WHERE peer_id = 98 AND month_key = '2026-03'"
        ).fetchone()
        assert (int(monthly_98_march["rx"]), int(monthly_98_march["tx"])) == (150, 250)

        ledger = conn.execute(
            "SELECT source_peer_id, target_peer_id, usage_minute_rows, usage_daily_rows, usage_monthly_rows FROM peer_totals_merge ORDER BY source_peer_id"
        ).fetchall()
        assert [(int(row["source_peer_id"]), int(row["target_peer_id"])) for row in ledger] == [
            (30, 98),
            (48, 1),
            (88, 43),
            (93, 98),
        ]

        summary = conn.execute(
            """
            SELECT p.id, COALESCE(SUM(d.rx), 0) AS rx, COALESCE(SUM(d.tx), 0) AS tx
            FROM peers p
            LEFT JOIN usage_daily d ON d.peer_id = p.id
            WHERE p.selected = 1
            GROUP BY p.id
            ORDER BY p.id
            """
        ).fetchall()
        summary_by_peer = {int(row["id"]): (int(row["rx"]), int(row["tx"])) for row in summary}
        assert summary_by_peer[43] == (10, 5)
        assert summary_by_peer[98] == (48, 69)
        assert 93 not in summary_by_peer

        raw_counts = conn.execute(
            "SELECT peer_id, COUNT(*) AS count, SUM(rx) AS rx_sum, SUM(tx) AS tx_sum FROM usage_samples WHERE peer_id IN (93, 98) GROUP BY peer_id ORDER BY peer_id"
        ).fetchall()
        assert [(int(row["peer_id"]), int(row["count"]), int(row["rx_sum"]), int(row["tx_sum"])) for row in raw_counts] == [
            (93, 2, 1200, 1600),
            (98, 2, 2100, 4200),
        ]


def test_apply_peer_totals_merge_is_noop_on_second_run(tmp_path):
    db_path = _seed_merge_db(tmp_path)

    first = apply_peer_totals_merge(db_path, backup_path=str(tmp_path / "backup-first.db"))
    second = apply_peer_totals_merge(db_path, backup_path=str(tmp_path / "backup-second.db"))

    assert first.no_op is False
    assert second.no_op is True
    assert second.skipped_sources == (30, 48, 88, 93)
    assert second.pairs == ()
    assert not os.path.exists(tmp_path / "backup-second.db")


@pytest.mark.skipif(
    not os.environ.get("WGMIK_PEER_TOTALS_MERGE_DB_PATH"),
    reason="set WGMIK_PEER_TOTALS_MERGE_DB_PATH to run against a live DB copy",
)
def test_live_db_shape_dry_run_matches_expected_counts(tmp_path):
    source_path = Path(os.environ["WGMIK_PEER_TOTALS_MERGE_DB_PATH"]).resolve()
    db_copy = tmp_path / source_path.name
    shutil.copy2(source_path, db_copy)

    plan = build_merge_plan(str(db_copy))

    assert plan.distinct_source_count == 46
    assert plan.distinct_target_count == 45
    assert plan.total_usage_minute_rows == 380_373
    assert plan.total_usage_daily_rows == 604
    assert plan.total_usage_monthly_rows == 42
