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
- option to have the edit made to entries of the same studio/group

### Filename Cleanup Rules

`[ ]` Add configurable filename cleanup rules for normalizing noisy release tags after the app generates a rename plan.

#### Goal

Filename Cleanup Rules let users define simple replacement aliases for release tags, codecs, resolution labels, source labels, language markers, and other repeated filename tokens. The first implementation should focus on safe post-generation cleanup of subtitle output names, with optional expansion later to video renaming and pre-match normalization.

Example rule file:

```text
# Resolution cleanup
Ma10p_1080p, 1080p, FHD = HD
2160p, UHD, 4K = 4K

# Codec cleanup
x265_flac_aac, x265, h265, h.265, HEVC = HEVC
x264, h264, h.264, AVC = AVC
```

Example result:

```text
Before:
[hyakuhuyu&VCB-Studio] Re Zero kara Hajimeru Isekai Seikatsu 3rd Season [51][Ma10p_1080p][x265_flac_aac].ass

After:
[hyakuhuyu&VCB-Studio] Re Zero kara Hajimeru Isekai Seikatsu 3rd Season [51][HD][HEVC].ass
```

#### User-Facing Name

Use **Filename Cleanup Rules** in the UI. Internally, names such as `filename_cleanup`, `cleanup_rules`, or `replacement_map` are acceptable.

#### Rule File Format

- Store user-editable rules in `filename_cleanup.txt` under the app config directory.
- Seed a default file from bundled config on first startup, similar to `langmap.txt`.
- Ignore blank lines.
- Ignore comment lines beginning with `#`.
- Parse each rule as:

```text
alias 1, alias 2, alias 3 = replacement
```

- Left side is a comma-separated list of aliases.
- Right side is the output replacement.
- Preserve user casing on the replacement side.
- Match aliases case-insensitively by default.
- Apply longer aliases before shorter aliases to avoid partial replacement problems.

#### Initial Scope: Version 1

- `[ ]` Apply cleanup rules after preview-name generation.
- `[ ]` Apply cleanup rules only to generated subtitle output names by default.
- `[ ]` Do not rename video files in v1.
- `[ ]` Do not use cleanup rules to alter actual matching logic in v1.
- `[ ]` Show the cleaned output name in the existing **New Name** preview column.
- `[ ]` Add a preview status or tooltip indicating that cleanup rules changed the generated name.
- `[ ]` Keep manually edited **New Name** values respected; decide whether cleanup applies before or after manual edits.

Recommended v1 behavior:

```text
video/subtitle matching -> generated new subtitle name -> filename cleanup rules -> preview table -> actual rename
```

#### Matching Modes

Start conservative. Do not blindly replace every substring in the filename.

- `[ ]` **Bracket-tag mode**: replace exact tags inside `[]`, such as `[1080p]` -> `[HD]`.
- `[ ]` **Token-aware mode**: replace tokens separated by spaces, dots, underscores, dashes, or brackets.
- `[ ]` Avoid replacing inside ordinary words.

Example of what to avoid:

```text
Rule:
US = United States

Bad replacement:
SubsPlease -> SUnited StatesbsPlease
```

#### Settings/UI

Add settings under **Settings → General** or a future **Settings → Naming** page:

- `[ ]` Enable Filename Cleanup Rules
- `[ ]` Edit Cleanup Rules
- `[ ]` Reset Cleanup Rules to Defaults
- `[ ]` Apply cleanup after preview generation
- `[ ]` Optional later: apply cleanup before matching
- `[ ]` Optional later: apply cleanup to video filenames

Add a small rule test area/dialog:

- Input filename
- Output preview
- List of matched rules

#### Preview Diff for Cleanup Rules

`[ ]` Add a cleanup-specific diff view so users can see exactly which tokens changed.

This diff should be limited to Filename Cleanup Rules rather than becoming a general rename diff system.

Example:

```text
[Ma10p_1080p] -> [HD]
[x265_flac_aac] -> [HEVC]
```

Possible UI locations:

- Tooltip on the **New Name** cell
- Log entry during preview generation
- Small dialog from right-click → **Show Cleanup Diff**

#### Engine/API Design

Suggested new helper functions:

```python
def parse_filename_cleanup_rules(text: str) -> list[CleanupRule]:
    ...


def load_filename_cleanup_rules(path: Path | None = None) -> list[CleanupRule]:
    ...


def apply_filename_cleanup_rules(filename: str, rules: list[CleanupRule]) -> CleanupResult:
    ...
```

Suggested result object:

```python
@dataclass
class CleanupResult:
    original_name: str
    cleaned_name: str
    changed: bool
    replacements: list[tuple[str, str]]
```

Recommended integration point:

- Generate the proposed subtitle filename first.
- Apply cleanup rules to the proposed filename.
- Store both original generated name and cleaned name in preview metadata.
- Use cleaned name as the actual output name if cleanup is enabled.

#### Future Expansion

- `[ ]` Apply rules before matching to normalize messy subtitle/video titles.
- `[ ]` Add regex cleanup rules for advanced users.
- `[ ]` Add presets such as Anime, Plex, Jellyfin, Movie, and TV Series.
- `[ ]` Add optional video filename cleanup with undo support.
- `[ ]` Include cleanup changes in rename history once history support exists.

---

## UI & Workflow

- `[ ]` Add undo/redo support for completed operations
- `[ ]` Add a frozen/pinned table column to improve navigation on wide tables
- `[ ]` Migrate UI and session state to QSettings

### Undo / Redo Support

`[ ]` Add app-level undo support for completed file operations.

The undo system should track completed transactions instead of relying only on the OS trash. Rename, overwrite, cleanup, and delete operations should record enough information to reverse the last completed action safely. For delete/overwrite operations, prefer app-managed staging so files can be restored deterministically.

### Batch Presets

`[ ]` Add user-selectable presets for common workflows.

Presets can store extension selections, conflict policy, preview mode, language suffix behavior, group suffix behavior, filename cleanup settings, and other naming preferences. Example presets: **Anime**, **Movies**, **Plex/Jellyfin**, **Chinese Subtitles**, and **Raw Video Name Match**.

### Subtitle Encoding Repair

`[ ]` Add a subtitle encoding checker and UTF-8 conversion tool.

This tool should detect common subtitle encodings such as UTF-8, UTF-8 BOM, Big5, GB18030, and Shift-JIS, then optionally convert subtitle files to UTF-8. This is especially useful for Chinese and Japanese subtitle files that display correctly in one player but appear garbled in another.

### Subtitle File Validator

`[ ]` Add validation checks before rename execution.

The validator should flag empty files, suspiciously tiny files, corrupted text, files with the wrong extension, subtitle files with no timestamp lines, HTML/error pages saved as subtitle files, and other invalid inputs. Preview status can show warnings before the user commits a rename job.

### Duplicate Subtitle Detector

`[ ]` Add duplicate subtitle detection.

Detect exact duplicates by file hash and optionally detect near-duplicates by normalized subtitle content. The UI should show duplicate groups and allow the user to keep one file while removing or ignoring the rest.

### Subtitle Timing Offset Tool

`[ ]` Add a simple timing-shift utility for subtitle files.

Users should be able to select one or more subtitle files, enter a positive or negative millisecond offset, preview the first/last adjusted timestamps, and save an adjusted copy. Start with `.srt`; consider `.ass` support later.

### Target Folder Analyzer

`[ ]` Expand the current folder scan into a folder analysis report.

The analyzer should identify videos without subtitles, subtitles without matching videos, duplicate subtitles, conflict risks, suspicious filenames, mixed formats, likely language tags, and orphaned subtitle files. This can become a dedicated **Tools → Analyze Folder** workflow.

### Manual Pairing Mode

`[ ]` Add manual subtitle-to-video pairing for edge cases.

When automatic matching fails, the user should be able to select a subtitle row and manually assign a target video from the current target folder. This is useful for specials, OVAs, absolute episode numbering, unusual movie titles, or files that guessit/regex matching cannot resolve confidently.

---

## Plugin System

*(No items currently planned — open to contributions)*
