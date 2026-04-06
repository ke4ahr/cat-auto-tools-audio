# CAT-1000 LPC Export — Quick Start

## Export LPC clips (using existing CSV)

```bash
# LPC files only (no PyTI dependency)
python3 cat-1000_lpc_export.py cat-1000_clips.csv -o cat-1000_lpc_clips/

# LPC + WAV files (requires PyTI_LPC_CMD -- run INSTALL_PyTI.sh first)
python3 cat-1000_lpc_export.py cat-1000_clips.csv --wav \
    -o cat-1000_lpc_clips/ --wav-dir cat-1000_wav_clips/
```

`cat-1000_clips.csv` already contains all 481 clip boundaries and names.
Output files are written to `cat-1000_lpc_clips/` as `NNNN_Name.lpc` (4-digit word number).
Default bit order is `--endian msb` (bit-reversed per byte; required for synthesis with PyTI_LPC_CMD).
Use `--endian lsb` for native TMS5220 D0-first bit order (direct chip streaming).

---

## Regenerate the CSV from the EPROM, then export

Use this if the EPROM image changes or you want to rebuild boundaries from scratch.

```bash
python3 cat-1000_lpc_export.py --dump-csv > cat-1000_clips.csv
python3 cat-1000_lpc_export.py cat-1000_clips.csv -o cat-1000_lpc_clips/
```

`--dump-csv` scans the voice EPROM for STOP frames to determine clip boundaries and
looks up names from `cat-1000_phrases.csv` automatically. Clips with no matching name
get a `word{N}` placeholder.

---

## Options

| Option | Default | Description |
|--------|---------|-------------|
| `--endian msb` | default | Bit-reversed per byte (for synthesis tools expecting MSB-first) |
| `--endian lsb` | -- | Native TMS5220 D0-first bit order (for direct chip streaming) |
| `-o DIR` | `lpc_phrases` | Output directory for .lpc files |
| `--wav-dir DIR` | same as `-o` | Separate output directory for .wav files |
| `--wav` | off | Also synthesize each clip to a WAV file |
| `-q` | off | Suppress per-file output lines |
| `--voice FILE` | `eprom_images/cat-1000-voice_27SF512.BIN` | Voice EPROM image |

---

## CAT-310DX -- Extract all LPC clips

```bash
python3 cat-310dx_extract.py --wav
```

Reads the word address dispatch table at 0x4AC3, derives 414 unique clip boundaries,
and writes `.lpc` files to `cat-310dx_lpc_clips/` and `.wav` files to `cat-310dx_wav_clips/`.

Expected: `Exported 414 clip(s).`

To extract LPC files only (skip WAV synthesis):

```bash
python3 cat-310dx_extract.py
```

File naming: `{seq}_{Label}.lpc` / `.wav` (e.g., `0000_Zero.lpc`, `0600_Sixty.lpc`).
Word ID mapping: hex value = decimal number spoken (0x00=zero, 0x63=ninety-nine).

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the GNU General Public License v3.0 or later. You may redistribute and/or modify it under the terms of the GNU GPL as published by the Free Software Foundation. See <https://www.gnu.org/licenses/> for details.
