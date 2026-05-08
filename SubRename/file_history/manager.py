from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Iterable

from .file_service import FileMutationService, recycle_and_record
from .models import (
    FileFingerprint,
    HistorySummary,
    OperationResult,
    SourceRowSnapshot,
    json_dumps,
    json_loads,
    operation_label,
)
from .store import HistoryStore
from .trash_base import (
    RestorableBackendUnavailableError,
    TrashBackend,
    UnsafeFilesystemMutationError,
    choose_restore_destination,
)


@dataclass
class HistoryJob:
    manager: "OperationHistoryManager"
    job_id: str
    mutations: FileMutationService
    _finished: bool = False

    def complete(self) -> None:
        if not self._finished:
            self.manager.complete_job(self.job_id)
            self._finished = True

    def abandon_if_empty(self) -> None:
        if not self._finished:
            self.manager.store.abandon_empty_job(self.job_id)
            self._finished = True


class OperationHistoryManager:
    def __init__(self, store: HistoryStore, backend: TrashBackend):
        self.store = store
        self.backend = backend
        self._ensure_backend_ready()
        self.accepting_jobs = True
        self.store.recover_interrupted()
        self.store.maintenance()

    @property
    def pending_recovery_records(self) -> list[dict[str, Any]]:
        return self.store.manual_recovery_records()

    def acknowledge_manual_recovery(self) -> int:
        return self.store.acknowledge_manual_recovery()

    @property
    def supports_undo(self) -> bool:
        return bool(self.backend.supports_restore)

    def _ensure_backend_ready(self) -> None:
        if not self.backend.supports_restore:
            raise RestorableBackendUnavailableError(
                "Refusing filesystem access because the configured backend cannot restore exact items."
            )
        if getattr(self.backend, "startup_checked", False):
            return
        try:
            self.backend.ensure_available()
        except RestorableBackendUnavailableError:
            raise
        except Exception as exc:
            raise RestorableBackendUnavailableError(
                f"The restorable filesystem backend is not working: {exc}"
            ) from exc

    def _preflight_job(self, paths: Iterable[str]) -> frozenset[str]:
        try:
            return self.backend.preflight_job(paths)
        except RestorableBackendUnavailableError:
            raise
        except Exception as exc:
            raise RestorableBackendUnavailableError(
                f"The restorable filesystem job preflight failed: {exc}"
            ) from exc

    def begin_job(
        self,
        *,
        operation_type: str,
        description: str = "",
        paths: Iterable[str] = (),
    ) -> HistoryJob:
        if not self.accepting_jobs:
            raise RuntimeError("SubApp is shutting down and is not accepting filesystem jobs")
        capability = self._preflight_job(paths)
        label = operation_label(operation_type, description or "File Operation")
        try:
            job_id = self.store.begin_job(operation_type, label)
        except RuntimeError as exc:
            if "manual-recovery" not in str(exc):
                raise
            raise UnsafeFilesystemMutationError(str(exc)) from exc
        return HistoryJob(
            manager=self,
            job_id=job_id,
            mutations=FileMutationService(self.store, self.backend, job_id, capability),
        )

    def complete_job(self, job_id: str) -> None:
        row = self.store.complete_job(job_id)
        if row and not row["item_count"]:
            self.store.abandon_empty_job(job_id)
        self.store.maintenance()

    def undo_summary(self) -> HistorySummary | None:
        return self.store.undo_summary() if self.supports_undo else None

    def redo_summary(self) -> HistorySummary | None:
        return self.store.redo_summary() if self.supports_undo else None

    @staticmethod
    def _remap_path(path: str, folder_map: dict[str, str]) -> str:
        normalized = os.path.normcase(os.path.abspath(path))
        for original, actual in sorted(folder_map.items(), key=lambda item: len(item[0]), reverse=True):
            original_abs = os.path.abspath(original)
            original_key = os.path.normcase(original_abs)
            if normalized == original_key:
                return actual
            if normalized.startswith(original_key + os.sep):
                return os.path.join(actual, os.path.relpath(os.path.abspath(path), original_abs))
        return path

    def _restore_token(
        self,
        command_id: str,
        role: str,
        requested_path: str,
        reserved: set[str],
        result: OperationResult,
    ) -> str | None:
        token = self.store.latest_token(command_id, role)
        if token is None or not token.restorable:
            result.missing += 1
            result.details.append(f"Missing exact {role} Recycle Bin token: {requested_path}")
            return None
        destination = choose_restore_destination(requested_path, reserved)
        restored = self.backend.restore(token, destination)
        if not restored.success or not restored.actual_path:
            is_missing = bool(restored.error and "missing" in restored.error.lower())
            self.store.update_token(
                token.token_id,
                state="MISSING" if is_missing else "RESTORE_FAILED",
                error_message=restored.error,
            )
            if is_missing:
                result.missing += 1
                result.details.append(f"Missing from Recycle Bin: {requested_path}")
            else:
                result.failed += 1
                result.details.append(f"Restore failed: {requested_path}: {restored.error}")
            return None
        actual = os.path.abspath(restored.actual_path)
        self.store.update_token(token.token_id, state="RESTORED")
        reserved.add(actual)
        if os.path.normcase(actual) != os.path.normcase(os.path.abspath(token.original_path)):
            result.kept_both += 1
            result.details.append(f"Kept both: {token.original_path} -> {actual}")
        return actual

    def _required_token_exists(
        self,
        command_id: str,
        role: str,
        requested_path: str,
        result: OperationResult,
    ) -> bool:
        token = self.store.latest_token(command_id, role)
        if token is not None and token.restorable and self.backend.token_exists(token):
            return True
        if token is not None:
            self.store.update_token(
                token.token_id,
                state="MISSING",
                error_message="Exact Recycle Bin item is missing",
            )
        message = f"Missing required old item from Recycle Bin: {requested_path}"
        self.store.update_command(command_id, error_message=message)
        result.missing += 1
        result.details.append(message)
        return False

    def _recycle_active(
        self,
        command_id: str,
        role: str,
        path: str,
        result: OperationResult,
        *,
        fingerprint: FileFingerprint | None = None,
        require_empty_folder: bool = False,
    ) -> bool:
        if not os.path.exists(path):
            self.store.update_command(command_id, state="MISSING", error_message="Expected path is missing")
            result.missing += 1
            result.details.append(f"Expected path is missing: {path}")
            return False
        modified = fingerprint is not None and not fingerprint.matches(path)
        modified = modified or (
            require_empty_folder and (not os.path.isdir(path) or bool(os.listdir(path)))
        )
        if modified:
            self.store.update_command(
                command_id,
                state="SKIPPED_MODIFIED",
                error_message="Filesystem content changed after the recorded operation",
            )
            result.details.append(f"Skipped modified file: {path}")
            return False
        try:
            recycle_and_record(
                self.store,
                self.backend,
                command_id=command_id,
                role=role,
                path=path,
            )
            return True
        except UnsafeFilesystemMutationError:
            raise
        except Exception as exc:
            self.store.update_command(command_id, state="FAILED", error_message=str(exc))
            result.failed += 1
            result.details.append(f"Recycle failed: {path}: {exc}")
            return False

    @staticmethod
    def _new_result(action: str, summary: HistorySummary, commands: list[Any]) -> OperationResult:
        eligible_states = {"APPLIED", "REDONE"} if action == "UNDO" else {"UNDONE"}
        return OperationResult(
            action=action,
            job_id=summary.job_id,
            description=summary.description,
            state=f"{action}ING",
            operation_type=summary.operation_type,
            original_total=len(commands),
            eligible_count=sum(command["state"] in eligible_states for command in commands),
        )

    def _finish_result(self, result: OperationResult) -> OperationResult:
        result.state = self.store.finish_transition(
            result.job_id,
            result.action,
            transitioned_count=result.succeeded,
        )
        counts = self.store.command_state_counts(result.job_id)
        result.modified_preserved = counts.get("SKIPPED_MODIFIED", 0)
        return result

    def _record_active_path(
        self,
        command_id: str,
        state: str,
        actual: str,
        requested: str,
        data: dict[str, Any] | None = None,
        *,
        fingerprint: bool = False,
    ) -> None:
        fields: dict[str, Any] = {
            "state": state,
            "active_path": actual,
            "alternate_path": (
                actual if os.path.normcase(actual) != os.path.normcase(requested) else None
            ),
        }
        if data is not None:
            fields["command_data_json"] = json_dumps(data)
        if fingerprint:
            fields["after_fingerprint"] = FileFingerprint.capture(actual).to_json()
        self.store.update_command(command_id, **fields)

    def undo(self, cancel_event: Any | None = None) -> OperationResult:
        # Retained for caller compatibility; active history transitions ignore shutdown cancellation.
        summary = self.undo_summary()
        if summary is None:
            return OperationResult("UNDO", None, "", "UNAVAILABLE")
        commands = self.store.commands(summary.job_id, reverse=True)
        self._preflight_job(self._paths_for_commands(commands))
        result = self._new_result("UNDO", summary, commands)
        folder_map: dict[str, str] = {}
        reserved: set[str] = set()
        for command in commands:
            if command["state"] not in {"APPLIED", "REDONE"}:
                continue
            command_id = command["command_id"]
            command_type = command["command_type"]
            data = json_loads(command["command_data_json"], {}) or {}
            snapshot = SourceRowSnapshot.from_json(command["ui_snapshot_json"])
            try:
                if command_type == "COPY_OUTPUT":
                    active = command["active_path"] or command["destination_path"]
                    fingerprint = FileFingerprint.from_json(command["after_fingerprint"])
                    if not self._recycle_active(
                        command_id, "GENERATED_OUTPUT", active, result, fingerprint=fingerprint
                    ):
                        continue
                    self.store.update_command(command_id, state="UNDONE")

                elif command_type == "OVERWRITE_OUTPUT":
                    requested = self._remap_path(
                        data.get("old_destination_path") or command["destination_path"], folder_map
                    )
                    if not self._required_token_exists(
                        command_id, "OLD_DESTINATION", requested, result
                    ):
                        continue
                    generated = command["active_path"] or data.get("generated_path") or command["destination_path"]
                    fingerprint = FileFingerprint.from_json(command["after_fingerprint"])
                    if not self._recycle_active(
                        command_id, "GENERATED_OUTPUT", generated, result, fingerprint=fingerprint
                    ):
                        continue
                    old_actual = self._restore_token(
                        command_id, "OLD_DESTINATION", requested, reserved, result
                    )
                    if old_actual is None:
                        self.store.update_command(command_id, state="MISSING")
                        continue
                    data["old_active_path"] = old_actual
                    self._record_active_path(
                        command_id, "UNDONE", old_actual, requested, data
                    )

                elif command_type == "REPLACE_ORIGINAL":
                    original = self._remap_path(command["source_path"], folder_map)
                    original_recycled = data.get("original_recycled", True)
                    if original_recycled:
                        if not self._required_token_exists(
                            command_id, "ORIGINAL_SOURCE", original, result
                        ):
                            continue
                    elif not os.path.exists(original):
                        message = f"Missing required original file: {original}"
                        self.store.update_command(command_id, error_message=message)
                        result.missing += 1
                        result.details.append(message)
                        continue
                    generated = command["active_path"] or data.get("generated_path") or command["destination_path"]
                    fingerprint = FileFingerprint.from_json(command["after_fingerprint"])
                    if not self._recycle_active(
                        command_id, "GENERATED_OUTPUT", generated, result, fingerprint=fingerprint
                    ):
                        continue
                    original_actual = None
                    if original_recycled:
                        original_actual = self._restore_token(
                            command_id, "ORIGINAL_SOURCE", original, reserved, result
                        )
                    elif os.path.exists(original):
                        original_actual = original
                    if original_actual is None:
                        self.store.update_command(command_id, state="MISSING")
                        continue
                    data["original_active_path"] = original_actual
                    self._record_active_path(
                        command_id, "UNDONE", original_actual, original, data
                    )
                    if snapshot:
                        result.ui_events.append({"type": "REMOVE_SOURCE_ROW", "path": generated})
                        result.ui_events.append({
                            "type": "RESTORE_SOURCE_ROW",
                            "path": original_actual,
                            "snapshot": json_loads(snapshot.to_json(), {}),
                        })

                elif command_type in {"RECYCLE_FILE", "RECYCLE_EMPTY_FOLDER"}:
                    requested = self._remap_path(
                        command["destination_path"] or command["source_path"], folder_map
                    )
                    actual = self._restore_token(
                        command_id, "RECYCLED_ITEM", requested, reserved, result
                    )
                    if actual is None:
                        self.store.update_command(command_id, state="MISSING")
                        continue
                    self._record_active_path(command_id, "UNDONE", actual, requested)
                    if command_type == "RECYCLE_EMPTY_FOLDER":
                        folder_map[command["source_path"]] = actual
                    elif snapshot:
                        result.ui_events.append({
                            "type": "RESTORE_SOURCE_ROW",
                            "path": actual,
                            "snapshot": json_loads(snapshot.to_json(), {}),
                        })
                else:
                    raise RuntimeError(f"Unknown history command type: {command_type}")
                result.succeeded += 1
            except UnsafeFilesystemMutationError:
                raise
            except Exception as exc:
                result.failed += 1
                result.details.append(
                    f"Undo failed for {command['active_path'] or command['source_path']}: {exc}"
                )
                self.store.update_command(command_id, state="FAILED", error_message=str(exc))
        return self._finish_result(result)

    def redo(self, cancel_event: Any | None = None) -> OperationResult:
        # Retained for caller compatibility; active history transitions ignore shutdown cancellation.
        summary = self.redo_summary()
        if summary is None:
            return OperationResult("REDO", None, "", "UNAVAILABLE")
        commands = self.store.commands(summary.job_id)
        self._preflight_job(self._paths_for_commands(commands))
        result = self._new_result("REDO", summary, commands)
        reserved: set[str] = set()
        for command in commands:
            if command["state"] != "UNDONE":
                continue
            command_id = command["command_id"]
            command_type = command["command_type"]
            data = json_loads(command["command_data_json"], {}) or {}
            snapshot = SourceRowSnapshot.from_json(command["ui_snapshot_json"])
            try:
                if command_type == "COPY_OUTPUT":
                    requested = command["destination_path"]
                    actual = self._restore_token(
                        command_id, "GENERATED_OUTPUT", requested, reserved, result
                    )
                    if actual is None:
                        self.store.update_command(
                            command_id,
                            error_message=f"Missing generated output from Recycle Bin: {requested}",
                        )
                        continue
                    data["generated_path"] = actual
                    self._record_active_path(
                        command_id, "REDONE", actual, requested, data, fingerprint=True
                    )

                elif command_type == "OVERWRITE_OUTPUT":
                    old_active = data.get("old_active_path") or command["active_path"]
                    before = FileFingerprint.from_json(command["before_fingerprint"])
                    if old_active and os.path.exists(old_active):
                        if not self._recycle_active(
                            command_id,
                            "OLD_DESTINATION",
                            old_active,
                            result,
                            fingerprint=before,
                        ):
                            continue
                    requested = command["destination_path"]
                    actual = self._restore_token(
                        command_id, "GENERATED_OUTPUT", requested, reserved, result
                    )
                    if actual is None:
                        rollback = self._restore_token(
                            command_id, "OLD_DESTINATION", old_active, reserved, result
                        ) if old_active else None
                        data["old_active_path"] = rollback or old_active
                        self.store.update_command(
                            command_id,
                            state="UNDONE" if rollback else "MISSING",
                            active_path=rollback,
                            command_data_json=json_dumps(data),
                        )
                        continue
                    data["generated_path"] = actual
                    self._record_active_path(
                        command_id, "REDONE", actual, requested, data, fingerprint=True
                    )

                elif command_type == "REPLACE_ORIGINAL":
                    original_active = data.get("original_active_path") or command["source_path"]
                    before = FileFingerprint.from_json(command["before_fingerprint"])
                    if original_active and os.path.exists(original_active):
                        if not self._recycle_active(
                            command_id,
                            "ORIGINAL_SOURCE",
                            original_active,
                            result,
                            fingerprint=before,
                        ):
                            continue
                    requested = command["destination_path"]
                    actual = self._restore_token(
                        command_id, "GENERATED_OUTPUT", requested, reserved, result
                    )
                    if actual is None:
                        rollback = self._restore_token(
                            command_id, "ORIGINAL_SOURCE", original_active, reserved, result
                        ) if original_active else None
                        data["original_active_path"] = rollback or original_active
                        self.store.update_command(
                            command_id,
                            state="UNDONE" if rollback else "MISSING",
                            active_path=rollback,
                            command_data_json=json_dumps(data),
                        )
                        continue
                    data["generated_path"] = actual
                    self._record_active_path(
                        command_id, "REDONE", actual, requested, data, fingerprint=True
                    )
                    if snapshot:
                        result.ui_events.append({"type": "REMOVE_SOURCE_ROW", "path": original_active})

                elif command_type in {"RECYCLE_FILE", "RECYCLE_EMPTY_FOLDER"}:
                    active = command["active_path"]
                    before = FileFingerprint.from_json(command["before_fingerprint"])
                    if not active or not self._recycle_active(
                        command_id,
                        "RECYCLED_ITEM",
                        active,
                        result,
                        fingerprint=before,
                        require_empty_folder=command_type == "RECYCLE_EMPTY_FOLDER",
                    ):
                        continue
                    self.store.update_command(command_id, state="REDONE")
                    if command_type == "RECYCLE_FILE" and snapshot:
                        result.ui_events.append({"type": "REMOVE_SOURCE_ROW", "path": active})
                else:
                    raise RuntimeError(f"Unknown history command type: {command_type}")
                result.succeeded += 1
            except UnsafeFilesystemMutationError:
                raise
            except Exception as exc:
                result.failed += 1
                result.details.append(
                    f"Redo failed for {command['active_path'] or command['source_path']}: {exc}"
                )
                self.store.update_command(command_id, state="FAILED", error_message=str(exc))
        return self._finish_result(result)

    def shutdown(self) -> None:
        self.accepting_jobs = False

    @staticmethod
    def _paths_for_commands(commands: Iterable[Any]) -> list[str]:
        paths: list[str] = []
        for command in commands:
            for field in ("source_path", "destination_path", "active_path", "alternate_path"):
                value = command[field]
                if value:
                    paths.append(value)
        return paths
