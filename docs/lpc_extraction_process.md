# LPC Data Extraction from CAT-1000 Voice EPROM
## Complete Technical Process

**Device:** CAT-1000 Repeater Controller
**Speech chip:** TI TSP53C30 (TMS5220-compatible)
**Date completed:** 2026-04-02

---

## 1. Hardware Overview

The CAT-1000 uses two 27C512/27SF512 EPROMs:

- **Program EPROM** -- 64 KB Intel 80C186 firmware. Contains the main control loop, ISR handlers, and a far-call stub that invokes the voice EPROM's dispatch routine.
- **Voice EPROM** -- 64 KB. Self-contained: contains an 8086 dispatch routine at offset 0x0000, a phrase index table, and ~48 KB of TMS5220 LPC-10 bit-packed speech data.

The program EPROM calls `CALL FAR F000:0000` with `AH = group_id`, `AL = phrase_id`. The voice EPROM's dispatch code searches its own phrase index and returns `BX = start_offset` of the LPC data for that phrase.

The INT1 ISR (program EPROM offset 0x152F) reads LPC bytes one at a time from the voice EPROM and writes them directly to I/O port 0x0180 (the TSP53C30 data port) with no bit transformation.

---

## 2. Voice EPROM Memory Map

```
0x0000–0x009E  8086 dispatch routine (called via CALL FAR F000:0000)
0x009F–0x0641  Phrase index tables (9 groups, 482 entries total)
0x0642–0xC4C1  TMS5220 LPC bit-packed speech data (~48 KB)
```

---

## 3. Phrase Index Format

Each entry in the phrase index is exactly 3 bytes:

```
[phrase_id]  [offset_lo]  [offset_hi]
```

The offset is a 16-bit little-endian byte address within the voice EPROM pointing to the start of the phrase's LPC data.

The 9 group tables and their locations:

| Group | Table start | Entry count |
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

Note: there is no group 1. Word number = `group × 100 + phrase_id`.

### Reading the phrase index in Python

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

---

## 4. Deriving Phrase Boundaries

The phrase index stores only **start addresses**. End addresses are not stored. To find where each phrase ends, sort all start addresses and treat each phrase's end as the start of the next phrase in address order:

```python
sorted_starts = sorted(set(a for _, a in entries if 0x0642 <= a < 0x10000))

def index_end(start):
    i = sorted_starts.index(start)
    return sorted_starts[i+1] if i+1 < len(sorted_starts) else 0xC4C1
```

The constant `0xC4C1` is the known end of the speech data region (verified by the last phrase, word 982 "Good Evening", being 149 bytes with its STOP frame at that exact position).

---

## 5. TMS5220 LPC Bitstream Format

TMS5220 LPC data is bit-packed. Each frame consists of:

| Field | Bits | Notes |
|-------|------|-------|
| Energy index | 4 | 0 = silent frame; 15 = STOP frame |
| Repeat flag | 1 | Only present if energy ≠ 0 and ≠ 15 |
| Pitch index | 6 | 0 = unvoiced; >0 = voiced |
| K1–K4 | 5,5,4,4 | Always present (if not repeat) |
| K5–K10 | 4,4,4,3,3,3 | Only present if voiced (pitch > 0) |

Frame rate: 40 frames/second (25 ms per frame).

A **STOP frame** has energy index = 15 (binary `1111`) and no further fields.

---

## 6. Bit Direction

This is the most critical and most easily confused aspect of TMS5220 data extraction.

**The CAT-1000 stores LPC bytes natively: the MSB of each LPC parameter field is in bit 7 of the byte.** Reading bytes from the EPROM in normal byte order, processing each byte from bit 7 down to bit 0, and assembling fields MSB-first gives correct parameter values.

This is called **native MSB-first** in this project.

### Common confusion

The TMS5220 datasheet describes its serial interface as "D0-first," which sounds like LSB-first. In practice, the first bit transmitted to the TMS5220 is the MSB of the first parameter field, and that bit is stored at bit position 7 (D7) of the first byte in EPROM. So "D0-first" in the datasheet refers to the chip's internal shift register pin naming, not to a bit reversal.

**Do not bit-reverse EPROM bytes before scanning for STOP frames.** This is the error that produced incorrect phrase boundaries in the original CSV.

### Correct STOP frame scanner

```python
def find_stop_msb(native_data):
    """Return bytes consumed up to and including the STOP frame, or None."""
    bp = 0; bitp = 7

    def rb(n):
        nonlocal bp, bitp
        v = 0
        for _ in range(n):
            if bp >= len(native_data): return None
            v = (v << 1) | ((native_data[bp] >> bitp) & 1)
            bitp -= 1
            if bitp < 0: bitp = 7; bp += 1
        return v

    def bc():
        return bp + (0 if bitp == 7 else 1)

    while True:
        e = rb(4)
        if e is None: return None
        if e == 15: return bc()          # STOP frame
        if e == 0: continue              # silent frame -- no more fields
        rep = rb(1); pitch = rb(6)
        if rep is None or pitch is None: return None
        if not rep:
            k_bits = [5,5,4,4] if pitch == 0 else [5,5,4,4,4,4,4,3,3,3]
            for w in k_bits:
                if rb(w) is None: return None
```

---

## 7. Synthesis Pipeline

`cat-1000_lpc_export.py` synthesizes WAV files using PyTI_LPC_CMD:

```
EPROM bytes (native MSB-first)
    → bit-reverse each byte via _BIT_REVERSE lookup table
    → pass to PyTI LPCSynthesizer.synthesize(data, TMS5220_PARAMS)
        [PyTI internally bit-reverses each byte again]
    → net effect: PyTI processes original native bytes MSB-first ✓
    → apply 75% gain, scale to int16
    → write 8000 Hz mono 16-bit WAV
```

The double reversal is intentional: `render_phrase_to_pcm` pre-reverses to match PyTI's internal convention.

### TMS5220 parameter tables

The synthesis uses the correct TMS5220 tables (not TMS5100 or MAME values):

- Energy: `[0,1,2,3,4,6,8,11,16,23,33,47,63,85,114,0]`
- Pitch: starts at 15, includes 153, ends at 159 (per tms5220.txt)
- K1–K10: integer values from tms5220.txt, divided by 512.0 in the lattice filter
- Chirp: TMS5220 waveform (all non-negative values)

---

## 8. Extracting LPC Files

### Step 1 -- Obtain EPROM images

Dump both EPROMs with a standard EPROM programmer. The voice EPROM is the only one needed for LPC extraction.

### Step 2 -- Generate or verify the phrase CSV

The CSV (`cat-1000_clips.csv`) maps word numbers to EPROM address ranges.

**From scratch (using EPROM index):**

```python
# Build correct_end dict from the EPROM index (see Section 4)
# Write CSV rows: NNNN,"Label",0xSTART,0xEND
```

**From existing CSV (verify/fix addresses):**

```python
correct_end = {addr: index_end(addr) for addr in sorted_starts}
for row in csv.reader(open('cat-1000_clips.csv')):
    start = int(row[2], 16)
    ce = correct_end.get(start)
    if ce and int(row[3], 16) != ce:
        # fix end address
```

### Step 3 -- Export LPC and WAV files

```bash
# LPC files only (no synthesis dependency):
python3 cat-1000_lpc_export.py cat-1000_clips.csv -o cat-1000_lpc_clips/

# LPC + WAV (requires PyTI_LPC_CMD):
python3 cat-1000_lpc_export.py cat-1000_clips.csv --wav \
    -o cat-1000_lpc_clips/ --wav-dir cat-1000_wav_clips/
```

Output: `cat-1000_lpc_clips/NNNN_Label.lpc` and `cat-1000_wav_clips/NNNN_Label.wav` for each phrase.

---

## 9. Verification

After export, verify synthesis is working by checking word 220 "Affirmative":

```bash
ls -la cat-1000_lpc_clips/0220_Affirmative.lpc cat-1000_wav_clips/0220_Affirmative.wav
# Expected: .lpc = 180 bytes, .wav = 14044 bytes (44 header + 7000 samples × 2)
```

Play the WAV and confirm it sounds like "Affirmative" (~875 ms).

---

## 10. Known Edge Cases

### Duplicate EPROM entries (word 600)
Word 600 "Miles" has two entries in the voice EPROM phrase index, both with phrase_id=0 in group 6. The dispatch code's linear search returns the first (at 0x6C26). The second entry (at 0x9041) is also word 700 "Pull." The `cat-1000_clips.csv` contains only the first entry for word 600.

### Undocumented words
The vocabulary reference files (`cat-1000_voc85–88.txt`) were produced by extracting the word-listing pages from Chapter 11 of the CAT-1000 manual and converting them to plain text using `lesspipe(1)`. Nine words appear in the phrase index but not in that reference:

| Word # | Label | Start address | Notes |
|--------|-------|---------------|-------|
| 299 | Comm | 0x3874 | confirmed by listening |
| 377 | File | 0x4E17 | confirmed by listening |
| 393 | Fall | 0x4E7D | confirmed by listening |
| 457 | Heat | 0x5BE0 | confirmed by listening |
| 486 | Index | 0x5DAB | confirmed by listening |
| 703 | Percent | 0x90FF | confirmed by listening |
| 704 | Pressure | 0x9151 | confirmed by listening |
| 852 | Today's | 0xAC1D | confirmed by listening |
| 910 | Windchill | 0xBE59 | confirmed by listening |

All nine are confirmed and included in `cat-1000_clips.csv`. Words 486 (Index), 852 (Today's), and 910 (Windchill) were the last to be identified, completing the full 481-word vocabulary.

### Silent frames
Energy index 0 is a silent frame with no further fields -- just a 4-bit token. Unvoiced frames (energy ≠ 0, pitch = 0) read only K1–K4 (18 parameter bits total), not all 10 K coefficients. Missing this distinction causes the bit scanner to desync and find false STOP frames.

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the GNU General Public License v3.0 or later. You may redistribute and/or modify it under the terms of the GNU GPL as published by the Free Software Foundation. See <https://www.gnu.org/licenses/> for details.
