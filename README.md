# CAT-1000 LPC Speech Extraction

Extract, synthesize, and label all spoken phrases from the CAT-1000 repeater controller's voice EPROM.

**Device:** CAT-1000 Repeater Controller, firmware V3.04A (1998)
**Speech chip:** TI TSP53C30 (TMS5220-compatible LPC-10 vocoder)
**Result:** 481 labelled phrases as .lpc (raw bitstream) and .wav (synthesized audio) — vocabulary complete

---

## Quick Start

```bash
# Install PyTI_LPC_CMD into a local subdirectory (required for --wav only)
bash INSTALL_PyTI.sh
# Clones https://github.com/ke4ahr/PyTI_LPC_CMD into PyTI_LPC_CMD/

# Export LPC files to cat-1000_lpc_clips/ (no PyTI dependency)
python3 cat-1000_lpc_export.py cat-1000_clips.csv -o cat-1000_lpc_clips/

# Export LPC + WAV to separate directories (requires PyTI_LPC_CMD)
python3 cat-1000_lpc_export.py cat-1000_clips.csv --wav \
    -o cat-1000_lpc_clips/ --wav-dir cat-1000_wav_clips/
```

**Requires:** Python 3.8+. PyTI_LPC_CMD (for `--wav`) cloned into `PyTI_LPC_CMD/` by `INSTALL_PyTI.sh`.


---

## Files

| File | Description |
|------|-------------|
| `eprom_images/cat-1000-voice_27SF512.BIN` | Voice EPROM image (64 KB) — primary input |
| `eprom_images/cat-1000-V304A_program_27C512.BIN` | Program EPROM image (64 KB) — reference |
| `cat-1000_clips.csv` | Primary phrase table (no header): `NNN,"Label",0xSTART,0xEND` -- 481 entries, 3-digit zero-padded word number. Input to `cat-1000_lpc_export.py`; read/rewritten by `fix_csv_addresses.py`. |
| `cat-1000_phrases.csv` | Alternate phrase table (has header row): `word_number,word_text,start_address,end_address,notes` -- same 481 phrases, sorted alphabetically by label. Used by `cat-1000_lpc_export.py --dump-csv` to populate word labels when regenerating `cat-1000_clips.csv`. |
| `cat-1000_lpc_export.py` | Main export script |
| `cat-1000_analysis.py` | EPROM analysis utilities |
| `cat-1000_lpc_repack.py` | Repack modified LPC data into EPROM format |
| `INSTALL_PyTI.sh` | Clone PyTI_LPC_CMD into `PyTI_LPC_CMD/` subdirectory |
| `PyTI_LPC_CMD/` | PyTI_LPC_CMD synthesizer (cloned by INSTALL_PyTI.sh; not in repo) |
| `cat-1000_lpc_clips/` | Output: `NNNN_Label.lpc` for each phrase |
| `cat-1000_wav_clips/` | Output: `NNNN_Label.wav` for each phrase (synthesized) |
| `build_docs.py` | Build all documentation PDFs from source |
| `docs/` | Technical documentation |

---

## Voice EPROM Structure

```
0x0000–0x009E  8086 dispatch routine
0x009F–0x0641  Phrase index (9 groups × N entries, 3 bytes each)
0x0642–0xC4C1  TMS5220 LPC bit-packed speech data (~49 KB)
```

The phrase index is self-contained in the voice EPROM. The program EPROM calls `CALL FAR F000:0000` with `AH=group_id`, `AL=phrase_id`; the voice EPROM dispatch code performs a linear search and returns `BX=start_offset`.

Word numbering: `word_number = group * 100 + phrase_id`

---

## Phrase Boundary Method

End addresses are **not stored** in the EPROM index. Each phrase ends where the next phrase (sorted by start address) begins. This is authoritative and used exclusively in `cat-1000_clips.csv`.

```python
sorted_starts = sorted(set(addr for each index entry))
end_of_phrase = sorted_starts[i+1]   # or 0xC4C1 for the last phrase
```

---

## Bit Direction

LPC bytes are stored native MSB-first: bit 7 of each byte is the MSB of the first LPC parameter field. Read bytes in EPROM order, process each byte from bit 7 down to bit 0. **Do not bit-reverse before parsing.**

The export script handles a double-reversal internally to match PyTI's synthesis convention — this is intentional and correct.

---

## CSV Format

`cat-1000_clips.csv` (no header row):
```
NNN,"Label",0xSTART,0xEND
```

`cat-1000_phrases.csv` (has header):
```
word_number,word_text,start_address,end_address,notes
```

All addresses are verified against the voice EPROM phrase index as of 2026-04-02.

---

## Export Script Options

```
python3 cat-1000_lpc_export.py [CSV] [options]

  --wav           Synthesize WAV files (requires PyTI_LPC_CMD)
  -o DIR          Output directory for .lpc files (default: lpc_phrases/)
  --wav-dir DIR   Separate output directory for .wav files (default: same as -o)
  --voice FILE    Voice EPROM image (default: eprom_images/cat-1000-voice_27SF512.BIN)
  -q              Suppress per-file output
  --dump-csv      Print phrase table as CSV to stdout (no input CSV needed)
```

---

## CAT-310DX — Digital Weather Station a.k.a. Road Monitor

**Device:** CAT-310DX V1.00 (C)1998, checksum 7A69
**CPU:** Intel 8052 (MCS-51). **Speech chip:** TSP53C30 (TMS5220-compatible) at MOVX 0xC000.
**Result:** 414 unique LPC clips extracted and synthesized — numbers 0–99, 4–9 recordings each

```bash
# Extract all 414 clips (LPC + WAV)
python3 cat-310dx_extract.py --wav
# LPC: cat-310dx_lpc_clips/  WAV: cat-310dx_wav_clips/  CSV: cat-310dx_clips.csv
```

| File | Description |
|------|-------------|
| `eprom_images/CAT-310DX_V1-00_1998_7A69.BIN` | Unified firmware + speech EPROM (64 KB) |
| `cat-310dx_extract.py` | Extraction script -- reads dispatch table at 0x4AC3, writes sequence-numbered clips |
| `cat-310dx_correlate.py` | Correlate 310DX clips against CAT-1000 clips by checksum |
| `cat-310dx_rename_clips.py` | Rename old-style address-named clips using correlation map |
| `cat-310dx_synth.py` | Bit-direction validation tool |
| `cat-310dx_lpc_clips/` | Output: 414 × .lpc clips |
| `cat-310dx_wav_clips/` | Output: 414 × .wav clips (synthesized) |
| `cat-310dx_clips.csv` | Phrase table: seq number, label, start/end address (414 entries) |
| `docs/cat310dx_re_report.md` | Full reverse engineering report |
| `docs/cat310dx_analysis.md` | Technical reference: memory map, ISR disassembly, dispatch table |

**LPC format is identical to CAT-1000** (MSB-first TMS5220 codec). The same synthesis pipeline handles both devices.

```bash
# Re-extract all 414 clips with sequence-numbered names
python3 cat-310dx_extract.py --wav
# LPC: cat-310dx_lpc_clips/  WAV: cat-310dx_wav_clips/

# Correlate against CAT-1000 to find byte-identical clips
python3 cat-310dx_correlate.py
```

---

## Documentation

- [`docs/cat-1000_re_report.md`](docs/cat-1000_re_report.md) — CAT-1000 reverse engineering report
- [`docs/cat310dx_re_report.md`](docs/cat310dx_re_report.md) — CAT-310DX reverse engineering report
- [`docs/lpc_extraction_process.md`](docs/lpc_extraction_process.md) — Complete LPC extraction process: EPROM layout, phrase index, bitstream format, synthesis pipeline
- [`docs/handoff.md`](docs/handoff.md) — Project state and known pitfalls for both devices
- [`docs/csv_address_audit.md`](docs/csv_address_audit.md) — History of the CAT-1000 phrase boundary audit and corrections
- [`docs/activity_log.md`](docs/activity_log.md) — Work log 2026-03-23 to 2026-04-05

## Building Documentation

```bash
python3 build_docs.py
```

Compiles all LaTeX papers, TikZ diagrams, Graphviz diagrams, and Markdown documents to PDF. Requires `pdflatex`, `xelatex`, `dot` (Graphviz), and `pandoc`. See [`build_docs.md`](build_docs.md) for details.

---

## Verification

After export, `cat-1000_wav_clips/0220_Affirmative.wav` should be ~875 ms and sound clearly like "Affirmative."
`cat-1000_lpc_clips/0220_Affirmative.lpc` should be 180 bytes.

---

## Acknowledgements

The author wishes to thank the following: Al Preuss W4MGK (SK), Ron Eichholtz WB4UFA, Harlie Henson KB4CRG, Brian Kirby KD4FM, Russ Harper, Marion "Gib" Gibson W4LHR (SK), Leigh Bartlow WD4CPF, The Huntsville Amateur Radio Club (K4BFT), Ron Shaffer W4VM (SK), Ralph Hogan W4XE (ex: WB4TUR), The North Alabama Repeater Association (NARA), Don Hediger N4MSN, The Marshall Space Flight Center Amateur Radio Club NN4SA (ex: WA4NZD), Robert Meister WA1MIK (SK), Mike Morris WA6ILQ, Josh Hatton W4ZZK, Kevin K. Custer W3KKC, CAT-Controllers at groups.io, catauto at groups.io, Sig Loeb W4WOH (SK), and the various named, unnamed, pseudonymous, and anonymous members of the TI-99/4A, TI Speech, CAT Auto, and Repeater-Builder communities.

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the GNU General Public License v3.0 or later. You may redistribute and/or modify it under the terms of the GNU GPL as published by the Free Software Foundation. See <https://www.gnu.org/licenses/> for details.
