from __future__ import annotations

import argparse
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .db import sqlite_database_path


DEFAULT_SOURCE_ROUTER_NAME = "Arvan"
DEFAULT_TARGET_ROUTER_NAME = "Asia"
DEFAULT_MANUAL_PAIRS: tuple[tuple[int, int, str], ...] = (
    (88, 43, "manual_address"),
    (30, 98, "manual_canonical"),
    (93, 98, "manual_canonical"),
)
DEFAULT_IGNORED_SOURCES: frozenset[int] = frozenset({47})
MERGE_MODE_TOTALS_ONLY = "totals_only"


LEDGER_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS peer_totals_merge (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    source_peer_id INTEGER NOT NULL UNIQUE,
    target_peer_id INTEGER NOT NULL,
    source_router_id INTEGER NOT NULL,
    target_router_id INTEGER NOT NULL,
    merge_mode VARCHAR(32) NOT NULL DEFAULT 'totals_only',
    match_type VARCHAR(64) NOT NULL DEFAULT '',
    usage_minute_rows INTEGER NOT NULL DEFAULT 0,
    usage_daily_rows INTEGER NOT NULL DEFAULT 0,
    usage_monthly_rows INTEGER NOT NULL DEFAULT 0,
    applied_at DATETIME NOT NULL,
    FOREIGN KEY(source_peer_id) REFERENCES peers (id) ON DELETE CASCADE,
    FOREIGN KEY(target_peer_id) REFERENCES peers (id) ON DELETE CASCADE,
    FOREIGN KEY(source_router_id) REFERENCES routers (id) ON DELETE CASCADE,
    FOREIGN KEY(target_router_id) REFERENCES routers (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_peer_totals_merge_target_peer_id
ON peer_totals_merge (target_peer_id);
"""


class PeerTotalsMergeError(RuntimeError):
    """Base error for peer totals merge operations."""


class PeerTotalsMergePreflightError(PeerTotalsMergeError):
    """Raised when the merge cannot safely proceed."""


@dataclass(frozen=True)
class PeerRow:
    id: int
    router_id: int
    router_name: str
    interface: str
    name: str
    public_key: str
    allowed_address: str
    selected: bool
    disabled: bool


@dataclass(frozen=True)
class MergePair:
    source_peer: PeerRow
    target_peer: PeerRow
    match_type: str
    usage_minute_rows: int
    usage_daily_rows: int
    usage_monthly_rows: int
    overlap_minute_rows: int
    overlap_daily_rows: int
    overlap_monthly_rows: int


@dataclass(frozen=True)
class MergePlan:
    db_path: str
    source_router_id: int
    source_router_name: str
    target_router_id: int
    target_router_name: str
    pairs: tuple[MergePair, ...]
    ignored_sources: tuple[int, ...]
    already_merged_sources: tuple[int, ...]

    @property
    def total_usage_minute_rows(self) -> int:
        return sum(pair.usage_minute_rows for pair in self.pairs)

    @property
    def total_usage_daily_rows(self) -> int:
        return sum(pair.usage_daily_rows for pair in self.pairs)

    @property
    def total_usage_monthly_rows(self) -> int:
        return sum(pair.usage_monthly_rows for pair in self.pairs)

    @property
    def distinct_source_count(self) -> int:
        return len(self.pairs)

    @property
    def distinct_target_count(self) -> int:
        return len({pair.target_peer.id for pair in self.pairs})


@dataclass(frozen=True)
class ApplyPairResult:
    source_peer_id: int
    target_peer_id: int
    match_type: str
    usage_minute_rows: int
    usage_daily_rows: int
    usage_monthly_rows: int


@dataclass(frozen=True)
class ApplyResult:
    db_path: str
    backup_path: str | None
    no_op: bool
    skipped_sources: tuple[int, ...]
    pairs: tuple[ApplyPairResult, ...]

    @property
    def total_usage_minute_rows(self) -> int:
        return sum(pair.usage_minute_rows for pair in self.pairs)

    @property
    def total_usage_daily_rows(self) -> int:
        return sum(pair.usage_daily_rows for pair in self.pairs)

    @property
    def total_usage_monthly_rows(self) -> int:
        return sum(pair.usage_monthly_rows for pair in self.pairs)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _resolve_db_path(db_path: str | None) -> str:
    if db_path:
        return os.path.abspath(db_path)
    resolved = sqlite_database_path()
    if resolved and os.path.exists(resolved):
        return resolved
    fallback = Path(__file__).resolve().parents[1] / "data" / "wgmik.db"
    if fallback.exists():
        return str(fallback.resolve())
    if not resolved:
        raise PeerTotalsMergePreflightError("peer totals merge requires a file-based SQLite database")
    return resolved


def _connect(path: str, *, read_only: bool) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{Path(path).resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    else:
        conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _parse_pair(raw: str) -> tuple[int, int]:
    left, sep, right = raw.partition(":")
    if not sep:
        raise argparse.ArgumentTypeError(f"invalid pair '{raw}' (expected source:target)")
    try:
        source_id = int(left)
        target_id = int(right)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid pair '{raw}' (expected integer ids)") from exc
    if source_id <= 0 or target_id <= 0:
        raise argparse.ArgumentTypeError(f"invalid pair '{raw}' (ids must be positive)")
    return source_id, target_id


def _parse_peer_id(raw: str) -> int:
    try:
        peer_id = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid peer id '{raw}'") from exc
    if peer_id <= 0:
        raise argparse.ArgumentTypeError(f"invalid peer id '{raw}'")
    return peer_id


def _router_row(conn: sqlite3.Connection, router_name: str) -> sqlite3.Row:
    rows = conn.execute(
        "SELECT id, name FROM routers WHERE lower(name) = lower(?) ORDER BY id",
        (router_name,),
    ).fetchall()
    if not rows:
        raise PeerTotalsMergePreflightError(f"router '{router_name}' not found")
    if len(rows) > 1:
        raise PeerTotalsMergePreflightError(f"router name '{router_name}' is not unique")
    return rows[0]


def _peer_row(conn: sqlite3.Connection, peer_id: int) -> PeerRow:
    row = conn.execute(
        """
        SELECT
            p.id,
            p.router_id,
            r.name AS router_name,
            p.interface,
            p.name,
            p.public_key,
            p.allowed_address,
            p.selected,
            p.disabled
        FROM peers p
        JOIN routers r ON r.id = p.router_id
        WHERE p.id = ?
        """,
        (peer_id,),
    ).fetchone()
    if row is None:
        raise PeerTotalsMergePreflightError(f"peer {peer_id} not found")
    return PeerRow(
        id=int(row["id"]),
        router_id=int(row["router_id"]),
        router_name=str(row["router_name"]),
        interface=str(row["interface"] or ""),
        name=str(row["name"] or ""),
        public_key=str(row["public_key"] or ""),
        allowed_address=str(row["allowed_address"] or ""),
        selected=bool(row["selected"]),
        disabled=bool(row["disabled"]),
    )


def _count_rows(conn: sqlite3.Connection, table: str, source_peer_id: int) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table} WHERE peer_id = ?", (source_peer_id,)).fetchone()
    return int(row["count"] or 0)


def _count_overlaps(conn: sqlite3.Connection, table: str, key_col: str, source_peer_id: int, target_peer_id: int) -> int:
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS count
        FROM {table} s
        JOIN {table} t
          ON t.peer_id = ?
         AND t.{key_col} = s.{key_col}
        WHERE s.peer_id = ?
        """,
        (target_peer_id, source_peer_id),
    ).fetchone()
    return int(row["count"] or 0)


def _manual_pairs_with_overrides(
    explicit_pairs: Sequence[tuple[int, int]],
) -> list[tuple[int, int, str]]:
    manual = {source_id: (target_id, match_type) for source_id, target_id, match_type in DEFAULT_MANUAL_PAIRS}
    for source_id, target_id in explicit_pairs:
        manual[source_id] = (target_id, "manual_override")
    return [(source_id, target_id, match_type) for source_id, (target_id, match_type) in manual.items()]


def _ledger_sources(conn: sqlite3.Connection, source_peer_ids: Iterable[int]) -> list[int]:
    ids = sorted({int(source_id) for source_id in source_peer_ids})
    if not ids:
        return []
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'peer_totals_merge'"
    ).fetchone()
    if table is None:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT source_peer_id FROM peer_totals_merge WHERE source_peer_id IN ({placeholders}) ORDER BY source_peer_id",
        ids,
    ).fetchall()
    return [int(row["source_peer_id"]) for row in rows]


def _build_plan_from_conn(
    conn: sqlite3.Connection,
    *,
    source_router_name: str,
    target_router_name: str,
    explicit_pairs: Sequence[tuple[int, int]],
    explicit_ignores: Sequence[int],
) -> MergePlan:
    source_router = _router_row(conn, source_router_name)
    target_router = _router_row(conn, target_router_name)
    source_router_id = int(source_router["id"])
    target_router_id = int(target_router["id"])

    pair_rows = conn.execute(
        """
        SELECT
            src.id AS source_peer_id,
            dst.id AS target_peer_id
        FROM peers src
        JOIN peers dst
          ON dst.router_id = ?
         AND dst.interface = src.interface
         AND dst.public_key = src.public_key
        WHERE src.router_id = ?
        ORDER BY src.id
        """,
        (target_router_id, source_router_id),
    ).fetchall()
    pair_map: dict[int, tuple[int, str]] = {
        int(row["source_peer_id"]): (int(row["target_peer_id"]), "exact_public_key")
        for row in pair_rows
    }

    ignored_sources = set(DEFAULT_IGNORED_SOURCES)
    for ignored_id in ignored_sources:
        pair_map.pop(ignored_id, None)
    ignored_sources.difference_update(source_id for source_id, _target_id in explicit_pairs)

    for source_peer_id, target_peer_id, match_type in _manual_pairs_with_overrides(explicit_pairs):
        pair_map[source_peer_id] = (target_peer_id, match_type)

    for ignored_id in explicit_ignores:
        ignored_sources.add(int(ignored_id))
        pair_map.pop(int(ignored_id), None)

    pairs: list[MergePair] = []
    for source_peer_id in sorted(pair_map):
        target_peer_id, match_type = pair_map[source_peer_id]
        if source_peer_id == target_peer_id:
            raise PeerTotalsMergePreflightError(f"peer {source_peer_id} cannot merge into itself")
        source_peer = _peer_row(conn, source_peer_id)
        target_peer = _peer_row(conn, target_peer_id)
        if source_peer.router_id == target_peer.router_id and source_peer.id == target_peer.id:
            raise PeerTotalsMergePreflightError(f"peer {source_peer_id} cannot merge into itself")
        pairs.append(
            MergePair(
                source_peer=source_peer,
                target_peer=target_peer,
                match_type=match_type,
                usage_minute_rows=_count_rows(conn, "usage_minute", source_peer_id),
                usage_daily_rows=_count_rows(conn, "usage_daily", source_peer_id),
                usage_monthly_rows=_count_rows(conn, "usage_monthly", source_peer_id),
                overlap_minute_rows=_count_overlaps(conn, "usage_minute", "minute_ts", source_peer_id, target_peer_id),
                overlap_daily_rows=_count_overlaps(conn, "usage_daily", "day", source_peer_id, target_peer_id),
                overlap_monthly_rows=_count_overlaps(conn, "usage_monthly", "month_key", source_peer_id, target_peer_id),
            )
        )

    already_merged = _ledger_sources(conn, [pair.source_peer.id for pair in pairs])
    return MergePlan(
        db_path="",
        source_router_id=source_router_id,
        source_router_name=str(source_router["name"]),
        target_router_id=target_router_id,
        target_router_name=str(target_router["name"]),
        pairs=tuple(pairs),
        ignored_sources=tuple(sorted(ignored_sources)),
        already_merged_sources=tuple(already_merged),
    )


def build_merge_plan(
    db_path: str | None = None,
    *,
    source_router_name: str = DEFAULT_SOURCE_ROUTER_NAME,
    target_router_name: str = DEFAULT_TARGET_ROUTER_NAME,
    explicit_pairs: Sequence[tuple[int, int]] = (),
    explicit_ignores: Sequence[int] = (),
) -> MergePlan:
    resolved_db_path = _resolve_db_path(db_path)
    if not os.path.exists(resolved_db_path):
        raise PeerTotalsMergePreflightError(f"database not found: {resolved_db_path}")
    with _connect(resolved_db_path, read_only=True) as conn:
        plan = _build_plan_from_conn(
            conn,
            source_router_name=source_router_name,
            target_router_name=target_router_name,
            explicit_pairs=explicit_pairs,
            explicit_ignores=explicit_ignores,
        )
    return MergePlan(
        db_path=resolved_db_path,
        source_router_id=plan.source_router_id,
        source_router_name=plan.source_router_name,
        target_router_id=plan.target_router_id,
        target_router_name=plan.target_router_name,
        pairs=plan.pairs,
        ignored_sources=plan.ignored_sources,
        already_merged_sources=plan.already_merged_sources,
    )


def _quick_check(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA quick_check").fetchone()
    return str(row[0] if row else "unknown")


def _ensure_ledger_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(LEDGER_TABLE_SQL)


def _default_backup_path(db_path: str) -> str:
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = _utc_now().strftime("%Y%m%d-%H%M%S")
    return os.path.join(backup_dir, f"peer-totals-merge-{stamp}.db")


def _backup_database(source_conn: sqlite3.Connection, backup_path: str) -> None:
    os.makedirs(os.path.dirname(backup_path), exist_ok=True)
    if os.path.exists(backup_path):
        raise PeerTotalsMergePreflightError(f"backup path already exists: {backup_path}")
    target_conn = sqlite3.connect(backup_path, timeout=30)
    try:
        source_conn.backup(target_conn)
    finally:
        target_conn.close()


def _copy_usage_rows(
    conn: sqlite3.Connection,
    *,
    table: str,
    key_col: str,
    source_peer_id: int,
    target_peer_id: int,
) -> None:
    conn.execute(
        f"""
        INSERT INTO {table} (peer_id, {key_col}, rx, tx)
        SELECT ?, {key_col}, rx, tx
        FROM {table}
        WHERE peer_id = ?
        ON CONFLICT(peer_id, {key_col}) DO UPDATE SET
            rx = rx + excluded.rx,
            tx = tx + excluded.tx
        """,
        (target_peer_id, source_peer_id),
    )


def apply_peer_totals_merge(
    db_path: str | None = None,
    *,
    source_router_name: str = DEFAULT_SOURCE_ROUTER_NAME,
    target_router_name: str = DEFAULT_TARGET_ROUTER_NAME,
    explicit_pairs: Sequence[tuple[int, int]] = (),
    explicit_ignores: Sequence[int] = (),
    backup_path: str | None = None,
) -> ApplyResult:
    resolved_db_path = _resolve_db_path(db_path)
    if not os.path.exists(resolved_db_path):
        raise PeerTotalsMergePreflightError(f"database not found: {resolved_db_path}")

    with _connect(resolved_db_path, read_only=True) as preview_conn:
        qc = _quick_check(preview_conn)
        if qc != "ok":
            raise PeerTotalsMergePreflightError(f"database quick_check failed: {qc}")
        preview_plan = _build_plan_from_conn(
            preview_conn,
            source_router_name=source_router_name,
            target_router_name=target_router_name,
            explicit_pairs=explicit_pairs,
            explicit_ignores=explicit_ignores,
        )
        if not preview_plan.pairs:
            return ApplyResult(
                db_path=resolved_db_path,
                backup_path=None,
                no_op=True,
                skipped_sources=preview_plan.already_merged_sources,
                pairs=(),
            )
        preview_sources = {pair.source_peer.id for pair in preview_plan.pairs}
        preview_merged = set(preview_plan.already_merged_sources)
        if preview_merged:
            if preview_merged == preview_sources:
                return ApplyResult(
                    db_path=resolved_db_path,
                    backup_path=None,
                    no_op=True,
                    skipped_sources=tuple(sorted(preview_merged)),
                    pairs=(),
                )
            partial = ", ".join(str(peer_id) for peer_id in sorted(preview_merged))
            raise PeerTotalsMergePreflightError(
                f"refusing partial rerun; sources already merged: {partial}"
            )

        resolved_backup_path = os.path.abspath(backup_path) if backup_path else _default_backup_path(resolved_db_path)
        _backup_database(preview_conn, resolved_backup_path)

    with _connect(resolved_db_path, read_only=False) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            qc = _quick_check(conn)
            if qc != "ok":
                raise PeerTotalsMergePreflightError(f"database quick_check failed after backup: {qc}")

            plan = _build_plan_from_conn(
                conn,
                source_router_name=source_router_name,
                target_router_name=target_router_name,
                explicit_pairs=explicit_pairs,
                explicit_ignores=explicit_ignores,
            )
            if not plan.pairs:
                conn.rollback()
                return ApplyResult(
                    db_path=resolved_db_path,
                    backup_path=resolved_backup_path,
                    no_op=True,
                    skipped_sources=plan.already_merged_sources,
                    pairs=(),
                )

            planned_sources = {pair.source_peer.id for pair in plan.pairs}
            already_merged = set(plan.already_merged_sources)
            if already_merged:
                if already_merged == planned_sources:
                    conn.rollback()
                    return ApplyResult(
                        db_path=resolved_db_path,
                        backup_path=resolved_backup_path,
                        no_op=True,
                        skipped_sources=tuple(sorted(already_merged)),
                        pairs=(),
                    )
                partial = ", ".join(str(peer_id) for peer_id in sorted(already_merged))
                raise PeerTotalsMergePreflightError(
                    f"refusing partial rerun; sources already merged: {partial}"
                )

            _ensure_ledger_schema(conn)

            applied_pairs: list[ApplyPairResult] = []
            for pair in plan.pairs:
                _copy_usage_rows(
                    conn,
                    table="usage_minute",
                    key_col="minute_ts",
                    source_peer_id=pair.source_peer.id,
                    target_peer_id=pair.target_peer.id,
                )
                _copy_usage_rows(
                    conn,
                    table="usage_daily",
                    key_col="day",
                    source_peer_id=pair.source_peer.id,
                    target_peer_id=pair.target_peer.id,
                )
                _copy_usage_rows(
                    conn,
                    table="usage_monthly",
                    key_col="month_key",
                    source_peer_id=pair.source_peer.id,
                    target_peer_id=pair.target_peer.id,
                )
                if pair.source_peer.selected:
                    conn.execute("UPDATE peers SET selected = 1 WHERE id = ?", (pair.target_peer.id,))
                conn.execute("UPDATE peers SET selected = 0 WHERE id = ?", (pair.source_peer.id,))
                conn.execute(
                    """
                    INSERT INTO peer_totals_merge (
                        source_peer_id,
                        target_peer_id,
                        source_router_id,
                        target_router_id,
                        merge_mode,
                        match_type,
                        usage_minute_rows,
                        usage_daily_rows,
                        usage_monthly_rows,
                        applied_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pair.source_peer.id,
                        pair.target_peer.id,
                        pair.source_peer.router_id,
                        pair.target_peer.router_id,
                        MERGE_MODE_TOTALS_ONLY,
                        pair.match_type,
                        pair.usage_minute_rows,
                        pair.usage_daily_rows,
                        pair.usage_monthly_rows,
                        _utc_now().replace(tzinfo=None).isoformat(sep=" "),
                    ),
                )
                applied_pairs.append(
                    ApplyPairResult(
                        source_peer_id=pair.source_peer.id,
                        target_peer_id=pair.target_peer.id,
                        match_type=pair.match_type,
                        usage_minute_rows=pair.usage_minute_rows,
                        usage_daily_rows=pair.usage_daily_rows,
                        usage_monthly_rows=pair.usage_monthly_rows,
                    )
                )

            conn.commit()
            return ApplyResult(
                db_path=resolved_db_path,
                backup_path=resolved_backup_path,
                no_op=False,
                skipped_sources=(),
                pairs=tuple(applied_pairs),
            )
        except Exception:
            conn.rollback()
            raise


def _print_plan(plan: MergePlan) -> None:
    print(f"DB: {plan.db_path}")
    print(f"Source router: {plan.source_router_name} ({plan.source_router_id})")
    print(f"Target router: {plan.target_router_name} ({plan.target_router_id})")
    print(f"Pairs: {plan.distinct_source_count} sources -> {plan.distinct_target_count} targets")
    print(
        "Rows to copy: "
        f"minute={plan.total_usage_minute_rows}, "
        f"daily={plan.total_usage_daily_rows}, "
        f"monthly={plan.total_usage_monthly_rows}"
    )
    if plan.already_merged_sources:
        merged_ids = ", ".join(str(peer_id) for peer_id in plan.already_merged_sources)
        print(f"Already merged sources: {merged_ids}")
    if plan.ignored_sources:
        ignored = ", ".join(str(peer_id) for peer_id in plan.ignored_sources)
        print(f"Ignored sources: {ignored}")
    print("Planned pairs:")
    for pair in plan.pairs:
        print(
            f"  {pair.source_peer.id} ({pair.source_peer.router_name}:{pair.source_peer.name})"
            f" -> {pair.target_peer.id} ({pair.target_peer.router_name}:{pair.target_peer.name})"
            f" [{pair.match_type}] minute={pair.usage_minute_rows} daily={pair.usage_daily_rows}"
            f" monthly={pair.usage_monthly_rows}"
        )


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Copy peer summary totals into canonical peers for retiring Arvan.")
    parser.add_argument("--db-path", help="SQLite database path. Defaults to the configured app database path.")
    parser.add_argument(
        "--pair",
        action="append",
        default=[],
        type=_parse_pair,
        help="Explicit source:target pair override. Can be repeated.",
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        type=_parse_peer_id,
        help="Source peer id to ignore. Can be repeated.",
    )
    parser.add_argument("--backup-path", help="Backup file path to create before apply.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Print the merge plan without writing.")
    mode.add_argument("--apply", action="store_true", help="Apply the merge plan.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)
    dry_run = bool(args.dry_run or not args.apply)
    try:
        if dry_run:
            plan = build_merge_plan(
                db_path=args.db_path,
                explicit_pairs=args.pair,
                explicit_ignores=args.ignore,
            )
            _print_plan(plan)
            return 0
        result = apply_peer_totals_merge(
            db_path=args.db_path,
            explicit_pairs=args.pair,
            explicit_ignores=args.ignore,
            backup_path=args.backup_path,
        )
        if result.no_op:
            if result.skipped_sources:
                skipped = ", ".join(str(peer_id) for peer_id in result.skipped_sources)
                print(f"No-op: sources already merged ({skipped})")
            else:
                print("No-op: nothing to merge")
            return 0
        print(f"Applied peer totals merge to {len(result.pairs)} source peers")
        print(f"Backup: {result.backup_path}")
        print(
            "Copied rows: "
            f"minute={result.total_usage_minute_rows}, "
            f"daily={result.total_usage_daily_rows}, "
            f"monthly={result.total_usage_monthly_rows}"
        )
        return 0
    except PeerTotalsMergeError as exc:
        print(f"peer totals merge failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
