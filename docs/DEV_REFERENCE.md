# SubRename — Developer Reference

This file documents everything relevant to running from source, using the headless Python API, writing plugins, and configuring the runtime environment. End users running the packaged executable generally do not need anything here.

---

## Table of Contents

- [Running from Source](#running-from-source)
- [Settings Reference](#settings-reference)
- [Python API](#python-api)
- [Plugin Development](#plugin-development)
- [Source Tree](#source-tree)
- [Environment Variables](#environment-variables)
- [Runtime Path Resolution](#runtime-path-resolution)
- [Addon Path Resolution Order](#addon-path-resolution-order)
- [Dev Workflow Examples](#dev-workflow-examples)

---

## Running from Source

### Dependencies

| Package | Purpose |
|---------|---------|
| `PyQt6 >= 6.9.0` | GUI framework |
| `guessit >= 3.7.0` | Media filename parsing (optional but strongly recommended) |
| `rapidfuzz >= 3.0.0` | Fuzzy title matching in movie mode (optional) |
| `send2trash >= 1.8.0` | Move originals to system trash instead of deleting |
| `pathvalidate` | Sanitise generated filenames |

### Setup

```bash
# Standard
pip install -r requirements.txt
python SubRename/SubRenameUI.py

# Virtual environment (recommended)
python -m venv .venv
.venv\Scripts\activate      # Windows
source .venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
python SubRename/SubRenameUI.py
```

### Platform notes

- The core renaming logic (`SubRename.py`) is platform-independent.
- Title-bar theming (dark/light) is Windows-only.
- DPI scaling follows the OS.

---

## Settings Reference

Settings are saved in the user config directory (not beside the executable):

- Windows: `%LOCALAPPDATA%\EZTools\SubApp\config\settings.json`
- macOS: `~/Library/Application Support/EZTools/SubApp/config/settings.json`
- Linux: `~/.config/EZTools/SubApp/config/settings.json`

A `settings_version` key tracks schema migrations.

| Key | Default | Description |
|-----|---------|-------------|
| `dark_mode` | `true` | Use dark theme |
| `zoom_level` | `100` | UI scale percentage (50–300) |
| `compact_mode` | `false` | Shorter button labels |
| `auto_run` | `false` | Rename automatically when subtitle files are loaded |
| `use_default_tag_if_found` | `true` | Pre-fill suffix prompt with detected group name |
| `always_prompt_tag_always` | `false` | Always show suffix prompt, even without a conflict |
| `cache_per_set` | `true` | Re-use the chosen suffix for all files in the same group |
| `preview_mode` | `true` | Dry-run mode |
| `show_preview_name_column` | `false` | Show New Name column |
| `show_preview_status_column` | `false` | Show Preview column |
| `conflict_policy` | `"ASK"` | `ASK` / `SKIP` / `OVERWRITE` / `SUFFIX` |
| `apply_all_conflicts` | `false` | Pre-check "Apply to All" in conflict dialogs |
| `group_suffix_enabled` | `true` | Append detected group as a filename suffix |
| `lang_suffix_enabled` | `false` | Append detected language code as a filename suffix |
| `unknown_lang_action` | `"append"` | `"append"` (use alpha2 code) or `"skip"` (omit unknown language) |
| `completion_behavior` | `"do_nothing"` | What to do after a run: `"do_nothing"` or `"exit"` |
| `show_video_table` | `true` | Show video panel |
| `show_log` | `true` | Show log panel |
| `show_switch_bar` | `true` | Show Main/Log tab bar |
| `show_info_messages` | `true` | Show info log entries |
| `show_success_messages` | `true` | Show success log entries |
| `show_warning_messages` | `true` | Show warning log entries |
| `show_error_messages` | `true` | Show error log entries |
| `delete_empty_folders` | `false` | Delete the subtitle source folder after rename if empty |
| `last_target_folder` | `""` | Most recently used video folder |
| `last_subtitle_folder` | `""` | Most recently used subtitle folder |
| `recent_target_folders` | `[]` | List of recent video folders |
| `enabled_video_extensions` | built-in subset | Extensions shown in the destination dropdown |
| `enabled_subtitle_extensions` | built-in subset | Extensions shown in the source dropdown |
| `custom_video_extensions` | `[]` | User-added video extensions |
| `custom_subtitle_extensions` | `[]` | User-added subtitle extensions |
| `disabled_builtin_video_extensions` | `[]` | Built-in video extensions to hide |
| `disabled_builtin_subtitle_extensions` | `[]` | Built-in subtitle extensions to hide |
| `splitter_sizes` | auto | Saved panel heights |
| `settings_version` | `1` | Schema version for migration |

---

## Python API

The renaming logic lives entirely in `SubRename.py` and can be used without the GUI.

### `RenameConfig`

```python
from SubRename import RenameConfig, ConflictPolicy

config = RenameConfig(
    directory="/path/to/videos",
    src_ext=".ass",            # subtitle extension (or list)
    dst_ext=".mkv",            # video extension (or list)
    cust_ext="GroupName",      # default group suffix
    subtitle_files=[...],      # explicit list, or None to scan directory
    video_files=[...],         # explicit list, or None to scan directory
    preview_mode=True,         # dry run
    conflict_policy=ConflictPolicy.ASK,
    group_suffix_enabled=True,
    lang_suffix_enabled=False,
    unknown_lang_action="append",  # "append" or "skip"
)
```

### `run_job`

```python
from SubRename import run_job, RenameConfig

result = run_job(config)

# result keys:
# "OK"            – list of successfully renamed paths
# "FAIL"          – list of failed paths
# "SKIPPED"       – list of skipped paths
# "PREVIEW"       – list of preview dicts (preview_mode only)
# "RENAMED_PATHS" – {old_path: new_path} map for in-place renames
```

`run_job` also accepts raw keyword arguments for quick scripting:

```python
result = run_job(directory="/path/to/videos", src_ext=".srt", preview_mode=True)
```

---

## Plugin Development

### File structure

Three layouts are supported:

| Layout | Entry point |
|--------|-------------|
| Single file | `addons/myplugin.py` |
| Folder (preferred) | `addons/myplugin/plugin.py` |
| Package | `addons/myplugin/__init__.py` |

Python entry-point discovery is not used. Plugins are loaded in filesystem order. Sibling imports within a folder plugin work via a temporary `sys.path` entry.

### Plugin contract

A plugin module must expose a `plugin` object (or a `get_plugin()` callable) with the following interface, matching the `UIPlugin` protocol in `plugins/api.py`:

```python
class MyPlugin:
    id      = "my_plugin"        # stable, unique identifier
    name    = "My Plugin"        # display name shown in the tab bar
    version = "1.0.0"

    def create_pages(self, app_ctx: dict):
        """Return an iterable of (tab_label, QWidget) tuples."""
        yield "My Tab", MyWidget(app_ctx)

plugin = MyPlugin()
```

`create_pages()` is called on the UI thread at startup and must return quickly — defer any heavy initialisation to the widget itself. No hot reload or unload is supported; changes require an app restart.

### `app_ctx` reference

The context dictionary passed to `create_pages()` provides the following keys:

| Key | Type / value | Description |
|-----|-------------|-------------|
| `context_version` | `str` | Schema version; check this if your plugin needs a minimum API |
| `logger` | `PluginLoggerProxy` | Thread-safe logger with `.info()`, `.warning()`, `.error()`, `.log(msg, category)` |
| `log_signal` | Qt signal | Raw signal for emitting log entries directly |
| `status_update_signal` | Qt signal | Emit short status bar text |
| `show_message` | `callable(message, title, msg_type)` | Thread-safe message dialog |
| `apply_theme_to_widget` | `callable(widget)` | Apply the current stylesheet to a widget |
| `settings` | `callable()` | Returns the current settings dict |
| `get_assets_path` | `callable()` | Path to the bundled `assets/` directory |
| `get_current_theme` | `callable()` | `"dark"` or `"light"` |
| `get_dark_theme` | `callable()` | Dark theme stylesheet string |
| `get_light_theme` | `callable()` | Light theme stylesheet string |
| `generate_stylesheet` | `callable()` | Regenerate stylesheet for current zoom/theme |
| `get_zoom_level` | `callable()` | Current UI zoom percentage |
| `plugin_runtime_key` | `str` | Stable unique key for this plugin instance |
| `plugin_data_dir_self` | `callable()` | Returns a writable per-plugin data directory (created on first call) |

---

## Source Tree

```
SubApp/
├── SubRename/
│   ├── SubRename.py            # core renaming logic (GUI-independent)
│   ├── SubRenameUI.py          # PyQt6 application entry point
│   ├── app_paths.py            # platform path resolution + env-var overrides
│   ├── logging_utils.py        # filtered file logging
│   ├── plugins/
│   │   ├── api.py              # UIPlugin protocol definition
│   │   ├── context.py          # app_ctx builder and logger proxy
│   │   └── manager.py          # plugin discovery and instantiation
│   ├── addons/                 # bundled / sample plugins
│   ├── assets/
│   └── config/
│       └── langmap.txt         # bundled default (copied to user config on first run)
├── tests/
├── requirements.txt
└── SubRename.spec
```

---

## Environment Variables

All variable names are prefixed with `SUBRENAME_`.

### Path overrides

| Variable | Description |
|----------|-------------|
| `SUBRENAME_BASE_DIR` | Relocates **all** runtime data roots to a single directory. Sub-paths become `<base>/config`, `<base>/log`, `<base>/addons`, `<base>/plugin_data`. |
| `SUBRENAME_ADDONS_DIR` | Explicit addons directory override. Takes priority over `SUBRENAME_BASE_DIR/addons` and the platform default. |

Relative values for both variables are resolved against the **runtime anchor** (see [Runtime Path Resolution](#runtime-path-resolution)), not the current working directory.

### Addon loading

| Variable | Default | Description |
|----------|---------|-------------|
| `SUBRENAME_DISABLE_ADDONS` | `false` | Set to `1` / `true` / `yes` / `on` to disable plugin discovery entirely. |
| `SUBRENAME_DISABLE_DEFAULT_ADDONS` | `false` | Disables loading from the platform-default addons path. Has no effect if `SUBRENAME_ADDONS_DIR` is also set. |

### Qt / UI

| Variable | Default | Description |
|----------|---------|-------------|
| `SUBRENAME_PROFILE` | `SubApp` | Qt settings profile name used for window geometry and state persistence. Override to isolate dev and prod environments. |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `SUBRENAME_LOG_LEVEL` | `INFO` | Logging threshold. Accepted values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`. |
| `SUBRENAME_LOG_CONSOLE` | `false` | Set to `1` / `true` / `yes` / `on` to mirror log output to the terminal in addition to `rename_log.txt`. |

---

## Runtime Path Resolution

### Runtime anchor

Relative paths supplied via environment variables are resolved against a deterministic **runtime anchor**, not the current working directory:

| Run mode | Anchor |
|----------|--------|
| Source / dev (`python SubRenameUI.py`) | `<repo>/SubRename/` (directory containing `app_paths.py`) |
| Frozen executable (`SubApp.exe`) | Directory containing the executable |

### Platform default paths

When no overrides are set, runtime data is stored under:

| Directory | Windows / macOS | Linux |
|-----------|----------------|-------|
| Config | `<config-base>/EZTools/SubApp/config/` | `~/.config/EZTools/SubApp/config/` |
| Addons | `<config-base>/EZTools/SubApp/addons/` | `~/.config/EZTools/SubApp/addons/` |
| Logs | `<config-base>/EZTools/SubApp/log/` | `~/.local/state/EZTools/SubApp/log/` |
| Plugin data | `<config-base>/EZTools/SubApp/plugin_data/` | `~/.local/state/EZTools/SubApp/plugin_data/` |

Where `config-base` is:

- Windows: `%LOCALAPPDATA%`
- macOS: `~/Library/Application Support`

### Effect of `SUBRENAME_BASE_DIR`

Setting `SUBRENAME_BASE_DIR=dev_data` in source mode resolves to `<repo>/SubRename/dev_data/`, and the four runtime directories are created under it:

```
<repo>/SubRename/dev_data/
├── config/
├── log/
├── addons/
└── plugin_data/
```

This keeps dev data fully separate from a production install.

---

## Addon Path Resolution Order

The effective addons directory is resolved in this priority order:

1. `SUBRENAME_ADDONS_DIR` (highest priority)
2. `SUBRENAME_BASE_DIR/addons`
3. Platform default (`<config-base>/EZTools/SubApp/addons`)

---

## Dev Workflow Examples

### Enable debug logging with console output

**PowerShell:**
```powershell
$env:SUBRENAME_LOG_LEVEL = "DEBUG"
$env:SUBRENAME_LOG_CONSOLE = "1"
py .\SubRename\SubRenameUI.py
```

**Bash:**
```bash
SUBRENAME_LOG_LEVEL=DEBUG SUBRENAME_LOG_CONSOLE=1 python SubRename/SubRenameUI.py
```

### Isolate dev data from production

**PowerShell:**
```powershell
$env:SUBRENAME_BASE_DIR = "dev_data"
$env:SUBRENAME_PROFILE  = "SubApp_dev"
py .\SubRename\SubRenameUI.py
```

This stores all runtime files under `<repo>/SubRename/dev_data/` and uses a separate Qt geometry profile so dev and production window state don't collide.

### Test with a custom addons directory

**PowerShell:**
```powershell
$env:SUBRENAME_ADDONS_DIR = "test_addons"
py .\SubRename\SubRenameUI.py
```

Resolves to `<repo>/SubRename/test_addons/`. Useful for testing plugins without touching your real addons folder.

### Disable all addon loading

```powershell
$env:SUBRENAME_DISABLE_ADDONS = "1"
py .\SubRename\SubRenameUI.py
```
