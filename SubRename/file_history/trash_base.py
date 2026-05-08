from __future__ import annotations

import os
import sys
from typing import Iterable, Protocol

from .models import RecycleToken, RestoreResult


class TrashBackend(Protocol):
    supports_restore: bool

    def ensure_available(self) -> None: ...
    def preflight_job(self, paths: Iterable[str]) -> frozenset[str]: ...
    def recycle(self, path: str) -> RecycleToken: ...
    def restore(self, token: RecycleToken, requested_path: str) -> RestoreResult: ...
    def token_exists(self, token: RecycleToken) -> bool: ...


class RestorableBackendUnavailableError(RuntimeError):
    """The Windows backend cannot currently guarantee restoration."""


class UnsafeFilesystemMutationError(RuntimeError):
    """A user-file mutation was refused because a safety boundary failed."""


class RecycleRecoveryRequiredError(UnsafeFilesystemMutationError):
    """Internal signal: Windows moved an item and could not put it back."""

    def __init__(self, message: str, *, token: RecycleToken, rollback_error: str | None = None):
        super().__init__(message)
        self.token = token
        self.rollback_error = rollback_error


class ManualRecoveryRequiredError(UnsafeFilesystemMutationError):
    """A known Recycle Bin item needs explicit user attention."""


def filesystem_root(path: str) -> str:
    normalized = os.path.abspath(os.path.normpath(path))
    drive, _ = os.path.splitdrive(normalized)
    root = drive + os.sep if drive else os.path.abspath(os.sep)
    return os.path.normcase(os.path.normpath(root))


def choose_restore_destination(original_path: str, reserved_paths: set[str] | None = None) -> str:
    """Choose a Keep Both path without replacing anything already on disk."""
    reserved = {os.path.normcase(os.path.abspath(path)) for path in (reserved_paths or set())}
    original = os.path.abspath(original_path)
    if not os.path.lexists(original) and os.path.normcase(original) not in reserved:
        return original
    parent, name = os.path.split(original)
    stem, suffix = os.path.splitext(name)
    for number in range(1, 100_000):
        candidate = os.path.join(parent, f"{stem} ({number}){suffix}")
        key = os.path.normcase(os.path.abspath(candidate))
        if not os.path.lexists(candidate) and key not in reserved:
            return candidate
    raise FileExistsError(f"Could not choose a Keep Both path for {original}")


def create_trash_backend() -> TrashBackend:
    if not sys.platform.startswith("win"):
        raise RestorableBackendUnavailableError(
            "Filesystem changes require Windows and its restorable Recycle Bin backend."
        )
    try:
        from .windows_recycle import WindowsRecycleBackend, windows_backend_available

        if not windows_backend_available():
            raise RestorableBackendUnavailableError(
                "The Windows Recycle Bin backend is unavailable. Install pywin32 and restart SubApp."
            )
        return WindowsRecycleBackend()
    except RestorableBackendUnavailableError:
        raise
    except Exception as exc:
        raise RestorableBackendUnavailableError(
            f"The Windows Recycle Bin backend failed its safety check: {exc}"
        ) from exc
