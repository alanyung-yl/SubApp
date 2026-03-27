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

from pathlib import Path
import hashlib
import os
import re
import sys

VENDOR = "EZTools"
APP    = "SubApp"


# ── Env helpers ──────────────────────────────────────────────────────────────

def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _runtime_anchor() -> Path:
    """Deterministic anchor for relative env-var paths.

    Dev/source : SubApp/SubRename/   (where app_paths.py lives)
    Frozen     : folder containing the exe
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _override_path(env_name: str) -> Path | None:
    """Resolve an env var to an absolute Path.

    Relative values are anchored to _runtime_anchor() so CWD never
    affects the result. Returns None if the variable is unset or empty.
    """
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _runtime_anchor() / p
    return p.resolve(strict=False)


def _base_override() -> Path | None:
    """SUBRENAME_BASE_DIR → single root for all runtime dirs."""
    return _override_path("SUBRENAME_BASE_DIR")


# ── Addon-control helpers ────────────────────────────────────────────────────

def addons_disabled() -> bool:
    return _env_flag("SUBRENAME_DISABLE_ADDONS", False)


def default_addons_disabled() -> bool:
    return _env_flag("SUBRENAME_DISABLE_DEFAULT_ADDONS", False)


def addons_override_dir() -> Path | None:
    """SUBRENAME_ADDONS_DIR — highest-priority addons path override."""
    return _override_path("SUBRENAME_ADDONS_DIR")


def addons_enabled() -> bool:
    """Whether plugin discovery should run at all."""
    if addons_disabled():
        return False
    if default_addons_disabled() and addons_override_dir() is None:
        return False
    return True


def qt_profile() -> str:
    """QSettings application profile name.

    Defaults to APP ('SubApp'), overridable via SUBRENAME_PROFILE for
    dev/prod isolation of window geometry.
    """
    raw = os.environ.get("SUBRENAME_PROFILE", "").strip()
    return raw if raw else APP


# ── Path helpers ─────────────────────────────────────────────────────────────

def package_root() -> Path:
    """Read-only bundled files (assets, seed config)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _config_base() -> Path:
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _state_base() -> Path:
    if sys.platform.startswith("linux"):
        return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return _config_base()


# ── Public path API ──────────────────────────────────────────────────────────

def config_dir(create: bool = False) -> Path:
    base = _base_override()
    p = (base / "config") if base else (_config_base() / VENDOR / APP / "config")
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir(create: bool = False) -> Path:
    base = _base_override()
    p = (base / "log") if base else (_state_base() / VENDOR / APP / "log")
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def settings_file() -> Path:
    return config_dir(False) / "settings.json"


def rename_log_file() -> Path:
    return log_dir(False) / "rename_log.txt"


def user_langmap_file() -> Path:
    return config_dir(False) / "langmap.txt"


def addons_dir(create: bool = False) -> Path:
    """User-writable plugin install directory.

    Resolution order:
    1) SUBRENAME_ADDONS_DIR
    2) SUBRENAME_BASE_DIR / addons
    3) platform default: <config-base>/EZTools/SubApp/addons
    """
    override = addons_override_dir()
    if override is not None:
        p = override
    else:
        base = _base_override()
        p = (base / "addons") if base else (_config_base() / VENDOR / APP / "addons")
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_plugin_key(runtime_key: str) -> str:
    raw = str(runtime_key or "unknown")
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._-")
    if not slug:
        slug = "plugin"
    slug = slug[:48]  # display only; digest below guarantees uniqueness
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{slug}_{digest}"


def plugin_data_root_dir(create: bool = False) -> Path:
    """Root folder for plugin data."""
    base = _base_override()
    p = (base / "plugin_data") if base else (_state_base() / VENDOR / APP / "plugin_data")
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def plugin_data_dir(runtime_key: str, create: bool = False) -> Path:
    """Per-plugin writable storage (caches, databases, etc.)."""
    p = plugin_data_root_dir(False) / _safe_plugin_key(runtime_key)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def bundled_langmap_file() -> Path:
    return package_root() / "config" / "langmap.txt"
