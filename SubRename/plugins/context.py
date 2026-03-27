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

"""Plugin application-context builder and thread-safe proxies."""

from __future__ import annotations


CONTEXT_VERSION = "2"


class PluginLoggerProxy:
    """Thread-safe logger proxy exposed as app_ctx['logger']."""

    def __init__(self, log_signal):
        self._signal = log_signal

    def log(self, message: str, category: str = "info") -> None:
        self._signal.emit(str(message), str(category))

    def info(self, message: str) -> None:
        self.log(message, "info")

    def warning(self, message: str) -> None:
        self.log(message, "warning")

    def error(self, message: str) -> None:
        self.log(message, "error")


def build_app_ctx(
    *,
    log_signal,
    status_update_signal,
    plugin_message_signal,
    plugin_theme_signal,
    shutdown_signal,
    settings_loader,
    assets_path: str,
    current_theme_getter,
    dark_theme,
    light_theme,
    stylesheet_generator,
    zoom_level_getter,
) -> dict:
    """Build base context. Per-plugin keys are injected by PluginManager."""
    logger = PluginLoggerProxy(log_signal)

    return {
        "context_version": CONTEXT_VERSION,

        # Thread-safe logging
        "logger": logger,
        "log_signal": log_signal,
        "status_update_signal": status_update_signal,
        "shutdown_signal": shutdown_signal,

        # Thread-safe UI bridge
        "show_message": lambda message, title="Plugin Message", msg_type="info":
            plugin_message_signal.emit(str(message), str(title), str(msg_type)),
        "apply_theme_to_widget": lambda widget: plugin_theme_signal.emit(widget),

        # Pure helpers
        "settings": settings_loader,
        "get_assets_path": lambda: assets_path,
        "get_current_theme": current_theme_getter,
        "get_dark_theme": lambda: dark_theme,
        "get_light_theme": lambda: light_theme,
        "generate_stylesheet": stylesheet_generator,
        "get_zoom_level": zoom_level_getter,

        # Per-plugin keys added later in manager:
        # "plugin_runtime_key"
        # "plugin_data_dir_self"
    }
