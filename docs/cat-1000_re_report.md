# CAT-1000 Reverse Engineering Report
## TMS5220-Compatible LPC-10 Speech Synthesis in a Repeater Controller

**Author:** Kris Kirby, KE4AHR
**Date:** 2026-04-04
**Device:** CAT-1000 Repeater Controller, firmware V3.04A (1998-03-01)

---

## Abstract

This report documents the complete reverse engineering of the speech synthesis subsystem of the CAT-1000 Repeater Controller. The CAT-1000 uses a TI TSP53C30 speech synthesizer (TMS5220-compatible) driven by an Intel 80C186 CPU. The voice vocabulary is stored in a 64 KB EPROM as TMS5220 LPC-10 bit-packed frames. Through analysis of the dispatch firmware, EPROM phrase index, and LPC bitstream format, all 481 unique speech phrases have been successfully extracted, decoded, synthesized to WAV audio, and labelled. This report covers the hardware architecture, EPROM memory map, phrase dispatch mechanism, LPC bitstream format, bit direction analysis, synthesis pipeline, phrase boundary derivation, and data quality audit methodology.

---

## 1. Hardware Architecture

### 1.1 System Overview

The CAT-1000 is an Intel 80C186-based embedded controller designed for amateur radio repeater operation. Its primary function is controlling a repeater with autopatch: it answers calls, recognizes DTMF input, and responds with synthesized speech announcements such as time, date, weather, and status information.

**CPU:** Intel 80C186 (16-bit x86-compatible, 10 MHz)
**Speech synthesizer:** TI TSP53C30 (TMS5220-compatible, LPC-10 vocoder)
**Speech output:** Analog audio via DAC, fed to telephone line interface
**Memory:** Two 27C512/27SF512 EPROMs (64 KB each): program and voice

### 1.2 EPROM Configuration

The 80C186 maps the two EPROMs into its address space:

- **Program EPROM** (27C512, 64 KB): Maps to the upper address space. Contains the 80C186 firmware including the main control loop, ISR handlers, DTMF decoder, and a far-call stub at the reset vector. Firmware version V3.04A, dated 1998-03-01.
- **Voice EPROM** (27SF512, 64 KB): Maps to segment `F000h`. Contains a self-contained speech dispatch routine, a phrase index table, and ~48 KB of TMS5220 LPC-10 bit-packed speech data. The voice EPROM is entirely self-sufficient -- the program EPROM invokes it via a single far call and the voice EPROM handles all internal indexing.

### 1.3 Speech Synthesizer Interface

The TSP53C30 is connected to the 80C186 via the I/O address space at port `0x0180`. The CPU writes 8-bit data bytes directly to this port to stream LPC frames to the synthesizer. The INT1 interrupt service routine (located at program EPROM offset `0x152F`) handles the byte-by-byte streaming during speech playback.

**Critical observation:** The ISR writes raw EPROM bytes to the TSP53C30 with no bit transformation. This means the LPC frame bits are stored in the EPROM exactly as they are transmitted to the chip.

---

## 2. Voice EPROM Memory Map

```
Offset       Size    Contents
────────     ─────   ────────────────────────────────────────────────
0x0000       159 B   8086 dispatch routine
0x009F        84 B   Group 0 phrase index (28 entries × 3 bytes)
0x00F3       237 B   Group 2 phrase index (79 entries × 3 bytes)
0x01E0       162 B   Group 3 phrase index (54 entries × 3 bytes)
0x0282       129 B   Group 4 phrase index (43 entries × 3 bytes)
0x0303       150 B   Group 5 phrase index (50 entries × 3 bytes)
0x0399       195 B   Group 6 phrase index (65 entries × 3 bytes)
0x0459       183 B   Group 7 phrase index (61 entries × 3 bytes)
0x0510       180 B   Group 8 phrase index (60 entries × 3 bytes)
0x05C4       126 B   Group 9 phrase index (42 entries × 3 bytes)
0x0642     48767 B   TMS5220 LPC-10 bit-packed speech data
0xC4C1      ...      (end of speech data / top of used EPROM)
```

Total phrase index: 482 entries across 9 groups. Total speech data: 48,767 bytes (~48 KB).

---

## 3. Dispatch Mechanism

### 3.1 Call Convention

The program EPROM invokes speech via:
```
MOV AH, group_id      ; speech group (0, 2–9; no group 1)
MOV AL, phrase_id     ; phrase ID within group
CALL FAR F000:0000    ; invoke voice EPROM dispatch
; BX = start offset of LPC data, or 0x81F1 if not found
```

The value `0x81F1` is an error sentinel indicating the requested phrase was not found.

### 3.2 Dispatch Algorithm

The voice EPROM dispatch routine (8086 code at offsets 0x0000–0x009E) performs the following:

1. Use `AH` (group_id) to locate the correct group's index table (hardcoded table of group offsets within the dispatch code).
2. **Linear search** through that group's 3-byte entries for a matching `phrase_id` (first byte of each entry).
3. On match, read the following 2-byte little-endian offset: `BX = voice[base+1] | (voice[base+2] << 8)`.
4. Return `BX` to the caller. The ISR then streams bytes from `voice[BX]` onward to the TSP53C30.

The linear search has an important implication: if a group has duplicate `phrase_id` entries, the **first** one is always used. This was observed for word 600 "Miles" (group 6, phrase_id 0) which has two index entries; the hardware always plays the first (at EPROM offset 0x6C26).

### 3.3 Word Number Formula

Word numbers in the CAT-1000 vocabulary are computed as:
```
word_number = group_id × 100 + phrase_id
```

This formula was verified against the 302-entry CAT-1000 vocabulary reference with zero mismatches. The vocabulary reference files (`cat-1000_voc85–88.txt`) were produced by extracting the word-listing pages from Chapter 11 of the CAT-1000 manual and converting them to plain text using `lesspipe(1)`.

The group numbering (0, 2–9, no group 1) means valid word numbers are:
- Group 0: words 0–99 (digits and numeric helpers: 0–20, 30, 40, 50, 60, 70, 80, 90)
- Groups 2–9: words 200–999

---

## 4. Phrase Index Structure

Each entry in a group's index table occupies exactly 3 bytes:

```
Byte 0: phrase_id    (matches AL register passed to dispatch)
Byte 1: offset_lo    (low byte of 16-bit EPROM offset)
Byte 2: offset_hi    (high byte of 16-bit EPROM offset)
```

The offset is a byte address within the voice EPROM pointing to the first byte of that phrase's LPC data. All phrase data starts within the range 0x0642–0xC4C0.

### 4.1 Group Table Locations and Counts

| Group | Table offset | Entry count | Word range |
|-------|-------------|-------------|------------|
| 0 | 0x009F | 28 | 0–90 (digits + decade words) |
| 2 | 0x00F3 | 79 | 200–279 |
| 3 | 0x01E0 | 54 | 300–354 |
| 4 | 0x0282 | 43 | 400–449 |
| 5 | 0x0303 | 50 | 500–554 |
| 6 | 0x0399 | 65 | 600–666 |
| 7 | 0x0459 | 61 | 700–754 |
| 8 | 0x0510 | 60 | 800–852 |
| 9 | 0x05C4 | 42 | 900–954 |

**Note on group 0 count:** The group 0 table has exactly 28 entries (words 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 30, 40, 50, 60, 70, 80, 90). An off-by-one error treating this as 29 entries would mis-read the first entry of group 2 as a spurious group 0 entry.

### 4.2 Complete Python Reader

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

## 5. Phrase Boundary Derivation

### 5.1 End Addresses Are Not Stored

The voice EPROM index stores only phrase **start** addresses. End addresses are derived as "the start of the next phrase in address-sorted order." This is the authoritative phrase boundary -- there is no other end-marker structure in the EPROM.

```python
sorted_starts = sorted(set(a for _, a in entries if 0x0642 <= a < 0x10000))

def index_end(start_addr):
    i = sorted_starts.index(start_addr)
    return sorted_starts[i+1] if i+1 < len(sorted_starts) else 0xC4C1
```

The terminal constant `0xC4C1` is the end of the speech data region, confirmed by the last phrase (word 982 "Good Evening", 149 bytes, STOP frame verified at this position).

### 5.2 Duplicate Entries

Word 600 "Miles" (group 6, phrase_id=0) has two entries in the EPROM index:

| Entry | EPROM offset | Length | Content |
|-------|-------------|--------|---------|
| First (used by hardware) | 0x6C26 | 136 B | "Miles" |
| Second (unreachable) | 0x9041 | 57 B | Same audio as word 700 "Pull" |

The hardware dispatch always returns the first match, so only the first entry is ever played. The second entry appears to be a firmware data error or legacy artifact.

---

## 6. TMS5220 LPC-10 Bitstream Format

### 6.1 Frame Structure

TMS5220 LPC data is bit-packed with no byte alignment between frames. Each frame contains:

| Field | Width (bits) | Conditions |
|-------|-------------|-----------|
| Energy index | 4 | Always present |
| Repeat flag | 1 | Only if energy ≠ 0 and ≠ 15 |
| Pitch index | 6 | Only if energy ≠ 0 and ≠ 15 |
| K1–K4 | 5,5,4,4 = 18 bits | If not repeat |
| K5–K10 | 4,4,4,3,3,3 = 21 bits | If not repeat AND pitch > 0 (voiced) |

**Special energy values:**
- `0000` (0): Silent frame. No further fields. Just a 4-bit token.
- `1111` (15): STOP frame. Terminates the phrase. No further fields.

**Frame rate:** 40 frames/second (25 ms per frame)

### 6.2 Parameter Coding

**Energy:** 4-bit index into a 16-entry table:
`[0, 1, 2, 3, 4, 6, 8, 11, 16, 23, 33, 47, 63, 85, 114, 0]`
(Index 15 is the STOP sentinel; its energy value is never used.)

**Pitch:** 6-bit index into a 64-entry table (0 = unvoiced). Non-zero values map to fundamental frequency periods. Table starts at 15, includes 153, ends at 159.

**K coefficients (reflection coefficients):** Integer values scaled by 512, divided by 512.0 in the lattice filter:
- K1: 5-bit signed (32 levels)
- K2: 5-bit signed
- K3: 4-bit signed (16 levels)
- K4: 4-bit signed
- K5–K10: 4-bit and 3-bit signed (voiced frames only)

### 6.3 Synthesis Algorithm

The TMS5220 uses a 10-stage lattice (PARCOR) filter:
```
excitation → K10 stage → K9 stage → ... → K1 stage → output
```

For voiced frames the excitation is a periodic chirp waveform; for unvoiced frames it is white noise. The chirp is the TMS5220-specific waveform (not the TMS5100 waveform which has negative values).

---

## 7. Bit Direction Analysis

### 7.1 The Problem

The TMS5220 datasheet describes its data interface with D7 labelled as D0 which is ambiguous. Two interpretations exist:

1. **LSB-first:** Bit 0 of each byte is transmitted first; the LPC parameter MSB is in bit 7 of the *last* byte of the field.
2. **MSB-first per byte, assembled MSB-first:** Bit 7 of each byte is the MSB of the current LPC field being filled.

Getting this wrong causes the STOP frame scanner to find false positives (the wrong 4-bit pattern `1111` appears at the wrong positions), resulting in phrase boundaries that are too short.

### 7.2 Determination of Correct Direction

The voice EPROM phrase index provides authoritative phrase lengths (via `index_end`). Using these as ground truth:

**Test methodology:** For a set of known phrases, scan the LPC data in both bit directions and compare the resulting STOP frame position to the index-derived phrase length.

**Calibration results:**

| Word | Label | Index length | MSB-native STOP | LSB-first STOP |
|------|-------|-------------|-----------------|----------------|
| 220 | Affirmative | 180 B | 179 B (±1) ✓ | 43 B ✗ |
| 981 | Good Afternoon | 195 B | 195 B ✓ | incorrect ✗ |
| 982 | Good Evening | 149 B | 149 B ✓ | incorrect ✗ |

**Conclusion:** The LPC encoder stored each frame's MSB in bit 7 of the first byte, reading each byte from bit 7 down to bit 0, assembling fields MSB-first. This is **native MSB-first**.

The systematic 1-byte off-by-one between the MSB-native scanner and the index length (seen in word 220) is because `bytes_consumed()` rounds the final partially-consumed byte upward. This is harmless.

### 7.3 Correct STOP Frame Scanner

```python
def find_stop_msb(native_data):
    """Returns bytes consumed up to and including the STOP frame, or None."""
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
        if e == 0:  continue             # silent frame -- no additional fields
        rep = rb(1); pitch = rb(6)
        if rep is None or pitch is None: return None
        if not rep:
            k_bits = [5,5,4,4] if pitch == 0 else [5,5,4,4,4,4,4,3,3,3]
            for w in k_bits:
                if rb(w) is None: return None
```

**Critical detail:** Unvoiced frames (pitch index = 0) read only K1–K4 (18 bits), not all 10 coefficients. This was the most common source of false STOP frames in early versions of the scanner.

### 7.4 Synthesis Bit Handling

The export script (`cat-1000_lpc_export.py`) uses PyTI_LPC_CMD for synthesis:
- `render_phrase_to_pcm(lpc_native)` pre-reverses each byte via `_BIT_REVERSE` lookup table.
- PyTI `LPCSynthesizer` internally bit-reverses each byte again.
- Net effect: original native bytes are processed MSB-first. ✓

This double-reversal is intentional and correct.

---

## 8. CSV Phrase Database

### 8.1 Format

Two CSV files maintain the phrase database:

**`cat-1000_clips.csv`** (no header row, primary export input):
```
NNNN,"Label",0xSTART,0xEND
```

**`cat-1000_phrases.csv`** (header row, alphabetically sorted, human-readable):
```
word_number,word_text,start_address,end_address,notes
```

### 8.2 Address Audit History

The original CSV end addresses were generated using an incorrect LSB-first STOP frame scanner, producing systematically truncated phrase boundaries. Full audit on 2026-04-02 found:

- **465 incorrect end addresses** out of 481 total entries
- All corrected to `index_end(start_addr)` from the voice EPROM phrase table
- Method: per-start-address correction ensures duplicate-entry words (like word 600) receive the correct boundary for each specific start address

After correction, all 481 entries exactly match the voice EPROM index boundary. The CSV is now a faithful representation of the hardware phrase table.

---

## 9. Vocabulary

### 9.1 Coverage

| Category | Count |
|----------|-------|
| Total phrase entries | 482 |
| Unique phrases | 481 |
| Documented in CAT-1000 vocabulary reference | 472 |
| Undocumented (present in EPROM, not in manual) | 9 |
| All phrases positively identified and labelled | 481 |
| Still unlabelled | 0 |

### 9.2 Phrase Groups by Content

- **Group 0 (words 0–90):** Digits 0–9, 10–20, decade words 30–90
- **Group 2 (200s):** Core vocabulary A–C: Acknowledge, Affirmative, Alert, Altitude, Amps, etc.
- **Group 3 (300s):** Core vocabulary D–F: Data, Date, Day, Delta, Emergency, Error, etc.
- **Group 4 (400s):** Core vocabulary G–I: Gear, Golf, Hail, Henry, High, Hotel, Ice, etc.
- **Group 5 (500s):** Core vocabulary J–L: January, Juliet, Key, Kilo, Land, Lima, Lock, etc.
- **Group 6 (600s):** Core vocabulary M: Machine, Malfunction, March, May, Mayday, Miles, etc.
- **Group 7 (700s):** Core vocabulary N–R: Negative, North, Oscar, Papa, Phone, Police, Pull, etc.
- **Group 8 (800s):** Core vocabulary S–T: Safe, Saturday, Security, Storm, Sunday, System, etc.
- **Group 9 (900s):** Core vocabulary U–Z + special effects: Uniform, Victor, Warning, Weather, Yankee, Zulu, Gunshot, Laser, Laughter, etc.

### 9.3 Special / Undocumented Words

These 9 words appear in the voice EPROM index but are absent from the CAT-1000 printed vocabulary reference (cat-1000_voc85–88.txt). All have been positively identified by ear.

| Word | Label | Notes |
|------|-------|-------|
| 299 | Comm | Communication prefix |
| 377 | File | Confirmed by listening; vocabulary term |
| 393 | Fall | Season (weather vocabulary) |
| 457 | Heat | Weather term |
| 486 | Index | General vocabulary |
| 703 | Percent | Unit suffix |
| 704 | Pressure | Weather/status |
| 852 | Today's | Possessive form for date announcements |
| 910 | Windchill | Weather vocabulary |

### 9.4 Sound Effects (Group 9)

Beyond speech, the voice EPROM contains several non-speech audio phrases:

| Word | Label | Length | Duration |
|------|-------|--------|---------|
| 960 | Pause 1 | 9 B | 100 ms |
| 961 | Pause 2 | 9 B | 150 ms |
| 962 | Pause 3 | 10 B | 200 ms |
| 963 | Pause 4 | 11 B | 250 ms |
| 964 | Chime 1 | 27 B | 450 ms |
| 965 | Chime 2 | 27 B | 450 ms |
| 966 | Chime 3 | 44 B | 675 ms |
| 967 | Gunshot | 131 B | 975 ms |
| 968 | Laser | 56 B | 300 ms |
| 969 | Phaser | 32 B | 575 ms |
| 970 | Tic | 16 B | 150 ms |
| 971 | Toc | 16 B | 150 ms |
| 972 | Laughter | 558 B | 2775 ms |

---

## 10. Synthesis Verification

### 10.1 Baseline Word

Word 220 "Affirmative" is the established verification baseline:

| Metric | Expected value |
|--------|---------------|
| LPC file size | 180 bytes |
| WAV file size | 14,044 bytes |
| Frame count | ~34 (33 data + STOP) |
| Sample count | ~7,000 |
| Duration | ~875 ms |
| Subjective | Clearly "Affirmative" |

### 10.2 Synthesis Bugs Encountered and Fixed

| # | Bug | Symptom | Fix |
|---|-----|---------|-----|
| 1 | Bit-reversed bytes fed to synthesis | Unintelligible noise | Pass `lpc_native` not `lpc_out` to render |
| 2 | LSB-first BitStream | Wrong frame parsing | Rewrote: `bitp=7`, decrement, MSB-first |
| 3 | Wrong ENERGY_TABLE (MAME) | Wrong amplitude scaling | Replaced with TMS5220 values |
| 4 | Wrong PITCH_TABLE | Wrong fundamental frequency | Corrected: start 15, include 153, end 159 |
| 5 | Wrong K1–K10 (TMS5100 values) | Incorrect formants | Replaced with TMS5220 integer values |
| 6 | Wrong CHIRP (TMS5100 waveform) | Harsh/incorrect voiced frames | Replaced with TMS5220 chirp |
| 7 | K values pre-divided; no `/512.0` | Filter arithmetic error | Added `/512.0` in lattice filter |
| 8 | Built-in synthesis subtle errors | Residual quality issues | Replaced with PyTI_LPC_CMD backend |

---

## 11. Tools and Scripts

### `cat-1000_lpc_export.py`

Primary export tool. Reads `cat-1000_clips.csv`, extracts LPC data from the voice EPROM for each phrase, synthesizes WAV audio via PyTI_LPC_CMD, and writes output files.

```bash
python3 cat-1000_lpc_export.py cat-1000_clips.csv --wav -o cat-1000_lpc_clips/ --wav-dir cat-1000_wav_clips/
```

Key functions:
- `iter_all_phrases()`: reads CSV, yields (word_num, label, lpc_bytes)
- `render_phrase_to_pcm()`: bit-reverses + PyTI synthesis → int16 PCM
- `write_wav()`: 8000 Hz mono 16-bit WAV output

### `cat-1000_analysis.py`

EPROM analysis utilities: phrase index dumping, STOP frame scanning, data comparison.

### `cat-1000_lpc_repack.py`

Repacks modified LPC data back into the EPROM image format.

### `fix_csv_addresses.py`

One-shot utility that bulk-corrected all 465 incorrect end addresses in both CSVs using per-start-address `index_end()` from the voice EPROM. No longer needed; retained for reference.

---

## 12. Conclusions

The CAT-1000 voice EPROM is a self-contained TMS5220 LPC-10 speech store with a compact 3-byte-per-entry phrase index. The dispatch mechanism is a simple linear search, making the index trivially decodable once the group table locations are known.

The critical insight for correct data extraction is bit direction: LPC frames are stored MSB-first per byte, matching the order in which the bytes are written directly to the TSP53C30. Using LSB-first scanning produces false STOP frame positions and systematically truncated phrase boundaries -- the original CSV had 465 of 481 end addresses incorrect for this reason.

Once phrase boundaries are derived from the phrase index (the authoritative source), all 481 phrases can be extracted and synthesized with high audio fidelity using PyTI_LPC_CMD with the correct TMS5220 parameter tables.

The complete vocabulary of 481 phrases has been extracted, synthesized, and labelled. All 481 phrases are positively identified, including 9 undocumented words absent from the printed vocabulary reference.

---

## Appendix A: Voice EPROM Phrase Index -- Complete Listing

See `cat-1000_phrases.csv` for the complete 481-entry phrase table with EPROM addresses.

## Appendix B: TMS5220 Parameter Tables

See source code in `cat-1000_lpc_export.py` (lines 120–220) for the complete energy, pitch, K1–K10, and chirp tables used in synthesis, with references to `tms5220.txt`.

## Appendix C: Key Addresses

| Symbol | Value | Description |
|--------|-------|-------------|
| `LPC_DATA_START` | 0x0642 | First byte of speech data |
| `LPC_DATA_END` | 0xC4C1 | Exclusive end of speech data |
| `ERROR_SENTINEL` | 0x81F1 | Dispatch return value when phrase not found |
| `TSP53C30_PORT` | 0x0180 | I/O port for LPC byte streaming |
| `DISPATCH_ENTRY` | F000:0000 | Far call address for speech dispatch |
| `ISR_OFFSET` | 0x152F | INT1 handler in program EPROM |

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the GNU General Public License v3.0 or later. You may redistribute and/or modify it under the terms of the GNU GPL as published by the Free Software Foundation. See <https://www.gnu.org/licenses/> for details.
