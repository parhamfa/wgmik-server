from __future__ import annotations

import argparse
import os
import shlex
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


DEFAULT_RETENTION = {
    "raw_sample_retention_hours": 24,
    "minute_rollup_retention_days": 90,
    "daily_rollup_retention_days": 0,
}

USAGE_SAMPLE_INDEX_NAME = "ix_usage_samples_peer_id_ts"


@dataclass
class RecoveryResult:
    output_path: str
    live_path: str | None
    archive_path: str | None
    backup_max_sample_id: int
    backup_max_action_id: int
    salvaged_samples: int
    salvaged_actions: int
    quick_check: str
    usage_samples_max_ts: str | None
    usage_minute_min_ts: str | None
    usage_minute_max_ts: str | None
    file_size_bytes: int


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _naive_utc(dt: datetime) -> datetime:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=None)


def _connect(path: str, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        uri = f"file:{Path(path).resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    else:
        conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA temp_store=FILE")
    return conn


def _quick_check(path: str) -> str:
    with _connect(path, read_only=True) as conn:
        row = conn.execute("PRAGMA quick_check").fetchone()
    return row[0] if row else "unknown"


def _foreign_key_check(path: str) -> list[sqlite3.Row]:
    with _connect(path, read_only=True) as conn:
        return conn.execute("PRAGMA foreign_key_check").fetchall()


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [str(row["name"]) for row in rows]


def _read_int_setting(conn: sqlite3.Connection, key: str, default: int, minimum: int, maximum: int) -> int:
    row = conn.execute("SELECT value FROM settings_kv WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    try:
        value = int(row["value"])
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _load_retention_settings(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "raw_sample_retention_hours": _read_int_setting(
            conn,
            "raw_sample_retention_hours",
            DEFAULT_RETENTION["raw_sample_retention_hours"],
            1,
            24 * 365,
        ),
        "minute_rollup_retention_days": _read_int_setting(
            conn,
            "minute_rollup_retention_days",
            DEFAULT_RETENTION["minute_rollup_retention_days"],
            1,
            3650,
        ),
        "daily_rollup_retention_days": _read_int_setting(
            conn,
            "daily_rollup_retention_days",
            DEFAULT_RETENTION["daily_rollup_retention_days"],
            0,
            36500,
        ),
    }


def _probe_salvage_source(corrupt_path: str, work_dir: str) -> tuple[str, str]:
    probe_path = os.path.join(work_dir, "salvage-probe.db")
    shutil.copy2(corrupt_path, probe_path)
    with _connect(probe_path) as conn:
        conn.execute(f"DROP INDEX IF EXISTS {USAGE_SAMPLE_INDEX_NAME}")
        conn.commit()
    result = _quick_check(probe_path)
    if result == "ok":
        return probe_path, "probe_copy_without_usage_samples_index"
    return corrupt_path, f"original_corrupt_source_after_probe_failed:{result}"


def _build_recover_source(corrupt_path: str, work_dir: str) -> str:
    recovered_source = os.path.join(work_dir, "salvage-recovered.db")
    if os.path.exists(recovered_source):
        os.remove(recovered_source)
    command = (
        f"sqlite3 {shlex.quote(corrupt_path)} '.recover' | "
        f"sqlite3 {shlex.quote(recovered_source)}"
    )
    subprocess.run(["/bin/zsh", "-lc", command], check=True)
    return recovered_source


def _stream_copy_rows(
    source_conn: sqlite3.Connection,
    target_conn: sqlite3.Connection,
    *,
    select_sql: str,
    insert_sql: str,
    start_value: int,
    batch_size: int = 50_000,
) -> int:
    copied = 0
    cursor_value = start_value
    while True:
        rows = source_conn.execute(select_sql, (cursor_value, batch_size)).fetchall()
        if not rows:
            break
        target_conn.executemany(insert_sql, [tuple(row) for row in rows])
        target_conn.commit()
        copied += len(rows)
        cursor_value = int(rows[-1][0])
    return copied


def _merge_small_table(source_conn: sqlite3.Connection, target_conn: sqlite3.Connection, table: str) -> int:
    columns = _table_columns(source_conn, table)
    if not columns:
        return 0
    column_list = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    upsert_sql = f"INSERT OR REPLACE INTO {table} ({column_list}) VALUES ({placeholders})"
    rows = source_conn.execute(f"SELECT {column_list} FROM {table}").fetchall()
    if not rows:
        return 0
    target_conn.executemany(upsert_sql, [tuple(row) for row in rows])
    target_conn.commit()
    return len(rows)


def _rebuild_usage_rollups(target_conn: sqlite3.Connection, *, minute_cutoff: datetime) -> None:
    target_conn.execute("DELETE FROM usage_minute")
    target_conn.execute("DELETE FROM usage_daily")
    target_conn.execute("DELETE FROM usage_monthly")
    target_conn.execute("DROP TABLE IF EXISTS _rebuild_usage_delta")
    target_conn.commit()

    target_conn.execute(
        f"""
        CREATE TABLE _rebuild_usage_delta AS
        SELECT peer_id, ts, delta_rx, delta_tx
        FROM (
            SELECT
                peer_id,
                ts,
                CASE
                    WHEN prev_rx IS NULL THEN 0
                    WHEN rx < prev_rx THEN rx
                    ELSE rx - prev_rx
                END AS delta_rx,
                CASE
                    WHEN prev_tx IS NULL THEN 0
                    WHEN tx < prev_tx THEN tx
                    ELSE tx - prev_tx
                END AS delta_tx
            FROM (
                SELECT
                    peer_id,
                    ts,
                    rx,
                    tx,
                    LAG(rx) OVER (PARTITION BY peer_id ORDER BY ts, id) AS prev_rx,
                    LAG(tx) OVER (PARTITION BY peer_id ORDER BY ts, id) AS prev_tx
                FROM usage_samples INDEXED BY {USAGE_SAMPLE_INDEX_NAME}
            )
        )
        WHERE delta_rx <> 0 OR delta_tx <> 0
        """
    )
    target_conn.execute("CREATE INDEX IF NOT EXISTS ix_rebuild_usage_delta_peer_ts ON _rebuild_usage_delta (peer_id, ts)")
    target_conn.commit()

    target_conn.execute(
        """
        INSERT INTO usage_minute (peer_id, minute_ts, rx, tx)
        SELECT
            peer_id,
            strftime('%Y-%m-%d %H:%M:00', ts) AS minute_ts,
            SUM(delta_rx),
            SUM(delta_tx)
        FROM _rebuild_usage_delta
        WHERE ts >= ?
        GROUP BY peer_id, minute_ts
        """,
        (minute_cutoff.strftime("%Y-%m-%d %H:%M:%S"),),
    )
    target_conn.execute(
        """
        INSERT INTO usage_daily (peer_id, day, rx, tx)
        SELECT
            peer_id,
            substr(ts, 1, 10) AS day,
            SUM(delta_rx),
            SUM(delta_tx)
        FROM _rebuild_usage_delta
        GROUP BY peer_id, day
        """
    )
    target_conn.execute(
        """
        INSERT INTO usage_monthly (peer_id, month_key, rx, tx)
        SELECT
            peer_id,
            substr(ts, 1, 7) AS month_key,
            SUM(delta_rx),
            SUM(delta_tx)
        FROM _rebuild_usage_delta
        GROUP BY peer_id, month_key
        """
    )
    target_conn.execute("DROP TABLE IF EXISTS _rebuild_usage_delta")
    target_conn.commit()


def _ensure_recovery_schema(target_conn: sqlite3.Connection) -> None:
    target_conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS usage_minute (
            id INTEGER NOT NULL PRIMARY KEY,
            peer_id INTEGER NOT NULL,
            minute_ts DATETIME NOT NULL,
            rx BIGINT NOT NULL DEFAULT 0,
            tx BIGINT NOT NULL DEFAULT 0,
            FOREIGN KEY(peer_id) REFERENCES peers (id) ON DELETE CASCADE
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_minute_peer_ts ON usage_minute (peer_id, minute_ts);
        CREATE INDEX IF NOT EXISTS ix_usage_minute_minute_ts ON usage_minute (minute_ts);
        CREATE INDEX IF NOT EXISTS ix_usage_minute_peer_id_minute_ts ON usage_minute (peer_id, minute_ts);
        CREATE INDEX IF NOT EXISTS ix_usage_samples_peer_id_ts ON usage_samples (peer_id, ts);
        """
    )
    target_conn.commit()


def _cleanup_orphan_rows(target_conn: sqlite3.Connection) -> None:
    target_conn.execute("DELETE FROM peers WHERE router_id NOT IN (SELECT id FROM routers)")
    target_conn.execute("DELETE FROM quotas WHERE peer_id NOT IN (SELECT id FROM peers)")
    target_conn.execute("DELETE FROM usage_samples WHERE peer_id NOT IN (SELECT id FROM peers)")
    target_conn.execute("DELETE FROM usage_minute WHERE peer_id NOT IN (SELECT id FROM peers)")
    target_conn.execute("DELETE FROM usage_daily WHERE peer_id NOT IN (SELECT id FROM peers)")
    target_conn.execute("DELETE FROM usage_monthly WHERE peer_id NOT IN (SELECT id FROM peers)")
    target_conn.execute(
        "DELETE FROM actions WHERE peer_id IS NOT NULL AND peer_id NOT IN (SELECT id FROM peers)"
    )
    target_conn.commit()


def _batch_delete_before(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    cutoff_value: str,
    batch_size: int = 20_000,
) -> int:
    deleted = 0
    while True:
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE {column} < ? ORDER BY id LIMIT ?",
            (cutoff_value, batch_size),
        ).fetchall()
        if not rows:
            return deleted
        ids = [int(row[0]) for row in rows]
        placeholders = ", ".join("?" for _ in ids)
        conn.execute(f"DELETE FROM {table} WHERE id IN ({placeholders})", ids)
        conn.commit()
        deleted += len(ids)


def _validate_recovered_db(
    path: str,
    *,
    expected_usage_samples_max_id: int,
    expected_usage_samples_max_ts: str,
    expected_action_max_id: int,
    expected_peer_disabled: dict[int, int],
    expected_settings: dict[str, str],
    minimum_minute_start: str,
) -> None:
    quick = _quick_check(path)
    if quick != "ok":
        raise RuntimeError(f"Recovered database quick_check failed: {quick}")
    fk_errors = _foreign_key_check(path)
    if fk_errors:
        raise RuntimeError(f"Recovered database foreign_key_check failed: {fk_errors[:5]}")

    with _connect(path, read_only=True) as conn:
        usage_row = conn.execute(
            "SELECT MAX(id) AS max_id, MAX(ts) AS max_ts FROM usage_samples"
        ).fetchone()
        if int(usage_row["max_id"] or 0) != expected_usage_samples_max_id:
            raise RuntimeError(
                f"Recovered usage_samples max id mismatch: {usage_row['max_id']} != {expected_usage_samples_max_id}"
            )
        if str(usage_row["max_ts"] or "") != expected_usage_samples_max_ts:
            raise RuntimeError(
                f"Recovered usage_samples max ts mismatch: {usage_row['max_ts']} != {expected_usage_samples_max_ts}"
            )

        action_row = conn.execute("SELECT MAX(id) AS max_id FROM actions").fetchone()
        if int(action_row["max_id"] or 0) != expected_action_max_id:
            raise RuntimeError(
                f"Recovered actions max id mismatch: {action_row['max_id']} != {expected_action_max_id}"
            )

        for peer_id, disabled in expected_peer_disabled.items():
            row = conn.execute("SELECT disabled FROM peers WHERE id = ?", (peer_id,)).fetchone()
            if row is None or int(row["disabled"] or 0) != disabled:
                raise RuntimeError(f"Recovered peer {peer_id} disabled mismatch")

        for key, value in expected_settings.items():
            row = conn.execute("SELECT value FROM settings_kv WHERE key = ?", (key,)).fetchone()
            if row is None or str(row["value"]) != value:
                raise RuntimeError(f"Recovered setting {key} mismatch")

        minute_row = conn.execute(
            "SELECT MIN(minute_ts) AS min_ts, MAX(minute_ts) AS max_ts FROM usage_minute"
        ).fetchone()
        second_sample_row = conn.execute(
            """
            SELECT MIN(ts) AS min_ts
            FROM (
                SELECT
                    ts,
                    ROW_NUMBER() OVER (PARTITION BY peer_id ORDER BY ts, id) AS rn
                FROM usage_samples
            )
            WHERE rn > 1
            """
        ).fetchone()
        eligible_minute_start = minimum_minute_start
        if second_sample_row and second_sample_row["min_ts"] is not None:
            eligible_minute_start = max(str(second_sample_row["min_ts"]), minimum_minute_start)
        if minute_row["min_ts"] is None or str(minute_row["min_ts"]) > eligible_minute_start:
            raise RuntimeError(
                f"Recovered minute coverage starts too late: {minute_row['min_ts']} > {eligible_minute_start}"
            )


def recover_database(
    *,
    clean_backup_path: str,
    corrupt_db_path: str,
    output_path: str,
    live_db_path: str | None = None,
    work_dir: str | None = None,
) -> RecoveryResult:
    clean_backup_path = os.path.abspath(clean_backup_path)
    corrupt_db_path = os.path.abspath(corrupt_db_path)
    output_path = os.path.abspath(output_path)
    if live_db_path:
        live_db_path = os.path.abspath(live_db_path)

    if not os.path.exists(clean_backup_path):
        raise FileNotFoundError(clean_backup_path)
    if not os.path.exists(corrupt_db_path):
        raise FileNotFoundError(corrupt_db_path)

    work_dir = os.path.abspath(work_dir or os.path.join(os.path.dirname(output_path), "work"))
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    source_path, source_mode = _probe_salvage_source(corrupt_db_path, work_dir)
    print(f"Using salvage source: {source_path} ({source_mode})")

    shutil.copy2(clean_backup_path, output_path)

    try:
        with _connect(clean_backup_path, read_only=True) as clean_conn:
            backup_usage_row = clean_conn.execute(
                "SELECT MAX(id) AS max_id, MAX(ts) AS max_ts FROM usage_samples"
            ).fetchone()
            backup_action_row = clean_conn.execute(
                "SELECT MAX(id) AS max_id FROM actions"
            ).fetchone()
            backup_max_sample_id = int(backup_usage_row["max_id"] or 0)
            backup_max_sample_ts = str(backup_usage_row["max_ts"] or "")
            backup_max_action_id = int(backup_action_row["max_id"] or 0)

        try:
            salvaged_samples, salvaged_actions = _recover_into_output(
                source_path=source_path,
                output_path=output_path,
                backup_max_sample_id=backup_max_sample_id,
                backup_max_action_id=backup_max_action_id,
            )
        except sqlite3.DatabaseError:
            recovered_source = _build_recover_source(corrupt_db_path, work_dir)
            salvaged_samples, salvaged_actions = _recover_into_output(
                source_path=recovered_source,
                output_path=output_path,
                backup_max_sample_id=backup_max_sample_id,
                backup_max_action_id=backup_max_action_id,
            )

        with _connect(corrupt_db_path, read_only=True) as source_conn:
            usage_row = source_conn.execute(
                """
                SELECT MAX(u.id) AS max_id, MAX(u.ts) AS max_ts
                FROM usage_samples u NOT INDEXED
                JOIN peers p ON p.id = u.peer_id
                """
            ).fetchone()
            action_row = source_conn.execute(
                """
                SELECT MAX(a.id) AS max_id
                FROM actions a
                LEFT JOIN peers p ON p.id = a.peer_id
                WHERE a.peer_id IS NULL OR p.id IS NOT NULL
                """
            ).fetchone()
            peer_rows = source_conn.execute(
                "SELECT id, disabled FROM peers WHERE id IN (42, 43) ORDER BY id"
            ).fetchall()
            expected_peer_disabled = {int(row["id"]): int(row["disabled"] or 0) for row in peer_rows}
            settings_rows = source_conn.execute(
                "SELECT key, value FROM settings_kv WHERE key IN ('peer_default_scope_unit')"
            ).fetchall()
            expected_settings = {str(row["key"]): str(row["value"]) for row in settings_rows}
            expected_usage_samples_max_id = int(usage_row["max_id"] or 0)
            expected_usage_samples_max_ts = str(usage_row["max_ts"] or "")
            expected_action_max_id = int(action_row["max_id"] or 0)

        with _connect(output_path) as output_conn:
            _ensure_recovery_schema(output_conn)
            _cleanup_orphan_rows(output_conn)
            retention = _load_retention_settings(output_conn)
            minute_cutoff = _naive_utc(_utc_now() - timedelta(days=retention["minute_rollup_retention_days"]))
            _rebuild_usage_rollups(output_conn, minute_cutoff=minute_cutoff)
            _validate_recovered_db(
                output_path,
                expected_usage_samples_max_id=expected_usage_samples_max_id,
                expected_usage_samples_max_ts=expected_usage_samples_max_ts,
                expected_action_max_id=expected_action_max_id,
                expected_peer_disabled=expected_peer_disabled,
                expected_settings=expected_settings,
                minimum_minute_start=minute_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
            )

            raw_cutoff = _naive_utc(_utc_now() - timedelta(hours=retention["raw_sample_retention_hours"]))
            minute_prune_cutoff = _naive_utc(_utc_now() - timedelta(days=retention["minute_rollup_retention_days"]))
            _batch_delete_before(
                output_conn,
                table="usage_samples",
                column="ts",
                cutoff_value=raw_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
            )
            _batch_delete_before(
                output_conn,
                table="usage_minute",
                column="minute_ts",
                cutoff_value=minute_prune_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
            )
            if retention["daily_rollup_retention_days"] > 0:
                daily_cutoff = (_utc_now() - timedelta(days=retention["daily_rollup_retention_days"])).date().strftime("%Y-%m-%d")
                _batch_delete_before(
                    output_conn,
                    table="usage_daily",
                    column="day",
                    cutoff_value=daily_cutoff,
                )

        compacted_path = f"{output_path}.compacted"
        if os.path.exists(compacted_path):
            os.remove(compacted_path)
        with _connect(output_path) as conn:
            escaped = compacted_path.replace("'", "''")
            conn.execute(f"VACUUM INTO '{escaped}'")
        os.replace(compacted_path, output_path)

        _validate_recovered_db(
            output_path,
            expected_usage_samples_max_id=expected_usage_samples_max_id,
            expected_usage_samples_max_ts=expected_usage_samples_max_ts,
            expected_action_max_id=expected_action_max_id,
            expected_peer_disabled=expected_peer_disabled,
            expected_settings=expected_settings,
            minimum_minute_start=minute_cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        )

        archive_path = None
        if live_db_path:
            archive_dir = os.path.join(os.path.dirname(live_db_path), "backups")
            os.makedirs(archive_dir, exist_ok=True)
            archive_path = os.path.join(
                archive_dir,
                f"wgmik-{_utc_now().strftime('%Y%m%d-%H%M%S')}-corrupt-pre-recovered-cutover.db",
            )
            if os.path.exists(live_db_path):
                shutil.copy2(live_db_path, archive_path)
            shutil.copy2(output_path, live_db_path)

        with _connect(output_path, read_only=True) as conn:
            minute_row = conn.execute(
                "SELECT MIN(minute_ts) AS min_ts, MAX(minute_ts) AS max_ts FROM usage_minute"
            ).fetchone()
            usage_row = conn.execute(
                "SELECT MAX(ts) AS max_ts FROM usage_samples"
            ).fetchone()

        return RecoveryResult(
            output_path=output_path,
            live_path=live_db_path,
            archive_path=archive_path,
            backup_max_sample_id=backup_max_sample_id,
            backup_max_action_id=backup_max_action_id,
            salvaged_samples=salvaged_samples,
            salvaged_actions=salvaged_actions,
            quick_check=_quick_check(output_path),
            usage_samples_max_ts=str(usage_row["max_ts"] or "") if usage_row else None,
            usage_minute_min_ts=str(minute_row["min_ts"] or "") if minute_row else None,
            usage_minute_max_ts=str(minute_row["max_ts"] or "") if minute_row else None,
            file_size_bytes=os.path.getsize(output_path),
        )
    except Exception:
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass
        raise


def _recover_into_output(
    *,
    source_path: str,
    output_path: str,
    backup_max_sample_id: int,
    backup_max_action_id: int,
) -> tuple[int, int]:
    with _connect(source_path, read_only=True) as source_conn, _connect(output_path) as output_conn:
        for table in ("routers", "peers", "quotas", "settings_kv", "users"):
            _merge_small_table(source_conn, output_conn, table)

        salvaged_samples = _stream_copy_rows(
            source_conn,
            output_conn,
            select_sql=(
                "SELECT id, peer_id, ts, rx, tx, endpoint "
                "FROM usage_samples NOT INDEXED "
                "WHERE id > ? ORDER BY id LIMIT ?"
            ),
            insert_sql=(
                "INSERT OR IGNORE INTO usage_samples (id, peer_id, ts, rx, tx, endpoint) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            ),
            start_value=backup_max_sample_id,
        )
        salvaged_actions = _stream_copy_rows(
            source_conn,
            output_conn,
            select_sql=(
                "SELECT id, peer_id, ts, action, note "
                "FROM actions WHERE id > ? ORDER BY id LIMIT ?"
            ),
            insert_sql=(
                "INSERT OR IGNORE INTO actions (id, peer_id, ts, action, note) "
                "VALUES (?, ?, ?, ?, ?)"
            ),
            start_value=backup_max_action_id,
        )
    return salvaged_samples, salvaged_actions


def _build_default_output_path(corrupt_db_path: str) -> str:
    base_dir = os.path.join(os.path.dirname(corrupt_db_path), "recovery")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, f"wgmik-recovered-{_utc_now().strftime('%Y%m%d-%H%M%S')}.db")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover a corrupt WGMIK SQLite database from a clean backup plus salvaged deltas.")
    parser.add_argument("--clean-backup", required=True, help="Path to the last clean SQLite backup.")
    parser.add_argument("--corrupt-db", required=True, help="Path to the corrupt live SQLite database.")
    parser.add_argument("--output", help="Path to write the recovered database copy.")
    parser.add_argument("--live-db", help="Optional live DB path to replace after validation.")
    parser.add_argument("--work-dir", help="Working directory for probe and fallback files.")
    args = parser.parse_args(argv)

    output_path = args.output or _build_default_output_path(args.corrupt_db)
    result = recover_database(
        clean_backup_path=args.clean_backup,
        corrupt_db_path=args.corrupt_db,
        output_path=output_path,
        live_db_path=args.live_db,
        work_dir=args.work_dir,
    )
    print(f"Recovered DB: {result.output_path}")
    print(f"quick_check: {result.quick_check}")
    print(f"salvaged_samples: {result.salvaged_samples}")
    print(f"salvaged_actions: {result.salvaged_actions}")
    print(f"usage_samples_max_ts: {result.usage_samples_max_ts}")
    print(f"usage_minute_range: {result.usage_minute_min_ts} -> {result.usage_minute_max_ts}")
    print(f"file_size_bytes: {result.file_size_bytes}")
    if result.live_path:
        print(f"cutover_live_db: {result.live_path}")
    if result.archive_path:
        print(f"archived_previous_live_db: {result.archive_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
