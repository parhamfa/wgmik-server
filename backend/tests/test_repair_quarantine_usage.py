"""Tests for backend.tools.repair_quarantine_usage.

Each test builds a small SQLite database by replaying polls through a simulator
of the OLD poller (near-32-bit quarantine, drop polls count 0) and, after an
optional "deploy" time, the fixed fresh-start poller. The simulator also tracks
the target state: the old numbers with every quarantined window counted by the
fresh-start rule. After --apply the usage tables must equal that target.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.tools import repair_quarantine_usage as rq

GIB = 1024 ** 3
MIB = 1024 ** 2
NEAR32 = int(3.5 * GIB)
LOW = 768 * MIB
TEH = timedelta(hours=3, minutes=30)
FMT = "%Y-%m-%d %H:%M:%S.%f"

SCHEMA = """
CREATE TABLE peers (id INTEGER PRIMARY KEY, router_id INTEGER, interface VARCHAR(128), ros_id VARCHAR(64),
    name VARCHAR(255), public_key VARCHAR(255), allowed_address VARCHAR(255), comment VARCHAR(255),
    disabled BOOLEAN, selected BOOLEAN);
CREATE TABLE usage_samples (id INTEGER PRIMARY KEY, peer_id INTEGER, ts DATETIME, rx BIGINT, tx BIGINT,
    endpoint VARCHAR(255));
CREATE INDEX ix_usage_samples_peer_id_ts ON usage_samples (peer_id, ts);
CREATE TABLE usage_minute (id INTEGER PRIMARY KEY, peer_id INTEGER, minute_ts DATETIME, rx BIGINT, tx BIGINT,
    CONSTRAINT uq_usage_minute_peer_ts UNIQUE (peer_id, minute_ts));
CREATE INDEX ix_usage_minute_minute_ts ON usage_minute (minute_ts);
CREATE TABLE usage_daily (id INTEGER PRIMARY KEY, peer_id INTEGER, day VARCHAR(10), rx BIGINT, tx BIGINT,
    CONSTRAINT uq_daily_peer_day UNIQUE (peer_id, day));
CREATE TABLE usage_monthly (id INTEGER PRIMARY KEY, peer_id INTEGER, month_key VARCHAR(7), rx BIGINT, tx BIGINT,
    CONSTRAINT uq_month_peer UNIQUE (peer_id, month_key));
CREATE TABLE actions (id INTEGER PRIMARY KEY, peer_id INTEGER, ts DATETIME, action VARCHAR(64), note VARCHAR);
CREATE INDEX ix_actions_ts ON actions (ts);
CREATE TABLE settings_kv (key VARCHAR(64) PRIMARY KEY, value VARCHAR);
CREATE TABLE usage_holds (id INTEGER PRIMARY KEY, peer_id INTEGER, direction VARCHAR(2), reason VARCHAR(32),
    start_ts DATETIME, end_ts DATETIME, bytes BIGINT, minutes_json TEXT, status VARCHAR(16), created_at DATETIME,
    decided_at DATETIME);
"""


def fresh(prev: int, cur: int) -> int:
    return cur - prev if cur >= prev else cur


class Sim:
    def __init__(self, path: Path, settings=None):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.executescript(SCHEMA)
        base = {"timezone": "Asia/Tehran", "minute_rollup_retention_days": "90"}
        base.update(settings or {})
        for k, v in base.items():
            if v is not None:
                self.conn.execute("INSERT INTO settings_kv VALUES (?, ?)", (k, v))
        self.last = {}
        self.quar = {}
        self.expected = {"minute": defaultdict(lambda: [0, 0]), "daily": defaultdict(lambda: [0, 0]),
                         "monthly": defaultdict(lambda: [0, 0])}

    def add_peer(self, pid: int, name: str):
        self.conn.execute(
            "INSERT INTO peers (id, router_id, interface, name, public_key, allowed_address) VALUES (?,1,'wg0',?,?,'')",
            (pid, name, f"key{pid}"),
        )

    def _bump(self, table, col, pid, key, rx, tx):
        row = self.conn.execute(f"SELECT id FROM {table} WHERE peer_id=? AND {col}=?", (pid, key)).fetchone()
        if row:
            self.conn.execute(f"UPDATE {table} SET rx=rx+?, tx=tx+? WHERE id=?", (rx, tx, row[0]))
        else:
            self.conn.execute(f"INSERT INTO {table} (peer_id, {col}, rx, tx) VALUES (?,?,?,?)", (pid, key, rx, tx))

    def poll(self, pid, ts, rx, tx, code="old", hold=None):
        """hold: direction whose bytes the fixed code puts in a pending usage hold this poll."""
        c = self.conn
        day = (ts + TEH).strftime("%Y-%m-%d")
        month = ts.strftime("%Y-%m")
        mk = ts.strftime("%Y-%m-%d %H:%M")
        c.execute("INSERT INTO usage_samples (peer_id, ts, rx, tx, endpoint) VALUES (?,?,?,?,'')",
                  (pid, ts.strftime(FMT), rx, tx))
        prev = self.last.get(pid)
        self.last[pid] = {"rx": rx, "tx": tx}
        if prev is None:
            return
        cur = {"rx": rx, "tx": tx}
        actual, target, dropped_any = {}, {}, False
        for d in ("rx", "tx"):
            dropped = cur[d] < prev[d]
            dropped_any = dropped_any or dropped
            f = fresh(prev[d], cur[d])
            old = 0 if dropped else cur[d] - prev[d]
            qk = (pid, d, day)
            if code == "old":
                if dropped and prev[d] >= NEAR32 and cur[d] <= LOW and qk not in self.quar:
                    self.quar[qk] = ts
                    c.execute("INSERT INTO settings_kv VALUES (?, ?)", (
                        f"usage_counter_unstable:{pid}:{d}:{day}", ts.replace(tzinfo=timezone.utc).isoformat()))
                    c.execute("INSERT INTO actions (peer_id, ts, action, note) VALUES (?,?,?,?)",
                              (pid, ts.strftime(FMT), "usage_anomaly", f"Quarantined {d} usage"))
                actual[d] = 0 if qk in self.quar else old
            else:
                actual[d] = f
            target[d] = f if qk in self.quar else actual[d]
            if hold == d and actual[d]:
                c.execute(
                    "INSERT INTO usage_holds (peer_id, direction, reason, start_ts, end_ts, bytes, minutes_json, status)"
                    " VALUES (?, ?, 'one_sided', ?, ?, ?, ?, 'pending')",
                    (pid, d, ts.strftime(FMT), ts.strftime(FMT), actual[d],
                     json.dumps({ts.strftime("%Y-%m-%d %H:%M:00"): actual[d]})))
                actual[d] = target[d] = 0  # held bytes are not usage until approved
        if dropped_any:
            c.execute("INSERT INTO actions (peer_id, ts, action, note) VALUES (?,?,?,?)", (
                pid, ts.strftime(FMT), "counter_reset",
                f"Detected counter reset: rx {prev['rx']}->{rx}, tx {prev['tx']}->{tx}"))
        if actual["rx"] or actual["tx"]:
            self._bump("usage_minute", "minute_ts", pid, mk + ":00.000000", actual["rx"], actual["tx"])
            self._bump("usage_daily", "day", pid, day, actual["rx"], actual["tx"])
            self._bump("usage_monthly", "month_key", pid, month, actual["rx"], actual["tx"])
        if target["rx"] or target["tx"]:
            for table, key in (("minute", mk), ("daily", day), ("monthly", month)):
                cell = self.expected[table][(pid, key)]
                cell[0] += target["rx"]
                cell[1] += target["tx"]

    def run(self, pid, polls, code_fn=None, holds=None):
        holds = holds or {}
        for ts, rx, tx in polls:
            self.poll(pid, ts, rx, tx, code=code_fn(ts) if code_fn else "old", hold=holds.get(ts))

    def finish(self, raw_keep_after: datetime):
        self.conn.execute("DELETE FROM usage_samples WHERE ts < ?", (raw_keep_after.strftime(FMT),))
        self.conn.commit()
        self.conn.close()

    def target(self):
        return {t: {k: tuple(v) for k, v in rows.items() if v != [0, 0]} for t, rows in self.expected.items()}


def gen_polls(start, end, step, rx, tx, drops, grow):
    """drops: {ts: {dir: value}} (the other direction stays still); grow(i, t) -> (drx, dtx)."""
    out, t, i = [], start, 0
    while t <= end:
        if t in drops:
            rx = drops[t].get("rx", rx)
            tx = drops[t].get("tx", tx)
        else:
            drx, dtx = grow(i, t)
            rx += drx
            tx += dtx
        out.append((t, rx, tx))
        t += step
        i += 1
    return out


def window_fresh(polls, d, start, end):
    idx = 1 if d == "rx" else 2
    total, by_month = 0, defaultdict(int)
    for a, b in zip(polls, polls[1:]):
        if start <= b[0] < end:
            v = fresh(a[idx], b[idx])
            total += v
            by_month[b[0].strftime("%Y-%m")] += v
    return total, dict(by_month)


def dump(path, with_zero=False):
    c = sqlite3.connect(str(path))
    out = {
        "minute": {(p, str(ts)[:16]): (rx, tx) for p, ts, rx, tx in c.execute(
            "SELECT peer_id, minute_ts, rx, tx FROM usage_minute")},
        "daily": {(p, k): (rx, tx) for p, k, rx, tx in c.execute("SELECT peer_id, day, rx, tx FROM usage_daily")},
        "monthly": {(p, k): (rx, tx) for p, k, rx, tx in c.execute(
            "SELECT peer_id, month_key, rx, tx FROM usage_monthly")},
    }
    c.close()
    if not with_zero:
        out = {t: {k: v for k, v in rows.items() if v != (0, 0)} for t, rows in out.items()}
    return out


def full_dump(path):
    c = sqlite3.connect(str(path))
    out = {
        "minute": sorted(c.execute("SELECT peer_id, minute_ts, rx, tx FROM usage_minute")),
        "daily": sorted(c.execute("SELECT peer_id, day, rx, tx FROM usage_daily")),
        "monthly": sorted(c.execute("SELECT peer_id, month_key, rx, tx FROM usage_monthly")),
        "settings": sorted(c.execute("SELECT key, value FROM settings_kv")),
        "actions": sorted(c.execute("SELECT peer_id, ts, action, note FROM actions")),
    }
    c.close()
    return out


def run_tool(*args):
    buf = io.StringIO()
    code = rq.main([str(a) for a in args], out=buf)
    return code, buf.getvalue()


def set_kv(path, key, value):
    c = sqlite3.connect(str(path))
    c.execute("INSERT OR REPLACE INTO settings_kv VALUES (?, ?)", (key, value))
    c.commit()
    c.close()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# scenario: 64-bit-era reset (Amir-PC style), drop-log mode, Tehran midnight
# ---------------------------------------------------------------------------

AMIR = 28
A_TQ = datetime(2026, 9, 30, 22, 10)       # Tehran 2026-10-01 01:40
A_D1 = datetime(2026, 10, 1, 6, 0)         # second tx drop the same Tehran day
A_DAY_END = datetime(2026, 10, 1, 20, 30)  # Tehran midnight -> 2026-10-02
A_NEXT = datetime(2026, 10, 2, 3, 0)       # first tx drop after the day end
A_NOW = datetime(2026, 10, 3, 12, 0, 30)


def amir_polls():
    return gen_polls(
        datetime(2026, 9, 30, 12, 0), datetime(2026, 10, 3, 12, 0), timedelta(minutes=10),
        rx=1_000_000_000, tx=6_829_000_000,
        drops={A_TQ: {"tx": 0}, A_D1: {"tx": 5_000}, A_NEXT: {"tx": 1_000}},
        # upload is exactly 1/10 of download, so the activity-weighted spread is exact
        grow=lambda i, t: (10_000 * ((i * 7) % 11), 100_000 * ((i * 7) % 11)),
    )


def build_amir(tmp_path, settings=None, name="main.db"):
    sim = Sim(tmp_path / name, settings)
    sim.add_peer(AMIR, "Amir-PC")
    polls = amir_polls()
    sim.run(AMIR, polls)
    sim.finish(raw_keep_after=A_NOW - timedelta(hours=24))
    return sim, polls


def test_64bit_reset_drop_log_exact_across_tehran_midnight(tmp_path):
    sim, polls = build_amir(tmp_path)
    db = sim.path
    before = dump(db)
    assert sim.quar == {(AMIR, "tx", "2026-10-01"): A_TQ}

    hidden, by_month = window_fresh(polls, "tx", A_TQ, A_DAY_END)
    assert set(by_month) == {"2026-09", "2026-10"} and by_month["2026-09"] > 0

    code, out = run_tool("--db", db, "--now", A_NOW.isoformat(), "--apply", "--allow-old-code", "--verbose")
    assert code == 0, out
    assert "mode=drop-log" in out
    after = dump(db)

    # whole tables equal the fresh-start target, minute by minute
    assert after == sim.target()
    # and the explicit numbers
    assert after["daily"][(AMIR, "2026-10-01")][1] - before["daily"][(AMIR, "2026-10-01")][1] == hidden
    assert after["daily"][(AMIR, "2026-10-01")][0] == before["daily"][(AMIR, "2026-10-01")][0]
    for day in ("2026-09-30", "2026-10-02", "2026-10-03"):
        assert after["daily"][(AMIR, day)] == before["daily"][(AMIR, day)]
    for month, add in by_month.items():
        assert after["monthly"][(AMIR, month)][1] - before["monthly"].get((AMIR, month), (0, 0))[1] == add
    added_minutes = {k: v[1] - before["minute"].get(k, (0, 0))[1] for k, v in after["minute"].items()}
    added_minutes = {k: v for k, v in added_minutes.items() if v}
    assert sum(added_minutes.values()) == hidden
    assert min(k[1] for k in added_minutes) >= "2026-09-30 22:10"
    assert max(k[1] for k in added_minutes) <= "2026-10-01 20:29"

    c = sqlite3.connect(db)
    marker = json.loads(c.execute("SELECT value FROM settings_kv WHERE key=?",
                                  (f"usage_quarantine_repaired:{AMIR}:tx:2026-10-01",)).fetchone()[0])
    note = c.execute("SELECT note FROM actions WHERE action='usage_repair' AND peer_id=?", (AMIR,)).fetchone()[0]
    assert c.execute("SELECT COUNT(*) FROM settings_kv WHERE key LIKE 'usage_counter_unstable:%'").fetchone()[0] == 1
    c.close()
    assert marker["bytes"] == hidden and marker["run_id"] and marker["applied_at"]
    assert note == (f"Usage repair: added back {hidden / GIB:.2f} GB download and 0.00 GB upload hidden by "
                    "the old counter quarantine on 1 day (2026-10-01).")


def test_32bit_rollover_closes_from_latest_sample(tmp_path):
    pid, tq = 7, datetime(2026, 10, 20, 10, 0)
    day_end, now = datetime(2026, 10, 20, 20, 30), datetime(2026, 10, 23, 0, 0, 30)
    polls = gen_polls(
        datetime(2026, 10, 20, 6, 0), datetime(2026, 10, 23, 0, 0), timedelta(minutes=15),
        rx=4_293_000_000, tx=50_000_000, drops={tq: {"rx": 3_000_000}},
        grow=lambda i, t: (100_000 * (i % 5), 10_000 * (i % 5)) if t < tq else (90_000 * (i % 4), 9_000 * (i % 4)),
    )
    assert polls[[p[0] for p in polls].index(tq) - 1][1] > 4_294_000_000  # just below 2**32
    sim = Sim(tmp_path / "main.db")
    sim.add_peer(pid, "phone")
    sim.run(pid, polls)
    sim.finish(raw_keep_after=now - timedelta(hours=24))
    before = dump(sim.path)

    hidden, _ = window_fresh(polls, "rx", tq, day_end)
    code, out = run_tool("--db", sim.path, "--now", now.isoformat(), "--apply", "--allow-old-code", "--verbose")
    assert code == 0, out
    after = dump(sim.path)
    assert after == sim.target()
    assert after["daily"][(pid, "2026-10-20")][0] - before["daily"][(pid, "2026-10-20")][0] == hidden
    # fresh-start counts the 3_000_000 after the wrap, not the wrap remainder
    assert hidden == 3_000_000 + sum(90_000 * (i % 4) for i, p in enumerate(polls) if tq < p[0] < day_end)
    assert "upload" in out and "1405-07 Mehr" in out


def test_negative_final_segment_is_clamped_and_reported(tmp_path):
    pid, tq = 7, datetime(2026, 10, 20, 10, 0)
    now = datetime(2026, 10, 23, 0, 0, 30)
    polls = gen_polls(
        datetime(2026, 10, 20, 6, 0), datetime(2026, 10, 23, 0, 0), timedelta(minutes=15),
        rx=4_293_000_000, tx=50_000_000, drops={tq: {"rx": 3_000_000}},
        grow=lambda i, t: (100_000, 10_000),
    )
    sim = Sim(tmp_path / "main.db")
    sim.add_peer(pid, "phone")
    sim.run(pid, polls)
    sim.finish(raw_keep_after=now - timedelta(hours=24))
    c = sqlite3.connect(sim.path)  # a bogus counted minute after the day end makes M too small
    c.execute("UPDATE usage_minute SET rx = rx + 999000000000 WHERE peer_id=? AND minute_ts LIKE '2026-10-21 05:00%'",
              (pid,))
    c.commit()
    c.close()
    code, out = run_tool("--db", sim.path, "--now", now.isoformat())
    assert code == 0, out
    assert "clamped negative values: 1" in out
    assert "clamped to 0" in out


# ---------------------------------------------------------------------------
# raw-sample exact mode
# ---------------------------------------------------------------------------

def test_raw_sample_exact_mode(tmp_path):
    pid = 9
    tq = datetime(2026, 10, 29, 12, 0, 45)
    d1 = datetime(2026, 10, 29, 15, 0, 15)
    now = datetime(2026, 10, 29, 21, 0, 0)
    polls = gen_polls(
        datetime(2026, 10, 28, 19, 0, 15), datetime(2026, 10, 29, 20, 59, 45), timedelta(seconds=30),
        rx=5_000_000, tx=3_899_000_000, drops={tq: {"tx": 100}, d1: {"tx": 50}},
        grow=lambda i, t: (0, (i * 7919) % 300_000),  # upload idle: drop-log mode could not place these
    )
    sim = Sim(tmp_path / "main.db")
    sim.add_peer(pid, "laptop")
    sim.run(pid, polls)
    sim.finish(raw_keep_after=now - timedelta(hours=24))
    before = dump(sim.path)
    assert before["minute"][(pid, "2026-10-29 12:00")][1] > 0  # a pre-trigger poll in the trigger minute

    hidden, _ = window_fresh(polls, "tx", tq, datetime(2026, 10, 29, 20, 30))
    code, out = run_tool("--db", sim.path, "--now", now.isoformat(), "--apply", "--allow-old-code", "--verbose")
    assert code == 0, out
    assert "mode=raw" in out and "from raw samples" in out
    after = dump(sim.path)
    assert after == sim.target()
    assert after["daily"][(pid, "2026-10-29")][1] - before["daily"][(pid, "2026-10-29")][1] == hidden


# ---------------------------------------------------------------------------
# partially counted window (fixed code deployed mid-window)
# ---------------------------------------------------------------------------

def test_partially_counted_window_adds_only_the_difference(tmp_path):
    pid = 11
    tq = datetime(2026, 10, 10, 2, 0)
    deploy = datetime(2026, 10, 10, 9, 0)
    d_after = datetime(2026, 10, 10, 14, 0)    # counted by the fixed code (cur goes in)
    nxt = datetime(2026, 10, 11, 5, 0)
    now = datetime(2026, 10, 11, 20, 0, 30)
    polls = gen_polls(
        datetime(2026, 10, 9, 18, 0), datetime(2026, 10, 11, 20, 0), timedelta(minutes=10),
        rx=2_000_000, tx=4_000_000_000, drops={tq: {"tx": 1_000}, d_after: {"tx": 7_000}, nxt: {"tx": 30_000}},
        grow=lambda i, t: (3_000 * (i % 6), 30_000 * (i % 6)),
    )
    sim = Sim(tmp_path / "main.db")
    sim.add_peer(pid, "tablet")
    sim.run(pid, polls, code_fn=lambda t: "fixed" if t >= deploy else "old")
    sim.finish(raw_keep_after=now - timedelta(hours=24))
    before = dump(sim.path)

    hidden, _ = window_fresh(polls, "tx", tq, deploy)
    code, out = run_tool("--db", sim.path, "--now", now.isoformat(), "--apply", "--allow-old-code", "--verbose")
    assert code == 0, out
    assert "mode=drop-log" in out
    after = dump(sim.path)
    assert after == sim.target()
    assert after["daily"][(pid, "2026-10-10")][1] - before["daily"][(pid, "2026-10-10")][1] == hidden
    deploy_key = deploy.strftime("%Y-%m-%d %H:%M")
    for k, v in before["minute"].items():
        if k[1] >= deploy_key:
            assert after["minute"][k] == v


def test_pending_holds_count_as_already_counted(tmp_path):
    """Bytes the fixed code put on hold (in the window and before the closing drop) are never re-added."""
    pid = 12
    tq = datetime(2026, 10, 10, 2, 0)
    deploy = datetime(2026, 10, 10, 9, 0)
    held_in_window = datetime(2026, 10, 10, 12, 0)
    held_after_day = datetime(2026, 10, 11, 1, 0)
    nxt = datetime(2026, 10, 11, 5, 0)
    now = datetime(2026, 10, 11, 20, 0, 30)
    polls = gen_polls(
        datetime(2026, 10, 9, 18, 0), datetime(2026, 10, 11, 20, 0), timedelta(minutes=10),
        rx=2_000_000, tx=4_000_000_000, drops={tq: {"tx": 1_000}, nxt: {"tx": 30_000}},
        grow=lambda i, t: (3_000 * (i % 6 + 1), 30_000 * (i % 6 + 1)),
    )
    sim = Sim(tmp_path / "main.db")
    sim.add_peer(pid, "held")
    sim.run(pid, polls, code_fn=lambda t: "fixed" if t >= deploy else "old",
            holds={held_in_window: "tx", held_after_day: "tx"})
    sim.finish(raw_keep_after=now - timedelta(hours=24))
    before = dump(sim.path)

    hidden, _ = window_fresh(polls, "tx", tq, deploy)
    code, out = run_tool("--db", sim.path, "--now", now.isoformat(), "--apply", "--allow-old-code", "--verbose")
    assert code == 0, out
    assert "mode=drop-log" in out and "usage holds treated as already counted" in out
    after = dump(sim.path)
    assert after == sim.target()
    assert after["daily"][(pid, "2026-10-10")][1] - before["daily"][(pid, "2026-10-10")][1] == hidden


@pytest.mark.parametrize("raw_hours,mode", [(24, "raw"), (2, "drop-log")])
def test_open_window_after_deploy(tmp_path, raw_hours, mode):
    """Today's window is still open; the fixed code has counted since the deploy."""
    pid = 15
    tq = datetime(2026, 10, 30, 2, 0)
    d1 = datetime(2026, 10, 30, 6, 0)
    deploy = datetime(2026, 10, 30, 9, 0)
    now = datetime(2026, 10, 30, 15, 0, 30)  # Tehran 18:30, the day ends 20:30 UTC
    polls = gen_polls(
        datetime(2026, 10, 29, 20, 0), datetime(2026, 10, 30, 15, 0), timedelta(minutes=10),
        rx=2_000_000, tx=4_100_000_000, drops={tq: {"tx": 2_000}, d1: {"tx": 9_000}},
        grow=lambda i, t: (5_000 * (i % 4), 50_000 * (i % 4)),
    )
    sim = Sim(tmp_path / "main.db")
    sim.add_peer(pid, "desk")
    sim.run(pid, polls, code_fn=lambda t: "fixed" if t >= deploy else "old")
    sim.finish(raw_keep_after=now - timedelta(hours=raw_hours))
    before = dump(sim.path)

    hidden, _ = window_fresh(polls, "tx", tq, deploy)
    code, out = run_tool("--db", sim.path, "--now", now.isoformat(), "--apply", "--allow-old-code", "--verbose")
    assert code == 0, out
    assert f"mode={mode}" in out
    after = dump(sim.path)
    assert after == sim.target()
    assert after["daily"][(pid, "2026-10-30")][1] - before["daily"][(pid, "2026-10-30")][1] == hidden


# ---------------------------------------------------------------------------
# one-sided segment held back
# ---------------------------------------------------------------------------

def test_one_sided_segment_is_skipped_and_reported(tmp_path):
    pid = 13
    tq = datetime(2026, 10, 15, 1, 0)
    d1 = datetime(2026, 10, 15, 5, 0)
    day_end = datetime(2026, 10, 15, 20, 30)
    nxt = datetime(2026, 10, 16, 2, 0)
    now = datetime(2026, 10, 18, 0, 0, 30)

    def grow(i, t):
        if tq < t < d1:
            return (4_000, 90 * MIB)  # ~2 GiB download with ~100 KB upload: counter garbage
        return (2_000 * (i % 3), 20_000 * (i % 3))

    polls = gen_polls(datetime(2026, 10, 14, 20, 0), datetime(2026, 10, 18, 0, 0), timedelta(minutes=10),
                      rx=10_000_000, tx=3_800_000_000, drops={tq: {"tx": 10}, d1: {"tx": 20}, nxt: {"tx": 5}},
                      grow=grow)
    sim = Sim(tmp_path / "main.db")
    sim.add_peer(pid, "router-x")
    sim.run(pid, polls)
    sim.finish(raw_keep_after=now - timedelta(hours=24))
    before = dump(sim.path)

    one_sided, _ = window_fresh([p for p in polls if tq <= p[0] < d1], "tx", tq + timedelta(seconds=1), d1)
    assert one_sided >= GIB
    total, _ = window_fresh(polls, "tx", tq, day_end)
    code, out = run_tool("--db", sim.path, "--now", now.isoformat(), "--apply", "--allow-old-code")
    assert code == 0, out
    assert "one-sided segments held back: 1" in out
    assert "one-sided segments NOT added (manual review)" in out and f"{pid}:tx:2026-10-15" in out
    after = dump(sim.path)
    added = after["daily"][(pid, "2026-10-15")][1] - before["daily"][(pid, "2026-10-15")][1]
    assert added == total - one_sided
    assert added == 10 + 20 + sum(20_000 * (i % 3) for i, p in enumerate(polls) if d1 < p[0] < day_end)
    ledger_path = re.search(r"ledger: (\S+)", out).group(1)
    ledger = json.loads(Path(ledger_path).read_text())
    assert len(ledger["keys"][f"{pid}:tx:2026-10-15"]["flagged_segments"]) == 1


# ---------------------------------------------------------------------------
# lifecycle: dry run, refusal, backup, idempotency, undo
# ---------------------------------------------------------------------------

def test_dry_run_refusal_apply_idempotent_and_undo(tmp_path):
    sim, _polls = build_amir(tmp_path)
    db = sim.path
    backups = tmp_path / "backups"
    now = A_NOW.isoformat()
    original_hash = sha(db)

    # dry run writes nothing (also no backup dir), but can write the CSV report
    csv_path = tmp_path / "report.csv"
    code, out = run_tool("--db", db, "--now", now, "--report", csv_path)
    assert code == 0, out
    assert "DRY RUN" in out and "1405-07 Mehr" in out and "Amir-PC" in out
    assert sha(db) == original_hash and not backups.exists()
    rows = csv_path.read_text().splitlines()
    assert rows[0].startswith("peer_id,peer_name,persian_month") and "Amir-PC" in rows[1]

    # refusal without the version marker: nothing written, no backup
    code, out = run_tool("--db", db, "--now", now, "--apply")
    assert code == 2
    assert "usage_counting_version" in out and "Refusing to apply" in out
    assert sha(db) == original_hash and not backups.exists()

    set_kv(db, "usage_counting_version", "2")
    snapshot = full_dump(db)

    code, out = run_tool("--db", db, "--now", now, "--apply")
    assert code == 0, out
    backup_path = Path(re.search(r"backup: (\S+)", out).group(1))
    ledger_path = Path(re.search(r"ledger: (\S+)", out).group(1))
    assert backup_path.parent == backups and backup_path.name.startswith("wgmik-pre-quarantine-repair-")
    assert full_dump(backup_path) == snapshot
    ledger = json.loads(ledger_path.read_text())
    assert ledger["run_id"] and ledger["increments"]
    assert {i["table"] for i in ledger["increments"]} == {"usage_minute", "usage_daily", "usage_monthly"}
    applied = full_dump(db)
    assert applied["minute"] != snapshot["minute"]

    # second apply: nothing to do, nothing written, no extra backup
    code, out = run_tool("--db", db, "--now", now, "--apply")
    assert code == 0, out
    assert "Nothing to repair" in out and "1 already repaired" in out
    assert full_dump(db) == applied
    assert len(list(backups.glob("wgmik-pre-quarantine-repair-*.db"))) == 1

    # undo restores the exact original usage rows and removes the markers
    code, out = run_tool("--db", db, "--now", now, "--undo", ledger_path)
    assert code == 0, out
    restored = full_dump(db)
    for table in ("minute", "daily", "monthly"):
        assert restored[table] == snapshot[table]
    assert restored["settings"] == snapshot["settings"]
    assert [a[2] for a in restored["actions"] if a[2].startswith("usage_repair")] == ["usage_repair",
                                                                                       "usage_repair_undone"]
    assert len(list(backups.glob("wgmik-pre-quarantine-repair-undo-*.db"))) == 1

    # a second undo refuses and writes nothing
    code, out = run_tool("--db", db, "--now", now, "--undo", ledger_path)
    assert code == 2 and "Refusing to undo" in out
    assert full_dump(db) == restored

    # and the repair can be applied again after an undo
    code, out = run_tool("--db", db, "--now", now, "--apply")
    assert code == 0 and "APPLIED" in out
    assert dump(db) == sim.target()


def test_refuses_after_maintenance_rebuilt_minutes_since_fix(tmp_path):
    sim, _polls = build_amir(tmp_path)
    db = sim.path
    now = A_NOW.isoformat()
    set_kv(db, "usage_counting_version", "2")
    set_kv(db, "usage_counting_version_since", "2026-10-30T12:00:00+00:00")
    set_kv(db, "usage_maintenance_status", json.dumps({"started_at": "2026-10-30T23:30:00+00:00"}))
    original_hash = sha(db)

    code, out = run_tool("--db", db, "--now", now, "--apply")
    assert code == 2
    assert "usage maintenance started" in out and "--allow-after-maintenance" in out
    assert sha(db) == original_hash and not (tmp_path / "backups").exists()

    # maintenance that ran before the fix went live is fine
    set_kv(db, "usage_maintenance_status", json.dumps({"started_at": "2026-10-29T23:30:00+00:00"}))
    code, out = run_tool("--db", db, "--now", now, "--apply")
    assert code == 0 and "APPLIED" in out, out


def test_minute_retention_and_extra_minutes_database(tmp_path):
    sim, polls = build_amir(tmp_path, settings={"minute_rollup_retention_days": "1"})
    db = Path(sim.path)
    old = tmp_path / "old.db"
    shutil.copy(db, old)  # the older database still holds every minute
    cutoff = (A_NOW - timedelta(days=1)).strftime(FMT)
    c = sqlite3.connect(str(db))
    c.execute("DELETE FROM usage_minute WHERE minute_ts < ?", (cutoff,))
    c.commit()
    c.close()
    before = dump(db)

    code, out = run_tool("--db", db, "--now", A_NOW.isoformat())
    assert code == 0, out
    assert "1 skipped" in out and "--extra-minutes" in out

    code, out = run_tool("--db", db, "--extra-minutes", old, "--now", A_NOW.isoformat(), "--apply",
                         "--allow-old-code")
    assert code == 0, out
    after = dump(db)
    target = sim.target()
    assert after["minute"] == before["minute"]  # every window minute is older than retention
    assert after["daily"] == target["daily"]
    assert after["monthly"] == target["monthly"]
    assert sha(old) == sha(old)  # opened read-only
    assert dump(old)["minute"] != after["minute"]


def test_jalali_matches_calendar_utils_and_labels():
    from backend.calendar_utils import gregorian_to_jalali

    d = date(2026, 1, 1)
    while d < date(2028, 1, 1):
        assert rq.gregorian_to_jalali(d.year, d.month, d.day) == gregorian_to_jalali(d.year, d.month, d.day)
        d += timedelta(days=1)
    assert rq.persian_month_label("2026-06-22") == "1405-04 Tir"
    assert rq.persian_month_label("2026-07-23") == "1405-05 Mordad"
    assert rq.persian_month_label("2026-08-23") == "1405-06 Shahrivar"
    assert rq.persian_month_label("2026-09-23") == "1405-07 Mehr"
    assert rq.persian_month_label("2026-10-30") == "1405-08 Aban"


def test_helpers():
    assert rq.parse_ts("2026-10-22T10:15:03.123456+00:00") == datetime(2026, 10, 22, 10, 15, 3, 123456)
    assert rq.parse_ts("2026-10-22 10:15:03") == datetime(2026, 10, 22, 10, 15, 3)
    assert rq.parse_ts("2026-10-22T13:45:03+03:30") == datetime(2026, 10, 22, 10, 15, 3)
    clock = rq.LocalClock("Asia/Tehran")
    assert clock.day_bounds("2026-10-01") == (datetime(2026, 9, 30, 20, 30), datetime(2026, 10, 1, 20, 30))
    assert clock.day_of(datetime(2026, 10, 1, 20, 29, 59)) == "2026-10-01"
    assert clock.day_of(datetime(2026, 10, 1, 20, 30)) == "2026-10-02"
    assert rq.allocate(10, [("a", 1), ("b", 1), ("c", 1)]) == {"a": 4, "b": 3, "c": 3}
    assert sum(rq.allocate(1_000_003, [("a", 7), ("b", 0), ("c", 13)]).values()) == 1_000_003
