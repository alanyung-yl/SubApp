from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def new_id() -> str:
    return str(uuid4())


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_loads(value: str | None, default: Any = None) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class FileFingerprint:
    size: int
    mtime_ns: int
    sha256: str | None = None

    @classmethod
    def capture(cls, path: str | os.PathLike[str], *, hash_file: bool = True) -> "FileFingerprint":
        p = os.fspath(path)
        stat = os.stat(p, follow_symlinks=False)
        digest: str | None = None
        if hash_file and os.path.isfile(p):
            sha = hashlib.sha256()
            with open(p, "rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    sha.update(block)
            digest = sha.hexdigest()
        return cls(size=stat.st_size, mtime_ns=stat.st_mtime_ns, sha256=digest)

    def matches(self, path: str | os.PathLike[str]) -> bool:
        try:
            current = FileFingerprint.capture(path, hash_file=self.sha256 is not None)
        except OSError:
            return False
        return current == self

    def to_json(self) -> str:
        return json_dumps(asdict(self))

    @classmethod
    def from_json(cls, value: str | None) -> "FileFingerprint | None":
        data = json_loads(value)
        return cls(**data) if isinstance(data, dict) else None


@dataclass
class SourceRowSnapshot:
    original_row_index: int
    original_name: str
    rename_to: str
    source_path: str
    plan_status: str
    execution_status: str
    subtitle_status: str
    row_plan_metadata: dict[str, Any] = field(default_factory=dict)
    previous_rename_value: str = ""
    rename_in_place: bool = False

    def to_json(self) -> str:
        return json_dumps(asdict(self))

    @classmethod
    def from_json(cls, value: str | None) -> "SourceRowSnapshot | None":
        data = json_loads(value)
        return cls(**data) if isinstance(data, dict) else None


@dataclass
class RecycleToken:
    token_id: str
    original_path: str
    recycle_identity_pidl: bytes | None = None
    restorable: bool = False

    @classmethod
    def from_row(cls, row: Any) -> "RecycleToken":
        keys = set(row.keys())
        return cls(
            token_id=row["token_id"],
            original_path=row["original_path"],
            recycle_identity_pidl=row["recycle_identity_pidl"],
            restorable=bool(row["restorable"]) if "restorable" in keys else bool(row["recycle_identity_pidl"]),
        )


@dataclass
class RestoreResult:
    success: bool
    requested_path: str
    actual_path: str | None = None
    error: str | None = None


@dataclass
class HistorySummary:
    job_id: str
    description: str
    operation_type: str


OPERATION_LABELS = {
    "RENAME_BATCH": "Rename",
    "RETRY_BATCH": "Retry",
    "DELETE_DESTINATION": "Delete Destination Subtitles",
    "DELETE_SOURCE_TABLE": "Delete Source Subtitles",
}


def operation_label(operation_type: str, fallback: str = "File Operation") -> str:
    """Return a stable, count-free label, including for legacy database rows."""
    return OPERATION_LABELS.get(operation_type, fallback)


@dataclass
class OperationResult:
    action: str
    job_id: str | None
    description: str
    state: str
    operation_type: str = ""
    original_total: int = 0
    eligible_count: int = 0
    succeeded: int = 0
    kept_both: int = 0
    modified_preserved: int = 0
    missing: int = 0
    failed: int = 0
    details: list[str] = field(default_factory=list)
    ui_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def had_issues(self) -> bool:
        return bool(self.modified_preserved or self.missing or self.failed)

    @property
    def label(self) -> str:
        return operation_label(self.operation_type, self.description or "File Operation")

    def summary_text(self) -> str:
        """Format the concise, stable text shared by the UI and file log."""
        action = "Undo" if self.action == "UNDO" else "Redo"
        prefix = f"{action} {self.label}"
        if self.action == "UNDO" and self.original_total:
            text = f"{prefix}: {self.succeeded} of {self.original_total} files."
        else:
            text = f"{prefix}: {self.succeeded} files."
        if self.modified_preserved:
            disposition = "preserved" if self.action == "UNDO" else "remained unchanged"
            text += f" {self.modified_preserved} user modified files {disposition}."
        extras: list[str] = []
        if self.kept_both:
            extras.append(f"{self.kept_both} kept both")
        if self.missing:
            extras.append(f"{self.missing} missing")
        if self.failed:
            extras.append(f"{self.failed} failed")
        if extras:
            text += " " + ", ".join(extras).capitalize() + "."
        return text
