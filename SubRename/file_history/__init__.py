"""Persistent, fail-closed filesystem history for SubApp."""

from .manager import OperationHistoryManager
from .models import (
    FileFingerprint,
    HistorySummary,
    OperationResult,
    RecycleToken,
    RestoreResult,
    SourceRowSnapshot,
    operation_label,
)
from .trash_base import (
    ManualRecoveryRequiredError,
    RestorableBackendUnavailableError,
    UnsafeFilesystemMutationError,
    choose_restore_destination,
    create_trash_backend,
)

__all__ = [
    "FileFingerprint",
    "HistorySummary",
    "ManualRecoveryRequiredError",
    "OperationHistoryManager",
    "OperationResult",
    "RecycleToken",
    "RestorableBackendUnavailableError",
    "RestoreResult",
    "SourceRowSnapshot",
    "UnsafeFilesystemMutationError",
    "choose_restore_destination",
    "create_trash_backend",
    "operation_label",
]
