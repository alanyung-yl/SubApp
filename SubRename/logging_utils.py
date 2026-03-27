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

"""
Centralized logging utilities for the SubRename application.
This module provides filtered logging functionality that respects user settings.
"""

import logging
import json
import os
import app_paths as ap
from pathlib import Path


def _env_log_level() -> int:
    raw = os.environ.get("SUBRENAME_LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, raw, logging.INFO)


class FilteredFileHandler(logging.FileHandler):
    """Custom file handler that respects user settings for message filtering"""
    
    def emit(self, record):
        """Only emit log records if the corresponding user setting allows it"""
        # Load settings to check what message types are enabled
        settings = load_user_settings()
        
        # Check for success messages first (they have a custom category attribute)
        if hasattr(record, 'category') and record.category == 'success':
            if not settings.get('show_success_messages', True):
                return
        else:
            # Map logging levels to user setting keys for regular log levels
            level_to_setting = {
                logging.DEBUG: 'show_debug_messages',
                logging.INFO: 'show_info_messages',
                logging.WARNING: 'show_warning_messages', 
                logging.ERROR: 'show_error_messages',
                logging.CRITICAL: 'show_error_messages',
            }
            
            # Check if this log level should be written to file
            setting_key = level_to_setting.get(record.levelno)
            if setting_key:
                default = True if setting_key != "show_debug_messages" else False
                if not settings.get(setting_key, default):
                    return
        super().emit(record)

def load_user_settings():
    """Load user settings from the settings file"""
    try:
        settings_path = ap.settings_file()
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    
    # Return default settings if file doesn't exist or can't be read
    return {
        'show_info_messages': True,
        'show_success_messages': True,
        'show_warning_messages': True,
        'show_error_messages': True,
        'show_debug_messages': False,
    }

def log_success(message):
    """Log a success message with proper category for filtering"""
    logger = logging.getLogger()
    # Create a custom log record with category attribute
    record = logging.LogRecord(
        name=logger.name,
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg=message,
        args=(),
        exc_info=None
    )
    record.category = 'success'
    logger.callHandlers(record)

def setup_logging(log_file):
    """Set up logging with custom filtered handler"""
    # Only set up if no handlers exist
    if not logging.getLogger().hasHandlers():
        level = _env_log_level()
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
        file_handler = FilteredFileHandler(str(log_path))
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        
        # Configure the root logger
        logger = logging.getLogger()
        logger.setLevel(level)
        logger.addHandler(file_handler)

        if ap._env_flag("SUBRENAME_LOG_CONSOLE", False):
            console = logging.StreamHandler()
            console.setFormatter(formatter)
            console.setLevel(level)
            logger.addHandler(console)
