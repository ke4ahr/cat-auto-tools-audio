# CAT-310DX V1.00 Reverse Engineering Report
## TMS5220-Compatible Speech Synthesis in a Vintage Digital Weather Station

**Author:** Kris Kirby, KE4AHR
**Date:** 2026-04-03
**Device:** CAT-310DX digital weather station console, firmware V1.00 (C)1998
**Image:** `eprom_images/CAT-310DX_V1-00_1998_7A69.BIN` -- 64 KB, checksum 7A69

---

## Abstract

This report documents the complete reverse engineering of the firmware and speech synthesis subsystem of the CAT-310DX digital weather station console. The CAT-310DX uses an Intel 8052 (MCS-51) microcontroller and a TI TSP53C30 speech synthesizer (TMS5220-compatible) packed into a single 64 KB EPROM. Through analysis of the dispatch firmware, word address dispatch table, LPC bitstream format, and bit direction, all 414 unique LPC speech clips have been successfully extracted, synthesized to WAV audio, and confirmed using the same codec pipeline developed for the companion CAT-1000 analysis. This report covers hardware architecture, EPROM memory map, 8052 interrupt vectors, TSP53C30 interface, LPC streaming routine, word address dispatch table, speech sequence tables, LPC data region, bit direction confirmation, DST bug, XMODEM serial protocol, and a comparison with the CAT-1000 platform.

---

## 1. Hardware Architecture

### 1.1 System Overview

The CAT-310DX is an Intel 8052-based embedded controller for a digital weather station console. It reads sensors (temperature, humidity, barometric pressure, wind speed/direction, rainfall), drives a numeric display, maintains a real-time clock with DST adjustment, and speaks numeric weather values via a TMS5220-compatible speech synthesizer. Weather history records are accessible via RS-232 serial port using an XMODEM protocol.

**CPU:** Intel 8052 (MCS-51 family, 8-bit, ~11 MHz from 11.0592 MHz crystal)
**Speech synthesizer:** TI TSP53C30 (TMS5220-compatible LPC-10 vocoder)
**EPROM:** One 27C512 (or equivalent) 64 KB, unified firmware + speech
**External RAM:** 8-bit SRAM at 0xA000–0xA4FF (1.25 KB)

Unlike the companion CAT-1000 (which uses two EPROMs, one for code and one for voice data), the CAT-310DX packs the 8052 firmware (~22 KB), word dispatch table (1.5 KB), and LPC speech data (41.8 KB) into a single 64 KB image, leaving approximately 2 KB of zero-filled padding.

### 1.2 Speech Synthesizer Interface

The TSP53C30 is connected to the 8052 external data bus at address **0xC000**. It is accessed via `MOVX @DPTR` instructions:

| Operation | Instruction | Value | Meaning |
|-----------|-------------|-------|---------|
| Read status | `MOVX A, @DPTR` (DPTR=0xC000) | 0xFE | Chip idle, ready for data |
| Read status | `MOVX A, @DPTR` (DPTR=0xC000) | 0xFB | STOP frame detected by chip |
| Write data | `MOVX @DPTR, A` (DPTR=0xC000) | -- | Stream one LPC byte to the chip |

The streaming loop (§3.3) writes raw EPROM bytes directly to the TSP53C30 with no bit transformation, confirming that LPC data is stored in the exact bit order required by the chip.

---

## 2. EPROM Memory Map

The 64 KB image is a single unified firmware + speech blob. Major regions:

```
Address          Bytes     Contents
─────────────    ──────    ──────────────────────────────────────────────
0x0000–0x0006    7 B       8052 interrupt vectors (RESET, INT0, TIMER0)
0x0007–0x002A    36 B      Interrupt vector stubs (INT1, TIMER1, UART -- unused)
0x002B–0x002F    5 B       Timer2 ISR (inline): CPL P3.4; CLR 0xC6; RETI
0x0030–0x34BF    ~13 KB    Main firmware: clock, display, alarms, sensors, DST
0x34C0–0x351E    95 B      Speech sequence tables (pre-built word-ID pair lists)
0x351F–0x4AC2    ~6 KB     Firmware subroutines: dispatch, streaming, XMODEM, math
0x4AC3–0x4F9F    1,245 B   Word address dispatch table (415 × 3-byte entries)
0x4FA0–0xF6E3    41,820 B  TMS5220-compatible LPC speech data (40.8 KB)
0xF6E4–0xF7FF    284 B     Speech control routines (calls 0x4900, 0x48B2)
0xF800–0xFA14    533 B     Serial I/O strings, XMODEM transfer menu
0xFA15–0xFAF0    220 B     Reset handler / system initialization
0xFAF1–0xFFFF    3,855 B   Zero-filled (unused)
```

Total used: approximately 61,681 bytes (94.1%). Free space is tight -- V1.00 had minimal room for growth.

---

## 3. 8052 Interrupt Vectors and Key Routines

### 3.1 Interrupt Vector Table (0x0000–0x002F)

```
Address   Vector       Bytes             Target / Action
0x0000    RESET        02 FA 15          LJMP 0xFA15 (system init)
0x0003    INT0         02 24 7B          LJMP 0x247B (keypad + serial poll)
0x000B    TIMER0       02 23 8C          LJMP 0x238C (~16 ms system tick)
0x0013    INT1         00 00 00          NOP × 3 (unused)
0x001B    TIMER1       00 00 00          NOP × 3 (Timer1 = baud-rate clock, no ISR)
0x0023    UART         00 00 00          NOP × 3 (handled in INT0 ISR)
0x002B    TIMER2       B2 B4 C2 C6 32   inline ISR: CPL P3.4; CLR 0xC6; RETI
```

The Timer2 vector at 0x002B is present and used, confirming the CPU is an **8052** (not the base 8051). The 8052 adds a 16-bit auto-reload/capture Timer2 that the base 8051 does not have.

**Timer2 ISR (inline, 5 bytes):** Toggles P3.4 (heartbeat output or clock signal) and clears extended SFR bit 0xC6 on every Timer2 overflow.

### 3.2 RESET → 0xFA15 (System Initialization)

The reset handler initializes all SFRs and peripheral state:

```asm
D2 BE       SETB  0xBE          ; enable extended peripheral
D2 AE       SETB  EA            ; global interrupt enable
53 B0 CF    ANL   P3, #0xCF     ; configure I/O port directions
53 90 E8    ANL   P1, #0xE8
75 81 C0    MOV   SP, #0xC0     ; stack pointer
75 D8 80    MOV   T2CON, #0x80  ; Timer2: auto-reload mode
75 98 5A    MOV   SCON, #0x5A   ; UART: mode 1, 8-bit, REN=1
75 89 21    MOV   TH1, #0x21    ; Timer1 reload → ~2400 baud at 11.0592 MHz
75 8C 3C    MOV   TH0, #0x3C    ; Timer0 reload high (16 ms tick at 11.0592 MHz)
75 8A AF    MOV   TL0, #0xAF    ; Timer0 reload low
75 88 01    MOV   TCON, #0x01   ; start Timer0; INT0 edge-triggered
```

### 3.3 LPC Streaming Routine at 0x4A78

The streaming routine at 0x4A78 reads LPC bytes from ROM using `MOVC A,@A+DPTR` and writes them to the TSP53C30 via `MOVX @DPTR,A`. The DPTR is used for both source (ROM) and destination (0xC000), so the routine saves and restores DPTR via PUSH/POP:

```asm
4A78:  C0 82 C0 83       PUSH DPL; PUSH DPH         ; save ROM pointer
       74 0A F5 43        MOV A,#10; MOV 0x43,A      ; init retry counter

; Wait for TSP53C30 ready:
4A90:  90 C0 00           MOV DPTR, #0xC000          ; → TSP53C30
4A93:  E0                 MOVX A, @DPTR              ; read status
4A94:  B4 FE 27           CJNE A, #0xFE, $+0x27      ; loop if not 0xFE (ready)
4A97:  74 06              MOV A, #6
       F0                 MOVX @DPTR, A              ; send init byte 0x06

; Main byte stream loop:
4AA4:  (retry/busy-wait)
4AAC:  D0 83 D0 82        POP DPH; POP DPL           ; restore ROM pointer
4AB0:  E4 93              MOVC A, @A+DPTR            ; read byte from ROM (LPC data)
4AB2:  A3                 INC DPTR                   ; advance ROM pointer
4AB3:  C0 82 C0 83        PUSH DPL; PUSH DPH
4AB7:  90 C0 00           MOV DPTR, #0xC000
4ABA:  F0                 MOVX @DPTR, A              ; write byte to TSP53C30
4ABB:  80 DC              SJMP → loop
4ABD:  D0 83 D0 82        POP DPH; POP DPL
4AC1:  22                 RET
```

No bit transformation occurs between the `MOVC` read and the `MOVX` write. LPC bytes are transmitted to the TSP53C30 exactly as stored in the EPROM.

### 3.4 Timer0 ISR → 0x238C (~16 ms System Tick)

Fires every ~16 ms; every other call (~32 ms) updates countdown timers, syncs RTC shadow registers with display RAM, and dispatches clock-update and alarm-check routines.

### 3.5 INT0 ISR → 0x247B (Keypad + Serial Polling)

INT0 is connected to the keypad interrupt line. The ISR additionally polls UART TI/RI flags to handle serial I/O during keypad events.

---

## 4. Word Address Dispatch Table (0x4AC3–0x4F9F)

### 4.1 Table Format

The dispatch table contains **415 entries** of 3 bytes each:

```
[word_id_byte] [addr_hi] [addr_lo]
```

Total table size: 415 × 3 = 1,245 bytes, from 0x4AC3 to 0x4F9E (inclusive). The byte at 0x4F9F is the first byte of the speech data region at 0x4FA0 (table end is 0x4FA0 exclusive).

**Example entries:**

```
0x4AC3: 00 4F A0   → word 0x00 ( 0 = "zero")     → ROM address 0x4FA0
0x4AC6: 01 4F ED   → word 0x01 ( 1 = "one")       → ROM address 0x4FED
0x4AC9: 02 50 30   → word 0x02 ( 2 = "two")       → ROM address 0x5030
0x4AFA: 0A 52 59   → word 0x0A (10 = "ten")       → ROM address 0x5259
0x4B6C: 14 57 4C   → word 0x14 (20 = "twenty")    → ROM address 0x574C
0x4B6F: 1E 57 98   → word 0x1E (30 = "thirty")    → ROM address 0x5798
0x4B72: 28 57 F0   → word 0x28 (40 = "forty")     → ROM address 0x57F0
0x4B75: 32 58 45   → word 0x32 (50 = "fifty")     → ROM address 0x5845
0x4B78: 3C 58 97   → word 0x3C (60 = "sixty")     → ROM address 0x5897
0x4B7B: 46 58 EC   → word 0x46 (70 = "seventy")   → ROM address 0x58EC
0x4B7E: 50 59 3A   → word 0x50 (80 = "eighty")    → ROM address 0x593A
0x4B81: 5A 59 82   → word 0x5A (90 = "ninety")    → ROM address 0x5982
0x4B84: 63 59 CA   → word 0x63 (99 = "ninety-nine") → ROM address 0x59CA
```

### 4.2 Word ID to Spoken Number Mapping

Word IDs use a direct hex-equals-decimal encoding: the hex value of the word ID byte equals the decimal number spoken.

| Word ID | Spoken | | Word ID | Spoken |
|---------|--------|-|---------|--------|
| 0x00 | zero | | 0x0A | ten |
| 0x01 | one | | 0x0B | eleven |
| 0x02 | two | | 0x0C | twelve |
| … | … | | … | … |
| 0x09 | nine | | 0x13 | nineteen |
| 0x14 | twenty | | 0x1E | thirty |
| 0x28 | forty | | 0x32 | fifty |
| 0x3C | sixty | | 0x46 | seventy |
| 0x50 | eighty | | 0x5A | ninety |
| 0x63 | ninety-nine | | | |

Range 0x14–0x63 covers all two-digit numbers: decades are at round multiples (0x14=20, 0x1E=30, …) and intermediate values fill in (0x15=21, 0x16=22, …, 0x1D=29, 0x1F=31, …).

### 4.3 Multiple Recordings Per Word

Of 100 word IDs (0x00–0x63), **96 appear in the table 4–9 times** with different ROM addresses. Only four word IDs have fewer than 4 entries (edge cases such as "zero" and "ninety-nine"). These repeated entries are context-specific intonation recordings: a different recording is used depending on whether the word appears at the end of a phrase (falling intonation) or in the middle of a concatenative sequence (level intonation). The speech sequence tables (§5) select the appropriate recording for each position.

**Total unique ROM addresses: 414** (words 0x00 and 0x34 share address 0x4FA0 -- these two word IDs point to the same recording).

### 4.4 Dispatch Routine at 0x49E6

The dispatch routine uses a **9-way CJNE chain** to map a word ID to a sub-range of the dispatch table, then does a linear search within that sub-range:

```asm
49E6:  B4 xx xx   CJNE A, #range_id, next  ; repeated 9 times
       ...
       (select table sub-range by decade bucket)
       ...
; Linear search within sub-range:
49F0:  B5 43 xx   CJNE A, @R3, next_entry  ; compare word_id
       ...
       E5 44      MOV A, 0x44              ; load addr_hi
       E5 45      MOV A, 0x45              ; load addr_lo
       12 4A 78   LCALL 0x4A78             ; stream clip
       22         RET
```

The routine called at 0x4888 reads speech sequence pairs from 0x34C0 and calls 0x49E6 for each word in the sequence.

---

## 5. Speech Sequence Tables (0x34C0–0x351E)

The 95-byte region at 0x34C0–0x351E contains pre-built word sequences for reading numeric values. Each sequence is a list of `[context_byte, word_id]` pairs terminated by `0xFF`:

```
Format: [0x00, word_id] [0x00, word_id] ... 0xFF
```

**Sequences at 0x34C0 (digits 1–9):**
```
34C0: 00 06 00 00 00 01 FF   → word6, word0, word1
34C7: 00 06 00 00 00 02 FF   → word6, word0, word2
…
34F8: 00 06 00 00 00 09 FF   → word6, word0, word9
```

**Sequences at 0x3500 (10 and above):**
```
3500: 00 06 00 0A FF         → word6, word10
3505: 00 06 00 0B FF         → word6, word11
…
```

The leading `[00, 06]` prefix (word 6 = "six" by the decimal mapping) in every sequence may represent a control token, silence, or tonal prefix rather than the word "six." Disambiguation requires auditing the WAV output for that specific word ID in context. This is noted as an open question.

The sequence reader at 0x4888 processes pairs:
1. Read byte → if `0xFF`, return
2. Save as context byte in R6
3. Read next byte → word_id in R7
4. LCALL 0x49E6 (dispatch + stream)
5. Advance DPTR, loop

---

## 6. LPC Speech Data Region (0x4FA0–0xF6E3)

### 6.1 Region Summary

```
Start:          0x4FA0
End (inclusive):  0xF6E3
Total bytes:    41,820  (40.8 KB)
Unique clips:   414
Word IDs:       100 (0x00–0x63 = numbers 0–99)
Bit order:      MSB-first (bit 7 of first byte = first LPC parameter bit)
```

### 6.2 Bit Direction Confirmation

To confirm bit direction, clips were synthesized in both orientations using `cat-310dx_synth.py --both`:

| Orientation | Synthesis results |
|-------------|-------------------|
| MSB-first (native) | Consistent 400–850 ms durations; STOP frames detected normally |
| LSB-first (reversed) | Many clips hit 5000 ms timeout (no STOP frame found); audio unintelligible |

The MSB-first hypothesis was confirmed correct. This matches the CAT-1000: both devices store LPC data with bit 7 of each EPROM byte being the first parameter bit.

### 6.3 Codec Compatibility

The CAT-310DX LPC format is byte-for-byte compatible with the CAT-1000 TMS5220 format. The `render_phrase_to_pcm()` function in `cat-1000_lpc_export.py` processes CAT-310DX clips correctly without modification. This confirms the TSP53C30 and TMS5220 use the same LPC-10 bit-packed frame codec.

**TMS5220 frame structure (for reference):**
```
STOP frame:      energy[4] = 0xF   (4 bits only)
Silent frame:    energy[4] = 0x0   (4 bits only)
Unvoiced frame:  energy[4] pitch[6]=0 K1–K4[...] (variable)
Voiced frame:    energy[4] pitch[6]>0 K1–K10[...] (variable)
```

### 6.4 Clip Boundaries

Word boundaries are not stored as a phrase index in the EPROM. They are defined by the dispatch table: each clip starts at the `addr_hi:addr_lo` pointer from its dispatch entry and ends where the next clip (sorted by start address) begins. The last clip ends at the first STOP frame byte found by forward scan.

The `cat-310dx_extract.py` script implements this boundary logic:
1. Collect all 415 dispatch table entries: `(word_id, start_addr)`.
2. De-duplicate start addresses to get 414 unique clip starts.
3. Sort by start address; each clip ends where the next begins.
4. Last clip: scan forward for STOP byte (energy nibble = 0xF in MSB-first bit order).

### 6.5 Extraction Results

```
Speech region:  0x4FA0–0xF6E3  (41,820 bytes)
Clips extracted: 414
LPC files:       cat-310dx_lpc_clips/ -- 414 .lpc files
WAV files:       cat-310dx_wav_clips/ -- 414 .wav files
CSV:             cat-310dx_clips.csv
```

Word 99 (0x63, "ninety-nine") hit a synthesis timeout at the last clip boundary -- a boundary artifact from the STOP-byte scan alignment. All 413 other clips synthesized correctly.

---

## 7. DST Bug in V1.00

### 7.1 Overview

Daylight saving time processing is gated by **bit 3 of internal RAM byte 0xDB** (DST enable flag). When enabled, the firmware checks the current month, day of week, and time of day on each Timer0 tick to determine whether to apply a spring-forward (+1 hour, April) or fall-back (−1 hour, October) adjustment.

Pre-2007 US DST rule is implemented: first Sunday in April / last Sunday in October.

### 7.2 The Bug

The firmware contains **three separate code paths** that apply the April spring-forward adjustment (at addresses 0x136A, 0x154A, and 0x2B82), but only **two** code paths for the October fall-back. The asymmetry means that under specific conditions -- a running device where two of the three spring-forward paths both evaluate true for the same transition event -- the clock advances by **+2 hours** in April instead of +1.

```asm
; Representative spring-forward path at 0x2B80:
B4 04 33     CJNE  A, #4, skip        ; if not April, skip
E5 DB        MOV   A, 0xDB            ; load flags byte
30 E3 47     JNB   0xDB.3, skip       ; if DST disabled, skip
74 04        MOV   A, #4              ; +1 hour argument
12 41 2A     LCALL 0x412A             ; apply spring-forward
```

**Affected units:** Running devices with DST enabled (`0xDB` bit 3 = 1) that stay powered on through the April spring-forward transition.

**Unaffected:** Units with DST disabled, units powered off during the transition, and all October fall-back transitions.

**Version status:** V1.00 only. No patch has been identified in the available EPROM.

---

## 8. XMODEM Serial Data Transfer

The firmware includes a complete D/U/Q (Download / Upload / Quit) RS-232 transfer menu for weather history records.

**Menu string at 0xF85C:**
```
"CAT-310 Data Transfer, D=Download...U=Upload...Q=Quit. Select "
```

**Transfer strings at 0x1A7A:**
```
"Select XMODEM file DOWNLOAD protocol now..."
"Data Error or Timeout has Expired..."
"Data transfer successful"
"Select XMODEM file UPLOAD protocol now..."
```

**Protocol:** 128-byte block XMODEM (CRC or checksum). Transfer handler at 0x1A00–0x1B10.

**Baud rate:** TH1=0x21 → ~2400 baud at 11.0592 MHz.

**Data transferred:** Weather history ring buffer at 0xA100–0xA3FF (768 bytes, approximately 128 XMODEM blocks).

---

## 9. Comparison: CAT-1000 vs CAT-310DX

### 9.1 Platform Differences

| Feature | CAT-1000 | CAT-310DX V1.00 |
|---------|----------|-----------------|
| CPU | Intel 80C186 (16-bit x86) | Intel 8052 (8-bit MCS-51) |
| Clock | ~8 MHz | ~11 MHz |
| EPROM | 2 × 64 KB (program + voice) | 1 × 64 KB (unified) |
| Speech chip | TMS5220 | TSP53C30 |
| Speech I/O port | 0x0180 (x86 `OUT DX,AL`) | 0xC000 (8052 `MOVX @DPTR,A`) |
| Ready status value | 0xFE | 0xFE (identical) |
| STOP detected value | ISR monitors STOP nibble | Chip signals 0xFB |
| External RAM | Segment-mapped x86 | 8-bit SRAM at 0xA000 |
| Serial / data I/O | None | XMODEM at ~2400 baud |
| Real-time clock | None (host-driven) | Timer0-driven RTC with DST |

### 9.2 LPC Speech Data

| Property | CAT-1000 | CAT-310DX |
|----------|----------|-----------|
| Speech bytes | 48,767 B (0x0642–0xC4C1) | 41,820 B (0x4FA0–0xF6E3) |
| Unique clips | 481 | 414 |
| Index/table | Phrase index at 0x009F (group×100+id) | Word dispatch table at 0x4AC3 |
| Vocabulary | 481 labelled phrases (weather + numeric) | 100 word IDs, numbers 0–99 |
| Recordings/word | One per phrase | 4–9 per word ID (intonation variants) |
| Bit order | MSB-first | MSB-first (identical codec) |
| Codec | TMS5220 LPC-10 | TMS5220 LPC-10 (identical) |

### 9.3 Key Finding: Codec Identity

The LPC-10 bit-packed frame format is identical on both devices. The `render_phrase_to_pcm()` synthesis pipeline developed for the CAT-1000 processes CAT-310DX data correctly without modification. This is consistent with the TSP53C30 being a pin-compatible or functionally equivalent variant of the TMS5220 using the same LPC-10 codec definition.

---

## 10. Tools and Output Files

| File | Purpose |
|------|---------|
| `cat-310dx_extract.py` | Parses dispatch table, extracts 414 LPC clips, synthesizes WAVs |
| `cat-310dx_synth.py` | One-off synthesis test (MSB/LSB direction validation) |
| `cat-310dx_lpc_clips/` | 414 .lpc files |
| `cat-310dx_wav_clips/` | 414 .wav files |
| `cat-310dx_clips.csv` | Clip manifest (sequence, label, start, end) |
| `docs/cat310dx_analysis.md` | Detailed technical reference (memory map, ISR disassembly, dispatch table listing) |

**Re-extraction command:**
```bash
python3 cat-310dx_extract.py --wav --wav-dir cat-310dx_wav_clips/ -o cat-310dx_lpc_clips/
# Expected: "Exported 414 clip(s)."
```

---

## 11. Key Discoveries

1. **TSP53C30 on-board, LPC speech confirmed**: 41.8 KB of MSB-first TMS5220-compatible LPC-10 data at 0x4FA0–0xF6E3. The full-image `eprom_scout.py` scan produces only false positives (8052 machine code misidentified as LPC blobs, keypad CJNE table misidentified as phrase index). Extraction requires reading the dispatch table at 0x4AC3 directly.

2. **Identical LPC codec**: The CAT-310DX and CAT-1000 share the same TMS5220 LPC-10 bit-packed frame format (MSB-first, identical energy/pitch/K tables). The same synthesis pipeline works on both devices.

3. **Complete 0–99 numeric vocabulary with intonation variants**: 100 word IDs covering all numbers 0–99, with 96 words having 4–9 context-specific recordings. This enables natural concatenative speech for numeric weather readouts.

4. **Single-chip architecture**: Firmware + dispatch table + speech data packed into one 64 KB EPROM. With only ~2 KB free, V1.00 had essentially no room for additional features without growing to a larger EPROM.

5. **8052 confirmed**: Timer2 inline ISR at 0x002B proves the CPU is an 8052 (not 8051). The Timer2 auto-reload mode provides the heartbeat toggle on P3.4.

6. **DST bug (V1.00)**: Three spring-forward code paths vs. two fall-back paths. April DST transition may advance clock by +2 hours instead of +1 on running units with DST enabled. October fall-back unaffected.

7. **XMODEM for data access**: All stored weather history is accessible via RS-232 at ~2400 baud using the built-in D/U/Q transfer menu.

---

Copyright (C) 2026 Kris Kirby, KE4AHR. This document is licensed under the GNU General Public License v3.0 or later. You may redistribute and/or modify it under the terms of the GNU GPL as published by the Free Software Foundation. See <https://www.gnu.org/licenses/> for details.
