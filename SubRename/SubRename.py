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

"""Core subtitle-renaming engine for matching subtitle files to video files."""

import os
import re
import shutil
import logging
from enum import Enum
import dataclasses
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional, List, Callable, Tuple
from pathlib import Path

try:
    from guessit import guessit
    GUESSIT = True
except ImportError:
    GUESSIT = False
    logging.warning("Dependency guessit not available")

try:
    from rapidfuzz import fuzz
    RAPIDFUZZ = True
except ImportError:
    RAPIDFUZZ = False
    logging.warning("Dependency rapidfuzz not available")

import app_paths as ap
from logging_utils import log_success, setup_logging
from file_history import RestorableBackendUnavailableError, UnsafeFilesystemMutationError
from file_history.file_service import FileMutationService
from pathvalidate import sanitize_filename


class ConflictPolicy(Enum):
    """How to handle destination-file conflicts (file already exists at target path)."""
    ASK = "ASK"
    SKIP = "SKIP"
    OVERWRITE = "OVERWRITE"
    SUFFIX = "SUFFIX"


class PlanIssue(Enum):
    """Internal issue classification for rename-plan rows."""
    NONE = "NONE"
    ON_DISK_COLLISION = "ON_DISK_COLLISION"
    IN_BATCH_COLLISION = "IN_BATCH_COLLISION"
    USER_ALWAYS_PROMPT = "USER_ALWAYS_PROMPT"
    NO_MATCH = "NO_MATCH"
    SOURCE_EQUALS_DEST = "SOURCE_EQUALS_DEST"
    MANUAL_EDIT = "MANUAL_EDIT"


# === Regex Constants ===
EPISODE_REGEX = r'\b(?:S\d+E|E|Episode\s*)?(\d{1,4})(?:v\d+)?(?:[^\d\s]*)\b'  # Matches S01E01, E01, 01, Episode 01, etc.
STUDIO_REGEX = r'\[(.*?)\]'
# Language and Country patterns
LANGUAGE_COUNTRY_PATTERNS = [
    r'\b(?:us|uk|jp|kr|cn|tw|hk|in|au|ca|ru|fr|de|es|it|br|mx|nl|se|no|dk|fi)\b',  # Country/Region
    r'\b(Chinese|Chi|CHT|CHS|English|Eng|EN|Spanish|Spa|SP|French|Fra|FR|German|Ger|DE|Italian|Ita|IT|Portuguese|Por|PT|Russian|Rus|RU|'
    r'Japanese|Jpn|JP|Korean|Kor|KR|Arabic|Ara|AR|Dutch|Ned|NL|Swedish|Swe|SV|Norwegian|Nor|NO|Danish|Dan|DA|Finnish|Fin|FI|Polish|Pol|PL|'
    r'Turkish|Tur|TR|Greek|Gre|EL|Hebrew|Heb|HE|Hungarian|Hun|HU|Czech|Cze|CS|Slovak|Slo|SK|Romanian|Rum|RO|Bulgarian|Bul|BG|'
    r'Croatian|Cro|HR|Serbian|Ser|SR|Slovenian|Slo|SL|Estonian|Est|ET|Latvian|Lav|LV|Lithuanian|Lit|LT|Catalan|Cat|CA|Welsh|Wel|CY|Irish|Gle|GA|Scottish|Gla|GD|'
    r'Thai|Tha|TH|Vietnamese|Vie|VI|Indonesian|Ind|ID|Malay|May|MS|Hindi|Hin|HI|Bengali|Ben|BN|Urdu|Urd|UR|Persian|Per|FA)\b',
]

# Other filename filter patterns
FILENAME_FILTER_PATTERNS = [
    r'(?<![a-zA-Z0-9])(2160p|1440p|1080p|720p|576p|540p|480p|432p|360p|4K|8K)(?![a-zA-Z0-9])',
    r'(?<![a-zA-Z0-9])((WEB|DVD|BD|BR|CAM|TS|R5|(F|U|Q|WQ)?HD|SD)?[ .:_-]?(DL|Rip|REMUX|TV|CAM|TS|R5)|Blu[ .:_-]?Ray)(?![a-zA-Z0-9])',
    r'(?<![a-zA-Z0-9])(x264|x265|H\.?264|H\.?265|HEVC|AVC|AV1|xvid|divx|vc[- ]?1)(?![a-zA-Z0-9])',
    r'(?<![a-zA-Z0-9])(DTS[ .:_-]?(HD|XLL)?[ .:_-]?(MA)?(X)?|AC3|AAC|OPUS|Vorbis|MP3|FLAC|TrueHD|Dolby[ .:_-]?(Digital|Atmos)?|Atmos|DD[+P]?)(?![a-zA-Z0-9])',
    r'(?<![a-zA-Z0-9])(Director\'?[ .:_-]?s?[ .:_-]?cut|Collector(s)?|(SPECIAL|LIMITED)[ .:_-]?EDITION|COMPLETE(D)?|IMAX|(SUB|DUB)(BED)?|PROPER|INTERNAL|(DIR|NFO)FIX|READNFO)(?![a-zA-Z0-9])',
    r'(?<![a-zA-Z0-9])(AMZN|NF|NETFLIX|HULU|DSNP|Disney[+]?|MAX|HMAX|HBO|ATVP|Apple[ .:_-]?s?TV[+]?|i[Tt]unes)(?![a-zA-Z0-9])',
    r'(?<![a-zA-Z0-9])(ULTIMATE|THEATRICAL|REMASTERED|EXTENDED|ENHANCED|UNCUT|UNRATED|REPACK|REPACK2|RERIP|DC|SE|EE|TC|UC)(?![a-zA-Z0-9])',
    r'(?<![a-zA-Z0-9])(\d{3,4}x\d{3,4})(?![a-zA-Z0-9])',  # resolutions
    # Group tags
    r'(?<![a-zA-Z0-9])(?:LQ|MQ|HQ|UHQ|Very?[ .:_-]?(Low|High)[ .:_-]?Quality)(?![a-zA-Z0-9])',
    # r'(?<![a-zA-Z0-9])(?:SAMPLE|TRAILER|TEASER|Preview)(?![a-zA-Z0-9])',
    r'(?<![a-zA-Z0-9])(?:(?:mono|stereo)|surround|(?:[12]\.0|2\.1|5\.1|7\.1|9\.1|5\.1\.2|7\.1\.4)|MULTI|DUAL[ .:_-]?(AUDIO)?)(?![a-zA-Z0-9])',
    r'(?<![a-zA-Z0-9])(?:10bit|8bit|12bit|bit[ .:_-]?depth(?:[ .:_-]?\d{1,2})?)(?![a-zA-Z0-9])',  # Bit depth
    r'(?<![a-zA-Z0-9])(?:HDR(?:\d{0,2}\+?)?|SDR|Dolby[ .:_-]?Vision|Do[ .:_-]?Vi|dv)(?![a-zA-Z0-9])',  # HDR/Dolby Vision
    r'(?<![a-zA-Z0-9])(?:bt[ .:_-]?2020|pq|hlg)(?![a-zA-Z0-9])',  # BT
    r'(?<![a-zA-Z0-9])(?:(?:23\.976|24|25|29\.97|30|50|59\.94|60)fps)(?![a-zA-Z0-9])',  # FPS
    r'-[ .:_-]?Copy(?![a-zA-Z0-9])',
    r'\((?:[^\)]+)\)',  # Extras
]

# === Module Constants ===
DEFAULT_SRC_EXT = '.ass'
DEFAULT_DST_EXT = '.mkv'
DEFAULT_TAG = 'SubsPlease'

# === Language Map ===
LANGMAP_FILE = str(ap.user_langmap_file())

DEFAULT_LANG_MAP_TEXT = """\
cht = zh-hant, zh-tw, zh-hk, zht, hant, chinese traditional, traditional chinese, tc, big5, tchi
chs = zh-hans, zh-cn, zh-sg, zhs, hans, chinese simplified, simplified chinese, sc, gb, schi, mandarin
zh = chinese, zho, chi
en = english, eng, en-us, en-gb
ja = japanese, jpn, jp
ko = korean, kor, kr
es = spanish, spa, esp, es-es, es-la
fr = french, fra, fre
de = german, ger, deu
it = italian, ita
pt = portuguese, por, pt-br, pt-pt, brazilian portuguese
ru = russian, rus
ar = arabic, ara
hi = hindi, hin
th = thai, tha
vi = vietnamese, vie
id = indonesian, ind, bahasa indonesia
ms = malay, may, msa, bahasa melayu
"""

def parse_lang_map_text(text):
    """Parse langmap text into (map_dict, reverse_dict).

    map_dict:     {lower_key: {"output": user_cased_key, "aliases": set(lower)}}
    reverse_dict: {lower_alias_or_key: user_cased_output}
    """
    map_dict = {}
    reverse_dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' not in line:
            continue
        key_part, _, aliases_part = line.partition('=')
        output_key = key_part.strip()
        if not output_key:
            continue
        lower_key = output_key.lower()
        aliases = {a.strip().lower() for a in aliases_part.split(',') if a.strip()}

        for alias in aliases:
            if alias in reverse_dict and reverse_dict[alias] != output_key:
                logging.warning(
                    f"langmap: duplicate alias '{alias}' "
                    f"(was '{reverse_dict[alias]}', now '{output_key}')"
                )

        map_dict[lower_key] = {"output": output_key, "aliases": aliases}
        reverse_dict[lower_key] = output_key
        for alias in aliases:
            reverse_dict[alias] = output_key
    return map_dict, reverse_dict

def load_lang_map(path=None):
    """Load user langmap, seed on first use, fallback to DEFAULT_LANG_MAP_TEXT."""
    user_path = Path(path) if path else ap.user_langmap_file()

    try:
        if not user_path.exists():
            user_path.parent.mkdir(parents=True, exist_ok=True)
            seed = ap.bundled_langmap_file()
            if seed.exists():
                shutil.copy2(seed, user_path)  # one-time seed
            else:
                user_path.write_text(DEFAULT_LANG_MAP_TEXT, encoding="utf-8")

        text = user_path.read_text(encoding="utf-8")
        parsed = parse_lang_map_text(text)
        if parsed[0]:
            return parsed
        logging.warning(f"langmap '{user_path}' is empty/invalid, using defaults")
    except Exception as e:
        logging.warning(f"Failed to load langmap from '{user_path}': {e}")

    return parse_lang_map_text(DEFAULT_LANG_MAP_TEXT)

def reload_lang_map():
    """Re-read langmap file and update module-level maps."""
    global LANG_MAP, LANG_REVERSE, _lang_map_loaded
    LANG_MAP, LANG_REVERSE = load_lang_map()
    _lang_map_loaded = True

def serialize_lang_map(map_dict):
    """Convert map_dict back to the human-readable langmap text format."""
    lines = []
    for lower_key in sorted(map_dict.keys()):
        entry = map_dict[lower_key]
        output = entry["output"]
        aliases = sorted(entry["aliases"])
        if aliases:
            lines.append(f"{output} = {', '.join(aliases)}")
        else:
            lines.append(f"{output} =")
    return '\n'.join(lines) + '\n'

LANG_MAP: dict = {}
LANG_REVERSE: dict = {}
_lang_map_loaded: bool = False

def _ensure_lang_map() -> None:
    """Initialise LANG_MAP / LANG_REVERSE on first use (not on import)."""
    global LANG_MAP, LANG_REVERSE, _lang_map_loaded
    if not _lang_map_loaded:
        LANG_MAP, LANG_REVERSE = load_lang_map()
        _lang_map_loaded = True

def resolve_lang(token):
    """Case-insensitive langmap lookup.  Returns the user-cased output code or None."""
    if not token:
        return None
    _ensure_lang_map()
    return LANG_REVERSE.get(token.strip().lower())

@dataclass
class RenameConfig:
    """Configuration for subtitle renaming operations."""
    directory: str
    src_ext: str | List[str] = DEFAULT_SRC_EXT
    dst_ext: str | List[str] = DEFAULT_DST_EXT
    cust_ext: str = DEFAULT_TAG
    ask_fn: Optional[Callable[[str, Optional[str]], str]] = None
    subtitle_files: Optional[List[str]] = None
    video_files: Optional[List[str]] = None
    auto_run: bool = False
    use_default_tag: bool = False
    always_prompt_tag: bool = False
    cache_per_set: bool = True
    cache_per_set_fn: Optional[Callable[[], bool]] = None  # overrides cache_per_set on live set
    conflict_policy: ConflictPolicy = ConflictPolicy.ASK
    conflict_resolver_fn: Optional[Callable[..., Tuple[str, Optional[str], bool]]] = None
    log_file: Optional[str] = None
    preview_mode: bool = False
    custom_names: Optional[dict[str, str]] = None
    pre_resolved_conflicts: Optional[dict[str, dict[str, str]]] = None
    rename_in_place_sources: Optional[set[str]] = None
    group_suffix_enabled: bool = True
    lang_suffix_enabled: bool = False
    unknown_lang_action: str = "append"
    ui_preview_mode: bool = False
    file_mutations: FileMutationService | None = None
    source_row_snapshots: Optional[dict[str, object]] = None
    cancel_event: object | None = None


def _require_job_bound_mutations(config: RenameConfig) -> None:
    if config.preview_mode:
        return
    validator = getattr(config.file_mutations, "assert_job_active", None)
    if not callable(validator):
        raise UnsafeFilesystemMutationError(
            "Refusing to modify files without a job-bound restorable history service."
        )
    validator()


# === Utility Functions ===
def match_extension(filename: str, extensions: str | List[str]) -> bool:
    """Check if filename matches any of the given extensions."""
    if isinstance(extensions, str):
        return filename.lower().endswith(extensions.lower())
    else:
        return any(filename.lower().endswith(ext.lower()) for ext in extensions)

def extract_episode(filename):
    if GUESSIT:  
        try:
            parsed = guessit(filename)
            if parsed.get('type') == 'movie':
                return None
            elif parsed.get('type') == 'episode':
                episode_number = parsed.get('episode')
                if episode_number is not None:
                    return str(episode_number)
        except Exception:  # If guessit fails, fall back to regex-based detection
            pass

    # Strip format indicators before matching episode numbers
    cleaned_filename = filename
    for pattern in FILENAME_FILTER_PATTERNS + LANGUAGE_COUNTRY_PATTERNS:
        cleaned_filename = re.sub(pattern, ' ', cleaned_filename, flags=re.IGNORECASE)
    cleaned_filename = re.sub(r'\s+', ' ', cleaned_filename).strip()  # Collapse whitespace

    # Match the episode number in the cleaned filename
    match = re.search(EPISODE_REGEX, cleaned_filename)
    if match:
        episode = match.group(1)
        # Check for movie-specific patterns that should not be treated as episodes
        movie_pattern = re.compile(
            r'\b(?:chapter|part|volume)\b'
            r'[\s._-]*(?:[ivxlcdm]+|\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten)\b'
            r'[^0-9]{0,40}\b(?:19|20)\d{2}\b',
            re.IGNORECASE
        )
        # Check for movie patterns
        if movie_pattern.search(filename):
            return None
                    
        return str(episode)
    return None

def is_movie(target_files):
    """
    Determine if the target folder contains movies (no episodes) or series (has episodes).
    Returns True if movie mode, False if series mode.
    Ambiguous cases default to series mode.
    """
    if not target_files:
        logging.warning("is_movie: empty file list; defaulting to series mode")
        return False

    saw_movie_signal = False

    for video_file in target_files:
        parsed = None
        if GUESSIT:
            try:
                parsed = guessit(video_file)
            except Exception as exc:
                logging.debug("is_movie: guessit failed for %s: %s", video_file, exc)

        if parsed:
            if parsed.get("type") == "episode":
                return False
            if parsed.get("season") is not None or parsed.get("episode") is not None:
                return False
            if parsed.get("type") == "movie":
                saw_movie_signal = True
                continue

        if extract_episode(video_file) is not None:
            return False

    if not saw_movie_signal:
        logging.warning("is_movie: ambiguous classification; defaulting to series mode")
        return False

    return True

def normalize_title(raw_name: str) -> str:
    """
    Normalize title using guessit for better parsing of video filenames.
    Falls back to simple regex if guessit is not available.
    """
    if not GUESSIT:
        # Fallback to simple regex normalization
        base = os.path.splitext(raw_name)[0]
        name = re.sub(r'[._\-]+', ' ', base)
        # Remove common scene/release tags
        for pat in FILENAME_FILTER_PATTERNS + LANGUAGE_COUNTRY_PATTERNS:
            name = re.sub(pat, ' ', name, flags=re.IGNORECASE)
        name = re.sub(r'\s+', ' ', name).strip()  # Collapse whitespace
        return name
    
    # Use guessit for better parsing
    try:
        parsed = guessit(raw_name)
        
        # Print all parsed groups for debugging
        # print(f"=== Guessit parsing for: {raw_name} ===")
        # for key, value in parsed.items():
            # print(f"{key}: {value}")
        # print("=" * 50)
       
        # Extract title and year
        title = parsed.get('title', '')
        year = parsed.get('year')
        
        # Build normalized name
        normalized_parts = []
        if title:
            normalized_parts.append(str(title))
        if year:
            normalized_parts.append(str(year))
        
        # Join parts and clean up
        normalized = ' '.join(normalized_parts)
        # print(f"Normalized: {normalized}")
        
        # Strip language/country patterns from title as additional safety measure
        for pat in LANGUAGE_COUNTRY_PATTERNS:
            normalized = re.sub(pat, ' ', normalized, flags=re.IGNORECASE)
        
        normalized = re.sub(r'\s+', ' ', normalized).strip()
        # print(f"Normalized: {normalized}")
        
        return normalized
    except Exception as e:
        logging.warning(f"guessit failed for '{raw_name}': {e}")
        # Fallback to simple extraction
        base = os.path.splitext(raw_name)[0]
        return re.sub(r'[._\-]+', ' ', base).strip()

def find_best_movie_match(subtitle_name, video_files, similarity_threshold=0.8):
    """
    Find the best matching video file for a subtitle based on filename similarity.
    Uses guessit for better parsing when available.
    Returns (best_video, score) or (None, best_score_if_below_threshold).
    """
    def extract_year_candidates(text: str) -> set[str]:
        years: set[str] = set()
        # Standard 4-digit years
        for m in re.finditer(r'\b(19\d{2}|20\d{2})\b', text):
            years.add(m.group(1))
        # Handle 5-6 digit tokens starting with 19/20 (e.g., 201022 → 2010)
        for m in re.finditer(r'\b(19\d{3,4}|20\d{3,4})\b', text):
            candidate = m.group(1)[:4]
            if candidate.startswith(('19', '20')):
                years.add(candidate)
        return years

    # Extract subtitle info
    if GUESSIT:
        # Clean subtitle name using guessit
        parsed = guessit(subtitle_name)
        sub_title = parsed.get('title', '')
        sub_year = parsed.get('year')
        sub_years = {str(sub_year)} if sub_year else set()
    else:
        sub_title = normalize_title(subtitle_name)
        sub_years = extract_year_candidates(sub_title)

    best_match = None
    best_score = 0.0

    for video_file in video_files:
        # Extract video info
        if GUESSIT:
            parsed = guessit(video_file)
            vid_title = parsed.get('title', '')
            vid_year = parsed.get('year')
            vid_years = {str(vid_year)} if vid_year else set()
        else:
            vid_title = normalize_title(video_file)
            vid_years = extract_year_candidates(vid_title)

        score = 0.0
        
        if RAPIDFUZZ:
            # Scenario 1: guessit.get(title) + rapidfuzz
            if sub_title and vid_title:
                ratio = fuzz.ratio(sub_title, vid_title) / 100.0
                partial_ratio = fuzz.partial_ratio(sub_title, vid_title) / 100.0
                token_sort_ratio = fuzz.token_sort_ratio(sub_title, vid_title) / 100.0
                token_set_ratio = fuzz.token_set_ratio(sub_title, vid_title) / 100.0
                score = max(ratio, partial_ratio, token_sort_ratio, token_set_ratio)
                # print(f"({'Guessit + ' if GUESSIT else ''}RapidFuzz): '{sub_title}' vs '{vid_title}' - best={score:.3f}")
            else:
                score = 0.0
                
        else:  # Sequencematcher
            if sub_title and vid_title:
                score = SequenceMatcher(None, sub_title.lower(), vid_title.lower()).ratio()
                # print(f"({'Guessit + ' if GUESSIT else ''}SequenceMatcher): '{sub_title}' vs '{vid_title}' - score={score:.3f}")
            else:
                score = 0.0

        # Year matching requirement
        common_year = bool(sub_years & vid_years)
        
        # If years don't match, force score to 0 (mismatch)
        if not common_year and (sub_years and vid_years):
            score = 0.0
            # print(f"Year mismatch: {sub_years} vs {vid_years}")
        elif common_year:
            # Apply year boost for matching years
            score = min(1.0, score + 0.15)
            # print(f"Year boost applied: {sub_years} ∩ {vid_years} = {sub_years & vid_years}")

        # print(f"Final score: {score:.3f} (year match: {common_year})")

        # Track best
        if score > best_score:
            best_score = score
            best_match = video_file

    if best_score >= similarity_threshold:
        return best_match, best_score
    return None, best_score

def _clean_group_name(raw_group):
    """Strip trailing language suffixes from a release-group string."""
    group = raw_group.strip().strip('.-_ ')
    if group.startswith('[') and group.endswith(']'):
        group = group[1:-1].strip()

    # Split while keeping separators so we can reconstruct accurately.
    parts = re.split(r'([-._\s]+)', group)
    tokens = parts[::2]

    if len(tokens) <= 1:
        return re.sub(r'[\\/:*?"<>|]', '_', group) if group else group

    # Find the first language token (never strip the very first token).
    first_lang_idx = None
    for i in range(1, len(tokens)):
        if tokens[i] and resolve_lang(tokens[i]) is not None:
            first_lang_idx = i
            break

    if first_lang_idx is not None:
        group = ''.join(parts[:first_lang_idx * 2]).rstrip('.-_ ')

    group = re.sub(r'[\\/:*?"<>|]', '_', group)
    return group if group else raw_group.strip()

def _resolve_guessit_lang(lang_obj):
    """Try to map a guessit/babelfish Language object through langmap."""
    for attr in ('alpha2', 'alpha3'):
        try:
            val = getattr(lang_obj, attr, None)
            if val:
                code = resolve_lang(str(val))
                if code:
                    return code
        except Exception:
            pass
    return resolve_lang(str(lang_obj))

def extract_studio_name(filename):
    """Extract studio/group name from filename (base group only, no lang suffix).
    
    Uses guessit's release_group first.  If guessit's release_group is actually
    a language token (matches langmap), fall back to bracket regex instead.
    """
    group = None
    if GUESSIT:
        try:
            parsed = guessit(filename)
            raw_group = parsed.get('release_group', '')
            if raw_group:
                raw_str = str(raw_group).strip()
                cleaned_group = _clean_group_name(raw_str)
                if (resolve_lang(raw_str) is not None or (cleaned_group and resolve_lang(cleaned_group) is not None)):
                    group = None
                elif cleaned_group:
                    group = cleaned_group
        except Exception:
            pass

    if group is None:
        match = re.match(STUDIO_REGEX, filename)
        if match:
            group = match.group(1)

    if not group:  # Fallback
        base = os.path.splitext(os.path.basename(filename))[0]
        scene_match = re.search(r'(?:\[(?P<bracket>[^\[\]\\/]+)\]|(?:\s-\s|-(?=[^-]+$))(?P<dash>[^-\\/]+))', base)
        if scene_match:
            candidate = _clean_group_name(scene_match.group('bracket') or scene_match.group('dash'))
            if candidate and resolve_lang(candidate) is None:
                group = candidate

    return group if group else DEFAULT_TAG

def extract_language_suffix(filename, unknown_lang_action="append"):
    """Detect language(s) from a subtitle filename via guessit + filename scan.

    Returns a hyphen-joined suffix string (e.g. "cht", "cht-jpn") or "".

    When guessit's release_group is itself a language (matches langmap), its
    subtitle_language field is likely wrong (derived from the same token), so we
    combine the release_group language with subtitle_language through langmap
    instead of trusting subtitle_language directly.
    """
    detected: list[tuple[int, str]] = []  # (position_in_filename, mapped_code)
    seen_codes: set[str] = set()
    filename_lower = filename.lower()
    release_group_is_lang = False

    def _add(code, token):
        """Deduplicate and record position of *token* in the raw filename."""
        if not code or code in seen_codes:
            return
        pos = filename_lower.find(token.lower()) if token else len(filename_lower)
        if pos < 0:
            pos = len(filename_lower)
        detected.append((pos, code))
        seen_codes.add(code)

    def _handle_lang_obj(lang_obj):
        code = _resolve_guessit_lang(lang_obj)
        if code:
            _add(code, str(lang_obj))
            return
        if unknown_lang_action == "append":
            try:
                fallback = lang_obj.alpha2
            except Exception:
                fallback = str(lang_obj)
            _add(fallback, str(lang_obj))

    if GUESSIT:
        try:
            parsed = guessit(filename)
            raw_group = parsed.get('release_group', '')
            if raw_group and resolve_lang(str(raw_group)) is not None:
                release_group_is_lang = True
                _add(resolve_lang(str(raw_group)), str(raw_group))

            for key in ('subtitle_language', 'language'):
                langs = parsed.get(key)
                if langs is None:
                    continue
                if not isinstance(langs, list):
                    langs = [langs]
                for lang_obj in langs:
                    if release_group_is_lang:
                        code = _resolve_guessit_lang(lang_obj)
                        if code:
                            _add(code, str(lang_obj))
                        elif unknown_lang_action == "append":
                            try:
                                fb = lang_obj.alpha2
                            except Exception:
                                fb = str(lang_obj)
                            _add(fb, str(lang_obj))
                    else:
                        _handle_lang_obj(lang_obj)
        except Exception:
            pass

    if not detected:
        base = os.path.splitext(filename)[0]
        for tok in re.split(r'[-._\s\[\]()&]+', base):
            if tok:
                code = resolve_lang(tok)
                if code:
                    _add(code, tok)

    if not detected:
        return ""

    detected.sort(key=lambda t: t[0])
    return "-".join(code for _, code in detected)

class UserCancelledPrompt(Exception):
    """Raised when the user cancels the custom-tag dialog."""

def normalize_prompt_tag(response, studio_name):
    """Return the sanitized tag that the suffix prompt would accept."""
    new_tag = (response or studio_name).strip('.')
    return sanitize_filename(new_tag, platform="auto") if new_tag else ''

def prompt_for_tag(studio_name, ask_fn=None, context="always_prompt", filename=None):
    """Prompt user for a group suffix via the ask_user dialog.

    Only the ``always_prompt`` context is used by the current workflow.

    Returns the sanitised tag string chosen by the user.
    Raises ``UserCancelledPrompt`` when the user skips.
    """
    def ask(p, f=None):
        if ask_fn:
            try:
                return ask_fn(p, f)
            except TypeError:
                return ask_fn(p)
        else:
            return input(p).strip()

    prompt = f"Enter a custom suffix for {studio_name}"

    while True:
        resp = ask(prompt, filename)
        if resp is None:
            raise UserCancelledPrompt
        new_tag = normalize_prompt_tag(resp, studio_name)
        if new_tag == '':
            prompt = f"Please enter a valid suffix for {studio_name} "
            continue
        return new_tag

def generate_suffixed_path(base_name: str, ext: str, directory: str, renamed_files: list[str]) -> str:
    """Generate a unique path by appending .(N) suffix to avoid collisions."""
    count = 1
    while True:
        suffixed_name = f"{base_name}.({count}){ext}"
        suffixed_path = os.path.join(directory, suffixed_name)
        if not os.path.exists(suffixed_path) and suffixed_path not in renamed_files:
            return suffixed_name
        count += 1

def detect_plan_issue(source_path: str, new_path: str, renamed_files: list[str]) -> PlanIssue:
    """Classify the provisional destination before optional prompt suffixes."""
    src_norm = os.path.normcase(os.path.abspath(source_path))
    dst_norm = os.path.normcase(os.path.abspath(new_path))
    if src_norm == dst_norm:
        return PlanIssue.SOURCE_EQUALS_DEST
    if new_path in renamed_files:
        return PlanIssue.IN_BATCH_COLLISION
    if os.path.exists(new_path):
        return PlanIssue.ON_DISK_COLLISION
    return PlanIssue.NONE

def resolve_conflict(
    new_sub_name: str,
    new_path: str,
    config: RenameConfig,
    renamed_files: list[str],
    source_path: str,
    ask_cache: dict,
    renamed_source_by_path: Optional[dict[str, str]] = None,
    include_metadata: bool = False,
) -> Tuple[str, str, str] | Tuple[str, str, str, dict[str, Optional[str]]]:
    """
    Resolve a destination-file conflict.

    Returns (new_sub_name, new_path, status) where status is one of:
        "OK", "OVERWRITE", "SUFFIX", "SKIP", "SKIP_EXISTS"
    """
    def done(
        resolved_name: str,
        resolved_path: str,
        status: str,
        issue_type: PlanIssue,
        conflict_path: Optional[str] = None,
        conflicting_source_path: Optional[str] = None,
    ):
        if include_metadata:
            return resolved_name, resolved_path, status, {
                "issue_type": issue_type.value,
                "conflict_path": conflict_path,
                "conflicting_source_path": conflicting_source_path,
            }
        return resolved_name, resolved_path, status

    src_norm = os.path.normcase(os.path.abspath(source_path))
    dst_norm = os.path.normcase(os.path.abspath(new_path))
    if src_norm == dst_norm:
        return done(new_sub_name, new_path, "SKIP_EXISTS", PlanIssue.SOURCE_EQUALS_DEST)

    in_batch_collision = new_path in renamed_files
    on_disk_collision = os.path.exists(new_path)
    original_conflict_path = new_path if on_disk_collision else None

    if not in_batch_collision and not on_disk_collision:
        return done(new_sub_name, new_path, "OK", PlanIssue.NONE)

    # In-batch collisions always suffix regardless of policy
    if in_batch_collision:
        conflicting_source_path = None
        if renamed_source_by_path:
            conflicting_source_path = renamed_source_by_path.get(new_path)

        pre_resolved = config.pre_resolved_conflicts or {}
        pre = pre_resolved.get(source_path)
        if pre is None:
            pre = pre_resolved.get(os.path.normcase(os.path.abspath(source_path)))
        if pre:
            pre_status = pre.get("status")
            pre_name = (pre.get("new_name") or "").strip()

            if pre_status == "SKIP":
                return done(
                    new_sub_name,
                    new_path,
                    "SKIP",
                    PlanIssue.IN_BATCH_COLLISION,
                    conflict_path=original_conflict_path,
                    conflicting_source_path=conflicting_source_path,
                )

            if pre_status in ("SUFFIX", "TAG"):
                if pre_name:
                    pre_name = os.path.basename(pre_name)
                    pre_path = os.path.join(config.directory, pre_name)
                    if not (os.path.exists(pre_path) or pre_path in renamed_files):
                        return done(
                            pre_name,
                            pre_path,
                            pre_status,
                            PlanIssue.IN_BATCH_COLLISION,
                            conflict_path=original_conflict_path,
                            conflicting_source_path=conflicting_source_path,
                        )

                base_name, ext = os.path.splitext(new_sub_name)
                fallback = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
                return done(
                    fallback,
                    os.path.join(config.directory, fallback),
                    "SUFFIX",
                    PlanIssue.IN_BATCH_COLLISION,
                    conflict_path=original_conflict_path,
                    conflicting_source_path=conflicting_source_path,
                )

        policy = config.conflict_policy
        if policy == ConflictPolicy.ASK and ask_cache.get("apply_all"):
            policy = ask_cache["cached_policy"]
            if policy == ConflictPolicy.SKIP:
                return done(
                    new_sub_name,
                    new_path,
                    "SKIP",
                    PlanIssue.IN_BATCH_COLLISION,
                    conflict_path=original_conflict_path,
                    conflicting_source_path=conflicting_source_path,
                )
            if policy == ConflictPolicy.SUFFIX and "cached_tag" in ask_cache:
                cached_tag = ask_cache["cached_tag"]
                base_name, ext = os.path.splitext(new_sub_name)
                new_sub_name = f"{base_name}.{cached_tag}{ext}"
                new_path = os.path.join(config.directory, new_sub_name)
                if os.path.exists(new_path) or new_path in renamed_files:
                    base_name, ext = os.path.splitext(new_sub_name)
                    new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
                    new_path = os.path.join(config.directory, new_sub_name)
                return done(
                    new_sub_name,
                    new_path,
                    "TAG",
                    PlanIssue.IN_BATCH_COLLISION,
                    conflict_path=original_conflict_path,
                    conflicting_source_path=conflicting_source_path,
                )

        if policy == ConflictPolicy.ASK and config.conflict_resolver_fn:
            orig_base = os.path.splitext(new_sub_name)[0]
            try:
                action, alt_path, apply_all = config.conflict_resolver_fn(
                    source_path,
                    new_path,
                    new_sub_name,
                    issue_type=PlanIssue.IN_BATCH_COLLISION.value,
                    conflicting_source_path=conflicting_source_path,
                    conflict_path=original_conflict_path,
                    allow_overwrite=False,
                    show_disabled_overwrite=True,
                )
            except TypeError as exc:
                if "unexpected keyword" not in str(exc):
                    raise
                action, alt_path, apply_all = config.conflict_resolver_fn(
                    source_path, new_path, new_sub_name
                )

            if apply_all:
                if action in ("SUFFIX", "TAG", "OVERWRITE"):
                    ask_cache["cached_policy"] = ConflictPolicy.SUFFIX
                    if alt_path:
                        alt_base = os.path.splitext(os.path.basename(alt_path))[0]
                        if alt_base.startswith(orig_base + "."):
                            ask_cache["cached_tag"] = alt_base[len(orig_base) + 1:]
                elif action == "SKIP":
                    ask_cache["cached_policy"] = ConflictPolicy.SKIP
                ask_cache["apply_all"] = True

            if action == "SKIP":
                return done(
                    new_sub_name,
                    new_path,
                    "SKIP",
                    PlanIssue.IN_BATCH_COLLISION,
                    conflict_path=original_conflict_path,
                    conflicting_source_path=conflicting_source_path,
                )
            if action in ("SUFFIX", "TAG"):
                if alt_path:
                    new_sub_name = os.path.basename(alt_path)
                    new_path = os.path.join(config.directory, new_sub_name)
                    if os.path.exists(new_path) or new_path in renamed_files:
                        base_name, ext = os.path.splitext(new_sub_name)
                        new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
                        new_path = os.path.join(config.directory, new_sub_name)
                else:
                    base_name, ext = os.path.splitext(new_sub_name)
                    new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
                    new_path = os.path.join(config.directory, new_sub_name)
                return done(
                    new_sub_name,
                    new_path,
                    ("TAG" if action == "TAG" and alt_path else "SUFFIX"),
                    PlanIssue.IN_BATCH_COLLISION,
                    conflict_path=original_conflict_path,
                    conflicting_source_path=conflicting_source_path,
                )

        if policy == ConflictPolicy.SKIP:
            return done(
                new_sub_name,
                new_path,
                "SKIP",
                PlanIssue.IN_BATCH_COLLISION,
                conflict_path=original_conflict_path,
                conflicting_source_path=conflicting_source_path,
            )

        base_name, ext = os.path.splitext(new_sub_name)
        new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
        new_path = os.path.join(config.directory, new_sub_name)
        return done(
            new_sub_name,
            new_path,
            "SUFFIX",
            PlanIssue.IN_BATCH_COLLISION,
            conflict_path=original_conflict_path,
            conflicting_source_path=conflicting_source_path,
        )

    # Reuse preview conflict decisions in the actual run (avoid re-prompting)
    if on_disk_collision:
        pre_resolved = config.pre_resolved_conflicts or {}
        pre = pre_resolved.get(source_path)
        if pre is None:
            pre = pre_resolved.get(os.path.normcase(os.path.abspath(source_path)))
        if pre:
            pre_status = pre.get("status")
            pre_name = (pre.get("new_name") or "").strip()

            if pre_status == "OVERWRITE":
                return done(new_sub_name, new_path, "OVERWRITE", PlanIssue.ON_DISK_COLLISION, conflict_path=original_conflict_path)

            if pre_status in ("SUFFIX", "TAG"):
                if pre_name:
                    pre_name = os.path.basename(pre_name)
                    pre_path = os.path.join(config.directory, pre_name)
                    if not (os.path.exists(pre_path) or pre_path in renamed_files):
                        return done(pre_name, pre_path, pre_status, PlanIssue.ON_DISK_COLLISION, conflict_path=original_conflict_path)
                base_name, ext = os.path.splitext(new_sub_name)
                fallback = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
                return done(
                    fallback,
                    os.path.join(config.directory, fallback),
                    "SUFFIX",
                    PlanIssue.ON_DISK_COLLISION,
                    conflict_path=original_conflict_path,
                )

            if pre_status == "SKIP":
                return done(new_sub_name, new_path, "SKIP", PlanIssue.ON_DISK_COLLISION, conflict_path=original_conflict_path)

    # On-disk collision: apply conflict policy
    policy = config.conflict_policy

    # Check ASK cache first
    if policy == ConflictPolicy.ASK and ask_cache.get("apply_all"):
        policy = ask_cache["cached_policy"]
        # If the user chose "different tag" with apply-all, reuse that tag
        if policy == ConflictPolicy.SUFFIX and "cached_tag" in ask_cache:
            cached_tag = ask_cache["cached_tag"]
            base_name, ext = os.path.splitext(new_sub_name)
            new_sub_name = f"{base_name}.{cached_tag}{ext}"
            new_path = os.path.join(config.directory, new_sub_name)
            if os.path.exists(new_path) or new_path in renamed_files:
                base_name, ext = os.path.splitext(new_sub_name)
                new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
                new_path = os.path.join(config.directory, new_sub_name)
            return done(new_sub_name, new_path, "TAG", PlanIssue.ON_DISK_COLLISION, conflict_path=original_conflict_path)

    if policy == ConflictPolicy.ASK:
        if config.conflict_resolver_fn:
            orig_base = os.path.splitext(new_sub_name)[0]
            action, alt_path, apply_all = config.conflict_resolver_fn(
                source_path, new_path, new_sub_name
            )
            if apply_all:
                if action == "OVERWRITE":
                    ask_cache["cached_policy"] = ConflictPolicy.OVERWRITE
                elif action in ("SUFFIX", "TAG"):
                    ask_cache["cached_policy"] = ConflictPolicy.SUFFIX
                    if alt_path:
                        alt_base = os.path.splitext(os.path.basename(alt_path))[0]
                        if alt_base.startswith(orig_base + "."):
                            ask_cache["cached_tag"] = alt_base[len(orig_base) + 1:]
                elif action == "SKIP":
                    ask_cache["cached_policy"] = ConflictPolicy.SKIP
                ask_cache["apply_all"] = True

            if action == "OVERWRITE":
                return done(new_sub_name, new_path, "OVERWRITE", PlanIssue.ON_DISK_COLLISION, conflict_path=original_conflict_path)
            elif action in ("SUFFIX", "TAG"):
                if alt_path:
                    new_sub_name = os.path.basename(alt_path)
                    new_path = os.path.join(config.directory, new_sub_name)
                    if os.path.exists(new_path) or new_path in renamed_files:
                        base_name, ext = os.path.splitext(new_sub_name)
                        new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
                        new_path = os.path.join(config.directory, new_sub_name)
                else:
                    base_name, ext = os.path.splitext(new_sub_name)
                    new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
                    new_path = os.path.join(config.directory, new_sub_name)
                return done(
                    new_sub_name,
                    new_path,
                    ("TAG" if action == "TAG" and alt_path else "SUFFIX"),
                    PlanIssue.ON_DISK_COLLISION,
                    conflict_path=original_conflict_path,
                )
            else:
                return done(new_sub_name, new_path, "SKIP", PlanIssue.ON_DISK_COLLISION, conflict_path=original_conflict_path)
        else:
            # No resolver function, fall back to SUFFIX
            base_name, ext = os.path.splitext(new_sub_name)
            new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
            new_path = os.path.join(config.directory, new_sub_name)
            return done(new_sub_name, new_path, "SUFFIX", PlanIssue.ON_DISK_COLLISION, conflict_path=original_conflict_path)

    elif policy == ConflictPolicy.SKIP:
        return done(new_sub_name, new_path, "SKIP", PlanIssue.ON_DISK_COLLISION, conflict_path=original_conflict_path)

    elif policy == ConflictPolicy.OVERWRITE:
        return done(new_sub_name, new_path, "OVERWRITE", PlanIssue.ON_DISK_COLLISION, conflict_path=original_conflict_path)

    elif policy == ConflictPolicy.SUFFIX:
        base_name, ext = os.path.splitext(new_sub_name)
        new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
        new_path = os.path.join(config.directory, new_sub_name)
        return done(new_sub_name, new_path, "SUFFIX", PlanIssue.ON_DISK_COLLISION, conflict_path=original_conflict_path)

    return done(new_sub_name, new_path, "OK", PlanIssue.NONE)


def rename_files(config: RenameConfig):
    """
    Build or execute a rename plan for subtitle files.

    The pipeline is:
      1. collect source subtitles and target videos,
      2. group subtitles by detected release group/studio,
      3. decide any group suffix for that group,
      4. match each subtitle to a target video,
      5. build the proposed destination filename,
      6. resolve in-batch/on-disk conflicts,
      7. either return preview rows or copy files.
    
    Returns a dict: {"OK": [...], "FAIL": [...], "SKIPPED": [...]} where each list contains file paths
    """
    _require_job_bound_mutations(config)

    renamed_files = []
    renamed_source_by_path: dict[str, str] = {}
    results = {"OK": [], "FAIL": [], "SKIPPED": [], "RENAMED_PATHS": [], "ROW_META": []}
    in_place = {os.path.normpath(p) for p in (config.rename_in_place_sources or set())}
    preview_rows: list[dict] = []
    ask_cache: dict = {}

    def make_preview_row(
        source_path: str,
        new_name: str,
        status: str,
        issue_type: PlanIssue | str = PlanIssue.NONE,
        detected_group: str = "",
        last_generated_name: str = "",
        clean_generated_name: str = "",
        conflict_path: Optional[str] = None,
        conflicting_source_path: Optional[str] = None,
    ) -> dict:
        issue_value = issue_type.value if isinstance(issue_type, PlanIssue) else str(issue_type)
        return {
            "source_path": source_path,
            "new_name": new_name,
            "status": status,
            "issue_type": issue_value,
            "user_modified": False,
            "detected_group": detected_group,
            "last_generated_name": last_generated_name or new_name,
            "clean_generated_name": clean_generated_name or last_generated_name or new_name,
            "conflict_path": conflict_path,
            "conflicting_source_path": conflicting_source_path,
        }

    try:
        all_files = os.listdir(config.directory)
        # Use provided video_files if given; else detect from directory by dst_ext
        if config.video_files is not None:
            target_files = [os.path.basename(f) for f in config.video_files]
        else:
            target_files = [f for f in all_files if match_extension(f, config.dst_ext)]
        src_files_in_dir = [f for f in all_files if match_extension(f, config.src_ext)]

        # Use provided subtitle_files if given, else use all in dir
        source_filenames = config.subtitle_files if config.subtitle_files is not None else [os.path.join(config.directory, f) for f in src_files_in_dir]

        # Determine if this is movie or series
        movie_mode = is_movie(target_files)

        if movie_mode:
            # Movie mode uses fuzzy filename matching against the flat video list.
            video_files = target_files
            subtitle_files = [os.path.basename(s) for s in source_filenames]
            logging.info(f"Found {len(video_files)} video files and {len(subtitle_files)} subtitle files")
        else:
            # Series mode pre-indexes videos by episode number for fast lookup.
            episode_to_video = {}
            for v in target_files:
                ep = extract_episode(v)
                if ep is not None:
                    episode_to_video.setdefault(ep, []).append(v)
            episode_to_subs = {}
            for s in src_files_in_dir:
                ep = extract_episode(s)
                if ep is not None:
                    episode_to_subs.setdefault(ep, []).append(s)

        # Group source files by studio (base group only, no lang suffix)
        studio_to_files = {}
        for s in source_filenames:
            studio = extract_studio_name(os.path.basename(s))
            studio_to_files.setdefault(studio, []).append(s)

        studio_tags = {}
        processed_episodes_in_job = set()  # Track episodes processed in current job
        for studio, files in studio_to_files.items():
            # Re-read cache_per_set at the start of each studio so a checkbox
            # toggle from the previous dialog takes effect immediately.
            ask_cache.clear()
            cache_per_set = config.cache_per_set_fn() if config.cache_per_set_fn else config.cache_per_set

            # ------------------------------------------------------------------
            # Determine the group suffix for this studio.
            #
            # Priority order:
            #   1. Cached tag for this studio.
            #   2. Auto-apply detected group name (use_default_tag) in
            #      multi-group jobs when the studio is not the fallback default.
            #   3. always_prompt_tag: defer the prompt until each file's
            #      no-suffix destination is known to be conflict-free.
            #   4. No suffix.
            # ------------------------------------------------------------------
            tag = None
            prompt_per_file = False  # Gate
            has_reusable_decisions = (
                not config.preview_mode and bool(config.custom_names or config.pre_resolved_conflicts)
            )  # Pre-resolved conflict results should be reused instead of prompting for the same suffix decision again.

            if cache_per_set and studio in studio_tags:
                tag = studio_tags[studio]

            elif (config.group_suffix_enabled
                  and config.use_default_tag
                  and len(studio_to_files) > 1
                  and studio != DEFAULT_TAG):
                tag = studio
                if cache_per_set:
                    studio_tags[studio] = tag

            elif (config.group_suffix_enabled
                  and config.always_prompt_tag
                  and not has_reusable_decisions):
                # Skip prompting entirely if no files in this group can match.
                # This avoids showing a suffix dialog for rows that will become "No Match" regardless of the user's suffix choice.
                has_matchable = False
                if movie_mode:
                    for s in files:
                        matched, _ = find_best_movie_match(os.path.basename(s), video_files)
                        if matched is not None:
                            has_matchable = True
                            break
                else:
                    for s in files:
                        if extract_episode(os.path.basename(s)) is not None:
                            has_matchable = True
                            break
                if not has_matchable:
                    tag = ''

                else:
                    prompt_per_file = True

            prompted_group_tag = None

            # Process each source subtitle file in this studio group.
            for source_index, source_path in enumerate(files):
                if config.cancel_event is not None and config.cancel_event.is_set():
                    logging.warning("Rename job interrupted between files during application shutdown")
                    return {"PREVIEW": preview_rows} if config.preview_mode else results
                try:
                    source_filename = os.path.basename(source_path)

                    if movie_mode:
                        matching_video, similarity_score = find_best_movie_match(source_filename, video_files)
                        if matching_video is None:
                            logging.info(f"SKIPPED: No matching video file found for subtitle: {source_filename} (best similarity: {similarity_score:.2f})")
                            if config.preview_mode:
                                preview_rows.append(make_preview_row(
                                    source_path, "", "FAIL",
                                    PlanIssue.NO_MATCH, detected_group=studio,
                                ))
                            else:
                                results["FAIL"].append(source_path)
                            continue
                        video_file = matching_video
                        video_base = os.path.splitext(video_file)[0]
                    else:
                        # Series mode: Use episode-based matching
                        episode = extract_episode(source_filename)
                        if episode is None:
                            logging.error(f"Could not extract episode from source filename: {source_filename}")
                            if config.preview_mode:
                                preview_rows.append(make_preview_row(
                                    source_path, "", "FAIL",
                                    PlanIssue.NO_MATCH, detected_group=studio,
                                ))
                            else:
                                results["FAIL"].append(source_path)
                            continue
                        matching_videos = episode_to_video.get(episode, [])
                        if not matching_videos:
                            logging.info(f"SKIPPED: No matching video file found for episode {episode}")
                            if config.preview_mode:
                                preview_rows.append(make_preview_row(
                                    source_path, "", "FAIL",
                                    PlanIssue.NO_MATCH, detected_group=studio,
                                ))
                            else:
                                results["FAIL"].append(source_path)
                            continue
                        if len(matching_videos) > 1:
                            logging.warning(f"Multiple video files found for episode {episode}. Using the first one.")
                        video_file = matching_videos[0]
                        video_base = os.path.splitext(video_file)[0]
                    
                    original_ext = os.path.splitext(source_filename)[1]

                    # Table edit is preferred over generated naming and is sanitized here.
                    custom_name = (config.custom_names or {}).get(source_path, '') or ''
                    custom_name = custom_name.strip() if custom_name else ''
                    if custom_name:
                        custom_name = sanitize_filename(custom_name, platform="auto")
                        if not custom_name:
                            logging.warning(
                                f"Custom name for '{source_filename}' was entirely invalid; "
                                "falling back to auto-generated name"
                            )

                    def build_destination_name(active_file_tag: str) -> str:
                        """Build the intended filename before conflict resolution."""
                        if custom_name:
                            return custom_name
                        suffix_parts = []
                        if config.group_suffix_enabled and active_file_tag:
                            suffix_parts.append(active_file_tag)
                        if config.lang_suffix_enabled:
                            lang_code = extract_language_suffix(
                                source_filename,
                                unknown_lang_action=config.unknown_lang_action,
                            )
                            if lang_code:
                                suffix_parts.append(lang_code)
                        if suffix_parts:
                            return f"{video_base}.{'.'.join(suffix_parts)}{original_ext}"
                        return f"{video_base}{original_ext}"

                    # clean_generated_name is the action base used when the UI changes
                    # a row's conflict/suffix decision after preview generation.
                    clean_generated_name = build_destination_name('')

                    # First build the destination without any always_prompt_tag suffix so conflicts keep their proper conflict-dialog path.
                    base_file_tag = tag or ''
                    provisional_name = build_destination_name(base_file_tag)
                    provisional_path = os.path.join(config.directory, provisional_name)
                    provisional_issue = detect_plan_issue(source_path, provisional_path, renamed_files)

                    prompted_for_suffix = False
                    file_tag = base_file_tag
                    new_sub_name = None
                    if (
                        prompt_per_file
                        and not custom_name
                        and provisional_issue == PlanIssue.NONE
                    ):
                        if cache_per_set and prompted_group_tag is not None:
                            file_tag = prompted_group_tag
                            prompted_for_suffix = True
                            new_sub_name = build_destination_name(file_tag)
                        else:
                            default_file_tag = normalize_prompt_tag('', studio)
                            default_name = build_destination_name(default_file_tag)
                            default_path = os.path.join(config.directory, default_name)
                            default_issue = detect_plan_issue(source_path, default_path, renamed_files)

                            if default_issue != PlanIssue.NONE:
                                file_tag = default_file_tag
                                new_sub_name = default_name
                            else:
                                try:
                                    file_tag = prompt_for_tag(studio, config.ask_fn, filename=source_filename)
                                except UserCancelledPrompt:
                                    logging.info(f"User skipped file: {source_filename}")
                                    if config.preview_mode:
                                        preview_rows.append(make_preview_row(
                                            source_path, "", "SKIP",
                                            PlanIssue.USER_ALWAYS_PROMPT,
                                            detected_group=studio,
                                            clean_generated_name=clean_generated_name,
                                        ))
                                    else:
                                        results["SKIPPED"].append(source_path)

                                    cache_per_set = config.cache_per_set_fn() if config.cache_per_set_fn else cache_per_set
                                    if cache_per_set:
                                        for skipped_path in files[source_index + 1:]:
                                            logging.info(f"User skipped studio {studio}; skipping file: {os.path.basename(skipped_path)}")
                                            if config.preview_mode:
                                                preview_rows.append(make_preview_row(
                                                    skipped_path, "", "SKIP",
                                                    PlanIssue.USER_ALWAYS_PROMPT,
                                                    detected_group=studio,
                                                ))
                                            else:
                                                results["SKIPPED"].append(skipped_path)
                                        break
                                    continue

                                cache_per_set = config.cache_per_set_fn() if config.cache_per_set_fn else cache_per_set
                                if cache_per_set:
                                    prompted_group_tag = file_tag
                                prompted_for_suffix = True
                                new_sub_name = build_destination_name(file_tag)
                    if new_sub_name is None:
                        new_sub_name = provisional_name

                    # last_generated_name preserves the pre-conflict name so the
                    # UI can distinguish "what naming generated" from "what conflict
                    # resolution changed it to" (e.g. .(1) keep-both suffixes).
                    last_generated_name = new_sub_name
                    prompt_issue = PlanIssue.USER_ALWAYS_PROMPT if prompted_for_suffix else PlanIssue.NONE
                    new_path = os.path.join(config.directory, new_sub_name)

                    # An explicit preview decision to skip is authoritative even
                    # if the on-disk conflict disappeared before execution.
                    pre_resolved = config.pre_resolved_conflicts or {}
                    pre_decision = pre_resolved.get(source_path)
                    if pre_decision is None:
                        pre_decision = pre_resolved.get(os.path.normcase(os.path.abspath(source_path)))
                    if not config.preview_mode and pre_decision and pre_decision.get("status") == "SKIP":
                        logging.info(f"SKIPPED (pre-resolved): {source_filename}")
                        results["SKIPPED"].append(source_path)
                        continue

                    # Resolve in-batch and on-disk conflicts after the name is generated.
                    new_sub_name, new_path, conflict_status, conflict_meta = resolve_conflict(
                        new_sub_name, new_path, config,
                        renamed_files, source_path, ask_cache,
                        renamed_source_by_path=renamed_source_by_path,
                        include_metadata=True,
                    )
                    issue_type = conflict_meta.get("issue_type") or PlanIssue.NONE.value
                    if issue_type == PlanIssue.NONE.value and prompt_issue != PlanIssue.NONE:
                        issue_type = prompt_issue.value

                    if conflict_status == "SKIP":
                        if config.preview_mode:
                            preview_rows.append(make_preview_row(
                                source_path, new_sub_name, "SKIP", issue_type,
                                detected_group=studio,
                                last_generated_name=last_generated_name,
                                clean_generated_name=clean_generated_name,
                                conflict_path=conflict_meta.get("conflict_path"),
                                conflicting_source_path=conflict_meta.get("conflicting_source_path"),
                            ))
                        else:
                            logging.info(f"SKIPPED (conflict policy): {source_filename}")
                            results["SKIPPED"].append(source_path)
                        continue

                    if conflict_status == "SKIP_EXISTS":
                        if config.preview_mode:
                            preview_rows.append(make_preview_row(
                                source_path, new_sub_name, "SKIP_EXISTS", issue_type,
                                detected_group=studio,
                                last_generated_name=last_generated_name,
                                clean_generated_name=clean_generated_name,
                                conflict_path=conflict_meta.get("conflict_path"),
                                conflicting_source_path=conflict_meta.get("conflicting_source_path"),
                            ))
                        else:
                            logging.info(f"SKIPPED (same file): {source_filename}")
                            results["SKIPPED"].append(source_path)
                        continue

                    # Non-skipped rows claim their destination so later rows can detect in-batch collisions.
                    renamed_files.append(new_path)
                    renamed_source_by_path[new_path] = source_path

                    if config.preview_mode:
                        preview_rows.append(make_preview_row(
                            source_path, new_sub_name, conflict_status,
                            issue_type, detected_group=studio,
                            last_generated_name=last_generated_name,
                            clean_generated_name=clean_generated_name,
                            conflict_path=conflict_meta.get("conflict_path"),
                            conflicting_source_path=conflict_meta.get("conflicting_source_path"),
                        ))
                    else:
                        # Filesystem intent is routed through one service. Preview
                        # mode never reaches this block.
                        file_mutations = config.file_mutations
                        norm_source = os.path.normpath(source_path)
                        replace_original = bool(
                            in_place
                            and norm_source in in_place
                            and norm_source != os.path.normpath(new_path)
                        )
                        row_snapshot = (config.source_row_snapshots or {}).get(source_path)
                        if replace_original and conflict_status == "OVERWRITE" and os.path.exists(new_path):
                            file_mutations.overwrite_output(
                                source_path=source_path, destination_path=new_path
                            )
                            file_mutations.recycle_file(
                                source_path, row_snapshot=row_snapshot, command_type="RECYCLE_FILE"
                            )
                            log_success(f"OVERWRITE: safely replaced existing '{new_sub_name}'")
                        elif replace_original:
                            file_mutations.replace_original(
                                source_path=source_path,
                                destination_path=new_path,
                                row_snapshot=row_snapshot,
                            )
                        elif conflict_status == "OVERWRITE" and os.path.exists(new_path):
                            file_mutations.overwrite_output(
                                source_path=source_path, destination_path=new_path
                            )
                            log_success(f"OVERWRITE: safely replaced existing '{new_sub_name}'")
                        else:
                            file_mutations.copy_output(
                                source_path=source_path, destination_path=new_path
                            )
                        exec_status = {
                            "OK": "SUCCESS", "OVERWRITE": "OVERWRITTEN", "SUFFIX": "SUFFIXED", "TAG": "TAGGED",
                        }.get(conflict_status, "SUCCESS")
                        log_success(f"{exec_status}: {source_filename} -> {new_sub_name}")
                        results["OK"].append(source_path)
                        results["ROW_META"].append({
                            "source_path": source_path,
                            "new_name": new_sub_name,
                            "conflict_status": conflict_status,
                            "issue_type": issue_type,
                            "last_generated_name": last_generated_name,
                            "clean_generated_name": clean_generated_name,
                        })

                        if replace_original:
                            log_success(f"IN-PLACE: moved original '{source_filename}' to recycle bin")
                            results["RENAMED_PATHS"].append({"source_path": source_path, "new_path": new_path})

                    if not movie_mode:
                        episode_to_subs.setdefault(episode, []).append(new_sub_name)
                        processed_episodes_in_job.add(episode)

                except (RestorableBackendUnavailableError, UnsafeFilesystemMutationError):
                    raise
                except Exception as e:
                    logging.error(f"Error processing {source_path}: {e}")
                    if config.preview_mode:
                        preview_rows.append(make_preview_row(
                            source_path, "", "FAIL",
                            PlanIssue.NO_MATCH, detected_group=studio,
                        ))
                    else:
                        results["FAIL"].append(source_path)
    except (RestorableBackendUnavailableError, UnsafeFilesystemMutationError):
        raise
    except Exception as e:
        logging.error(f"Error in rename_files: {e}")

    if config.preview_mode:
        return {"PREVIEW": preview_rows}
    return results

# === Public API for GUI ===
def run_job(config: RenameConfig | None = None, /, **kwargs):
    """Public entry point.  Accepts a RenameConfig or legacy keyword arguments."""
    if config is None:
        config = RenameConfig(**kwargs)

    if config.log_file is None:
        ap.log_dir(create=True)
        config = dataclasses.replace(config, log_file=str(ap.rename_log_file()))
    setup_logging(config.log_file)

    return rename_files(config)
