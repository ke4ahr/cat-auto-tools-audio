# cat-310dx_correlate.py -- CAT-310DX / CAT-1000 LPC and WAV Clip Correlator

**Version:** 1.1.0
**Author:** Kris Kirby, KE4AHR
**Date:** 2026-04-05

---

## Purpose

`cat-310dx_correlate.py` performs two independent byte-for-byte checksum
comparisons between CAT-310DX clips and CAT-1000 clips:

- **LPC match**: compares `.lpc` files in their respective LPC directories.
- **WAV match**: compares `.wav` files in their respective WAV directories.

Both devices use MSB-first TMS5220-compatible LPC-10 and share the same codec.
The correlation reveals whether any speech recordings were shared between the
two devices at the mastering stage. A checksum match on the LPC files is
stronger evidence of shared recordings than a WAV match (which may reflect
identical synthesis output rather than identical source material).

---

## Output Files

Both output files are written to `tmp/`.

### `tmp/cat-310dx_correlation.csv`

One row per CAT-310DX `.lpc` file. Columns:

| Column | Description |
|--------|-------------|
| `dx_filename` | CAT-310DX clip filename (e.g. `0001_One.lpc`) |
| `dx_size` | File size in bytes |
| `algo` | Hash algorithm used |
| `lpc_checksum` | Hex digest of the `.lpc` file |
| `lpc_match` | `yes` or `no` |
| `lpc_cat1000_filename` | Matched CAT-1000 `.lpc` filename, or empty |
| `lpc_cat1000_word_id` | Matched CAT-1000 word number, or empty |
| `lpc_cat1000_label` | Matched CAT-1000 label, or empty |
| `wav_checksum` | Hex digest of the `.wav` file |
| `wav_match` | `yes`, `no`, or `n/a` (if WAV dir not provided) |
| `wav_cat1000_filename` | Matched CAT-1000 `.wav` filename, or empty |
| `wav_cat1000_word_id` | Matched CAT-1000 word number, or empty |
| `wav_cat1000_label` | Matched CAT-1000 label, or empty |
| `match_type` | `both`, `lpc_only`, `wav_only`, `conflict`, or `none` |

### `tmp/cat-310dx_rename_map.json`

Maps old-style address-named basenames to label-tagged basenames for use with
`cat-310dx_rename_clips.py`. Only populated for matched clips.

```json
{
  "4FED_w01": "4FED_w01_One",
  "5030_w02": "5030_w02_Two"
}
```

Clips extracted with the updated `cat-310dx_extract.py` (which already produces
sequence-numbered names like `0001_One.lpc`) do not need renaming.

---

## Usage

```bash
# Default: sha256, standard directories, output to tmp/
python3 cat-310dx_correlate.py

# Use MD5
python3 cat-310dx_correlate.py --algo md5

# Custom LPC directories
python3 cat-310dx_correlate.py \
    --dx-dir cat-310dx_lpc_clips/ \
    --cat1000-dir cat-1000_lpc_clips/

# Custom WAV directories (enables WAV matching)
python3 cat-310dx_correlate.py \
    --310dx-wav-dir cat-310dx_wav_clips/ \
    --cat1000-wav-dir cat-1000_wav_clips/

# Quiet mode (suppress per-file progress)
python3 cat-310dx_correlate.py -q
```

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--algo {md5,sha1,sha256}` | `sha256` | Hash algorithm |
| `--dx-dir DIR` | `cat-310dx_lpc_clips` | CAT-310DX LPC clips directory |
| `--310dx-wav-dir DIR` | `cat-310dx_wav_clips` | CAT-310DX WAV clips directory |
| `--cat1000-dir DIR` | `cat-1000_lpc_clips` | CAT-1000 LPC clips directory |
| `--cat1000-wav-dir DIR` | `cat-1000_wav_clips` | CAT-1000 WAV clips directory |
| `-o FILE` | `tmp/cat-310dx_correlation.csv` | Output CSV file |
| `--map FILE` | `tmp/cat-310dx_rename_map.json` | Output JSON rename map |
| `-q` | -- | Suppress per-file progress |

---

## Interpreting Results

**`lpc_match: yes`** means the `.lpc` bytes are identical between the two
devices. This is strong evidence the clip uses the same audio recording.

**`wav_match: yes`** means the synthesized `.wav` bytes are identical. This
can result from identical LPC data or from coincidentally identical synthesis
output for different LPC data -- treat as weaker evidence than an LPC match.

**`match_type` values:**

| Value | Meaning |
|-------|---------|
| `both` | LPC match and WAV match agree on the same CAT-1000 clip |
| `lpc_only` | LPC match found; no WAV match |
| `wav_only` | WAV match found; no LPC match |
| `conflict` | LPC match and WAV match point to different CAT-1000 clips |
| `none` | No match of either type |

The CAT-1000 vocabulary covers 481 unique clips (weather terms plus numbers
0--90). The CAT-310DX vocabulary covers only numbers 0--99 with multiple
recordings per word. Matches, if any, will be confined to numeric clips in
CAT-1000 group 0 (word numbers 0--90: digits 0--9, teen words 10--20,
and decade words 30, 40, 50, 60, 70, 80, 90).

---

## Related Scripts

| Script | Purpose |
|--------|---------|
| `cat-310dx_extract.py` | Extract all CAT-310DX clips with sequence-numbered names |
| `cat-310dx_rename_clips.py` | Apply rename map to old-style address-named clips |
| `cat-1000_lpc_export.py` | Extract all CAT-1000 clips |

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the
GNU General Public License v3.0 or later.
