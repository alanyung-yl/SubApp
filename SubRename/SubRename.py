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
from send2trash import send2trash
from pathvalidate import sanitize_filename


class ConflictPolicy(Enum):
    """How to handle destination-file conflicts (file already exists at target path)."""
    ASK = "ASK"
    SKIP = "SKIP"
    OVERWRITE = "OVERWRITE"
    SUFFIX = "SUFFIX"


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

def prompt_for_tag(existing_tags, studio_name, ask_fn=None, context="conflict", filename=None):
    """
    Prompt user for a custom tag.
    """
    def ask(p, f=None):
        if ask_fn:
            try:
                return ask_fn(p, f)
            except TypeError:
                return ask_fn(p)
        else:
            return input(p).strip()
    
    if context == "conflict":
        prompt = f"Found existing subtitles. Enter a unique suffix for {studio_name}"
    elif context == "always_prompt":
        prompt = f"Enter a custom suffix for {studio_name}"
    elif context == "multi_set":
        prompt = f"Found multiple subtitles for the same episode. Enter a unique suffix for {studio_name}"
    else:
        prompt = f"Enter a unique suffix for {studio_name}"

    while True:
        resp = ask(prompt, filename)
        if resp is None:
            raise UserCancelledPrompt
        new_tag = (resp or studio_name).strip('.')
        new_tag = sanitize_filename(new_tag, platform="auto") if new_tag else ''
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

def resolve_conflict(
    new_sub_name: str,
    new_path: str,
    config: RenameConfig,
    renamed_files: list[str],
    source_path: str,
    ask_cache: dict,
) -> Tuple[str, str, str]:
    """
    Resolve a destination-file conflict.

    Returns (new_sub_name, new_path, status) where status is one of:
        "OK", "OVERWRITE", "SUFFIX", "SKIP", "SKIP_EXISTS"
    """
    src_norm = os.path.normcase(os.path.abspath(source_path))
    dst_norm = os.path.normcase(os.path.abspath(new_path))
    if src_norm == dst_norm:
        return new_sub_name, new_path, "SKIP_EXISTS"

    in_batch_collision = new_path in renamed_files
    on_disk_collision = os.path.exists(new_path)

    if not in_batch_collision and not on_disk_collision:
        return new_sub_name, new_path, "OK"

    # In-batch collisions always suffix regardless of policy
    if in_batch_collision:
        base_name, ext = os.path.splitext(new_sub_name)
        new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
        new_path = os.path.join(config.directory, new_sub_name)
        return new_sub_name, new_path, "SUFFIX"

    # Reuse preview conflict decisions in the actual run (avoid re-prompting)
    if on_disk_collision:
        pre = (config.pre_resolved_conflicts or {}).get(source_path)
        if pre:
            pre_status = pre.get("status")
            pre_name = (pre.get("new_name") or "").strip()

            if pre_status == "OVERWRITE":
                return new_sub_name, new_path, "OVERWRITE"

            if pre_status in ("SUFFIX", "TAG"):
                if pre_name:
                    pre_name = os.path.basename(pre_name)
                    pre_path = os.path.join(config.directory, pre_name)
                    if not (os.path.exists(pre_path) or pre_path in renamed_files):
                        return pre_name, pre_path, pre_status
                base_name, ext = os.path.splitext(new_sub_name)
                fallback = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
                return fallback, os.path.join(config.directory, fallback), "SUFFIX"

            if pre_status == "SKIP":
                return new_sub_name, new_path, "SKIP"

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
            return new_sub_name, new_path, "TAG"

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
                return new_sub_name, new_path, "OVERWRITE"
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
                return new_sub_name, new_path, ("TAG" if action == "TAG" and alt_path else "SUFFIX")
            else:
                return new_sub_name, new_path, "SKIP"
        else:
            # No resolver function, fall back to SUFFIX
            base_name, ext = os.path.splitext(new_sub_name)
            new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
            new_path = os.path.join(config.directory, new_sub_name)
            return new_sub_name, new_path, "SUFFIX"

    elif policy == ConflictPolicy.SKIP:
        return new_sub_name, new_path, "SKIP"

    elif policy == ConflictPolicy.OVERWRITE:
        return new_sub_name, new_path, "OVERWRITE"

    elif policy == ConflictPolicy.SUFFIX:
        base_name, ext = os.path.splitext(new_sub_name)
        new_sub_name = generate_suffixed_path(base_name, ext, config.directory, renamed_files)
        new_path = os.path.join(config.directory, new_sub_name)
        return new_sub_name, new_path, "SUFFIX"

    return new_sub_name, new_path, "OK"


def rename_files(config: RenameConfig):
    """
    For each subtitle, find matching video(s) for the episode, check existing subtitle files for the same episode, 
    and determine the new subtitle filename with appropriate tag.
    If multiple subtitle files are found for the same episode, prompt for a unique tag.
    If no matching video is found, skip the subtitle.
    If multiple video files are found for the same episode, prompt for the correct video file.
    If the subtitle file already exists, skip it.
    If the subtitle file is already in the destination folder, skip it. 
    
    Returns a dict: {"OK": [...], "FAIL": [...], "SKIPPED": [...]} where each list contains file paths
    """
    renamed_files = []
    results = {"OK": [], "FAIL": [], "SKIPPED": [], "RENAMED_PATHS": []}
    in_place = {os.path.normpath(p) for p in (config.rename_in_place_sources or set())}
    preview_rows: list[dict] = []
    ask_cache: dict = {}  # Per-run ASK cache for "Apply to all conflicts"
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
            # Build simple video and subtitle lists
            video_files = target_files
            subtitle_files = [os.path.basename(s) for s in source_filenames]
            logging.info(f"Found {len(video_files)} video files and {len(subtitle_files)} subtitle files")
        else:
            # Series, build episode-to-video and episode-to-subs dicts
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
        cancelled_studios = set()  # Track studios cancelled when cache_per_set is True
        processed_episodes_in_job = set()  # Track episodes processed in current job
        for studio, files in studio_to_files.items():
            # Re-read cache_per_set at the start of each studio iteration.
            # If cache_per_set_fn is provided, it reads the live setting so
            # a checkbox toggle in the previous dialog takes effect immediately here.
            ask_cache.clear()  # Per studio cache for "Apply to all conflicts"
            cache_per_set = config.cache_per_set_fn() if config.cache_per_set_fn else config.cache_per_set

            if cache_per_set and studio in cancelled_studios:
                logging.info(f"Skipping studio {studio} (previously skipped)")
                continue
            tag = None
            studio_prompted = False
            if cache_per_set and studio in studio_tags:
                tag = studio_tags[studio]
            else:
                # Gather all existing tags for this studio's files
                existing_tags = set()
                default_name_conflict = False
                
                # Check for conflicts: existing files in target directory + episodes already processed in this job
                for s in files:
                    if movie_mode:
                        # Movie mode: Check for conflicts with any video file
                        subtitle_name = os.path.basename(s)
                        for video_file in video_files:
                            video_base = os.path.splitext(video_file)[0]
                            
                            # Check for existing subtitle files in target directory
                            potential_subtitle_name = f"{video_base}{config.src_ext}"
                            if os.path.exists(os.path.join(config.directory, potential_subtitle_name)):
                                default_name_conflict = True
                            
                            # Also check for existing subtitle files with tags
                            for existing_file in all_files:
                                if existing_file.endswith(config.src_ext):
                                    existing_base = os.path.splitext(existing_file)[0]
                                    if existing_base.startswith(video_base + "."):
                                        tag_part = existing_base[len(video_base) + 1:]
                                        if tag_part:
                                            existing_tags.add(tag_part)
                                            default_name_conflict = True
                    else:
                        # Series mode: Check for conflicts with episode-based matching
                        episode = extract_episode(os.path.basename(s))
                        if episode is not None:
                            # Check if this episode was already processed by a previous studio in this job
                            if episode in processed_episodes_in_job:
                                default_name_conflict = True
                            
                            video_files = episode_to_video.get(episode, [])
                            if video_files:
                                video_base = os.path.splitext(video_files[0])[0]
                                
                                # Check for existing subtitle files in target directory
                                potential_subtitle_name = f"{video_base}{config.src_ext}"
                                if os.path.exists(os.path.join(config.directory, potential_subtitle_name)):
                                    default_name_conflict = True
                                
                                # Also check for existing subtitle files with tags
                                for existing_file in all_files:
                                    if existing_file.endswith(config.src_ext):
                                        existing_base = os.path.splitext(existing_file)[0]
                                        if existing_base.startswith(video_base + "."):
                                            tag_part = existing_base[len(video_base) + 1:]
                                            if tag_part:
                                                existing_tags.add(tag_part)
                                                default_name_conflict = True
                # Use studio name as tag automatically if it's not the hardcoded fallback
                should_use_default_tag = False
                if config.use_default_tag and len(studio_to_files) > 1 and studio != DEFAULT_TAG:
                    should_use_default_tag = True
                    tag = studio
                    if cache_per_set:
                        studio_tags[studio] = tag
                
                # Naming ambiguity
                # Prompt if:
                # 1. In preview mode AND (always_prompt_tag OR conflict conditions), OR
                # 2. In rename mode AND (not config.preview_mode AND (always_prompt_tag OR conflict conditions))
                # 3. OR if use_default_tag is enabled but studio is "DEFAULT_TAG" or not found (fallback case)
                # DON'T prompt if: done preview and onto rename mode (not config.preview_mode and ui_preview_mode flag)
                should_prompt = False
                # Ambiguity/tag prompts only matter when group suffix is enabled.
                if not config.group_suffix_enabled:
                    should_prompt = False
                elif should_use_default_tag:
                    should_prompt = False
                elif config.preview_mode and (config.always_prompt_tag or default_name_conflict or (config.use_default_tag and len(studio_to_files) > 1)):
                    should_prompt = True
                elif not config.preview_mode and not config.ui_preview_mode and (config.always_prompt_tag or default_name_conflict or (config.use_default_tag and len(studio_to_files) > 1)):
                    should_prompt = True
                elif not config.preview_mode and config.ui_preview_mode and config.auto_run and (config.always_prompt_tag or default_name_conflict or (config.use_default_tag and len(studio_to_files) > 1)):
                    should_prompt = True

                if movie_mode and should_prompt:
                    has_match = False
                    for s in files:
                        try:
                            matched_video, _ = find_best_movie_match(os.path.basename(s), video_files)
                        except Exception:
                            matched_video = None
                        if matched_video is not None:
                            has_match = True
                            break
                    if not has_match:
                        should_prompt = False
                
                if should_prompt:
                    if default_name_conflict:
                        context = "conflict"
                    elif config.use_default_tag and len(studio_to_files) > 1:
                        context = "multi_set"
                    elif config.always_prompt_tag:
                        context = "always_prompt"
                    else:
                        context = "conflict"

                    if cache_per_set:  # Prompt once for the entire studio set
                        try:
                            first_filename = os.path.basename(files[0]) if files else None  # Use first file's name for studio-level prompt
                            tag = prompt_for_tag(existing_tags, studio, config.ask_fn, context=context, filename=first_filename)
                            studio_tags[studio] = tag
                            studio_prompted = True
                        except UserCancelledPrompt:
                            cancelled_studios.add(studio)
                            logging.info(f"User skipped studio {studio}; skipping all files from this studio.")
                            for source_path in files:
                                if config.preview_mode:
                                    preview_rows.append({
                                        "source_path": source_path,
                                        "new_name": "",
                                        "status": "SKIP",
                                    })
                                else: 
                                    results["SKIPPED"].append(source_path)
                            continue
                    # cache_per_set=False: tag stays None, prompt per-file in the loop below
                elif not should_use_default_tag:
                    tag = ''
                    
            studio_file_tag = None  # Track last prompted tag within per-file mode
            for source_path in files:
                try:
                    source_filename = os.path.basename(source_path)
                    
                    if movie_mode:
                        # Movie mode: Find best matching video based on filename similarity
                        matching_video, similarity_score = find_best_movie_match(source_filename, video_files)
                        if matching_video is None:
                            logging.info(f"SKIPPED: No matching video file found for subtitle: {source_filename} (best similarity: {similarity_score:.2f})")
                            if config.preview_mode:
                                preview_rows.append({
                                    "source_path": source_path,
                                    "new_name": "",
                                    "status": "FAIL",
                                })
                            else:
                                results["FAIL"].append(source_path)
                            continue
                        
                        video_file = matching_video
                        video_base = os.path.splitext(video_file)[0]
                        # print(f"Movie mode: Matched '{source_filename}' to '{video_file}' (similarity: {similarity_score:.2f})")
                        # logging.info(f"Movie mode: Matched '{source_filename}' to '{video_file}' (similarity: {similarity_score:.2f})")
                        
                    else:
                        # Series mode: Use episode-based matching
                        episode = extract_episode(source_filename)
                        if episode is None:
                            # print(f"Could not extract episode from source filename: {source_filename}")
                            logging.error(f"Could not extract episode from source filename: {source_filename}")
                            results["FAIL"].append(source_path)
                            continue
                        matching_videos = episode_to_video.get(episode, [])
                        if not matching_videos:
                            # print(f"Skipped: No matching video file found for episode {episode}")
                            logging.info(f"SKIPPED: No matching video file found for episode {episode}")
                            if config.preview_mode:
                                preview_rows.append({
                                    "source_path": source_path,
                                    "new_name": "",
                                    "status": "FAIL",
                                })
                            else:
                                results["FAIL"].append(source_path)
                            continue
                        if len(matching_videos) > 1:
                            # print(f"Warning: Multiple video files found for episode {episode}. Using the first one.")
                            logging.warning(f"Multiple video files found for episode {episode}. Using the first one.")
                        video_file = matching_videos[0]
                        video_base = os.path.splitext(video_file)[0]
                    
                    original_ext = os.path.splitext(source_filename)[1]

                    if tag is not None:
                        file_tag = tag
                        if studio_prompted:
                            live_cps = config.cache_per_set_fn() if config.cache_per_set_fn else cache_per_set
                            if not live_cps:
                                studio_file_tag = tag
                                existing_tags.add(tag)
                                tag = None
                    else:
                        live_cps = config.cache_per_set_fn() if config.cache_per_set_fn else cache_per_set
                        if live_cps and studio_file_tag is not None:
                            file_tag = studio_file_tag
                        else:
                            try:
                                file_tag = prompt_for_tag(existing_tags, studio, config.ask_fn, context=context, filename=source_filename)
                                existing_tags.add(file_tag)
                                studio_file_tag = file_tag
                            except UserCancelledPrompt:
                                logging.info(f"User skipped file: {source_filename}")
                                if config.preview_mode:
                                    preview_rows.append({
                                        "source_path": source_path,
                                        "new_name": "",
                                        "status": "SKIP",
                                    })
                                else:
                                    results["SKIPPED"].append(source_path)
                                continue

                    custom_name = (config.custom_names or {}).get(source_path, '') or ''
                    custom_name = custom_name.strip() if custom_name else ''
                    if custom_name:
                        custom_name = sanitize_filename(custom_name, platform="auto")
                        if not custom_name:
                            logging.warning(
                                f"Custom name for '{source_filename}' was entirely invalid; "
                                "falling back to auto-generated name"
                            )
                    if custom_name:
                        new_sub_name = custom_name
                    else:
                        suffix_parts = []
                        if config.group_suffix_enabled and file_tag:
                            suffix_parts.append(file_tag)
                        if config.lang_suffix_enabled:
                            lang_code = extract_language_suffix(
                                source_filename,
                                unknown_lang_action=config.unknown_lang_action,
                            )
                            if lang_code:
                                suffix_parts.append(lang_code)
                        if suffix_parts:
                            new_sub_name = f"{video_base}.{'.'.join(suffix_parts)}{original_ext}"
                        else:
                            new_sub_name = f"{video_base}{original_ext}"

                    new_path = os.path.join(config.directory, new_sub_name)
                    new_sub_name, new_path, conflict_status = resolve_conflict(
                        new_sub_name, new_path, config, renamed_files, source_path, ask_cache,
                    )

                    if conflict_status == "SKIP":
                        if config.preview_mode:
                            preview_rows.append({
                                "source_path": source_path,
                                "new_name": new_sub_name,
                                "status": "SKIP",
                            })
                        else:
                            logging.info(f"SKIPPED (conflict policy): {source_filename}")
                            results["SKIPPED"].append(source_path)
                        continue

                    if conflict_status == "SKIP_EXISTS":
                        if config.preview_mode:
                            preview_rows.append({
                                "source_path": source_path,
                                "new_name": new_sub_name,
                                "status": "SKIP_EXISTS",
                            })
                        else:
                            logging.info(f"SKIPPED (same file): {source_filename}")
                            results["SKIPPED"].append(source_path)
                        continue

                    renamed_files.append(new_path)
                    if config.preview_mode:
                        preview_rows.append({
                            "source_path": source_path,
                            "new_name": new_sub_name,
                            "status": conflict_status,  # OK, OVERWRITE, or SUFFIX
                        })
                    else:
                        if conflict_status == "OVERWRITE" and os.path.exists(new_path):
                            send2trash(os.path.normpath(new_path))
                            log_success(f"OVERWRITE: moved existing '{new_sub_name}' to recycle bin")
                        shutil.copy2(source_path, new_path)
                        exec_status = {"OK": "SUCCESS", "OVERWRITE": "OVERWRITTEN", "SUFFIX": "SUFFIXED", "TAG": "TAGGED"}.get(conflict_status, "SUCCESS")
                        log_success(f"{exec_status}: {source_filename} -> {new_sub_name}")
                        results["OK"].append(source_path)

                        norm_source = os.path.normpath(source_path)
                        if in_place and norm_source in in_place and norm_source != os.path.normpath(new_path):
                            try:
                                send2trash(norm_source)
                                log_success(f"IN-PLACE: moved original '{source_filename}' to recycle bin")
                                results["RENAMED_PATHS"].append({"source_path": source_path, "new_path": new_path})
                            except Exception as trash_err:
                                logging.warning(f"IN-PLACE: failed to trash source '{source_filename}': {trash_err}")
                    
                    if not movie_mode:
                        # Update episode_to_subs for future checks (series mode only)
                        episode_to_subs.setdefault(episode, []).append(new_sub_name)
                        processed_episodes_in_job.add(episode)  # Track this episode as processed in current job
                        
                except Exception as e:
                    logging.error(f"Error processing {source_path}: {e}")
                    results["FAIL"].append(source_path)
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
