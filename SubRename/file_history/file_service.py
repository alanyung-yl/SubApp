from __future__ import annotations

from contextlib import contextmanager
import os
import shutil
from typing import Any, Iterator

from .models import FileFingerprint, RecycleToken, SourceRowSnapshot, json_dumps
from .store import HistoryStore
from .trash_base import (
    ManualRecoveryRequiredError,
    RecycleRecoveryRequiredError,
    TrashBackend,
    UnsafeFilesystemMutationError,
    choose_restore_destination,
    filesystem_root,
)


def _manual_recovery(
    store: HistoryStore,
    command_id: str,
    path: str,
    message: str,
) -> None:
    try:
        store.mark_manual_recovery(command_id, message)
    except Exception:
        pass
    raise ManualRecoveryRequiredError(message)


def recycle_and_record(
    store: HistoryStore,
    backend: TrashBackend,
    *,
    command_id: str,
    role: str,
    path: str,
) -> RecycleToken:
    """Recycle one item, or restore it immediately if its exact token cannot be stored."""
    normalized = os.path.abspath(path)
    try:
        token = backend.recycle(normalized)
    except RecycleRecoveryRequiredError as exc:
        message = (
            f"Manual recovery is required for {normalized}. Windows moved the item but "
            f"SubApp could not restore it ({exc.rollback_error or exc}). The item should "
            "remain in the Recycle Bin; stop filesystem work and restore it manually."
        )
        _manual_recovery(store, command_id, normalized, message)

    failure: Exception | None = None
    if not token.restorable or not token.recycle_identity_pidl:
        failure = RuntimeError("the backend returned no exact restorable Recycle Bin identity")
    else:
        try:
            store.add_token(command_id, role, token)
            return token
        except Exception as exc:
            failure = exc

    restore_path = choose_restore_destination(normalized)
    try:
        restored = backend.restore(token, restore_path)
    except Exception as exc:
        restored = None
        rollback_error = str(exc)
    else:
        rollback_error = restored.error if restored else "backend did not return a result"
    if restored and restored.success and restored.actual_path:
        raise UnsafeFilesystemMutationError(
            f"Recycling {normalized} was rolled back because its exact history token could "
            f"not be persisted: {failure}. The item was restored to {restored.actual_path}."
        ) from failure
    message = (
        f"Manual recovery is required for {normalized}. Its exact history token could not "
        f"be persisted ({failure}) and automatic restoration failed ({rollback_error}). "
        "The item remains in the Recycle Bin; stop filesystem work and restore it manually."
    )
    _manual_recovery(store, command_id, normalized, message)
    raise AssertionError("unreachable")


class FileMutationService:
    """The only public path for job-bound user-file changes."""

    def __init__(
        self,
        store: HistoryStore,
        backend: TrashBackend,
        job_id: str,
        approved_roots: frozenset[str],
    ):
        self.store = store
        self.backend = backend
        self.job_id = job_id
        self.approved_roots = approved_roots

    def _assert_ready(self, *paths: str) -> None:
        job = self.store.job(self.job_id)
        if job is None or job["state"] != "STARTED":
            raise UnsafeFilesystemMutationError(
                "Filesystem changes require an active STARTED history job."
            )
        if not self.backend.supports_restore:
            raise UnsafeFilesystemMutationError(
                "Filesystem changes require an exact restorable backend."
            )
        for path in paths:
            if filesystem_root(path) not in self.approved_roots:
                raise UnsafeFilesystemMutationError(
                    f"Filesystem path was not approved by this job's preflight: {path}"
                )

    def assert_job_active(self) -> None:
        self._assert_ready()

    def _recycle(self, command_id: str, role: str, path: str) -> RecycleToken:
        return recycle_and_record(
            self.store,
            self.backend,
            command_id=command_id,
            role=role,
            path=path,
        )

    def _fail(self, command_id: str, error: Exception | str) -> None:
        self.store.update_command(command_id, state="FAILED", error_message=str(error))

    @contextmanager
    def _staged_copy(
        self, source: str, destination: str, command_id: str
    ) -> Iterator[tuple[str, FileFingerprint]]:
        stage = os.path.join(
            os.path.dirname(destination),
            f".{os.path.basename(destination)}.subapp-stage-{self.job_id}-{command_id}",
        )
        try:
            shutil.copy2(source, stage)
            yield stage, FileFingerprint.capture(stage)
        finally:
            if os.path.exists(stage):
                try:
                    os.unlink(stage)
                except OSError:
                    pass

    def copy_output(self, *, source_path: str, destination_path: str) -> str:
        self._assert_ready(destination_path)
        if os.path.lexists(destination_path):
            return self._overwrite_output(source_path, destination_path)
        return self._copy_output(source_path, destination_path)

    def _copy_output(self, source_path: str, destination_path: str) -> str:
        command_id = self.store.add_command(
            self.job_id,
            "COPY_OUTPUT",
            source_path=source_path,
            destination_path=destination_path,
            active_path=destination_path,
            command_data_json=json_dumps({"generated_path": destination_path}),
        )
        try:
            with self._staged_copy(source_path, destination_path, command_id) as (stage, fingerprint):
                # Windows os.rename is non-replacing, so a file created during
                # the copy is preserved.
                os.rename(stage, destination_path)
            self.store.update_command(
                command_id,
                state="APPLIED",
                active_path=destination_path,
                after_fingerprint=fingerprint.to_json(),
            )
            return destination_path
        except Exception as exc:
            self._fail(command_id, exc)
            raise

    def overwrite_output(self, *, source_path: str, destination_path: str) -> str:
        self._assert_ready(destination_path)
        if not os.path.lexists(destination_path):
            return self._copy_output(source_path, destination_path)
        return self._overwrite_output(source_path, destination_path)

    def _overwrite_output(self, source_path: str, destination_path: str) -> str:
        before = FileFingerprint.capture(destination_path)
        command_id = self.store.add_command(
            self.job_id,
            "OVERWRITE_OUTPUT",
            source_path=source_path,
            destination_path=destination_path,
            active_path=destination_path,
            before_fingerprint=before.to_json(),
            command_data_json=json_dumps({
                "generated_path": destination_path,
                "old_destination_path": destination_path,
            }),
        )
        try:
            with self._staged_copy(source_path, destination_path, command_id) as (stage, installed):
                if not before.matches(destination_path):
                    raise UnsafeFilesystemMutationError(
                        f"Refusing to overwrite a file that changed during the operation: {destination_path}"
                    )
                old_token = self._recycle(command_id, "OLD_DESTINATION", destination_path)
                try:
                    os.rename(stage, destination_path)
                except Exception as install_error:
                    restore_path = choose_restore_destination(destination_path)
                    restored = self.backend.restore(old_token, restore_path)
                    if restored.success and restored.actual_path:
                        self.store.update_token(old_token.token_id, state="RESTORED")
                        raise UnsafeFilesystemMutationError(
                            f"The new file could not be installed at {destination_path}; the old file "
                            f"was preserved at {restored.actual_path}: {install_error}"
                        ) from install_error
                    _manual_recovery(
                        self.store,
                        command_id,
                        destination_path,
                        f"Manual recovery is required for {destination_path}. Installing the new file "
                        f"failed ({install_error}) and restoring the old file failed ({restored.error}). "
                        "The old file remains in the Recycle Bin.",
                    )
            self.store.update_command(
                command_id,
                state="APPLIED",
                active_path=destination_path,
                after_fingerprint=installed.to_json(),
            )
            return destination_path
        except ManualRecoveryRequiredError:
            raise
        except Exception as exc:
            self._fail(command_id, exc)
            raise

    def replace_original(
        self,
        *,
        source_path: str,
        destination_path: str,
        row_snapshot: SourceRowSnapshot | None = None,
    ) -> str:
        self._assert_ready(source_path, destination_path)
        before = FileFingerprint.capture(source_path)
        data = {
            "generated_path": destination_path,
            "original_active_path": source_path,
            "restore_source_row": True,
            "original_recycled": False,
        }
        command_id = self.store.add_command(
            self.job_id,
            "REPLACE_ORIGINAL",
            source_path=source_path,
            destination_path=destination_path,
            active_path=destination_path,
            before_fingerprint=before.to_json(),
            ui_snapshot_json=row_snapshot.to_json() if row_snapshot else None,
            command_data_json=json_dumps(data),
        )
        installed = False
        try:
            with self._staged_copy(source_path, destination_path, command_id) as (stage, fingerprint):
                os.rename(stage, destination_path)
            installed = True
            if not before.matches(source_path):
                self.store.update_command(
                    command_id,
                    state="APPLIED",
                    after_fingerprint=fingerprint.to_json(),
                    command_data_json=json_dumps(data),
                    error_message="Original changed before it could be recycled",
                )
                raise UnsafeFilesystemMutationError(
                    f"The original changed during Replace Original and was preserved: {source_path}. "
                    "The generated output remains tracked and can be undone."
                )
            try:
                self._recycle(command_id, "ORIGINAL_SOURCE", source_path)
                data["original_recycled"] = True
            except ManualRecoveryRequiredError:
                raise
            except Exception as exc:
                self.store.update_command(
                    command_id,
                    state="APPLIED",
                    after_fingerprint=fingerprint.to_json(),
                    command_data_json=json_dumps(data),
                    error_message=str(exc),
                )
                raise
            self.store.update_command(
                command_id,
                state="APPLIED",
                active_path=destination_path,
                after_fingerprint=fingerprint.to_json(),
                command_data_json=json_dumps(data),
            )
            return destination_path
        except (ManualRecoveryRequiredError, UnsafeFilesystemMutationError):
            raise
        except Exception as exc:
            if not installed:
                self._fail(command_id, exc)
            raise

    def recycle_file(
        self,
        path: str,
        *,
        row_snapshot: SourceRowSnapshot | None = None,
        command_type: str = "RECYCLE_FILE",
    ) -> str:
        self._assert_ready(path)
        before = FileFingerprint.capture(path) if os.path.isfile(path) else None
        command_id = self.store.add_command(
            self.job_id,
            command_type,
            source_path=path,
            destination_path=path,
            active_path=path,
            before_fingerprint=before.to_json() if before else None,
            ui_snapshot_json=row_snapshot.to_json() if row_snapshot else None,
            command_data_json=json_dumps({"restore_source_row": row_snapshot is not None}),
        )
        try:
            if before is not None and not before.matches(path):
                raise UnsafeFilesystemMutationError(f"File changed before recycling: {path}")
            self._recycle(command_id, "RECYCLED_ITEM", path)
            self.store.update_command(command_id, state="APPLIED")
            return path
        except ManualRecoveryRequiredError:
            raise
        except Exception as exc:
            self._fail(command_id, exc)
            raise

    def recycle_empty_folder(self, path: str) -> str:
        if not os.path.isdir(path) or os.listdir(path):
            raise OSError(f"Folder is not empty: {path}")
        return self.recycle_file(path, command_type="RECYCLE_EMPTY_FOLDER")
