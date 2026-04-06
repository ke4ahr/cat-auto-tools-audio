# eprom_scout.py -- Development Log and Discoveries

**Tool:** `eprom_scout.py` -- general-purpose TMS5220 LPC speech discovery tool
**Test target:** CAT-1000 voice EPROM (`eprom_images/cat-1000-voice_27SF512.BIN`, 64 KB)
**Final result:** 481 unique clips (482 index entries), phrase index at 0x009F, MSB-first, 0x0180 speech port, ~5 s runtime

---

## What the tool does

`eprom_scout.py` takes an unknown EPROM image (and optionally a companion program EPROM) and recovers all TMS5220 LPC speech phrases without any prior knowledge of the EPROM layout:

1. **Bit direction detection** -- determines whether LPC parameter MSBs are in bit 7 (MSB-first/native) or bit 0 (LSB-first/reversed) by scanning both ways and comparing coverage.
2. **LPC blob detection** -- locates all byte ranges that parse as valid TMS5220 LPC sequences ending in a STOP frame.
3. **Phrase index detection** -- finds the `[phrase_id][addr_lo][addr_hi]` index table that most systems use to dispatch to individual phrases.
4. **Boundary derivation** -- computes each phrase's end address from the sorted start addresses in the index.
5. **Export** -- writes one `.lpc` file per unique phrase plus a CSV and Markdown report.
6. **Program EPROM analysis** (optional) -- identifies speech dispatch `CALL FAR` patterns and the speech chip I/O port from the program EPROM.

---

## Discoveries about the CAT-1000

These are the concrete findings produced by the tool against the CAT-1000 EPROM pair.

### Bit direction: MSB-first (native)

Scanning MSB-first produces 48,767 bytes of valid LPC coverage; LSB-first produces roughly 3 KB of short false hits. The CAT-1000 voice EPROM stores LPC bytes in native format -- bit 7 is the MSB of the first parameter field in each byte. Raw EPROM bytes can be streamed directly to the TMS5220 data port without transformation.

### Voice EPROM layout

```
0x0000–0x009E   8086 dispatch routine (far call entry point at 0x0000)
0x009F–0x0641   Phrase index: 9 groups × N entries, 3 bytes each
0x0642–0xC4C1   TMS5220 bit-packed LPC speech data (~48 KB)
0xC4C2–0xFBFF   Zero-filled (0x00)
0xFCC7–0xFFDE   Interrupt vectors / bootstrap code (false LPC blobs detected here)
```

### Phrase index structure

Each entry is `[phrase_id][addr_lo][addr_hi]` (3 bytes, little-endian address). The index is located at 0x009F with 482 entries across 9 groups. Group boundaries:

| Group | Offset | Entries |
|-------|--------|---------|
| 0 | 0x009F | 28 |
| 2 | 0x00F3 | 79 |
| 3 | 0x01E0 | 54 |
| 4 | 0x0282 | 43 |
| 5 | 0x0303 | 50 |
| 6 | 0x0399 | 65 |
| 7 | 0x0459 | 61 |
| 8 | 0x0510 | 60 |
| 9 | 0x05C4 | 42 |

Word numbering: `word_number = group × 100 + phrase_id`. End addresses are not stored; each phrase ends where the next one (in address order) begins.

### Unique speech clips: 481

The index has 482 entries. One address (0x9041) appears twice: once as the unreachable second entry for word 600 "Miles" (group 6, phrase_id=0) and once as the entry for word 700 "Pull". The hardware dispatch always returns the first match, so the second word-600 entry is never played. There are 481 unique physical speech segments.

### TMS5220 LPC frame structure (confirmed by scanner)

Frames are bit-packed MSB-first:

| Frame type | Fields | Bits |
|------------|--------|------|
| STOP | energy=0xF | 4 |
| Silent | energy=0x0 | 4 |
| Voiced (pitch>0) | energy(4)+repeat(1)+pitch(6)+K1–K10([5,5,4,4,4,4,4,3,3,3]) | 50 |
| Unvoiced (pitch=0) | energy(4)+repeat(1)+pitch(6)+K1–K4([5,5,4,4]) | 29 |
| Repeat | energy(4)+repeat=1+pitch(6) | 11 |

### Speech chip I/O port: 0x0180

The program EPROM contains a single speech ISR at offset 0x152F. The ISR:
1. Loads `MOV DX, 0x0180` (BA 80 01)
2. Polls `IN AL, DX` followed immediately by `CMP AL, imm8` -- busy-wait on a ready flag
3. Writes one LPC byte via `OUT DX, AL` (EE)
4. Repeats per interrupt

Port 0x0080 has 219 blind `OUT 0x80, AL` writes (POST diagnostic/delay port -- a standard PC/AT practice), which dominated a naive "most-writes" heuristic. The speech port 0x0180 was correctly identified by the compound signature: IN → CMP-within-2-bytes + 8-bit OUT to the same DX value.

### Speech dispatch pattern

Program EPROM contains 44 `CALL FAR` instructions, 6 identified as speech dispatch calls (target offset 0x0000, AH = group 0–9, AL = phrase_id 0–99). The dispatch routine in the voice EPROM performs a linear search and returns the phrase start address in BX.

---

## Development history

The tool was developed iteratively against the CAT-1000 baseline, with each run revealing a new bug. What follows is the complete sequence.

---

### Bug 1 -- Wrong bit direction selected

**Symptom:** Tool selected LSB-first; CAT-1000 is known MSB-first.

**Root cause:** `detect_bit_direction` compared blob *count*. MSB scanning merged blobs into a few large ones (correct behaviour), LSB produced more small false hits. More blobs ≠ better direction.

**Fix:** Compare total bytes covered:
```python
msb_bytes = sum(b['length'] for b in msb)
lsb_bytes = sum(b['length'] for b in lsb)
return (msb_bytes >= lsb_bytes, ...)
```
MSB covers 48,767 bytes; LSB covers ~3 KB. No contest.

---

### Bug 2 -- Phrase index search region 0→0

**Symptom:** `find_phrase_index_tables` found 0 candidates.

**Root cause:** Code set `index_region_end = blobs[0]['start']`, expecting the first blob to start after the phrase index. But bytes 0x0000–0x0641 (dispatch code + phrase index) contain many `0x00` bytes, which parse as TMS5220 silent frames (energy nibble = 0). The scanner built a false blob at 0x0001, so `blobs[0]['start'] = 0` and `index_region_end = 0`.

**Fix:** Remove the pre-blob shortcut; always search the full EPROM. A different constraint (Bug 3) handles false positives in the speech region.

---

### Bug 3 -- False 4,800-entry table in speech data

**Symptom:** Phrase index "found" at 0xC4C1 with 4,800 entries and 100% confidence.

**Root cause:** Without filtering, nearly every 3-byte triplet in speech data satisfies `pid ≤ 127` and `data_start ≤ addr < data_end`. The scanner ran deep into the speech region producing a massive false positive.

**Fix:** *Forward-pointing constraint* -- a real phrase index always precedes the data it indexes:
```python
if addr <= pos: break   # entry must point forward of the table position
```
Speech data bytes point to addresses within their own region, so they fail this check on the first entry, giving immediate rejection. This also provides an early-exit speed benefit across the speech region.

---

### Bug 4 -- Performance timeout (30–120 seconds)

**Symptom:** Tool timed out on the 1,602-byte preamble (dispatch code + phrase index).

**Root cause (a):** The preamble is mostly `0x00` bytes. Each `0x00` nibble = silent frame (4 bits, loop continues). With a 1,024-byte window (8,192 bits), the parser could run for up to 2,048 silent frames per position before exhausting the window -- never finding the STOP frame that would terminate the sequence.

**Root cause (b):** Python's big integers. A 1,024-byte (8,192-bit) integer needs ~273 CPython "digits" per shift. Over 1,602 positions × ~170 loop iterations × 3 bit extractions × 273 digits ≈ 225 million digit operations ≈ 30 seconds.

**Fix 1 -- shrink the window:**
```python
_MAX_PARSE_BYTES = 64   # 512-bit window → ~17 CPython digits
```
16× smaller integer → 16× faster shift. A 64-byte window is still far larger than any TMS5220 frame (max 50 bits = 7 bytes) and sufficient to require min_frames=5 valid frames before accepting a sequence.

**Fix 2 -- sliding window:**
```python
# Advance by 1 byte: shift left, append new byte
window = ((window << 8) | work[next_byte]) & MASK
# After a match (jump forward): rebuild from scratch
window, avail_bits = _build_window(pos)
```

**Fix 3 -- batch K-coefficient skip:**
```python
_K_BITS_VOICED_TOTAL   = 39   # K1–K10 combined
_K_BITS_UNVOICED_TOTAL = 18   # K1–K4 combined
bit_off += k_total            # one addition instead of 10 field extractions
```
K values aren't needed for blob detection -- skip them atomically.

**Result:** ~5 seconds on a 64 KB EPROM.

---

### Bug 5 -- Valid phrase addresses rejected (mid-blob)

**Symptom (intermediate):** Correct phrase index found (482 entries), but entry addresses were rejected as "not in speech region."

**Root cause:** The validity check used `blob_starts = {b['start'] for b in blobs}` -- a set of blob start addresses. With 278 fragmented blobs, most phrase start addresses land in the middle of a blob, not at its start. They were wrongly rejected.

**Fix:** Range check instead of set membership:
```python
data_start = min(b['start'] for b in blobs)
data_end   = _dominant_region_end(blobs)   # see Bug 7
def _valid_addr(addr): return data_start <= addr < data_end
```

---

### Bug 6 -- 1,281+ duplicate phrases exported

**Symptom:** Tool reported "Phrases to export: 1281" (or higher in some runs).

**Root cause 1:** Boundary-building collected entries from all 149 candidate tables instead of just the best one, multiplying entries across overlapping tables.

**Root cause 2:** Phrase IDs are group-relative (0–99 per group, 9 groups). Using `phrase_id` directly as the word number caused word 20 to appear in all 9 groups simultaneously.

**Fix:**
```python
# Use only the highest-entry-count table
best = max(index_tables, key=lambda t: t['count'])

# Deduplicate by start address
seen = {}
for pid, addr in best['entries']:
    if addr not in seen: seen[addr] = pid

# Number sequentially in address order -- agnostic about the group*100+id scheme
sorted_starts = sorted(seen.keys())
word_entries  = [(i + 1, addr) for i, addr in enumerate(sorted_starts)]
```

---

### Bug 7 -- Last phrase over-extends into EPROM tail

**Symptom:** Last phrase (word 982 "Good Evening", start 0xC42C) exported as 1,837 bytes instead of 149.

**Root cause:** The high region of the EPROM (0xFCC7–0xFFDE) contains 8086 interrupt vectors and bootstrap code. This code happens to contain runs of `0x00` bytes followed by non-zero bytes with STOP nibbles -- valid-looking LPC sequences. The scanner built false blobs there, pulling `max(b['end'])` out to 0xFFDE. This expanded `data_end` to 0xFFDE, allowing a false phrase index entry from the dispatch code tail (0x009C, addr=0xCB59) to pass `_valid_addr`. The boundary builder then set word 982's end to 0xCB59 instead of 0xC4C1.

**Key observation:** The gap between the last real speech blob (end 0xC4C1) and the first false blob (start 0xFCC7) is 14,342 bytes. All inter-phrase gaps within the speech region are under ~200 bytes.

**Fix:** `_dominant_region_end(blobs, gap_threshold=4096)` -- walk blobs in start-address order, return the end of the last blob before the first gap ≥ 4 KB:
```python
def _dominant_region_end(blobs, gap_threshold=4096):
    sorted_blobs = sorted(blobs, key=lambda b: b['start'])
    for i in range(len(sorted_blobs) - 1):
        gap = sorted_blobs[i+1]['start'] - sorted_blobs[i]['end']
        if gap >= gap_threshold:
            return sorted_blobs[i]['end']
    return sorted_blobs[-1]['end']
```
Used for both the `_valid_addr` upper bound and the `last_addr` sentinel.

**Result:** `data_end = 0xC4C1`. False entry at 0x009C (addr=0xCB59) is rejected. Phrase index starts at exact 0x009F. All 479 boundaries correct.

---

### Bug 8 -- I/O port reported as 0x0080 instead of 0x0180

**Symptom:** Tool reported "Most-written I/O port: 0x0080" -- the POST diagnostic port, not the speech chip.

**Root cause:** Port 0x0080 has 219 `OUT 0x80, AL` (opcode E6 80) writes throughout the CAT-1000 firmware -- this is the standard PC/AT practice of writing POST codes and using port 0x80 as an I/O delay. Port 0x0180 (the TMS5220 data port) only has 2 `OUT DX, AL` writes total. The "most writes" heuristic was dominated by diagnostic writes.

**Investigation:** The speech ISR at 0x152F uses the pattern:
```
BA 80 01  MOV DX, 0x0180
EC        IN AL, DX         ; read TMS5220 status/ready
3C FE     CMP AL, 0xFE      ; check ready flag
74 xx     JZ ...             ; branch if ready
...       (retry loop)
8A 07     MOV AL, ES:[BX]   ; fetch LPC byte
EE        OUT DX, AL        ; write to TMS5220
```
Port 0x0080 in contrast is accessed via blind writes (`E6 80`) with no associated read.

**Fix:** Added detection for the compound speech chip signature:
1. `IN AL, DX` (EC) followed within 2 bytes by `CMP AL, imm8` (3C) -- busy-wait on a device ready flag
2. `OUT DX, AL` (EE, 8-bit) to the same DX port -- write a single data byte

This pattern is essentially unique to a hardware device that requires a ready-check before each byte write. Port 0x2DA0 (another busy-wait port in the firmware) was excluded because it uses 16-bit `OUT DX, AX` (EF), not 8-bit `OUT DX, AL` (EE). The TMS5220 is an 8-bit interface.

**Result:** Tool now reports `Speech data port: 0x0180 (busy-wait+write pattern, 2 data writes)` ✓

---

## Final validation summary

| Check | Expected | Result |
|-------|----------|--------|
| Bit direction | MSB-first | MSB-first ✓ (48,767 B vs ~3 KB LSB) |
| Phrase index offset | 0x009F | 0x009F ✓ |
| Phrase index entries | 482 | 482 ✓ |
| Phrases exported | 481 unique clips | 481 ✓ |
| Boundary mismatches | 0 | 0 ✓ |
| Speech I/O port | 0x0180 | 0x0180 ✓ |
| Runtime | < 30 s | ~5 s ✓ |

---

## Algorithm summary

| Step | Technique | Key constraint |
|------|-----------|----------------|
| Bit direction | Compare total bytes, not blob count | |
| LPC scanning | Sliding 64-byte integer window | min_frames=5 valid frames + STOP |
| Speech region | `_dominant_region_end`: cut at first gap ≥ 4 KB | Separates speech from interrupt vectors |
| Index detection | Full EPROM scan | Forward-pointing: entry addr > table pos |
| Phrase boundaries | Sort index start addresses | Last boundary = `_dominant_region_end` |
| Speech port | Busy-wait (IN→CMP) + 8-bit OUT to same port | Excludes write-only diagnostic ports |

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the GNU General Public License v3.0 or later. You may redistribute and/or modify it under the terms of the GNU GPL as published by the Free Software Foundation. See <https://www.gnu.org/licenses/> for details.
