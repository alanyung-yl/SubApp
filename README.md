# SubRename — Subtitle Renamer

A desktop application that renames subtitle files to match their corresponding video files. Handles both TV series and movies, supports multiple subtitle groups, resolves file conflicts, and previews every change before it is committed.

---

## Table of Contents

- [How It Works](#how-it-works)
- [Download](#download)
- [Quick Start](#quick-start)
- [Basic Workflow](#basic-workflow)
- [Series vs Movie Mode](#series-vs-movie-mode)
- [Group Suffix and Multiple Subtitle Groups](#group-suffix-and-multiple-subtitle-groups)
- [Language Suffix Detection](#language-suffix-detection)
- [Conflict Resolution](#conflict-resolution)
- [Preview Mode](#preview-mode)
- [Retry Failed Files](#retry-failed-files)
- [Replace Original Files](#replace-original-files)
- [File Format Support](#file-format-support)
- [Menus and Keyboard Shortcuts](#menus-and-keyboard-shortcuts)
- [Settings Reference](https://github.com/alanyung-yl/SubApp/blob/main/docs/DEV_REFERENCE.md#settings-reference)
- [Language Map (langmap.txt)](#language-map-langmaptxt)
- [Plugin System](#plugin-system)
- [Data File Locations](#data-file-locations)
- [FAQ](#faq)
- [Roadmap](https://github.com/alanyung-yl/SubApp/blob/main/docs/ROADMAP.md)
- [License](#license)

---

## How It Works

SubRename pairs each subtitle file with a video file by episode number (series mode) or by title similarity (movie mode), then copies the subtitle alongside the video under the video's base name.

```
Input
  Video:    Show.S01E03.1080p.mkv
  Subtitle: [GroupA] Show - 03.ass

Output
  Show.S01E03.1080p.ass          ← subtitle renamed to match video
```

If a renamed subtitle already exists at the destination, the app triggers conflict resolution rather than silently overwriting it.

---

## Download

| Platform | Status | Download |
|----------|--------|----------|
| Windows 10 / 11 | ✅ Available | [SubApp.exe](https://github.com/alanyung-yl/SubApp/releases/latest) |
| macOS | 🔜 Coming soon | — |
| Linux | 🔜 Coming soon | — |

> macOS and Linux builds are planned. See [ROADMAP.md](https://github.com/alanyung-yl/SubApp/blob/main/docs/ROADMAP.md) for details.

---

## Quick Start

### Packaged executable (recommended)

1. Download `SubApp.exe` from the [Download](#download) section above and run it — no installation required.
2. Click **Select Folder** (or use **File → Open Folder...**) to point the app at your video directory.
3. Add subtitle files using **Browse Files**, **File → Open Subtitle Files...**, drag-and-drop, or by clicking the subtitle table.
4. Start renaming with **Start Renaming**, **F5**, or **Tools → Rename All Files**.

### From source

```bash
pip install -r requirements.txt
python SubRename/SubRenameUI.py
```

> Running from source? See [DEV_REFERENCE.md](https://github.com/alanyung-yl/SubApp/blob/main/docs/DEV_REFERENCE.md).

---

## Basic Workflow

### 1. Select a target folder

The target folder is where your video files live and where renamed subtitles will be placed. The app scans the folder for video files and displays them in the top table, along with an episode count.

Use any of the following:
- **File → Open Folder...** (`Ctrl+O`)
- **Select Folder** button in the video controls row
- Clicking the video table area to open the folder picker
- Drag-and-drop a folder onto the video table area

Recent folders are available under **File → Open Recent**.

### 2. Choose formats

Both dropdowns support **Auto** and **All**, and control which extensions are matched:

| Dropdown | Controls |
|----------|----------|
| Source (left) | Subtitle extension to process (`.ass`, `.srt`, …, **Auto**, or **All**) |
| Destination (right) | Video extension to match against (`.mkv`, `.mp4`, …, **Auto**, or **All**) |

- **Auto**: picks the most common extension from currently relevant files.
- **All**: includes every enabled extension from your settings.

### 3. Load subtitle files

You can load subtitles with:
- **File → Open Subtitle Files...** (`Ctrl+Shift+O`)
- **Browse Files** button in the subtitle button row
- Drag-and-drop files onto the subtitle table
- Clicking the subtitle table to open a file picker

Files already present in the table are deduplicated automatically. The table shows five columns:

| Column | Contents |
|--------|----------|
| File Name | Original filename |
| New Name | Proposed filename (filled by Preview or after renaming) |
| Path | Full path to the source file |
| Preview | Pre-run status badge |
| Status | Post-run result badge |

**New Name** is editable. If you type a custom value there, that name overrides auto-generated naming for that file during the actual rename run. Custom names still go through the same conflict handling rules.

### 4. Run

Run using any of:
- **Start Renaming** button in the subtitle button row
- **F5**
- **Tools → Rename All Files**

The log box reports each file's outcome. If any files fail, **F6** retries only the failed ones.

---

## Series vs Movie Mode

The app automatically detects which mode to use by examining the video files in the target folder.

### Series mode

Mode detection works like this:
- `guessit` signals are checked first.
- Regex-based episode detection is used as fallback.
- Any episode signal forces **series mode**.
- Ambiguous cases default to **series mode**.

In series mode, episode numbers are extracted from both video and subtitle filenames and paired by episode.

Supported episode number formats include:
- `S01E03`, `s1e3`
- `E03`, `e3`
- `Episode 3`, `Episode03`
- Bare numbers: `[03]`, ` 03 `, `_03_`

### Movie mode

Movie mode is used only when no episode signals are detected. The subtitle filename is compared against all video filenames using title-similarity scoring. Metadata tokens — resolution, codec, audio track, streaming service tags, HDR flags, etc. — are stripped before comparison so that `The.Movie.2023.2160p.HDR.TrueHD.mkv` and `The Movie 2023.srt` score as a high match.

When `guessit` is available, both filenames are parsed for title and year before the similarity score is computed.

---

## Group Suffix and Multiple Subtitle Groups

When two subtitle files would both rename to the same destination, the second one needs a differentiating suffix appended.

### Automatic group detection

The app reads the group name from:
1. A leading bracket group — `[GroupName] Show - 01.ass`
2. guessit's `release_group` field
3. A trailing scene-style group after a dash — `Show.01-GroupName.ass`

The extracted name is used as the default suffix proposal in the prompt dialog.

### Prompting behaviour

| Setting | Effect |
|---------|--------|
| **Auto-Apply Detected Group Suffix** | Uses the extracted group name automatically as the suffix token |
| **Always Ask for Group Suffix** | Opens the suffix dialog for every subtitle, even if no conflict exists |
| **Use This for All Files of the Same Group** | Re-uses the chosen suffix token for all remaining files in the current run without asking again |

When a suffix is chosen, the renamed file becomes `Video.S01E03.GroupName.ass`.

---

## Language Suffix Detection

When **Language Suffix** is enabled (Settings → Language tab), a language code is appended after the group suffix:

```
Video.S01E03.cht.ass          ← traditional Chinese subtitle, no group-suffix conflict
Video.S01E03.GroupA.cht.ass   ← group suffix + language
```

Detection pipeline:
1. `guessit` parses `subtitle_language` and `language` fields from the filename.
2. Each language object is resolved against the **language map** (user `langmap.txt`; see [Language Map](#language-map-langmaptxt)) to produce a canonical code.
3. Unknown languages fall through to either **append** (use `alpha2` code as-is) or **skip** (omit the code), depending on the **Unknown Language Action** setting.
4. If `guessit` is unavailable, the filename tokens are scanned directly against the language map.

Multiple detected languages are joined with a hyphen: `cht-jpn`.

### Group suffix

When **Group Suffix** is enabled, the detected group name is appended as a suffix using the same detection logic described above. Group suffix and language suffix can be active simultaneously.

---

## Conflict Resolution

A conflict occurs when the computed destination path already exists on disk or would be used by another file in the same batch.

The conflict policy is set in **Preferences → Conflicts**:

| Policy | Behaviour |
|--------|-----------|
| **Ask** | Opens a dialog for each conflict |
| **Skip** | Skips the file entirely |
| **Overwrite** | Replaces the existing file |
| **Keep both** | Appends an auto-incrementing numeric suffix |

### Ask dialog options

- **Overwrite** — replace the existing file
- **Keep both** — append `(1)`, `(2)`, … to the filename
- **Enter custom suffix** — type a custom suffix to distinguish the file
- **Use this for all conflicts of the same group** checkbox — applies the chosen action to every remaining conflict in the run

The **Use This for All Conflicts of the Same Group** menu toggle pre-enables the checkbox in every conflict dialog.

---

## Preview Mode

Enable with **Preferences → Enable Preview Mode**.

In preview mode, no files are written. The **Preview** column in the subtitle table fills with status, and the **New Name** column shows the proposed filename. Conflict dialogs appear during the preview so you can resolve them before committing.

Once you are satisfied, press **Start Renaming**, **F5**, or **Tools → Rename All Files** to execute with the cached conflict decisions.

Status:

| Badge | Meaning |
|-------|---------|
| ✅ Ready | Will rename without issue |
| 📝 Overwrite | Will overwrite an existing file |
| 📚 Keep both | Will add a numeric suffix |
| 🏷️ Custom suffix | Will use a custom suffix |
| 🚫 Skip | Will be skipped |
| ⚠️ Exists | Destination already exists |
| ❌ Error | Could not be resolved |
| ⏳ Pending | Not yet processed |

---

## Retry Failed Files

After a run, files that failed (permissions error, locked file, unmapped episode, etc.) stay in the table with a failure status. Press **Retry Failed**, **F6** or **Tools → Retry Failed Files** to attempt those files again without re-processing the successful ones.

---

## Replace Original Files

`Replace Original File(s)` is available in the **Orphaned Subtitle Files** dialog that appears after you choose/open a destination folder, when there are subtitle files in that folder that do not match any video filename base.

If you enable this option, only the checked orphaned files from that dialog are marked for replace-original handling. During the actual rename run (not preview), it copies each file to its new destination name, then moves the original source file to the system trash.

This is not a global move mode for every subtitle in the table. It applies only to files explicitly marked through that orphaned-files flow. The behavior is available in both series and movie runs.

---

## File Format Support

### Default video extensions
`.mp4` `.mkv` `.avi` `.mov` `.wmv` `.flv` `.f4v` `.webm` `.m4v` `.3gp` `.ogv` `.ts` `.mts` `.m2ts` `.vob` `.asf` `.rm` `.rmvb` `.divx` `.xvid` `.mpg` `.mpeg` `.m2v` `.3g2`

### Default subtitle extensions
`.srt` `.ass` `.ssa` `.sub` `.idx` `.vtt` `.smi` `.sami` `.mpl` `.txt` `.rt` `.pjs` `.psb` `.dks` `.jss` `.aqt` `.gsub` `.mpsub` `.sbv` `.ttml` `.dfxp` `.xml` `.ttxt`

Both lists are fully customisable in **Settings → Extensions**. Individual built-in extensions can be disabled, and custom extensions can be added. Enabled subsets control what the source and destination dropdowns offer.

---

## Menus and Keyboard Shortcuts

### File
| Action | Shortcut |
|--------|----------|
| Open Folder… | `Ctrl+O` |
| Open Folder in Explorer | — |
| Open Recent | — |
| Open Subtitle Files… | `Ctrl+Shift+O` |
| Remove All Files from Table | `Ctrl+Shift+Del` |
| Open User Data Folder | — |
| Exit | `Ctrl+Q` |

### Tools
| Action | Shortcut |
|--------|----------|
| Rename All Files | `F5` |
| Retry Failed Files | `F6` |
| Clear Completed Files | `F7` |
| Clear All Subtitle Files | `F8` |
| Analyze Folder | `F11` |
| On Complete → Do Nothing / Exit | — |

### Preferences
| Action | Notes |
|--------|-------|
| Enable Preview Mode | Toggle dry-run mode |
| Auto-Run Renaming | Start rename automatically when subtitle files are loaded |
| Auto-Apply Detected Group Suffix | Use extracted group name automatically as suffix token |
| Always Ask for Group Suffix | Force suffix dialog even without conflict |
| Use This for All Files of the Same Group | Re-use chosen suffix token across entire group |
| Conflicts submenu | Set global conflict policy |
| Settings… | Open full settings dialog |

### View
| Action | Shortcut / Notes |
|--------|-----------------|
| Theme → Light / Dark | — |
| Zoom In / Out / Reset | `Ctrl+=` / `Ctrl+-` / `Ctrl+0` |
| Show Preview Name | Toggle New Name column |
| Show Preview Status | Toggle Preview column |
| Show Video Table | Toggle video panel |
| Show Log Box | Toggle log panel |
| Show Switch Bar | Toggle the Main/Log tab bar |
| Log → Info / Success / Warning / Error Messages | Per-category log filtering |
| Log → Open Log Window | Open rename_log.txt in a popup |
| Compact Mode | Reduce button label text |

### Help
| Action | Shortcut |
|--------|----------|
| Help | `F1` |
| About | — |

### Subtitle table
| Action | Shortcut |
|--------|----------|
| Remove Selected Files from List | `Delete` |

---

## Settings Reference

See [`docs/DEV_REFERENCE.md`](https://github.com/alanyung-yl/SubApp/blob/main/docs/DEV_REFERENCE.md) for the full settings key reference:
[`Settings Reference`](https://github.com/alanyung-yl/SubApp/blob/main/docs/DEV_REFERENCE.md#settings-reference).

---

## Language Map (langmap.txt)

`langmap.txt` is stored in the same user config root as settings:

- Windows: `%LOCALAPPDATA%\EZTools\SubApp\config\langmap.txt`
- macOS: `~/Library/Application Support/EZTools/SubApp/config/langmap.txt`
- Linux: `~/.config/EZTools/SubApp/config/langmap.txt`

If missing, a default file is created automatically on first launch.

The file defines how raw language tokens are resolved to canonical codes.

### Format

```
# comment lines start with #
cht = zh-hant, chinese traditional, chi-t, traditional chinese
chs = zh-hans, chinese simplified, chi-s, simplified chinese
en  = english, eng
ja  = japanese, jpn
```

- Left of `=` is the output code that appears in the renamed filename.
- Right of `=` is a comma-separated list of aliases that map to that code.
- Lookup is case-insensitive.
- Duplicate aliases emit a warning in the log.

You can edit this file directly or use **Settings → Language → Edit Language Map** in the settings dialog. The in-app editor validates the file on save.

---

## Plugin System

Plugins add extra tabs to the main window. Drop a plugin into the addons directory and restart the app.

| Platform | Default addons path |
|----------|-------------------|
| Windows | `%LOCALAPPDATA%\EZTools\SubApp\addons` |
| macOS | `~/Library/Application Support/EZTools/SubApp/addons` |
| Linux | `~/.config/EZTools/SubApp/addons` |

Plugins run with full system access — install only plugins you trust. No app restart is needed to install settings or language map changes, but a restart is required to load or remove a plugin.

> For plugin development documentation (file structure, plugin contract, `app_ctx` API), see [DEV_REFERENCE.md](https://github.com/alanyung-yl/SubApp/blob/main/docs/DEV_REFERENCE.md).

### Bundled addons

| Addon | Description |
|-------|-------------|
| `drive_logger` | Scans folder trees via a checkbox tree view, persists the result to a local SQLite database, and exports to JSON, CSV, or Markdown |
| `subtitle_downloader` | Downloads subtitles from an external source |

---

## Data File Locations

All user data is stored in your profile directory.

| Data | Windows | macOS | Linux |
|------|---------|-------|-------|
| Settings & config | `%LOCALAPPDATA%\EZTools\SubApp\config\` | `~/Library/Application Support/EZTools/SubApp/config/` | `~/.config/EZTools/SubApp/config/` |
| Addons | `%LOCALAPPDATA%\EZTools\SubApp\addons\` | `~/Library/Application Support/EZTools/SubApp/addons/` | `~/.config/EZTools/SubApp/addons/` |
| Logs | `%LOCALAPPDATA%\EZTools\SubApp\log\` | `~/Library/Application Support/EZTools/SubApp/log/` | `~/.local/state/EZTools/SubApp/log/` |

Use **File → Open User Data Folder** to jump straight to the config directory from within the app.

> For the source tree layout and runtime path override details, see [DEV_REFERENCE.md](https://github.com/alanyung-yl/SubApp/blob/main/docs/DEV_REFERENCE.md).

---

## Roadmap

- [ROADMAP.md](https://github.com/alanyung-yl/SubApp/blob/main/docs/ROADMAP.md) — planned features and future platform support

---

## FAQ

**Is SubRename available on macOS or Linux?**

Not yet as a packaged download. macOS and Linux packaging is planned; see [ROADMAP.md](https://github.com/alanyung-yl/SubApp/blob/main/docs/ROADMAP.md) for status.

Running from source is supported on both platforms, but this path has not been fully tested yet. See [Requirements and Installation](#requirements-and-installation) for setup steps.

**The subtitle files are not matching any videos. What should I check?**

First, enable Preview Mode and run — the Preview column will show which files matched and which did not, and the log will explain why. Common causes:
- The episode numbers in the subtitle filenames use a format the parser does not recognise. Check whether the number appears as `[01]`, `E01`, `S01E01`, or bare `01` and compare with the video filenames.
- The video extension is set to a format that does not match the files in the folder. Try setting the destination dropdown to **Auto**.
- The source extension filter is excluding your subtitle files. Set the source dropdown to **All** to bypass filtering.

**I have two subtitle groups for the same show. How do I rename both?**

Load all subtitle files at once. When the app reaches a filename that already exists, it opens a conflict dialog. Choose a custom suffix (or use the detected group suffix automatically) to distinguish the second group. Enable **Use This for All Files of the Same Group** to apply that suffix to the rest of the batch without further prompts.

**The app renamed the subtitles but left the originals behind.**

This is the default behaviour — the app copies rather than moves. To remove originals automatically, use the **Replace Original File(s)** checkbox in the **Orphaned Subtitle Files** dialog when those files are offered for import. Marked files are trashed after a successful non-preview rename. Alternatively, enable **Delete Empty Folders** in Settings if your subtitles were in their own folder and you want that folder cleaned up.

**Preview mode shows conflicts but I want to resolve them automatically.**

Set the conflict policy to **Keep both** or **Overwrite** under **Preferences → Conflicts**. The app will then apply that policy silently without opening a dialog.

**The language code appended to the filename is wrong or not what I want.**

Edit your `langmap.txt` (paths under [Language Map](#language-map-langmaptxt)) to change the output code or add aliases. For example, to make traditional Chinese files say `tc` instead of `cht`, change the key:
```
tc = cht, zh-hant, chinese traditional, ...
```

**Unknown languages are being appended as raw codes like `zho` that I do not want.**

Set **Unknown Language Action** to **Skip** in Settings → Language. The app will then omit any language token that does not appear in the language map.

**The UI looks blurry or oversized on my high-DPI display.**

SubRename follows OS DPI behavior. Use **View → Zoom In/Out/Reset** to adjust UI scale inside the app.

**The settings I changed do not survive a restart.**

Settings are stored in the user config directory (`.../EZTools/SubApp/config/settings.json`), not next to the executable. If settings are not persisting, check write permissions for your user profile directory.

**How do I add a language that is not in the default map?**

Use the in-app editor: **Settings → General → Language → Edit Language Map**, or open your `langmap.txt` and add a line:
```
my_code = full language name, abbreviation, ...
```
Aliases are case-insensitive. Restart the app or save through the in-app editor to apply.

**Can I write a plugin?**

Yes. Drop a plugin file or folder into the addons directory (see [Plugin System](#plugin-system)) and restart the app. See [DEV_REFERENCE.md](https://github.com/alanyung-yl/SubApp/blob/main/docs/DEV_REFERENCE.md) for the full plugin development guide.

---

## License

This project is licensed under the GNU General Public License v3.0 — see the [LICENSE](LICENSE) file for details ([view on GitHub](https://github.com/alanyung-yl/SubApp/blob/main/LICENSE)).
