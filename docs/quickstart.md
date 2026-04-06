# CAT-1000 Firmware Analysis — Quickstart Guide

CAT-1000 repeater controller · Intel 80C186 · TMS5220 speech synthesizer

---

## Prerequisites

- Python 3.8 or later
- Python packages:
  - `numpy` — array math for LPC decode
  - `scipy` — WAV file output (`scipy.io.wavfile`)
- Both EPROM binary files in the `eprom_images/` directory:
  - `eprom_images/cat-1000-V304A_program_27C512.BIN` (64 KB program EPROM)
  - `eprom_images/cat-1000-voice_27SF512.BIN` (64 KB voice EPROM)
- `cat-1000_phrases.csv` — the 481-entry phrase index (already shipped with the project)

```sh
pip install numpy scipy
```

---

## Workflow Overview

```
 Inputs                       Scripts                      Outputs
 ──────────────────────────────────────────────────────────────────
 program_27C512.BIN ─┐
                     ├──► cat-1000_analysis.py  ──────────► .wav (speech)
 voice_27SF512.BIN  ─┤
                     │
                     └──► cat-1000_lpc_export.py ──────────► NNNN_WordText.lpc
 cat-1000_phrases.csv ────►                      ──────────► .wav (optional)
```

**Phrase numbering:** `word_number = group × 100 + phrase_id`
(groups 0, 2–9; 482 phrase index entries, 481 unique clips)

**LPC data region in voice EPROM:** `0x0642` – `0xC4C1`

---

## Quick Start

### 1. Verify your files

```sh
ls -lh eprom_images/cat-1000-V304A_program_27C512.BIN \
        eprom_images/cat-1000-voice_27SF512.BIN \
        cat-1000_phrases.csv
```

### 2. Print a firmware summary

Displays version info, build date, and a high-level analysis of the program EPROM.

```sh
python cat-1000_analysis.py summary
```

### 3. List all voice phrases

Prints every phrase number, group, and text label recognised in the voice EPROM.

```sh
python cat-1000_analysis.py phrases
```

### 4. Speak a single phrase to a WAV file

Word numbers follow the convention `group × 100 + phrase_id`.
For example, word 302 is group 3, phrase 2.

```sh
python cat-1000_analysis.py speak -g 3 -i 2 --out output.wav
```

### 5. Render a number announcement

Synthesises how the controller would announce a numeric value.

```sh
python cat-1000_analysis.py number 146520 --out freq.wav
```

### 6. Extract all phrases to LPC files

Writes one `NNNN_WordText.lpc` file per phrase into the specified directory.

```sh
python cat-1000_analysis.py extract -o lpc_out/
```

### 7. Export LPC data via the dedicated exporter (with optional WAV output)

```sh
python3 cat-1000_lpc_export.py cat-1000_clips.csv --wav \
    -o cat-1000_lpc_clips/ --wav-dir cat-1000_wav_clips/
```

### 8. Regenerate the starter CSV

```sh
python cat-1000_lpc_export.py --dump-csv
```

---

## Common Commands

| Command | Purpose |
|---|---|
| `cat-1000_analysis.py summary` | High-level firmware summary |
| `cat-1000_analysis.py phrases` | List all 481 voice phrases |
| `cat-1000_analysis.py extract [-o DIR]` | Dump all phrases to `.lpc` files |
| `cat-1000_analysis.py speak -g G -i ID --out FILE` | Synthesise phrase `G×100+ID` to WAV |
| `cat-1000_analysis.py number N [--out FILE]` | Announce number `N` as WAV |
| `cat-1000_lpc_export.py --dump-csv` | Generate starter `cat-1000_phrases.csv` |
| `cat-1000_lpc_export.py CSV [--wav] [-o DIR]` | Export phrases; optionally decode to WAV |

---

## Tips & Troubleshooting

### No audio / silent WAV file

The TMS5220 LPC decoder expects energy/pitch frames to be non-zero. If you hear
silence, check that the voice EPROM offset is correct (`0x0642`) and that the
binary file is not byte-swapped or truncated (expected size: exactly 65536 bytes).

### CSV column errors from `cat-1000_lpc_export.py`

The primary input CSV is `cat-1000_clips.csv` -- no header row, four positional
fields: `NNNN,"Label",0xSTART,0xEND`. A 5th field `"undocumented"` is present on
9 rows. Run `--dump-csv` to regenerate this file from the voice EPROM, then
cross-reference or extend it.

### `ModuleNotFoundError: numpy` or `scipy`

```sh
pip install numpy scipy
```

If you are working inside a virtual environment, make sure it is activated before
running the scripts.

### Wrong firmware version

The scripts target firmware revision V3.04A
(`eprom_images/cat-1000-V304A_program_27C512.BIN`). Other revisions may work but phrase table
offsets could differ.

### Inspecting LPC frames manually

Each `.lpc` file is a raw stream of bit-packed TMS5220 LPC-10 frames. A frame is
either a stop frame (4 bits), a silence/repeat frame, or a voiced/unvoiced frame
of 50–54 bits. Use a hex editor or a purpose-built TMS5220 frame parser to inspect
individual frames.

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the GNU General Public License v3.0 or later. You may redistribute and/or modify it under the terms of the GNU GPL as published by the Free Software Foundation. See <https://www.gnu.org/licenses/> for details.
