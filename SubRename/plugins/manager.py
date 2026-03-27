# Copyright (C) 2025  EZTools
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Plugin discovery and instantiation (filesystem-only)."""

from __future__ import annotations

import glob
import hashlib
import importlib.util
import os
import sys
import traceback
import types
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import List, Tuple

from PyQt6.QtWidgets import QWidget

import app_paths as ap


@contextmanager
def _temp_sys_path(path: str):
    """Temporarily prepend a directory to sys.path during module execution."""
    added = False
    if path and path not in sys.path:
        sys.path.insert(0, path)
        added = True
    try:
        yield
    finally:
        if added:
            try:
                sys.path.remove(path)
            except ValueError:
                pass


@dataclass
class LoadedPlugin:
    id: str
    name: str
    version: str
    obj: object
    runtime_key: str
    pages: List[Tuple[str, QWidget]] = field(default_factory=list)
    error: str | None = None


class PluginManager:
    """Discovers plugins via filesystem scanning in addons_path."""

    def __init__(self, addons_path: str, app_ctx: dict):
        self.addons_path = addons_path
        self.app_ctx = app_ctx

    def load_all(self) -> List[LoadedPlugin]:
        return [self._instantiate(m) for m in self._discover_filesystem()]

    def _load_module_from_path(self, path: str) -> types.ModuleType:
        """Load a plugin module with package-aware context for folder plugins."""
        base = os.path.basename(path)
        plugin_dir = os.path.dirname(path)

        if base == "plugin.py":
            # Folder plugin entrypoint: load as synthetic package child module.
            package_name = self._module_name(plugin_dir)
            module_name = f"{package_name}.plugin"

            if package_name not in sys.modules:
                pkg = types.ModuleType(package_name)
                pkg.__path__ = [plugin_dir]
                pkg.__package__ = package_name
                sys.modules[package_name] = pkg

            spec = importlib.util.spec_from_file_location(module_name, path)
            import_root = plugin_dir

        elif base == "__init__.py":
            # Package plugin entrypoint.
            package_name = self._module_name(plugin_dir)
            module_name = package_name
            spec = importlib.util.spec_from_file_location(
                module_name,
                path,
                submodule_search_locations=[plugin_dir],
            )
            import_root = plugin_dir

        else:
            # Flat single-file plugin.
            module_name = self._module_name(path)
            spec = importlib.util.spec_from_file_location(module_name, path)
            import_root = os.path.dirname(path)

        if not spec or not spec.loader:
            raise ImportError(f"Cannot create module spec for {path}")

        mod = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = mod

        # Optional compatibility for absolute sibling imports within plugin folders.
        with _temp_sys_path(import_root):
            spec.loader.exec_module(mod)

        return mod

    def _discover_filesystem(self) -> List[types.ModuleType]:
        mods: List[types.ModuleType] = []
        if not os.path.isdir(self.addons_path):
            return mods

        candidates: list[str] = []
        candidates += glob.glob(os.path.join(self.addons_path, "*.py"))

        for entry in os.scandir(self.addons_path):
            if not entry.is_dir():
                continue
            plugin_py = os.path.join(entry.path, "plugin.py")
            init_py = os.path.join(entry.path, "__init__.py")
            if os.path.isfile(plugin_py):
                candidates.append(plugin_py)
            elif os.path.isfile(init_py):
                candidates.append(init_py)

        # Deduplicate by realpath
        seen: set[str] = set()
        unique_paths: list[str] = []
        for p in candidates:
            rp = os.path.realpath(p)
            if rp not in seen:
                seen.add(rp)
                unique_paths.append(rp)

        for path in unique_paths:
            try:
                mod = self._load_module_from_path(path)
                mods.append(mod)
            except Exception:
                print(f"[Plugin] Failed importing {path}:\n{traceback.format_exc()}")

        return mods


    @staticmethod
    def _module_name(path: str) -> str:
        path_hash = hashlib.sha256(os.path.realpath(path).encode("utf-8")).hexdigest()[:12]
        return f"subrename_addon_{path_hash}"

    @staticmethod
    def _runtime_key(prefix: str, mod: types.ModuleType) -> str:
        file_attr = getattr(mod, "__file__", None)
        if isinstance(file_attr, str) and file_attr.strip():
            source = f"file:{os.path.realpath(file_attr)}"
        else:
            mod_name = getattr(mod, "__name__", None)
            if isinstance(mod_name, str) and mod_name:
                source = f"name:{mod_name}"
            else:
                source = f"anon:{id(mod)}"

        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
        return f"{prefix}::{digest}"

    def _instantiate(self, mod: types.ModuleType) -> LoadedPlugin:
        try:
            plugin = getattr(mod, "plugin", None) or getattr(mod, "get_plugin", lambda: None)()
            pid = str(getattr(plugin, "id"))
            name = str(getattr(plugin, "name"))
            version = str(getattr(plugin, "version"))

            runtime_key = self._runtime_key(pid, mod)

            plugin_ctx = dict(self.app_ctx)
            plugin_ctx["plugin_runtime_key"] = runtime_key
            plugin_ctx["plugin_data_dir_self"] = (
                lambda rk=runtime_key: str(ap.plugin_data_dir(rk, create=True))
            )

            pages = list(plugin.create_pages(plugin_ctx))
            return LoadedPlugin(
                id=pid,
                name=name,
                version=version,
                obj=plugin,
                runtime_key=runtime_key,
                pages=pages,
            )
        except Exception as e:
            return LoadedPlugin(
                id=getattr(mod, "__name__", "unknown"),
                name="(invalid plugin)",
                version="0",
                obj=mod,
                runtime_key=self._runtime_key("invalid", mod),
                pages=[],
                error=str(e),
            )
