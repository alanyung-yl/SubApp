from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from .models import (
    HistorySummary,
    RecycleToken,
    new_id,
    operation_label,
    utc_now,
)


SCHEMA_VERSION = 1
MAX_JOBS = 100
MAX_COMMANDS = 20_000
TARGET_DATABASE_BYTES = 20 * 1024 * 1024


SCHEMA = """
CREATE TABLE IF NOT EXISTS history_meta (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    schema_version INTEGER NOT NULL,
    cursor_sequence INTEGER NOT NULL DEFAULT 0,
    next_sequence INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    sequence_no INTEGER NOT NULL UNIQUE,
    operation_type TEXT NOT NULL,
    description TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    item_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    error_summary TEXT
);

CREATE TABLE IF NOT EXISTS commands (
    command_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    sequence_index INTEGER NOT NULL,
    command_type TEXT NOT NULL,
    state TEXT NOT NULL,
    source_path TEXT,
    destination_path TEXT,
    active_path TEXT,
    alternate_path TEXT,
    before_fingerprint TEXT,
    after_fingerprint TEXT,
    ui_snapshot_json TEXT,
    command_data_json TEXT,
    error_message TEXT,
    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE,
    UNIQUE(job_id, sequence_index)
);

CREATE TABLE IF NOT EXISTS recycle_tokens (
    token_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    token_role TEXT NOT NULL,
    token_generation INTEGER NOT NULL DEFAULT 1,
    backend TEXT NOT NULL,
    original_path TEXT NOT NULL,
    recycle_identity_pidl BLOB,
    restorable INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL,
    error_message TEXT,
    FOREIGN KEY(command_id) REFERENCES commands(command_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_sequence ON jobs(sequence_no);
CREATE INDEX IF NOT EXISTS idx_commands_job ON commands(job_id, sequence_index);
CREATE INDEX IF NOT EXISTS idx_tokens_command ON recycle_tokens(command_id, token_role, token_generation);
"""


class HistoryStore:
    """Small connection-per-operation SQLite store safe for worker threads."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
        finally:
            connection.close()
        with self.transaction() as connection:
            connection.executescript(SCHEMA)
            connection.execute(
                "INSERT OR IGNORE INTO history_meta(singleton_id, schema_version) VALUES(1, ?)",
                (SCHEMA_VERSION,),
            )
            version = connection.execute(
                "SELECT schema_version FROM history_meta WHERE singleton_id=1"
            ).fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(f"Operation history schema {version} is newer than supported {SCHEMA_VERSION}")

    def recover_interrupted(self) -> int:
        now = utc_now()
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT job_id, sequence_no, state FROM jobs "
                "WHERE state='STARTED'"
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE commands SET state='FAILED', "
                    "error_message='SubApp stopped before the command state was recorded' "
                    "WHERE job_id=? AND state='STARTED'",
                    (row["job_id"],),
                )
                counts = connection.execute(
                    "SELECT COUNT(*) AS total, "
                    "SUM(state IN ('APPLIED','REDONE')) AS active "
                    "FROM commands WHERE job_id=?",
                    (row["job_id"],),
                ).fetchone()
                total = int(counts["total"])
                active = int(counts["active"] or 0)
                state, success = ("INTERRUPTED", active) if active else ("FAILED", 0)
                connection.execute(
                    "UPDATE jobs SET state=?, item_count=?, success_count=?, failed_count=?, "
                    "error_summary=?, updated_at=? WHERE job_id=?",
                    (state, total, success, total - success, "SubApp stopped during this job", now, row["job_id"]),
                )
                if success:
                    connection.execute(
                        "UPDATE history_meta SET cursor_sequence=MAX(cursor_sequence, ?) WHERE singleton_id=1",
                        (row["sequence_no"],),
                    )
            return len(rows)

    def begin_job(self, operation_type: str, description: str) -> str:
        now = utc_now()
        job_id = new_id()
        with self.transaction() as connection:
            blocked = connection.execute(
                "SELECT job_id FROM jobs WHERE state='MANUAL_RECOVERY_REQUIRED' LIMIT 1"
            ).fetchone()
            if blocked:
                raise RuntimeError(
                    "New filesystem work is blocked until the unresolved manual-recovery "
                    f"job is reviewed: {blocked['job_id']}"
                )
            meta = connection.execute(
                "SELECT cursor_sequence, next_sequence FROM history_meta WHERE singleton_id=1"
            ).fetchone()
            connection.execute(
                "DELETE FROM jobs WHERE sequence_no > ? AND state!='MANUAL_RECOVERY_REQUIRED'",
                (meta["cursor_sequence"],),
            )
            sequence = int(meta["next_sequence"])
            connection.execute(
                "INSERT INTO jobs(job_id, sequence_no, operation_type, description, state, created_at, updated_at) "
                "VALUES(?,?,?,?, 'STARTED', ?, ?)",
                (job_id, sequence, operation_type, description, now, now),
            )
            connection.execute(
                "UPDATE history_meta SET next_sequence=? WHERE singleton_id=1", (sequence + 1,)
            )
        return job_id

    def add_command(
        self,
        job_id: str,
        command_type: str,
        *,
        source_path: str | None = None,
        destination_path: str | None = None,
        active_path: str | None = None,
        before_fingerprint: str | None = None,
        after_fingerprint: str | None = None,
        ui_snapshot_json: str | None = None,
        command_data_json: str | None = None,
    ) -> str:
        command_id = new_id()
        with self.transaction() as connection:
            job = connection.execute(
                "SELECT state FROM jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if job is None or job["state"] != "STARTED":
                raise RuntimeError("Commands may only be added to an active STARTED history job")
            sequence_index = int(connection.execute(
                "SELECT COALESCE(MAX(sequence_index), -1) + 1 FROM commands WHERE job_id=?",
                (job_id,),
            ).fetchone()[0])
            connection.execute(
                "INSERT INTO commands(command_id, job_id, sequence_index, command_type, state, source_path, "
                "destination_path, active_path, before_fingerprint, after_fingerprint, ui_snapshot_json, command_data_json) "
                "VALUES(?,?,?,?, 'STARTED', ?,?,?,?,?,?,?)",
                (
                    command_id, job_id, sequence_index, command_type, source_path,
                    destination_path, active_path, before_fingerprint, after_fingerprint,
                    ui_snapshot_json, command_data_json,
                ),
            )
            connection.execute(
                "UPDATE jobs SET item_count=item_count+1, updated_at=? WHERE job_id=?", (utc_now(), job_id)
            )
        return command_id

    def update_command(self, command_id: str, **fields: Any) -> None:
        allowed = {
            "state", "source_path", "destination_path", "active_path", "alternate_path",
            "before_fingerprint", "after_fingerprint", "ui_snapshot_json", "command_data_json",
            "error_message",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE commands SET {assignments} WHERE command_id=?",
                (*values.values(), command_id),
            )
            connection.execute(
                "UPDATE jobs SET updated_at=? WHERE job_id=(SELECT job_id FROM commands WHERE command_id=?)",
                (utc_now(), command_id),
            )

    def add_token(self, command_id: str, role: str, token: RecycleToken) -> int:
        with self.transaction() as connection:
            generation = int(connection.execute(
                "SELECT COALESCE(MAX(token_generation), 0) + 1 FROM recycle_tokens "
                "WHERE command_id=? AND token_role=?",
                (command_id, role),
            ).fetchone()[0])
            connection.execute(
                "INSERT INTO recycle_tokens(token_id, command_id, token_role, token_generation, backend, "
                "original_path, recycle_identity_pidl, restorable, state) "
                "VALUES(?,?,?,?, 'windows-recycle', ?,?,?, 'AVAILABLE')",
                (
                    token.token_id, command_id, role, generation, token.original_path,
                    token.recycle_identity_pidl, int(token.restorable),
                ),
            )
            return generation

    def latest_token(self, command_id: str, role: str, *, state: str | None = "AVAILABLE") -> RecycleToken | None:
        query = "SELECT * FROM recycle_tokens WHERE command_id=? AND token_role=?"
        params: list[Any] = [command_id, role]
        if state is not None:
            query += " AND state=?"
            params.append(state)
        query += " ORDER BY token_generation DESC LIMIT 1"
        with self.read() as connection:
            row = connection.execute(query, params).fetchone()
        return RecycleToken.from_row(row) if row else None

    def update_token(self, token_id: str, *, state: str, error_message: str | None = None) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE recycle_tokens SET state=?, error_message=? WHERE token_id=?",
                (state, error_message, token_id),
            )

    def mark_manual_recovery(self, command_id: str, error_message: str) -> None:
        """Record the rare case where an exact Recycle Bin item needs user attention."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT job_id FROM commands WHERE command_id=?", (command_id,)
            ).fetchone()
            if row is None:
                return
            connection.execute(
                "UPDATE commands SET state='MANUAL_RECOVERY_REQUIRED', error_message=? "
                "WHERE command_id=?",
                (error_message, command_id),
            )
            connection.execute(
                "UPDATE jobs SET state='MANUAL_RECOVERY_REQUIRED', error_summary=?, updated_at=? "
                "WHERE job_id=?",
                (error_message, utc_now(), row["job_id"]),
            )

    def complete_job(self, job_id: str) -> sqlite3.Row | None:
        with self.transaction() as connection:
            current = connection.execute(
                "SELECT state FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            counts = connection.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN state IN ('APPLIED','REDONE') THEN 1 ELSE 0 END) AS ok, "
                "SUM(CASE WHEN state NOT IN ('APPLIED','REDONE') THEN 1 ELSE 0 END) AS failed "
                "FROM commands WHERE job_id=?",
                (job_id,),
            ).fetchone()
            total, ok, failed = int(counts["total"]), int(counts["ok"] or 0), int(counts["failed"] or 0)
            state = (
                "MANUAL_RECOVERY_REQUIRED"
                if current and current["state"] == "MANUAL_RECOVERY_REQUIRED"
                else "APPLIED" if ok and not failed
                else "PARTIALLY_APPLIED" if ok
                else "FAILED"
            )
            connection.execute(
                "UPDATE jobs SET state=?, item_count=?, success_count=?, failed_count=?, updated_at=? WHERE job_id=?",
                (state, total, ok, failed, utc_now(), job_id),
            )
            row = connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row and ok:
                connection.execute(
                    "UPDATE history_meta SET cursor_sequence=? WHERE singleton_id=1", (row["sequence_no"],)
                )
            return row

    def abandon_empty_job(self, job_id: str) -> None:
        with self.transaction() as connection:
            connection.execute("DELETE FROM jobs WHERE job_id=? AND item_count=0", (job_id,))

    def job(self, job_id: str) -> sqlite3.Row | None:
        with self.read() as connection:
            return connection.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()

    def commands(self, job_id: str, *, reverse: bool = False) -> list[sqlite3.Row]:
        direction = "DESC" if reverse else "ASC"
        with self.read() as connection:
            return connection.execute(
                f"SELECT * FROM commands WHERE job_id=? ORDER BY sequence_index {direction}", (job_id,)
            ).fetchall()

    def manual_recovery_records(self) -> list[dict[str, Any]]:
        with self.read() as connection:
            rows = connection.execute(
                "SELECT j.job_id, j.state AS job_state, j.error_summary, "
                "c.command_id, c.state AS command_state, c.source_path, c.destination_path, "
                "c.active_path, c.error_message FROM jobs j "
                "LEFT JOIN commands c ON c.job_id=j.job_id "
                "WHERE j.state='MANUAL_RECOVERY_REQUIRED' "
                "AND (c.command_id IS NULL OR c.state='MANUAL_RECOVERY_REQUIRED') "
                "ORDER BY j.sequence_no, c.sequence_index"
            ).fetchall()
        records: list[dict[str, Any]] = []
        for row in rows:
            records.append({
                "recovery_id": f"sqlite:{row['job_id']}:{row['command_id'] or 'job'}",
                "status": "MANUAL_RECOVERY_REQUIRED",
                "job_id": row["job_id"],
                "command_id": row["command_id"],
                "original_path": row["active_path"] or row["destination_path"] or row["source_path"],
                "operation_error": row["error_message"] or row["error_summary"],
                "source": "operation_history.sqlite3",
            })
        return records

    def acknowledge_manual_recovery(self) -> int:
        with self.transaction() as connection:
            jobs = connection.execute(
                "SELECT job_id, sequence_no FROM jobs "
                "WHERE state='MANUAL_RECOVERY_REQUIRED'"
            ).fetchall()
            connection.execute(
                "UPDATE commands SET state='FAILED' "
                "WHERE state='MANUAL_RECOVERY_REQUIRED'"
            )
            for job in jobs:
                counts = connection.execute(
                    "SELECT COUNT(*) AS total, "
                    "SUM(CASE WHEN state IN ('APPLIED','REDONE') THEN 1 ELSE 0 END) AS ok "
                    "FROM commands WHERE job_id=?",
                    (job["job_id"],),
                ).fetchone()
                total, ok = int(counts["total"]), int(counts["ok"] or 0)
                state = (
                    "APPLIED"
                    if ok and ok == total
                    else "PARTIALLY_APPLIED" if ok else "FAILED"
                )
                connection.execute(
                    "UPDATE jobs SET state=?, item_count=?, success_count=?, failed_count=?, "
                    "updated_at=? WHERE job_id=?",
                    (state, total, ok, total - ok, utc_now(), job["job_id"]),
                )
                if ok:
                    connection.execute(
                        "UPDATE history_meta SET cursor_sequence=MAX(cursor_sequence, ?) "
                        "WHERE singleton_id=1",
                        (job["sequence_no"],),
                    )
            return len(jobs)

    def command_state_counts(self, job_id: str) -> dict[str, int]:
        with self.read() as connection:
            rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM commands WHERE job_id=? GROUP BY state",
                (job_id,),
            ).fetchall()
        return {str(row["state"]): int(row["count"]) for row in rows}

    def _summary(self, redo: bool) -> HistorySummary | None:
        with self.read() as connection:
            cursor = int(connection.execute(
                "SELECT cursor_sequence FROM history_meta WHERE singleton_id=1"
            ).fetchone()[0])
            if redo:
                row = connection.execute(
                    "SELECT * FROM jobs WHERE sequence_no>? AND state IN ('UNDONE','PARTIALLY_UNDONE') "
                    "ORDER BY sequence_no ASC LIMIT 1", (cursor,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM jobs WHERE sequence_no<=? AND success_count>0 AND state IN "
                    "('APPLIED','REDONE','PARTIALLY_APPLIED','PARTIALLY_REDONE','INTERRUPTED') "
                    "ORDER BY sequence_no DESC LIMIT 1", (cursor,),
                ).fetchone()
        if not row:
            return None
        return HistorySummary(
            job_id=row["job_id"],
            description=operation_label(row["operation_type"], row["description"]),
            operation_type=row["operation_type"],
        )

    def undo_summary(self) -> HistorySummary | None:
        return self._summary(False)

    def redo_summary(self) -> HistorySummary | None:
        return self._summary(True)

    def finish_transition(
        self,
        job_id: str,
        action: str,
        *,
        transitioned_count: int | None = None,
    ) -> str:
        with self.transaction() as connection:
            job = connection.execute(
                "SELECT sequence_no, state FROM jobs WHERE job_id=?", (job_id,)
            ).fetchone()
            if job["state"] == "MANUAL_RECOVERY_REQUIRED":
                return "MANUAL_RECOVERY_REQUIRED"
            if transitioned_count == 0:
                return str(job["state"])
            rows = connection.execute(
                "SELECT state, COUNT(*) AS count FROM commands WHERE job_id=? GROUP BY state",
                (job_id,),
            ).fetchall()
            counts = {str(row["state"]): int(row["count"]) for row in rows}
            success_state = "UNDONE" if action == "UNDO" else "REDONE"
            succeeded = counts.get(success_state, 0)
            total = sum(counts.values())
            partial = succeeded < total
            state = ("PARTIALLY_UNDONE" if partial else "UNDONE") if action == "UNDO" else (
                "PARTIALLY_REDONE" if partial else "REDONE"
            )
            connection.execute(
                "UPDATE jobs SET state=?, success_count=?, failed_count=?, updated_at=? WHERE job_id=?",
                (state, succeeded, total - succeeded, utc_now(), job_id),
            )
            if action == "UNDO":
                previous = connection.execute(
                    "SELECT COALESCE(MAX(sequence_no), 0) FROM jobs WHERE sequence_no < ?",
                    (job["sequence_no"],),
                ).fetchone()[0]
                connection.execute(
                    "UPDATE history_meta SET cursor_sequence=? WHERE singleton_id=1", (previous,)
                )
            else:
                connection.execute(
                    "UPDATE history_meta SET cursor_sequence=? WHERE singleton_id=1", (job["sequence_no"],)
                )
        return state

    def maintenance(self) -> None:
        with self.transaction() as connection:
            while True:
                counts = connection.execute(
                    "SELECT COUNT(*) AS jobs, (SELECT COUNT(*) FROM commands) AS commands FROM jobs"
                ).fetchone()
                if counts["jobs"] <= MAX_JOBS and counts["commands"] <= MAX_COMMANDS:
                    break
                row = connection.execute(
                    "SELECT job_id FROM jobs WHERE state NOT IN "
                    "('STARTED','INTERRUPTED','MANUAL_RECOVERY_REQUIRED') "
                    "ORDER BY sequence_no ASC LIMIT 1"
                ).fetchone()
                if not row:
                    break
                connection.execute("DELETE FROM jobs WHERE job_id=?", (row["job_id"],))
        try:
            if self.path.stat().st_size > TARGET_DATABASE_BYTES:
                with self.read() as connection:
                    connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except OSError:
            pass
