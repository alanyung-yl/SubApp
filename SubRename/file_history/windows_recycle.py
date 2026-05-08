from __future__ import annotations

from contextlib import contextmanager
import ctypes
import os
import sys
from typing import Any, Iterable, Iterator

from .models import RecycleToken, RestoreResult, new_id
from .trash_base import (
    RecycleRecoveryRequiredError,
    RestorableBackendUnavailableError,
    UnsafeFilesystemMutationError,
    filesystem_root,
)


try:  # Optional on non-Windows and in source-only test environments.
    import pythoncom
    from win32com.server.policy import DesignatedWrapPolicy
    from win32com.shell import shell, shellcon
except ImportError:  # pragma: no cover - exercised through backend selection
    pythoncom = None
    DesignatedWrapPolicy = object
    shell = None
    shellcon = None


# FOF/FOFX values are kept local because older pywin32 builds do not expose all
# post-Vista constants from shellcon.
FOF_SILENT = 0x0004
FOF_RENAMEONCOLLISION = 0x0008
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOCONFIRMMKDIR = 0x0200
FOF_NOERRORUI = 0x0400
FOFX_RECYCLEONDELETE = 0x00080000
FOFX_EARLYFAILURE = 0x00100000
FOFX_PRESERVEFILEEXTENSIONS = 0x00200000
FOFX_ADDUNDORECORD = 0x20000000

S_OK = 0
DRIVE_FIXED = 3


def windows_backend_available() -> bool:
    return bool(
        sys.platform.startswith("win")
        and pythoncom is not None
        and shell is not None
        and hasattr(shell, "IID_IFileOperation")
        and hasattr(shell, "IID_IFileOperationProgressSink")
        and hasattr(shell, "SHGetIDListFromObject")
    )


@contextmanager
def _sta_apartment() -> Iterator[None]:
    if pythoncom is None:
        raise RuntimeError("pywin32 is required for Windows Recycle Bin history")
    initialized = False
    try:
        pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
        initialized = True
        yield
    finally:
        if initialized:
            pythoncom.CoUninitialize()


def _as_shell_item(value: Any) -> Any:
    if value is None:
        return None
    try:
        return value.QueryInterface(shell.IID_IShellItem)
    except AttributeError:
        return value


def _pidl_list_bytes(pidl: Any) -> bytes:
    raw = shell.PIDLAsString(pidl)
    return raw if isinstance(raw, bytes) else raw.encode("latin-1")


def _parsing_name(item: Any) -> str | None:
    if item is None:
        return None
    item = _as_shell_item(item)
    for flag_name in ("SIGDN_DESKTOPABSOLUTEPARSING", "SIGDN_FILESYSPATH"):
        try:
            return item.GetDisplayName(getattr(shellcon, flag_name))
        except Exception:
            continue
    return None


def _iter_recycle_items() -> Iterator[tuple[bytes, Any, str | None]]:
    """Yield stable virtual Recycle Bin identities inside the current STA."""
    bin_pidl = shell.SHGetFolderLocation(0, shellcon.CSIDL_BITBUCKET, 0, 0)
    desktop = shell.SHGetDesktopFolder()
    recycle_folder = desktop.BindToObject(bin_pidl, None, shell.IID_IShellFolder)
    flags = (
        shellcon.SHCONTF_FOLDERS
        | shellcon.SHCONTF_NONFOLDERS
        | shellcon.SHCONTF_INCLUDEHIDDEN
    )
    for child_pidl in recycle_folder.EnumObjects(None, flags):
        absolute_pidl = list(bin_pidl) + list(child_pidl)
        item = shell.SHCreateItemFromIDList(absolute_pidl, shell.IID_IShellItem)
        yield _pidl_list_bytes(absolute_pidl), item, _parsing_name(item)


def _same_parsing_name(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return False
    return os.path.normcase(os.path.normpath(left)) == os.path.normcase(os.path.normpath(right))


class _ProgressSink(DesignatedWrapPolicy):
    _com_interfaces_ = [shell.IID_IFileOperationProgressSink] if shell is not None else []
    _public_methods_ = [
        "StartOperations", "FinishOperations", "PreRenameItem", "PostRenameItem",
        "PreMoveItem", "PostMoveItem", "PreCopyItem", "PostCopyItem",
        "PreDeleteItem", "PostDeleteItem", "PreNewItem", "PostNewItem",
        "UpdateProgress", "ResetTimer", "PauseTimer", "ResumeTimer",
    ]

    def __init__(self) -> None:
        # IFileOperationProgressSink is a custom (non-IDispatch) COM
        # interface. DesignatedWrapPolicy registers the Python methods with
        # pywin32's shell gateway before pythoncom.WrapObject requests it.
        self._wrap_(self)
        self.delete_hresult: int | None = None
        self.deleted_item: Any = None
        self.move_hresult: int | None = None
        self.moved_item: Any = None

    def StartOperations(self): return S_OK
    def FinishOperations(self, result): return S_OK
    def PreRenameItem(self, flags, item, new_name): return S_OK
    def PostRenameItem(self, flags, item, new_name, result, created): return S_OK
    def PreMoveItem(self, flags, item, destination, new_name): return S_OK

    def PostMoveItem(self, flags, item, destination, new_name, result, created):
        self.move_hresult = result
        self.moved_item = created
        return S_OK

    def PreCopyItem(self, flags, item, destination, new_name): return S_OK
    def PostCopyItem(self, flags, item, destination, new_name, result, created): return S_OK
    def PreDeleteItem(self, flags, item): return S_OK

    def PostDeleteItem(self, flags, item, result, created):
        self.delete_hresult = result
        self.deleted_item = created
        return S_OK

    def PreNewItem(self, flags, destination, new_name): return S_OK
    def PostNewItem(self, flags, destination, new_name, template, attributes, result, created): return S_OK
    def UpdateProgress(self, total, completed): return S_OK
    def ResetTimer(self): return S_OK
    def PauseTimer(self): return S_OK
    def ResumeTimer(self): return S_OK


def _new_operation(flags: int) -> tuple[Any, _ProgressSink, Any, int]:
    operation = pythoncom.CoCreateInstance(
        shell.CLSID_FileOperation,
        None,
        pythoncom.CLSCTX_INPROC_SERVER,
        shell.IID_IFileOperation,
    )
    operation.SetOperationFlags(flags)
    sink = _ProgressSink()
    wrapped = pythoncom.WrapObject(sink, shell.IID_IFileOperationProgressSink)
    cookie = operation.Advise(wrapped)
    return operation, sink, wrapped, cookie


def _operation_failed(result: int | None) -> bool:
    return result is None or bool(int(result) & 0x80000000)


class WindowsRecycleBackend:
    """Exact Windows Recycle Bin backend implemented with IFileOperation."""

    supports_restore = True

    def __init__(self) -> None:
        if not windows_backend_available():
            raise RestorableBackendUnavailableError(
                "Windows Recycle Bin history is unavailable; install pywin32"
            )
        self.startup_checked = False
        self.ensure_available()

    @staticmethod
    def _volume_root(path: str) -> str:
        normalized = os.path.abspath(os.path.normpath(path))
        drive, _ = os.path.splitdrive(normalized)
        if not drive or drive.startswith("\\\\"):
            raise RestorableBackendUnavailableError(
                f"Windows Recycle Bin restoration is not guaranteed for this path: {normalized}"
            )
        root = drive + os.sep
        if ctypes.windll.kernel32.GetDriveTypeW(root) != DRIVE_FIXED:
            raise RestorableBackendUnavailableError(
                f"Only fixed local volumes are allowed for undoable filesystem operations: {root}"
            )
        return root

    @staticmethod
    def _query_recycle_bin(root: str) -> None:
        try:
            shell.SHQueryRecycleBin(root)
        except Exception as exc:
            raise RestorableBackendUnavailableError(
                f"The Windows Recycle Bin is not accessible for {root}: {exc}"
            ) from exc

    def ensure_available(self) -> None:
        root = self._volume_root(os.environ.get("SystemDrive", "C:") + os.sep)
        with _sta_apartment():
            self._query_recycle_bin(root)
            operation, _, wrapped, cookie = _new_operation(
                FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOFX_EARLYFAILURE
            )
            operation.Unadvise(cookie)
        self.startup_checked = True

    def ensure_restorable_path(self, path: str) -> None:
        self._volume_root(path)

    def preflight_job(self, paths: Iterable[str]) -> frozenset[str]:
        path_list = [os.fspath(path) for path in paths]
        roots = {self._volume_root(path) for path in path_list}
        if not roots:
            roots.add(self._volume_root(os.environ.get("SystemDrive", "C:") + os.sep))
        with _sta_apartment():
            for root in sorted(roots, key=os.path.normcase):
                self._query_recycle_bin(root)
        return frozenset(filesystem_root(root) for root in roots)

    @staticmethod
    def _recovery_token(
        normalized: str,
        *,
        identity: bytes | None = None,
    ) -> RecycleToken:
        return RecycleToken(
            token_id=new_id(),
            original_path=normalized,
            recycle_identity_pidl=identity,
            restorable=bool(identity),
        )

    @staticmethod
    def _move_item_in_apartment(
        recycled_item: Any,
        token: RecycleToken,
        requested: str,
    ) -> RestoreResult:
        if recycled_item is None:
            return RestoreResult(False, requested, error="Windows did not return the recycled Shell item")
        if os.path.lexists(requested):
            return RestoreResult(False, requested, error="Requested restore path is occupied")
        parent = os.path.dirname(requested)
        if not os.path.isdir(parent):
            return RestoreResult(False, requested, error=f"Restore parent does not exist: {parent}")
        destination = shell.SHCreateItemFromParsingName(parent, None, shell.IID_IShellItem)
        operation, sink, wrapped, cookie = _new_operation(
            FOF_SILENT | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_NOCONFIRMMKDIR
            | FOF_RENAMEONCOLLISION | FOFX_PRESERVEFILEEXTENSIONS | FOFX_EARLYFAILURE
        )
        try:
            operation.MoveItem(recycled_item, destination, os.path.basename(requested), None)
            operation.PerformOperations()
            aborted = operation.GetAnyOperationsAborted()
        finally:
            operation.Unadvise(cookie)
        if aborted or _operation_failed(sink.move_hresult):
            return RestoreResult(
                False,
                requested,
                error=f"Windows Shell restore failed (HRESULT={sink.move_hresult!r})",
            )
        actual = _parsing_name(sink.moved_item) or requested
        return RestoreResult(
            True,
            requested,
            actual,
        )

    def _raise_after_changed_recycle(
        self,
        *,
        normalized: str,
        problem: str,
        deleted_item: Any,
        identity: bytes | None,
    ) -> None:
        if os.path.lexists(normalized):
            raise OSError(problem)
        token = self._recovery_token(
            normalized,
            identity=identity,
        )
        try:
            restored = self._move_item_in_apartment(deleted_item, token, normalized)
        except Exception as exc:
            restored = RestoreResult(False, normalized, error=str(exc))
        if restored.success:
            raise UnsafeFilesystemMutationError(
                f"{problem}. Windows moved the item, but SubApp restored it to its original path."
            )
        raise RecycleRecoveryRequiredError(
            problem,
            token=token,
            rollback_error=restored.error,
        )

    def recycle(self, path: str) -> RecycleToken:
        normalized = os.path.abspath(os.path.normpath(path))
        self.ensure_restorable_path(normalized)
        if not os.path.exists(normalized):
            raise FileNotFoundError(normalized)
        with _sta_apartment():
            item = shell.SHCreateItemFromParsingName(normalized, None, shell.IID_IShellItem)
            operation, sink, wrapped, cookie = _new_operation(
                FOF_SILENT | FOF_NOCONFIRMATION | FOF_ALLOWUNDO | FOF_NOERRORUI
                | FOFX_RECYCLEONDELETE | FOFX_EARLYFAILURE | FOFX_ADDUNDORECORD
            )
            try:
                operation.DeleteItem(item, None)
                operation.PerformOperations()
                aborted = operation.GetAnyOperationsAborted()
            finally:
                operation.Unadvise(cookie)
            if aborted or _operation_failed(sink.delete_hresult):
                self._raise_after_changed_recycle(
                    normalized=normalized,
                    problem=(
                        f"Windows Shell failed to recycle {normalized} "
                        f"(HRESULT={sink.delete_hresult!r})"
                    ),
                    deleted_item=sink.deleted_item,
                    identity=None,
                )
            if sink.deleted_item is None:
                self._raise_after_changed_recycle(
                    normalized=normalized,
                    problem=(
                        f"Windows changed {normalized}, but returned no Recycle Bin item for recovery"
                    ),
                    deleted_item=None,
                    identity=None,
                )
            parsing_name = _parsing_name(sink.deleted_item)
            # PostDeleteItem can expose the physical $R item. Its filesystem
            # PIDL is not the same identity returned by the virtual Recycle Bin
            # folder, so rebind it immediately by its Shell-created parsing
            # name and persist that virtual PIDL instead.
            rebound = [
                (identity, item)
                for identity, item, candidate_name in _iter_recycle_items()
                if _same_parsing_name(candidate_name, parsing_name)
            ]
            if len(rebound) != 1:
                self._raise_after_changed_recycle(
                    normalized=normalized,
                    problem=(
                        f"Windows recycled {normalized}, but its exact Recycle Bin identity "
                        f"could not be rebound ({len(rebound)} candidates)"
                    ),
                    deleted_item=sink.deleted_item,
                    identity=None,
                )
            identity, _ = rebound[0]

        token = RecycleToken(
            token_id=new_id(),
            original_path=normalized,
            recycle_identity_pidl=identity,
            restorable=True,
        )
        return token

    def _find_recycle_item(self, token: RecycleToken) -> Any | None:
        if not token.recycle_identity_pidl:
            return None
        for identity, item, _ in _iter_recycle_items():
            if identity == bytes(token.recycle_identity_pidl):
                return item
        return None

    def restore(self, token: RecycleToken, requested_path: str) -> RestoreResult:
        requested = os.path.abspath(os.path.normpath(requested_path))
        self.ensure_restorable_path(requested)
        if os.path.lexists(requested):
            return RestoreResult(False, requested, error="Requested restore path is occupied")
        parent = os.path.dirname(requested)
        if not os.path.isdir(parent):
            return RestoreResult(False, requested, error=f"Restore parent does not exist: {parent}")
        with _sta_apartment():
            recycled_item = self._find_recycle_item(token)
            if recycled_item is None:
                return RestoreResult(False, requested, error="Exact Recycle Bin item is missing")
            return self._move_item_in_apartment(recycled_item, token, requested)

    def token_exists(self, token: RecycleToken) -> bool:
        if not token.restorable:
            return False
        with _sta_apartment():
            return self._find_recycle_item(token) is not None
