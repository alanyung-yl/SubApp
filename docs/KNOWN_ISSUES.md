# Known Issues

## File Operation Reliability
- `send2trash` is not atomic and can fail due to permissions or file locks.

## Episode Detection / Parsing
- Fallback regex matching can misclassify movie years as episode numbers.
- Example: `Movie.Title.2023.1080p.BluRay.x264.DTS.mkv` can incorrectly yield episode `2023`.


problem when theres pre existing sub on dest and group suffix is false.
 - overwrite will apply to all groups as all group default to use base filename.

 as of now the orphaned dialog that add orphaned files from destination(target) folder does not really respect the user selection on the "Subtitle Format (Source):" combobox.

it will now only show enabled ext via `enabled_subtitle_extensions`, and it is working fine when user is on "ALL" mode on combobox, but on other ext and on "auto" it will still show the complete "enabled_subtitle_extensions" instead i want to a