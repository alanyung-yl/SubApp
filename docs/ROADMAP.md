# SubRename — Roadmap

This file tracks planned features and improvements. Items are grouped by theme and marked with their current status.

**Status key:** `[ ]` Planned · `[~]` In progress · `[x]` Done

---

## Platform Support

| Platform | Packaged Executable |
|----------|-------------------|
| Windows 10 / 11 | `[x]` Available |
| macOS | `[ ]` Planned |
| Linux | `[ ]` Planned |

---

## Core Rename Engine

- `[ ]` Improve subtitle-to-video matching with MediaInfo or FFprobe for richer media metadata
- `[ ]` Fine-tune fuzzy title matching (rapidfuzz) for edge cases in movie mode
- `[ ]` Investigate OpenSubtitles hash support for hash-based subtitle matching

---

## UI & Workflow

- `[ ]` Add undo/redo support for completed operations
- `[ ]` Add a frozen/pinned table column to improve navigation on wide tables
- `[ ]` Migrate UI and session state to QSettings

---

## Plugin System

*(No items currently planned — open to contributions)*