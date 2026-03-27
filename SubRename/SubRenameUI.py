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

"""PyQt6 desktop UI for configuring and running SubRename operations."""
import sys

try:
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog, QTextEdit, QFrame, QSizePolicy, 
        QTableWidget, QTableWidgetItem, QHBoxLayout, QComboBox, QInputDialog, QMenuBar, QMenu, 
        QDialog, QCheckBox, QDialogButtonBox, QFormLayout, QMessageBox, QSplitter, QTabBar, QStackedWidget,
        QTextBrowser,
        QListWidget, QListWidgetItem, QListView, QScrollArea, QGroupBox, QSpacerItem, QStyleOptionGroupBox, QStyle,
        QLineEdit, QStyledItemDelegate, QToolTip, QLayout
    )
    from PyQt6.QtCore import (
        Qt, QMetaObject, pyqtSignal, Q_ARG, QTimer, pyqtSlot, QThread, QSettings, QObject, QEvent, QMimeData, 
        QRegularExpression, QRect
    )
    from PyQt6.QtGui import (
        QColor, QIcon, QAction, QTextCursor, QMouseEvent, QShortcut, QKeySequence, QRegularExpressionValidator,
        QPainterPath, QRegion
    )
except ImportError:
    print("PyQt6 is required. Please install it with 'pip install PyQt6'.")
    sys.exit(1)
from html import escape
import os
import json
import platform
import threading
import SubRename as sr
import app_paths as ap
import subprocess
import logging
from dataclasses import dataclass, field
from pathlib import Path
from send2trash import send2trash
from enum import IntEnum
from logging_utils import log_success, setup_logging, load_user_settings


def reveal_in_explorer(path: str) -> None:
    if not path:
        return
    p = os.path.abspath(os.path.normpath(path))
    if not os.path.exists(p):
        return
    if platform.system() == "Windows":
        subprocess.Popen(["explorer", "/select,", p]) if os.path.isfile(p) else os.startfile(p)
    else:
        folder = os.path.dirname(p) if os.path.isfile(p) else p
        subprocess.Popen(["xdg-open", folder] if platform.system() == "Linux" else ["open", folder])


class NoFocusDelegate(QStyledItemDelegate):
    """Item delegate that does not draw focus rect on table cells."""
    def paint(self, painter, option, index):
        option.state &= ~QStyle.StateFlag.State_HasFocus
        super().paint(painter, option, index)


class FilenameLineEdit(QLineEdit):
    INVALID_MSG = 'Invalid character: \\ / : * ? " < > |'
    _invalid_re = QRegularExpression(r'[\x00-\x1f\x7f-\x9f<>:"/\\|?*]')

    def __init__(self, parent=None):
        super().__init__(parent)

        # Allows only valid filename chars while typing.
        allowed_re = QRegularExpression(r'^[^\x00-\x1f\x7f-\x9f<>:"/\\|?*]*$')
        self.setValidator(QRegularExpressionValidator(allowed_re, self))
        self.inputRejected.connect(self._show_invalid_tooltip)

    def _show_invalid_tooltip(self):
        QToolTip.showText(
            self.mapToGlobal(self.rect().bottomLeft()),
            self.INVALID_MSG,
            self,
        )

    @classmethod
    def _clean_text(cls, text: str) -> str:
        return cls._invalid_re.replace(text or "", "")

    def insertFromMimeData(self, source: QMimeData | None):
        text = source.text() if source else ""
        cleaned = self._clean_text(text)

        if cleaned != text:
            self._show_invalid_tooltip()

        if cleaned:
            self.insert(cleaned)


class FilenameDelegate(NoFocusDelegate):
    """Delegate that uses FilenameLineEdit for editing cells."""

    def createEditor(self, parent, option, index):
        # We only assign this delegate to the "New Name" column, so always use FilenameLineEdit.
        return FilenameLineEdit(parent)


class PopupStyledComboBox(QComboBox):
    """QComboBox with popup flags tuned to avoid square/shadow artifacts around rounded popups."""
    POPUP_GAP_PX = 4

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setView(QListView(self))
        self._configure_popup()

    def showPopup(self):
        self._configure_popup()
        super().showPopup()

        popup = self.view().window()
        if popup is not None:
            # Keep Qt's default popup placement, then add a subtle visual gap.
            combo_top_left = self.mapToGlobal(self.rect().topLeft())
            combo_top = combo_top_left.y()
            combo_bottom = combo_top_left.y() + self.height()
            popup_top = popup.y()
            popup_bottom = popup.y() + popup.height()

            if popup_top >= combo_bottom - 1:
                # Popup is below the combo.
                popup.move(popup.x(), popup.y() + self.POPUP_GAP_PX)
            elif popup_bottom <= combo_top + 1:
                # Popup is above the combo (when near bottom of screen).
                popup.move(popup.x(), popup.y() - self.POPUP_GAP_PX)

    def _configure_popup(self):
        view = self.view()
        if view is None:
            return

        view.setFrameShape(QFrame.Shape.NoFrame)
        popup = view.window()
        if popup is None:
            return

        popup.setObjectName("ComboPopupContainer")
        if is_windows():
            flags = (
                popup.windowFlags()
                | Qt.WindowType.Popup
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.NoDropShadowWindowHint
            )
            popup.setWindowFlags(flags)
            popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)


class ClickOutsideFilter(QObject):
    """Event filter that clears table selection when the user clicks outside the table,
    but preserves clicks on specific protected widgets (e.g., action buttons)."""

    def __init__(self, table, window, preserve_widgets=()):
        super().__init__()
        self.table = table
        self.window = window
        self.preserve_widgets = tuple(preserve_widgets)

    def _is_preserved(self, w):
        return any(w is p or p.isAncestorOf(w) for p in self.preserve_widgets)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            w = QApplication.widgetAt(event.globalPosition().toPoint())
            if w and (w == self.window or self.window.isAncestorOf(w)):
                # Do not clear selection when clicking on protected widgets
                if self._is_preserved(w):
                    return False
                # Clear selection only when clicking outside the table
                if not (w == self.table or self.table.isAncestorOf(w)):
                    self.table.clearSelection()
                    self.table.setCurrentCell(-1, -1)
        return False


@dataclass
class RuntimeState:
    """Thread-safe in-memory state shared between the UI thread and worker threads."""
    _cache_per_set: bool = True
    _conflict_policy: str = "ASK"
    _apply_all_conflicts: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_cache_per_set(self, value: bool):
        with self._lock:
            self._cache_per_set = bool(value)

    def get_cache_per_set(self) -> bool:
        with self._lock:
            return bool(self._cache_per_set)

    def set_apply_all_conflicts(self, value: bool):
        with self._lock:
            self._apply_all_conflicts = bool(value)

    def get_apply_all_conflicts(self) -> bool:
        with self._lock:
            return bool(self._apply_all_conflicts)

    def set_conflict_policy(self, value: str):
        with self._lock:
            self._conflict_policy = value

    def get_conflict_policy(self) -> str:
        with self._lock:
            return self._conflict_policy

runtime_state = RuntimeState()

# Windows-specific imports for title bar theming
if platform.system() == "Windows":
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        ctypes = None

# Platform detection helpers
def is_windows():
    return sys.platform.startswith("win")
 
def is_macos():
    return sys.platform == "darwin"

def is_linux():
    return sys.platform.startswith("linux")

# PyInstaller resource path helper for app icon (bundled assets).
_BASE = ap.package_root()
_ICON = "appicon.ico" if is_windows() else "appicon.png"

APP_ICON = None  # Set after QApplication is created (prevents QPixmap/QGuiApplication errors).

def set_windows_title_bar_theme(window, dark_mode=True):
    """ Set Windows 10/11 title bar to dark or light theme. """
    if platform.system() != "Windows" or not ctypes:
        return False
        
    try:
        hwnd = int(window.winId())
        
        # Windows 10 version 1809+ and Windows 11
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        
        # Set the title bar to dark:1 or light:0
        value = wintypes.DWORD(1 if dark_mode else 0)
        
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            wintypes.HWND(hwnd),
            wintypes.DWORD(DWMWA_USE_IMMERSIVE_DARK_MODE),
            ctypes.byref(value),
            ctypes.sizeof(value)
        )
        return True
        
    except Exception as e:
        print(f"Failed to set Windows title bar theme: {e}")
        return False


# Fixing the corner artifacts on the menu bar
class AdaptiveRoundedMenu(QMenu):
    """QMenu with rounded corners that adapts to platform capabilities."""
    def __init__(self, title="", parent=None):
        super().__init__(title, parent)
        self.use_mask_fallback = False
        self.radius = 8
        
        self.setWindowFlag(Qt.WindowType.NoDropShadowWindowHint, True)  # kill the rectangular OS shadow so it won't go outside of corners.
        
        # Attempt smooth path
        if is_windows() or is_macos():  # Frameless + translucent for true antialiased corners
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            if is_windows():
                self.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
            self.use_mask_fallback = False  # No mask in the smooth path
        else:  # Linux: try translucency without frameless
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.use_mask_fallback = False  # True to force the mask path if corner artifact still occurs
    
    def showEvent(self, e):
        super().showEvent(e)
        # Heuristic: if translucency is not enforced, force the mask path at runtime.
        if not self.use_mask_fallback:
            # Grab a pixel and see if alpha seems supported;
            # if not, switch to mask mode (coarse but effective).
            # Note: This is a cheap check; you can remove it if unnecessary.   debug
            try:
                img = self.grab().toImage()
                # Check a corner pixel; if fully opaque and QSS radius is set,
                # likely the platform isn't honoring translucency.
                px = img.pixelColor(0, 0)
                if px.alpha() == 255 and is_linux():
                    self.use_mask_fallback = True
                    self.apply_mask()
            except Exception:
                pass
    
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.use_mask_fallback:
            self.apply_mask()
        else:  # Smooth path to ensure any previous mask is cleared
            if not self.mask().isEmpty():
                self.clearMask()
    
    def apply_mask(self):
        # Bitmap/region masks are aliased, but they clip reliably without compositing.
        rect = QRect(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, self.radius, self.radius)
        region = QRegion(path.toFillPolygon().toPolygon())
        self.setMask(region)


class CheckmarkAction(QAction):
    """Thin wrapper over QAction that defaults to checkable=True."""
    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        

class CheckableListWidget(QListWidget):
    """Custom QListWidget with improved checkbox responsiveness"""
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            item = self.itemAt(event.pos())
            if item and (item.flags() & Qt.ItemFlag.ItemIsUserCheckable):
                current_state = item.checkState()
                new_state = (Qt.CheckState.Unchecked if current_state == Qt.CheckState.Checked else Qt.CheckState.Checked)
                item.setCheckState(new_state)
                return
        super().mousePressEvent(event)


SETTINGS_FILE = str(ap.settings_file())
RENAME_LOG_FILE = str(ap.rename_log_file())
SETTINGS_VERSION = 1
settings_cache: dict | None = None

def _migrate_settings(data: dict) -> dict:
    """Migrate settings from older versions. Mutates *data* in place and returns it."""
    v = data.get("settings_version", 0)
    if v < SETTINGS_VERSION:
        data["settings_version"] = SETTINGS_VERSION
    return data

def preload_settings():
    """Load settings cache and ensure settings.json exists on first startup."""
    global settings_cache

    default_settings = {"settings_version": SETTINGS_VERSION}
    needs_write = False

    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)

            if not isinstance(loaded, dict):
                loaded = {}

            before = dict(loaded)
            settings_cache = _migrate_settings(loaded)
            if settings_cache is None:
                settings_cache = loaded
            needs_write = settings_cache != before
        except Exception:
            settings_cache = default_settings.copy()
            needs_write = True
    else:
        settings_cache = default_settings.copy()
        needs_write = True

    if needs_write:
        try:
            ap.config_dir(create=True)
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(settings_cache, f, indent=2)
        except Exception:
            pass

    return settings_cache

def preload_log_target():
    """Ensure log directory and rename_log.txt exist on first startup."""
    try:
        ap.log_dir(create=True)
        ap.rename_log_file().touch(exist_ok=True)
    except Exception:
        pass

settings = preload_settings()
preload_log_target()
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ.pop("QT_AUTO_SCREEN_SCALE_FACTOR", None)
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

setup_logging(RENAME_LOG_FILE)
ap.plugin_data_root_dir(create=True)
sr.reload_lang_map()  # seed langmap.txt on first startup and populate cache

# Video file extensions
VIDEO_EXTENSIONS = [
    '.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.f4v', '.webm', 
    '.m4v', '.3gp', '.ogv', '.ts', '.mts', '.m2ts', '.vob', '.asf', '.rm', 
    '.rmvb', '.divx', '.xvid', '.mpg', '.mpeg', '.m2v', '.m4v', '.3g2'
]

# Subtitle file extensions  
SUBTITLE_EXTENSIONS = [
    '.srt', '.ass', '.ssa', '.sub', '.idx', '.vtt', '.smi', '.sami',
    '.mpl', '.txt', '.rt', '.pjs', '.psb', '.dks', '.jss', '.aqt',
    '.gsub', '.mpsub', '.sbv', '.ttml', '.dfxp', '.xml', '.ttxt'
]

class SubCol(IntEnum):
    """Column indices for the subtitle table."""
    FILE_NAME = 0
    NEW_NAME  = 1
    PATH      = 2
    PREVIEW   = 3
    STATUS    = 4

# ─── settings helpers ──────────────────────────────────────────────────────────────
def load_settings() -> dict:
    """Return the in-memory settings cache, loading from disk on first call."""
    global settings_cache
    if settings_cache is None:
        settings_cache = preload_settings()
    return settings_cache

def flush_settings() -> None:
    """Write the in-memory cache to disk atomically (write-to-temp then replace)."""
    import tempfile
    try:
        dir_ = os.path.dirname(SETTINGS_FILE) or "."
        os.makedirs(dir_, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(settings_cache, f, indent=2)
            os.replace(tmp_path, SETTINGS_FILE)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        pass

def save_settings(data: dict) -> None:
    """Update the in-memory cache and flush to disk atomically."""
    global settings_cache
    settings_cache = data
    flush_settings()

def get_last_target_folder():
    return load_settings().get("last_target_folder", "")

def set_last_target_folder(folder):
    settings = load_settings()
    settings["last_target_folder"] = folder
    save_settings(settings)

def get_last_subtitle_folder():
    return load_settings().get("last_subtitle_folder", "")

def set_last_subtitle_folder(folder):
    settings = load_settings()
    settings["last_subtitle_folder"] = folder
    save_settings(settings)

def get_compact_mode():
    return load_settings().get("compact_mode", False)

def set_compact_mode(enabled):
    settings = load_settings()
    settings["compact_mode"] = enabled
    save_settings(settings)

def get_zoom_level():
    return load_settings().get("zoom_level", 100)

def set_zoom_level(level):
    settings = load_settings()
    settings["zoom_level"] = level
    save_settings(settings)

def get_theme():
    return load_settings().get("dark_mode", True)

def get_preview_mode():
    return load_settings().get("preview_mode", True)

def get_cache_per_set():
    return runtime_state.get_cache_per_set()

def get_conflict_policy():
    return runtime_state.get_conflict_policy()

def set_preview_mode(enabled):
    settings = load_settings()
    settings["preview_mode"] = enabled
    save_settings(settings)

def get_delete_empty_folders():
    return load_settings().get("delete_empty_folders", False)

def set_delete_empty_folders(enabled):
    settings = load_settings()
    settings["delete_empty_folders"] = enabled
    save_settings(settings)

def get_last_src_format():
    return load_settings().get("last_src_format", "Auto")

def set_last_src_format(format):
    settings = load_settings()
    settings["last_src_format"] = format
    save_settings(settings)

def get_last_dst_format():
    return load_settings().get("last_dst_format", "Auto")

def set_last_dst_format(format):
    settings = load_settings()
    settings["last_dst_format"] = format
    save_settings(settings)

def get_enabled_dst_ext():
    settings = load_settings()
    enabled = settings.get("enabled_video_extensions", ['.avi', '.mkv', '.mov', '.mp4', '.webm', '.wmv'])
    return [ext for ext in enabled if ext in get_all_video_extensions()]

def set_enabled_video_extensions(extensions):
    settings = load_settings()
    settings["enabled_video_extensions"] = extensions
    save_settings(settings)

def get_enabled_src_ext():
    settings = load_settings()
    enabled = settings.get("enabled_subtitle_extensions", ['.ass', '.srt', '.ssa', '.sub'])
    return [ext for ext in enabled if ext in get_all_subtitle_extensions()]

def set_enabled_subtitle_extensions(extensions):
    settings = load_settings()
    settings["enabled_subtitle_extensions"] = extensions
    save_settings(settings)

def get_custom_video_extensions():
    return load_settings().get("custom_video_extensions", [])

def set_custom_video_extensions(extensions):
    settings = load_settings()
    settings["custom_video_extensions"] = extensions
    save_settings(settings)

def get_custom_subtitle_extensions():
    return load_settings().get("custom_subtitle_extensions", [])
    
def set_custom_subtitle_extensions(extensions):
    settings = load_settings()
    settings["custom_subtitle_extensions"] = extensions
    save_settings(settings)

def get_disabled_builtin_video_extensions():
    return load_settings().get("disabled_builtin_video_extensions", [])

def set_disabled_builtin_video_extensions(extensions):
    settings = load_settings()
    settings["disabled_builtin_video_extensions"] = extensions
    save_settings(settings)

def get_disabled_builtin_subtitle_extensions():
    return load_settings().get("disabled_builtin_subtitle_extensions", [])

def set_disabled_builtin_subtitle_extensions(extensions):
    settings = load_settings()
    settings["disabled_builtin_subtitle_extensions"] = extensions
    save_settings(settings)

def get_all_video_extensions():
    disabled = set(get_disabled_builtin_video_extensions())
    available_builtin = [ext for ext in VIDEO_EXTENSIONS if ext not in disabled]
    return sorted(set(available_builtin + get_custom_video_extensions()))

def get_all_subtitle_extensions():
    disabled = set(get_disabled_builtin_subtitle_extensions())
    available_builtin = [ext for ext in SUBTITLE_EXTENSIONS if ext not in disabled]
    return sorted(set(available_builtin + get_custom_subtitle_extensions()))

def get_subtitle_file_filter():
    """Get dynamic subtitle file filter based on enabled extensions"""
    enabled_exts = get_enabled_src_ext()
    if enabled_exts:
        filter_parts = [f"*{ext}" for ext in enabled_exts]
        filter_string = " ".join(filter_parts)
        return f"Subtitle Files ({filter_string});;All Files (*)"
    else:
        return "All Files (*)"

# ─── Theme Styles ──────────────────────────────────────────────────────────────
LIGHT_THEME = {
    'window_bg': '#ffffff',
    'widget_bg': '#f0f0f0',
    'text_color': '#000000',
    'border_color': '#888888',
    'button_bg': '#e0e0e0',
    'button_hover': '#d0d0d0',
    'table_header_bg': '#f8f8f8',
    'table_alternate_bg': '#f5f5f5',
    'table_select_bg': '#3399ff',
    'table_select_color': '#0073e7',
    'drop_area_bg': '#fafafa',
    'log_bg': '#ffffff',
    'success_color': '#008000',
    'error_color': '#ff0000',
    'warning_color': '#ff4500',
    'info_color': '#17a2b8',
}

DARK_THEME = {
    'window_bg': '#0f0f0f',
    'widget_bg': '#1a1a1a',
    'text_color': '#e0e0e0',
    'border_color': '#2a2a2a',
    'button_bg': '#2a2a2a',
    'button_hover': '#3a3a3a',
    'table_header_bg': '#252525',
    'table_alternate_bg': '#151515',
    'table_select_bg': '#e6382f',
    'table_select_color': '#ee5e56',
    'drop_area_bg': '#1a1a1a',
    'log_bg': '#1a1a1a',
    'success_color': '#28a745',
    'error_color': '#dc3545',
    'warning_color': '#ffc107',
    'info_color': '#569cd6',
}

# ─── Stylesheets ───────────────────────────────────────────────────────────
def get_drop_area_frame_style(theme, zoom_level=100):
    multiplier = zoom_level / 100.0
    return f'''
        QFrame {{
            border: {int(2 * multiplier)}px solid {theme['border_color']};
            border-radius: {int(10 * multiplier)}px;
            background: {theme['drop_area_bg']};
        }}
    '''

_stylesheet_cache: dict[tuple, str] = {}

def generate_stylesheet(theme):
    """Generate a comprehensive stylesheet for the application. Cached by (theme id, zoom)."""
    zoom_level = get_zoom_level()
    cache_key = (id(theme), zoom_level)
    if cache_key in _stylesheet_cache:
        return _stylesheet_cache[cache_key]
    multiplier = zoom_level / 100.0
    
    # Build paths to shared assets
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if theme == DARK_THEME:
        arrow_file = "chevrons_dark.svg"
        chevron_right_file = "chevron_right_dark.svg"
        chevron_down_file = "chevron_down_dark.svg"
        checkmark_file = "checkmark_dark.svg"
        # grip_horz_file = "grip_6dots_horz_dark.svg"
        # grip_vert_file = "grip_6dots_vert_dark.svg"
    else:
        arrow_file = "chevrons_light.svg"
        chevron_right_file = "chevron_right_light.svg"
        chevron_down_file = "chevron_down_light.svg"
        checkmark_file = "checkmark_light.svg"
        # grip_horz_file = "grip_6dots_horz_light.svg"
        # grip_vert_file = "grip_6dots_vert_light.svg"
    
    arrow_path = os.path.join(base_dir, "assets", arrow_file).replace('\\', '/')
    chevron_right_path = os.path.join(base_dir, "assets", chevron_right_file).replace('\\', '/')
    chevron_down_path = os.path.join(base_dir, "assets", chevron_down_file).replace('\\', '/')
    checkmark_path = os.path.join(base_dir, "assets", checkmark_file).replace('\\', '/')
    
    result = f'''
        QWidget {{
            background: {theme['window_bg']};
            color: {theme['text_color']};
        }}
        QTabBar::tab {{
            background: {theme['widget_bg']};
            color: {theme['text_color']};
            padding: {int(6 * multiplier)}px {int(14 * multiplier)}px;
            border: 1px solid {theme['border_color']};
            border-bottom: none;
            border-top-left-radius: {int(8 * multiplier)}px;
            border-top-right-radius: {int(8 * multiplier)}px;
            margin-right: {int(4 * multiplier)}px;
        }}
        QTabBar::tab:hover {{
            background: {theme['button_hover']};
        }}
        QTabBar::tab:selected {{
            background: {theme['window_bg']};
            color: {theme['text_color']};
        }}
        QPushButton {{
            background: {theme['button_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['border_color']};
            padding: {int(8 * multiplier)}px {int(16 * multiplier)}px;
            border-radius: {int(8 * multiplier)}px;
            font-weight: bold;
        }}
        QPushButton:hover {{
            background: {theme['button_hover']};
        }}
        QPushButton:pressed {{
            background: {theme['border_color']};
        }}
        QToolTip {{
            background: {theme['button_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['border_color']};
            padding: {int(6 * multiplier)}px {int(10 * multiplier)}px;
            font-weight: bold;
        }}
        QLabel {{
            color: {theme['text_color']};
            padding: {int(4 * multiplier)}px;
        }}
        QCheckBox {{
            color: {theme['text_color']};
            spacing: 8px;
        }}
        QCheckBox::indicator {{
            width: {int(20 * multiplier)}px;
            height: {int(20 * multiplier)}px;
            border-radius: {int(4 * multiplier)}px;
            border: 1px solid {theme['border_color']};
            background: {theme['widget_bg']};
            margin-right: {int(8 * multiplier)}px;
        }}
        QCheckBox::indicator:hover {{
            border-color: {theme['button_hover']};
            background: {theme['button_bg']};
        }}
        QCheckBox::indicator:unchecked {{
            image: none;
        }}
        QCheckBox::indicator:checked {{
            image: url("{checkmark_path}");
        }}
        QCheckBox::indicator:disabled {{
            background: {theme['widget_bg']};
            border-color: {theme['border_color']};
            image: none;
        }}
        QComboBox {{
            combobox-popup: 0;
            background: {theme['widget_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['border_color']};
            padding: {int(6 * multiplier)}px;
            border-radius: {int(4 * multiplier)}px;
        }}
        QComboBox::drop-down {{
            border: none;
            width: {int(26 * multiplier)}px;
            background: transparent;
        }}
        QComboBox::down-arrow {{
            image: url("{arrow_path}");
            width: {int(16 * multiplier)}px;
            height: {int(20 * multiplier)}px;
            margin-right: {int(6 * multiplier)}px;
            margin-top: 0px;
        }}
        QComboBox QAbstractItemView {{
            background: {theme['widget_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['border_color']};
            padding: {int(6 * multiplier)}px;
            border-radius: {int(4 * multiplier)}px;
            outline: 0px;
        }}
        QFrame#ComboPopupContainer {{
            background: transparent;
            border: none;
        }}
        /* Group boxes: rounded frame with overlaid title to mimic native look */
        QGroupBox {{
            background: transparent;
            border: 1px solid {theme['border_color']};
            border-radius: {int(8 * multiplier)}px;
            /* keep title centered on the top border across zoom levels */
            margin-top: {int(10 * multiplier)}px;
            padding: {int(8 * multiplier)}px {int(8 * multiplier)}px {int(8 * multiplier)}px {int(8 * multiplier)}px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: {int(16 * multiplier)}px;
            padding: 0 {int(6 * multiplier)}px;
            background-color: {theme['window_bg']};
            color: {theme['text_color']};
        }}
        QTextEdit {{
            background: {theme['log_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['border_color']};
            border-radius: {int(8 * multiplier)}px;
            padding: {int(8 * multiplier)}px;
        }}
        QMenuBar {{
            background: {theme['widget_bg']};
            color: {theme['text_color']};
            border-bottom: 1px solid {theme['border_color']};
        }}
        QMenuBar::item {{
            background: transparent;
            padding: {int(6 * multiplier)}px {int(12 * multiplier)}px;
            border-radius: {int(4 * multiplier)}px;
        }}
        QMenuBar::item:selected {{
            background: {theme['button_hover']};
        }}
        QMenu {{
            background: {theme['widget_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['border_color']};
            border-radius: {int(8 * multiplier)}px;
            padding: {int(6 * multiplier)}px;
        }}
        QMenu::item {{
            padding: {int(6 * multiplier)}px 0px {int(6 * multiplier)}px {int(4 * multiplier)}px;
            margin-left: {int(8 * multiplier)}px;
            margin-right: {int(8 * multiplier)}px;
            border-radius: {int(4 * multiplier)}px;
        }}
        QMenu::item:selected {{
            background: {theme['button_hover']};
        }}
        QMenu::indicator {{
            width: {int(20 * multiplier)}px;
            height: {int(20 * multiplier)}px;
            padding-left: {int(6 * multiplier)}px;
            padding-right: {int(6 * multiplier)}px;
        }}
        QMenu::indicator:unchecked {{
            image: none;
        }}
        QMenu::indicator:checked {{
            image: url("{checkmark_path}");
        }}
        QMenu::separator {{
            height: 1px;
            background: {theme['border_color']};
            margin: {int(4 * multiplier)}px 0px;
        }}
        QMenu::right-arrow {{
            image: url("{chevron_right_path}");
            width: {int(16 * multiplier)}px;
            height: {int(16 * multiplier)}px;
            padding: {int(6 * multiplier)}px {int(10 * multiplier)}px {int(6 * multiplier)}px {int(10 * multiplier)}px;
            margin-left: {int(4 * multiplier)}px;
        }}
        QTableWidget {{  
            padding: {int(6 * multiplier)}px;
            background: {theme['widget_bg']};
            color: {theme['text_color']};
            gridline-color: {theme['border_color']};
        }}
        QTableWidget::item {{
            border-radius: 0px;
            padding: 0px {int(8 * multiplier)}px 0px {int(8 * multiplier)}px;
        }}
        QTableWidget::item:selected,
        QTableWidget::item:selected:active,
        QTableWidget::item:selected:!active {{
            background: {theme['button_hover']};
            color: {theme['text_color']};
        }}
        QTableWidget QLineEdit::focus {{
            background: {theme['button_hover']};
            color: {theme['text_color']};
            border: none;
            border-radius: {int(4 * multiplier)}px;
            selection-background-color: {theme['table_select_bg']};
            selection-color: {theme['text_color']};
        }}
        QLineEdit {{
            background: {theme['widget_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['widget_bg']};
            border-radius: {int(4 * multiplier)}px;
            padding: 0px {int(8 * multiplier)}px 0px {int(8 * multiplier)}px;
            selection-background-color: {theme['table_select_bg']};
            selection-color: {theme['text_color']};
        }}
        QLineEdit:focus {{
            background: {theme['window_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['table_select_bg']};
            border-radius: {int(4 * multiplier)}px;
            padding: 0px {int(8 * multiplier)}px 0px {int(8 * multiplier)}px;
            selection-background-color: {theme['table_select_bg']};
            selection-color: {theme['text_color']};
        }}
        QHeaderView {{
            background: {theme['table_header_bg']};
            color: {theme['text_color']};
            border: none;
            border-radius: 0px;
        }}
        QHeaderView::section {{
            background: {theme['table_header_bg']};
            color: {theme['text_color']};
            padding: {int(4 * multiplier)}px;
            border: 1px solid {theme['border_color']};
        }}
        QTableCornerButton::section {{
            background: {theme['table_header_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['border_color']};
        }}
        QScrollBar:vertical {{
            background: {theme['widget_bg']};
            width: {int(16 * multiplier)}px;
            margin: {int(2 * multiplier)}px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {theme['border_color']};
            min-height: {int(20 * multiplier)}px;
            border-radius: {int(4 * multiplier)}px;
        }}
        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0px;
            background: none;
            border: none;
        }}
        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {{
            background: none;
        }}
        QScrollBar:horizontal {{
            background: {theme['widget_bg']};
            height: {int(16 * multiplier)}px;
            margin: {int(2 * multiplier)}px;
            border: none;
        }}
        QScrollBar::handle:horizontal {{
            background: {theme['border_color']};
            min-width: {int(20 * multiplier)}px;
            border-radius: {int(4 * multiplier)}px;
        }}
        QScrollBar::add-line:horizontal,
        QScrollBar::sub-line:horizontal {{
            width: 0px;
            background: none;
            border: none;
        }}
        QScrollBar::add-page:horizontal,
        QScrollBar::sub-page:horizontal {{
            background: none;
        }}
        QDialog {{
            background: {theme['window_bg']};
            color: {theme['text_color']};
        }}
        QListWidget {{
            background: {theme['widget_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['border_color']};
            border-radius: {int(4 * multiplier)}px;
            padding: {int(4 * multiplier)}px;
            selection-background-color: {theme['button_hover']};
            outline: none;
        }}
        QListWidget::item {{
            padding: {int(6 * multiplier)}px;
            border-radius: {int(2 * multiplier)}px;
            margin: {int(1 * multiplier)}px;
            border: none;
            outline: none;
        }}
        QListWidget::item:hover {{
            background: {theme['button_hover']};
        }}
        QListWidget::item:selected {{
            background: {theme['button_hover']};
        }}
        QListWidget::item:focus {{
            outline: none;
            border: none;
        }}
        QListWidget::indicator {{
            width: {int(20 * multiplier)}px;
            height: {int(20 * multiplier)}px;
            border-radius: {int(4 * multiplier)}px;
            border: 1px solid {theme['border_color']};
            background: {theme['widget_bg']};
            margin-right: {int(8 * multiplier)}px;
        }}
        QListWidget::indicator:hover {{
            border-color: {theme['button_hover']};
            background: {theme['button_bg']};
        }}
        QListWidget::indicator:unchecked {{
            image: none;
        }}
        QListWidget::indicator:checked {{
            image: url("{checkmark_path}");
        }}
        QSplitter::handle:horizontal,
        QSplitter::handle:vertical {{
            image: none;
        }}
        QTreeWidget {{
            background-color: {theme['widget_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['border_color']};
            border-radius: {int(4 * multiplier)}px;
            outline: none;
        }}
        QTreeWidget::item {{
            padding: {int(4 * multiplier)}px {int(2 * multiplier)}px;
            border: none;
        }}
        QTreeWidget::item:hover {{
            background-color: {theme['table_alternate_bg']};
        }}
        QTreeWidget::item:selected {{
            background: #3874f2;
            color: white;
        }}
        QTreeWidget::branch:has-children:!has-siblings:closed,
        QTreeWidget::branch:closed:has-children:has-siblings {{
            image: url("{chevron_right_path}");
        }}
        QTreeWidget::branch:open:has-children:!has-siblings,
        QTreeWidget::branch:open:has-children:has-siblings {{
            image: url("{chevron_down_path}");
        }}
        QTreeWidget::indicator {{
            width: {int(18 * multiplier)}px;
            height: {int(18 * multiplier)}px;
            border: 2px solid {theme['border_color']};
            border-radius: {int(4 * multiplier)}px;
            background-color: {theme['widget_bg']};
        }}
        QTreeWidget::indicator:unchecked {{
            background-color: {theme['widget_bg']};
        }}
        QTreeWidget::indicator:unchecked:hover {{
            border-color: {theme['info_color']};
        }}
        QTreeWidget::indicator:checked {{
            background-color: {theme['info_color']};
            border-color: {theme['info_color']};
            image: url("{checkmark_path}");
        }}
        QTreeWidget::indicator:indeterminate {{
            background-color: {theme['info_color']};
            border-color: {theme['info_color']};
        }}
    '''
    _stylesheet_cache[cache_key] = result
    return result


# ─── UI ──────────────────────────────────────────────────────────────────────────────
class DropArea(QFrame):
    def __init__(self, on_files_selected, on_selection_changed=None, parent_window=None):
        super().__init__()
        self.setAcceptDrops(True)
        self.on_files_selected = on_files_selected
        self.on_selection_changed = on_selection_changed
        self.parent_window = parent_window
        self.current_theme = LIGHT_THEME  # Default theme
        
        self.label = QLabel("Click or drag-and-drop subtitle files here", self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(["File Name", "New Name", "Path", "Preview", "Status"])
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        # Default delegate for most columns, filename-aware editor for "New Name"
        self.table.setItemDelegate(NoFocusDelegate(self.table))
        self.table.setItemDelegateForColumn(1, FilenameDelegate(self.table))
        self.table.hide()  # Hide initially
        
        # Set maximum column widths and auto-resize
        self.table.setColumnWidth(0, 300)  # File name column
        self.table.setColumnWidth(1, 300)  # New Name column
        self.table.setColumnWidth(2, 400)  # Path column  
        self.table.setColumnWidth(3, 100)  # Preview column
        self.table.setColumnWidth(4, 100)  # Status column
        
        """Override resizeColumnsToContents to cap path column width"""
        original_resize = self.table.resizeColumnsToContents
        def resize_with_path_limit():
            original_resize()
            if self.table.columnWidth(2) > 500:
                self.table.setColumnWidth(2, 500)
        self.table.resizeColumnsToContents = resize_with_path_limit
        
        self.table.resizeColumnsToContents()
        
        if self.on_selection_changed:
            self.table.selectionModel().selectionChanged.connect(self.on_selection_changed)

        self.setMinimumHeight(150)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(0)
        layout.addWidget(self.label)
        layout.addWidget(self.table)
        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        self.update_theme(self.current_theme)

    def update_theme(self, theme, zoom_level=100):
        self.current_theme = theme
        self.setStyleSheet(get_drop_area_frame_style(theme, zoom_level))
        self.table.setStyleSheet(generate_stylesheet(theme))
        self.table.resizeColumnsToContents()

    def filter_new_files(self, candidates: list[str]) -> list[str]:
        """Remove files already present in the table."""
        existing = set()
        for row in range(self.table.rowCount()):
            path_item = self.table.item(row, SubCol.PATH)
            if path_item:
                existing.add(path_item.text())
        return [f for f in candidates if f not in existing]

    def accept_files(self, files: list[str]) -> None:
        """Deduplicate, forward to callback, and persist the last-used folder."""
        append_mode = self.table.rowCount() > 0
        if append_mode and files:
            files = self.filter_new_files(files)
        if files:
            self.on_files_selected(files, append=append_mode)
            set_last_subtitle_folder(os.path.dirname(files[0]))
        else:
            self.on_files_selected([], append=append_mode)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            last_folder = get_last_subtitle_folder()
            files, _ = QFileDialog.getOpenFileNames(
                self, "Select Subtitle Files", last_folder, get_subtitle_file_filter()
            )
            self.accept_files(files)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            urls = event.mimeData().urls()
            enabled_exts = get_enabled_src_ext()
            for url in urls:
                path = url.toLocalFile()
                if os.path.isfile(path):
                    file_ext = os.path.splitext(path)[1].lower()
                    if file_ext in enabled_exts:
                        event.acceptProposedAction()
                        return
        # If no valid subtitle files found, don't accept the drop

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        enabled_exts = get_enabled_src_ext()
        files = [
            url.toLocalFile() for url in urls
            if os.path.isfile(url.toLocalFile())
            and os.path.splitext(url.toLocalFile())[1].lower() in enabled_exts
        ]
        self.accept_files(files)

    def display_files(self, files, append=False):
        self.label.hide()
        self.table.show()
        if not append:
            self.table.setRowCount(0)
        
        for file_path in files:
            row = self.table.rowCount()
            self.table.insertRow(row)
            filename_item = QTableWidgetItem(os.path.basename(file_path))
            filename_item.setFlags(filename_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            new_name_item = QTableWidgetItem("")  # Empty initially, filled during preview
            path_item = QTableWidgetItem(file_path)
            path_item.setFlags(path_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            
            preview_text = "⏳" if get_compact_mode() else "⏳ Pending"
            preview_item = QTableWidgetItem(preview_text)
            preview_item.setFlags(preview_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            
            status_text = "⏳" if get_compact_mode() else "⏳ Pending"
            status_item = QTableWidgetItem(status_text)
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            filename_item.setToolTip(file_path)
            new_name_item.setToolTip("New name will be shown here during preview")
            path_item.setToolTip(file_path)
            self.table.setItem(row, SubCol.FILE_NAME, filename_item)
            self.table.setItem(row, SubCol.NEW_NAME, new_name_item)
            self.table.setItem(row, SubCol.PATH, path_item)
            self.table.setItem(row, SubCol.PREVIEW, preview_item)
            self.table.setItem(row, SubCol.STATUS, status_item)
        self.table.resizeColumnsToContents()
        
        if self.on_selection_changed:
            self.on_selection_changed()

    def clear_files(self):
        self.table.hide()
        self.label.show()
        self.table.setRowCount(0)
        
        if self.on_selection_changed:
            self.on_selection_changed()

    def update_preview(self, preview_data):
        """
        Update the preview column with new names from dry run results.
        preview_data should be a list of dicts with {"source_path": str, "new_name": str, "status": str}
        """
        if not self.table.isVisible():
            return

        # Create a mapping from source_path to preview info
        preview_map = {item["source_path"]: item for item in preview_data}
        
        for row in range(self.table.rowCount()):
            path_item = self.table.item(row, SubCol.PATH)
            if path_item:
                source_path = path_item.text()
                if source_path in preview_map:
                    preview_info = preview_map[source_path]
                    new_name_item = self.table.item(row, SubCol.NEW_NAME)
                    preview_item = self.table.item(row, SubCol.PREVIEW)
                    
                    if new_name_item:
                        new_name_item.setText(preview_info["new_name"])
                        
                        status = preview_info.get("status", "")
                        status_map = {
                            "OK": ("✅", "✅ Ready"),
                            "OVERWRITE": ("📝", "📝 Overwrite"),
                            "SUFFIX": ("📚", "📚 Keep Both"),
                            "TAG": ("🏷️", "🏷️ Tag"),
                            "SKIP": ("🚫", "🚫 Skip"),
                            "SKIP_EXISTS": ("⚠️", "⚠️ Exists"),
                            "FAIL": ("❌", "❌ Error"),
                            "PENDING": ("⏳", "⏳ Pending"),
                        }
                        compact_text, full_text = status_map.get(status, ("⏳", "⏳ Pending"))
                        preview_item.setText(compact_text if get_compact_mode() else full_text)

        self.table.resizeColumnsToContents()

    def get_custom_names(self):
        """
        Collect custom names from the table's "New Name" column.
        Returns a dict mapping source_path -> custom_new_name
        """
        custom_names = {}
        if not self.table.isVisible():
            return custom_names
            
        for row in range(self.table.rowCount()):
            path_item = self.table.item(row, SubCol.PATH)
            new_name_item = self.table.item(row, SubCol.NEW_NAME)
            
            if path_item and new_name_item:
                source_path = path_item.text()
                custom_name = new_name_item.text().strip()
                
                if custom_name:  # Only include if the custom name is not empty
                    custom_names[source_path] = custom_name
        return custom_names


class VideoDropArea(QFrame):
    def __init__(self, parent=None):
        super().__init__()
        self.setAcceptDrops(True)
        self.current_theme = LIGHT_THEME
        self.parent_window = parent
        
        self.label = QLabel("Click or drag-and-drop video folder here", self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Video File Name", "Size (MB)"])
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setItemDelegate(NoFocusDelegate(self.table))
        self.table.hide()  # Hide initially
        
        self.table.setColumnWidth(0, 400)  # File name column
        self.table.setColumnWidth(1, 100)  # Size column
        self.table.resizeColumnsToContents()

        self.setMinimumHeight(150)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.label)
        layout.addWidget(self.table)
        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        self.update_theme(self.current_theme)

    def update_theme(self, theme, zoom_level=100):
        self.current_theme = theme
        self.setStyleSheet(get_drop_area_frame_style(theme, zoom_level))
        self.table.setStyleSheet(generate_stylesheet(theme))
        self.table.resizeColumnsToContents()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.parent_window:
                self.parent_window.select_target_folder()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            # Check if any URL is a directory
            urls = event.mimeData().urls()
            for url in urls:
                path = url.toLocalFile()
                if os.path.isdir(path):
                    event.acceptProposedAction()
                    return
        # If no valid folder found, don't accept the drop

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                # Set the target folder and update the video table
                if self.parent_window:
                    self.parent_window.target_folder = path
                    self.parent_window.video_table_locked = False
                    self.parent_window.target_label.setText(f"Destination Folder: {path}")
                    set_last_target_folder(path)
                    add_recent_target_folder(path)
                    self.parent_window.update_video_table(log_count=True)
                    self.parent_window.update_subtitle_count()
                break

    def display_files(self, files):
        self.label.hide()
        self.table.show()
        self.table.setRowCount(0)
        
        for i, file in enumerate(files):
            self.table.insertRow(i)
            filename_item = QTableWidgetItem(os.path.basename(file))
            filename_item.setFlags(filename_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            
            file_size = os.path.getsize(file) / (1024 * 1024)
            size_item = QTableWidgetItem(f"{file_size:.2f}")
            size_item.setFlags(size_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            filename_item.setToolTip(file)
            self.table.setItem(i, 0, filename_item)
            self.table.setItem(i, 1, size_item)
        
        self.table.resizeColumnsToContents()

    def clear_files(self):
        self.table.hide()
        self.label.show()
        self.table.setRowCount(0)


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.resize(900, 600)
        # Ensure dialog uses the same QSS as main window even under Fusion style
        theme = parent.current_theme if parent and hasattr(parent, 'current_theme') else (DARK_THEME if get_theme() else LIGHT_THEME)
        self.setStyleSheet(generate_stylesheet(theme))
        
        # Track changes for unsaved changes
        self.has_unsaved_changes = False
        self.original_settings = {}
        self.cached_extension_changes = {
            'custom_video': [],
            'custom_subtitle': [],
            'disabled_video': [],
            'disabled_subtitle': [],
            'enabled_video': [],
            'enabled_subtitle': []
        }
        
        main_layout = QHBoxLayout(self)
        
        # Create category list on the left
        self.category_list = QListWidget()
        self.category_list.setFixedWidth(150)
        self.category_list.addItem("General")
        self.category_list.addItem("Extensions")
        self.category_list.addItem("View")
        self.category_list.setCurrentRow(0)
        self.category_list.currentRowChanged.connect(self.switch_tab)
        main_layout.addWidget(self.category_list)
        
        self.stacked_widget = QStackedWidget()  # Create stacked widget for content
        
        self.create_general_tab()
        self.create_extensions_tab()
        self.create_view_tab()
        
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.on_accept_clicked)
        self.button_box.rejected.connect(self.on_reject_clicked)
        self.right_side_container = QWidget()
        right_side_layout = QVBoxLayout(self.right_side_container)
        right_side_layout.setContentsMargins(0, 0, 0, 0)

        right_side_layout.addWidget(self.stacked_widget, 1)

        buttons_row = QHBoxLayout()
        buttons_row.addStretch()
        buttons_row.addWidget(self.button_box)
        right_side_layout.addLayout(buttons_row)

        main_layout.addWidget(self.right_side_container, 1)
        
        self.setLayout(main_layout)
        
        self.load_settings()
        self.store_original_settings()  # Store for comparison
        self.connect_change_signals()
        self.has_unsaved_changes = False

    def _wrap_settings_page(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)  # page fills width when possible
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)  # clip on narrow widths
        # Optional: prevents inner controls from being squeezed
        page.setMinimumSize(page.sizeHint())
        scroll.setWidget(page)
        return scroll
    
    def create_general_tab(self):
        general_widget = QWidget()
        layout = QVBoxLayout(general_widget)

        # Behavior
        behavior_group = QGroupBox("Behavior")
        behavior_layout = QFormLayout(behavior_group)
        self.delete_empty_folders_checkbox = QCheckBox("Delete Empty Folders When Deleting Subtitle Files")
        behavior_layout.addRow(self.delete_empty_folders_checkbox)
        self.preview_mode_checkbox = QCheckBox("Enable Preview Mode")
        behavior_layout.addRow(self.preview_mode_checkbox)
        layout.addWidget(behavior_group)

        # Preferences
        preferences_group = QGroupBox("Naming Preference")
        preference_layout = QFormLayout(preferences_group)
        self.auto_run_checkbox = QCheckBox("Auto-Run Renaming")
        preference_layout.addRow(self.auto_run_checkbox)
        self.use_default_tag_checkbox = QCheckBox("Auto-Apply Detected Group Suffix")
        preference_layout.addRow(self.use_default_tag_checkbox)
        self.always_prompt_tag_checkbox = QCheckBox("Always Ask for Group Suffix")
        preference_layout.addRow(self.always_prompt_tag_checkbox)
        self.cache_per_set_checkbox = QCheckBox("Use This for All Files of the Same Group")
        preference_layout.addRow(self.cache_per_set_checkbox)
        layout.addWidget(preferences_group)

        # Conflict policy
        conflict_policy_group = QGroupBox("Name Conflict Handling")
        form_layout = QFormLayout(conflict_policy_group)
        self.conflict_policy_combo = PopupStyledComboBox()
        self.conflict_policy_combo.addItems(["Ask", "Skip", "Overwrite", "Keep Both"])
        self.conflict_policy_combo.setToolTip(
            "Ask: prompt for each conflict\n"
            "Skip: leave existing file untouched\n"
            "Overwrite: replace existing (recycle bin)\n"
            "Keep both: add numbered suffix"
        )
        form_layout.addRow("If Target Name Exists:", self.conflict_policy_combo)
        self.apply_all_conflicts_checkbox = QCheckBox("Use This for All Conflicts of the Same Group")
        form_layout.addRow(self.apply_all_conflicts_checkbox)
        layout.addWidget(conflict_policy_group)

        # Language suffix
        lang_group = QGroupBox("Language")
        lang_layout = QFormLayout(lang_group)
        self.group_suffix_checkbox = QCheckBox("Append Group Name to Renamed File")
        self.group_suffix_checkbox.setToolTip(
            "When enabled, the detected studio/group name is included as a suffix.\ne.g.  VideoName.Group.ass"
        )
        lang_layout.addRow(self.group_suffix_checkbox)
        self.lang_suffix_checkbox = QCheckBox("Append Language Suffix to Renamed File")
        self.lang_suffix_checkbox.setToolTip(
            "When enabled, the detected language code is appended.\ne.g.  VideoName.Group.lang.ass\ne.g.  VideoName.lang.ass"
        )
        lang_layout.addRow(self.lang_suffix_checkbox)
        self.unknown_lang_combo = PopupStyledComboBox()
        self.unknown_lang_combo.addItems([
            "Append",
            "Skip",
        ])
        self.unknown_lang_combo.setToolTip(
            "For languages not in the map:\nAppend: append detected language code\nSkip: don't append any language code"
        )
        lang_layout.addRow("Unknown Language:", self.unknown_lang_combo)
        self.langmap_editor = QTextEdit()
        self.langmap_editor.setMinimumHeight(120)
        # self.langmap_editor.setMaximumHeight(200)
        lang_layout.addRow("Language Map:", self.langmap_editor)
        self.langmap_reset_btn = QPushButton("Reset to Defaults")
        self.langmap_reset_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.langmap_reset_btn.clicked.connect(self.reset_langmap)
        lang_layout.addRow("", self.langmap_reset_btn)
        layout.addWidget(lang_group)

        data_group = QGroupBox("Data")
        data_layout = QVBoxLayout(data_group)

        self.open_user_data_btn = QPushButton("Open User Data Folder")
        self.open_user_data_btn.clicked.connect(self.open_user_data_folder_from_settings)
        data_layout.addWidget(self.open_user_data_btn)
        layout.addWidget(data_group)

        layout.addStretch()
        self.stacked_widget.addWidget(self._wrap_settings_page(general_widget))
    
    def open_user_data_folder_from_settings(self):
        p = self.parent()
        if p and hasattr(p, "open_user_data_folder"):
            p.open_user_data_folder()

    def reset_langmap(self):
        """Reset language map editor to built-in defaults."""
        self.langmap_editor.setPlainText(sr.DEFAULT_LANG_MAP_TEXT.strip())
        self.mark_changed()

    def _make_manage_column(self, button: QPushButton) -> QWidget:
        col = QVBoxLayout()
        # Match QGroupBox border top offset from stylesheet margin-top (~7 * zoom)
        col.setContentsMargins(0, int(7 * (get_zoom_level() / 100.0)), 0, 0)
        col.setSpacing(0)
        col.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        col.addStretch()
        wrap = QWidget()
        wrap.setLayout(col)
        return wrap

    def create_extensions_tab(self):
        extensions_widget = QWidget()
        page_layout = QVBoxLayout(extensions_widget)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(12)

        # Row 1: Video
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)
        row1.setSpacing(10)

        video_group = QGroupBox("Video Extensions")
        video_layout = QVBoxLayout(video_group)
        video_scroll = QScrollArea()
        video_scroll.setWidgetResizable(True)
        video_content = QWidget()
        self.video_extensions_layout = QVBoxLayout(video_content)
        self.video_checkboxes = {}

        for ext in get_all_video_extensions():
            checkbox = QCheckBox(ext)
            checkbox.toggled.connect(self.mark_changed)
            self.video_checkboxes[ext] = checkbox
            self.video_extensions_layout.addWidget(checkbox)

        video_scroll.setWidget(video_content)
        video_layout.addWidget(video_scroll)
        row1.addWidget(video_group, 1)

        self.manage_video_btn = QPushButton("Manage")
        self.manage_video_btn.clicked.connect(lambda: self.open_manage_dialog("video"))
        self.manage_video_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row1.addWidget(self._make_manage_column(self.manage_video_btn), 0, Qt.AlignmentFlag.AlignTop)

        # Row 2: Subtitle
        row2 = QHBoxLayout()
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(10)

        subtitle_group = QGroupBox("Subtitle Extensions")
        subtitle_layout = QVBoxLayout(subtitle_group)
        subtitle_scroll = QScrollArea()
        subtitle_scroll.setWidgetResizable(True)
        subtitle_content = QWidget()
        self.subtitle_extensions_layout = QVBoxLayout(subtitle_content)
        self.subtitle_checkboxes = {}

        for ext in get_all_subtitle_extensions():
            checkbox = QCheckBox(ext)
            checkbox.toggled.connect(self.mark_changed)
            self.subtitle_checkboxes[ext] = checkbox
            self.subtitle_extensions_layout.addWidget(checkbox)

        subtitle_scroll.setWidget(subtitle_content)
        subtitle_layout.addWidget(subtitle_scroll)
        row2.addWidget(subtitle_group, 1)

        self.manage_subtitle_btn = QPushButton("Manage")
        self.manage_subtitle_btn.clicked.connect(lambda: self.open_manage_dialog("subtitle"))
        self.manage_subtitle_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        row2.addWidget(self._make_manage_column(self.manage_subtitle_btn), 0, Qt.AlignmentFlag.AlignTop)

        page_layout.addLayout(row1)
        page_layout.addLayout(row2)
        page_layout.addStretch()
        self.stacked_widget.addWidget(extensions_widget)
    
    def create_view_tab(self):
        view_widget = QWidget()
        layout = QVBoxLayout(view_widget)

        # Theme
        theme_group = QGroupBox("Theme")
        theme_layout = QFormLayout(theme_group)
        self.theme_combo = PopupStyledComboBox()
        self.theme_combo.addItems(["Light", "Dark"])
        theme_layout.addRow("Appearance:", self.theme_combo)
        layout.addWidget(theme_group)

        # Layout & visibility
        layout_group = QGroupBox("Layout")
        layout_form = QFormLayout(layout_group)
        self.show_video_table_checkbox = QCheckBox("Show Video Table")
        layout_form.addRow(self.show_video_table_checkbox)
        self.show_preview_name_checkbox = QCheckBox("Show Preview Name Column")
        layout_form.addRow(self.show_preview_name_checkbox)
        self.show_preview_status_checkbox = QCheckBox("Show Preview Status Column")
        layout_form.addRow(self.show_preview_status_checkbox)
        self.show_log_checkbox = QCheckBox("Show Log Box")
        layout_form.addRow(self.show_log_checkbox)
        self.show_switch_bar_checkbox = QCheckBox("Show Switch Bar")
        layout_form.addRow(self.show_switch_bar_checkbox)
        self.compact_mode_checkbox = QCheckBox("Compact Mode")
        layout_form.addRow(self.compact_mode_checkbox)
        layout.addWidget(layout_group)

        # Log message types
        log_group = QGroupBox("Log Message Types")
        log_layout = QVBoxLayout(log_group)
        self.show_info_messages_checkbox = QCheckBox("Info Messages")
        self.show_success_messages_checkbox = QCheckBox("Success Messages")
        self.show_warning_messages_checkbox = QCheckBox("Warning Messages")
        self.show_error_messages_checkbox = QCheckBox("Error Messages")
        log_layout.addWidget(self.show_info_messages_checkbox)
        log_layout.addWidget(self.show_success_messages_checkbox)
        log_layout.addWidget(self.show_warning_messages_checkbox)
        log_layout.addWidget(self.show_error_messages_checkbox)
        layout.addWidget(log_group)

        layout.addStretch()
        self.stacked_widget.addWidget(self._wrap_settings_page(view_widget))
    
    def switch_tab(self, index):
        self.stacked_widget.setCurrentIndex(index)
    
    def load_settings(self):
        """Load current settings into the dialog"""
        settings = load_settings()
        self.auto_run_checkbox.setChecked(settings.get("auto_run", False))
        self.use_default_tag_checkbox.setChecked(settings.get("use_default_tag_if_found", False))
        self.always_prompt_tag_checkbox.setChecked(settings.get("always_prompt_tag_always", True))
        self.cache_per_set_checkbox.setChecked(settings.get("cache_per_set", True))
        self.apply_all_conflicts_checkbox.setChecked(settings.get("apply_all_conflicts", False))
        policy = settings.get("conflict_policy", "ASK")
        policy_map = {"ASK": 0, "SKIP": 1, "OVERWRITE": 2, "SUFFIX": 3}
        self.conflict_policy_combo.setCurrentIndex(policy_map.get(policy, 0))
        self.preview_mode_checkbox.setChecked(settings.get("preview_mode", True))
        self.delete_empty_folders_checkbox.setChecked(settings.get("delete_empty_folders", False))

        # Language settings
        self.group_suffix_checkbox.setChecked(settings.get("group_suffix_enabled", True))
        self.lang_suffix_checkbox.setChecked(settings.get("lang_suffix_enabled", False))
        ula = settings.get("unknown_lang_action", "append")
        ula_map = {"append": 0, "skip": 1}
        self.unknown_lang_combo.setCurrentIndex(ula_map.get(ula, 0))
        self.langmap_editor.setPlainText(sr.serialize_lang_map(sr.LANG_MAP).strip())

        # View tab
        self.theme_combo.setCurrentIndex(1 if settings.get("dark_mode", True) else 0)
        self.show_video_table_checkbox.setChecked(settings.get("show_video_table", True))
        self.show_log_checkbox.setChecked(settings.get("show_log", True))
        self.show_switch_bar_checkbox.setChecked(settings.get("show_switch_bar", True))
        preview_mode = settings.get("preview_mode", True)
        self.show_preview_name_checkbox.setChecked(settings.get("show_preview_name_column", preview_mode))
        self.show_preview_status_checkbox.setChecked(settings.get("show_preview_status_column", preview_mode))
        self.compact_mode_checkbox.setChecked(settings.get("compact_mode", False))
        self.show_info_messages_checkbox.setChecked(settings.get("show_info_messages", True))
        self.show_success_messages_checkbox.setChecked(settings.get("show_success_messages", True))
        self.show_warning_messages_checkbox.setChecked(settings.get("show_warning_messages", True))
        self.show_error_messages_checkbox.setChecked(settings.get("show_error_messages", True))
        
        # Load extension settings
        enabled_video = get_enabled_dst_ext()
        enabled_subtitle = get_enabled_src_ext()
        
        for ext, checkbox in self.video_checkboxes.items():
            checkbox.setChecked(ext in enabled_video)
        
        for ext, checkbox in self.subtitle_checkboxes.items():
            checkbox.setChecked(ext in enabled_subtitle)

    def store_original_settings(self):
        """Store original settings for comparison."""
        self.original_settings = {
            'auto_run': self.auto_run_checkbox.isChecked(),
            'use_default_tag': self.use_default_tag_checkbox.isChecked(),
            'always_prompt_tag': self.always_prompt_tag_checkbox.isChecked(),
            'cache_per_set': self.cache_per_set_checkbox.isChecked(),
            'apply_all_conflicts': self.apply_all_conflicts_checkbox.isChecked(),
            'conflict_policy': ["ASK", "SKIP", "OVERWRITE", "SUFFIX"][self.conflict_policy_combo.currentIndex()],
            'preview_mode': self.preview_mode_checkbox.isChecked(),
            'show_preview_name_column': self.show_preview_name_checkbox.isChecked(),
            'show_preview_status_column': self.show_preview_status_checkbox.isChecked(),
            'dark_mode': self.theme_combo.currentIndex() == 1,
            'show_video_table': self.show_video_table_checkbox.isChecked(),
            'show_log': self.show_log_checkbox.isChecked(),
            'show_switch_bar': self.show_switch_bar_checkbox.isChecked(),
            'compact_mode': self.compact_mode_checkbox.isChecked(),
            'show_info_messages': self.show_info_messages_checkbox.isChecked(),
            'show_success_messages': self.show_success_messages_checkbox.isChecked(),
            'show_warning_messages': self.show_warning_messages_checkbox.isChecked(),
            'show_error_messages': self.show_error_messages_checkbox.isChecked(),
            'group_suffix_enabled': self.group_suffix_checkbox.isChecked(),
            'lang_suffix_enabled': self.lang_suffix_checkbox.isChecked(),
            'unknown_lang_action': ["append", "skip"][self.unknown_lang_combo.currentIndex()],
            'langmap_text': self.langmap_editor.toPlainText(),

            'enabled_video': set(get_enabled_dst_ext()),
            'enabled_subtitle': set(get_enabled_src_ext()),
            'custom_video': set(get_custom_video_extensions()),
            'custom_subtitle': set(get_custom_subtitle_extensions()),
            'disabled_video': set(get_disabled_builtin_video_extensions()),
            'disabled_subtitle': set(get_disabled_builtin_subtitle_extensions())
        }
        
        # Initialize cache with current settings
        self.cached_extension_changes = {
            'custom_video': list(get_custom_video_extensions()),
            'custom_subtitle': list(get_custom_subtitle_extensions()),
            'disabled_video': list(get_disabled_builtin_video_extensions()),
            'disabled_subtitle': list(get_disabled_builtin_subtitle_extensions()),
            'enabled_video': list(get_enabled_dst_ext()),
            'enabled_subtitle': list(get_enabled_src_ext())
        }

    def connect_change_signals(self):
        """Connect signals to track changes."""
        self.auto_run_checkbox.toggled.connect(self.mark_changed)
        self.use_default_tag_checkbox.toggled.connect(self.mark_changed)
        self.always_prompt_tag_checkbox.toggled.connect(self.mark_changed)
        self.cache_per_set_checkbox.toggled.connect(self.mark_changed)
        self.conflict_policy_combo.currentIndexChanged.connect(self.mark_changed)
        self.preview_mode_checkbox.toggled.connect(self.on_preview_mode_toggled)
        self.theme_combo.currentIndexChanged.connect(self.mark_changed)
        self.show_video_table_checkbox.toggled.connect(self.mark_changed)
        self.show_log_checkbox.toggled.connect(self.mark_changed)
        self.show_switch_bar_checkbox.toggled.connect(self.mark_changed)
        self.show_preview_name_checkbox.toggled.connect(self.mark_changed)
        self.show_preview_status_checkbox.toggled.connect(self.mark_changed)
        self.compact_mode_checkbox.toggled.connect(self.mark_changed)
        self.show_info_messages_checkbox.toggled.connect(self.mark_changed)
        self.show_success_messages_checkbox.toggled.connect(self.mark_changed)
        self.show_warning_messages_checkbox.toggled.connect(self.mark_changed)
        self.show_error_messages_checkbox.toggled.connect(self.mark_changed)
        self.group_suffix_checkbox.toggled.connect(self.mark_changed)
        self.lang_suffix_checkbox.toggled.connect(self.mark_changed)
        self.unknown_lang_combo.currentIndexChanged.connect(self.mark_changed)
        self.langmap_editor.textChanged.connect(self.mark_changed)

        self.connect_extension_checkbox_signals()

    def on_preview_mode_toggled(self, checked: bool):
        """When preview mode is toggled in the dialog, mirror it to the preview column checkboxes."""
        self.show_preview_name_checkbox.setChecked(checked)
        self.show_preview_status_checkbox.setChecked(checked)
        self.mark_changed()

    def connect_extension_checkbox_signals(self):
        """Connect signals for extension checkboxes."""
        for checkbox in self.video_checkboxes.values():
            checkbox.toggled.connect(self.on_extension_checkbox_changed)
        for checkbox in self.subtitle_checkboxes.values():
            checkbox.toggled.connect(self.on_extension_checkbox_changed)

    def on_extension_checkbox_changed(self):
        """Handle extension checkbox changes and update cache."""
        self.cached_extension_changes['enabled_video'] = [ext for ext, checkbox in self.video_checkboxes.items() if checkbox.isChecked()]
        self.cached_extension_changes['enabled_subtitle'] = [ext for ext, checkbox in self.subtitle_checkboxes.items() if checkbox.isChecked()]
        self.mark_changed()

    def mark_changed(self):
        self.has_unsaved_changes = True

    def on_reject_clicked(self):
        if self.has_unsaved_changes:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Do you want to close without saving?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.reject()
        else:
            self.reject()

    def closeEvent(self, event):
        if self.has_unsaved_changes:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Do you want to close without saving?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            event.accept() if reply == QMessageBox.StandardButton.Yes else event.ignore()
        else:
            event.accept()

    def on_accept_clicked(self):
        self.save_current_settings()
        self.accept()

    def save_current_settings(self):
        """Save current settings to storage."""
        settings = load_settings()
        settings["auto_run"] = self.auto_run_checkbox.isChecked()
        settings["use_default_tag_if_found"] = self.use_default_tag_checkbox.isChecked()
        settings["always_prompt_tag_always"] = self.always_prompt_tag_checkbox.isChecked()
        settings["cache_per_set"] = self.cache_per_set_checkbox.isChecked()
        settings["apply_all_conflicts"] = self.apply_all_conflicts_checkbox.isChecked()
        settings["conflict_policy"] = ["ASK", "SKIP", "OVERWRITE", "SUFFIX"][self.conflict_policy_combo.currentIndex()]
        settings["preview_mode"] = self.preview_mode_checkbox.isChecked()
        settings["show_preview_name_column"] = self.show_preview_name_checkbox.isChecked()
        settings["show_preview_status_column"] = self.show_preview_status_checkbox.isChecked()
        settings["delete_empty_folders"] = self.delete_empty_folders_checkbox.isChecked()
        settings["dark_mode"] = self.theme_combo.currentIndex() == 1
        settings["show_video_table"] = self.show_video_table_checkbox.isChecked()
        settings["show_log"] = self.show_log_checkbox.isChecked()
        settings["show_switch_bar"] = self.show_switch_bar_checkbox.isChecked()
        settings["compact_mode"] = self.compact_mode_checkbox.isChecked()
        settings["show_info_messages"] = self.show_info_messages_checkbox.isChecked()
        settings["show_success_messages"] = self.show_success_messages_checkbox.isChecked()
        settings["show_warning_messages"] = self.show_warning_messages_checkbox.isChecked()
        settings["show_error_messages"] = self.show_error_messages_checkbox.isChecked()
        settings["group_suffix_enabled"] = self.group_suffix_checkbox.isChecked()
        settings["lang_suffix_enabled"] = self.lang_suffix_checkbox.isChecked()
        settings["unknown_lang_action"] = ["append", "skip"][self.unknown_lang_combo.currentIndex()]

        save_settings(settings)

        # Save langmap editor contents to file and reload
        langmap_text = self.langmap_editor.toPlainText().strip()
        if langmap_text:
            test_map, _ = sr.parse_lang_map_text(langmap_text)
            if not test_map:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self, "Invalid Language Map",
                    "Could not parse the language map.\n"
                    "Please check the format: OUTPUT = alias1, alias2, ...",
                )
                return
            try:
                os.makedirs(os.path.dirname(sr.LANGMAP_FILE), exist_ok=True)
                with open(sr.LANGMAP_FILE, 'w', encoding='utf-8') as f:
                    f.write(langmap_text + '\n')
                sr.reload_lang_map()
            except Exception as e:
                logging.warning(f"Failed to save langmap: {e}")

        # Save cached extension settings
        set_custom_video_extensions(self.cached_extension_changes['custom_video'])
        set_custom_subtitle_extensions(self.cached_extension_changes['custom_subtitle'])
        set_disabled_builtin_video_extensions(self.cached_extension_changes['disabled_video'])
        set_disabled_builtin_subtitle_extensions(self.cached_extension_changes['disabled_subtitle'])
        set_enabled_video_extensions(self.cached_extension_changes['enabled_video'])
        set_enabled_subtitle_extensions(self.cached_extension_changes['enabled_subtitle'])
        
        self.has_unsaved_changes = False

    def normalize_ext(self, ext: str) -> str:
        ext = ext.strip().lower()
        if not ext:
            return ""
        if not ext.startswith('.'):
            ext = '.' + ext
        return ext

    def open_manage_dialog(self, file_type: str):
        """Open a Manage dialog for 'video' or 'subtitle' extensions, allowing add/remove with confirmation."""
        is_video = (file_type == 'video')
        title = "Manage Video Extensions" if is_video else "Manage Subtitle Extensions"
        all_exts = self.get_all_video_extensions() if is_video else self.get_all_subtitle_extensions()
        custom_exts = set(self.cached_extension_changes['custom_video'] if is_video else self.cached_extension_changes['custom_subtitle'])

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(420, 480)
        v = QVBoxLayout(dlg)
        info = QLabel("Check extensions to remove. Custom extensions are marked with '(custom)'.")
        v.addWidget(info)
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(get_drop_area_frame_style(self.parent().current_theme if hasattr(self.parent(), 'current_theme') else (DARK_THEME if get_theme() else LIGHT_THEME)))
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(0, 0, 0, 0)
        frame_layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(4)
        # Build list of checkboxes, mark custom ones, all extensions are selectable for removal
        cb_map = {}
        for ext in all_exts:
            label = f"{ext} (custom)" if ext in custom_exts else ext
            cb = QCheckBox(label)
            cb.setChecked(False)
            cb.setEnabled(True)
            cb_map[ext] = cb
            content_layout.addWidget(cb)
        content_layout.addStretch()
        scroll.setWidget(content)
        frame_layout.addWidget(scroll)
        v.addWidget(frame)
        
        # Controls row
        controls = QHBoxLayout()
        add_btn = QPushButton("Add…")
        remove_btn = QPushButton("Remove Selected")
        default_btn = QPushButton("Restore Defaults")
        default_btn.setToolTip("Restore built-in extensions and delete customs")
        controls.addWidget(add_btn)
        controls.addWidget(remove_btn)
        controls.addWidget(default_btn)
        controls.addStretch()
        v.addLayout(controls)
        
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        v.addWidget(buttons)
        
        def do_add():
            if is_video:
                self.add_custom_video_extension()
            else:
                self.add_custom_subtitle_extension()
            self.refresh_manage_dialog_list(dlg, is_video, cb_map, content_layout)
            self.refresh_settings_extensions_list()
            self.mark_changed()
        
        def do_remove():
            to_remove = [ext for ext in cb_map.keys() if cb_map[ext].isChecked()]
            if not to_remove:
                QMessageBox.information(dlg, "Remove Extensions", "No extensions selected to remove.")
                return
            confirm = QMessageBox.question(
                dlg,
                "Confirm Removal",
                "Remove the following extensions?\n\n" + ", ".join(to_remove),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            if is_video:
                self.remove_custom_video_extension(to_remove=to_remove)
            else:
                self.remove_custom_subtitle_extension(to_remove=to_remove)
            dlg.close()
            self.refresh_settings_extensions_list()
            self.mark_changed()
        
        def do_restore_defaults():
            """Restore all built-in extensions and set default enabled ones."""
            confirm = QMessageBox.question(
                dlg,
                "Restore Defaults",
                "Are you sure? This will restore all default extensions and delete all customs.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return

            if is_video:
                self.cached_extension_changes['custom_video'] = []
                self.cached_extension_changes['disabled_video'] = []
                self.cached_extension_changes['enabled_video'] = ['.avi', '.mkv', '.mov', '.mp4', '.webm', '.wmv']
            else:
                self.cached_extension_changes['custom_subtitle'] = []
                self.cached_extension_changes['disabled_subtitle'] = []
                self.cached_extension_changes['enabled_subtitle'] = ['.ass', '.srt', '.ssa', '.sub']
            self.refresh_manage_dialog_list(dlg, is_video, cb_map, content_layout)
            self.refresh_settings_extensions_list()
            self.mark_changed()
        
        add_btn.clicked.connect(do_add)
        remove_btn.clicked.connect(do_remove)
        default_btn.clicked.connect(do_restore_defaults)
        buttons.rejected.connect(dlg.reject)
        buttons.clicked.connect(lambda btn: dlg.reject())
        dlg.exec()

    def refresh_manage_dialog_list(self, dlg, is_video, cb_map, content_layout):
        # Clear existing checkboxes
        for cb in cb_map.values():
            cb.setParent(None)
            cb.deleteLater()
        cb_map.clear()
        
        # Rebuild the list with cached data
        all_exts = self.get_all_video_extensions() if is_video else self.get_all_subtitle_extensions()
        custom_exts = set(self.cached_extension_changes['custom_video'] if is_video else self.cached_extension_changes['custom_subtitle'])
        
        for ext in all_exts:
            label = f"{ext} (custom)" if ext in custom_exts else ext
            cb = QCheckBox(label)
            cb.setChecked(False)
            cb.setEnabled(True)
            cb_map[ext] = cb
            content_layout.addWidget(cb)

    def refresh_settings_extensions_list(self):
        # Clear existing checkboxes
        for cb in self.video_checkboxes.values():
            cb.setParent(None)
            cb.deleteLater()
        self.video_checkboxes.clear()

        for cb in self.subtitle_checkboxes.values():
            cb.setParent(None)
            cb.deleteLater()
        self.subtitle_checkboxes.clear()
        
        # Rebuild extensions list
        all_video_exts = self.get_all_video_extensions()
        for ext in all_video_exts:
            checkbox = QCheckBox(ext)
            self.video_checkboxes[ext] = checkbox
            self.video_extensions_layout.addWidget(checkbox)

        all_subtitle_exts = self.get_all_subtitle_extensions()
        for ext in all_subtitle_exts:
            checkbox = QCheckBox(ext)
            self.subtitle_checkboxes[ext] = checkbox
            self.subtitle_extensions_layout.addWidget(checkbox)
        
        # Set checked states based on cached enabled extensions
        for ext, checkbox in self.video_checkboxes.items():
            checkbox.setChecked(ext in self.cached_extension_changes['enabled_video'])
        for ext, checkbox in self.subtitle_checkboxes.items():
            checkbox.setChecked(ext in self.cached_extension_changes['enabled_subtitle'])
        self.connect_extension_checkbox_signals()
    
    def get_auto_run(self):
        return self.auto_run_checkbox.isChecked()
    
    def get_use_default_tag(self):
        return self.use_default_tag_checkbox.isChecked()
    
    def get_always_prompt_tag(self):
        return self.always_prompt_tag_checkbox.isChecked()
    
    def get_cache_per_set(self):
        return self.cache_per_set_checkbox.isChecked()
    
    def get_apply_all_conflicts(self):
        return self.apply_all_conflicts_checkbox.isChecked()
    
    def get_conflict_policy(self):
        return ["ASK", "SKIP", "OVERWRITE", "SUFFIX"][self.conflict_policy_combo.currentIndex()]
    
    def get_preview_mode(self):
        return self.preview_mode_checkbox.isChecked()
    
    def get_delete_empty_folders(self):
        return self.delete_empty_folders_checkbox.isChecked()
    
    def get_enabled_dst_ext(self):
        return [ext for ext, checkbox in self.video_checkboxes.items() if checkbox.isChecked()]
    
    def get_enabled_src_ext(self):
        return [ext for ext, checkbox in self.subtitle_checkboxes.items() if checkbox.isChecked()]

    def get_all_video_extensions(self):
        disabled = set(self.cached_extension_changes['disabled_video'])
        available_builtin = [ext for ext in VIDEO_EXTENSIONS if ext not in disabled]
        return sorted(set(available_builtin + self.cached_extension_changes['custom_video']))

    def get_all_subtitle_extensions(self):
        disabled = set(self.cached_extension_changes['disabled_subtitle'])
        available_builtin = [ext for ext in SUBTITLE_EXTENSIONS if ext not in disabled]
        return sorted(set(available_builtin + self.cached_extension_changes['custom_subtitle']))

    def add_custom_video_extension(self):
        text, ok = QInputDialog.getText(self, "Add Video Extension", "Enter a video extension (e.g. .mkv or mkv):")
        if not ok:
            return
        ext = self.normalize_ext(text)
        if not ext:
            return
        if ext not in self.cached_extension_changes['custom_video']:
            self.cached_extension_changes['custom_video'].append(ext)
            self.cached_extension_changes['custom_video'].sort()

    def add_custom_subtitle_extension(self):
        text, ok = QInputDialog.getText(self, "Add Subtitle Extension", "Enter a subtitle extension (e.g. .ass or ass):")
        if not ok:
            return
        ext = self.normalize_ext(text)
        if not ext:
            return
        if ext not in self.cached_extension_changes['custom_subtitle']:
            self.cached_extension_changes['custom_subtitle'].append(ext)
            self.cached_extension_changes['custom_subtitle'].sort()

    def remove_custom_video_extension(self, to_remove):
        custom = set(self.cached_extension_changes['custom_video'])
        builtin = set(VIDEO_EXTENSIONS)
        
        # Handle custom extensions
        custom_to_remove = [ext for ext in to_remove if ext in custom]
        for ext in custom_to_remove:
            self.cached_extension_changes['custom_video'].remove(ext)
        
        # Handle built-in extensions
        builtin_to_disable = [ext for ext in to_remove if ext in builtin and ext not in custom]
        for ext in builtin_to_disable:
            if ext not in self.cached_extension_changes['disabled_video']:
                self.cached_extension_changes['disabled_video'].append(ext)
        
        # Remove from enabled list
        for ext in to_remove:
            if ext in self.cached_extension_changes['enabled_video']:
                self.cached_extension_changes['enabled_video'].remove(ext)

    def remove_custom_subtitle_extension(self, to_remove):
        custom = set(self.cached_extension_changes['custom_subtitle'])
        builtin = set(SUBTITLE_EXTENSIONS)
        
        # Handle custom extensions
        custom_to_remove = [ext for ext in to_remove if ext in custom]
        for ext in custom_to_remove:
            self.cached_extension_changes['custom_subtitle'].remove(ext)
        
        # Handle built-in extensions
        builtin_to_disable = [ext for ext in to_remove if ext in builtin and ext not in custom]
        for ext in builtin_to_disable:
            if ext not in self.cached_extension_changes['disabled_subtitle']:
                self.cached_extension_changes['disabled_subtitle'].append(ext)
        
        # Remove from enabled list
        for ext in to_remove:
            if ext in self.cached_extension_changes['enabled_subtitle']:
                self.cached_extension_changes['enabled_subtitle'].remove(ext)

def get_recent_target_folders():
    settings = load_settings()
    recent_folders = settings.get("recent_target_folders", [])
    valid_folders = [folder for folder in recent_folders if os.path.exists(folder)]  # Filter out invalid paths
    if len(valid_folders) != len(recent_folders):
        settings["recent_target_folders"] = valid_folders
        save_settings(settings)
    return valid_folders

def add_recent_target_folder(folder):
    if not folder or not os.path.exists(folder):
        return
    settings = load_settings()
    recent_folders = settings.get("recent_target_folders", [])
    
    if folder in recent_folders:
        recent_folders.remove(folder)  # Remove the folder if it already exists
    recent_folders.insert(0, folder)
    recent_folders = recent_folders[:10]  # Keep the last 10 folders only
    
    settings["recent_target_folders"] = recent_folders
    save_settings(settings)

def remove_recent_target_folder(folder):
    settings = load_settings()
    recent_folders = settings.get("recent_target_folders", [])
    if folder in recent_folders:
        recent_folders.remove(folder)
        settings["recent_target_folders"] = recent_folders
        save_settings(settings)

def clear_recent_target_folders():
    settings = load_settings()
    settings["recent_target_folders"] = []
    save_settings(settings)


class MainWindow(QWidget):
    log_signal = pyqtSignal(str, str)  # Signal for log messages with category (message, category)
    status_update_signal = pyqtSignal(dict)  # Signal for updating status
    job_completed_signal = pyqtSignal()  # Signal for job completion
    preview_update_signal = pyqtSignal(list)  # Signal for updating preview in table
    plugin_message_signal = pyqtSignal(str, str, str)  # (message, title, msg_type) — thread-safe plugin dialogs
    plugin_theme_signal = pyqtSignal(object)  # QWidget — thread-safe theme application
    shutdown_signal = pyqtSignal()  # Emitted during app close so plugins can stop workers

    def __init__(self):
        super().__init__()
        self._is_closing = False
        self.setWindowTitle("SubApp")
        if APP_ICON is not None:
            self.setWindowIcon(APP_ICON)
        self.target_folder = None
        self.selected_files = []
        self.src_ext = sr.DEFAULT_SRC_EXT
        self.dst_ext = sr.DEFAULT_DST_EXT
        self.cust_ext = sr.DEFAULT_TAG
        # Subtitle tracking
        self.subtitle_status = {}  # file_path: "pending", "success", "failed", "skipped"
        self.preview_conflict_decisions = {}  # source_path -> {"status", "new_name"}
        self.adding_orphaned_files = False  # Flag to prevent double preview triggers
        self.rename_in_place_paths = set()  # Orphaned paths marked for in-place rename
        settings = load_settings()
        self.current_theme = DARK_THEME if settings.get("dark_mode", True) else LIGHT_THEME

        self.init_ui()
        self.log_signal.connect(self.append_log)
        self.status_update_signal.connect(self.update_status_from_signal)
        self.job_completed_signal.connect(self.on_job_completed)
        self.preview_update_signal.connect(self.update_preview_in_table)
        self.plugin_message_signal.connect(self._show_plugin_message)
        self.plugin_theme_signal.connect(self._apply_theme_to_plugin_widget)

        # initialize plugin maps once
        self.plugin_pages = {}  # runtime_key -> [(page_name, widget, tab_index), ...]
        self.plugin_tabs = {}   # tab_index -> widget

        self.apply_theme()  # Apply initial theme with current zoom level
        self.restore_window_geometry()
        self.restore_splitter_sizes()
        
        self._load_plugins()  # Load plugins after UI initialization
        
        prefs = load_settings()  # Apply initial visibility preferences for switch bar, video table and log

        # Log switch bar
        show_bar = prefs.get("show_switch_bar", True)
        self.log_switch_bar.setVisible(show_bar)
        self.toggle_log_switcher_action.setChecked(show_bar)

        initial_zoom = get_zoom_level()
        if initial_zoom != 100:
            self.apply_zoom(initial_zoom)

        QTimer.singleShot(0, lambda: (
            self.show_video_table_action.setChecked(prefs.get("show_video_table", True)),
            self.show_log_action.setChecked(prefs.get("show_log", True)),
            self.preview_mode_action.setChecked(prefs.get("preview_mode", True)),
            self.show_preview_name_action.setChecked(prefs.get("show_preview_name_column", prefs.get("preview_mode", True))),
            self.show_preview_status_action.setChecked(prefs.get("show_preview_status_column", prefs.get("preview_mode", True))),
            (self.toggle_video_table() if not prefs.get("show_video_table", True) else None),
            (self.toggle_log()        if not prefs.get("show_log", True)        else None),
            self.apply_preview_visibility()
        ))

    def get_current_video_files(self):
        """Return a list of full paths for videos currently shown in the video table."""
        files = []
        table = self.video_drop_area.table
        for row in range(table.rowCount()):
            name_item = table.item(row, 0)
            if name_item:
                tooltip = name_item.toolTip()  # display_files sets tooltip to full path
                if tooltip:
                    files.append(tooltip)
                elif self.target_folder:
                    files.append(os.path.join(self.target_folder, name_item.text()))
        return files

    def init_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)

        # Menu Bar
        self.menu_bar = QMenuBar(self)
        self.file_menu = AdaptiveRoundedMenu("File", self)
        self.view_menu = AdaptiveRoundedMenu("View", self)
        self.tools_menu = AdaptiveRoundedMenu("Tools", self)
        self.settings_menu = AdaptiveRoundedMenu("Preferences", self)
        self.help_menu = AdaptiveRoundedMenu("Help", self)
        self.menu_bar.addMenu(self.file_menu)
        self.menu_bar.addMenu(self.view_menu)
        self.menu_bar.addMenu(self.tools_menu)
        self.menu_bar.addMenu(self.settings_menu)
        self.menu_bar.addMenu(self.help_menu)
        layout.setMenuBar(self.menu_bar)

        # Optional top switch bar to switch between Main and Log
        self.log_switch_bar = QTabBar()
        self.log_switch_bar.addTab("Main")
        self.log_switch_bar.addTab("Log")
        self.log_switch_bar.setExpanding(False)
        self.log_switch_bar.setDrawBase(True)
        self.log_switch_bar.hide()  # Hidden by default unless enabled via View > Log
        layout.addWidget(self.log_switch_bar)
        self.log_switch_bar.currentChanged.connect(self.on_log_switch_changed)

        # Central stacked widget: page 0 = Main UI, page 1 = Log file view
        self.stacked = QStackedWidget()
        layout.addWidget(self.stacked)

        self.setup_file_menu()
        self.setup_view_menu()
        self.setup_tools_menu()
        self.setup_settings_menu()
        self.setup_help_menu()

        # Video files drop area
        self.video_drop_area = VideoDropArea(self)
        self.video_drop_area.table.selectionModel().selectionChanged.connect(self.update_video_remove_button_text)
        self.video_table_locked = False  # Prevent auto-repopulate after manual edits

        # Target folder controls
        target_row = QHBoxLayout()
        vid_grp = QHBoxLayout()
        vid_grp.setSpacing(1)
        self.video_format_label = QLabel("<b>Video Format (Destination):</b>")
        vid_grp.addWidget(self.video_format_label)
        self.dst_edit = PopupStyledComboBox()
        self.dst_edit.setEditable(False)
        self.dst_edit.addItems(['Auto', 'All'] + get_enabled_dst_ext())
        self.dst_edit.setCurrentText(get_last_dst_format())
        self.dst_edit.setMinimumWidth(80)
        self.dst_edit.setMaximumWidth(120)
        self.dst_edit.currentTextChanged.connect(self.on_dst_format_changed)
        vid_grp.addWidget(self.dst_edit)
        
        sub_grp = QHBoxLayout()
        sub_grp.setSpacing(1)
        self.subtitle_format_label = QLabel("<b>Subtitle Format (Source):</b>")
        sub_grp.addWidget(self.subtitle_format_label)
        self.src_edit = PopupStyledComboBox()
        self.src_edit.setEditable(False)
        self.src_edit.addItems(['Auto', 'All'] + get_enabled_src_ext())
        self.src_edit.setCurrentText(get_last_src_format())
        self.src_edit.setMinimumWidth(80)
        self.src_edit.setMaximumWidth(120)
        self.src_edit.currentTextChanged.connect(self.on_src_format_changed)
        sub_grp.addWidget(self.src_edit)
        
        target_row.addLayout(vid_grp)
        target_row.addLayout(sub_grp)
        target_row.addStretch()
        
        # Target folder button
        self.target_btn = QPushButton("📁 Select Folder")
        self.target_btn.clicked.connect(self.select_target_folder)
        target_row.addWidget(self.target_btn)
        
        # Remove all videos button
        self.remove_videos_btn = QPushButton("🗑️ Remove All Videos")
        self.remove_videos_btn.setToolTip("Remove all video files from the table")
        self.remove_videos_btn.clicked.connect(self.remove_all_videos)
        target_row.addWidget(self.remove_videos_btn)
        self.target_label = QLabel("No Destination Folder Selected.")

        self.delete_subs_btn = QPushButton("🗑️ Delete All Subs")
        self.delete_subs_btn.setToolTip("Move all subtitle files to recycle bin")
        self.delete_subs_btn.clicked.connect(self.delete_all_subs)
        target_row.addWidget(self.delete_subs_btn)

        # Create main splitter for resizable sections
        self.main_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter.setChildrenCollapsible(False)
        # Disable resizing of the handle between target (index 0) and subtitle (index 1) when video container is hidden
        self.main_splitter.setHandleWidth(6)
        
        # --- Video container ---
        self.target_container = QWidget()
        target_layout = QVBoxLayout(self.target_container)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(self.target_label)
        target_layout.addWidget(self.video_drop_area)

        # Wrap video controls so we can measure/collapse cleanly when video container is hidden
        self.target_controls_container = QWidget()
        target_controls_layout = QVBoxLayout(self.target_controls_container)
        target_controls_layout.setContentsMargins(0, 0, 0, 0)
        target_controls_layout.setSpacing(6)
        target_controls_layout.addLayout(target_row)
        self.target_controls_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        target_layout.addWidget(self.target_controls_container)
        
        # --- Subtitle section container ---
        subtitle_container = QWidget()
        subtitle_layout = QVBoxLayout(subtitle_container)
        subtitle_layout.setContentsMargins(0, 0, 0, 0)
        
        self.drop_area = DropArea(self.on_files_selected, self.on_selection_changed, self)
        subtitle_layout.addWidget(self.drop_area)

        # Button Row
        btn_row = QHBoxLayout()
        self.open_folder_btn = QPushButton("📂 Browse Files")
        self.open_folder_btn.setToolTip("Browse and add subtitle files to the table")
        self.open_folder_btn.clicked.connect(self.open_subtitle_folder)
        btn_row.addWidget(self.open_folder_btn)

        self.delete_all_btn = QPushButton("🗑️ Remove All")
        self.delete_all_btn.setToolTip("Remove subtitle files from the table")
        self.delete_all_btn.clicked.connect(self.remove_src_files_from_table)
        btn_row.addWidget(self.delete_all_btn)

        self.delete_completed_btn = QPushButton("✅ Remove Completed")
        self.delete_completed_btn.setToolTip("Remove all completed subtitle files from the table")
        self.delete_completed_btn.clicked.connect(self.delete_completed_files)
        btn_row.addWidget(self.delete_completed_btn)

        self.redo_btn = QPushButton("🔄 Retry Failed")
        self.redo_btn.setToolTip("Retry renaming for all failed subtitle files")
        self.redo_btn.clicked.connect(self.redo_failed)
        btn_row.addWidget(self.redo_btn)

        self.rename_all_btn = QPushButton("🚀 Start Renaming")
        self.rename_all_btn.setToolTip("Start renaming all subtitle files in the table")
        self.rename_all_btn.clicked.connect(self.rename_all_files)
        btn_row.addWidget(self.rename_all_btn)

        self.delete_src_btn = QPushButton("🗑️ Delete All Subs")
        self.delete_src_btn.setToolTip("Move all subtitle files to recycle bin")
        self.delete_src_btn.clicked.connect(self.delete_all_subs_from_src_table)
        btn_row.addWidget(self.delete_src_btn)

        subtitle_layout.addLayout(btn_row)
        # subtitle_layout.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        
        # --- Log box ---
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        # Add widgets to splitter in order: video container, subtitle container, log box
        self.main_splitter.addWidget(self.target_container)
        self.main_splitter.addWidget(subtitle_container)
        self.main_splitter.addWidget(self.log_box)

        self.main_splitter.setSizes([250, 500, 250])
        self.main_splitter.splitterMoved.connect(self.on_splitter_moved)
        
        # Wrap the current main content inside the stacked widget's Main page
        self.main_page = QWidget()
        main_page_layout = QVBoxLayout(self.main_page)
        main_page_layout.setContentsMargins(0, 0, 0, 0)
        main_page_layout.addWidget(self.main_splitter)
        self.stacked.addWidget(self.main_page)

        # Displays the log page
        self.log_page = QWidget()
        log_page_layout = QVBoxLayout(self.log_page)
        log_page_layout.setContentsMargins(6, 6, 6, 6)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        log_page_layout.addWidget(self.log_view)

        # Buttons for the log page: Copy All, Clear, Close
        log_btn_row = QHBoxLayout()
        self.log_copy_btn = QPushButton("Copy All")
        self.log_clear_btn = QPushButton("Clear")
        self.log_close_btn = QPushButton("Close")
        self.log_copy_btn.clicked.connect(self.on_log_copy_btn)
        self.log_close_btn.clicked.connect(self.on_log_close_btn)
        self.log_clear_btn.clicked.connect(self.on_log_clear_btn)
        log_btn_row.addStretch()
        log_btn_row.addWidget(self.log_copy_btn)
        log_btn_row.addWidget(self.log_clear_btn)
        log_btn_row.addWidget(self.log_close_btn)
        log_page_layout.addLayout(log_btn_row)
        self.stacked.addWidget(self.log_page)

        # Default to Main page
        self.stacked.setCurrentWidget(self.main_page)
        
        # Apply compact mode if enabled
        if get_compact_mode():
            self.apply_compact_mode()
        
        # Initialize recent folders menu
        self.update_recent_folders_menu()

        # Click outside table to clear selection (install on app so we see all mouse presses)
        # Protect action buttons so their click handlers still see the current selection.
        app = QApplication.instance()
        if app:
            self._sub_click_filter = ClickOutsideFilter(
                self.drop_area.table,
                self,
                preserve_widgets=(self.delete_all_btn, self.delete_src_btn),
            )
            self._video_click_filter = ClickOutsideFilter(
                self.video_drop_area.table,
                self,
                preserve_widgets=(self.remove_videos_btn, self.delete_subs_btn),
            )
            app.installEventFilter(self._sub_click_filter)
            app.installEventFilter(self._video_click_filter)

    def _load_plugins(self):
        """Load all available plugins and add their pages to the UI."""
        try:
            if not ap.addons_enabled():
                self.plugin_pages.clear()
                self.plugin_tabs.clear()
                if ap.addons_disabled():
                    self.log("<b>Addons disabled by SUBRENAME_DISABLE_ADDONS.</b>", "system")
                else:
                    self.log(
                        "<b>Default addons path disabled by SUBRENAME_DISABLE_DEFAULT_ADDONS. "
                        "Set SUBRENAME_ADDONS_DIR to load addons from a custom path.</b>",
                        "system",
                    )
                return

            from plugins.manager import PluginManager
            from plugins.context import build_app_ctx

            app_ctx = build_app_ctx(
                log_signal=self.log_signal,
                status_update_signal=self.status_update_signal,
                plugin_message_signal=self.plugin_message_signal,
                plugin_theme_signal=self.plugin_theme_signal,
                shutdown_signal=self.shutdown_signal,
                settings_loader=load_settings,
                assets_path=str(ap.package_root() / "assets"),
                current_theme_getter=lambda: self.current_theme,
                dark_theme=DARK_THEME,
                light_theme=LIGHT_THEME,
                stylesheet_generator=generate_stylesheet,
                zoom_level_getter=get_zoom_level,
            )

            addons_path = str(ap.addons_dir(create=True))
            pm = PluginManager(addons_path, app_ctx)
            loaded_plugins = pm.load_all()

            self.plugin_pages.clear()
            self.plugin_tabs.clear()

            id_seen: dict[str, int] = {}

            for plugin in loaded_plugins:
                if plugin.error:
                    self.log(f"<b>Plugin '{plugin.id}' failed to load: {plugin.error}</b>", "system")
                    continue

                n = id_seen.get(plugin.id, 0) + 1
                id_seen[plugin.id] = n
                if n > 1:
                    self.log(
                        f"<b>Duplicate plugin.id '{plugin.id}' detected (instance {n}). "
                        f"Using runtime key '{plugin.runtime_key}'.</b>",
                        "warning",
                    )

                label_suffix = "" if n == 1 else f" ({plugin.id} #{n})"
                self.log(f"Loaded plugin: {plugin.name}{label_suffix} v{plugin.version}", "info")

                pages_for_runtime_key = []
                for page_name, widget in plugin.pages:
                    self.stacked.addWidget(widget)
                    tab_index = self.log_switch_bar.addTab(f"{page_name}{label_suffix}")
                    self.log_switch_bar.setTabToolTip(tab_index, f"{plugin.name} [{plugin.id}]")
                    pages_for_runtime_key.append((page_name, widget, tab_index))
                    self.plugin_tabs[tab_index] = widget

                self.plugin_pages[plugin.runtime_key] = pages_for_runtime_key

        except ImportError as e:
            print(f"[Plugin] Plugin system not available: {e}")
        except Exception as e:
            self.log(f"<b>Error loading plugins: {e}</b>", "system")

    def _apply_theme_to_plugin_widget(self, widget):
        """Apply the current theme to a plugin widget (runs on UI thread via signal)."""
        try:
            widget.setStyleSheet(generate_stylesheet(self.current_theme))
        except Exception as e:
            print(f"Error applying theme to plugin widget: {e}")

    def _show_plugin_message(self, message, title="Plugin Message", msg_type="info"):
        """Show a message dialog for a plugin (runs on UI thread via signal)."""
        from PyQt6.QtWidgets import QMessageBox

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)

        if msg_type == "error":
            msg_box.setIcon(QMessageBox.Icon.Critical)
        elif msg_type == "warning":
            msg_box.setIcon(QMessageBox.Icon.Warning)
        elif msg_type == "question":
            msg_box.setIcon(QMessageBox.Icon.Question)
        else:
            msg_box.setIcon(QMessageBox.Icon.Information)

        msg_box.exec()

    def setup_settings_menu(self):
        # Add checkboxes to the settings menu
        self.auto_run_action = CheckmarkAction("Auto-Run Renaming", self)
        self.use_default_tag_action = CheckmarkAction("Auto-Apply Detected Group Suffix", self)
        self.always_prompt_tag_action = CheckmarkAction("Always Ask for Group Suffix", self)
        self.cache_per_set_action = CheckmarkAction("Use This for All Files of the Same Group", self)
        self.preview_mode_action = CheckmarkAction("Enable Preview Mode", self)
        # Conflicts submenu
        self.conflicts_menu = AdaptiveRoundedMenu("Conflicts", self)
        self.conflict_ask_action = CheckmarkAction("Ask", self)
        self.conflict_skip_action = CheckmarkAction("Skip", self)
        self.conflict_overwrite_action = CheckmarkAction("Overwrite", self)
        self.conflict_keepboth_action = CheckmarkAction("Keep Both", self)
        self.conflict_actions = [self.conflict_ask_action, self.conflict_skip_action, self.conflict_overwrite_action, self.conflict_keepboth_action]
        self.conflicts_menu.addAction(self.conflict_ask_action)
        self.conflicts_menu.addAction(self.conflict_skip_action)
        self.conflicts_menu.addAction(self.conflict_overwrite_action)
        self.conflicts_menu.addAction(self.conflict_keepboth_action)
        self.conflicts_menu.addSeparator()
        self.apply_all_conflicts_action = CheckmarkAction("Use This for All Conflicts of the Same Group", self)
        self.conflicts_menu.addAction(self.apply_all_conflicts_action)
        self.conflicts_menu.addSeparator()
        self.conflict_open_settings_action = QAction("Open Settings...", self)
        self.conflicts_menu.addAction(self.conflict_open_settings_action)

        self.settings_menu.addAction(self.preview_mode_action)
        self.settings_menu.addSeparator()
        self.settings_menu.addAction(self.auto_run_action)
        self.settings_menu.addAction(self.use_default_tag_action)
        self.settings_menu.addAction(self.always_prompt_tag_action)
        self.settings_menu.addAction(self.cache_per_set_action)
        self.settings_menu.addSeparator()
        self.settings_menu.addMenu(self.conflicts_menu)
        self.settings_menu.addSeparator()

        # Add the fallback settings dialog action
        self.settings_dialog_action = QAction("Settings...", self)
        self.settings_menu.addAction(self.settings_dialog_action)

        # Connect signals for checkable actions
        self.auto_run_action.triggered.connect(self.on_settings_changed)
        self.use_default_tag_action.triggered.connect(self.on_settings_changed)
        self.always_prompt_tag_action.triggered.connect(self.on_settings_changed)
        self.cache_per_set_action.triggered.connect(self.on_settings_changed)
        self.preview_mode_action.triggered.connect(self.toggle_preview_mode)
        self.apply_all_conflicts_action.triggered.connect(self.on_settings_changed)

        self.conflict_ask_action.triggered.connect(lambda: self.set_conflict_policy("ASK"))
        self.conflict_skip_action.triggered.connect(lambda: self.set_conflict_policy("SKIP"))
        self.conflict_overwrite_action.triggered.connect(lambda: self.set_conflict_policy("OVERWRITE"))
        self.conflict_keepboth_action.triggered.connect(lambda: self.set_conflict_policy("SUFFIX"))
        self.conflict_open_settings_action.triggered.connect(self.open_settings_dialog)

        # Connect the settings dialog action
        self.settings_dialog_action.triggered.connect(self.open_settings_dialog)

        # Load current settings
        self.load_settings_to_menu()

    def setup_file_menu(self):
        # Add actions to the file menu
        self.open_target_folder_action = QAction("Open Folder...", self)
        self.open_target_folder_action.setShortcut("Ctrl+O")
        self.open_target_folder_action.triggered.connect(self.select_target_folder)
        
        self.open_subtitle_files_action = QAction("Open Subtitle Files...", self)
        self.open_subtitle_files_action.setShortcut("Ctrl+Shift+O")
        self.open_subtitle_files_action.triggered.connect(self.open_subtitle_folder)
        
        self.open_target_in_explorer_action = QAction("Open Folder in Explorer", self)
        self.open_target_in_explorer_action.triggered.connect(self.open_target_in_explorer)
        
        self.clear_all_files_action = QAction("Remove All Files from Table", self)
        self.clear_all_files_action.setShortcut("Ctrl+Shift+Del")
        self.clear_all_files_action.triggered.connect(self.remove_src_files_from_table)
        
        # --- Open Recent submenu ---
        self.open_recent_menu = AdaptiveRoundedMenu("Open Recent", self)
        self.clear_recent_action = QAction("Clear Recent Folders", self)
        self.clear_recent_action.triggered.connect(self.clear_recent_folders)
        
        self.file_menu.addAction(self.open_target_folder_action)
        self.file_menu.addAction(self.open_target_in_explorer_action)
        self.file_menu.addMenu(self.open_recent_menu)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.open_subtitle_files_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.clear_all_files_action)
        self.file_menu.addSeparator()

        self.open_user_data_action = QAction("Open User Data Folder", self)
        self.open_user_data_action.triggered.connect(self.open_user_data_folder)
        self.file_menu.addAction(self.open_user_data_action)
        
        # Exit action
        self.exit_action = QAction("Exit", self)
        self.exit_action.setShortcut("Ctrl+Q")
        self.exit_action.triggered.connect(self.close)
        self.file_menu.addAction(self.exit_action)

    def setup_view_menu(self):
        # --- Theme submenu ---
        self.theme_menu = AdaptiveRoundedMenu("Theme", self)
        self.light_theme_action = CheckmarkAction("Light Theme", self)
        self.light_theme_action.triggered.connect(lambda: self.change_theme(False))
        
        self.dark_theme_action = CheckmarkAction("Dark Theme", self)
        self.dark_theme_action.triggered.connect(lambda: self.change_theme(True))
        
        self.theme_menu.addAction(self.light_theme_action)
        self.theme_menu.addAction(self.dark_theme_action)
        
        # Set initial theme state
        settings = load_settings()
        dark_mode = settings.get("dark_mode", True)
        self.light_theme_action.setChecked(not dark_mode)
        self.dark_theme_action.setChecked(dark_mode)
        
        # --- Zoom submenu ---
        self.zoom_menu = AdaptiveRoundedMenu("Zoom", self)
        self.zoom_in_action = QAction("Zoom In", self)
        self.zoom_in_action.setShortcut("Ctrl+=")
        self.zoom_in_action.triggered.connect(self.zoom_in)
        
        self.zoom_out_action = QAction("Zoom Out", self)
        self.zoom_out_action.setShortcut("Ctrl+-")
        self.zoom_out_action.triggered.connect(self.zoom_out)
        
        self.zoom_reset_action = QAction("Reset Zoom", self)
        self.zoom_reset_action.setShortcut("Ctrl+0")
        self.zoom_reset_action.triggered.connect(self.zoom_reset)
        
        self.zoom_menu.addAction(self.zoom_in_action)
        self.zoom_menu.addAction(self.zoom_out_action)
        self.zoom_menu.addSeparator()
        self.zoom_menu.addAction(self.zoom_reset_action)
        
        # --- View options ---
        settings = load_settings()
        preview_pref = settings.get("preview_mode", True)

        self.show_video_table_action = CheckmarkAction("Show Video Table", self)
        self.show_video_table_action.setChecked(settings.get("show_video_table", True))
        self.show_video_table_action.triggered.connect(self.toggle_video_table)
        
        self.show_log_action = CheckmarkAction("Show Log Box", self)
        self.show_log_action.setChecked(settings.get("show_log", True))
        self.show_log_action.triggered.connect(self.toggle_log)

        # Toggle in-app switcher bar under the menubar
        self.toggle_log_switcher_action = CheckmarkAction("Show Switch Bar", self)
        self.toggle_log_switcher_action.setChecked(load_settings().get("show_switch_bar", True))
        self.toggle_log_switcher_action.triggered.connect(self.toggle_log_switch_bar)
        
        self.show_preview_name_action = CheckmarkAction("Show Preview Name", self)
        self.show_preview_name_action.setChecked(settings.get("show_preview_name_column", preview_pref))
        self.show_preview_name_action.triggered.connect(self.toggle_preview_name_column)

        self.show_preview_status_action = CheckmarkAction("Show Preview Status", self)
        self.show_preview_status_action.setChecked(settings.get("show_preview_status_column", preview_pref))
        self.show_preview_status_action.triggered.connect(self.toggle_preview_status_column)
        
        self.compact_mode_action = CheckmarkAction("Compact Mode", self)
        self.compact_mode_action.setChecked(get_compact_mode())
        self.compact_mode_action.triggered.connect(self.toggle_compact_mode)

        # --- Log submenu ---
        self.log_menu = AdaptiveRoundedMenu("Log", self)

        # Log message filtering options
        self.show_info_messages_action = CheckmarkAction("Info Messages", self)
        self.show_info_messages_action.setChecked(load_settings().get("show_info_messages", True))
        self.show_info_messages_action.triggered.connect(self.toggle_info_messages)
        self.log_menu.addAction(self.show_info_messages_action)
        
        self.show_success_messages_action = CheckmarkAction("Success Messages", self)
        self.show_success_messages_action.setChecked(load_settings().get("show_success_messages", True))
        self.show_success_messages_action.triggered.connect(self.toggle_success_messages)
        self.log_menu.addAction(self.show_success_messages_action)
        
        self.show_warning_messages_action = CheckmarkAction("Warning Messages", self)
        self.show_warning_messages_action.setChecked(load_settings().get("show_warning_messages", True))
        self.show_warning_messages_action.triggered.connect(self.toggle_warning_messages)
        self.log_menu.addAction(self.show_warning_messages_action)
        
        self.show_error_messages_action = CheckmarkAction("Error Messages", self)
        self.show_error_messages_action.setChecked(load_settings().get("show_error_messages", True))
        self.show_error_messages_action.triggered.connect(self.toggle_error_messages)
        self.log_menu.addAction(self.show_error_messages_action)
        
        # Open log file in popup
        self.log_menu.addSeparator()
        self.open_log_popup_action = QAction("Open Log Window", self)
        self.open_log_popup_action.triggered.connect(self.open_rename_log_popup)
        self.log_menu.addAction(self.open_log_popup_action)

        self.view_menu.addMenu(self.theme_menu)
        self.view_menu.addMenu(self.zoom_menu)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.show_preview_name_action)
        self.view_menu.addAction(self.show_preview_status_action)
        self.view_menu.addAction(self.show_video_table_action)
        self.view_menu.addAction(self.show_log_action)
        self.view_menu.addAction(self.toggle_log_switcher_action)
        self.view_menu.addSeparator()
        self.view_menu.addMenu(self.log_menu)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.compact_mode_action)

    def setup_tools_menu(self):
        # --- Renaming tools ---
        self.rename_all_action = QAction("Rename All Files", self)
        self.rename_all_action.setShortcut("F5")
        self.rename_all_action.triggered.connect(self.rename_all_files)
        
        self.retry_failed_action = QAction("Retry Failed Files", self)
        self.retry_failed_action.setShortcut("F6")
        self.retry_failed_action.triggered.connect(self.redo_failed)
        
        self.clear_completed_action = QAction("Clear Completed Files", self)
        self.clear_completed_action.setShortcut("F7")
        self.clear_completed_action.triggered.connect(self.delete_completed_files)
        
        self.clear_all_subs_action = QAction("Clear All Subtitle Files", self)
        self.clear_all_subs_action.setShortcut("F8")
        self.clear_all_subs_action.triggered.connect(self.delete_all_subs)
        
        # --- Analysis tools ---
        self.analyze_folder_action = QAction("Analyze Folder", self)
        self.analyze_folder_action.setShortcut("F11")
        self.analyze_folder_action.triggered.connect(self.analyze_target_folder)
        
        # --- On Complete submenu ---
        self.on_complete_menu = AdaptiveRoundedMenu("On Complete", self)
        self.do_nothing_action = CheckmarkAction("Do Nothing", self)
        self.do_nothing_action.triggered.connect(lambda: self.set_completion_behavior("do_nothing"))
        
        self.exit_action = CheckmarkAction("Exit", self)
        self.exit_action.triggered.connect(lambda: self.set_completion_behavior("exit"))
        
        self.on_complete_menu.addAction(self.do_nothing_action)
        self.on_complete_menu.addAction(self.exit_action)
        
        # Set initial state
        completion_behavior = load_settings().get("completion_behavior", "do_nothing")
        self.do_nothing_action.setChecked(completion_behavior == "do_nothing")
        self.exit_action.setChecked(completion_behavior == "exit")
        
        self.tools_menu.addAction(self.rename_all_action)
        self.tools_menu.addAction(self.retry_failed_action)
        self.tools_menu.addSeparator()
        self.tools_menu.addAction(self.clear_completed_action)
        self.tools_menu.addAction(self.clear_all_subs_action)
        self.tools_menu.addSeparator()
        self.tools_menu.addAction(self.analyze_folder_action)
        self.tools_menu.addSeparator()
        self.tools_menu.addMenu(self.on_complete_menu)

    def setup_help_menu(self):
        self.about_action = QAction("About", self)
        self.about_action.triggered.connect(self.show_about)
        
        self.help_action = QAction("Help", self)
        self.help_action.setShortcut("F1")
        self.help_action.triggered.connect(self.show_help)
        
        self.help_menu.addAction(self.help_action)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.about_action)

    def load_settings_to_menu(self):
        settings = load_settings()
        self.auto_run_action.setChecked(settings.get("auto_run", False))
        self.use_default_tag_action.setChecked(settings.get("use_default_tag_if_found", False))
        self.always_prompt_tag_action.setChecked(settings.get("always_prompt_tag_always", False))
        self.cache_per_set_action.setChecked(settings.get("cache_per_set", True))
        self.apply_all_conflicts_action.setChecked(settings.get("apply_all_conflicts", False))
        self.preview_mode_action.setChecked(settings.get("preview_mode", True))

        # Conflict policy
        policy = settings.get("conflict_policy", "ASK")
        policy_action_map = {
            "ASK": self.conflict_ask_action,
            "SKIP": self.conflict_skip_action,
            "OVERWRITE": self.conflict_overwrite_action,
            "SUFFIX": self.conflict_keepboth_action,
        }
        for key, action in policy_action_map.items():
            action.setChecked(key == policy)

        # Load completion behavior
        completion_behavior = settings.get("completion_behavior", "do_nothing")
        self.do_nothing_action.setChecked(completion_behavior == "do_nothing")
        self.exit_action.setChecked(completion_behavior == "exit")

    def on_settings_changed(self):
        settings = load_settings()
        settings["auto_run"] = self.auto_run_action.isChecked()
        settings["use_default_tag_if_found"] = self.use_default_tag_action.isChecked()
        settings["always_prompt_tag_always"] = self.always_prompt_tag_action.isChecked()
        settings["cache_per_set"] = self.cache_per_set_action.isChecked()
        settings["apply_all_conflicts"] = self.apply_all_conflicts_action.isChecked()
        save_settings(settings)

    def set_conflict_policy(self, policy: str):
        """Set conflict policy from the Conflicts submenu (mutually exclusive)."""
        policy_action_map = {
            "ASK": self.conflict_ask_action,
            "SKIP": self.conflict_skip_action,
            "OVERWRITE": self.conflict_overwrite_action,
            "SUFFIX": self.conflict_keepboth_action,
        }
        for key, action in policy_action_map.items():
            action.setChecked(key == policy)
        settings = load_settings()
        settings["conflict_policy"] = policy
        save_settings(settings)

    def set_completion_behavior(self, behavior):
        """Set what happens when job completes"""
        self.do_nothing_action.setChecked(behavior == "do_nothing")
        self.exit_action.setChecked(behavior == "exit")
        settings = load_settings()
        settings["completion_behavior"] = behavior
        save_settings(settings)

    def on_job_completed(self):
        """Called when a rename job is completed"""
        # Re-enable buttons
        self.rename_all_btn.setEnabled(True)
        self.redo_btn.setEnabled(True)
        self.delete_completed_btn.setEnabled(True)
        self.delete_subs_btn.setEnabled(True)
        
        # Check if we should exit upon job completion
        settings = load_settings()
        completion_behavior = settings.get("completion_behavior", "do_nothing")
        
        if completion_behavior == "exit":  # Only exit if ALL files are success or skipped
            if self.subtitle_status and all(status in ["success", "skipped"] for status in self.subtitle_status.values()):
                QApplication.instance().quit()

    def apply_theme(self):
        """Apply the current theme to the entire application"""
        self.apply_theme_with_zoom(get_zoom_level())

    def apply_theme_with_zoom(self, zoom_level):
        """Apply the current theme with zoom-adjusted image sizes"""
        # Use stylesheet system
        self.setStyleSheet(generate_stylesheet(self.current_theme))
        
        self.drop_area.update_theme(self.current_theme, zoom_level)
        self.video_drop_area.update_theme(self.current_theme, zoom_level)
        
        # Apply Windows title bar theming (Windows 10/11 only)
        if platform.system() == "Windows":
            is_dark_theme = self.current_theme == DARK_THEME
            set_windows_title_bar_theme(self, is_dark_theme)

    def change_theme(self, dark_mode):
        self.current_theme = DARK_THEME if dark_mode else LIGHT_THEME
        
        self.light_theme_action.setChecked(not dark_mode)
        self.dark_theme_action.setChecked(dark_mode)
        
        settings = load_settings()
        settings["dark_mode"] = dark_mode
        save_settings(settings)
        
        self.apply_theme()

    def showEvent(self, event):
        """Override showEvent to ensure title bar theming is applied when window is shown"""
        super().showEvent(event)
        # Apply title bar theming after window is shown (when HWND is valid)
        if platform.system() == "Windows":
            is_dark_theme = self.current_theme == DARK_THEME
            set_windows_title_bar_theme(self, is_dark_theme)

    def select_target_folder(self):
        last_folder = get_last_target_folder()
        folder = QFileDialog.getExistingDirectory(self, "Select Folder (Video/Output)", last_folder)
        if folder:
            self.target_folder = folder
            self.video_table_locked = False
            self.target_label.setText(f"Destination Folder: {folder}")
            set_last_target_folder(folder)
            add_recent_target_folder(folder)
            self.update_video_table(log_count=True)
            self.update_subtitle_count()
            
            # Check if "Auto" is selected for destination format and update accordingly
            if self.dst_edit.currentText() == "Auto":
                self.on_dst_format_changed("Auto")
            
            self.update_recent_folders_menu()
            self.check_orphaned_files()  # Check for orphaned subtitle files and prompt user to add them
            
            # If subtitle files were already selected, check settings after orphaned check
            if self.selected_files:
                settings = load_settings()
                auto_run = settings.get("auto_run", False)
                preview_mode = settings.get("preview_mode", True)
                
                if auto_run:
                    self.run_renamer(preview_mode=False)
                elif preview_mode:
                    self.run_renamer(preview_mode=True)
                # else: wait for user to click "Start Renaming"

    def open_recent_folder(self, folder):
        if os.path.exists(folder):
            self.target_folder = folder
            self.video_table_locked = False
            self.target_label.setText(f"Destination Folder: {folder}")
            set_last_target_folder(folder)
            add_recent_target_folder(folder)
            self.update_video_table(log_count=True)
            self.update_subtitle_count()
            
            # Check if "Auto" is selected for destination format and update accordingly
            if self.dst_edit.currentText() == "Auto":
                self.on_dst_format_changed("Auto")
            
            self.update_recent_folders_menu()
            self.check_orphaned_files()  # Check for orphaned subtitle files and prompt user to add them

            # If subtitle files were already selected, check settings after orphaned check
            if self.selected_files:
                settings = load_settings()
                auto_run = settings.get("auto_run", False)
                preview_mode = settings.get("preview_mode", True)

                if auto_run:
                    self.run_renamer(preview_mode=False)
                elif preview_mode:
                    self.run_renamer(preview_mode=True)
                # else: wait for user to click "Start Renaming"
        else:  # Folder no longer exists, remove it from recent folders and update menu
            self.log(f"'{folder}' no longer exists, removed from recent folders.", "info")
            remove_recent_target_folder(folder)
            self.update_recent_folders_menu()

    def clear_recent_folders(self):
        clear_recent_target_folders()
        self.update_recent_folders_menu()

    def check_orphaned_files(self):
        """Find subs in target that don't match any video and prompt to add them."""
        def has_match(sub_name: str) -> bool:
            sub_base = os.path.splitext(sub_name)[0].lower()
            return any(v in sub_base for v in video_bases)

        def on_item_changed():
            update_select_all_state()

        def update_select_all_state():
            total = list_widget.count()
            checked = sum(1 for i in range(total) if list_widget.item(i).checkState() == Qt.CheckState.Checked)
            select_all_checkbox.toggled.disconnect()
            select_all_checkbox.setCheckState(Qt.CheckState.Checked if checked == total else Qt.CheckState.Unchecked)
            select_all_checkbox.toggled.connect(on_select_all_changed)
            
        def on_select_all_changed(checked):
            if checked:  # When checked, check all items
                for i in range(list_widget.count()):
                    list_widget.item(i).setCheckState(Qt.CheckState.Checked)
            else:  # When unchecked, uncheck all items
                for i in range(list_widget.count()):
                    list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

        if not self.target_folder:
            return
        entries = os.listdir(self.target_folder)

        video_exts = set(get_all_video_extensions())
        sub_exts = set(get_enabled_src_ext())

        video_files = [f for f in entries if any(f.lower().endswith(ext) for ext in video_exts)]
        subtitle_files = [f for f in entries if any(f.lower().endswith(ext) for ext in sub_exts)]

        video_bases = [os.path.splitext(v)[0].lower() for v in video_files]
        orphaned_subtitles = [s for s in subtitle_files if not has_match(s)]
        
        if not video_files or not subtitle_files or not orphaned_subtitles:
            return
            
        dialog = QDialog(self)
        dialog.setWindowTitle("Orphaned Subtitle Files")
        dialog.setModal(True)
        dialog.resize(900, 600)
        theme = self.current_theme
        dialog.setStyleSheet(generate_stylesheet(theme))
        
        layout = QVBoxLayout(dialog)
        
        header_label = QLabel(f"Found {len(orphaned_subtitles)} subtitle {'file' if len(orphaned_subtitles) == 1 else 'files'} in the destination that don't match any video files:")
        header_label.setWordWrap(True)
        layout.addWidget(header_label)
        
        # Select All checkbox
        select_all_checkbox = QCheckBox("Select All")
        select_all_checkbox.setChecked(True)
        select_all_checkbox.toggled.connect(on_select_all_changed)
        layout.addWidget(select_all_checkbox)
        
        list_widget = CheckableListWidget()
        list_widget.setMinimumHeight(300)
        list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        for f in orphaned_subtitles:
            item = QListWidgetItem(f)
            item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setCheckState(Qt.CheckState.Checked)  # Default to checked
            list_widget.addItem(item)
        list_widget.itemChanged.connect(lambda: on_item_changed())
        layout.addWidget(list_widget)
        update_select_all_state()
        
        question_label = QLabel(f"Would you like to add {'this file' if len(orphaned_subtitles) == 1 else 'these files'} to the subtitle table for renaming?")
        question_label.setWordWrap(True)
        layout.addWidget(question_label)
        
        rename_in_place_checkbox = QCheckBox(
            f"Replace Original {'File' if len(orphaned_subtitles) == 1 else 'Files'}"
        )
        rename_in_place_checkbox.setChecked(load_settings().get("rename_in_place", False))
        layout.addWidget(rename_in_place_checkbox)
        
        # Buttons
        button_layout = QHBoxLayout()
        accept_button = QPushButton("Accept")
        reject_button = QPushButton("Cancel")
        accept_button.clicked.connect(dialog.accept)
        reject_button.clicked.connect(dialog.reject)
        button_layout.addWidget(accept_button)
        button_layout.addWidget(reject_button)
        layout.addLayout(button_layout)
        accept_button.setDefault(True)
        
        reply = dialog.exec()
        if reply == QDialog.DialogCode.Accepted:
            settings = load_settings()
            settings["rename_in_place"] = rename_in_place_checkbox.isChecked()
            save_settings(settings)

            selected_files = []  # Collect only the checked items
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    selected_files.append(item.text())
            selected_paths = [os.path.join(self.target_folder, f) for f in selected_files]  # Build full paths for selected files
            
            self.adding_orphaned_files = True  # Set flag to prevent double preview
            append_mode = self.drop_area.table.rowCount() > 0
            if append_mode and selected_paths:  # Filter out files that already exist in the table
                existing_files = set()
                for row in range(self.drop_area.table.rowCount()):
                    path_item = self.drop_area.table.item(row, SubCol.PATH)
                    if path_item:
                        existing_files.add(path_item.text())
                selected_paths = [f for f in selected_paths if f not in existing_files]

            if rename_in_place_checkbox.isChecked():
                self.rename_in_place_paths.update(selected_paths)

            if selected_paths:  # Proceed if there are new files to add
                self.on_files_selected(selected_paths, append=append_mode)
            else:  # All files were duplicates or no files selected
                self.on_files_selected([], append=append_mode)
            self.adding_orphaned_files = False  # Clear flag
            self.log(f"Added {len(selected_files)} selected orphaned subtitle file(s) to the table.", "info")
        else:
            self.log(f"Skipped adding {len(orphaned_subtitles)} orphaned subtitle file(s).", "info")  # debug

    def update_recent_folders_menu(self):
        """Update the recent folders submenu with current recent folders"""
        self.open_recent_menu.clear()
        
        recent_folders = get_recent_target_folders()
        
        if not recent_folders:
            no_recent_action = QAction("No Recent Folders", self)
            no_recent_action.setEnabled(False)
            self.open_recent_menu.addAction(no_recent_action)
        else:
            for folder in recent_folders:
                # Create a display name (only the folder name, not full path)
                folder_name = os.path.basename(folder)
                if not folder_name:
                    folder_name = folder.rstrip('/\\').split('/')[-1].split('\\')[-1]
                
                action = QAction(folder_name, self)
                action.setToolTip(folder)
                action.triggered.connect(lambda checked, f=folder: self.open_recent_folder(f))
                self.open_recent_menu.addAction(action)
        
        self.open_recent_menu.addSeparator()
        self.open_recent_menu.addAction(self.clear_recent_action)

    def open_settings_dialog(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            # Save the settings
            settings = load_settings()
            settings["auto_run"] = dlg.get_auto_run()
            settings["use_default_tag_if_found"] = dlg.get_use_default_tag()
            settings["always_prompt_tag_always"] = dlg.get_always_prompt_tag()
            settings["cache_per_set"] = dlg.get_cache_per_set()
            settings["conflict_policy"] = dlg.get_conflict_policy()
            settings["preview_mode"] = dlg.get_preview_mode()
            settings["delete_empty_folders"] = dlg.get_delete_empty_folders()

            save_settings(settings)
            
            # Save extension settings
            set_enabled_video_extensions(dlg.get_enabled_dst_ext())
            set_enabled_subtitle_extensions(dlg.get_enabled_src_ext())
            
            # Refresh comboboxes with new enabled extensions
            self.refresh_extension_comboboxes()
            
            # Apply preview visibility and keep menus in sync
            preview_mode = settings.get("preview_mode", True)
            self.apply_preview_visibility()
            self.preview_mode_action.setChecked(preview_mode)
            self.show_preview_name_action.setChecked(settings.get("show_preview_name_column", preview_mode))
            self.show_preview_status_action.setChecked(settings.get("show_preview_status_column", preview_mode))

            # Apply view tab settings: theme, layout, log filters
            dark_mode = settings.get("dark_mode", True)
            self.change_theme(dark_mode)
            show_video = settings.get("show_video_table", True)
            show_log = settings.get("show_log", True)
            self.show_video_table_action.setChecked(show_video)
            self.video_drop_area.setVisible(show_video)
            if not show_video:
                self.target_controls_container.show()
            self.show_log_action.setChecked(show_log)
            self.log_box.setVisible(show_log)
            show_bar = settings.get("show_switch_bar", True)
            self.toggle_log_switcher_action.setChecked(show_bar)
            self.log_switch_bar.setVisible(show_bar)
            compact = settings.get("compact_mode", False)
            self.compact_mode_action.setChecked(compact)
            if compact:
                self.apply_compact_mode()
            else:
                self.remove_compact_mode()
            self.show_info_messages_action.setChecked(settings.get("show_info_messages", True))
            self.show_success_messages_action.setChecked(settings.get("show_success_messages", True))
            self.show_warning_messages_action.setChecked(settings.get("show_warning_messages", True))
            self.show_error_messages_action.setChecked(settings.get("show_error_messages", True))
            
            # Immediately reflect updated settings in the Preference menu
            self.load_settings_to_menu()

    def open_rename_log_popup(self):
        """Open rename_log.txt in a simple popup window"""
        log_path = RENAME_LOG_FILE
        text = ""
        try:
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            else:
                text = "rename_log.txt not found."
        except Exception as e:
            text = f"Error reading log: {e}"

        dlg = QDialog(self)
        dlg.setWindowTitle("Execution Log")
        dlg.resize(900, 1080)
        v = QVBoxLayout(dlg)
        edit = QTextEdit()
        edit.setReadOnly(True)
        edit.setPlainText(text)
        v.addWidget(edit)

        btns = QDialogButtonBox()
        close_btn = btns.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        copy_btn = btns.addButton("Copy All", QDialogButtonBox.ButtonRole.ActionRole)
        clear_btn = btns.addButton("Clear", QDialogButtonBox.ButtonRole.ActionRole)

        close_btn.clicked.connect(dlg.reject)

        # Copy all log text to clipboard
        copy_btn.clicked.connect(self.on_log_copy_btn)

        # Clear the log file and update the text box
        clear_btn.clicked.connect(self.on_log_clear_btn)
        clear_btn.clicked.connect(dlg.reject)

        v.addWidget(btns)
        # Scroll to latest log lines
        try:
            edit.moveCursor(QTextCursor.MoveOperation.End)
        except AttributeError:
            edit.moveCursor(QTextCursor.End)
        edit.ensureCursorVisible()
        dlg.exec()

    def toggle_log_switch_bar(self):
        """Show/hide the top switch bar and persist preference"""
        show = self.toggle_log_switcher_action.isChecked()
        if show:
            self.log_switch_bar.show()
        else:
            self.log_switch_bar.hide()
            # Always switch back to Main when hiding the switch bar
            self.log_switch_bar.setCurrentIndex(0)
        s = load_settings()
        s["show_switch_bar"] = show
        save_settings(s)


    def toggle_info_messages(self):
        show = self.show_info_messages_action.isChecked()
        settings = load_settings()
        settings["show_info_messages"] = show
        save_settings(settings)

    def toggle_success_messages(self):
        show = self.show_success_messages_action.isChecked()
        settings = load_settings()
        settings["show_success_messages"] = show
        save_settings(settings)

    def toggle_warning_messages(self):
        show = self.show_warning_messages_action.isChecked()
        settings = load_settings()
        settings["show_warning_messages"] = show
        save_settings(settings)

    def toggle_error_messages(self):
        show = self.show_error_messages_action.isChecked()
        settings = load_settings()
        settings["show_error_messages"] = show
        save_settings(settings)

    def on_log_switch_changed(self, index: int):
        """Switch between Main, Log, and plugin views using the tab bar."""
        # 0 = Main page, 1 = Log page, 2+ = Plugin pages
        if index == 0:
            self.stacked.setCurrentWidget(self.main_page)
        elif index == 1:
            self.stacked.setCurrentWidget(self.log_page)
            self.load_rename_log_into_view()
            self.log_view.setFocus()
        else:
            self._switch_to_plugin_page(index)

    def _switch_to_plugin_page(self, tab_index: int):
        """Switch to a plugin page based on tab index."""
        widget = self.plugin_tabs.get(tab_index)
        if widget is not None:
            self.stacked.setCurrentWidget(widget)

    def load_rename_log_into_view(self):
        """Load the contents of rename_log.txt into the dedicated log view."""
        try:
            log_path = RENAME_LOG_FILE
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            else:
                text = "rename_log.txt not found."
        except Exception as e:
            text = f"Error reading log: {e}"
        self.log_view.setPlainText(text)
        # Scroll to latest lines
        try:
            self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        except AttributeError:
            self.log_view.moveCursor(QTextCursor.End)
        self.log_view.ensureCursorVisible()

    def on_log_close_btn(self):
        """Switch back to the Main page from the Log tab."""
        self.stacked.setCurrentWidget(self.main_page)
        self.log_switch_bar.setCurrentIndex(0)

    def on_log_copy_btn(self):
        """Copy all text from the log view to the clipboard."""
        clipboard = QApplication.instance().clipboard()
        clipboard.setText(self.log_view.toPlainText())

    def on_log_clear_btn(self):
        """Clear rename_log.txt and the in-tab log view."""
        log_path = RENAME_LOG_FILE
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("")
            self.log_view.setPlainText("")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to clear log file:\n{e}")

    def on_files_selected(self, files, append=False):
        # Check if "Auto" is selected for source format and update accordingly
        if self.src_edit.currentText() == "Auto":
            # combined_files = self.selected_files + files if append else files  # consider both existing and new files to find most common extension
            self.on_src_format_changed("Auto", files)

        # Filter files based on combobox selection
        combobox_selection = self.src_edit.currentText()
        if combobox_selection not in ["Auto", "All"]:
            files = [f for f in files if f.lower().endswith(combobox_selection.lower())]
        elif combobox_selection == "Auto":
            if files:
                files = [f for f in files if f.lower().endswith(self.src_ext.lower())]
                
        if append:
            # Append to existing files, avoid duplicates
            new_files_count = 0
            for file_path in files:
                if file_path not in self.selected_files:
                    self.selected_files.append(file_path)
                    self.subtitle_status[file_path] = "pending"
                    new_files_count += 1
        else:
            self.selected_files = files
            # Initialize status tracking for new files
            for file_path in files:
                self.subtitle_status[file_path] = "pending"
        self.update_video_table(log_count=False)  # Ensure the video table is up-to-date

        if append:
            if new_files_count > 0:
                self.log(f"Added {new_files_count} new files. Total: {len(self.selected_files)} files.", "info")
            else:
                self.log(f"No new files added. Total: {len(self.selected_files)} files.", "info")
        elif files:  # Only log if there are actually files
            self.log(f"Selected {len(files)} subtitle files.", "success")
        else:  # No files after filtering
            self.drop_area.clear_files() 
        
        if files:  # Update the table display
            self.drop_area.display_files(files, append=append)
        
        # Check if target folder is selected and has video files
        if self.target_folder and not self.adding_orphaned_files:
            settings = load_settings()
            auto_run = settings.get("auto_run", False)
            preview_mode = settings.get("preview_mode", True)
            
            if auto_run:
                self.run_renamer(preview_mode=False)
            elif preview_mode:
                self.run_renamer(preview_mode=True)
            # else: wait for user to click "Start Renaming"
        # else: wait for user to select target folder

    def run_renamer(self, preview_mode=False):
        if not self.target_folder:
            self.log("<b>Please select the destination folder first.</b>", "warning")
            return

        # Disable buttons during processing
        self.rename_all_btn.setEnabled(False)
        self.redo_btn.setEnabled(False)
        self.delete_completed_btn.setEnabled(False)
        self.delete_subs_btn.setEnabled(False)

        self.log("<b>Generating preview..." if preview_mode else "Processing...</b>", "info")

        def worker():
            try:
                settings = load_settings()
                auto_run = settings.get("auto_run", False)
                use_default_tag = settings.get("use_default_tag_if_found", False)
                always_prompt_tag = settings.get("always_prompt_tag_always", False)
                cache_per_set = settings.get("cache_per_set", True)
                conflict_policy_str = settings.get("conflict_policy", "ASK")
                runtime_state.set_cache_per_set(cache_per_set)
                runtime_state.set_apply_all_conflicts(settings.get("apply_all_conflicts", False))
                runtime_state.set_conflict_policy(conflict_policy_str)
                conflict_policy = sr.ConflictPolicy(conflict_policy_str)
                group_suffix_enabled = settings.get("group_suffix_enabled", True)
                lang_suffix_enabled = settings.get("lang_suffix_enabled", False)
                unknown_lang_action = settings.get("unknown_lang_action", "append")

                def ask_user_with_title(prompt: str, filename: str | None = None) -> str:
                    title = "Preview" if preview_mode else "SubApp"
                    return self.ask_user(prompt, title, filename)

                def conflict_resolver(source_path, dest_path, new_sub_name):
                    return self.ask_conflict(source_path, dest_path, new_sub_name, preview_mode)

                # Collect custom names from the table if not in preview mode
                custom_names = None
                if not preview_mode:
                    custom_names = self.drop_area.get_custom_names()

                pre_resolved_conflicts = None if preview_mode else (
                    dict(self.preview_conflict_decisions) if self.preview_conflict_decisions else None
                )
                in_place = self.rename_in_place_paths & set(self.selected_files) if not preview_mode else None

                config = sr.RenameConfig(
                    directory=self.target_folder,
                    src_ext=self.src_ext,
                    dst_ext=self.dst_ext,
                    cust_ext=self.cust_ext,
                    ask_fn=ask_user_with_title,
                    subtitle_files=self.selected_files,
                    video_files=self.get_current_video_files(),
                    auto_run=auto_run,
                    use_default_tag=use_default_tag,
                    always_prompt_tag=always_prompt_tag,
                    cache_per_set=cache_per_set,
                    cache_per_set_fn=runtime_state.get_cache_per_set,
                    conflict_policy=conflict_policy,
                    conflict_resolver_fn=conflict_resolver,
                    preview_mode=preview_mode,
                    ui_preview_mode=get_preview_mode(),
                    custom_names=custom_names,
                    pre_resolved_conflicts=pre_resolved_conflicts,
                    rename_in_place_sources=in_place,
                    group_suffix_enabled=group_suffix_enabled,
                    lang_suffix_enabled=lang_suffix_enabled,
                    unknown_lang_action=unknown_lang_action,
                )
                results = sr.run_job(config)
                
                if preview_mode:
                    # Preview mode: Update the table with preview data
                    preview_data = results.get('PREVIEW', [])
                    self.preview_update_signal.emit(preview_data)
                    self.preview_conflict_decisions = {
                        r["source_path"]: {"status": r["status"], "new_name": r.get("new_name", "")}
                        for r in preview_data
                        if r["status"] in ("OVERWRITE", "SUFFIX", "TAG", "SKIP")
                    }
                    ok_count = len([r for r in preview_data if r["status"] == "OK"])
                    overwrite_count = len([r for r in preview_data if r["status"] == "OVERWRITE"])
                    suffix_count = len([r for r in preview_data if r["status"] == "SUFFIX"])
                    tag_count = len([r for r in preview_data if r["status"] == "TAG"])
                    skip_count = len([r for r in preview_data if r["status"] == "SKIP"])
                    skip_exists_count = len([r for r in preview_data if r["status"] == "SKIP_EXISTS"])
                    failed_count = len([r for r in preview_data if r["status"] == "FAIL"])

                    parts = []
                    if ok_count:
                        parts.append(f"Ready: {ok_count}")
                    if overwrite_count:
                        parts.append(f"Overwrite: {overwrite_count}")
                    if suffix_count:
                        parts.append(f"Suffix: {suffix_count}")
                    if tag_count:
                        parts.append(f"Tag: {tag_count}")
                    if skip_count:
                        parts.append(f"Skip: {skip_count}")
                    if skip_exists_count:
                        parts.append(f"Same file: {skip_exists_count}")
                    if failed_count:
                        parts.append(f"Fail: {failed_count}")
                    if not parts:
                        parts.append("No items to preview")
                    self.log(f"<b>Preview complete! {', '.join(parts)}</b>", "success")

                    # Filter out non-actionable files from the rename list
                    non_actionable = [r["source_path"] for r in preview_data if r["status"] in ["FAIL", "SKIP_EXISTS", "SKIP"]]
                    if non_actionable:
                        self.selected_files = [f for f in self.selected_files if f not in non_actionable]
                        self.log(f"<b>Removed {len(non_actionable)} non-actionable files from rename list.</b>", "success")

                else:
                    self.status_update_signal.emit(results)
                    success_count = len(results.get('OK', []))
                    failed_count = len(results.get('FAIL', []))
                    cancelled_count = len(results.get('SKIPPED', []))
                    self.log(f"<b>Done! Success: {success_count}, Failed: {failed_count}, Skipped : {cancelled_count}</b>", "success")
                    logging.info(f"Success: {success_count}, Failed: {failed_count}, Skipped : {cancelled_count}")
                    self.preview_conflict_decisions.clear()
                
                self.job_completed_signal.emit()
            except Exception as e:
                self.log(f"<b>Error: {e}</b>", "error")
                logging.error(f"Error: {e}")
                # Still emit completion signal even on error
                self.job_completed_signal.emit()

        threading.Thread(target=worker, daemon=True).start()

    def ask_user(self, prompt: str, title: str = "SubApp", filename: str | None = None) -> str | None:
        """Tag/naming prompt dialog"""
        answer_box: dict[str, str | None] = {}
        done = threading.Event()

        def _do_dialog():
            settings = load_settings()

            dlg = QDialog(self)
            dlg.setWindowTitle(f"{title} - {filename}" if filename else title)
            dlg.setModal(True)

            layout = QVBoxLayout(dlg)
            link_color = self.current_theme["table_select_color"]

            studio_name = sr.extract_studio_name(filename) if filename else ""
            source_path = next((p for p in self.selected_files if os.path.basename(p) == filename), None) if filename else None
            if studio_name and source_path:
                prompt_with_link = prompt.replace(
                    studio_name,
                    f'<a href="open-sub"><span style="color:{link_color}; text-decoration:none;">{escape(filename)}</span></a>',
                    1,
                )
                label = QLabel(prompt_with_link, dlg)
                label.setWordWrap(False)
                label.setTextFormat(Qt.TextFormat.RichText)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
                label.setOpenExternalLinks(False)
                label.linkActivated.connect(lambda _href, p=source_path: reveal_in_explorer(p))
            else:
                label = QLabel(prompt, dlg)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                label.setWordWrap(False)
            label.setMinimumWidth(label.sizeHint().width())
            layout.addWidget(label)

            edit = FilenameLineEdit(dlg)
            edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            if studio_name and studio_name != sr.DEFAULT_TAG:
                edit.setPlaceholderText(studio_name)
            layout.addWidget(edit)

            cache_checkbox = QCheckBox("Apply to All Files in the Same Set", dlg)
            cache_checkbox.setChecked(runtime_state.get_cache_per_set())
            layout.addWidget(cache_checkbox)

            buttons = QDialogButtonBox(dlg)
            ok_btn = buttons.addButton("OK", QDialogButtonBox.ButtonRole.AcceptRole)
            skip_btn = buttons.addButton("Skip", QDialogButtonBox.ButtonRole.RejectRole)

            ok_btn.clicked.connect(dlg.accept)
            skip_btn.clicked.connect(dlg.reject)
            layout.addWidget(buttons)

            edit.setFocus()
            ok_btn.setDefault(True)

            dlg.setFixedHeight(dlg.sizeHint().height())

            result = dlg.exec()

            runtime_state.set_cache_per_set(cache_checkbox.isChecked())
            settings["cache_per_set"] = cache_checkbox.isChecked()

            save_settings(settings)

            self.cache_per_set_action.setChecked(cache_checkbox.isChecked())

            answer_box["val"] = edit.text() if result == QDialog.DialogCode.Accepted else None

        def _safe_dialog():
            try:
                _do_dialog()
            except Exception:
                logging.exception("ask_user dialog callback failed")
            finally:
                done.set()

        if QThread.currentThread() == self.thread():
            _safe_dialog()
        else:
            QMetaObject.invokeMethod(self, "_invoke", Qt.ConnectionType.QueuedConnection, Q_ARG(object, _safe_dialog))
            done.wait()

        return answer_box.get("val")

    def ask_conflict(self, source_path: str, dest_path: str, new_sub_name: str, preview_mode: bool = False) -> tuple[str, str | None, bool]:
        """
        Conflict resolution dialog (destination file exists).
        If the input field is empty, pressing Enter will trigger the suffix action.
        Returns (action, alt_path_or_None, apply_all) where action is
        "OVERWRITE", "SUFFIX", or "SKIP".
        """
        result_box: dict = {}
        done = threading.Event()

        def _do_dialog():
            title = "Preview - Name Conflict" if preview_mode else "Name Conflict"
            dlg = QDialog(self)
            dlg.setWindowTitle(title)
            dlg.setModal(True)

            layout = QVBoxLayout(dlg)
            link_color = self.current_theme["table_select_color"]

            src_label = QLabel(dlg)
            src_label.setWordWrap(True)
            src_label.setTextFormat(Qt.TextFormat.RichText)
            src_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            src_label.setOpenExternalLinks(False)
            src_name = escape(os.path.basename(source_path))
            src_label.setText(f'Source: <a href="open-src"><span style="color:{link_color}; text-decoration:none;">{src_name}</span></a>')
            src_label.linkActivated.connect(lambda _href, p=source_path: reveal_in_explorer(p))
            layout.addWidget(src_label)

            dst_label = QLabel(dlg)
            dst_label.setWordWrap(True)
            dst_label.setTextFormat(Qt.TextFormat.RichText)
            dst_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
            dst_label.setOpenExternalLinks(False)
            dst_name = escape(new_sub_name)
            dst_label.setText(f'Destination exists: <a href="open-dst"><span style="color:{link_color}; text-decoration:none;">{dst_name}</span></a>')
            dst_label.linkActivated.connect(lambda _href, p=dest_path: reveal_in_explorer(p))
            layout.addWidget(dst_label)

            tag_edit = FilenameLineEdit(dlg)
            tag_edit.setPlaceholderText("Enter custom suffix")
            tag_edit.setFocus()
            layout.addWidget(tag_edit)

            apply_all_cb = QCheckBox("Use This for All Conflicts of the Same Group", dlg)
            apply_all_cb.setChecked(runtime_state.get_apply_all_conflicts())
            layout.addWidget(apply_all_cb)

            buttons = QDialogButtonBox(dlg)
            overwrite_btn = buttons.addButton("Overwrite", QDialogButtonBox.ButtonRole.ActionRole)
            keepboth_btn = buttons.addButton("Keep Both", QDialogButtonBox.ButtonRole.ActionRole)
            tag_btn = buttons.addButton("Use Custom Suffix", QDialogButtonBox.ButtonRole.ActionRole)
            skip_btn = buttons.addButton("Skip", QDialogButtonBox.ButtonRole.RejectRole)
            overwrite_btn.setToolTip("Shortcut: Ctrl+1")
            keepboth_btn.setToolTip("Shortcut: Ctrl+2")
            tag_btn.setToolTip("Shortcut: Ctrl+3")
            skip_btn.setToolTip("Shortcut: Ctrl+4")
            layout.addWidget(buttons)

            chosen = {"action": "SKIP", "tag": ""}

            def on_overwrite():
                chosen["action"] = "OVERWRITE"
                dlg.accept()
            def on_keepboth():
                chosen["action"] = "SUFFIX"
                dlg.accept()
            def on_tag():
                tag_text = tag_edit.text().strip().strip(".")
                if not tag_text:
                    QMessageBox.warning(dlg, "Empty Tag", "Please enter a valid tag")
                    tag_edit.setFocus()
                    return
                chosen["tag"] = tag_text
                chosen["action"] = "TAG"
                dlg.accept()
            def on_skip():
                chosen["action"] = "SKIP"
                dlg.reject()
            def on_enter():
                on_tag()

            overwrite_btn.clicked.connect(on_overwrite)
            keepboth_btn.clicked.connect(on_keepboth)
            tag_btn.clicked.connect(on_tag)
            skip_btn.clicked.connect(on_skip)

            # Capture Enter on the input itself so it cannot fall through to dialog buttons.
            return_shortcut = QShortcut(QKeySequence("Return"), tag_edit)
            enter_shortcut = QShortcut(QKeySequence("Enter"), tag_edit)
            return_shortcut.activated.connect(on_enter)
            enter_shortcut.activated.connect(on_enter)

            shortcut_overwrite = QShortcut(QKeySequence("Ctrl+1"), dlg)
            shortcut_keepboth = QShortcut(QKeySequence("Ctrl+2"), dlg)
            shortcut_tag = QShortcut(QKeySequence("Ctrl+3"), dlg)
            shortcut_skip = QShortcut(QKeySequence("Ctrl+4"), dlg)
            shortcut_overwrite.activated.connect(on_overwrite)
            shortcut_keepboth.activated.connect(on_keepboth)
            shortcut_tag.activated.connect(on_tag)
            shortcut_skip.activated.connect(on_skip)

            dlg.exec()

            action = chosen["action"]
            alt_path = None
            apply_all = apply_all_cb.isChecked()

            runtime_state.set_apply_all_conflicts(apply_all)
            settings = load_settings()
            settings["apply_all_conflicts"] = apply_all
            save_settings(settings)

            self.apply_all_conflicts_action.setChecked(apply_all)

            if action == "TAG":
                video_base = os.path.splitext(new_sub_name)[0]
                ext = os.path.splitext(source_path)[1]
                alt_name = f"{video_base}.{chosen['tag']}{ext}"
                alt_path = os.path.join(os.path.dirname(dest_path), alt_name)

            result_box["val"] = (action, alt_path, apply_all)

        def _safe_dialog():
            try:
                _do_dialog()
            except Exception:
                logging.exception("ask_conflict dialog callback failed")
            finally:
                done.set()

        if QThread.currentThread() == self.thread():
            _safe_dialog()
        else:
            QMetaObject.invokeMethod(self, "_invoke", Qt.ConnectionType.QueuedConnection, Q_ARG(object, _safe_dialog))
            done.wait()

        return result_box.get("val", ("SKIP", None, False))

    def log_async(self, html, category="info"):
        """Emit a categorized log message"""
        self.log_signal.emit(html, category)
    
    def log(self, message, category="info"):
        """
        Categories:
        - info: General information (file counts, auto-selected formats, etc.)
        - success: Successful operations (completed tasks, successful renames)
        - warning: User needs attention (no folder selected, no files, etc.)
        - error: Errors and exceptions
        - system: Critical system messages (cannot be disabled)
        """
        self.log_signal.emit(message, category)

    def open_subtitle_folder(self):
        last_folder = get_last_subtitle_folder()
        files, _ = QFileDialog.getOpenFileNames(self, "Select Subtitle Files", last_folder, get_subtitle_file_filter())
        self.drop_area.accept_files(files)

    def update_video_table(self, log_count=False):
        # Determine extension to use for listing videos
        if getattr(self, 'video_table_locked', False):  # Do not auto-repopulate since user has edited the table
            return
        ext_text = self.dst_edit.currentText().strip()
        if not self.target_folder:
            self.video_drop_area.clear_files()
            return
            
        # Get all video file counts by extension
        video_ext_counts = self.get_extension_counts(self.target_folder, get_all_video_extensions())
        
        # Resolve Auto against ALL supported extensions (built-in + custom)
        if not ext_text or ext_text == "Auto":
            all_video_files = [
                os.path.join(self.target_folder, f)
                for f in os.listdir(self.target_folder)
                if any(f.lower().endswith(e) for e in get_all_video_extensions())
            ]
            ext = self.get_comm_ext(all_video_files, get_all_video_extensions()) or '.mp4'
            self.dst_ext = ext
            files = [
                os.path.join(self.target_folder, f)
                for f in os.listdir(self.target_folder)
                if f.lower().endswith(ext.lower())
            ]
        elif ext_text == "All":
            self.dst_ext = get_enabled_dst_ext()
            files = [
                os.path.join(self.target_folder, f)
                for f in os.listdir(self.target_folder)
                if any(f.lower().endswith(e) for e in get_enabled_dst_ext())
            ]
        else:
            ext = ext_text
            self.dst_ext = ext
            files = [
                os.path.join(self.target_folder, f)
                for f in os.listdir(self.target_folder)
                if f.lower().endswith(ext.lower())
            ]
        
        self.video_drop_area.display_files(sorted(files)) if files else self.video_drop_area.clear_files()
            
        # Log all video file types found when requested
        if log_count and video_ext_counts:
            # Log each file type found
            for ext_type, count in sorted(video_ext_counts.items()):
                self.log(f"Found {count} video ({ext_type}) files.", "info")

    def update_subtitle_count(self):
        """Log the count of subtitle files in the target folder"""
        if not self.target_folder:
            return
        ext_text = self.src_edit.currentText().strip()
        if not ext_text:
            return
            
        # Get all subtitle file counts by extension
        subtitle_ext_counts = self.get_extension_counts(self.target_folder, get_all_subtitle_extensions())
        
        if ext_text == "Auto":
            files_all = [f for f in os.listdir(self.target_folder) if any(f.lower().endswith(e) for e in get_all_subtitle_extensions())]
            ext = self.get_comm_ext([os.path.join(self.target_folder, f) for f in files_all], get_all_subtitle_extensions()) or '.ass'
        else:
            ext = ext_text
        self.src_ext = ext
        
        if subtitle_ext_counts:  # Log all subtitle file types found
            # Log each file type found
            for ext_type, count in sorted(subtitle_ext_counts.items()):
                self.log(f"Found {count} subtitle ({ext_type}) files.", "success")

    def update_subtitle_status_display(self):
        """Update the visual status of subtitle files in the table"""
        try:
            for row in range(self.drop_area.table.rowCount()):
                path_item = self.drop_area.table.item(row, SubCol.PATH)
                status_item = self.drop_area.table.item(row, SubCol.STATUS)
                if path_item and status_item:
                    file_path = path_item.text()
                    status = self.subtitle_status.get(file_path, "pending")
                    
                    if status == "success":
                        status_text = "✅" if get_compact_mode() else "✅ Success"
                        status_item.setText(status_text)
                        color = QColor(self.current_theme['success_color'])
                    elif status == "failed":
                        status_text = "❌" if get_compact_mode() else "❌ Failed"
                        status_item.setText(status_text)
                        color = QColor(self.current_theme['error_color'])
                    elif status == "skipped":
                        status_text = "🚫" if get_compact_mode() else "🚫 Skipped "
                        status_item.setText(status_text)
                        color = QColor(self.current_theme['warning_color'])
                    else:  # pending
                        status_text = "⏳" if get_compact_mode() else "⏳ Pending"
                        status_item.setText(status_text)
                        color = QColor(self.current_theme['text_color'])
                    
                    for col in range(self.drop_area.table.columnCount()):
                        item = self.drop_area.table.item(row, col)
                        if item:
                            item.setForeground(color)
            
            # Resize columns to fit content dynamically
            self.drop_area.table.resizeColumnsToContents()
        except Exception as e:
            self.log(f"<b>Error updating status display: {e}</b>", "error")

    def update_status_from_signal(self, results):
        """Update status tracking and display from signal"""
        try:
            # Update status tracking
            for file_path in results.get("OK", []):
                self.subtitle_status[file_path] = "success"
            for file_path in results.get("FAIL", []):
                self.subtitle_status[file_path] = "failed"
            for file_path in results.get("SKIPPED", []):
                self.subtitle_status[file_path] = "skipped"

            # Apply in-place renames: remap table rows to new paths
            for entry in results.get("RENAMED_PATHS", []):
                old_path = entry["source_path"]
                new_path = entry["new_path"]
                new_name = os.path.basename(new_path)

                if old_path in self.selected_files:
                    idx = self.selected_files.index(old_path)
                    self.selected_files[idx] = new_path

                if old_path in self.subtitle_status:
                    self.subtitle_status[new_path] = self.subtitle_status.pop(old_path)

                self.rename_in_place_paths.discard(old_path)

                for row in range(self.drop_area.table.rowCount()):
                    path_item = self.drop_area.table.item(row, SubCol.PATH)
                    if path_item and path_item.text() == old_path:
                        name_item = self.drop_area.table.item(row, SubCol.FILE_NAME)
                        if name_item:
                            name_item.setText(new_name)
                            name_item.setToolTip(new_path)
                        path_item.setText(new_path)
                        break

            # Update UI with results
            self.update_subtitle_status_display()
        except Exception as e:
            self.log(f"<b>Error updating status from signal: {e}</b>", "error")

    def update_preview_in_table(self, preview_data):
        """Update the subtitle table with preview data"""
        self.drop_area.update_preview(preview_data)

    def on_dst_ext_changed(self):
        """Update video table if video extension changes"""
        if getattr(self, 'video_table_locked', False):
            return
        self.update_video_table(log_count=False)

    def remove_src_files_from_table(self):
        selected_rows = set(item.row() for item in self.drop_area.table.selectedItems())
        
        if selected_rows:  # Remove selected rows
            rows_to_remove = sorted(selected_rows, reverse=True)  # Avoid index shifting
            
            # Remove from tracking and table
            for row in rows_to_remove:
                path_item = self.drop_area.table.item(row, SubCol.PATH)
                if path_item and path_item.text() in self.selected_files:
                    self.selected_files.remove(path_item.text())
                if path_item and path_item.text() in self.subtitle_status:
                    del self.subtitle_status[path_item.text()]
                self.drop_area.table.removeRow(row)
            
            # Update display 
            if self.drop_area.table.rowCount() == 0:
                self.drop_area.clear_files()
            else:
                self.drop_area.table.resizeColumnsToContents()
                # self.update_subtitle_status_display()
            
            self.log(f"<b>Removed {len(rows_to_remove)} selected files from table.</b>", "debug")
 
        else:  # Remove all files
            self.selected_files = []
            self.subtitle_status.clear()
            self.drop_area.clear_files()
            self.log("<b>All files deleted from table.</b>", "debug")

    def update_remove_button_text(self):
        """Update the delete button text based on selection"""
        selected_rows = set(item.row() for item in self.drop_area.table.selectedItems())
        
        if get_compact_mode():
            self.delete_all_btn.setText("🗑️")
        elif selected_rows:
            self.delete_all_btn.setText("🗑️ Remove Selected")
            self.delete_all_btn.setToolTip(f"Remove {len(selected_rows)} selected files from the table")
        else:
            self.delete_all_btn.setText("🗑️ Remove All")
            self.delete_all_btn.setToolTip("Remove all subtitle files from the table")

    def update_delete_src_button_text(self):
        """Update the delete source button text based on selection"""
        selected_rows = set(item.row() for item in self.drop_area.table.selectedItems())
        if get_compact_mode():
            self.delete_all_btn.setText("🗑️")
        elif selected_rows:
            self.delete_src_btn.setText("🗑️ Delete Selected")
            self.delete_src_btn.setToolTip(f"Move {len(selected_rows)} selected subtitle files to recycle bin")
        else:
            self.delete_src_btn.setText("🗑️ Delete All Subs")
            self.delete_src_btn.setToolTip("Move all subtitle files to recycle bin")

    def on_selection_changed(self):
        """Handle table selection changes - update both remove and delete button texts"""
        self.update_remove_button_text()
        self.update_delete_src_button_text()

    def remove_all_videos(self):
        selected_rows = set(item.row() for item in self.video_drop_area.table.selectedItems())
        
        if selected_rows:  # Remove selected rows
            rows_to_remove = sorted(selected_rows, reverse=True)  # Avoid index shifting
            
            for row in rows_to_remove:  # Remove from table
                self.video_drop_area.table.removeRow(row)
            self.video_table_locked = True  # Lock table to prevent auto refresh since user edited it
            if self.video_drop_area.table.rowCount() == 0: 
                self.video_drop_area.clear_files()
            else:
                self.video_drop_area.table.resizeColumnsToContents()
            
            self.log(f"Removed {len(rows_to_remove)} selected video files from table.", "success")

        else:  # Remove all files
            self.video_drop_area.clear_files()
            self.target_folder = None
            self.target_label.setText("No Destination Folder Selected.")
            self.log("All video files removed from table.", "success")
        
        self.update_video_remove_button_text()

    def update_video_remove_button_text(self):
        """Update the video remove button text based on selection"""
        selected_rows = set(item.row() for item in self.video_drop_area.table.selectedItems())
        if selected_rows:
            self.remove_videos_btn.setText("🗑️ Remove Selected")
            self.remove_videos_btn.setToolTip(f"Remove {len(selected_rows)} selected video files from the table")
        else:
            self.remove_videos_btn.setText("🗑️ Remove All Videos")
            self.remove_videos_btn.setToolTip("Remove all video files from the table")

    def delete_completed_files(self):
        completed_files = [file_path for file_path, status in self.subtitle_status.items() if status == "success"]
        
        if not completed_files:
            self.log("<b>No completed files to remove.</b>", "warning")
            return
        
        # Remove from selected_files list
        self.selected_files = [f for f in self.selected_files if f not in completed_files]
        
        for file_path in completed_files:  # Remove from status tracking
            del self.subtitle_status[file_path]
        
        # Remove from table display
        rows_to_remove = []
        for row in range(self.drop_area.table.rowCount()):
            path_item = self.drop_area.table.item(row, SubCol.PATH)
            if path_item and path_item.text() in completed_files:
                rows_to_remove.append(row)
        
        # Remove rows in reverse order to avoid index shifting
        for row in reversed(rows_to_remove):
            self.drop_area.table.removeRow(row)
        
        # If table is now empty, switch back to drop area view
        if self.drop_area.table.rowCount() == 0:
            self.drop_area.clear_files()
        else:  # Update display for remaining files
            self.update_subtitle_status_display()
        self.log(f"Removed {len(completed_files)} completed files from table.", "success")

    def redo_failed(self):
        failed_files = [file_path for file_path, status in self.subtitle_status.items() if status == "failed"]
        cancelled_files = [file_path for file_path, status in self.subtitle_status.items() if status == "skipped"]
        retry_files = failed_files + cancelled_files
        
        if not retry_files:
            self.log("<b>No failed or skipped files to retry.</b>", "warning")
            return
        if not self.target_folder:
            self.log("<b>Please select the destination folder first.</b>", "warning")
            return
        
        for file_path in retry_files:  # Reset status to pending for retry and update display
            self.subtitle_status[file_path] = "pending"
        self.update_subtitle_status_display()  # Update display to show pending status
        
        def worker():
            try:
                settings = load_settings()
                auto_run = settings.get("auto_run", False)
                use_default_tag = settings.get("use_default_tag_if_found", False)
                always_prompt_tag = settings.get("always_prompt_tag_always", False)
                cache_per_set = settings.get("cache_per_set", True)
                conflict_policy_str = settings.get("conflict_policy", "ASK")
                runtime_state.set_cache_per_set(cache_per_set)
                runtime_state.set_apply_all_conflicts(settings.get("apply_all_conflicts", False))
                runtime_state.set_conflict_policy(conflict_policy_str)
                conflict_policy = sr.ConflictPolicy(conflict_policy_str)
                group_suffix_enabled = settings.get("group_suffix_enabled", True)
                lang_suffix_enabled = settings.get("lang_suffix_enabled", False)
                unknown_lang_action = settings.get("unknown_lang_action", "append")

                # Collect custom names from the table for retry
                custom_names = self.drop_area.get_custom_names()

                def ask_user_with_title_retry(prompt: str, filename: str | None = None) -> str:
                    title = "SubApp"
                    return self.ask_user(prompt, title, filename)

                def conflict_resolver_retry(source_path, dest_path, new_sub_name):
                    return self.ask_conflict(source_path, dest_path, new_sub_name, False)

                in_place_retry = self.rename_in_place_paths & set(retry_files) if self.rename_in_place_paths else None

                retry_config = sr.RenameConfig(
                    directory=self.target_folder,
                    src_ext=self.src_ext,
                    dst_ext=self.dst_ext,
                    cust_ext=self.cust_ext,
                    ask_fn=ask_user_with_title_retry,
                    subtitle_files=retry_files,
                    auto_run=auto_run,
                    use_default_tag=use_default_tag,
                    always_prompt_tag=always_prompt_tag,
                    cache_per_set=cache_per_set,
                    cache_per_set_fn=runtime_state.get_cache_per_set,
                    conflict_policy=conflict_policy,
                    conflict_resolver_fn=conflict_resolver_retry,
                    custom_names=custom_names,
                    group_suffix_enabled=group_suffix_enabled,
                    lang_suffix_enabled=lang_suffix_enabled,
                    unknown_lang_action=unknown_lang_action,
                    rename_in_place_sources=in_place_retry,
                    ui_preview_mode=get_preview_mode(),
                )
                results = sr.run_job(retry_config)
                self.status_update_signal.emit(results)
                success_count = len(results.get('OK', []))
                failed_count = len(results.get('FAIL', []))
                cancelled_count = len(results.get('SKIPPED', []))
                self.log(f"<b>Retry complete! Success: {success_count}, Failed: {failed_count}, Skipped: {cancelled_count}</b>", "success")
                logging.info(f"Retry complete! Success: {success_count}, Failed: {failed_count}, Skipped: {cancelled_count}")
                self.job_completed_signal.emit()
            except Exception as e:
                self.log(f"<b>Error during retry: {e}</b>", "error")
                logging.error(f"Error during retry: {e}")
                # Still emit completion signal even on error
                self.job_completed_signal.emit()
        
        threading.Thread(target=worker, daemon=True).start()

    def rename_all_files(self):
        if not self.selected_files:
            self.log("<b>No files to rename.</b>", "warning")
            return
        self.run_renamer(preview_mode=False)

    def delete_all_subs(self):
        if not self.target_folder:
            self.log("<b>No destination folder selected.</b>", "warning")
            return
        count = 0
        enabled_subtitle_exts = get_enabled_src_ext()
        for filename in os.listdir(self.target_folder):
            if any(filename.lower().endswith(ext) for ext in enabled_subtitle_exts):
                try:
                    # Use os.path.normpath to properly handle the path
                    file_path = os.path.normpath(os.path.join(self.target_folder, filename))
                    
                    # Check if file exists before trying to move it
                    if os.path.exists(file_path):
                        send2trash(file_path)
                        count += 1
                    else:
                        self.log(f"<b>File not found: {filename}</b>", "warning")
                except Exception as e:
                    self.log(f"<b>Error moving {filename} to recycle bin: {e}</b>")
        self.log(f"<b>Moved {count} subtitle files to recycle bin.</b>", "success")
        self.update_video_table()

    def delete_all_subs_from_src_table(self):
        """Delete selected or all subtitle files from the source table and optionally empty folders"""
        if not self.selected_files:
            self.log("<b>No subtitle files selected.</b>", "warning")
            return
        selected_rows = set(item.row() for item in self.drop_area.table.selectedItems())  # Get selected rows from the table
        
        # Determine which files to delete
        files_to_delete = []
        if selected_rows:
            # Delete only selected files
            for row in selected_rows:
                if row < self.drop_area.table.rowCount():
                    path_item = self.drop_area.table.item(row, SubCol.PATH)
                    if path_item:
                        files_to_delete.append(path_item.text())
        else:
            # Delete all files in the table
            for row in range(self.drop_area.table.rowCount()):
                path_item = self.drop_area.table.item(row, SubCol.PATH)
                if path_item:
                    files_to_delete.append(path_item.text())
        
        if not files_to_delete:
            self.log("<b>No files to delete.</b>", "warning")
            return
        
        # Delete files and track folders
        folders_to_check = set()
        enabled_subtitle_exts = get_enabled_src_ext()
        deleted_files = []  # Track which files were actually deleted
        
        for file_path in files_to_delete:
            try:
                normalized_path = os.path.normpath(file_path)
                # Check if file exists and is a subtitle file
                if os.path.exists(normalized_path):
                    file_ext = os.path.splitext(normalized_path)[1].lower()
                    if file_ext in enabled_subtitle_exts:
                        # Track the folder for empty folder check
                        folder_path = os.path.dirname(normalized_path)
                        folders_to_check.add(folder_path)
                        send2trash(normalized_path)
                        deleted_files.append(file_path)  # Track successful deletion
                        
                        # Remove from selected_files and subtitle_status
                        if file_path in self.selected_files:
                            self.selected_files.remove(file_path)
                        if file_path in self.subtitle_status:
                            del self.subtitle_status[file_path]
                else:
                    self.log(f"<b>File not found: {os.path.basename(file_path)}</b>", "warning")
            except Exception as e:
                self.log(f"<b>Error deleting {os.path.basename(file_path)}: {e}</b>", "error")
        
        # Check and move empty folders to recycle bin if setting is enabled
        folders_deleted = 0
        if get_delete_empty_folders():
            for folder_path in folders_to_check:
                try:
                    normalized_folder_path = os.path.normpath(folder_path)  # Normalize the folder path
                    if os.path.exists(normalized_folder_path) and not os.listdir(normalized_folder_path):
                        send2trash(normalized_folder_path)
                        folders_deleted += 1
                        self.log(f"<b>Moved empty folder to recycle bin: {os.path.basename(folder_path)}</b>", "success")
                except Exception as e:
                    self.log(f"<b>Error moving empty folder to recycle bin {os.path.basename(folder_path)}: {e}</b>", "error")
        
        # Update UI
        if selected_rows:
            # Remove the selected rows only
            rows_to_remove = sorted(selected_rows, reverse=True)  # Avoid index shifting
            for row in rows_to_remove:
                if row < self.drop_area.table.rowCount():
                    path_item = self.drop_area.table.item(row, SubCol.PATH)
                    if path_item and path_item.text() in deleted_files:
                        self.drop_area.table.removeRow(row)  # Remove successfully deleted files
            
            if self.drop_area.table.rowCount() == 0:
                self.drop_area.clear_files()
            else:
                self.drop_area.table.resizeColumnsToContents()
        else:
            self.drop_area.clear_files()
        
        self.update_video_table()
        
        # debug
        if folders_deleted > 0:
            self.log(f"<b>Moved {len(deleted_files)} subtitle files and {folders_deleted} empty folders to recycle bin.</b>", "success")
        else:
            self.log(f"<b>Moved {len(deleted_files)} subtitle files to recycle bin.</b>", "success")

    def open_target_in_explorer(self):
        """Open the last target folder in Windows Explorer"""
        settings = load_settings()
        last_target_folder = settings.get("last_target_folder", "")
        
        if last_target_folder and os.path.exists(last_target_folder):
            try:
                reveal_in_explorer(last_target_folder)
            except Exception as e:
                self.log(f"<b>Error opening folder: {e}</b>", "error")
        else:
            self.log("<b>No valid destination folder selected.</b>", "warning")

    def open_user_data_folder(self):
        # ensure dirs exist
        ap.config_dir(create=True)
        ap.addons_dir(create=True)
        ap.plugin_data_root_dir(create=True)
        ap.log_dir(create=True)

        # root user-data folder
        user_root = ap.config_dir(False).parent
        reveal_in_explorer(str(user_root))

    def toggle_video_table(self):
        """Toggle the visibility of the video drop area"""
        if self.video_drop_area.isVisible():
            self.video_drop_area.hide()
            self.target_controls_container.show()
            self.show_video_table_action.setChecked(False)
        else:
            self.video_drop_area.show()
            self.target_controls_container.show()
            self.show_video_table_action.setChecked(True)
        
        # Adjust splitter to collapse/expand the video area space when toggled
        sizes = self.main_splitter.sizes()
        if sizes and len(sizes) == 3:
            video_size, sub_size, log_size = sizes
            if not self.video_drop_area.isVisible():
                controls_height = self.target_controls_container.sizeHint().height()
                spare = video_size - controls_height
                if spare > 0:
                    sub_size += spare
                    video_size = controls_height
            else:
                total = sum(sizes)
                desired_video = max(150, int(total * 0.25))
                delta = desired_video - video_size
                if delta != 0:
                    sub_size = max(100, sub_size - delta)
                    video_size = desired_video
            self.main_splitter.setSizes([video_size, sub_size, log_size])
        
        settings = load_settings()
        settings["show_video_table"] = self.video_drop_area.isVisible()
        save_settings(settings)

    def toggle_log(self):
        """Toggle the visibility of the log box"""
        if self.log_box.isVisible():
            self.log_box.hide()
            self.show_log_action.setChecked(False)
        else:
            self.log_box.show()
            self.show_log_action.setChecked(True)
        settings = load_settings()
        settings["show_log"] = self.log_box.isVisible()
        save_settings(settings)

    def toggle_preview_mode(self):
        """Toggle preview mode on/off from the Preference menu. Also check/uncheck Show Preview Name and Show Preview Status to match."""
        settings = load_settings()
        enabled = self.preview_mode_action.isChecked()
        settings["preview_mode"] = enabled
        settings["show_preview_name_column"] = enabled
        settings["show_preview_status_column"] = enabled
        save_settings(settings)

        self.show_preview_name_action.setChecked(enabled)
        self.show_preview_status_action.setChecked(enabled)
        self.apply_preview_visibility()

        if enabled and getattr(self, "selected_files", None):
            if len(self.selected_files) > 0:
                self.run_renamer(preview_mode=True)

    def toggle_preview_name_column(self):
        """Toggle visibility of the 'New Name' column independently."""
        settings = load_settings()
        settings["show_preview_name_column"] = self.show_preview_name_action.isChecked()
        save_settings(settings)
        self.apply_preview_visibility()

    def toggle_preview_status_column(self):
        """Toggle visibility of the 'Preview Status' column independently."""
        settings = load_settings()
        settings["show_preview_status_column"] = self.show_preview_status_action.isChecked()
        save_settings(settings)
        self.apply_preview_visibility()

    def toggle_compact_mode(self):
        """Toggle compact mode on/off"""
        compact_mode = self.compact_mode_action.isChecked()
        set_compact_mode(compact_mode)
        
        if compact_mode:
            self.apply_compact_mode()
        else:
            self.remove_compact_mode()

    def apply_preview_visibility(self):
        try:
            settings = load_settings()
            preview_mode = settings.get("preview_mode", True)
            show_name = settings.get("show_preview_name_column", preview_mode)
            show_status = settings.get("show_preview_status_column", preview_mode)
            self.drop_area.table.setColumnHidden(1, not (preview_mode and show_name))
            self.drop_area.table.setColumnHidden(3, not (preview_mode and show_status))
            self.drop_area.table.setHorizontalHeaderLabels(["File Name", "New Name", "Path", "Preview", "Status"])
        except Exception:
            pass
        
    def apply_compact_mode(self):
        """Apply compact mode - remove text from buttons and status text"""
        if not hasattr(self, 'original_button_texts'):
            self.original_button_texts = {}
            self.original_button_texts['remove_videos_btn'] = self.remove_videos_btn.text()
            self.original_button_texts['target_btn'] = self.target_btn.text()
            self.original_button_texts['open_folder_btn'] = self.open_folder_btn.text()
            self.original_button_texts['delete_all_btn'] = self.delete_all_btn.text()
            self.original_button_texts['delete_completed_btn'] = self.delete_completed_btn.text()
            self.original_button_texts['redo_btn'] = self.redo_btn.text()
            self.original_button_texts['rename_all_btn'] = self.rename_all_btn.text()
            self.original_button_texts['delete_subs_btn'] = self.delete_subs_btn.text()
            self.original_button_texts['delete_src_btn'] = self.delete_src_btn.text()
        
        # Remove text from buttons
        self.target_btn.setText("📁")
        self.remove_videos_btn.setText("🗑️")
        self.open_folder_btn.setText("📂")
        self.delete_all_btn.setText("🗑️")
        self.delete_completed_btn.setText("✅")
        self.redo_btn.setText("🔄")
        self.rename_all_btn.setText("🚀")
        self.delete_subs_btn.setText("🗑️")
        self.delete_src_btn.setText("🗑️")
        
        # Update table headers
        self.drop_area.table.setHorizontalHeaderLabels(["File Name", "New Name", "Path", "Preview", "Status"])
        self.video_drop_area.table.setHorizontalHeaderLabels(["Video File Name", "Size"])

    def remove_compact_mode(self):
        """Remove compact mode - restore original button texts"""
        if hasattr(self, 'original_button_texts'):
            self.remove_videos_btn.setText(self.original_button_texts['remove_videos_btn'])
            self.target_btn.setText(self.original_button_texts['target_btn'])
            self.open_folder_btn.setText(self.original_button_texts['open_folder_btn'])
            self.delete_all_btn.setText(self.original_button_texts['delete_all_btn'])
            self.delete_completed_btn.setText(self.original_button_texts['delete_completed_btn'])
            self.redo_btn.setText(self.original_button_texts['redo_btn'])
            self.rename_all_btn.setText(self.original_button_texts['rename_all_btn'])
            self.delete_subs_btn.setText(self.original_button_texts['delete_subs_btn'])
            self.delete_src_btn.setText(self.original_button_texts['delete_src_btn'])
        
        # Restore table headers
        self.drop_area.table.setHorizontalHeaderLabels(["File Name", "New Name", "Path", "Preview", "Status"])
        self.video_drop_area.table.setHorizontalHeaderLabels(["Video File Name", "Size (MB)"])

    def analyze_target_folder(self):
        """Analyze the target folder and show detailed information"""
        if not self.target_folder:
            self.log("<b>No destination folder selected.</b>", "warning")
            return
        
        try:
            video_ext = self.dst_edit.currentText().strip()
            sub_ext = self.src_edit.currentText().strip()
            
            video_files = [f for f in os.listdir(self.target_folder) if f.lower().endswith(video_ext.lower())]
            sub_files = [f for f in os.listdir(self.target_folder) if f.lower().endswith(sub_ext.lower())]
            
            total_size = sum(os.path.getsize(os.path.join(self.target_folder, f)) for f in video_files + sub_files)
            total_size_mb = total_size / (1024 * 1024)
            
            self.log(f"<b> Folder Analysis:</b>", "system")
            self.log(f"• Folder: {self.target_folder}", "system")
            self.log(f"• Video files ({video_ext}): {len(video_files)} files", "system")
            self.log(f"• Subtitle files ({sub_ext}): {len(sub_files)} files", "system")
            self.log(f"• Total size: {total_size_mb:.2f} MB", "system")
            self.log(f"• Video files: {', '.join(video_files[:5])}{'...' if len(video_files) > 5 else ''}", "system")
            self.log(f"• Subtitle files: {', '.join(sub_files[:5])}{'...' if len(sub_files) > 5 else ''}", "system")
            
        except Exception as e:
            self.log(f"<b>Error analyzing folder: {e}</b>", "error")

    def show_about(self):
        """Show the about dialog."""
        # ── helpers (unchanged from original) ──────────────────────────
        def get_package_version(package_name):
            try:
                import importlib.metadata
                return importlib.metadata.version(package_name)
            except Exception:
                try:
                    import pkg_resources
                    return pkg_resources.get_distribution(package_name).version
                except Exception:
                    return "Not installed"

        def get_os_name() -> str:
            if is_windows():
                try:
                    import winreg
                    path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
                        def read(name, default=""):
                            try:
                                return winreg.QueryValueEx(key, name)[0]
                            except OSError:
                                return default
                        build = read("CurrentBuildNumber") or read("CurrentBuild")
                        try:
                            is_win11 = int(build) >= 22000 if build else False
                        except (ValueError, TypeError):
                            is_win11 = False
                        ver = read("DisplayVersion") or read("ReleaseId")
                        product = "Windows 11" if is_win11 else read("ProductName", "Windows")
                        return f"{product}, version {ver}" if ver else product
                except Exception:
                    return f"Windows {platform.release()}"
            if is_macos():
                try:
                    name = subprocess.check_output(["sw_vers", "-productName"], text=True).strip()
                    ver  = subprocess.check_output(["sw_vers", "-productVersion"], text=True).strip()
                    return f"{name} {ver}"
                except Exception:
                    return f"macOS (Darwin {platform.release()})"
            if is_linux():
                os_release = Path("/etc/os-release")
                if os_release.exists():
                    try:
                        kv = {}
                        for line in os_release.read_text(encoding="utf-8", errors="ignore").splitlines():
                            if "=" in line and not line.strip().startswith("#"):
                                k, v = line.split("=", 1)
                                kv[k] = v.strip().strip('"')
                        if kv.get("PRETTY_NAME"):
                            return kv["PRETTY_NAME"]
                        return f"{kv.get('NAME', 'Linux')} {kv.get('VERSION') or kv.get('VERSION_ID', '')}".strip()
                    except Exception:
                        pass
                return f"Linux {platform.release()}"
            return f"{platform.system()} {platform.release()}".strip()

        def get_arch_label() -> str:
            m = (platform.machine() or "").lower()
            if m in ("amd64", "x86_64"):   return "x64"
            if m in ("arm64", "aarch64"):  return "arm64"
            if m in ("i386", "i686", "x86"): return "x86"
            return platform.machine() or "unknown"

        # ── data ───────────────────────────────────────────────────────
        app_version  = "1.0"
        release_date = "2025-12-25"
        release_year = release_date[:4]

        pyqt6_ver     = get_package_version("PyQt6")
        guessit_ver   = get_package_version("guessit")
        rapidfuzz_ver = get_package_version("rapidfuzz")
        send2trash_ver= get_package_version("send2trash")
        python_ver    = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        platform_info = f"{get_os_name()} ({get_arch_label()})"

        # ── dialog ────────────────────────────────────────────────────
        theme = self.current_theme
        dlg = QDialog(self)
        dlg.setWindowTitle("About SubRename")
        dlg.setModal(True)
        dlg.resize(480, 420)
        dlg.setStyleSheet(generate_stylesheet(theme))

        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setFrameShape(QFrame.Shape.NoFrame)
        browser.setHtml(f"""
    <style type="text/css">
    a, a:link, a:visited {{ text-decoration: none; }}
    </style>
    <h2 style="margin-bottom:2px;">SubRename &nbsp;<span style="font-size:14px;font-weight:normal;">v{app_version}</span></h2>
    <p style="margin-top:0;color:grey;">Released {release_date}</p>
    <p>A subtitle renamer for TV series and movies.<br>
    Handles multiple subtitle groups, conflict resolution, and preview mode.</p>
    <p>
        <a href="https://github.com/alanyung-yl/SubApp">GitHub</a> &nbsp;·&nbsp;
        <a href="https://github.com/alanyung-yl/SubApp/blob/main/README.md">Documentation</a> &nbsp;·&nbsp;
        <a href="https://github.com/alanyung-yl/SubApp/blob/main/docs/ROADMAP.md">Roadmap</a> &nbsp;·&nbsp;
        <a href="https://github.com/alanyung-yl/SubApp/blob/main/docs/KNOWN_ISSUES.md">Known Issues</a>
    </p>
    <hr/>
    <h3>Dependencies</h3>
    <table cellspacing="4">
        <tr><td>PyQt6</td>        <td>{pyqt6_ver}</td></tr>
        <tr><td>guessit</td>      <td>{guessit_ver}</td></tr>
        <tr><td>rapidfuzz</td>    <td>{rapidfuzz_ver}</td></tr>
        <tr><td>send2trash</td>   <td>{send2trash_ver}</td></tr>
    </table>
    <hr/>
    <h3>System</h3>
    <table cellspacing="4">
        <tr><td>Python</td>   <td>{python_ver}</td></tr>
        <tr><td>Platform</td> <td>{platform_info}</td></tr>
    </table>
    <hr/>
    <p style="color:grey;font-size:11px;">&copy; {release_year} SubRename &nbsp;·&nbsp; Built with PyQt6</p>
    """)
        layout.addWidget(browser)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.accept)
        layout.addWidget(buttons)

        dlg.exec()


    def show_help(self):
        """Show the help dialog."""
        theme = self.current_theme
        dlg = QDialog(self)
        dlg.setWindowTitle("Help — SubRename")
        dlg.setModal(True)
        dlg.resize(560, 580)
        dlg.setStyleSheet(generate_stylesheet(theme))

        layout = QVBoxLayout(dlg)
        layout.setSpacing(8)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setFrameShape(QFrame.Shape.NoFrame)
        browser.setHtml("""
    <style type="text/css">
    a, a:link, a:visited { text-decoration: none; }
    </style>
    <h2>SubRename Help</h2>
    <p>Renames subtitle files to match their corresponding video files.
    Supports TV series (episode-based) and movie (similarity-based) matching,
    multiple subtitle groups, conflict resolution, and preview mode.</p>
    <p>For full documentation, see the
    <a href="https://github.com/alanyung-yl/SubApp/blob/main/README.md">README</a>.</p>

    <hr/>
    <h3>Getting Started</h3>
    <ol>
        <li>Select the folder containing your video files (<b>File → Open Folder</b> or <b>Ctrl+O</b>).</li>
        <li>Add subtitle files via <b>Browse Files</b>, <b>File → Open Subtitle Files</b>,
            drag-and-drop, or by clicking the subtitle table.</li>
        <li>Adjust the source and destination format dropdowns if needed
            (<b>Auto</b> picks the most common extension automatically).</li>
        <li>Press <b>Start Renaming</b> or <b>F5</b>. Use <b>Preview Mode</b> first
            to see proposed changes without writing anything to disk.</li>
    </ol>

    <hr/>
    <h3>Keyboard Shortcuts</h3>
    <table cellspacing="4" width="100%">
        <tr><th align="left">Action</th><th align="left">Shortcut</th></tr>
        <tr><td>Open folder</td>                  <td>Ctrl+O</td></tr>
        <tr><td>Open subtitle files</td>           <td>Ctrl+Shift+O</td></tr>
        <tr><td>Remove all files from table</td>   <td>Ctrl+Shift+Del</td></tr>
        <tr><td>Exit</td>                          <td>Ctrl+Q</td></tr>
        <tr><td>&nbsp;</td><td></td></tr>
        <tr><td>Start renaming</td>                <td>F5</td></tr>
        <tr><td>Retry failed files</td>            <td>F6</td></tr>
        <tr><td>Clear completed files</td>         <td>F7</td></tr>
        <tr><td>Clear all subtitle files</td>      <td>F8</td></tr>
        <tr><td>Analyze folder</td>                <td>F11</td></tr>
        <tr><td>Help</td>                          <td>F1</td></tr>
        <tr><td>&nbsp;</td><td></td></tr>
        <tr><td>Zoom in / out / reset</td>         <td>Ctrl+= &nbsp;/&nbsp; Ctrl+- &nbsp;/&nbsp; Ctrl+0</td></tr>
        <tr><td>Remove selected subtitle files</td><td>Delete</td></tr>
    </table>

    <hr/>
    <h3>Key Features</h3>
    <ul>
        <li><b>Series &amp; movie mode</b> — auto-detected from the video files in the folder.
            Series mode pairs by episode number; movie mode uses fuzzy title matching.</li>
        <li><b>Preview mode</b> — see every proposed rename and resolve conflicts before
            anything is written to disk. Enable via <b>Preferences → Enable Preview Mode</b>.</li>
        <li><b>Conflict resolution</b> — choose Ask, Skip, Overwrite, or Keep Both globally,
            or resolve each conflict individually with a custom suffix.</li>
        <li><b>Group suffix</b> — automatically detects and appends the subtitle group name
            (e.g. <code>Show.S01E03.GroupA.ass</code>) when multiple groups are loaded.</li>
        <li><b>Language suffix</b> — appends a language code derived from the filename
            (e.g. <code>Show.S01E03.cht.ass</code>). Controlled via <b>Settings → Language</b>
            and configured in <code>langmap.txt</code>.</li>
        <li><b>Plugin system</b> — drop extra plugins into the addons folder to add new tabs.
            See <a href="https://github.com/alanyung-yl/SubApp/blob/main/README.md#plugin-system">Plugin System</a>
            in the docs.</li>
    </ul>

    <hr/>
    <h3>Tips</h3>
    <ul>
        <li>The <b>New Name</b> column is editable — type a custom filename to override
            the auto-generated name for any individual file.</li>
        <li>Use <b>File → Open User Data Folder</b> to jump to the config directory
            (settings, language map, addons, logs).</li>
        <li>If files fail to match, enable Preview Mode and check the Preview column —
            the log will explain why each file was skipped or failed.</li>
        <li>Set <b>On Complete → Exit</b> under the Tools menu to auto-close after a
            successful batch run.</li>
    </ul>
    """)
        layout.addWidget(browser)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dlg.accept)
        layout.addWidget(buttons)

        dlg.exec()

    @pyqtSlot(object)
    def _invoke(self, fn):
        fn()

    def save_window_geometry(self):
        settings = QSettings(ap.VENDOR, ap.qt_profile())
        settings.setValue("geometry", self.saveGeometry())
        if hasattr(self, "saveState"):
            settings.setValue("windowState", self.saveState())

    def restore_window_geometry(self):
        settings = QSettings(ap.VENDOR, ap.qt_profile())
        geometry = settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        state = settings.value("windowState")
        if state:
            self.restoreState(state)
        if not geometry:
            self.resize(1440, 900)
            screen = QApplication.primaryScreen().availableGeometry()
            self.move((screen.width() - self.width()) // 2,
                      (screen.height() - self.height()) // 2)

    def on_splitter_moved(self, pos, index):
        """Called when splitter is moved - handle special cases and save sizes"""
        # If user drags the handle between target and subtitle while video is hidden,
        # interpret that as intention to reveal video area, so unhide it.
        if index == 1 and not self.video_drop_area.isVisible():
            self.video_drop_area.show()
            self.show_video_table_action.setChecked(True)
            settings = load_settings()
            settings["show_video_table"] = True
            save_settings(settings)
            # Expand video area to a reasonable portion
            sizes = self.main_splitter.sizes()
            if sizes and len(sizes) == 3:
                video_size, sub_size, log_size = sizes
                total = sum(sizes) if sum(sizes) > 0 else (self.height() or 900)
                desired_video = max(150, int(total * 0.25))
                delta = desired_video - video_size
                if delta != 0:
                    sub_size = max(100, sub_size - delta)
                    video_size = desired_video
                    self.main_splitter.setSizes([video_size, sub_size, log_size])
        # Always save sizes after any move
        self.save_splitter_sizes()

    def save_splitter_sizes(self):
        """Save splitter sizes to settings"""
        settings = load_settings()
        settings["splitter_sizes"] = self.main_splitter.sizes()
        save_settings(settings)

    def restore_splitter_sizes(self):
        """Restore splitter sizes from settings"""
        settings = load_settings()
        sizes = settings.get("splitter_sizes")

        if sizes and len(sizes) == 3:
            min_size = 100
            total_size = sum(sizes)

            if all(size >= min_size for size in sizes) and total_size > 0:
                self.main_splitter.setSizes(sizes)
            else:
                total_height = self.main_splitter.height() or 900
                default_sizes = [
                    int(total_height * 0.25),
                    int(total_height * 0.50),
                    int(total_height * 0.25)
                ]
                self.main_splitter.setSizes(default_sizes)

    def get_comm_ext(self, files, valid_ext):
        """Get the most common file extension from the given files"""
        if not files:
            return None
            
        extension_counts = {}
        for file_path in files:
            _, ext = os.path.splitext(file_path.lower())
            if ext in valid_ext:
                extension_counts[ext] = extension_counts.get(ext, 0) + 1
        
        if not extension_counts:
            return None
            
        return max(extension_counts.items(), key=lambda x: x[1])[0]

    def get_extension_counts(self, folder_path, valid_extensions):
        """Get count of files by extension in the given folder"""
        if not folder_path or not os.path.exists(folder_path):
            return {}
        extension_counts = {}
        for filename in os.listdir(folder_path):
            _, ext = os.path.splitext(filename.lower())
            if ext in valid_extensions:
                extension_counts[ext] = extension_counts.get(ext, 0) + 1
        return extension_counts

    def on_dst_format_changed(self, text):
        """Handle destination format combobox change"""
        set_last_dst_format(text)
        
        if text == "Auto":
            if self.target_folder:
                video_files = []
                for f in os.listdir(self.target_folder):
                    if any(f.lower().endswith(ext) for ext in get_all_video_extensions()):
                        video_files.append(os.path.join(self.target_folder, f))
                
                # Find most common video extension
                comm = self.get_comm_ext(video_files, get_all_video_extensions())
                if comm:
                    self.dst_ext = comm
                    self.log(f"Auto-selected video format: {comm}", "success")  # system
                else:
                    # No video files found
                    self.dst_ext = '.mp4'
                    self.log("No video files found", "warning")
            else:
                # No target folder
                self.dst_ext = '.mp4'
        elif text == "All":
            self.dst_ext = get_enabled_dst_ext()
        else:
            self.dst_ext = text

    def on_src_format_changed(self, text, files=None):
        """Handle source format combobox change"""
        set_last_src_format(text)
        if text == "Auto":
            files = files if files is not None else self.selected_files
            if files:
                # Find most common subtitle extension
                comm = self.get_comm_ext(files, get_all_subtitle_extensions())
                if comm:
                    self.src_ext = comm
                    self.log(f"Auto-selected subtitle format: {comm}", "success")  # system
                else:
                    # No subtitle files found
                    self.src_ext = '.ass'
                    self.log("No subtitle files found", "warning")
        elif text == "All":  # Use the first enabled extension as default
            enabled_exts = get_enabled_src_ext()
            self.src_ext = enabled_exts[0] if enabled_exts else '.ass'
        else:
            self.src_ext = text

    def refresh_extension_comboboxes(self):
        """Refresh the extension comboboxes with current enabled extensions"""
        # Store current selections
        current_dst = self.dst_edit.currentText()
        current_src = self.src_edit.currentText()
        
        # Clear and repopulate destination combobox
        self.dst_edit.clear()
        self.dst_edit.addItems(['Auto', 'All'] + get_enabled_dst_ext())
        
        # Restore selection if it's still valid, otherwise use Auto
        if current_dst in ['Auto', 'All'] + get_enabled_dst_ext():
            self.dst_edit.setCurrentText(current_dst)
        else:
            self.dst_edit.setCurrentText('Auto')
        
        # Clear and repopulate source combobox
        self.src_edit.clear()
        self.src_edit.addItems(['Auto', 'All'] + get_enabled_src_ext())
        
        # Restore selection if it's still valid, otherwise use Auto
        if current_src in ['Auto', 'All'] + get_enabled_src_ext():
            self.src_edit.setCurrentText(current_src)
        else:
            self.src_edit.setCurrentText('Auto')

    def closeEvent(self, event):
        """Save window geometry and splitter sizes when closing"""
        if self._is_closing:
            event.accept()
            return
        self._is_closing = True
        self.shutdown_signal.emit()
        self.save_window_geometry()
        settings = load_settings()
        settings["splitter_sizes"] = self.main_splitter.sizes()
        save_settings(settings)
        event.accept()

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts including zoom controls"""
        if event.key() == Qt.Key.Key_Minus and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.zoom_out()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_Plus and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.zoom_in()
            event.accept()
            return
        elif event.key() == Qt.Key.Key_0 and event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            self.zoom_reset()
            event.accept()
            return
        
        super().keyPressEvent(event)

    def zoom_in(self):
        """Increase zoom level by 10%"""
        current_zoom = get_zoom_level()
        new_zoom = min(current_zoom + 10, 250)
        if new_zoom != current_zoom:
            set_zoom_level(new_zoom)
            self.apply_zoom(new_zoom)
            self.log(f"Zoom: {new_zoom}%", "info")

    def zoom_out(self):
        """Decrease zoom level by 10%"""
        current_zoom = get_zoom_level()
        new_zoom = max(current_zoom - 10, 50)
        if new_zoom != current_zoom:
            set_zoom_level(new_zoom)
            self.apply_zoom(new_zoom)
            self.log(f"Zoom: {new_zoom}%", "info")

    def zoom_reset(self):
        """Reset zoom level to 100%"""
        current_zoom = get_zoom_level()
        if current_zoom != 100:
            set_zoom_level(100)
            self.apply_zoom(100)
            self.log("<b>Zoom: 100%</b>", "info")

    def apply_zoom(self, zoom_level):
        multiplier = zoom_level / 100.0
        
        app = QApplication.instance()
        if app:
            font = app.font()
            base_size = 10
            new_size = max(1, round(base_size * multiplier * 10) / 10)
            font.setPointSizeF(new_size)
            app.setFont(font)
            
            # Re-apply theme with zoom-adjusted image sizes
            self.apply_theme_with_zoom(zoom_level)
            
            self.style().unpolish(self)
            self.style().polish(self)
            self.update_all_widgets_font()

    def append_log(self, html, category="info"):
        """Append log message with category filtering and auto-scroll to bottom"""
        if self._is_closing:
            return
        if not html.strip():
            self.log_box.append("<div><br></div>")
            try:
                self.log_box.moveCursor(QTextCursor.MoveOperation.End)
            except AttributeError:
                self.log_box.moveCursor(QTextCursor.End)
            self.log_box.ensureCursorVisible()
            return

        # Check if this category should be displayed
        settings = load_settings()
        force_debug = os.environ.get("SUBRENAME_LOG_LEVEL", "").strip().upper() == "DEBUG"
        visible_by_category = {
            'info': settings.get('show_info_messages', True),
            'success': settings.get('show_success_messages', True),
            'warning': settings.get('show_warning_messages', True),
            'error': settings.get('show_error_messages', True),
            'debug': settings.get('show_debug_messages', force_debug),
            'system': True,  # System messages cannot be disabled
        }
        
        if not visible_by_category.get(category, True):
            return  # Skip messages that are filtered out
        
        # Append message with category data attribute for potential styling
        self.log_box.append(f'<div style="font-weight: normal;" data-category="{category}">{html}</div>')            
        try:
            self.log_box.moveCursor(QTextCursor.MoveOperation.End)
        except AttributeError:
            self.log_box.moveCursor(QTextCursor.End)
        self.log_box.ensureCursorVisible()

    def update_all_widgets_font(self):
        """Update font for all widgets in the application"""
        app = QApplication.instance()
        if not app:
            return

        self.setFont(app.font())

        # Do NOT set font on each child; preserve custom widget fonts.
        for w in self.findChildren(QWidget):
            if w is self:
                continue
            try:
                if not w.testAttribute(Qt.WidgetAttribute.WA_SetFont):
                    w.update()
            except RuntimeError:
                pass

if __name__ == "__main__":
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    APP_ICON = QIcon(str(_BASE / "assets" / "icons" / _ICON))
    if APP_ICON.isNull():
        APP_ICON = QIcon(str(_BASE / "assets" / "icons" / "appicon.png"))
    app.setWindowIcon(APP_ICON)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    sys.exit(app.exec()) 
