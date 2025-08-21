""" 
Now the popup window for prompting custom extension has two buttons: OK and CANCEL. 
The issues i have with it is that when i press "enter" or "OK" it will do the default tag if input box is empty. 
but when i press "CANCEL" or "clokse window(X)" it will still do the default tag. 
My intended behavior for pressing "CANCEL" or "close window(X)" is to close the window

To-do:
- redo dialogwindow to add skip button
- redo the checkmark!!!!
- setting window layout:
  - OK/CANCEL button
- why there's two remove_custom_subtitle_extension() and remove_custom_video_extension()?
- Is the setting window's save change function effective?
- add a button to the settings dialog to reset the settings to default
- add a button to the settings dialog to save the settings to a file
- add a button to the settings dialog to load the settings from a file
- see if it's possible to add all files from the "select subtitle files" window to the table instead of having to select each file manually
"""
try:
    from PyQt6.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog, QTextEdit, QFrame, QSizePolicy, 
        QTableWidget, QTableWidgetItem, QLineEdit, QHBoxLayout, QComboBox, QInputDialog, QMenuBar, QMenu, 
        QDialog, QCheckBox, QDialogButtonBox, QFormLayout, QStyleFactory, QHeaderView, QMessageBox, QSplitter, QTabBar, QStackedWidget,
        QListWidget, QScrollArea, QGroupBox, QSpacerItem, QStyleOptionGroupBox, QStyle
    )
    from PyQt6.QtCore import Qt, QMetaObject, pyqtSignal, Q_ARG, QTimer, pyqtSlot, QSize
    from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QColor, QIcon, QAction, QPixmap, QPainter, QTextCursor
except ImportError:
    print("PyQt6 is required. Please install it with 'pip install PyQt6'.")
    import sys
    sys.exit(1)
import sys
import os
import json
import platform

# Windows-specific imports for title bar theming
if platform.system() == "Windows":
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        ctypes = None

def set_windows_title_bar_theme(window, dark_mode=True):
    """ Set Windows 10/11 title bar to dark or light theme. """
    if platform.system() != "Windows" or not ctypes:
        return False
        
    try:
        # Get the window handle
        hwnd = int(window.winId())
        
        # Windows 10 version 1809+ and Windows 11 support
        # DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        
        # Set the title bar to dark (1) or light (0) mode
        value = wintypes.DWORD(1 if dark_mode else 0)
        
        # Call DwmSetWindowAttribute
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

# Custom QAction class for checkmark symbols
class CheckmarkAction(QAction):
    """
    QAction that behaves like a normal checkable menu item but shows a Unicode ✓ 
    in the icon/indicator slot so text is always perfectly left-aligned.
    """
    # Cache the two icons so it's only created once
    _CHECK_ICON = None
    _BLANK_ICON = None

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)

        # Build the two icons once with theme-aware color
        if CheckmarkAction._CHECK_ICON is None:
            CheckmarkAction._update_icons_for_theme()

        self.toggled.connect(self._update_icon)
        self._update_icon()       # set the initial icon

    @staticmethod
    def _create_icon(char: str, color: QColor = None) -> QIcon:
        """Return a 16×16 pixmap with <char> rendered, or transparent if empty."""
        size = 16
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        if char:
            painter = QPainter(pm)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            font = painter.font()
            font.setPointSize(12)
            painter.setFont(font)
            if color:
                painter.setPen(color)
            else:
                painter.setPen(QColor(255, 255, 255))
            painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, char)
            painter.end()
        return QIcon(pm)

    @staticmethod
    def _update_icons_for_theme():
        """Update checkmark icons based on current theme"""
        dark_mode = get_theme()
        if dark_mode:
            check_color = QColor(255, 255, 255)
        else:
            check_color = QColor(0, 0, 0)
        
        CheckmarkAction._CHECK_ICON = CheckmarkAction._create_icon("🗸", check_color)
        CheckmarkAction._BLANK_ICON = CheckmarkAction._create_icon("")

    def _update_icon(self):
        icon = (CheckmarkAction._CHECK_ICON
                if self.isChecked() else
                CheckmarkAction._BLANK_ICON)
        self.setIcon(icon)

# ─── DPI Scaling Configuration ──────────────────────────────────────────────────────
SETTINGS_FILE = "settings.json"

def preload_settings():
    """Load settings before Qt initialization"""
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

settings = preload_settings()
if settings.get("disable_dpi_scaling", False):
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ["QT_AUTO_SCREEN_SCALE_FACTOR"] = "0"
else:
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

import threading
import SubRename as sr
import subprocess
import shutil
import logging

SUBTITLE_DIALOG_TITLE = "Select Subtitle Files"

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

# ─── settings helpers ──────────────────────────────────────────────────────────────
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_settings(settings):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f)
    except Exception:
        pass

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

def get_disable_dpi_scaling():
    return load_settings().get("disable_dpi_scaling", False)

def set_disable_dpi_scaling(enabled):
    settings = load_settings()
    settings["disable_dpi_scaling"] = enabled
    save_settings(settings)

def get_zoom_level():
    return load_settings().get("zoom_level", 100)

def set_zoom_level(level):
    settings = load_settings()
    settings["zoom_level"] = level
    save_settings(settings)

def get_theme():
    return load_settings().get("dark_mode", False)

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
    'window_bg': '#f0f0f0',
    'widget_bg': '#ffffff',
    'text_color': '#000000',
    'border_color': '#888888',
    'button_bg': '#e0e0e0',
    'button_hover': '#d0d0d0',
    'table_header_bg': '#f8f8f8',
    'table_alternate_bg': '#f5f5f5',
    'drop_area_bg': '#fafafa',
    'log_bg': '#ffffff',
    'success_color': '#28a745',
    'error_color': '#dc3545',
    'warning_color': '#ffc107',
    'info_color': '#17a2b8'
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
    'drop_area_bg': '#1a1a1a',
    'log_bg': '#1a1a1a',
    'success_color': '#4ec9b0',
    'error_color': '#f44747',
    'warning_color': '#ce9178',
    'info_color': '#569cd6'
}

# ─── Stylesheets ───────────────────────────────────────────────────────────
def get_drop_area_frame_style(theme):
    return f'''
        QFrame {{
            border: 2px solid {theme['border_color']};
            border-radius: 10px;
            background: {theme['drop_area_bg']};
        }}
    '''

def get_table_widget_style(theme):
    """Table Widget in drop areas"""
    return f'''
        QTableWidget {{  
            padding: 6px;
            background: {theme['widget_bg']};
            color: {theme['text_color']};
            gridline-color: {theme['border_color']};
            alternate-background-color: {theme['table_alternate_bg']};
        }}
        QTableWidget::item {{
            border-radius: 0px;
            padding: 4px;
        }}
        QTableWidget::item:selected {{
            background: #3874f2;
            color: white;
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
            padding: 4px;
            border: 1px solid {theme['border_color']};
        }}
        QTableCornerButton::section {{
            background: {theme['table_header_bg']};
            color: {theme['text_color']};
            border: 1px solid {theme['border_color']};
        }}
        QScrollBar:vertical {{
            background: {theme['widget_bg']};
            width: 16px;
            margin: 2px;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {theme['border_color']};
            min-height: 20px;
            border-radius: 4px;
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
    '''

# ─── UI ──────────────────────────────────────────────────────────────────────────────

class DropArea(QFrame):
    def __init__(self, on_files_selected):
        super().__init__()
        self.setAcceptDrops(True)
        self.on_files_selected = on_files_selected
        self.current_theme = LIGHT_THEME  # Default theme
        
        self.label = QLabel("Click or drag-and-drop subtitle files here", self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["File Name", "Path", "Status"])
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.hide()  # Hide table initially
        
        # Set maximum column widths and auto-resize
        self.table.setColumnWidth(0, 400)  # File name column
        self.table.setColumnWidth(1, 400)  # Path column  
        self.table.setColumnWidth(2, 120)  # Status column
        self.table.resizeColumnsToContents()

        self.setMinimumHeight(150)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(0)
        layout.addWidget(self.label)
        layout.addWidget(self.table)
        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        # Apply theme after all widgets are created
        self.update_theme(self.current_theme)

    def update_theme(self, theme):
        self.current_theme = theme
        self.setStyleSheet(get_drop_area_frame_style(theme))
        self.table.setStyleSheet(get_table_widget_style(theme))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            last_folder = get_last_subtitle_folder()
            files, _ = QFileDialog.getOpenFileNames(
                self, "Select Subtitle Files", last_folder, get_subtitle_file_filter()
            )
            if files:
                self.display_files(files)
                self.on_files_selected(files)
                set_last_subtitle_folder(os.path.dirname(files[0]))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        files = [url.toLocalFile() for url in urls if os.path.isfile(url.toLocalFile())]
        if files:
            self.display_files(files)
            self.on_files_selected(files)
            set_last_subtitle_folder(os.path.dirname(files[0]))

    def display_files(self, files):
        self.label.hide()
        self.table.show()
        self.table.setRowCount(0)
        for file_path in files:
            row = self.table.rowCount()
            self.table.insertRow(row)
            filename_item = QTableWidgetItem(os.path.basename(file_path))
            path_item = QTableWidgetItem(file_path)
            status_text = "⏳" if get_compact_mode() else "⏳ Pending"
            status_item = QTableWidgetItem(status_text)
            filename_item.setToolTip(file_path)
            path_item.setToolTip(file_path)
            status_item.setToolTip("Status: Pending")
            status_item.setBackground(QColor(128, 128, 128))
            self.table.setItem(row, 0, filename_item)
            self.table.setItem(row, 1, path_item)
            self.table.setItem(row, 2, status_item)
        # Auto-resize columns to fit content
        self.table.resizeColumnsToContents()

    def clear_files(self):
        self.table.hide()
        self.label.show()
        self.table.setRowCount(0)

class VideoDropArea(QFrame):
    def __init__(self, parent=None):
        super().__init__()
        self.setAcceptDrops(True)
        self.current_theme = LIGHT_THEME
        self.parent_window = parent
        
        self.label = QLabel("Click or drag-and-drop video files here", self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Video File Name", "Size (MB)"])
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.hide()  # Hide table initially
        
        # Set maximum column widths and auto-resize
        self.table.setColumnWidth(0, 400)  # File name column
        self.table.setColumnWidth(1, 100)  # Size column
        self.table.resizeColumnsToContents()

        self.setMinimumHeight(150)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)  # Add spacing inside the border
        layout.setSpacing(0)
        layout.addWidget(self.label)
        layout.addWidget(self.table)
        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        
        self.update_theme(self.current_theme)

    def update_theme(self, theme):
        self.current_theme = theme
        self.setStyleSheet(get_drop_area_frame_style(theme))
        self.table.setStyleSheet(get_table_widget_style(theme))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Open folder dialog to select target folder
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

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                # Set the target folder and update the video table
                if self.parent_window:
                    self.parent_window.target_folder_line.setText(path)
                    self.parent_window.update_video_table(log_count=True)
                break

    def display_files(self, files):
        self.label.hide()
        self.table.show()
        self.table.setRowCount(0)
        
        for i, file in enumerate(files):
            self.table.insertRow(i)
            filename_item = QTableWidgetItem(os.path.basename(file))
            # Get file size in MB
            file_size = os.path.getsize(file) / (1024 * 1024)
            size_item = QTableWidgetItem(f"{file_size:.2f}")
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
        self.category_list.setCurrentRow(0)
        self.category_list.currentRowChanged.connect(self.switch_tab)
        main_layout.addWidget(self.category_list)
        
        # Create stacked widget for content
        self.stacked_widget = QStackedWidget()
        main_layout.addWidget(self.stacked_widget)
        
        self.create_general_tab()
        self.create_extensions_tab()
        
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self.save_and_accept)
        self.button_box.rejected.connect(self.check_unsaved_changes)
        # Create a right column container so we can compute offsets for alignment
        self.right_column_container = QWidget()
        button_layout = QVBoxLayout(self.right_column_container)
        button_layout.setContentsMargins(0, 0, 0, 0)
        # Manage buttons for Extensions
        self.manage_video_btn = QPushButton("Manage")
        self.manage_video_btn.clicked.connect(lambda: self.open_manage_dialog('video'))
        self.manage_video_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.manage_subtitle_btn = QPushButton("Manage")
        self.manage_subtitle_btn.clicked.connect(lambda: self.open_manage_dialog('subtitle'))
        self.manage_subtitle_btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._manage_top_spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self._manage_mid_spacer = QSpacerItem(0, 0, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        button_layout.addItem(self._manage_top_spacer)
        button_layout.addWidget(self.manage_video_btn)
        button_layout.addItem(self._manage_mid_spacer)
        button_layout.addWidget(self.manage_subtitle_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.button_box)
        main_layout.addWidget(self.right_column_container)
        
        self.setLayout(main_layout)
        
        self.closeEvent = self.check_unsaved_changes_on_close  # Override closeEvent to check for unsaved changes
        self.resizeEvent = self.on_resize_event  # Override resizeEvent to update manage button alignment
        self.load_settings()
        self.store_original_settings()  # Store original settings for comparison
        self.connect_change_signals()
        # Reset change flag after initial setup
        self.has_unsaved_changes = False
        # Align manage buttons initially and when the current page changes
        try:
            self.stacked_widget.currentChanged.connect(lambda _: self.update_manage_btn_alignment())
        except Exception:
            pass
        # Show/hide manage buttons based on current tab
        self.category_list.currentRowChanged.connect(self.update_manage_button_visibility)
        self.update_manage_button_visibility()
    
    def create_general_tab(self):
        general_widget = QWidget()
        form_layout = QFormLayout(general_widget)
        
        self.auto_run_checkbox = QCheckBox("Auto-run renaming upon selecting subtitle files")
        form_layout.addRow(self.auto_run_checkbox)
        self.always_prompt_multi_checkbox = QCheckBox("Always prompt for tag if multiple subtitle sets per episode")
        form_layout.addRow(self.always_prompt_multi_checkbox)
        self.always_prompt_tag_checkbox = QCheckBox("Always prompt for custom tag (even if no existing sub occupies the default name)")
        form_layout.addRow(self.always_prompt_tag_checkbox)
        self.cache_per_set_checkbox = QCheckBox("Cache studio tags per set (not per file)")
        form_layout.addRow(self.cache_per_set_checkbox)
        
        self.stacked_widget.addWidget(general_widget)
    
    def create_extensions_tab(self):
        extensions_widget = QWidget()
        layout = QVBoxLayout(extensions_widget)
        
        # Video extensions
        video_group = QGroupBox("Video Extensions")
        video_layout = QVBoxLayout(video_group)
        video_scroll = QScrollArea()
        video_scroll.setWidgetResizable(True)
        video_content = QWidget()
        self.video_extensions_layout = QVBoxLayout(video_content)
        self.video_checkboxes = {}
        
        # Add video extension checkboxes (built-in + custom)
        for ext in get_all_video_extensions():
            checkbox = QCheckBox(ext)
            checkbox.toggled.connect(self.mark_changed)
            self.video_checkboxes[ext] = checkbox
            self.video_extensions_layout.addWidget(checkbox)
        
        video_scroll.setWidget(video_content)
        video_layout.addWidget(video_scroll)
        layout.addWidget(video_group)
        
        # Subtitle extensions
        subtitle_group = QGroupBox("Subtitle Extensions")
        subtitle_layout = QVBoxLayout(subtitle_group)
        subtitle_scroll = QScrollArea()
        subtitle_scroll.setWidgetResizable(True)
        subtitle_content = QWidget()
        self.subtitle_extensions_layout = QVBoxLayout(subtitle_content)
        self.subtitle_checkboxes = {}
        
        # Add subtitle extension checkboxes (built-in + custom)
        for ext in get_all_subtitle_extensions():
            checkbox = QCheckBox(ext)
            checkbox.toggled.connect(self.mark_changed)
            self.subtitle_checkboxes[ext] = checkbox
            self.subtitle_extensions_layout.addWidget(checkbox)
        
        subtitle_scroll.setWidget(subtitle_content)
        subtitle_layout.addWidget(subtitle_scroll)
        layout.addWidget(subtitle_group)
        
        layout.addStretch()
        self.stacked_widget.addWidget(extensions_widget)
        # After building, compute alignment positions
        QTimer.singleShot(0, self.update_manage_btn_alignment)
    
    def switch_tab(self, index):
        self.stacked_widget.setCurrentIndex(index)
        self.update_manage_btn_alignment()
        self.update_manage_button_visibility()
    
    def load_settings(self):
        """Load current settings into the dialog"""
        settings = load_settings()
        self.auto_run_checkbox.setChecked(settings.get("auto_run_on_select", True))
        self.always_prompt_multi_checkbox.setChecked(settings.get("always_prompt_tag_multi_set", False))
        self.always_prompt_tag_checkbox.setChecked(settings.get("always_prompt_tag_always", False))
        self.cache_per_set_checkbox.setChecked(settings.get("cache_per_set", True))
        
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
            'always_prompt_multi': self.always_prompt_multi_checkbox.isChecked(),
            'always_prompt_tag': self.always_prompt_tag_checkbox.isChecked(),
            'cache_per_set': self.cache_per_set_checkbox.isChecked(),
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
        self.always_prompt_multi_checkbox.toggled.connect(self.mark_changed)
        self.always_prompt_tag_checkbox.toggled.connect(self.mark_changed)
        self.cache_per_set_checkbox.toggled.connect(self.mark_changed)
        self.connect_extension_checkbox_signals()

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

    def check_unsaved_changes(self):
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

    def check_unsaved_changes_on_close(self, event):
        if self.has_unsaved_changes:
            reply = QMessageBox.question(
                self,
                "Unsaved Changes",
                "You have unsaved changes. Do you want to close without saving?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

    def on_resize_event(self, event):
        """Handle resize events to update manage button alignment."""
        super().resizeEvent(event)
        QTimer.singleShot(0, self.update_manage_btn_alignment)  # Update manage button alignment after resize

    def save_and_accept(self):
        self.save_current_settings()
        self.accept()

    def save_current_settings(self):
        """Save current settings to storage."""
        # Save general settings
        settings = load_settings()
        settings["auto_run_on_select"] = self.auto_run_checkbox.isChecked()
        settings["always_prompt_tag_multi_set"] = self.always_prompt_multi_checkbox.isChecked()
        settings["always_prompt_tag_always"] = self.always_prompt_tag_checkbox.isChecked()
        settings["cache_per_set"] = self.cache_per_set_checkbox.isChecked()
        save_settings(settings)
        
        # Save cached extension settings
        set_custom_video_extensions(self.cached_extension_changes['custom_video'])
        set_custom_subtitle_extensions(self.cached_extension_changes['custom_subtitle'])
        set_disabled_builtin_video_extensions(self.cached_extension_changes['disabled_video'])
        set_disabled_builtin_subtitle_extensions(self.cached_extension_changes['disabled_subtitle'])
        set_enabled_video_extensions(self.cached_extension_changes['enabled_video'])
        set_enabled_subtitle_extensions(self.cached_extension_changes['enabled_subtitle'])
        
        self.has_unsaved_changes = False
        # Ensure alignment in case sizes changed after loading
        QTimer.singleShot(0, self.update_manage_btn_alignment)

    def normalize_ext(self, ext: str) -> str:
        ext = ext.strip().lower()
        if not ext:
            return ""
        if not ext.startswith('.'):
            ext = '.' + ext
        return ext

    def _compute_group_top(self, group: QGroupBox) -> int:
        """Compute Y offset of a group border top relative to the right column container."""
        try:
            opt = QStyleOptionGroupBox()
            opt.initFrom(group)
            opt.text = group.title()
            opt.lineWidth = group.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth, opt, group)
            opt.subControls = QStyle.SubControl.SC_GroupBoxFrame | QStyle.SubControl.SC_GroupBoxLabel
            frame_rect = group.style().subControlRect(QStyle.ComplexControl.CC_GroupBox, opt, QStyle.SubControl.SC_GroupBoxFrame, group)
            # Map the frame rect top-left
            frame_top_global = group.mapToGlobal(frame_rect.topLeft())
            right_top_global = self.right_column_container.mapToGlobal(self.right_column_container.rect().topLeft())
            dy = frame_top_global.y() - right_top_global.y()
            return max(0, dy)
        except Exception:
            return 0

    def update_manage_btn_alignment(self):
        """Adjust vertical spacers so Manage buttons align with their groups."""
        try:
            # Find current Extensions page; only align there
            current = self.stacked_widget.currentWidget()
            if not current:
                return
            # Locate groups in the Extensions page
            video_group = None
            subtitle_group = None
            for child in current.findChildren(QGroupBox):
                if child.title() == "Video Extensions":
                    video_group = child
                elif child.title() == "Subtitle Extensions":
                    subtitle_group = child
            if not video_group or not subtitle_group:
                return
            # Compute top offsets relative to right column
            video_top = self._compute_group_top(video_group)
            subtitle_top = self._compute_group_top(subtitle_group)
            # Manage button heights and layout spacing between stacked widgets
            btn_h = self.manage_video_btn.sizeHint().height()
            spacing = self.right_column_container.layout().spacing()
            # Set spacer heights: top spacer brings first manage to video group top
            # mid spacer sets distance between the two manage buttons to subtitle_top - video_top - btn_h
            self._manage_top_spacer.changeSize(0, max(0, video_top), QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            self._manage_mid_spacer.changeSize(0, max(0, subtitle_top - video_top - btn_h - spacing), QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            # Re-layout
            self.right_column_container.layout().invalidate()
            self.right_column_container.layout().activate()
        except Exception:
            pass

    def update_manage_button_visibility(self):
        """Show manage buttons only when Extensions tab is selected."""
        current_row = self.category_list.currentRow()
        is_extensions_tab = (current_row == 1)  # Extensions is the second item (index 1)
        
        self.manage_video_btn.setVisible(is_extensions_tab)
        self.manage_subtitle_btn.setVisible(is_extensions_tab)

    def add_custom_video_extension(self):
        text, ok = QInputDialog.getText(self, "Add Video Extension", "Enter a video extension (e.g. .mkv or mkv):")
        if not ok:
            return
        ext = self.normalize_ext(text)
        if not ext:
            return
        # Merge into custom list if it's not built-in
        if ext not in get_all_video_extensions():
            custom = get_custom_video_extensions()
            if ext not in custom:
                custom.append(ext)
                set_custom_video_extensions(sorted(set(custom)))
        # If checkbox doesn't exist yet, add it
        if ext not in self.video_checkboxes:
            cb = QCheckBox(ext)
            self.video_checkboxes[ext] = cb
            self.video_extensions_layout.addWidget(cb)
        self.video_checkboxes[ext].setChecked(True)

    def add_custom_subtitle_extension(self):
        text, ok = QInputDialog.getText(self, "Add Subtitle Extension", "Enter a subtitle extension (e.g. .ass or ass):")
        if not ok:
            return
        ext = self.normalize_ext(text)
        if not ext:
            return
        # Merge into custom list if it's not built-in
        if ext not in get_all_subtitle_extensions():
            custom = get_custom_subtitle_extensions()
            if ext not in custom:
                custom.append(ext)
                set_custom_subtitle_extensions(sorted(set(custom)))
        # If checkbox doesn't exist yet, add it
        if ext not in self.subtitle_checkboxes:
            cb = QCheckBox(ext)
            self.subtitle_checkboxes[ext] = cb
            self.subtitle_extensions_layout.addWidget(cb)
        self.subtitle_checkboxes[ext].setChecked(True)

    def remove_custom_video_extension(self, to_remove=None, prompt=True):
        # Determine which extensions are checked
        custom = set(get_custom_video_extensions())
        if to_remove is None:
            to_remove = [ext for ext, cb in self.video_checkboxes.items() if cb.isChecked()]
        if not to_remove:
            if prompt:
                QMessageBox.information(self, "Remove Extensions", "No extensions selected. Check the extensions you want to remove, then click Remove.")
            return
        # Confirm removal with the user
        if prompt:
            confirm = QMessageBox.question(
                self,
                "Confirm Removal",
                "Remove the following video extensions?\n\n" + ", ".join(to_remove),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        # Update settings - handle both custom and built-in extensions
        custom_to_remove = [ext for ext in to_remove if ext in custom]
        builtin_to_disable = [ext for ext in to_remove if ext in VIDEO_EXTENSIONS and ext not in custom]
        
        # Remove custom extensions from custom list
        if custom_to_remove:
            remaining = sorted(custom.difference(custom_to_remove))
            set_custom_video_extensions(remaining)
        
        # Add built-in extensions to disabled list
        if builtin_to_disable:
            current_disabled = set(get_disabled_builtin_video_extensions())
            new_disabled = current_disabled.union(builtin_to_disable)
            set_disabled_builtin_video_extensions(sorted(new_disabled))
        
        # Remove checkboxes from UI and dict
        for ext in to_remove:
            cb = self.video_checkboxes.pop(ext, None)
            if cb:
                cb.setParent(None)
                cb.deleteLater()
        # Also uncheck from enabled list if present
        enabled = set(get_enabled_dst_ext())
        changed = False
        for ext in to_remove:
            if ext in enabled:
                enabled.discard(ext)
                changed = True
        if changed:
            set_enabled_video_extensions(sorted(enabled))

    def remove_custom_subtitle_extension(self, to_remove=None, prompt=True):
        # Determine which extensions are checked
        custom = set(get_custom_subtitle_extensions())
        if to_remove is None:
            to_remove = [ext for ext, cb in self.subtitle_checkboxes.items() if cb.isChecked()]
        if not to_remove:
            if prompt:
                QMessageBox.information(self, "Remove Extensions", "No extensions selected. Check the extensions you want to remove, then click Remove.")
            return
        # Confirm removal with the user
        if prompt:
            confirm = QMessageBox.question(
                self,
                "Confirm Removal",
                "Remove the following subtitle extensions?\n\n" + ", ".join(to_remove),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
        # Update settings - handle both custom and built-in extensions
        custom_to_remove = [ext for ext in to_remove if ext in custom]
        builtin_to_disable = [ext for ext in to_remove if ext in SUBTITLE_EXTENSIONS and ext not in custom]
        
        # Remove custom extensions from custom list
        if custom_to_remove:
            remaining = sorted(custom.difference(custom_to_remove))
            set_custom_subtitle_extensions(remaining)
        
        # Add built-in extensions to disabled list
        if builtin_to_disable:
            current_disabled = set(get_disabled_builtin_subtitle_extensions())
            new_disabled = current_disabled.union(builtin_to_disable)
            set_disabled_builtin_subtitle_extensions(sorted(new_disabled))
        
        # Remove checkboxes from UI and dict
        for ext in to_remove:
            cb = self.subtitle_checkboxes.pop(ext, None)
            if cb:
                cb.setParent(None)
                cb.deleteLater()
        # Also uncheck from enabled list if present
        enabled = set(get_enabled_src_ext())
        changed = False
        for ext in to_remove:
            if ext in enabled:
                enabled.discard(ext)
                changed = True
        if changed:
            set_enabled_subtitle_extensions(sorted(enabled))

    def open_manage_dialog(self, kind: str):
        """Open a Manage dialog for 'video' or 'subtitle' extensions, allowing add/remove with confirmation."""
        is_video = (kind == 'video')
        title = "Manage Video Extensions" if is_video else "Manage Subtitle Extensions"
        all_exts = get_all_video_extensions() if is_video else get_all_subtitle_extensions()
        custom_exts = set(get_custom_video_extensions() if is_video else get_custom_subtitle_extensions())

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
        # Build list of checkboxes, mark custom ones
        cb_map = {}
        for ext in all_exts:
            label = f"{ext} (custom)" if ext in custom_exts else ext
            cb = QCheckBox(label)
            cb.setChecked(False)  # Start unchecked
            cb.setEnabled(True)  # All extensions are selectable for removal
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
        default_btn.setToolTip("Restore built-in extensions and delete custom ones")
        controls.addWidget(add_btn)
        controls.addWidget(remove_btn)
        controls.addWidget(default_btn)
        controls.addStretch()
        v.addLayout(controls)
        
        # Dialog buttons
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
                # Commit removals to cache
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
                "Are you sure? This will restore all default extensions and delete all custom ones.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
                
            if is_video:
                # Update cache with defaults
                self.cached_extension_changes['custom_video'] = []
                self.cached_extension_changes['disabled_video'] = []
                self.cached_extension_changes['enabled_video'] = ['.avi', '.mkv', '.mov', '.mp4', '.webm', '.wmv']
            else:
                # Update cache with defaults
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
            cb.setChecked(False)  # Start unchecked
            cb.setEnabled(True)  # All extensions are selectable for removal
            cb_map[ext] = cb
            content_layout.addWidget(cb)

    def refresh_settings_extensions_list(self):
        # Clear existing video checkboxes
        for cb in self.video_checkboxes.values():
            cb.setParent(None)
            cb.deleteLater()
        self.video_checkboxes.clear()
        
        # Clear existing subtitle checkboxes
        for cb in self.subtitle_checkboxes.values():
            cb.setParent(None)
            cb.deleteLater()
        self.subtitle_checkboxes.clear()
        
        # Rebuild video extensions list
        all_video_exts = self.get_all_video_extensions()
        for ext in all_video_exts:
            checkbox = QCheckBox(ext)
            self.video_checkboxes[ext] = checkbox
            self.video_extensions_layout.addWidget(checkbox)
        
        # Rebuild subtitle extensions list
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
    
    def get_always_prompt_multi(self):
        return self.always_prompt_multi_checkbox.isChecked()
    
    def get_always_prompt_tag(self):
        return self.always_prompt_tag_checkbox.isChecked()
    
    def get_cache_per_set(self):
        return self.cache_per_set_checkbox.isChecked()
    
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

class MainWindow(QWidget):
    log_signal = pyqtSignal(str)
    status_update_signal = pyqtSignal(dict)  # Signal for updating status
    job_completed_signal = pyqtSignal()  # Signal for job completion
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Subtitle Renamer")
        self.setWindowIcon(QIcon("appicon.png"))
        self.target_folder = None
        self.selected_files = []
        self.src_ext = sr.DEFAULT_SRC_EXT
        self.dst_ext = sr.DEFAULT_DST_EXT
        self.cust_ext = sr.DEFAULT_CUST_EXT
        # Subtitle tracking system
        self.subtitle_status = {}  # {file_path: "pending", "success", "failed", "cancelled"}
        
        # Theme system
        settings = load_settings()
        self.current_theme = DARK_THEME if settings.get("dark_mode", False) else LIGHT_THEME
        
        # Initialize checkmark icons with correct theme before creating menus
        CheckmarkAction._update_icons_for_theme()
        
        self.init_ui()
        self.log_signal.connect(self.log_box.append)
        self.status_update_signal.connect(self.update_status_from_signal)
        self.job_completed_signal.connect(self.on_job_completed)
        self.apply_theme()  # Apply initial theme
        self.restore_window_geometry()
        self.restore_splitter_sizes()
        prefs = load_settings()  # Apply initial visibility preferences for switch bar, video table and log

        # Log switch bar
        if prefs.get("show_log_switch_bar", False):
            self.log_switch_bar.show()
            # Ensure menu reflects state
            if hasattr(self, 'toggle_log_switcher_action'):
                self.toggle_log_switcher_action.setChecked(True)
        else:
            self.log_switch_bar.hide()
            if hasattr(self, 'toggle_log_switcher_action'):
                self.toggle_log_switcher_action.setChecked(False)

        initial_zoom = get_zoom_level()  # Apply initial zoom level
        if initial_zoom != 100:
            self.apply_zoom(initial_zoom)

        QTimer.singleShot(0, lambda: (
            self.show_video_table_action.setChecked(prefs.get("show_video_table", True)),
            self.show_log_action.setChecked(prefs.get("show_log", True)),
            (self.toggle_video_table() if prefs.get("show_video_table", True) != self.video_drop_area.isVisible() else None),
            (self.toggle_log()        if prefs.get("show_log", True)        != self.log_box.isVisible()        else None)
        ))

    def init_ui(self):
        layout = QVBoxLayout()
        self.setLayout(layout)

        # Menu Bar
        self.menu_bar = QMenuBar(self)
        self.file_menu = QMenu("File", self)
        self.view_menu = QMenu("View", self)
        self.tools_menu = QMenu("Tools", self)
        self.settings_menu = QMenu("Preference", self)
        self.help_menu = QMenu("Help", self)
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
        self.log_switch_bar.hide()  # Hidden by default unless enabled via Tools > Log
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

        # Target folder controls
        target_row = QHBoxLayout()
        vid_grp = QHBoxLayout()
        vid_grp.setSpacing(1)  # Tight spacing between label and combobox
        self.video_format_label = QLabel("<b>Video format (destination):</b>")
        vid_grp.addWidget(self.video_format_label)
        self.dst_edit = QComboBox()
        self.dst_edit.setEditable(False)
        self.dst_edit.addItems(['Auto'] + get_enabled_dst_ext())
        self.dst_edit.setCurrentText(get_last_dst_format())
        self.dst_edit.setMinimumWidth(80)
        self.dst_edit.setMaximumWidth(120)
        self.dst_edit.currentTextChanged.connect(self.on_dst_format_changed)
        vid_grp.addWidget(self.dst_edit)
        
        sub_grp = QHBoxLayout()
        sub_grp.setSpacing(1)  # Tight spacing between label and combobox
        self.subtitle_format_label = QLabel("<b>Subtitle format (source):</b>")
        sub_grp.addWidget(self.subtitle_format_label)
        self.src_edit = QComboBox()
        self.src_edit.setEditable(False)
        self.src_edit.addItems(['Auto'] + get_enabled_src_ext())
        self.src_edit.setCurrentText(get_last_src_format())
        self.src_edit.setMinimumWidth(80)
        self.src_edit.setMaximumWidth(120)
        self.src_edit.currentTextChanged.connect(self.on_src_format_changed)
        sub_grp.addWidget(self.src_edit)
        
        target_row.addLayout(vid_grp)
        target_row.addLayout(sub_grp)
        target_row.addStretch()  # Push everything to the left
        
        # Target folder button
        self.target_btn = QPushButton("📁 Select Target Folder")
        self.target_btn.clicked.connect(self.select_target_folder)
        target_row.addWidget(self.target_btn)
        
        # Remove all videos button
        self.remove_videos_btn = QPushButton("🗑️ Remove All Videos")
        self.remove_videos_btn.clicked.connect(self.remove_all_videos)
        target_row.addWidget(self.remove_videos_btn)
        self.target_label = QLabel("No target folder selected.")

        # Create main splitter for resizable sections
        self.main_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter.setChildrenCollapsible(False)  # Prevent sections from collapsing completely
        # Disable resizing of the handle between target (index 0) and subtitle (index 1) when video is hidden
        self.main_splitter.setHandleWidth(6)
        
        self.target_container = QWidget()
        target_layout = QVBoxLayout(self.target_container)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(self.video_drop_area)

        # Wrap video controls so we can measure/collapse cleanly when video table is hidden
        self.target_controls_container = QWidget()
        target_controls_layout = QVBoxLayout(self.target_controls_container)
        target_controls_layout.setContentsMargins(0, 0, 0, 0)
        target_controls_layout.setSpacing(6)
        target_controls_layout.addLayout(target_row)
        target_controls_layout.addWidget(self.target_label)
        # Keep controls from expanding vertically
        self.target_controls_container.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        target_layout.addWidget(self.target_controls_container)
        
        # Create subtitle section container
        subtitle_container = QWidget()
        subtitle_layout = QVBoxLayout(subtitle_container)
        subtitle_layout.setContentsMargins(0, 0, 0, 0)
        
        self.drop_area = DropArea(self.on_files_selected)
        subtitle_layout.addWidget(self.drop_area)

        # --- Button Row ---
        btn_row = QHBoxLayout()
        self.open_folder_btn = QPushButton("📂 Browse Files")
        self.open_folder_btn.clicked.connect(self.open_subtitle_folder)
        btn_row.addWidget(self.open_folder_btn)

        self.delete_all_btn = QPushButton("🗑️ Remove All")
        self.delete_all_btn.clicked.connect(self.delete_all_files)
        btn_row.addWidget(self.delete_all_btn)

        self.delete_completed_btn = QPushButton("✅ Remove Completed")
        self.delete_completed_btn.clicked.connect(self.delete_completed_files)
        btn_row.addWidget(self.delete_completed_btn)

        self.redo_btn = QPushButton("🔄 Retry Failed")
        self.redo_btn.clicked.connect(self.redo_failed)
        btn_row.addWidget(self.redo_btn)

        self.rename_all_btn = QPushButton("🚀 Start Renaming")
        self.rename_all_btn.clicked.connect(self.rename_all_files)
        btn_row.addWidget(self.rename_all_btn)

        self.delete_subs_btn = QPushButton("🗑️ Clear All Subs")
        self.delete_subs_btn.clicked.connect(self.delete_all_subs)
        btn_row.addWidget(self.delete_subs_btn)

        subtitle_layout.addLayout(btn_row)
        # --- End Button Row ---
        
        # Log box
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        # Add widgets to splitter in order: video (container), subtitle+buttons, log
        self.main_splitter.addWidget(self.target_container)
        self.main_splitter.addWidget(subtitle_container)
        self.main_splitter.addWidget(self.log_box)
        
        # Set default sizes (proportional): video 25%, subtitle 50%, log 25%
        self.main_splitter.setSizes([250, 500, 250])
        
        # Connect splitter movement to save sizes
        self.main_splitter.splitterMoved.connect(self.on_splitter_moved)
        
        # Wrap the current main content inside the stacked widget's Main page
        self.main_page = QWidget()
        main_page_layout = QVBoxLayout(self.main_page)
        main_page_layout.setContentsMargins(0, 0, 0, 0)
        main_page_layout.addWidget(self.main_splitter)
        self.stacked.addWidget(self.main_page)

        # Create the Log page (displays rename_log.txt)
        self.log_page = QWidget()
        log_page_layout = QVBoxLayout(self.log_page)
        log_page_layout.setContentsMargins(6, 6, 6, 6)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        log_page_layout.addWidget(self.log_view)

        # Buttons for the Log page: Copy All, Clear, Close
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

    def setup_settings_menu(self):
        # Add checkboxes to the settings menu
        self.auto_run_action = CheckmarkAction("Auto-run renaming upon selecting subtitle files")
        self.always_prompt_multi_action = CheckmarkAction("Always prompt for tag if multiple subtitle sets per episode")
        self.always_prompt_tag_action = CheckmarkAction("Always prompt for custom tag (even if no existing sub occupies the default name)")
        self.cache_per_set_action = CheckmarkAction("Cache studio tags per set (not per file)")

        self.settings_menu.addSeparator()
        self.settings_menu.addAction(self.auto_run_action)
        self.settings_menu.addAction(self.always_prompt_multi_action)
        self.settings_menu.addAction(self.always_prompt_tag_action)
        self.settings_menu.addAction(self.cache_per_set_action)
        self.settings_menu.addSeparator()
        
        # Add the fallback settings dialog action
        self.settings_dialog_action = QAction("Settings...", self)
        self.settings_menu.addAction(self.settings_dialog_action)

        # Connect signals for checkable actions
        self.auto_run_action.triggered.connect(self.on_settings_changed)
        self.always_prompt_multi_action.triggered.connect(self.on_settings_changed)
        self.always_prompt_tag_action.triggered.connect(self.on_settings_changed)
        self.cache_per_set_action.triggered.connect(self.on_settings_changed)

        # Connect the settings dialog action
        self.settings_dialog_action.triggered.connect(self.open_settings_dialog)

        # Load current settings
        self.load_settings_to_menu()

    def setup_file_menu(self):
        # Add actions to the file menu
        self.open_target_folder_action = QAction("Open Target Folder...", self)
        self.open_target_folder_action.setShortcut("Ctrl+O")
        self.open_target_folder_action.triggered.connect(self.select_target_folder)
        
        self.open_subtitle_files_action = QAction("Open Subtitle Files...", self)
        self.open_subtitle_files_action.setShortcut("Ctrl+Shift+O")
        self.open_subtitle_files_action.triggered.connect(self.open_subtitle_folder)
        
        self.open_target_in_explorer_action = QAction("Open Target Folder in Explorer", self)
        self.open_target_in_explorer_action.triggered.connect(self.open_target_in_explorer)
        
        self.clear_all_files_action = QAction("Clear All Files", self)
        self.clear_all_files_action.setShortcut("Ctrl+Shift+Del")
        self.clear_all_files_action.triggered.connect(self.delete_all_files)
        
        self.file_menu.addAction(self.open_target_folder_action)
        self.file_menu.addAction(self.open_subtitle_files_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.open_target_in_explorer_action)
        self.file_menu.addSeparator()
        self.file_menu.addAction(self.clear_all_files_action)
        self.file_menu.addSeparator()
        
        # Exit action
        self.exit_action = QAction("Exit", self)
        self.exit_action.setShortcut("Ctrl+Q")
        self.exit_action.triggered.connect(self.close)
        self.file_menu.addAction(self.exit_action)

    def setup_view_menu(self):
        # --- Theme submenu ---
        self.theme_menu = QMenu("Theme", self)
        self.light_theme_action = CheckmarkAction("Light Theme")
        self.light_theme_action.triggered.connect(lambda: self.change_theme(False))
        
        self.dark_theme_action = CheckmarkAction("Dark Theme")
        self.dark_theme_action.triggered.connect(lambda: self.change_theme(True))
        
        self.theme_menu.addAction(self.light_theme_action)
        self.theme_menu.addAction(self.dark_theme_action)
        
        # Set initial theme state
        settings = load_settings()
        dark_mode = settings.get("dark_mode", False)
        self.light_theme_action.setChecked(not dark_mode)
        self.dark_theme_action.setChecked(dark_mode)
        
        # --- Zoom submenu ---
        self.zoom_menu = QMenu("Zoom", self)
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
        show_video_pref = settings.get("show_video_table", True)
        show_log_pref = settings.get("show_log", True)

        self.show_video_table_action = CheckmarkAction("Show Video Table")
        self.show_video_table_action.setChecked(show_video_pref)
        self.show_video_table_action.triggered.connect(self.toggle_video_table)
        
        self.show_log_action = CheckmarkAction("Show Log")
        self.show_log_action.setChecked(show_log_pref)
        self.show_log_action.triggered.connect(self.toggle_log)
        
        self.compact_mode_action = CheckmarkAction("Compact Mode")
        self.compact_mode_action.setChecked(get_compact_mode())
        self.compact_mode_action.triggered.connect(self.toggle_compact_mode)
        
        self.disable_dpi_scaling_action = CheckmarkAction("Disable DPI Scaling")
        self.disable_dpi_scaling_action.setChecked(get_disable_dpi_scaling())
        self.disable_dpi_scaling_action.triggered.connect(self.toggle_dpi_scaling)
        
        self.view_menu.addMenu(self.theme_menu)
        self.view_menu.addMenu(self.zoom_menu)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.show_video_table_action)
        self.view_menu.addAction(self.show_log_action)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.compact_mode_action)
        self.view_menu.addAction(self.disable_dpi_scaling_action)

    def setup_tools_menu(self):
        # --- Renaming tools ---
        self.rename_all_action = QAction("Rename All Files", self)
        self.rename_all_action.setShortcut("F5")
        self.rename_all_action.triggered.connect(self.rename_all_files)
        
        self.retry_failed_action = QAction("Retry Failed Files", self)
        self.retry_failed_action.setShortcut("F6")
        self.retry_failed_action.triggered.connect(self.redo_failed)
        
        self.clear_completed_action = QAction("Clear Completed Files", self)
        self.clear_completed_action.triggered.connect(self.delete_completed_files)
        
        self.clear_all_subs_action = QAction("Clear All Subtitle Files", self)
        self.clear_all_subs_action.triggered.connect(self.delete_all_subs)
        
        # --- Analysis tools ---
        self.analyze_folder_action = QAction("Analyze Target Folder", self)
        self.analyze_folder_action.setShortcut("F11")
        self.analyze_folder_action.triggered.connect(self.analyze_target_folder)
        
        # --- On Complete submenu ---
        self.on_complete_menu = QMenu("On Complete", self)
        self.do_nothing_action = CheckmarkAction("Do Nothing")
        self.do_nothing_action.triggered.connect(lambda: self.set_completion_behavior("do_nothing"))
        
        self.exit_action = CheckmarkAction("Exit")
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

        # --- Log submenu ---
        self.log_menu = QMenu("Log", self)

        # Open log file in popup
        self.open_log_popup_action = QAction("Open Log Window", self)
        self.open_log_popup_action.triggered.connect(self.open_rename_log_popup)
        self.log_menu.addAction(self.open_log_popup_action)

        # Toggle in-app log switcher bar under the menubar
        self.toggle_log_switcher_action = CheckmarkAction("Show Log Switch Bar")
        self.toggle_log_switcher_action.setChecked(load_settings().get("show_log_switch_bar", False))
        self.toggle_log_switcher_action.triggered.connect(self.toggle_log_switch_bar)
        self.log_menu.addAction(self.toggle_log_switcher_action)

        self.tools_menu.addSeparator()
        self.tools_menu.addMenu(self.log_menu)

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
        self.auto_run_action.setChecked(settings.get("auto_run_on_select", True))
        self.always_prompt_multi_action.setChecked(settings.get("always_prompt_tag_multi_set", False))
        self.always_prompt_tag_action.setChecked(settings.get("always_prompt_tag_always", False))
        self.cache_per_set_action.setChecked(settings.get("cache_per_set", True))
        
        # Load completion behavior
        completion_behavior = settings.get("completion_behavior", "do_nothing")
        self.do_nothing_action.setChecked(completion_behavior == "do_nothing")
        self.exit_action.setChecked(completion_behavior == "exit")

    def on_settings_changed(self):
        settings = load_settings()
        settings["auto_run_on_select"] = self.auto_run_action.isChecked()
        settings["always_prompt_tag_multi_set"] = self.always_prompt_multi_action.isChecked()
        settings["always_prompt_tag_always"] = self.always_prompt_tag_action.isChecked()
        settings["cache_per_set"] = self.cache_per_set_action.isChecked()
        save_settings(settings)

    def set_completion_behavior(self, behavior):
        """Set what happens when job completes: 'do_nothing' or 'exit'"""
        self.do_nothing_action.setChecked(behavior == "do_nothing")
        self.exit_action.setChecked(behavior == "exit")
        settings = load_settings()
        settings["completion_behavior"] = behavior
        save_settings(settings)

    def on_job_completed(self):
        """Check if we should exit upon job completion"""
        settings = load_settings()
        completion_behavior = settings.get("completion_behavior", "do_nothing")
        
        if completion_behavior == "exit":
            # Only exit if ALL files are successfully completed
            if self.subtitle_status and all(status == "success" for status in self.subtitle_status.values()):
                QApplication.instance().quit()

    def apply_theme(self):
        """Apply the current theme to the entire application"""
        # Build paths to static chevron icons
        base_dir = os.path.dirname(os.path.abspath(__file__))
        if self.current_theme == DARK_THEME:
            arrow_file = "chevrons_dark.svg"
            submenu_arrow_file = "chevron_right_dark.svg"
        else:
            arrow_file = "chevrons_light.svg"
            submenu_arrow_file = "chevron_right_light.svg"
        _arrow_path = os.path.join(base_dir, "assets", arrow_file).replace('\\', '/')
        _submenu_arrow_path = os.path.join(base_dir, "assets", submenu_arrow_file).replace('\\', '/')

        self.setStyleSheet(f'''
            QWidget {{
                background: {self.current_theme['window_bg']};
                color: {self.current_theme['text_color']};
            }}
            QTabBar::tab {{
                background: {self.current_theme['widget_bg']};
                color: {self.current_theme['text_color']};
                padding: 6px 14px;
                border: 1px solid {self.current_theme['border_color']};
                border-bottom: none;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 4px;
            }}
            QTabBar::tab:hover {{
                background: {self.current_theme['button_hover']};
            }}
            QTabBar::tab:selected {{
                background: {self.current_theme['window_bg']};
                color: {self.current_theme['text_color']};
            }}
            QPushButton {{
                background: {self.current_theme['button_bg']};
                color: {self.current_theme['text_color']};
                border: 1px solid {self.current_theme['border_color']};
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: {self.current_theme['button_hover']};
            }}
            QPushButton:pressed {{
                background: {self.current_theme['border_color']};
            }}
            QLabel {{
                color: {self.current_theme['text_color']};
                padding: 4px;
            }}
            QComboBox {{
                background: {self.current_theme['widget_bg']};
                color: {self.current_theme['text_color']};
                border: 1px solid {self.current_theme['border_color']};
                padding: 6px;
                border-radius: 4px;
            }}
            QComboBox::drop-down {{
                border: none;
                width: 26px;
                background: transparent;
            }}
            QComboBox::down-arrow {{
                image: url("{_arrow_path}");
                width: 16px;
                height: 20px;
                margin-right: 6px;
                margin-top: 0px;
            }}
            QComboBox QAbstractItemView {{
                background: {self.current_theme['widget_bg']};
                color: {self.current_theme['text_color']};
                border: 1px solid {self.current_theme['border_color']};
            }}
            QTextEdit {{
                background: {self.current_theme['log_bg']};
                color: {self.current_theme['text_color']};
                border: 1px solid {self.current_theme['border_color']};
                border-radius: 4px;
                padding: 8px;
            }}
            QTableWidget {{
                background: {self.current_theme['widget_bg']};
                color: {self.current_theme['text_color']};
                gridline-color: {self.current_theme['border_color']};
                alternate-background-color: {self.current_theme['table_alternate_bg']};
            }}
            QTableWidget::item {{
                padding: 4px;
            }}
            QTableWidget::item:selected {{
                background: #3874f2;
                color: white;
            }}
            QHeaderView {{
                background: {self.current_theme['table_header_bg']};
                color: {self.current_theme['text_color']};
            }}
            QHeaderView::section {{
                background: {self.current_theme['table_header_bg']};
                color: {self.current_theme['text_color']};
                padding: 4px;
                border: 1px solid {self.current_theme['border_color']};
            }}
            QTableCornerButton::section {{
                background: {self.current_theme['table_header_bg']};
                color: {self.current_theme['text_color']};
                border: 1px solid {self.current_theme['border_color']};
            }}
            QMenuBar {{
                background: {self.current_theme['widget_bg']};
                color: {self.current_theme['text_color']};
                border-bottom: 1px solid {self.current_theme['border_color']};
            }}
            QMenuBar::item {{
                background: transparent;
                padding: 6px 12px;
                border-radius: 4px;
            }}
            QMenuBar::item:selected {{
                background: {self.current_theme['button_hover']};
            }}
            QMenu {{
                background: {self.current_theme['widget_bg']};
                color: {self.current_theme['text_color']};
                border: 1px solid {self.current_theme['border_color']};
                border-radius: 6px;
            }}
            QMenu::item {{
                padding: 6px 0px 6px 4px;
                margin-left: 8px;
                margin-right: 8px;
                border-radius: 4px;
            }}
            QMenu::item:selected {{
                background: {self.current_theme['button_hover']};
            }}
            QMenu::separator {{
                height: 1px;
                background: {self.current_theme['border_color']};
                margin: 4px 0px;
            }}
            QMenu::right-arrow {{
                image: url("{_submenu_arrow_path}");
                width: 12px;
                height: 12px;
                padding: 6px 10px 6px 10px;
                margin-left: 4px;
            }}
            QScrollBar:vertical {{
                background: {self.current_theme['widget_bg']};
                width: 16px;
                margin: 2px;
                border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {self.current_theme['border_color']};
                min-height: 20px;
                border-radius: 4px;
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
        ''')
        
        # Update drop area theme
        self.drop_area.update_theme(self.current_theme)
        self.video_drop_area.update_theme(self.current_theme)
        
        # Apply Windows title bar theming (Windows 10/11 only)
        if platform.system() == "Windows":
            # Determine if we're using a dark theme
            is_dark_theme = self.current_theme == DARK_THEME
            set_windows_title_bar_theme(self, is_dark_theme)

    def change_theme(self, dark_mode):
        self.current_theme = DARK_THEME if dark_mode else LIGHT_THEME
        
        # Update theme actions
        self.light_theme_action.setChecked(not dark_mode)
        self.dark_theme_action.setChecked(dark_mode)
        
        # Save theme preference
        settings = load_settings()
        settings["dark_mode"] = dark_mode
        save_settings(settings)
        
        # Update checkmark icons for new theme
        CheckmarkAction._update_icons_for_theme()
        self._refresh_checkmark_actions()
        self.apply_theme()

    def _refresh_checkmark_actions(self):
        """Refresh all checkmark actions to update their icons for the new theme"""
        # Find all CheckmarkAction instances in menus and update their icons
        for menu in [self.settings_menu, self.view_menu, self.tools_menu, self.theme_menu, self.on_complete_menu]:
            for action in menu.actions():
                if isinstance(action, CheckmarkAction):
                    action._update_icon()

    def showEvent(self, event):
        """Override showEvent to ensure title bar theming is applied when window is shown"""
        super().showEvent(event)
        # Apply title bar theming after window is shown (when HWND is valid)
        if platform.system() == "Windows":
            is_dark_theme = self.current_theme == DARK_THEME
            set_windows_title_bar_theme(self, is_dark_theme)

    def select_target_folder(self):
        last_folder = get_last_target_folder()
        folder = QFileDialog.getExistingDirectory(self, "Select Target Folder (Video/Output)", last_folder)
        if folder:
            self.target_folder = folder
            self.target_label.setText(f"Target Folder: {folder}")
            set_last_target_folder(folder)
            self.update_video_table(log_count=True)
            self.update_subtitle_count()
            
            # Check if "Auto" is selected for destination format and update accordingly
            if self.dst_edit.currentText() == "Auto":
                self.on_dst_format_changed("Auto")
            
            # If files were already selected, check auto-run setting before running
            if self.selected_files:
                settings = load_settings()
                auto_run = settings.get("auto_run_on_select", True)
                if auto_run:
                    self.run_renamer()
                # else: wait for user to click "Rename All"

    def open_settings_dialog(self):
        dlg = SettingsDialog(self)
        if dlg.exec():
            # Save the settings
            settings = load_settings()
            settings["auto_run_on_select"] = dlg.get_auto_run()
            settings["always_prompt_tag_multi_set"] = dlg.get_always_prompt_multi()
            settings["always_prompt_tag_always"] = dlg.get_always_prompt_tag()
            settings["cache_per_set"] = dlg.get_cache_per_set()
            save_settings(settings)
            
            # Save extension settings
            set_enabled_video_extensions(dlg.get_enabled_dst_ext())
            set_enabled_subtitle_extensions(dlg.get_enabled_src_ext())
            
            # Refresh comboboxes with new enabled extensions
            self.refresh_extension_comboboxes()

            # Immediately reflect updated settings in the Preference menu
            self.load_settings_to_menu()

    def open_rename_log_popup(self):
        """Open rename_log.txt in a simple popup window"""
        log_path = os.path.join(os.getcwd(), "rename_log.txt")
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

        # Add three buttons: Close, Copy All, Clear
        btns = QDialogButtonBox()
        close_btn = btns.addButton("Close", QDialogButtonBox.ButtonRole.RejectRole)
        copy_btn = btns.addButton("Copy All", QDialogButtonBox.ButtonRole.ActionRole)
        clear_btn = btns.addButton("Clear", QDialogButtonBox.ButtonRole.ActionRole)

        close_btn.clicked.connect(dlg.reject)

        # Copy all log text to clipboard when "Copy All" is pressed
        copy_btn.clicked.connect(self.on_log_copy_btn)

        # Clear the log file and update the text box when "Clear" is pressed
        clear_btn.clicked.connect(self.on_log_clear_btn)
        clear_btn.clicked.connect(dlg.reject)

        v.addWidget(btns)
        # Scroll to end so latest log lines are visible (PyQt6 enum compatibility)
        try:
            edit.moveCursor(QTextCursor.MoveOperation.End)
        except AttributeError:
            # Fallback for environments exposing QTextCursor.End
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
        s["show_log_switch_bar"] = show
        save_settings(s)

    def on_log_switch_changed(self, index: int):
        """Switch between Main and Log views using the tab bar."""
        # 0 = Main page, 1 = Log page (rename_log.txt)
        if index == 1:
            self.stacked.setCurrentWidget(self.log_page)
            self.load_rename_log_into_view()
            self.log_view.setFocus()
        else:
            self.stacked.setCurrentWidget(self.main_page)

    def load_rename_log_into_view(self):
        """Load the contents of rename_log.txt into the dedicated log view."""
        try:
            log_path = os.path.join(os.path.dirname(__file__), "rename_log.txt")
            if os.path.exists(log_path):
                with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            else:
                text = "rename_log.txt not found."
        except Exception as e:
            text = f"Error reading log: {e}"
        self.log_view.setPlainText(text)
        # Scroll to end to show latest lines
        try:
            self.log_view.moveCursor(QTextCursor.MoveOperation.End)
        except AttributeError:
            self.log_view.moveCursor(QTextCursor.End)
        self.log_view.ensureCursorVisible()

    def on_log_close_btn(self):
        """Switch back to the Main page from the Log tab."""
        if hasattr(self, 'stacked') and hasattr(self, 'main_page'):
            self.stacked.setCurrentWidget(self.main_page)
        if hasattr(self, 'log_switch_bar'):
            self.log_switch_bar.setCurrentIndex(0)

    def on_log_copy_btn(self):
        """Copy all text from the log view to the clipboard."""
        clipboard = QApplication.instance().clipboard()
        clipboard.setText(self.log_view.toPlainText())

    def on_log_clear_btn(self):
        """Clear rename_log.txt and the in-tab log view."""
        log_path = os.path.join(os.path.dirname(__file__), "rename_log.txt")
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("")
            self.log_view.setPlainText("")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to clear log file:\n{e}")

    def on_files_selected(self, files):
        self.selected_files = files
        # Initialize status tracking for new files
        for file_path in files:
            self.subtitle_status[file_path] = "pending"
        self.update_video_table(log_count=False)  # Don't log count when subtitle files are selected
        # Always log the subtitle count first
        self.log_signal.emit(f"Selected {len(files)} subtitle files.")
        
        # Check if "Auto" is selected for source format and update accordingly
        if self.src_edit.currentText() == "Auto":
            self.on_src_format_changed("Auto")
        
        # Check auto-run setting
        settings = load_settings()
        auto_run = settings.get("auto_run_on_select", True)
        if auto_run:
            self.run_renamer()
        # else: wait for user to click "Rename All"

    def run_renamer(self):
        if not self.target_folder:
            self.log_signal.emit("<b>Please select the target folder first.</b>")
            return

        self.log_signal.emit("<b>Processing...</b>")

        def worker():
            try:
                settings = load_settings()
                always_prompt_multi = settings.get("always_prompt_tag_multi_set", False)
                always_prompt_tag = settings.get("always_prompt_tag_always", False)
                cache_per_set = settings.get("cache_per_set", True)
                # Pass always_prompt_multi and cache_per_set to the renaming logic
                results = sr.run_job(
                    directory=self.target_folder,
                    src_ext=self.src_ext,
                    dst_ext=self.dst_ext,
                    cust_ext=self.cust_ext,
                    log_file=None,
                    ask_fn=self.ask_user,
                    subtitle_files=self.selected_files,
                    always_prompt_multi=always_prompt_multi,
                    always_prompt_tag=always_prompt_tag,
                    cache_per_set=cache_per_set
                )
                # Emit signal to update status from main thread
                self.status_update_signal.emit(results)
                success_count = len(results.get('OK', []))
                failed_count = len(results.get('FAIL', []))
                cancelled_count = len(results.get('CANCELLED', []))
                self.log_signal.emit(f"<b>Done! Success: {success_count}, Failed: {failed_count}, Cancelled: {cancelled_count}</b>")
                logging.info(f"Success: {success_count}, Failed: {failed_count}, Cancelled: {cancelled_count}")
                
                # Emit job completion signal
                self.job_completed_signal.emit()
            except Exception as e:
                self.log_signal.emit(f"<b>Error:</b> {e}")
                logging.error(f"Error: {e}")
                # Still emit completion signal even on error
                self.job_completed_signal.emit()

        threading.Thread(target=worker, daemon=True).start()

    def ask_user(self, prompt: str) -> str:
        answer_box = {}
        done = threading.Event()

        def _do_dialog():
            text, ok = QInputDialog.getText(self, "Subtitle Renamer", prompt)
            answer_box['val'] = text if ok else None
            done.set()

        QMetaObject.invokeMethod(self, "_invoke", Qt.ConnectionType.QueuedConnection, Q_ARG(object, _do_dialog))
        done.wait()
        return answer_box.get('val', '')

    def log_async(self, html):
        self.log_signal.emit(html)

    def open_subtitle_folder(self):
        last_folder = get_last_subtitle_folder()
        files, _ = QFileDialog.getOpenFileNames(self, "Select Subtitle Files", last_folder, get_subtitle_file_filter())
        if files:
            self.drop_area.display_files(files)
            self.on_files_selected(files)
            set_last_subtitle_folder(os.path.dirname(files[0]))

    def update_video_table(self, log_count=False):
        # Determine extension to use for listing videos
        ext_text = self.dst_edit.currentText().strip()
        if not self.target_folder:
            self.video_drop_area.clear_files()
            return
            
        # Resolve Auto against ALL supported extensions (built-in + custom)
        if not ext_text or ext_text == "Auto":
            all_video_files = [
                os.path.join(self.target_folder, f)
                for f in os.listdir(self.target_folder)
                if any(f.lower().endswith(e) for e in get_all_video_extensions())
            ]
            ext = self.get_comm_ext(all_video_files, get_all_video_extensions()) or '.mp4'
        else:
            ext = ext_text
        
        self.dst_ext = ext
        
        files = [
            os.path.join(self.target_folder, f)
            for f in os.listdir(self.target_folder)
            if f.lower().endswith(ext.lower())
        ]
        
        if files:
            self.video_drop_area.display_files(sorted(files))
        else:
            self.video_drop_area.clear_files()
            
        # Log the number of video files found only when requested
        if log_count:
            self.log_signal.emit(f"Found {len(files)} video ({ext}) files.")

    def update_subtitle_count(self):
        """Log the count of subtitle files in the target folder"""
        if not self.target_folder:
            return
        ext_text = self.src_edit.currentText().strip()
        if not ext_text:
            return
        if ext_text == "Auto":
            files_all = [f for f in os.listdir(self.target_folder) if any(f.lower().endswith(e) for e in get_all_subtitle_extensions())]
            ext = self.get_comm_ext([os.path.join(self.target_folder, f) for f in files_all], get_all_subtitle_extensions()) or '.ass'
        else:
            ext = ext_text
        self.src_ext = ext
        files = [f for f in os.listdir(self.target_folder) if f.lower().endswith(ext.lower())]
        self.log_signal.emit(f"Found {len(files)} subtitle ({ext}) files.")

    def update_subtitle_status_display(self):
        """Update the visual status of subtitle files in the table"""
        try:
            for row in range(self.drop_area.table.rowCount()):
                path_item = self.drop_area.table.item(row, 1)
                status_item = self.drop_area.table.item(row, 2)
                if path_item and status_item:
                    file_path = path_item.text()
                    status = self.subtitle_status.get(file_path, "pending")
                    
                    if status == "success":
                        status_text = "✅" if get_compact_mode() else "✅ Success"
                        status_item.setText(status_text)
                        color = QColor(0, 128, 0)
                        status_item.setToolTip("Status: Success")
                    elif status == "failed":
                        status_text = "❌" if get_compact_mode() else "❌ Failed"
                        status_item.setText(status_text)
                        color = QColor(255, 69, 0)
                        status_item.setToolTip("Status: Failed")
                    elif status == "cancelled":
                        status_text = "🚫" if get_compact_mode() else "🚫 Cancelled"
                        status_item.setText(status_text)
                        color = QColor(255, 0, 0)
                        status_item.setToolTip("Status: Cancelled")
                    else:  # pending
                        status_text = "⏳" if get_compact_mode() else "⏳ Pending"
                        status_item.setText(status_text)
                        color = QColor(128, 128, 128)
                        status_item.setToolTip("Status: Pending")
                    
                    for col in range(self.drop_area.table.columnCount()):
                        item = self.drop_area.table.item(row, col)
                        if item:
                            item.setForeground(color)
            
            # Resize columns to fit content dynamically
            self.drop_area.table.resizeColumnsToContents()
        except Exception as e:
            self.log_signal.emit(f"<b>Error updating status display: {e}</b>")

    def update_status_from_signal(self, results):
        """Update status tracking and display from signal (called in main thread)"""
        try:
            # Update status tracking
            for file_path in results.get("OK", []):
                self.subtitle_status[file_path] = "success"
            for file_path in results.get("FAIL", []):
                self.subtitle_status[file_path] = "failed"
            for file_path in results.get("CANCELLED", []):
                self.subtitle_status[file_path] = "cancelled"
            # Update UI with results
            self.update_subtitle_status_display()
        except Exception as e:
            self.log_signal.emit(f"<b>Error updating status from signal: {e}</b>")

    # Update video table if video extension changes
    def on_dst_ext_changed(self):
        self.update_video_table(log_count=False)  # Don't log count on extension change

    def delete_all_files(self):
        self.selected_files = []
        self.subtitle_status.clear()
        self.drop_area.clear_files()
        self.log_signal.emit("<b>All files deleted from table.</b>")

    def remove_all_videos(self):
        self.video_drop_area.clear_files()

    def delete_completed_files(self):
        completed_files = [file_path for file_path, status in self.subtitle_status.items() if status == "success"]
        
        if not completed_files:
            self.log_signal.emit("<b>No completed files to remove.</b>")
            return
        
        # Remove from selected_files list
        self.selected_files = [f for f in self.selected_files if f not in completed_files]
        
        # Remove from status tracking
        for file_path in completed_files:
            del self.subtitle_status[file_path]
        
        # Remove from table display
        rows_to_remove = []
        for row in range(self.drop_area.table.rowCount()):
            path_item = self.drop_area.table.item(row, 1)
            if path_item and path_item.text() in completed_files:
                rows_to_remove.append(row)
        
        # Remove rows in reverse order to avoid index shifting
        for row in reversed(rows_to_remove):
            self.drop_area.table.removeRow(row)
        
        # Check if table is now empty, if so, switch back to drop area view
        if self.drop_area.table.rowCount() == 0:
            self.drop_area.clear_files()
        else:
            # Update display for remaining files
            self.update_subtitle_status_display()
        self.log_signal.emit(f"<b>Removed {len(completed_files)} completed files from table.</b>")

    def redo_failed(self):
        failed_files = [file_path for file_path, status in self.subtitle_status.items() if status == "failed"]
        cancelled_files = [file_path for file_path, status in self.subtitle_status.items() if status == "cancelled"]
        retry_files = failed_files + cancelled_files
        
        if not retry_files:
            self.log_signal.emit("<b>No failed or cancelled files to retry.</b>")
            return
        if not self.target_folder:
            self.log_signal.emit("<b>Please select the target folder first.</b>")
            return
        
        # Reset status to pending for retry and update display
        for file_path in retry_files:
            self.subtitle_status[file_path] = "pending"
        
        # Update display to show pending status
        self.update_subtitle_status_display()
        
        def worker():
            try:
                settings = load_settings()
                always_prompt_multi = settings.get("always_prompt_tag_multi_set", False)
                always_prompt_tag = settings.get("always_prompt_tag_always", False)
                cache_per_set = settings.get("cache_per_set", True)
                
                results = sr.run_job(
                    directory=self.target_folder,
                    src_ext=self.src_ext,
                    dst_ext=self.dst_ext,
                    cust_ext=self.cust_ext,
                    log_file=None,
                    ask_fn=self.ask_user,
                    subtitle_files=retry_files,
                    always_prompt_multi=always_prompt_multi,
                    always_prompt_tag=always_prompt_tag,
                    cache_per_set=cache_per_set
                )
                
                # Emit signal to update status from main thread
                self.status_update_signal.emit(results)
                success_count = len(results.get('OK', []))
                failed_count = len(results.get('FAIL', []))
                cancelled_count = len(results.get('CANCELLED', []))
                self.log_signal.emit(f"<b>Retry complete! Success: {success_count}, Still Failed: {failed_count}, Cancelled: {cancelled_count}</b>")
                logging.info(f"Retry complete! Success: {success_count}, Still Failed: {failed_count}, Cancelled: {cancelled_count}")
                
                # Emit job completion signal
                self.job_completed_signal.emit()
            except Exception as e:
                self.log_signal.emit(f"<b>Error during retry:</b> {e}")
                logging.error(f"Error during retry: {e}")
                # Still emit completion signal even on error
                self.job_completed_signal.emit()
        
        threading.Thread(target=worker, daemon=True).start()

    def rename_all_files(self):
        if not self.selected_files:
            self.log_signal.emit("<b>No files to rename.</b>")
            return
        self.run_renamer()

    def delete_all_subs(self):
        if not self.target_folder:
            self.log_signal.emit("<b>No target folder selected.</b>")
            return
        count = 0
        enabled_subtitle_exts = get_enabled_src_ext()
        for fname in os.listdir(self.target_folder):
            if any(fname.lower().endswith(ext) for ext in enabled_subtitle_exts):
                try:
                    os.remove(os.path.join(self.target_folder, fname))
                    count += 1
                except Exception as e:
                    self.log_signal.emit(f"<b>Error deleting {fname}: {e}</b>")
        self.log_signal.emit(f"<b>Deleted {count} subtitle files from target folder.</b>")
        self.update_video_table()

    def open_target_in_explorer(self):
        """Open the target folder in Windows Explorer"""
        if self.target_folder and os.path.exists(self.target_folder):
            try:
                subprocess.run(['explorer', self.target_folder], check=True)
                self.log_signal.emit(f"<b>Opened target folder in Explorer: {self.target_folder}</b>")
            except Exception as e:
                self.log_signal.emit(f"<b>Error opening folder: {e}</b>")
        else:
            self.log_signal.emit("<b>No valid target folder selected.</b>")

    def toggle_video_table(self):
        """Toggle the visibility of the video drop area"""
        # We keep the controls visible always; only hide/show the table widget
        if self.video_drop_area.isVisible():
            self.video_drop_area.hide()
            # Ensure the controls container remains visible and splitter rebalances
            if hasattr(self, 'target_controls_container'):
                self.target_controls_container.show()
            self.show_video_table_action.setChecked(False)
        else:
            self.video_drop_area.show()
            if hasattr(self, 'target_controls_container'):
                self.target_controls_container.show()
            self.show_video_table_action.setChecked(True)
        
        # Adjust splitter to collapse/expand the video area space when toggled
        if hasattr(self, 'main_splitter') and hasattr(self, 'target_container'):
            sizes = self.main_splitter.sizes()
            if sizes and len(sizes) == 3:
                video_size, sub_size, log_size = sizes
                if not self.video_drop_area.isVisible():
                    # Collapse the video area to its controls' height only
                    controls_height = self.target_controls_container.sizeHint().height() if hasattr(self, 'target_controls_container') else 60
                    spare = video_size - controls_height
                    if spare > 0:
                        # Allocate spare height to subtitle area by default
                        sub_size += spare
                        video_size = controls_height
                else:
                    # Expand video area to a reasonable portion (25%) from current total
                    total = sum(sizes)
                    desired_video = max(150, int(total * 0.25))
                    delta = desired_video - video_size
                    if delta != 0:
                        # Take/give space primarily to subtitle area
                        sub_size = max(100, sub_size - delta)
                        video_size = desired_video
                self.main_splitter.setSizes([video_size, sub_size, log_size])
        
        # Persist preference
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
        # Persist preference
        settings = load_settings()
        settings["show_log"] = self.log_box.isVisible()
        save_settings(settings)

    def toggle_compact_mode(self):
        """Toggle compact mode on/off"""
        compact_mode = self.compact_mode_action.isChecked()
        set_compact_mode(compact_mode)
        
        if compact_mode:
            self.apply_compact_mode()
        else:
            self.remove_compact_mode()

    def toggle_dpi_scaling(self):
        """Toggle DPI scaling on/off"""
        disable_dpi = self.disable_dpi_scaling_action.isChecked()
        set_disable_dpi_scaling(disable_dpi)
        
        if disable_dpi:
            self.log_signal.emit("<b>DPI scaling disabled. Restart the application for changes to take effect.</b>")
        else:
            self.log_signal.emit("<b>DPI scaling enabled. Restart the application for changes to take effect.</b>")

    def apply_compact_mode(self):
        """Apply compact mode - remove text from buttons and status text"""
        # Store original texts for restoration
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
        
        # Remove text from buttons (keep only icons)
        self.target_btn.setText("📁")
        self.remove_videos_btn.setText("🗑️")
        self.open_folder_btn.setText("📂")
        self.delete_all_btn.setText("🗑️")
        self.delete_completed_btn.setText("✅")
        self.redo_btn.setText("🔄")
        self.rename_all_btn.setText("🚀")
        self.delete_subs_btn.setText("🗑️")
        
        # Update table headers to remove status text
        self.drop_area.table.setHorizontalHeaderLabels(["File Name", "Path", ""])
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
        
        # Restore table headers
        self.drop_area.table.setHorizontalHeaderLabels(["File Name", "Path", "Status"])
        self.video_drop_area.table.setHorizontalHeaderLabels(["Video File Name", "Size (MB)"])

    def analyze_target_folder(self):
        """Analyze the target folder and show detailed information"""
        if not self.target_folder:
            self.log_signal.emit("<b>No target folder selected.</b>")
            return
        
        try:
            video_ext = self.dst_edit.currentText().strip()
            sub_ext = self.src_edit.currentText().strip()
            
            video_files = [f for f in os.listdir(self.target_folder) if f.lower().endswith(video_ext.lower())]
            sub_files = [f for f in os.listdir(self.target_folder) if f.lower().endswith(sub_ext.lower())]
            
            total_size = sum(os.path.getsize(os.path.join(self.target_folder, f)) for f in video_files + sub_files)
            total_size_mb = total_size / (1024 * 1024)
            
            self.log_signal.emit(f"<b>Target Folder Analysis:</b>")
            self.log_signal.emit(f"• Folder: {self.target_folder}")
            self.log_signal.emit(f"• Video files ({video_ext}): {len(video_files)} files")
            self.log_signal.emit(f"• Subtitle files ({sub_ext}): {len(sub_files)} files")
            self.log_signal.emit(f"• Total size: {total_size_mb:.2f} MB")
            self.log_signal.emit(f"• Video files: {', '.join(video_files[:5])}{'...' if len(video_files) > 5 else ''}")
            self.log_signal.emit(f"• Subtitle files: {', '.join(sub_files[:5])}{'...' if len(sub_files) > 5 else ''}")
            
        except Exception as e:
            self.log_signal.emit(f"<b>Error analyzing folder: {e}</b>")

    def show_about(self):
        """Show the about dialog"""
        QMessageBox.about(self, "About Subtitle Renamer",
                         "Subtitle Renamer v1.0\n\n"
                         "A tool for automatically renaming subtitle files to match video files.\n\n"
                         "Features:\n"
                         "• Drag and drop subtitle files\n"
                         "• Automatic video file detection\n"
                         "• Custom tag support\n"
                         "• Dark/Light theme support\n"
                         "• Batch processing\n\n"
                         "Created with PyQt6")

    def show_help(self):
        """Show the help dialog"""
        help_text = f"""
<b>Subtitle Renamer Help</b>

<b>Getting Started:</b>
1. Select a target folder containing video files
2. Add subtitle files by clicking the drop area or using File menu
3. Configure video and subtitle formats if needed
4. Click "Start Renaming" or press F5

<b>Keyboard Shortcuts:</b>
• Ctrl+O: Open target folder
• Ctrl+Shift+O: Open subtitle files
• F5: Start renaming
• F6: Retry failed files
• Ctrl+Q: Exit application
• F1: Show this help
• Ctrl++: Zoom in
• Ctrl+-: Zoom out
• Ctrl+0: Reset zoom

<b>Features:</b>
• Drag and drop subtitle files
• Automatic video file detection
• Custom tag support for multiple subtitle sets
• Dark/Light theme support
• Zoom controls (50% - 200%)
• Batch processing with status tracking
• Folder analysis tools

<b>File Formats:</b>
• Supported video formats: {', '.join(get_all_video_extensions())}
• Supported subtitle formats: {', '.join(get_all_subtitle_extensions())}

<b>Tips:</b>
• Use the Tools menu for quick access to common functions
• Check the View menu to customize the interface and zoom level
• Use the Preference menu to configure automatic behavior
        """
        QMessageBox.information(self, "Help", help_text)

    @pyqtSlot(object)
    def _invoke(self, fn):
        fn()

    def save_window_geometry(self):
        """Save window size and position to settings"""
        settings = load_settings()
        settings["window_geometry"] = {
            "x": self.x(),
            "y": self.y(),
            "width": self.width(),
            "height": self.height(),
            "maximized": self.isMaximized()
        }
        save_settings(settings)

    def restore_window_geometry(self):
        """Restore window size and position from settings"""
        settings = load_settings()
        geometry = settings.get("window_geometry")
        
        if geometry:
            # Check if the saved position is still valid (on screen)
            screen = QApplication.primaryScreen().availableGeometry()
            x = geometry.get("x", 100)
            y = geometry.get("y", 100)
            width = geometry.get("width", 1440)
            height = geometry.get("height", 900)
            
            # Ensure window is at least partially visible
            if x + 100 > screen.width():
                x = screen.width() - width
            if y + 100 > screen.height():
                y = screen.height() - height
            if x < -width + 100:
                x = 0
            if y < 0:
                y = 0
                
            # Apply geometry
            self.setGeometry(x, y, width, height)
            
            # Restore maximized state if needed
            if geometry.get("maximized", False):
                self.showMaximized()
        else:
            # Default size if no saved geometry
            self.resize(1440, 900)
            # Center the window on screen
            screen = QApplication.primaryScreen().availableGeometry()
            self.move((screen.width() - self.width()) // 2,
                      (screen.height() - self.height()) // 2)

    def on_splitter_moved(self, pos, index):
        """Called when splitter is moved - handle special cases and save sizes"""
        # If user drags the handle between target and subtitle while video is hidden,
        # interpret that as intention to reveal video area, so unhide it.
        if index == 1 and hasattr(self, 'video_drop_area') and not self.video_drop_area.isVisible():
            self.video_drop_area.show()
            if hasattr(self, 'show_video_table_action'):
                self.show_video_table_action.setChecked(True)
            # Expand video area to a reasonable portion
            if hasattr(self, 'main_splitter'):
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
        if hasattr(self, 'main_splitter'):
            settings = load_settings()
            settings["splitter_sizes"] = self.main_splitter.sizes()
            save_settings(settings)

    def restore_splitter_sizes(self):
        """Restore splitter sizes from settings"""
        if hasattr(self, 'main_splitter'):
            settings = load_settings()
            sizes = settings.get("splitter_sizes")
            
            if sizes and len(sizes) == 3:
                # Ensure minimum sizes for each section
                min_size = 100  # Minimum height for each section
                total_size = sum(sizes)
                
                # Validate that sizes are reasonable
                if all(size >= min_size for size in sizes) and total_size > 0:
                    self.main_splitter.setSizes(sizes)
                else:
                    # Fallback to default proportional sizes
                    total_height = self.main_splitter.height() or 900
                    default_sizes = [
                        int(total_height * 0.25),  # Video: 25%
                        int(total_height * 0.50),  # Subtitle: 50% 
                        int(total_height * 0.25)   # Log: 25%
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
                    self.log_signal.emit(f"Auto-selected video format: {comm}")
                else:
                    # No video files found
                    self.dst_ext = '.mp4'
                    self.log_signal.emit("No video files found")
            else:
                # No target folder
                self.dst_ext = '.mp4'
        else:
            self.dst_ext = text

    def on_src_format_changed(self, text):
        """Handle source format combobox change"""
        # Save the selection to settings
        set_last_src_format(text)
        
        if text == "Auto":
            if self.selected_files:
                # Find most common subtitle extension
                comm = self.get_comm_ext(self.selected_files, get_all_subtitle_extensions())
                if comm:
                    self.src_ext = comm
                    self.log_signal.emit(f"Auto-selected subtitle format: {comm}")
                else:
                    # No subtitle files found
                    self.src_ext = '.ass'
                    self.log_signal.emit("No subtitle files found")
        else:
            self.src_ext = text

    def refresh_extension_comboboxes(self):
        """Refresh the extension comboboxes with current enabled extensions"""
        # Store current selections
        current_dst = self.dst_edit.currentText()
        current_src = self.src_edit.currentText()
        
        # Clear and repopulate destination combobox
        self.dst_edit.clear()
        self.dst_edit.addItems(['Auto'] + get_enabled_dst_ext())
        
        # Restore selection if it's still valid, otherwise use Auto
        if current_dst in ['Auto'] + get_enabled_dst_ext():
            self.dst_edit.setCurrentText(current_dst)
        else:
            self.dst_edit.setCurrentText('Auto')
        
        # Clear and repopulate source combobox
        self.src_edit.clear()
        self.src_edit.addItems(['Auto'] + get_enabled_src_ext())
        
        # Restore selection if it's still valid, otherwise use Auto
        if current_src in ['Auto'] + get_enabled_src_ext():
            self.src_edit.setCurrentText(current_src)
        else:
            self.src_edit.setCurrentText('Auto')

    def closeEvent(self, event):
        """Save window geometry and splitter sizes when closing"""
        self.save_window_geometry()
        self.save_splitter_sizes()
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
        new_zoom = min(current_zoom + 10, 200)
        if new_zoom != current_zoom:
            set_zoom_level(new_zoom)
            self.apply_zoom(new_zoom)
            self.log_signal.emit(f"<b>Zoom: {new_zoom}%</b>")

    def zoom_out(self):
        """Decrease zoom level by 10%"""
        current_zoom = get_zoom_level()
        new_zoom = max(current_zoom - 10, 50)
        if new_zoom != current_zoom:
            set_zoom_level(new_zoom)
            self.apply_zoom(new_zoom)
            self.log_signal.emit(f"<b>Zoom: {new_zoom}%</b>")

    def zoom_reset(self):
        """Reset zoom level to 100%"""
        current_zoom = get_zoom_level()
        if current_zoom != 100:
            set_zoom_level(100)
            self.apply_zoom(100)
            self.log_signal.emit("<b>Zoom: 100%</b>")

    def apply_zoom(self, zoom_level):
        multiplier = zoom_level / 100.0
        
        app = QApplication.instance()
        if app:
            font = app.font()
            base_size = 9
            new_size = max(1, round(base_size * multiplier))
            font.setPointSize(new_size)
            app.setFont(font)
            
            self.style().unpolish(self)
            self.style().polish(self)
            self.update_all_widgets_font()

    def update_all_widgets_font(self):
        """Update font for all widgets in the application"""
        app = QApplication.instance()
        if not app:
            return
            
        font = app.font()
        widgets_to_update = [
            self.menu_bar,
            self.log_switch_bar,
            self.drop_area,
            getattr(self.drop_area, 'label', None),
            self.video_drop_area,
            getattr(self.video_drop_area, 'label', None),
            self.log_box,
            self.log_view,
            self.target_btn,
            self.remove_videos_btn,
            self.open_folder_btn,
            self.delete_all_btn,
            self.delete_completed_btn,
            self.redo_btn,
            self.rename_all_btn,
            self.delete_subs_btn,
            self.dst_edit,
            self.src_edit,
            self.target_label,
            getattr(self, 'video_format_label', None),
            getattr(self, 'subtitle_format_label', None)
        ]
        
        for widget in widgets_to_update:
            if widget:
                widget.setFont(font)
                widget.update()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("appicon.png"))
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec()) 