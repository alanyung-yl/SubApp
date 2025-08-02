""" 
Now the popup window for prompting custom extension has two buttons: OK and CANCEL. 
The issues i have with it is that when i press "enter" or "OK" it will do the default tag if input box is empty. 
but when i press "CANCEL" or "clokse window(X)" it will still do the default tag. 
My intended behavior for pressing "CANCEL" or "close window(X)" is to close the window

"On completion" in tools that is for :
when job complete -  either do nothing or exit app
"""
try:
    from PyQt5.QtWidgets import (
        QApplication, QWidget, QVBoxLayout, QLabel, QPushButton, QFileDialog, QTextEdit, QFrame, QSizePolicy, QTableWidget, QTableWidgetItem, QLineEdit, QHBoxLayout, QComboBox, QInputDialog, QMenuBar, QMenu, QAction, QDialog, QCheckBox, QDialogButtonBox, QFormLayout
    )
    from PyQt5.QtCore import Qt, QMetaObject, pyqtSignal, Q_ARG
    from PyQt5.QtGui import QDragEnterEvent, QDropEvent, QColor
except ImportError:
    print("PyQt5 is required. Please install it with 'pip install PyQt5'.")
    import sys
    sys.exit(1)
import sys
import os
import threading
import SubRename as sr
import json
import subprocess
from PyQt5.QtCore import QTimer
from PyQt5.QtCore import pyqtSlot
import shutil
import logging
from PyQt5.QtGui import QIcon

SETTINGS_FILE = "settings.json"
SUBTITLE_FILE_FILTER = "Subtitle Files (*.ass *.srt *.ssa);;All Files (*)"
SUBTITLE_DIALOG_TITLE = "Select Subtitle Files"

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
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.hide()  # Hide table initially
        
        # Set maximum column widths and auto-resize
        self.table.setColumnWidth(0, 400)  # File name column
        self.table.setColumnWidth(1, 400)  # Path column  
        self.table.setColumnWidth(2, 120)  # Status column
        self.table.resizeColumnsToContents()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)  # Add spacing inside the border
        layout.setSpacing(0)
        layout.addWidget(self.label)
        layout.addWidget(self.table)
        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        # Apply theme after all widgets are created
        self.update_theme(self.current_theme)

    def update_theme(self, theme):
        """Update the drop area styling based on the current theme"""
        self.current_theme = theme
        self.setStyleSheet(f'''
            QFrame {{
                border: 2px solid {theme['border_color']};
                border-radius: 10px;
                background: {theme['drop_area_bg']};
            }}
        ''')
        
        self.table.setStyleSheet(f'''
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
        ''')

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            last_folder = get_last_subtitle_folder()
            files, _ = QFileDialog.getOpenFileNames(
                self, "Select Subtitle Files", last_folder, SUBTITLE_FILE_FILTER
            )
            if files:
                self.display_files(files)
                self.on_files_selected(files)
                # Save the folder of the first file
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
            # Save the folder of the first file
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
            # Add tooltips for full paths
            filename_item.setToolTip(file_path)
            path_item.setToolTip(file_path)
            status_item.setToolTip("Status: Pending")
            # Set initial background color for pending status
            status_item.setBackground(Qt.gray)
            self.table.setItem(row, 0, filename_item)
            self.table.setItem(row, 1, path_item)
            self.table.setItem(row, 2, status_item)
        # Auto-resize columns to fit content
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
        form_layout = QFormLayout(self)
        self.auto_run_checkbox = QCheckBox("Auto-run renaming upon selecting subtitle files")
        form_layout.addRow(self.auto_run_checkbox)
        self.always_prompt_multi_checkbox = QCheckBox("Always prompt for tag if multiple subtitle sets per episode")
        form_layout.addRow(self.always_prompt_multi_checkbox)
        self.always_prompt_tag_checkbox = QCheckBox("Always prompt for custom tag (even if no existing sub occupies the default name)")
        form_layout.addRow(self.always_prompt_tag_checkbox)
        self.cache_per_set_checkbox = QCheckBox("Cache studio tags per set (not per file)")
        form_layout.addRow(self.cache_per_set_checkbox)
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        form_layout.addWidget(self.button_box)
        self.setLayout(form_layout)
        # Load current setting
        settings = load_settings()
        self.auto_run_checkbox.setChecked(settings.get("auto_run_on_select", True))
        self.always_prompt_multi_checkbox.setChecked(settings.get("always_prompt_tag_multi_set", False))
        self.always_prompt_tag_checkbox.setChecked(settings.get("always_prompt_tag_always", False))
        self.cache_per_set_checkbox.setChecked(settings.get("cache_per_set", True))
    def get_auto_run(self):
        return self.auto_run_checkbox.isChecked()
    def get_always_prompt_multi(self):
        return self.always_prompt_multi_checkbox.isChecked()
    def get_always_prompt_tag(self):
        return self.always_prompt_tag_checkbox.isChecked()
    def get_cache_per_set(self):
        return self.cache_per_set_checkbox.isChecked()

class MainWindow(QWidget):
    log_signal = pyqtSignal(str)
    status_update_signal = pyqtSignal(dict)  # Signal for updating status
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Subtitle Renamer")
        self.setWindowIcon(QIcon("appicon.png"))
        self.resize(1440, 900)
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
        
        self.init_ui()
        self.log_signal.connect(self.log_box.append)
        self.status_update_signal.connect(self.update_status_from_signal)
        
        # Apply initial theme
        self.apply_theme()

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

        # Setup all menus
        self.setup_file_menu()
        self.setup_view_menu()
        self.setup_tools_menu()
        self.setup_settings_menu()
        self.setup_help_menu()

        self.target_btn = QPushButton("📁 Select Target Folder")
        self.target_btn.clicked.connect(self.select_target_folder)
        layout.addWidget(self.target_btn)

        self.target_label = QLabel("No target folder selected.")
        layout.addWidget(self.target_label)

        # Video files table
        self.video_table = QTableWidget(0, 2)
        self.video_table.setHorizontalHeaderLabels(["Video File Name", "Size (MB)"])
        self.video_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.video_table.setMaximumHeight(200)
        # Set maximum column widths and auto-resize
        self.video_table.setColumnWidth(0, 400)  # File name column max width
        self.video_table.setColumnWidth(1, 100)  # Size column max width
        self.video_table.resizeColumnsToContents()
        layout.addWidget(self.video_table)

        # Extension controls
        self.src_edit = QComboBox()
        self.src_edit.setEditable(True)
        self.src_edit.addItems(['.srt', '.ass', '.ssa'])
        self.src_edit.setCurrentText(self.src_ext)
        self.dst_edit = QComboBox()
        self.dst_edit.setEditable(True)
        self.dst_edit.addItems(['.mp4', '.mkv', '.avi', '.mov'])
        self.dst_edit.setCurrentText(self.dst_ext)
        layout.addWidget(QLabel("Video format (destination)"))
        layout.addWidget(self.dst_edit)
        layout.addWidget(QLabel("Subtitle format (source)"))
        layout.addWidget(self.src_edit)

        self.drop_area = DropArea(self.on_files_selected)
        layout.addWidget(self.drop_area)

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

        layout.addLayout(btn_row)
        # --- End Button Row ---
        
        # Apply compact mode if enabled
        if get_compact_mode():
            self.apply_compact_mode()

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box)

    def setup_settings_menu(self):
        # Add checkboxes to the settings menu
        self.auto_run_action = QAction("Auto-run renaming upon selecting subtitle files", self)
        self.auto_run_action.setCheckable(True)
        self.always_prompt_multi_action = QAction("Always prompt for tag if multiple subtitle sets per episode", self)
        self.always_prompt_multi_action.setCheckable(True)
        self.always_prompt_tag_action = QAction("Always prompt for custom tag (even if no existing sub occupies the default name)", self)
        self.always_prompt_tag_action.setCheckable(True)
        self.cache_per_set_action = QAction("Cache studio tags per set (not per file)", self)
        self.cache_per_set_action.setCheckable(True)

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
        # Theme submenu
        self.theme_menu = QMenu("Theme", self)
        self.light_theme_action = QAction("Light Theme", self)
        self.light_theme_action.setCheckable(True)
        self.light_theme_action.triggered.connect(lambda: self.change_theme(False))
        
        self.dark_theme_action = QAction("Dark Theme", self)
        self.dark_theme_action.setCheckable(True)
        self.dark_theme_action.triggered.connect(lambda: self.change_theme(True))
        
        self.theme_menu.addAction(self.light_theme_action)
        self.theme_menu.addAction(self.dark_theme_action)
        
        # Set initial theme state
        settings = load_settings()
        dark_mode = settings.get("dark_mode", False)
        self.light_theme_action.setChecked(not dark_mode)
        self.dark_theme_action.setChecked(dark_mode)
        
        # View options
        self.show_video_table_action = QAction("Show Video Table", self)
        self.show_video_table_action.setCheckable(True)
        self.show_video_table_action.setChecked(True)
        self.show_video_table_action.triggered.connect(self.toggle_video_table)
        
        self.show_log_action = QAction("Show Log", self)
        self.show_log_action.setCheckable(True)
        self.show_log_action.setChecked(True)
        self.show_log_action.triggered.connect(self.toggle_log)
        
        self.compact_mode_action = QAction("Compact Mode", self)
        self.compact_mode_action.setCheckable(True)
        self.compact_mode_action.setChecked(get_compact_mode())
        self.compact_mode_action.triggered.connect(self.toggle_compact_mode)
        
        self.view_menu.addMenu(self.theme_menu)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.show_video_table_action)
        self.view_menu.addAction(self.show_log_action)
        self.view_menu.addSeparator()
        self.view_menu.addAction(self.compact_mode_action)

    def setup_tools_menu(self):
        # Renaming tools
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
        
        # Analysis tools
        self.analyze_folder_action = QAction("Analyze Target Folder", self)
        self.analyze_folder_action.triggered.connect(self.analyze_target_folder)
        
        self.tools_menu.addAction(self.rename_all_action)
        self.tools_menu.addAction(self.retry_failed_action)
        self.tools_menu.addSeparator()
        self.tools_menu.addAction(self.clear_completed_action)
        self.tools_menu.addAction(self.clear_all_subs_action)
        self.tools_menu.addSeparator()
        self.tools_menu.addAction(self.analyze_folder_action)

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

    def on_settings_changed(self):
        settings = load_settings()
        settings["auto_run_on_select"] = self.auto_run_action.isChecked()
        settings["always_prompt_tag_multi_set"] = self.always_prompt_multi_action.isChecked()
        settings["always_prompt_tag_always"] = self.always_prompt_tag_action.isChecked()
        settings["cache_per_set"] = self.cache_per_set_action.isChecked()
        save_settings(settings)

    def apply_theme(self):
        """Apply the current theme to the entire application"""
        self.setStyleSheet(f'''
            QWidget {{
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
                width: 20px;
            }}
            QComboBox::down-arrow {{
                image: none;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 5px solid {self.current_theme['text_color']};
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
            QMenuBar {{
                background: {self.current_theme['widget_bg']};
                color: {self.current_theme['text_color']};
                border-bottom: 1px solid {self.current_theme['border_color']};
            }}
            QMenuBar::item {{
                background: transparent;
                padding: 6px 12px;
            }}
            QMenuBar::item:selected {{
                background: {self.current_theme['button_hover']};
            }}
            QMenu {{
                background: {self.current_theme['widget_bg']};
                color: {self.current_theme['text_color']};
                border: 1px solid {self.current_theme['border_color']};
            }}
            QMenu::item {{
                padding: 6px 20px;
            }}
            QMenu::item:selected {{
                background: {self.current_theme['button_hover']};
            }}
            QMenu::separator {{
                height: 1px;
                background: {self.current_theme['border_color']};
                margin: 4px 0px;
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

    def change_theme(self, dark_mode):
        """Change between light and dark themes"""
        self.current_theme = DARK_THEME if dark_mode else LIGHT_THEME
        
        # Update theme actions
        self.light_theme_action.setChecked(not dark_mode)
        self.dark_theme_action.setChecked(dark_mode)
        
        # Save theme preference
        settings = load_settings()
        settings["dark_mode"] = dark_mode
        save_settings(settings)
        
        # Apply the new theme
        self.apply_theme()

    def select_target_folder(self):
        last_folder = get_last_target_folder()
        folder = QFileDialog.getExistingDirectory(self, "Select Target Folder (Video/Output)", last_folder)
        if folder:
            self.target_folder = folder
            self.target_label.setText(f"Target Folder: {folder}")
            set_last_target_folder(folder)
            self.update_video_table(log_count=True)  # Log count when target folder is selected
            self.update_subtitle_count()  # Log subtitle count when target folder is selected
            # If files were already selected, check auto-run setting before running
            if self.selected_files:
                settings = load_settings()
                auto_run = settings.get("auto_run_on_select", True)
                if auto_run:
                    self.run_renamer()
                # else: wait for user to click "Rename All"

    def open_settings_dialog(self):
        dlg = SettingsDialog(self)
        if dlg.exec_():
            # Save the setting
            settings = load_settings()
            settings["auto_run_on_select"] = dlg.get_auto_run()
            settings["always_prompt_tag_multi_set"] = dlg.get_always_prompt_multi()
            settings["always_prompt_tag_always"] = dlg.get_always_prompt_tag()
            settings["cache_per_set"] = dlg.get_cache_per_set()
            save_settings(settings)

    def on_files_selected(self, files):
        self.selected_files = files
        # Initialize status tracking for new files
        for file_path in files:
            self.subtitle_status[file_path] = "pending"
        self.update_video_table(log_count=False)  # Don't log count when subtitle files are selected
        # Always log the subtitle count first
        self.log_signal.emit(f"Selected {len(files)} subtitle files.")
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
            except Exception as e:
                self.log_signal.emit(f"<b>Error:</b> {e}")

        threading.Thread(target=worker, daemon=True).start()

    def ask_user(self, prompt: str) -> str:
        answer_box = {}
        done = threading.Event()

        def _do_dialog():
            text, ok = QInputDialog.getText(self, "Subtitle Renamer", prompt)
            answer_box['val'] = text if ok else None
            done.set()

        QMetaObject.invokeMethod(self, "_invoke", Qt.QueuedConnection, Q_ARG(object, _do_dialog))
        done.wait()
        return answer_box.get('val', '')

    def log_async(self, html):
        self.log_signal.emit(html)

    def open_subtitle_folder(self):
        last_folder = get_last_subtitle_folder()
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Select Subtitle Files",
            last_folder,
            SUBTITLE_FILE_FILTER
        )
        if files:
            self.drop_area.display_files(files)
            self.on_files_selected(files)
            set_last_subtitle_folder(os.path.dirname(files[0]))

    # Can perhaps use sr.get_destination_filename()?
    def update_video_table(self, log_count=False):
        # Get current video extension
        ext = self.dst_edit.currentText().strip()
        if not ext or not self.target_folder:
            self.video_table.setRowCount(0)
            return
        files = [f for f in os.listdir(self.target_folder) if f.endswith(ext)]
        self.video_table.setRowCount(len(files))
        for i, fname in enumerate(sorted(files)):
            size_mb = os.path.getsize(os.path.join(self.target_folder, fname)) / (1024*1024)
            filename_item = QTableWidgetItem(fname)
            size_item = QTableWidgetItem(f"{size_mb:.2f}")
            # Add tooltip for full path
            full_path = os.path.join(self.target_folder, fname)
            filename_item.setToolTip(full_path)
            size_item.setToolTip(full_path)
            self.video_table.setItem(i, 0, filename_item)
            self.video_table.setItem(i, 1, size_item)
        # Auto-resize columns to fit content
        self.video_table.resizeColumnsToContents()
        # Log the number of video files found only when requested
        if log_count:
            self.log_signal.emit(f"Found {len(files)} video ({ext}) files.")

    def update_subtitle_count(self):
        """Log the count of subtitle files in the target folder"""
        if not self.target_folder:
            return
        ext = self.src_edit.currentText().strip()
        if not ext:
            return
        files = [f for f in os.listdir(self.target_folder) if f.endswith(ext)]
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
        self.subtitle_status.clear()  # Clear status tracking
        self.drop_area.clear_files()
        self.log_signal.emit("<b>All files deleted from table.</b>")

    def delete_completed_files(self):
        # Stub: Implement logic to track and remove completed files
        self.log_signal.emit("<b>Delete Completed: Not yet implemented.</b>")

    def redo_failed(self):
        """Retry failed subtitle files"""
        failed_files = [file_path for file_path, status in self.subtitle_status.items() if status == "failed"]
        cancelled_files = [file_path for file_path, status in self.subtitle_status.items() if status == "cancelled"]
        retry_files = failed_files + cancelled_files
        
        if not retry_files:
            self.log_signal.emit("<b>No failed or cancelled files to retry.</b>")
            return
        
        if not self.target_folder:
            self.log_signal.emit("<b>Please select the target folder first.</b>")
            return
        
        self.log_signal.emit(f"<b>Retrying {len(retry_files)} failed/cancelled files...</b>")
        
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
            except Exception as e:
                self.log_signal.emit(f"<b>Error during retry:</b> {e}")
        
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
        for fname in os.listdir(self.target_folder):
            if fname.lower().endswith(('.ass', '.srt', '.ssa')):
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
        """Toggle the visibility of the video table"""
        if self.video_table.isVisible():
            self.video_table.hide()
            self.show_video_table_action.setChecked(False)
        else:
            self.video_table.show()
            self.show_video_table_action.setChecked(True)

    def toggle_log(self):
        """Toggle the visibility of the log box"""
        if self.log_box.isVisible():
            self.log_box.hide()
            self.show_log_action.setChecked(False)
        else:
            self.log_box.show()
            self.show_log_action.setChecked(True)

    def toggle_compact_mode(self):
        """Toggle compact mode on/off"""
        compact_mode = self.compact_mode_action.isChecked()
        set_compact_mode(compact_mode)
        
        if compact_mode:
            self.apply_compact_mode()
        else:
            self.remove_compact_mode()

    def apply_compact_mode(self):
        """Apply compact mode - remove text from buttons and status text"""
        # Store original texts for restoration
        if not hasattr(self, 'original_button_texts'):
            self.original_button_texts = {}
            self.original_button_texts['target_btn'] = self.target_btn.text()
            self.original_button_texts['open_folder_btn'] = self.open_folder_btn.text()
            self.original_button_texts['delete_all_btn'] = self.delete_all_btn.text()
            self.original_button_texts['delete_completed_btn'] = self.delete_completed_btn.text()
            self.original_button_texts['redo_btn'] = self.redo_btn.text()
            self.original_button_texts['rename_all_btn'] = self.rename_all_btn.text()
            self.original_button_texts['delete_subs_btn'] = self.delete_subs_btn.text()
        
        # Remove text from buttons (keep only icons)
        self.target_btn.setText("📁")
        self.open_folder_btn.setText("📂")
        self.delete_all_btn.setText("🗑️")
        self.delete_completed_btn.setText("✅")
        self.redo_btn.setText("🔄")
        self.rename_all_btn.setText("🚀")
        self.delete_subs_btn.setText("🗑️")
        
        # Update table headers to remove status text
        self.drop_area.table.setHorizontalHeaderLabels(["File Name", "Path", ""])
        self.video_table.setHorizontalHeaderLabels(["Video File Name", "Size"])

    def remove_compact_mode(self):
        """Remove compact mode - restore original button texts"""
        if hasattr(self, 'original_button_texts'):
            self.target_btn.setText(self.original_button_texts['target_btn'])
            self.open_folder_btn.setText(self.original_button_texts['open_folder_btn'])
            self.delete_all_btn.setText(self.original_button_texts['delete_all_btn'])
            self.delete_completed_btn.setText(self.original_button_texts['delete_completed_btn'])
            self.redo_btn.setText(self.original_button_texts['redo_btn'])
            self.rename_all_btn.setText(self.original_button_texts['rename_all_btn'])
            self.delete_subs_btn.setText(self.original_button_texts['delete_subs_btn'])
        
        # Restore table headers
        self.drop_area.table.setHorizontalHeaderLabels(["File Name", "Path", "Status"])
        self.video_table.setHorizontalHeaderLabels(["Video File Name", "Size (MB)"])

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
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.about(self, "About Subtitle Renamer", 
                         "Subtitle Renamer v1.0\n\n"
                         "A tool for automatically renaming subtitle files to match video files.\n\n"
                         "Features:\n"
                         "• Drag and drop subtitle files\n"
                         "• Automatic video file detection\n"
                         "• Custom tag support\n"
                         "• Dark/Light theme support\n"
                         "• Batch processing\n\n"
                         "Created with PyQt5")

    def show_help(self):
        """Show the help dialog"""
        from PyQt5.QtWidgets import QMessageBox
        help_text = """
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

<b>Features:</b>
• Drag and drop subtitle files
• Automatic video file detection
• Custom tag support for multiple subtitle sets
• Dark/Light theme support
• Batch processing with status tracking
• Folder analysis tools

<b>File Formats:</b>
• Supported video formats: .mp4, .mkv, .avi, .mov
• Supported subtitle formats: .srt, .ass, .ssa

<b>Tips:</b>
• Use the Tools menu for quick access to common functions
• Check the View menu to customize the interface
• Use the Preference menu to configure automatic behavior
        """
        QMessageBox.information(self, "Help", help_text)

    @pyqtSlot(object)
    def _invoke(self, fn):
        fn()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("appicon.png"))
    
    window = MainWindow()
    window.show()
    sys.exit(app.exec_()) 