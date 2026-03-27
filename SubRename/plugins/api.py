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

from typing import Protocol, Iterable, Tuple
from PyQt6.QtWidgets import QWidget

class UIPlugin(Protocol):
    """A plugin can contribute 0..N pages to your stacked UI."""
    id: str                 # stable id, e.g. "driver"
    name: str               # display name, e.g. "Driver"
    version: str            # "1.0.0"

    def create_pages(self, app_ctx: dict) -> Iterable[Tuple[str, QWidget]]:
        """
        Return iterable of (page_name, widget) tuples to add to the stack.
        app_ctx can hold shared services: logger, settings, signals, etc.
        """
        ...
