"""
SubRename.py

This module is used to rename subtitle files so they match video files.

Algorithm (per subtitle):
1. Parse episode from subtitle name
2. Find exactly one video with same episode number in target_files
    If none → Skip & log
    If >1 → warn user & pick first / abort
3. Derive video_base = Path(video).stem
4. Determine extension tag ("", SubsPlease, …)
    If no other subtitles with that base → tag may be empty
    If another subtitle with same base already exists → prompt for a unique tag
5. Copy subtitle to target_folder / f"{video_base}{('.' + tag) if tag else ''}{subtitle_ext}"

Summary Table - Expected Behavior for Multiple Subtitle Sets:
+----------+-------------+----------------+----------------+-------------+------------------+
| Scenario | Existing sub? | Always prompt? | Should prompt? | Should skip? | Should use tag? |
+----------+-------------+----------------+----------------+-------------+------------------+
| 1st set  | No          | No             | No             | No          | No              |
| 2nd set  | Yes         | No             | Yes            | No          | Yes             |
| 1st set  | No          | Yes            | Yes            | No          | Yes             |
| 2nd set  | Yes         | Yes            | Yes            | No          | Yes             |
+----------+-------------+----------------+----------------+-------------+------------------+

Logic:
- "Existing sub?" refers to whether subtitle files for the same episodes already exist in target directory
- "Always prompt?" refers to the always_prompt_tag setting
- "Should prompt?" determines if user should be prompted for a custom tag
- "Should skip?" determines if the studio set should be skipped entirely
- "Should use tag?" determines if a tag should be applied to the renamed files
"""

import os
import re
import shutil
import logging
from dataclasses import dataclass
from typing import Optional, List, Callable
# from pathlib import Path

# === Regex Constants ===
EPISODE_REGEX = r'\b(\d{1,2})(?:v\d+)?(?:[^\d\s]*)\b'  # TODO: upgrade to more robust pattern if needed
STUDIO_REGEX = r'\[(.*?)\]'

# === Module Constants ===
DEFAULT_SRC_EXT = '.ass' # account for the combobox default
DEFAULT_DST_EXT = '.mkv'
DEFAULT_CUST_EXT = ''

@dataclass
class RenameConfig:
    """Configuration for subtitle renaming operations."""
    directory: str
    src_ext: str = DEFAULT_SRC_EXT
    dst_ext: str = DEFAULT_DST_EXT
    cust_ext: str = DEFAULT_CUST_EXT
    ask_fn: Optional[Callable[[str], str]] = None
    subtitle_files: Optional[List[str]] = None
    always_prompt_multi: bool = False
    always_prompt_tag: bool = False
    cache_per_set: bool = True
    log_file: Optional[str] = None

# === Utility Functions ===
def remove_all_extensions(filename):
    while '.' in filename:
        filename = os.path.splitext(filename)[0]
    return filename

def extract_episode(filename):
    # Match the episode number and ignore any suffix
    match = re.search(EPISODE_REGEX, filename)
    if match:
        episode = match.group(1)
        return int(episode)
    return None

# extract the studio name from the filename
def extract_studio_name(filename):
    # Extracts the studio name from a filename like '[BeanSub] Chainsaw Man [01].CHT.ass' -> 'BeanSub'
    match = re.match(STUDIO_REGEX, filename)
    if match:
        return match.group(1)
    return "SubsPlease"  # Fallback default

class UserCancelledPrompt(Exception):
    """Raised when the user cancels the custom-extension dialog."""

def prompt_for_extension(existing_extensions, studio_name, ask_fn=None, context="conflict"):
    """
    Prompt user for a custom extension/tag.
    
    Args:
        existing_extensions: Set of existing extensions to avoid conflicts
        studio_name: Default studio name to suggest
        ask_fn: Function to get user input (for GUI)
        context: Why the user is being prompted ("conflict", "always_prompt", or "multi_set")
    """
    ask = ask_fn or (lambda msg: input(msg).strip())
    
    # Choose appropriate prompt based on context
    if context == "conflict":
        prompt = f"Found existing subtitle file. Enter a unique extension (default: {studio_name}): "
    elif context == "always_prompt":
        prompt = f"Enter a custom extension for {studio_name} subtitles (default: {studio_name}): "
    elif context == "multi_set":
        prompt = f"Found multiple subtitle sets for the same episode. Enter a unique extension (default: {studio_name}): "
    else:
        prompt = f"Enter a unique extension (default: {studio_name}): "
    
    while True:
        resp = ask(prompt)
        if resp is None:
            raise UserCancelledPrompt
        new_extension = (resp or studio_name).lstrip('.')
        if new_extension == '':
            prompt = f"Empty extension. Please enter a valid extension (default: {studio_name}): "
        elif new_extension not in existing_extensions:
            return new_extension
        else:
            prompt = f"Extension '{new_extension}' already exists. Please enter a different one (default: {studio_name}): "

""" 
✘ Global cus_ext argument is ignored 
– the new path builders never append cus_ext, whereas copy_files() could add it (...{cus_ext or ''}{src_ext}).

✘ remove_all_extensions() logic lost 
– the new function no longer de-duplicates by stripping dot layers, 
so a folder that already contains Episode-01.Studio.ass is treated as if only Episode-01.ass existed.
"""
def rename_files(config: RenameConfig):
    """
    Optimized: Preprocess video and subtitle files for O(n + m) lookups.
    For each subtitle, find matching video(s) for the episode, check existing subtitle files for the same episode, and determine the new subtitle filename with appropriate extension/tag.
    If multiple subtitle files are found for the same episode, prompt for a unique extension.
    If no matching video is found, skip the subtitle.
    If multiple video files are found for the same episode, prompt for the correct video file.
    If the subtitle file already exists, skip it.
    If the subtitle file is already in the destination folder, skip it. 
    Refactored: Always use the chosen tag for all files from a studio in the batch.
    
    Returns a dict: {"OK": [...], "FAIL": [...], "CANCELLED": [...]} where each list contains file paths
    """
    logging.info(f"============================== New Job ==============================")
    results = {"OK": [], "FAIL": [], "CANCELLED": []}
    try:
        all_files = os.listdir(config.directory)
        target_files = [f for f in all_files if f.endswith(config.dst_ext)]
        src_files_in_dir = [f for f in all_files if f.endswith(config.src_ext)]

        # Use provided subtitle_files if given, else use all in dir
        source_filenames = config.subtitle_files if config.subtitle_files is not None else [os.path.join(config.directory, f) for f in src_files_in_dir]

        # Build episode-to-video and episode-to-subs dicts
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

        # Group source files by studio
        studio_to_files = {}
        for s in source_filenames:
            studio = extract_studio_name(os.path.basename(s))
            studio_to_files.setdefault(studio, []).append(s)

        studio_extensions = {}
        cancelled_studios = set()  # Track studios cancelled when cache_per_set is True
        processed_episodes_in_job = set()  # Track episodes processed in current job
        for studio, files in studio_to_files.items():
            # Prompt for tag once per studio (if not already cached)
            if config.cache_per_set and studio in cancelled_studios:
                logging.info(f"Skipping studio {studio} (previously cancelled)")
                continue
            tag = None
            if config.cache_per_set and studio in studio_extensions:
                tag = studio_extensions[studio]
            else:
                # Gather all existing extensions for this studio's files
                existing_extensions = set()
                default_name_conflict = False
                
                # Check for conflicts: existing files in target directory + episodes already processed in this job
                for s in files:
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
                            
                            # Also check for existing subtitle files with extensions
                            for existing_file in all_files:
                                if existing_file.endswith(config.src_ext):
                                    existing_base = os.path.splitext(existing_file)[0]
                                    if existing_base.startswith(video_base + "."):
                                        ext = existing_base[len(video_base) + 1:]
                                        if ext:
                                            existing_extensions.add(ext)
                                            default_name_conflict = True
                # Only prompt if always_prompt_tag is True, or if there is a default name conflict
                # Also check if we should prompt due to multiple subtitle sets per episode
                if config.always_prompt_tag or default_name_conflict or config.always_prompt_multi:
                    # Determine the context for the prompt
                    # Priority: actual conflicts > always_prompt_tag > always_prompt_multi
                    if default_name_conflict and not config.always_prompt_multi:
                        context = "conflict"
                    elif config.always_prompt_multi:
                        context = "multi_set"
                    elif config.always_prompt_tag:
                        context = "always_prompt"
                    else:
                        context = "conflict"  # fallback
                    
                    try:
                        tag = prompt_for_extension(existing_extensions, studio, config.ask_fn, context=context)
                    except UserCancelledPrompt:
                        if config.cache_per_set:
                            cancelled_studios.add(studio)
                            logging.info(f"User cancelled studio {studio}; skipping all remaining files from this studio.")
                            # Add all files from this studio to CANCELLED list
                            for source_path in files:
                                results["CANCELLED"].append(source_path)
                        else:
                            logging.info(f"User cancelled at studio {studio}; skipping these files.")
                            # Add all files from this studio to CANCELLED list
                            for source_path in files:
                                results["CANCELLED"].append(source_path)
                        continue
                    if config.cache_per_set:
                        studio_extensions[studio] = tag
                else:
                    tag = ''
            # Now process all files for this studio, always using the tag
            for source_path in files:
                try:
                    source_filename = os.path.basename(source_path)
                    episode = extract_episode(source_filename)
                    if episode is None:
                        print(f"Could not extract episode from source filename: {source_filename}")
                        logging.error(f"Could not extract episode from source filename: {source_filename}")
                        results["FAIL"].append(source_path)
                        continue
                    matching_videos = episode_to_video.get(episode, [])
                    if not matching_videos:
                        print(f"Skipped: No matching video file found for episode {episode}")
                        logging.info(f"Skipped: No matching video file found for episode {episode}")
                        results["FAIL"].append(source_path)
                        continue
                    if len(matching_videos) > 1:
                        print(f"Warning: Multiple video files found for episode {episode}. Using the first one.")
                        logging.warning(f"Multiple video files found for episode {episode}. Using the first one.")
                    video_file = matching_videos[0]
                    video_base = os.path.splitext(video_file)[0]
                    # Always use the tag for this studio
                    if tag == '':
                        new_sub_name = f"{video_base}{config.src_ext}"
                    else:
                        new_sub_name = f"{video_base}.{tag}{config.src_ext}"

                    new_path = os.path.join(config.directory, new_sub_name)
                    if os.path.exists(new_path):
                        print(f"File {new_sub_name} already exists – skipping.")
                        logging.info(f"File {new_sub_name} already exists – skipping.")
                        results["FAIL"].append(source_path)
                        continue
                    shutil.copy2(source_path, new_path)
                    print(f"Copied: {source_filename} -> {new_sub_name}")
                    logging.info(f"Copied: {source_filename} -> {new_sub_name}")
                    results["OK"].append(source_path)
                    # Update episode_to_subs for future checks
                    episode_to_subs.setdefault(episode, []).append(new_sub_name)
                    # Track this episode as processed in current job
                    processed_episodes_in_job.add(episode)
                except Exception as e:
                    logging.error(f"Error processing {source_path}: {e}")
                    results["FAIL"].append(source_path)
    except Exception as e:
        print(f"Error in rename_files: {e}")
    
    return results

def logging_files(log_file):
    if not logging.getLogger().hasHandlers():
        logging.basicConfig(filename=log_file, level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# === Public API for GUI ===
def run_job(
    directory: str,
    src_ext: str = DEFAULT_SRC_EXT,
    dst_ext: str = DEFAULT_DST_EXT,
    cust_ext: str = DEFAULT_CUST_EXT,
    log_file: str | None = None,
    ask_fn=None,
    subtitle_files: list[str] | None = None,
    always_prompt_multi: bool = False,
    always_prompt_tag: bool = False,
    cache_per_set: bool = True,          
    # overwrite: bool = False,        
    # min_file_size: int = 100 * 1024,
    # dry_run: bool = False,          
):
    """
    Rename subtitles so they match video files.

    Returns a dict:  {"OK": [...], "FAIL": [...]}
    """
    if log_file is None:
        log_file = os.path.join(os.path.dirname(__file__), "rename_log.txt")  # Default log file in the same directory
    logging_files(log_file)

    config = RenameConfig(
        directory=directory,
        src_ext=src_ext,
        dst_ext=dst_ext,
        cust_ext=cust_ext,
        ask_fn=ask_fn,
        subtitle_files=subtitle_files,
        always_prompt_multi=always_prompt_multi,
        always_prompt_tag=always_prompt_tag,
        cache_per_set=cache_per_set,
        log_file=log_file
    )

    return rename_files(config)
# No CLI code, only library for GUI use