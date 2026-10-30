"""One-off repair: add back usage hidden by the old near-32-bit counter quarantine.

Background
----------
From 2026-06-22 until the fresh-start fix, the poller zeroed a peer's counted
usage in one direction for the rest of the local (Tehran) day whenever that
direction's cumulative counter fell from >= 3.5 GiB to <= 768 MiB.  Each trigger
is remembered in settings_kv as ``usage_counter_unstable:{peer}:{rx|tx}:{day}``
whose value is the ISO UTC time of the triggering poll ("t_q").

The fixed code counts with the *fresh-start rule*: for two consecutive readings,
delta = cur - prev when cur >= prev, else delta = cur (a drop is a reset; the bytes
since the reset count).  For every quarantined (peer, direction D, day X) this tool
adds back exactly

    hidden = fresh-start bytes of the polls in the window [t_q, end of day X)
             - D bytes usage_minute already counted in that window

Subtracting what was counted keeps the result right when part of the window was
already counted by the fixed code after its deploy.

How the window is rebuilt
-------------------------
* Raw mode (exact): usage_samples still holds a reading before the trigger poll,
  so every poll of the window is replayed with the fresh-start rule.  Each poll's
  bytes go to that poll's own minute.
* Drop-log mode: actions.counter_reset notes carry both counters just before and
  just after every drop.  With d_0 = the drop at t_q and d_1..d_k the later
  D-drops of that day, the fresh-start rule counts
      sum(cur(d_i)) + sum(prev(d_i+1) - cur(d_i)) + (M - cur(d_k))
  where M is the D counter at the last poll of day X:
      next D-drop n after the day exists: M = prev(n) - D bytes counted from the
          day end up to just before n (the n poll itself counted 0 for D in the old
          code; when raw samples still cover n's minute, the part of that minute
          before n is replayed from them instead);
      otherwise: M = latest raw D reading - D bytes counted since the day end;
      window still open (day end in the future): the last segment runs to the
          latest raw sample.
  cur(d_i) lands in d_i's minute; each growth segment is spread over its minutes in
  proportion to the opposite direction's counted bytes (they show when the peer was
  active), or evenly when those are all zero.

Safety rules
------------
* A growth segment of >= 1 GiB whose opposite direction grew by < 0.3 % over the
  same readings is NOT added (reported for manual review) unless the opposite
  direction also dropped inside that segment.
* Negative intermediate values (polling gaps) are clamped to 0 and reported.
* Bytes in pending or discarded usage_holds (the fixed code's held-back usage) count
  as already counted, both inside the window and when finding M, so a later hold
  approval can never count them a second time.
* "Counted" bytes for minutes that no minute table holds any more are taken as 0.
  That is acceptable only because the quarantine zeroed the direction for the
  whole rest of a past day; the M computation, which needs real counted data after
  the day end, is refused (key skipped) when minute data does not reach back that
  far.  Pass --extra-minutes with an older database to extend coverage.
* Default is a dry run that writes nothing.  --apply refuses unless the fixed code
  has written settings_kv usage_counting_version = '2', backs the database up with
  the sqlite3 backup API first, writes everything in one BEGIN IMMEDIATE
  transaction, records every increment in a JSON ledger and marks each repaired
  key with ``usage_quarantine_repaired:{peer}:{dir}:{day}`` so a rerun skips it.
  --undo LEDGER subtracts exactly the ledger's increments.

Usage
-----
    python -m backend.tools.repair_quarantine_usage --db /data/wgmik.db \
        [--extra-minutes /data/old.db] [--apply | --undo LEDGER] \
        [--report out.csv] [--peer ID ...] [--verbose] [--now ISO]

Stdlib only (runs with plain python3 inside the production container).
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import os
import re
import sqlite3
import sys
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

GIB = 1024 ** 3
ONE_SIDED_MIN_BYTES = GIB
ONE_SIDED_RATIO = 0.003
DROP_MATCH_SECONDS = 5
SAMPLE_MATCH_SECONDS = 1
BUSY_TIMEOUT_MS = 30000

QUARANTINE_PREFIX = "usage_counter_unstable:"
REPAIRED_PREFIX = "usage_quarantine_repaired:"
VERSION_KEY = "usage_counting_version"
VERSION_SINCE_KEY = "usage_counting_version_since"
MAINTENANCE_STATUS_KEY = "usage_maintenance_status"
REQUIRED_VERSION = "2"
ACTION_APPLY = "usage_repair"
ACTION_UNDO = "usage_repair_undone"
TS_FMT = "%Y-%m-%d %H:%M:%S.%f"
DIRECTIONS = ("rx", "tx")

PERSIAN_MONTH_NAMES = [
    "Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
    "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand",
]

NOTE_RE = re.compile(r"^Detected counter reset: rx (\d+)->(\d+), tx (\d+)->(\d+)$")
TS_RE = re.compile(
    r"^\s*(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?)?"
    r"\s*(Z|[+-]\d{2}:?\d{2})?\s*$"
)


class RepairError(Exception):
    """Refusal or failure that must stop the run without writing anything."""


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------

def parse_ts(value: Any) -> datetime:
    """Parse a SQLite/ISO timestamp into a naive UTC datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    m = TS_RE.match(str(value))
    if not m:
        raise ValueError(f"unparseable timestamp: {value!r}")
    y, mo, d, hh, mi, ss, frac, tz = m.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    dt = datetime(int(y), int(mo), int(d), int(hh or 0), int(mi or 0), int(ss or 0), micro)
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        dt -= sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0))
    return dt


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def minute_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def key_to_dt(key: str) -> datetime:
    return datetime(int(key[0:4]), int(key[5:7]), int(key[8:10]), int(key[11:13]), int(key[14:16]))


def next_minute_key(key: str) -> str:
    return minute_key(key_to_dt(key) + timedelta(minutes=1))


def normalize_minute_key(raw: Any) -> Optional[str]:
    text = str(raw)
    if len(text) < 16 or text[4] != "-" or text[13] != ":":
        try:
            return minute_key(parse_ts(text))
        except ValueError:
            return None
    if text[10] != " ":
        text = text[:10] + " " + text[11:]
    return text[:16]


def sqlite_ts(dt: datetime) -> str:
    return dt.strftime(TS_FMT)


def iso_utc(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat()


def gib(n: float) -> float:
    return n / GIB


class LocalClock:
    """Local-day arithmetic. Asia/Tehran is fixed UTC+03:30 (no DST since 2022)."""

    def __init__(self, name: str):
        self.name = name or "UTC"
        self._fixed: Optional[timedelta] = None
        self._zone = None
        if self.name in ("Asia/Tehran", "Iran"):
            self._fixed = timedelta(hours=3, minutes=30)
        elif self.name in ("UTC", "Etc/UTC", "GMT", "Z"):
            self._fixed = timedelta(0)
        else:
            from zoneinfo import ZoneInfo  # stdlib

            self._zone = ZoneInfo(self.name)

    def describe(self) -> str:
        if self._fixed is not None:
            total = int(self._fixed.total_seconds() // 60)
            return f"{self.name} (fixed UTC{'+' if total >= 0 else '-'}{abs(total) // 60:02d}:{abs(total) % 60:02d})"
        return f"{self.name} (zoneinfo)"

    def to_local(self, utc_naive: datetime) -> datetime:
        if self._fixed is not None:
            return utc_naive + self._fixed
        return utc_naive.replace(tzinfo=timezone.utc).astimezone(self._zone).replace(tzinfo=None)

    def day_of(self, utc_naive: datetime) -> str:
        return self.to_local(utc_naive).strftime("%Y-%m-%d")

    def day_bounds(self, day: str) -> Tuple[datetime, datetime]:
        local = datetime(int(day[0:4]), int(day[5:7]), int(day[8:10]))
        nxt = local + timedelta(days=1)
        if self._fixed is not None:
            return local - self._fixed, nxt - self._fixed
        start = local.replace(tzinfo=self._zone).astimezone(timezone.utc).replace(tzinfo=None)
        end = nxt.replace(tzinfo=self._zone).astimezone(timezone.utc).replace(tzinfo=None)
        return start, end


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> Tuple[int, int, int]:
    """Same algorithm as backend.calendar_utils.gregorian_to_jalali (kept local: stdlib only)."""
    g_days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    j_days_in_month = [31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
    gy -= 1600
    gm -= 1
    gd -= 1
    g_day_no = 365 * gy + (gy + 3) // 4 - (gy + 99) // 100 + (gy + 399) // 400
    for i in range(gm):
        g_day_no += g_days_in_month[i]
    if gm > 1 and ((gy + 1600) % 4 == 0 and ((gy + 1600) % 100 != 0 or (gy + 1600) % 400 == 0)):
        g_day_no += 1
    g_day_no += gd
    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053
    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461
    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365
    jm = 0
    while jm < 11 and j_day_no >= j_days_in_month[jm]:
        j_day_no -= j_days_in_month[jm]
        jm += 1
    return jy, jm + 1, j_day_no + 1


def persian_month_label(day: str) -> str:
    jy, jm, _ = gregorian_to_jalali(int(day[0:4]), int(day[5:7]), int(day[8:10]))
    return f"{jy}-{jm:02d} {PERSIAN_MONTH_NAMES[jm - 1]}"


def fresh_delta(prev: int, cur: int) -> int:
    return cur - prev if cur >= prev else cur


def other(direction: str) -> str:
    return "tx" if direction == "rx" else "rx"


def allocate(total: int, items: Sequence[Tuple[str, int]]) -> Dict[str, int]:
    """Split integer `total` over keys in proportion to positive weights (largest remainder)."""
    items = [(k, int(w)) for k, w in items if int(w) > 0]
    if total <= 0 or not items:
        return {}
    weight_sum = sum(w for _, w in items)
    out = {k: total * w // weight_sum for k, w in items}
    rest = total - sum(out.values())
    if rest:
        order = sorted(items, key=lambda kw: (-((total * kw[1]) % weight_sum), kw[0]))
        for k, _ in order[:rest]:
            out[k] += 1
    return {k: v for k, v in out.items() if v}


def minute_range(lo_key: str, hi_key: str) -> List[str]:
    if hi_key < lo_key:
        return [lo_key]
    out = []
    cur = key_to_dt(lo_key)
    end = key_to_dt(hi_key)
    while cur <= end:
        out.append(minute_key(cur))
        cur += timedelta(minutes=1)
    return out


# ---------------------------------------------------------------------------
# data containers
# ---------------------------------------------------------------------------

@dataclass
class Event:
    ts: datetime
    prev: Dict[str, int]
    cur: Dict[str, int]

    def dropped(self, direction: str) -> bool:
        return self.cur[direction] < self.prev[direction]


@dataclass
class Sample:
    ts: datetime
    vals: Dict[str, int]


class MinuteSeries:
    """Counted usage_minute values of one peer (main DB preferred over --extra-minutes)."""

    def __init__(self, rows: Dict[str, Tuple[int, int]]):
        self.rows = rows
        self.keys = sorted(rows)
        self._pref: Dict[str, List[int]] = {}
        for idx, direction in ((0, "rx"), (1, "tx")):
            acc = 0
            out = [0]
            for k in self.keys:
                acc += rows[k][idx]
                out.append(acc)
            self._pref[direction] = out

    def value(self, direction: str, key: str) -> int:
        row = self.rows.get(key)
        if row is None:
            return 0
        return row[0] if direction == "rx" else row[1]

    def total(self, direction: str, lo: Optional[str] = None, hi: Optional[str] = None) -> int:
        """Sum over minutes lo <= key < hi (None = unbounded)."""
        a = 0 if lo is None else bisect.bisect_left(self.keys, lo)
        b = len(self.keys) if hi is None else bisect.bisect_left(self.keys, hi)
        if b <= a:
            return 0
        pref = self._pref[direction]
        return pref[b] - pref[a]

    def between(self, direction: str, lo: str, hi_inclusive: str) -> List[Tuple[str, int]]:
        a = bisect.bisect_left(self.keys, lo)
        b = bisect.bisect_right(self.keys, hi_inclusive)
        idx = 0 if direction == "rx" else 1
        return [(k, self.rows[k][idx]) for k in self.keys[a:b] if self.rows[k][idx] > 0]


@dataclass
class Quarantine:
    peer_id: int
    direction: str
    day: str
    t_q: datetime

    @property
    def tag(self) -> str:
        return f"{self.peer_id}:{self.direction}:{self.day}"


@dataclass
class KeyResult:
    q: Quarantine
    status: str = "ok"  # ok | skipped
    reason: str = ""
    mode: str = ""
    fresh_bytes: int = 0
    counted_bytes: int = 0
    hidden_bytes: int = 0
    adds: Dict[str, int] = field(default_factory=dict)  # minute key -> bytes in q.direction
    clamps: List[str] = field(default_factory=list)
    flagged: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class Context:
    now: datetime
    clock: LocalClock
    covered_from: Optional[str]  # first minute key with complete counted data, None = no data
    minute_retention_days: int
    daily_retention_days: int


@dataclass
class Plan:
    context: Context
    names: Dict[int, str]
    total_keys: int
    already_repaired: List[str]
    results: List[KeyResult]
    warnings: List[str]
    version: Optional[str]


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

def connect_readonly(path: str) -> sqlite3.Connection:
    if not os.path.exists(path):
        raise RepairError(f"Database not found: {path}")
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    except sqlite3.Error:
        conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.execute("PRAGMA query_only=ON")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def _setting(conn: sqlite3.Connection, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM settings_kv WHERE key=?", (key,)).fetchone()
    return None if row is None else row[0]


def _int_setting(conn: sqlite3.Connection, key: str, default: int, lo: int, hi: int) -> int:
    raw = _setting(conn, key)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _minute_bounds(conn: sqlite3.Connection) -> Tuple[Optional[str], Optional[str]]:
    lo = conn.execute("SELECT MIN(minute_ts) FROM usage_minute").fetchone()[0]
    hi = conn.execute("SELECT MAX(minute_ts) FROM usage_minute").fetchone()[0]
    return (normalize_minute_key(lo) if lo else None, normalize_minute_key(hi) if hi else None)


def parse_event(ts: Any, note: Any) -> Optional[Event]:
    m = NOTE_RE.match((note or "").strip())
    if not m:
        return None
    a, b, c, d = (int(x) for x in m.groups())
    return Event(ts=parse_ts(ts), prev={"rx": a, "tx": c}, cur={"rx": b, "tx": d})


def _held_minutes(main: sqlite3.Connection, peer_id: int) -> Dict[str, List[int]]:
    """Bytes of pending/discarded usage holds per minute ({key: [rx, tx]}).

    The fixed code keeps such bytes out of usage_minute until an admin decides; an
    approval adds them back. They are real counter growth that is already accounted
    for, so they are treated exactly like counted bytes (never re-added here).
    """
    out: Dict[str, List[int]] = {}
    for direction, minutes_json in main.execute(
        "SELECT direction, minutes_json FROM usage_holds WHERE peer_id=? AND status IN ('pending', 'discarded')",
        (peer_id,),
    ):
        if direction not in DIRECTIONS:
            continue
        try:
            raw = json.loads(minutes_json or "{}")
        except ValueError:
            continue
        idx = 0 if direction == "rx" else 1
        for key, amount in (raw.items() if isinstance(raw, dict) else []):
            k = normalize_minute_key(key)
            if k:
                out.setdefault(k, [0, 0])[idx] += int(amount or 0)
    return out


def load_peer(main: sqlite3.Connection, extra: Optional[sqlite3.Connection], peer_id: int,
              events: List[Event], max_action_id: int, warnings: List[str], with_holds: bool = False):
    """One short read transaction per peer: newer drop events, raw samples, minutes, holds."""
    main.execute("BEGIN")
    try:
        bad = 0
        for ts, note in main.execute(
            "SELECT ts, note FROM actions WHERE id > ? AND peer_id=? AND action='counter_reset'",
            (max_action_id, peer_id),
        ):
            ev = parse_event(ts, note)
            if ev is None:
                bad += 1
            else:
                events.append(ev)
        samples = [
            Sample(parse_ts(ts), {"rx": int(rx or 0), "tx": int(tx or 0)})
            for ts, rx, tx in main.execute("SELECT ts, rx, tx FROM usage_samples WHERE peer_id=?", (peer_id,))
        ]
        rows: Dict[str, Tuple[int, int]] = {}
        main_rows = main.execute("SELECT minute_ts, rx, tx FROM usage_minute WHERE peer_id=?", (peer_id,)).fetchall()
        held = _held_minutes(main, peer_id) if with_holds else {}
    finally:
        main.execute("COMMIT")
    if bad:
        warnings.append(f"peer {peer_id}: {bad} newer counter_reset note(s) could not be parsed")
    if extra is not None:
        for ts, rx, tx in extra.execute("SELECT minute_ts, rx, tx FROM usage_minute WHERE peer_id=?", (peer_id,)):
            k = normalize_minute_key(ts)
            if k:
                rows[k] = (int(rx or 0), int(tx or 0))
    for ts, rx, tx in main_rows:
        k = normalize_minute_key(ts)
        if k:
            rows[k] = (int(rx or 0), int(tx or 0))
    if held:
        for k, (hrx, htx) in held.items():
            rx, tx = rows.get(k, (0, 0))
            rows[k] = (rx + hrx, tx + htx)
        total = sum(v[0] + v[1] for v in held.values())
        warnings.append(f"peer {peer_id}: {total} bytes in pending/discarded usage holds treated as already counted")
    events.sort(key=lambda e: e.ts)
    samples.sort(key=lambda s: s.ts)
    return samples, MinuteSeries(rows)


def build_plan(main: sqlite3.Connection, extra: Optional[sqlite3.Connection], now: datetime,
               peer_filter: Optional[set] = None, tz_override: Optional[str] = None) -> Plan:
    warnings: List[str] = []
    main.execute("BEGIN")
    try:
        tz_name = tz_override or _setting(main, "timezone") or os.environ.get("TIMEZONE") or "UTC"
        minute_ret = _int_setting(main, "minute_rollup_retention_days", 90, 1, 3650)
        daily_ret = _int_setting(main, "daily_rollup_retention_days", 0, 0, 36500)
        version = _setting(main, VERSION_KEY)
        names = {int(pid): (name or f"peer {pid}") for pid, name in main.execute("SELECT id, name FROM peers")}
        kv = main.execute(
            "SELECT key, value FROM settings_kv WHERE key LIKE 'usage_counter_unstable:%' OR key LIKE 'usage_quarantine_repaired:%'"
        ).fetchall()
        max_action_id = main.execute("SELECT COALESCE(MAX(id), 0) FROM actions").fetchone()[0]
        reset_rows = main.execute(
            "SELECT peer_id, ts, note FROM actions WHERE action='counter_reset' AND peer_id IS NOT NULL"
        ).fetchall()
        main_lo, _main_hi = _minute_bounds(main)
        with_holds = _table_exists(main, "usage_holds")
        merged = _table_exists(main, "peer_totals_merge") and main.execute(
            "SELECT source_peer_id, target_peer_id FROM peer_totals_merge"
        ).fetchall() or []
    finally:
        main.execute("COMMIT")

    clock = LocalClock(tz_name)
    covered_from = None
    if main_lo:
        covered_from = next_minute_key(main_lo)
    if extra is not None:
        extra_lo, extra_hi = _minute_bounds(extra)
        if extra_lo and extra_hi and (covered_from is None or extra_hi >= main_lo):
            covered_from = next_minute_key(extra_lo) if covered_from is None else min(covered_from, next_minute_key(extra_lo))
        elif extra_lo:
            warnings.append(
                f"--extra-minutes ends at {extra_hi} but the main DB minutes start at {main_lo}: "
                "there is a gap, so the extra database is not used for coverage"
            )
    ctx = Context(now=now, clock=clock, covered_from=covered_from,
                  minute_retention_days=minute_ret, daily_retention_days=daily_ret)

    repaired = set()
    quarantines: List[Quarantine] = []
    for key, value in kv:
        if key.startswith(REPAIRED_PREFIX):
            repaired.add(key[len(REPAIRED_PREFIX):])
            continue
        if not key.startswith(QUARANTINE_PREFIX):
            continue
        parts = key.split(":")
        try:
            if len(parts) != 4 or parts[2] not in DIRECTIONS:
                raise ValueError("bad key")
            datetime.strptime(parts[3], "%Y-%m-%d")
            q = Quarantine(int(parts[1]), parts[2], parts[3], parse_ts(value))
        except (ValueError, TypeError):
            warnings.append(f"ignored malformed quarantine key {key!r} = {value!r}")
            continue
        if peer_filter and q.peer_id not in peer_filter:
            continue
        quarantines.append(q)
    quarantines.sort(key=lambda q: (q.peer_id, q.t_q, q.direction))
    already = [q.tag for q in quarantines if q.tag in repaired]
    todo = [q for q in quarantines if q.tag not in repaired]

    events_by_peer: Dict[int, List[Event]] = defaultdict(list)
    bad_notes = defaultdict(int)
    wanted = {q.peer_id for q in todo}
    for pid, ts, note in reset_rows:
        if pid not in wanted:
            continue
        ev = parse_event(ts, note)
        if ev is None:
            bad_notes[pid] += 1
        else:
            events_by_peer[pid].append(ev)
    for pid, n in sorted(bad_notes.items()):
        warnings.append(f"peer {pid}: {n} counter_reset note(s) could not be parsed and were ignored")
    merge_sources = {int(s): int(t) for s, t in merged}

    results: List[KeyResult] = []
    by_peer: Dict[int, List[Quarantine]] = defaultdict(list)
    for q in todo:
        by_peer[q.peer_id].append(q)
    for pid, qs in sorted(by_peer.items()):
        if pid not in names:
            for q in qs:
                results.append(KeyResult(q, status="skipped", reason="peer no longer exists"))
            continue
        if pid in merge_sources:
            warnings.append(
                f"peer {pid} ({names[pid]}) is the source of a totals merge into peer {merge_sources[pid]}; "
                f"its repair is still written to peer {pid}"
            )
        events = events_by_peer.get(pid, [])
        samples, minutes = load_peer(main, extra, pid, events, max_action_id, warnings, with_holds)
        for q in qs:
            results.append(reconstruct(q, ctx, events, samples, minutes))
    return Plan(context=ctx, names=names, total_keys=len(quarantines), already_repaired=already,
                results=results, warnings=warnings, version=version)


# ---------------------------------------------------------------------------
# reconstruction
# ---------------------------------------------------------------------------

def _one_sided(growth: int, opp_growth: int, opp_dropped: bool) -> bool:
    return growth >= ONE_SIDED_MIN_BYTES and not opp_dropped and opp_growth < ONE_SIDED_RATIO * growth


def _raw_mode(q: Quarantine, res: KeyResult, samples: List[Sample], day_end: datetime,
              open_window: bool) -> Optional[Dict[str, int]]:
    """Exact replay from raw samples, or None when they do not cover the window."""
    if len(samples) < 2:
        return None
    times = [s.ts for s in samples]
    i = bisect.bisect_left(times, q.t_q - timedelta(seconds=SAMPLE_MATCH_SECONDS))
    if i == 0 or i >= len(samples):
        return None
    if abs((samples[i].ts - q.t_q).total_seconds()) > SAMPLE_MATCH_SECONDS:
        return None
    d, o = q.direction, other(q.direction)
    if not samples[i].vals[d] < samples[i - 1].vals[d]:
        return None
    f: Dict[str, int] = defaultdict(int)
    segments: List[Dict[str, Any]] = []
    seg: Optional[Dict[str, Any]] = None
    for j in range(i, len(samples)):
        a, b = samples[j - 1], samples[j]
        if not open_window and b.ts >= day_end:
            break
        mk = minute_key(b.ts)
        if b.vals[d] < a.vals[d]:
            f[mk] += b.vals[d]
            seg = {"start": b.ts, "end": b.ts, "growth": 0, "opp": 0, "opp_dropped": False, "minutes": defaultdict(int)}
            segments.append(seg)
            continue
        delta = b.vals[d] - a.vals[d]
        seg["growth"] += delta
        seg["minutes"][mk] += delta
        seg["end"] = b.ts
        if b.vals[o] < a.vals[o]:
            seg["opp_dropped"] = True
        else:
            seg["opp"] += b.vals[o] - a.vals[o]
    for seg in segments:
        if _one_sided(seg["growth"], seg["opp"], seg["opp_dropped"]):
            res.flagged.append(_flag(q, seg["start"], seg["end"], seg["growth"], seg["opp"]))
            continue
        for mk, v in seg["minutes"].items():
            f[mk] += v
    return f


def _flag(q: Quarantine, start: datetime, end: datetime, growth: int, opp: int) -> Dict[str, Any]:
    return {
        "key": q.tag,
        "segment_start": iso_utc(start),
        "segment_end": iso_utc(end),
        "direction": q.direction,
        "growth_bytes": int(growth),
        "opposite_growth_bytes": int(opp),
    }


def _spread(f: Dict[str, int], amount: int, lo: str, hi: str, minutes: MinuteSeries, opp: str) -> None:
    if amount <= 0:
        return
    weights = minutes.between(opp, lo, hi) if hi >= lo else []
    if not weights:
        weights = [(k, 1) for k in minute_range(lo, hi)]
    for k, v in allocate(amount, weights).items():
        f[k] += v


def _counted_before_drop(n: Event, d: str, day_end_key: str, samples: List[Sample], minutes: MinuteSeries) -> int:
    """D bytes counted from the day end up to (not including) the poll at drop n."""
    n_key = minute_key(n.ts)
    total = minutes.total(d, day_end_key, n_key)
    n_minute = key_to_dt(n_key)
    if samples and samples[0].ts < n_minute:
        part = 0
        for a, b in zip(samples, samples[1:]):
            if b.ts < n_minute:
                continue
            if b.ts >= n.ts - timedelta(seconds=SAMPLE_MATCH_SECONDS):
                break
            part += fresh_delta(a.vals[d], b.vals[d])
        return total + part
    return total + minutes.value(d, n_key)


def _drop_log_mode(q: Quarantine, res: KeyResult, ctx: Context, events: List[Event], samples: List[Sample],
                   minutes: MinuteSeries, day_end: datetime, open_window: bool) -> Optional[Dict[str, int]]:
    d, o = q.direction, other(q.direction)
    ev_d = [e for e in events if e.dropped(d)]
    candidates = [e for e in ev_d if abs((e.ts - q.t_q).total_seconds()) <= DROP_MATCH_SECONDS]
    if not candidates:
        res.status, res.reason = "skipped", "no matching drop event at the trigger time (and no raw samples)"
        return None
    d0 = min(candidates, key=lambda e: abs((e.ts - q.t_q).total_seconds()))
    inday = [d0] + [e for e in ev_d if d0.ts < e.ts < day_end]
    f: Dict[str, int] = defaultdict(int)
    for e in inday:
        f[minute_key(e.ts)] += e.cur[d]

    for e1, e2 in zip(inday, inday[1:]):
        growth = e2.prev[d] - e1.cur[d]
        if growth < 0:
            res.clamps.append(f"{q.tag}: segment {iso_utc(e1.ts)}..{iso_utc(e2.ts)} grew {growth} bytes; clamped to 0")
            continue
        opp_growth = e2.prev[o] - e1.cur[o]
        opp_dropped = opp_growth < 0 or any(e1.ts < e.ts < e2.ts and e.dropped(o) for e in events)
        if _one_sided(growth, opp_growth, opp_dropped):
            res.flagged.append(_flag(q, e1.ts, e2.ts, growth, opp_growth))
            continue
        _spread(f, growth, minute_key(e1.ts), minute_key(e2.ts), minutes, o)

    dk = inday[-1]
    day_end_key = minute_key(day_end)
    last_window_key = minute_key(day_end - timedelta(minutes=1))
    # span_d / span_o: growth of both directions between the same two readings
    # (cur at d_k and the closing reading), used only by the one-sided check.
    span_end_inclusive = True
    if open_window:
        if not samples or samples[-1].ts < dk.ts:
            res.status, res.reason = "skipped", "window still open but no raw sample after the last drop"
            return None
        latest = samples[-1]
        m_value = latest.vals[d]
        end_key = minute_key(latest.ts)
        span_d, span_o = latest.vals[d] - dk.cur[d], latest.vals[o] - dk.cur[o]
        span_end = latest.ts
    else:
        if ctx.covered_from is None or day_end_key < ctx.covered_from:
            res.status = "skipped"
            res.reason = (
                f"no counted minute data after the day end ({day_end_key} UTC; minute data starts "
                f"{ctx.covered_from or 'nowhere'}); pass --extra-minutes with an older database"
            )
            return None
        nxt = next((e for e in ev_d if e.ts >= day_end), None)
        if nxt is not None:
            m_value = nxt.prev[d] - _counted_before_drop(nxt, d, day_end_key, samples, minutes)
            span_d, span_o = nxt.prev[d] - dk.cur[d], nxt.prev[o] - dk.cur[o]
            span_end, span_end_inclusive = nxt.ts, False
            end_key = last_window_key
        elif samples:
            latest = samples[-1]
            m_value = latest.vals[d] - minutes.total(d, day_end_key, next_minute_key(minute_key(latest.ts)))
            span_d, span_o = latest.vals[d] - dk.cur[d], latest.vals[o] - dk.cur[o]
            span_end = latest.ts
            end_key = min(last_window_key, minute_key(latest.ts))
        else:
            res.status, res.reason = "skipped", "no closing data (no later drop and no raw samples)"
            return None
    growth = m_value - dk.cur[d]
    if growth < 0:
        res.clamps.append(f"{q.tag}: final segment from {iso_utc(dk.ts)} grew {growth} bytes; clamped to 0")
        growth = 0
    opp_dropped = span_o < 0 or any(
        dk.ts < e.ts and (e.ts <= span_end if span_end_inclusive else e.ts < span_end) and e.dropped(o)
        for e in events
    )
    if growth >= ONE_SIDED_MIN_BYTES and not opp_dropped and span_o < ONE_SIDED_RATIO * span_d:
        res.flagged.append(_flag(q, dk.ts, span_end, growth, span_o))
    else:
        _spread(f, growth, minute_key(dk.ts), end_key, minutes, o)
    return f


def reconstruct(q: Quarantine, ctx: Context, events: List[Event], samples: List[Sample],
                minutes: MinuteSeries) -> KeyResult:
    res = KeyResult(q)
    clock = ctx.clock
    if clock.day_of(q.t_q) != q.day:
        res.status = "skipped"
        res.reason = f"trigger time {iso_utc(q.t_q)} is not on local day {q.day} in {clock.name}"
        return res
    _, day_end = clock.day_bounds(q.day)
    open_window = day_end > ctx.now
    f = _raw_mode(q, res, samples, day_end, open_window)
    if f is not None:
        res.mode = "raw"
    else:
        res.flagged.clear()
        f = _drop_log_mode(q, res, ctx, events, samples, minutes, day_end, open_window)
        if f is None:
            return res
        res.mode = "drop-log"

    d = q.direction
    tq_key = minute_key(q.t_q)
    hi = None if open_window else minute_key(day_end)
    # Counted D bytes in the window. The trigger minute itself is left out: in the old
    # code it can only hold D bytes of a poll *before* t_q (later polls counted 0).
    counted: Dict[str, int] = {}
    keys = minutes.keys
    for idx in range(bisect.bisect_right(keys, tq_key), len(keys)):
        k = keys[idx]
        if hi is not None and k >= hi:
            break
        v = minutes.value(d, k)
        if v:
            counted[k] = v
    res.fresh_bytes = sum(f.values())
    res.counted_bytes = sum(counted.values())
    hidden = res.fresh_bytes - res.counted_bytes
    if hidden < 0:
        res.clamps.append(
            f"{q.tag}: counted {res.counted_bytes} exceeds the fresh-start total {res.fresh_bytes}; clamped to 0"
        )
        hidden = 0
    res.hidden_bytes = hidden
    eligible = [(k, w) for k, w in sorted(f.items()) if w > 0 and (k == tq_key or counted.get(k, 0) == 0)]
    if not eligible:
        eligible = [(k, w) for k, w in sorted(f.items()) if w > 0]
    res.adds = allocate(hidden, eligible)
    return res


# ---------------------------------------------------------------------------
# increments (shared by the report and the writer)
# ---------------------------------------------------------------------------

@dataclass
class KeyIncrements:
    minute: Dict[str, int]
    daily: Dict[str, int]
    monthly: Dict[str, int]
    minute_skipped_bytes: int
    daily_skipped_bytes: int


def key_increments(res: KeyResult, ctx: Context) -> KeyIncrements:
    minute_cutoff = minute_key(ctx.now - timedelta(days=ctx.minute_retention_days))
    daily_cutoff = (
        ctx.clock.day_of(ctx.now - timedelta(days=ctx.daily_retention_days)) if ctx.daily_retention_days > 0 else None
    )
    minute: Dict[str, int] = defaultdict(int)
    daily: Dict[str, int] = defaultdict(int)
    monthly: Dict[str, int] = defaultdict(int)
    m_skip = d_skip = 0
    for mk, b in sorted(res.adds.items()):
        if b <= 0:
            continue
        if mk > minute_cutoff:
            minute[mk] += b
        else:
            m_skip += b
        day = ctx.clock.day_of(key_to_dt(mk))
        if daily_cutoff is not None and day < daily_cutoff:
            d_skip += b
        else:
            daily[day] += b
        monthly[mk[:7]] += b
    return KeyIncrements(dict(minute), dict(daily), dict(monthly), m_skip, d_skip)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def summarize(plan: Plan) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    cells: Dict[Tuple[int, str], Dict[str, Any]] = {}
    totals = {"rx": 0, "tx": 0}
    for r in plan.results:
        if r.status != "ok" or r.hidden_bytes <= 0:
            continue
        label = persian_month_label(r.q.day)
        cell = cells.setdefault((r.q.peer_id, label), {
            "peer_id": r.q.peer_id, "peer_name": plan.names.get(r.q.peer_id, f"peer {r.q.peer_id}"),
            "persian_month": label, "rx": 0, "tx": 0, "keys": 0,
        })
        cell[r.q.direction] += r.hidden_bytes
        cell["keys"] += 1
        totals[r.q.direction] += r.hidden_bytes
    rows = sorted(cells.values(), key=lambda c: (c["peer_name"].lower(), c["peer_id"], c["persian_month"]))
    return rows, totals


def print_report(plan: Plan, out, verbose: bool = False) -> None:
    ctx = plan.context
    ok = [r for r in plan.results if r.status == "ok"]
    skipped = [r for r in plan.results if r.status == "skipped"]
    raw = sum(1 for r in ok if r.mode == "raw")
    clamps = [c for r in ok for c in r.clamps]
    flagged = [f for r in ok for f in r.flagged]
    m_skip = sum(key_increments(r, ctx).minute_skipped_bytes for r in ok)
    d_skip = sum(key_increments(r, ctx).daily_skipped_bytes for r in ok)
    w = out.write
    w(f"now {iso_utc(ctx.now)}  timezone {ctx.clock.describe()}\n")
    w(f"minute data usable from {ctx.covered_from or '(none)'} UTC; minute retention {ctx.minute_retention_days} days "
      "(older minutes go to daily/monthly only)\n")
    w(f"quarantine keys: {plan.total_keys} in scope, {len(plan.already_repaired)} already repaired, "
      f"{len(ok)} to repair ({raw} from raw samples, {len(ok) - raw} from the drop log), {len(skipped)} skipped\n")
    w(f"clamped negative values: {len(clamps)}   one-sided segments held back: {len(flagged)}\n")
    if m_skip:
        w(f"bytes in minutes older than retention (daily/monthly only, no minute rows): {gib(m_skip):.2f} GiB\n")
    if d_skip:
        w(f"bytes on days older than daily retention (not added to usage_daily): {gib(d_skip):.2f} GiB\n")

    rows, totals = summarize(plan)
    months = sorted({r["persian_month"] for r in rows})
    if rows:
        name_w = max(12, min(28, max(len(r["peer_name"]) for r in rows)))
        col_w = max(12, max(len(m) for m in months) + 1)
        w("\nGiB to add (download + upload) per peer and Persian month:\n")
        w("peer".ljust(name_w) + "".join(m.rjust(col_w) for m in months) + "total".rjust(10) + "\n")
        per_peer: Dict[Tuple[int, str], Dict[str, int]] = defaultdict(dict)
        for r in rows:
            per_peer[(r["peer_id"], r["peer_name"])][r["persian_month"]] = r["rx"] + r["tx"]
        month_tot: Dict[str, int] = defaultdict(int)
        for (pid, name), vals in sorted(per_peer.items(), key=lambda kv: (kv[0][1].lower(), kv[0][0])):
            line = name[:name_w].ljust(name_w)
            for m in months:
                v = vals.get(m, 0)
                month_tot[m] += v
                line += (f"{gib(v):.2f}" if v else "-").rjust(col_w)
            line += f"{gib(sum(vals.values())):.2f}".rjust(10)
            w(line + "\n")
        w("TOTAL".ljust(name_w) + "".join(f"{gib(month_tot[m]):.2f}".rjust(col_w) for m in months)
          + f"{gib(sum(month_tot.values())):.2f}".rjust(10) + "\n")
    w(f"\ndownload (tx) {gib(totals['tx']):.2f} GiB   upload (rx) {gib(totals['rx']):.2f} GiB   "
      f"total {gib(totals['tx'] + totals['rx']):.2f} GiB\n")

    for msg in plan.warnings:
        w(f"WARNING: {msg}\n")
    if skipped:
        w("\nskipped keys (nothing written for them):\n")
        for r in skipped:
            w(f"  {r.q.tag} ({plan.names.get(r.q.peer_id, '?')}): {r.reason}\n")
    if clamps:
        w("\nclamped to 0:\n")
        for c in clamps:
            w(f"  {c}\n")
    if flagged:
        w("\none-sided segments NOT added (manual review):\n")
        for fl in flagged:
            w(f"  {fl['key']} {fl['segment_start']}..{fl['segment_end']}: {fl['direction']} grew "
              f"{gib(fl['growth_bytes']):.2f} GiB, opposite grew {fl['opposite_growth_bytes']} bytes\n")
    if verbose and ok:
        w("\nper key:\n")
        for r in ok:
            w(f"  {r.q.tag} {plan.names.get(r.q.peer_id, '?')} t_q={iso_utc(r.q.t_q)} mode={r.mode} "
              f"fresh={r.fresh_bytes} counted={r.counted_bytes} add={r.hidden_bytes} ({gib(r.hidden_bytes):.3f} GiB)\n")


def write_csv(plan: Plan, path: str) -> None:
    rows, _ = summarize(plan)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.writer(fh)
        wr.writerow(["peer_id", "peer_name", "persian_month", "keys", "upload_rx_bytes", "download_tx_bytes",
                     "upload_rx_gib", "download_tx_gib", "total_gib"])
        for r in rows:
            wr.writerow([r["peer_id"], r["peer_name"], r["persian_month"], r["keys"], r["rx"], r["tx"],
                         f"{gib(r['rx']):.3f}", f"{gib(r['tx']):.3f}", f"{gib(r['rx'] + r['tx']):.3f}"])


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    path = directory / f"{stem}{suffix}"
    n = 2
    while path.exists():
        path = directory / f"{stem}-{n}{suffix}"
        n += 1
    return path


def make_backup(db_path: str, label: str) -> str:
    directory = Path(db_path).resolve().parent / "backups"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    target = _unique_path(directory, f"wgmik-{label}-{stamp}", ".db")
    src = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000)
    dst = sqlite3.connect(str(target))
    try:
        src.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        src.backup(dst)
        check = dst.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise RepairError(f"backup quick_check failed: {check}")
    except Exception as exc:
        dst.close()
        src.close()
        raise RepairError(f"Backup failed, nothing written: {exc}") from exc
    dst.close()
    src.close()
    return str(target)


def _find_minute_row(conn: sqlite3.Connection, peer_id: int, key: str, exact: Optional[str] = None):
    if exact is not None:
        row = conn.execute(
            "SELECT rowid, minute_ts, rx, tx FROM usage_minute WHERE peer_id=? AND minute_ts=?", (peer_id, exact)
        ).fetchone()
        if row is not None:
            return row
    nxt = next_minute_key(key)
    return conn.execute(
        "SELECT rowid, minute_ts, rx, tx FROM usage_minute WHERE peer_id=? AND "
        "((minute_ts >= ? AND minute_ts < ?) OR (minute_ts >= ? AND minute_ts < ?)) ORDER BY rowid LIMIT 1",
        (peer_id, key, nxt, key.replace(" ", "T"), nxt.replace(" ", "T")),
    ).fetchone()


ROLLUP_COLUMNS = {"usage_daily": "day", "usage_monthly": "month_key"}


def _add_row(conn: sqlite3.Connection, table: str, peer_id: int, key: str, rx: int, tx: int) -> Dict[str, Any]:
    if table == "usage_minute":
        row = _find_minute_row(conn, peer_id, key)
        col = "minute_ts"
    else:
        col = ROLLUP_COLUMNS[table]
        row = conn.execute(f"SELECT rowid, {col}, rx, tx FROM {table} WHERE peer_id=? AND {col}=?",
                           (peer_id, key)).fetchone()
    if row is None:
        stored = key + ":00.000000" if table == "usage_minute" else key
        conn.execute(f"INSERT INTO {table} (peer_id, {col}, rx, tx) VALUES (?, ?, ?, ?)", (peer_id, stored, rx, tx))
        inserted = True
    else:
        stored = row[1]
        conn.execute(f"UPDATE {table} SET rx = rx + ?, tx = tx + ? WHERE rowid=?", (rx, tx, row[0]))
        inserted = False
    return {"table": table, "peer_id": peer_id, col: stored, "rx_add": rx, "tx_add": tx, "inserted": inserted}


def _days_phrase(days: List[str]) -> str:
    days = sorted(set(days))
    if len(days) == 1:
        return f"on 1 day ({days[0]})"
    return f"on {len(days)} days ({days[0]} to {days[-1]})"


def _maintenance_refusal(conn: sqlite3.Connection) -> Optional[str]:
    """Usage maintenance rebuilds the last day of usage_minute from raw samples but leaves
    usage_daily/usage_monthly alone. A run after the fix went live fills hidden minutes by
    itself, so 'hidden = fresh - counted minutes' would come out short for the daily and
    monthly totals of those days."""
    since_raw = _setting(conn, VERSION_SINCE_KEY)
    status_raw = _setting(conn, MAINTENANCE_STATUS_KEY)
    if not since_raw or not status_raw:
        return None
    try:
        started_raw = (json.loads(status_raw) or {}).get("started_at")
    except ValueError:
        return None
    if not started_raw:
        return None
    since, started = parse_ts(since_raw), parse_ts(started_raw)
    if started <= since:
        return None
    return (
        f"Refusing to apply: usage maintenance started at {iso_utc(started)}, after the fixed counting code "
        f"went live at {iso_utc(since)}. Maintenance rebuilt the last day of minute rollups without the daily "
        "and monthly totals, so this repair would under-fill those days. Run the repair before the next "
        "maintenance, or pass --allow-after-maintenance to accept that shortfall."
    )


def apply_plan(db_path: str, plan: Plan, allow_old_code: bool, ledger_path: Optional[str], out,
               allow_after_maintenance: bool = False) -> int:
    ctx = plan.context
    todo = [r for r in plan.results if r.status == "ok"]
    if not todo:
        out.write("\nNothing to repair: every quarantine key in scope is already repaired or skipped. Nothing written.\n")
        return 0
    if not allow_old_code and plan.version != REQUIRED_VERSION:
        raise RepairError(_version_refusal(plan.version))
    if not allow_after_maintenance:
        pre = connect_readonly(db_path)
        try:
            refusal = _maintenance_refusal(pre)
        finally:
            pre.close()
        if refusal:
            raise RepairError(refusal)

    backup = make_backup(db_path, "pre-quarantine-repair")
    out.write(f"\nbackup: {backup}\n")
    run_id = uuid.uuid4().hex[:12]
    applied_at = iso_utc(ctx.now)
    backups_dir = Path(db_path).resolve().parent / "backups"
    ledger_file = Path(ledger_path) if ledger_path else _unique_path(
        backups_dir, f"quarantine-repair-ledger-{utc_now().strftime('%Y%m%d-%H%M%S')}-{run_id}", ".json")

    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    ledger_written = False
    try:
        conn.execute("BEGIN IMMEDIATE")
        version = _setting(conn, VERSION_KEY)
        if not allow_old_code and version != REQUIRED_VERSION:
            raise RepairError(_version_refusal(version))
        if not allow_after_maintenance:
            refusal = _maintenance_refusal(conn)
            if refusal:
                raise RepairError(refusal)
        clash =[r.q.tag for r in todo if _setting(conn, REPAIRED_PREFIX + r.q.tag) is not None]
        if clash:
            raise RepairError(f"{len(clash)} key(s) were repaired by another run meanwhile ({clash[:3]}...); rerun")
        increments: List[Dict[str, Any]] = []
        keys_out: Dict[str, Any] = {}
        per_peer: Dict[int, Dict[str, Any]] = defaultdict(lambda: {"rx": 0, "tx": 0, "days": []})
        for r in todo:
            inc = key_increments(r, ctx)
            d = r.q.direction
            for table, values in (("usage_minute", inc.minute), ("usage_daily", inc.daily),
                                  ("usage_monthly", inc.monthly)):
                for k, b in sorted(values.items()):
                    entry = _add_row(conn, table, r.q.peer_id, k, b if d == "rx" else 0, b if d == "tx" else 0)
                    entry["key"] = r.q.tag
                    increments.append(entry)
            keys_out[r.q.tag] = {
                "peer_id": r.q.peer_id, "direction": d, "day": r.q.day, "t_q": iso_utc(r.q.t_q), "mode": r.mode,
                "fresh_start_bytes": r.fresh_bytes, "counted_bytes": r.counted_bytes, "bytes": r.hidden_bytes,
                "minute_bytes_outside_retention": inc.minute_skipped_bytes,
                "daily_bytes_outside_retention": inc.daily_skipped_bytes,
                "clamped": r.clamps, "flagged_segments": r.flagged,
            }
            conn.execute(
                "INSERT INTO settings_kv (key, value) VALUES (?, ?)",
                (REPAIRED_PREFIX + r.q.tag, json.dumps({
                    "bytes": r.hidden_bytes, "applied_at": applied_at, "run_id": run_id, "mode": r.mode,
                    "flagged_segments": len(r.flagged), "ledger": str(ledger_file),
                })),
            )
            if r.hidden_bytes > 0:
                per_peer[r.q.peer_id][d] += r.hidden_bytes
                per_peer[r.q.peer_id]["days"].append(r.q.day)
        actions = []
        for pid, info in sorted(per_peer.items()):
            note = (f"Usage repair: added back {gib(info['tx']):.2f} GB download and {gib(info['rx']):.2f} GB "
                    f"upload hidden by the old counter quarantine {_days_phrase(info['days'])}.")
            conn.execute("INSERT INTO actions (peer_id, ts, action, note) VALUES (?, ?, ?, ?)",
                         (pid, sqlite_ts(ctx.now), ACTION_APPLY, note))
            actions.append({"peer_id": pid, "note": note})
        ledger = {
            "tool": "repair_quarantine_usage", "ledger_version": 1, "run_id": run_id,
            "db": str(Path(db_path).resolve()), "applied_at": applied_at, "backup": backup,
            "totals": {"rx": sum(i["rx"] for i in per_peer.values()), "tx": sum(i["tx"] for i in per_peer.values())},
            "keys": keys_out, "actions": actions, "increments": increments,
        }
        ledger_file.parent.mkdir(parents=True, exist_ok=True)
        with open(ledger_file, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=1)
            fh.flush()
            os.fsync(fh.fileno())
        ledger_written = True
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        if ledger_written:
            os.replace(ledger_file, str(ledger_file) + ".rolled-back")
        raise
    finally:
        conn.close()
    out.write(f"ledger: {ledger_file}\n")
    out.write(f"APPLIED run {run_id}: {len(todo)} key(s), {len(increments)} row increment(s), "
              f"{len(actions)} peer action(s).\n")
    return 0


def _version_refusal(version: Optional[str]) -> str:
    return (
        f"Refusing to apply: settings_kv {VERSION_KEY} is {version!r}, expected {REQUIRED_VERSION!r}. "
        "Deploy and start the fixed counting code first; while the old code runs, the quarantine keeps hiding "
        "usage and the repair would be incomplete."
    )


def undo_ledger(db_path: str, ledger_path: str, now: datetime, out) -> int:
    try:
        with open(ledger_path, encoding="utf-8") as fh:
            ledger = json.load(fh)
    except (OSError, ValueError) as exc:
        raise RepairError(f"Cannot read ledger {ledger_path}: {exc}") from exc
    run_id = ledger.get("run_id") if isinstance(ledger, dict) else None
    if not run_id or "increments" not in ledger or "keys" not in ledger:
        raise RepairError(f"{ledger_path} is not a repair ledger")
    if ledger.get("db") and os.path.realpath(ledger["db"]) != os.path.realpath(db_path):
        out.write(f"WARNING: ledger was written for {ledger['db']}, undoing on {db_path}\n")

    probe = connect_readonly(db_path)
    try:
        status = {tag: _setting(probe, REPAIRED_PREFIX + tag) for tag in ledger["keys"]}
    finally:
        probe.close()
    _check_undo_keys(status, run_id)

    backup = make_backup(db_path, "pre-quarantine-repair-undo")
    out.write(f"backup: {backup}\n")
    conn = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    missing: List[str] = []
    clamped: List[str] = []
    deleted = updated = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        _check_undo_keys({tag: _setting(conn, REPAIRED_PREFIX + tag) for tag in ledger["keys"]}, run_id)
        grouped: Dict[Tuple[str, int, str], Dict[str, Any]] = {}
        for inc in ledger["increments"]:
            table = inc["table"]
            col = "minute_ts" if table == "usage_minute" else ROLLUP_COLUMNS[table]
            g = grouped.setdefault((table, int(inc["peer_id"]), inc[col]), {"rx": 0, "tx": 0, "inserted": False})
            g["rx"] += int(inc["rx_add"])
            g["tx"] += int(inc["tx_add"])
            g["inserted"] = g["inserted"] or bool(inc.get("inserted"))
        for (table, pid, stored), g in sorted(grouped.items()):
            if table == "usage_minute":
                row = _find_minute_row(conn, pid, normalize_minute_key(stored) or stored, exact=stored)
            else:
                col = ROLLUP_COLUMNS[table]
                row = conn.execute(f"SELECT rowid, {col}, rx, tx FROM {table} WHERE peer_id=? AND {col}=?",
                                   (pid, stored)).fetchone()
            if row is None:
                missing.append(f"{table} peer {pid} {stored}")
                continue
            new_rx, new_tx = int(row[2] or 0) - g["rx"], int(row[3] or 0) - g["tx"]
            if new_rx < 0 or new_tx < 0:
                clamped.append(f"{table} peer {pid} {stored}: rx {new_rx}, tx {new_tx} clamped to 0")
                new_rx, new_tx = max(0, new_rx), max(0, new_tx)
            if g["inserted"] and new_rx == 0 and new_tx == 0:
                conn.execute(f"DELETE FROM {table} WHERE rowid=?", (row[0],))
                deleted += 1
            else:
                conn.execute(f"UPDATE {table} SET rx=?, tx=? WHERE rowid=?", (new_rx, new_tx, row[0]))
                updated += 1
        for tag in ledger["keys"]:
            conn.execute("DELETE FROM settings_kv WHERE key=?", (REPAIRED_PREFIX + tag,))
        per_peer: Dict[int, Dict[str, Any]] = defaultdict(lambda: {"rx": 0, "tx": 0, "days": []})
        for tag, info in ledger["keys"].items():
            if int(info.get("bytes") or 0) > 0:
                p = per_peer[int(info["peer_id"])]
                p[info["direction"]] += int(info["bytes"])
                p["days"].append(info["day"])
        for pid, info in sorted(per_peer.items()):
            note = (f"Usage repair undone: removed {gib(info['tx']):.2f} GB download and {gib(info['rx']):.2f} GB "
                    f"upload that repair run {run_id} had added back {_days_phrase(info['days'])}.")
            conn.execute("INSERT INTO actions (peer_id, ts, action, note) VALUES (?, ?, ?, ?)",
                         (pid, sqlite_ts(now), ACTION_UNDO, note))
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()
    out.write(f"UNDONE run {run_id}: {updated} row(s) reduced, {deleted} row(s) the repair had created removed, "
              f"{len(ledger['keys'])} repaired marker(s) removed.\n")
    if missing:
        out.write(f"{len(missing)} row(s) no longer exist and were skipped (e.g. pruned minutes):\n")
        for m in missing[:50]:
            out.write(f"  {m}\n")
    if clamped:
        out.write(f"{len(clamped)} row(s) would have gone below 0 and were clamped:\n")
        for c in clamped:
            out.write(f"  {c}\n")
    return 0


def _check_undo_keys(status: Dict[str, Optional[str]], run_id: str) -> None:
    bad = []
    for tag, value in status.items():
        try:
            ok = value is not None and json.loads(value).get("run_id") == run_id
        except (ValueError, AttributeError):
            ok = False
        if not ok:
            bad.append(tag)
    if bad:
        raise RepairError(
            f"Refusing to undo: {len(bad)} of {len(status)} key(s) are not marked as repaired by run {run_id} "
            f"(already undone, or never committed?), e.g. {bad[:3]}. Nothing written."
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m backend.tools.repair_quarantine_usage",
        description="Add back usage hidden by the old near-32-bit counter quarantine. Dry run unless --apply.",
    )
    p.add_argument("--db", required=True, help="main SQLite database (e.g. /data/wgmik.db)")
    p.add_argument("--extra-minutes", help="older read-only database whose usage_minute rows extend coverage")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="write the repair (backup + one transaction + ledger)")
    mode.add_argument("--undo", metavar="LEDGER", help="subtract exactly the increments of a ledger")
    p.add_argument("--report", metavar="CSV", help="also write the peer x Persian month table as CSV")
    p.add_argument("--ledger", metavar="PATH", help="ledger path for --apply (default: <db dir>/backups/...)")
    p.add_argument("--peer", type=int, action="append", help="limit to this peer id (repeatable)")
    p.add_argument("--timezone", help="override settings_kv timezone (default: settings_kv, else UTC)")
    p.add_argument("--now", help="pretend the current UTC time is this ISO timestamp (tests)")
    p.add_argument("--verbose", action="store_true", help="print one line per quarantine key")
    p.add_argument("--allow-old-code", action="store_true", help=argparse.SUPPRESS)  # tests only
    p.add_argument("--allow-after-maintenance", action="store_true",
                   help="apply even though usage maintenance ran after the fixed code went live")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None, out=None) -> int:
    out = out or sys.stdout
    args = parse_args(argv)
    now = parse_ts(args.now) if args.now else utc_now()
    try:
        if args.undo:
            out.write(f"UNDO using ledger {args.undo} on {args.db}\n")
            return undo_ledger(args.db, args.undo, now, out)
        main_conn = connect_readonly(args.db)
        extra_conn = connect_readonly(args.extra_minutes) if args.extra_minutes else None
        try:
            plan = build_plan(main_conn, extra_conn, now, set(args.peer) if args.peer else None, args.timezone)
        finally:
            main_conn.close()
            if extra_conn is not None:
                extra_conn.close()
        header = "APPLY" if args.apply else "DRY RUN (nothing is written; add --apply to write)"
        out.write(f"Quarantine usage repair - {header}\ndb {args.db}"
                  + (f"   extra minutes {args.extra_minutes}" if args.extra_minutes else "") + "\n")
        print_report(plan, out, verbose=args.verbose)
        if args.report:
            write_csv(plan, args.report)
            out.write(f"\nreport: {args.report}\n")
        if args.apply:
            return apply_plan(args.db, plan, args.allow_old_code, args.ledger, out,
                              allow_after_maintenance=args.allow_after_maintenance)
        if plan.version != REQUIRED_VERSION:
            out.write(f"\nnote: {VERSION_KEY} is {plan.version!r}; --apply will refuse until the fixed code runs.\n")
        return 0
    except RepairError as exc:
        out.write(f"\n{exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
