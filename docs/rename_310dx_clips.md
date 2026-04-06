# cat-310dx_rename_clips.py -- CAT-310DX Clip Renamer

**Version:** 1.0.0
**Author:** Kris Kirby, KE4AHR
**Date:** 2026-04-03

---

## Purpose

`cat-310dx_rename_clips.py` renames `.lpc` and `.wav` file pairs in the
CAT-310DX clip directory using the JSON rename map produced by
`cat-310dx_correlate.py`.

Its primary use is tagging old-style address-named clips (e.g. `4FED_w01.lpc`)
with the corresponding CAT-1000 label (e.g. `4FED_w01_One.lpc`) for clips that
have a checksum match. Clips that did not match are left unchanged.

> **Note:** Clips extracted with the updated `cat-310dx_extract.py` already use
> sequence-numbered names (`0001_One.lpc`) and do not need renaming.

---

## Default Behavior (Dry-Run)

By default the script prints what it *would* do without renaming anything.
Pass `--apply` to perform the renames.

```
Dry-run mode -- use --apply to rename files.
  would rename: 4FED_w01.lpc -> 4FED_w01_One.lpc
  would rename: 4FED_w01.wav -> 4FED_w01_One.wav
  would rename: 5030_w02.lpc -> 5030_w02_Two.lpc
  would rename: 5030_w02.wav -> 5030_w02_Two.wav
  ...
Would rename 42 pair(s)  (0 skipped, 0 missing).
```

---

## Usage

```bash
# Preview what would be renamed (dry-run, no changes)
python3 cat-310dx_rename_clips.py

# Apply renames
python3 cat-310dx_rename_clips.py --apply

# Rename .lpc files only, skip .wav
python3 cat-310dx_rename_clips.py --apply --no-wav

# Use a custom map and directory
python3 cat-310dx_rename_clips.py --map tmp/cat-310dx_rename_map.json \
    --dir cat-310dx_lpc_clips --apply
```

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--map FILE` | `tmp/cat-310dx_rename_map.json` | JSON rename map from `cat-310dx_correlate.py` |
| `--dir DIR` | `cat-310dx_lpc_clips` | Directory containing clips to rename |
| `--apply` | -- | Actually rename files (default: dry-run) |
| `--no-wav` | -- | Skip `.wav` files; rename `.lpc` only |
| `-q` | -- | Suppress per-file output |

---

## Idempotency

The script is safe to run multiple times:

- If the source file is absent but the destination file already exists, the
  pair is counted as **already done** rather than an error.
- If both source and destination exist, the pair is **skipped** (no overwrite).

---

## Workflow

### With new-style clips (updated `cat-310dx_extract.py`)

Re-extract to get sequence-numbered names directly -- no renaming needed:

```bash
python3 cat-310dx_extract.py --wav -o cat-310dx_lpc_clips/ --wav-dir cat-310dx_wav_clips/
```

### With old-style clips (original `cat-310dx_extract.py`)

1. Run `cat-310dx_correlate.py` to find matches and build the rename map.
2. Preview the renames.
3. Apply the renames.

```bash
python3 cat-310dx_correlate.py
python3 cat-310dx_rename_clips.py          # preview
python3 cat-310dx_rename_clips.py --apply  # apply
```

---

## Related Scripts

| Script | Purpose |
|--------|---------|
| `cat-310dx_correlate.py` | Produce the rename map via checksum correlation |
| `cat-310dx_extract.py` | Re-extract with new sequence-numbered names |

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the
GNU General Public License v3.0 or later.
