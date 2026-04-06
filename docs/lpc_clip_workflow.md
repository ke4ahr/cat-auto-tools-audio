# LPC Clip Extraction and Cross-Device Correlation Workflow

**Devices:** CAT-1000 (Intel 80C186) and CAT-310DX V1.00 (Intel 8052)
**Speech chip:** TI TSP53C30 (TMS5220-compatible LPC-10)
**Author:** Kris Kirby, KE4AHR
**Date:** 2026-04-05

---

## Overview

This document traces the complete pipeline from raw EPROM images to a
cross-device correlation table. Each step is described in four parts:

- **Source** -- the data or artifact consumed by this step and where it originates
- **Flow** -- the transformation or algorithm that acts on the data
- **User** -- the command or action the user performs
- **Output** -- the artifact or data structure produced by this step

The pipeline covers seven stages:

1. Locate LPC speech data in the CAT-1000 voice EPROM
2. Derive phrase boundaries from the CAT-1000 phrase index
3. Export labeled LPC and WAV files for the CAT-1000
4. Locate LPC speech data in the CAT-310DX unified EPROM
5. Derive clip boundaries from the CAT-310DX dispatch table
6. Export labeled LPC and WAV files for the CAT-310DX
7. Correlate CAT-310DX clips against CAT-1000 clips by checksum

---

## Step 1 -- Locate LPC speech data in the CAT-1000 voice EPROM

### Source

Two 64 KB EPROM images produced by reading the chips with a standard
parallel EPROM programmer:

- `eprom_images/cat-1000-V304A_program_27C512.BIN` -- 80C186 firmware (not needed for
  LPC extraction)
- `eprom_images/cat-1000-voice_27SF512.BIN` -- voice data chip; this is the only file
  needed for all LPC work

The voice EPROM is self-contained. It begins with a short 8086 dispatch
routine at offset 0x0000, followed immediately by phrase index tables.
LPC speech data occupies the bulk of the chip:

```
0x0000-0x009E  8086 dispatch routine
0x009F-0x0641  Phrase index tables (9 groups, 482 entries)
0x0642-0xC4C1  TMS5220 LPC-10 bit-packed speech data (~49 KB)
```

### Flow

The phrase index begins at offset 0x009F and is divided into nine groups
(groups 0, 2-9; there is no group 1). Each entry is 3 bytes:

```
[phrase_id]  [offset_lo]  [offset_hi]
```

The 16-bit little-endian offset is an absolute byte address within the
voice EPROM pointing to the start of the phrase's LPC data. Word number
is computed as `group * 100 + phrase_id`.

Group table locations:

| Group | Table offset | Entry count |
|-------|-------------|-------------|
| 0 | 0x009F | 28 |
| 2 | 0x00F3 | 79 |
| 3 | 0x01E0 | 54 |
| 4 | 0x0282 | 43 |
| 5 | 0x0303 | 50 |
| 6 | 0x0399 | 65 |
| 7 | 0x0459 | 61 |
| 8 | 0x0510 | 60 |
| 9 | 0x05C4 | 42 |

Total: 482 entries; 481 unique phrases (word 600 has a duplicate entry
at 0x9041; the hardware uses only the first entry at 0x6C26).

### User

Read the voice EPROM into memory and walk every group table to collect
all `(word_number, start_address)` pairs:

```python
PHRASE_GROUPS = {
    0:(0x009F,28), 2:(0x00F3,79), 3:(0x01E0,54), 4:(0x0282,43),
    5:(0x0303,50), 6:(0x0399,65), 7:(0x0459,61), 8:(0x0510,60),
    9:(0x05C4,42),
}

with open('eprom_images/cat-1000-voice_27SF512.BIN', 'rb') as f:
    voice = f.read()

entries = []
for grp, (off, cnt) in PHRASE_GROUPS.items():
    for i in range(cnt):
        base = off + i * 3
        pid  = voice[base]
        addr = voice[base+1] | (voice[base+2] << 8)
        entries.append((grp * 100 + pid, addr))
```

### Output

A list of 482 `(word_number, start_address)` tuples covering all 481
unique phrases. This list is the input to Step 2.

---

## Step 2 -- Derive phrase boundaries from the CAT-1000 phrase index

### Source

The list of 482 `(word_number, start_address)` tuples from Step 1 and
the voice EPROM bytes. The phrase index stores **start addresses only** --
end addresses are not recorded anywhere in the EPROM.

### Flow

Sort all unique start addresses in ascending order. Each phrase ends
where the next phrase in address order begins. The last phrase ends at
the known data region boundary 0xC4C1, confirmed by verifying that
word 982 "Good Evening" has its STOP frame at exactly that position.

```python
LPC_DATA_END = 0xC4C1   # inclusive end of speech data region

sorted_starts = sorted(set(a for _, a in entries if 0x0642 <= a < 0x10000))

def index_end(start):
    i = sorted_starts.index(start)
    return sorted_starts[i+1] if i+1 < len(sorted_starts) else LPC_DATA_END
```

Each phrase's byte slice is `voice[start : index_end(start)]`.

**Word 600 duplicate:** Both phrase index entries for word 600 point to
different ROM addresses (0x6C26 and 0x9041). Only the first (0x6C26) is
used. The second address (0x9041) belongs to word 700 "Pull" and its end
address is derived from the next entry in sorted order.

### User

Build a dictionary mapping each start address to its computed end address,
then apply it to produce a `(word_number, label, start, end)` table.
The script `fix_csv_addresses.py` automates verification and correction
of an existing CSV against the EPROM-derived boundaries:

```python
correct_end = {addr: index_end(addr) for addr in sorted_starts}
for row in csv.reader(open('cat-1000_clips.csv')):
    start = int(row[2], 16)
    if start in correct_end and int(row[3], 16) != correct_end[start]:
        print(f"Word {row[0]}: end {row[3]} should be {hex(correct_end[start])}")
```

### Output

A complete phrase boundary table: one row per phrase with fields
`(word_number, label, start_address, end_address)`. This table is
persisted as `cat-1000_clips.csv` and is the input to Step 3.

```
"NNNN","Label","0xSTART","0xEND"
"NNNN","Label","0xSTART","0xEND","undocumented"
```

Nine phrases carry a fifth field `"undocumented"` -- they appear in the
phrase index but not in the CAT-1000 vocabulary reference. All nine are
confirmed by listening: Comm(299), File(377), Fall(393), Heat(457),
Index(486), Percent(703), Pressure(704), Today's(852), Windchill(910).

---

## Step 3 -- Export labeled LPC and WAV files for the CAT-1000

### Source

- `eprom_images/cat-1000-voice_27SF512.BIN` -- voice EPROM image
- `cat-1000_clips.csv` -- phrase boundary table from Step 2
- `PyTI_LPC_CMD` -- TMS5220 synthesis library, installed via
  `INSTALL_PyTI.sh`

### Flow

For each CSV row the export script performs two operations:

**LPC extraction:** Read `voice[start:end]` -- the raw LPC bytes in
native MSB-first order -- and write that slice directly to
`NNNN_Label.lpc`. No transformation is applied.

**WAV synthesis** (when `--wav` is specified): Call
`render_phrase_to_pcm(lpc_native)`:

1. Bit-reverse each byte via `_BIT_REVERSE` lookup table.
2. Pass reversed bytes to `LPCSynthesizer.synthesize(data, TMS5220_PARAMS)`.
3. PyTI internally bit-reverses each byte again.
4. Net effect: PyTI processes the original native bytes MSB-first.
5. Apply 75% gain, scale to int16, write 8000 Hz mono 16-bit WAV.

The double reversal is intentional. PyTI's internal convention expects
bytes pre-reversed by the caller; the two reversals cancel so native
MSB-first data is processed correctly. Do not bit-reverse EPROM bytes
before passing them to `render_phrase_to_pcm` -- the function handles
the double-reversal pattern itself.

### User

Install PyTI once, then export:

```bash
bash INSTALL_PyTI.sh

python3 cat-1000_lpc_export.py cat-1000_clips.csv --wav \
    -o cat-1000_lpc_clips/ --wav-dir cat-1000_wav_clips/
# Expected: "Exported 481 phrase(s), 0 skipped."
```

Verify the baseline clip after export:

```bash
ls -la cat-1000_lpc_clips/0220_Affirmative.*
# .lpc = 180 bytes   .wav = 14044 bytes
# Play and confirm it sounds like "Affirmative" (~875 ms)
```

### Output

```
cat-1000_lpc_clips/    481 .lpc files  (0000_Zero.lpc ... 0982_Good_Evening.lpc)
cat-1000_wav_clips/    481 .wav files  (same stem names, .wav extension)
```

Each `.lpc` file is a raw TMS5220 MSB-first bitstream slice. Each `.wav`
file is 8000 Hz mono 16-bit PCM. These directories are the input to
Step 7.

---

## Step 4 -- Locate LPC speech data in the CAT-310DX unified EPROM

### Source

A single 64 KB EPROM image: `eprom_images/CAT-310DX_V1-00_1998_7A69.BIN`.

Unlike the CAT-1000, the CAT-310DX packs 8052 firmware and LPC speech
data into one chip. The firmware occupies roughly the first 20 KB; the
LPC data occupies the upper 40 KB. The full memory map:

```
0x0000-0x002F  8052 interrupt vectors
0x0030-0x34BF  Main firmware (clock, display, alarms, DST, sensors)
0x34C0-0x351E  Speech word sequence tables ([0x00, word_id] pairs + 0xFF)
0x351F-0x4AC2  Firmware subroutines (dispatch, streaming, XMODEM, math)
0x4AC3-0x4F9F  Word address dispatch table (415 x 3-byte entries)
0x4FA0-0xF6E3  TMS5220-compatible LPC speech data (41,820 bytes)
0xF6E4-0xF7FF  Speech control routines
0xF800-0xFA14  Serial I/O, XMODEM handler, transfer menu strings
0xFA15-0xFAF0  Reset handler and system initialization
0xFAF1-0xFFFF  Zero-filled (unused)
```

Running `eprom_scout.py` on the full image produces **364 false positives**
from the 8052 code region and a false phrase index at 0x1098 (a CJNE
dispatch table that coincidentally matches the 3-byte entry pattern).
Do not use `eprom_scout.py` for the CAT-310DX; the dispatch table at
0x4AC3 is the authoritative clip index.

### Flow

The word address dispatch table at 0x4AC3 contains 415 three-byte entries
in the format `[word_id_byte, addr_hi, addr_lo]`.

This is big-endian address order -- the opposite of the CAT-1000's
phrase index which stores `[phrase_id, lo, hi]` (little-endian). Both
tables are 3 bytes per entry but the address byte order differs.

The firmware uses 100 word IDs (0x00-0x63 = decimal 0-99). The word ID
byte value equals the decimal number spoken. 96 of the 100 word IDs
appear 4-9 times each -- different recordings of the same number for
intonation variation in concatenative speech. Total unique ROM addresses:
414 (word IDs 0x00 and 0x34 share address 0x4FA0).

The dispatch routine at 0x49E6 uses a 9-way CJNE chain to jump to a
sub-range of the dispatch table, then does a linear search for the
matching word ID.

### User

Scan the dispatch table to collect all `(word_id, rom_address)` pairs:

```python
EPROM_FILE     = 'eprom_images/CAT-310DX_V1-00_1998_7A69.BIN'
DISPATCH_START = 0x4AC3
DISPATCH_END   = 0x4FA0   # exclusive

with open(EPROM_FILE, 'rb') as f:
    rom = f.read()

entries = []
off = DISPATCH_START
while off + 3 <= DISPATCH_END:
    word_id = rom[off]
    addr    = (rom[off+1] << 8) | rom[off+2]   # big-endian
    entries.append((word_id, addr))
    off += 3
```

### Output

A list of 415 `(word_id, rom_address)` tuples. Deduplicated by address,
this yields 414 unique clip start addresses. This list is the input
to Step 5.

---

## Step 5 -- Derive CAT-310DX clip boundaries from the dispatch table

### Source

The 415 `(word_id, rom_address)` tuples from Step 4 and the EPROM bytes.
The dispatch table stores **start addresses only** -- end addresses must
be derived, as with the CAT-1000.

### Flow

Sort the 414 unique addresses in ascending order. Each clip ends where
the next clip in address order begins. The last clip ends at the known
LPC data region boundary 0xF6E4.

```python
LPC_END = 0xF6E4   # exclusive

unique_addrs = sorted(set(addr for _, addr in entries))

def clip_end(start):
    i = unique_addrs.index(start)
    return unique_addrs[i+1] if i+1 < len(unique_addrs) else LPC_END
```

Each clip's byte slice is `rom[start : clip_end(start)]`.

**Sequence numbering:** Filenames use a 4-digit zero-padded sequence
number derived from the dispatch table group structure. The dispatch
routine's 9-way CJNE chain defines nine groups; the sequence number
formula is `group_id * 100 + index_within_group`:

| Group | Table start | Entry count |
|-------|-------------|-------------|
| 0 | 0x4AC3 | 29 |
| 2 | 0x4B17 | 71 |
| 3 | 0x4BEC | 48 |
| 4 | 0x4C7C | 36 |
| 5 | 0x4CC8 | 40 |
| 6 | 0x4D5D | 55 |
| 7 | 0x4E02 | 49 |
| 8 | 0x4E95 | 54 |
| 9 | 0x4F37 | 36 |

Entry 0 of group 6 gets sequence 600, producing filename `0600_Sixty.lpc`.

The spoken-number label for each word ID is derived directly from the
word ID value: 0x00 = "Zero", 0x01 = "One", ..., 0x63 = "Ninety_Nine".
All clips sharing a word ID receive the same label regardless of which
intonation variant they represent.

### User

No separate action is required for this step. `cat-310dx_extract.py`
performs Steps 4 and 5 together: it reads the dispatch table, computes
boundaries, assigns sequence numbers and labels, and writes the output
files and CSV in a single pass.

### Output

An internal table of 414 clip records:
`(sequence_number, label, start_address, end_address, byte_slice)`.
This table drives the file writes and CSV in Step 6.

---

## Step 6 -- Export labeled LPC and WAV files for the CAT-310DX

### Source

- `eprom_images/CAT-310DX_V1-00_1998_7A69.BIN` -- unified EPROM image
- The 414-clip boundary table from Step 5 (computed internally)
- `PyTI_LPC_CMD` -- TMS5220 synthesis library (shared with Step 3)

No separate phrase CSV is required as input. The dispatch table within
the EPROM is the authoritative index.

### Flow

`cat-310dx_extract.py` applies the same synthesis pipeline as the
CAT-1000 export. The LPC bit direction is **MSB-first** -- identical to
the CAT-1000. The `render_phrase_to_pcm` function from
`cat-1000_lpc_export.py` is imported directly and used without
modification.

The shared bit direction and codec format confirm that the TSP53C30 on
the CAT-310DX is a TMS5220-compatible device. No adapter layer is needed
between the two extraction pipelines.

The CSV output uses the same `csv.QUOTE_NONNUMERIC` format as the
CAT-1000 CSV but with a 3-digit sequence number and no "undocumented"
field (all CAT-310DX words are numeric and fully documented):

```
"NNN","Label","0xSTART","0xEND"
```

### User

```bash
python3 cat-310dx_extract.py --wav
# Expected: "Exported 414 clip(s)."
```

To specify output directories explicitly:

```bash
python3 cat-310dx_extract.py --wav \
    -o cat-310dx_lpc_clips/ \
    --wav-dir cat-310dx_wav_clips/
```

### Output

```
cat-310dx_lpc_clips/    414 .lpc files  (0000_Zero.lpc ... 0952_Zero.lpc)
cat-310dx_wav_clips/    414 .wav files  (same stem names, .wav extension)
cat-310dx_clips.csv     414 rows
```

Each `.lpc` file is a raw TMS5220 MSB-first bitstream slice in the same
format as the CAT-1000 `.lpc` files. These directories are the input
to Step 7.

---

## Step 7 -- Correlate CAT-310DX clips against CAT-1000 clips by checksum

### Source

- `cat-310dx_lpc_clips/` -- 414 `.lpc` files from Step 6
- `cat-1000_lpc_clips/` -- 481 `.lpc` files from Step 3

Both sets are raw TMS5220 MSB-first LPC bitstreams. A byte-for-byte
match between a CAT-310DX clip and a CAT-1000 clip means the identical
audio recording was used in both devices -- identical ROM bytes produce
identical synthesized output.

### Flow

`cat-310dx_correlate.py` builds a hash lookup from the CAT-1000 set,
then queries it for each CAT-310DX clip:

1. Read all `.lpc` files from `cat-1000_lpc_clips/`.
2. Compute SHA-256 (default) digest of each file's raw bytes.
3. Build a dictionary: `{checksum: {filename, word_id, label}}`.
4. Read all `.lpc` files from `cat-310dx_lpc_clips/`.
5. Compute the same digest for each CAT-310DX file.
6. Look up the digest in the CAT-1000 dictionary.
7. Write one output row per CAT-310DX clip -- match or no-match.
8. For each matched clip, add a rename entry to the JSON map.

**What a match means:** Both devices sourced that recording from a shared
pool. Both chips were manufactured at or near the same time (1998) and
both use TMS5220-compatible LPC. A match confirms that TI or a contract
studio supplied common numeric vocabulary across product lines.

**What no match means:** The recording is device-specific. The CAT-1000
covers a full weather and communications vocabulary; the CAT-310DX covers
only numbers 0-99. Most CAT-1000 vocabulary has no CAT-310DX counterpart.

The rename map entry for a matched clip tags the old-style address-named
stem with the matched CAT-1000 label:

```
"0003_Three" → "0003_Three_Three"
```

Clips extracted with `cat-310dx_extract.py` already have meaningful names
and do not require renaming even when a match is found.

### User

Both export steps (Steps 3 and 6) must complete before correlating.

```bash
python3 cat-310dx_correlate.py
# Expected output:
# Hashing CAT-1000 clips (sha256)...
#   481 CAT-1000 files → 481 unique checksums
# Hashing CAT-310DX clips (sha256)...
# Correlated 414 CAT-310DX clips: N matched, M unmatched.
# Wrote tmp/cat-310dx_correlation.csv  (414 rows)
# Wrote tmp/cat-310dx_rename_map.json  (N rename entries)
```

To use MD5 (faster; sufficient for byte-level identity):

```bash
python3 cat-310dx_correlate.py --algo md5
```

To review matched clips only:

```bash
grep '"yes"' tmp/cat-310dx_correlation.csv
```

To apply rename labels to old-style address-named clips:

```bash
python3 cat-310dx_rename_clips.py           # preview
python3 cat-310dx_rename_clips.py --apply   # rename
```

### Output

```
tmp/cat-310dx_correlation.csv    414 rows, one per CAT-310DX clip
tmp/cat-310dx_rename_map.json    {old_stem: new_stem} for matched clips only
```

CSV columns:

| Column | Contents |
|--------|----------|
| dx_filename | CAT-310DX clip filename |
| dx_size | raw byte count |
| algo | hash algorithm used |
| lpc_checksum | hex digest of the `.lpc` file |
| lpc_match | `yes` or `no` |
| lpc_cat1000_filename | matched CAT-1000 `.lpc` filename, or empty |
| lpc_cat1000_word_id | matched CAT-1000 word number, or empty |
| lpc_cat1000_label | matched CAT-1000 label, or empty |
| wav_checksum | hex digest of the `.wav` file |
| wav_match | `yes`, `no`, or `n/a` |
| wav_cat1000_filename | matched CAT-1000 `.wav` filename, or empty |
| wav_cat1000_word_id | matched CAT-1000 word number (WAV match), or empty |
| wav_cat1000_label | matched CAT-1000 label (WAV match), or empty |
| match_type | `both`, `lpc_only`, `wav_only`, `conflict`, or `none` |

---

## End-to-End Command Sequence

```bash
# 1. Install synthesis library
bash INSTALL_PyTI.sh

# 2. Export CAT-1000 clips
python3 cat-1000_lpc_export.py cat-1000_clips.csv --wav \
    -o cat-1000_lpc_clips/ --wav-dir cat-1000_wav_clips/

# 3. Export CAT-310DX clips
python3 cat-310dx_extract.py --wav

# 4. Correlate
python3 cat-310dx_correlate.py
```

---

## Known Pitfalls

1. **Bit direction** -- MSB-first is native on both devices. Do not
   bit-reverse EPROM bytes before passing to `render_phrase_to_pcm`.
   The function handles the PyTI double-reversal internally.

2. **CAT-1000 end addresses** -- Derive from EPROM index boundaries only.
   STOP frame scanning produces wrong results because the wrong bit
   direction or a frame-parsing error desync the scanner.

3. **CAT-310DX EPROM scanner** -- `eprom_scout.py` on the full CAT-310DX
   image produces only false positives. Use `cat-310dx_extract.py` which
   reads the dispatch table directly.

4. **Word 600 duplicate** -- The CAT-1000 phrase index has two entries
   for word 600. The hardware uses the first (0x6C26). Only the first is
   in `cat-1000_clips.csv`.

5. **Unvoiced frame K coefficients** -- Unvoiced frames (energy > 0,
   pitch = 0) carry K1-K4 only. Voiced frames (pitch > 0) carry K1-K10.
   Misreading this causes bitstream desync and false STOP frame detection.

6. **CAT-310DX word 99 boundary** -- The last clip (sequence 0952) ends
   at the LPC data region constant rather than a following clip address.
   A minor boundary artifact may result. All other 413 clips are clean.

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under
the GNU General Public License v3.0 or later. You may redistribute and/or
modify it under the terms of the GNU GPL as published by the Free Software
Foundation. See <https://www.gnu.org/licenses/> for details.
